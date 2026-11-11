use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use nizho_shell_common::{BrowserSandbox, SandboxStatus};
use crate::error::NizhodError;
use crate::state::SharedState;
use super::AsyncEnforcer;

/// Options the wrapper must pass to bubblewrap. Each is a run of whole words
/// on lines that are not comments. Other options may sit around them.
///   --tmpfs /home     the real home directories are hidden
///   --unshare-pid     the browser cannot see other processes
///   --cap-drop ALL    the browser holds no capabilities
const MARKERS: [&[&str]; 3] = [&["--tmpfs", "/home"], &["--unshare-pid"], &["--cap-drop", "ALL"]];

type EventLog = Arc<dyn Fn(&str, &str, &str) + Send + Sync>;

pub struct SandboxEnforcer {
    bwrap: String,
    /// The name programs launch. Must resolve to `wrapper`.
    firefox: PathBuf,
    wrapper: PathBuf,
    unwrapped: PathBuf,
    log: EventLog,
    /// The reason last written to the event log, so one cause is logged once
    /// and not on every 60 second check.
    last_reason: Mutex<Option<String>>,
}

impl SandboxEnforcer {
    pub fn new() -> Self {
        Self::with_parts(
            "bwrap",
            "/usr/bin/firefox",
            "/usr/bin/firefox-sandbox",
            "/usr/bin/firefox-unwrapped",
            Arc::new(crate::log::log_event),
        )
    }

    fn with_parts(bwrap: &str, firefox: &str, wrapper: &str, unwrapped: &str, log: EventLog) -> Self {
        Self {
            bwrap: bwrap.into(),
            firefox: firefox.into(),
            wrapper: wrapper.into(),
            unwrapped: unwrapped.into(),
            log,
            last_reason: Mutex::new(None),
        }
    }

    /// Checks the installed files. This reads what is installed. It does not
    /// start a browser and does not test that the sandbox holds.
    fn check_files(&self) -> Result<(), String> {
        let resolved = std::fs::canonicalize(&self.firefox)
            .map_err(|e| format!("{} cannot be resolved ({})", self.firefox.display(), e.kind()))?;
        if resolved != self.wrapper {
            return Err(format!(
                "{} points to {}, expected the sandbox wrapper {}",
                self.firefox.display(), resolved.display(), self.wrapper.display()
            ));
        }
        if !self.unwrapped.is_file() {
            return Err(format!("the browser program {} is missing", self.unwrapped.display()));
        }
        let script = std::fs::read(&self.wrapper)
            .map_err(|e| format!("the sandbox wrapper {} cannot be read ({})", self.wrapper.display(), e.kind()))?;
        let missing = missing_markers(&String::from_utf8_lossy(&script));
        if !missing.is_empty() {
            return Err(format!("the sandbox wrapper lacks the option {}", missing.join(" and ")));
        }
        Ok(())
    }

    /// Persistent when the wrapper binds the saved profile folder and offers a private session,
    /// Ephemeral otherwise. Only called after check_files succeeded, so the wrapper is readable.
    fn profile_mode(&self) -> BrowserSandbox {
        let script = std::fs::read(&self.wrapper).map(|b| String::from_utf8_lossy(&b).into_owned()).unwrap_or_default();
        if script.contains("firefox-profile") && script.contains("--private-session") {
            BrowserSandbox::Persistent
        } else {
            BrowserSandbox::Ephemeral
        }
    }

    async fn check_bwrap(&self) -> Result<(), String> {
        crate::command::run(&self.bwrap, &["--version"]).await
            .map(|_| ())
            .map_err(|e| format!("bubblewrap ({}) did not run: {e}", self.bwrap))
    }

    /// Writes the reason to the event log when it is new.
    fn note(&self, outcome: &Result<(), String>) {
        let mut last = self.last_reason.lock().unwrap_or_else(|p| p.into_inner());
        let reason = outcome.as_ref().err();
        if reason != last.as_ref() {
            if let Some(reason) = reason {
                (self.log)("sandbox", "detail", &format!("Sandboxed browser not confirmed: {reason}"));
            }
            *last = reason.cloned();
        }
    }
}

/// The markers that `script` does not contain, as text for an event.
fn missing_markers(script: &str) -> Vec<String> {
    let words: Vec<&str> = script.lines()
        .map(str::trim_start)
        .filter(|line| !line.starts_with('#'))
        .flat_map(str::split_whitespace)
        .collect();
    MARKERS.iter()
        .filter(|marker| !words.windows(marker.len()).any(|w| w == **marker))
        .map(|marker| marker.join(" "))
        .collect()
}

#[async_trait::async_trait]
impl AsyncEnforcer for SandboxEnforcer {
    fn name(&self) -> &'static str { "sandbox" }

    async fn enforce(&self) -> Result<(), NizhodError> {
        // Sandbox is baked in at build time via the Containerfile.
        // Nothing to enforce at runtime.
        Ok(())
    }

