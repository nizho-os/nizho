#!/usr/bin/env bash
# Run INSIDE a booted Nizho VM, as root:   sudo bash vm-test-phone-link.sh
#
# Checks the phone-link helper against the real firewall, step by step. Each check prints PASS, FAIL,
# SKIP or INFO with the evidence. The exit code is non-zero if any check failed.
#
# It changes the firewall and /etc/nizho/phone-link only through the helper, plus (for the connection
# tests) one temporary network namespace "nzt" with a veth pair, and two temporary listeners. All of that
# is removed on exit, and the original on/off state is put back (also on Ctrl+C).
#
# Why a namespace: a connection from the VM to its own address travels over the loopback path, which the
# firewall always accepts, so it would prove nothing. A connection from the namespace arrives on a real
# interface and meets the firewall zone like a connection from another computer on the network.
#
# Takes up to about 6 minutes: the two drift checks wait for the privacy daemon (nizhod).

HELPER=${NIZHO_PHONE_LINK_HELPER:-/usr/libexec/nizho-phone-link}
STATE=/etc/nizho/phone-link
ZONE=nizho
SVC=kdeconnect
NS=nzt
HOST_IF=nzt-h
NS_IF=nzt-n
HOST_IP=10.213.77.1
NS_IP=10.213.77.2
IN_PORT=1764        # inside the KDE Connect range, last port, so kdeconnectd is unlikely to hold it
OUT_PORT=1800       # outside the range
POLL_MAX=150
POLL_STEP=5

PASSN=0; FAILN=0; SKIPN=0; INFON=0
LISTENERS=()
STARTED_KDEC_PID=""
NS_UP=0
INIT_FLAG=""        # "on" or "off"
INIT_RT=""; INIT_PERM=""
RESTORED=0

say()  { printf '%s\n' "$*"; }
pass() { PASSN=$((PASSN+1)); say "PASS  $1"; shift; for l in "$@"; do say "        $l"; done; }
fail() { FAILN=$((FAILN+1)); say "FAIL  $1"; shift; for l in "$@"; do say "        $l"; done; }
skip() { SKIPN=$((SKIPN+1)); say "SKIP  $1"; shift; for l in "$@"; do say "        $l"; done; }
info() { INFON=$((INFON+1)); say "INFO  $1"; shift; for l in "$@"; do say "        $l"; done; }
head_() { say ""; say "== $* =="; }

fw() { firewall-cmd "$@" 2>&1; }
rt_has()   { firewall-cmd --zone=$ZONE --list-services 2>/dev/null | tr ' ' '\n' | grep -qx "$SVC"; }
perm_has() { firewall-cmd --permanent --zone=$ZONE --list-services 2>/dev/null | tr ' ' '\n' | grep -qx "$SVC"; }
flag_of()  { if [ "$(cat "$STATE" 2>/dev/null)" = on ]; then echo on; else echo off; fi; }
helper()   { env -u NIZHO_PHONE_LINK_TEST "$HELPER" "$@" 2>&1; }

restore() {
    [ "$RESTORED" = 1 ] && return
    RESTORED=1
    say ""; say "== Restoring the original state =="
    for p in "${LISTENERS[@]}"; do kill "$p" 2>/dev/null; done
    if [ -n "$STARTED_KDEC_PID" ]; then kill "$STARTED_KDEC_PID" 2>/dev/null; say "stopped the kdeconnectd this script started (pid $STARTED_KDEC_PID)"; fi
    if [ "$NS_UP" = 1 ]; then ip netns del $NS 2>/dev/null; ip link del $HOST_IF 2>/dev/null; fi
    if [ -n "$INIT_FLAG" ] && [ -x "$HELPER" ]; then
        if [ "$INIT_FLAG" = on ]; then helper on >/dev/null; else helper off >/dev/null; fi
        # A rule added by hand during the test must not stay behind.
        if [ "$INIT_FLAG" = off ]; then
            rt_has   && firewall-cmd --zone=$ZONE --remove-service=$SVC >/dev/null 2>&1
            perm_has && firewall-cmd --permanent --zone=$ZONE --remove-service=$SVC >/dev/null 2>&1
        fi
        say "initial: flag=$INIT_FLAG runtime-rule=$INIT_RT permanent-rule=$INIT_PERM"
        say "now:     flag=$(flag_of) runtime-rule=$(rt_has && echo yes || echo no) permanent-rule=$(perm_has && echo yes || echo no)"
    fi
}
on_signal() { say ""; say "Interrupted."; FAILN=$((FAILN+1)); restore; exit 130; }
trap restore EXIT
trap on_signal INT TERM

