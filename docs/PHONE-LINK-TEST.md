# Phone link: test with real phones

For the owner. Do this after `sudo bash scripts/vm-test-phone-link.sh` has run in the VM. Plain steps.
Write pass or fail on each line and put anything odd in the notes.

What you need: Nizho running (a VM with the network bridged to your Wi-Fi, or real hardware), a real
Android phone with the KDE Connect app (F-Droid or Play Store), a real iPhone with the KDE Connect app
from the App Store, both phones on the same Wi-Fi as Nizho. Write down the app versions first.

Android app version: ________  iPhone app version: ________  Nizho image: ________

What the iPhone app supports was not checked when this was written. Whatever it cannot do, mark it
"not offered" and do not count it as a failure.

Before starting, run `sudo firewall-cmd --zone=nizho --list-services` and `rpm -q kde-connect`.
Services should not list `kdeconnect`. Write the package version here: ____________

## Part 1: Android

| # | Step | Expected | Result | Notes |
|---|------|----------|--------|-------|
| 1 | Open the app on the phone. | The phone does not list the Nizho computer. | pass / fail | |
| 2 | In Nizho, Settings > Internet, turn the phone link on. | Switch shows on. Services list now contains `kdeconnect`. | pass / fail | |
| 3 | On the phone, pull down to refresh the device list. | The computer appears by name. | pass / fail | |
| 4 | Tap the computer on the phone, then Request pairing. | A pairing request appears on the computer. | pass / fail | |
| 5 | Compare the verification key on both screens before accepting. | The same short key shows on both. If no key shows, write that down. | pass / fail | |
| 6 | Accept on both. | Both show paired. | pass / fail | |
| 7 | Send a file from the phone to the computer. | It arrives in Downloads. Note whether the computer asked first. | pass / fail | |
| 8 | Send a file from the computer to the phone. | It arrives on the phone. | pass / fail | |
| 9 | Make the phone show a notification (send yourself a message). | It shows on the computer. | pass / fail | |
| 10 | In the computer's KDE Connect menu, choose Ring my phone. | The phone rings. Stop it on the phone. | pass / fail | |
| 11 | Copy text on the phone, paste on the computer. Then the other way. | The text matches both ways. | pass / fail | |
| 12 | On the phone, share a web link to the computer. | Note what happens. The link may open in the browser without a prompt. | pass / fail | |
| 13 | Unpair from the computer. | The phone shows unpaired. | pass / fail | |
| 14 | In Settings, turn the phone link off. | Switch shows off. Services list does not contain `kdeconnect`. | pass / fail | |
| 15 | On the phone, refresh the device list. | The computer no longer appears. Wait a minute and refresh again. | pass / fail | |

## Part 2: iPhone

Repeat steps 1 to 15 with the iPhone. Steps the app does not offer are "not offered".

| # | Step | Result | Notes |
|---|------|--------|-------|
| 1 to 3 | Phone link on, the computer appears in the app. | pass / fail | |
| 4 to 6 | Pair, compare the verification key, accept. | pass / fail | |
| 7 to 8 | Files both ways. | pass / fail / not offered | |
| 9 | Notification. | pass / fail / not offered | |
| 10 | Ring the phone. | pass / fail / not offered | |
| 11 | Clipboard. | pass / fail / not offered | |
| 13 to 15 | Unpair, link off, the phone no longer finds the computer. | pass / fail | |

## Part 3: after turning it off

| # | Step | Expected | Result | Notes |
|---|------|----------|--------|-------|
| 1 | Run `sudo /usr/libexec/nizho-phone-link status`. | Prints `off`. | pass / fail | |
| 2 | Run `ss -lunt` and look for ports 1714 to 1764. | Nothing listens. | pass / fail | |
| 3 | Restart the computer and check the same. | Still off, nothing listens. | pass / fail | |
| 4 | Join a different Wi-Fi network, with the link on, and look at the services list. | Write what you see. The rule applies to every network. | pass / fail | |

Overall: ________________________________________
