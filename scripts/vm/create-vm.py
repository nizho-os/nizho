#!/usr/bin/env python3
"""Create a disposable Nizho disk inside the dedicated builder VM.

Only the VM receives privileged container access. Host operations create regular
files under output/archon-validation; no host block device is ever passed in.
All artifacts (including failed builds) are retained for inspection.
"""
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
MACHINE = "nizho-archon-builder"


def main():
    os.umask(0o077)
    podman = os.environ.get("PODMAN", "podman")
    for tool in (podman, "ssh-keygen", "qemu-img"):
        if not shutil.which(tool):
            raise SystemExit(f"Missing required tool: {tool}")
    image = os.environ.get("NIZHO_IMAGE", "localhost/nizho:archon-test")
    info = json.loads(subprocess.check_output([podman, "image", "inspect", image]))[0]
    image_id = info["Id"]
    if not re.fullmatch(r"(?:sha256:)?[a-f0-9]{64}", image_id):
        raise SystemExit("Invalid source image identity")
    machine = json.loads(subprocess.check_output([podman, "machine", "inspect", MACHINE]))[0]
    if machine["State"] != "running" or not machine["Rootful"]:
        raise SystemExit("Dedicated rootful builder VM must be running")
    output = ROOT / "output/archon-validation"
    output.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(output).free < 60 * 1024**3:
        raise SystemExit("At least 60 GiB free space is required")
    state = Path(tempfile.mkdtemp(prefix="nizho-disk.", dir=output))
    guest = "/workspace/" + state.name
    print(f"Preserving all VM artifacts in {state}", flush=True)
    marker = secrets.token_hex(32)
    (state / "share.marker").write_text(marker)

    def remote(args, *, capture=False, log=None):
        command = [podman, "machine", "ssh", MACHINE, shlex.join(args)]
        if capture:
            return subprocess.check_output(command, text=True).strip()
        subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT)

    if remote(["cat", guest + "/share.marker"], capture=True) != marker:
        raise SystemExit("Builder VM does not share the expected task directory")
    if remote(["systemd-detect-virt"], capture=True) not in ("kvm", "qemu"):
        raise SystemExit("Refusing disk installation outside the dedicated virtual machine")
    guest_free = int(remote(["df", "--output=avail", "-B1", "/var"], capture=True).splitlines()[-1])
    if guest_free < 30 * 1024**3:
        raise SystemExit("Builder VM requires at least 30 GiB free storage")
    with (state / "create.log").open("w") as log:
        archive = state / "nizho.oci.tar"
        scratch = state / "export-tmp"
        scratch.mkdir()
        subprocess.run([podman, "save", "--format", "oci-archive", "--output", str(archive), image_id],
                       check=True, stdout=log, stderr=subprocess.STDOUT,
                       env=dict(os.environ, TMPDIR=str(scratch)))
        remote(["sudo", "podman", "load", "--input", guest + "/nizho.oci.tar"], log=log)
        imported = remote(["sudo", "podman", "image", "inspect", "--format", "{{.Id}}", image_id], capture=True)
        if imported.removeprefix("sha256:") != image_id.removeprefix("sha256:"):
            raise SystemExit("Image identity changed during transfer")
        # Production intentionally rejects inbound SSH. Add a test-only layer
        # allowing just QEMU user networking's gateway; never weaken the shipped
        # firewall zone or expose SSH outside the host's loopback forwarding.
        context = state / "test-context"
        context.mkdir()
        (context / "access.service").write_text("""[Unit]
Description=Disposable Nizho VM test access
After=firewalld.service
Requires=firewalld.service
ConditionPathExists=/etc/nizho-archon-disposable-test
[Service]
Type=oneshot
ExecStart=/usr/bin/firewall-cmd --zone=nizho "--add-rich-rule=rule family=ipv4 source address=10.0.2.2/32 service name=ssh accept"
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
""")
        (context / "marker").write_text("Disposable VM test image only\n")
        (context / "Containerfile").write_text(f"FROM {image_id}\n"
            "COPY access.service /etc/systemd/system/nizho-test-access.service\n"
            "COPY marker /etc/nizho-archon-disposable-test\n"
            "RUN systemctl enable nizho-test-access.service\n")
        remote(["sudo", "podman", "build", "--pull=never", "--iidfile", guest + "/test-image.id",
                "--file", guest + "/test-context/Containerfile", guest + "/test-context"], log=log)
        test_image = (state / "test-image.id").read_text().strip()
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", test_image):
            raise SystemExit("Invalid derived test image identity")
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "nizho-disposable-vm",
                        "-f", str(state / "ssh_key")], check=True)
        raw = state / "nizho.raw"
        with raw.open("xb") as disk:
            disk.truncate(48 * 1024**3)
        # The sole privileged command runs INSIDE the verified VM. The target
        # is our exclusively created regular file, using bootc's loopback mode.
        # The source is the exact locally built image ID; future official pulls
        # retain the image's existing official Nizho signature policy.
        remote(["sudo", "podman", "run", "--rm", "--privileged", "--pid=host",
                "--security-opt", "label=type:unconfined_t",
                "-v", "/var/lib/containers:/var/lib/containers", "-v", "/dev:/dev",
                "-v", guest + ":/output", test_image, "bootc", "install", "to-disk",
                "--via-loopback", "--generic-image", "--filesystem", "xfs",
                "--target-imgref", "ghcr.io/nizho-os/nizho:latest",
                "--root-ssh-authorized-keys", "/output/ssh_key.pub",
                "--karg", "console=tty0", "--karg", "console=ttyS0,115200n8",
                "/output/nizho.raw"], log=log)
        subprocess.run(["qemu-img", "convert", "-f", "raw", "-O", "qcow2",
                        str(raw), str(state / "nizho.qcow2")], check=True, stdout=log,
                       stderr=subprocess.STDOUT)
    (state / "source.json").write_text(json.dumps({"image": image, "id": image_id,
        "test_image": test_image, "machine": MACHINE, "disk_gib": 48,
        "status": "disk-created-not-booted"}, indent=2) + "\n")
    print(f"Disk created, boot validation still required: {state / 'nizho.qcow2'}")


if __name__ == "__main__":
    main()
