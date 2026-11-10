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

FLATHUB_FILE = os.path.normpath(os.path.join(HERE, "..", "integrations", "archon", "flathub.flatpakrepo"))

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
        self.assertIn("Talk to 2 other services on the desktop bus: "
                      "org.freedesktop.Notifications, org.kde.StatusNotifierWatcher", narrow)

    def test_negated_permissions_are_not_listed(self):
        _, narrow = nizho.parse_permissions(SAMPLE)
        self.assertFalse(any("Music" in s for s in narrow))

    def test_no_permissions(self):
        self.assertEqual(nizho.parse_permissions(""), ([], []))
        self.assertIn("asks for no extra access", nizho.describe("X", "", ""))

    NO_ACCESS = "asks for no extra access"

    def said(self, text):
        """The description of an app with this metadata, and its two lists."""
        return nizho.describe("X", "", text), *nizho.parse_permissions(text)

    def test_only_talking_to_one_service_is_not_no_access(self):
        text, wide, narrow = self.said("[Session Bus Policy]\norg.freedesktop.secrets=talk\n")
        self.assertNotIn(self.NO_ACCESS, text)
        self.assertEqual(narrow, ["Talk to 1 other service on the desktop bus: org.freedesktop.secrets"])

    def test_the_whole_system_bus_is_broad_access(self):
        text, wide, _ = self.said("[Context]\nsockets=system-bus;\n")
        self.assertNotIn(self.NO_ACCESS, text)
        self.assertEqual(wide, ["Talk to every service on the system bus, with no filter"])

    def test_the_whole_session_bus_is_broad_access(self):
        text, wide, _ = self.said("[Context]\nsockets=session-bus;\n")
        self.assertNotIn(self.NO_ACCESS, text)
        self.assertEqual(wide, ["Talk to every program and service on the desktop bus, with no filter"])

    def test_a_system_bus_name_is_listed(self):
        text, _, narrow = self.said("[System Bus Policy]\norg.freedesktop.UPower=talk\n")
        self.assertNotIn(self.NO_ACCESS, text)
        self.assertEqual(narrow, ["Talk to 1 other service on the system bus: org.freedesktop.UPower"])

    def test_talking_to_flatpak_itself_is_broad_access(self):
        text, wide, _ = self.said("[Session Bus Policy]\norg.freedesktop.Flatpak=talk\n")
        self.assertNotIn(self.NO_ACCESS, text)
        self.assertEqual(len(wide), 1)
        self.assertIn("outside its sandbox", wide[0])
        # The portal with a similar name is open to every app and is not the same thing.
        _, wide, narrow = self.said("[Session Bus Policy]\norg.freedesktop.portal.Flatpak=talk\n")
        self.assertEqual(wide, [])
        self.assertEqual(len(narrow), 1)

    def test_wildcard_names_are_broad_access(self):
        for policy, expect in (("*=talk", "Talk to every service on the desktop bus"),
                               ("org.kde.*=talk", "Talk to every service on the desktop bus whose name starts with org.kde."),
                               ("org.kde.*=own", "Take the name of every service on the desktop bus whose name starts with org.kde.")):
            text, wide, _ = self.said("[Session Bus Policy]\n" + policy + "\n")
            self.assertNotIn(self.NO_ACCESS, text, policy)
            self.assertEqual(wide, [expect])

    def test_owning_and_seeing_a_name_are_listed(self):
        text, _, narrow = self.said("[Session Bus Policy]\norg.example.A=own\norg.example.B=see\n")
        self.assertNotIn(self.NO_ACCESS, text)
        self.assertEqual(narrow, ["Take the name of 1 other service on the desktop bus: org.example.A",
                                  "See 1 other service on the desktop bus: org.example.B"])

    def test_a_hidden_name_alone_is_no_access(self):
        text, wide, narrow = self.said("[Session Bus Policy]\norg.example.Hidden=none\n")
        self.assertEqual((wide, narrow), ([], []))
        self.assertIn(self.NO_ACCESS, text)

    def test_the_session_folder_is_broad_access(self):
        text, wide, _ = self.said("[Context]\nfilesystems=xdg-run/pipewire-0:ro;\n")
        self.assertNotIn(self.NO_ACCESS, text)
        self.assertEqual(len(wide), 1)
        self.assertTrue(wide[0].startswith("Read pipewire-0 in your session folder"))

    def test_every_documented_permission_has_plain_words(self):
        # The names flatpak-metadata(5) lists for each key of [Context].
        documented = {
            "shared": "network ipc",
            "sockets": "x11 wayland fallback-x11 pulseaudio session-bus system-bus ssh-auth pcsc cups "
                       "gpg-agent inherit-wayland-socket",
            "devices": "dri input usb kvm all shm",
            "features": "devel multiarch bluetooth canbus per-app-dev-shm",
            "filesystems": "home home/a host host-os host-etc host-root xdg-desktop xdg-documents xdg-download "
                           "xdg-music xdg-pictures xdg-public-share xdg-videos xdg-templates xdg-documents/a "
                           "xdg-cache xdg-config xdg-data xdg-cache/a xdg-config/a xdg-data/a xdg-run/a /a ~/a ~ "
                           "home:ro home:rw ~/a:create",
        }
        for key, names in documented.items():
            for name in names.split():
                text, wide, narrow = self.said(f"[Context]\n{key}={name};\n")
                self.assertNotIn(self.NO_ACCESS, text, name)
                self.assertEqual(len(wide) + len(narrow), 1, name)
                self.assertNotIn("no plain words", text, name)

    def test_a_permission_without_plain_words_is_still_shown(self):
        for body in ("[Context]\nsockets=brand-new;\n", "[Context]\nfilesystems=xdg-run;\n",
                     "[Context]\nnew-key=1\n", "[Policy background]\nautostart=yes\n",
                     "[Session Bus Policy]\norg.example.A=everything\n"):
            text, wide, _ = self.said(body)
            self.assertNotIn(self.NO_ACCESS, text, body)
            self.assertEqual(len(wide), 1, body)
            self.assertIn("no plain words", wide[0], body)

    def test_other_permission_classes_are_listed(self):
        for body in ("[Context]\npersistent=.myapp;\n", "[USB Devices]\nenumerable-devices=all;\n"):
            text, wide, narrow = self.said(body)
            self.assertNotIn(self.NO_ACCESS, text, body)
            self.assertEqual(len(wide) + len(narrow), 1, body)

    def test_a_private_home_is_worded_as_one(self):
        _, _, narrow = self.said("[Context]\npersistent=.;\n")
        self.assertEqual(narrow, ["Keep its own private home folder, apart from yours"])

    def test_conditional_permissions_are_listed_and_a_never_true_one_is_not(self):
        _, wide, narrow = self.said("[Context]\nsockets=wayland;if:x11:!has-wayland;if:pcsc:false;\n")
        self.assertEqual(narrow, ["Show windows on your screen"])
        self.assertEqual(len(wide), 1)
        self.assertTrue(wide[0].endswith("(only in some sessions)"))

    def test_parts_that_grant_nothing_are_no_access(self):
        body = ("[Application]\nname=org.x.Y\nruntime=org.x.Platform/x86_64/1\n\n[Context]\nsockets=!x11;\n"
                "unset-environment=FOO;\n\n[Environment]\nA=b\n\n[Extension org.x.Y.Plugin]\ndirectory=p\n\n[X-DConf]\nmigrate-path=/a/\n")
        text, wide, narrow = self.said(body)
        self.assertEqual((wide, narrow), ([], []))
        self.assertIn(self.NO_ACCESS, text)

    def test_an_unreadable_permission_list_is_not_no_access(self):
        text, wide, _ = self.said("sockets=x11\n")
        self.assertNotIn(self.NO_ACCESS, text)
        self.assertEqual(len(wide), 1)
        self.assertIn("unknown", wide[0])

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
                    "if sys.argv[1] == 'remote-info':\n"
                    "    sys.stdout.write(os.environ.get('FAKE_METADATA', ''))\n"
                    "sys.exit(int(os.environ.get('FAKE_EXIT', '0')))\n")
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
        env = {"PATH": self.tmp.name + os.pathsep + os.environ["PATH"], "FAKE_LOG": self.log}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        catalog = mock.patch.object(nizho, "CATALOG", os.path.join(HERE, "apps.json"))
        catalog.start()
        self.addCleanup(catalog.stop)
        remote = mock.patch.object(nizho, "REMOTE_FILE", FLATHUB_FILE)
        remote.start()
        self.addCleanup(remote.stop)

    def all_calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return [json.loads(l) for l in f]

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

    def test_flathub_is_added_from_the_file_in_the_image_and_never_from_a_web_address(self):
        self.run_main("update")
        self.assertEqual(self.all_calls()[0], ["remote-add", "--user", "--if-not-exists", "flathub", FLATHUB_FILE])
        for call in self.all_calls():
            self.assertEqual([a for a in call if "://" in a], [])

    def test_without_the_file_nothing_is_asked_of_flatpak(self):
        with mock.patch.object(nizho, "REMOTE_FILE", os.path.join(self.tmp.name, "missing.flatpakrepo")):
            code, _, err = self.run_main("update")
        self.assertNotEqual(code, 0)
        self.assertEqual(self.all_calls(), [])
        self.assertIn("missing", err)

    def test_a_failed_add_stops_before_anything_else(self):
        os.environ["FAKE_EXIT"] = "1"
        code, _, err = self.run_main("info", "VLC")
        self.assertNotEqual(code, 0)
        self.assertEqual([c[0] for c in self.all_calls()], ["remote-add"])
        self.assertIn("couldn't add Flathub", err)

    def test_info_reads_from_flathub_and_a_bus_only_app_is_not_called_harmless(self):
        os.environ["FAKE_METADATA"] = "[Session Bus Policy]\norg.freedesktop.Flatpak=talk\n"
        code, out, _ = self.run_main("info", "VLC")
        self.assertEqual(code, 0)
        self.assertEqual(self.calls(), [["remote-info", "--user", "--show-metadata", "flathub", "org.videolan.VLC"]])
        self.assertNotIn("asks for no extra access", out)
        self.assertIn("Broad access:", out)

    def test_install_shows_the_list_then_installs_for_the_user_only(self):
        os.environ["FAKE_METADATA"] = "[Context]\nsockets=system-bus;\n"
        code, out, _ = self.run_main("install", "VLC", answer="y")
        self.assertEqual(code, 0)
        self.assertIn("Talk to every service on the system bus", out)
        self.assertEqual(self.calls()[-1], ["install", "--user", "-y", "--noninteractive", "flathub", "org.videolan.VLC"])

    def test_a_declined_install_installs_nothing(self):
        code, _, _ = self.run_main("install", "VLC", answer="n")
        self.assertEqual(code, 3)
        self.assertEqual([c[0] for c in self.calls()], ["remote-info"])


