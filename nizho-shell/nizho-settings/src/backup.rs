//! The Backup page's logic: it runs `nizho-backup` and turns its output into plain words.
//! Nothing here touches the screen, so it can be tested without one.

use std::path::Path;
use std::process::{Command, Stdio};

#[derive(Debug, Clone, PartialEq)]
pub struct Status {
    pub configured: bool,
    pub connected: bool,
    pub text: String,
}

impl Status {
    pub fn unknown() -> Self {
        Status { configured: false, connected: false, text: "Couldn't read the backup status.".into() }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct SetupDone {
    pub key: String,
    pub folder: String,
}

fn bin() -> String {
    std::env::var("NIZHO_BACKUP_BIN").ok().filter(|s| !s.is_empty()).unwrap_or_else(|| "nizho-backup".into())
}

/// Runs the tool. On failure returns the tool's own message, without its "nizho-backup: " prefix.
fn run(args: &[&str]) -> Result<String, String> {
    let out = Command::new(bin())
        .args(args)
        .stdin(Stdio::null())
        .output()
        .map_err(|_| "Backups are not available on this system.".to_string())?;
    if out.status.success() {
        return Ok(String::from_utf8_lossy(&out.stdout).into_owned());
    }
    let err = String::from_utf8_lossy(&out.stderr);
    let msg = err.lines().rev().find(|l| !l.trim().is_empty()).unwrap_or("The backup did not finish.");
    Err(msg.trim().trim_start_matches("nizho-backup:").trim().to_string())
}

pub fn parse_status(json: &str) -> Status {
    let v: serde_json::Value = match serde_json::from_str(json) {
        Ok(v) => v,
        Err(_) => return Status::unknown(),
    };
    let configured = v.get("configured").and_then(|x| x.as_bool()).unwrap_or(false);
    let connected = v.get("connected").and_then(|x| x.as_bool()).unwrap_or(false);
    let text = v.get("text").and_then(|x| x.as_str()).unwrap_or("").to_string();
    if text.is_empty() {
        return Status::unknown();
    }
    Status { configured, connected, text }
}

pub fn status() -> Status {
    run(&["status", "--json"]).map(|s| parse_status(&s)).unwrap_or_else(|e| Status { configured: false, connected: false, text: e })
}

/// The recovery key is the indented line after the "Recovery key" line.
pub fn parse_recovery_key(stdout: &str) -> Option<String> {
    let mut lines = stdout.lines();
    while let Some(l) = lines.next() {
        if l.starts_with("Recovery key") {
            return lines.next().map(|k| k.trim().to_string()).filter(|k| !k.is_empty());
        }
    }
    None
}

pub fn setup(folder: &Path) -> Result<SetupDone, String> {
    let f = folder.to_string_lossy().into_owned();
    let out = run(&["setup", "--to", &f])?;
    let key = parse_recovery_key(&out).ok_or("The backup was set up, but the recovery key could not be read. Run \"nizho-backup setup\" help in a terminal.")?;
    Ok(SetupDone { key, folder: f })
}

pub fn backup_now() -> Result<(), String> {
    run(&["now"]).map(|_| ())
}

/// A path inside the home folder, written relative to it, such as Documents/report.odt.
pub fn valid_restore_path(p: &str) -> bool {
    let p = p.trim();
    !p.is_empty() && !p.starts_with('/') && !p.starts_with('~') && !p.split('/').any(|c| c == "..")
}

pub fn restore(path: &str, target: &Path) -> Result<String, String> {
    if !valid_restore_path(path) {
        return Err("Write the file or folder as it appears in your home folder, for example Documents/report.odt.".into());
    }
    let t = target.to_string_lossy().into_owned();
    let out = run(&["restore", path.trim(), "--to", &t])?;
    Ok(out.lines().find(|l| l.starts_with("Restored to ")).unwrap_or("Restored.").to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;
    use std::sync::Mutex;

    // The tests below point NIZHO_BACKUP_BIN at a stand-in script, so they cannot run side by side.
    static LOCK: Mutex<()> = Mutex::new(());

    fn fake(script: &str) -> (tempdir::Dir, std::sync::MutexGuard<'static, ()>) {
        let g = LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let d = tempdir::Dir::new();
        let p = d.path().join("nizho-backup");
        std::fs::write(&p, format!("#!/bin/sh\n{script}\n")).unwrap();
        std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o755)).unwrap();
        std::env::set_var("NIZHO_BACKUP_BIN", &p);
        (d, g)
    }

    mod tempdir {
        pub struct Dir(std::path::PathBuf);
        impl Dir {
            pub fn new() -> Self {
                let p = std::env::temp_dir().join(format!("nizho-backup-test-{}-{:?}", std::process::id(), std::thread::current().id()));
                let _ = std::fs::remove_dir_all(&p);
                std::fs::create_dir_all(&p).unwrap();
                Dir(p)
            }
            pub fn path(&self) -> &std::path::Path { &self.0 }
        }
        impl Drop for Dir { fn drop(&mut self) { let _ = std::fs::remove_dir_all(&self.0); } }
    }

    #[test]
    fn status_json_becomes_plain_words() {
        let s = parse_status(r#"{"configured": true, "connected": true, "text": "Last backup: 2 hours ago."}"#);
        assert_eq!(s, Status { configured: true, connected: true, text: "Last backup: 2 hours ago.".into() });
        let n = parse_status(r#"{"configured": false, "text": "Backups are not set up."}"#);
        assert!(!n.configured && !n.connected);
    }

    #[test]
    fn unreadable_status_says_so_instead_of_claiming_something() {
        assert_eq!(parse_status("not json"), Status::unknown());
        assert_eq!(parse_status(r#"{"configured": true}"#), Status::unknown());
    }

    #[test]
    fn recovery_key_is_the_line_after_its_heading() {
        let out = "Backups are set up.\nBackup folder: /x\nRecovery key (write this down or keep a copy somewhere other than this computer):\n  abc_DEF-123\nIf this computer is lost...\n";
        assert_eq!(parse_recovery_key(out), Some("abc_DEF-123".into()));
        assert_eq!(parse_recovery_key("Backups are set up.\n"), None);
    }

    #[test]
    fn restore_paths_must_stay_inside_the_home_folder() {
        assert!(valid_restore_path("Documents/report.odt"));
        assert!(valid_restore_path("  Pictures  "));
        for bad in ["", "  ", "/etc/passwd", "~/x", "../x", "a/../../b"] {
            assert!(!valid_restore_path(bad), "{bad:?}");
        }
    }

    #[test]
    fn a_failed_run_shows_the_tools_own_message_without_its_prefix() {
        let (_d, _g) = fake("echo 'nizho-backup: The backup drive is not connected: /run/media/x' >&2; exit 1");
        assert_eq!(backup_now(), Err("The backup drive is not connected: /run/media/x".into()));
    }

    #[test]
    fn a_missing_tool_is_reported_plainly() {
        let _g = LOCK.lock().unwrap_or_else(|e| e.into_inner());
        std::env::set_var("NIZHO_BACKUP_BIN", "/nonexistent/nizho-backup");
        assert_eq!(backup_now(), Err("Backups are not available on this system.".into()));
        assert!(!status().configured);
    }

    #[test]
    fn setup_returns_the_key_and_folder() {
        let (_d, _g) = fake("printf 'Backups are set up.\\nBackup folder: %s\\nRecovery key (keep a copy):\\n  KEY-1\\nOK\\n' \"$3\"");
        let done = setup(Path::new("/media/drive/nizho-backup")).unwrap();
        assert_eq!(done, SetupDone { key: "KEY-1".into(), folder: "/media/drive/nizho-backup".into() });
    }

    #[test]
    fn restore_reports_where_the_file_went_and_refuses_bad_paths_without_running_anything() {
        let (d, _g) = fake("echo ran >> \"$(dirname \"$0\")/ran\"; echo 'Restored to /tmp/r/home/u/Documents/a.txt'; echo 'Your existing files were not touched.'");
        assert!(restore("/etc/passwd", Path::new("/tmp/r")).is_err());
        assert!(restore("../x", Path::new("/tmp/r")).is_err());
        assert!(!d.path().join("ran").exists(), "a refused path never reaches the tool");
        assert_eq!(restore("Documents/a.txt", Path::new("/tmp/r")).unwrap(), "Restored to /tmp/r/home/u/Documents/a.txt");
        assert!(d.path().join("ran").exists());
    }
}
