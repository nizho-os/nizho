#!/usr/bin/env bash
# Private bus, real service; no privileged operations or mock backend.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
cargo build --locked --manifest-path "$root/archon-service/Cargo.toml"
export ARCHON_TEST_BIN="$root/archon-service/target/debug/nizho-archon-service"
exec dbus-run-session -- bash -eu -o pipefail <<'BUS'
"$ARCHON_TEST_BIN" --session &
service=$!
trap 'kill "$service" 2>/dev/null || true; wait "$service" 2>/dev/null || true' EXIT
for attempt in $(seq 1 100); do
    if busctl --user status org.nizho.Archon1 >/dev/null 2>&1; then break; fi
    kill -0 "$service"
    sleep 0.05
done
busctl --user introspect org.nizho.Archon1 /org/nizho/Archon1
call() { busctl --user call org.nizho.Archon1 /org/nizho/Archon1 org.nizho.Archon1 "$@"; }
call GetCapabilities
if output=$(call SearchPackages s '' 2>&1); then echo 'Empty search accepted' >&2; exit 1; fi
[[ "$output" == *'1–128'* ]]
if output=$(call GetPackageInfo s '../etc/passwd' 2>&1); then echo 'Unsafe ID accepted' >&2; exit 1; fi
if output=$(call ApplyPlan s replay 2>&1); then echo 'Unsupported mutation accepted' >&2; exit 1; fi
[[ "$output" == *'disabled'* ]]
manager() { busctl --user call org.nizho.Archon1 /org/nizho/Archon1 org.nizho.Archon1.Manager "$@"; }
manager GetCapabilities
manager GetInstalledPackages
manager GetRepositories
manager GetTransactionHistory uu 0 100
if output=$(manager SearchPackages ssuu '' '' 0 101 2>&1); then echo 'Oversized page accepted' >&2; exit 1; fi
if output=$(manager PlanTransaction sss install '../escape' flathub 2>&1); then echo 'Unsafe transaction accepted' >&2; exit 1; fi
if output=$(manager CommitTransaction s '../escape' 2>&1); then echo 'Unsafe plan accepted' >&2; exit 1; fi
if output=$(manager CancelTransaction s '00000000000000000000000000000000' 2>&1); then echo 'Unknown cancellation accepted' >&2; exit 1; fi
echo 'PASS: real Flatpak inventory/repositories, history, pagination bounds, malformed plans'
echo 'PASS: real D-Bus startup, introspection, capabilities, invalid arguments, mutation rejection'
BUS