class ShippedRemote(unittest.TestCase):
    """The Flathub description the image ships is the one whose checksum the build checks."""

    def test_the_file_matches_its_recorded_checksum(self):
        import hashlib
        with open(FLATHUB_FILE, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        with open(os.path.join(os.path.dirname(FLATHUB_FILE), "flathub.sha256")) as f:
            self.assertEqual(f.read().split(), [digest, "flathub.flatpakrepo"])

    def test_the_file_names_flathub_and_carries_a_signing_key(self):
        import configparser
        cp = configparser.ConfigParser(interpolation=None)
        cp.read(FLATHUB_FILE)
        self.assertEqual(cp["Flatpak Repo"]["Url"], "https://dl.flathub.org/repo/")
        self.assertGreater(len(cp["Flatpak Repo"]["GPGKey"]), 1000)

    def test_the_image_puts_the_file_where_the_command_reads_it(self):
        with open(os.path.join(HERE, "..", "Containerfile")) as f:
            self.assertIn(f"COPY integrations/archon/flathub.flatpakrepo {nizho.REMOTE_FILE}\n", f.read())

    def test_the_command_holds_no_web_address_for_the_remote(self):
        with open(os.path.join(HERE, "nizho-install")) as f:
            self.assertNotIn("://", f.read())


if __name__ == "__main__":
    unittest.main()
