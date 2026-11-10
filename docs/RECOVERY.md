# Recovery: going back to the previous version

Status on 2026-11-10. Read this before trusting any line below.

- The automatic rollback tests in [RELEASE-VALIDATION.md](RELEASE-VALIDATION.md) were run in
  October 2026 in virtual machines, on images built before the project was renamed to Nizho. They
  have not been repeated on an image published as `ghcr.io/nizho-os/nizho`.
- No rollback has been tested on a real computer.
- The Nizho health checks described here are new. Their logic is tested with fake commands
  (`scripts/test-greenboot-checks.py`). They have not run under greenboot on a booted Nizho, and no
  rollback caused by them has been observed.

## What Nizho keeps

Nizho installs each update next to the version you are using. The version you had stays on the
disk as the previous version. Going back means starting the computer from that previous version.
Only one previous version is kept.

## What going back changes

| Part of the computer | What happens when you go back |
|---|---|
| The system (`/usr`: programs and libraries that come with Nizho) | Returns to the previous version. |
| Your files (`/home`) | Not touched. Nothing is restored and nothing is removed. |
| Other data in `/var` (Flatpak apps and their data, logs) | Not touched. |
| System settings in `/etc` | Each version has its own copy. You get the previous version's copy as it was when you last used that version. Settings you changed after the update stay with the newer version. |

Going back is not a backup. It does not bring back a deleted file. It does not undo what a newer
app did to its own data, so an older app may fail to read data that a newer one rewrote. For your
files, use [BACKUP.md](BACKUP.md).

