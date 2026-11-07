#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
python3 "$root/scripts/prepare-archon-source.py"
source="$root/integrations/archon/source"
python3 "$source/scripts/test-flatpak-metadata.py"
sandbox_test=commands::install::tests::cached_sources_fast_forward_and_preserve_local_edits_and_divergence
if command -v bwrap >/dev/null && bwrap --unshare-user --uid 0 --gid 0 true >/dev/null 2>&1; then
    cargo test --locked --manifest-path "$source/Cargo.toml" --workspace -- --test-threads=2
else
    echo "SKIP $sandbox_test: runner does not permit bubblewrap user namespaces" >&2
    cargo test --locked --manifest-path "$source/Cargo.toml" --workspace -- --test-threads=2 --skip "$sandbox_test"
fi
cargo test --locked --manifest-path "$root/archon-service/Cargo.toml"
python3 -m unittest discover -s "$root/archon-service/owner" -v
python3 "$root/scripts/test-archon-owner-key.py"
cargo clippy --locked --manifest-path "$root/archon-service/Cargo.toml" --all-targets -- -D warnings
cargo check --locked --manifest-path "$root/nizho-shell/Cargo.toml" -p nizho-capsule
cargo test --locked --manifest-path "$root/nizho-shell/Cargo.toml" -p nizho-capsule
"$root/scripts/test-archon-dbus.sh"
NIZHO_NATIVE_DBUS_TEST="$root/scripts/test-archon-native-dbus.py" \
NIZHO_ARCHON_SERVICE="$root/archon-service/target/debug/nizho-archon-service" \
    python3 "$source/scripts/test-flatpak-native.py"
if [[ -n "${XVFB:-}" ]] || command -v Xvfb >/dev/null; then
    "$root/scripts/test-archon-ui.sh"
    "$root/scripts/test-archon-native-ui.sh"
else
    echo 'UI tests NOT RUN: set XVFB or install Xvfb' >&2
    exit 2
fi
