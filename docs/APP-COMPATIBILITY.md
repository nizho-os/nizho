# Will my app run on Nizho?

Nizho is Fedora with KDE Plasma and a read-only system. That settles some questions and leaves
others open. This page says which is which. It describes what we know, not a promise that your
particular app works.

## What the read-only system changes

- Apps come from Flathub through Capsule or `nizho install`. Windows `.exe` files and `.deb`
  packages do not install.
- Nizho does not support adding other system packages. Use a Flatpak app, or ask us.
- A browser extension that talks to a password manager on the computer may not be able to reach it
  when the browser is a Flatpak. Nizho's own Firefox is not a Flatpak.

## Things people ask about

- **Microsoft Office.** The desktop apps do not run on Linux. Microsoft 365 in the browser does, and
  LibreOffice is included for opening and saving Word, Excel and PowerPoint files. Complex documents
  may look different, so check an important one before you rely on it.
- **Adobe apps.** There is no Linux version of Photoshop, Illustrator or Premiere. Some people use
  GIMP, Inkscape, Krita and Kdenlive instead. They are different programs, not drop-in copies.
- **Games.** Many Windows games run through Steam's Proton. Some games with kernel-level
  anti-cheat do not run at all, and this changes from game to game. Check ProtonDB or
  areweanticheatyet.com for the title before you move. Nizho has not been tested with games.
- **NVIDIA graphics.** Nizho uses the open driver. The proprietary NVIDIA driver is not included
  yet, so performance and power use on NVIDIA cards will be lower and CUDA is not available.
- **Printers, Wi-Fi, Bluetooth, sleep.** These depend on your hardware. Nizho has booted on one real
  laptop and has no list of tested machines yet. Try it from a USB stick before you install.
- **Video and audio formats.** Fedora's media libraries leave out some patented formats. If a file
  does not play, tell us which format it was.

If an app you need is not on this page, assume it is untested.
