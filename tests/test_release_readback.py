import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch

ROOT=Path(__file__).parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import release_assets
import release_transport as transport

class ReadbackTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'tests');self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'fixture.apk'
        self.content=b'synthetic apk fixture\n'*10000
        self.identity={'repo':'owner/app','tag':'v1.0','assetId':1,'size':len(self.content),'sha256':hashlib.sha256(self.content).hexdigest()}
        self.env={k:v for k,v in os.environ.items() if 'proxy' not in k.lower()}
        self.env['NO_PROXY']='127.0.0.1'

    def server(self):
        content=self.content;requests=[]
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                start=int(self.headers.get('Range','bytes=0-').split('=')[1].split('-')[0])
                requests.append(start)
                self.send_response(206 if start else 200)
                if start:self.send_header('Content-Range',f'bytes {start}-{len(content)-1}/{len(content)}')
                self.send_header('Content-Length',str(len(content)-start));self.end_headers();self.wfile.write(content[start:])
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def cleanup():server.shutdown();server.server_close();thread.join(2)
        self.addCleanup(cleanup)
        return f'http://127.0.0.1:{server.server_port}/fixture',requests

    def test_actual_readback_resume_and_cache_evidence_are_distinct(self):
        url,requests=self.server()
        partial=self.path.with_name(self.path.name+'.part');partial.write_bytes(self.content[:1000])
        self.path.with_name(self.path.name+'.part.json').write_text(json.dumps(self.identity))
        result=transport.curl_download(url,self.path,self.identity,'',self.env,deadline=10)
        self.assertEqual(self.content,self.path.read_bytes());self.assertEqual([1000],requests)
        self.assertTrue(result['downloadedThisRun']);self.assertEqual(1000,result['resumedFromBytes'])
        cached=transport.curl_download(url,self.path,self.identity,'',self.env)
        self.assertFalse(cached['downloadedThisRun']);self.assertEqual([1000],requests)

    def test_unknown_partial_and_wrong_complete_file_are_preserved(self):
        partial=self.path.with_name(self.path.name+'.part');partial.write_bytes(b'unknown')
        with self.assertRaisesRegex(RuntimeError,'归属'):
            transport.curl_download('https://invalid',self.path,self.identity,'',self.env)
        self.assertEqual(b'unknown',partial.read_bytes())
        self.path.write_bytes(b'existing')
        with self.assertRaisesRegex(RuntimeError,'拒绝覆盖'):
            transport.curl_download('https://invalid',self.path,self.identity,'',self.env)
        self.assertEqual(b'existing',self.path.read_bytes())

    def test_bad_download_never_becomes_final(self):
        url,_=self.server();identity=dict(self.identity,sha256='0'*64)
        with self.assertRaisesRegex(RuntimeError,'SHA256'):
            transport.curl_download(url,self.path,identity,'',self.env,deadline=10)
        self.assertFalse(self.path.exists())

    def test_manifest_defaults_read_only_and_checks_local_hash_first(self):
        self.path.write_bytes(self.content)
        manifest=self.path.parent/'release.json'
        spec={'repo':'owner/app','tag':'v1.0','target':'a'*40,'title':'test','body':'test',
              'assets':[{'path':self.path.name,'sha256':self.identity['sha256']}]}
        manifest.write_text(json.dumps(spec))
        output=self.path.parent/'state';output.mkdir()
        with patch.object(release_assets,'publish_verified',return_value={}) as publish:
            release_assets.execute(manifest,output)
            self.assertTrue(publish.call_args.kwargs['verify_only'])
            self.assertFalse(publish.call_args.kwargs['public_release'])
        self.path.write_bytes(b'changed')
        with patch.object(release_assets,'Runner') as runner:
            with self.assertRaisesRegex(RuntimeError,'发生变化'):release_assets.execute(manifest,output,publish=True)
            runner.assert_not_called()

if __name__=='__main__':unittest.main()
