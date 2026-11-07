#!/usr/bin/env bash
# Smoke/visual test; not a replacement for interactive acceptance testing.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
xvfb=${XVFB:-Xvfb}
command -v "$xvfb" >/dev/null
cargo build --locked --manifest-path "$root/nizho-shell/Cargo.toml" -p nizho-capsule
cargo build --locked --manifest-path "$root/archon-service/Cargo.toml"
state=$(mktemp -d "${TMPDIR:-/tmp}/nizho-archon-ui.XXXXXXXX")
echo "Preserved UI artifacts: $state"
"$xvfb" -displayfd 3 -screen 0 1280x900x24 -nolisten tcp 3>"$state/display" >"$state/xvfb.log" 2>&1 &
xpid=$!
trap 'kill "$xpid" 2>/dev/null || true; wait "$xpid" 2>/dev/null || true' EXIT
for attempt in $(seq 1 100); do [[ -s "$state/display" ]] && break; kill -0 "$xpid"; sleep .05; done
unset WAYLAND_DISPLAY
export GTK_A11Y=none GIO_USE_VFS=local GTK_USE_PORTAL=0 GSK_RENDERER=cairo
export DISPLAY=":$(cat "$state/display")" GDK_BACKEND=x11 NIZHO_ARCHON_TEST_SESSION=1
export ARCHON_UI_ROOT="$root" ARCHON_UI_STATE="$state"
# Service-available and service-unavailable launches use separate private buses.
for mode in available unavailable; do
export ARCHON_UI_MODE="$mode"
dbus-run-session -- bash -eu -o pipefail <<'BUS'
service=""
if [[ "$ARCHON_UI_MODE" == available ]]; then
    "$ARCHON_UI_ROOT/archon-service/target/debug/nizho-archon-service" --session >"$ARCHON_UI_STATE/service.log" 2>&1 &
    service=$!
fi
"$ARCHON_UI_ROOT/nizho-shell/target/debug/nizho-capsule" >"$ARCHON_UI_STATE/$ARCHON_UI_MODE.log" 2>&1 &
ui=$!
trap 'kill "$ui" ${service:+"$service"} 2>/dev/null || true; wait 2>/dev/null || true' EXIT
sleep 8
kill -0 "$ui"
if command -v import >/dev/null; then import -display "$DISPLAY" -window root "$ARCHON_UI_STATE/$ARCHON_UI_MODE.png"; fi
if rg -i 'panicked|segmentation fault|Gtk-ERROR' "$ARCHON_UI_STATE/$ARCHON_UI_MODE.log"; then exit 1; fi
echo "PASS: UI launch survived with service $ARCHON_UI_MODE"
BUS
done
