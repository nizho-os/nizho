# Archon service security review

This review describes implemented controls and remaining validation gaps. It is
not a production security certification.

## Trust boundaries

The UI and CLI are unprivileged and untrusted. The system service trusts only
bus-authenticated identity, its protected records and administrator-provisioned
remote configuration. Catalogue text, XML, metadata, refs and downloaded artifacts
remain untrusted input. Flatpak performs native dependency resolution, GPG
verification and deployment. Nizho's separate image trust and rollback system
must not be inferred from a Flatpak transaction result.

## Controls implemented

- No arbitrary commands, argv, URLs, paths or caller-supplied plans over D-Bus.
- Ref/action/repository validation in both the Rust adapter and native bridge.
- Explicit polkit action, system-bus-name subject, interactive flag propagation,
  timeout cancellation and fail-closed missing/denied/malformed authority handling.
- Plans bind to UID and unique sender; only opaque server IDs are committed.
- Expiry, future-clock rejection, consumed-state checks, a global conflict gate,
  native re-resolution and comparison of operation/installed/trust state.
- Native dependency remotes must already be configured and signature-enforcing.
  New remotes and interactive authenticator substitution are rejected.
- Public repository output strips URL credentials, query strings and fragments.
- Private journal directories, strict file identities, exclusive temporary files,
  fsync and atomic rename. Restart marks uncertain work interrupted and never
  retries it automatically. Record ownership restricts history access.
- Python runs isolated with a fixed embedded program, cleared environment and
  no shell. Requests/events/metadata/output/runtime/concurrency are bounded.
- Root service filesystem restrictions, no capabilities, NoNewPrivileges,
  private tmp/devices, protected kernel/control groups/home and explicit writable
  state paths. The production Nizho release marker is required for commits.
- Debug-only isolated installation options are not exposed through D-Bus and
  cannot authorize system commits. Release builds reject those options.
- GTK displays plain text; native repository text does not become Pango markup.
  CLI search/progress removes non-printable terminal characters.
- Source RPM imports now require a valid trusted signature; no new signature
  bypass or privileged host package installation was used for development.

## Threats tested

Real signed disposable repositories exercised planning without installation,
application/runtime resolution, signed installation and update, stale native
state/replay rejection, expiry, cancellation, removal, unsigned artifacts and
signature-disabled remotes. A private bus exercised actual service catalogue,
details, plans, denied commits, cancellation, history and restart recovery.
Unit tests cover unsafe IDs/private-state paths, caller/expiry/consumed-state
checks and authorization denial/challenge/timeout/unavailable authority.

The real GTK contract test also exercises catalogue/details/confirmation,
category/search filtering, empty states, history and service loss through the
actual service and signed fixture. It does not authorize a root system commit.

Mock authorization authorities are confined to isolated unit tests. They are
not evidence of successful guest polkit authentication. Real authorization and
system Flatpak commits through the root service remain guest acceptance gates.

## Residual risks / unfinished work

- The production unit and sandbox need installed-image validation, including
  libflatpak/GPG subprocess behavior, polkit agent interaction and service recovery.
- Native transactions are not atomic across all applications/runtimes. Cancellation
  may occur after an earlier operation completed. History must not imply rollback.
- Public read/planning endpoints need stress/DoS tests and journal archival policy.
  Existing quotas fail closed, but are not a complete production retention system.
- Plan details and repositories include JSON inside a versioned D-Bus API. Final
  schema compatibility and granular backend error codes need release work.
- Catalogue metadata has limits but should receive fuzzing, large-repository and
  XML parser resource testing before production. Native icon integration is partial.
- Per-user installations remain in the legacy path. Caller-scoped migration or
  user-session brokering needs separate design and validation.
- No safe image-package build/sign/stage broker exists yet. System RPM changes
  remain unsupported. Production signing credentials must be provisioned through
  the release infrastructure, not supplied to the GTK application or guessed.
- Nizho boot/reboot/persistence/rollback and complete graphical/accessibility tests
  are required before calling the integration production-ready.
