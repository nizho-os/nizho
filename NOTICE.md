# Notices

Nizho's own source is licensed under GPL-3.0 (see [LICENSE](LICENSE)). This file lists the
third-party and bundled material and says what the repository can prove about each. Where the
repository holds no record of where something came from, this file says so and does not guess.

## Third-party material in the repository

| Material | Where | Licence | What the repository shows |
|---|---|---|---|
| Crystal Dock | `desktop/crystal-dock/` | GPL-3.0-only, as stated in Fedora's `crystal-dock` package | The repository holds two patches and settings, and no copy of the dock's source. The image build rebuilds Fedora's source package with those patches. See [desktop/crystal-dock/README.md](desktop/crystal-dock/README.md). |

## Installed from Fedora packages, not bundled

The image build installs these with `dnf`. No font file is stored in this repository. The licence
column is the licence field of each Fedora package, read from the package sources on
src.fedoraproject.org (rawhide branch) on 2026-11-10. The version a given image installs can
differ, and its own package metadata is the authority.

| Font | Fedora package | Licence field |
|---|---|---|
| Inter | `rsms-inter-fonts` | OFL-1.1 |
| JetBrains Mono | `jetbrains-mono-fonts` | OFL-1.1 |
| Liberation Sans, Serif and Mono | `liberation-sans-fonts`, `liberation-serif-fonts`, `liberation-mono-fonts` (source package `liberation-fonts`) | OFL-1.1-RFN |
| Carlito | `google-carlito-fonts` | OFL-1.1 |
| Caladea | `google-crosextra-caladea-fonts` | Apache-2.0 |

The image also contains the rest of the Fedora and KDE packages it installs. Each keeps its own
licence, recorded in its package.

## Made by the build

These are generated when the image is built, so the repository holds the recipe and no asset file.

| Asset | How it is made |
|---|---|
| Nizho sound theme | Synthesised with `sox` from sine tones and noise in the [Containerfile](Containerfile). No recording is used. |
| Default wallpapers | Drawn by [desktop/wallpapers/make-default.sh](desktop/wallpapers/make-default.sh) during the build. |

## Mixed licence metadata

The repository is GPL-3.0, and most Plasma component metadata files say `GPL-3.0-or-later`.
Two component metadata files say MIT:

- `desktop/sddm-theme-nizho/metadata.desktop` (`License=MIT`)
- `desktop/kwin-scripts/nizho-showdesktop/metadata.json` (`"License": "MIT"`)

The repository does not record whether that is intended. Until the owner decides, both
statements stand as written in those files.

## Origin not recorded

For each item below, the origin is not recorded in the repository: no file names its author,
source or licence. These are open items for the owner.

| Asset | Where | Open question |
|---|---|---|
| Tour video | `website/media/nizho-tour.mp4` | Origin not recorded in the repository. Who made it, and from which footage. |
| Music and sound in the tour video | inside `website/media/nizho-tour.mp4` | Origin not recorded in the repository. Its source and licence. |
| DP1 screenshots | `assets/screenshots/dp1/` | Origin not recorded in the repository beyond [docs/DP2.md](docs/DP2.md), which says they were captured live on 2026-07-11. They are ArkaOS-era captures. |
| Wordmarks and marks | `assets/branding/nizho-wordmark*.svg`, `assets/branding/nizho-mark*.png` | Origin not recorded in the repository. The commit history says they were drawn for Nizho, and no file states the author or the tools. |
| Logo images used by the boot splash and login screen | `desktop/plymouth-theme-nizho/logo-src.png`, `desktop/sddm-theme-nizho/logo-*.png` | Origin not recorded in the repository. |
| Website and README images | `website/img/`, `assets/branding/*.svg`, `assets/bar.gif` | Origin not recorded in the repository. |

No licence is claimed here for anything in this last table.
