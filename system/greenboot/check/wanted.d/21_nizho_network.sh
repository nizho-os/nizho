#!/bin/bash
# Wanted greenboot check (a failure is logged, never rolls back): NetworkManager.service,
# which manages network connections, is running.
set -u
state=$(systemctl is-active NetworkManager.service 2>/dev/null)
if [ "$state" = active ]; then echo "NetworkManager.service is active"; exit 0; fi
echo "NetworkManager.service is '${state:-unknown}'" >&2
exit 1
