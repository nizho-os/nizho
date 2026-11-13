ARG FEDORA_VERSION=44
# Stage 1: build nizhod as a static musl binary
FROM docker.io/library/rust:alpine AS builder
RUN apk add --no-cache musl-dev
COPY nizhod/ /build/
COPY nizho-shell/nizho-shell-common/ /nizho-shell/nizho-shell-common/
WORKDIR /build
RUN cargo build --locked --release --target x86_64-unknown-linux-musl

# nizho-pulsed: the reliability daemon, also a static musl binary (its D-Bus
# surface uses the same zbus stack as nizhod). Built after nizhod so a change to
# one does not invalidate the other's layer. --features dbus turns on the
# optional daemon; the core stays zero-dep without it.
COPY nizho-pulse/ /nizho-pulse/
RUN cargo build --locked --release --target x86_64-unknown-linux-musl \
      --features dbus --manifest-path /nizho-pulse/Cargo.toml

# Stage 2: build the nizho-shell GTK4 apps (glibc required, not musl)
FROM docker.io/library/fedora:${FEDORA_VERSION} AS shell-builder
RUN --mount=type=cache,target=/var/cache/libdnf5,sharing=locked dnf install --setopt=keepcache=True -y -q gtk4-devel gtk4-layer-shell-devel libadwaita-devel rust cargo gcc pkgconf-pkg-config
COPY nizho-shell/ /build/
WORKDIR /build
RUN cargo build --locked --release

# Archon remains an independent source project, checked out at an exact revision.
FROM docker.io/library/fedora:${FEDORA_VERSION} AS archon-builder
RUN dnf install -y rust cargo gcc git pkgconf-pkg-config
COPY integrations/archon/source-tag integrations/archon/source-revision integrations/archon/flathub.flatpakrepo integrations/archon/flathub.sha256 /integration/
RUN cd /integration && sha256sum -c flathub.sha256 && \
    git init /archon && cd /archon && \
    git remote add origin https://github.com/nizho-os/archon.git && \
    git fetch --depth 1 origin "refs/tags/$(cat /integration/source-tag)" && \
    git checkout --detach FETCH_HEAD && \
    test "$(git rev-parse HEAD)" = "$(cat /integration/source-revision)" && \
    cargo build --locked --release -p archon-cli --bin archon
COPY archon-service/ /service/
RUN mkdir -p /integrations/archon && ln -s /archon /integrations/archon/source
RUN cargo build --locked --release --manifest-path /service/Cargo.toml

# Stage 2b: the dock. Fedora's crystal-dock source package rebuilt with
# crystal-dock/nizho-look.patch (blurred background, label bubble, divider
# before Trash) and nizho-per-user.patch (one dock per account, not per
# computer).
FROM docker.io/library/fedora:${FEDORA_VERSION} AS dock-builder
RUN --mount=type=cache,target=/var/cache/libdnf5,sharing=locked dnf install --setopt=keepcache=True -y -q rpm-build 'dnf-command(builddep)' 'dnf-command(download)'
COPY desktop/crystal-dock/nizho-look.patch desktop/crystal-dock/nizho-per-user.patch desktop/crystal-dock/build-rpm.sh /build/
RUN --mount=type=cache,target=/var/cache/libdnf5,sharing=locked sh /build/build-rpm.sh

# Stage 2c: the format wheel (convert/wheel), a small Qt Quick program that
# waits in the session and shows a ring of formats at the cursor.
FROM docker.io/library/fedora:${FEDORA_VERSION} AS wheel-builder
RUN --mount=type=cache,target=/var/cache/libdnf5,sharing=locked dnf install --setopt=keepcache=True -y -q cmake gcc-c++ extra-cmake-modules qt6-qtbase-devel qt6-qtdeclarative-devel kf6-kglobalaccel-devel kf6-kwindowsystem-devel layer-shell-qt-devel
COPY convert/wheel/ /build/
RUN cmake -S /build -B /build/out -DCMAKE_BUILD_TYPE=Release && cmake --build /build/out -j"$(nproc)"
# The edit window (convert/edit): crop, black out, annotate, background, trim, split, beep.
COPY convert/edit/ /build-edit/
RUN cmake -S /build-edit -B /build-edit/out -DCMAKE_BUILD_TYPE=Release && cmake --build /build-edit/out -j"$(nproc)"

# Stage 2d: VARA, version control (github.com/thulasiramk-2310/vara), built
# from its v0.4.4 release, with the web files of its Hub. The checkout must be
# exactly the release commit, so a moved tag fails the build instead of
# changing what ships. Pure Go, static.
FROM docker.io/library/golang:alpine AS vara-builder
RUN apk add --no-cache git
ARG VARA_TAG=v0.4.4
ARG VARA_COMMIT=33098ed279a8bb731a049f2512f0ef04836058fb
RUN git clone --quiet --depth 1 --branch "$VARA_TAG" https://github.com/thulasiramk-2310/vara /src && \
    test "$(git -C /src rev-parse HEAD)" = "$VARA_COMMIT" && \
    cd /src && CGO_ENABLED=0 go build -trimpath -ldflags "-s -w -X main.version=${VARA_TAG#v}" -o /out/vara ./cmd/vara && \
    cp -r /src/web /out/hub && rm -f /out/hub/README.md

# Staging stages for the small, often-edited files and the built binaries,
# laid out at their final paths. Each COPY into the 11 GB bootc image costs
# about a minute and a half (the layer diff walks the whole image), so the
# final stage takes each tree in ONE step instead of ~35. COPY into an empty
# stage is near-instant.
# Stage 2f: the boot menu theme (fonts need grub2-mkfont, images need ImageMagick; neither ships).
FROM docker.io/library/fedora:${FEDORA_VERSION} AS grub-theme-builder
RUN dnf install -y -q --setopt=retries=25 grub2-tools-extra rsms-inter-fonts ImageMagick fontconfig
COPY desktop/grub-theme-nizho/ /src/
RUN sh /src/make-theme.sh "" /out && ls /out/theme.txt /out/sans-18.pf2 /out/select_c.png

