import contextlib
import errno
import hashlib
import html.parser
import importlib.machinery
import importlib.util
import io
import json
import os
import plistlib
import shutil
import sqlite3
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

    def test_ctrl_c_midway_leaves_no_half_file(self):
        write(os.path.join(self.src, "big.bin"), b"z" * 40)
        real = ni.open_source

        class Stop:
            def __init__(self, f):
                self.f, self.n = f, 0

            def read(self, n):
                self.n += 1
                if self.n > 1:
                    raise KeyboardInterrupt
                return self.f.read(n)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                self.f.close()

        calls = {}

        def fake(path):
            calls[path] = calls.get(path, 0) + 1
            return Stop(real(path)) if calls[path] > 1 else real(path)

        with mock.patch.object(ni, "open_source", fake), mock.patch.object(ni, "CHUNK", 8):
            code, _, err = self.run_cli("files", self.src)
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", err)
        left = []
        for dp, _, fn in os.walk(self.home):
            left += fn
        self.assertEqual(left, [], "no half-written file or temp file is left in the home folder")

    def test_file_that_changes_while_copied_is_not_imported(self):
        path = os.path.join(self.src, "log.txt")
        write(path, b"a" * 40)
        real = ni.open_source
        calls = {}

        class Grow:
            def __init__(self, f):
                self.f, self.done = f, False

            def read(self, n):
                data = self.f.read(n)
                if not self.done:
                    self.done = True
                    with open(path, "ab") as g:
                        g.write(b"more")
                return data

            def __enter__(self):
                return self

            def __exit__(self, *a):
                self.f.close()

        def fake(p):
            calls[p] = calls.get(p, 0) + 1
            return Grow(real(p)) if calls[p] > 1 else real(p)

        with mock.patch.object(ni, "open_source", fake), mock.patch.object(ni, "CHUNK", 8):
            code, out, _ = self.run_cli("files", self.src)
        self.assertEqual(code, 2)
        self.assertIn("changed while", out)
        self.assertFalse(os.path.exists(self.h("Imported", "drive", "log.txt")))
        self.assertIn("Copied 0 files", out)

    def test_names_that_are_not_utf8_do_not_crash_the_report(self):
        write(os.path.join(self.src, "Documents", "report.txt"), b"1")
        write(os.path.join(self.src, "Pictures", "x.jpg"), b"2")
        bad = os.path.join(os.fsencode(self.src), b"Documents", b"caf\xe9.txt")
        with open(bad, "wb") as f:
            f.write(b"old windows name")
        for args in (["files", self.src, "--dry-run"], ["files", self.src], ["scan", self.src]):
            # a real strict UTF-8 stream, like a terminal, not a StringIO that accepts anything
            raw = io.BytesIO()
            out = io.TextIOWrapper(raw, encoding="utf-8", errors="strict")
            code = ni.main(args, out)
            out.flush()
            self.assertEqual(code, 0, args)
            if args[0] == "files" and "--dry-run" in args:
                self.assertIn(b"caf\\xe9.txt", raw.getvalue())
        self.assertEqual(rd(os.path.join(os.fsencode(self.home), b"Documents", b"caf\xe9.txt")),
                         b"old windows name")

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

