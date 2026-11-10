# Evidence

How Nizho documents say what is true, and where each claim stands today.

Nizho began as a copy of ArkaOS and was renamed on 2026-11-01 (commit `d3dd912`). The rename
changed names inside older documents too, so an old record can say "Nizho" about a test that
ran on an ArkaOS build. This file is the rule for telling the two apart.

## The convention

Every claim about a feature carries one status, one test kind and one build family.

**Status** (pick one):

| Status | Meaning |
|---|---|
| Implemented and verified on Nizho | Seen working in a booted image built from the Nizho tree. The record says what was observed. |
| Implemented but not tested | The code is in the tree. No test of it in a booted Nizho image is recorded. |
| Inherited from ArkaOS | The code came with the copy and its recorded tests ran on ArkaOS builds. |
| Experimental prototype | It exists to learn from and is not part of the shipped image. |
| Documented but not implemented | A document describes it and the code does not exist. |
| Planned | Intended work with no code and no date. |

**Test kind** (the strongest one that is recorded):

| Kind | Meaning |
|---|---|
| unit test | A test inside the source, run with the language's test tool. |
| script test in CI | A script under `scripts/` or beside a tool, run by the Source checks workflow. |
| VM boot | Seen in a booted virtual machine. |
| real hardware | Seen on a physical computer, which the record names. |

A unit test or a script test says the code behaves as written. It says nothing about a booted
image. A VM boot says nothing about real hardware.

**Build family**: Nizho or ArkaOS. A test belongs to ArkaOS when its image was built from a
commit before `d3dd912`, whatever name the document now uses.

## The rule

A result recorded on an ArkaOS build is never reported as a Nizho result. It may be cited as
"Inherited from ArkaOS" with its date. It becomes a Nizho result only when the test is run
again on a Nizho image and recorded.

"Verified" covers only what the record says was observed. If the record says a screen showed
"Configured", the claim is that the screen showed it.

## How to record a result

Add a dated section to [TEST-LOG.md](TEST-LOG.md) with these five things:

1. The date.
2. The image: the commit it was built from, or the CI run id. Write "not recorded" if there is none.
3. Where it ran: a VM (say which kind) or real hardware (name the machine).
4. What was observed.
5. What was not observed or not tried.

A result without the fifth item is incomplete.

## Status of the ten main claims

Taken from [TEST-LOG.md](TEST-LOG.md), [RELEASE-VALIDATION.md](RELEASE-VALIDATION.md),
[FEATURES.md](FEATURES.md), [BACKUP.md](BACKUP.md) and the [README](../README.md) on 2026-11-10.
Nothing in this table ran on real hardware under the Nizho name.

| Claim | Status | Test kind | Build family | Observed | Not observed |
|---|---|---|---|---|---|
| Automatic rollback after a broken update | Inherited from ArkaOS | real hardware (one Nokia laptop, 2026-10-12), VM boot (2026-10-11) | ArkaOS | Three failed boots, then a return to the earlier image, in a VM and on the one laptop. | Any rollback on a Nizho image. An update that fails before the system starts, on hardware. |
| Signed updates | Inherited from ArkaOS | VM boot (2026-10-17) | ArkaOS | An unsigned image and an image checked against another key were refused. The signed image installed and booted. | A refused update on a Nizho image. On the Nizho tree, `scripts/test-signing.py` runs in CI and the Stick image workflow runs `cosign verify` before it writes a disk. |
| Installer ISO with disk encryption | Inherited from ArkaOS | VM boot (2026-10-12) | ArkaOS | Install, unlock at boot, setup, desktop. | A Nizho installer ISO. Any install on real hardware. |
| Read-only root (composefs) | Inherited from ArkaOS | VM boot | ArkaOS | The README's DP1 table lists it as seen in a VM. On 2026-11-01 the root refused fs-verity, so no file contents are checked by digest. | A recorded check of the read-only root on a Nizho image. |
| `nizhod` applies and re-checks the privacy settings | Implemented and verified on Nizho | VM boot (2026-11-09) | Nizho | The status pill read "Privacy: 6 of 6 settings in place" and the dashboard listed six rows as "Configured". | No record of each setting checked on its own with system tools on a Nizho image, of drift being corrected, or of a real network. |
| Browser sandbox | Inherited from ArkaOS | VM boot ([PHASE4-SANDBOX.md](PHASE4-SANDBOX.md)) | ArkaOS | The earlier isolation checks in that file. | The isolation checks repeated on a Nizho image. On the Nizho tree, `scripts/test-firefox-wrapper.py` runs in CI, and on 2026-11-09 the dashboard row "Sandboxed browser" read "Configured". |
| Setup wizard | Inherited from ArkaOS | VM boot (2026-11-01, commit `506bc2d`) | ArkaOS | All 9 pages and the account creation. Two defects were found. | The same pass on a Nizho image. The README records only that setup came up on the first Nizho image. The language and keyboard page has not been run in a booted image. |
| Dock and launcher | Inherited from ArkaOS | VM boot (2026-10-18, 2026-10-20) | ArkaOS | The tests listed in the test log's README table. | Either one tested on a Nizho image beyond appearing on the desktop. Real monitors and a real touchpad. |
| Convert tools (format wheel, edit, make smaller) | Inherited from ArkaOS | VM boot (2026-10-19 to 2026-11-01) | ArkaOS | The conversions listed in the test log. `scripts/test-nizho-convert.py` and `scripts/test-wheel-formats.py` exist on the Nizho tree. | Any conversion on a Nizho image. Most sound and video formats. |
| Backup | Implemented but not tested | unit test (`backup/test_nizho_backup.py`, in CI) | Nizho | On 2026-11-09 the Settings page and `nizho-backup` were loaded by hand into a Nizho VM. Set up, back up and restore a file worked to a folder on the VM's own disk. | The tool as built into an image. A real external drive. Real hardware. |

When a row changes, change it here and add the record to the test log in the same commit.