FROM scratch AS files
COPY --from=archon-builder /archon/target/release/archon /usr/bin/archon
COPY --from=archon-builder /service/target/release/nizho-archon-service /usr/libexec/nizho-archon-service
COPY archon-service/archon-service.service /usr/lib/systemd/system/archon-service.service
COPY archon-service/org.nizho.Archon1.conf /usr/share/dbus-1/system.d/org.nizho.Archon1.conf
COPY archon-service/org.nizho.Archon1.service /usr/share/dbus-1/system-services/org.nizho.Archon1.service
COPY archon-service/org.nizho.archon.policy /usr/share/polkit-1/actions/org.nizho.archon.policy
COPY archon-service/nizho-archon.conf /usr/lib/tmpfiles.d/nizho-archon.conf
COPY integrations/archon/source-revision /usr/share/nizho/archon-source-revision
COPY integrations/archon/source-tag /usr/share/nizho/archon-source-tag
COPY integrations/archon/flathub.flatpakrepo /usr/share/nizho/flathub.flatpakrepo
COPY system/firewalld/nizho.xml /usr/lib/firewalld/zones/nizho.xml
COPY system/kernel/10-nizho-hardening.toml /usr/lib/bootc/kargs.d/10-nizho-hardening.toml
COPY system/kernel/60-nizho-hardening.conf /usr/lib/sysctl.d/60-nizho-hardening.conf
COPY system/kernel/20-nizho-recovery.toml /usr/lib/bootc/kargs.d/20-nizho-recovery.toml
COPY system/nizho-arm-boot-counter /usr/libexec/nizho-arm-boot-counter
COPY system/nizho-phone-link /usr/libexec/nizho-phone-link
COPY system/polkit/org.nizho.phone-link.policy /usr/share/polkit-1/actions/org.nizho.phone-link.policy
COPY system/nizho-stale-trigger-check /usr/libexec/nizho-stale-trigger-check
COPY system/autostart/nizho-privacy-dots.desktop /etc/xdg/autostart/nizho-privacy-dots.desktop
COPY desktop/crystal-dock/nizho-dock.desktop /etc/xdg/autostart/nizho-dock.desktop
COPY desktop/crystal-dock/nizho-dock-start /usr/libexec/nizho-dock-start
# Meta+D: KDE's own Show Desktop hides the dock too, so a small KWin script minimizes the windows instead,
# and a login step hands it the key.
COPY desktop/kwin-scripts/nizho-showdesktop /usr/share/kwin/scripts/nizho-showdesktop
COPY desktop/kwin-scripts/nizho-showdesktop-keys /usr/libexec/nizho-showdesktop-keys
COPY system/autostart/nizho-showdesktop-keys.desktop /etc/xdg/autostart/nizho-showdesktop-keys.desktop
# A display scale once for each new screen from its size in the EDID, for screens KWin left at 100%
# (system/nizho-display-scale). Runs at every login, acts once per screen, never blocks login.
COPY system/nizho-display-scale /usr/libexec/nizho-display-scale
COPY system/autostart/nizho-display-scale.desktop /etc/xdg/autostart/nizho-display-scale.desktop
COPY desktop/crystal-dock/panel_1.conf /etc/skel/.config/crystal-dock/KDE/panel_1.conf
COPY desktop/crystal-dock/appearance.conf /etc/skel/.config/crystal-dock/KDE/appearance.conf
COPY desktop/crystal-dock/panel_1.conf /etc/skel/.config/crystal-dock/generic/panel_1.conf
COPY desktop/crystal-dock/appearance.conf /etc/skel/.config/crystal-dock/generic/appearance.conf
COPY system/nizho-rtc-mode /usr/libexec/nizho-rtc-mode
COPY system/systemd/nizho-rtc-mode.service /usr/lib/systemd/system/nizho-rtc-mode.service
COPY system/nizho-hide-system-image /usr/libexec/nizho-hide-system-image
COPY system/systemd/nizho-hide-system-image.service /usr/lib/systemd/system/nizho-hide-system-image.service
# Each boot: warn (file in /run for the Updates app, and the journal) when this
# computer follows an image other than ghcr.io/nizho-os/nizho, for example
# because the installer's `bootc switch` step failed. Reports only.
COPY system/nizho-image-ref-check /usr/libexec/nizho-image-ref-check
COPY system/systemd/nizho-image-ref-check.service /usr/lib/systemd/system/nizho-image-ref-check.service
COPY system/nizho-dns-after-clock /usr/libexec/nizho-dns-after-clock
COPY system/systemd/nizho-dns-after-clock.service /usr/lib/systemd/system/nizho-dns-after-clock.service
COPY system/tmpfiles/nizho-nizhosetup.conf /usr/lib/tmpfiles.d/nizho-nizhosetup.conf
COPY system/systemd/greenboot-healthcheck.service.d/10-nizho-stale-trigger.conf /usr/lib/systemd/system/greenboot-healthcheck.service.d/10-nizho-stale-trigger.conf
COPY system/systemd/greenboot-set-rollback-trigger.service.d/10-nizho-stop-at-shutdown.conf /usr/lib/systemd/system/greenboot-set-rollback-trigger.service.d/10-nizho-stop-at-shutdown.conf
# Nizho's own greenboot checks. required.d: the login screen and the privacy daemon
# run (a failure counts toward going back to the previous version). wanted.d: a
# display, NetworkManager and firewalld (reported only). greenboot 0.16.4 reads
# /usr/lib/greenboot and /etc/greenboot; /usr/lib is used so the checks always
# match the image. See docs/RECOVERY.md and scripts/test-greenboot-checks.py.
COPY system/greenboot/check/ /usr/lib/greenboot/check/
COPY system/greenboot/nizho-greenboot-unit-check /usr/libexec/nizho-greenboot-unit-check
COPY system/nizho-apply-look /usr/libexec/nizho-apply-look
COPY convert/nizho-convert /usr/bin/nizho-convert
COPY apps/nizho-install /usr/bin/nizho
COPY apps/apps.json /usr/share/nizho/apps.json
# nizho-backup: encrypted backups of your files, to a drive you choose. nizho-import brings
# personal files in from a Windows or macOS drive. nizho-hwreport prints a privacy-first
# hardware report for testers. None of them send anything over the network. This stage is
# FROM scratch, so it can only hold COPY lines; restic and the timer are set up in the final stage.
COPY backup/nizho-backup /usr/bin/nizho-backup
COPY system/systemd/nizho-backup.service /usr/lib/systemd/user/nizho-backup.service
COPY system/systemd/nizho-backup.timer /usr/lib/systemd/user/nizho-backup.timer
COPY import/nizho-import /usr/bin/nizho-import
COPY hardware/nizho-hwreport /usr/bin/nizho-hwreport
COPY convert/nizho-convert-image.desktop /usr/share/kio/servicemenus/nizho-convert-image.desktop
COPY convert/nizho-convert-pdf.desktop /usr/share/kio/servicemenus/nizho-convert-pdf.desktop
COPY convert/nizho-convert-office.desktop /usr/share/kio/servicemenus/nizho-convert-office.desktop
COPY convert/nizho-convert-text.desktop /usr/share/kio/servicemenus/nizho-convert-text.desktop
COPY convert/nizho-smaller.desktop /usr/share/kio/servicemenus/nizho-smaller.desktop
COPY convert/nizho-zip.desktop /usr/share/kio/servicemenus/nizho-zip.desktop
COPY convert/nizho-convert-audio.desktop /usr/share/kio/servicemenus/nizho-convert-audio.desktop
COPY convert/nizho-convert-video.desktop /usr/share/kio/servicemenus/nizho-convert-video.desktop
COPY convert/nizho-wheel-servicemenu.desktop /usr/share/kio/servicemenus/nizho-wheel.desktop
COPY convert/nizho-clean.desktop /usr/share/kio/servicemenus/nizho-clean.desktop
COPY convert/nizho-edit-image.desktop /usr/share/kio/servicemenus/nizho-edit-image.desktop
COPY convert/nizho-edit-pdf.desktop /usr/share/kio/servicemenus/nizho-edit-pdf.desktop
COPY convert/nizho-edit-pdf-merge.desktop /usr/share/kio/servicemenus/nizho-edit-pdf-merge.desktop
COPY convert/nizho-edit-media.desktop /usr/share/kio/servicemenus/nizho-edit-media.desktop
COPY convert/nizho-edit-video.desktop /usr/share/kio/servicemenus/nizho-edit-video.desktop
COPY convert/nizho-edit-media-join.desktop /usr/share/kio/servicemenus/nizho-edit-media-join.desktop
COPY convert/org.nizho.edit.desktop /usr/share/applications/org.nizho.edit.desktop
COPY convert/org.nizho.wheel.desktop /usr/share/applications/org.nizho.wheel.desktop
COPY convert/nizho-wheel-autostart.desktop /etc/xdg/autostart/nizho-wheel.desktop
COPY system/systemd/vara-hub.service /usr/lib/systemd/user/vara-hub.service
COPY desktop/plasma-style/panel /usr/share/plasma/desktoptheme/nizho-dark
COPY desktop/plasma-style/panel /usr/share/plasma/desktoptheme/nizho-light
COPY nizhod/org.nizho.nizhod.policy /usr/share/polkit-1/actions/org.nizho.nizhod.policy
COPY system/nizho-firefox /usr/bin/firefox-sandbox
# Fedora's own Firefox launcher (rpms/firefox f44, org.mozilla.firefox.desktop)
# with one more action, "New private session, nothing saved". It replaces the
# packaged copy because the files stage is copied in after Firefox is installed.
COPY system/org.mozilla.firefox.desktop /usr/share/applications/org.mozilla.firefox.desktop
COPY nizhod/nizhod.service /usr/lib/systemd/system/nizhod.service
COPY nizhod/nizhod.toml /etc/nizhod/nizhod.toml
COPY nizhod/org.nizho.nizhod.conf /etc/dbus-1/system.d/org.nizho.nizhod.conf
COPY nizhod/org.nizho.nizhod.service /usr/share/dbus-1/system-services/org.nizho.nizhod.service
COPY nizho-pulse/nizho-pulsed.service /usr/lib/systemd/system/nizho-pulsed.service
COPY nizho-pulse/org.nizho.pulse.conf /etc/dbus-1/system.d/org.nizho.pulse.conf
COPY nizho-pulse/org.nizho.pulse.service /usr/share/dbus-1/system-services/org.nizho.pulse.service
COPY docs/RELIABILITY-NIZHO-PULSE.md /usr/share/doc/nizho/RELIABILITY-NIZHO-PULSE.md
COPY system/firefox-policies.json /usr/lib64/firefox/distribution/policies.json

