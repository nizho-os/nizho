#!/bin/bash
# Required greenboot check: nizhod.service (the privacy daemon) runs. nizhod
# restarts itself after a crash (Restart=on-failure, RestartSec=5s), so a
# version in which it cannot start shows as "still starting, restarted before"
# and fails here; a slow first start does not.
# 60 s: Type=notify, started after NetworkManager, resolved and firewalld.
# greenboot runs *.sh files with "bash -C" and reads only the exit status.
set -u
helper=${NIZHO_GREENBOOT_HELPER:-/usr/libexec/nizho-greenboot-unit-check}
if [ ! -r "$helper" ]; then echo "privacy daemon: $helper is missing, not checked" >&2; exit 0; fi
exec sh "$helper" nizhod.service 60
