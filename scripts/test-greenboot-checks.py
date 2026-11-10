"""Nizho's greenboot checks against fake systemctl, grub2-editenv and /sys; never reads host state.

    python3 -I scripts/test-greenboot-checks.py

Each check runs the way greenboot 0.16.4 runs a *.sh file: `bash -C <script>`, exit status only.
This proves what the scripts decide for a given answer from systemctl. It does not prove that
greenboot on a booted Nizho runs them, or that a rollback follows: that needs a VM test.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
CHECKS = ROOT / "system" / "greenboot" / "check"
HELPER = ROOT / "system" / "greenboot" / "nizho-greenboot-unit-check"
LOGIN = CHECKS / "required.d" / "20_nizho_login_screen.sh"
DAEMON = CHECKS / "required.d" / "21_nizho_privacy_daemon.sh"
DISPLAY = CHECKS / "wanted.d" / "20_nizho_display.sh"
NETWORK = CHECKS / "wanted.d" / "21_nizho_network.sh"
FIREWALL = CHECKS / "wanted.d" / "22_nizho_firewall.sh"

# is-active answers come from a file, one per call; the last line repeats.
SYSTEMCTL = r'''
echo "systemctl $*" >> "$TEST_LOG"
case "$1" in
    get-default) echo "${TEST_TARGET:-graphical.target}" ;;
    is-enabled) echo "${TEST_ENABLED:-enabled}"; [ "${TEST_ENABLED:-enabled}" = enabled ] ;;
    show) echo "${TEST_RESTARTS:-0}" ;;
    is-active)
        f="$TEST_STATES"
        state=$(sed -n 1p "$f")
        [ "$(wc -l < "$f")" -gt 1 ] && sed -i 1d "$f"
        echo "$state"
        [ "$state" = active ] ;;
    *) exit 1 ;;
esac
'''
GRUB = r'''
[ "${TEST_GRUB_FAIL:-0}" = 0 ] || exit 1
[ "${TEST_TRIAL:-1}" = 1 ] && echo 'greenboot_next_deployment_id=sha256:test'
echo 'boot_success=0'
'''


class GreenbootChecksTest(unittest.TestCase):
    def run_check(self, script, states="active", cards=("card1",), connectors=None, no_systemctl=False, **settings):
        with tempfile.TemporaryDirectory(prefix="nizho-greenboot-test-") as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            commands = {"grub2-editenv": GRUB, "sleep": 'echo "sleep $*" >> "$TEST_LOG"'}
            if not no_systemctl:
                commands["systemctl"] = SYSTEMCTL
            for name, body in commands.items():
                p = bin_dir / name
                p.write_text("#!/bin/sh\n" + body)
                p.chmod(0o755)
            # Only these host tools are reachable, so no check can ask the host's systemctl.
            for tool in ("sh", "bash", "sed", "wc", "grep"):
                (bin_dir / tool).symlink_to(shutil.which(tool))
            drm = root / "sys" / "class" / "drm"
            drm.mkdir(parents=True)
            for card in cards:
                (drm / card).mkdir()
            for name, status in (connectors or {}).items():
                (drm / name).mkdir()
                (drm / name / "status").write_text(status + "\n")
            (root / "states").write_text("\n".join(states.split()) + "\n")
            (root / "log").touch()
            env = {k: v for k, v in os.environ.items() if not k.startswith(("TEST_", "NIZHO_"))}
            env.update(PATH=str(bin_dir), TEST_LOG=str(root / "log"), TEST_STATES=str(root / "states"),
                       NIZHO_GREENBOOT_HELPER=str(HELPER), NIZHO_GREENBOOT_SYSFS=str(root / "sys"),
                       NIZHO_GREENBOOT_WAIT="6", NIZHO_GREENBOOT_STEP="1", NIZHO_GREENBOOT_SETTLE="1")
            env.update({(k if k.startswith("NIZHO_") else "TEST_" + k): str(v) for k, v in settings.items()})
            r = subprocess.run([shutil.which("bash"), "-C", str(script)], env=env, capture_output=True, text=True, timeout=20)
            return r, (root / "log").read_text().splitlines()

    # required: the login screen

    def test_login_screen_running_passes(self):
        r, _ = self.run_check(LOGIN)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("sddm.service is active", r.stdout)

    def test_login_screen_that_failed_fails_the_boot(self):
        r, _ = self.run_check(LOGIN, states="failed")
        self.assertEqual(r.returncode, 1)
        self.assertIn("sddm.service failed", r.stderr)

    def test_login_screen_that_starts_during_the_wait_passes(self):
        r, calls = self.run_check(LOGIN, states="inactive activating activating active")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("sleep 1", calls)

    def test_a_service_that_recovers_from_a_failure_passes(self):
        r, _ = self.run_check(LOGIN, states="failed failed activating active")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_service_that_is_active_for_a_moment_and_then_fails_fails(self):
        r, _ = self.run_check(LOGIN, states="active failed")
        self.assertEqual(r.returncode, 1)

    def test_a_crash_loop_fails(self):
        r, _ = self.run_check(LOGIN, states="activating", RESTARTS=4)
        self.assertEqual(r.returncode, 1)
        self.assertIn("4 restarts", r.stderr)

    def test_a_slow_start_without_restarts_is_not_a_failure(self):
        r, _ = self.run_check(LOGIN, states="activating", RESTARTS=0)
        self.assertEqual(r.returncode, 0)
        self.assertIn("still starting", r.stderr)

    def test_the_wait_is_bounded(self):
        r, calls = self.run_check(LOGIN, states="activating", NIZHO_GREENBOOT_WAIT=3)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(calls.count("sleep 1"), 3)

    def test_a_unit_systemd_never_started_is_not_a_failure(self):
        r, _ = self.run_check(LOGIN, states="inactive")
        self.assertEqual(r.returncode, 0)
        self.assertIn("not treated as a failure", r.stderr)

    def test_first_boot_setup_on_multi_user_target_is_not_checked(self):
        r, calls = self.run_check(LOGIN, states="failed", TARGET="multi-user.target")
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("systemctl is-active sddm.service", calls)

    def test_headless_machine_without_a_graphics_device_is_not_checked(self):
        r, calls = self.run_check(LOGIN, states="failed", cards=())
        self.assertEqual(r.returncode, 0)
        self.assertIn("no graphics device", r.stdout)
        self.assertNotIn("systemctl is-active sddm.service", calls)

    def test_headless_vm_with_a_graphics_device_and_no_screen_passes_when_sddm_runs(self):
        r, _ = self.run_check(LOGIN, connectors={"card1-Virtual-1": "disconnected"})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_disabled_login_screen_is_not_checked(self):
        r, _ = self.run_check(LOGIN, states="failed", ENABLED="disabled")
        self.assertEqual(r.returncode, 0)
        self.assertIn("not enabled", r.stderr)

    def test_no_update_on_trial_never_fails_and_does_not_wait(self):
        for states in ("failed", "activating", "inactive"):
            r, calls = self.run_check(LOGIN, states=states, TRIAL=0, RESTARTS=9)
            self.assertEqual(r.returncode, 0, states)
            self.assertIn("no update is on trial", r.stderr)
            self.assertFalse([c for c in calls if c.startswith("sleep")])

    def test_unreadable_boot_environment_never_fails(self):
        r, _ = self.run_check(LOGIN, states="failed", GRUB_FAIL=1)
        self.assertEqual(r.returncode, 0)

    def test_missing_helper_or_systemctl_never_fails(self):
        r, _ = self.run_check(LOGIN, states="failed", NIZHO_GREENBOOT_HELPER="/nonexistent/helper")
        self.assertEqual(r.returncode, 0)
        self.assertIn("is missing", r.stderr)
        r, _ = self.run_check(DAEMON, states="failed", no_systemctl=True)
        self.assertEqual(r.returncode, 0)

    # required: the privacy daemon

    def test_privacy_daemon_running_passes(self):
        r, calls = self.run_check(DAEMON)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("systemctl is-active nizhod.service", calls)

    def test_privacy_daemon_failed_fails_the_boot(self):
        r, _ = self.run_check(DAEMON, states="failed")
        self.assertEqual(r.returncode, 1)
        self.assertIn("nizhod.service failed", r.stderr)

    def test_privacy_daemon_crash_loop_fails(self):
        r, _ = self.run_check(DAEMON, states="activating", RESTARTS=2)
        self.assertEqual(r.returncode, 1)

    def test_privacy_daemon_is_checked_without_a_graphics_device(self):
        r, _ = self.run_check(DAEMON, states="failed", cards=())
        self.assertEqual(r.returncode, 1)

    # wanted: reported, never part of the rollback decision

    def test_display_connected(self):
        r, _ = self.run_check(DISPLAY, connectors={"card1-eDP-1": "connected", "card1-HDMI-A-1": "disconnected"})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_display_missing_or_disconnected_is_reported(self):
        for connectors in ({}, {"card1-Virtual-1": "disconnected"}):
            r, _ = self.run_check(DISPLAY, connectors=connectors)
            self.assertEqual(r.returncode, 1)
            self.assertIn("no connected display", r.stderr)

    def test_network_and_firewall(self):
        for script in (NETWORK, FIREWALL):
            self.assertEqual(self.run_check(script)[0].returncode, 0)
            self.assertEqual(self.run_check(script, states="failed")[0].returncode, 1)
            self.assertEqual(self.run_check(script, no_systemctl=True)[0].returncode, 1)

    # layout: what greenboot 0.16.4 needs to pick the files up

    def test_checks_are_named_sh_and_executable_and_only_two_are_required(self):
        required = sorted(p.name for p in (CHECKS / "required.d").iterdir())
        self.assertEqual(required, ["20_nizho_login_screen.sh", "21_nizho_privacy_daemon.sh"])
        for p in list(CHECKS.glob("*/*")):
            self.assertEqual(p.suffix, ".sh", p)
            self.assertTrue(os.access(p, os.X_OK), p)

    def test_the_image_installs_every_check_and_the_helper(self):
        text = (ROOT / "Containerfile").read_text()
        self.assertIn("COPY system/greenboot/check/ /usr/lib/greenboot/check/", text)
        self.assertIn("COPY system/greenboot/nizho-greenboot-unit-check /usr/libexec/nizho-greenboot-unit-check", text)
        self.assertIn("chmod 755 /usr/libexec/nizho-greenboot-unit-check /usr/lib/greenboot/check/required.d/2?_nizho_*.sh "
                      "/usr/lib/greenboot/check/wanted.d/2?_nizho_*.sh", text)


if __name__ == "__main__":
    unittest.main()
