#!/usr/bin/env bash
# On-device smoke test for nizho-agent. Prints PASS/FAIL per step.
# Needs: a running Ollama with qwen2.5 pulled, and nizhod on the system bus.
#   ./scripts/live-check.sh
set -uo pipefail
cd "$(dirname "$0")/.."

pass=0; fail=0
ok(){   echo "PASS  $1"; pass=$((pass+1)); }
no(){   echo "FAIL  $1"; fail=$((fail+1)); }

OLLAMA="${OLLAMA_URL:-http://127.0.0.1:11434}"

# 1. Ollama up
if curl -fsS "$OLLAMA/api/tags" >/dev/null 2>&1; then ok "Ollama reachable at $OLLAMA"; else no "Ollama not reachable at $OLLAMA"; fi

# 2. the supported model is pulled (there is no fallback model)
tags="$(curl -fsS "$OLLAMA/api/tags" 2>/dev/null || echo '')"
if printf '%s' "$tags" | grep -q 'qwen2.5:7b-instruct'; then ok "model qwen2.5:7b-instruct present"
else no "qwen2.5:7b-instruct not pulled (ollama pull qwen2.5:7b-instruct)"; fi

# 3. nizhod on the system bus
if command -v busctl >/dev/null 2>&1 && busctl --system list 2>/dev/null | grep -q 'org.nizho.nizhod'; then
    ok "nizhod on the system bus (org.nizho.nizhod)"
elif command -v gdbus >/dev/null 2>&1 && gdbus introspect --system --dest org.nizho.nizhod --object-path /org/nizho/nizhod >/dev/null 2>&1; then
    ok "nizhod on the system bus (org.nizho.nizhod)"
else
    no "nizhod not found on the system bus"
fi

# 4. build
if cargo build -q 2>/dev/null; then ok "cargo build"; else no "cargo build"; fi
BIN=target/debug/nizho-agent

# 5. a read request answers
if [ -x "$BIN" ] && "$BIN" ask "is DNS-over-TLS on?" >/tmp/nizho-live-read.out 2>&1; then
    ok "ask \"is DNS-over-TLS on?\" -> $(tail -1 /tmp/nizho-live-read.out | cut -c1-60)"
else
    no "read request failed"; tail -3 /tmp/nizho-live-read.out 2>/dev/null
fi

# 6. a write request, DRY-RUN first, auto-declined at the prompt (no real change)
DRYCFG="$(mktemp --suffix=.toml)"
printf 'dry_run = true\n' > "$DRYCFG"
if [ -x "$BIN" ] && printf 'n\n' | "$BIN" --config "$DRYCFG" ask "turn off MAC randomization" >/tmp/nizho-live-write.out 2>&1; then
    ok "write request (dry-run) reached the approval prompt and was declined cleanly"
else
    no "write request (dry-run) errored"; tail -3 /tmp/nizho-live-write.out 2>/dev/null
fi
rm -f "$DRYCFG"

# 7. audit chain verifies
if [ -x "$BIN" ] && "$BIN" log verify >/tmp/nizho-live-verify.out 2>&1 && grep -q 'chain intact\|OK' /tmp/nizho-live-verify.out; then
    ok "log verify -> $(cat /tmp/nizho-live-verify.out)"
else
    no "log verify failed"; cat /tmp/nizho-live-verify.out 2>/dev/null
fi

echo "---"
echo "live-check: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