The `/etc` row comes from the ostree and bootc documentation
([ostree deployments](https://ostreedev.github.io/ostree/deployment/),
[bootc filesystem](https://bootc-dev.github.io/bootc/filesystem.html)). Nizho has tested two
files in `/home` and `/var` across a rollback in a VM, and has not tested `/etc`.

## How Nizho decides a boot is bad

After every start, greenboot 0.16.4 runs the check scripts in `/usr/lib/greenboot/check` and
`/etc/greenboot/check`.

- A script in `required.d` that fails marks the boot as bad. greenboot restarts the computer. After
  3 bad boots of a new version (`GREENBOOT_MAX_BOOT_ATTEMPTS` in `/etc/greenboot/greenboot.conf`)
  it goes back to the previous version.
- A script in `wanted.d` that fails is written to the journal. Nothing else happens.
- greenboot gives a script no time limit. Each Nizho script limits its own wait.

Sources: [greenboot.rs](https://github.com/fedora-iot/greenboot-rs/blob/v0.16.4/src/lib/greenboot.rs)
(folders, `bash -C`, required and wanted),
[main.rs](https://github.com/fedora-iot/greenboot-rs/blob/v0.16.4/src/main.rs) (restarts and
rollback), [greenboot.conf](https://github.com/fedora-iot/greenboot-rs/blob/v0.16.4/etc/greenboot/greenboot.conf).
Fedora 44 ships this version
([package data](https://mdapi.fedoraproject.org/f44/pkg/greenboot)).

Required checks added by Nizho:

| Check | Fails when | Wait |
|---|---|---|
| `20_nizho_login_screen.sh` | `sddm.service` is in the failed state, or is still starting after being restarted | up to 120 s |
| `21_nizho_privacy_daemon.sh` | `nizhod.service` is in the failed state, or is still starting after being restarted | up to 60 s |

Wanted checks added by Nizho: a connected display, `NetworkManager.service`, `firewalld.service`.

A wrong failure throws away a good update, so the required checks pass whenever the answer is
unclear:

- They fail only while an update is on trial, which means greenboot has recorded a version to go
  back to. On an ordinary start a failure could not lead to a rollback. greenboot would restart the
  computer three times and then stop. So the checks only report in that case.
- The login screen check is skipped during first-time setup and on a computer with no graphics
  device. A virtual machine with no screen attached still passes.
- A service that is slow and was never restarted passes with a warning.

A bad update that does fail these checks costs time before the rollback: up to about 3 minutes of
waiting on each of 3 starts. On an encrypted disk every one of those starts asks for the
passphrase.

## What is covered and what is not

"VM, before the rename" means the October 2026 tests named at the top.

| Problem after an update | Covered? | Evidence |
|---|---|---|
| Kernel panic, or a stop before the login screen can start | Yes, by GRUB's boot counter and the boot options `panic=10 rd.shell=0 rd.emergency=reboot` | VM, before the rename. Not on Nizho, not on hardware. |
| A required greenboot check fails | Yes, after 3 starts | VM, before the rename, with a test check that always fails. |
| Login screen service failed or restarting in a loop | Yes, since this change | Script logic only. Before this change it was not detected. |
| Black screen while every service looks healthy | No | The checks ask systemd, and cannot see the screen. |
| Bad graphics driver | Only if it makes `sddm.service` fail. A wrong or frozen picture is not detected. The check is skipped when no graphics device appears. | Script logic only. |
| Privacy daemon cannot start | Yes, since this change | Script logic only. |
| Download interrupted | bootc is designed to leave the running version untouched until a download is complete. | Not tested by Nizho. |
| Power lost or `/boot` full while the update is applied at shutdown | The computer starts the version it had. `nizho-stale-trigger-check` stops greenboot from going back one version too far. | VM, before the rename, with a forced failure. Real power loss and a really full `/boot` were not tested. |
| Power lost while files are being written to the boot partition | Unknown | Not tested. |
| First start of a new install fails | No. There is no previous version. Reinstall from the USB stick. | Not tested. |
| Wrong clock | No rollback. Secure connections can fail until the clock is right, so updates may not download. | Not tested for updates. |
| Boot loader damaged, or the firmware cannot find it | No. Nothing in Nizho repairs it. Start from the install USB stick. | Not tested. |
| An app or the desktop breaks after you log in | No. The checks end before login. Go back by hand. | |

## What to do

**The computer restarts a few times after an update and then works.** It went back on its own.
Open Updates to see which version is installed. Your files are as you left them.

**The computer works but something is wrong since the update.** Open Updates and choose "Use the
previous version after restart", then restart. In a terminal the same thing is:

    sudo bootc rollback

Then restart.

**The screen stays black or the login screen never appears.**

1. Hold the power button until the computer turns off. Turn it on again.
2. When the boot menu appears, pick the second entry with the arrow keys and press Enter. That is
   the previous version. If no menu appears, turn the computer on again and press Esc several
   times as soon as it starts.
3. If the previous version starts, log in, open Updates and choose "Use the previous version after
   restart". Without this step the next start uses the broken version again.

Step 2 depends on the boot menu showing both versions. This path has not been written up as tested
on a Nizho install.

**Nothing starts at all.** Start from the Nizho USB stick. Reinstalling erases the disk. Restore
your files from your backup ([BACKUP.md](BACKUP.md)).

**Updates says this computer is not set to receive Nizho updates.** The computer follows an image
that gets no updates, for example because the last step of the installer failed. The message shows
the command to fix it:

    sudo bootc switch ghcr.io/nizho-os/nizho:latest

Then restart. Nizho does not run this command for you. The check that produces the message was
tested against sample `bootc status` output. It was not tested on an installed computer.

**Updates refuses to download because the disk is nearly full.** Updates asks for 3 GB free before
a download. The number is a rough guess, and a download can still fail above it. Free some space
and try again. Going back to the previous version is never refused for this reason. Updates that
download in the background do not use this check.

## For developers

- Checks: `system/greenboot/`. Tests: `python3 -I scripts/test-greenboot-checks.py`.
- To switch one check off on a computer, add its file name to `DISABLED_HEALTHCHECKS` in
  `/etc/greenboot/greenboot.conf`.
- Before a release claims any row above for Nizho, repeat the steps in
  [RELEASE-VALIDATION.md](RELEASE-VALIDATION.md) on a Nizho image, and add a case where
  `sddm.service` is made to fail in the update. See [RELEASE-GATE.md](RELEASE-GATE.md).
