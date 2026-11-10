# Family and parental controls

Status: research and a plan. Nothing here was run on Nizho. No image was built or booted for it, and no child account was tested. Every claim marked "verified" was read from the source linked next to it, not observed on Nizho. Nizho does not claim to make a computer safe for children.

## What we want to offer, and who it is for

A parent or carer who installs Nizho for a child, on a computer the adult controls. The goals are modest: the child has no admin rights, sees apps that suit their age, and the adult can see what is set. Nothing leaves the computer. No account, no cloud, no reports sent anywhere.

It is not for a determined teenager with physical access to the machine. See the table below.

## What is possible today with no new code

1. Create the child as a standard account. Nizho's own setup creates the first user in the `wheel` group (verified in `system/nizho-oobe-apply`). A second user created without `wheel` is a standard account. In System Settings, Users, the account type should offer Standard or Administrator. We did not verify that Plasma 6 shows it, so check it. The command line works too: `useradd -m -s /bin/bash NAME`, then `passwd NAME`, run by an adult in a root shell. `family/nizho-family plan` prints these lines.
2. Installing apps system wide goes through Archon and asks for an administrator password (verified: `archon-service/org.nizho.archon.policy`, `auth_admin`). A standard user can still install Flatpak apps for their own account with `flatpak install --user`, and Capsule lists per-user apps (verified in `nizho-capsule` source). Only the malcontent setting `--disallow-user-installation` is documented to stop that (man page below). Whether it works on Nizho is untested.
3. Firefox policies. Mozilla documents `DisablePrivateBrowsing`, `BlockAboutConfig` and a lockable `DNSOverHTTPS` policy ([policy reference](https://mozilla.github.io/enterprise-admin-reference/reference/policies/)). Nizho ships `the image` (source: `system/firefox-policies.json`). Policies apply to every user of the image, so the adult would get them too. They are not per user. A user report says the DNSOverHTTPS lock needs checking on the actual build.
4. The firewall. The Nizho zone rejects incoming connections only (`system/firewalld/nizho.xml`). It does not limit what a child's apps reach. It is not a parental control.

## Recommended design in three phases

Phase 1: a documented set-up. A standard account, plus malcontent's app filter if the packages are added to the image. The filter is a list of maximum content ratings per category (OARS) and an optional block list of programs and Flatpak refs, stored by AccountsService. Flatpak refuses to run blocked apps ([Flatpak docs](https://docs.flatpak.org/en/latest/flatpak-run.html), the `flatpak-run` page states it), and GNOME Software hides them (via its [malcontent plugin](https://gitlab.gnome.org/GNOME/gnome-software/-/merge_requests/190)). Adding the packages is a Containerfile change, which this branch does not make.

Phase 2: session time limits. malcontent 0.14.0 stores a daily limit or a daily schedule per user (`malcontent-client set-session-limits`, [man page](https://man.archlinux.org/man/malcontent-client.8.en)). GNOME Shell does the enforcing and locks the session ([GNOME blog](https://blogs.gnome.org/ignapk/2025/11/)). We found no Plasma component that reads these limits, so on Nizho nothing would enforce them. This phase needs a small Nizho-owned enforcer, for example a user service that reads the stored limit and locks the screen. That enforcer does not exist. Until it does, we say time limits are not available.

Phase 3: out of scope. Anything that needs a cloud account or a parent's phone: remote control, location, activity reports sent to a parent, purchase approval through a service. Nizho is offline first and has no account service.

DNS filtering is a separate option, not tied to a phase. Nizho sets one encrypted resolver for the whole computer (Quad9, `9.9.9.9`, name `dns.quad9.net`, `nizhod/src/config.rs` and `enforcers/dns.rs`). systemd-resolved scopes servers by network link, global setting and routing domain, and its manual describes no per-user setting ([man page](https://www.freedesktop.org/software/systemd/man/latest/systemd-resolved.service.html)). So a filtered resolver applies to every user of the computer, or to nobody. An adult who wants it can change the nizhod DNS server and name in `/etc/nizhod/nizhod.toml`. Options, from their own sites:

| Service | Filter | Address | DoT name |
|---|---|---|---|
| [Quad9](https://quad9.net/service/service-addresses-and-features/) | malware only, no adult filter listed | 9.9.9.9, 149.112.112.112 | dns.quad9.net |
| [CleanBrowsing](https://cleanbrowsing.org/filters) | Family (adult, proxies and VPN, SafeSearch) | 185.228.168.168, 185.228.169.168 | family-filter-dns.cleanbrowsing.org |
| CleanBrowsing | Adult | 185.228.168.10, 185.228.169.11 | adult-filter-dns.cleanbrowsing.org |
| [Cloudflare](https://developers.cloudflare.com/1.1.1.1/setup/) | malware | 1.1.1.2, 1.0.0.2 | security.cloudflare-dns.com |
| Cloudflare | malware and adult | 1.1.1.3, 1.0.0.3 | family.cloudflare-dns.com |

CleanBrowsing's free tier is throttled. A child can bypass any of these by using a browser with its own DNS setting, a VPN, or another network.

## What each phase can and cannot stop

"Stops" means the design intends it. Most rows are our reasoning, not a test.

| Route | Phase 1 (standard account, app filter) | Phase 2 (time limit, needs new code) |
|---|---|---|
| Child gains admin rights through the UI | Stops, while the admin password stays secret | Same |
| Live USB stick | Does not stop | Does not stop |
| Boot menu options such as editing the kernel line | Does not stop. A firmware password and a GRUB password would help. Not set up by Nizho | Same |
| Offline access to the disk | Disk encryption stops it if the passphrase is kept from the child. The installer is meant to encrypt by default, which I did not check in this branch | Same |
| Second user account | Needs admin rights to create. Stops | Same |
| Changing the clock | Not relevant | Stops only if the clock needs admin rights to change. Untested |
| Firefox private mode | Stops only with the Firefox policy, and it applies to all users | Same |
| Flatpak from another remote or `--user` install | Stops only with `--disallow-user-installation`. Untested | Same |
| Native apps outside Flatpak | The app filter matches by path, and only programs that ask malcontent honour it. The Plasma launcher, Discover and Capsule were not found to ask | Same |
| VPN or a different DNS | Does not stop | Does not stop |
| Another device, or a phone | Out of scope | Out of scope |
| Reading the stored limit and editing it | A standard user may not change it (polkit action in the malcontent package) | Same |

A read-only image, disk encryption and a firmware password help, and they are design facts, not parental features. They also protect the adult from the child changing the system.

## malcontent packages (Fedora 44 and rawhide)

Versions 0.14.0 on [packages.fedoraproject.org](https://packages.fedoraproject.org/pkgs/malcontent/malcontent/fedora-44.html). Sizes are installed sizes from [mdapi](https://mdapi.fedoraproject.org/rawhide/pkg/malcontent), rawhide builds of the same version, so the Fedora 44 build may differ slightly.

| Package | Installed size | What is in it |
|---|---|---|
| malcontent | 1.1 MB | timerd, webd, polkit rules, D-Bus services (file list from [mdapi](https://mdapi.fedoraproject.org/rawhide/files/malcontent)) |
| malcontent-libs | 0.16 MB | libmalcontent, needs libaccountsservice |
| malcontent-tools | 0.06 MB | `malcontent-client`, needs Python 3 |
| malcontent-control | 0.27 MB | GTK 4 and libadwaita admin app |

Licence: the Fedora pages ship `COPYING` files but do not show the licence string, and I did not confirm it from upstream. Check before shipping.

Facts from the man page: `set-app-filter` replaces the whole filter and accepts programs, Flatpak refs, content types and `section=value` pairs. `set-session-limits` supports `none`, `daily-schedule` and `daily-limit`. There is also `set-web-filter` with block and allow lists. The man page says the command line and output are unstable. `malcontent-webd` is a web filter service; we did not find which browsers use it, so we make no web filter claim.

KDE has no integration. A KDE forum thread says so ([discuss.kde.org](https://discuss.kde.org/t/parental-control-application/957)) and links the old bug 169225 for a parental controls KCM. KDE's Kiosk framework locks settings with `[$i]`, but its own documentation says URL rules affect only KDE applications, not other browsers ([KDE docs](https://develop.kde.org/docs/administration/kiosk/introduction/)).

## Other platforms, for comparison

Microsoft Family Safety limits screen time on Windows, Xbox and Android and filters web and search in Edge, blocking other browsers ([Microsoft](https://www.microsoft.com/en-us/microsoft-365/family-safety)). Apple Screen Time needs Family Sharing and an Apple Account for the child ([Apple](https://support.apple.com/en-us/105121)). Google Family Link needs a Google Account and adds location and app approval ([Google](https://families.google/familylink/)). All three depend on a cloud account. Nizho will not copy that part.

## Open questions and what to test on a real image

1. Does Fedora 44's Flatpak build enforce the malcontent filter? Test a blocked app with `flatpak run` as the child.
2. Do the packages install on the bootc image and start the D-Bus and AccountsService parts? Is AccountsService present?
3. What does `set-app-filter` mean by `section=value`: a maximum? Check with `malcontent-client check-app-filter`.
4. Does Plasma's launcher, Discover or Capsule hide or run a blocked app?
5. Does System Settings, Users offer a Standard type, and does the child's session stay without admin pages?
6. Does `--disallow-user-installation` stop `flatpak install --user`?
7. Can a standard user change the clock or the DNS?
8. Which Firefox policies work in the sandboxed wrapper?
9. Try each row of the bypass table with a real child account.

## Decisions for the owner

Add the malcontent packages to the image, or ship only the documented standard account? Build a Nizho time-limit enforcer, or leave time limits out? Offer the DNS filter as a setting? Agree the wording: "age-based app filter on a standard account", never "safe for children".
