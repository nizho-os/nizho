import contextlib
import importlib.machinery
import importlib.util
import io
import os
import shutil
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
loader = importlib.machinery.SourceFileLoader("nizho_hwreport", os.path.join(HERE, "nizho-hwreport"))
spec = importlib.util.spec_from_loader("nizho_hwreport", loader)
hw = importlib.util.module_from_spec(spec)
loader.exec_module(hw)

SECRETS = ["SERIAL-ABC123XYZ", "11:22:33:44:55:66", "HomeNetwork-SSID", "aabbccdd-1111-2222-3333-444455556666",
           "SECRETBOARD99", "192.168.1.77", "fe80::1a2b:3c4d:5e6f:7788", "alice-laptop", "My Disk Label"]


def put(base, rel, text, mode="w"):
    path = os.path.join(base, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode) as f:
        f.write(text)


class Fake(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old = dict(os.environ)
        os.environ["NIZHO_HW_ROOT"] = self.tmp

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def p(self, rel, text):
        put(self.tmp, rel, text)

    def full_tree(self):
        d = "sys/class/dmi/id/"
        self.p(d + "sys_vendor", "ACME\n")
        self.p(d + "product_name", "Laptop 9\n")
        self.p(d + "bios_version", "N.1.02\n")
        self.p(d + "product_serial", SECRETS[0] + "\n")
        self.p(d + "product_uuid", SECRETS[3] + "\n")
        self.p(d + "board_serial", SECRETS[4] + "\n")
        self.p(d + "chassis_serial", SECRETS[0] + "\n")
        self.p("proc/cpuinfo", "processor\t: 0\nmodel name\t: Test CPU @ 2GHz\n\nprocessor\t: 1\nmodel name\t: Test CPU @ 2GHz\n")
        self.p("proc/meminfo", "MemTotal:        8388608 kB\n")
        self.p("proc/sys/kernel/osrelease", "6.99.1-test\n")
        self.p("usr/lib/nizho-release", "0.1.0-dp1\n")
        # GPU
        self.p("sys/class/drm/card0/device/vendor", "0x8086\n")
        self.p("sys/class/drm/card0/device/device", "0x9b41\n")
        os.makedirs(os.path.join(self.tmp, "sys/bus/pci/drivers/i915"))
        os.symlink("../../../../bus/pci/drivers/i915", os.path.join(self.tmp, "sys/class/drm/card0/device/driver"))
        self.p("sys/class/drm/card0-eDP-1/status", "connected\n")
        self.p("sys/class/drm/card0-eDP-1/modes", "1920x1080\n1280x720\n")
        self.p("sys/class/drm/card0-eDP-1/edid_serial", SECRETS[0])
        # Wi-Fi
        self.p("sys/class/net/wlan0/wireless/dummy", "")
        self.p("sys/class/net/wlan0/operstate", "up\n")
        self.p("sys/class/net/wlan0/address", SECRETS[1] + "\n")
        self.p("sys/class/net/wlan0/ssid", SECRETS[2] + "\n")
        self.p("sys/class/net/wlan0/device/vendor", "0x8086\n")
        self.p("sys/class/net/wlan0/device/device", "0x02f0\n")
        os.makedirs(os.path.join(self.tmp, "sys/bus/pci/drivers/iwlwifi"))
        os.symlink("../../../../../bus/pci/drivers/iwlwifi", os.path.join(self.tmp, "sys/class/net/wlan0/device/driver"))
        self.p("sys/class/net/lo/address", "00:00:00:00:00:00\n")
        self.p("etc/hostname", SECRETS[7] + "\n")
        # Bluetooth on USB
        usb = "sys/devices/usb1/1-10/"
        self.p(usb + "idVendor", "8087\n")
        self.p(usb + "idProduct", "0026\n")
        os.makedirs(os.path.join(self.tmp, "sys/class/bluetooth"))
        os.makedirs(os.path.join(self.tmp, usb, "1-10:1.0/bluetooth/hci0"))
        os.symlink(os.path.join(self.tmp, usb, "1-10:1.0/bluetooth/hci0"), os.path.join(self.tmp, "sys/class/bluetooth/hci0"))
        os.symlink(os.path.join(self.tmp, usb, "1-10:1.0"), os.path.join(self.tmp, usb, "1-10:1.0/bluetooth/hci0/device"))
        os.makedirs(os.path.join(self.tmp, "sys/bus/usb/drivers/btusb"))
        os.symlink("../../../../bus/usb/drivers/btusb", os.path.join(self.tmp, usb, "1-10:1.0/driver"))
        self.p("sys/class/bluetooth/hci0/address", SECRETS[1])
        # audio, camera, input, fingerprint
        self.p("proc/asound/cards", " 0 [PCH            ]: HDA-Intel - HDA Intel PCH\n                      HDA Intel PCH at 0xb1228000 irq 148\n")
        self.p("sys/class/video4linux/video0/name", "Integrated Camera\n")
        self.p("proc/bus/input/devices",
               'I: Bus=0018\nN: Name="SYNA2BA6:00 06CB:CE2D Touchpad"\nU: Uniq=\n\n'
               'I: Bus=0005\nN: Name="Alice\'s Mouse"\nU: Uniq=11:22:33:44:55:66\n\n'
               'I: Bus=0011\nN: Name="AT Translated Set 2 keyboard"\nU: Uniq=\n')
        self.p("sys/bus/usb/devices/1-4/idVendor", "27c6\n")
        self.p("sys/bus/usb/devices/1-4/idProduct", "55a4\n")
        self.p("sys/bus/usb/devices/1-4/serial", SECRETS[0] + "\n")
        # battery
        self.p("sys/class/power_supply/BAT0/type", "Battery\n")
        self.p("sys/class/power_supply/BAT0/energy_full", "40000000\n")
        self.p("sys/class/power_supply/BAT0/energy_full_design", "50000000\n")
        self.p("sys/class/power_supply/BAT0/serial_number", SECRETS[0] + "\n")
        # firmware
        self.p("sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c", "")
        with open(os.path.join(self.tmp, "sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c"), "wb") as f:
            f.write(b"\x06\x00\x00\x00\x01")


class Report(Fake):
    def test_values_appear(self):
        self.full_tree()
        r = hw.build_report()
        for want in ["ACME", "Laptop 9", "N.1.02", "Test CPU @ 2GHz", "Logical CPUs: 2", "8.0 GiB", "6.99.1-test",
                     "0.1.0-dp1", "8086:9b41, driver i915", "8086:02f0, driver iwlwifi", "state up",
                     "8087:0026, driver btusb", "HDA Intel PCH", "Integrated Camera", "Touchpad",
                     "27c6:55a4", "health 80%", "Secure Boot: enabled", "Booted via UEFI: yes", "1920x1080"]:
            self.assertIn(want, r)

    def test_no_identifiers_leak(self):
        self.full_tree()
        r = hw.build_report()
        for s in SECRETS:
            self.assertNotIn(s, r)
        self.assertNotIn(self.tmp, r)
        self.assertNotIn("Alice", r)
        self.assertEqual(hw.redact_scan(r), [])

    def test_missing_probes_say_not_available(self):
        r = hw.build_report()  # empty tree
        self.assertIn("- Vendor: not available", r)
        self.assertIn("- CPU: not available", r)
        self.assertIn("- RAM: not available", r)
        self.assertIn("- Wi-Fi interface: not available", r)
        self.assertIn("- Bluetooth adapter: not available", r)
        self.assertIn("- Battery: not available", r)
        self.assertIn("- Secure Boot: not available", r)
        self.assertIn("- Nizho image version: not available", r)
        self.assertIn("## Checklist", r)

    def test_checklist_is_blank(self):
        r = hw.build_report()
        for item in hw.CHECKLIST:
            self.assertIn("### " + item, r)
        self.assertNotIn("[x]", r)
        self.assertEqual(r.count("Notes:"), len(hw.CHECKLIST))

    def test_header_states_read_only(self):
        r = hw.build_report()
        self.assertIn("sent nothing over the network", r.split("##")[0])

    def test_scrub_removes_identifiers_in_values(self):
        self.p("sys/class/dmi/id/product_name", "Model 11:22:33:44:55:66 10.0.0.5")
        r = hw.build_report()
        self.assertNotIn("11:22:33:44:55:66", r)
        self.assertNotIn("10.0.0.5", r)
        self.assertIn("[removed]", r)

    def test_battery_absent(self):
        os.makedirs(os.path.join(self.tmp, "sys/class/power_supply/AC"))
        self.p("sys/class/power_supply/AC/type", "Mains\n")
        self.assertIn("Battery present: no", hw.build_report())


class RedactCheck(Fake):
    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = hw.main(list(args))
        return rc, out.getvalue(), err.getvalue()

    def test_scan_catches_each_kind(self):
        for bad in ["aa:bb:cc:dd:ee:ff", "AA-BB-CC-DD-EE-FF", "192.168.0.10", "2001:db8:0:0:0:0:0:1", "fe80::1a2b:3c4d",
                    "aabbccdd-1111-2222-3333-444455556666", "0123456789abcdef0123", "Serial Number: 12345"]:
            self.assertTrue(hw.redact_scan("x " + bad + " y"), bad)

    def test_scan_catches_forms_that_slipped_through(self):
        for bad in ["2001:db8::", "fe80::", "1::", "gateway 10.0.0.1.", "mac aabbccddeeff here", "AABBCCDDEEFF",
                    "aabb.ccdd.eeff", "{AABBCCDD-1111-2222-3333-444455556666}"]:
            self.assertTrue(hw.redact_scan("x " + bad + " y"), bad)
            self.assertNotEqual(hw.scrub(bad), bad, bad)

    def test_scan_ignores_normal_lines(self):
        ok = "- GPU: 8086:9b41, driver i915\n- Kernel: 6.12.0-225.el10.x86_64\n- BIOS date: 10/23/2020\n- mode 1920x1080@60.00*"
        self.assertEqual(hw.redact_scan(ok), [])

    def test_cli_passes_on_clean_report(self):
        self.full_tree()
        rc, out, err = self.run_cli("--redact-check")
        self.assertEqual(rc, 0)
        self.assertIn("# Nizho hardware report", out)
        self.assertIn("Redaction check passed", err)

    def test_cli_fails_on_injected_mac_and_ipv4(self):
        path = os.path.join(self.tmp, "report.md")
        put(self.tmp, "report.md", "# report\n- Wi-Fi: de:ad:be:ef:00:01 at 10.1.2.3\n")
        rc, out, err = self.run_cli("--redact-check", path)
        self.assertEqual(rc, 1)
        self.assertIn("FAILED", err)
        self.assertIn("de:ad:be:ef:00:01", err)
        self.assertIn("10.1.2.3", err)

    def test_cli_missing_file(self):
        rc, _, err = self.run_cli("--redact-check", os.path.join(self.tmp, "nope.md"))
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
