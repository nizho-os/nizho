#!/bin/bash
# Wanted greenboot check (a failure is logged, never rolls back): at least one
# display connector reports "connected". A virtual machine or server without a
# screen fails this, which is why it is not a required check.
set -u
sys=${NIZHO_GREENBOOT_SYSFS:-/sys}
for status in "$sys"/class/drm/card*-*/status; do
    [ -r "$status" ] || continue
    read -r state < "$status" || continue
    if [ "$state" = connected ]; then echo "display connected: ${status%/status}"; exit 0; fi
done
echo "no connected display found under $sys/class/drm" >&2
exit 1
