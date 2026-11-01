// When screens are added or removed, start nizho-panels, which gives a new
// screen the Nizho top bar (see /usr/libexec/nizho-panels).
workspace.screensChanged.connect(function () {
    callDBus("org.freedesktop.systemd1", "/org/freedesktop/systemd1",
        "org.freedesktop.systemd1.Manager", "StartUnit", "nizho-panels.service", "replace");
});
