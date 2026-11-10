#!/bin/bash
# Required greenboot check: the login screen service (sddm.service) runs.
# Before this check a new version whose login screen crashed passed, because
# greenboot's own checks only look at DNS and the watchdog.
#
# Skipped (pass) when the computer is not meant to show a login screen:
#   - the default target is not graphical.target. Nizho's first boot runs setup
#     on multi-user.target and switches to graphical.target when it is done.
#   - there is no graphics device under /sys/class/drm (a headless machine).
# What it cannot see: sddm running while the screen stays black. A graphics
# driver that does not load at all removes the device, so that case is
# skipped too. Both are misses, chosen over false alarms.
#
# 120 s: sddm starts after plymouth and logind, in parallel with this check,
# and greenboot-healthcheck.service already waits for network-online.target.
# greenboot runs *.sh files with "bash -C" and reads only the exit status.
set -u
helper=${NIZHO_GREENBOOT_HELPER:-/usr/libexec/nizho-greenboot-unit-check}
sys=${NIZHO_GREENBOOT_SYSFS:-/sys}
if [ ! -r "$helper" ]; then echo "login screen: $helper is missing, not checked" >&2; exit 0; fi
target=$(systemctl get-default 2>/dev/null)
if [ "$target" != graphical.target ]; then
    echo "login screen: default target is '${target:-unknown}', not checked"; exit 0
fi
found=0
for card in "$sys"/class/drm/card*; do
    [ -e "$card" ] && found=1 && break
done
if [ "$found" = 0 ]; then echo "login screen: no graphics device, not checked"; exit 0; fi
exec sh "$helper" sddm.service 120