    async fn update_state(&self, state: &SharedState) {
        let files = self.check_files();
        let all = match &files {
            Ok(()) => self.check_bwrap().await,
            Err(reason) => Err(reason.clone()),
        };
        self.note(&all);

        // The wrapper hides /home behind a tmpfs. A wrapper that also binds a saved profile folder
        // (and offers --private-session for a profile that is thrown away) keeps bookmarks and
        // logins between launches: Persistent. An older wrapper without it discards the whole
        // profile on close: Ephemeral. Report what the installed wrapper does (docs/PHASE4-SANDBOX.md).
        let browser = if files.is_ok() { self.profile_mode() } else { BrowserSandbox::None };
        let status = if all.is_ok() { SandboxStatus::Active } else { SandboxStatus::Inactive };

        let mut s = state.write().await;
        s.sandbox_status = status;
        s.browser_sandbox = browser;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::{symlink, DirBuilderExt, PermissionsExt};
    use std::sync::atomic::{AtomicU32, Ordering};
    use crate::state::NizhodState;

    const WRAPPER: &str = "#!/bin/bash\nBROWSER=/usr/bin/firefox-unwrapped\nBWRAP_ARGS=(\n    --ro-bind /usr /usr\n    # Home isolation\n    --tmpfs /home\n    --tmpfs /root\n    --unshare-pid\n    --unshare-ipc\n    --cap-drop ALL\n    --new-session\n)\nexec bwrap \"${BWRAP_ARGS[@]}\" \"$BROWSER\" \"$@\"\n";

    /// A private directory laid out like /usr/bin, removed on drop.
    struct Bin(PathBuf);
    impl Bin {
        fn new() -> Self {
            static NEXT: AtomicU32 = AtomicU32::new(0);
            let made = std::env::temp_dir().join(format!(
                "nizhod-sandbox-test-{}-{}", std::process::id(), NEXT.fetch_add(1, Ordering::SeqCst)
            ));
            std::fs::DirBuilder::new().mode(0o700).create(&made).expect("temp dir");
            // Compare against the real path: the temp dir itself may be a link.
            let bin = Self(std::fs::canonicalize(&made).unwrap());
            bin.script("firefox-sandbox", WRAPPER);
            bin.script("firefox-unwrapped", "#!/bin/sh\n");
            symlink("firefox-sandbox", bin.0.join("firefox")).unwrap();
            bin
        }
        fn script(&self, name: &str, body: &str) {
            let path = self.0.join(name);
            std::fs::write(&path, body).unwrap();
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755)).unwrap();
        }
        fn path(&self, name: &str) -> String { self.0.join(name).to_str().unwrap().to_owned() }
        /// These files are only read, never run. `true` stands in for a
        /// working bubblewrap, so no test runs a file it has just written.
        fn enforcer(&self) -> (SandboxEnforcer, Arc<Mutex<Vec<String>>>) {
            self.enforcer_with("true")
        }
        fn enforcer_with(&self, bwrap: &str) -> (SandboxEnforcer, Arc<Mutex<Vec<String>>>) {
            let events = Arc::new(Mutex::new(Vec::new()));
            let sink = events.clone();
            let log: EventLog = Arc::new(move |cat, ev, msg| sink.lock().unwrap().push(format!("{cat}|{ev}|{msg}")));
            let e = SandboxEnforcer::with_parts(
                bwrap, &self.path("firefox"), &self.path("firefox-sandbox"), &self.path("firefox-unwrapped"), log,
            );
            (e, events)
        }
    }
    impl Drop for Bin {
        fn drop(&mut self) {
            if let Err(e) = std::fs::remove_dir_all(&self.0) {
                eprintln!("could not remove {}: {e}", self.0.display());
            }
        }
    }

    async fn observe(e: &SandboxEnforcer) -> (SandboxStatus, BrowserSandbox, bool) {
        let state = Arc::new(tokio::sync::RwLock::new(NizhodState::default()));
        e.update_state(&state).await;
        let s = state.read().await;
        (s.sandbox_status.clone(), s.browser_sandbox.clone(), s.control_healthy("sandbox"))
    }

    #[tokio::test]
    async fn a_complete_installation_is_confirmed_without_any_event() {
        let bin = Bin::new();
        let (e, events) = bin.enforcer();
        assert_eq!(observe(&e).await, (SandboxStatus::Active, BrowserSandbox::Ephemeral, true));
        assert!(events.lock().unwrap().is_empty());
    }

    #[tokio::test]
    async fn a_wrapper_with_a_saved_profile_is_reported_as_persistent() {
        let bin = Bin::new();
        bin.script("firefox-sandbox", &format!(
            "{WRAPPER}\n# --private-session keeps nothing\nPROFILE=\"$HOME/.local/share/nizho/firefox-profile\"\n"
        ));
        let (e, events) = bin.enforcer();
        assert_eq!(observe(&e).await, (SandboxStatus::Active, BrowserSandbox::Persistent, true));
        assert!(events.lock().unwrap().is_empty());
    }

    #[test]
    fn the_shipped_wrapper_has_every_marker() {
        // The real file from this repository, read as text only.
        let shipped = include_str!("../../../system/nizho-firefox");
        assert_eq!(missing_markers(shipped), Vec::<String>::new());
    }

    #[test]
    fn markers_are_whole_words_outside_comments_and_tolerate_other_options() {
        assert!(missing_markers(WRAPPER).is_empty());
        let extra = WRAPPER.replace("--unshare-pid", "--unshare-pid --unshare-cgroup-try --seccomp 9");
        assert!(missing_markers(&extra).is_empty());
        let one_line = "exec bwrap --new-session   --cap-drop  ALL --tmpfs\t/home --unshare-pid -- firefox";
        assert!(missing_markers(one_line).is_empty());
        for (weakened, missing) in [
            (WRAPPER.replace("    --tmpfs /home\n", ""), "--tmpfs /home"),
            (WRAPPER.replace("--tmpfs /home\n", "--tmpfs /home/guest\n"), "--tmpfs /home"),
            (WRAPPER.replace("--tmpfs /home\n", "--bind /home /home\n"), "--tmpfs /home"),
            (WRAPPER.replace("    --tmpfs /home\n", "    # --tmpfs /home\n"), "--tmpfs /home"),
            (WRAPPER.replace("--unshare-pid", "--unshare-pid-try"), "--unshare-pid"),
            (WRAPPER.replace("    --unshare-pid\n", ""), "--unshare-pid"),
            (WRAPPER.replace("--cap-drop ALL", "--cap-drop CAP_SYS_ADMIN"), "--cap-drop ALL"),
            (WRAPPER.replace("--cap-drop ALL", "--cap-add ALL"), "--cap-drop ALL"),
        ] {
            assert_eq!(missing_markers(&weakened), [missing], "{weakened}");
        }
        assert_eq!(missing_markers("").len(), 3);
    }

    #[tokio::test]
    async fn each_broken_part_is_not_confirmed_and_the_event_names_the_reason() {
        type Break = fn(&Bin);
        let cases: [(Break, &str); 6] = [
            (|b| std::fs::remove_file(b.0.join("firefox")).unwrap(),
             "cannot be resolved"),
            (|b| { std::fs::remove_file(b.0.join("firefox")).unwrap(); symlink("firefox-unwrapped", b.0.join("firefox")).unwrap(); },
             "expected the sandbox wrapper"),
            (|b| { std::fs::remove_file(b.0.join("firefox")).unwrap(); std::fs::copy(b.0.join("firefox-sandbox"), b.0.join("firefox")).unwrap(); },
             "expected the sandbox wrapper"),
            (|b| std::fs::remove_file(b.0.join("firefox-unwrapped")).unwrap(),
             "firefox-unwrapped is missing"),
            (|b| b.script("firefox-sandbox", &WRAPPER.replace("    --tmpfs /home\n", "")),
             "lacks the option --tmpfs /home"),
            (|b| b.script("firefox-sandbox", "#!/bin/sh\nexec /usr/bin/firefox-unwrapped \"$@\"\n"),
             "lacks the option --tmpfs /home and --unshare-pid and --cap-drop ALL"),
        ];
        for (break_it, reason) in cases {
            let bin = Bin::new();
            break_it(&bin);
            let (e, events) = bin.enforcer();
            assert_eq!(observe(&e).await, (SandboxStatus::Inactive, BrowserSandbox::None, false), "{reason}");
            let events = events.lock().unwrap();
            assert_eq!(events.len(), 1, "{reason}");
            assert!(events[0].starts_with("sandbox|detail|Sandboxed browser not confirmed: "), "{}", events[0]);
            assert!(events[0].contains(reason), "{} lacks {reason}", events[0]);
        }
    }

    #[tokio::test]
    async fn a_missing_or_failing_bubblewrap_is_not_confirmed() {
        let bin = Bin::new();
        for bwrap in ["false", "/nonexistent/bwrap"] {
            let (e, events) = bin.enforcer_with(bwrap);
            assert_eq!(observe(&e).await, (SandboxStatus::Inactive, BrowserSandbox::Ephemeral, false));
            let events = events.lock().unwrap();
            assert_eq!(events.len(), 1);
            assert!(events[0].contains(&format!("bubblewrap ({bwrap}) did not run")), "{}", events[0]);
        }
    }

    #[tokio::test]
    async fn one_cause_is_logged_once_and_a_new_cause_is_logged_again() {
        let bin = Bin::new();
        let (e, events) = bin.enforcer();
        std::fs::remove_file(bin.0.join("firefox-unwrapped")).unwrap();
        for _ in 0..3 { observe(&e).await; }
        assert_eq!(events.lock().unwrap().len(), 1);
        bin.script("firefox-unwrapped", "#!/bin/sh\n");
        assert!(observe(&e).await.2);
        assert_eq!(events.lock().unwrap().len(), 1, "a confirmed check adds no event here");
        bin.script("firefox-sandbox", &WRAPPER.replace("--cap-drop ALL", ""));
        for _ in 0..2 { observe(&e).await; }
        std::fs::remove_file(bin.0.join("firefox-unwrapped")).unwrap();
        observe(&e).await;
        let events = events.lock().unwrap();
        assert_eq!(events.len(), 3);
        assert!(events[1].contains("--cap-drop ALL"));
        assert!(events[2].contains("is missing"));
    }
}
