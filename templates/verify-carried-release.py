from pathlib import Path
import hashlib,json,os,re,subprocess,zipfile
from release_assets import execute
root=Path(__file__).resolve().parent
meta=json.loads((root/'delivery.json').read_text())
spec=json.loads((root/'release.json').read_text())
assert meta['sourceCommit']==spec['target']
assert isinstance(meta['publicRelease'],bool)
assert os.environ['GITHUB_REPOSITORY']==spec['repo']
assert os.environ['GITHUB_REF']=='refs/heads/'+meta['branch']
changed=subprocess.check_output(['git','diff','--name-only',spec['target'],'HEAD'],text=True).splitlines()
assert all(p.startswith('.release-delivery/') or p==meta['workflow'] for p in changed)
assert Path(meta['apk']).name==meta['apk'] and meta['apk'].endswith('.apk')
assert Path(meta['notes']).name==meta['notes']
assert len({c['name'] for c in meta['chunks']})==len(meta['chunks'])
assert all(Path(c['name']).name==c['name'] and 0<c['bytes']<=4*1024*1024 for c in meta['chunks'])
apk=root/meta['apk']
with apk.open('xb') as target:
 for item in meta['chunks']:
  part=root/item['name'];data=part.read_bytes()
  assert len(data)==item['bytes'] and hashlib.sha256(data).hexdigest()==item['sha256']
  target.write(data)
assert apk.stat().st_size==meta['bytes']
with apk.open('rb') as source:assert hashlib.file_digest(source,'sha256').hexdigest()==meta['sha256']
sdk=Path(os.environ.get('ANDROID_HOME') or os.environ['ANDROID_SDK_ROOT'])
tools=list((sdk/'build-tools'/meta.get('buildToolsVersion','36.0.0')).glob('apksigner'))
assert tools,'Android SDK build tools unavailable; no release'
tool=tools[-1].parent
certs=subprocess.check_output([str(tool/'apksigner'),'verify','--verbose','--print-certs',str(apk)],text=True)
fingerprints=re.findall(r'certificate SHA-256 digest: ([0-9a-fA-F]{64})',certs)
print('Verified signer SHA256:',fingerprints,flush=True)
assert [fp.lower() for fp in fingerprints]==[meta['certificateSha256'].lower()]
assert re.search(r'Verified using v2 scheme.*:\s*true',certs)
badging=subprocess.check_output([str(tool/'aapt2'),'dump','badging',str(apk)],text=True)
match=re.search(r"^package: name='([^']+)' versionCode='([^']+)' versionName='([^']+)'",badging,re.M)
assert match and match.groups()==(meta['packageName'],str(meta['versionCode']),meta['versionName'])
assert 'application-debuggable' not in badging
with zipfile.ZipFile(apk) as archive:
 assert sorted({n.split('/')[1] for n in archive.namelist() if n.startswith('lib/') and n.endswith('.so')})==meta['nativeAbis']
 assert archive.read('assets/release-notes.json')==(root/meta['notes']).read_bytes()
print('Final-source signed APK verified in cloud; no build or signing performed',flush=True)
result=execute(root/'release.json',root/'state',publish=True,public_release=meta['publicRelease'],verify_downloads=True)
assert result['downloadPerformedThisRun'] and all(x['downloadedThisRun'] for x in result['downloads'])
summary='正式发布及三个附件实际回读通过：'+result['url']+'\nAPK SHA256: '+meta['sha256']+'\n'
Path(os.environ['GITHUB_STEP_SUMMARY']).write_text(summary,encoding='utf-8')
print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
