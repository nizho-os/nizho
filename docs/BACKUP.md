# Backing up your files

Status: first version. The tool was tested on a development computer against restic 0.19.1, and
the Settings page was clicked through in a virtual machine (set up, back up, get a file back). It
has not been built into an image yet, and it has not been tried on a real laptop with a real
external drive.

Settings has a Backup page: choose a drive, back up now, and get a file back. The same things work
from a terminal with `nizho-backup`.

## What it does

`nizho-backup` saves the files in your home folder to a folder on a drive you choose, encrypted.
Old copies are kept, so you can get back a file you deleted last week.

```
nizho-backup setup --to /run/media/you/MyDrive/nizho-backup
nizho-backup now
nizho-backup status
nizho-backup list
nizho-backup restore Documents/report.odt --to ~/Restored
nizho-backup tidy
```

`setup` prints a recovery key once. Write it down or keep a copy somewhere other than this computer.
A daily timer runs `now` for you after setup, and does nothing when the drive is not plugged in.

## What it does not do

- It does not back up the system. Updates keep the previous system image for that, and going back
  to it does not restore older copies of your files.
- It is not an offsite backup. A backup drive kept next to the computer will not survive a fire or
  a theft that takes both.
- It skips caches and the trash, so it is smaller than a full copy of your home folder.
- Restoring never overwrites your files. It puts the old copy in a folder you choose, and you copy
  back what you need.
- If you lose the recovery key and this computer, the backup cannot be opened. That is how the
  encryption works.
- Backup progress is not shown while it runs, and nothing tells you when a scheduled backup fails.
- Restoring one file at a time means typing its path, for example Documents/report.odt. There is no
  list of saved files to browse yet.

## What we checked

The tests make a backup, delete a file, restore it, and check that the restored copy matches. They
also check that the backup folder does not contain your file names or contents in the clear, that a
wrong key is refused, and that a backup folder inside your home folder is refused.
