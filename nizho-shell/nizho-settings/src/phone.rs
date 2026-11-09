//! The phone link's logic for Settings: the firewall rule's state file, the privileged
//! helper call, and the words for each outcome. Nothing here touches the screen, so it
//! can be tested without one.
//!
//! Two things make the link work: KDE Connect's own program (the user daemon) and a
//! firewall rule that the helper `nizho-phone-link` opens and closes as root.

use std::process::{Command, Stdio};

const HELPER: &str = "/usr/libexec/nizho-phone-link";
const STATE: &str = "/etc/nizho/phone-link";

pub const OFF: &str = "Off. Phones on your network cannot find this computer.";
pub const ON: &str = "On. Phones on the same network can find this computer, and the firewall allows \
KDE Connect from the local network. You still pair each phone yourself in the KDE Connect app.";
pub const STOPPED_RULE_OPEN: &str = "KDE Connect is stopped, but the firewall rule is still open because the \
password prompt was cancelled. Turn this off again to close it.";
pub const RUNNING_RULE_CLOSED: &str = "KDE Connect is running, but the firewall rule is closed, so phones on \
your network cannot reach this computer. Turn this off, then on again to open the rule.";
pub const ON_CANCELLED: &str = "The password prompt was cancelled, so phone linking stays off.";
pub const DAEMON_FAILED: &str = "The firewall rule is open, but KDE Connect did not start. Open the KDE Connect \
app to start it, or turn this off to close the rule.";

/// How the privileged helper ended.
#[derive(Debug, Clone, PartialEq)]
pub enum Outcome {
    Done,
    /// The password prompt was cancelled, or authorisation failed (exit 126 or 127).
    Cancelled,
    /// The helper failed; the text is its own message.
    Failed(String),
}

/// What the file /etc/nizho/phone-link says: exactly `on` means allowed, anything else means off.
pub fn parse_state(text: &str) -> bool {
    text.trim() == "on"
}

fn state_path() -> String {
    std::env::var("NIZHO_PHONE_STATE").ok().filter(|s| !s.is_empty()).unwrap_or_else(|| STATE.into())
}

/// Whether the firewall rule is open, read from the state file (no privilege needed).
pub fn rule_open() -> bool {
    std::fs::read_to_string(state_path()).map(|t| parse_state(&t)).unwrap_or(false)
}

/// The one place that runs the privileged helper: `pkexec /usr/libexec/nizho-phone-link on|off`.
/// Tests set NIZHO_PHONE_HELPER to a stand-in script, which then runs directly, without pkexec.
pub fn run_helper(arg: &str) -> Outcome {
    let mut cmd = match std::env::var("NIZHO_PHONE_HELPER").ok().filter(|s| !s.is_empty()) {
        Some(script) => {
            let mut c = Command::new(script);
            c.arg(arg);
            c
        }
        None => {
            let mut c = Command::new("pkexec");
            c.arg(HELPER).arg(arg);
            c
        }
    };
    match cmd.stdin(Stdio::null()).output() {
        Err(_) => Outcome::Failed("The phone link helper is not available on this system.".into()),
        Ok(out) => outcome_of(out.status.code(), &String::from_utf8_lossy(&out.stderr)),
    }
}

pub fn outcome_of(code: Option<i32>, stderr: &str) -> Outcome {
    match code {
        Some(0) => Outcome::Done,
        Some(126) | Some(127) => Outcome::Cancelled,
        _ => {
            let msg = stderr.lines().rev().find(|l| !l.trim().is_empty()).unwrap_or("The helper did not finish.");
            Outcome::Failed(msg.trim().trim_start_matches("nizho-phone-link:").trim().to_string())
        }
    }
}

/// The switch is on when either part is running or open, so it never hides a part left behind.
pub fn switch_on(daemon_running: bool, rule_open: bool) -> bool {
    daemon_running || rule_open
}

