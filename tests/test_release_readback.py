import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.request
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

    def test_alternative_readback_honors_range_and_verifies_bytes(self):
        url,requests=self.server()
        self.path.with_name(self.path.name+'.part').write_bytes(self.content[:2000])
        self.path.with_name(self.path.name+'.part.json').write_text(json.dumps(self.identity))
        result=transport.curl_download(url,self.path,self.identity,'',self.env,backend='urllib',deadline=10)
        self.assertEqual([2000],requests);self.assertEqual(self.content,self.path.read_bytes())
        self.assertEqual(2000,result['resumedFromBytes'])

    def test_manifest_defaults_read_only_and_checks_local_hash_first(self):
        self.path.write_bytes(self.content)
        manifest=self.path.parent/'release.json'
        spec={'repo':'owner/app','tag':'v1.0','target':'a'*40,'title':'test','body':'test',
              'assets':[{'path':self.path.name,'sha256':self.identity['sha256']}]}
        manifest.write_text(json.dumps(spec))
        output=self.path.parent/'state';output.mkdir()
        (output/'audit.json').write_text(json.dumps({'auditStatus':'failed','errorType':'OldFailure'}))
        with patch.object(release_assets,'publish_verified',return_value={}) as publish:
            result=release_assets.execute(manifest,output)
            self.assertTrue(publish.call_args.kwargs['verify_only'])
            self.assertFalse(publish.call_args.kwargs['public_release'])
            self.assertEqual(result['auditStatus'],'completed')
            self.assertEqual(json.loads((output/'audit.json').read_text())['auditStatus'],'completed')
            self.assertNotIn('errorType',result)
            self.assertFalse(result['downloadVerificationRequested'])
        self.path.write_bytes(b'changed')
        with patch.object(release_assets,'Runner') as runner:
            with self.assertRaisesRegex(RuntimeError,'发生变化'):release_assets.execute(manifest,output,publish=True)
            runner.assert_not_called()

    def test_download_failure_replaces_old_success_and_keeps_completed_assets(self):
        self.path.write_bytes(self.content)
        second=self.path.parent/'second.apk';second.write_bytes(self.content)
        manifest=self.path.parent/'release.json'
        manifest.write_text(json.dumps({'repo':'owner/app','tag':'v1.0','target':'a'*40,'title':'t','body':'b',
            'assets':[{'path':p.name,'sha256':self.identity['sha256']} for p in (self.path,second)]}))
        output=self.path.parent/'state';output.mkdir()
        audit=output/'audit.json';audit.write_text(json.dumps({'status':'published','auditStatus':'completed','oldSuccess':True}))
        runner=transport.Runner(self.env,notify=lambda text:None)
        runner.gh=lambda argv:json.dumps({'draft':False,'assets':[
            {'name':p.name,'id':i,'state':'uploaded','size':len(self.content),'digest':'sha256:'+self.identity['sha256'],
             'browser_download_url':f'https://github.com/owner/app/releases/download/v1.0/{p.name}'}
            for i,p in enumerate((self.path,second),1)]})
        partial=output/'downloads/second.apk.part'
        def download(url,path,identity,*args,**kwargs):
            if path.name==self.path.name:
                path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(self.content)
                return {'name':path.name,'downloadedThisRun':True,'size':len(self.content),'sha256':self.identity['sha256']}
            partial.write_bytes(self.content[:1000])
            partial.with_name(partial.name+'.json').write_text(json.dumps(identity))
            raise RuntimeError('SYNTHETIC_PRIVATE_DIAGNOSTIC')
        with patch.object(release_assets,'Runner',return_value=runner), \
             patch.object(release_assets,'publish_verified',return_value={'status':'published'}), \
             patch.object(transport,'curl_download',side_effect=download):
            with self.assertRaises(RuntimeError):
                release_assets.execute(manifest,output,public_release=True,verify_downloads=True)
        result=json.loads(audit.read_text())
        self.assertEqual(result['auditStatus'],'failed')
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['publicationStatus'],'published')
        self.assertEqual(result['failedDownload'],'second.apk')
        self.assertEqual([row['name'] for row in result['downloads']],['fixture.apk'])
        self.assertFalse(result['downloadVerificationComplete'])
        self.assertEqual(result['nextAction'],'resume_verified_downloads')
        self.assertEqual(partial.read_bytes(),self.content[:1000])
        self.assertNotIn('SYNTHETIC_PRIVATE_DIAGNOSTIC',audit.read_text())
        self.assertNotIn('oldSuccess',result)

    def test_remote_failure_has_current_receipt_without_repeating_mutation(self):
        self.path.write_bytes(self.content)
        manifest=self.path.parent/'release.json'
        manifest.write_text(json.dumps({'repo':'owner/app','tag':'v1.0','target':'a'*40,'title':'t','body':'b',
            'assets':[{'path':self.path.name,'sha256':self.identity['sha256']}]}))
        output=self.path.parent/'state';output.mkdir();audit=output/'audit.json'
        audit.write_text(json.dumps({'auditStatus':'completed','status':'published'}))
        runner=transport.Runner(self.env)
        def fail(*args,**kwargs):
            self.assertEqual(json.loads(audit.read_text())['auditStatus'],'in_progress')
            runner.mutations=1;runner.upload_attempts=1
            raise OSError('SYNTHETIC_PRIVATE_DIAGNOSTIC')
        with patch.object(release_assets,'Runner',return_value=runner), \
             patch.object(release_assets,'publish_verified',side_effect=fail) as publish:
            with self.assertRaises(OSError):release_assets.execute(manifest,output,publish=True)
        result=json.loads(audit.read_text())
        self.assertEqual(result['auditStatus'],'failed')
        self.assertEqual(result['stage'],'publication_verification')
        self.assertEqual(result['nextAction'],'inspect_remote_side_effects_before_retry')
        self.assertEqual(result['remoteMutationRequests'],1)
        self.assertEqual(result['uploadAttempts'],1)
        self.assertNotIn('SYNTHETIC_PRIVATE_DIAGNOSTIC',audit.read_text())
        self.assertEqual(publish.call_count,1)

    @unittest.skipUnless(os.name=='nt','Windows filename canonicalization')
    def test_manifest_preserves_declared_asset_case_on_windows(self):
        upper=self.path.parent/'App.apk';upper.write_bytes(self.content)
        manifest=self.path.parent/'release.json'
        manifest.write_text(json.dumps({'repo':'owner/app','tag':'v1.0','target':'a'*40,'title':'t','body':'b',
            'assets':[{'path':'app.apk','sha256':self.identity['sha256']}]}))
        output=self.path.parent/'state';output.mkdir()
        with patch.object(release_assets,'publish_verified',return_value={}) as publish:
            release_assets.execute(manifest,output)
            self.assertEqual('app.apk',publish.call_args.args[5][0].name)

    def test_redirects_strip_auth_and_uploads_never_follow(self):
        request=urllib.request.Request('https://api.github.com/repos/owner/app/releases/assets/1',headers={'Authorization':'Bearer fixture'})
        opener=transport.secure_opener({},asset_redirects=True)
        handler=next(h for h in opener.handlers if isinstance(h,urllib.request.HTTPRedirectHandler))
        redirected=handler.redirect_request(request,None,302,'',{},'https://release-assets.githubusercontent.com/example')
        self.assertIsNone(redirected.get_header('Authorization'))
        self.assertIsNone(handler.redirect_request(request,None,302,'',{},'https://unrelated.invalid/example'))
        upload=transport.secure_opener({})
        blocked=next(h for h in upload.handlers if isinstance(h,urllib.request.HTTPRedirectHandler))
        self.assertIsNone(blocked.redirect_request(request,None,302,'',{},'https://release-assets.githubusercontent.com/example'))

if __name__=='__main__':unittest.main()