class Tree(html.parser.HTMLParser):
    """Read a Netscape bookmarks file back into nested tuples, independently of the writer."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = []
        self.stack = [self.root]
        self.pending = None   # folder waiting for its <DL>
        self.cur = None       # ("h3"|"a", attrs, [text])
        self.last = None

    def handle_starttag(self, tag, attrs):
        if tag == "h3":
            self.cur = ["h3", dict(attrs), []]
        elif tag == "a":
            self.cur = ["a", dict(attrs), []]
        elif tag == "dl" and self.pending is not None:
            self.stack.append(self.pending)
            self.pending = None

    def handle_data(self, data):
        if self.cur:
            self.cur[2].append(data)

    def handle_endtag(self, tag):
        if tag in ("h3", "a") and self.cur:
            kind, attrs, text = self.cur
            self.cur = None
            name = "".join(text)
            if kind == "h3":
                node = ("folder", name, [], attrs.get("add_date"))
                self.stack[-1].append(node)
                self.pending = node[2]
            else:
                self.stack[-1].append(("link", name, attrs.get("href"), attrs.get("add_date")))
        elif tag == "dl" and len(self.stack) > 1:
            self.stack.pop()


def parse_html(text):
    t = Tree()
    t.feed(text)
    t.close()
    return t.root


def strip_dates(nodes):
    out = []
    for n in nodes:
        if n[0] == "folder":
            out.append(("folder", n[1], strip_dates(n[2])))
        else:
            out.append(("link", n[1], n[2]))
    return out


CHROME_DOC = {
    "checksum": "x", "version": 1,
    "roots": {
        "bookmark_bar": {"type": "folder", "name": "Bookmarks bar", "children": [
            {"type": "url", "name": "Example", "url": "https://example.com/?a=1&b=2",
             "date_added": "13300000000000000"},
            {"type": "folder", "name": "Café 日本語", "date_added": "13300000000000000", "children": [
                {"type": "url", "name": "Zeta ☃", "url": "https://zeta.example/"},
                {"type": "folder", "name": "Empty", "children": []},
            ]},
            {"type": "url", "name": "Bad", "url": "javascript:alert(1)"},
            {"type": "url", "name": "Data", "url": "data:text/html,hi"},
            {"type": "url", "name": "No address"},
            "not a dict",
            {"type": "mystery", "name": "?"},
            {"type": "url", "name": "Example again", "url": "https://example.com/?a=1&b=2"},
        ]},
        "other": {"type": "folder", "name": "Other bookmarks", "children": [
            {"type": "url", "name": "Tab\tand\x00ctl\nname", "url": "https://other.example/"},
        ]},
        "synced": {"type": "folder", "name": "Mobile bookmarks", "children": []},
    },
}


def chrome_dir(base, *parts):
    d = os.path.join(base, *parts)
    os.makedirs(d, exist_ok=True)
    return d


def write_chrome(base, doc=None, rel=("Users", "Sam", "AppData", "Local", "Google", "Chrome", "User Data", "Default")):
    path = os.path.join(base, *rel, "Bookmarks")
    write(path, json.dumps(doc or CHROME_DOC).encode("utf-8"))
    return path


def make_places(path, wal=False, extra=True):
    db = sqlite3.connect(path)
    if wal:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA wal_autocheckpoint=0")
    db.executescript("""
        CREATE TABLE moz_places (id INTEGER PRIMARY KEY, url LONGVARCHAR, title LONGVARCHAR);
        CREATE TABLE moz_bookmarks (id INTEGER PRIMARY KEY, type INTEGER, fk INTEGER, parent INTEGER,
            position INTEGER, title LONGVARCHAR, dateAdded INTEGER, guid TEXT);
    """)
    db.executemany("INSERT INTO moz_places VALUES (?,?,?)", [
        (1, "https://ff.example/one", "One"), (2, "https://ff.example/two?x=1&y=2", "Two"),
        (3, "place:type=6&sort=14", "Recent"), (4, "https://tagged.example/", "Tagged")])
    roots = [(1, 2, None, 0, 0, "", 0, "root________"), (2, 2, None, 1, 0, "menu", 0, "menu________"),
             (3, 2, None, 1, 1, "toolbar", 0, "toolbar_____"), (4, 2, None, 1, 2, "tags", 0, "tags________"),
             (5, 2, None, 1, 3, "unfiled", 0, "unfiled_____"), (6, 2, None, 1, 4, "mobile", 0, "mobile______")]
    db.executemany("INSERT INTO moz_bookmarks VALUES (?,?,?,?,?,?,?,?)", roots)
    db.executemany("INSERT INTO moz_bookmarks VALUES (?,?,?,?,?,?,?,?)", [
        (10, 1, 1, 3, 0, "One", 1655526400000000, "aaaaaaaaaaaa"),
        (11, 2, None, 3, 1, "Résumés", 1655526400000000, "bbbbbbbbbbbb"),
        (12, 1, 2, 11, 0, "Two", 1655526401000000, "cccccccccccc"),
        (13, 3, None, 11, 1, None, 0, "dddddddddddd"),
        (14, 2, None, 11, 2, "Empty folder", 0, "eeeeeeeeeeee"),
        (15, 1, 3, 2, 0, "Recent", 0, "ffffffffffff"),
        (16, 2, None, 4, 0, "mytag", 0, "gggggggggggg"),
        (17, 1, 4, 16, 0, "Tagged", 0, "hhhhhhhhhhhh"),
    ])
    db.commit()
    if extra:
        db.execute("INSERT INTO moz_bookmarks VALUES (18,1,1,5,0,'Late',0,'iiiiiiiiiiii')")
        db.commit()
    return db


def make_safari(top_children):
    return {"WebBookmarkType": "WebBookmarkTypeList", "Title": "", "Children": top_children}


SAFARI_DOC = make_safari([
    {"WebBookmarkType": "WebBookmarkTypeProxy", "Title": "History"},
    {"WebBookmarkType": "WebBookmarkTypeList", "Title": "BookmarksBar", "Children": [
        {"WebBookmarkType": "WebBookmarkTypeLeaf", "URLString": "https://apple.example/?q=a&r=b",
         "URIDictionary": {"title": "Apple & Pears"}},
        {"WebBookmarkType": "WebBookmarkTypeList", "Title": "Très bien", "Children": [
            {"WebBookmarkType": "WebBookmarkTypeLeaf", "URLString": "https://deep.example/",
             "URIDictionary": {"title": "Deep"}}]},
        {"WebBookmarkType": "WebBookmarkTypeList", "Title": "Empty"},
        {"WebBookmarkType": "WebBookmarkTypeLeaf", "URLString": "javascript:void(0)",
         "URIDictionary": {"title": "Bookmarklet"}},
    ]},
    {"WebBookmarkType": "WebBookmarkTypeList", "Title": "BookmarksMenu", "Children": []},
    {"WebBookmarkType": "WebBookmarkTypeList", "Title": "com.apple.ReadingList", "Children": [
        {"WebBookmarkType": "WebBookmarkTypeLeaf", "URLString": "https://later.example/",
         "URIDictionary": {"title": "Read later"}}]},
])


class Bookmarks(Env):
    def text(self, path):
        return rd(path).decode("utf-8")

    def out_file(self, name="Bookmarks from Chrome.html"):
        return self.h("Documents", name)

    def run_bm(self, *extra, source=None):
        return self.run_cli("bookmarks", source or self.src, *extra)

    def test_chrome_tree(self):
        write_chrome(self.src)
        code, out, err = self.run_bm()
        self.assertEqual((code, err), (0, ""), out)
        self.assertIn("Chrome (Sam / Default)", out)
        tree = parse_html(self.text(self.out_file()))
        self.assertEqual(strip_dates(tree), [("folder", "Chrome", [
            ("folder", "Bookmarks bar", [
                ("link", "Example", "https://example.com/?a=1&b=2"),
                ("folder", "Café 日本語", [
                    ("link", "Zeta ☃", "https://zeta.example/"),
                    ("folder", "Empty", [])])]),
            ("folder", "Other bookmarks", [("link", "Tab andctl name", "https://other.example/")])])])
        self.assertIn("Read 7 bookmarks and 2 folders", out)
        self.assertIn("Skipped 6 entries", out)
        self.assertIn("Wrote 3 bookmarks in 2 folders", out)
        self.assertIn("1 duplicate", out)
        self.assertIn("2 javascript: or data: address", out)
        self.assertIn("1 no usable address", out)
        self.assertIn("2 damaged entry", out)
        self.assertIn("Menu > Bookmarks > Manage bookmarks > Import and Backup > Import Bookmarks from HTML", out)
        self.assertIn("Nothing has been imported yet", out)

    def test_chrome_time_conversion(self):
        self.assertEqual(ni.chrome_time("13300000000000000"), 1655526400)
        self.assertEqual(ni.chrome_time(11644473600 * 1000000 + 5000000), 5)
        self.assertIsNone(ni.chrome_time("0"))
        self.assertIsNone(ni.chrome_time("junk"))
        self.assertIsNone(ni.chrome_time(None))
        self.assertEqual(ni.unix_micro_time(1655526400000000), 1655526400)
        write_chrome(self.src)
        self.run_bm()
        text = self.text(self.out_file())
        self.assertIn('ADD_DATE="1655526400">Example</A>', text)
        self.assertNotIn('ADD_DATE="0"', text)
        tree = parse_html(text)
        self.assertEqual(tree[0][2][0][2][0][3], "1655526400")

    def test_chrome_edge_brave_mac_and_linux_paths(self):
        write_chrome(self.src, rel=("Users", "Sam", "Library", "Application Support", "Microsoft Edge", "Default"))
        write_chrome(self.src, rel=("Users", "Sam", "Library", "Application Support", "BraveSoftware",
                                    "Brave-Browser", "Profile 1"))
        write_chrome(self.src, rel=("home", "pat", ".config", "google-chrome", "Default"))
        write_chrome(self.src, rel=("Users", "Sam", "AppData", "Roaming", "Opera Software", "Opera Stable", "."))
        code, out, _ = self.run_bm()
        self.assertEqual(code, 0, out)
        for name in ("Edge", "Brave", "Chrome", "Opera"):
            self.assertIn(f"  {name}", out)
        self.assertTrue(os.path.exists(self.out_file("Bookmarks from several browsers.html")))
        tree = parse_html(self.text(self.out_file("Bookmarks from several browsers.html")))
        self.assertEqual(sorted(n[1] for n in tree), ["Brave", "Chrome", "Edge", "Opera"])

    def test_two_profiles_get_subfolders(self):
        write_chrome(self.src)
        write_chrome(self.src, rel=("Users", "Sam", "AppData", "Local", "Google", "Chrome", "User Data", "Profile 2"))
        self.run_bm()
        tree = parse_html(self.text(self.out_file()))
        self.assertEqual(len(tree), 1)
        self.assertEqual(sorted(n[1] for n in tree[0][2]), ["Sam / Default", "Sam / Profile 2"])

    def test_case_insensitive_windows_names(self):
        write_chrome(self.src, rel=("users", "sam", "appdata", "local", "google", "chrome", "user data", "default"))
        code, out, _ = self.run_bm()
        self.assertEqual(code, 0, out)
        self.assertTrue(os.path.exists(self.out_file()))

    def test_profile_folder_as_source(self):
        d = os.path.join(self.src, "Microsoft", "Edge", "User Data", "Default")
        write(os.path.join(d, "Bookmarks"), json.dumps(CHROME_DOC).encode())
        code, out, _ = self.run_bm(source=d)
        self.assertEqual(code, 0, out)
        self.assertTrue(os.path.exists(self.out_file("Bookmarks from Edge.html")))

    def test_firefox_places(self):
        d = os.path.join(self.src, "Users", "Sam", "AppData", "Roaming", "Mozilla", "Firefox", "Profiles", "x.default")
        os.makedirs(d)
        db = make_places(os.path.join(d, "places.sqlite"), extra=False)
        db.close()
        code, out, err = self.run_bm()
        self.assertEqual((code, err), (0, ""), out)
        tree = strip_dates(parse_html(self.text(self.out_file("Bookmarks from Firefox.html"))))
        self.assertEqual(tree, [("folder", "Firefox", [
            ("folder", "Bookmarks menu", []) if False else
            ("folder", "Bookmarks toolbar", [
                ("link", "One", "https://ff.example/one"),
                ("folder", "Résumés", [
                    ("link", "Two", "https://ff.example/two?x=1&y=2"),
                    ("folder", "Empty folder", [])])])])])
        self.assertIn("Firefox search or tag shortcut", out)
        self.assertNotIn("Tagged", self.text(self.out_file("Bookmarks from Firefox.html")))
        text = self.text(self.out_file("Bookmarks from Firefox.html"))
        self.assertIn('ADD_DATE="1655526400">One</A>', text)

    def _firefox_dir(self):
        d = os.path.join(self.src, "home", "pat", ".mozilla", "firefox", "abc.default-release")
        os.makedirs(d)
        return d

    def test_firefox_locked_database(self):
        d = self._firefox_dir()
        path = os.path.join(d, "places.sqlite")
        make_places(path, extra=False).close()
        locker = sqlite3.connect(path, isolation_level=None)
        try:
            locker.execute("BEGIN EXCLUSIVE")
            before = snapshot(self.src)
            code, out, err = self.run_bm()
            self.assertEqual((code, err), (0, ""), out)
            self.assertEqual(before, snapshot(self.src))
        finally:
            locker.execute("ROLLBACK")
            locker.close()
        self.assertIn("https://ff.example/one", self.text(self.out_file("Bookmarks from Firefox.html")))

    def test_firefox_wal_is_read_from_a_copy(self):
        d = self._firefox_dir()
        path = os.path.join(d, "places.sqlite")
        db = make_places(path, wal=True, extra=True)   # "Late" exists only in the write-ahead log
        try:
            self.assertGreater(os.path.getsize(path + "-wal"), 0)
            before = snapshot(self.src)
            code, out, err = self.run_bm()
            self.assertEqual((code, err), (0, ""), out)
            self.assertEqual(before, snapshot(self.src))
        finally:
            db.close()
        text = self.text(self.out_file("Bookmarks from Firefox.html"))
        self.assertIn("https://ff.example/one", text)
        self.assertIn(">Late</A>", text)
        self.assertEqual(os.listdir(tempfile.gettempdir()).count("nizho-import-"), 0)

    def test_safari_xml_and_binary(self):
        for fmt, label in ((plistlib.FMT_XML, "xml"), (plistlib.FMT_BINARY, "binary")):
            with self.subTest(label):
                shutil.rmtree(self.src)
                shutil.rmtree(self.home)
                os.makedirs(self.src)
                os.makedirs(self.home)
                p = os.path.join(self.src, "Users", "sam", "Library", "Safari", "Bookmarks.plist")
                write(p, plistlib.dumps(SAFARI_DOC, fmt=fmt))
                code, out, err = self.run_bm()
                self.assertEqual((code, err), (0, ""), out)
                tree = strip_dates(parse_html(self.text(self.out_file("Bookmarks from Safari.html"))))
                self.assertEqual(tree, [("folder", "Safari", [("folder", "Favorites", [
                    ("link", "Apple & Pears", "https://apple.example/?q=a&r=b"),
                    ("folder", "Très bien", [("link", "Deep", "https://deep.example/")]),
                    ("folder", "Empty", [])])])])
                self.assertNotIn("later.example", self.text(self.out_file("Bookmarks from Safari.html")))

    def test_escaping_and_round_trip(self):
        nasty = 'A <b>"q"</b> & \'s\' </A><script>x</script>'
        doc = {"roots": {"bookmark_bar": {"children": [
            {"type": "url", "name": nasty, "url": 'https://e.example/?a=1&b="2"&c=<x>'},
            {"type": "folder", "name": nasty, "children": [
                {"type": "url", "name": "", "url": "https://untitled.example/"}]}]}}}
        write_chrome(self.src, doc)
        self.assertEqual(self.run_bm()[0], 0)
        text = self.text(self.out_file())
        self.assertNotIn("<script>", text)
        self.assertNotIn("<b>", text)
        self.assertIn("&amp;", text)
        tree = strip_dates(parse_html(text))
        self.assertEqual(tree, [("folder", "Chrome", [("folder", "Bookmarks bar", [
            ("link", nasty, 'https://e.example/?a=1&b="2"&c=<x>'),
            ("folder", nasty, [("link", "https://untitled.example/", "https://untitled.example/")])])])])
        self.assertTrue(text.startswith("<!DOCTYPE NETSCAPE-Bookmark-file-1>"))

    def test_long_title_capped(self):
        doc = {"roots": {"bookmark_bar": {"children": [
            {"type": "url", "name": "x" * 2000, "url": "https://long.example/"}]}}}
        write_chrome(self.src, doc)
        self.run_bm()
        tree = parse_html(self.text(self.out_file()))
        self.assertEqual(len(tree[0][2][0][2][0][1]), 500)

    def test_deep_nesting_does_not_crash(self):
        node = {"type": "url", "name": "bottom", "url": "https://bottom.example/"}
        for i in range(200):
            node = {"type": "folder", "name": f"f{i}", "children": [node]}
        write_chrome(self.src, {"roots": {"bookmark_bar": {"children": [node]}}})
        code, out, err = self.run_bm()
        self.assertEqual(err, "")
        self.assertEqual(code, 0, out)
        self.assertIn("folders nested too deeply", out)

    def test_never_overwrites(self):
        write_chrome(self.src)
        os.makedirs(self.h("Documents"))
        existing = self.out_file()
        write(existing, b"mine")
        code, out, _ = self.run_bm()
        self.assertEqual(code, 0)
        self.assertEqual(rd(existing), b"mine")
        self.assertTrue(os.path.exists(self.out_file("Bookmarks from Chrome (2).html")))
        self.run_bm()
        self.assertTrue(os.path.exists(self.out_file("Bookmarks from Chrome (3).html")))
        self.assertEqual(rd(existing), b"mine")
        target = os.path.join(self.tmp, "mine.html")
        write(target, b"keep")
        self.run_bm("--to", target)
        self.assertEqual(rd(target), b"keep")
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "mine (2).html")))

    def test_dry_run_writes_nothing(self):
        write_chrome(self.src)
        code, out, _ = self.run_bm("--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("Dry run: nothing was written", out)
        self.assertFalse(os.path.exists(self.h("Documents")))
        self.assertNotIn("Wrote", out)

    def test_source_is_byte_for_byte_unchanged(self):
        write_chrome(self.src, rel=("Users", "Sam", "AppData", "Local", "Google", "Chrome", "User Data", "Default"))
        write(os.path.join(self.src, "Users", "Sam", "Library", "Safari", "Bookmarks.plist"),
              plistlib.dumps(SAFARI_DOC, fmt=plistlib.FMT_BINARY))
        d = os.path.join(self.src, "Users", "Sam", "AppData", "Roaming", "Mozilla", "Firefox", "Profiles", "p.default")
        os.makedirs(d)
        make_places(os.path.join(d, "places.sqlite"), extra=False).close()
        write(os.path.join(self.src, "Users", "Sam", "Documents", "d.txt"), b"doc", mtime=1000000000)
        before = snapshot(self.src)
        for args in (["scan"], ["bookmarks"], ["bookmarks", "--dry-run"]):
            code, out, err = self.run_cli(args[0], self.src, *args[1:])
            self.assertIn(code, (0, 2), out + err)
            self.assertEqual(before, snapshot(self.src))
        self.assertEqual(len(before), len(snapshot(self.src)))

    def test_read_only_source(self):
        p = write_chrome(self.src)
        before = snapshot(self.src)
        for dp, dn, fn in os.walk(self.src):
            for n in dn:
                os.chmod(os.path.join(dp, n), 0o555)
        os.chmod(p, 0o444)
        before = snapshot(self.src)
        code, out, err = self.run_bm()
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual(before, snapshot(self.src))

    def test_source_inside_home_refused(self):
        inner = os.path.join(self.home, "old")
        write_chrome(inner)
        code, out, err = self.run_cli("bookmarks", inner)
        self.assertEqual(code, 1)
        self.assertIn("home folder", err)
        self.assertNotIn("Traceback", err)
        self.assertFalse(os.path.exists(self.h("Documents")))
        code, _, err = self.run_cli("bookmarks", self.home)
        self.assertEqual(code, 1)
        self.assertIn("home folder", err)

    def test_output_inside_source_refused(self):
        write_chrome(self.src)
        before = snapshot(self.src)
        code, out, err = self.run_bm("--to", os.path.join(self.src, "out.html"))
        self.assertEqual(code, 1)
        self.assertIn("inside the source", err)
        self.assertEqual(before, snapshot(self.src))

    def test_symlink_out_of_source_not_followed(self):
        outside = os.path.join(self.tmp, "outside")
        write(os.path.join(outside, "Bookmarks"), json.dumps(CHROME_DOC).encode())
        d = chrome_dir(self.src, "Users", "Sam", "AppData", "Local", "Google", "Chrome", "User Data", "Default")
        os.symlink(os.path.join(outside, "Bookmarks"), os.path.join(d, "Bookmarks"))
        code, out, err = self.run_bm()
        self.assertEqual(code, 1)
        self.assertIn("No browser bookmarks were found", err)
        link_dir = os.path.join(self.src, "Users", "Sam", "AppData", "Local", "Google", "Chrome", "User Data")
        shutil.rmtree(link_dir)
        os.symlink(outside, link_dir)
        self.assertEqual(self.run_bm()[0], 1)

    def test_nothing_found(self):
        write(os.path.join(self.src, "readme.txt"), b"hello")
        code, out, err = self.run_bm()
        self.assertEqual(code, 1)
        self.assertEqual(out.count("Bookmark files found"), 0)
        self.assertIn("nothing", err.lower())
        self.assertIn("No browser bookmarks were found", err)
        self.assertNotIn("Traceback", err)
        self.assertFalse(os.path.exists(self.h("Documents")))

    def test_missing_source(self):
        code, _, err = self.run_cli("bookmarks", os.path.join(self.tmp, "nope"))
        self.assertEqual(code, 1)
        self.assertIn("not a folder", err)

    def test_corrupt_inputs_plain_errors(self):
        cases = {
            "json": ("Bookmarks", b'{"roots": {"bookmark_bar": '),
            "notbookmarks": ("Bookmarks", b'[1, 2, 3]'),
            "binary": ("Bookmarks", bytes(range(256)) * 4),
        }
        for label, (name, data) in cases.items():
            with self.subTest(label):
                shutil.rmtree(self.src)
                os.makedirs(self.src)
                write(os.path.join(self.src, "Users", "Sam", ".config", "chromium", "Default", name), data)
                code, out, err = self.run_bm()
                self.assertEqual(code, 1)
                self.assertIn("could not be read", out)
                self.assertIn("None of the bookmark files could be read", err)
                self.assertNotIn("Traceback", out + err)
                self.assertFalse(os.path.exists(self.h("Documents")))
        shutil.rmtree(self.src)
        os.makedirs(self.src)
        d = os.path.join(self.src, "Users", "Sam", ".mozilla", "firefox", "bad.default")
        write(os.path.join(d, "places.sqlite"), b"this is not a database" * 100)
        code, out, err = self.run_bm()
        self.assertEqual(code, 1)
        self.assertIn("not a Firefox places database", out)
        self.assertNotIn("Traceback", out + err)
        shutil.rmtree(self.src)
        os.makedirs(self.src)
        write(os.path.join(self.src, "Users", "Sam", "Library", "Safari", "Bookmarks.plist"), b"bplist00garbage")
        code, out, err = self.run_bm()
        self.assertEqual(code, 1)
        self.assertIn("not a Safari bookmarks plist", out)
        self.assertNotIn("Traceback", out + err)
        # a sqlite file that is valid but is not Firefox
        shutil.rmtree(self.src)
        os.makedirs(self.src)
        d = os.path.join(self.src, "Users", "Sam", ".mozilla", "firefox", "other.default")
        os.makedirs(d)
        db = sqlite3.connect(os.path.join(d, "places.sqlite"))
        db.execute("CREATE TABLE t (a)")
        db.commit()
        db.close()
        code, out, err = self.run_bm()
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", out + err)

    def test_one_corrupt_file_gives_exit_2_and_still_writes(self):
        write_chrome(self.src)
        write(os.path.join(self.src, "Users", "Sam", "Library", "Safari", "Bookmarks.plist"), b"junk")
        code, out, err = self.run_bm()
        self.assertEqual(code, 2, out + err)
        self.assertIn("1 bookmark files could not be read", out)
        self.assertTrue(os.path.exists(self.out_file()))

    def test_unreadable_file(self):
        if os.geteuid() == 0:
            self.skipTest("root can read everything")
        p = write_chrome(self.src)
        os.chmod(p, 0)
        code, out, err = self.run_bm()
        self.assertEqual(code, 1)
        self.assertIn("permission denied", out)
        self.assertNotIn("Traceback", out + err)

    def test_write_failure_is_plain(self):
        write_chrome(self.src)
        blocker = os.path.join(self.tmp, "afile")
        write(blocker, b"x")
        code, out, err = self.run_bm("--to", os.path.join(blocker, "sub", "b.html"))
        self.assertEqual(code, 1)
        self.assertIn("Could not write", err)
        self.assertNotIn("Traceback", err)
        self.assertNotIn("Wrote", out)

    def test_write_failure_removes_partial_file(self):
        write_chrome(self.src)
        target = os.path.join(self.tmp, "o.html")
        real_fsync = os.fsync

        def boom(fd):
            raise OSError(errno.ENOSPC, "No space left on device")
        with mock.patch.object(os, "fsync", boom):
            code, out, err = self.run_bm("--to", target)
        self.assertEqual(code, 1)
        self.assertIn("no space left", err)
        self.assertFalse(os.path.exists(target))

    def test_scan_lists_browsers_and_states_what_is_not_imported(self):
        write_chrome(self.src)
        write(os.path.join(self.src, "Users", "Sam", "Library", "Safari", "Bookmarks.plist"),
              plistlib.dumps(SAFARI_DOC))
        code, out, _ = self.run_cli("scan", self.src)
        self.assertEqual(code, 0)
        self.assertIn("Bookmarks that can be exported", out)
        self.assertIn("Chrome (Sam / Default)", out)
        self.assertIn("Safari (Sam)", out)
        self.assertIn("Passwords, cookies, history and other browser data are not imported.", out)
        self.assertFalse(os.path.exists(self.h("Documents")))

    def test_scan_without_browsers(self):
        write(os.path.join(self.src, "a.txt"), b"a")
        _, out, _ = self.run_cli("scan", self.src)
        self.assertIn("No browser bookmarks found.", out)
        self.assertIn("are not imported", out)

    def test_summary_never_claims_import(self):
        write_chrome(self.src)
        _, out, _ = self.run_bm()
        for word in ("imported successfully", "safe", "complete"):
            self.assertNotIn(word, out.lower())
        self.assertNotIn("password", self.text(self.out_file()).lower())

    def test_default_names(self):
        self.assertEqual(ni.default_name(["Chrome"]), "Bookmarks from Chrome.html")
        self.assertEqual(ni.default_name(["Chrome", "Firefox"]), "Bookmarks from Chrome and Firefox.html")
        self.assertEqual(ni.default_name(["A", "B", "C"]), "Bookmarks from A, B and C.html")
        self.assertEqual(ni.default_name(list("ABCD")), "Bookmarks from several browsers.html")


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
