#!/usr/bin/env python3
"""Test system/nizho-display-scale with made-up screens, offline.

    python3 -I scripts/test-display-scale.py

Each test builds real 128-byte EDID blocks (header, maker, product, size in cm, checksum) in a fake
/sys/class/drm, puts a stand-in kscreen-doctor first on PATH that answers -j with JSON shaped like
libkscreen's ConfigSerializer, and runs the script as a program. Nothing here touches a real display.
"""
import importlib.machinery
import importlib.util
import json
import math
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "system" / "nizho-display-scale"


def load_module():
    loader = importlib.machinery.SourceFileLoader("nizho_display_scale", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


ds = load_module()


def edid(maker="BOE", product=0x0A1B, width_cm=29, height_cm=17, checksum_ok=True):
    """A 128-byte EDID 1.4 base block with one detailed timing slot left empty."""
    word = 0
    for letter in maker:
        word = (word << 5) | (ord(letter) - 64)
    block = bytearray(128)
    block[0:8] = bytes([0, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0])
    block[8], block[9] = word >> 8, word & 0xFF
    block[10], block[11] = product & 0xFF, product >> 8
    block[12:16] = (1234).to_bytes(4, "little")   # serial
    block[16], block[17] = 10, 33                  # week 10 of 2023
    block[18], block[19] = 1, 4                    # EDID 1.4
    block[20] = 0xA5                               # digital input, 8 bits per colour, DisplayPort
    block[21], block[22] = width_cm, height_cm
    block[23] = 120                                # gamma 2.2
    block[24] = 0x0A
    block[54:72] = bytes(18)
    for i in range(72, 126, 18):                   # three display descriptors marked unused
        block[i:i + 5] = bytes([0, 0, 0, 0x10, 0])
    block[126] = 0                                 # no extension blocks
    block[127] = (-sum(block[:127])) % 256
    if not checksum_ok:
        block[127] = (block[127] + 1) % 256
    return bytes(block)


def output(name, width_px, height_px, scale=1.0, oid=1, connected=True, enabled=True):
    """One output the way `kscreen-doctor -j` prints it (libkscreen configserializer.cpp)."""
    entry = {
        "id": oid, "name": name, "type": 14 if name.startswith("eDP") else 11, "icon": "",
        "pos": {"x": 0, "y": 0}, "scale": scale,
        "size": {"width": round(width_px / scale), "height": round(height_px / scale)},
        "rotation": 1, "currentModeId": "2", "preferredModes": ["2"], "connected": connected,
        "followPreferredMode": False, "priority": oid, "clones": [], "sizeMM": {"width": 0, "height": 0},
        "replicationSource": 0,
        "modes": [
            {"id": "1", "name": "1024x768@60", "size": {"width": 1024, "height": 768}, "refreshRate": 60.0},
            {"id": "2", "name": "%dx%d@60" % (width_px, height_px),
             "size": {"width": width_px, "height": height_px}, "refreshRate": 60.0},
        ],
    }
    if enabled is not None:
        entry["enabled"] = enabled
    return entry


FAKE_DOCTOR = textwrap.dedent("""\
    #!{python}
    import json, os, sys, time
    log = os.environ["FAKE_LOG"]
    with open(log, "a") as f:
        f.write(" ".join(sys.argv[1:]) + "\\n")
    if os.environ.get("FAKE_SLEEP"):
        time.sleep(float(os.environ["FAKE_SLEEP"]))
    if sys.argv[1:] == ["-j"]:
        if os.environ.get("FAKE_READ_EXIT"):
            sys.exit(int(os.environ["FAKE_READ_EXIT"]))
        with open(os.environ["FAKE_CONFIG"]) as f:
            sys.stdout.write(f.read())
        sys.exit(0)
    sys.exit(int(os.environ.get("FAKE_SET_EXIT", "0")))
    """)


class Machine:
    """A fake home, DRM tree and kscreen-doctor in one temporary folder."""

    def __init__(self, tmp):
        self.tmp = Path(tmp)
        self.drm = self.tmp / "drm"
        self.home = self.tmp / "home"
        self.bin = self.tmp / "bin"
        for d in (self.drm, self.home / ".config", self.bin):
            d.mkdir(parents=True)
        doctor = self.bin / "kscreen-doctor"
        doctor.write_text(FAKE_DOCTOR.format(python=sys.executable))
        doctor.chmod(doctor.stat().st_mode | stat.S_IXUSR)
        self.config = self.tmp / "config.json"
        self.log = self.tmp / "calls.log"
        self.outputs = []
        self.extra_env = {}

    def screen(self, name, blob, width_px, height_px, scale=1.0, card="card1", **kw):
        if blob is not None:
            d = self.drm / ("%s-%s" % (card, name))
            d.mkdir()
            (d / "edid").write_bytes(blob)
        self.outputs.append(output(name, width_px, height_px, scale, oid=len(self.outputs) + 1, **kw))

    def set_scale(self, name, scale):
        for o in self.outputs:
            if o["name"] == name:
                o["scale"] = scale

    def run(self, use_path=True, session="wayland"):
        self.config.write_text(json.dumps({"outputs": self.outputs, "screen": {"id": 0},
                                           "tabletModeAvailable": False, "tabletModeEngaged": False}))
        self.log.write_text("")
        env = {"HOME": str(self.home), "NIZHO_DRM_DIR": str(self.drm), "XDG_SESSION_TYPE": session,
               "PATH": (str(self.bin) + os.pathsep if use_path else "") + "/usr/bin:/bin",
               "FAKE_LOG": str(self.log), "FAKE_CONFIG": str(self.config), **self.extra_env}
        done = subprocess.run([sys.executable, "-I", str(SCRIPT)], env=env, capture_output=True,
                              text=True, timeout=60)
        self.stderr = done.stderr
        return done.returncode

    def calls(self):
        return [line for line in self.log.read_text().splitlines() if line]

    def set_calls(self):
        return [c for c in self.calls() if c != "-j"]

    def state(self):
        path = self.home / ".config" / "nizho" / "display-scale.json"
        return json.loads(path.read_text()) if path.exists() else None


# (screen, pixels, EDID size in cm as real panels round it, expected dpi, expected scale)
POLICY_CASES = [
    ("13.3 inch 1920x1080", (1920, 1080), (29, 17), 166, 1.5),
    ("14 inch 2880x1800", (2880, 1800), (30, 19), 243, 1.75),
    ("15.6 inch 1920x1080", (1920, 1080), (34, 19), 144, 1.25),
    ("27 inch 2560x1440", (2560, 1440), (60, 34), 108, 1.0),
    ("27 inch 3840x2160", (3840, 2160), (60, 34), 162, 1.5),
    ("13.5 inch 3000x2000", (3000, 2000), (29, 19), 264, 1.75),
    ("13.3 inch 3840x2400", (3840, 2400), (29, 18), 337, 2.0),
    ("24 inch 1920x1080", (1920, 1080), (53, 30), 91, 1.0),
]


class PolicyTests(unittest.TestCase):
    def test_policy_table_against_real_screen_sizes(self):
        for label, (w, h), (wcm, hcm), want_dpi, want_scale in POLICY_CASES:
            with self.subTest(label):
                dpi = ds.dpi_from(w, h, wcm, hcm)
                self.assertAlmostEqual(dpi, want_dpi, delta=1.0)
                self.assertEqual(ds.scale_for_dpi(dpi), want_scale)

    def test_the_named_sizes_match_their_nominal_diagonals(self):
        # The cm values above are what those panels report; check they are the screens named.
        for label, _, (wcm, hcm), _, _ in POLICY_CASES:
            with self.subTest(label):
                inches = float(label.split()[0])
                self.assertAlmostEqual(math.hypot(wcm, hcm) / 2.54, inches, delta=0.5)

    def test_boundaries_belong_to_the_lower_row(self):
        self.assertEqual(ds.scale_for_dpi(110), 1.0)
        self.assertEqual(ds.scale_for_dpi(110.1), 1.25)
        self.assertEqual(ds.scale_for_dpi(160), 1.25)
        self.assertEqual(ds.scale_for_dpi(160.1), 1.5)
        self.assertEqual(ds.scale_for_dpi(210), 1.5)
        self.assertEqual(ds.scale_for_dpi(210.1), 1.75)
        self.assertEqual(ds.scale_for_dpi(280.1), 2.0)

    def test_sizes_that_cannot_be_right_are_refused(self):
        for wcm, hcm in [(0, 0), (34, 0), (16, 9), (16, 10), (2, 1), (400, 225), (60, 10)]:
            with self.subTest((wcm, hcm)), self.assertRaises(ds.Skip):
                ds.dpi_from(1920, 1080, wcm, hcm)

    def test_edid_parse(self):
        self.assertEqual(ds.parse_edid(edid("BOE", 0x0A1B, 29, 17)), ("BOE", "0a1b", 29, 17))
        self.assertEqual(ds.parse_edid(edid("DEL", 0xA0F5, 60, 34) + bytes(128))[:2], ("DEL", "a0f5"))
        for bad in (edid()[:100], edid(checksum_ok=False), bytes(128)):
            with self.assertRaises(ds.Skip):
                ds.parse_edid(bad)


class RunTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.m = Machine(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_laptop_panel_is_set_once_and_recorded(self):
        self.m.screen("eDP-1", edid("BOE", 0x0A1B, 30, 19), 2880, 1800)
        self.assertEqual(self.m.run(), 0)
        self.assertEqual(self.m.set_calls(), ["output.eDP-1.scale.1.75"])
        record = self.m.state()["outputs"]["eDP-1:BOE:0a1b"]
        self.assertEqual((record["action"], record["scale"], record["dpi"]), ("set", 1.75, 243))

    def test_second_run_changes_nothing(self):
        self.m.screen("eDP-1", edid("BOE", 0x0A1B, 29, 17), 1920, 1080)
        self.m.run()
        self.assertEqual(self.m.set_calls(), ["output.eDP-1.scale.1.5"])
        before = self.m.state()
        self.m.set_scale("eDP-1", 1.5)
        self.m.run()
        self.assertEqual(self.m.set_calls(), [])
        self.assertEqual(self.m.state(), before)

    def test_a_scale_the_user_changed_later_stays(self):
        self.m.screen("eDP-1", edid("BOE", 0x0A1B, 29, 17), 1920, 1080)
        self.m.run()
        self.m.set_scale("eDP-1", 1.0)   # the user went back to 100% in Display settings
        self.m.run()
        self.assertEqual(self.m.set_calls(), [])

    def test_a_scale_kwin_or_the_user_already_chose_is_kept(self):
        self.m.screen("eDP-1", edid("BOE", 0x0A1B, 29, 17), 1920, 1080, scale=1.2)
        self.m.run()
        self.assertEqual(self.m.set_calls(), [])
        self.assertEqual(self.m.state()["outputs"]["eDP-1:BOE:0a1b"], {"action": "kept", "scale": 1.2})

    def test_internal_and_external_screens_are_handled_apart(self):
        self.m.screen("eDP-1", edid("BOE", 0x0A1B, 30, 19), 2880, 1800)
        self.m.screen("DP-2", edid("DEL", 0xA0F5, 60, 34), 2560, 1440, card="card1")
        self.m.run()
        self.assertEqual(self.m.set_calls(), ["output.eDP-1.scale.1.75"])
        outputs = self.m.state()["outputs"]
        self.assertEqual(outputs["DP-2:DEL:a0f5"]["action"], "kept")
        self.assertEqual(outputs["DP-2:DEL:a0f5"]["scale"], 1.0)

    def test_a_different_monitor_on_the_same_port_is_new(self):
        self.m.screen("DP-2", edid("DEL", 0xA0F5, 60, 34), 2560, 1440)
        self.m.run()
        self.m = Machine(tempfile.mkdtemp(dir=self._tmp.name))
        self.m.home = Path(self._tmp.name) / "home"   # same home, same record file
        self.m.screen("DP-2", edid("GSM", 0x5B7F, 60, 34), 3840, 2160)
        self.m.run()
        self.assertEqual(self.m.set_calls(), ["output.DP-2.scale.1.5"])
        self.assertEqual(sorted(self.m.state()["outputs"]), ["DP-2:DEL:a0f5", "DP-2:GSM:5b7f"])

    def test_missing_or_bad_edid_leaves_the_screen_alone_and_unrecorded(self):
        self.m.screen("eDP-1", None, 2880, 1800)
        self.m.screen("HDMI-A-1", edid("SAM", 0x1234, 16, 9), 3840, 2160)
        self.m.screen("DP-1", edid("AUO", 0x1111, 0, 0), 1920, 1080)
        self.m.screen("DP-3", edid(checksum_ok=False), 1920, 1080)
        self.assertEqual(self.m.run(), 0)
        self.assertEqual(self.m.set_calls(), [])
        self.assertIsNone(self.m.state())
        for name in ("eDP-1", "HDMI-A-1", "DP-1", "DP-3"):
            self.assertIn(name + " left alone", self.m.stderr)

    def test_disconnected_and_disabled_outputs_are_ignored(self):
        self.m.screen("eDP-1", edid("BOE", 0x0A1B, 30, 19), 2880, 1800, enabled=False)
        self.m.screen("DP-1", edid("AUO", 0x1111, 30, 19), 2880, 1800, connected=False)
        self.m.run()
        self.assertEqual(self.m.set_calls(), [])

    def test_an_output_without_the_enabled_key_counts_as_on(self):
        # libkscreen writes "enabled" only when the output can be turned off.
        self.m.screen("eDP-1", edid("BOE", 0x0A1B, 30, 19), 2880, 1800, enabled=None)
        self.m.run()
        self.assertEqual(self.m.set_calls(), ["output.eDP-1.scale.1.75"])

    def test_a_failed_set_is_not_recorded_and_is_tried_next_login(self):
        self.m.screen("eDP-1", edid("BOE", 0x0A1B, 30, 19), 2880, 1800)
        self.m.extra_env["FAKE_SET_EXIT"] = "1"
        self.assertEqual(self.m.run(), 0)
        self.assertIsNone(self.m.state())
        self.assertIn("could not be set", self.m.stderr)
        del self.m.extra_env["FAKE_SET_EXIT"]
        self.m.run()
        self.assertEqual(self.m.set_calls(), ["output.eDP-1.scale.1.75"])

    def test_kscreen_doctor_failing_or_missing_ends_quietly(self):
        self.m.screen("eDP-1", edid("BOE", 0x0A1B, 30, 19), 2880, 1800)
        self.m.extra_env["FAKE_READ_EXIT"] = "3"
        self.assertEqual(self.m.run(), 0)
        self.assertIn("nothing changed", self.m.stderr)
        self.assertEqual(self.m.stderr.count("\n"), 1)
        del self.m.extra_env["FAKE_READ_EXIT"]
        self.assertEqual(self.m.run(use_path=False), 0)
        self.assertIn("nothing changed", self.m.stderr)
        self.assertIsNone(self.m.state())

    def test_a_damaged_record_file_is_started_over(self):
        path = self.m.home / ".config" / "nizho" / "display-scale.json"
        path.parent.mkdir(parents=True)
        path.write_text("{not json")
        self.m.screen("eDP-1", edid("BOE", 0x0A1B, 30, 19), 2880, 1800)
        self.assertEqual(self.m.run(), 0)
        self.assertEqual(self.m.set_calls(), ["output.eDP-1.scale.1.75"])
        self.assertIn("eDP-1:BOE:0a1b", self.m.state()["outputs"])

    def test_an_x11_session_is_left_alone(self):
        self.m.screen("eDP-1", edid("BOE", 0x0A1B, 30, 19), 2880, 1800)
        self.assertEqual(self.m.run(session="x11"), 0)
        self.assertEqual(self.m.calls(), [])


class TimeLimitTests(unittest.TestCase):
    def test_a_hanging_kscreen_doctor_cannot_hold_login(self):
        with tempfile.TemporaryDirectory() as tmp:
            m = Machine(tmp)
            m.screen("eDP-1", edid("BOE", 0x0A1B, 30, 19), 2880, 1800)
            m.config.write_text(json.dumps({"outputs": m.outputs}))
            old = (ds.COMMAND_TIMEOUT, ds.READ_ATTEMPTS)
            env = os.environ.copy()
            os.environ.update({"PATH": str(m.bin) + os.pathsep + "/usr/bin:/bin", "FAKE_LOG": str(m.log),
                               "FAKE_CONFIG": str(m.config), "FAKE_SLEEP": "5"})
            try:
                ds.COMMAND_TIMEOUT, ds.READ_ATTEMPTS = 0.3, 2
                with self.assertRaises(RuntimeError) as caught:
                    ds.read_outputs(sleep=lambda _: None)
                self.assertIn("TimeoutExpired", str(caught.exception))
            finally:
                ds.COMMAND_TIMEOUT, ds.READ_ATTEMPTS = old
                os.environ.clear()
                os.environ.update(env)

    def test_the_limits_fit_inside_a_login(self):
        self.assertLessEqual(ds.TOTAL_TIMEOUT, 30)
        self.assertLessEqual(ds.COMMAND_TIMEOUT, ds.TOTAL_TIMEOUT)


if __name__ == "__main__":
    unittest.main(verbosity=1)
