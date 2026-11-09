# Future Considerations

These are observations and architectural considerations — a parking lot so ideas
aren't lost.

**Nothing in this document is committed to any release.** The scope of future
releases is determined independently, from real daily-driving experience (see
`docs/FIELD-NOTES.md`), not from this list. Presence here is not a promise.

> DP1 answered *design*. The next unknowns are *hardware* and *deployment*.
> The architecture is no longer the experiment — the hardware is.

The gaps below are **product and ecosystem maturity, not architectural flaws.**
That distinction matters: architectural flaws are expensive to fix later;
product maturity improves with testing, feedback, and time.

Where the uncertainty is: deployment and hardware. We have not measured it, and
the numeric readiness scores that used to be here had no test behind them.

---

## The dependency chain (why order matters)

The high-priority items are not peers — they form a chain, and everything assumes
the machine actually works:

```
Real hardware → drivers → power → suspend → installer → full-disk encryption
```

So the first unknown is not the installer. It is **hardware**.

## Hardware

The single biggest unknown. A VM answers none of this:

- CPUs/power: Intel + AMD desktop and laptop; AMD Ryzen power states.
- GPUs: NVIDIA (incl. suspend), AMD, Intel iGPU.
- Wireless: Intel AX and other WiFi chipsets; Bluetooth adapters.
- Input/peripherals: touchpad + gestures, fingerprint reader, webcam.
- Audio codecs; HDMI; USB-C docks; Thunderbolt.
- Displays: HiDPI, multi-monitor.
- Lifecycle: suspend/resume.

That first step has been taken once: Nizho booted from a USB stick on one real
laptop (see [HARDWARE.md](HARDWARE.md)). Every other item above is still untested.

## Compatibility

[HARDWARE.md](HARDWARE.md) is that page. It lists the one tested machine, what is
untested, and how to send a report with `nizho-hwreport`. Before 1.0 it needs more
machines. The list below is an example of the shape, not claims:

```
Validated hardware
  - Lenovo ThinkPad T14 Gen 4
  - Framework Laptop 13
  - ASUS Zephyrus G14
  - Dell XPS 13
  - Intel NUC
  - Ryzen desktop
Known issues
  …
```

This page becomes one of the most valuable things in the project.

## Security

- **Full-disk encryption — required before 1.0.** The installer ISO already
  encrypts the disk with LUKS2 and a passphrase by default (VM-tested, not on
  hardware; see [FEATURES.md](FEATURES.md)). What is still open is TPM-sealed
  unlocking, which needs real TPM hardware to prove, and a test on real hardware.
  A privacy-by-default OS where a stolen laptop's SSD reads clean needs this
  before 1.0.
- Secure Boot chain, production story:
  `firmware → Secure Boot → signed bootloader → signed kernel → measured boot → TPM`.
  Measured-boot work exists but PCR 11–15 are blocked on platform bits only
  shakeable on real hardware (see `PHASE3-FINDINGS.md`).
- Recovery: recovery mode, repair/safe mode — "desktop broken, how does the user
  recover?" Automatic rollback after a bad update and a by-hand `bootc rollback`
  exist (see [FEATURES.md](FEATURES.md)); a broken desktop on a healthy boot has
  no recovery path we have tested.
- Telemetry policy: formalize — no analytics; optional crash reports; a
  data-handling statement.

## Install

- Live ISO + thin installer (`download → flash USB → install → use`). An
  installer ISO now exists and was tested in a VM (see [FEATURES.md](FEATURES.md)).
  It is not published and not tested on real hardware, and an installer for
  unvalidated hardware can install a brick. DP1 had no installer by design
  (developer audience).

## Sandboxing / app isolation

- **Do not generalize the bespoke bubblewrap wrapper to all apps** — that is a
  "maintain-forever" trap that rebuilds what Flatpak portals already provide.
  The Firefox wrapper stays because it is part of the product identity; every
  other third-party app should ride Flatpak + portals, which the ecosystem
  maintains.

## Ecosystem

- Not an Nizho store. Capsule = curated Flathub: verified apps, update status,
  trust indicators — layered on Flathub's AppStream metadata, not a new registry.

## UX / product

- Update UX: the bootc mechanism exists; polish the messaging —
  `Update Ready · Restart Tonight / Restart Now / Remind Me Later`.
- User accounts: password change, multiple users, guest mode, optional parental
  controls.
- Settings state accuracy (e.g. the Automatic Login toggle reflecting the real
  SDDM state — see `docs/FIELD-NOTES.md`).
- Backup: a Settings page and `nizho-backup` now save your files to a drive you
  choose ([BACKUP.md](BACKUP.md)). Still open: tests with a real drive, a failure
  warning, progress, browsing saved files, offsite copies and a restore wizard.
