import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
import os
import shutil
import stat
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
loader = importlib.machinery.SourceFileLoader("nizho_import", os.path.join(HERE, "nizho-import"))
spec = importlib.util.spec_from_loader("nizho_import", loader)
ni = importlib.util.module_from_spec(spec)
loader.exec_module(ni)


def write(path, data=b"x", mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    if mtime:
        os.utime(path, (mtime, mtime))


def rd(path):
    with open(path, "rb") as f:
        return f.read()


def snapshot(root):
    """Every path under root with type, mode, mtime, size and a hash of file bytes."""
    snap = {}
    for dp, dn, fn in os.walk(root):
        for name in dn + fn:
            p = os.path.join(dp, name)
            st = os.lstat(p)
            digest = None
            if stat.S_ISREG(st.st_mode):
                with open(p, "rb") as f:
                    digest = hashlib.sha256(f.read()).hexdigest()
            snap[p] = (stat.S_IFMT(st.st_mode), st.st_mode, st.st_mtime_ns, st.st_size, digest)
    return snap


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = os.path.join(self.tmp, "home")
        self.src = os.path.join(self.tmp, "drive")
        os.makedirs(self.home)
        os.makedirs(self.src)
        self.old = dict(os.environ)
        os.environ["HOME"] = self.home

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old)
        for dp, dn, fn in os.walk(self.tmp):
            for n in dn:
                try:
                    os.chmod(os.path.join(dp, n), 0o755)
                except OSError:
                    pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err):
            code = ni.main(list(args), out)
        return code, out.getvalue(), err.getvalue()

    def win(self):
        u = os.path.join(self.src, "Users", "Sam")
        os.makedirs(os.path.join(u, "AppData"))
        for f in ni.WIN_FOLDERS:
            os.makedirs(os.path.join(u, f))
        return u

    def mac(self):
        for f in ("Documents", "Pictures", "Movies", "Music", "Desktop"):
            os.makedirs(os.path.join(self.src, f))
        return self.src

    def h(self, *p):
        return os.path.join(self.home, *p)


class Layouts(Env):
    def test_windows(self):
        u = self.win()
        write(os.path.join(u, "Documents", "a.txt"), b"doc")
        write(os.path.join(u, "Videos", "v.mp4"), b"vid")
        write(os.path.join(self.src, "Users", "Public", "Documents", "p.txt"), b"pub")
        code, out, _ = self.run_cli("files", self.src)
        self.assertEqual(code, 0)
        self.assertIn("Windows drive", out)
        self.assertEqual(rd(self.h("Documents", "a.txt")), b"doc")
        self.assertTrue(os.path.exists(self.h("Videos", "v.mp4")))
        self.assertFalse(os.path.exists(self.h("Documents", "p.txt")))

    def test_macos_movies_to_videos(self):
        self.mac()
        write(os.path.join(self.src, "Movies", "m.mov"), b"mov")
        write(os.path.join(self.src, "Documents", "n.txt"), b"n")
        code, out, _ = self.run_cli("files", self.src)
        self.assertEqual(code, 0)
        self.assertIn("macOS", out)
        self.assertEqual(rd(self.h("Videos", "m.mov")), b"mov")
        self.assertFalse(os.path.exists(self.h("Movies")))

    def test_mac_volume_with_users(self):
        u = os.path.join(self.src, "Users", "sam")
        os.makedirs(os.path.join(u, "Library"))
        write(os.path.join(u, "Movies", "m.mov"), b"m")
        code, out, _ = self.run_cli("files", self.src)
        self.assertIn("macOS", out)
        self.assertTrue(os.path.exists(self.h("Videos", "m.mov")))

    def test_plain_folder(self):
        write(os.path.join(self.src, "sub", "f.txt"), b"f")
        code, out, _ = self.run_cli("files", self.src)
        self.assertEqual(code, 0)
        self.assertIn("plain folder", out)
        self.assertEqual(rd(self.h("Imported", "drive", "sub", "f.txt")), b"f")

    def test_scan_summary_and_notice(self):
        u = self.win()
        write(os.path.join(u, "Pictures", "p.jpg"), b"1234")
        before = snapshot(self.home)
        code, out, _ = self.run_cli("scan", self.src)
        self.assertEqual(code, 0)
        self.assertIn("Windows drive", out)
        self.assertIn("1 files", out)
        self.assertIn("Import Data from Another Browser", out)
        self.assertIn("not imported", out.lower())
        self.assertEqual(before, snapshot(self.home))


