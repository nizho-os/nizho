#[derive(Clone, Copy)]
pub enum Operation {
    Check,
    Upgrade,
    Rollback,
}

/// The filesystem that holds every system version: the ostree repository is /sysroot/ostree/repo and
/// bootc's image store is under /sysroot/ostree/bootc. Nizho installs with no separate /var or /home
/// partition (system/installer/config.toml), so this is also the disk the user's files are on.
pub const SYSTEM_STORE: &str = "/sysroot";

/// Free space asked for on SYSTEM_STORE before a download starts: 3 GiB. A heuristic, not a
/// guarantee. bootc fetches only the layers that changed and Nizho has not measured the largest
/// update, so a download can still run out above this and a small one could have fitted below it.
/// It exists to refuse the clear case (a nearly full disk) before bootc fails part way through.
pub const MIN_FREE_FOR_UPDATE: u64 = 3 * 1024 * 1024 * 1024;

/// Why this operation should not start, or None to go ahead. Only a download is ever refused:
/// checking fetches metadata only, and going back to the previous version must never be blocked.
/// Unknown free space (None) is not a reason to refuse.
pub fn space_refusal(operation: Operation, free_bytes: Option<u64>) -> Option<String> {
    let free = match (operation, free_bytes) {
        (Operation::Upgrade, Some(free)) if free < MIN_FREE_FOR_UPDATE => free,
        _ => return None,
    };
    const GIB: f64 = (1024 * 1024 * 1024) as f64;
    // Round down, so the message never shows the minimum itself as the free amount.
    let shown = (free as f64 / GIB * 10.0).floor() / 10.0;
    Some(format!(
        "Not enough free disk space to download an update. This computer has {shown:.1} GB free and \
         Nizho asks for at least {:.0} GB before it starts. Free some space, then try again. Nothing \
         was downloaded.",
        MIN_FREE_FOR_UPDATE as f64 / GIB
    ))
}

impl Operation {
    pub fn script(self) -> String {
        let (command, success) = match self {
            Self::Check => ("sudo bootc upgrade --check", "Check finished. Review the result above."),
            Self::Upgrade => ("sudo bootc upgrade", "Update command finished. Review the result above; reboot to apply a staged update."),
            Self::Rollback => ("sudo bootc rollback", "Previous deployment selected. Reboot to apply. User data is not rolled back."),
        };
        // These are fixed commands, never interpolated user input. Keep both
        // success and failure output visible, and preserve the command's exit code.
        format!("{command}\nresult=$?\nif [ \"$result\" -eq 0 ]; then\n  printf '%s\\n' '{success}'\nelse\n  printf 'Operation failed (exit %s). Review the error above.\\n' \"$result\"\nfi\nprintf 'Press Enter to close… '\nIFS= read -r reply\nexit \"$result\"\n")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::process::{Command, Stdio};

    fn run(operation: Operation, exit: u8) -> std::process::Output {
        // The shell function replaces sudo completely: no host command runs.
        let script = format!(
            "sudo() {{ printf 'requested: %s\\n' \"$*\"; return {exit}; }}\n{}",
            operation.script()
        );
        Command::new("sh")
            .args(["-c", &script])
            .stdin(Stdio::null())
            .output()
            .unwrap()
    }

    #[test]
    fn failures_remain_visible_and_preserve_exit_status() {
        for op in [Operation::Check, Operation::Upgrade, Operation::Rollback] {
            let output = run(op, 42);
            let text = String::from_utf8(output.stdout).unwrap();
            assert_eq!(output.status.code(), Some(42));
            assert!(text.contains("Operation failed (exit 42)"));
            assert!(text.contains("Press Enter"));
            assert!(!text.contains("Previous deployment selected"));
        }
    }

    const GIB: u64 = 1024 * 1024 * 1024;

    #[test]
    fn a_download_is_refused_only_when_space_is_clearly_short() {
        assert_eq!(MIN_FREE_FOR_UPDATE, 3 * GIB);
        assert!(space_refusal(Operation::Upgrade, Some(MIN_FREE_FOR_UPDATE)).is_none());
        assert!(space_refusal(Operation::Upgrade, Some(u64::MAX)).is_none());
        let text = space_refusal(Operation::Upgrade, Some(MIN_FREE_FOR_UPDATE - 1)).unwrap();
        assert!(text.contains("2.9 GB free"), "{text}");
        assert!(text.contains("at least 3 GB"), "{text}");
        assert!(text.contains("Nothing was downloaded"), "{text}");
        assert!(space_refusal(Operation::Upgrade, Some(0)).unwrap().contains("0.0 GB free"));
    }

    #[test]
    fn unknown_free_space_does_not_refuse() {
        assert!(space_refusal(Operation::Upgrade, None).is_none());
    }

    #[test]
    fn going_back_and_checking_are_never_refused() {
        for free in [Some(0), Some(1), Some(MIN_FREE_FOR_UPDATE - 1), None] {
            assert!(space_refusal(Operation::Rollback, free).is_none());
            assert!(space_refusal(Operation::Check, free).is_none());
        }
        assert_eq!(SYSTEM_STORE, "/sysroot");
    }

    #[test]
    fn check_does_not_run_an_upgrade() {
        let output = run(Operation::Check, 0);
        assert!(output.status.success());
        assert!(String::from_utf8(output.stdout)
            .unwrap()
            .contains("requested: bootc upgrade --check\n"));
    }

    #[test]
    fn rollback_does_not_claim_to_restore_user_data() {
        let output = run(Operation::Rollback, 0);
        assert!(output.status.success());
        let text = String::from_utf8(output.stdout).unwrap();
        assert!(text.contains("requested: bootc rollback\n"));
        assert!(text.contains("User data is not rolled back"));
    }
}
