//! Terminal workflow: user installation, visible failures, literal app ID argv.
pub const SCRIPT: &str = r#"
nizho install "$1"
result=$?
case "$result" in
    0) printf '%s\n' 'Installation finished.' ;;
    3) printf '%s\n' 'Nothing was installed.' ;;
    *) printf 'Installation failed (exit %s). Review the error above.\n' "$result" ;;
esac
printf 'Press Enter to close… '
IFS= read -r reply
exit "$result"
"#;

/// Remove one app. `nizho remove` shows its own question; "n" there exits 3.
pub const REMOVE_SCRIPT: &str = r#"
nizho remove "$1"
result=$?
case "$result" in
    0) printf '%s\n' 'Removal finished.' ;;
    3) printf '%s\n' 'Nothing was removed.' ;;
    *) printf 'Removal failed (exit %s). Review the error above.\n' "$result" ;;
esac
printf 'Press Enter to close… '
IFS= read -r reply
exit "$result"
"#;

/// Update every app the user installed.
pub const UPDATE_SCRIPT: &str = r#"
nizho update
result=$?
case "$result" in
    0) printf '%s\n' 'Update finished.' ;;
    *) printf 'Update failed (exit %s). Review the error above.\n' "$result" ;;
esac
printf 'Press Enter to close… '
IFS= read -r reply
exit "$result"
"#;

#[cfg(test)]
mod tests {
    use super::*;
    use std::process::{Command, Stdio};
    // `nizho` is the Nizho install command (apps/nizho-install): it shows what the app can
    // reach, asks, and installs for the user's account only. Here it is a stand-in.
    fn run(nizho_exit: u8, id: &str) -> std::process::Output {
        let mock = format!(r#"nizho() {{
            printf 'call: <%s>\n' "$@"
            return {nizho_exit}
        }}
        {SCRIPT}"#);
        Command::new("sh").args(["-c", &mock, "nizho-capsule-install", id])
            .stdin(Stdio::null()).output().unwrap()
    }
    #[test]
    fn installs_through_the_nizho_command() {
        let o = run(0, "org.example.App");
        assert!(o.status.success());
        let s = String::from_utf8(o.stdout).unwrap();
        assert!(s.contains("<install>") && s.contains("<org.example.App>"));
        assert!(s.contains("Installation finished"));
    }
    #[test]
    fn saying_no_is_reported_as_nothing_installed() {
        let o = run(3, "org.example.App");
        assert_eq!(o.status.code(), Some(3));
        let s = String::from_utf8(o.stdout).unwrap();
        assert!(s.contains("Nothing was installed"));
        assert!(!s.contains("Installation finished") && !s.contains("Installation failed"));
    }
    #[test]
    fn a_failed_install_is_visible_and_nonzero() {
        let o = run(42, "org.example.App");
        assert_eq!(o.status.code(), Some(42));
        let s = String::from_utf8(o.stdout).unwrap();
        assert!(s.contains("Installation failed (exit 42)"));
        assert!(!s.contains("Installation finished"));
    }
    #[test]
    fn app_id_is_literal_not_shell_source() {
        let id = "org.example.App; printf INJECTED";
        let o = run(0, id);
        let s = String::from_utf8(o.stdout).unwrap();
        assert!(s.contains(&format!("<{id}>")));
        assert!(!s.lines().any(|line| line == "INJECTED"));
    }

    fn run_script(script: &str, nizho_exit: u8, id: &str) -> std::process::Output {
        let mock = format!(r#"nizho() {{
            printf 'call: <%s>\n' "$@"
            return {nizho_exit}
        }}
        {script}"#);
        Command::new("sh").args(["-c", &mock, "nizho-capsule-script", id])
            .stdin(Stdio::null()).output().unwrap()
    }
    #[test]
    fn removes_through_the_nizho_command() {
        let o = run_script(REMOVE_SCRIPT, 0, "org.example.App");
        assert!(o.status.success());
        let s = String::from_utf8(o.stdout).unwrap();
        assert!(s.contains("<remove>") && s.contains("<org.example.App>"));
        assert!(s.contains("Removal finished"));
    }
    #[test]
    fn saying_no_to_a_removal_is_reported_as_nothing_removed() {
        let o = run_script(REMOVE_SCRIPT, 3, "org.example.App");
        assert_eq!(o.status.code(), Some(3));
        let s = String::from_utf8(o.stdout).unwrap();
        assert!(s.contains("Nothing was removed"));
        assert!(!s.contains("Removal finished") && !s.contains("Removal failed"));
    }
    #[test]
    fn a_failed_removal_is_visible_and_nonzero() {
        let o = run_script(REMOVE_SCRIPT, 9, "org.example.App");
        assert_eq!(o.status.code(), Some(9));
        assert!(String::from_utf8(o.stdout).unwrap().contains("Removal failed (exit 9)"));
    }
    #[test]
    fn removal_app_id_is_literal_not_shell_source() {
        let id = "org.example.App; printf INJECTED";
        let o = run_script(REMOVE_SCRIPT, 0, id);
        let s = String::from_utf8(o.stdout).unwrap();
        assert!(s.contains(&format!("<{id}>")));
        assert!(!s.lines().any(|line| line == "INJECTED"));
    }
    #[test]
    fn updates_through_the_nizho_command_with_no_app_name() {
        let o = run_script(UPDATE_SCRIPT, 0, "ignored");
        let s = String::from_utf8(o.stdout).unwrap();
        assert!(s.contains("<update>") && !s.contains("<ignored>"));
        assert!(s.contains("Update finished"));
    }
    #[test]
    fn a_failed_update_is_visible_and_nonzero() {
        let o = run_script(UPDATE_SCRIPT, 1, "x");
        assert_eq!(o.status.code(), Some(1));
        let s = String::from_utf8(o.stdout).unwrap();
        assert!(s.contains("Update failed (exit 1)") && !s.contains("Update finished"));
    }
}
