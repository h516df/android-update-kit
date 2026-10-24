"""Regression for mixed Windows transport failures; never uses real credentials or GitHub."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import release_transport as transport


class ChannelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'fixture.apk'
        self.path.write_bytes(b'synthetic APK bytes')
        self.asset = dict(id=123, name=self.path.name, state='uploaded',
                          size=self.path.stat().st_size, digest='sha256:' + transport.sha(self.path),
                          browser_download_url='https://github.com/owner/app/releases/download/v1.0/fixture.apk')

    def test_legacy_transport_still_selects_all_three_channels(self):
        instance = transport.Runner({}, upload_transport='urllib')
        self.assertEqual(('urllib', 'urllib', 'urllib', 'gh'),
                         (instance.metadata_transport, instance.upload_transport,
                          instance.download_transport, instance.write_transport))

    def test_metadata_can_use_urllib_while_public_download_uses_curl_without_auth(self):
        instance = transport.Runner({}, metadata_transport='urllib', download_transport='curl')
        with patch.object(instance, 'auth_config', return_value='synthetic-auth'), patch.object(
                transport, 'urllib_metadata', return_value='{"id":1}') as read, patch.object(
                transport.subprocess, 'run') as native:
            self.assertEqual({'id': 1}, json.loads(instance.api_get(['gh','api','repos/owner/app'])))
            read.assert_called_once()
            native.assert_not_called()
        with patch.object(instance, 'gh', return_value=json.dumps({'draft':False,'assets':[self.asset]})), patch.object(
                instance, 'auth_config') as auth, patch.object(transport, 'curl_download', return_value={}) as download:
            instance.verify_downloads('owner/app','v1.0',[self.path],Path(self.temp.name)/'downloads',True)
            auth.assert_not_called()
            self.assertEqual('', download.call_args.args[3])
            self.assertEqual('curl', download.call_args.kwargs['backend'])

    def test_upload_choice_does_not_follow_metadata_choice(self):
        instance = transport.Runner({}, upload_transport='urllib', metadata_transport='curl')
        replies = [json.dumps([[{'id':1,'tag_name':'v1.0','draft':True,'assets':[]}]]),
                   'synthetic-credential', json.dumps({'assets':[self.asset]})]
        with patch.object(instance,'gh',side_effect=replies), patch.object(
                transport,'urllib_upload',return_value=({'status':'201'},'')) as upload, patch.object(
                transport,'curl_upload') as other:
            instance.upload('v1.0',self.path,'owner/app')
            upload.assert_called_once()
            other.assert_not_called()

    def payload(self):
        path = Path(self.temp.name)/'release.json'
        path.write_text(json.dumps({'draft':True,'name':'synthetic release'}))
        return ['gh','api','repos/owner/app/releases','--method','POST','--input',str(path)]

    def test_explicit_rest_write_uses_stdin_auth_and_never_follows_redirects(self):
        instance = transport.Runner({},write_transport='curl')
        with patch.object(instance,'auth_config',return_value='synthetic-auth'), patch.object(
                transport.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'{"id":7}\n201','')) as call:
            self.assertEqual({'id':7},json.loads(instance(self.payload())))
            args = call.call_args.args[0]
            self.assertNotIn('synthetic-auth',' '.join(args))
            self.assertNotIn('--location',args)
            self.assertEqual('synthetic-auth',call.call_args.kwargs['input'])
            self.assertEqual(1,instance.mutations)

    def test_uncertain_write_never_replays_or_exposes_response(self):
        instance = transport.Runner({},write_transport='curl')
        with patch.object(instance,'auth_config',return_value='synthetic-auth'), patch.object(
                transport.subprocess,'run',return_value=subprocess.CompletedProcess([],28,'private response\n000','')) as call:
            with self.assertRaisesRegex(RuntimeError,'核对远端') as error:
                instance(self.payload())
            self.assertNotIn('private response',str(error.exception))
            self.assertEqual(1,call.call_count)
            self.assertEqual(1,instance.mutations)

    def test_release_rest_write_refuses_other_endpoints_before_auth(self):
        instance = transport.Runner({},write_transport='curl')
        args=self.payload();args[2]='repos/owner/app/actions/secrets'
        with patch.object(instance,'auth_config') as auth, patch.object(transport.subprocess,'run') as call:
            with self.assertRaises(ValueError): instance(args)
            auth.assert_not_called();call.assert_not_called()
            self.assertEqual(0,instance.mutations)


if __name__ == '__main__':
    unittest.main()
