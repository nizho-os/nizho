//! The Backup page's logic: it runs `nizho-backup` and turns its output into plain words.
//! Nothing here touches the screen, so it can be tested without one.

use std::path::Path;
use std::process::{Command, Stdio};

/// How many older copies nizho-backup keeps, in words. Must match KEEP in backup/nizho-backup
/// (--keep-daily 7 --keep-weekly 4 --keep-monthly 6); a test reads that file to check.
pub const KEEP_TEXT: &str = "One a day for the last 7 days, one a week for 4 weeks, one a month for 6 months";

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

/// Restores from one chosen backup (its id), or the newest when there is none. The path goes after
/// `--`, so a name that starts with a dash is never read as an option.
pub fn restore(path: &str, target: &Path, snapshot: Option<&str>) -> Result<String, String> {
    if !valid_restore_path(path) {
        return Err("Write the file or folder as it appears in your home folder, for example Documents/report.odt.".into());
    }
    let t = target.to_string_lossy().into_owned();
    let mut args = vec!["restore", "--to", &t];
    if let Some(id) = snapshot {
        args.extend(["--snapshot", id]);
    }
    args.extend(["--", path.trim()]);
    let out = run(&args)?;
    Ok(out.lines().find(|l| l.starts_with("Restored to ")).unwrap_or("Restored.").to_string())
}

// ── Browsing a backup ───────────────────────────────────────────────────────

/// One thing inside a folder of a backup.
#[derive(Debug, Clone, PartialEq)]
pub struct Entry {
    pub name: String,
    pub is_dir: bool,
    pub size: u64,
    pub mtime: String,
}

/// One saved backup: its id and when it was made, as the tool prints it.
#[derive(Debug, Clone, PartialEq)]
pub struct Snap {
    pub id: String,
    pub time: String,
}

pub fn parse_entries(json: &str) -> Result<Vec<Entry>, String> {
    let v: serde_json::Value = serde_json::from_str(json).map_err(|_| "The list of saved files could not be read.".to_string())?;
    let items = v.as_array().ok_or("The list of saved files could not be read.")?;
    Ok(items
        .iter()
        .filter_map(|e| {
            let name = e.get("name")?.as_str()?.to_string();
            if name.is_empty() || name.contains('/') {
                return None;
            }
            Some(Entry {
                name,
                is_dir: e.get("type").and_then(|t| t.as_str()) == Some("dir"),
                size: e.get("size").and_then(|s| s.as_u64()).unwrap_or(0),
                mtime: e.get("mtime").and_then(|m| m.as_str()).unwrap_or("").to_string(),
            })
        })
        .collect())
}

/// Newest first, as the tool sorts them.
pub fn parse_snaps(json: &str) -> Result<Vec<Snap>, String> {
    let v: serde_json::Value = serde_json::from_str(json).map_err(|_| "The list of backups could not be read.".to_string())?;
    let items = v.as_array().ok_or("The list of backups could not be read.")?;
    Ok(items
        .iter()
        .filter_map(|s| Some(Snap { id: s.get("id")?.as_str()?.to_string(), time: s.get("time")?.as_str()?.to_string() }))
        .collect())
}

pub fn snapshots() -> Result<Vec<Snap>, String> {
    parse_snaps(&run(&["snapshots", "--json"])?)
}

/// A folder inside the home folder, written relative to it. The empty string is the home folder.
pub fn valid_browse_path(p: &str) -> bool {
    !p.starts_with('/') && !p.split('/').any(|c| c == "..")
}

/// What is directly inside `path` ("" for the home folder) in the backup `snapshot`.
/// The path goes after `--`, so a folder name that starts with a dash is not read as an option.
pub fn list(path: &str, snapshot: &str) -> Result<Vec<Entry>, String> {
    if !valid_browse_path(path) {
        return Err("That folder is outside your home folder.".into());
    }
    let p = if path.is_empty() { "." } else { path };
    parse_entries(&run(&["ls", "--json", "--snapshot", snapshot, "--", p])?)
}

pub fn child_path(folder: &str, name: &str) -> String {
    if folder.is_empty() { name.to_string() } else { format!("{folder}/{name}") }
}

pub fn parent_path(folder: &str) -> String {
    folder.rsplit_once('/').map(|(p, _)| p.to_string()).unwrap_or_default()
}

fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let doy = (153 * (if m > 2 { m - 3 } else { m + 9 }) + 2) / 5 + d - 1;
    era * 146097 + yoe * 365 + yoe / 4 - yoe / 100 + doy - 719468
}