- **Display adaptation (requirement, near-term):** *Nizho must adapt its desktop
  presentation to the detected display — never assume a fixed resolution or scale.*
  - **Detect** resolution, refresh, DPI/physical size from **EDID** — KDE/Wayland
    already does this; lean on it, don't reinvent.
  - **Auto-scale policy (the only Nizho-specific bit):** at first boot compute DPI
    and pick a sensible default (≈ ≤110 dpi→100 % · 110–160→125 % · 160–210→150 % ·
    >210→175–200 %), apply once via `kscreen-doctor`. KDE's own default is too
    conservative (100 % on HiDPI) — that's the gap we fill. ~30-line first-boot step.
  - **Remember per display:** free — KDE's `kscreen` daemon already stores
    scale/config keyed by each output's EDID (laptop 125 % vs external 4K 175 %).
  - **User override, Windows-style:** resolution / scale / refresh / multi-monitor
    arrangement all user-changeable and remembered — **KDE's Display & Monitor panel
    already provides this**; Nizho just surfaces it clearly in Settings.
  - **Why it matters:** the first bare-metal boot's "everything looks tiny" was
    almost certainly wrong global scale (100 % on a HiDPI panel), which cascades
    into SDDM / panel / text / maximize all looking wrong — **one root cause, not
    many bugs.** Diagnose the live machine from real EDID values, never hardcode a
    per-machine fix. See the display/UX pass in `docs/FIELD-NOTES.md`.

## Performance

- Measure continuously and track regressions over time: boot time, idle RAM,
  idle CPU, app launch latency.

## Accessibility

- Needs a proper audit: screen readers, keyboard navigation, large text,
  contrast, color blindness, focus visibility. Often overlooked; easier to build
  in than bolt on.

## Internationalization

- Setup now lets you pick a language and keyboard layout (not yet run in a booted
  image). Still open: how much of the desktop is translated into each language,
  RTL languages, Unicode coverage and regional formats. We have not checked these.

## Architecture — the long-term shape

The single most valuable non-feature investment, and the one place where
deferring is genuinely expensive later:

**Every Nizho app talks to Nizho *service interfaces*, never KDE/Plasma APIs
directly.** `WindowService` is the template. Each service is an Nizho interface
with a swappable implementation:

```
PowerService  → PowerDevil   (today)  →  Nizho Power   (tomorrow)   — no UI change
WindowService → KWin         (today)  →  NizhoWM       (tomorrow)   — no UI change
```

Candidate services: `WindowService` (exists), `PowerService`, `NetworkService`,
`NotificationService`, `PermissionService`, `PrivacyService`, `UpdateService`,
`ThemeService`, `ReliabilityService` (see
[`RELIABILITY-NIZHO-PULSE.md`](RELIABILITY-NIZHO-PULSE.md)). None of them name KDE. This is what keeps the "swap Plasma /
bring NizhoWM" door open without rewriting the desktop apps — so defining the
trait first (even when KDE is the only implementation) is the discipline worth
keeping during the freeze.

### Agentic Nizho — the AI layer, if it comes (distant)

An on-device assistant that can *operate* Nizho — but built on the same **"the
model is untrusted"** spine as nizho-pulse ([`RELIABILITY-NIZHO-PULSE.md`](RELIABILITY-NIZHO-PULSE.md)), not a
chatbot bolted on. The agent never touches the system directly; it emits
*structured intent* that flows through the very service interfaces and policy
gates every other actor already uses:

```
Nizho Agent
   → Structured Intent
   → Nizho Service Interface   (WindowService, PrivacyService, ReliabilityService, …)
   → Policy Engine            (RiskLevel: auto / ask-human / deny)
   → Auditable Action         (predefined registry only — never free-form)
   → Verify                   (re-check that it did what it claimed)
```

The point: this is **not** "add an AI assistant." It is the nizho-pulse safety
model generalised to the whole OS — the agent *proposes*, deterministic policy
*decides*, every action is auditable and re-verified, and nothing bypasses the
service layer. Local-only and privacy-first, same bar as `nizhod`. It is exactly
the service-interface + policy discipline above that makes this buildable later
without a rewrite — which is why the discipline is worth keeping now, even though
the agent itself is years out.

**Long-horizon direction (record, don't build):** Agentic Nizho · NizhoWM (own WM) ·
a *hardened* Linux LTS kernel — hardening/config first, custom work only if it
earns it, never a from-scratch kernel on a whim (see guardrails) · Mobile / tablet
/ (much later) car. Desktop-first for the next few years; everything else waits.

## Guardrails (the don't-do list)

- **Don't reimplement mature Linux infrastructure** — own package manager,
  network stack, filesystem, kernel — without a compelling reason. Nizho's value
  is the *experience*, not novel plumbing.
- **Don't try to be everything at once** — distro + DE + WM + package ecosystem +
  mobile + car. That list has killed better-funded projects. For the next few
  years: build the best desktop operating-system experience possible. Everything
  else can come later.
