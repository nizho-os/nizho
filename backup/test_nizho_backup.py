import contextlib
import datetime as dt
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
loader = importlib.machinery.SourceFileLoader("nizho_backup", os.path.join(HERE, "nizho-backup"))
spec = importlib.util.spec_from_loader("nizho_backup", loader)
nb = importlib.util.module_from_spec(spec)
loader.exec_module(nb)

RESTIC = os.environ.get("NIZHO_RESTIC") or shutil.which("restic")


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = os.path.join(self.tmp, "home")
        self.drive = os.path.join(self.tmp, "drive")
        os.makedirs(self.home)
        os.makedirs(self.drive)
        self.old = dict(os.environ)
        os.environ["HOME"] = self.home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.home, ".config")

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = nb.main(list(args))
        return code, out.getvalue(), err.getvalue()


class Rules(Env):
    def test_ago_wording(self):
        now = dt.datetime.now()
        self.assertEqual(nb.ago((now - dt.timedelta(seconds=20)).isoformat()), "just now")
        self.assertEqual(nb.ago((now - dt.timedelta(minutes=30)).isoformat()), "30 minutes ago")
        self.assertEqual(nb.ago((now - dt.timedelta(hours=5)).isoformat()), "5 hours ago")
        self.assertEqual(nb.ago((now - dt.timedelta(days=3)).isoformat()), "3 days ago")
        self.assertEqual(nb.ago("garbage"), "unknown")

    def test_status_before_setup(self):
        code, out, _ = self.run_cli("status")
        self.assertEqual((code, out.strip()), (0, "Backups are not set up."))

    def test_commands_need_setup(self):
        for c in (["now"], ["list"], ["tidy"], ["restore", "a", "--to", self.tmp + "/r"]):
            code, _, err = self.run_cli(*c)
            self.assertEqual(code, 1, c)
            self.assertIn("not set up", err)

    def test_refuses_backup_folder_inside_home(self):
        code, _, err = self.run_cli("setup", "--to", os.path.join(self.home, "backups"))
        self.assertEqual(code, 1)
        self.assertIn("inside your home folder", err)
        self.assertFalse(os.path.exists(nb.key_path()))

    def test_refuses_backup_folder_that_is_a_symlink_into_home(self):
        os.makedirs(os.path.join(self.home, "real"))
        link = os.path.join(self.drive, "link")
        os.symlink(os.path.join(self.home, "real"), link)
        code, _, err = self.run_cli("setup", "--to", os.path.join(link, "repo"))
        self.assertEqual(code, 1)
        self.assertIn("inside your home folder", err)
        self.assertFalse(os.path.exists(nb.key_path()))

    def test_key_removed_when_setup_is_interrupted_or_fails_late(self):
        os.environ["NIZHO_RESTIC"] = "/bin/true"
        for exc in (KeyboardInterrupt(), OSError("disk full")):
            with mock.patch.object(nb, "save_config", side_effect=exc):
                try:
                    self.run_cli("setup", "--to", self.drive)
                except (KeyboardInterrupt, OSError):
                    pass
            self.assertFalse(os.path.exists(nb.key_path()), repr(exc))

    def test_existing_key_without_settings_is_kept(self):
        os.makedirs(os.path.dirname(nb.key_path()), exist_ok=True)
        with open(nb.key_path(), "w") as f:
            f.write("precious\n")
        code, _, err = self.run_cli("setup", "--to", self.drive)
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)
        with open(nb.key_path()) as f:
            self.assertEqual(f.read(), "precious\n")

    def test_second_instance_is_refused_while_one_runs(self):
        import fcntl
        nb.save_config({"repo": self.drive, "paths": [self.home]})
        open(os.path.join(self.drive, "config"), "w").close()
        fd = os.open(nb.lock_path(), os.O_WRONLY | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            code, _, err = self.run_cli("now")
            self.assertEqual(code, 1)
            self.assertIn("already running", err)
            self.assertEqual(self.run_cli("now", "--quiet"), (0, "", ""))
            self.assertEqual(self.run_cli("status")[0], 0, "status does not need the lock")
        finally:
            os.close(fd)

    def test_ordinary_mistakes_give_plain_errors_not_tracebacks(self):
        nb.save_config({"repo": self.drive, "paths": [self.home]})
        open(os.path.join(self.drive, "config"), "w").close()
        afile = os.path.join(self.tmp, "afile")
        open(afile, "w").close()
        code, _, err = self.run_cli("restore", "a", "--to", afile)
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", err)
        code, _, err = self.run_cli("restore", "a", "--to", os.path.join(self.tmp, "r"), "--snapshot=--password-file=x")
        self.assertEqual(code, 1)
        self.assertIn("snapshot", err)
        os.makedirs(os.path.join(self.tmp, "ro"), mode=0o500)
        if os.geteuid() != 0:
            code, _, err = self.run_cli("restore", "a", "--to", os.path.join(self.tmp, "ro", "x"))
            self.assertEqual(code, 1)
            self.assertNotIn("Traceback", err)
        with open(nb.config_path(), "w") as f:
            f.write("[1, 2]")
        for c in (["status"], ["now"], ["list"]):
            code, _, err = self.run_cli(*c)
            self.assertEqual(code, 1, c)
            self.assertIn("damaged", err)

    def test_a_backup_dated_in_the_future_is_not_called_recent(self):
        future = (dt.datetime.now() + dt.timedelta(days=30)).isoformat()
        self.assertEqual(nb.ago(future), "unknown")

    def test_missing_restic_is_said_plainly(self):
        os.environ["NIZHO_RESTIC"] = ""
        os.environ["PATH"] = "/nonexistent"
        code, _, err = self.run_cli("setup", "--to", self.drive)
        self.assertEqual(code, 1)
        self.assertIn("restic is not installed", err)
        self.assertFalse(os.path.exists(nb.key_path()), "no key file is left behind after a failed setup")

    def test_quiet_run_with_drive_missing_is_silent(self):
        nb.save_config({"repo": os.path.join(self.tmp, "gone"), "paths": [self.home]})
        code, out, err = self.run_cli("now", "--quiet")
        self.assertEqual((code, out, err), (0, "", ""))

    def test_status_when_drive_missing(self):
        nb.save_config({"repo": os.path.join(self.tmp, "gone"), "paths": [self.home],
                        "last_ok": (dt.datetime.now() - dt.timedelta(days=2)).isoformat()})
        _, out, _ = self.run_cli("status")
        self.assertIn("2 days ago", out)
        self.assertIn("not connected", out)

    def test_restore_rejects_paths_outside_home(self):
        nb.save_config({"repo": self.drive, "paths": [self.home]})
        open(os.path.join(self.drive, "config"), "w").close()
        for bad in ("/etc/passwd", "../outside"):
            code, _, err = self.run_cli("restore", bad, "--to", os.path.join(self.tmp, "r"))
            self.assertEqual(code, 1, bad)


@unittest.skipUnless(RESTIC, "needs a real restic (set NIZHO_RESTIC)")
class RealRestic(Env):
    def setUp(self):
        super().setUp()
        os.environ["NIZHO_RESTIC"] = RESTIC
        os.makedirs(os.path.join(self.home, "Documents"))
        with open(os.path.join(self.home, "Documents", "report.txt"), "w") as f:
            f.write("quarterly numbers\n")
        os.makedirs(os.path.join(self.home, ".cache", "junk"))
        with open(os.path.join(self.home, ".cache", "junk", "big.bin"), "w") as f:
            f.write("cache")
        self.repo = os.path.join(self.drive, "nizho-backup")

    def test_round_trip(self):
        code, out, err = self.run_cli("setup", "--to", self.repo)
        self.assertEqual(code, 0, err)
        with open(nb.key_path()) as kf:
            key = kf.read().strip()
        self.assertIn(key, out, "the recovery key is shown once")
        self.assertEqual(oct(os.stat(nb.key_path()).st_mode & 0o777), "0o600")
        code, _, err = self.run_cli("now")
        self.assertEqual(code, 0, err)
        code, out, _ = self.run_cli("list")
        self.assertEqual(len(out.strip().splitlines()), 1)
        _, out, _ = self.run_cli("status", "--json")
        info = json.loads(out)
        self.assertTrue(info["connected"])
        self.assertEqual(info["text"], "Last backup: just now.")
        # lose the file, then get it back without touching anything else
        os.remove(os.path.join(self.home, "Documents", "report.txt"))
        target = os.path.join(self.tmp, "restored")
        code, out, err = self.run_cli("restore", "Documents/report.txt", "--to", target)
        self.assertEqual(code, 0, err)
        exact = os.path.join(target, "Documents", "report.txt")
        self.assertIn("Restored to " + exact, out, "the message names the exact restored file")
        self.assertTrue(os.path.isfile(exact))
        found = []
        for root, _, files in os.walk(target):
            found += [os.path.join(root, f) for f in files]
        self.assertEqual(found, [exact], "only the restored file is left, no staging folders")
        with open(found[0]) as rf:
            self.assertEqual(rf.read(), "quarterly numbers\n")
        self.assertFalse(os.path.exists(os.path.join(self.home, "Documents", "report.txt")),
                         "restore does not write into the home folder")
        # caches are not backed up
        ls = subprocess.run([RESTIC, "-r", self.repo, "--password-file", nb.key_path(), "ls", "latest"],
                            capture_output=True, text=True).stdout
        self.assertNotIn("big.bin", ls)
        self.assertIn("report.txt", ls)
        # restore refuses a non-empty folder
        code, _, err = self.run_cli("restore", "Documents", "--to", target)
        self.assertEqual(code, 1)
        self.assertIn("not empty", err)

    def test_repository_is_unreadable_without_the_key(self):
        self.run_cli("setup", "--to", self.repo)
        self.run_cli("now")
        wrong = os.path.join(self.tmp, "wrong.key")
        with open(wrong, "w") as wf:
            wf.write("not the key\n")
        p = subprocess.run([RESTIC, "-r", self.repo, "--password-file", wrong, "snapshots"],
                           capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        raw = b""
        for root, _, files in os.walk(self.repo):
            for f in files:
                with open(os.path.join(root, f), "rb") as fh:
                    raw += fh.read()
        self.assertNotIn(b"quarterly numbers", raw, "file contents are not stored in the clear")
        self.assertNotIn(b"report.txt", raw, "file names are not stored in the clear")

    def test_restoring_a_folder_and_a_missing_name(self):
        os.makedirs(os.path.join(self.home, "Pictures", "trip"))
        with open(os.path.join(self.home, "Pictures", "trip", "a.txt"), "w") as f:
            f.write("a")
        self.run_cli("setup", "--to", self.repo)
        self.run_cli("now")
        target = os.path.join(self.tmp, "r2")
        code, _, err = self.run_cli("restore", "Pictures/trip", "--to", target)
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isfile(os.path.join(target, "Pictures", "trip", "a.txt")))
        code, _, err = self.run_cli("restore", "Nothing/here.txt", "--to", os.path.join(self.tmp, "r3"))
        self.assertEqual(code, 1)
        self.assertIn("was not found", err)
        code, _, err = self.run_cli("restore", ".", "--to", os.path.join(self.tmp, "r4"))
        self.assertEqual(code, 1)
        self.assertIn("not the whole home folder", err)

    def test_unreadable_file_still_counts_but_says_so(self):
        if os.geteuid() == 0:
            self.skipTest("root can read everything")
        self.run_cli("setup", "--to", self.repo)
        secret = os.path.join(self.home, "Documents", "locked.txt")
        with open(secret, "w") as f:
            f.write("x")
        os.chmod(secret, 0)
        code, _, err = self.run_cli("now")
        self.assertEqual(code, 0, err)
        self.assertIn("some files could not be read", err)
        _, out, _ = self.run_cli("status", "--json")
        self.assertIsNotNone(json.loads(out)["last_ok"])
        self.assertEqual(len(self.run_cli("list")[1].strip().splitlines()), 1)

    def test_drive_unplugged_during_quiet_run_stays_quiet_and_unmarked(self):
        self.run_cli("setup", "--to", self.repo)
        fake = os.path.join(self.tmp, "fake-restic")
        with open(fake, "w") as f:
            f.write('#!/bin/sh\nrm -rf "$RESTIC_REPOSITORY"\necho boom >&2\nexit 1\n')
        os.chmod(fake, 0o755)
        os.environ["NIZHO_RESTIC"] = fake
        self.assertEqual(self.run_cli("now", "--quiet"), (0, "", ""))
        self.assertIsNone(nb.load_config().get("last_ok"))
        code, _, err = self.run_cli("now")
        self.assertEqual(code, 1, "without --quiet an unplugged drive is still reported")

    def test_restoring_names_with_pattern_characters(self):
        names = ["trip [2020]", "what?", "star*", "back\\slash", "-rf"]
        for n in names:
            os.makedirs(os.path.join(self.home, "Pictures", n))
            with open(os.path.join(self.home, "Pictures", n, "a.txt"), "w") as f:
                f.write("in " + n)
        self.run_cli("setup", "--to", self.repo)
        self.run_cli("now")
        for i, n in enumerate(names):
            target = os.path.join(self.tmp, "r%d" % i)
            code, _, err = self.run_cli("restore", "Pictures/" + n, "--to", target)
            self.assertEqual(code, 0, (n, err))
            with open(os.path.join(target, "Pictures", n, "a.txt")) as f:
                self.assertEqual(f.read(), "in " + n)
        target = os.path.join(self.tmp, "only")
        self.assertEqual(self.run_cli("restore", "Pictures/trip [2020]", "--to", target)[0], 0)
        self.assertFalse(os.path.exists(os.path.join(target, "Pictures", "what?")))

    def test_second_setup_and_reused_folder_are_refused(self):
        self.run_cli("setup", "--to", self.repo)
        code, _, err = self.run_cli("setup", "--to", self.repo)
        self.assertEqual(code, 1)
        self.assertIn("already set up", err)

    def test_tidy_keeps_the_newest(self):
        self.run_cli("setup", "--to", self.repo)
        self.run_cli("now")
        self.run_cli("now")
        code, out, err = self.run_cli("tidy")
        self.assertEqual(code, 0, err)
        _, out, _ = self.run_cli("list")
        self.assertGreaterEqual(len(out.strip().splitlines()), 1)


@unittest.skipUnless(RESTIC, "needs a real restic (set NIZHO_RESTIC)")
class Browse(Env):
    def setUp(self):
        super().setUp()
        os.environ["NIZHO_RESTIC"] = RESTIC
        self.repo = os.path.join(self.drive, "nizho-backup")
        self.put("Documents/report.odt", "r")
        self.put("Documents/Zeta.txt", "zz")
        self.put("Documents/alpha.txt", "a")
        self.put("Documents/Old/2019/tax.pdf", "t")
        self.put("Documents/Ärger notes.txt", "u")
        self.put("Documents/my file (1).txt", "s")
        self.put("zebra.txt", "z")
        os.makedirs(os.path.join(self.home, "Documents", "Empty"))
        self.assertEqual(self.run_cli("setup", "--to", self.repo)[0], 0)
        self.assertEqual(self.run_cli("now")[0], 0)

    def put(self, rel, text):
        p = os.path.join(self.home, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)

    def ls(self, *args):
        code, out, err = self.run_cli("ls", "--json", *args)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def names(self, *args):
        return [e["name"] for e in self.ls(*args)]

    def test_lists_the_home_folder_and_a_folder(self):
        top = self.names()
        self.assertIn("Documents", top)
        self.assertIn("zebra.txt", top)
        self.assertNotIn("report.odt", top, "only direct entries, not nested ones")
        self.assertEqual(self.names("."), top)
        self.assertEqual(self.names("Documents/"), self.names("Documents"))

    def test_nested_folder(self):
        self.assertEqual(self.names("Documents/Old"), ["2019"])
        self.assertEqual(self.names("Documents/Old/2019"), ["tax.pdf"])

    def test_sort_is_folders_first_then_names_ignoring_case(self):
        self.assertEqual(self.names("Documents"),
                         ["Empty", "Old", "alpha.txt", "my file (1).txt", "report.odt", "Zeta.txt", "Ärger notes.txt"])

    def test_unicode_and_spaces(self):
        by = {e["name"]: e for e in self.ls("Documents")}
        self.assertIn("Ärger notes.txt", by)
        self.assertEqual(self.names("Documents").count("my file (1).txt"), 1)
        code, out, _ = self.run_cli("ls", "Documents")
        self.assertIn("Ärger notes.txt", out)

    def test_json_shape(self):
        items = self.ls("Documents")
        for e in items:
            self.assertEqual(set(e), {"name", "type", "size", "mtime"})
            self.assertIn(e["type"], ("file", "dir"))
            self.assertIsInstance(e["size"], int)
        by = {e["name"]: e for e in items}
        self.assertEqual(by["Zeta.txt"]["size"], 2)
        self.assertEqual(by["Old"]["type"], "dir")
        self.assertRegex(by["Zeta.txt"]["mtime"], r"^\d{4}-\d\d-\d\dT")

    def test_plain_output_marks_folders(self):
        code, out, _ = self.run_cli("ls", "Documents")
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("dir") and lines[0].endswith("Empty/"))
        self.assertTrue(lines[2].startswith("file") and lines[2].endswith("alpha.txt"))

    def test_file_deleted_since_the_backup_is_still_listed(self):
        os.remove(os.path.join(self.home, "Documents", "report.odt"))
        self.assertIn("report.odt", self.names("Documents"))

    def test_empty_folder(self):
        self.assertEqual(self.ls("Documents/Empty"), [])
        code, out, _ = self.run_cli("ls", "Documents/Empty")
        self.assertEqual((code, out.strip()), (0, "This folder is empty."))

    def test_a_file_path_is_refused_plainly(self):
        code, _, err = self.run_cli("ls", "Documents/report.odt")
        self.assertEqual(code, 1)
        self.assertIn("is a file, not a folder", err)

    def test_missing_path(self):
        code, _, err = self.run_cli("ls", "Nothing/here")
        self.assertEqual(code, 1)
        self.assertIn("not found in that backup", err)

    def test_dotdot_and_absolute_paths_are_refused(self):
        for bad in ("..", "../x", "Documents/../..", "/etc", self.home):
            code, _, err = self.run_cli("ls", bad)
            self.assertEqual(code, 1, bad)
            self.assertIn("relative to your home folder", err)

    def test_an_option_looking_name_is_not_an_option(self):
        self.put("-rf/inside.txt", "x")
        self.run_cli("now")
        code, out, err = self.run_cli("ls", "--json", "--", "-rf")
        self.assertEqual(code, 0, err)
        self.assertEqual([e["name"] for e in json.loads(out)], ["inside.txt"])

    def test_two_snapshots_show_what_changed(self):
        _, out, _ = self.run_cli("snapshots", "--json")
        first = json.loads(out)
        self.assertEqual(len(first), 1)
        os.remove(os.path.join(self.home, "Documents", "alpha.txt"))
        self.put("Documents/new.txt", "n")
        self.assertEqual(self.run_cli("now")[0], 0)
        _, out, _ = self.run_cli("snapshots", "--json")
        snaps = json.loads(out)
        self.assertEqual(len(snaps), 2)
        self.assertGreater(snaps[0]["time"], snaps[1]["time"], "newest first")
        self.assertEqual(snaps[1]["id"], first[0]["id"])
        old = self.names("Documents", "--snapshot", snaps[1]["id"])
        new = self.names("Documents")
        self.assertIn("alpha.txt", old)
        self.assertNotIn("new.txt", old)
        self.assertNotIn("alpha.txt", new)
        self.assertIn("new.txt", new)
        self.assertEqual(self.names("Documents", "--snapshot", snaps[1]["id"][:8]), old)
        code, _, err = self.run_cli("ls", "--snapshot", "deadbeef")
        self.assertEqual(code, 1)
        self.assertIn("was not found", err)

    def test_home_reached_through_a_symlink(self):
        link = os.path.join(self.tmp, "homelink")
        os.symlink(self.home, link)
        os.environ["HOME"] = link
        self.assertIn("Documents", self.names())
        self.assertIn("alpha.txt", self.names("Documents"))

    def test_no_backups_yet_and_drive_missing(self):
        other = os.path.join(self.tmp, "second")
        nb.save_config({"repo": other, "paths": [self.home]})
        os.makedirs(other)
        env = dict(os.environ, RESTIC_REPOSITORY=other, RESTIC_PASSWORD_FILE=nb.key_path())
        subprocess.run([RESTIC, "init", "-q"], env=env, check=True, capture_output=True)
        code, out, _ = self.run_cli("ls")
        self.assertEqual((code, out.strip()), (0, "There are no backups yet."))
        code, out, _ = self.run_cli("ls", "--json")
        self.assertEqual((code, json.loads(out)), (0, []))
        shutil.rmtree(other)
        for cmd in (["ls"], ["snapshots", "--json"]):
            code, _, err = self.run_cli(*cmd)
            self.assertEqual(code, 1)
            self.assertIn("drive is not connected", err)

    def test_ls_before_setup(self):
        os.remove(nb.config_path())
        code, _, err = self.run_cli("ls")
        self.assertEqual(code, 1)
        self.assertIn("not set up", err)


if __name__ == "__main__":
    unittest.main()
