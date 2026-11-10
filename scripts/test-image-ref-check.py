"""nizho-image-ref-check against fake `bootc status --json` output; never runs the host's bootc.

    python3 -I scripts/test-image-ref-check.py
"""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parent.parent / "system" / "nizho-image-ref-check"
loader = importlib.machinery.SourceFileLoader("nizho_image_ref_check", str(SCRIPT))
check = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
sys.dont_write_bytecode = True
loader.exec_module(check)


def entry(reference, transport="registry"):
    return {"image": {"image": {"image": reference, "transport": transport},
                      "version": "44.1", "imageDigest": "sha256:123", "timestamp": None},
            "incompatible": False, "pinned": False, "ostree": {"checksum": "123"}}


def host(booted, staged=None):
    return json.dumps({"apiVersion": "org.containers.bootc/v1", "kind": "BootcHost",
                       "spec": {"bootOrder": "default"},
                       "status": {"booted": booted, "staged": staged, "rollback": None}})


class ParsingTest(unittest.TestCase):
    def test_the_published_image_gives_no_warning(self):
        for reference in ("ghcr.io/nizho-os/nizho:latest", "ghcr.io/nizho-os/nizho",
                          "ghcr.io/nizho-os/nizho:candidate", "ghcr.io/nizho-os/nizho@sha256:abc"):
            self.assertIsNone(check.warning_for(host(entry(reference))), reference)

    def test_the_iso_copy_gives_the_warning_with_the_documented_command(self):
        text = check.warning_for(host(entry("localhost/nizho:dev", "containers-storage")))
        self.assertTrue(text.startswith("This computer is not set to receive Nizho updates."))
        self.assertIn('"localhost/nizho:dev"', text)
        self.assertTrue(text.endswith("\nsudo bootc switch ghcr.io/nizho-os/nizho:latest"))

    def test_other_images_and_look_alikes_give_the_warning(self):
        for reference in ("ghcr.io/nizho-os/nizho-nvidia:latest", "ghcr.io/someone/nizho:latest",
                          "quay.io/nizho-os/nizho:latest", "localhost:5000/nizho:dev",
                          "ghcr.io/nizho-os/nizho/extra:latest"):
            self.assertIsNotNone(check.warning_for(host(entry(reference))), reference)

    def test_the_right_name_from_local_storage_gives_the_warning(self):
        self.assertIsNotNone(check.warning_for(host(entry("ghcr.io/nizho-os/nizho:latest", "containers-storage"))))

    def test_a_switch_waiting_for_a_restart_counts(self):
        booted = entry("localhost/nizho:dev", "containers-storage")
        self.assertIsNone(check.warning_for(host(booted, entry("ghcr.io/nizho-os/nizho:latest"))))
        away = host(entry("ghcr.io/nizho-os/nizho:latest"), entry("localhost/nizho:dev", "containers-storage"))
        self.assertIsNotNone(check.warning_for(away))

    def test_anything_unreadable_gives_no_warning(self):
        for text in ("", "not json", "[]", "{}", '{"status": null}', host(None),
                     host({"image": None, "incompatible": False, "pinned": False}),
                     host({"image": {"image": {"image": 7}}})):
            self.assertIsNone(check.warning_for(text), text)

    def test_repository_strips_tag_and_digest_but_not_a_registry_port(self):
        self.assertEqual(check.repository("localhost:5000/nizho:dev"), "localhost:5000/nizho")
        self.assertEqual(check.repository("ghcr.io/nizho-os/nizho:latest@sha256:abc"), "ghcr.io/nizho-os/nizho")


class RunTest(unittest.TestCase):
    def run_script(self, bootc_body, existing=None):
        with tempfile.TemporaryDirectory(prefix="nizho-image-ref-test-") as tmp:
            root = Path(tmp)
            (root / "bin").mkdir()
            if bootc_body is not None:
                (root / "status.json").write_text(bootc_body)
                fake = root / "bin" / "bootc"
                fake.write_text('#!/bin/sh\necho "$*" > "$TEST_ARGS"\ncat "$TEST_STATUS"\nexit "${TEST_EXIT:-0}"\n')
                fake.chmod(0o755)
            (root / "bin" / "cat").symlink_to("/usr/bin/cat")
            (root / "bin" / "sh").symlink_to("/bin/sh")
            warning = root / "run" / "nizho" / "image-ref-warning"
            if existing is not None:
                warning.parent.mkdir(parents=True)
                warning.write_text(existing)
            env = {"PATH": str(root / "bin"), "NIZHO_IMAGE_REF_WARNING": str(warning),
                   "TEST_STATUS": str(root / "status.json"), "TEST_ARGS": str(root / "args"),
                   "TEST_EXIT": "1" if bootc_body == "fail" else "0"}
            r = subprocess.run([sys.executable, "-I", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=20)
            args = (root / "args").read_text().strip() if (root / "args").exists() else None
            return r, (warning.read_text() if warning.exists() else None), args

    def test_wrong_image_writes_the_warning_file_and_the_journal_line(self):
        r, written, args = self.run_script(host(entry("localhost/nizho:dev", "containers-storage")))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(args, "status --json --format-version=1")
        self.assertIn("not set to receive Nizho updates", written)
        self.assertIn("sudo bootc switch ghcr.io/nizho-os/nizho:latest", written)
        self.assertIn("<4>nizho-image-ref-check: This computer is not set to receive Nizho updates.", r.stderr)

    def test_right_image_writes_nothing_and_removes_an_old_warning(self):
        r, written, _ = self.run_script(host(entry("ghcr.io/nizho-os/nizho:latest")), existing="old\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNone(written)

    def test_missing_or_failing_bootc_never_fails_and_writes_nothing(self):
        for body in (None, "fail", "not json"):
            r, written, _ = self.run_script(body)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIsNone(written)
            self.assertIn("nothing checked", r.stderr)

    def test_it_never_runs_bootc_switch(self):
        self.assertNotIn('"switch"', SCRIPT.read_text().split('"""', 2)[2])


if __name__ == "__main__":
    unittest.main()
