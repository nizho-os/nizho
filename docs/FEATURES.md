# What has been built since DP1, and how each part was tested

This page was moved out of the README. Most "Tested in a VM" lines record ArkaOS builds, made before the project
was renamed Nizho. The first Nizho image was built and booted in a VM on 2026-11-01; what that run checked is in
[TEST-LOG.md](TEST-LOG.md). Entries added later say what they were tested with.


This is source work plus the test results we wrote down along the way. The
rollback and installer results (2026-10-11 and 2026-10-12) are written up in
[Release Validation](RELEASE-VALIDATION.md). The other dated results come
from our own test sessions and are not all recorded in a repo document. Everything
here was tested in a VM between 2026-10-11 and 2026-10-26 unless it says otherwise. "Not yet on hardware" means
exactly that: we haven't tried it.

### Real hardware

- **First boot on a real laptop.** An Intel i5-10210U laptop booted Nizho from
  a USB stick, with the internal disk untouched: GRUB, then the KDE Plasma
  desktop. Wi-Fi, touchpad, display, brightness and suspend/resume worked, and
  `nizho-pulse` read a real temperature sensor. That is one machine, so treat it
  as a first data point.
- **Fixes from that test.** The disk image no longer turns on a serial console,
  which was crash-looping the login there. `scripts/flash-usb.sh` writes and
  verifies USB sticks and repairs the GPT. Browser audio socket access and
  Capsule's user-level Flathub setup were fixed in the source. None of these is
  confirmed on the laptop yet. A Capsule install from Flathub did complete in a
  VM.
- **nizho-pulse.** A read-only *System Health* view (`org.nizho.pulse`, like
  `nizhod`). It only watches, and it says what it can't measure yet. See the
  [design](RELIABILITY-NIZHO-PULSE.md).

### Installing, updating, rolling back

- **Installer ISO with disk encryption (first version).** `scripts/make-iso.sh`
  builds an installer from the image using Fedora's installer (Anaconda), set up
  in `system/installer/config.toml`. You pick the disk, nothing is written before
  "Begin Installation", and the disk is encrypted (LUKS2) by default. Nizho
  setup creates your account on first boot. Tested in a VM on 2026-10-12:
  install, unlocking at boot, setup with all six settings configured, the
  desktop, and automatic rollback after a broken update. Known problems: the
  installer still shows Fedora's branding, it asks for the passphrase twice, the
  unlock prompt shows the disk's internal name, every restart of an encrypted
  computer (rollback retries included) needs the passphrase, and the 6.7 GB ISO
  is not published. Not tested on real hardware.
- **Signed updates from ghcr.io.** GitHub Actions builds the image on pushes to
  `main` that touch more than docs, the website, assets or Markdown files, and
  once a week. It splits the image into
  small layers, signs it with cosign and publishes it as
  `ghcr.io/nizho-os/nizho`. Nizho accepts an image from there only if it
  is signed with the Nizho key (`cosign.pub`). In a VM on 2026-10-17 an unsigned
  image and an image checked against another key were both refused before
  anything was downloaded, and the signed image installed and booted. A later
  update downloaded 5 of the 65 layers (863 MB instead of 5.3 GB). New installs
  take their updates from there. Not yet on hardware.
- **Updates never restart the computer on their own.** They download in the
  background. The Updates app shows the installed version, any downloaded
  update, whether a restart is needed and whether a previous version is
  available. You choose when to restart.
