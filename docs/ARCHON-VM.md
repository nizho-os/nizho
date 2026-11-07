# Disposable Archon VM validation

All VM disks are files created for this task. Never supply host block devices,
host EFI state or a real root disk. VM state is preserved unless explicitly
removed by its owner. Only the task validation-output directory is shared with
the build VM.

## Host tools

On this Arch host a rootless toolchain was extracted into
`~/.cache/nizho-build-tools`, not installed with pacman/sudo. Each package is
pinned by URL and SHA-256 in `integrations/archon/host-tools.sha256`, and its
signature is checked using the installed Arch signing keyring. gvproxy is built
from exact upstream commit `6dbb5d4fa0033356c72a201346f8350594229868`.

```sh
python3 scripts/vm/prepare-tools.py
PODMAN="$HOME/.cache/nizho-build-tools/podman"
"$PODMAN" info
```

Prepare tools before starting builds. Do not replace an executable/library while
it is in use. The generated wrapper/preparation lock prevents concurrent updates
for subsequent prepared-tool runs. QEMU/KVM, user namespaces/subuid mappings,
Go, bsdtar, gpgv and the Arch signing keyring are prerequisites for this host-only
bootstrap. Other hosts should install equivalent tools normally.

Image build:

```sh
python3 scripts/prepare-archon-source.py
PODMAN="$HOME/.cache/nizho-build-tools/podman" scripts/vm/build-image.sh
```

Logs are timestamped under `output/archon-validation/`; failed exit codes are
preserved by pipefail. No image success is inferred from successful Rust stages.

## Builder VM used

- QEMU/KVM, x86_64, 2 CPUs, 4 GiB RAM, 80 GiB disposable qcow2 disk.
- Fedora CoreOS machine OS: 44.20260928.3.2, Podman 6.1.4.
- Machine artifact manifest: `quay.io/podman/machine-os@sha256:9ce65f36295b0c0b9ea359e8b10c8ae940f968ea593ba3807ac03da41cdbbddf`.
- Compressed disk blob SHA-256: `f920fb922240233198d73a7cc2f60fe7716516b6c8a72da6c92c420f3dc4ee94`.
- Machine name: `nizho-archon-builder`.
- Shared path: repository `output/archon-validation` → guest `/workspace`.
- API/QMP/SSH bind locally; no host disk passthrough.

The Podman version used could not initialize directly from the manifest digest;
the exact artifact blob was downloaded and SHA-256 checked, then supplied as a
local compressed image. The normal tool cache contains this verified image.

```sh
"$PODMAN" machine init --provider qemu --rootful --cpus 2 --memory 4096 --disk-size 80 \
  --image "$HOME/.cache/nizho-build-tools/podman-machine.qcow2.zst" \
  --volume "$PWD/output/archon-validation:/workspace" nizho-archon-builder
"$PODMAN" machine start nizho-archon-builder
"$PODMAN" machine ssh nizho-archon-builder 'uname -m; sudo podman --version; systemctl --failed --no-legend'
```

In execution environments that kill subprocesses when a tool cell exits, keep
the VM-starting cell alive while using the machine. Do not count “started” output
alone as a healthy VM; verify a subsequent SSH command. A successful check here
reported x86_64, Podman 6.1.4, systemd running and no failed units. This builder VM
is not Nizho acceptance evidence.

## Nizho guest gates

After a successful complete image build, create the disposable disk with:

```sh
PODMAN="$HOME/.cache/nizho-build-tools/podman" python3 scripts/vm/create-vm.py
```

This checks the dedicated running builder VM and its task-only share, exports
the exact built image ID, verifies the imported ID, and invokes privileged bootc
only inside the VM against a newly created 48 GiB regular file. The resulting
qcow2, raw image, logs and disposable SSH key remain under a unique
`output/archon-validation/nizho-disk.*` directory. It never overwrites an existing
disk. Its initial source is the locally built development image; subsequent
official pulls retain Nizho's existing signature policy. This does not validate
the separate machine-owner signed deployment workflow.
The current disk helper adds a documented test-only image layer allowing SSH
from QEMU's 10.0.2.2 gateway and marks the image as disposable. It leaves the
production firewall definition intact. Both original and derived image IDs are
recorded in `source.json`.

With a successfully created Nizho qcow2 and raw-format OVMF firmware:

```sh
VM_BASE=/absolute/path/to/nizho.qcow2 \
OVMF_CODE=/absolute/path/to/OVMF_CODE.4m.fd \
OVMF_VARS=/absolute/path/to/OVMF_VARS.4m.fd \
VM_MEMORY=4096 VM_CPUS=2 VM_SSH_PORT=22220 scripts/vm/run-vm.sh
```

The runner makes an exclusive temporary state directory, copies firmware vars,
creates a qcow2 overlay, logs serial output and keeps the base image read-only.
It is a headless runner, not an automated graphical acceptance suite.
SSH is forwarded only on host loopback. Use the corresponding disposable key:

```sh
ssh -i output/archon-validation/nizho-disk.STATE/ssh_key -p 22220 \
  -o UserKnownHostsFile=output/archon-validation/nizho-disk.STATE/known_hosts \
  -o StrictHostKeyChecking=accept-new root@127.0.0.1
```

Required guest checks include:

```sh
systemctl --failed
systemctl status archon-service
journalctl -b -u archon-service
busctl introspect org.nizho.Archon1 /org/nizho/Archon1
archon capabilities
archon search Calculator
nizho-capsule
```

Then exercise real polkit authorization, trusted app install/update/remove,
service loss/recovery, invalid metadata/signatures, persistence across reboot,
and compare native Flatpak inventory/history. Test system image deployment and
rollback only after a verified image-package broker exists. There is currently
no supported system-package rollback test command. Do not substitute host DNF,
unsigned images or mocked deployment success.

`scripts/vm/test-guest-apps.py` is the production-service application test. Copy
it into the marked disposable guest and run it as root. It creates a signed
native fixture, checks rejection of an inactive unprivileged caller by real
polkit, then plans/installs/removes through the real system service. It checks
progress/completion signals, native installed state and history. The test retains
its repository/runtime/logs for reboot inspection and refuses physical hosts or
unmarked guests. Source presence is not a passing result; consult guest logs.
