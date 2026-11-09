# Accessibility

This page says what Nizho does for accessibility, what the code audit of 2026-11-10 found, and what nobody has tested. It does not claim that Nizho meets any accessibility standard. Nobody has run a screen reader or a keyboard-only pass over the whole system for this audit.

## What Plasma, Qt and GTK already provide

These come from KDE, Qt and GTK. Nizho does not write them.

| Feature | Where it lives | In the Nizho image |
| --- | --- | --- |
| Orca screen reader | `orca` | Probably yes, see below |
| Speech engine | `speech-dispatcher`, `speech-dispatcher-espeak-ng`, `espeak-ng` | Probably yes, see below |
| Accessibility bus (AT-SPI) | `at-spi2-core`, `at-spi2-atk` | Probably yes, see below |
| Braille | `brltty` | Probably yes, see below |
| Sticky keys, slow keys, bounce keys | Plasma, System Settings, Accessibility | Part of Plasma; not tried by us |
| Large text | System Settings, Fonts, and display scaling | Part of Plasma; not tried by us with Nizho's own apps |
| High contrast | A high contrast colour scheme in System Settings | Not checked; Nizho sets its own colour scheme |
| On-screen keyboard | `plasma-keyboard` (Wayland virtual keyboard) | No, see the gaps |
| Reduce animations | System Settings, Animations (written by the Settings app as `AnimationDurationFactor=0`) | Yes, a switch in Nizho Settings |

### Is Orca installed? Unconfirmed

The Containerfile does not name `orca`, `speech-dispatcher` or `espeak-ng`. It installs the group `@kde-desktop-environment`. In the Fedora 44 package groups (the `comps-Everything` file of the release repository), that environment lists the group `desktop-accessibility`. That group lists `at-spi2-core`, `at-spi2-atk`, `brltty`, `orca` and `speech-dispatcher` as default packages. `speech-dispatcher` requires `speech-dispatcher-espeak-ng`, which brings `espeak-ng`.

So Orca and a speech engine are expected in the image. This was not confirmed against a built image. The launcher notes in `docs/NIZHO-SHELL-PLAN.md` record an Orca test in a VM on 2026-10-20, which fits. To confirm, run this on the booted image:

```
rpm -q orca speech-dispatcher speech-dispatcher-espeak-ng espeak-ng at-spi2-core brltty
```

If any of them says "not installed", that is the top gap. Add the packages in the table below to the Containerfile install line. This task did not change the Containerfile.

### Packages worth deciding on

Sizes and licences are from the Fedora 44 release repository metadata. "Installed" is the unpacked size of that one package, not counting dependencies already present.

| Package | Why | Installed size | Licence |
| --- | --- | --- | --- |
| `orca` 50.0.9 | Screen reader | 22.8 MB | LGPL-2.1-or-later AND CC-BY-SA-3.0 |
| `speech-dispatcher` 0.12.1 | Speech service Orca talks to | 29.3 MB | GPL-2.0-or-later AND LGPL-2.1-only OR LGPL-2.0-only |
| `speech-dispatcher-espeak-ng` 0.12.1 | Connects the service to eSpeak NG | 0.1 MB | same as speech-dispatcher |
| `espeak-ng` 1.52.0 | The voice | 25.3 MB | GPL-3.0-only AND GPL-3.0-or-later AND Apache-2.0 AND BSD-2-Clause AND Unicode-DFS-2016 AND CC-BY-SA-3.0 |
| `qt6-qtspeech` 6.10.2 | Lets Qt apps speak | 0.4 MB | GPL/LGPL Qt terms AND BSD-3-Clause |
| `qt6-qtspeech-speechd` 6.10.2 | Qt Speech backend for speech-dispatcher | 0.1 MB | same as qt6-qtspeech |
| `plasma-keyboard` 6.6.4 | On-screen keyboard for Wayland | 1.4 MB | several, including LGPL-2.1-only and GPL-3.0-or-later |
| `brltty` 6.8 | Braille display support | 8.4 MB | LGPL-2.0-or-later AND LGPL-2.1-or-later AND GPL-2.0-or-later |

