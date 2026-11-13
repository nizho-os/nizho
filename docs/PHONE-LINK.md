# Phone link (KDE Connect)

Status: tested in a VM on 2026-11-13 (see the end of this page). Nothing has been tried with a real
phone yet; `docs/PHONE-LINK-TEST.md` is the checklist for that.

## What it is

The phone link lets a phone running the KDE Connect app talk to this computer over the local network.
It can send files, show phone notifications, share the clipboard and ring the phone.

It is off by default, and when on it works only on networks you marked as home (Settings, Internet,
"Home networks"). You turn it on when you want it.

## What turning it on changes

1. The firewall zone `nizho-home` gets the firewalld service `kdeconnect`, in the running firewall and
   in the saved configuration. `nizho-home` is the zone of networks marked as home; every other network
   is in the zone `nizho`, which never gets the service. firewalld defines that service as TCP and UDP ports 1714 to 1764
   (https://github.com/firewalld/firewalld/blob/main/config/services/kdeconnect.xml).
2. The file `/etc/nizho/phone-link` is created and contains the word `on`.
3. Settings starts the KDE Connect daemon (`kdeconnectd`) in your session. The helper
   `/usr/libexec/nizho-phone-link` does steps 1 and 2 only. It runs as root through polkit
   (action `org.nizho.phone-link`).

The rule applies to the home zone only. Turning the link off removes the rule and the file and stops
the daemon.

Nizho's privacy daemon (`nizhod`) checks about every minute. It removes the rule from `nizho` always, removes
it from `nizho-home` when the file is missing, and adds it back to `nizho-home` when the file says `on`.
A machine that had the rule in `nizho` from an older Nizho gets it moved to `nizho-home` on the first
check.

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

Networks you did not mark as home, such as a café, an airport or work Wi-Fi, are in the zone `nizho`.
There the KDE Connect ports stay closed while the link is on, so other people on that network cannot
connect or ask to pair. The attack in CVE-2025-66270 needs the attacker to send packets to your
computer, and those packets are rejected on such networks.

The computer also stays quiet there: a firewalld policy, `nizho-away-quiet`, drops the discovery
packets KDE Connect sends to UDP port 1716 on networks in the zone `nizho`. Not covered: KDE
Connect 26.08, the version in the image, also announces itself by mDNS (port 5353), and that is not
blocked. KDE Connect has no setting to turn its mDNS off, and dropping port 5353 would also stop
finding printers and other local devices on those networks. While the link is on, devices on any
network can therefore see that this computer runs KDE Connect, though they cannot connect to it.

On a home network everyone on it can reach the ports. Mark a network as home only if you trust
everyone on it.

## Turning it on and off

In Settings, open Internet and use the phone link switch, or use the step in first-time setup. Turn it
on to pair, and off when you are done with it.

To see the rule:

```
sudo firewall-cmd --zone=nizho-home --list-services
sudo firewall-cmd --get-active-zones
```

With the link on the first list contains `kdeconnect`. With it off, it does not. The file
`/etc/nizho/phone-link` exists only while the link is on. `sudo /usr/libexec/nizho-phone-link status`
prints `on` or `off`, and also `mismatch` with exit code 3 if the file and the firewall disagree.

## What was tested

In a VM booted from a built image (2026-11-13, image `candidate-0500b2b`), one wired connection:

- Marking it as home moved it to the zone `nizho-home` without dropping the connection; unmarking
  moved it back to `nizho`.
- Turning the link on in Settings showed the warning, asked for the administrator password, put
  `kdeconnect` in `nizho-home` only and started KDE Connect.
- nizhod moved a `kdeconnect` rule put into `nizho` by hand to `nizho-home`, and removed it from
  `nizho-home` when the file `/etc/nizho/phone-link` was missing.
- Captured on the network interface: on the home network KDE Connect's broadcast to UDP 1716 went out;
  on the same connection unmarked, it did not. Its mDNS announcement went out in both cases.
- The top-bar pill and quick-settings tile showed the new wording.

Not tested: a real phone, either platform; pairing; connections to the opened ports from another
machine; Wi-Fi (the VM has none); the iOS app.

To be run by the owner: the checklist in `docs/PHONE-LINK-TEST.md` with real phones.
