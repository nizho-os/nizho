# Changelog

## Unreleased (after DP1)

The full list, with how each part was tested, is in [docs/FEATURES.md](docs/FEATURES.md).

### Added
- Backup page in Settings and `nizho-backup`: encrypted backups of your files to a
  drive you choose. Tested on a development computer and, for the page, in a VM; not
  with a real drive.
- `nizho-import`: copies files in from a Windows or macOS drive. Tested with
  temporary folders only.
- `nizho-hwreport` and `docs/HARDWARE.md`: a hardware report for bug reports, and
  the list of the one tested laptop and what is untested.
- Phosphor tile icon set for the Nizho apps and common KDE apps. Firefox and Trash
  keep their own icons. A critic panel scored it about 6.7 against 6.5 for the old
  set, a small gain.
- Language and keyboard page in setup. Not yet run in a booted image.

- `docs/EVIDENCE.md`: one status, one test kind and one build family for every
  claim, the rule that ArkaOS results are never reported as Nizho results, and
  the current status of the ten main claims.
- `NOTICE.md`: third-party material, the fonts installed from Fedora packages,
  and the assets whose origin the repository does not record.
- Test log entry for the first checks on a published Nizho image (2026-11-09, in
  a VM only).

### Changed
- `docs/RELEASE-VALIDATION.md`, `docs/FIELD-NOTES.md` and `docs/DP2.md` now say
  that their older results come from ArkaOS builds. `docs/TESTING.md` no longer
  names a removed crate or a built-in login.
- Dependabot also watches `archon-service`.
- The Privacy dashboard no longer shows a privacy score. It lists each setting as
  Configured or not.

### Known Issues
- Everything above is untested on real hardware.

## v0.1.0-dp1 — Developer Preview 1 (2026-07-11)

### Added
- Immutable OS foundation: bootc image model, composefs read-only rootfs,
  atomic updates with rollback, TPM PCR 0–10 measurement.
- `nizhod` privacy daemon: MAC randomization, encrypted DNS (DoT/Quad9),
  hostname normalization, IPv6 privacy addresses; enforce-on-boot +
  re-verify every 60 s; privacy event log + D-Bus interface + privacy score.
- Firefox bubblewrap sandbox: tmpfs home, /etc allowlist, no D-Bus,
  Downloads-only passthrough.
- KDE Plasma desktop branded as Nizho: top panel + floating dock +
  full-screen launcher layout, dark + green design system throughout.
- Nizho Design System v1.0 (`desktop/nizho-design-system/`): tokens, typography,
  motion, components, privacy color mapping.
- Custom SDDM login theme, Plymouth boot animation (▲ → glow → NIZHO),
  Nizho icon pack (12 icons), NizhoCursor, Nizho sound theme, generated
  signature wallpaper, Inter/JetBrains Mono typography.
- First-party apps: Dashboard (privacy score, timeline, weekly report),
  Capsule (app store), Permissions, Settings, WiFi, Sound, Update, Welcome.
- Everyday features: power profiles, rich file previews, Bluetooth (bluedevil),
  KRunner search, Klipper clipboard.
- First-boot wizard (user creation, autologin choice) and first-login
  branding one-shot.
- Developer docs (`docs/`): architecture, building, testing. QA boot-loop
  harness (`scripts/qa-boot-loop.sh`).

### Fixed
- KDE package transaction failure on bootc base (`rootfiles` exclusion).
- Transient Fedora mirror failures during the 1500-package KDE layer
  (dnf retries=25 + fastestmirror).
- Small VM display (640×480) — first-login resolution bump via kscreen-doctor.
- GUI hangs in hardware-less VMs (all hw tool calls bounded by timeout).

### Known Issues
- GRUB is the active bootloader; measured UKI boot (PCRs 11–15) blocked until
  bootupd gains a systemd-boot component upstream.
- No per-file fs-verity pinning in composefs yet (structural immutability only).
- GTK apps (Adwaita) and Qt apps (Breeze) match in color/font but not in
  every widget metric — consistency audit in progress.
- Tested only in QEMU/KVM (virtio-vga); no bare-metal hardware matrix yet.
