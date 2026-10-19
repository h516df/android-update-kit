import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).parents[1]
TEMPLATE=ROOT/'templates/verify-carried-release.py'

class CarriedReleaseGuards(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'tests',prefix='.carried-')
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)
        (self.path/'verify.py').write_bytes(TEMPLATE.read_bytes())
        (self.path/'release_assets.py').write_text("from pathlib import Path\ndef execute(*a,**k):\n Path(__file__).with_name('REMOTE_WRITE_ATTEMPTED').touch()\n raise RuntimeError('Publication must not be reached')\n")

    def run_guard(self,meta,spec,**environment):
        (self.path/'delivery.json').write_text(json.dumps(meta))
        (self.path/'release.json').write_text(json.dumps(spec))
        env={**os.environ,**environment}
        result=subprocess.run([sys.executable,str(self.path/'verify.py')],env=env,capture_output=True,text=True,timeout=15)
        self.assertNotEqual(0,result.returncode)
        self.assertIn('AssertionError',result.stderr)
        self.assertFalse((self.path/'REMOTE_WRITE_ATTEMPTED').exists())
        self.assertFalse(any(self.path.glob('*.apk')))

    def test_source_mismatch_stops_before_package_or_remote_access(self):
        self.run_guard({'sourceCommit':'a'*40,'publicRelease':False},{'target':'b'*40})

    def test_visibility_mode_must_be_a_real_boolean(self):
        self.run_guard({'sourceCommit':'a'*40,'publicRelease':'false'},{'target':'a'*40})

    def test_job_cannot_publish_to_a_different_repository(self):
        self.run_guard({'sourceCommit':'a'*40,'publicRelease':False},
                       {'target':'a'*40,'repo':'owner/approved'},GITHUB_REPOSITORY='owner/other')

if __name__=='__main__':unittest.main()
