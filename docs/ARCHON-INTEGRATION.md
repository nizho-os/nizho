# Archon integration and validation

Status: development implementation. Application transactions have real native
backend and private-bus validation. Production release acceptance is incomplete;
system RPM image transactions remain unsupported. Do not describe this as a
completed, VM-validated default package manager.

## Architecture and ownership

Archon remains an independent Rust project. Its `archon-distro::flatpak` module
owns Flatpak request validation and a narrow Python/GObject bridge to libflatpak.
The bridge is embedded in the Rust service. libflatpak owns its native catalogue,
repository trust, dependency resolution, runtime closure and installation state;
there is no second package database or custom dependency resolver.

Nizho's `archon-service` owns the system D-Bus endpoint, caller identity, polkit,
plan IDs, expiry, durable records, conflict exclusion and transaction signals.
Capsule is the unprivileged GTK4/libadwaita client. Its default Packages page has
Discover, Installed, Updates, Repositories, History and Settings. Existing Apps,
Running and Favorites views are preserved. Apps retains the previous per-user
Flatpak workflow; the new service manages the system Flatpak installation.
These scopes are displayed explicitly. Per-user package migration is not done.
The desktop launcher is named Archon Package Center.

On Nizho, the Archon CLI routes search, list, info, repositories, history,
capabilities, install, remove, plan and application upgrades through the same
system service. `archon plan` cancels its server plan after displaying it.
Administrator authorization is still required with `--yes`. Other immutable
operations return unsupported instead of falling through to DNF. Existing distro
backend implementations are unchanged. `archon update` remains the generic self-update alias, but the Nizho adapter
rejects self-updating the OS-managed executable. Archon itself updates with the
OS image; application upgrades use `archon upgrade`.

The original RPM inventory/image-status interface remains for compatibility.
Nizho's existing bootc status publisher supplies deployment metadata. Nizho keeps
ownership of image construction, signing, staging, boot health and rollback.
There is no live-root RPM mutation or ad-hoc bootc command in the new client.

## Independent, pinned source

Archon remains a separate repository at `https://github.com/nizho-os/archon.git`.
The Nizho guard, Flatpak adapter and CLI routing are maintained there as regular
Archon commits. Nizho builds the organization-owned tag
`nizho-integration-v1.0.1`, pinned to commit
`3626b93fda399cbda097016018a4170ee659225a`. The image build verifies that the
tag resolves to that exact commit before building with Cargo.lock; it does not
apply a downstream patch. The full Archon repository is not merged into Nizho.

Prepare a fresh checkout before building the service:

```sh
python3 scripts/prepare-archon-source.py
```

This creates an ignored link at `integrations/archon/source` to an external cache
named with the tag and exact commit. The image builder independently fetches the
tag from the Nizho organization, checks its identity, and builds with Cargo.lock.
The generated source link is excluded from the image context. No local sibling
checkout is copied into the image. The service's path dependency therefore works
across checkout layouts.

To update the integration, make and review normal commits in the independent
Archon repository, publish a new organization-owned tag after the commits are
accepted, then update `integrations/archon/source-tag` and
`integrations/archon/source-revision` together. Run
`python3 scripts/check-archon-source.py` before building. Regenerate the service
lockfile with Cargo if dependencies changed. Never edit the prepared cache as
development source.

The Flathub configuration and public signing key are stored as a checksummed
`.flatpakrepo` file. Service startup registers that system remote if absent;
it does not replace an administrator's existing remote or disabled trust policy.
Changing this configuration/key requires review and an updated checksum.

The builder base images use Fedora 44, Rust Alpine and Go Alpine tags; the
bootc base uses `quay.io/fedora/fedora-bootc:44`, matching the image's
`--pull=newer` update workflow. Weekly builds pull updated base layers and RPM
security fixes. RPM repository snapshots are not pinned, so this is not a
bit-for-bit reproducible release. Crystal Dock's source RPM is pinned
to `2.16.0-1.fc44` and must pass a trusted signature check before import. The
previous `--nosignature` import was removed. Source metadata downloads disable
zchunk optimization and retain signature checks.

## Build and source tests

From Nizho's repository root:

```sh
python3 scripts/prepare-archon-source.py
cargo test --locked --manifest-path archon-service/Cargo.toml
cargo clippy --locked --manifest-path archon-service/Cargo.toml --all-targets -- -D warnings
cargo check --locked --manifest-path nizho-shell/Cargo.toml -p nizho-capsule
cargo test --locked --manifest-path nizho-shell/Cargo.toml -p nizho-capsule
scripts/test-archon-dbus.sh
```

