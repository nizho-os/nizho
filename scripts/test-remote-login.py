#!/usr/bin/env python3
"""The remote-login helper against fake firewall-cmd, systemctl, systemd-run and sshd; never touches the machine.

    python3 -I scripts/test-remote-login.py
"""
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

HELPER = Path(__file__).resolve().parent.parent / "system" / "nizho-remote-login"

# One fake for all four commands, told apart by $0. firewall-cmd keeps runtime/permanent service lists of the
# zone it is given; systemctl keeps enabled/active/timer flags. FAKE_FAIL=<word> makes the call whose arguments
# contain that word fail.
FAKE = r'''#!/bin/sh
d=$FAKE_DIR
me=${0##*/}
echo "$me $*" >> "$d/log"
case " $me $* " in *" ${FAKE_FAIL:-@@} "*) exit 1 ;; esac
case $me in
  firewall-cmd)
    list=$d/runtime; zone=
    for a in "$@"; do case $a in --permanent) list=$d/permanent ;; --zone=*) zone=${a#--zone=} ;; esac; done
    [ "$zone" = nizho-home ] || exit 2
    touch "$list"
    for a in "$@"; do case $a in
      --add-service=*) grep -qx "${a#*=}" "$list" || echo "${a#*=}" >> "$list" ;;
      --remove-service=*) grep -vx "${a#*=}" "$list" > "$list.n"; mv "$list.n" "$list" ;;
    esac; done ;;
  systemctl)
    case "$*" in
      "enable --now sshd.service") touch "$d/sshd-enabled" "$d/sshd-active" ;;
      "disable --now sshd.service") rm -f "$d/sshd-enabled" "$d/sshd-active" ;;
      "reload sshd.service") ;;
      "stop nizho-remote-login-off.timer") rm -f "$d/timer" ;;
      "is-active --quiet nizho-remote-login-off.timer") [ -e "$d/timer" ] ;;
      *) exit 2 ;;
    esac ;;
  systemd-run) touch "$d/timer" ;;
  sshd) [ "$*" = "-t" ] || exit 2 ;;
esac
'''


