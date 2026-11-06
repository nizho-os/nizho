#!/usr/bin/env python3
"""Native GPG key test in a disposable directory; never changes system trust.

This validates key isolation and cryptographic rejection, not image deployment.
"""
import importlib.util
from pathlib import Path
import subprocess
import tempfile

source = Path(__file__).resolve().parents[1] / "archon-service/owner/trust.py"
spec = importlib.util.spec_from_file_location("owner_trust", source)
trust = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trust)

with tempfile.TemporaryDirectory(prefix="nizho-owner-key-test.") as temporary:
    root = Path(temporary)
    secret = root / "private"
    try:
        fingerprint, public = trust.generate_key(secret)
        (root / "public.gpg").write_bytes(public)
        payload = root / "manifest.json"
        payload.write_text('{"test":"owner-signing-key"}\n')
        command = ["gpg", "--homedir", str(secret), "--batch", "--no-tty"]
        subprocess.run(command + ["--local-user", fingerprint, "--detach-sign", str(payload)],
                       check=True, timeout=30)
        verify = ["gpgv", "--homedir", str(root), "--keyring", str(root / "public.gpg"),
                  str(payload) + ".sig", str(payload)]
        subprocess.run(verify, check=True, timeout=15, capture_output=True)
        payload.write_text('{"test":"tampered"}\n')
        failure = subprocess.run(verify, timeout=15, capture_output=True)
        assert failure.returncode != 0, "Modified content was accepted"
        assert secret.stat().st_mode & 0o077 == 0
        for key in (secret / "private-keys-v1.d").iterdir():
            assert key.stat().st_mode & 0o077 == 0
        print("PASS native owner key generation, public-only verification, tamper rejection, private permissions")
    finally:
        subprocess.run(["gpgconf", "--homedir", str(secret), "--kill", "gpg-agent"],
                       timeout=15, check=False)
