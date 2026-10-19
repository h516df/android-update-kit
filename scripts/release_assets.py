"""Cross-project release entry. Prepared, verified asset manifest; read-only unless --publish."""
import argparse
import json
import os
from pathlib import Path
import sys
from release_remote import publish_verified, file_digest
from release_transport import Runner, local_proxy


def execute(manifest_path, output=None, publish=False, public_release=False, verify_downloads=False):
    manifest_path=Path(manifest_path).resolve()
    spec=json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    assets=[]
    for item in spec['assets']:
        path=(manifest_path.parent/item['path']).resolve()
        if not path.is_file() or file_digest(path)!='sha256:'+item['sha256']:
            raise RuntimeError('已验收资产发生变化：'+path.name)
        assets.append(path)
    output=Path(output or manifest_path.parent/'release-state')
    runner=Runner(local_proxy(os.environ))
    result=publish_verified(spec['repo'],spec['tag'],spec['target'],spec['title'],spec['body'],
        assets,output,runner,verify_only=not publish,public_release=public_release)
    result.update(remoteMutationRequests=runner.mutations,nativeCurlUploadAttempts=runner.upload_attempts,
                  downloadPerformedThisRun=False)
    if verify_downloads:
        result['downloads']=runner.verify_downloads(spec['repo'],spec['tag'],assets,output/'downloads',public_release)
        result['downloadPerformedThisRun']=any(r['downloadedThisRun'] for r in result['downloads'])
    (output/'audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='全局 Android 发布入口：默认只读核对，显式发布')
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--publish',action='store_true')
    parser.add_argument('--public-release',action='store_true')
    parser.add_argument('--verify-downloads',action='store_true',help='实际下载并按本地已验收资产核验；支持接续自己的部分下载')
    args=parser.parse_args()
    try:
        print(json.dumps(execute(args.manifest,args.output,args.publish,args.public_release,args.verify_downloads),ensure_ascii=False,indent=2))
    except Exception as error:
        print(f'停止：{type(error).__name__}: {error}',file=sys.stderr)
        sys.exit(1)
