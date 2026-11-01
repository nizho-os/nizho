Name:           nizho-convert
Version:        0.1.0
Release:        1%{?dist}
Summary:        Offline file conversion and format wheel for Nizho
License:        GPL-3.0-or-later
URL:            https://github.com/nizho-os/nizhoos
Source0:        %{name}-%{version}.tar.gz

BuildRequires:  cmake
BuildRequires:  gcc-c++
BuildRequires:  cmake(Qt6Gui)
BuildRequires:  cmake(Qt6Quick)
BuildRequires:  cmake(Qt6DBus)
BuildRequires:  cmake(Qt6QuickControls2)
BuildRequires:  cmake(KF6GlobalAccel)
BuildRequires:  cmake(KF6WindowSystem)
BuildRequires:  cmake(LayerShellQt)
BuildRequires:  systemd-rpm-macros

Requires:       bubblewrap
Requires:       ffmpeg-free
Requires:       python3-pillow
Requires:       poppler-utils
Requires:       ghostscript
Requires:       libnotify
Requires:       dbus-tools
Requires:       kf6-kio
Requires:       layer-shell-qt
Requires:       qt6-qtdeclarative
Recommends:     mat2

%description
nizho-convert converts pictures, sound, video and PDFs next to the original,
inside a bubblewrap sandbox with no network. nizho-wheel is the ring of formats
shown at the cursor (Meta+Alt+C, or Convert to... in Dolphin), and of edits
(Meta+Alt+E). nizho-edit is the small window for crop, black out, background
and trim.

%prep
%autosetup

%build
%cmake -S convert
%cmake_build

%install
%cmake_install
install -Dm755 convert/nizho-convert %{buildroot}%{_bindir}/nizho-convert
for f in image pdf audio video; do
  install -Dm644 convert/nizho-convert-$f.desktop %{buildroot}%{_datadir}/kio/servicemenus/nizho-convert-$f.desktop
done
install -Dm644 convert/nizho-wheel-servicemenu.desktop %{buildroot}%{_datadir}/kio/servicemenus/nizho-wheel.desktop
install -Dm644 convert/nizho-clean.desktop %{buildroot}%{_datadir}/kio/servicemenus/nizho-clean.desktop
for f in image pdf pdf-merge media; do
  install -Dm644 convert/nizho-edit-$f.desktop %{buildroot}%{_datadir}/kio/servicemenus/nizho-edit-$f.desktop
done
install -Dm644 convert/org.nizho.edit.desktop %{buildroot}%{_datadir}/applications/org.nizho.edit.desktop
install -Dm644 convert/org.nizho.wheel.desktop %{buildroot}%{_datadir}/applications/org.nizho.wheel.desktop
%{_datadir}/applications/org.nizho.edit.desktop
install -Dm644 convert/packaging/nizho-wheel.service %{buildroot}%{_userunitdir}/nizho-wheel.service
install -Dm644 convert/packaging/org.nizho.Wheel.service %{buildroot}%{_datadir}/dbus-1/services/org.nizho.Wheel.service

%post
%systemd_user_post nizho-wheel.service

%preun
%systemd_user_preun nizho-wheel.service

%files
%{_bindir}/nizho-convert
%{_bindir}/nizho-wheel
%{_bindir}/nizho-edit
%{_datadir}/kio/servicemenus/nizho-*.desktop
%{_datadir}/applications/org.nizho.wheel.desktop
%{_userunitdir}/nizho-wheel.service
%{_datadir}/dbus-1/services/org.nizho.Wheel.service

%changelog
* Sun Oct 25 2026 Thulasiram K <thulasiramk.git@gmail.com> - 0.1.0-1
- First package
