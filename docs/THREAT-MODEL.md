# Nizho threat model

Reviewed 2026-10-04. These are implementation boundaries and limitations,
not a certification of the deployed image. See [release validation](RELEASE-VALIDATION.md).

## Privileged changes and local applications

nizhod runs with system privileges to maintain privacy configuration. Status is
readable over D-Bus. Manual `EnforceAll` checks the authenticated bus sender with
polkit before invoking an enforcer. The shipped policy requires administrator
authentication for active local users, denies ordinary inactive/remote sessions,
and does not retain authorization. Denials, service errors, malformed replies
and timeouts fail closed. Periodic maintenance does not require a prompt.
Administrator-supplied polkit rules can change authorization decisions.

Desktop and assistant clients allow a prompt for user-triggered repair. The
assistant's own confirmation is an additional UI step, not a privilege boundary.
An application running as the user can still read that user's files and access
services that grant that user permission.

First-run setup rejects existing accounts and invalid inputs, serializes account
creation, receives passwords on stdin, and revokes setup sudo access on completion.
User-owned preferences must be written with the new account's permissions;
root must not follow paths inside a writable user home. A later failure can leave
a partial account requiring administrator recovery. Retrying cannot reset that
account's password. Physical access during initial provisioning is trusted.

The KWin bridge treats journal-derived window IDs as untrusted: only UUIDs are
accepted for JavaScript interpolation. Scripts use fresh private directories and
files rather than predictable shared paths. This prevents another ordinary
account from preplacing or rewriting the script. Applications sharing the same
user account are not isolated from each other by this mechanism.

## Browser boundary

The Firefox wrapper constructs a bubblewrap filesystem with read-only system
files, limited `/etc`, temporary home directories and writable Downloads.
Downloads symlinks are rejected. By default it also binds a saved profile folder,
`~/.local/share/nizho/firefox-profile`, read and write at `~/.mozilla`, so
bookmarks, logins, history and extensions persist. It refuses a profile path that
is a symlink, not a folder, or owned by another user. `firefox --private-session`
binds no profile folder and nothing the browser stores outlives it. The inherited environment is cleared; only
locale, home, path and Wayland runtime variables are explicitly restored. API
keys and arbitrary launcher environment variables are not passed to the browser.
Environment-based proxy settings must instead be configured in the browser.

The wrapper creates PID/IPC/UTS namespaces, drops capabilities, uses a separate
session and terminates with its parent. It starts in the sandbox home. The host
network is shared. Wayland, PipeWire and PulseAudio access is deliberately exposed.
Direct audio-server access can include microphone recording; this is not a
playback-only or portal-mediated grant. Downloads and the saved profile are
readable and writable. Loopback services, such as the VARA Hub on
127.0.0.1:8737, are reachable from the browser. The host session D-Bus is not
bound; the saved profile may get a private bus with no services on it, used only
to pass a second launch to the running Firefox. There is no seccomp filter and no
separate user namespace from the wrapper.

The sandbox only applies to launches through `/usr/bin/firefox`.
`/usr/bin/firefox-unwrapped` stays executable by every user, so a program
running as the user can start Firefox without it.

This does not provide VM isolation or a complete defense against kernel,
compositor or audio-server exploits, malicious programs already running as the
same user, or network-accessible local services. Real Firefox startup, downloads,
audio, video and denied access still need guest tests. A shell smoke test alone
does not prove every browser path works.

## Network privacy

- **DNS:** checks strict DNS-over-TLS policy for the system resolver and its DNS
  scopes. It does not inspect every packet, prove reachability or govern every
  application's resolver. Destination IPs, other hostname disclosures and
  traffic patterns can remain visible.
- **MAC:** checks randomization defaults and changed locally administered
  addresses on active physical interfaces. Reconnection can be required.
  Accounts, cookies and other signals can still identify devices or users.
- **Hostname:** checks the configured static hostname, not every discovery
  protocol or application for personal information leaks.
- **IPv6:** checks temporary-address preferences on enabled interfaces. Actual
  address generation and use depend on network configuration and traffic.