The on-screen keyboard is not in the `kde-desktop` group, so it is a likely gap. It is also the first thing a person with a touchscreen or a switch device would need. `qt6-qtspeech` is only needed by apps that ask for it. None of these was tested in a Nizho image.

## What the audit looked at

The audit read the code of the GTK apps under `nizho-shell/`, the QML under `desktop/`, and computed contrast for literal colour pairs. It checked: names for icon-only controls, keyboard activation, status shown by colour alone, window titles and first focus, focus rings, fixed text sizes, and animations that ignore the reduce motion setting.

### Findings and status

| Where | Finding | Status |
| --- | --- | --- |
| nizho-wifi | Refresh button had an icon and no name | Fixed |
| nizho-wifi | Signal strength was shown as bar glyphs, which a screen reader reads as symbols | Fixed, now spoken as "Signal strength: good" and so on |
| nizho-bluetooth | Power switch and the "more" menu button had no name | Fixed |
| nizho-capsule | Favourite star and window close buttons had no name | Fixed; the star name changes between Add and Remove |
| nizho-capsule | Search result rows opened only with the mouse | Fixed, Enter and Space now open the result; untested on screen |
| nizho-sound | Volume slider had no name | Fixed |
| nizho-perms, nizho-bluetooth, nizho-wifi | Hard-coded dark-theme colours stayed in Light mode, some under 2:1 | Fixed, now theme colours; not looked at on screen |
| nizho-oobe | Text scramble effect and the pulsing outline ignored the GTK animation setting; a screen reader could read the random glyphs | Fixed; the setting itself is untested in the setup session |
| nizho-welcome | Shortcut rows highlighted on hover only | Fixed, also on keyboard focus |
| plasma-spaces | Desktop switcher was mouse only, no name, not focusable | Fixed with a name, Tab, Enter, Space and a thin focus outline; needs a look on screen |
| nizho-oobe | Setup entry fields show focus only by a darker bottom line (outline removed on purpose) | Listed, needs a visual check |
| nizho-settings and other libadwaita apps | No `outline: none` found in the shared theme or the app CSS, so Adwaita focus rings should remain | Listed as not checked on screen |
| plasma-brand | Name opens the launcher by tap only, no keyboard path | Listed. The launcher and the search button next to it are keyboard reachable |
| plasma-launcher, plasma-statuspill, plasma-search, SDDM theme | Names, roles and Enter handling are present | No change |
| plasma-launcher, SDDM theme | Fixed animation durations; the SDDM "press any key" prompt pulses forever | Listed. SDDM cannot read the user's reduce motion setting |
| Crystal Dock | Not audited: it is third-party C++ code with our patch | Listed |
| Status by colour alone | Dashboard, setup, Bluetooth, Wi-Fi all pair the coloured dot or label with text such as "Connected", "Configured", "Needs attention" | No change needed |
| Window titles | Every app sets a title | No change |

Not fixed because it needs a decision or a screen: the items marked "Listed".

### Contrast pairs under the thresholds

Computed with the WCAG relative luminance formula. 4.5:1 is the figure for normal text.

Fixed above, before the change (hard-coded colours): permissions app name `#d8e8f8` on white 1.25; permissions note `#f28b35` on white 2.46; Bluetooth connected `#4fc3f7` on white 2.00; Wi-Fi bars `#1a3050` on dark 1.27; Bluetooth paired `#4a6a88` on dark 2.98; permissions app id `#3a5a78` on dark 2.44; `#4ade80` on white 1.74.

Still below 4.5:1, not fixed:

| Pair | Ratio | Where |
| --- | --- | --- |
| Accent `#0e8a5f` on `#ffffff`, 12 px | 4.36 | Light palette, status text in Settings, badges |
| Muted text `#737373` on `#f6f7f9`, 10 px | 4.42 | Light palette, `label-xs` on raised panels |
| Warning `#b45309` on `#eceff3`, 12 px | 4.35 | SDDM theme, Light |
| Warning dot `#d97706` on `#f3f2ee` | 2.84 | Setup row dot; the row also has a text state, and a dot needs 3:1 as a graphic |