class RemoteLogin(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="nizho-remote-login-test-")
        self.dir = Path(self._tmp.name)
        for name in ("firewall-cmd", "systemctl", "systemd-run", "sshd"):
            f = self.dir / name
            f.write_text(FAKE)
            f.chmod(f.stat().st_mode | stat.S_IXUSR)
        self.state = self.dir / "etc" / "remote-login"
        self.dropin = self.dir / "sshd_config.d" / "10-nizho-remote-login.conf"
        self.offat = self.dir / "etc" / "remote-login-off-at"

    def tearDown(self):
        self._tmp.cleanup()

    def run_helper(self, *args, **env):
        d = self.dir
        e = {"PATH": os.environ["PATH"], "FAKE_DIR": str(d), "NIZHO_REMOTE_LOGIN_TEST": "1",
             "NIZHO_REMOTE_LOGIN_STATE": str(self.state), "NIZHO_REMOTE_LOGIN_DROPIN": str(self.dropin),
             "NIZHO_REMOTE_LOGIN_OFFAT": str(self.offat), "NIZHO_REMOTE_LOGIN_NOW": "1000000",
             "NIZHO_FIREWALL_CMD": str(d / "firewall-cmd"), "NIZHO_SYSTEMCTL": str(d / "systemctl"),
             "NIZHO_SYSTEMD_RUN": str(d / "systemd-run"), "NIZHO_SSHD": str(d / "sshd"), **env}
        return subprocess.run(["sh", str(HELPER), *args], env=e, capture_output=True, text=True)

    def services(self, which):
        p = self.dir / which
        return p.read_text().split() if p.exists() else []

    def assert_closed(self):
        self.assertFalse(self.state.exists())
        self.assertEqual(self.services("runtime"), [])
        self.assertEqual(self.services("permanent"), [])
        self.assertFalse((self.dir / "sshd-active").exists())
        self.assertFalse((self.dir / "sshd-enabled").exists())
        self.assertFalse(self.dropin.exists())
        self.assertFalse(self.offat.exists())

    def test_on_opens_ssh_at_home_with_keys_only(self):
        r = self.run_helper("on")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.state.read_text(), "on")
        self.assertEqual(self.services("runtime"), ["ssh"])
        self.assertEqual(self.services("permanent"), ["ssh"])
        self.assertTrue((self.dir / "sshd-active").exists())
        conf = self.dropin.read_text().splitlines()
        for line in ("PermitRootLogin no", "PasswordAuthentication no", "KbdInteractiveAuthentication no"):
            self.assertIn(line, conf)
        self.assertEqual(stat.S_IMODE(self.dropin.stat().st_mode), 0o644)
        self.assertEqual(self.run_helper("status").stdout.split(), ["on", "keys"])

    def test_passwords_only_when_asked(self):
        self.run_helper("on", "passwords")
        self.assertIn("PasswordAuthentication yes", self.dropin.read_text().splitlines())
        self.assertIn("PermitRootLogin no", self.dropin.read_text().splitlines())
        self.assertEqual(self.run_helper("status").stdout.split(), ["on", "passwords"])

    def test_one_hour_timer(self):
        self.run_helper("on", "1h")
        self.assertIn("--on-active=3600s", (self.dir / "log").read_text())
        self.assertEqual(self.offat.read_text(), "1003600")
        self.assertEqual(self.run_helper("status").stdout.split(), ["on", "keys", "1h"])
        self.run_helper("off")
        self.assertFalse((self.dir / "timer").exists(), "off cancels the timer")

    def test_on_without_the_hour_forgets_an_old_limit(self):
        self.run_helper("on", "1h")
        self.run_helper("on")
        self.assertFalse(self.offat.exists())

    def test_resume_after_a_restart_restarts_the_timer_for_the_time_left(self):
        self.run_helper("on", "1h")
        (self.dir / "timer").unlink()  # a restart loses the transient timer
        r = self.run_helper("resume", NIZHO_REMOTE_LOGIN_NOW="1001800")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--on-active=1800s", (self.dir / "log").read_text())
        self.assertEqual(self.run_helper("status").stdout.split(), ["on", "keys", "1h"])

    def test_resume_past_the_limit_turns_everything_off(self):
        self.run_helper("on", "passwords", "1h")
        r = self.run_helper("resume", NIZHO_REMOTE_LOGIN_NOW="1003600")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assert_closed()

    def test_resume_with_a_broken_limit_file_turns_everything_off(self):
        self.run_helper("on", "1h")
        self.offat.write_text("soon")
        self.run_helper("resume")
        self.assert_closed()

    def test_resume_without_a_limit_changes_nothing(self):
        self.run_helper("on")
        before = (self.dir / "log").read_text()
        self.assertEqual(self.run_helper("resume").returncode, 0)
        self.assertEqual(self.state.read_text(), "on")
        self.assertEqual((self.dir / "log").read_text(), before)

    def test_off_closes_everything(self):
        self.run_helper("on", "passwords", "1h")
        r = self.run_helper("off")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assert_closed()
        self.assertEqual(self.run_helper("status").stdout.split(), ["off", "keys"])

    def test_the_flag_is_written_last_and_cleared_first(self):
        self.run_helper("on")
        log = (self.dir / "log").read_text()
        self.assertLess(log.index("--add-service=ssh"), log.index("enable --now"))
        # A failure at the last step before the flag leaves everything closed.
        for step in ("--permanent", "--add-service=ssh", "sshd.service", "-t"):
            self.run_helper("off")
            (self.dir / "log").unlink()
            r = self.run_helper("on", FAKE_FAIL=step)
            self.assertEqual(r.returncode, 1, step)
            self.assert_closed()

    def test_off_reports_but_keeps_the_flag_off_when_the_firewall_is_down(self):
        self.run_helper("on")
        r = self.run_helper("off", FAKE_FAIL="--remove-service=ssh")
        self.assertEqual(r.returncode, 1)
        self.assertFalse(self.state.exists(), "the flag never vouches for an open port")

    def test_only_the_home_zone_is_touched(self):
        self.run_helper("on")
        self.run_helper("off")
        for line in (self.dir / "log").read_text().splitlines():
            if line.startswith("firewall-cmd"):
                self.assertIn("--zone=nizho-home", line.split())
                for bad in ("--add-port", "--set-default-zone", "--reload", "--direct", "--panic"):
                    self.assertNotIn(bad, line)

    def test_bad_usage(self):
        for args in ((), ("on", "everything"), ("on", "passwords", "1h", "x"), ("off", "now"), ("status", "x"), ("resume", "x"), ("open",)):
            self.assertEqual(self.run_helper(*args).returncode, 2, args)
        self.assertFalse(self.state.exists())

    def test_needs_root_outside_tests(self):
        if os.geteuid() == 0:
            self.skipTest("running as root")
        r = subprocess.run(["sh", str(HELPER), "on"], env={"PATH": os.environ["PATH"]}, capture_output=True, text=True)
        self.assertEqual(r.returncode, 77)


if __name__ == "__main__":
    unittest.main()
