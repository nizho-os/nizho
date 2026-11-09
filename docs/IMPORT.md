# nizho-import

`nizho-import` brings personal files into your home folder from a drive you plugged in or an old disk you mounted. It only reads the source.

## Commands

    nizho-import scan SOURCE
    nizho-import files SOURCE --dry-run
    nizho-import files SOURCE
    nizho-import bookmarks SOURCE [--to FILE] [--dry-run]

SOURCE can be:

- A Windows drive. It has Users/NAME with Documents, Pictures, Music, Videos, Desktop and Downloads. Each is copied to the same folder in your home. The Public and Default users are left out.
- A macOS home folder or a Time Machine style folder. It has Documents, Pictures, Movies, Music and Desktop. Movies goes to Videos.
- Any other folder. It is copied to ~/Imported/FOLDERNAME.

The tool says which one it detected.

## Bookmarks

    nizho-import bookmarks SOURCE
    nizho-import bookmarks SOURCE --dry-run
    nizho-import bookmarks SOURCE --to ~/Documents/my-bookmarks.html

This reads bookmark files and writes one standard bookmarks HTML file. Every browser can import that format. The default place is `~/Documents/Bookmarks from NAME.html`, where NAME is the browser found (for example Chrome, or "Chrome and Firefox"). A file that already exists is never replaced. The new file gets ` (2)`, ` (3)` and so on. `--dry-run` reads and reports but writes nothing.

SOURCE can be a mounted Windows drive, a macOS home folder, an old home folder, or a browser profile folder.

What is read, and only that:

- Chrome, Chromium, Edge, Brave, Vivaldi and Opera: the JSON file named `Bookmarks` in each profile folder (Default, Profile 1 and so on).
- Firefox: the bookmarks tables in `places.sqlite`.
- Safari: `Library/Safari/Bookmarks.plist`. Reading List and History are skipped.

Windows, macOS and Linux profile locations are checked. The tool prints which files it found.

The output has one top level folder per browser, so nothing is mixed. If a browser has several profiles, each profile gets its own folder inside. Folder order, names, addresses and added dates are kept. Safari does not store dates, so Safari bookmarks have none.

Left out and counted in the summary: addresses that start with `javascript:` or `data:`, entries with no address, Firefox `place:` shortcuts and tags, and the same address twice in one folder. Titles longer than 500 characters are cut, and control characters are removed.

The file is only written. The tool does not import it into any browser. To import it into Firefox: Menu, then Bookmarks, then Manage bookmarks, then Import and Backup, then Import Bookmarks from HTML, and pick the file.

How the source is protected:

- Firefox databases are opened read only and immutable. If the database has a write-ahead log (`-wal` file), the database and the log are copied to a temporary folder and the copy is read, because an immutable open would miss the newest bookmarks. The copy is deleted afterwards.
- Files that are symlinks pointing outside the source are not followed.
- A source inside your home folder is refused, as with `files`.
- The output file may not be placed inside the source.
- A bookmarks file that is damaged or cannot be read is reported with a plain message and the rest is still written.

Exit codes: 0 if all found files were read and the file was written, 2 if some bookmark files could not be read, 1 if nothing was found, nothing could be read, or the file could not be written.

Not imported, on purpose: saved passwords, cookies, history, autofill, sessions and whole browser profiles. Windows encrypts Chrome and Edge passwords with your Windows login (DPAPI), and macOS keeps them in the Keychain, so they cannot be read from a drive. Copying a whole profile would also carry tracking data. Firefox can import logins itself from a synced account.

`nizho-import scan` lists the browsers whose bookmarks can be exported and says that passwords and other browser data are not imported.

## Rules

- The source is never written, renamed, deleted or changed.
- Nothing in your home folder is overwritten. If a name exists with different content, both are kept. The new file is named `name (imported).ext`, then `name (imported 2).ext`, and so on.
- If the content is identical (same size, same SHA-256) the file is skipped and counted as already there.
- Skipped on purpose: desktop.ini, Thumbs.db, .DS_Store, $RECYCLE.BIN, System Volume Information, .Trashes, .Spotlight-V100, .fseventsd, .TemporaryItems, ._* files, *.tmp files, and symlinks. Symlinks are not followed. The count is shown.
- A source that is your home folder, inside it, or contains it is refused.
- A file that cannot be read is skipped. Each one is listed at the end with a reason.
- Free space is checked before copying. If there is not enough, nothing is copied.
- Modification times are kept on copied files.

Exit codes: 0 if everything copied, 2 if some files were skipped because of an error, 1 for a fatal error. Files skipped on purpose (junk, symlinks) do not change the exit code.

## What it does not do

- It does not move apps or licences.
- It does not copy browser profiles, saved passwords, cookies or keychains. Files named like password databases (.kdbx, .keychain) and folders such as AppData, Library, .mozilla, .ssh and .gnupg are left out. The `bookmarks` command below handles bookmarks only. Firefox can import logins itself from a synced account.
- It does not read Outlook or Mail data.
- It does not unlock encrypted drives. Unlock and mount the drive first.
- It does not delete anything after copying.

## What was tested

Tests are in import/test_nizho_import.py. They use real temp folders and files, with HOME set to a temp folder. They run in CI (`.github/workflows/checks.yml`). The Containerfile installs the tool as `/usr/bin/nizho-import`, but no test of a built image is recorded.

    cd import && python3 -I test_nizho_import.py -W error::ResourceWarning

Covered: Windows layout, macOS layout (home and Users/NAME), plain folder, scan output, rename sequence up to `(imported 3)`, identical skip, re-run gives no new copies, same size but different content, two Windows users with the same file name, junk and symlink and password file skipping, dry run changes nothing, modification times, refusal of a source inside home or equal to it or a symlink into it or containing it, source tree identical before and after (bytes, mtimes, modes, file list) including with a read-only source folder, an unreadable file and folder (chmod 000, skipped when running as root), a name too long for the filesystem, a FIFO that is not opened.

Two tests inject a fault because it cannot be created for real: the free space check (a patched free space value) and a read error halfway through a file (EIO).

Bookmarks tests use temporary files shaped like each browser's real files, not real browser profiles. Covered: Chrome style JSON with nested folders, unicode names, an empty folder, bad entries and script addresses; the Chrome time conversion with a known value; a Firefox style places.sqlite built with the sqlite3 module, including a locked database and one with a write-ahead log; Safari plists in XML and binary form; HTML escaping and a round trip through an HTML parser; no overwrite of an existing output file; the source tree unchanged (file list, bytes, mtimes); a source inside home refused; damaged files giving plain errors; a failed write.

Not tested for bookmarks: a real Windows or macOS drive, real browser profiles, current versions of each browser (the file formats are written from their documented shape), and importing the result into a real Firefox.

Not tested: a real NTFS or exFAT drive, a real Time Machine backup (Backups.backupdb layout is not handled), a real disk with bad sectors, a full destination disk during a copy, large amounts of data, Windows junctions, OneDrive placeholder files, and running on real hardware.
