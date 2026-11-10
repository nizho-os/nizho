# DP2 — post-DP1 work already landed on `main`

DP1 is frozen as a historical milestone (tag `v0.1.0-dp1`). This file records
the early work that landed on `main` after DP1. It is a record, not a plan. The
full list of what has been built since, with how each part was tested, is in
[FEATURES.md](FEATURES.md).

**DP2's actual scope is not decided here.** It is determined after a month of
daily driving, from real experience (`docs/FIELD-NOTES.md`). Candidate future
work — hardware, FDE, installer, the service-abstraction direction, and the rest
— lives in `docs/FUTURE-CONSIDERATIONS.md`, which is committed to nothing.

The theme of the work recorded here was *paying down Hyprland → Plasma debt*.
This file was written on ArkaOS builds, before the rename to Nizho on 2026-11-01.
The Hyprland shell crates it mentions were deleted on 2026-10-18 (commit
`4f695d0`) and there is no `legacy/` folder in the tree today.

## Carried over from DP1 (fixed on `main`, ships in DP2)

- **Lock Screen action** in Capsule/Bar used `swaylock` (Hyprland-era, not in
  the KDE image) → dead button. Now `loginctl lock-session`, which triggers
  KDE's kscreenlocker. The lock *screen* itself was always KDE and always
  worked; only the shell's shortcut to it was broken.
- **Terminal / file-manager actions** across Capsule, Update, and Settings used
  `foot` / `thunar` (Hyprland-era). Now `konsole` / `dolphin`, which the
  `@kde-desktop-environment` group actually installs.

These are source fixes and do **not** change the tagged DP1 image. Later image
builds from `main` contain them. We have not recorded a test of either action on a
booted image.

## Window-management abstraction (the headline of DP2)

Capsule's "Running" tab previously called `hyprctl` directly — a hard coupling
to a compositor Nizho no longer ships. That coupling is gone.

```
    Capsule ─▶ WindowService ─▶ KWin        (today)
    Capsule ─▶ WindowService ─▶ KWin/NizhoWM (tomorrow)
```

- `nizho-shell-common::window` defines a compositor-neutral `Window` type and a
  `WindowService` trait (`list` / `focus` / `close`). Apps speak only this.
- `window_service()` selects the backend at runtime. Today it returns the KWin
  backend; a future NizhoWM backend slots in here with no caller change.
- The KWin backend drives KWin's Scripting D-Bus interface via `dbus-send`
  (universally present, unlike the version-variable `qdbus`).

**Verification status:** the abstraction and the Capsule refactor compile and
are the correct architecture. The KWin backend's *runtime* behaviour —
especially `list()`, which reads window info back from the user journal — is
**to be verified live during the DP2 build cycle**. It is not yet claimed
working on a booted system. (Per project rule: compile success ≠ runtime
correctness.)

## Remaining Hyprland-era debt to clear

- **Vestigial shell crates: removed.** `nizho-bar`, `nizho-dock` and
  `nizho-launcher` were the custom Hyprland shell. Under Plasma they are replaced
  by the panel, Crystal Dock and the launcher widget, and they were never
  autostarted. They were first moved to a `legacy/` folder and then deleted in
  commit `4f695d0` (2026-10-18). The `legacy/` folder and its README no longer
  exist. The source is in git history up to `0d707ca`. They are not built or
  copied into the image.
- Audit any other `hyprctl` / `swaymsg` / `foot` / `thunar` / `wofi` residue
  and route real needs through the appropriate abstraction or KDE equivalent.
  A search of the Rust and shell sources on 2026-11-10 found no call to
  `hyprctl`, `swaymsg`, `swaylock` or `wofi`. One icon lookup in Capsule still
  matches the name `thunar`, and one comment mentions `hyprctl`.

## Capsule distribution model

Capsule is a **Flathub client, not a publisher.** It runs
`flatpak install flathub <app-id>` — pulling from Flathub's remote exactly like
KDE Discover or GNOME Software — and never hosts, redistributes, or re-signs
anyone's binaries. That role needs no per-developer agreement (an app store
does not need a vendor's sign-off to offer an install button for a public
Flathub app). Agreements/rights only become necessary if Nizho later (a) hosts
its own app repository and redistributes binaries, (b) bundles a proprietary app
into the image (that app's EULA applies), or (c) implies partnership/endorsement.

Two hygiene items: pull catalog metadata (names, icons, descriptions) from
Flathub's AppStream instead of hand-curating, so branding stays aligned and
self-updates; and let proprietary-app EULAs be accepted by the user at install
time, not by Nizho.

## Screenshot grid — historical DP1 capture

Real captures only — no mockups. The full DP1 set was captured live (2026-07-11)
and archived under `assets/screenshots/dp1/`: desktop, lock screen, wizard,
launcher, Privacy Dashboard, Privacy Report, Capsule, Settings, Wi-Fi. They show
the old name and logo, and the README no longer shows them.

---

Forward-looking candidate work (hardware, FDE, installer, service abstractions,
ecosystem, accessibility, …) is intentionally **not** here — it lives in
`docs/FUTURE-CONSIDERATIONS.md`, committed to nothing, so this file stays a
factual record of landed work rather than a speculative roadmap.

DP1 screenshots are outdated for the Fedora 44 candidate. Their privacy scores
and inferred DNS coverage are not current measurements; recapture after image
validation and UI work are complete.
