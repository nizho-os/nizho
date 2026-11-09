# nizho-import

`nizho-import` brings personal files into your home folder from a drive you plugged in or an old disk you mounted. It only reads the source.

## Commands

    nizho-import scan SOURCE
    nizho-import files SOURCE --dry-run
    nizho-import files SOURCE

SOURCE can be:

- A Windows drive. It has Users/NAME with Documents, Pictures, Music, Videos, Desktop and Downloads. Each is copied to the same folder in your home. The Public and Default users are left out.
- A macOS home folder or a Time Machine style folder. It has Documents, Pictures, Movies, Music and Desktop. Movies goes to Videos.
- Any other folder. It is copied to ~/Imported/FOLDERNAME.

The tool says which one it detected.

## Rules

- The source is never written, renamed, deleted or changed.
- Nothing in your home folder is overwritten. If a name exists with different content, both are kept. The new file is named `name (imported).ext`, then `name (imported 2).ext`, and so on.
- If the content is identical (same size, same SHA-256) the file is skipped and counted as already there.
- Skipped on purpose: desktop.ini, Thumbs.db, .DS_Store, $RECYCLE.BIN, System Volume Information, ._* files, *.tmp files, and symlinks. Symlinks are not followed. The count is shown.
- A source that is your home folder, inside it, or contains it is refused.
- A file that cannot be read is skipped. Each one is listed at the end with a reason.
- Free space is checked before copying. If there is not enough, nothing is copied.
- Modification times are kept on copied files.

Exit codes: 0 if everything copied, 2 if some files were skipped because of an error, 1 for a fatal error. Files skipped on purpose (junk, symlinks) do not change the exit code.

## What it does not do

- It does not move apps or licences.
- It does not copy browser profiles, saved passwords, cookies or keychains. Files named like password databases (.kdbx, .keychain) and folders such as AppData, Library, .mozilla, .ssh and .gnupg are left out. Firefox can import bookmarks and logins itself: Menu, then Import Data from Another Browser.
- It does not read Outlook or Mail data.
- It does not unlock encrypted drives. Unlock and mount the drive first.
- It does not delete anything after copying.

## What was tested

Tests are in import/test_nizho_import.py. They use real temp folders and files, with HOME set to a temp folder.

    cd import && python3 -I test_nizho_import.py -W error::ResourceWarning

Covered: Windows layout, macOS layout (home and Users/NAME), plain folder, scan output, rename sequence up to `(imported 3)`, identical skip, re-run gives no new copies, same size but different content, two Windows users with the same file name, junk and symlink and password file skipping, dry run changes nothing, modification times, refusal of a source inside home or equal to it or a symlink into it or containing it, source tree identical before and after (bytes, mtimes, modes, file list) including with a read-only source folder, an unreadable file and folder (chmod 000, skipped when running as root), a name too long for the filesystem, a FIFO that is not opened.

Two tests inject a fault because it cannot be created for real: the free space check (a patched free space value) and a read error halfway through a file (EIO).

Not tested: a real NTFS or exFAT drive, a real Time Machine backup (Backups.backupdb layout is not handled), a real disk with bad sectors, a full destination disk during a copy, large amounts of data, Windows junctions, OneDrive placeholder files, and running on real hardware.
