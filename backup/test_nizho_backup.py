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


if __name__ == "__main__":
    unittest.main()