From an Archon checkout containing the integration changes:

```sh
cargo build --offline --locked --release -p archon-cli --bin archon
cargo test --offline --locked --workspace -- --test-threads=2
python3 scripts/test-flatpak-native.py
```

A complete source/native/UI suite is available as:

```sh
XVFB=/absolute/path/to/Xvfb scripts/test-archon-integration.sh
# Focused real GTK/service contract:
XVFB=/absolute/path/to/Xvfb scripts/test-archon-native-ui.sh
```

It exits unsuccessfully if UI prerequisites are absent. It is not the VM
acceptance suite. The new source preparation can cause a first build to download
locked Cargo dependencies. Tests never invoke privileged host package operations.

## D-Bus API

See [the API contract](ARCHON-DBUS.md) for methods, signatures and errors.
The versioned service is `org.nizho.Archon1`, object `/org/nizho/Archon1`, manager
interface `org.nizho.Archon1.Manager`. Plan/transaction records are typed D-Bus
structures. Details, repositories and resolved native plan contents currently
use JSON strings; clients validate their schema and display plain text.

Plans last five minutes and are bound to the UID and unique bus name obtained
from the bus header, not client arguments. Every commit consumes a pending plan
before starting asynchronous work. A global gate excludes conflicting plans and
transactions. A commit re-resolves through libflatpak's `ready` callback and
compares the entire resolved operation set, native installed state, remote
configuration and trusted key material with the stored plan. Expiry is checked
again after authorization and before native application. Added remotes and
interactive remote/authenticator substitution are not accepted.

The service writes private, fsynced records by exclusive temporary-file creation
and atomic rename before work starts and after completion. Startup marks pending
and running transactions interrupted; it never auto-replays them. Native Flatpak
operations are not a multi-application atomic rollback system: cancellation or
failure can leave earlier operations completed. Inspect native inventory/history
and create a new plan after interruption. Completed state is emitted only after
native success and journal persistence. Journal failures remain errors.

## Security

See [the security review](ARCHON-SECURITY.md). The service is root because it
brokers system Flatpak transactions. Read-only operations do not prompt. Commits
use `org.nizho.archon.manage` with polkit's authenticated system-bus-name subject
and the caller's interactive-authorization flag. Challenges and unavailable or
malformed authority responses fail closed. Authorization decisions are logged.
The UI and CLI contain no sudo or direct privileged package command.

The unit grants write access only to `/var/lib/flatpak` and its private journal,
with ProtectSystem, ProtectHome, private temporary storage/devices, no capabilities
and NoNewPrivileges. The Nizho release marker is required for production commits.
Debug session mode cannot commit, even when a disposable installation is supplied.
Debug-only installation/state arguments are not D-Bus inputs and are rejected by
release builds. No production policy is installed on the development host.

## Validated so far

The full locked Archon workspace suite passed on this host, along with the
service's 16 tests, strict service Clippy, Capsule compilation and its 10 existing
unit tests. A separate ignored-by-default GTK contract test is explicitly run
by scripts/test-archon-native-ui.sh against the real service and signed fixture. Those Capsule unit tests use stand-ins for legacy terminal commands;
they do not certify real package installation.

`scripts/test-archon-dbus.sh` passed real service startup/introspection, native
system Flatpak inventory/repository reads, history, bounds and malformed requests.
A separate signed disposable repository tested the actual service's catalogue,
details, runtime dependency plan, denied commit, cancellation, persisted history,
restart recovery and replay rejection. This test first exposed and then verified
a fix for incorrectly flattened D-Bus plan/status structure replies.

The native backend test created a real temporary signing key and real Flatpak app
and runtime, then verified discovery, details, no-install planning, expiry,
cancellation, signed install, signed update, stale-plan rejection, removal,
unsigned-artifact rejection and rejection of disabled signature policy. Everything
happened in a disposable installation, without changing the host Flatpak database.
The tiny fixture validates deployment, not execution of a desktop application.