FROM scratch AS bins
COPY --from=builder /build/target/x86_64-unknown-linux-musl/release/nizhod /usr/bin/nizhod
COPY --from=builder /nizho-pulse/target/x86_64-unknown-linux-musl/release/nizho-pulsed /usr/bin/nizho-pulsed
COPY --from=shell-builder /build/target/release/nizho-dashboard /usr/bin/nizho-dashboard
COPY --from=shell-builder /build/target/release/nizho-wifi      /usr/bin/nizho-wifi
COPY --from=shell-builder /build/target/release/nizho-update    /usr/bin/nizho-update
COPY --from=shell-builder /build/target/release/nizho-hotkeys   /usr/bin/nizho-hotkeys
COPY --from=shell-builder /build/target/release/nizho-capsule   /usr/bin/nizho-capsule
COPY --from=shell-builder /build/target/release/nizho-perms    /usr/bin/nizho-perms
COPY --from=shell-builder /build/target/release/nizho-settings /usr/bin/nizho-settings-gtk
COPY --from=shell-builder /build/target/release/nizho-welcome  /usr/bin/nizho-welcome
COPY --from=shell-builder /build/target/release/nizho-sound      /usr/bin/nizho-sound
COPY --from=shell-builder /build/target/release/nizho-bluetooth  /usr/bin/nizho-bluetooth
COPY --from=shell-builder /build/target/release/nizho-privacy-tray /usr/bin/nizho-privacy-tray
COPY --from=shell-builder /build/target/release/nizho-oobe /usr/bin/nizho-oobe
COPY --from=shell-builder /build/target/release/nizho-privacy-dots /usr/bin/nizho-privacy-dots
COPY --from=wheel-builder /build/out/nizho-wheel /usr/bin/nizho-wheel
COPY --from=wheel-builder /build-edit/out/nizho-edit /usr/bin/nizho-edit
COPY --from=vara-builder /out/vara /usr/bin/vara
COPY --from=vara-builder /out/hub /usr/share/vara/hub

# Stage 3: bootc image
FROM quay.io/fedora/fedora-bootc:${FEDORA_VERSION}

RUN mkdir -p /etc/NetworkManager/conf.d && \
    printf '[device]\nwifi.scan-rand-mac-address=yes\n\n[connection]\nwifi.cloned-mac-address=random\nethernet.cloned-mac-address=random\n' \
      > /etc/NetworkManager/conf.d/00-nizho-mac-random.conf

RUN mkdir -p /usr/lib/bootc/install && \
    printf '[install.filesystem.root]\ntype = "xfs"\n' \
      > /usr/lib/bootc/install/00-defaults.toml

# Phase 3a: UKI toolchain + systemd-boot EFI binary
RUN --mount=type=cache,target=/var/cache/libdnf5,sharing=locked dnf install --setopt=keepcache=True -y systemd-ukify systemd-boot-unsigned binutils

# Switch kernel-install to UKI layout
RUN printf 'layout=uki\nuki_generator=ukify\n' > /etc/kernel/install.conf

# Phase 4: bubblewrap-sandboxed Firefox
RUN --mount=type=cache,target=/var/cache/libdnf5,sharing=locked dnf install --setopt=keepcache=True -y firefox bubblewrap

# Graphical session: full KDE Plasma spin + SDDM login manager.
# Plasma provides the panel, launcher, settings, file manager, window
# management and login — replacing the custom Hyprland shell. The Nizho
# privacy apps (dashboard, capsule, settings) run as regular Plasma apps.
# rootfiles is excluded: it ships /root dotfiles that already exist in the
# bootc base, so its cpio unpack fails the whole transaction. It's cosmetic.
# retries=25 + fastestmirror ride out the transient Fedora mirror hiccups that
# otherwise fail this 1500-package transaction ("All mirrors were tried").
RUN --mount=type=cache,target=/var/cache/libdnf5,sharing=locked dnf install --setopt=keepcache=True -y --setopt=retries=25 --setopt=fastestmirror=True \
    --setopt=max_parallel_downloads=10 --exclude=rootfiles \
    @kde-desktop-environment sddm \
    pipewire wireplumber pipewire-pulseaudio xorg-x11-server-Xwayland \
    libadwaita flatpak flatpak-libs python3-gobject xdg-user-dirs brightnessctl \
    bluez bluez-tools zram-generator-defaults

# Enable SDDM (started once firstboot flips the default to graphical.target).
# Default to the Plasma Wayland session.
# Fedora 44's KDE install makes Plasma Login Manager (plasmalogin) the display
# manager. Nizho keeps SDDM for now (its login theme is an SDDM theme), so
# --force repoints display-manager.service at sddm.
RUN systemctl enable --force sddm.service && \
    mkdir -p /etc/sddm.conf.d && \
    printf '[General]\nDisplayServer=wayland\n\n[Wayland]\nSessionDir=/usr/share/wayland-sessions\n' \
      > /etc/sddm.conf.d/10-wayland.conf





# Firefox managed privacy policy: Enhanced Tracking Protection (strict) with
# cryptomining + fingerprinting blocking. Placed in Firefox's app-dir under /usr
# (which the bwrap sandbox binds read-only) — NOT /etc/firefox, which the sandbox
# deliberately does not expose. Backs the dashboard's "Blocks fingerprinting
# scripts" claim; verify at runtime via about:policies. Copied in the late
# block below so editing it doesn't bust the KDE cache.

# skel/Pictures for screenshots. Plasma shows the notifications.
RUN mkdir -p /etc/skel/Pictures

# Preserve the distribution's password-quality policy for installed accounts.

# Timezone: Asia/Kolkata (UTC+5:30)
RUN ln -sf /usr/share/zoneinfo/Asia/Kolkata /etc/localtime && \
    echo "Asia/Kolkata" > /etc/timezone

# Silence console: kernel printk level=1 (EMERG+ALERT only) + no systemd status lines
RUN mkdir -p /etc/sysctl.d /etc/systemd/system.conf.d && \
    printf 'kernel.printk = 1 4 1 7\n' \
      > /etc/sysctl.d/00-nizho-quiet.conf && \
    printf '[Manager]\nShowStatus=no\n' \
      > /etc/systemd/system.conf.d/00-nizho-quiet.conf

# First-boot setup wizard
COPY system/nizho-firstboot         /usr/libexec/nizho-firstboot
COPY system/nizho-firstboot.service /usr/lib/systemd/system/nizho-firstboot.service
RUN chmod 755 /usr/libexec/nizho-firstboot

# First boot must land on the CONSOLE (multi-user.target) so the first-run flow
# runs and creates the account BEFORE any display manager. The wizard then flips
# the default to graphical.target and reboots into SDDM. Without this the image
# defaults to graphical.target and SDDM shows an empty, unusable login on boot 1.
RUN systemctl set-default multi-user.target

# ── Graphical first-run wizard (OOBE) ────────────────────────────────────────
# A Wayland compositor cannot get a seat as a pre-login *system* service, so the
# wizard runs inside a real logind LOGIN SESSION — exactly how DP1's sway worked.
# A dedicated `nizhosetup` user auto-logs in on tty1 (first boot only), logind
# grants it seat0 (DRM+input), and its login shell launches cage + the nizho-oobe
# GTK wizard. nizho-oobe runs unprivileged and creates the account only through a
# tight NOPASSWD sudo helper. If cage can't start, it falls back to the TUI.
RUN --mount=type=cache,target=/var/cache/libdnf5,sharing=locked dnf install --setopt=keepcache=True -y --setopt=retries=25 cage
RUN useradd -m -s /bin/bash nizhosetup && passwd -l nizhosetup && \
    printf 'nizhosetup ALL=(root) NOPASSWD: /usr/libexec/nizho-oobe-apply, /usr/libexec/nizho-firstboot\n' \
      > /etc/sudoers.d/95-nizho-oobe && \
    mkdir -p /etc/systemd/system/getty@tty1.service.d && \
    printf '[Unit]\nConditionPathExists=!/var/lib/nizho-firstboot-done\n[Service]\nExecStart=\nExecStart=-/sbin/agetty --autologin nizhosetup --skip-login --noissue --nohostname --nohints --noclear %%I $TERM\n' \
      > /etc/systemd/system/getty@tty1.service.d/nizhosetup-autologin.conf && \
    printf 'if [ "$(tty)" = /dev/tty1 ] && [ ! -e /var/lib/nizho-firstboot-done ]; then\n  printf "\\033[2J\\033[H\\033[?25l"\n  export GSK_RENDERER=cairo GDK_BACKEND=wayland\n  cage -- /usr/bin/nizho-oobe >"$HOME/nizho-oobe.log" 2>&1 || { printf "\\033[?25h"; sudo -n /usr/libexec/nizho-firstboot; }\nfi\n' \
      > /home/nizhosetup/.bash_profile && \
    touch /home/nizhosetup/.hushlogin && \
    chown nizhosetup:nizhosetup /home/nizhosetup/.bash_profile /home/nizhosetup/.hushlogin