- **Time:** chrony also asks four time servers by fixed address, over plain
  NTP, so a laptop with a dead clock battery can get the date right before DNS
  works. The addresses are 162.159.200.1 and 162.159.200.123 (Cloudflare) and
  216.239.35.0 and 216.239.35.4 (Google), set in the `Containerfile`. NTP is not
  encrypted or authenticated, so a hostile network can read these requests,
  answer them with a wrong time, or block them. A wrong clock can make
  certificate checks fail or pass when they should not. These servers also see
  your IP address. NTS (authenticated NTP) exists and Nizho does not use it.
  This setting is not one of the six that the dashboard checks.

Failed observations must not be shown as protection. Offline operation, captive
portals, VPNs, split DNS and reconnects need separate guest tests.

## Integrity, persistence and data at rest

The bootc design provides a read-only deployed image and staged updates.
Writable `/etc`, `/var` and user data remain possible persistence locations.
Rollback selects a previous system deployment; it does not restore personal
files or establish that either deployment is trustworthy. Repository object
checks do not establish release authenticity, Secure Boot or TPM coverage.

The current plain-root recipe does not provide disk encryption. Signing,
end-to-end image verification, verified boot, key management and recovery remain
release work. Immutability alone does not defend against theft of an unencrypted
disk, firmware attacks or a privileged attacker.

Build inputs trust Fedora, upstream tools and container registries. Rust
lockfiles do not pin every package or image. Release work must include reviewed
image digests, provenance, dependency advisories and upgrade/recovery tests.

The daemon and desktop lockfiles were updated from `event-listener 5.4.1` to
`5.4.2` for [RUSTSEC-2026-0221](https://rustsec.org/advisories/RUSTSEC-2026-0221.html).
The assistant and reliability lockfiles already used the patched version.
This was a targeted advisory review, not a complete dependency vulnerability scan.

## Known limitations

- **Setup account.** Before setup finishes, the `nizhosetup` user logs in on
  tty1 by itself. Its password is locked. A sudo rule lets it run two helpers as
  root without a password (`/usr/libexec/nizho-oobe-apply` and
  `/usr/libexec/nizho-firstboot`, `Containerfile`). The graphical setup is
  that user's session, so anyone at the keyboard before setup ends can create an
  account in the `wheel` group. Nizho trusts physical access during that
  time. `system/nizho-oobe-apply` deletes the sudo rule when it finishes
  (line 294). We have not checked that in a Nizho image.
- **nizhod runs as root.** `nizhod/nizhod.service` has no systemd sandboxing
  directives. This is a known limitation. Directives we have not tried and have
  not tested against the enforcers: `NoNewPrivileges=`, `ProtectHome=`,
  `PrivateTmp=`, `ProtectKernelModules=`, `ProtectControlGroups=` and a
  `CapabilityBoundingSet=` limited to what the enforcers need. Some may break
  an enforcer, so each needs a test.
- **Signed updates do not stop a downgrade.** The image policy checks that an
  image was signed with Nizho's key. That proves who made it. It does not prove
  that the image is the newest one, so a downgrade to an older signed image is
  not blocked.
- **Archon's polkit prompt.** The prompt for installing a package does not name
  the package. This is a known gap and is being fixed separately.

## Privacy records

Daemon events are local dashboard records, not a tamper-proof audit trail. The
assistant's hash chain detects inconsistent edits but can be recomputed by
someone controlling the log. Neither mechanism establishes the absence of
telemetry across installed software. Avoid secrets in logs and review actual
network behavior before making telemetry claims.

## Review references

- [polkit authorization API](https://polkit.pages.freedesktop.org/polkit/eggdbus-interface-org.freedesktop.PolicyKit1.Authority.html): authenticated bus-name subjects, explicit decisions and interactive authorization.
- [bubblewrap security model](https://github.com/containers/bubblewrap): the caller defines policy; exposed files and sockets determine the boundary.
- [KWin scripting API](https://develop.kde.org/docs/plasma/kwin/api/): window internal IDs are UUIDs and scripts execute through the compositor.
- [systemd execution restrictions](https://github.com/systemd/systemd/blob/main/man/systemd.exec.xml): further service restrictions need to preserve required interfaces and pass guest tests.