- **Automatic rollback after a bad update (greenboot, with two fixes).** After
  you restart into a new version, start-up checks run. If the version fails
  them, or fails before the desktop starts, the computer restarts and, after
  three failed boots, goes back to the previous version. Stock greenboot 0.16.4
  never recorded which version to go back to, so Nizho fixes that. It also
  arms GRUB's boot counter when an update is staged, so a kernel or early-boot
  failure falls back too, instead of waiting at a recovery screen.
  - VM, 2026-10-11: a broken update went back after three failed boots, an
    update that could not start went back within about 30 seconds, a healthy
    update stayed across restarts, `sudo bootc rollback` still worked by hand,
    and the test files in `/home` and `/var` were unchanged.
  - One real laptop (i5-10210U, USB stick), 2026-10-12: the broken update went
    back after three failed boots and the healthy update stayed across two
    restarts. An update that fails before the system starts has not been tried
    on hardware.
  - If applying an update fails at shutdown (for example a full `/boot`), Nizho
    keeps the version you were on. Stock greenboot would roll that healthy
    system back to an older one
    ([greenboot-rs#219](https://github.com/fedora-iot/greenboot-rs/issues/219)).
  - The installer gives `/boot` its own partition. On 2026-10-17 a broken update
    rolled back on such an install even with the Nizho greenboot fix switched
    off, because the trigger is stopped together with `/boot` there (reported as
    [greenboot-rs#218](https://github.com/fedora-iot/greenboot-rs/issues/218)).
    USB images have no separate `/boot` and rely on the fix.

### Privacy and security

- **Privacy Dashboard without a score.** It lists each setting as *Configured*
  or not, says how long ago it was checked, and has a page for updates and for
  going back to the previous version.
  `nizhod` still publishes a number on D-Bus as `PrivacyScore`. It is an
  internal number and no Nizho program reads or shows it. It runs from 0 to 90
  and every point comes from a setting that `nizhod` checked. It used to add 10
  fixed points for "telemetry blocked" and "tracking blocked". Nothing checked
  those, so they were removed.
- **How we word privacy.** Firefox's tracking and fingerprinting-script blocking
  reduce some tracking. They do not prevent fingerprinting. *Configured* means
  the checked setting is in place, not "you are protected". A check that is
  missing or stale shows as unknown. See the [threat model](THREAT-MODEL.md).
- **Firewall on by default.** An Nizho firewall zone rejects connections other
  computers try to open to this one. `nizhod` checks the zone's actual rules and
  policies, not just its name. Setup, the dashboard and the tray show the result.
- **Encrypted DNS, with one exception.** On a network that blocks encrypted DNS
  (some hotels and sign-in pages), the dashboard notices that names aren't
  resolving and offers to allow unencrypted DNS **on that network only**. It then
  shows *Off on this network*, and other networks stay strict.
- **Kernel hardening.** Stricter kernel boot options (memory wiping, lockdown in
  integrity mode, and others) and some runtime settings. User namespaces stay
  on, because Firefox's sandbox and Flatpak need them.
- **Fewer false alarms.** The tray only warns about a problem that lasts about
  30 seconds, so a firewall that is slow to start, or a setting `nizhod` fixes at
  once, no longer shows a popup. When a setting comes back on its own, the log
  says *Setting confirmed again*. Tested in a VM on 2026-10-14.
- **KDE Connect is off by default.** While it runs, it makes the computer
  visible to phones and computers on the same network. It no longer starts at
  login, from the tray or from Dolphin's right-click menu. Settings › Internet
  has a switch that starts it now and at every login, and turns it off again.
  Opening the KDE Connect app also turns it on until you log out. Tested in a VM
  (2026-10-20 and 2026-10-25): it started and stopped, port 1716 opened and
  closed, and it came back after logging in again. Not tested with a real phone.

### Desktop

- **New setup and a Light/Dark look (Fedora 44 candidate).** A rewritten
  first-boot setup: choose Light or Dark, create your account, see where each
  privacy setting stands right now, then sign in. The login screen, desktop,
  icons and every Nizho app follow that choice, and Settings › Appearance
  switches it later. The first desktop starts in the Nizho look, and the boot
  logo stays centred when the display changes resolution. Tested in a VM in both
  looks. Not yet on hardware.
- **Dock.** A dock at the bottom replaces the taskbar: pinned and open apps,
  icons that grow under the pointer, a small label with the app's name, a dot
  under running apps, a divider before Trash, and a bounce while an app starts.
  The desktop behind it is blurred on computers whose graphics run KDE's blur
  effect (not in a VM without 3D). It hides only when a window covers it and
  comes back at the bottom edge. Right-click it to keep it always visible or to
  change apps and sizes. It is
  [Crystal Dock](https://github.com/dangvd/crystal-dock) with an Nizho patch
  (see `desktop/crystal-dock/`). Full-screen apps hide it, and it does not come back at
  the edge until you leave full screen. Tested in a VM (2026-10-18). Not yet on
  hardware.
- **Launcher.** The button at the top left, or the Meta key, opens a panel
  instead of KDE's full-screen menu: a search box for apps and settings, the app
  categories, your pinned apps, and Lock, Log Out, Restart and Shut Down.
  Right-click an app to pin or unpin it. The line at the bottom shows the same
  privacy status as the tray and opens the Privacy Dashboard. The app list and
  search are Plasma's own, so newly installed apps show up, and the panel
  follows the Light or Dark look. New accounts get it. An older account keeps
  KDE's menu until you right-click it and pick *Show Alternatives*. Tested in a
  VM (2026-10-20) with mouse, keyboard and the Orca screen reader, in Light and
  Dark, at 150% scaling, and on two virtual screens. Not yet on hardware.
- **Top bar on every screen.** Each screen gets the bar with the launcher, clock
  and tray, including a screen plugged in later. A bar you remove from a screen
  stays removed. Tested in a VM with two virtual screens (2026-10-20). Not yet
  with real monitors.
- **Window snapping.** Meta+Left or Meta+Right puts a window on half of the
  screen. Dragging it to a screen edge shows a preview. This is KDE's own tiling,
  unchanged. Tested in a VM (2026-10-25) with the keyboard and by dragging. Not
  yet on a touchpad.
- **Move the dock.** Settings › Appearance has Bottom, Left and Right. Crystal
  Dock has no menu entry for this, so Settings changes its setting and restarts
  it. Tested in a VM (2026-10-25): each choice moved the dock and one dock kept
  running.
- **Popups with cut corners.** Notifications, the volume and brightness popups,
  the calendar, the tray and the launcher have corners cut at 45 degrees, like
  the peaks in the Nizho mark and wallpaper, with a thin border and a soft
  shadow. The launcher's search field, highlights and tiles match. It is part of
  the Nizho Plasma style, so it follows Light and Dark. Tested in a VM
  (2026-10-20).
- **Lock screen in Light.** The lock screen keeps the dark wallpaper, as the
  login screen does, so its white clock stays readable. New accounts' trays
  leave out Weather and Vaults. Both can be switched back on in the tray's
  settings. Tested in a VM (2026-10-20).
- **Phosphor icon set.** The Nizho icon theme (`desktop/nizho-icons/`) draws app icons as coloured tiles
  with [Phosphor](https://phosphoricons.com) line symbols (MIT licence, copy in
  `THIRD-PARTY-PHOSPHOR-LICENSE.txt`). It covers 39 app icons and two action icons (suspend, switch user):
  the Nizho apps and launcher button, Dolphin, Konsole, Discover, System Settings, System Monitor, Okular,
  Gwenview, KCalc and the LibreOffice apps. Firefox is not in the set and keeps its own icon, because its
  logo is a trademark. Trash stays the stock theme's icon, because the tile clashed with the line icons in
  Dolphin's sidebar. The theme inherits Breeze Dark for everything else, so other icons are not Phosphor.
  A critic panel scored the set about 6.7 against 6.5 for the old set on a strict rubric. That is a small
  gain, not a transformation, and the panel's notes are not kept in this repository. That score is the only
  test recorded for it. No run of a booted image with this theme is recorded, and it has not been seen on
  hardware.
- **Night light.** Settings › Appearance has a switch for warmer screen colours
  from 8 pm to 6 am. KDE's own schedule looks up your location by default. This
  one uses fixed times, so the switch asks for no location (we read this in the
  schedule library's source and did not check it with a network trace). Tested
  in a VM (2026-10-25): KWin reported Night light at 4500 K when on and 6500 K
  when off.
- **Reduce motion.** Settings › Appearance has a switch that turns off most
  window, menu and desktop animations, using KDE's own animation setting.
  Turning it off returns to KDE's normal speed. Tested in a VM (2026-10-25): the
  setting is written and removed, KDE's change notice is sent on the session bus,
  and Settings shows the real value when reopened. We did not time the
  animations.
- **Natural touchpad scrolling by default** (content follows the fingers). It
  can be turned off in System Settings. Tested in a VM with a virtual touchpad.
  Not yet on a real one.
- **Installing apps shows what they can reach first.** `nizho install NAME`, and
  Capsule's Install button, print what a Flathub app asks for in plain words
  (all your files, every device, the internet and so on) and ask before
  installing it for your account only. The text comes from Flathub's published
  metadata. It limits nothing by itself and does not make an app safe. We read
  the prompt and permission text against Flathub from the build machine and did
  not run a full install. `apps/test_nizho_install.py` runs in CI. Not tested in a VM or on hardware.
- **Keyboard shortcuts page.** Meta+/ opens a list of the Nizho shortcuts and
  KDE's window, split-screen and space shortcuts. Each key on it was checked
  against KDE's shortcut settings or is set by an Nizho component. In a VM
  (2026-11-01) we pressed most of them for real and the result matched the page;
  Alt+`, Ctrl+F3 and Meta+Ctrl+Shift+arrows were not pressed, and two more were
  pressed without checking the result. The list is in
  [docs/SHORTCUTS.md](SHORTCUTS.md). Not yet on hardware.
- **Three spaces.** New accounts start with three virtual desktops and the top bar
  shows KDE's space switcher. Older accounts get two more once, at login. We have
  not seen the switcher working: our test session has no window manager. Not
  tested in a VM or on hardware.
- **Language and keyboard in setup.** Setup has a page where you pick a language
  and a keyboard layout from lists on the computer; picking a language suggests a
  layout. The choice is applied at account creation with `localectl` and the
  session's keyboard settings (`system/nizho-oobe-apply`). Nothing is looked up
  online. Unit tests check the locale and layout names, and `scripts/test-oobe-apply.py`
  runs the helper with stubbed tools. A preview render of the page was made. It has
  not been run in a booted image, and we have not checked how much of the desktop is
  translated into each language.
- **Computer name and wheel tutorial in setup.** Setup now asks for a computer
  name (default `nizho`, which is the same on every Nizho computer) and ends with
  a short practice of the format wheel. We checked each page as a screenshot of
  the real window. It has not run at a real first boot.

### Working with files

- **Convert files from the right-click menu.** In Dolphin, *Convert to* turns
  pictures into PNG, JPEG, WebP or PDF, a PDF into one PNG per page or a smaller
  PDF (pictures reduced to 150 dpi; nothing is kept if it would not be smaller),
  sound into MP3, FLAC, WAV, Ogg, Opus or AAC, and video into MP4 (H.264), MKV or
  WebM, or just its sound as MP3. The copy is saved next to the original, or in
  Downloads if that folder is read-only, and never replaces a file. Each converter (ffmpeg, Pillow, poppler, Ghostscript)
  runs in a sandbox with no network access. Long sound and video conversions
  show progress, and stopping one leaves no half-made file behind. Tested in a VM
  (2026-10-19 and 2026-10-20). Not yet on hardware. Dolphin's preview of a very
  short H.264 video (about 2 seconds) can look scrambled. The file itself is fine.
- **Make files smaller, put them in a ZIP, and turn Word files into PDFs.**
  *Make smaller* (in Dolphin's right-click menu and on the format wheel) shrinks
  a JPEG, PNG or WebP picture (scaled down to at most 2560 pixels on its longest
  side, JPEG and WebP saved at quality 78, camera and location details not copied
  over), a sound into an MP3, a video into an MP4 no wider than 1280 pixels, or a
  PDF. A smaller copy is only kept if it really is smaller; otherwise it says so.
  *ZIP* on the wheel puts any file in its own ZIP. A ZIP bundles a file; it does
  not make pictures, sound or video smaller. Word, Excel and PowerPoint files (and
  their OpenDocument twins) become PDFs through LibreOffice. A PDF that has text
  can become a Word file, but the layout will differ, and a scanned PDF is refused
  for Word (its words can be saved as plain text with English text recognition
  instead, see below). Everything runs in the same sandbox with no network access.
  Tested first with real files on the development laptop (2026-11-01). The same
  day, in a booted VM running the image's own sandbox ([TEST-LOG.md](TEST-LOG.md)):
  a photo, a PNG, a WAV to MP3, a song, a video (H.264, so the OpenH264 encoder
  works in the image), ZIP, Word and Excel to PDF, and PDF to Word. A scanned PDF
  made no Word file and a GIF was not shrunk. Not yet on hardware.
- **More converter options in the source.** `nizho-convert` can also save the
  words in a picture or PDF as a text file (Tesseract, English only, can make
  mistakes), write AVIF, TIFF and BMP pictures, make files smaller at Light,
  Medium or Strong or aim for under 10 MB or 25 MB, black out boxes in a video,
  bleep out parts of a sound or video, draw on a picture, and split or join sound
  and video. These are tested by `scripts/test-nizho-convert.py` and
  `scripts/test-edit-window.py` in CI. No test of them in a booted VM is recorded
  here, and the Light, Medium and Strong levels are settings, not promised sizes.
- **Remove hidden details.** Photos, PDFs, sound and video files often carry
  details you can't see: where a photo was taken, the camera, the author's name,
  the software that made a document. *Remove hidden details* in Dolphin's
  right-click menu saves a copy without them, made by
  [mat2](https://0xacab.org/jvoisin/mat2) inside the same no-network sandbox. It
  works on PNG, JPEG, WebP, GIF, BMP, TIFF, PDF, MP3, FLAC, Ogg, WAV, MP4 and AVI
  files. It removes what mat2 knows about. It cannot remove what is in the
  picture or sound itself. Pictures are saved again, which can change a JPEG
  slightly. Tested in a VM (2026-10-20): a photo's camera and author, a video's
  GPS position and a PDF's author were gone from the copies.
- **Format wheel and edit wheel.** Drag a file, press Meta+Alt+C, and a ring of
  the formats that file can become appears at the pointer. Drop the file on one
  to convert it. Meta+Alt+E shows a ring of edits instead. *Convert with the
  format wheel* and *Edit with the edit wheel* in the right-click menu open the
  same rings for clicking. The ring is a flat octagon with cut corners; the choice
  under the pointer is outlined, the middle shows the file's name, and a short
  green tick on the middle's edge points at the choice. The shortcuts can be changed in System Settings › Shortcuts.
  Tested in a VM (2026-10-26) with a real drag from Dolphin: the format wheel
  converted a picture to WebP and left the original alone, and the edit wheel
  opened the crop window. Drags from Gwenview and Firefox were tested on
  2026-10-19. That was the earlier round look; the octagon look (2026-10-27) has
  only been checked in screenshots. Not yet with a real touchpad.
- **Edit from the right-click menu.** For a picture: *Crop* (drag the corners, or
  pick 1:1, 16:9, 3:2, 4:3 or 3:4), *Black out parts*, *Add a background* (a
  colour or gradient, and how much room to leave) and *Rotate left* or *right*.
  For PDFs: *Merge into one PDF* (select two or more) and *Split into pages*. For
  sound and video: *Trim* (type a start and an end). The result is a copy next to
  the original and never replaces a file. The work runs in the same no-network
  sandbox as converting. *Black out* replaces the pixels in the copy, so what was
  under a box can't be read back from the copy. It does not touch the file's name
  or its hidden details (use *Remove hidden details* for those).
  Tested in a VM (2026-10-26) with a real mouse and keyboard: the menu entries for
  rotate right and merge, and the crop (opened from the edit wheel), add a
  background and trim (MP3) windows. The other menu entries were seen but not
  clicked. Split into pages and trimming an MP4 ran in the VM from the command
  line. Black out's drag was checked in the VM, but its button was only clicked
  outside it. WebM trimming was checked on the build machine. The other sound and
  video formats were not tested. Not yet on hardware.

### Your files and your hardware

- **Backup page in Settings.** Choose a drive, back up now, and get a file back.
  `nizho-backup` (commands `setup --to`, `now`, `status`, `list`, `restore PATH --to`,
  `tidy`) saves your home folder to the folder you choose with restic, encrypted, and
  prints a recovery key once. A daily timer runs it after setup and skips the day when
  the drive is not plugged in. It does not back up the system, is not an offsite
  backup, never overwrites files when restoring, shows no progress, and does not tell
  you when a scheduled backup fails. Restoring one file means typing its path. Facts
  and limits: [BACKUP.md](BACKUP.md). Tested on a development computer against restic
  0.19.1 with temporary folders (make a backup, delete a file, restore it, compare;
  the backup folder holds no file names or contents in the clear; a wrong key and a
  backup folder inside home are refused), and the tests run in CI. The Settings page
  was clicked through in a VM: set up, back up, get a file back. Not tested with a
  real external drive, on hardware, or in a built image.
- **nizho-import.** `nizho-import scan SOURCE`, `files SOURCE --dry-run` and
  `files SOURCE` copy documents, pictures, music, videos, desktop and downloads from
  a mounted Windows drive, a macOS home folder or any folder into your home folder.
  It never changes the source, never overwrites your files (a different file with
  the same name is kept as `name (imported).ext`), and skips identical files. It
  leaves out apps, browser profiles, passwords, keychains and mail. It does not
  unlock encrypted drives. Facts and limits: [IMPORT.md](IMPORT.md). Tested with
  temporary folders and files in CI (layouts, name clashes, a read-only source,
  unreadable files, refusal of a source that is or contains your home folder). Two
  tests inject a fault: low free space and a read error. Not tested on a real NTFS
  or exFAT drive, a Time Machine backup, a failing disk, a full destination or
  large amounts of data. It is a command-line tool with no window. Not tested in a
  built image.
- **nizho-hwreport.** Prints a Markdown hardware report to paste into a GitHub
  issue, with serial numbers, MAC and IP addresses, host and user names, Wi-Fi names
  and disk labels left out. `--redact-check` scans the report again and fails if it
  finds an identifier-looking string, which is a safety net and not a promise that
  nothing private is left, so read the report before posting. The checklist at the
  end is filled in by hand. [HARDWARE.md](HARDWARE.md) has the one tested machine and
  a list of what is untested. Tested in CI against made-up file trees, not on a real
  machine and not in a built image.

### For developers

- **VARA and Git.** Under *Advanced options*, setup can install
  [VARA](https://github.com/thulasiramk-2310/vara) (version control with built-in
  undo and integrity checks) and set up Git with the name and email for your
  commits. Settings › Developer tools changes them later and can run a VARA Hub,
  a web view of your repositories that only this computer can reach. The Hub is
  off until you switch it on. VARA is built from its v0.4.4 release. Tested in a
  VM (2026-10-20). Not yet on hardware.
