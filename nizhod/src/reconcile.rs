use std::collections::HashSet;
use std::sync::{Mutex, MutexGuard, OnceLock};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use crate::{enforcers::AsyncEnforcer, score, state::SharedState};

async fn observe(e: &dyn AsyncEnforcer, state: &SharedState) {
    if tokio::time::timeout(Duration::from_secs(15), e.update_state(state)).await.is_err() {
        state.write().await.invalidate(e.name());
    }
}

/// Controls that logged a drift and have not been confirmed since. Lets the
/// log say "confirmed again" even when the setting came back without nizhod
/// applying it (for example firewalld restarted by hand), so the event list
/// never shows a drift as unresolved after it is fixed.
fn drifted() -> &'static Mutex<HashSet<&'static str>> {
    static SET: OnceLock<Mutex<HashSet<&'static str>>> = OnceLock::new();
    SET.get_or_init(|| Mutex::new(HashSet::new()))
}

/// Locks a mutex even when another thread panicked while holding it. Rust
/// marks such a mutex poisoned and `lock().unwrap()` would then panic on every
/// later use, which here would stop the reconcile loop for good. Only use this
/// where the guarded value is valid after any interrupted operation. That holds
/// for the drift set: an insert or remove either happened or did not.
fn lock_recovering<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(|poisoned| {
        tracing::warn!("a thread panicked while holding a nizhod lock; continuing with its last contents");
        mutex.clear_poison();
        poisoned.into_inner()
    })
}

/// Called under one shared mutex by the periodic loop and the D-Bus method.
/// Every failed control is retried, regardless of the aggregate score.
pub async fn reconcile(enforcers: &[Box<dyn AsyncEnforcer>], state: &SharedState, force: bool) -> Vec<String> {
    let mut failed = Vec::new();
    for e in enforcers {
        let was_healthy = state.read().await.control_healthy(e.name());
        observe(e.as_ref(), state).await;
        let healthy = state.read().await.control_healthy(e.name());
        if was_healthy && !healthy {
            crate::log::log_event(e.name(), "drift", "Setting could not be confirmed; trying again");
            lock_recovering(drifted()).insert(e.name());
        } else if healthy && lock_recovering(drifted()).remove(e.name()) {
            crate::log::log_event(e.name(), "recovered", "Setting confirmed again");
        }
        if force || !healthy {
            let applied = tokio::time::timeout(Duration::from_secs(15), e.enforce()).await;
            let apply_ok = matches!(applied, Ok(Ok(())));
            if !apply_ok {
                tracing::warn!(enforcer = e.name(), "enforcement failed or timed out");
            }
            observe(e.as_ref(), state).await;
            let verified = state.read().await.control_healthy(e.name());
            if !apply_ok || !verified {
                failed.push(e.name().to_owned());
            } else if !healthy {
                lock_recovering(drifted()).remove(e.name());
                crate::log::log_event(e.name(), "recovered", "Setting confirmed after applying it again");
            }
        }
        // A slow control must not starve the service watchdog between controls.
        let _ = sd_notify::notify(false, &[sd_notify::NotifyState::Watchdog]);
    }
    let mut s = state.write().await;
    s.privacy_score = score::compute(&s);
    s.last_check = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs();
    failed
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Arc, atomic::{AtomicUsize, Ordering}};
    use crate::{error::NizhodError, state::NizhodState};

    struct FailingControl(Arc<AtomicUsize>);
    #[async_trait::async_trait]
    impl AsyncEnforcer for FailingControl {
        fn name(&self) -> &'static str { "hostname" }
        async fn enforce(&self) -> Result<(), NizhodError> {
            self.0.fetch_add(1, Ordering::SeqCst);
            Err(NizhodError::Enforce("test failure".into()))
        }
        async fn update_state(&self, s: &SharedState) { s.write().await.hostname_privacy = false; }
    }

    #[tokio::test]
    async fn unchanged_failure_is_retried_and_returned_to_the_caller() {
        let attempts = Arc::new(AtomicUsize::new(0));
        let es: Vec<Box<dyn AsyncEnforcer>> = vec![Box::new(FailingControl(attempts.clone()))];
        let s = Arc::new(tokio::sync::RwLock::new(NizhodState::default()));
        for _ in 0..3 {
            assert_eq!(reconcile(&es, &s, false).await, ["hostname"]);
        }
        assert_eq!(attempts.load(Ordering::SeqCst), 3);
        assert!(!s.read().await.hostname_privacy);
        assert!(s.read().await.last_check > 0);
    }
}

#[cfg(test)]
mod drift_tracking_tests {
    use super::*;
    use std::sync::Arc;
    use std::sync::atomic::{AtomicBool, Ordering};
    use crate::{error::NizhodError, state::NizhodState};

    // Healthy or not according to a switch; enforce always fails, so only an
    // outside change can bring it back (like firewalld restarted by hand).
    struct Switch(Arc<AtomicBool>);
    #[async_trait::async_trait]
    impl AsyncEnforcer for Switch {
        fn name(&self) -> &'static str { "ipv6" }
        async fn enforce(&self) -> Result<(), NizhodError> { Err(NizhodError::Enforce("no".into())) }
        async fn update_state(&self, s: &SharedState) { s.write().await.ipv6_privacy = self.0.load(Ordering::SeqCst); }
    }

