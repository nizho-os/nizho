#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
podman=${PODMAN:-podman}
command -v "$podman" >/dev/null
mkdir -p "$root/output/archon-validation"
log="$root/output/archon-validation/build-$(date -u +%Y%m%dT%H%M%SZ).log"
# Sequential stages keep memory bounded on developer laptops.
"$podman" build --jobs "${BUILD_JOBS:-1}" -f "$root/Containerfile" \
    -t "${NIZHO_IMAGE:-localhost/nizho:archon-test}" "$root" 2>&1 | tee "$log"
