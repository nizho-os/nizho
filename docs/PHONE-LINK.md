# Phone link (KDE Connect)

Status: written before the feature was tested. The helper and the Settings screens were tested only
with stand-ins. Nothing has been tried with a real phone. `scripts/vm-test-phone-link.sh` and
`docs/PHONE-LINK-TEST.md` are the tests that still have to be run.

## What it is

The phone link lets a phone running the KDE Connect app talk to this computer over the local network.
It can send files, show phone notifications, share the clipboard and ring the phone.

It is off by default. When KDE Connect runs, the computer announces itself to every device on the
network, and Nizho rejects incoming connections otherwise. You turn it on when you want it.

## What turning it on changes

1. The firewall zone `nizho` gets the firewalld service `kdeconnect`, in the running firewall and in
   the saved configuration. firewalld defines that service as TCP and UDP ports 1714 to 1764
   (https://github.com/firewalld/firewalld/blob/main/config/services/kdeconnect.xml).
2. The file `/etc/nizho/phone-link` is created and contains the word `on`.
3. Settings starts the KDE Connect daemon (`kdeconnectd`) in your session. The helper
   `/usr/libexec/nizho-phone-link` does steps 1 and 2 only. It runs as root through polkit
   (action `org.nizho.phone-link`).

The rule applies to the zone, so it applies to every network this computer joins while the link is on.
Turning the link off removes the rule and the file and stops the daemon.

Nizho's privacy daemon (`nizhod`) checks about every minute. The design is that it removes the rule
when the file is missing and adds it back when the file says `on`. Whether it does both has to be
confirmed by the VM test; see the end of this page.

## Ports, from KDE's source

In the KDE Connect source (`core/backends/lan/lanlinkprovider.h`):
UDP 1716 for discovery, TCP from 1716 up to 1764 for connections. KDE's own page says the program
"uses dynamic ports in the range 1714-1764 for UDP and TCP"
(https://userbase.kde.org/KDEConnect). The firewalld service uses the wider range 1714 to 1764.
File transfers and file browsing use ports chosen at run time. The source does not name a fixed range
for them. A sshfs range of 1739 to 1764 was not found in any source we read.

## What another device on the network can see

Verified in the source (`lanlinkprovider.cpp`): the computer's discovery broadcast carries a device ID,
a protocol version and a TCP port. A device that is not paired can open a TCP connection to the
daemon. The daemon accepts a self-signed certificate from an unpaired device, so any device can reach
that point. It limits unpaired connections to 42, allows one connection per 500 milliseconds from the
same address, drops identity packets larger than 8192 bytes and closes a connection that sends no
identity within one second.

An unpaired device can also ask to pair. You then see a request on this computer and must accept it.

Not verified: exactly which fields the identity packet holds, such as the computer's name and device
type. Expect the name you gave this computer to be visible to the network.

## What a paired phone can do

Pairing is confirmed by you on both devices. KDE Connect uses TLS with a certificate per device. A
verification key derived from the certificates is meant to be compared on both screens. We have not
seen it on screen yet; the test checklist asks you to look.

These plugins are enabled by default on the computer side, read from the plugin files in the KDE
source: clipboard, notifications, remote commands (`runcommand`), file browsing (`sftp`), remote
mouse and keyboard (`mousepad`) and telephony. Whether each one does anything depends on the phone
app's own settings and Android permissions, which we did not check.

Two things from `shareplugin.cpp` that you should know. A file sent from the phone is saved to your
Downloads folder, and the code shows no accept step. A web or mail link sent from the phone is opened
in the default program straight away.

KDE Connect can run commands on the computer only if commands were configured. We did not check
whether a fresh install has any.

## Known security problems

- CVE-2025-66270, November 2025 (https://kde.org/info/security/advisory-20251128-1.txt). An attacker
  who knows the ID of an already paired device can pretend to be it and skip authentication. Affected:
  KDE Connect desktop 25.04 up to but not including 25.12, Android 1.33.0 up to but not including
  1.34.4, iOS 0.5.2 up to but not including 0.5.4. KDE's advice until fixed: stop KDE Connect on
  untrusted networks or unpair all devices.
- CVE-2025-32899, 32898, 32900 and 32901, April 2025
  (https://kde.org/info/security/advisory-20250418-1.txt and the three after it). These concern the
  Android app before 1.33.0: forged discovery packets can unpair devices, crash the app or change what
  it shows, and the device fingerprint is short enough to be brute-forced.
- CVE-2020-26164, a denial of service, fixed in 2020.

Nizho builds on Fedora 44. Fedora's package page lists `kde-connect` 26.04.x for Fedora 44
(https://packages.fedoraproject.org/pkgs/kde-connect/kdeconnectd/fedora-44-updates.html). That is newer
than 25.12, so the desktop side is past the fix for CVE-2025-66270. We read that from a web page and
did not check the package inside a built Nizho image. Run `rpm -q kde-connect` in the VM to confirm.
The phone app is a separate program. Update it from F-Droid or the Play Store.

## Public Wi-Fi

The rule is not tied to a network. In a cafe, an airport or any shared Wi-Fi, other people on the same
network can reach the KDE Connect ports while the link is on. The attack in CVE-2025-66270 needs the
attacker to send packets to your computer, so the local network is where it would be tried. KDE's own
firewall page tells firewalld users to avoid running KDE Connect in the `public` zone.

Advice: turn the phone link off on networks you do not trust, and unpair phones you no longer use.

Limiting the rule to trusted networks is possible in principle. NetworkManager has a per connection
setting `connection.zone`, and an unset value means the default zone
(https://networkmanager.dev/docs/api/latest/settings-connection.html). It would need a second
zone that has the service, and the home connection set to that zone. Not built, not tried. The
firewall check in `nizhod` expects only the `nizho` zone to be active, so a second zone would show as
"Needs attention" until that check is changed.

## Turning it on and off

In Settings, open Internet and use the phone link switch, or use the step in first-time setup. Turn it
on to pair, and off when you are done with it.

To see the rule:

```
sudo firewall-cmd --zone=nizho --list-services
```

With the link on the list contains `kdeconnect`. With it off, it does not. The file
`/etc/nizho/phone-link` exists only while the link is on. `sudo /usr/libexec/nizho-phone-link status`
prints `on` or `off`, and also `mismatch` with exit code 3 if the file and the firewall disagree.

## What was tested

Tested with stand-ins only: the helper against a fake firewall command, and the Settings screens
against a fake helper.

Not tested: a real phone, either platform; a booted image with the helper installed; the drift
handling in `nizhod` in both directions; connections to the opened ports from another machine; any
network other than the test VM; the iOS app.

To be run by the owner: `sudo bash scripts/vm-test-phone-link.sh` inside the VM, then the checklist in
`docs/PHONE-LINK-TEST.md` with real phones.