/// The subtitle for what is true now.
pub fn subtitle(daemon_running: bool, rule_open: bool) -> &'static str {
    match (daemon_running, rule_open) {
        (true, true) => ON,
        (false, false) => OFF,
        (false, true) => STOPPED_RULE_OPEN,
        (true, false) => RUNNING_RULE_CLOSED,
    }
}

/// Turn the link on or off. `helper` runs the privileged helper with "on" or "off";
/// `daemon` starts (true) or stops (false) the user daemon and says whether that worked.
/// Returns a message to show instead of the state's own subtitle, or None.
pub fn apply(
    want: bool,
    helper: impl Fn(&str) -> Outcome,
    daemon: impl Fn(bool) -> bool,
) -> Option<String> {
    if want {
        match helper("on") {
            Outcome::Done => {}
            Outcome::Cancelled => return Some(ON_CANCELLED.into()),
            Outcome::Failed(e) => {
                return Some(format!("Couldn't open the firewall for KDE Connect: {e} Phone linking stays off."))
            }
        }
        if daemon(true) { None } else { Some(DAEMON_FAILED.into()) }
    } else {
        let stopped = daemon(false);
        match helper("off") {
            Outcome::Done => {
                if stopped { None } else { Some("The firewall rule is closed, but KDE Connect is still running. Open the KDE Connect app to quit it.".into()) }
            }
            // The subtitle for "stopped, rule open" already says it.
            Outcome::Cancelled => None,
            Outcome::Failed(e) => Some(format!(
                "Couldn't close the firewall rule: {e} KDE Connect is stopped. Turn this off again to close the rule."
            )),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;
    use std::os::unix::fs::PermissionsExt;
    use std::sync::Mutex;

    // Some tests point NIZHO_PHONE_HELPER and NIZHO_PHONE_STATE at stand-ins, so they cannot run side by side.
    static LOCK: Mutex<()> = Mutex::new(());

    struct Dir(std::path::PathBuf);
    impl Dir {
        fn new() -> Self {
            let p = std::env::temp_dir().join(format!("nizho-phone-test-{}-{:?}", std::process::id(), std::thread::current().id()));
            let _ = std::fs::remove_dir_all(&p);
            std::fs::create_dir_all(&p).unwrap();
            Dir(p)
        }
    }
    impl Drop for Dir {
        fn drop(&mut self) { let _ = std::fs::remove_dir_all(&self.0); }
    }

    fn fake(d: &Dir, script: &str) {
        let p = d.0.join("nizho-phone-link");
        std::fs::write(&p, format!("#!/bin/sh\n{script}\n")).unwrap();
        std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o755)).unwrap();
        std::env::set_var("NIZHO_PHONE_HELPER", &p);
    }

    #[test]
    fn state_file_is_on_only_when_it_says_on() {
        assert!(parse_state("on\n"));
        assert!(parse_state("on"));
        assert!(!parse_state(""));
        assert!(!parse_state("off\n"));
        assert!(!parse_state("ON"));
        assert!(!parse_state("on please"));
        assert!(!parse_state("yes"));
    }

    #[test]
    fn rule_open_reads_the_state_file_and_defaults_to_closed() {
        let _g = LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let d = Dir::new();
        let f = d.0.join("phone-link");
        std::env::set_var("NIZHO_PHONE_STATE", &f);
        assert!(!rule_open(), "absent file means off");
        std::fs::write(&f, "on\n").unwrap();
        assert!(rule_open());
        std::fs::write(&f, "off\n").unwrap();
        assert!(!rule_open());
        std::env::remove_var("NIZHO_PHONE_STATE");
    }

    #[test]
    fn exit_codes_map_to_outcomes() {
        assert_eq!(outcome_of(Some(0), ""), Outcome::Done);
        assert_eq!(outcome_of(Some(126), ""), Outcome::Cancelled);
        assert_eq!(outcome_of(Some(127), ""), Outcome::Cancelled);
        assert_eq!(
            outcome_of(Some(1), "\nnizho-phone-link: could not write the rule\n"),
            Outcome::Failed("could not write the rule".into())
        );
        assert_eq!(outcome_of(Some(1), ""), Outcome::Failed("The helper did not finish.".into()));
        assert!(matches!(outcome_of(None, ""), Outcome::Failed(_)));
    }

    #[test]
    fn the_helper_runs_through_the_stand_in_and_reports_its_exit_code() {
        let _g = LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let d = Dir::new();
        fake(&d, r#"case "$1" in on) exit 0;; off) echo "nizho-phone-link: nft failed" >&2; exit 1;; *) exit 126;; esac"#);
        assert_eq!(run_helper("on"), Outcome::Done);
        assert_eq!(run_helper("off"), Outcome::Failed("nft failed".into()));
        assert_eq!(run_helper("status"), Outcome::Cancelled);
        std::env::remove_var("NIZHO_PHONE_HELPER");
    }

    #[test]
    fn the_switch_and_the_words_follow_what_is_true() {
        assert!(!switch_on(false, false));
        assert!(switch_on(true, true));
        assert!(switch_on(false, true), "a rule left open keeps the switch on");
        assert!(switch_on(true, false));
        assert_eq!(subtitle(false, false), "Off. Phones on your network cannot find this computer.");
        assert!(subtitle(true, true).starts_with("On. Phones on the same network can find this computer"));
        assert!(subtitle(true, true).ends_with("pair each phone yourself in the KDE Connect app."));
        assert_eq!(
            subtitle(false, true),
            "KDE Connect is stopped, but the firewall rule is still open because the password prompt was cancelled. Turn this off again to close it."
        );
        assert!(subtitle(true, false).contains("firewall rule is closed"));
    }

    #[test]
    fn turning_on_asks_for_the_rule_first_and_changes_nothing_else_if_refused() {
        for refused in [Outcome::Cancelled, Outcome::Failed("no".into())] {
            let calls = RefCell::new(Vec::new());
            let msg = apply(true, |a| { calls.borrow_mut().push(format!("helper {a}")); refused.clone() }, |on| { calls.borrow_mut().push(format!("daemon {on}")); true });
            assert_eq!(*calls.borrow(), vec!["helper on".to_string()], "the daemon must not start");
            assert!(msg.unwrap().contains("stays off"));
        }
        let calls = RefCell::new(Vec::new());
        let msg = apply(true, |a| { calls.borrow_mut().push(format!("helper {a}")); Outcome::Done }, |on| { calls.borrow_mut().push(format!("daemon {on}")); true });
        assert_eq!(*calls.borrow(), vec!["helper on".to_string(), "daemon true".to_string()]);
        assert_eq!(msg, None);
        assert_eq!(apply(true, |_| Outcome::Done, |_| false), Some(DAEMON_FAILED.to_string()));
    }

    #[test]
    fn turning_off_stops_the_daemon_first_then_closes_the_rule() {
        let calls = RefCell::new(Vec::new());
        let msg = apply(false, |a| { calls.borrow_mut().push(format!("helper {a}")); Outcome::Done }, |on| { calls.borrow_mut().push(format!("daemon {on}")); true });
        assert_eq!(*calls.borrow(), vec!["daemon false".to_string(), "helper off".to_string()]);
        assert_eq!(msg, None);
    }

    #[test]
    fn a_cancelled_prompt_on_off_leaves_the_rule_open_and_says_so_through_the_state() {
        let msg = apply(false, |_| Outcome::Cancelled, |_| true);
        assert_eq!(msg, None, "the state subtitle carries the wording");
        // daemon stopped, rule still open:
        assert!(switch_on(false, true));
        assert_eq!(subtitle(false, true), STOPPED_RULE_OPEN);
        let failed = apply(false, |_| Outcome::Failed("busy.".into()), |_| true).unwrap();
        assert!(failed.contains("busy.") && failed.contains("Turn this off again"));
    }
}
