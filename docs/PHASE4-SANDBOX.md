# Phase 4 — Default-on Browser Sandboxing

## Model

Firefox is sandboxed via bubblewrap (bwrap) by default. The wrapper is
`/usr/bin/firefox` in the deployed image, so the menu, the dock and a plain
`firefox` command all start the sandboxed browser.

There is still an unsandboxed launch path. `/usr/bin/firefox-unwrapped` (Fedora's
own launcher script) and `/usr/lib64/firefox/firefox` stay executable by every
user. Any program running as you can start Firefox outside the sandbox, and so
can you. The sandbox covers launches that go through `/usr/bin/firefox`.

**Why bwrap over Flatpak:** Flatpak + Firefox runtime adds ~1GB to an already 2GB
image and requires network access at build time or first boot. bwrap is ~100KB,
available in CentOS Stream 10 BaseOS, and the sandbox profile is a shell script
that is version-controlled, auditable, and baked into the image layer. Flatpak is
the right answer for a desktop with a package manager; bwrap fits an immutable
bootc image where every artifact is baked in.

## Interception mechanism

`/usr/bin/firefox` → symlink → `/usr/bin/firefox-sandbox` (the bwrap wrapper)

The real Firefox ELF is at `/usr/bin/firefox-unwrapped`. The rename was done at
image build time (`mv /usr/bin/firefox /usr/bin/firefox-unwrapped`). Because the
root filesystem is a composefs overlay (`ro` mount), the symlink cannot be changed
at runtime. That keeps `/usr/bin/firefox` pointing at the wrapper. It does not stop
anyone from running `/usr/bin/firefox-unwrapped` directly.

Desktop `.desktop` launchers that call `/usr/bin/firefox` (absolute path) land on
the sandbox wrapper. PATH-based invocations also resolve to the wrapper.

## Sandbox profile (`/usr/bin/firefox-sandbox`)

```
Filesystem binds (all read-only unless noted):
  /usr /lib /lib64 /bin /sbin    — system binaries and libraries
  /etc (tmpfs + allowlist only)  — see below
  /dev (dev)                     — device nodes
  /proc (proc)                   — process info
  /tmp (tmpfs)                   — ephemeral scratch
  /run (tmpfs)                   — host session and system D-Bus not bound
  /run/user/UID/wayland-0 (bind) — the Wayland display socket
  /run/user/UID/pipewire-0 (bind), /run/user/UID/pulse/native (bind)
                                 — audio sockets, microphone included
  /home (tmpfs)                  — real home HIDDEN
  /root (tmpfs)                  — real root home HIDDEN
  ~/Downloads (bind rw)          — your Downloads folder
  ~/.mozilla (bind rw)           — the saved profile folder (see below);
                                   not bound with --private-session

/etc allowlist (everything else is hidden):
  resolv.conf, hosts, nsswitch.conf — name resolution
  ssl/, pki/                        — CA certificates for HTTPS
  fonts/                            — font rendering
  localtime                         — timezone
  ld.so.cache, ld.so.conf.d        — dynamic linker
  alternatives/                     — RHEL alternatives symlinks

Hidden from /etc (confirmed absent inside sandbox):
  NetworkManager/     — WiFi credentials not visible to browser
  nizhod/              — privacy daemon config not visible
  machine-id          — unique host identifier not exposed
  shadow              — not needed by browser
  passwd              — the host file is hidden. When the private bus is on
                        (see below) the sandbox gets a one-line made-up
                        /etc/passwd with your user id, group id and home path,
                        because dbus-daemon will not start without it.

Namespace isolation:
  --unshare-pid       — isolated PID namespace
  --unshare-ipc       — isolated IPC
  --unshare-uts       — isolated hostname
  --new-session       — new session ID (no terminal escape)
  --die-with-parent   — sandbox dies when wrapper exits
  --cap-drop ALL, --clearenv

Not restricted:
  network             — host network namespace. Services that listen on this
                        computer's loopback address are reachable, for example
                        the VARA Hub at 127.0.0.1:8737.
  user namespace      — no --unshare-user; the browser runs as your user id
  seccomp             — the wrapper adds no seccomp filter
  audio               — direct PipeWire and PulseAudio access, which can
                        record the microphone. This is not a portal grant.

nizhod's DNS-over-TLS enforcer (Quad9) and IPv6 privacy extensions apply to the
system resolver and the kernel. Firefox's own DNS over HTTPS is turned off and
locked by policy, so its lookups go to the system resolver. We have not checked
whether Firefox's own content process sandbox works inside this one.
```

## Saved profile and private sessions