Headless Xvfb smoke tests launched Capsule with and without the actual service,
kept it alive and captured screenshots. The initial layout was visually inspected.
A real-widget GTK contract test additionally passed search, categories, details,
dependency-plan confirmation/cancellation, empty Installed/Updates, repositories,
history, Settings and service loss. Screenshots of the catalogue/details/review
were inspected. Full keyboard/accessibility, authorized commit/progress UI and
VM graphical acceptance remain unverified. The debug
session-bus override is compiled out of release behavior.

Latest small-fixture host measurements (debug build, not VM/release benchmarks):

| Measurement | Observed |
| --- | --- |
| Service startup | 27.36 ms |
| Catalogue/search, small signed fixture | 267.50 ms |
| Native transaction planning | 152.34 ms |
| GetCapabilities median, 30 GIO calls | 1.18 ms |
| Service RSS after these calls | 9,964 KiB |

Production catalogue scale, UI startup/idle memory and VM timings still need
measurement. The GUI creates at most 50 discovery rows per page. At most four
native bridge processes run concurrently; protocol events, metadata, runtime and
output have explicit bounds. Transaction progress is signalled rather than polled.

## Image and VM validation

See [the VM instructions](ARCHON-VM.md). Image build logs are retained under
`output/archon-validation/`. A pinned Fedora CoreOS builder VM was initialized and
booted with QEMU/KVM, 2 CPUs, 4 GiB RAM and an 80 GiB disposable disk. It reports
x86_64, Podman 6.1.4 and healthy systemd state. Only the validation-output directory
is shared. This is a build VM, not a booted Nizho acceptance result.

Image attempts and exact final status must be read from the current validation
logs. Initial failures included missing Podman, a cached-tool refresh conflict,
Fedora source metadata download errors, and a service source-path transition
between builds. No failed attempt is counted as a successful image build.

An earlier image attempt referenced a bootc digest that was unavailable; it is
replaced with the maintained Fedora 44 tag. Image builds now run on pull requests
and on the existing weekly scheduled workflow. The first complete image
succeeded on 2026-11-06:
`ec904159419d35e7fa85dbf5440500d81372b75ae95a1064e2330627ee1b5c24`.
`build-20261106T073801Z.log` records `Successfully tagged
localhost/nizho:archon-test`; bootc lint reported 10 passed checks, 1 skipped and
4 warnings. A read-only container check confirmed Archon 0.2.1, native Flatpak GI,
and successful `systemd-analyze verify` for the installed Archon unit. This is an
intermediate source snapshot; the latest owner-trust and build fixes require a
new image and guest validation before release.

The selected [machine-owner trust model](ARCHON-OWNER-TRUST.md) now has explicit
provisioning code and a scoped local registry unit. Native image verification and
installed-unit validation must pass before it becomes an advertised system
package capability.

Nizho boot, actual guest polkit authorization, graphical acceptance, guest package
operations, reboot persistence and rollback remain acceptance gates. System RPM
package planning/build/sign/stage/activation is unsupported: no configured,
validated image-package builder or signing/deployment broker exists. Deployment
signals for such operations are not fabricated. No complete rollback test command
is supplied for an unimplemented backend. Existing Nizho rollback/release
procedures remain unchanged.

## Limitations and troubleshooting

- The new backend manages system Flatpaks; legacy per-user applications are not
  merged into that catalogue. Settings explains the split.
- Private or interactive-authenticator remotes are unsupported. Repositories with
  disabled signature verification are excluded/rejected. Repository mutation is
  an administrator operation, not an arbitrary D-Bus URL/configuration method.
- Missing optional AppStream information remains unavailable. Package identity
  and commits come from native refs. Missing metadata/errors are not demo records.
- Details/repository JSON is a development ABI boundary; no final stable-release
  compatibility guarantee has been made.
- Plans and history have bounded request quotas. Journal retention/archival and
  large-catalogue stress testing need release work.
- An interrupted Flatpak operation requires native-state inspection. Restart does
  not certify rollback or boot success.
- Existing unrelated GTK CSS deprecation warnings appeared in Fedora builds.
  They were reported and not silently treated as strict Clippy passes.

In an installed image:

```sh
systemctl status archon-service
journalctl -b -u archon-service
busctl introspect org.nizho.Archon1 /org/nizho/Archon1
archon capabilities
archon search Calculator
nizho-capsule
```

If service startup fails, inspect repository setup, journal ownership/mode,
Python GObject/libflatpak runtime dependencies and the Nizho release marker.
Missing/stale bootc publisher data is shown as unavailable, not “up to date”.
No key, signature or authorization bypass is a supported troubleshooting step.