/// "Today 14:05", "Yesterday 09:30", "3 Nov, 14:05" or, from another year, "3 Nov 2025, 14:05".
/// `today` is (year, month, day) of the current day. `iso` is the time as the tool prints it.
pub fn friendly_time(iso: &str, today: (i32, u32, u32)) -> String {
    const MONTHS: [&str; 12] = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    let num = |a: usize, b: usize| iso.get(a..b).and_then(|x| x.parse::<i64>().ok());
    let (Some(y), Some(mo), Some(d), Some(h), Some(mi)) = (num(0, 4), num(5, 7), num(8, 10), num(11, 13), num(14, 16)) else {
        return "Unknown time".into();
    };
    if !(1..=12).contains(&mo) || !(1..=31).contains(&d) {
        return "Unknown time".into();
    }
    let clock = format!("{h:02}:{mi:02}");
    let ago = days_from_civil(today.0 as i64, today.1 as i64, today.2 as i64) - days_from_civil(y, mo, d);
    match ago {
        0 => format!("Today {clock}"),
        1 => format!("Yesterday {clock}"),
        _ if y == today.0 as i64 => format!("{d} {}, {clock}", MONTHS[(mo - 1) as usize]),
        _ => format!("{d} {} {y}, {clock}", MONTHS[(mo - 1) as usize]),
    }
}

