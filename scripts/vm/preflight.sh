#!/usr/bin/env bash
set -euo pipefail
failed=0
for tool in podman qemu-system-x86_64 qemu-img; do
    if ! command -v "$tool"; then echo "MISSING: $tool" >&2; failed=1; fi
done
if [[ ! -r /dev/kvm || ! -w /dev/kvm ]]; then echo 'MISSING: accessible /dev/kvm' >&2; failed=1; fi
free -h
df -h "${VM_STATE_ROOT:-$PWD}"
available=$(df -Pk "${VM_STATE_ROOT:-$PWD}" | awk 'END {print $4}')
if (( available < 62914560 )); then echo 'Need at least 60 GiB free' >&2; failed=1; fi
exit "$failed"