By default Firefox keeps its profile. The wrapper binds
`~/.local/share/nizho/firefox-profile` read and write at `~/.mozilla` inside the
sandbox, so bookmarks, logins, history and extensions stay after Firefox closes.
The wrapper creates the folder with mode 0700 the first time. It refuses to start
if that path is a symlink, is not a folder, resolves somewhere else, or belongs
to another user. `MOZ_LEGACY_HOME=1` keeps Firefox 147 and later on `~/.mozilla`.

`firefox --private-session` binds no profile folder. `~/.mozilla` is then on the
`/home` tmpfs, and everything Firefox saved is gone when it exits. Downloads are
still written to `~/Downloads`. The Firefox launcher has an action for this, "New
private session, nothing saved". Nizho ships its own copy of Fedora's
`org.mozilla.firefox.desktop` with that action added.

Firefox allows one running copy per profile. On Wayland a second launch finds the
first through the session D-Bus. The host session bus stays hidden, so when
`/usr/bin/dbus-daemon` is installed the first saved-profile launch starts a
private bus inside its sandbox, with its socket at `~/.mozilla/nizho-bus` and no
services on it. Later launches use that socket to hand their request to the
running Firefox. A lock file at `~/.local/share/nizho/firefox-profile.lock`
decides which launch starts the bus. If the bus is missing (no dbus-daemon, or a
home path with unusual characters), a second launch gets Firefox's own message
that it is already running. Firefox's profile lock is never bypassed. The
Containerfile does not install dbus-daemon itself. Whether another package pulls
it in has not been checked on a built image.

## Test mode

```bash
firefox --shell          # interactive bash in the identical sandbox
firefox --shell -c 'cmd' # non-interactive command in the identical sandbox
```

The `--shell` mode uses the exact same `BWRAP_ARGS` array as the browser launch.
It exists for isolation testing and is not a privilege-escalation vector (the
sandbox is equally restrictive for bash as for Firefox).

## Isolation proof (verified in VM, 2026-06-08)

This proof predates the Wayland and audio binds and the saved profile folder.
It has not been repeated since.

Setup:
```bash
echo "topsecret_1780909961" > ~/secret.txt   # written to real /var/home/ram/
```

Inside sandbox (`firefox --shell -c '...'`):
```
=== home ls ===
total 0
drwxr-xr-x. 2 1000 1000 40 Jun 8 09:12 .
drwxr-xr-x. 15 1000 1000 300 Jun 8 09:12 ..

=== secret read ===
cat: /var/home/ram/secret.txt: No such file or directory

=== home mountinfo ===
656 574 0:69 / /home rw,... - tmpfs tmpfs rw,...
658 574 252:4 /ostree/.../ram/Downloads /var/home/ram/Downloads rw,... - xfs ...

=== etc contents ===
alternatives fonts hosts ld.so.cache ld.so.conf.d nsswitch.conf pki resolv.conf ssl

=== NM in etc ===
ls: cannot access '/etc/NetworkManager': No such file or directory

=== nizhod in etc ===
ls: cannot access '/etc/nizhod': No such file or directory
```

After sandbox exit: `~/secret.txt` unchanged in real home. Sandbox writes went to
tmpfs and were discarded.

**The proof uses `firefox --shell`, not a hand-reconstructed bwrap invocation.**
The sandbox args are a single array in the wrapper; `--shell` reuses them directly.
There is no gap between what was tested and what ships.

## Regression checks (same boot, verified)

- `systemctl is-active nizhod` → `active` (Phase 2 privacy enforcers intact)
- `mount | grep composefs` → `ro` overlay (Phase 3 composefs rootfs intact)

## What this demonstrates vs. what it is not

**Demonstrates:**
- A compromised browser process cannot read files from the user's home directory,
  except `~/Downloads` and its own profile folder
- WiFi credentials (NM), daemon config (nizhod), and host identity (machine-id) are
  not visible to the browser

**Not yet implemented (known scope boundaries):**
- Encryption of the saved profile beyond what the disk itself has
- Blocking the unsandboxed launch path (`/usr/bin/firefox-unwrapped`)
- Wayland socket isolation (the compositor socket is bound as is)
- Portals (no host D-Bus, so no portal file chooser or screen sharing)
- Microphone control (the audio sockets allow recording)
- A seccomp filter or a separate network namespace
- Per-app sandboxing beyond Firefox (Phase 4 scopes to one reference app)
- TPM-sealed browser profile (requires PCRs 11-15, which are blocked by Phase 3
  platform limitation — see PHASE3-FINDINGS.md)
