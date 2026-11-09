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

- **Microsoft Office.** The Office desktop apps do not run on Linux. Macros (VBA) written for them
  do not carry over. What exists on Nizho:
  - LibreOffice opens and saves Word, Excel and PowerPoint formats. The image also installs
    Carlito (a stand-in for Calibri), Caladea (Cambria) and the Liberation fonts (Arial, Times New
    Roman, Courier New). These fonts are built to match the originals' letter widths, so lines and
    pages break in the same places more often. Microsoft's own fonts are not included.
  - ONLYOFFICE Desktop Editors is on the `nizho install` list. It is a separate program with its own
    document engine, so it may handle some .docx files differently from LibreOffice. We have not
    compared the two ourselves.
  - Microsoft 365 in the browser works. To send a file to someone who uses Word, choose Save As
    and pick .docx, .xlsx or .pptx in LibreOffice.
  - Complex documents can still look different, for example tracked changes, embedded objects and
    unusual fonts. Check an important file before you rely on it.
- **Adobe apps.** There is no Linux version of Photoshop, Illustrator, InDesign, Lightroom or
  Premiere. `nizho install` lists GIMP, Krita, Inkscape, Scribus, darktable and Kdenlive as stand-ins. They are
  different programs with their own file handling. Files from Adobe apps may not open, or may open
  with parts missing. Ask for an export (PDF, SVG, PNG, TIFF) from whoever made the file.
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

## What was checked for the Office fonts

- Fedora 44's package index lists google-carlito-fonts 1.103 (SIL OFL 1.1, 2.7 MB installed),
  google-crosextra-caladea-fonts 1.002 (Apache 2.0, 0.26 MB) and the three Liberation packages
  (2.1.5, SIL OFL 1.1, 1.2 to 1.7 MB each).
- In that index, libreoffice-core requires Caladea, Carlito and Liberation. If LibreOffice is
  in the image, they were probably already there. We did not boot the image to confirm which packages
  come with the KDE group, so the Containerfile now names them explicitly.
- Measured on a development machine, not in the Nizho image: a generated .docx with 12 Calibri and
  12 Cambria paragraphs, converted to PDF with LibreOffice 26.2.5. Without Carlito and Caladea it
  used Noto Sans and Noto Serif and came out at 12 pages. With them it used Carlito and Caladea
  and came out at 10 pages. The change shows the substitute fonts have different widths. We did
  not have real Calibri to compare against, so we did not measure how close 10 pages is to what Word
  produces.
- Not tested: real Word documents, spreadsheets, ONLYOFFICE's .docx output, and the new `nizho install`
  entries on a running Nizho.
