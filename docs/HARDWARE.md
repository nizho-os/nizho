# Validated hardware

Nizho has booted on one real laptop. No machine is listed here as working. This page records what testers report, including what failed.

## How to run the tool

`nizho-hwreport` prints a Markdown report. It reads files on your computer and runs three read-only tools if they are present: `lspci`, `kscreen-doctor` and `mokutil`. It sends nothing over the network and writes no files. The Containerfile installs it as `/usr/bin/nizho-hwreport`; from a source checkout, run `python3 hardware/nizho-hwreport`.

```
nizho-hwreport --redact-check > report.md
```

The report leaves out serial numbers, MAC and IP addresses, host and user names, Wi-Fi network names, disk labels and UUIDs, and paths in your home folder. The `--redact-check` flag scans the report again and fails if it finds a MAC address, an IP address, or a serial or UUID looking string. Read the report yourself before you post it. The tool's tests (`hardware/test_nizho_hwreport.py`, run in CI) read a made-up file tree, not a real machine.

The checklist at the end of the report is blank. Fill it in by hand with works, partly, broken or not tested. Leave out what you did not try.

## How to submit a report

1. Run the tool as above and fill in the checklist.
2. Open a GitHub issue in this repository and paste the report.
3. Write the image version and how you booted (USB stick, installed, virtual machine).

A report with "broken" lines is as useful as one with "works" lines.

## Tested machines

| Machine | Tested image | Date | What works | What does not | Report link |
| --- | --- | --- | --- | --- | --- |
| Nokia PureBook NKi510UL85S, Intel Core i5-10210U, Intel UHD graphics, 8 GB RAM, AMI Aptio UEFI, BIOS N.1.02 (2020-10-23) | First boot (2026-09-13): a raw image written to a USB stick, image version not recorded. Rollback test (2026-10-12): a USB stick with the image whose base digest is `684ffa4a`. | First boot 2026-09-13. Rollback test 2026-10-12. | Booted from the USB stick; the internal Windows disk was not touched. GRUB, kernel, setup wizard and KDE Plasma desktop at native resolution. Wi-Fi connects, touchpad, brightness and volume keys, suspend and resume. nizho-pulse read a temperature of 51 C. On 2026-10-12 a deliberately broken update rolled back after three failed boots and a healthy update stayed. | Browser had no audio (a fix is in the source, not confirmed on this laptop). Capsule installs did nothing because no Flathub remote existed (fix in the source, not confirmed). Old image crash-looped the serial login (agetty) on `ttyS0`, and the USB stick had a damaged primary GPT checksum. Everything looked small at first (scaling not recorded). | Not recorded |

Sources: `docs/FIELD-NOTES.md`, `docs/NOKIA-USB-DIAGNOSIS.md`, `docs/RELEASE-VALIDATION.md`, `docs/FEATURES.md`. These are the project's own notes from one tester. They were not made with `nizho-hwreport`, so no hardware report exists for this machine. Bluetooth, webcam, microphone, external monitor, fingerprint and printing are not recorded. The notes of 2026-09-13 also say that fixes made after that test are not confirmed on the laptop.

## Performance

Boot time, idle memory and battery have not been measured on any machine. [PERFORMANCE.md](PERFORMANCE.md)
has the budgets, the measuring script and what to record with each result.

## What we have not tested at all

- Any laptop or desktop other than the one above.
- Installing to the internal disk on real hardware, including the encrypted installer.
- Dual boot with Windows.
- Bluetooth pairing.
- Webcam and microphone.
- Speakers and the headphone jack (browser audio failed once and the fix is unconfirmed).
- External monitors and multiple screens.
- HiDPI scaling on real panels. The first boot looked too small and the scale was not recorded.
  The image now has a login step, `system/nizho-display-scale`, that sets a scale once for each new
  screen from its EDID size when KWin left it at 100%. It is tested only with made-up EDIDs and a
  stand-in `kscreen-doctor` (`scripts/test-display-scale.py`). It has not run on a real display or
  a booted Nizho image.
- Touchpad gestures beyond basic pointing.
- Fingerprint readers.
- Printing and scanning.
- NVIDIA graphics, hybrid graphics, and AMD graphics.
- Battery life.
- Suspend and resume on any machine other than the one above, and hibernate.
- Secure Boot on real hardware.
- Wi-Fi chips other than the one in the machine above.
- Updates that fail before the system starts, on hardware.
