#!/usr/bin/env bash
# Changes only a disposable rootless container; never provisions host trust.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
podman=${PODMAN:-podman}
[[ $("$podman" info --format '{{.Host.Security.Rootless}}') == true ]] || {
    echo 'This test requires rootless Podman' >&2; exit 2;
}
"$podman" run --rm --network private \
    -e NIZHO_OWNER_TEST_CONTAINER=1 \
    -v "$root/archon-service/owner:/test:ro" \
    "${NIZHO_IMAGE:-localhost/nizho:archon-test}" \
    python3 /test/test_repository.py
