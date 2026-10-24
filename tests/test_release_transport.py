import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import subprocess
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('release_runner', ROOT / 'scripts/release_transport.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class TransferChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'tests', prefix='transfer-test-')
        self.addCleanup(self.temp.cleanup)
        self.file = Path(self.temp.name) / 'fixture.apk'
        self.file.write_bytes(b'synthetic release fixture\n' * 10000)

    def server(self, pause=False):
        received = []
        finish = threading.Event()
        completed = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def handle(self):
                try:
                    super().handle()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

            def do_POST(self):
                received.append(self.rfile.read(int(self.headers['Content-Length'])))
                if pause:
                    finish.wait(timeout=5)
                try:
                    payload = b'{"ok":true}'
                    self.send_response(201)
                    self.send_header('Content-Length', str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
                finally:
                    completed.set()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def cleanup():
            finish.set()
            completed.wait(timeout=3)
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.addCleanup(cleanup)
        env = {k: v for k, v in os.environ.items() if 'proxy' not in k.lower()}
        env['NO_PROXY'] = '127.0.0.1'
        return f'http://127.0.0.1:{server.server_port}/fixture', received, env

    def test_native_curl_delivers_exact_bytes_and_reports_measurements(self):
        url, received, env = self.server()
        events = []
        measured, body = runner.curl_upload(url, self.file, '', env, stall=5, deadline=10,
                                            notify=events.append)
        self.assertEqual([self.file.read_bytes()], received)
        self.assertEqual('201', measured['status'])
        self.assertEqual(self.file.stat().st_size, measured['bytes'])
        self.assertEqual(0, measured['exit'])
        self.assertIsNone(measured['stopped'])
        self.assertTrue(json.loads(body)['ok'])
        self.assertTrue(events)

    def test_stalled_child_is_stopped_and_never_reports_success(self):
        url, _, env = self.server(pause=True)
        measured, _ = runner.curl_upload(url, self.file, '', env, stall=1, deadline=8,
                                         notify=lambda _: None)
        self.assertNotEqual(0, measured['exit'])
        self.assertNotEqual('201', measured['status'])
        self.assertEqual('传输进度长时间未增加', measured['stopped'])

    def test_explicit_streaming_alternative_delivers_exact_bytes(self):
        url,received,env=self.server()
        measured,body=runner.urllib_upload(url,self.file,'',env,stall=5,deadline=10,notify=lambda _:None)
        self.assertEqual([self.file.read_bytes()],received)
        self.assertEqual('201',measured['status']);self.assertTrue(json.loads(body)['ok'])

    def test_uncertain_upload_rechecks_remote_and_accepts_only_matching_asset(self):
        instance = runner.Runner({}, notify=lambda _: None)
        expected = {'name': self.file.name, 'state': 'uploaded', 'size': self.file.stat().st_size,
                    'digest': 'sha256:' + runner.sha(self.file)}
        replies = [json.dumps([[{'id': 123, 'tag_name':'v1.0', 'draft': True, 'assets': []}]]),
                   'fixture-not-a-real-token', json.dumps({'assets': [expected]})]
        with patch.object(instance, 'gh', side_effect=replies) as gh, patch.object(
                runner, 'curl_upload', return_value=({'status': '408'}, '')) as transfer:
            instance.upload('v1.0', self.file, 'h516df/fixture')
        self.assertEqual(1, transfer.call_count)
        self.assertEqual(3, gh.call_count)
        self.assertIn('releases/123', gh.call_args.args[0][-1])

    def test_starter_asset_is_preserved_without_auth_or_transfer(self):
        instance = runner.Runner({}, notify=lambda _: None)
        reply = json.dumps([[{'id': 123, 'tag_name':'v1.0', 'draft': True,
                            'assets': [{'name': self.file.name, 'state': 'starter'}]}]])
        with patch.object(instance, 'gh', return_value=reply) as gh, patch.object(
                runner, 'curl_upload') as transfer:
            with self.assertRaisesRegex(RuntimeError, '不删除、不覆盖'):
                instance.upload('v1.0', self.file, 'h516df/fixture')
        self.assertEqual(1, gh.call_count)
        transfer.assert_not_called()
        self.assertEqual(0, instance.mutations)

    def test_selected_draft_uses_its_id_without_eventually_consistent_list(self):
        instance=runner.Runner({},notify=lambda _:None)
        target='a'*40
        instance.select_release('h516df/fixture','v1.0',target,123)
        expected={'name':self.file.name,'state':'uploaded','size':self.file.stat().st_size,'digest':'sha256:'+runner.sha(self.file)}
        replies=[json.dumps(dict(id=123,tag_name='v1.0',target_commitish=target,draft=True,assets=[])),
                 'fixture-not-a-real-token',json.dumps({'assets':[expected]})]
        with patch.object(instance,'gh',side_effect=replies) as gh,patch.object(runner,'curl_upload',return_value=({'status':'201'},'')) as transfer:
            instance.upload('v1.0',self.file,'h516df/fixture')
        self.assertEqual('repos/h516df/fixture/releases/123',gh.call_args_list[0].args[0][2])
        self.assertEqual(1,transfer.call_count)

    def test_selected_draft_mismatch_stops_before_auth_or_transfer(self):
        instance=runner.Runner({})
        instance.select_release('h516df/fixture','v1.0','a'*40,123)
        with patch.object(instance,'gh',return_value=json.dumps(dict(id=123,tag_name='v2.0',target_commitish='a'*40,draft=True,assets=[]))) as gh,patch.object(runner,'curl_upload') as transfer:
            with self.assertRaisesRegex(RuntimeError,'身份发生变化'):
                instance.upload('v1.0',self.file,'h516df/fixture')
        self.assertEqual(1,gh.call_count)
        transfer.assert_not_called()
        self.assertEqual(0,instance.mutations)

    def test_process_error_after_start_also_rechecks_without_retry(self):
        instance = runner.Runner({}, notify=lambda _: None)
        replies = [json.dumps([[{'id': 123, 'tag_name':'v1.0', 'draft': True, 'assets': []}]]),
                   'fixture-not-a-real-token', json.dumps({'assets': []})]
        with patch.object(instance, 'gh', side_effect=replies) as gh, patch.object(
                runner, 'curl_upload', side_effect=BrokenPipeError('fixture disconnect')) as transfer:
            with self.assertRaisesRegex(RuntimeError, '不自动重传或删除'):
                instance.upload('v1.0', self.file, 'h516df/fixture')
        self.assertEqual(1, transfer.call_count)
        self.assertEqual(3, gh.call_count)

    def test_metadata_pages_keep_auth_out_of_arguments_and_stop_on_partial_response(self):
        instance=runner.Runner({})
        replies=[subprocess.CompletedProcess([],0,json.dumps([{}]*100)+'\n200',''),
                 subprocess.CompletedProcess([],0,'[]\n200','')]
        with patch.object(instance,'auth_config',return_value='fixture-auth'), patch.object(runner.subprocess,'run',side_effect=replies) as call:
            pages=json.loads(instance.api_get(['gh','api','repos/owner/app/releases?per_page=100','--paginate','--slurp']))
        self.assertEqual([100,0],list(map(len,pages)))
        for invocation in call.call_args_list:
            self.assertNotIn('fixture-auth',' '.join(invocation.args[0]))
            self.assertEqual('fixture-auth',invocation.kwargs['input'])
        with patch.object(instance,'auth_config',return_value='fixture-auth'), patch.object(runner.subprocess,'run',return_value=subprocess.CompletedProcess([],28,'{}\n200','')):
            with self.assertRaisesRegex(RuntimeError,'读取失败'):instance.api_get(['gh','api','repos/owner/app'])


    def test_cli_metadata_preserves_bounded_pagination_without_extracting_token(self):
        instance=runner.Runner({},metadata_transport='gh',write_transport='curl')
        replies=[subprocess.CompletedProcess([],0,json.dumps([{},{}]),''),subprocess.CompletedProcess([],0,'[]','')]
        with patch.object(instance,'auth_config',side_effect=AssertionError('CLI keeps its existing login')),patch.object(runner.subprocess,'run',side_effect=replies) as calls:
            result=instance(['gh','api','repos/owner/app/releases?per_page=2','--paginate','--slurp'])
        self.assertEqual([[{},{}],[]],json.loads(result))
        self.assertEqual(['gh','api','repos/owner/app/releases?per_page=2&page=1'],calls.call_args_list[0].args[0])
        self.assertTrue(all(c.kwargs['timeout']==5 and 'input' not in c.kwargs for c in calls.call_args_list))

    def test_cli_metadata_failure_does_not_retry_or_expose_raw_output(self):
        instance=runner.Runner({},metadata_transport='gh')
        with patch.object(runner.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'fixture-sensitive-output','fixture-private-url')) as call:
            with self.assertRaisesRegex(RuntimeError,'不记录原始响应') as error:
                instance(['gh','api','repos/owner/app'])
        self.assertNotIn('fixture-sensitive',str(error.exception))
        self.assertNotIn('fixture-private',str(error.exception))
        self.assertEqual(1,call.call_count)
        with patch.object(runner.subprocess,'run') as call:
            with self.assertRaisesRegex(RuntimeError,'明确仓库'):
                instance(['gh','api','https://example.invalid/resource'])
        call.assert_not_called()

if __name__ == '__main__':
    unittest.main(verbosity=2)