# Nizho default wallpapers, one per look and accent: the outline of the Nizho mark in the accent colour, with three
# neutral contour lines around it, no text, rendered at 4K so HiDPI panels stay sharp (desktop/wallpapers/make-default.sh).
# default.png (the dark one) backs the login and lock screens. Users can change them.
COPY desktop/wallpapers/make-default.sh /tmp/make-default.sh
RUN --mount=type=cache,target=/var/cache/libdnf5,sharing=locked dnf install --setopt=keepcache=True -y -q ImageMagick && \
    mkdir -p /usr/share/nizho/wallpapers && \
    sh /tmp/make-default.sh /usr/share/nizho/wallpapers/default-light.png 3840 2160 light && \
    sh /tmp/make-default.sh /usr/share/nizho/wallpapers/default-dark.png 3840 2160 dark && \
    sh /tmp/make-default.sh /usr/share/nizho/wallpapers/default-light-blue.png 3840 2160 light blue && \
    sh /tmp/make-default.sh /usr/share/nizho/wallpapers/default-dark-blue.png 3840 2160 dark blue && \
    cp /usr/share/nizho/wallpapers/default-dark.png /usr/share/nizho/wallpapers/default.png && \
    rm /tmp/make-default.sh && \
    dnf remove -y -q ImageMagick

# nizho-tools .desktop files for launcher search
COPY desktop/desktop-files/ /usr/share/applications/

# ── NizhoWM: brand KDE Plasma as Nizho ──────────────────────────────────────
# Inter typeface (Nizho design system) + the Nizho Plasma color scheme.
RUN --mount=type=cache,target=/var/cache/libdnf5,sharing=locked dnf install --setopt=keepcache=True -y rsms-inter-fonts
COPY desktop/Nizho.colors /usr/share/color-schemes/Nizho.colors
COPY desktop/NizhoLight.colors /usr/share/color-schemes/NizhoLight.colors
COPY desktop/NizhoBlue.colors /usr/share/color-schemes/NizhoBlue.colors
COPY desktop/NizhoLightBlue.colors /usr/share/color-schemes/NizhoLightBlue.colors

# Design-system defaults for every new user (read from /etc/skel on account
# creation): Nizho color scheme, green #4DB491 accent, Inter font — applied
# from the first frame.
RUN mkdir -p /etc/skel/.config && \
    printf '[General]\nColorScheme=Nizho\nAccentColor=77,180,145\naccentColorFromWallpaper=false\nfont=Inter,10,-1,5,50,0,0,0,0,0\nfixed=JetBrains Mono,10,-1,5,50,0,0,0,0,0\nmenuFont=Inter,10,-1,5,50,0,0,0,0,0\nsmallestReadableFont=Inter,8,-1,5,50,0,0,0,0,0\ntoolBarFont=Inter,10,-1,5,50,0,0,0,0,0\n\n[KDE]\nwidgetStyle=Breeze\nLookAndFeelPackage=org.kde.breezedark.desktop\n\n[Icons]\nTheme=Nizho\n\n[Sounds]\nTheme=Nizho\n\n[WM]\nactiveFont=Inter,10,-1,5,75,0,0,0,0,0\n' \
      > /etc/skel/.config/kdeglobals

# First-login one-shot: apply the Nizho wallpaper + accent via the official
# Plasma CLI tools (reliable across versions), then disable itself.
# (nizho-plasma-firstrun and nizho-layout.js are installed in the late block.)
RUN mkdir -p /etc/skel/.config/autostart /usr/share/nizho && \
    printf '[Desktop Entry]\nType=Application\nName=Nizho Branding\nExec=/usr/libexec/nizho-plasma-firstrun\nX-KDE-autostart-phase=2\nOnlyShowIn=KDE\nNoDisplay=true\n' \
      > /etc/skel/.config/autostart/nizho-plasma-firstrun.desktop

# Privacy Activity tray (item 1) — StatusNotifierItem that makes nizhod's
# enforcement visible. Read-only; reads nizhod D-Bus + /var/log/nizho/privacy.jsonl.
RUN printf '[Desktop Entry]\nType=Application\nName=Nizho Privacy Activity\nExec=/usr/bin/nizho-privacy-tray\nX-KDE-autostart-phase=2\nOnlyShowIn=KDE\nNoDisplay=true\n' \
      > /etc/skel/.config/autostart/nizho-privacy-tray.desktop

# Custom Nizho SDDM login theme (dark, green accent, fade-in animation).
COPY desktop/sddm-theme-nizho/ /usr/share/sddm/themes/nizho/
RUN printf '[Theme]\nCurrent=nizho\n' > /etc/sddm.conf.d/30-nizho-theme.conf && \
    printf '[Users]\nHideUsers=nizhosetup\n' > /etc/sddm.conf.d/31-nizho-hide-setup.conf
# The login theme follows the desktop accent (theme.conf accent=auto), read
# from the sddm user's colour scheme. Seed it with the Nizho scheme so a fresh
# install matches the desktop default. "C" copies only when the file is
# missing, so "Apply Plasma Settings" (which rewrites it) always wins.
RUN printf 'd /var/lib/sddm/.config 0755 sddm sddm -\nC /var/lib/sddm/.config/kdeglobals - - - - /usr/share/color-schemes/Nizho.colors\nz /var/lib/sddm/.config/kdeglobals 0644 sddm sddm -\n' \
      > /usr/lib/tmpfiles.d/nizho-sddm-accent.conf

# Lock screen: Nizho wallpaper behind the (already dark+green) Plasma locker.
RUN printf '[Greeter][Wallpaper][org.kde.image][General]\nImage=/usr/share/nizho/wallpapers/default.png\n' \
      > /etc/skel/.config/kscreenlockerrc

# (The desktop wallpaper and panel layout now come from the Nizho global
# themes built in the late block, so the first frame is already Nizho.)

# Keep expensive optional effects off by default on modest graphics hardware.
# Users can enable them through Plasma Desktop Effects.
RUN printf '[Plugins]\nwobblywindowsEnabled=false\nmagiclampEnabled=false\nkwin4_effect_scaleEnabled=true\n\n[Desktops]\nNumber=3\nRows=1\n' \
      > /etc/skel/.config/kwinrc

# Filename/content indexing is opt-in; desktop search still launches apps.
RUN printf '[Basic Settings]\nIndexing-Enabled=false\n' > /etc/skel/.config/baloofilerc

# Brand the session name shown in SDDM.
RUN if [ -f /usr/share/wayland-sessions/plasma.desktop ]; then \
      sed -i 's/^Name=.*/Name=Nizho Desktop/' /usr/share/wayland-sessions/plasma.desktop; \
    fi

# ── Nizho brand identity: icons, cursor, sounds, boot splash ─────────────────

# OS identity: About screens, neofetch, bug reports all say Nizho + version.
# ID stays "fedora" — tools key repo/firmware behavior off it.
RUN sed -i \
      -e 's/^NAME=.*/NAME="Nizho"/' \
      -e 's/^PRETTY_NAME=.*/PRETTY_NAME="Nizho 0.1.0-dp1 (Developer Preview 1)"/' \
      -e 's/^VERSION=.*/VERSION="0.1.0-dp1"/' \
      -e 's|^HOME_URL=.*|HOME_URL="https://github.com/nizho-os/nizho"|' \
      -e 's|^BUG_REPORT_URL=.*|BUG_REPORT_URL="https://github.com/nizho-os/nizho/issues"|' \
      /usr/lib/os-release && \
    echo "0.1.0-dp1" > /usr/lib/nizho-release

