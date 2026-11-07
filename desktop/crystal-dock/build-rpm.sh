#!/bin/sh
# Rebuild Fedora's crystal-dock package with nizho-look.patch and
# nizho-per-user.patch. Runs in the dock-builder stage of the Containerfile;
# the RPM lands in /build/out/.
set -eu
cd /build
dnf --setopt=zchunk=False --setopt=retries=10 download -q --source crystal-dock-2.16.0-1.fc44
# The Fedora keyring is provisioned by the signed base image/repository packages.
# Download-only commands do not enforce source-RPM signatures themselves.
for source in crystal-dock-*.src.rpm; do
    verification=$(rpmkeys --checksig --verbose "$source")
    printf '%s\n' "$verification"
    printf '%s\n' "$verification" | grep -Eiq 'signature.*: OK' || {
        echo 'Source RPM has no valid trusted signature' >&2; exit 1;
    }
    rpm -i "$source"
done
cp nizho-look.patch nizho-per-user.patch ~/rpmbuild/SOURCES/
spec=~/rpmbuild/SPECS/crystal-dock.spec
sed -i -e 's/^Release:\( *\)\([^%]*\)/Release:\1\2.nizho2/' \
       -e '/^Source0:/a Patch100:       nizho-look.patch\nPatch101:       nizho-per-user.patch' \
       -e '/^BuildRequires:  cmake(LayerShellQt)/a BuildRequires:  cmake(KF6WindowSystem)' "$spec"
grep -q '^Patch100:' "$spec"
grep -q '^Patch101:' "$spec"
grep -q 'cmake(KF6WindowSystem)' "$spec"
dnf builddep -y -q "$spec"
rpmbuild -bb --nocheck --define 'debug_package %{nil}' "$spec" >/build/rpmbuild.log 2>&1 || { tail -40 /build/rpmbuild.log; exit 1; }
mkdir -p out
cp ~/rpmbuild/RPMS/x86_64/crystal-dock-[0-9]*.rpm out/