class Conflicts(Env):
    def test_rename_sequence_and_never_overwrite(self):
        write(os.path.join(self.src, "r.txt"), b"new")
        write(self.h("Imported", "drive", "r.txt"), b"mine")
        self.assertEqual(self.run_cli("files", self.src)[0], 0)
        d = self.h("Imported", "drive")
        self.assertEqual(rd(os.path.join(d, "r.txt")), b"mine")
        self.assertEqual(rd(os.path.join(d, "r (imported).txt")), b"new")
        # different content again under the same name
        write(os.path.join(self.src, "r.txt"), b"newer")
        self.assertEqual(self.run_cli("files", self.src)[0], 0)
        self.assertEqual(rd(os.path.join(d, "r (imported 2).txt")), b"newer")
        write(os.path.join(self.src, "r.txt"), b"newest")
        self.run_cli("files", self.src)
        self.assertEqual(rd(os.path.join(d, "r (imported 3).txt")), b"newest")
        self.assertEqual(rd(os.path.join(d, "r.txt")), b"mine")

    def test_identical_skipped_and_counted(self):
        write(os.path.join(self.src, "a.txt"), b"same")
        write(self.h("Imported", "drive", "a.txt"), b"same")
        code, out, _ = self.run_cli("files", self.src)
        self.assertEqual(code, 0)
        self.assertIn("Already there (identical content, skipped): 1", out)
        self.assertIn("Copied 0 files", out)
        self.assertEqual(os.listdir(self.h("Imported", "drive")), ["a.txt"])

    def test_same_size_different_content_is_kept(self):
        write(os.path.join(self.src, "a.txt"), b"aaaa")
        write(self.h("Imported", "drive", "a.txt"), b"bbbb")
        self.run_cli("files", self.src)
        self.assertEqual(rd(self.h("Imported", "drive", "a (imported).txt")), b"aaaa")

    def test_rerun_is_idempotent(self):
        write(os.path.join(self.src, "a.txt"), b"one")
        write(self.h("Imported", "drive", "a.txt"), b"mine")
        self.run_cli("files", self.src)
        code, out, _ = self.run_cli("files", self.src)
        self.assertIn("Copied 0 files", out)
        self.assertIn("Already there (identical content, skipped): 1", out)
        self.assertEqual(len(os.listdir(self.h("Imported", "drive"))), 2)

    def test_two_users_same_name_both_kept(self):
        for n, data in (("A", b"1"), ("B", b"2")):
            u = os.path.join(self.src, "Users", n)
            os.makedirs(os.path.join(u, "AppData"))
            write(os.path.join(u, "Documents", "x.txt"), data)
        self.run_cli("files", self.src)
        self.assertEqual(sorted(os.listdir(self.h("Documents"))), ["x (imported).txt", "x.txt"])


