#!/usr/bin/env bash
# Explicit regular-file inputs and a private overlay; never opens a host disk.
set -euo pipefail
umask 077
root=$(cd "$(dirname "$0")/../.." && pwd)
: "${VM_BASE:?Set VM_BASE to a disposable Nizho qcow2 image}"
: "${OVMF_CODE:?Set OVMF_CODE to raw firmware code}"
: "${OVMF_VARS:?Set OVMF_VARS to raw firmware variables template}"
for tool in qemu-img qemu-system-x86_64; do command -v "$tool" >/dev/null; done
for file in "$VM_BASE" "$OVMF_CODE" "$OVMF_VARS"; do
    [[ -f "$file" && ! -L "$file" ]] || { echo "Not a regular file: $file" >&2; exit 2; }
    [[ "$file" != *','* && "$file" != *$'\n'* ]] || { echo 'Unsafe QEMU option separator in path' >&2; exit 2; }
done
memory=${VM_MEMORY:-4096}; cpus=${VM_CPUS:-2}
ssh_port=${VM_SSH_PORT:-22220}
[[ "$ssh_port" =~ ^[0-9]+$ ]] && (( ssh_port >= 1024 && ssh_port <= 65535 )) || {
    echo 'VM_SSH_PORT must be an unprivileged TCP port' >&2; exit 2;
}
[[ "$memory" =~ ^[0-9]+$ && "$cpus" =~ ^[0-9]+$ ]] || exit 2
available=$(awk '/MemAvailable:/ {print int($2/1024)}' /proc/meminfo)
(( memory >= 1024 && memory <= available-1024 && cpus >= 1 && cpus <= $(nproc) )) || {
    echo 'Invalid VM allocation or insufficient available memory (1 GiB host reserve required)' >&2; exit 2;
}
[[ -r /dev/kvm && -w /dev/kvm ]] || { echo 'KVM is not accessible' >&2; exit 2; }
state_root=${VM_STATE_ROOT:-"$root/output/vm"}
[[ "$state_root" != *','* && "$state_root" != *$'\n'* ]] || exit 2
mkdir -p "$state_root"
available_disk=$(df -Pk "$state_root" | awk 'END {print $4}')
(( available_disk >= 20971520 )) || { echo 'At least 20 GiB free disk space required' >&2; exit 2; }
state=$(mktemp -d "$state_root/nizho-archon-vm.XXXXXXXX")
printf '%s\n' 'nizho-archon-vm-v1' >"$state/owner.marker"
printf '%s\n' "$$" >"$state/qemu.pid"
echo "Preserved VM state: $state"
base=$(realpath "$VM_BASE")
qemu-img create -f qcow2 -F qcow2 -b "$base" "$state/disk.qcow2"
cp -- "$OVMF_VARS" "$state/vars.fd"
printf 'base=%s\nmemory_mib=%s\ncpus=%s\nssh_port=%s\n' "$base" "$memory" "$cpus" "$ssh_port" >"$state/config.txt"
exec qemu-system-x86_64 -enable-kvm -machine q35 -cpu host \
    -m "$memory" -smp "$cpus" \
    -drive "if=pflash,format=raw,readonly=on,file=$OVMF_CODE" \
    -drive "if=pflash,format=raw,file=$state/vars.fd" \
    -drive "if=virtio,format=qcow2,file=$state/disk.qcow2" \
    -nic "user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:$ssh_port-:22" -display none -monitor none \
    -serial "file:$state/serial.log" 2>"$state/qemu.log"
