import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

KIT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('installer', KIT / 'tools/install_kit.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallKitTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='.install-test-', dir=Path(__file__).parent)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / 'app').mkdir()
        self.config = self.root / 'app/src/main/java/dev/h516/update/UpdateConfig.kt'
        self.argv = [sys.executable, '-B', '-X', 'utf8', str(KIT / 'tools/install_kit.py'),
                     str(self.root), '--repo', 'owner/app', '--repo-id', '1',
                     '--package', 'cn.sample', '--prefix', 'App']

    def call(self, *extra):
        return subprocess.run(self.argv + list(extra), capture_output=True)

    def snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob('*') if path.is_file()}

    def put(self, relative, data):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_fresh_install_and_identical_repeat_preserve_bytes_and_mtime(self):
        self.assertEqual(0, self.call().returncode)
        config = self.config.read_text(encoding='utf-8')
        self.assertIn('const val REPO_ID = 1L', config)
        self.assertIn('const val PACKAGE = "cn.sample"', config)
        self.assertIn('const val PUBLIC_RELEASES = false', config)
        before = self.snapshot()
        times = {p: (self.root / p).stat().st_mtime_ns for p in before}
        self.assertEqual(0, self.call().returncode)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(times, {p: (self.root / p).stat().st_mtime_ns for p in before})

    def test_public_update_mode_is_explicit_repeatable_and_does_not_overwrite_private_config(self):
        self.assertEqual(0, self.call('--public-release').returncode)
        self.assertIn('const val PUBLIC_RELEASES = true', self.config.read_text(encoding='utf-8'))
        before = self.snapshot()
        self.assertEqual(0, self.call('--public-release').returncode)
        self.assertEqual(before, self.snapshot())
        self.assertNotEqual(0, self.call().returncode)
        self.assertEqual(before, self.snapshot())

    def test_changed_kotlin_aborts_before_any_file_write(self):
        self.put('app/src/main/java/dev/h516/update/Updater.kt', b'project custom updater')
        before = self.snapshot()
        result = self.call()
        self.assertNotEqual(0, result.returncode)
        self.assertEqual(before, self.snapshot())
        self.assertIn('Updater.kt', result.stderr.decode('utf-8'))

    def test_changed_publisher_and_xml_are_both_reported_and_preserved(self):
        self.put('scripts/publish_release.py', b'custom publisher')
        self.put('app/src/main/res/xml/update_paths.xml', b'custom XML')
        before = self.snapshot()
        result = self.call()
        self.assertNotEqual(0, result.returncode)
        self.assertEqual(before, self.snapshot())
        error = result.stderr.decode('utf-8')
        self.assertIn('publish_release.py', error)
        self.assertIn('update_paths.xml', error)

    def test_existing_release_notes_are_preserved(self):
        notes = self.put('app/src/main/assets/release-notes.json', b'project version notes')
        self.assertEqual(0, self.call().returncode)
        self.assertEqual(b'project version notes', notes.read_bytes())

    def test_changed_project_identity_is_not_silently_overwritten(self):
        self.assertEqual(0, self.call().returncode)
        before = self.snapshot()
        self.assertNotEqual(0, self.call('--repo', 'owner/different', '--repo-id', '2').returncode)
        self.assertEqual(before, self.snapshot())

    def test_invalid_repository_id_rejected_before_writes(self):
        for value in ('0', '-1', 'abc', '9223372036854775808'):
            with self.subTest(value=value):
                self.assertNotEqual(0, self.call('--repo-id', value).returncode)
                self.assertEqual({}, self.snapshot())

    def test_invalid_identifiers_rejected_before_writes(self):
        for flag, value in (('--repo', '../app'), ('--package', 'bad"package'),
                            ('--prefix', '../App'), ('--name', 'bad$interpolation')):
            with self.subTest(flag=flag):
                self.assertNotEqual(0, self.call(flag, value).returncode)
                self.assertEqual({}, self.snapshot())

    def test_directory_instead_of_target_file_aborts_all_writes(self):
        (self.root / 'scripts/publish_release.py').mkdir(parents=True)
        self.assertNotEqual(0, self.call().returncode)
        self.assertEqual({}, self.snapshot())

    def test_gh_failure_never_returns_zero_or_accepts_error_stdout(self):
        for completed in (subprocess.CompletedProcess([], 1, b'123'),
                          subprocess.CompletedProcess([], 0, b'0'),
                          subprocess.CompletedProcess([], 0, b'invalid')):
            with patch.object(installer.subprocess, 'run', return_value=completed):
                with self.assertRaises(SystemExit):
                    installer.repo_id_of('owner/app')
        with patch.object(installer.subprocess, 'run', side_effect=OSError('fixture failure')):
            with self.assertRaises(SystemExit):
                installer.repo_id_of('owner/app')
        with patch.object(installer.subprocess, 'run', side_effect=subprocess.TimeoutExpired('gh', 60)):
            with self.assertRaises(SystemExit):
                installer.repo_id_of('owner/app')

    def test_gh_positive_id_is_accepted(self):
        with patch.object(installer.subprocess, 'run',
                          return_value=subprocess.CompletedProcess([], 0, b'123\n')):
            self.assertEqual('123', installer.repo_id_of('owner/app'))

    def test_linked_destination_is_refused(self):
        outside = self.root / 'outside'
        outside.mkdir()
        if os.name == 'nt':
            script = self.put('create-junction.ps1',
                              b'param($LinkPath,$TargetPath)\n$ErrorActionPreference="Stop"\n'
                              b'New-Item -ItemType Junction -Path $LinkPath -Target $TargetPath | Out-Null\n')
            result = subprocess.run(['D:/Apps/PowerShell/pwsh.exe', '-NoProfile', '-NonInteractive',
                                     '-File', str(script), str(self.root / 'scripts'), str(outside)],
                                    capture_output=True)
            self.assertEqual(0, result.returncode, result.stderr.decode('utf-8', errors='replace'))
        else:
            (self.root / 'scripts').symlink_to(outside, target_is_directory=True)
        self.assertNotEqual(0, self.call().returncode)
        self.assertEqual([], list(outside.iterdir()))
        self.assertFalse(self.config.exists())

    def test_file_created_after_preflight_is_never_overwritten(self):
        path = self.put('scripts/publish_release.py', b'other writer created this')
        with self.assertRaises(FileExistsError):
            installer.write_new(path, b'kit content')
        self.assertEqual(b'other writer created this', path.read_bytes())
        self.assertEqual([], list(path.parent.glob('.update-kit-*')))

    def test_interrupted_new_install_can_resume_without_rewriting_existing_files(self):
        original = installer.write_new
        calls = 0
        def fail_second(path, data):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('fixture disk failure')
            original(path, data)
        with patch.object(sys, 'argv', self.argv[4:]), patch.object(installer, 'write_new', side_effect=fail_second):
            with self.assertRaisesRegex(SystemExit, '接入中断'):
                installer.main()
        self.assertEqual(1, len(self.snapshot()))
        existing = {p: (self.root / p).stat().st_mtime_ns for p in self.snapshot()}
        self.assertEqual(0, self.call().returncode)
        self.assertEqual(existing, {p: (self.root / p).stat().st_mtime_ns for p in existing})


if __name__ == '__main__':
    unittest.main()