pub fn friendly_size(n: u64) -> String {
    if n < 1000 {
        return if n == 1 { "1 byte".into() } else { format!("{n} bytes") };
    }
    let mut v = n as f64 / 1000.0;
    for unit in ["KB", "MB", "GB"] {
        if v < 1000.0 || unit == "GB" {
            return format!("{v:.1} {unit}");
        }
        v /= 1000.0;
    }
    unreachable!()
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
        assert!(restore("/etc/passwd", Path::new("/tmp/r"), None).is_err());
        assert!(restore("../x", Path::new("/tmp/r"), None).is_err());
        assert!(!d.path().join("ran").exists(), "a refused path never reaches the tool");
        assert_eq!(restore("Documents/a.txt", Path::new("/tmp/r"), None).unwrap(), "Restored to /tmp/r/home/u/Documents/a.txt");
        assert!(d.path().join("ran").exists());
    }

    #[test]
    fn entries_parse_from_the_tools_json() {
        let j = r#"[{"name":"Old","type":"dir","size":0,"mtime":"2026-11-09T10:00:00+05:30"},
                    {"name":"Ärger notes.txt","type":"file","size":2,"mtime":"2026-11-09T10:01:00+05:30"},
                    {"type":"file"}, {"name":"a/b","type":"file"}]"#;
        let e = parse_entries(j).unwrap();
        assert_eq!(e.len(), 2, "entries without a usable name are skipped");
        assert_eq!(e[0], Entry { name: "Old".into(), is_dir: true, size: 0, mtime: "2026-11-09T10:00:00+05:30".into() });
        assert_eq!(e[1].name, "Ärger notes.txt");
        assert!(!e[1].is_dir && e[1].size == 2);
        assert_eq!(parse_entries("[]").unwrap(), vec![]);
        assert!(parse_entries("oops").is_err());
        assert!(parse_entries(r#"{"name":"x"}"#).is_err());
    }

    #[test]
    fn snapshots_parse_in_the_order_given() {
        let s = parse_snaps(r#"[{"id":"b2","time":"2026-11-09T10:00:00+05:30"},{"id":"a1","time":"2026-11-08T10:00:00+05:30"},{"id":"x"}]"#).unwrap();
        assert_eq!(s.iter().map(|x| x.id.as_str()).collect::<Vec<_>>(), ["b2", "a1"]);
        assert!(parse_snaps("nope").is_err());
    }

    #[test]
    fn browse_paths_stay_inside_the_home_folder() {
        assert!(valid_browse_path("") && valid_browse_path("Documents/Old") && valid_browse_path("-rf") && valid_browse_path("~x"));
        for bad in ["/etc", "../x", "a/../b", ".."] {
            assert!(!valid_browse_path(bad), "{bad:?}");
        }
        assert_eq!(child_path("", "Documents"), "Documents");
        assert_eq!(child_path("Documents", "Old"), "Documents/Old");
        assert_eq!(parent_path("Documents/Old"), "Documents");
        assert_eq!(parent_path("Documents"), "");
        assert_eq!(parent_path(""), "");
    }

    #[test]
    fn dates_are_in_plain_words() {
        let today = (2026, 11, 10);
        assert_eq!(friendly_time("2026-11-10T14:05:33.5+05:30", today), "Today 14:05");
        assert_eq!(friendly_time("2026-11-09T09:30:00Z", today), "Yesterday 09:30");
        assert_eq!(friendly_time("2026-11-03T07:00:00Z", today), "3 Nov, 07:00");
        assert_eq!(friendly_time("2025-12-31T23:59:00Z", today), "31 Dec 2025, 23:59");
        assert_eq!(friendly_time("2026-10-31T10:00:00Z", (2026, 11, 1)), "Yesterday 10:00", "across a month end");
        assert_eq!(friendly_time("garbage", today), "Unknown time");
        assert_eq!(friendly_time("2026-13-01T10:00:00Z", today), "Unknown time");
    }

    #[test]
    fn sizes_are_in_plain_words() {
        assert_eq!(friendly_size(0), "0 bytes");
        assert_eq!(friendly_size(1), "1 byte");
        assert_eq!(friendly_size(999), "999 bytes");
        assert_eq!(friendly_size(5000), "5.0 KB");
        assert_eq!(friendly_size(2_500_000), "2.5 MB");
    }

    #[test]
    fn listing_asks_the_tool_with_the_path_after_double_dash() {
        let (d, _g) = fake("echo \"$@\" > \"$(dirname \"$0\")/args\"; echo '[{\"name\":\"a.txt\",\"type\":\"file\",\"size\":3,\"mtime\":\"2026-11-09T10:00:00Z\"}]'");
        let e = list("-rf/sub", "abc123").unwrap();
        assert_eq!(e.len(), 1);
        assert_eq!(std::fs::read_to_string(d.path().join("args")).unwrap().trim(), "ls --json --snapshot abc123 -- -rf/sub");
        list("", "abc123").unwrap();
        assert_eq!(std::fs::read_to_string(d.path().join("args")).unwrap().trim(), "ls --json --snapshot abc123 -- .");
    }

    #[test]
    fn a_bad_listing_path_never_reaches_the_tool() {
        let (d, _g) = fake("echo ran > \"$(dirname \"$0\")/ran\"; echo '[]'");
        assert!(list("../x", "abc").is_err());
        assert!(list("/etc", "abc").is_err());
        assert!(!d.path().join("ran").exists());
    }

    #[test]
    fn listing_errors_show_the_tools_own_words() {
        let (d, _g) = fake("echo \"nizho-backup: $(cat \"$(dirname \"$0\")/msg\")\" >&2; exit 1");
        std::fs::write(d.path().join("msg"), "Documents/x was not found in that backup.").unwrap();
        assert_eq!(list("Documents/x", "abc"), Err("Documents/x was not found in that backup.".into()));
        std::fs::write(d.path().join("msg"), "The backup drive is not connected: /run/media/x").unwrap();
        assert_eq!(snapshots(), Err("The backup drive is not connected: /run/media/x".into()));
    }

    #[test]
    fn restoring_from_a_chosen_backup_passes_its_id_and_the_path_after_double_dash() {
        let (d, _g) = fake("echo \"$@\" > \"$(dirname \"$0\")/args\"; echo 'Restored to /tmp/r/x'");
        restore("Documents/a.txt", Path::new("/tmp/r"), Some("abc123")).unwrap();
        assert_eq!(std::fs::read_to_string(d.path().join("args")).unwrap().trim(), "restore --to /tmp/r --snapshot abc123 -- Documents/a.txt");
        restore("Pictures", Path::new("/tmp/r"), None).unwrap();
        assert_eq!(std::fs::read_to_string(d.path().join("args")).unwrap().trim(), "restore --to /tmp/r -- Pictures");
    }

    #[test]
    fn the_kept_copies_text_matches_what_nizho_backup_keeps() {
        let tool = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/../../backup/nizho-backup"))
            .expect("backup/nizho-backup");
        let line = tool.lines().find(|l| l.starts_with("KEEP = ")).expect("KEEP line");
        assert_eq!(line, r#"KEEP = ["--keep-daily", "7", "--keep-weekly", "4", "--keep-monthly", "6"]"#);
        assert!(KEEP_TEXT.contains("7 days") && KEEP_TEXT.contains("4 weeks") && KEEP_TEXT.contains("6 months"));
    }
}
