# Plan: phone link and remote login only on home networks

Status: built on 2026-11-13; see "Answers" at the end for what was decided. Decided by the owner on 2026-11-13 (the "mixed model"): a feature
that needs an open port opens it only on networks the user marks as *home*, never on café Wi-Fi.

## What exists today

- Firewall zone `nizho` (`system/firewalld/nizho.xml`): target REJECT, only `dhcpv6-client`.
- Phone link: `nizho-phone-link on|off` (root, via pkexec) adds firewalld's `kdeconnect`
  service to zone `nizho`, on every network. nizhod (`nizhod/src/enforcers/firewall.rs`)
  re-checks every minute: `only_zone_active(…, "nizho")` and `restrictive_zone(text, phone_on)`.
- SSH server: off since 2026-11-13 (`10-nizho-no-sshd.preset`). No switch to turn it on yet.

## Design

1. **A second zone, `nizho-home`** (`/usr/lib/firewalld/zones/nizho-home.xml`): the same as `nizho`
   (REJECT, `dhcpv6-client`). Phone link and remote login add their service to this zone only.
2. **Marking a network as home.** Settings › Internet lists saved connections, each with a
   "This is my home network" switch. A root helper `nizho-home-network set <uuid> on|off` runs
   `nmcli connection modify <uuid> connection.zone nizho-home` (or `""` to go back to the default
   zone). pkexec with a new action, `auth_admin_keep`. Only NetworkManager connection UUIDs are accepted.
3. **Phone link.** Turning it on with no home network asks: "Phones can only find this computer on a
   home network. Mark <current Wi-Fi name> as home?" The `kdeconnect` service moves from `nizho`
   to `nizho-home`. Existing users with phone link on: nizhod moves the rule to `nizho-home` on
   its first check and marks no network home, so the link stays off on every network until they mark
   one. Settings › Internet then says so under the phone link switch. (Built this way instead of the
   confirm-and-notify step first planned.)
4. **Remote login** (Settings › Advanced, new): off by default. On = admin password, then
   `sshd` enabled with a drop-in `PasswordAuthentication no`, `KbdInteractiveAuthentication no`,
   `PermitRootLogin no`; the `ssh` service in zone `nizho-home` only. "Also allow passwords" is a
   second switch with its own warning. Optional "Turn off after 1 hour" (a transient timer).
   Keys: Settings shows `~/.ssh/authorized_keys` count and a "Add a key…" field.
5. **nizhod.** `only_zone_active` accepts `nizho` and `nizho-home`. `restrictive_zone` gets the
   allowed set per zone: `nizho` = {dhcpv6-client}; `nizho-home` = {dhcpv6-client} + kdeconnect
   iff phone on + ssh iff remote login on (flag file `/etc/nizho/remote-login`). Anything else in
   either zone is drift and is removed, as today.
6. **Honest status.** The pill and dashboard count a scoped exception separately:
   "6 of 6 settings in place · phone link on at home". Never "6 of 6" with a protection off.

## Warnings (one sentence each, shown before turning on)

- Phone link: "Devices on your home network can see this computer and ask to pair. Pair only phones you own."
- Remote login: "Anyone on your home network can try to sign in to this computer with a key you added."
- Passwords for remote login: "Passwords can be guessed by anyone on your home network; keys can't."

## Tests to write before building

- nizhod unit tests for both zones, every allowed/forbidden combination, and an interface left in a
  third zone (must stay reported, as today).
- VM: two NetworkManager connections (one home, one not), check `ss`/`firewall-cmd --get-active-zones`
  and a real KDE Connect pairing attempt from another VM on each.
- Rollback: a deployment rollback keeps `/etc/NetworkManager` zone choices (they live in /etc).

## Answers (owner, 2026-11-13)

- Outgoing discovery away from home: blocked. The firewalld policy `nizho-away-quiet` drops UDP 1716
  from this computer to networks in the zone `nizho`.
- Ethernet: asked like Wi-Fi; no connection is home until you mark it.
