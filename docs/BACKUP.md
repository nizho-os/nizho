# Backing up your files

Status: first version. The tool was tested on a development computer against restic 0.19.1, and
the Settings page was clicked through in a virtual machine (set up, back up, get a file back). The
Containerfile now installs the tool, restic and the daily timer, but no test of a built image is
recorded, and it has not been tried on a real laptop with a real external drive.

Settings has a Backup page: choose a drive, back up now, and get a file back. The same things work
from a terminal with `nizho-backup`.

## What it does

`nizho-backup` saves the files in your home folder to a folder on a drive you choose, encrypted.
Old copies are kept, so you can get back a file you deleted last week. After each scheduled backup,
`tidy` drops old copies and keeps 7 daily, 4 weekly and 6 monthly ones.

```
nizho-backup setup --to /run/media/you/MyDrive/nizho-backup
nizho-backup now
nizho-backup status
nizho-backup list
nizho-backup ls Documents
nizho-backup restore Documents/report.odt --to ~/Restored
nizho-backup tidy
```

`ls` shows what is inside a folder of a saved backup, folders first. Without a folder it shows your home
folder. `--snapshot ID` picks an older backup (the newest is the default) and `--json` prints a list for
programs. `nizho-backup snapshots --json` lists the backups, newest first.

`setup` prints a recovery key once. It also saves the key on this computer, in `~/.config/nizho/backup.key`
(readable only by you). Write it down or keep a copy somewhere other than this computer. The backup does
not contain its own key, so a lost key cannot be found inside it.
A daily timer runs `now` for you after setup, and does nothing when the drive is not plugged in.
`setup` refuses a backup folder inside your home folder unless you add `--allow-home`.

## Finding a file in Settings

Get a file back opens a window that shows the files in the backup. Pick the backup from the list at the
top (the newest is chosen, shown as for example Today 14:05). Click a folder to open it, and use Back to
go up. Tick the box beside a file or folder and choose Restore selected, then choose an empty folder for
the older copy. Arrow keys and Enter work in the list. If you do remember the path, Type a path instead
still asks for it as before.

This screen was built and its logic is covered by unit tests, but nobody has looked at it on a screen yet,
so its layout and keyboard behaviour are not visually verified.

## What it does not do

- It does not back up the system. Updates keep the previous system image for that, and going back
  to it does not restore older copies of your files.
- It is not an offsite backup. A backup drive kept next to the computer will not survive a fire or
  a theft that takes both.
- It skips caches, the trash, thumbnails, `node_modules` folders and anything on another mounted
  drive, so it is smaller than a full copy of your home folder.
- Firefox's saved profile, `~/.local/share/nizho/firefox-profile`, is in your home folder and is
  not excluded, so bookmarks, logins, history and extensions are backed up. A backup made while
  Firefox is open can catch its files half written. A private session saves nothing to back up.
- Restoring never overwrites your files. It puts the old copy in a folder you choose, and you copy
  back what you need.
- If you lose the recovery key and this computer, the backup cannot be opened. That is how the
  encryption works.
- Backup progress is not shown while it runs, and nothing tells you when a scheduled backup fails.
- You restore one file or folder at a time, because the restore folder must be empty.

## What we checked

The tests make a backup, delete a file, restore it, and check that the restored copy matches. They
also check that the backup folder does not contain your file names or contents in the clear, that a
wrong key is refused, and that a backup folder inside your home folder is refused. They run in CI
(`.github/workflows/checks.yml`) and use temporary folders, not a real external drive.
