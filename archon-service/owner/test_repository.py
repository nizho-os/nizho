#!/usr/bin/python3
"""Native TLS registry + signed image acceptance/rejection in a disposable container.

This changes the container's trust files. It refuses to run on the host. The
synthetic image is only a signing fixture, never evidence of a bootc deployment.
"""
import hashlib
import json
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import time
import urllib.request

import trust


def main():
    if (os.environ.get("NIZHO_OWNER_TEST_CONTAINER") != "1"
            or not Path("/run/.containerenv").is_file() or os.geteuid() != 0):
        raise SystemExit("This test may run only in its disposable rootless test container")
    trust.main()
    identity = json.loads((trust.STATE / "identity.json").read_text())
    with tempfile.TemporaryDirectory(prefix="nizho-owner-repository-test.") as temporary:
        root = Path(temporary)
        config = {"version": "0.1", "storage": {"filesystem": {"rootdirectory": str(root / "registry")}},
                  "http": {"addr": "127.0.0.1:30070", "tls": {
                      "certificate": "/etc/containers/certs.d/localhost:30070/server.crt",
                      "key": str(trust.STATE / "tls.key"),
                      "clientcas": ["/etc/containers/certs.d/localhost:30070/ca.crt"],
                      "clientauth": "require-and-verify-client-cert"}}}
        (root / "registry.json").write_text(json.dumps(config))
        with (root / "registry.log").open("w") as log:
            registry = subprocess.Popen(["registry", "serve", str(root / "registry.json")],
                                        stdout=log, stderr=subprocess.STDOUT)
            try:
                context = ssl.create_default_context(cafile=config["http"]["tls"]["clientcas"][0])
                context.load_cert_chain("/etc/containers/certs.d/localhost:30070/client.cert",
                                        "/etc/containers/certs.d/localhost:30070/client.key")
                for _ in range(100):
                    try:
                        urllib.request.urlopen("https://localhost:30070/v2/", context=context, timeout=1).close()
                        break
                    except OSError:
                        if registry.poll() is not None:
                            raise RuntimeError((root / "registry.log").read_text())
                        time.sleep(.1)
                else:
                    raise RuntimeError("TLS registry did not start")
                unauthenticated = ssl.create_default_context(cafile=config["http"]["tls"]["clientcas"][0])
                try:
                    urllib.request.urlopen("https://localhost:30070/v2/", context=unauthenticated, timeout=5).close()
                except OSError:
                    pass
                else:
                    raise AssertionError("Registry accepted a client without its private certificate")
                print("PASS registry rejects unauthenticated local clients", flush=True)
                source = root / "fixture"
                source.mkdir()
                # Locally constructed empty image: no downloaded artifacts and
                # no shell/package execution. Only this exact directory is an
                # accepted unsigned input in the TEST publication policy.
                image_config = json.dumps({"architecture": "amd64", "os": "linux",
                    "rootfs": {"type": "layers", "diff_ids": []}, "config": {}}).encode()
                config_digest = hashlib.sha256(image_config).hexdigest()
                (source / config_digest).write_bytes(image_config)
                manifest = json.dumps({"schemaVersion": 2,
                    "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
                    "config": {"mediaType": "application/vnd.docker.container.image.v1+json",
                               "digest": "sha256:" + config_digest, "size": len(image_config)},
                    "layers": []}).encode()
                (source / "manifest.json").write_bytes(manifest)
                fixture_policy = root / "fixture-policy.json"
                fixture_policy.write_text(json.dumps({"default": [{"type": "reject"}],
                    "transports": {"dir": {str(source): [{"type": "insecureAcceptAnything"}]}}}))
                env = dict(os.environ, GNUPGHOME=str(trust.STATE / "private"))
                target = "docker://" + trust.REPOSITORY + ":test"
                subprocess.run(["skopeo", "--policy", str(fixture_policy), "copy", "--sign-by",
                                identity["fingerprint"], "dir:" + str(source), target],
                               env=env, check=True, timeout=60)
                digest = "sha256:" + hashlib.sha256(manifest).hexdigest()
                pinned = "docker://" + trust.REPOSITORY + "@" + digest

                def verify(destination):
                    return subprocess.run(["skopeo", "copy", pinned, "dir:" + str(root / destination)],
                                          capture_output=True, text=True, timeout=60)

                accepted = verify("accepted")
                assert accepted.returncode == 0, accepted.stderr
                print("PASS TLS registry and native signed image verification by exact digest", flush=True)
                signatures = list((trust.STATE / "signatures").rglob("signature-*"))
                assert len(signatures) == 1, signatures
                signature = signatures[0]
                original = signature.read_bytes()
                signature.write_bytes(b"invalid signature")
                rejected = verify("invalid")
                assert rejected.returncode != 0, "Invalid image signature accepted"
                signature.unlink()
                missing = verify("unsigned")
                assert missing.returncode != 0, "Unsigned image accepted"
                signature.write_bytes(original)
                assert verify("restored").returncode == 0
                print("PASS invalid and missing image signature rejection with policy unchanged", flush=True)
            finally:
                registry.terminate()
                registry.wait(timeout=15)


if __name__ == "__main__":
    main()