# Nizho has its own welcome; KDE's Konqi tour would fight the identity.
# kded6's kded_plasma_welcome module launches it whenever plasma-welcomerc
# lacks [General] LastSeenVersion (verified via strings on the .so), so remove
# the package (rpm -e --test confirms no reverse deps) AND seed the config
# system-wide + in skel in case a future image update pulls it back in.
# mcelog: /dev/mcelog exists even in VMs, so gate on virtualization
# (the daemon starts then dies on the virtual CPU otherwise).
RUN rpm -e plasma-welcome && \
    printf '[General]\nLastSeenVersion=99.0.0\n' \
      > /etc/xdg/plasma-welcomerc && \
    printf '[General]\nLastSeenVersion=99.0.0\n' \
      > /etc/skel/.config/plasma-welcomerc && \
    mkdir -p /etc/systemd/system/mcelog.service.d && \
    printf '[Unit]\nConditionVirtualization=false\n' \
      > /etc/systemd/system/mcelog.service.d/10-hardware-only.conf

# Nizho icon theme (dark plates, green line glyphs; inherits breeze-dark).
COPY desktop/nizho-icons/ /usr/share/icons/Nizho/

# The KDE spin already ships power profiles (tuned-ppd — same D-Bus API as
# power-profiles-daemon, which CONFLICTS with it) plus kio-extras and
# ffmpegthumbs for file previews. Only the plymouth script engine is missing.
RUN --mount=type=cache,target=/var/cache/libdnf5,sharing=locked dnf install --setopt=keepcache=True -y --setopt=retries=25 plymouth-plugin-script

# NizhoCursor: white pointer with green outline, generated at build; every
# other shape inherits from breeze_cursors. NizhoSound: soft sine-based event
# sounds. Plymouth splash images. All need ImageMagick/xcursorgen/sox once.
COPY desktop/plymouth-theme-nizho/ /usr/share/plymouth/themes/nizho/
RUN --mount=type=cache,target=/var/cache/libdnf5,sharing=locked dnf install --setopt=keepcache=True -y -q ImageMagick xcursorgen sox liberation-sans-fonts && \
    # -- cursor ---------------------------------------------------------------
    mkdir -p /tmp/cur /usr/share/icons/NizhoCursor/cursors && \
    cd /tmp/cur && \
    magick -size 96x96 xc:none \
      -fill "#f5f7fa" -stroke "#4db491" -strokewidth 6 \
      -draw "polygon 12,8 12,84 34,62 48,92 60,86 46,58 72,58" ptr96.png && \
    for s in 24 32 48; do \
      magick ptr96.png -resize ${s}x${s} ptr_${s}.png; \
    done && \
    printf '24 3 2 ptr_24.png\n32 4 3 ptr_32.png\n48 6 4 ptr_48.png\n' > ptr.cfg && \
    xcursorgen ptr.cfg /usr/share/icons/NizhoCursor/cursors/left_ptr && \
    magick -size 96x96 xc:none \
      -stroke "#f5f7fa" -strokewidth 6 -fill none \
      -draw "line 48,14 48,82 line 36,14 60,14 line 36,82 60,82" ibeam96.png && \
    for s in 24 32 48; do \
      magick ibeam96.png -resize ${s}x${s} ibeam_${s}.png; \
    done && \
    printf '24 12 12 ibeam_24.png\n32 16 16 ibeam_32.png\n48 24 24 ibeam_48.png\n' > txt.cfg && \
    xcursorgen txt.cfg /usr/share/icons/NizhoCursor/cursors/text && \
    cd /usr/share/icons/NizhoCursor/cursors && \
    for a in default arrow top_left_arrow left_arrow; do ln -sf left_ptr $a; done && \
    for a in xterm ibeam; do ln -sf text $a; done && \
    printf '[Icon Theme]\nName=NizhoCursor\nComment=Nizho cursor theme\nInherits=breeze_cursors\n' \
      > /usr/share/icons/NizhoCursor/index.theme && \
    # -- sounds ---------------------------------------------------------------
    mkdir -p /usr/share/sounds/Nizho/stereo && cd /usr/share/sounds/Nizho/stereo && \
    sox -n -r 44100 desktop-login.wav        synth 0.35 sine 523:784  fade t 0.02 0.35 0.15 vol 0.30 && \
    sox -n -r 44100 message-new-instant.wav  synth 0.18 sine 880      fade t 0.01 0.18 0.09 vol 0.25 && \
    sox -n -r 44100 dialog-error.wav         synth 0.25 sine 185      fade t 0.01 0.25 0.10 vol 0.30 && \
    sox -n -r 44100 camera-shutter.wav       synth 0.06 whitenoise    fade t 0.005 0.06 0.03 vol 0.20 && \
    sox -n -r 44100 device-added.wav         synth 0.15 sine 587:880  fade t 0.01 0.15 0.07 vol 0.25 && \
    sox -n -r 44100 device-removed.wav       synth 0.15 sine 880:587  fade t 0.01 0.15 0.07 vol 0.25 && \
    sox -n -r 44100 battery-low.wav          synth 0.30 sine 330:262  fade t 0.01 0.30 0.12 vol 0.30 && \
    sox -n -r 44100 audio-volume-change.wav  synth 0.08 sine 660      fade t 0.005 0.08 0.04 vol 0.20 && \
    cp message-new-instant.wav bell.wav && \
    printf '[Sound Theme]\nName=Nizho\nComment=Nizho sound theme\nInherits=freedesktop\nDirectories=stereo\n\n[stereo]\nOutputProfile=stereo\n' \
      > /usr/share/sounds/Nizho/index.theme && \
    # -- plymouth splash images ------------------------------------------------
    cd /usr/share/plymouth/themes/nizho && \
    sh make-assets.sh && \
    rm make-assets.sh && \
    rm -rf /tmp/cur && \
    dnf remove -y -q ImageMagick xcursorgen sox

# Default cursor theme for new users.
RUN printf '[Mouse]\ncursorTheme=NizhoCursor\n' > /etc/skel/.config/kcminputrc

# Activate the boot splash: set the theme, rebuild the initramfs so plymouth
# carries it, and add rhgb+quiet kargs so the splash actually shows. Disk
# images get console=ttyS0 from bootc-image-builder, and plymouth falls back to
# text ("details") whenever a serial console is present, so tell it to leave
# serial consoles alone: splash on screen, boot log still on the serial port.
RUN plymouth-set-default-theme nizho && \
    mkdir -p /var/roothome && \
    KVER=$(ls /usr/lib/modules | head -1) && \
    dracut --force --no-hostonly /usr/lib/modules/$KVER/initramfs.img $KVER && \
    rmdir /var/roothome && \
    mkdir -p /usr/lib/bootc/kargs.d && \
    printf 'kargs = ["rhgb", "quiet", "plymouth.ignore-serial-consoles"]\n' > /usr/lib/bootc/kargs.d/10-nizho-splash.toml

# The boot menu: shown for 5 s, centred, in the Nizho theme (desktop/grub-theme-nizho). New installs
# get the drop-in in grub.cfg; nizho-grub-theme.service copies the theme to /boot and updates the
# block on machines installed earlier (bootupd never rewrites grub.cfg on updates).
COPY desktop/grub-theme-nizho/12_nizho_menu.cfg /usr/lib/bootupd/grub2-static/configs.d/12_nizho_menu.cfg
COPY --from=grub-theme-builder /out/ /usr/lib/nizho/grub-theme/
COPY desktop/grub-theme-nizho/nizho-grub-theme /usr/libexec/nizho-grub-theme
COPY desktop/grub-theme-nizho/nizho-grub-theme.service /usr/lib/systemd/system/nizho-grub-theme.service
# Keep kernel and udev chatter off the boot screen.
RUN systemctl enable nizho-grub-theme.service && \
    printf 'kargs = ["loglevel=3", "rd.udev.log_level=3"]\n' > /usr/lib/bootc/kargs.d/11-nizho-quiet.toml

