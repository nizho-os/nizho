#!/usr/bin/env python3
"""The phone-link helper against a fake firewall-cmd and a temp state file; never touches the real firewall.

    python3 -I scripts/test-phone-link.py
"""
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

HELPER = Path(__file__).resolve().parent.parent / "system" / "nizho-phone-link"

# Simulates one zone: permanent and runtime service lists live in files. Every call is logged. FAKE_FAIL_AT=N makes
# the Nth call fail (exit 1); FAKE_DOWN=1 makes every call exit 252 like "firewalld is not running".
FAKE = r'''#!/bin/sh
echo "$*" >> "$FAKE_DIR/log"
n=$(wc -l < "$FAKE_DIR/log")
[ "${FAKE_DOWN:-0}" = 0 ] || exit 252
[ "${FAKE_FAIL_AT:-0}" != "$n" ] || exit 1
list=$FAKE_DIR/runtime
action=
for a in "$@"; do
    case $a in
        --permanent) list=$FAKE_DIR/permanent ;;
        --zone=*) zone=${a#--zone=} ;;
        --add-service=*|--remove-service=*|--query-service=*) action=$a ;;
    esac
done
[ "$zone" = nizho-home ] || exit 2
svc=${action#*=}
touch "$list"
case $action in
    --add-service=*) grep -qx "$svc" "$list" || echo "$svc" >> "$list" ;;
    --remove-service=*) grep -vx "$svc" "$list" > "$list.new"; mv "$list.new" "$list" ;;
    --query-service=*) grep -qx "$svc" "$list" && { echo yes; exit 0; }; echo no; exit 1 ;;
    *) exit 2 ;;
esac
'''


class PhoneLinkTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="nizho-phone-link-test-")
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        fake = self.dir / "fake-firewall-cmd"
        fake.write_text(FAKE)
        fake.chmod(0o755)
        self.fake = fake
        self.state = self.dir / "etc" / "phone-link"

    def run_helper(self, *args, test_mode=True, **extra):
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "FAKE_DIR": str(self.dir),
               "NIZHO_PHONE_LINK_STATE": str(self.state), "NIZHO_FIREWALL_CMD": str(self.fake)}
        if test_mode:
            env["NIZHO_PHONE_LINK_TEST"] = "1"
        env.update(extra)
        return subprocess.run([str(HELPER), *args], env=env, capture_output=True, text=True)

    def zone(self, which):
        p = self.dir / which
        return p.read_text().split() if p.exists() else []

    def log(self):
        p = self.dir / "log"
        return p.read_text().splitlines() if p.exists() else []

    def assertClosed(self):
        self.assertFalse(self.state.exists())
        self.assertEqual(self.zone("runtime"), [])
        self.assertEqual(self.zone("permanent"), [])

    def test_on_then_status(self):
        r = self.run_helper("on")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.state.read_text(), "on")
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o644)
        self.assertEqual(self.zone("runtime"), ["kdeconnect"])
        self.assertEqual(self.zone("permanent"), ["kdeconnect"])
        r = self.run_helper("status")
        self.assertEqual((r.returncode, r.stdout), (0, "on\n"))
        self.assertEqual([p.name for p in self.state.parent.iterdir()], ["phone-link"], "no temp file left behind")

    def test_off_then_status(self):
        self.run_helper("on")
        r = self.run_helper("off")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertClosed()
        r = self.run_helper("status")
        self.assertEqual((r.returncode, r.stdout), (0, "off\n"))

    def test_status_on_a_fresh_system_is_off(self):
        r = self.run_helper("status")
        self.assertEqual((r.returncode, r.stdout), (0, "off\n"))

    def test_on_and_off_are_idempotent(self):
        for _ in range(2):
            self.assertEqual(self.run_helper("on").returncode, 0)
        self.assertEqual(self.zone("runtime"), ["kdeconnect"])
        self.assertEqual(self.state.read_text(), "on")
        for _ in range(2):
            self.assertEqual(self.run_helper("off").returncode, 0)
        self.assertClosed()

    def test_order_firewall_before_state_on_and_state_before_firewall_off(self):
        # the fake records the state file at each call through this wrapper
        wrapper = self.dir / "wrapper"
        wrapper.write_text(f'#!/bin/sh\nif [ -e "{self.state}" ]; then echo flag-on >> "{self.dir}/flags"; '
                           f'else echo flag-off >> "{self.dir}/flags"; fi\nexec "{self.fake}" "$@"\n')
        wrapper.chmod(0o755)
        self.run_helper("on", NIZHO_FIREWALL_CMD=str(wrapper))
        self.assertEqual((self.dir / "flags").read_text().split(), ["flag-off", "flag-off"])
        (self.dir / "flags").unlink()
        self.run_helper("off", NIZHO_FIREWALL_CMD=str(wrapper))
        self.assertEqual((self.dir / "flags").read_text().split(), ["flag-off", "flag-off"])

    def test_first_firewall_command_failing_changes_nothing(self):
        r = self.run_helper("on", FAKE_FAIL_AT="1")
        self.assertEqual(r.returncode, 1)
        self.assertTrue(r.stderr.strip())
        self.assertClosed()

    def test_second_firewall_command_failing_rolls_back(self):
        r = self.run_helper("on", FAKE_FAIL_AT="2")
        self.assertEqual(r.returncode, 1)
        self.assertClosed()
        self.assertIn("--permanent --zone=nizho-home --remove-service=kdeconnect", self.log())

    def test_firewalld_not_running_never_leaves_the_flag_on(self):
        r = self.run_helper("on", FAKE_DOWN="1")
        self.assertEqual(r.returncode, 1)
        self.assertFalse(self.state.exists())

    def test_unwritable_state_rolls_the_firewall_back(self):
        (self.dir / "etc").write_text("a file where the directory should be")
        r = self.run_helper("on")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.zone("runtime"), [])
        self.assertEqual(self.zone("permanent"), [])

    def test_off_removes_the_flag_even_when_the_firewall_fails(self):
        self.run_helper("on")
        n = len(self.log())
        r = self.run_helper("off", FAKE_FAIL_AT=str(n + 1))
        self.assertEqual(r.returncode, 1)
        self.assertFalse(self.state.exists(), "flag goes first, so nizhod sees off")
        self.assertEqual(self.zone("runtime"), [])  # the second command still ran

    def test_status_mismatch(self):
        self.run_helper("on")
        (self.dir / "runtime").write_text("")  # rule vanished from the running firewall
        r = self.run_helper("status")
        self.assertEqual((r.returncode, r.stdout), (3, "on\nmismatch\n"))
        self.run_helper("off")
        (self.dir / "runtime").write_text("kdeconnect\n")  # rule added by hand
        r = self.run_helper("status")
        self.assertEqual((r.returncode, r.stdout), (3, "off\nmismatch\n"))
        (self.dir / "runtime").write_text("")
        (self.dir / "permanent").write_text("kdeconnect\n")
        self.assertEqual(self.run_helper("status").returncode, 3)

    def test_status_with_garbage_flag_is_off(self):
        self.state.parent.mkdir(parents=True)
        self.state.write_text("yes please")
        r = self.run_helper("status")
        self.assertEqual((r.returncode, r.stdout), (0, "off\n"))

    def test_status_when_firewall_is_unreadable_is_not_claimed(self):
        r = self.run_helper("status", FAKE_DOWN="1")
        self.assertEqual((r.returncode, r.stdout), (1, "off\n"))
        self.assertNotIn("mismatch", r.stdout)

    def test_non_root_is_refused(self):
        if os.getuid() == 0:
            self.skipTest("running as root")
        r = self.run_helper("on", test_mode=False)
        self.assertEqual(r.returncode, 77)
        self.assertTrue(r.stderr.strip())
        self.assertEqual(self.log(), [])
        self.assertFalse(self.state.exists())

    def test_test_overrides_are_ignored_without_test_mode(self):
        if os.getuid() != 0:
            self.skipTest("only meaningful as root")
        r = self.run_helper("status", test_mode=False)
        self.assertEqual(self.log(), [], "the fake must not be used")

    def test_garbage_arguments(self):
        for args in [(), ("bogus",), ("on", "extra"), ("--help",), ("ON",)]:
            r = self.run_helper(*args)
            self.assertEqual(r.returncode, 2, args)
            self.assertIn("usage", r.stderr)
        self.assertEqual(self.log(), [])

    def test_only_the_home_zone_and_only_kdeconnect(self):
        self.run_helper("on")
        self.run_helper("status")
        self.run_helper("off")
        calls = self.log()
        self.assertTrue(calls)
        for c in calls:
            self.assertIn("--zone=nizho-home", c.split())
            self.assertEqual(sum(w.startswith("--") and "service" in w for w in c.split()), 1)
            self.assertTrue(c.split()[-1].endswith("-service=kdeconnect"), c)
            for bad in ("--add-port", "--set-default-zone", "--panic", "--reload", "--direct"):
                self.assertNotIn(bad, c)


if __name__ == "__main__":
    unittest.main()
