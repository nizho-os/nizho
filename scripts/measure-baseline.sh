#!/bin/bash
# Nizho: print one JSON record of boot time, idle memory and what runs, on a booted Nizho.
#
#   scripts/measure-baseline.sh [--idle SECONDS] [--note TEXT] > baseline.json
#
# Run it as the signed-in user, in the Plasma session, right after login. It waits SECONDS
# (default 60) so the desktop settles, then reads. It only reads: it changes no setting, starts
# or stops nothing and needs no root. A value it cannot read is printed as null.
# docs/PERFORMANCE.md says how to run it in a VM and on a laptop and what to record with it.

set -u
export LC_ALL=C

IDLE=60
NOTE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --idle) IDLE=${2:-}; shift 2 ;;
        --note) NOTE=${2:-}; shift 2 ;;
        -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done
case "$IDLE" in ''|*[!0-9]*) echo "--idle needs a whole number of seconds" >&2; exit 2 ;; esac

# A JSON string, or null when empty.
js() {
    if [ -z "${1-}" ]; then printf 'null'; return; fi
    local s=$1
    s=${s//\\/\\\\}
    s=${s//\"/\\\"}
    s=${s//$'\t'/\\t}
    s=${s//$'\r'/}
    s=${s//$'\n'/\\n}
    s=$(printf '%s' "$s" | tr -d '\000-\010\013\014\016-\037')
    printf '"%s"' "$s"
}

# A JSON number, or null when not a number.
jn() {
    case "${1-}" in ''|*[!0-9.]*|*.*.*) printf 'null' ;; *) printf '%s' "$1" ;; esac
}

# Lines of stdin as a JSON array of strings.
jlines() {
    local first=1 line
    printf '['
    while IFS= read -r line; do
        [ -n "$line" ] || continue
        [ $first = 1 ] || printf ','
        first=0
        js "$line"
    done
    printf ']'
}

has() { command -v "$1" >/dev/null 2>&1; }

os_field() {
    sed -n "s/^$1=//p" /etc/os-release 2>/dev/null | head -1 | tr -d '"'
}

meminfo_kb() {
    awk -v k="$1:" '$1 == k { print $2; exit }' /proc/meminfo 2>/dev/null
}

battery() {
    local dir first=1 v
    printf '['
    for dir in /sys/class/power_supply/*; do
        [ -r "$dir/type" ] && [ "$(cat "$dir/type" 2>/dev/null)" = Battery ] || continue
        [ $first = 1 ] || printf ','
        first=0
        printf '{"name":%s' "$(js "${dir##*/}")"
        printf ',"status":%s' "$(js "$(cat "$dir/status" 2>/dev/null)")"
        for v in energy_now energy_full energy_full_design power_now charge_now charge_full current_now voltage_now capacity; do
            printf ',"%s":%s' "$v" "$(jn "$(cat "$dir/$v" 2>/dev/null)")"
        done
        printf '}'
    done
    printf ']'
}

STARTED=$(date -u +%Y-%m-%dT%H:%M:%SZ)
sleep "$IDLE"

ANALYZE_TIME=""
BLAME=""
if has systemd-analyze; then
    ANALYZE_TIME=$(timeout 20 systemd-analyze time 2>/dev/null | head -3)
    BLAME=$(timeout 20 systemd-analyze blame --no-pager 2>/dev/null | head -15 | sed 's/^ *//')
fi

TOP_RSS=$(ps -eo rss=,comm= --sort=-rss 2>/dev/null | head -15 | awk '{ printf "%s kB %s\n", $1, $2 }')
PROCS=$(find /proc -maxdepth 1 -name '[0-9]*' -type d 2>/dev/null | wc -l)

SYS_ENABLED=""
USER_ENABLED=""
SYS_RUNNING=""
USER_RUNNING=""
if has systemctl; then
    SYS_ENABLED=$(timeout 20 systemctl list-unit-files --type=service --state=enabled --no-legend --no-pager 2>/dev/null | awk '{print $1}')
    USER_ENABLED=$(timeout 20 systemctl --user list-unit-files --type=service --state=enabled --no-legend --no-pager 2>/dev/null | awk '{print $1}')
    SYS_RUNNING=$(timeout 20 systemctl list-units --type=service --state=running --no-legend --no-pager --plain 2>/dev/null | awk '{print $1}')
    USER_RUNNING=$(timeout 20 systemctl --user list-units --type=service --state=running --no-legend --no-pager --plain 2>/dev/null | awk '{print $1}')
fi

VIRT=""
has systemd-detect-virt && VIRT=$(systemd-detect-virt 2>/dev/null)
# The booted image and its digest from rpm-ostree, which answers without root. When it cannot,
# this is null: then write the image and commit down by hand (docs/PERFORMANCE.md).
IMAGE=""
if has rpm-ostree && has python3; then
    IMAGE=$(timeout 20 rpm-ostree status --json 2>/dev/null | python3 -I -c '
import json, sys
try:
    for d in json.load(sys.stdin).get("deployments", []):
        if d.get("booted"):
            print(d.get("container-image-reference", ""), d.get("container-image-reference-digest", ""))
            break
except Exception:
    pass
' 2>/dev/null)
fi

{
    printf '{'
    printf '"measured_at":%s' "$(js "$STARTED")"
    printf ',"idle_seconds":%s' "$IDLE"
    printf ',"note":%s' "$(js "$NOTE")"
    printf ',"os":{"pretty_name":%s,"version_id":%s,"image_version":%s}' \
        "$(js "$(os_field PRETTY_NAME)")" "$(js "$(os_field VERSION_ID)")" "$(js "$(os_field IMAGE_VERSION)")"
    printf ',"booted_image":%s' "$(js "$IMAGE")"
    printf ',"virtualization":%s' "$(js "$VIRT")"
    printf ',"kernel":%s' "$(js "$(uname -r)")"
    printf ',"cpu_count":%s' "$(jn "$(nproc 2>/dev/null)")"
    printf ',"boot":{"systemd_analyze_time":%s,"blame_top15":%s}' "$(js "$ANALYZE_TIME")" "$(printf '%s\n' "$BLAME" | jlines)"
    printf ',"memory_kb":{"total":%s,"free":%s,"available":%s,"buffers":%s,"cached":%s,"shmem":%s,"swap_total":%s,"swap_free":%s}' \
        "$(jn "$(meminfo_kb MemTotal)")" "$(jn "$(meminfo_kb MemFree)")" "$(jn "$(meminfo_kb MemAvailable)")" \
        "$(jn "$(meminfo_kb Buffers)")" "$(jn "$(meminfo_kb Cached)")" "$(jn "$(meminfo_kb Shmem)")" \
        "$(jn "$(meminfo_kb SwapTotal)")" "$(jn "$(meminfo_kb SwapFree)")"
    printf ',"top15_rss":%s' "$(printf '%s\n' "$TOP_RSS" | jlines)"
    printf ',"process_count":%s' "$(jn "$PROCS")"
    printf ',"services":{"system_enabled":%s,"user_enabled":%s,"system_running":%s,"user_running":%s}' \
        "$(printf '%s\n' "$SYS_ENABLED" | jlines)" "$(printf '%s\n' "$USER_ENABLED" | jlines)" \
        "$(printf '%s\n' "$SYS_RUNNING" | jlines)" "$(printf '%s\n' "$USER_RUNNING" | jlines)"
    printf ',"battery":%s' "$(battery)"
    printf '}\n'
}