# Build the UKI only after the final initramfs and OS identity are ready.
# Build UKI from the final installed kernel+initramfs — no embedded cmdline
RUN set -e; \
    KVER=$(ls /usr/lib/modules | head -1); \
    ukify build \
      --linux  /usr/lib/modules/${KVER}/vmlinuz \
      --initrd /usr/lib/modules/${KVER}/initramfs.img \
      --stub   /usr/lib/systemd/boot/efi/linuxx64.efi.stub \
      --os-release /etc/os-release \
      --output /usr/lib/modules/${KVER}/${KVER}.efi; \
    echo "UKI built: $(ls -lh /usr/lib/modules/${KVER}/${KVER}.efi)"

# Fedora 44's KDE enables its own first-run wizard (plasma-setup) on top of
# Nizho's OOBE, so new users were asked to set up the machine twice. Mask it:
# Nizho's wizard creates the account. (Placed here, not by the SDDM step, so
# an existing build cache is not invalidated.)
RUN systemctl mask plasma-setup.service

# Monospace font the desktop config already names (kdeglobals fixed=) and the
# setup screen types its "hello world" in.
RUN --mount=type=cache,target=/var/cache/libdnf5,sharing=locked dnf install --setopt=keepcache=True -y jetbrains-mono-fonts

# Nizho Light icons: the same Nizho app icons, falling back to Breeze's
# light-background set instead of breeze-dark, so tray and launcher glyphs stay
# visible on the light look. nizho-plasma-firstrun switches to it for Light.
RUN cp -a /usr/share/icons/Nizho /usr/share/icons/Nizho-Light && \
    sed -i -e 's/^Name=.*/Name=Nizho Light/' -e 's/^Inherits=.*/Inherits=breeze/' \
           -e 's/^Comment=.*/Comment=Nizho icon theme for the light look/' \
           /usr/share/icons/Nizho-Light/index.theme && \
    gtk-update-icon-cache -q -f /usr/share/icons/Nizho-Light 2>/dev/null || true

# Update status for unprivileged apps (bootc status needs root): written to
# /run/nizho/bootc-status.json at boot, every 10 min, and when deployments change.
COPY system/systemd/nizho-bootc-status.service system/systemd/nizho-bootc-status.timer system/systemd/nizho-bootc-status.path /usr/lib/systemd/system/
RUN systemctl enable nizho-bootc-status.timer nizho-bootc-status.path

# Nizho wallpapers as real KDE wallpaper packages, and the system default
# (plasmarc [Wallpapers] defaultWallpaperTheme): the very first frame of a new
# desktop is Nizho's, instead of KDE's "Next" until a login script swaps it.
RUN for look in Dark Light; do for acc in "" -Blue; do \
      l=$(echo $look | tr A-Z a-z); s=$(echo "$acc" | tr A-Z a-z); d=/usr/share/wallpapers/Nizho-$look$acc; \
      mkdir -p $d/contents/images && \
      ln -sf /usr/share/nizho/wallpapers/default-$l$s.png $d/contents/images/3840x2160.png && \
      printf '{"KPlugin":{"Id":"Nizho-%s%s","Name":"Nizho %s%s","License":"GPL-3.0-or-later","Authors":[{"Name":"Nizho"}]}}\n' "$look" "$acc" "$look" "$(echo "$acc" | tr - ' ')" > $d/metadata.json; \
    done; done && \
    printf '[Wallpapers]\ndefaultWallpaperTheme=Nizho-Dark\ndefaultFileSuffix=.png\ndefaultWidth=3840\ndefaultHeight=2160\n' > /etc/xdg/plasmarc

# Setup helper and first-login script (here so editing them is a cheap rebuild).
COPY system/nizho-oobe-apply /usr/libexec/nizho-oobe-apply
COPY system/nizho-plasma-firstrun /usr/libexec/nizho-plasma-firstrun
COPY system/nizho-layout.js /usr/share/nizho/nizho-layout.js
COPY desktop/plasma-launcher /usr/share/plasma/plasmoids/org.nizho.launcher
COPY desktop/plasma-brand /usr/share/plasma/plasmoids/org.nizho.brand
COPY desktop/plasma-search /usr/share/plasma/plasmoids/org.nizho.search
COPY desktop/plasma-statuspill /usr/share/plasma/plasmoids/org.nizho.statuspill
COPY desktop/plasma-quicksettings /usr/share/plasma/plasmoids/org.nizho.quicksettings
COPY desktop/plasma-spaces /usr/share/plasma/plasmoids/org.nizho.spaces
# Apps pinned in the launcher for a new account. Fedora's kde-settings pins
# Kontact and KWrite through its own kicker-extra-favoritesrc; /etc/xdg comes
# first in XDG_CONFIG_DIRS, so this list is the one Plasma reads.
RUN printf '[General]\nPrepend=%s\nIgnoreDefaults=true\n' \
      'org.mozilla.firefox.desktop;org.kde.dolphin.desktop;org.kde.konsole.desktop;org.kde.discover.desktop;org.nizho.settings.desktop;org.nizho.dashboard.desktop;libreoffice-writer.desktop;libreoffice-calc.desktop;org.kde.kcalc.desktop;org.kde.okular.desktop;org.kde.gwenview.desktop;org.kde.plasma-systemmonitor.desktop' \
      > /etc/xdg/kicker-extra-favoritesrc

# A top bar on every screen, including one connected later (panels/).
COPY desktop/panels/nizho-panels /usr/libexec/nizho-panels
COPY desktop/panels/nizho-panels.service /usr/lib/systemd/user/nizho-panels.service
COPY desktop/panels/kwin /usr/share/kwin/scripts/nizho-panels
RUN chmod 755 /usr/libexec/nizho-panels && systemctl --global enable nizho-panels.service

# KDE Connect is off by default: while it runs, it makes the computer visible
# to phones and computers on the same network. Opening the KDE Connect app
# still starts it. Its login autostart is hidden, its tray widget is taken out
# once per account (plasma-updates/), and Dolphin's send-to-device entry, which
# started it on every right-click, is off (Dolphin's settings can turn it on).
COPY desktop/plasma-updates/nizho-kdeconnect-off.js /usr/share/plasma/shells/org.kde.plasma.desktop/contents/updates/nizho-kdeconnect-off.js
RUN test -f /etc/xdg/autostart/org.kde.kdeconnect.daemon.desktop && \
    echo 'Hidden=true' >> /etc/xdg/autostart/org.kde.kdeconnect.daemon.desktop && \
    printf '[Show]\nkdeconnectfileitemaction=false\n' > /etc/xdg/kservicemenurc

# Updates come from ghcr.io/nizho-os/nizho, built and signed by the
# Image workflow. Only images signed with the Nizho key are accepted from
# there; a pull of an unsigned or differently signed image fails before
# anything is installed. Other registries keep Fedora's default policy.
COPY cosign.pub /etc/pki/containers/nizho.pub
COPY system/containers/nizho.yaml /etc/containers/registries.d/nizho.yaml
RUN python3 -c 'import json; p="/etc/containers/policy.json"; c=json.load(open(p)); \
c.setdefault("transports", {}).setdefault("docker", {})["ghcr.io/nizho-os/nizho"] = \
[{"type": "sigstoreSigned", "keyPath": "/etc/pki/containers/nizho.pub", "signedIdentity": {"type": "matchRepository"}}]; \
json.dump(c, open(p, "w"), indent=4)'
RUN chmod 755 /usr/libexec/nizho-oobe-apply /usr/libexec/nizho-plasma-firstrun

# Touchpads scroll the content with the fingers ("natural" scrolling), as on
# macOS, Windows and GNOME; KDE's own default is the opposite. KWin reads this
# default at login for every touchpad; System Settings can still turn it off.
RUN printf '[Libinput][Defaults][Touchpad]\nNaturalScroll=true\n' > /etc/xdg/kcminputrc

