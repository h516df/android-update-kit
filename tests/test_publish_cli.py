import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_release_remote import FakeGitHub, TARGET

KIT = Path(__file__).parents[1]
sys.path.insert(0, str(KIT / 'scripts'))
spec = importlib.util.spec_from_file_location('publisher', KIT / 'scripts/publish_release.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


class PublishCliTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='.release-test-', dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.assertEqual(Path(__file__).parent.resolve(), self.root.resolve().parent)
        self.apk = self.root / 'reviewed.apk'
        self.apk.write_bytes(b'isolated unit-test APK fixture')
        (self.root / 'app').mkdir()
        (self.root / 'app/build.gradle.kts').write_text('versionName = "1.0"\nversionCode = 2', encoding='utf-8')
        self.notes = self.root / 'notes.json'
        self.notes.write_text(json.dumps({'versionName': '1.0', 'versionCode': 2, '新增': ['更新']}, ensure_ascii=False), encoding='utf-8')
        self.config = self.root / 'UpdateConfig.kt'
        self.config.write_text('const val REPO = "owner/app"\nconst val APK_PREFIX = "App"\nconst val PACKAGE = "cn.sample"', encoding='utf-8')
        for key, value in {'ROOT': self.root, 'DIST': self.root / 'dist', 'NOTES': self.notes, 'CONFIG': self.config}.items():
            patcher = patch.object(p, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def call(self, *args):
        with patch.object(sys, 'argv', ['publisher', *args]):
            return p.main()

    def test_no_build_requires_explicit_reviewed_apk(self):
        with self.assertRaisesRegex(SystemExit, 'prepared-apk'):
            self.call('--no-build')

    def test_publish_requires_target_and_original_signer_before_any_build(self):
        with patch.object(p, 'run') as run:
            with self.assertRaisesRegex(SystemExit, 'target'):
                self.call()
            with self.assertRaisesRegex(SystemExit, 'signer-sha256'):
                self.call('--target', TARGET)
            run.assert_not_called()

    def test_cli_verify_published_version_performs_no_remote_mutation(self):
        args = ['--no-build', '--prepared-apk', str(self.apk)]
        self.call(*args, '--dry-run')
        out = self.root / 'dist/v1.0'
        assets = [out / 'App-v1.0-universal.apk', out / 'App-v1.0-release-notes.json', out / 'App-v1.0-SHA256.txt']
        fake = FakeGitHub(assets, {'id': 1, 'tag_name': 'v1.0', 'target_commitish': TARGET,
                                  'draft': False, 'prerelease': False, 'published_at': '2026-10-12',
                                  'assets': [], 'html_url': 'https://github.com/owner/app/releases/tag/v1.0'},
                          [{'ref': 'refs/tags/v1.0', 'object': {'type': 'commit', 'sha': TARGET}}])
        fake.release['assets'] = [fake.asset(path.name) for path in assets]
        with patch.object(p, 'run', side_effect=fake), patch.object(p, 'verify_apk') as verify:
            self.call(*args, '--verify-only', '--target', TARGET, '--signer-sha256', 'b' * 64)
            verify.assert_called_once()
        self.assertEqual([], fake.writes)
        self.assertTrue(json.loads((out / 'publication.json').read_text(encoding='utf-8'))['read_only'])
        self.assertIn('App-v1.0-universal.apk', assets[2].read_text(encoding='utf-8'))

    def test_installer_includes_recovery_module_without_network(self):
        result = subprocess.run([sys.executable, '-X', 'utf8', str(KIT / 'tools/install_kit.py'),
                                 str(self.root), '--repo', 'owner/app', '--repo-id', '1',
                                 '--package', 'cn.sample', '--prefix', 'App'], capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr.decode('utf-8', errors='replace'))
        self.assertEqual((KIT / 'scripts/release_remote.py').read_bytes(),
                         (self.root / 'scripts/release_remote.py').read_bytes())

    def test_actual_apk_metadata_and_original_certificate_are_required(self):
        tools = self.root / 'sdk/build-tools/36.0.0'
        tools.mkdir(parents=True)
        (tools / ('aapt.exe' if sys.platform.startswith('win') else 'aapt')).touch()
        good = "package: name='cn.sample' versionCode='2' versionName='1.0'"
        signature = 'Signer #1 certificate SHA-256 digest: ' + 'b' * 64
        with patch.dict('os.environ', {'ANDROID_HOME': str(self.root / 'sdk')}):
            with patch.object(p, 'run', side_effect=[good, signature]):
                p.verify_apk(self.apk, '1.0', 2, 'b' * 64)
            for badging in (good.replace('cn.sample', 'other.app'), good+'\napplication-debuggable'):
                with patch.object(p, 'run', return_value=badging), self.assertRaises(SystemExit):
                    p.verify_apk(self.apk, '1.0', 2, 'b' * 64)
            with patch.object(p, 'run', side_effect=[good, signature]), self.assertRaisesRegex(SystemExit, '签名'):
                p.verify_apk(self.apk, '1.0', 2, 'c' * 64)


if __name__ == '__main__':
    unittest.main()
