#!/usr/bin/python3
"""Machine-owner trust provisioning. Run only by the installed root setup unit.

Build workers never receive the private directory. Native containers/image
signedBy verification uses the public key and local signature lookaside.
Provisioning is exclusive and fails closed; it never rotates existing keys.
"""
import copy
import fcntl
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import shutil

REPOSITORY = "localhost:30070/nizho-owner"
OFFICIAL = "ghcr.io/nizho-os/nizho"
STATE = Path("/var/lib/nizho-owner")
PUBLIC = "/etc/pki/containers/nizho-owner.gpg"


def policy_with_owner(current):
    """Preserve explicit trust, reject broad exceptions, add one local scope."""
    policy = copy.deepcopy(current)
    if not isinstance(policy, dict) or not isinstance(policy.get("transports"), dict):
        raise ValueError("Invalid container trust policy")
    docker = policy["transports"].setdefault("docker", {})
    official = docker.get(OFFICIAL)
    if not official or any(r.get("type") != "sigstoreSigned" for r in official):
        raise ValueError("Official Nizho signature policy is missing")
    if REPOSITORY in docker:
        raise ValueError("Machine-owner trust already exists; refusing key replacement")
    # A more specific scope could override the owner's mandatory signature.
    if any(s == "localhost:30070" or s.startswith(REPOSITORY + "/")
           or s.startswith(REPOSITORY + ":") or s.startswith(REPOSITORY + "@")
           for s in docker):
        raise ValueError("Conflicting local registry policy")
    # Fedora ships this local daemon exception. Tighten it as well as default;
    # the system deployment broker never accepts docker-daemon transport.
    if policy["transports"].get("docker-daemon") == {"": [{"type": "insecureAcceptAnything"}]}:
        policy["transports"]["docker-daemon"] = {"": [{"type": "reject"}]}
    for scopes in policy["transports"].values():
        if not isinstance(scopes, dict):
            raise ValueError("Invalid transport scopes")
        for requirements in scopes.values():
            if not requirements or any(r.get("type") == "insecureAcceptAnything"
                                       for r in requirements):
                raise ValueError("Remove unsigned transport exceptions before setup")
    policy["default"] = [{"type": "reject"}]
    docker[REPOSITORY] = [{
        "type": "signedBy", "keyType": "GPGKeys", "keyPath": PUBLIC,
        "signedIdentity": {"type": "matchRepository"},
    }]
    return policy


def private_directory(path):
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError(f"Unsafe private directory: {path}")