[ "$(id -u)" = 0 ] || { say "Run as root: sudo bash $0"; exit 2; }
for t in firewall-cmd ss runuser systemctl journalctl; do
    command -v "$t" >/dev/null || { say "missing tool: $t"; exit 2; }
done

# ---- record the initial state before touching anything -----------------------------------------------
if [ -e "$STATE" ]; then INIT_FLAG=$(flag_of); else INIT_FLAG=off; fi
INIT_RT=$(rt_has && echo yes || echo no)
INIT_PERM=$(perm_has && echo yes || echo no)
say "Nizho phone-link test, $(date -Is)"
say "helper: $HELPER"
say "initial state: flag=$INIT_FLAG runtime-rule=$INIT_RT permanent-rule=$INIT_PERM"
say "kde-connect package: $(rpm -q kde-connect 2>/dev/null || rpm -q kdeconnect-kde 2>/dev/null || echo 'not found by rpm')"

# ---- helpers for the connection test -------------------------------------------------------------------
start_listener() {   # port; prints nothing, appends pid to LISTENERS; returns 1 if it could not start
    local port=$1
    if ss -ltn "sport = :$port" 2>/dev/null | grep -q LISTEN; then return 1; fi
    if command -v python3 >/dev/null; then
        python3 -c 'import socket,sys
s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
s.bind(("0.0.0.0",int(sys.argv[1]))); s.listen(8)
while True:
    c,_=s.accept(); c.close()' "$port" &
    elif command -v nc >/dev/null; then
        ( while true; do nc -l "$port" </dev/null >/dev/null 2>&1 || sleep 1; done ) &
    else
        return 1
    fi
    LISTENERS+=("$!")
    sleep 1
    ss -ltn "sport = :$port" 2>/dev/null | grep -q LISTEN
}

setup_ns() {
    command -v ip >/dev/null || return 1
    ip netns del $NS 2>/dev/null; ip link del $HOST_IF 2>/dev/null
    ip netns add $NS || return 1
    NS_UP=1
    ip link add $HOST_IF type veth peer name $NS_IF || return 1
    ip link set $NS_IF netns $NS || return 1
    ip addr add $HOST_IP/30 dev $HOST_IF && ip link set $HOST_IF up || return 1
    ip -n $NS addr add $NS_IP/30 dev $NS_IF && ip -n $NS link set $NS_IF up && ip -n $NS link set lo up || return 1
    ip netns exec $NS bash -c "exec 3<>/dev/tcp/$HOST_IP/9 " >/dev/null 2>&1   # warm up ARP; result ignored
    return 0
}

# conn PORT -> prints accepted | refused | timeout(dropped) ; connection made from the namespace.
conn() {
    local port=$1 rc
    ip netns exec $NS timeout 4 bash -c "exec 3<>/dev/tcp/$HOST_IP/$port" >/dev/null 2>&1
    rc=$?
    case $rc in 0) echo accepted ;; 124) echo "timeout(dropped)" ;; *) echo refused ;; esac
}

# Wait up to POLL_MAX seconds for a condition (a shell function name); echoes seconds waited.
poll_until() {
    local t=0
    while [ $t -lt $POLL_MAX ]; do
        if "$@"; then echo "$t"; return 0; fi
        sleep $POLL_STEP; t=$((t+POLL_STEP))
    done
    "$@" && { echo "$t"; return 0; }
    echo "$t"; return 1
}
no_rt() { ! rt_has; }
has_rt() { rt_has; }