# Nizho global themes ("look-and-feel"), built from Breeze's. Plasma reads a
# new desktop's wallpaper and panel layout from the global theme, so these
# make the very first frame Nizho: colours, Plasma style, icons, cursor,
# wallpaper, top bar + dock. No splash between login and desktop. Every part
# stays changeable in System Settings.
RUN for v in dark light; do \
      if [ $v = dark ]; then base=org.kde.breezedark.desktop; scheme=Nizho; style=nizho-dark; icons=Nizho; wall=Nizho-Dark; \
      else base=org.kde.breeze.desktop; scheme=NizhoLight; style=nizho-light; icons=Nizho-Light; wall=Nizho-Light; fi; \
      d=/usr/share/plasma/look-and-feel/org.nizho.$v.desktop; \
      cp -a /usr/share/plasma/look-and-feel/$base $d && \
      python3 -c 'import json,sys; p=sys.argv[1]; m=json.load(open(p)); k=m["KPlugin"]; v=sys.argv[2]; \
k.update(Id="org.nizho."+v+".desktop", Name="Nizho "+v.title(), Description="The Nizho "+v+" look"); \
[k.pop(x) for x in list(k) if x.startswith(("Name[","Description["))]; json.dump(m,open(p,"w"),indent=1)' $d/metadata.json $v && \
      f=$d/contents/defaults && \
      kwriteconfig6 --file $f --group kdeglobals --group General --key ColorScheme $scheme && \
      kwriteconfig6 --file $f --group kdeglobals --group Icons --key Theme $icons && \
      kwriteconfig6 --file $f --group plasmarc --group Theme --key name $style && \
      kwriteconfig6 --file $f --group Wallpaper --key Image $wall && \
      kwriteconfig6 --file $f --group kcminputrc --group Mouse --key cursorTheme NizhoCursor && \
      kwriteconfig6 --file $f --group ksplashrc --group KSplash --key Theme None && \
      kwriteconfig6 --file $f --group ksplashrc --group KSplash --key Engine none && \
      cp /usr/share/nizho/nizho-layout.js $d/contents/layouts/org.kde.plasma.desktop-layout.js || exit 1; \
    done && \
    kwriteconfig6 --file /etc/skel/.config/kdeglobals --group KDE --key LookAndFeelPackage org.nizho.dark.desktop && \
    kwriteconfig6 --file /etc/xdg/kdeglobals --group KDE --key LookAndFeelPackage org.nizho.dark.desktop && \
    printf '[KSplash]\nEngine=none\nTheme=None\n' > /etc/skel/.config/ksplashrc

# Firewall on by default: the Nizho zone rejects unsolicited incoming
# connections (Fedora KDE's default zone allows ports 1025-65535). nizhod
# checks it and the Privacy dashboard shows it.
# greenboot: health checks after each boot. If a new version fails them, the
# computer restarts to retry (3 boots) and then goes back to the previous
# version on its own. greenboot-set-rollback-trigger records that version from
# its ExecStop during shutdown, but upstream's unit (v0.16.4 and main) has no
# shutdown conflict, so systemd never stopped it and ExecStop never ran:
# "Boot counter exhausted but no next-deployment-id set" (VM test 2026-10-11).
# Our drop-in adds Conflicts/Before=final.target (files stage); with it the
# same test rolled back unattended and a healthy update stayed.
RUN --mount=type=cache,target=/var/cache/libdnf5,sharing=locked dnf install --setopt=keepcache=True -y -q \
      firewalld greenboot greenboot-default-health-checks && \
    systemctl enable firewalld.service && \
    # No SSH server until the user turns on remote login (decided 2026-11-13): Fedora's presets enable
    # sshd, and only the firewall kept port 22 closed. The preset file also wins on first boot, when
    # systemd applies presets again. The VM test harness reaches the guest over vsock, which
    # systemd-ssh-generator serves without sshd.service.
    printf 'disable sshd.service\ndisable sshd.socket\n' > /usr/lib/systemd/system-preset/10-nizho-no-sshd.preset && \
    systemctl disable sshd.service sshd.socket && \
    for u in $(rpm -ql greenboot | grep -E '^/usr/lib/systemd/system/[^/]+\.service$' | xargs -n1 basename); do \
      systemctl enable "$u" && echo "greenboot: enabled $u"; \
    done
# Small files and binaries: one COPY each from the staging stages above.
# Keep each file's purpose next to its line in the "files" stage; what the
# RUN below does with them:
#  - firewall: Nizho zone (rejects unsolicited incoming connections; Fedora
#    KDE's default zone allows ports 1025-65535) made the default. nizhod checks
#    it and the Privacy dashboard shows it.
#  - kernel hardening: boot options via bootc kargs.d, runtime via sysctl.d.
#  - updates download in the background but never restart the computer on
#    their own: Fedora's bootc-fetch-apply-updates timer runs `bootc upgrade
#    --apply`, which reboots. Keep the timer, drop --apply; the Updates app
#    shows "Restart needed" and the user picks the moment.
#  - light login screen: the same SDDM theme with look=light and the light
#    wallpaper; nizho-oobe-apply selects it when the user picks Light.
#  - Firefox runs through the bubblewrap sandbox wrapper.
#  - nizhod and nizho-pulsed (read-only org.nizho.pulse, mirrors nizhod) enabled.
#  - time and names after a wrong clock: chrony also asks four time servers by address
#    (so it works without DNS) and nizho-dns-after-clock restarts systemd-resolved once
#    when the clock jumps. Fixes "Server not found" on a dead-battery laptop (Nokia).
#  - nizho-rtc-mode (first boot): when the firmware lists Windows Boot Manager, read
#    the hardware clock as local time like Windows; otherwise Nizho shows a time
#    5 h 30 min off. Changes how Linux reads the clock only, never Windows.
#  - Nizho Plasma styles (nizho-dark/-light): Breeze's colours with Nizho
#    panels (24px corners, 1px border, soft shadow) and popups (corners cut at
#    45 degrees; plasma-style/). Plasma takes every other element from its
#    default style.
# Runtime library for the privacy dots overlay, and the dock (Crystal Dock with
# the Nizho patch, its own layer-shell window, so icons can grow above it;
# see crystal-dock/).
# A late layer: the big package layers above stay cached.
RUN --mount=type=cache,target=/var/cache/libdnf5,sharing=locked --mount=type=bind,from=dock-builder,source=/build/out,target=/tmp/dock \
    dnf install --setopt=keepcache=True -y -q gtk4-layer-shell /tmp/dock/crystal-dock-*.rpm
# "Convert to" in the Dolphin right-click menu (convert/nizho-convert): ffmpeg for
# sound and video (Fedora's build includes MP3, AAC and H.264 through OpenH264),
# Pillow for pictures, poppler for PDF pages, mat2 for "Remove hidden details".
# The tool runs them with no network.
# The format wheel (nizho-wheel) needs Qt Quick, KGlobalAccel, KWindowSystem and
# LayerShellQt, which Plasma already brings; listed so they stay.
RUN --mount=type=cache,target=/var/cache/libdnf5,sharing=locked \
    dnf install --setopt=keepcache=True -y -q ffmpeg-free python3-pillow poppler-utils mat2 tesseract tesseract-langpack-eng \
      qt6-qtdeclarative qt6-qtmultimedia kf6-kglobalaccel kf6-kwindowsystem layer-shell-qt
# Git, offered next to VARA in setup's Advanced options.
RUN --mount=type=cache,target=/var/cache/libdnf5,sharing=locked \
    dnf install --setopt=keepcache=True -y -q git-core
# restic, from Fedora's own repository, for nizho-backup.
RUN --mount=type=cache,target=/var/cache/libdnf5,sharing=locked \
    dnf install --setopt=keepcache=True -y -q restic
# Metric-compatible stand-ins for Microsoft's Calibri (Carlito, OFL-1.1), Cambria (Caladea, Apache-2.0),
# Arial, Times New Roman and Courier New (Liberation, OFL-1.1), so Word and Excel files keep their line
# and page breaks in LibreOffice. libreoffice-core already requires them in Fedora 44; this keeps them
# installed even if LibreOffice's dependencies change.
RUN --mount=type=cache,target=/var/cache/libdnf5,sharing=locked \
    dnf install --setopt=keepcache=True -y -q google-carlito-fonts google-crosextra-caladea-fonts \
      liberation-sans-fonts liberation-serif-fonts liberation-mono-fonts