SDDM text sits over a wallpaper gradient, so the real ratio depends on the wallpaper. The numbers above are against the flat background.

### Fixed text sizes (listed, not changed)

Many CSS strings use `font-size` in px, for example: shared theme (10 to 30 px), Capsule (10 to 14 px), Bluetooth, Wi-Fi, Sound, Update, Welcome, Permissions (11 to 36 px), the privacy dots toast (12 px) and the setup (up to 64 px). In GTK, px sizes are not expected to follow the text scale factor the way pt or em do. Whether Nizho apps grow with System Settings, Fonts was not tested. Changing them is a design decision, so they are listed only.

### Reduce motion

The Settings app stores the switch as `AnimationDurationFactor=0` in `kdeglobals`. KWin and Plasma follow it. Our own widgets:

- Setup (`nizho-oobe`): page changes and, now, the text effect and outline check the GTK animation setting.
- GTK stack transitions in Capsule and the Dashboard use GTK's own setting.
- QML widgets and the SDDM theme use fixed millisecond durations and do not read the factor.
- The inactive `desktop/nizho-dock` QML dock bounces forever; the Containerfile ships Crystal Dock instead.

Whether the GTK setting follows `AnimationDurationFactor` inside a Plasma session was not tested.

## What is not tested

- No screen reader was run for this audit. Every accessible name above is a statement about code, not about what Orca says.
- No real users with disabilities took part.
- No keyboard-only pass was made on a screen. Tab order, focus visibility and Escape were read in code only.
- No high contrast scheme, large text setting or scaling was tried with Nizho's own apps.
- The fixes were built and unit tested only. Their look and behaviour on screen were not checked.
- Hardware was not used.

## Checklist for the owner, on a booted image with Orca

Start Orca with Super+Alt+S, or run `orca` from a terminal. Use only the keyboard.

1. In a terminal, run `rpm -q orca speech-dispatcher espeak-ng`. All three should print a version. Run `spd-say hello` and listen.
2. Log out and in again. At the login screen, does Orca read the username field, the password field, "Show password" and "Sign in"?
3. Launcher: Super, type a name, press Enter. Does Orca read the category and each result?
4. Settings, Backup: Tab through every row. Does Orca read each row title and subtitle? Press Enter on "Get a file back". Is the first file focused?
5. Settings, Appearance: does Orca say "Reduce motion" with its on or off state? Switch it and watch for animations stopping.
6. Settings, any page: press Escape. Does the window close, and does it stay open while you type in a text box?
7. Wi-Fi: Tab to the refresh button. Does Orca say "Refresh the network list"? Move through the list. Does each row say its signal strength, Secured or Open, and Connected where it applies?
8. Bluetooth: does the header switch say "Bluetooth" and its state? Does the menu button say "More actions for" and the device name?
9. Capsule: Tab to a star. Does it say "Add ... as a favourite"? Press Space, does the name change to Remove? Search for an app, arrow to a result, press Enter. Does it start?
10. Sound: Tab to the slider. Does Orca say "Volume" and the value? Do the arrow keys change it?
11. Privacy Dashboard: does each row say its state in words?
12. Top bar: Tab to the Spaces switcher with two desktops. Is each one named "Desktop 1, current" and so on? Is the focus outline visible?
13. Setup (use a fresh VM): can every page be finished with the keyboard alone? Is the focus line visible in the name and password fields?
14. Switch to Light mode. Open Permissions, Bluetooth and Wi-Fi. Is every line readable?
15. Raise the font size in System Settings, Fonts. Do Nizho apps grow? Note which do not.
16. Turn on sticky keys in System Settings, Accessibility. Press Shift five times, then press a shortcut such as Ctrl and C one key at a time. Does it work?
17. Install `plasma-keyboard` in a test image and check that it appears in a text field.

Write down every step where Orca says nothing, says "unlabelled", or reads symbols. Those are the next fixes.