class Filters(Env):
    def test_junk_and_symlinks_and_private(self):
        write(os.path.join(self.src, "keep.txt"), b"k")
        for n in ("desktop.ini", "Thumbs.db", ".DS_Store", "._keep.txt", "t.tmp", "T.TMP"):
            write(os.path.join(self.src, n), b"j")
        write(os.path.join(self.src, "$RECYCLE.BIN", "x"), b"j")
        write(os.path.join(self.src, "System Volume Information", "x"), b"j")
        write(os.path.join(self.src, "AppData", "Roaming", "Mozilla", "logins.json"), b"secret")
        write(os.path.join(self.src, "vault.kdbx"), b"secret")
        write(os.path.join(self.src, "login.keychain-db"), b"secret")
        outside = os.path.join(self.tmp, "outside")
        write(os.path.join(outside, "o.txt"), b"o")
        os.symlink(outside, os.path.join(self.src, "linkdir"))
        os.symlink(os.path.join(outside, "o.txt"), os.path.join(self.src, "linkfile"))
        code, out, _ = self.run_cli("files", self.src)
        self.assertEqual(code, 0)
        d = self.h("Imported", "drive")
        self.assertEqual(os.listdir(d), ["keep.txt"])
        self.assertIn("2 symlinks", out)
        self.assertIn("3 browser or password items", out)

    def test_dry_run_copies_nothing(self):
        write(os.path.join(self.src, "a.txt"), b"a")
        before = snapshot(self.home)
        code, out, _ = self.run_cli("files", self.src, "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("Dry run: nothing was copied", out)
        self.assertIn("WOULD COPY", out)
        self.assertEqual(before, snapshot(self.home))

    def test_dry_run_shows_rename(self):
        write(os.path.join(self.src, "a.txt"), b"a")
        write(self.h("Imported", "drive", "a.txt"), b"b")
        _, out, _ = self.run_cli("files", self.src, "--dry-run")
        self.assertIn("a (imported).txt", out)

    def test_mtime_preserved(self):
        write(os.path.join(self.src, "a.txt"), b"a", mtime=1_000_000_000)
        self.run_cli("files", self.src)
        self.assertEqual(int(os.stat(self.h("Imported", "drive", "a.txt")).st_mtime), 1_000_000_000)


class Safety(Env):
    def test_refuse_inside_home(self):
        inner = self.h("old")
        write(os.path.join(inner, "a.txt"))
        for src in (inner, self.home):
            code, _, err = self.run_cli("files", src)
            self.assertEqual(code, 1)
            self.assertIn("home folder", err)
        self.assertEqual(self.run_cli("scan", inner)[0], 1)

    def test_refuse_inside_home_via_symlink(self):
        write(self.h("old", "a.txt"))
        link = os.path.join(self.tmp, "link")
        os.symlink(self.h("old"), link)
        self.assertEqual(self.run_cli("files", link)[0], 1)

    def test_refuse_source_containing_home(self):
        self.assertEqual(self.run_cli("files", self.tmp)[0], 1)

    def test_missing_source(self):
        self.assertEqual(self.run_cli("scan", os.path.join(self.tmp, "nope"))[0], 1)

    def test_source_is_read_only(self):
        u = self.win()
        write(os.path.join(u, "Documents", "a.txt"), b"a", mtime=1_100_000_000)
        write(os.path.join(u, "Pictures", "b.jpg"), b"b" * 5000, mtime=1_200_000_000)
        write(os.path.join(u, "Documents", "desktop.ini"), b"j")
        os.symlink("a.txt", os.path.join(u, "Documents", "ln"))
        write(self.h("Documents", "a.txt"), b"different")
        os.chmod(os.path.join(u, "Documents", "a.txt"), 0o444)
        before = snapshot(self.src)
        for args in (("scan", self.src), ("files", self.src, "--dry-run"), ("files", self.src)):
            self.assertIn(self.run_cli(*args)[0], (0,))
        self.assertEqual(before, snapshot(self.src))

    def test_source_unwritable_still_works(self):
        write(os.path.join(self.src, "a.txt"), b"a")
        os.chmod(self.src, 0o555)
        try:
            self.assertEqual(self.run_cli("files", self.src)[0], 0)
        finally:
            os.chmod(self.src, 0o755)

    def test_not_enough_space(self):
        write(os.path.join(self.src, "a.txt"), b"a" * 100)
        with mock.patch.object(ni, "free_space", return_value=10):
            code, _, err = self.run_cli("files", self.src)
        self.assertEqual(code, 1)
        self.assertIn("Not enough free space", err)
        self.assertFalse(os.path.exists(self.h("Imported")))

    def test_enough_space_uses_real_value(self):
        self.assertGreater(ni.free_space(self.home), 0)
        self.assertGreater(ni.free_space(self.h("does", "not", "exist")), 0)

    @unittest.skipIf(os.geteuid() == 0, "root can read everything")
    def test_unreadable_file_skipped_and_reported(self):
        write(os.path.join(self.src, "good.txt"), b"g")
        bad = os.path.join(self.src, "bad.txt")
        write(bad, b"b")
        os.chmod(bad, 0)
        write(os.path.join(self.src, "locked", "in.txt"), b"i")
        os.chmod(os.path.join(self.src, "locked"), 0)
        try:
            code, out, _ = self.run_cli("files", self.src)
        finally:
            os.chmod(bad, 0o644)
            os.chmod(os.path.join(self.src, "locked"), 0o755)
        self.assertEqual(code, 2)
        self.assertIn("bad.txt: permission denied", out)
        self.assertIn("locked", out)
        self.assertTrue(os.path.exists(self.h("Imported", "drive", "good.txt")))
        self.assertFalse(os.path.exists(self.h("Imported", "drive", "bad.txt")))

    def test_read_error_midway_skips_file(self):
        write(os.path.join(self.src, "a.txt"), b"a" * 10)
        write(os.path.join(self.src, "b.txt"), b"b" * 10)
        real = ni.open_source
        calls = {}

        class Bad:
            def __init__(self, f):
                self.f = f

            def read(self, n):
                import errno
                raise OSError(errno.EIO, "Input/output error")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                self.f.close()

        def fake(path):
            f = real(path)
            if path.endswith("a.txt") and calls.setdefault(path, 0) >= 1:
                return Bad(f)
            calls[path] = calls.get(path, 0) + 1
            return f

        # injected: a real bad sector cannot be made in a test, so the read raises EIO
        with mock.patch.object(ni, "open_source", fake):
            code, out, _ = self.run_cli("files", self.src)
        self.assertEqual(code, 2)
        self.assertIn("bad sectors", out)
        self.assertFalse(os.path.exists(self.h("Imported", "drive", "a.txt")))
        self.assertTrue(os.path.exists(self.h("Imported", "drive", "b.txt")))

    def test_long_name_skipped(self):
        write(os.path.join(self.src, "ok.txt"), b"o")
        long = "n" * 250 + ".txt"
        write(os.path.join(self.src, long), b"l")
        # (imported) suffix pushes the name past 255 bytes on the home filesystem
        write(self.h("Imported", "drive", long), b"different")
        code, out, _ = self.run_cli("files", self.src)
        self.assertEqual(code, 2)
        self.assertIn("too long", out)
        self.assertTrue(os.path.exists(self.h("Imported", "drive", "ok.txt")))

    def test_fifo_not_opened(self):
        os.mkfifo(os.path.join(self.src, "pipe"))
        write(os.path.join(self.src, "a.txt"), b"a")
        code, out, _ = self.run_cli("files", self.src)
        self.assertEqual(code, 0)
        self.assertIn("1 special files", out)

    def test_summary_is_honest(self):
        write(os.path.join(self.src, "a.txt"), b"abc")
        _, out, _ = self.run_cli("files", self.src)
        self.assertIn("Copied 1 files, 3 bytes.", out)
        for word in ("safe", "complete", "successfully"):
            self.assertNotIn(word, out.lower())


if __name__ == "__main__":
    import sys
    import warnings
    # accept "-W error::ResourceWarning" after the script name, as the docs show
    rest = []
    argv = sys.argv[1:]
    while argv:
        a = argv.pop(0)
        if a == "-W" and argv:
            warnings.filterwarnings("error", category=ResourceWarning)
            argv.pop(0)
        else:
            rest.append(a)
    unittest.main(argv=[sys.argv[0]] + rest, warnings="default")