# Packages a mainstream desktop ships. In Fedora 44 the KDE environment above already pulls in all of
# these except the scanner ones; naming them keeps them if Fedora's groups change.
#  - accessibility: the Orca screen reader, its speech service and voice, and Plasma's on-screen keyboard.
#  - hardware: firmware updates (fwupd starts only when asked over D-Bus), Thunderbolt device approval,
#    and sound firmware for Intel laptops that use SOF.
#  - printing: CUPS and its filters.
#  - fonts for the setup's language list: Tamil, Hindi, and Chinese, Japanese and Korean.
#  - scanning (new): SANE drivers and Skanpage, a small KDE scanning app.
#  - browser: dbus-daemon, so the Firefox sandbox can run its own private message bus and a second
#    launch opens a window in the running Firefox (system/nizho-firefox). Fedora's own bus is dbus-broker.
RUN --mount=type=cache,target=/var/cache/libdnf5,sharing=locked \
    dnf install --setopt=keepcache=True -y -q \
      orca speech-dispatcher espeak-ng plasma-keyboard \
      fwupd bolt alsa-sof-firmware \
      cups cups-filters \
      default-fonts-ta default-fonts-hi default-fonts-cjk-sans \
      sane-backends-drivers-scanners skanpage \
      dbus-daemon
# Remove two Fedora packages that only fail on Nizho:
#  - PackageKit-command-not-found: PackageKit never runs on an image-based system, so every mistyped
#    command in a terminal printed a "PackageKit unit failed" error.
#  - imsettings: an X11 input-method starter that exits with an error at every sign-in. Plasma on
#    Wayland sets up input methods itself (System Settings > Keyboard > Virtual Keyboard).
RUN dnf remove -y -q $(rpm -q --qf '%{NAME}\n' PackageKit-command-not-found imsettings imsettings-qt imsettings-libs | grep -v ' ') && \
    ! rpm -q PackageKit-command-not-found imsettings >/dev/null && \
    test ! -e /etc/xdg/autostart/imsettings-start.desktop
COPY --from=files / /
# Session lock screen (Meta+L): the Nizho clock placement. The script stops the build if Plasma's file changed.
COPY desktop/lockscreen/nizho-lockscreen-patch.py /tmp/nizho-lockscreen-patch.py
RUN python3 /tmp/nizho-lockscreen-patch.py && rm /tmp/nizho-lockscreen-patch.py
# Nizho's greenboot checks sit next to the ones the greenboot package installed
# above. Fail the build if the package's own checks are missing.
RUN chmod 755 /usr/libexec/nizho-greenboot-unit-check /usr/lib/greenboot/check/required.d/2?_nizho_*.sh /usr/lib/greenboot/check/wanted.d/2?_nizho_*.sh && \
    test -f /usr/lib/greenboot/check/required.d/01_repository_dns_check.sh
RUN chmod 755 /usr/bin/nizho-backup /usr/bin/nizho-import /usr/bin/nizho-hwreport && \
    systemctl --global enable nizho-backup.timer
RUN sed -i 's/^DefaultZone=.*/DefaultZone=nizho/' /etc/firewalld/firewalld.conf && \
    grep -q '^DefaultZone=nizho' /etc/firewalld/firewalld.conf && \
    chmod 755 /usr/libexec/nizho-apply-look /usr/libexec/nizho-arm-boot-counter /usr/libexec/nizho-phone-link /usr/libexec/nizho-stale-trigger-check /usr/libexec/nizho-rtc-mode /usr/libexec/nizho-hide-system-image /usr/libexec/nizho-dns-after-clock /usr/libexec/nizho-image-ref-check /usr/libexec/nizho-display-scale /usr/bin/firefox-sandbox /usr/bin/nizho-convert /usr/bin/nizho && \
    mkdir -p /usr/lib/systemd/system/bootc-fetch-apply-updates.service.d && \
    printf '[Service]\nExecStart=\nExecStart=/usr/bin/bootc upgrade --quiet\n' \
      > /usr/lib/systemd/system/bootc-fetch-apply-updates.service.d/10-nizho-no-auto-reboot.conf && \
    cp -a /usr/share/sddm/themes/nizho /usr/share/sddm/themes/nizho-light && \
    sed -i -e 's/^look=.*/look=light/' \
           -e 's|^background=.*|background=/usr/share/nizho/wallpapers/default-light.png|' \
           /usr/share/sddm/themes/nizho-light/theme.conf && \
    sed -i -e 's/^Name=.*/Name=Nizho Light/' -e 's/^Theme-Id=.*/Theme-Id=nizho-light/' \
           /usr/share/sddm/themes/nizho-light/metadata.desktop && \
    for t in nizho:dark nizho-light:light; do \
      base=${t%%:*}; look=${t##*:}; blue=$base-blue; \
      if [ $base = nizho-light ]; then blue=nizho-light-blue; fi; \
      cp -a /usr/share/sddm/themes/$base /usr/share/sddm/themes/$blue && \
      sed -i -e 's/^accent=.*/accent=#4f86ff/' \
             -e "s|^background=.*|background=/usr/share/nizho/wallpapers/default-$look-blue.png|" \
             /usr/share/sddm/themes/$blue/theme.conf && \
      grep -q '^accent=#4f86ff' /usr/share/sddm/themes/$blue/theme.conf && \
      sed -i -e "s/^Name=.*/Name=Nizho $look blue/" -e "s/^Theme-Id=.*/Theme-Id=$blue/" \
             /usr/share/sddm/themes/$blue/metadata.desktop || exit 1; \
    done && \
    mv /usr/bin/firefox /usr/bin/firefox-unwrapped && \
    ln -sf firefox-sandbox /usr/bin/firefox && \
    for v in dark light; do \
      d=/usr/share/plasma/desktoptheme/nizho-$v && \
      if [ $v = dark ]; then n=Dark; else n=Light; fi && \
      cp /usr/share/plasma/desktoptheme/breeze-$v/colors /usr/share/plasma/desktoptheme/breeze-$v/plasmarc $d/ && \
      printf '{"KPlugin": {"Id": "nizho-%s", "Name": "Nizho %s", "Description": "Breeze %s with Nizho panels and popups", "License": "LGPL-2.1-or-later"}, "X-Plasma-API": "5.0"}\n' \
        $v $n $n > $d/metadata.json || exit 1; \
    done && \
    printf '\n# Nizho: also ask these time servers by address. With a dead clock battery the date\n# starts wrong, DNS then rejects every answer, and a time service that needs DNS\n# can never fix the date. Cloudflare and Google answer on fixed addresses.\nserver 162.159.200.1 iburst\nserver 162.159.200.123 iburst\nserver 216.239.35.0 iburst\nserver 216.239.35.4 iburst\n' >> /etc/chrony.conf && \
    mkdir -p /usr/share/nizho/nizhosetup && \
    cp -a /var/home/nizhosetup/.bash_profile /var/home/nizhosetup/.hushlogin /usr/share/nizho/nizhosetup/ && \
    systemctl enable nizhod.service nizho-pulsed.service nizho-rtc-mode.service nizho-hide-system-image.service nizho-dns-after-clock.service nizho-image-ref-check.service

# Keep tools for developers and theme authors out of the launcher, search and
# "Open with". They stay installed and run from a terminal.
RUN for f in gcdmaster org.kde.contactprintthemeeditor org.kde.contactthemeeditor \
      org.kde.drkonqi.coredump.gui org.kde.headerthemeeditor org.kde.kdebugsettings; do \
      d=/usr/share/applications/$f.desktop; [ -f "$d" ] || continue; \
      grep -q '^NoDisplay=true' "$d" || sed -i '0,/^\[Desktop Entry\]/s//[Desktop Entry]\nNoDisplay=true/' "$d"; \
    done

# Built binaries go in LAST: they change on every Rust edit, and anything after
# a changed layer rebuilds. Units and configs that reference them are installed
# (and enabled) above; nothing in between executes them. nizho-bar / nizho-dock /
# nizho-launcher are not shipped: Plasma's panel, Crystal Dock and the
# org.nizho.launcher widget replace them (the old ones are in git history,
# before 0d707ca).
COPY --from=bins / /
RUN chmod 755 /usr/bin/nizhod /usr/bin/nizho-pulsed /usr/bin/nizho-oobe \
      /usr/bin/nizho-dashboard /usr/bin/nizho-wifi /usr/bin/nizho-update \
      /usr/bin/nizho-hotkeys /usr/bin/nizho-capsule /usr/bin/nizho-perms \
      /usr/bin/nizho-settings-gtk /usr/bin/nizho-welcome /usr/bin/nizho-sound \
      /usr/bin/nizho-bluetooth /usr/bin/nizho-privacy-tray /usr/bin/nizho-privacy-dots && \
    bootc container lint
