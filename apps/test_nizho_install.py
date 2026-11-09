import importlib.machinery
import importlib.util
import io
import json
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
loader = importlib.machinery.SourceFileLoader("nizho_install", os.path.join(HERE, "nizho-install"))
spec = importlib.util.spec_from_loader("nizho_install", loader)
nizho = importlib.util.module_from_spec(spec)
loader.exec_module(nizho)

SAMPLE = """[Context]
shared=network;ipc;
sockets=x11;pulseaudio;wayland;fallback-x11;
devices=dri;all;
filesystems=xdg-download:ro;xdg-documents;home;~/Projects:ro;!xdg-music;

[Session Bus Policy]
org.freedesktop.Notifications=talk
org.kde.StatusNotifierWatcher=talk
org.example.Hidden=none
"""


class Permissions(unittest.TestCase):
    def test_wide_and_narrow_are_split(self):
        wide, narrow = nizho.parse_permissions(SAMPLE)
        self.assertIn("Read and change your whole home folder", wide)
        self.assertTrue(any("every device" in s for s in wide))
        self.assertTrue(any("X11" in s for s in wide))
        self.assertIn("Connect to the internet", narrow)
        self.assertIn("Read your Downloads folder", narrow)
        self.assertIn("Read and change your Documents folder", narrow)
        self.assertIn("Read the folder ~/Projects", narrow)
        self.assertIn("Talk to 2 other services on the desktop bus", narrow)

    def test_negated_permissions_are_not_listed(self):
        _, narrow = nizho.parse_permissions(SAMPLE)
        self.assertFalse(any("Music" in s for s in narrow))

    def test_no_permissions(self):
        self.assertEqual(nizho.parse_permissions(""), ([], []))
        self.assertIn("asks for no extra access", nizho.describe("X", "", ""))

    def test_description_says_what_the_list_is_not(self):
        text = nizho.describe("VLC", "Plays video.", SAMPLE)
        self.assertIn("Broad access:", text)
        self.assertIn("don't make it safe", text)


class Catalog(unittest.TestCase):
    def test_catalog_file_is_valid_and_unique(self):
        import json
        with open(os.path.join(HERE, "apps.json")) as f:
            apps = json.load(f)
        ids = [a["id"] for a in apps]
        self.assertEqual(len(ids), len(set(ids)))
        for a in apps:
            self.assertTrue({"id", "name", "about", "category"} <= a.keys())
            self.assertIn(".", a["id"])

    def test_entries_follow_the_wording_rules(self):
        import json, re
        with open(os.path.join(HERE, "apps.json")) as f:
            apps = json.load(f)
        categories = {"Media", "Graphics", "Office", "Internet", "Utilities", "Developer"}
        banned = re.compile(r"full compatib|fully compatible|100%|drop-in|perfect|identical|exactly like|best |free of", re.I)
        names = [a["name"] for a in apps]
        self.assertEqual(len(names), len(set(names)))
        for a in apps:
            self.assertIn(a["category"], categories, a["id"])
            self.assertRegex(a["id"], r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_-]+)+$", a["id"])
            self.assertTrue(a["about"].endswith("."), a["id"])
            self.assertLessEqual(len(a["about"]), 130, a["id"])
            self.assertIsNone(banned.search(a["about"]), a["id"])
            self.assertNotIn("\u2014", a["about"], a["id"])
            self.assertNotIn(" - ", a["about"], a["id"])

    def test_office_and_creative_stand_ins_are_listed(self):
        import json
        with open(os.path.join(HERE, "apps.json")) as f:
            listed = {a["id"] for a in json.load(f)}
        wanted = {
            "org.onlyoffice.desktopeditors", "org.libreoffice.LibreOffice", "org.gimp.GIMP",
            "org.kde.krita", "org.inkscape.Inkscape", "org.kde.kdenlive",
            "org.darktable.Darktable", "net.scribus.Scribus", "com.github.jeromerobert.pdfarranger",
        }
        self.assertEqual(wanted - listed, set())

    def test_stand_in_entries_never_claim_full_compatibility(self):
        import json
        with open(os.path.join(HERE, "apps.json")) as f:
            apps = {a["id"]: a for a in json.load(f)}
        for i in ("org.onlyoffice.desktopeditors", "org.libreoffice.LibreOffice"):
            self.assertIn("can differ", apps[i]["about"])
        for i in ("org.gimp.GIMP", "org.inkscape.Inkscape", "net.scribus.Scribus", "org.darktable.Darktable"):
            self.assertIn("in place of", apps[i]["about"])

    def test_every_capsule_app_is_on_the_list(self):
        # Capsule installs through nizho, so an app on its grid must be on nizho's list too.
        import json, re
        capsule = os.path.join(HERE, "..", "nizho-shell", "nizho-capsule", "src", "main.rs")
        with open(capsule) as f:
            ids = re.findall(r'CatalogApp \{[^}]*?id: "([^"]+)"', f.read())
        with open(os.path.join(HERE, "apps.json")) as f:
            listed = {a["id"] for a in json.load(f)}
        self.assertGreaterEqual(len(ids), 10)
        self.assertEqual([i for i in ids if i not in listed], [])

    def test_find_by_name_or_id_ignores_case(self):
        cat = [{"id": "org.videolan.VLC", "name": "VLC", "about": "", "category": "Media"}]
        self.assertEqual(nizho.find("vlc", cat)["id"], "org.videolan.VLC")
        self.assertEqual(nizho.find("ORG.VIDEOLAN.VLC", cat)["name"], "VLC")
        self.assertIsNone(nizho.find("nope", cat))


