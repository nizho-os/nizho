#!/usr/bin/env python3
"""Check how Nizho images are signed and how installed systems verify them, offline.

    python3 -I scripts/test-signing.py

Two kinds of test. The setup tests read the repository (workflows, Containerfile, the policy files and
cosign.pub) and check that they agree with each other, so a build can never go green while quietly skipping
its signing step. The round-trip tests use a throwaway key and the real cosign program, if it is installed,
to show that a signature checks out, and that a wrong key or a changed file does not.
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"

# SHA-256 of the DER form of public keys that must never sign Nizho images again. The first one is the
# key ArkaOS used before the project was renamed.
RETIRED_KEYS = {
    "2c1f5ca087a4e9755500ee0f88ce6efa401dbff0600d5c53258194996383b0cd": "ArkaOS signing key",
}


def text(path):
    return (ROOT / path).read_text(encoding="utf-8")


def tracked_files():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return [f for f in out.decode().split("\0") if f]


def public_key_der(path):
    return subprocess.run(["openssl", "pkey", "-pubin", "-in", str(path), "-outform", "DER"],
                          capture_output=True, check=True).stdout


class SetupTests(unittest.TestCase):
    def test_workflows_run_on_the_default_branch(self):
        # The new repository's default branch is main. A leftover "master" means builds never start.
        for name in ("image.yml", "pages.yml"):
            body = text(f".github/workflows/{name}")
            self.assertNotIn("master", body, f"{name} still mentions master")

    def test_publish_and_sign_steps_are_gated_on_main(self):
        body = text(".github/workflows/image.yml")
        gates = re.findall(r"if:\s*github\.ref == 'refs/heads/(\w+)'", body)
        self.assertGreaterEqual(len(gates), 4, "the push, sign and verify steps should each be gated")
        self.assertEqual(set(gates), {"main"})

    def test_public_key_is_a_p256_key(self):
        out = subprocess.run(["openssl", "pkey", "-pubin", "-in", str(ROOT / "cosign.pub"), "-noout", "-text"],
                             capture_output=True, text=True, check=True).stdout
        self.assertIn("256 bit", out)
        self.assertIn("prime256v1", out)

    def test_public_key_is_not_a_retired_key(self):
        digest = hashlib.sha256(public_key_der(ROOT / "cosign.pub")).hexdigest()
        self.assertNotIn(digest, RETIRED_KEYS, f"cosign.pub is the retired {RETIRED_KEYS.get(digest)}")

    def test_no_private_key_is_tracked(self):
        for f in tracked_files():
            self.assertFalse(f.endswith((".key", "cosign.key")), f"{f} looks like a private key")
            path = ROOT / f
            if path.is_file() and path.stat().st_size < 4096:
                head = path.read_text(errors="ignore")[:80]
                self.assertNotIn("ENCRYPTED SIGSTORE PRIVATE KEY", head, f"{f} holds a private key")

    def test_private_keys_are_ignored(self):
        ignore = text(".gitignore")
        self.assertIn("cosign.key", ignore)

    def test_installed_systems_trust_the_key_in_the_repository(self):
        containerfile = text("Containerfile")
        self.assertIn("COPY cosign.pub /etc/pki/containers/nizho.pub", containerfile)
        self.assertIn('"keyPath": "/etc/pki/containers/nizho.pub"', containerfile)
        self.assertIn('"type": "matchRepository"', containerfile)

    def test_policy_scope_matches_the_published_image(self):
        images = set()
        for name in ("image.yml", "disk.yml"):
            m = re.search(r"^\s*IMAGE:\s*(\S+)", text(f".github/workflows/{name}"), re.M)
            self.assertIsNotNone(m, f"{name} has no IMAGE")
            images.add(m.group(1))
        self.assertEqual(len(images), 1, "image.yml and disk.yml publish and read different images")
        image = images.pop()
        self.assertIn(f"{image}:", text("system/containers/nizho.yaml"))
        self.assertTrue(image.startswith("ghcr.io/nizho-os/"), image)

    def test_signing_uses_the_org_secret_and_checks_its_own_work(self):
        body = text(".github/workflows/image.yml")
        self.assertIn("COSIGN_PRIVATE_KEY: ${{ secrets.SIGNING_SECRET }}", body)
        self.assertLess(body.index("cosign sign"), body.index("cosign verify --key cosign.pub"),
                        "the signature must be verified after it is made")

    def test_this_check_runs_in_ci(self):
        self.assertIn("scripts/test-signing.py", text(".github/workflows/checks.yml"))


@unittest.skipUnless(shutil.which("cosign") and shutil.which("openssl"), "cosign is not installed")
class RoundTripTests(unittest.TestCase):
    """A throwaway key proves the signing tool and the key format work together. Nothing here touches a registry."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.env = {**os.environ, "COSIGN_PASSWORD": ""}
        cls.make_key(cls.dir / "a")
        cls.make_key(cls.dir / "b")
        cls.blob = cls.dir / "payload.txt"
        cls.blob.write_text("a Nizho image digest would go here\n")
        sign = subprocess.run(["cosign", "sign-blob", "--yes", "--key", str(cls.dir / "a" / "cosign.key"),
                               "--bundle", str(cls.dir / "payload.bundle"), str(cls.blob)],
                              env=cls.env, capture_output=True, text=True)
        assert sign.returncode == 0, sign.stderr

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def make_key(cls, folder):
        folder.mkdir()
        done = subprocess.run(["cosign", "generate-key-pair"], cwd=folder, env=cls.env,
                              capture_output=True, text=True)
        assert done.returncode == 0, done.stderr

    def verify(self, pub, blob=None):
        return subprocess.run(["cosign", "verify-blob", "--key", str(pub), "--bundle",
                               str(self.dir / "payload.bundle"), str(blob or self.blob)],
                              env=self.env, capture_output=True, text=True)

    def test_a_signature_checks_out_with_its_own_public_key(self):
        self.assertEqual(self.verify(self.dir / "a" / "cosign.pub").returncode, 0)

    def test_a_different_key_does_not_verify_it(self):
        self.assertNotEqual(self.verify(self.dir / "b" / "cosign.pub").returncode, 0)

    def test_a_changed_file_does_not_verify(self):
        tampered = self.dir / "tampered.txt"
        tampered.write_text("a different payload\n")
        self.assertNotEqual(self.verify(self.dir / "a" / "cosign.pub", tampered).returncode, 0)

    def test_generated_public_key_has_the_shape_the_repository_expects(self):
        out = subprocess.run(["openssl", "pkey", "-pubin", "-in", str(self.dir / "a" / "cosign.pub"), "-noout",
                              "-text"], capture_output=True, text=True, check=True).stdout
        self.assertIn("prime256v1", out)


if __name__ == "__main__":
    sys.exit(unittest.main(verbosity=1))
