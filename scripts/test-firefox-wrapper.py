#!/usr/bin/env python3
"""Check sandbox launch arguments using a fake bwrap and temporary home."""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
PROFILE = '.local/share/nizho/firefox-profile'


HAS_DBUS_DAEMON = os.access('/usr/bin/dbus-daemon', os.X_OK)
needs_dbus_daemon = unittest.skipUnless(HAS_DBUS_DAEMON, '/usr/bin/dbus-daemon is not installed')


class WrapperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nizho-browser-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'home with spaces'
        self.home.mkdir()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        fake = self.bin / 'bwrap'
        fake.write_text('#!/usr/bin/python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n')
        fake.chmod(0o755)
        self.wrapper = Path(os.environ.get('NIZHO_BROWSER_WRAPPER_SOURCE',
            Path(__file__).resolve().parents[1] / 'system' / 'nizho-firefox'))
        self.env = dict(os.environ, HOME=str(self.home), WAYLAND_DISPLAY='wayland-0',
                        PATH=str(self.bin) + ':/usr/bin:/bin', NIZHO_TEST_SECRET='synthetic-secret')

    def run_wrapper(self, *args):
        return subprocess.run(['bash', str(self.wrapper), *args], env=self.env,
                              capture_output=True, text=True, timeout=5)

    def plain_home(self):
        """A home path with no spaces, which is what turns the private bus on."""
        self.home = self.root / 'home'
        self.home.mkdir()
        self.env['HOME'] = str(self.home)
        return self.home

    @staticmethod
    def pairs(args, flag):
        return [(args[i + 1], args[i + 2]) for i, arg in enumerate(args) if arg == flag]

    def test_environment_is_allowlisted_and_capabilities_are_dropped(self):
        result = self.run_wrapper('https://example.invalid/')
        self.assertEqual(result.returncode, 0, result.stderr)
        args = json.loads(result.stdout)
        self.assertIn('--clearenv', args)
        self.assertEqual(args[args.index('--cap-drop') + 1], 'ALL')
        names = {args[i + 1] for i, value in enumerate(args) if value == '--setenv'}
        self.assertEqual(names, {'PATH', 'HOME', 'LANG', 'LC_ALL', 'XDG_RUNTIME_DIR',
                                 'WAYLAND_DISPLAY', 'MOZ_ENABLE_WAYLAND', 'MOZ_LEGACY_HOME',
                                 'PULSE_SERVER'})
        self.assertNotIn('synthetic-secret', args)
        self.assertIn('--new-session', args)

    def test_audio_sockets_and_server_are_explicit_without_exposing_runtime_directory(self):
        self.env['PULSE_SERVER'] = 'tcp:untrusted.invalid'
        args = json.loads(self.run_wrapper().stdout)
        runtime = f'/run/user/{os.getuid()}'
        binds = [(args[i + 1], args[i + 2]) for i, arg in enumerate(args) if arg == '--bind-try']
        for socket in [f'{runtime}/pulse/native', f'{runtime}/pipewire-0']:
            self.assertIn((socket, socket), binds)
        self.assertNotIn((runtime, runtime), binds)
        self.assertNotIn((f'{runtime}/pulse', f'{runtime}/pulse'), binds)
        pos = args.index('PULSE_SERVER')
        self.assertEqual(args[pos + 1], f'unix:{runtime}/pulse/native')
        self.assertNotIn('tcp:untrusted.invalid', args)

    def test_downloads_symlink_cannot_expose_the_whole_home(self):
        (self.home / 'Downloads').symlink_to(self.home, target_is_directory=True)
        result = self.run_wrapper()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('symlink', result.stderr)
        self.assertEqual(result.stdout, '')

    def test_downloads_symlink_to_another_directory_is_rejected(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (self.home / 'Downloads').symlink_to(outside, target_is_directory=True)
        self.assertNotEqual(self.run_wrapper().returncode, 0)

    def test_socket_path_cannot_request_arbitrary_host_binds(self):
        for name in ['../../other', '/tmp/socket', '..', '']:
            if not name:
                continue  # An empty value intentionally selects wayland-0.
            self.env['WAYLAND_DISPLAY'] = name
            self.assertNotEqual(self.run_wrapper().returncode, 0, name)

    def test_shell_and_browser_use_the_same_boundary(self):
        normal = json.loads(self.run_wrapper().stdout)
        shell = json.loads(self.run_wrapper('--shell', '-c', 'true').stdout)
        self.assertEqual(normal[:-1], shell[:-3])
        self.assertEqual(shell[-3:], ['/bin/bash', '-c', 'true'])
        private = json.loads(self.run_wrapper('--private-session').stdout)
        shell = json.loads(self.run_wrapper('--private-session', '--shell', '-c', 'true').stdout)
        self.assertEqual(private[:-1], shell[:-3])

    def test_browser_arguments_are_preserved_without_shell_evaluation(self):
        url = 'https://example.invalid/;touch /tmp/never-run'
        args = json.loads(self.run_wrapper(url).stdout)
        self.assertEqual(args[-1], url)
        self.assertEqual(args[args.index('--bind') + 1:args.index('--bind') + 3],
                         [str(self.home / 'Downloads')] * 2)
        private = json.loads(self.run_wrapper('--private-session', url).stdout)
        self.assertEqual(private[-2:], ['/usr/bin/firefox-unwrapped', url])
        self.assertNotIn('--private-session', private)

    def test_saved_profile_is_bound_at_dot_mozilla_and_created_private(self):
        result = self.run_wrapper()
        self.assertEqual(result.returncode, 0, result.stderr)
        args = json.loads(result.stdout)
        profile = self.home / PROFILE
        self.assertEqual(self.pairs(args, '--bind'),
                         [(str(self.home / 'Downloads'),) * 2, (str(profile), str(self.home / '.mozilla'))])
        self.assertEqual(stat.S_IMODE(profile.stat().st_mode), 0o700)
        self.assertEqual(profile.stat().st_uid, os.getuid())
        # No tmpfs sits on the profile or on anything under the home folder.
        tmpfs = [args[i + 1] for i, arg in enumerate(args) if arg == '--tmpfs']
        self.assertEqual(tmpfs, ['/etc', '/tmp', '/run', '/home', '/root'])
        # The bind comes after the /home tmpfs, or the tmpfs would cover it.
        self.assertGreater(args.index(str(profile)), args.index('/home'))
        self.assertEqual(args[args.index('MOZ_LEGACY_HOME') + 1], '1')

    def test_home_reached_through_a_symlink_like_fedora_home_works(self):
        # Fedora bootc has /home -> /var/home. HOME may name either path.
        real = self.root / 'var' / 'home' / 'user'
        real.mkdir(parents=True)
        (self.root / 'home').symlink_to(self.root / 'var' / 'home', target_is_directory=True)
        for home in [self.root / 'home' / 'user', real]:
            self.env['HOME'] = str(home)
            result = self.run_wrapper('https://example.invalid/')
            self.assertEqual(result.returncode, 0, result.stderr)
            args = json.loads(result.stdout)
            self.assertIn((str(home / PROFILE), str(home / '.mozilla')), self.pairs(args, '--bind'))
            self.assertIn((str(home / 'Downloads'), str(home / 'Downloads')), self.pairs(args, '--bind'))
            self.assertIn(('--dir', str(home)), list(zip(args, args[1:])))
        self.assertEqual(stat.S_IMODE((real / PROFILE).stat().st_mode), 0o700)

    def test_an_existing_profile_folder_is_reused_and_its_contents_kept(self):
        profile = self.home / PROFILE
        profile.mkdir(parents=True)
        (profile / 'keep').write_text('bookmarks')
        self.assertEqual(self.run_wrapper().returncode, 0)
        self.assertEqual((profile / 'keep').read_text(), 'bookmarks')

    def test_private_session_binds_no_host_profile(self):
        self.plain_home()
        result = self.run_wrapper('--private-session')
        self.assertEqual(result.returncode, 0, result.stderr)
        args = json.loads(result.stdout)
        self.assertEqual(self.pairs(args, '--bind'), [(str(self.home / 'Downloads'),) * 2])
        self.assertFalse((self.home / '.local').exists())
        self.assertNotIn('.mozilla', ' '.join(args))
        self.assertNotIn('DBUS_SESSION_BUS_ADDRESS', args)
        self.assertNotIn('/etc/passwd', args)
        self.assertEqual(args[-1], '/usr/bin/firefox-unwrapped')

    @needs_dbus_daemon
    def test_private_session_differs_from_the_saved_one_only_by_the_profile(self):
        self.plain_home()
        saved = json.loads(self.run_wrapper('--shell', '-c', 'true').stdout)
        private = json.loads(self.run_wrapper('--private-session', '--shell', '-c', 'true').stdout)
        extra = ['--bind', str(self.home / PROFILE), str(self.home / '.mozilla'),
                 '--setenv', 'DBUS_SESSION_BUS_ADDRESS', f'unix:path={self.home}/.mozilla/nizho-bus',
                 '--ro-bind-data', '8', '/etc/passwd']
        cut = saved.index('--chdir')
        self.assertEqual(saved[cut - len(extra):cut], extra)
        self.assertEqual(saved[:cut - len(extra)] + saved[cut:], private)

    def test_symlinked_profile_folder_is_refused(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (self.home / '.local/share/nizho').mkdir(parents=True)
        (self.home / PROFILE).symlink_to(outside, target_is_directory=True)
        result = self.run_wrapper()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('not a link', result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertEqual(list(outside.iterdir()), [])

    def test_profile_reached_through_a_symlinked_parent_is_refused(self):
        outside = self.root / 'outside'
        (outside / 'nizho/firefox-profile').mkdir(parents=True)
        (self.home / '.local').mkdir()
        (self.home / '.local/share').symlink_to(outside, target_is_directory=True)
        result = self.run_wrapper()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('outside its expected place', result.stderr)
        self.assertEqual(result.stdout, '')

    def test_profile_path_that_is_a_file_is_refused(self):
        (self.home / '.local/share/nizho').mkdir(parents=True)
        (self.home / PROFILE).write_text('')
        result = self.run_wrapper()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('not a folder', result.stderr)
        self.assertEqual(result.stdout, '')

    def test_profile_folder_owned_by_another_user_is_refused(self):
        # A real foreign-owned folder needs root, so a fake `stat` reports one.
        fake = self.bin / 'stat'
        fake.write_text('#!/bin/sh\necho 4294967294\n')
        fake.chmod(0o755)
        result = self.run_wrapper()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('another user', result.stderr)
        self.assertEqual(result.stdout, '')

    def test_private_session_ignores_a_bad_profile_folder(self):
        (self.home / '.local/share/nizho').mkdir(parents=True)
        (self.home / PROFILE).symlink_to(self.root, target_is_directory=True)
        self.assertEqual(self.run_wrapper('--private-session').returncode, 0)

    def test_host_dbus_and_network_rules_are_unchanged(self):
        self.plain_home()
        runtime = f'/run/user/{os.getuid()}'
        for mode in [(), ('--private-session',)]:
            args = json.loads(self.run_wrapper(*mode).stdout)
            # The host buses are never bound, and the private one lives in the profile.
            joined = '\n'.join(args)
            self.assertNotIn(f'{runtime}/bus', joined)
            self.assertNotIn('/run/dbus', joined)
            self.assertNotIn('/var/run', joined)
            self.assertEqual(sorted(dst for _, dst in self.pairs(args, '--bind-try')),
                             sorted([f'{runtime}/wayland-0', f'{runtime}/pipewire-0', f'{runtime}/pulse/native']))
            # The network namespace was shared before and still is; the other namespaces stay.
            unshare = sorted(a for a in args if a.startswith('--unshare') or a == '--share-net')
            self.assertEqual(unshare, ['--unshare-ipc', '--unshare-pid', '--unshare-uts'])
            self.assertIn('--die-with-parent', args)
            # /etc is still the same allowlist.
            etc = sorted(dst for _, dst in self.pairs(args, '--ro-bind-try'))
            self.assertEqual(etc, sorted('/etc/' + n for n in [
                'resolv.conf', 'hosts', 'nsswitch.conf', 'ssl', 'pki', 'fonts', 'localtime',
                'ld.so.cache', 'ld.so.conf.d', 'alternatives']))
            self.assertEqual(self.pairs(args, '--ro-bind'),
                             [(d, d) for d in ['/usr', '/lib', '/lib64', '/bin', '/sbin']])

    @needs_dbus_daemon
    def test_first_saved_launch_starts_the_private_bus_and_a_second_does_not(self):
        self.plain_home()
        first = json.loads(self.run_wrapper('https://example.invalid/').stdout)
        self.assertEqual(first[-6:-4], ['/bin/bash', '-c'])
        self.assertIn('dbus-daemon', first[-4])
        self.assertIn('<listen>', first[-4])
        self.assertNotIn('eavesdrop', first[-4])
        self.assertEqual(first[-3:], ['nizho-firefox', '/usr/bin/firefox-unwrapped', 'https://example.invalid/'])
        # Hold the launch lock the way a running sandbox does.
        lock = self.home / '.local/share/nizho/firefox-profile.lock'
        holder = subprocess.Popen(['flock', str(lock), 'sleep', '30'])
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        for _ in range(50):
            if subprocess.run(['flock', '-n', str(lock), 'true']).returncode:
                break
            subprocess.run(['sleep', '0.1'])
        # A stand-in for the first sandbox's bus socket, so the wrapper does not wait for one.
        import socket
        bus = socket.socket(socket.AF_UNIX)
        self.addCleanup(bus.close)
        bus.bind(str(self.home / PROFILE / 'nizho-bus'))
        second = json.loads(self.run_wrapper('https://example.invalid/2').stdout)
        self.assertEqual(second[-2:], ['/usr/bin/firefox-unwrapped', 'https://example.invalid/2'])
        self.assertEqual(second[:-2], first[:-6])
        self.assertTrue((self.home / PROFILE / 'nizho-bus').exists())

    @needs_dbus_daemon
    def test_made_up_passwd_line_holds_only_ids_and_home(self):
        self.plain_home()
        fake = self.bin / 'bwrap'
        fake.write_text('#!/usr/bin/python3\nimport os\nprint(os.read(8, 4096).decode(), end="")\n')
        result = self.run_wrapper('--shell')
        self.assertEqual(result.stdout,
                         f'user:x:{os.getuid()}:{os.getgid()}::{self.home}:/usr/sbin/nologin\n')

    @unittest.skipUnless(shutil.which('bwrap'), 'bubblewrap is not installed')
    def test_real_bubblewrap_shows_the_profile_and_downloads_and_nothing_else(self):
        # Skip where bubblewrap cannot build this kind of sandbox at all (no
        # unprivileged namespaces, or a system without these folders).
        probe = ['bwrap', '--unshare-pid', '--proc', '/proc', '--dev', '/dev']
        for folder in ['/usr', '/lib', '/lib64', '/bin', '/sbin']:
            probe += ['--ro-bind', folder, folder]
        if subprocess.run(probe + ['/bin/true'], capture_output=True).returncode:
            self.skipTest('bubblewrap cannot build a sandbox on this machine')
        self.plain_home()
        (self.home / 'secret.txt').write_text('secret')
        self.env['PATH'] = '/usr/bin:/bin'
        probe = 'cd "$HOME" && ls -A && echo kept > .mozilla/kept && test ! -e /run/user/*/bus'
        result = subprocess.run(['bash', str(self.wrapper), '--shell', '-c', probe], env=self.env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(result.stdout.split()), ['.mozilla', 'Downloads'])
        self.assertEqual((self.home / PROFILE / 'kept').read_text(), 'kept\n')
        result = subprocess.run(['bash', str(self.wrapper), '--private-session', '--shell', '-c',
                                 'cd "$HOME" && ls -A && mkdir .mozilla && echo gone > .mozilla/gone'],
                                env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), ['Downloads'])
        self.assertFalse((self.home / PROFILE / 'gone').exists())
        self.assertFalse((self.home / '.mozilla').exists())


class PolicyTests(unittest.TestCase):
    def test_policies_turn_off_telemetry_studies_and_browser_doh(self):
        policies = json.loads((REPO / 'system' / 'firefox-policies.json').read_text())['policies']
        self.assertIs(policies['DisableTelemetry'], True)
        self.assertIs(policies['DisableFirefoxStudies'], True)
        self.assertEqual(policies['DNSOverHTTPS'], {'Enabled': False, 'Locked': True})
        self.assertIs(policies['EnableTrackingProtection']['Value'], True)


class DesktopFileTests(unittest.TestCase):
    PATH = REPO / 'system' / 'org.mozilla.firefox.desktop'

    def sections(self):
        out, name = {}, None
        for line in self.PATH.read_text().splitlines():
            if line.startswith('[') and line.endswith(']'):
                name = line[1:-1]
                out[name] = {}
            elif '=' in line and name:
                key, value = line.split('=', 1)
                out[name][key] = value
        return out

    def test_private_session_action_is_listed_and_defined(self):
        sections = self.sections()
        entry = sections['Desktop Entry']
        self.assertEqual(entry['Exec'], 'firefox %u')
        actions = entry['Actions'].rstrip(';').split(';')
        self.assertEqual(actions, ['new-window', 'new-private-window', 'profile-manager-window',
                                   'nizho-private-session'])
        for action in actions:
            self.assertIn(f'Desktop Action {action}', sections)
        ours = sections['Desktop Action nizho-private-session']
        self.assertEqual(ours, {'Name': 'New private session, nothing saved',
                                'Exec': 'firefox --private-session %u'})

    def test_every_launch_goes_through_the_wrapper(self):
        for name, keys in self.sections().items():
            if 'Exec' in keys:
                self.assertTrue(keys['Exec'].startswith('firefox '), name)

    def test_the_image_installs_it_over_the_packaged_launcher(self):
        text = (REPO / 'Containerfile').read_text()
        line = 'COPY system/org.mozilla.firefox.desktop /usr/share/applications/org.mozilla.firefox.desktop'
        self.assertEqual(text.count(line), 1)
        # It must sit in the files stage, which is copied in after Firefox is installed.
        files = text.index('FROM scratch AS files')
        self.assertTrue(files < text.index(line) < text.index('FROM scratch AS bins'))
        self.assertLess(text.index('dnf install --setopt=keepcache=True -y firefox bubblewrap'),
                        text.index('COPY --from=files / /'))

    @unittest.skipUnless(shutil.which('desktop-file-validate'), 'desktop-file-validate is not installed')
    def test_it_is_a_valid_desktop_file(self):
        result = subprocess.run(['desktop-file-validate', str(self.PATH)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
