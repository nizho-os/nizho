import contextlib
import importlib.machinery
import importlib.util
import io
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
loader = importlib.machinery.SourceFileLoader("nizho_family", os.path.join(HERE, "nizho-family"))
spec = importlib.util.spec_from_loader("nizho_family", loader)
nf = importlib.util.module_from_spec(spec)
loader.exec_module(nf)


def run_cli(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = nf.main(list(args))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


class Validation(unittest.TestCase):
    def test_good_names(self):
        for n in ["mia", "kid2", "a", "a-b", "x" * 32]:
            self.assertEqual(nf.check_name(n), n)

    def test_bad_names(self):
        for n in ["", "1kid", "-kid", "Kid", "kid_x", "kid name", "kid-", "x" * 33,
                  "root", "wheel", "sudo", "nobody", "systemd-resolve", "a;b", "$(id)"]:
            with self.assertRaises(nf.Fail, msg=n):
                nf.check_name(n)

    def test_ages(self):
        for a in (3, 10, 17):
            self.assertEqual(nf.check_age(a), a)
        for a in (2, 18, -1, 0, True, "9", 9.5):
            with self.assertRaises(nf.Fail, msg=repr(a)):
                nf.check_age(a)


class Plan(unittest.TestCase):
    def test_no_admin_words_for_any_age(self):
        for age in range(3, 18):
            text = nf.plan("mia", age)
            low = text.lower()
            self.assertNotIn("wheel", low)
            self.assertNotIn("sudo", low)
            self.assertNotIn("admin group", low)

    def test_useradd_has_no_groups(self):
        useradd = [c for _, c in nf.commands("mia", 9) if c.startswith("useradd")][0]
        self.assertNotIn(" -G", useradd)
        self.assertNotIn("--groups", useradd)

    def test_filter_depends_on_age(self):
        self.assertEqual(nf.oars_for_age(5)["language-profanity"], "none")
        self.assertEqual(nf.oars_for_age(16)["language-profanity"], "mild")
        self.assertNotEqual(nf.oars_for_age(5), nf.oars_for_age(16))

    def test_values_are_oars_levels(self):
        for age in range(3, 18):
            for v in nf.oars_for_age(age).values():
                self.assertIn(v, ("none", "mild", "moderate", "intense"))

    def test_forbidden_command_is_refused(self):
        old = nf.shlex.quote
        try:
            nf.shlex.quote = lambda s: "root"
            with self.assertRaises(nf.Fail):
                nf.commands("mia", 9)
        finally:
            nf.shlex.quote = old

    def test_states_limits(self):
        self.assertIn("Not run on Nizho", nf.plan("mia", 9))


class Cli(unittest.TestCase):
    def test_ok(self):
        code, out, err = run_cli("plan", "--name", "mia", "--age", "9")
        self.assertEqual(code, 0)
        self.assertIn("useradd -m -s /bin/bash mia", out)

    def test_bad_name(self):
        code, out, err = run_cli("plan", "--name", "root", "--age", "9")
        self.assertEqual(code, 2)
        self.assertEqual(out, "")

    def test_bad_age(self):
        code, out, err = run_cli("plan", "--name", "mia", "--age", "30")
        self.assertEqual(code, 2)
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