    #[tokio::test]
    async fn a_drift_fixed_from_outside_is_tracked_until_confirmed() {
        let on = Arc::new(AtomicBool::new(true));
        let es: Vec<Box<dyn AsyncEnforcer>> = vec![Box::new(Switch(on.clone()))];
        let s = Arc::new(tokio::sync::RwLock::new(NizhodState::default()));
        assert!(reconcile(&es, &s, false).await.is_empty());
        on.store(false, Ordering::SeqCst);
        assert_eq!(reconcile(&es, &s, false).await, ["ipv6"]);
        assert!(lock_recovering(drifted()).contains("ipv6"), "drift remembered");
        on.store(true, Ordering::SeqCst);
        assert!(reconcile(&es, &s, false).await.is_empty());
        assert!(!lock_recovering(drifted()).contains("ipv6"), "confirmed again, forgotten");
    }
}

#[cfg(test)]
mod freshness_tests {
    use super::*;
    use std::sync::Arc;
    use crate::{error::NizhodError, state::NizhodState};

    struct BlockedControl(Arc<tokio::sync::Notify>);
    #[async_trait::async_trait]
    impl AsyncEnforcer for BlockedControl {
        fn name(&self) -> &'static str { "hostname" }
        async fn enforce(&self) -> Result<(), NizhodError> { Ok(()) }
        async fn update_state(&self, state: &SharedState) {
            self.0.notify_one();
            std::future::pending::<()>().await;
            state.write().await.hostname_privacy = true;
        }
    }

    #[tokio::test]
    async fn unfinished_or_cancelled_reconcile_does_not_refresh_timestamp() {
        let state = Arc::new(tokio::sync::RwLock::new(NizhodState::default()));
        assert_eq!(state.read().await.last_check, 0);
        reconcile(&[], &state, false).await;
        let completed = state.read().await.last_check;
        assert!(completed > 0);
        let started = Arc::new(tokio::sync::Notify::new());
        let enforcers: Vec<Box<dyn AsyncEnforcer>> = vec![Box::new(BlockedControl(started.clone()))];
        let copy = state.clone();
        let task = tokio::spawn(async move { reconcile(&enforcers, &copy, false).await });
        started.notified().await;
        assert_eq!(state.read().await.last_check, completed);
        task.abort();
        assert!(task.await.unwrap_err().is_cancelled());
        assert_eq!(state.read().await.last_check, completed);
    }
}

#[cfg(test)]
mod poison_tests {
    use super::*;
    use std::sync::Arc;
    use std::sync::atomic::{AtomicBool, Ordering};
    use crate::{error::NizhodError, state::NizhodState};

    fn poison<T: Send + 'static>(mutex: &'static Mutex<T>) {
        let panicked = std::thread::spawn(move || {
            let _guard = mutex.lock().unwrap_or_else(|p| p.into_inner());
            panic!("deliberate panic to poison the lock (expected in this test)");
        })
        .join();
        assert!(panicked.is_err());
    }

    #[test]
    fn a_poisoned_lock_is_recovered_with_its_contents_and_cleared() {
        static LOCAL: OnceLock<Mutex<HashSet<&'static str>>> = OnceLock::new();
        let mutex = LOCAL.get_or_init(|| Mutex::new(HashSet::from(["dns"])));
        poison(mutex);
        assert!(mutex.is_poisoned());
        assert!(mutex.lock().is_err(), "a plain lock() would have failed here");
        assert!(lock_recovering(mutex).contains("dns"));
        assert!(!mutex.is_poisoned());
        assert!(mutex.lock().is_ok());
    }

    // Uses "mac", which no other test in this file tracks in the shared set.
    struct Switch(Arc<AtomicBool>);
    #[async_trait::async_trait]
    impl AsyncEnforcer for Switch {
        fn name(&self) -> &'static str { "mac" }
        async fn enforce(&self) -> Result<(), NizhodError> { Err(NizhodError::Enforce("no".into())) }
        async fn update_state(&self, s: &SharedState) { s.write().await.mac_randomization = self.0.load(Ordering::SeqCst); }
    }

    #[tokio::test]
    async fn reconcile_keeps_running_and_tracking_after_the_drift_lock_is_poisoned() {
        let on = Arc::new(AtomicBool::new(true));
        let es: Vec<Box<dyn AsyncEnforcer>> = vec![Box::new(Switch(on.clone()))];
        let s = Arc::new(tokio::sync::RwLock::new(NizhodState::default()));
        assert!(reconcile(&es, &s, false).await.is_empty());
        poison(drifted());
        on.store(false, Ordering::SeqCst);
        assert_eq!(reconcile(&es, &s, false).await, ["mac"]);
        assert!(lock_recovering(drifted()).contains("mac"), "drift still remembered");
        poison(drifted());
        on.store(true, Ordering::SeqCst);
        assert!(reconcile(&es, &s, false).await.is_empty());
        assert!(!lock_recovering(drifted()).contains("mac"), "confirmed again, forgotten");
        assert!(s.read().await.last_check > 0);
    }
}
