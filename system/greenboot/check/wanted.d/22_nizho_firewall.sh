#!/bin/bash
# Wanted greenboot check (a failure is logged, never rolls back): firewalld.service,
# which manages the firewall, is running.
set -u
state=$(systemctl is-active firewalld.service 2>/dev/null)
if [ "$state" = active ]; then echo "firewalld.service is active"; exit 0; fi
echo "firewalld.service is '${state:-unknown}'" >&2
exit 1
