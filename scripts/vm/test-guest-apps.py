#!/usr/bin/env python3
"""Real production service transactions, restricted to a disposable Nizho VM.

Run as root inside the guest. The fixture is a signed native Flatpak repository;
installation/removal go only through the installed system-bus authority.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time


def run(*args, **kwargs):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT, **kwargs)


def main():
    if os.geteuid() != 0 or not Path("/usr/lib/nizho-release").is_file():
        raise SystemExit("Requires root inside a disposable Nizho guest")
    if run("systemd-detect-virt").strip() not in ("kvm", "qemu"):
        raise SystemExit("Refusing production package tests outside a QEMU test VM")
    if not Path("/etc/nizho-archon-disposable-test").is_file():
        raise SystemExit("Create /etc/nizho-archon-disposable-test only in the disposable test guest")
    from gi.repository import Gio, GLib
    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)

    def call(method, signature=None, args=()):
        reply = bus.call_sync("org.nizho.Archon1", "/org/nizho/Archon1", "org.nizho.Archon1.Manager",
                              method, GLib.Variant(signature, args) if signature else None,
                              None, Gio.DBusCallFlags.NONE, 120000, None)
        return reply.unpack()[0] if reply.n_children() else None

    package = "app/org.nizho.ArchonGuestTest/x86_64/stable"
    repository = "nizho-disposable-test"
    existing = json.loads(call("GetRepositories"))
    if any(row.get("name") == repository or row.get("id") == repository for row in existing):
        raise SystemExit("Test remote already exists; refusing to modify existing state")
    if any(row[0] == package for row in call("GetInstalledPackages")):
        raise SystemExit("Test application already exists")
    # /var/lib is visible to the real service despite its PrivateTmp namespace.
    root = Path(tempfile.mkdtemp(prefix="nizho-archon-guest.", dir="/var/lib"))
    print(f"Preserved signed fixture and test log directory: {root}", flush=True)
    keyring = root / "keys"
    keyring.mkdir(mode=0o700)
    env = dict(os.environ, GNUPGHOME=str(keyring))
    run("gpg", "--batch", "--pinentry-mode", "loopback", "--passphrase", "",
        "--quick-generate-key", "Nizho disposable VM test", "ed25519", "sign", "1d", env=env)
    fingerprint = next(line.split(":")[9] for line in
                       run("gpg", "--batch", "--with-colons", "--list-keys", env=env).splitlines()
                       if line.startswith("fpr:"))
    key = root / "key.gpg"
    key.write_bytes(subprocess.check_output(["gpg", "--batch", "--export", fingerprint], env=env))
    runtime = root / "runtime"
    (runtime / "usr").mkdir(parents=True)
    (runtime / "files").mkdir()
    (runtime / "metadata").write_text("[Runtime]\nname=org.nizho.ArchonGuestPlatform\n"
        "runtime=org.nizho.ArchonGuestPlatform/x86_64/1\nsdk=org.nizho.ArchonGuestPlatform/x86_64/1\n")
    app = root / "app"
    (app / "files/bin").mkdir(parents=True)
    (app / "files/bin/test").write_text("#!/bin/sh\nexit 0\n")
    (app / "files/bin/test").chmod(0o755)
    (app / "metadata").write_text("[Application]\nname=org.nizho.ArchonGuestTest\n"
        "runtime=org.nizho.ArchonGuestPlatform/x86_64/1\nsdk=org.nizho.ArchonGuestPlatform/x86_64/1\ncommand=test\n")
    repo = root / "repo"
    run("flatpak", "build-export", "--runtime", f"--gpg-sign={fingerprint}", str(repo), str(runtime), "1", env=env)
    run("flatpak", "build-finish", str(app), env=env)
    run("flatpak", "build-export", f"--gpg-sign={fingerprint}", str(repo), str(app), "stable", env=env)
    run("flatpak", "build-update-repo", f"--gpg-sign={fingerprint}", str(repo), env=env)
    run("flatpak", "remote-add", "--system", f"--gpg-import={key}", repository, repo.as_uri())
    assert any(row[0] == package for row in call("SearchPackages", "(ssuu)", ("ArchonGuestTest", "", 0, 50)))
    assert "ArchonGuestPlatform" in call("GetPackageDetails", "(ss)", (package, repository))
    print("PASS production service discovery and dependency details", flush=True)
    unauthorized = '''
from gi.repository import Gio,GLib
bus=Gio.bus_get_sync(Gio.BusType.SYSTEM,None)
def call(method, signature, args):
    return bus.call_sync('org.nizho.Archon1','/org/nizho/Archon1',
      'org.nizho.Archon1.Manager',method,GLib.Variant(signature,args),None,
      Gio.DBusCallFlags.NONE,120000,None).unpack()[0]
plan=call('PlanTransaction','(sss)',('install','app/org.nizho.ArchonGuestTest/x86_64/stable','nizho-disposable-test'))
try:
    call('CommitTransaction','(s)',(plan[0],))
except GLib.Error as error:
    assert 'AccessDenied' in str(error), error
else:
    raise AssertionError('Unauthorized production commit was accepted')
bus.call_sync('org.nizho.Archon1','/org/nizho/Archon1','org.nizho.Archon1.Manager',
  'CancelTransaction',GLib.Variant('(s)',(plan[0],)),None,Gio.DBusCallFlags.NONE,120000,None)
print('PASS real polkit rejection for unprivileged inactive caller')
'''
    print(run("runuser", "--user", "nobody", "--", "python3", "-c", unauthorized), end="", flush=True)
    events = []
    bus.signal_subscribe("org.nizho.Archon1", "org.nizho.Archon1.Manager", None,
        "/org/nizho/Archon1", None, Gio.DBusSignalFlags.NONE,
        lambda _bus, _sender, _path, _interface, name, parameters, _data:
        events.append((name, parameters.unpack())), None)

    def transact(action):
        plan = call("PlanTransaction", "(sss)", (action, package, repository))
        assert plan[2] == "flatpak-system"
        if action == "install":
            assert "ArchonGuestPlatform" in plan[3]
            assert not any(row[0] == package for row in call("GetInstalledPackages"))
        transaction = call("CommitTransaction", "(s)", (plan[0],))
        for _ in range(240):
            while GLib.MainContext.default().pending():
                GLib.MainContext.default().iteration(False)
            state = call("GetTransactionStatus", "(s)", (transaction,))
            if state[1] in ("succeeded", "failed", "cancelled", "interrupted"):
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("Guest transaction did not finish within 60 seconds")
        assert state[1] == "succeeded", state
        for _ in range(50):
            while GLib.MainContext.default().pending():
                GLib.MainContext.default().iteration(False)
            if ("TransactionCompleted", (transaction,)) in events:
                break
            time.sleep(.1)
        assert ("TransactionCompleted", (transaction,)) in events, events
        assert any(name == "TransactionProgress" and args[0] == transaction
                   for name, args in events), events
        try:
            call("CommitTransaction", "(s)", (plan[0],))
        except GLib.Error:
            pass
        else:
            raise AssertionError("Consumed plan was accepted")
        return transaction

    installed = transact("install")
    assert any(row[0] == package for row in call("GetInstalledPackages"))
    assert "org.nizho.ArchonGuestTest" in run("flatpak", "list", "--system", "--app", "--columns=application")
    print("PASS authorized production D-Bus installation and consumed-plan rejection", flush=True)
    removed = transact("remove")
    assert not any(row[0] == package for row in call("GetInstalledPackages"))
    history = call("GetTransactionHistory", "(uu)", (0, 100))
    assert {installed, removed} <= {row[0] for row in history}
    (root / "result.json").write_text(json.dumps({"install": installed, "remove": removed,
                                                 "history": history}, indent=2) + "\n")
    print("PASS production D-Bus removal and persisted history", flush=True)
    print("Runtime, remote and signed fixture retained for reboot/recovery inspection", flush=True)


if __name__ == "__main__":
    main()
