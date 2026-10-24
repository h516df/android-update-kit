"""Cross-project release entry. Prepared, verified asset manifest; read-only unless --publish."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from datetime import datetime, timezone
from release_remote import publish_verified, file_digest
from release_transport import Runner, local_proxy


def write_audit(output, result):
    output.mkdir(parents=True,exist_ok=True)
    result['checkedAt']=datetime.now(timezone.utc).isoformat()
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=output,delete=False) as stream:
            temporary=Path(stream.name)
            json.dump(result,stream,ensure_ascii=False,indent=2)
            stream.write('\n')
        os.replace(temporary,output/'audit.json')
    finally:
        if temporary and temporary.exists():temporary.unlink()


def execute(manifest_path, output=None, publish=False, public_release=False, verify_downloads=False, upload_transport='curl', *,
            metadata_transport=None, download_transport=None, write_transport='gh'):
    manifest_path=Path(manifest_path).resolve()
    spec=json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    assets=[]
    for item in spec['assets']:
        candidate=manifest_path.parent/item['path']
        # Windows resolve() may replace the declared filename's case with disk
        # spelling. GitHub and Android asset selection are case-sensitive.
        path=candidate.resolve().with_name(candidate.name)
        if not path.is_file() or file_digest(path)!='sha256:'+item['sha256']:
            raise RuntimeError('已验收资产发生变化：'+path.name)
        assets.append(path)
    output=Path(output or manifest_path.parent/'release-state')
    if (output/'audit.json').resolve() in {manifest_path,*(path.resolve() for path in assets)}:
        raise RuntimeError('回执不能覆盖发布清单或已验收资产')
    result=dict(status='in_progress',auditStatus='in_progress',stage='publication_verification',
                publicationStatus='not_verified',downloadVerificationRequested=verify_downloads,
                downloadVerificationComplete=False,downloads=[])
    # Invalidate an old success before any remote operation; abrupt termination
    # then leaves an explicitly incomplete receipt rather than stale success.
    write_audit(output,result)
    runner=None
    def measurements():
        result.update(remoteMutationRequests=runner.mutations if runner else 0,
                      uploadAttempts=runner.upload_attempts if runner else 0,
                      nativeCurlUploadAttempts=runner.upload_attempts if runner and upload_transport=='curl' else 0,
                      uploadTransport=upload_transport,
                      metadataTransport=upload_transport if metadata_transport is None else metadata_transport,
                      downloadTransport=upload_transport if download_transport is None else download_transport,
                      writeTransport=write_transport,
                      downloadPerformedThisRun=any(row['downloadedThisRun'] for row in result['downloads']))
    try:
        overrides={}
        if metadata_transport is not None:overrides['metadata_transport']=metadata_transport
        if download_transport is not None:overrides['download_transport']=download_transport
        if write_transport!='gh':overrides['write_transport']=write_transport
        runner=Runner(local_proxy(os.environ),upload_transport=upload_transport,**overrides)
        publication=publish_verified(spec['repo'],spec['tag'],spec['target'],spec['title'],spec['body'],
            assets,output,runner,verify_only=not publish,public_release=public_release)
        result.update(publication)
        result['publicationStatus']=publication.get('status','not_verified')
        if verify_downloads:
            result['stage']='download_verification'
            measurements();write_audit(output,result)
            result['downloads']=runner.verify_downloads(spec['repo'],spec['tag'],assets,output/'downloads',public_release)
            result['downloadVerificationComplete']=True
        result.update(status=publication.get('status','verified'),auditStatus='completed',stage='completed')
        measurements();write_audit(output,result)
    except Exception as error:
        if runner and result['stage']=='download_verification':
            result['downloads']=runner.download_reports
            result['failedDownload']=runner.download_failed_asset
        result.update(status='failed',auditStatus='failed',errorType=type(error).__name__,
                      nextAction='resume_verified_downloads' if result['stage']=='download_verification'
                      else 'inspect_remote_side_effects_before_retry')
        measurements();write_audit(output,result)
        raise
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='全局 Android 发布入口：默认只读核对，显式发布')
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--publish',action='store_true')
    parser.add_argument('--public-release',action='store_true')
    parser.add_argument('--verify-downloads',action='store_true',help='实际下载并按本地已验收资产核验；支持接续自己的部分下载')
    parser.add_argument('--transport','--upload-transport',dest='upload_transport',choices=['curl','urllib'],default='curl',help='显式选择元数据、上传及回读传输；不自动重传')
    parser.add_argument('--metadata-transport',choices=['curl','urllib'],help='仅覆盖只读元数据传输')
    parser.add_argument('--download-transport',choices=['curl','urllib'],help='仅覆盖资产下载传输')
    parser.add_argument('--write-transport',choices=['gh','curl'],default='gh',help='显式选择标签/Release写入通道；失败先核对远端，不自动重放')
    args=parser.parse_args()
    try:
        print(json.dumps(execute(args.manifest,args.output,args.publish,args.public_release,args.verify_downloads,args.upload_transport,
                                 metadata_transport=args.metadata_transport,download_transport=args.download_transport,
                                 write_transport=args.write_transport),ensure_ascii=False,indent=2))
    except Exception as error:
        print(f'停止：{type(error).__name__}: {error}',file=sys.stderr)
        sys.exit(1)
