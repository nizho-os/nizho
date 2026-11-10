use std::collections::HashSet;
use std::io::Write;
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt, PermissionsExt};
use std::path::Path;
use std::sync::{Mutex, OnceLock};
use std::time::{SystemTime, UNIX_EPOCH};

const LOG_DIR: &str = "/var/log/nizho";
const LOG_NAME: &str = "privacy.jsonl";
/// The one older file that is kept. The next rotation replaces it.
const PREVIOUS_NAME: &str = "privacy.jsonl.1";

/// World-readable on purpose. The privacy tray and the dashboard run as the
/// desktop user and read this file directly. It holds setting names, fixed
/// event texts and NetworkManager connection names. Tightening it to 0640
/// needs a reader group or a D-Bus method for those two programs first.
const LOG_MODE: u32 = 0o644;
const DIR_MODE: u32 = 0o755;

/// The current file is rotated before a line would take it past this size, so
/// the two files together stay under about twice this.
const MAX_LOG_BYTES: u64 = 5 * 1024 * 1024;

/// The step that failed.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
enum Failure {
    CreateDir,
    Rotate,
    Open,
    SetMode,
    Write,
}

impl Failure {
    fn what(self) -> &'static str {
        match self {
            Failure::CreateDir => "could not create the event log directory",
            Failure::Rotate => "could not rotate the event log; it keeps growing",
            Failure::Open => "could not open the event log; the event was not recorded",
            Failure::SetMode => "could not set the event log permissions",
            Failure::Write => "could not write to the event log; the event was not recorded",
        }
    }
}

fn reported() -> &'static Mutex<HashSet<Failure>> {
    static SET: OnceLock<Mutex<HashSet<Failure>>> = OnceLock::new();
    SET.get_or_init(|| Mutex::new(HashSet::new()))
}

/// Which of this call's failures to put in the journal. A class is reported
/// once, then stays quiet while it keeps failing. It is reported again only
/// after a call in between went through without it.
fn newly_failed(seen: &mut HashSet<Failure>, failed: &[Failure]) -> Vec<Failure> {
    let new: Vec<Failure> = failed.iter().copied().filter(|f| seen.insert(*f)).collect();
    seen.retain(|f| failed.contains(f));
    new
}

/// Records one event. Never panics and never stops the daemon: a failure is
/// written to the journal instead (see `newly_failed`).
pub fn log_event(cat: &str, ev: &str, msg: &str) {
    let ts = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let line = format_line(ts, cat, ev, msg);
    let mut failures: Vec<(Failure, std::io::Error)> = Vec::new();
    append(Path::new(LOG_DIR), MAX_LOG_BYTES, &line, &mut |f, e| failures.push((f, e)));

    let classes: Vec<Failure> = failures.iter().map(|(f, _)| *f).collect();
    let mut seen = reported().lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    for class in newly_failed(&mut seen, &classes) {
        if let Some((_, error)) = failures.iter().find(|(f, _)| *f == class) {
            tracing::warn!(%error, "{}", class.what());
        }
    }
}

/// One JSON object per line, as the tray and the dashboard parse it.
fn format_line(ts: u64, cat: &str, ev: &str, msg: &str) -> String {
    format!(
        "{{\"ts\":{ts},\"cat\":\"{}\",\"ev\":\"{}\",\"msg\":\"{}\"}}\n",
        json_escape(cat),
        json_escape(ev),
        json_escape(msg)
    )
}

/// Event texts can carry outside names (a network connection name), so quotes,
/// backslashes and control characters must not break the line.
fn json_escape(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for c in text.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out
}

