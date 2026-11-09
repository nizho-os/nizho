# Backing up your files

Status: written for the first version of `nizho-backup`. It has been tested on a development
computer against restic 0.19.1. It has not yet been built into an image or run on Nizho itself, and
it has no graphical screen yet. Treat it as a command-line tool for people who are comfortable
with a terminal.

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

## What we checked

The tests make a backup, delete a file, restore it, and check that the restored copy matches. They
also check that the backup folder does not contain your file names or contents in the clear, that a
wrong key is refused, and that a backup folder inside your home folder is refused.