def publish(path, data, *, replace=False, mode=0o644):
    """Publish a fully synced file, never follow a destination symlink."""
    path = Path(path)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".nizho-owner-", delete=False) as f:
        temp = Path(f.name)
        try:
            f.write(data)
            f.flush()
            os.fchmod(f.fileno(), mode)
            os.fsync(f.fileno())
            if replace:
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
                    raise ValueError("Unsafe existing policy")
                os.replace(temp, path)
            else:
                os.link(temp, path)  # exclusive publication; EEXIST is an error
            fd = os.open(path.parent, os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            temp.unlink(missing_ok=True)


def generate_key(directory):
    private_directory(directory)
    command = ["/usr/bin/gpg", "--homedir", str(directory), "--batch", "--no-tty"]
    subprocess.run(command + ["--pinentry-mode", "loopback", "--passphrase", "",
                   "--quick-generate-key", "Nizho machine owner", "rsa3072", "sign", "0"],
                   check=True, timeout=120, stdout=subprocess.DEVNULL)
    keys = subprocess.check_output(command + ["--with-colons", "--list-secret-keys"], timeout=15)
    fingerprints = [line.split(b":")[9].decode("ascii")
                    for line in keys.splitlines() if line.startswith(b"fpr:")]
    if len(fingerprints) != 1:
        raise ValueError("Expected exactly one owner signing key")
    public = subprocess.check_output(command + ["--export", fingerprints[0]], timeout=15)
    if not public:
        raise ValueError("No public key exported")
    return fingerprints[0], public


def main():
    if os.geteuid() != 0 or not Path("/usr/lib/nizho-release").is_file():
        raise SystemExit("Owner trust setup requires root on installed Nizho")
    for tool in ("/usr/bin/gpg", "/usr/bin/openssl", "/usr/bin/skopeo", "/usr/bin/registry"):
        if not shutil.which(tool):
            raise SystemExit(f"Missing required owner repository tool: {tool}")
    os.umask(0o077)
    private_directory(STATE)
    lock = os.open(STATE / "setup.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock, "w") as locked:
        fcntl.flock(locked, fcntl.LOCK_EX | fcntl.LOCK_NB)
        policy_path = Path("/etc/containers/policy.json")
        original = policy_path.read_bytes()
        policy = policy_with_owner(json.loads(original))
        # Do not retry over partial key material: preserve it for recovery.
        keydir = STATE / "private"
        if keydir.exists():
            raise SystemExit("Owner key already exists; inspect interrupted setup before retrying")
        fingerprint, public = generate_key(keydir)
        private_directory(STATE / "signatures")
        # TLS is separate from image signing. Never disable registry TLS checks.
        certdir = Path("/etc/containers/certs.d/localhost:30070")
        certdir.mkdir(mode=0o755)
        certdir.chmod(0o755)  # Public certificate; setup's private umask must not hide it.
        with tempfile.TemporaryDirectory(dir=STATE, prefix="tls-") as temporary:
            temporary = Path(temporary)
            def openssl(*args):
                subprocess.run(["/usr/bin/openssl", *args], cwd=temporary,
                               check=True, timeout=120, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
            openssl("req", "-x509", "-newkey", "rsa:3072", "-noenc", "-days", "3650",
                    "-subj", "/CN=Nizho local registry CA", "-addext", "basicConstraints=critical,CA:TRUE",
                    "-addext", "keyUsage=critical,keyCertSign,cRLSign", "-keyout", "ca.key", "-out", "ca.crt")
            for name, purpose in (("server", "serverAuth"), ("client", "clientAuth")):
                openssl("req", "-new", "-newkey", "rsa:3072", "-noenc", "-subj", f"/CN={name}",
                        "-keyout", name + ".key", "-out", name + ".csr")
                (temporary / "extensions").write_text("basicConstraints=critical,CA:FALSE\n"
                    "keyUsage=critical,digitalSignature,keyEncipherment\n"
                    f"extendedKeyUsage={purpose}\nsubjectAltName=DNS:localhost,IP:127.0.0.1\n")
                openssl("x509", "-req", "-in", name + ".csr", "-CA", "ca.crt", "-CAkey", "ca.key",
                        "-CAcreateserial", "-days", "3650", "-extfile", "extensions", "-out", name + ".crt")
            publish(STATE / "tls-ca.key", (temporary / "ca.key").read_bytes(), mode=0o600)
            publish(STATE / "tls.key", (temporary / "server.key").read_bytes(), mode=0o600)
            publish(certdir / "ca.crt", (temporary / "ca.crt").read_bytes())
            publish(certdir / "server.crt", (temporary / "server.crt").read_bytes())
            publish(certdir / "client.cert", (temporary / "client.crt").read_bytes())
            publish(certdir / "client.key", (temporary / "client.key").read_bytes(), mode=0o600)
        publish(STATE / "policy.before.json", original)
        publish(PUBLIC, public)
        publish("/etc/containers/registries.d/nizho-owner.yaml",
                (f"docker:\n  {REPOSITORY}:\n"
                 "    lookaside: file:///var/lib/nizho-owner/signatures\n").encode())
        # Trust is activated last, after the public key and signature location.
        if policy_path.read_bytes() != original:
            raise SystemExit("Container policy changed during setup; not replacing it")
        publish(policy_path, (json.dumps(policy, indent=2) + "\n").encode(), replace=True)
        publish(STATE / "identity.json", (json.dumps({"schema": 1, "repository": REPOSITORY,
                "fingerprint": fingerprint}) + "\n").encode())
        print(f"Owner trust provisioned for {REPOSITORY}; fingerprint {fingerprint}")


if __name__ == "__main__":
    main()