/// Appends one line to `dir/privacy.jsonl`. Returns whether the line was
/// written. Every failed step is passed to `report`; later steps still run
/// where that can save the event.
fn append(dir: &Path, max_bytes: u64, line: &str, report: &mut dyn FnMut(Failure, std::io::Error)) -> bool {
    if let Err(e) = std::fs::DirBuilder::new().recursive(true).mode(DIR_MODE).create(dir) {
        report(Failure::CreateDir, e);
    }
    let current = dir.join(LOG_NAME);

    // Rotate first, so the current file does not pass the cap. An empty file is
    // never rotated: one oversized line must not wipe the previous file.
    if let Ok(meta) = std::fs::metadata(&current) {
        if meta.len() > 0 && meta.len().saturating_add(line.len() as u64) > max_bytes {
            if let Err(e) = std::fs::rename(&current, dir.join(PREVIOUS_NAME)) {
                report(Failure::Rotate, e);
            }
        }
    }

    // The mode is given at creation, so a new file is never wider than
    // intended, even for a moment. The process umask can only narrow it.
    let mut file = match std::fs::OpenOptions::new().create(true).append(true).mode(LOG_MODE).open(&current) {
        Ok(file) => file,
        Err(e) => {
            report(Failure::Open, e);
            return false;
        }
    };
    // An older file, or one made under a narrow umask, gets the intended mode.
    match file.metadata() {
        Ok(meta) if meta.permissions().mode() & 0o7777 != LOG_MODE => {
            if let Err(e) = file.set_permissions(std::fs::Permissions::from_mode(LOG_MODE)) {
                report(Failure::SetMode, e);
            }
        }
        Ok(_) => (),
        Err(e) => report(Failure::SetMode, e),
    }
    match file.write_all(line.as_bytes()) {
        Ok(()) => true,
        Err(e) => {
            report(Failure::Write, e);
            false
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicU32, Ordering};

    /// A fresh private directory under the system temp dir, removed on drop.
    struct TempDir(PathBuf);
    impl TempDir {
        fn new() -> Self {
            static NEXT: AtomicU32 = AtomicU32::new(0);
            let path = std::env::temp_dir().join(format!(
                "nizhod-log-test-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::SeqCst)
            ));
            std::fs::DirBuilder::new().mode(0o700).create(&path).expect("temp dir");
            Self(path)
        }
    }
    impl Drop for TempDir {
        fn drop(&mut self) {
            if let Err(e) = std::fs::remove_dir_all(&self.0) {
                eprintln!("could not remove {}: {e}", self.0.display());
            }
        }
    }

    fn write(dir: &Path, max: u64, line: &str) -> (bool, Vec<Failure>) {
        let mut failures = Vec::new();
        let written = append(dir, max, line, &mut |f, _| failures.push(f));
        (written, failures)
    }

    fn mode(path: &Path) -> u32 {
        std::fs::metadata(path).unwrap().permissions().mode() & 0o7777
    }

    #[test]
    fn creates_the_directory_and_file_with_the_intended_mode_and_appends() {
        let tmp = TempDir::new();
        let dir = tmp.0.join("a/nizho");
        assert_eq!(write(&dir, MAX_LOG_BYTES, "one\n"), (true, vec![]));
        assert_eq!(write(&dir, MAX_LOG_BYTES, "two\n"), (true, vec![]));
        assert_eq!(std::fs::read_to_string(dir.join(LOG_NAME)).unwrap(), "one\ntwo\n");
        assert_eq!(mode(&dir.join(LOG_NAME)), LOG_MODE);
        assert!(!dir.join(PREVIOUS_NAME).exists());
    }

    #[test]
    fn an_existing_file_with_another_mode_is_brought_to_the_intended_mode() {
        let tmp = TempDir::new();
        let file = tmp.0.join(LOG_NAME);
        for wrong in [0o600, 0o666] {
            std::fs::write(&file, "old\n").unwrap();
            std::fs::set_permissions(&file, std::fs::Permissions::from_mode(wrong)).unwrap();
            assert_eq!(write(&tmp.0, MAX_LOG_BYTES, "new\n"), (true, vec![]));
            assert_eq!(mode(&file), LOG_MODE, "from {wrong:o}");
            assert_eq!(std::fs::read_to_string(&file).unwrap(), "old\nnew\n");
        }
    }

    #[test]
    fn rotates_at_the_cap_and_keeps_exactly_one_previous_file() {
        let tmp = TempDir::new();
        let (current, previous) = (tmp.0.join(LOG_NAME), tmp.0.join(PREVIOUS_NAME));
        // Cap of 10 bytes, 4-byte lines: two lines fit, the third rotates.
        for line in ["aaa\n", "bbb\n"] {
            assert_eq!(write(&tmp.0, 10, line), (true, vec![]));
        }
        assert!(!previous.exists());
        assert_eq!(write(&tmp.0, 10, "ccc\n"), (true, vec![]));
        assert_eq!(std::fs::read_to_string(&previous).unwrap(), "aaa\nbbb\n");
        assert_eq!(std::fs::read_to_string(&current).unwrap(), "ccc\n");
        assert_eq!(mode(&current), LOG_MODE);
        assert_eq!(mode(&previous), LOG_MODE);
        // The next rotation replaces the previous file; nothing older is kept.
        for line in ["ddd\n", "eee\n"] {
            assert_eq!(write(&tmp.0, 10, line), (true, vec![]));
        }
        assert_eq!(std::fs::read_to_string(&previous).unwrap(), "ccc\nddd\n");
        assert_eq!(std::fs::read_to_string(&current).unwrap(), "eee\n");
        let mut names: Vec<_> = std::fs::read_dir(&tmp.0).unwrap()
            .map(|e| e.unwrap().file_name().into_string().unwrap()).collect();
        names.sort();
        assert_eq!(names, [LOG_NAME, PREVIOUS_NAME]);
        for path in [&current, &previous] {
            assert!(std::fs::metadata(path).unwrap().len() <= 10);
        }
    }

    #[test]
    fn a_line_longer_than_the_cap_is_still_written_and_keeps_the_previous_file() {
        let tmp = TempDir::new();
        assert_eq!(write(&tmp.0, 4, "aa\n"), (true, vec![]));
        assert_eq!(write(&tmp.0, 4, "a long line\n"), (true, vec![]));
        assert_eq!(std::fs::read_to_string(tmp.0.join(PREVIOUS_NAME)).unwrap(), "aa\n");
        assert_eq!(std::fs::read_to_string(tmp.0.join(LOG_NAME)).unwrap(), "a long line\n");
    }

    #[test]
    fn failures_are_reported_by_class_and_do_not_panic() {
        let tmp = TempDir::new();
        // The log directory path is a regular file: nothing can be created.
        let blocked = tmp.0.join("not-a-dir");
        std::fs::write(&blocked, "").unwrap();
        assert_eq!(write(&blocked, MAX_LOG_BYTES, "x\n"), (false, vec![Failure::CreateDir, Failure::Open]));

        // The log path is a directory: opening it for append fails.
        let dir = tmp.0.join("d");
        std::fs::create_dir_all(dir.join(LOG_NAME)).unwrap();
        assert_eq!(write(&dir, MAX_LOG_BYTES, "x\n"), (false, vec![Failure::Open]));

        // The previous-file path is a non-empty directory: rotation fails, the
        // event is still appended to the current file.
        let dir = tmp.0.join("r");
        std::fs::create_dir_all(dir.join(PREVIOUS_NAME).join("keep")).unwrap();
        assert_eq!(write(&dir, 4, "aaa\n"), (true, vec![]));
        assert_eq!(write(&dir, 4, "bbb\n"), (true, vec![Failure::Rotate]));
        assert_eq!(std::fs::read_to_string(dir.join(LOG_NAME)).unwrap(), "aaa\nbbb\n");
    }

    #[test]
    fn a_failure_class_is_reported_once_until_it_clears() {
        let mut seen = HashSet::new();
        let open = [Failure::CreateDir, Failure::Open];
        assert_eq!(newly_failed(&mut seen, &open), open);
        assert!(newly_failed(&mut seen, &open).is_empty(), "same failure, already reported");
        assert_eq!(newly_failed(&mut seen, &[Failure::Open, Failure::Write]), [Failure::Write]);
        assert!(newly_failed(&mut seen, &[]).is_empty());
        assert!(seen.is_empty(), "a clean write re-arms every class");
        assert_eq!(newly_failed(&mut seen, &[Failure::Open]), [Failure::Open]);
    }

    #[test]
    fn every_failure_class_has_its_own_plain_text() {
        let all = [Failure::CreateDir, Failure::Rotate, Failure::Open, Failure::SetMode, Failure::Write];
        let texts: HashSet<_> = all.iter().map(|f| f.what()).collect();
        assert_eq!(texts.len(), all.len());
    }

    #[test]
    fn lines_stay_one_json_object_whatever_the_text_holds() {
        assert_eq!(
            format_line(7, "dns", "exception", "plain"),
            "{\"ts\":7,\"cat\":\"dns\",\"ev\":\"exception\",\"msg\":\"plain\"}\n"
        );
        let line = format_line(7, "dns", "exception", "Cafe \"Free\" \\ WiFi\nx\u{1}\ty");
        assert_eq!(
            line,
            "{\"ts\":7,\"cat\":\"dns\",\"ev\":\"exception\",\"msg\":\"Cafe \\\"Free\\\" \\\\ WiFi\\nx\\u0001\\ty\"}\n"
        );
        assert_eq!(line.matches('\n').count(), 1);
    }
}