# ======================================================================================================
head_ "1. The helper exists, is executable and is root-only"
if [ -x "$HELPER" ]; then
    mode=$(stat -c '%a %U:%G' "$HELPER")
    pass "helper exists and is executable" "$HELPER  mode/owner: $mode"
    perm=$(stat -c '%a' "$HELPER"); own=$(stat -c '%U' "$HELPER")
    if [ "$own" = root ] && [ $((8#$perm & 8#022)) -eq 0 ]; then pass "owned by root, not writable by group or others" "$mode"
    else fail "ownership or permissions allow others to change the helper" "$mode"; fi
    out=$(runuser -u nobody -- env -u NIZHO_PHONE_LINK_TEST "$HELPER" status 2>&1); rc=$?
    if [ $rc = 77 ]; then pass "non-root user gets exit 77" "as nobody: exit $rc, output: $out"
    else fail "non-root run should exit 77" "as nobody: exit $rc, output: $out"; fi
    before=$(flag_of)$(rt_has && echo y || echo n)
    out=$(runuser -u nobody -- env -u NIZHO_PHONE_LINK_TEST "$HELPER" on 2>&1); rc=$?
    after=$(flag_of)$(rt_has && echo y || echo n)
    if [ $rc = 77 ] && [ "$before" = "$after" ]; then pass "non-root 'on' refused and changed nothing" "exit $rc, output: $out"
    else fail "non-root 'on' was not refused cleanly" "exit $rc, before/after: $before/$after, output: $out"; fi
else
    fail "helper missing or not executable" "$HELPER"
    say "Cannot continue without the helper."; exit 1
fi
if command -v pkexec >/dev/null && [ -f /usr/share/polkit-1/actions/org.nizho.phone-link.policy ]; then
    pass "polkit action file present" "/usr/share/polkit-1/actions/org.nizho.phone-link.policy"
else
    fail "polkit action file or pkexec missing" "expected /usr/share/polkit-1/actions/org.nizho.phone-link.policy"
fi

# ======================================================================================================
head_ "2. Initial state is off"
if [ "$INIT_FLAG" = off ] && [ "$INIT_RT" = no ] && [ "$INIT_PERM" = no ] && [ ! -e "$STATE" ]; then
    pass "starts off: no state file, no kdeconnect service in the zone (runtime and permanent)" \
         "$(fw --zone=$ZONE --list-services | sed 's/^/services: /')"
    if firewall-cmd --zone=$ZONE --list-all 2>/dev/null | grep -Eq '1714-1764|1716'; then
        fail "zone lists KDE Connect ports directly" "$(firewall-cmd --zone=$ZONE --list-all)"
    else pass "no KDE Connect port range in the zone listing"; fi
else
    skip "the VM did not start with phone linking off, so the shipped default is not checked here" \
         "flag=$INIT_FLAG runtime=$INIT_RT permanent=$INIT_PERM state-file-exists=$([ -e "$STATE" ] && echo yes || echo no)" \
         "the script turns it off now and puts the original state back at the end"
    helper off >/dev/null
fi

# ======================================================================================================
head_ "3. 'on' (no connection test yet)"
out=$(helper on); rc=$?
[ $rc = 0 ] && pass "'on' exits 0" "$out" || fail "'on' exit code is $rc" "$out"
[ "$(cat "$STATE" 2>/dev/null)" = on ] && pass "state file says 'on'" "$STATE: $(cat "$STATE" 2>/dev/null)" \
    || fail "state file does not say 'on'" "$STATE: $(cat "$STATE" 2>/dev/null || echo missing)"
rt_has   && pass "runtime zone lists the service" "$(fw --zone=$ZONE --list-services)" \
         || fail "runtime zone does not list the service" "$(fw --zone=$ZONE --list-services)"
perm_has && pass "permanent zone lists the service" "$(fw --permanent --zone=$ZONE --list-services)" \
         || fail "permanent zone does not list the service" "$(fw --permanent --zone=$ZONE --list-services)"
other=$(firewall-cmd --get-active-zones 2>/dev/null | grep -v '^ ' | grep -vx "$ZONE")
if firewall-cmd --list-services --zone=public 2>/dev/null | tr ' ' '\n' | grep -qx "$SVC"; then
    fail "the service also appeared in the public zone" "$(fw --zone=public --list-services)"
else pass "no other zone got the service (checked: public)" "other active zones: ${other:-none}"; fi
listening=$(ss -lunt 2>/dev/null | awk 'NR>1 {print $5}' | grep -E ':(17(1[4-9]|[2-5][0-9]|6[0-4]))$')
if pgrep -x kdeconnectd >/dev/null; then
    info "kdeconnectd is already running, so listeners in 1714-1764 are expected" "$listening"
elif [ -z "$listening" ]; then
    pass "nothing listens in 1714-1764 yet" "Normal: the rule only allows traffic. Ports open when kdeconnectd runs."
else
    fail "something listens in 1714-1764 although kdeconnectd is not running" "$listening"
fi

# ======================================================================================================
head_ "4. Real connections from another interface (on, then off)"
NS_OK=0
if setup_ns; then
    NS_OK=1; pass "test namespace and veth created" "host $HOST_IF $HOST_IP, namespace $NS $NS_IP"
else
    skip "could not create the test namespace (needs ip netns and veth)" "connection tests 4a to 4d skipped"
fi
if [ $NS_OK = 1 ]; then
    if start_listener $IN_PORT && start_listener $OUT_PORT; then
        say "      temporary listeners on 0.0.0.0:$IN_PORT (inside the range) and :$OUT_PORT (outside)"
        # 4a: on
        helper on >/dev/null
        a=$(conn $IN_PORT); b=$(conn $OUT_PORT)
        [ "$a" = accepted ] && pass "4a on: connection to port $IN_PORT is accepted" "from $NS_IP to $HOST_IP:$IN_PORT -> $a" \
            || fail "4a on: connection to port $IN_PORT was not accepted" "result: $a"
        [ "$b" != accepted ] && pass "4b on: port $OUT_PORT (outside the range) is still refused" "result: $b" \
            || fail "4b on: port $OUT_PORT outside the range is reachable" "result: $b"
        # 4c: off
        helper off >/dev/null
        a=$(conn $IN_PORT)
        [ "$a" != accepted ] && pass "4c off: connection to port $IN_PORT is refused" "result: $a" \
            || fail "4c off: connection to port $IN_PORT is still accepted" "result: $a"
    else
        skip "could not start test listeners (ports $IN_PORT/$OUT_PORT busy, or no python3 or nc)" ""
    fi
fi

# 4d: with the real kdeconnectd
uid=""; user=""
while read -r sid suid sname _; do
    [ "$suid" -ge 1000 ] 2>/dev/null || continue
    if [ -S "/run/user/$suid/bus" ]; then uid=$suid; user=$sname; break; fi
done < <(loginctl list-sessions --no-legend 2>/dev/null)
if [ -z "$uid" ]; then
    skip "4d real kdeconnectd: no logged-in user session with a session bus" "log in graphically in the VM and run again"
elif [ ! -x /usr/bin/kdeconnectd ]; then
    skip "4d real kdeconnectd: /usr/bin/kdeconnectd not found" ""
else
    helper on >/dev/null
    pre=$(pgrep -x -u "$uid" kdeconnectd | head -1)
    if [ -z "$pre" ]; then
        runuser -u "$user" -- env XDG_RUNTIME_DIR="/run/user/$uid" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus" \
            /usr/bin/kdeconnectd >/dev/null 2>&1 &
        sleep 6
        STARTED_KDEC_PID=$(pgrep -x -u "$uid" kdeconnectd | head -1)
    fi
    if [ -z "$(pgrep -x -u "$uid" kdeconnectd | head -1)" ]; then
        fail "4d kdeconnectd did not start as $user" "try: systemctl --user status / run /usr/bin/kdeconnectd by hand"
    else
        udp=$(ss -lun 2>/dev/null | awk '{print $5}' | grep -E ':1716$')
        tcp=$(ss -ltn 2>/dev/null | awk '{print $4}' | grep -E ':17(1[4-9]|[2-5][0-9]|6[0-4])$')
        [ -n "$udp" ] && pass "4d UDP 1716 is listening" "$udp" || fail "4d UDP 1716 is not listening" "$(ss -lun)"
        [ -n "$tcp" ] && pass "4d TCP listener in 1714-1764" "$tcp" || fail "4d no TCP listener in 1714-1764" "$(ss -ltn)"
        port=$(echo "$tcp" | head -1 | sed 's/.*://')
        if [ $NS_OK = 1 ] && [ -n "$port" ]; then
            a=$(conn "$port")
            [ "$a" = accepted ] && pass "4d on: kdeconnectd TCP port $port reachable from the namespace" "result: $a" \
                || fail "4d on: kdeconnectd TCP port $port not reachable" "result: $a"
            helper off >/dev/null
            a=$(conn "$port")
            [ "$a" != accepted ] && pass "4d off: the same port is refused (daemon still running)" "result: $a" \
                || fail "4d off: kdeconnectd port still reachable after 'off'" "result: $a"
        fi
        # UDP 1716 is not tested: UDP has no refusal to observe without sending a real KDE Connect packet.
        info "UDP discovery packets were not tested from the namespace" "only the listening socket was checked"
    fi
fi

# ======================================================================================================
head_ "5. 'off'"
helper on >/dev/null
out=$(helper off); rc=$?
[ $rc = 0 ] && pass "'off' exits 0" "$out" || fail "'off' exit code is $rc" "$out"
[ ! -e "$STATE" ] && pass "state file is gone" "$STATE absent" || fail "state file still exists" "$(cat "$STATE")"
if ! rt_has && ! perm_has; then pass "rule gone from runtime and permanent" "$(fw --zone=$ZONE --list-services)"
else fail "rule still present" "runtime: $(fw --zone=$ZONE --list-services) | permanent: $(fw --permanent --zone=$ZONE --list-services)"; fi
say "      (the refused-connection check after 'off' is 4c and the last part of 4d)"

# ======================================================================================================
head_ "6. Idempotence"
o1=$(helper on); r1=$?; o2=$(helper on); r2=$?
if [ $r1 = 0 ] && [ $r2 = 0 ] && rt_has && perm_has; then pass "on twice: both exit 0, rule present" "exits $r1 $r2; services: $(fw --zone=$ZONE --list-services)"
else fail "on twice failed" "exits $r1 $r2; $o1 | $o2"; fi
n=$(firewall-cmd --zone=$ZONE --list-services | tr ' ' '\n' | grep -cx "$SVC")
[ "$n" = 1 ] && pass "service listed exactly once" "count=$n" || fail "service listed $n times" ""
o1=$(helper off); r1=$?; o2=$(helper off); r2=$?
if [ $r1 = 0 ] && [ $r2 = 0 ] && ! rt_has && ! perm_has && [ ! -e "$STATE" ]; then pass "off twice: both exit 0, rule and state gone" "exits $r1 $r2"
else fail "off twice failed" "exits $r1 $r2; $o1 | $o2"; fi

# ======================================================================================================
head_ "8. 'status' (run before the slow drift checks)"
helper on >/dev/null
out=$(helper status); rc=$?
[ "$out" = on ] && [ $rc = 0 ] && pass "status prints 'on', exit 0" "output: $out, exit $rc" || fail "status after on" "output: $out, exit $rc"
helper off >/dev/null
out=$(helper status); rc=$?
[ "$out" = off ] && [ $rc = 0 ] && pass "status prints 'off', exit 0" "output: $out, exit $rc" || fail "status after off" "output: $out, exit $rc"
firewall-cmd --zone=$ZONE --add-service=$SVC >/dev/null 2>&1
out=$(helper status 2>&1); rc=$?
if [ $rc = 3 ] && printf '%s\n' "$out" | grep -qx mismatch; then pass "state off + rule present: prints 'mismatch', exit 3" "output: $(echo "$out" | tr '\n' ' '), exit $rc"
else fail "expected 'mismatch' and exit 3" "output: $(echo "$out" | tr '\n' ' '), exit $rc (nizhod may have corrected it already)"; fi
firewall-cmd --zone=$ZONE --remove-service=$SVC >/dev/null 2>&1

# ======================================================================================================
head_ "7. Drift (needs the privacy daemon nizhod; waits up to ${POLL_MAX}s each)"
if ! systemctl is-active --quiet nizhod; then
    skip "nizhod is not running" "$(systemctl is-active nizhod 2>&1)"
else
    helper off >/dev/null
    t0=$(date +%s)
    firewall-cmd --zone=$ZONE --add-service=$SVC >/dev/null 2>&1
    say "      7a: flag off, service added by hand at $(date +%T); polling every ${POLL_STEP}s"
    w=$(poll_until no_rt); rc=$?
    logs=$(journalctl -u nizhod --since "@$t0" --no-pager -o cat 2>/dev/null | tail -15)
    if [ $rc = 0 ]; then pass "7a the rule was removed again after about ${w}s" "log lines since the change:" "${logs:-(none)}"
    else fail "7a the rule was still present after ${POLL_MAX}s" "services now: $(fw --zone=$ZONE --list-services)" "log lines since the change:" "${logs:-(none)}"
         firewall-cmd --zone=$ZONE --remove-service=$SVC >/dev/null 2>&1; fi

    helper on >/dev/null
    t0=$(date +%s)
    firewall-cmd --zone=$ZONE --remove-service=$SVC >/dev/null 2>&1
    say "      7b: flag on, runtime service removed by hand at $(date +%T); polling every ${POLL_STEP}s"
    w=$(poll_until has_rt); rc=$?
    logs=$(journalctl -u nizhod --since "@$t0" --no-pager -o cat 2>/dev/null | tail -15)
    st=$(helper status 2>&1 | tr '\n' ' ')
    if [ $rc = 0 ]; then pass "7b the rule was restored after about ${w}s" "helper status: $st" "log lines:" "${logs:-(none)}"
    else info "7b NOT restored within ${POLL_MAX}s (this is what actually happens)" \
         "helper status: $st" "nizhod log lines since the change:" "${logs:-(none)}" \
         "Decide whether that is acceptable: the user would see phone linking 'on' with the port closed."; fi
fi

# ======================================================================================================
head_ "Summary"
say "PASS $PASSN   FAIL $FAILN   SKIP $SKIPN   INFO $INFON"
restore
if [ $FAILN -gt 0 ]; then say "RESULT: FAIL"; exit 1; fi
say "RESULT: no failures (read the SKIP and INFO lines: they were not proven)"
exit 0
