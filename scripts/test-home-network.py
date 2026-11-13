#!/usr/bin/env python3
"""The home-network helper against a fake nmcli; never touches the real NetworkManager.

    python3 -I scripts/test-home-network.py
"""
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

HELPER = Path(__file__).resolve().parent.parent / "system" / "nizho-home-network"

WIFI = "5f693841-2b78-4f80-a3fa-7693ad7133c6"
WIRED = "0a408e41-e53d-3281-a13c-28368a135a3b"
BRIDGE = "11111111-2222-3333-4444-555555555555"

# Connections live in $FAKE_DIR/<uuid>/{type,zone,name,active,devices}. Every call is logged.
FAKE = r'''#!/bin/sh
echo "$*" >> "$FAKE_DIR/log"
[ "${FAKE_DOWN:-0}" = 0 ] || exit 8
d=$FAKE_DIR
case "$*" in
  "-g UUID,TYPE,ACTIVE,NAME connection show")
    for c in "$d"/c/*; do
      printf '%s:%s:%s:%s\n' "${c##*/}" "$(cat "$c/type")" "$(cat "$c/active")" "$(sed 's/\\/\\\\/g; s/:/\\:/g' "$c/name")"
    done ;;
  "-g connection.zone connection show uuid "*) c=$d/c/$6; [ -d "$c" ] || exit 10; cat "$c/zone" ;;
  "-g connection.uuid,connection.type,connection.zone connection show uuid "*)
    c=$d/c/$6; [ -d "$c" ] || exit 10; printf '%s\n%s\n%s\n' "$6" "$(cat "$c/type")" "$(cat "$c/zone")" ;;
  "-g GENERAL.DEVICES connection show uuid "*) c=$d/c/$6; cat "$c/devices" ;;
  "connection modify uuid "*) c=$d/c/$4; [ -d "$c" ] || exit 10; [ "${FAKE_MODIFY_FAILS:-0}" = 0 ] || exit 1; printf '%s' "$6" > "$c/zone" ;;
  "device reapply "*) [ "${FAKE_REAPPLY_FAILS:-0}" = 0 ] || exit 1 ;;
  *) exit 2 ;;
esac
'''


class HomeNetwork(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="nizho-home-network-test-")
        self.dir = Path(self._tmp.name)
        fake = self.dir / "nmcli"
        fake.write_text(FAKE)
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        self.add(WIFI, "802-11-wireless", "Café: Ram\\x", active="no")
        self.add(WIRED, "802-3-ethernet", "Wired connection 1", active="yes", devices="enp0s3")
        self.add(BRIDGE, "bridge", "br9")

    def tearDown(self):
        self._tmp.cleanup()

    def add(self, uuid, type_, name, zone="", active="no", devices=""):
        c = self.dir / "c" / uuid
        c.mkdir(parents=True)
        for f, v in (("type", type_), ("name", name), ("zone", zone), ("active", active), ("devices", devices)):
            (c / f).write_text(v)

    def zone(self, uuid):
        return (self.dir / "c" / uuid / "zone").read_text()

    def run_helper(self, *args, **env):
        e = {"PATH": os.environ["PATH"], "FAKE_DIR": str(self.dir), "NIZHO_HOME_NETWORK_TEST": "1",
             "NIZHO_NMCLI": str(self.dir / "nmcli"), **env}
        return subprocess.run(["sh", str(HELPER), *args], env=e, capture_output=True, text=True)

    def log(self):
        p = self.dir / "log"
        return p.read_text() if p.exists() else ""

    def test_list_shows_wifi_and_wired_only_with_names_unescaped(self):
        r = self.run_helper("list")
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = sorted(l.split("\t") for l in r.stdout.splitlines())
        self.assertEqual(rows, sorted([[WIFI, "wifi", "no", "no", "Café: Ram\\x"],
                                       [WIRED, "ethernet", "no", "yes", "Wired connection 1"]]))

    def test_list_marks_home_networks(self):
        self.run_helper("set", WIFI, "on")
        r = self.run_helper("list")
        self.assertIn(f"{WIFI}\twifi\tyes\tno\tCafé: Ram\\x", r.stdout.splitlines())

    def test_on_sets_the_home_zone_and_reapplies_an_active_connection(self):
        r = self.run_helper("set", WIRED, "on")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.zone(WIRED), "nizho-home")
        self.assertIn("device reapply enp0s3", self.log())

    def test_off_clears_only_the_home_zone(self):
        self.run_helper("set", WIFI, "on")
        self.assertEqual(self.run_helper("set", WIFI, "off").returncode, 0)
        self.assertEqual(self.zone(WIFI), "")
        (self.dir / "c" / WIFI / "zone").write_text("trusted")
        self.assertEqual(self.run_helper("set", WIFI, "off").returncode, 0)
        self.assertEqual(self.zone(WIFI), "trusted", "a zone set some other way is left alone")

    def test_setting_what_is_already_set_changes_nothing(self):
        self.run_helper("set", WIFI, "on")
        before = self.log().count("connection modify")
        self.assertEqual(self.run_helper("set", WIFI, "on").returncode, 0)
        self.assertEqual(self.log().count("connection modify"), before)

    def test_only_wifi_and_wired_can_be_home(self):
        r = self.run_helper("set", BRIDGE, "on")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.zone(BRIDGE), "")

    def test_only_uuids_are_accepted(self):
        for bad in ("Wired connection 1", "x; rm -rf /", "--help", WIFI.upper(), WIFI + "0", "", "../" + WIFI):
            r = self.run_helper("set", bad, "on")
            self.assertEqual(r.returncode, 1, bad)
        self.assertNotIn("connection modify", self.log())

    def test_unknown_connection_and_bad_usage(self):
        self.assertEqual(self.run_helper("set", "00000000-0000-0000-0000-000000000000", "on").returncode, 1)
        for args in ((), ("set", WIFI), ("set", WIFI, "yes"), ("list", "extra"), ("on",)):
            self.assertEqual(self.run_helper(*args).returncode, 2, args)

    def test_a_failed_change_reports_and_a_failed_reapply_still_saves(self):
        r = self.run_helper("set", WIRED, "on", FAKE_MODIFY_FAILS="1")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.zone(WIRED), "")
        r = self.run_helper("set", WIRED, "on", FAKE_REAPPLY_FAILS="1")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.zone(WIRED), "nizho-home")
        self.assertIn("next time", r.stderr)

    def test_networkmanager_down_is_an_error(self):
        self.assertEqual(self.run_helper("list", FAKE_DOWN="1").returncode, 1)
        self.assertEqual(self.run_helper("set", WIFI, "on", FAKE_DOWN="1").returncode, 1)

    def test_set_needs_root_outside_tests(self):
        if os.geteuid() == 0:
            self.skipTest("running as root")
        r = subprocess.run(["sh", str(HELPER), "set", WIFI, "on"], env={"PATH": os.environ["PATH"]},
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 77)


if __name__ == "__main__":
    unittest.main()