class RemoveAndUpdate(unittest.TestCase):
    """remove and update, run against a fake flatpak that records what it was asked to do."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = os.path.join(self.tmp.name, "calls")
        fake = os.path.join(self.tmp.name, "flatpak")
        with open(fake, "w") as f:
            f.write("#!/usr/bin/env python3\nimport json, os, sys\n"
                    "open(os.environ['FAKE_LOG'], 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
                    "sys.exit(int(os.environ.get('FAKE_EXIT', '0')))\n")
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
        env = {"PATH": self.tmp.name + os.pathsep + os.environ["PATH"], "FAKE_LOG": self.log}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        catalog = mock.patch.object(nizho, "CATALOG", os.path.join(HERE, "apps.json"))
        catalog.start()
        self.addCleanup(catalog.stop)

    def calls(self):
        """What was asked of flatpak, without the one-off check that Flathub is set up."""
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return [c for c in (json.loads(l) for l in f) if c[0] != "remote-add"]

    def run_main(self, *argv, answer=None, tty=True):
        out, err = io.StringIO(), io.StringIO()
        stdin = mock.Mock(isatty=lambda: tty)
        with redirect_stdout(out), redirect_stderr(err), mock.patch("sys.stdin", stdin), \
                mock.patch("builtins.input", lambda *_: answer if answer is not None else ""):
            try:
                code = nizho.main(list(argv))
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def test_remove_asks_first_and_a_no_removes_nothing(self):
        code, out, _ = self.run_main("remove", "org.videolan.VLC", answer="n")
        self.assertEqual(code, 3)
        self.assertEqual(self.calls(), [])
        self.assertIn("Nothing was removed", out)

    def test_remove_after_a_yes_removes_for_the_user_only(self):
        code, _, _ = self.run_main("remove", "org.videolan.VLC", answer="y")
        self.assertEqual(code, 0)
        self.assertEqual(self.calls(), [["uninstall", "--user", "-y", "org.videolan.VLC"]])

    def test_remove_with_yes_does_not_ask(self):
        code, _, _ = self.run_main("remove", "VLC", "--yes", tty=False)
        self.assertEqual(code, 0)
        self.assertEqual(self.calls(), [["uninstall", "--user", "-y", "org.videolan.VLC"]])

    def test_remove_will_not_run_unasked_without_a_terminal(self):
        code, _, err = self.run_main("remove", "VLC", tty=False)
        self.assertNotEqual(code, 0)
        self.assertEqual(self.calls(), [])
        self.assertIn("won't remove without asking", err)

    def test_remove_data_deletes_the_apps_saved_data_too(self):
        self.run_main("remove", "VLC", "--data", "--yes", tty=False)
        self.assertEqual(self.calls(), [["uninstall", "--user", "-y", "--delete-data", "org.videolan.VLC"]])

    def test_a_failed_remove_is_not_reported_as_done(self):
        os.environ["FAKE_EXIT"] = "1"
        code, _, _ = self.run_main("remove", "VLC", "--yes", tty=False)
        self.assertEqual(code, 1)

    def test_update_all_updates_the_users_apps_only(self):
        code, _, _ = self.run_main("update")
        self.assertEqual(code, 0)
        self.assertEqual(self.calls(), [["update", "--user", "-y", "--noninteractive"]])

    def test_update_one_app_updates_just_that_app(self):
        self.run_main("update", "VLC")
        self.assertEqual(self.calls(), [["update", "--user", "-y", "--noninteractive", "org.videolan.VLC"]])

    def test_a_failed_update_is_not_reported_as_done(self):
        os.environ["FAKE_EXIT"] = "1"
        code, _, _ = self.run_main("update")
        self.assertEqual(code, 1)

    def test_update_does_not_take_an_option_as_an_app_name(self):
        code, _, err = self.run_main("update", "--remote=evil")
        self.assertNotEqual(code, 0)
        self.assertEqual(self.calls(), [])

if __name__ == "__main__":
    unittest.main()
