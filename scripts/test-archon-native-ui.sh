#!/usr/bin/env bash
# Actual GTK widgets + real service + signed disposable Flatpak catalogue.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
xvfb=${XVFB:-Xvfb}
command -v "$xvfb" >/dev/null
python3 "$root/scripts/prepare-archon-source.py"
cargo build --locked --manifest-path "$root/archon-service/Cargo.toml"
state=$(mktemp -d "${TMPDIR:-/tmp}/nizho-native-ui.XXXXXXXX")
cargo test --locked --manifest-path "$root/nizho-shell/Cargo.toml" -p nizho-capsule --no-run --message-format=json >"$state/build.json"
ui_test=$(python3 - "$state/build.json" <<'PY'
import json,sys
for line in open(sys.argv[1]):
    event=json.loads(line)
    if event.get('reason')=='compiler-artifact' and event.get('profile',{}).get('test') and event.get('executable'):
        print(event['executable']);break
else:raise SystemExit('Missing UI test executable')
PY
)
echo "Preserved UI artifacts: $state"
"$xvfb" -displayfd 3 -screen 0 1280x900x24 -nolisten tcp 3>"$state/display" >"$state/xvfb.log" 2>&1 &
xpid=$!
trap 'kill "$xpid" 2>/dev/null || true; wait "$xpid" 2>/dev/null || true' EXIT
for attempt in $(seq 1 100); do [[ -s "$state/display" ]] && break; kill -0 "$xpid"; sleep .05; done
unset WAYLAND_DISPLAY
export DISPLAY=":$(cat "$state/display")" GDK_BACKEND=x11 GTK_A11Y=none GIO_USE_VFS=local GTK_USE_PORTAL=0 GSK_RENDERER=cairo
export NIZHO_UI_TEST_BINARY="$ui_test" NIZHO_UI_CAPTURE_DIR="$state"
export NIZHO_NATIVE_DBUS_TEST="$root/scripts/test-archon-native-dbus.py"
export NIZHO_ARCHON_SERVICE="$root/archon-service/target/debug/nizho-archon-service"
python3 "$root/integrations/archon/source/scripts/test-flatpak-native.py"
