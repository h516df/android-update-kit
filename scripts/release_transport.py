"""Bounded GitHub release transport; credentials stay in process pipes."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import quote, urlsplit

CURL = shutil.which('curl.exe' if sys.platform == 'win32' else 'curl') or 'curl'



def sha(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def local_proxy(env):
    """Reuse an existing process proxy, otherwise only a credential-free loopback system proxy."""
    env = dict(env)
    if any(env.get(k) for k in ('HTTPS_PROXY', 'https_proxy', 'ALL_PROXY', 'all_proxy')):
        return env
    if sys.platform == 'win32':
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'Software\Microsoft\Windows\CurrentVersion\Internet Settings') as key:
            if not winreg.QueryValueEx(key, 'ProxyEnable')[0]:
                return env
            value = winreg.QueryValueEx(key, 'ProxyServer')[0]
        if '=' in value:
            value = dict(p.split('=', 1) for p in value.split(';') if '=' in p).get('https', '')
        parsed = urlsplit(value if '://' in value else 'http://' + value)
        if parsed.hostname in ('127.0.0.1', 'localhost', '::1') and parsed.port and not parsed.username:
            env['HTTPS_PROXY'] = parsed.geturl()
    return env


def curl_upload(url, path, auth_config, env, *, stall=60, deadline=None, notify=print):
    """No credential argv/file, bounded progress, and only this child may be terminated."""
    size = path.stat().st_size
    deadline = deadline or min(7200, max(900, int(size / 32768) + 120))
    command = [CURL, '--config', '-', '--http1.1', '--request', 'POST',
               '--header', 'Content-Type: application/octet-stream',
               '--header', 'Accept: application/vnd.github+json',
               '--header', 'Expect: 100-continue', '--data-binary', '@' + str(path),
               '--connect-timeout', '10', '--max-time', str(deadline),
               '--speed-limit', '1024', '--speed-time', str(max(60, stall)), '--progress-bar',
               '--write-out', '\n{"status":"%{http_code}","bytes":%{size_upload},'
               '"rate":%{speed_upload},"seconds":%{time_total}}', url]
    started = time.monotonic()
    progress = {'percent': 0.0, 'changed': started}
    proc = subprocess.Popen(command, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')

    def consume_progress():
        # Curl's progress is parsed, never echoed: errors can contain temporary URLs.
        for line in proc.stderr:
            values = re.findall(r'(\d+(?:\.\d+)?)%', line)
            if values and float(values[-1]) > progress['percent']:
                progress.update(percent=min(100.0, float(values[-1])), changed=time.monotonic())

    reader = threading.Thread(target=consume_progress, daemon=True)
    reader.start()
    reason = None
    try:
        proc.stdin.write(auth_config)
        proc.stdin.close()
        announced = started
        while proc.poll() is None:
            now = time.monotonic()
            if now - started > deadline or now - progress['changed'] > stall:
                reason = '传输达到总时限' if now - started > deadline else '传输进度长时间未增加'
                proc.terminate()
                break
            if now - announced >= 30:
                notify(f"上传 {path.name}: {progress['percent']:.1f}% / {size:,} 字节")
                announced = now
            time.sleep(0.1)
        proc.wait(timeout=5)
        response = proc.stdout.read()
        reader.join(timeout=2)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        if not proc.stdin.closed:
            proc.stdin.close()
        proc.stdout.close()
        proc.stderr.close()
    body, _, stats = response.rpartition('\n')
    try:
        measured = json.loads(stats)
    except ValueError:
        measured = {'status': 'unknown', 'bytes': None, 'seconds': round(time.monotonic() - started, 2)}
    measured.update(exit=proc.returncode, stopped=reason)
    notify(json.dumps(measured, ensure_ascii=False))
    return measured, body


def curl_download(url, path, identity, auth_config, env, *, stall=60, deadline=None, notify=print):
    """Resume only an owned partial of this exact asset. Never overwrite a complete file."""
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        if path.stat().st_size != identity['size'] or sha(path) != identity['sha256']:
            raise RuntimeError('已有下载文件不匹配，拒绝覆盖：'+path.name)
        return {'name':path.name,'downloadedThisRun':False,'cachedVerified':True}
    partial=path.with_name(path.name+'.part')
    marker=path.with_name(path.name+'.part.json')
    if partial.exists() or marker.exists():
        if not marker.is_file() or json.loads(marker.read_text(encoding='utf-8'))!=identity:
            raise RuntimeError('部分下载归属不匹配，保留现场：'+path.name)
    else:
        marker.write_text(json.dumps(identity,sort_keys=True),encoding='utf-8')
    before=partial.stat().st_size if partial.exists() else 0
    if before>identity['size']:
        raise RuntimeError('部分下载超出目标大小')
    deadline=deadline or min(7200,max(900,int(identity['size']/32768)+120))
    if before<identity['size']:
        command=[CURL,'--config','-','--http1.1','--location','--fail','--silent','--show-error',
            '--header','Accept: application/octet-stream','--continue-at','-',
            '--output',str(partial),'--connect-timeout','10','--max-time',str(deadline),
            '--speed-limit','1024','--speed-time',str(max(60,stall)),url]
        started=changed=announced=time.monotonic()
        previous=before
        proc=subprocess.Popen(command,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            proc.stdin.write(auth_config.encode());proc.stdin.close()
            while proc.poll() is None:
                now=time.monotonic();size=partial.stat().st_size if partial.exists() else 0
                if size>previous:previous=size;changed=now
                if now-started>deadline or now-changed>stall:
                    proc.terminate();proc.wait(timeout=5)
                    raise RuntimeError('下载停止：进度停滞或达到时限；保留可接续部分')
                if now-announced>=30:
                    notify(f'下载 {path.name}: {size:,}/{identity["size"]:,} 字节');announced=now
                time.sleep(.1)
            if proc.returncode:
                raise RuntimeError(f'下载失败（curl {proc.returncode}）；保留可接续部分，不输出临时下载地址')
        finally:
            if proc.poll() is None:proc.kill();proc.wait()
            if not proc.stdin.closed:proc.stdin.close()
            proc.stdout.close();proc.stderr.close()
    if not partial.is_file() or partial.stat().st_size!=identity['size'] or sha(partial)!=identity['sha256']:
        raise RuntimeError('下载字节数或 SHA256 不一致；保留现场，不交付')
    # Hard-link creation fails if another writer created the final file in the meantime.
    os.link(partial,path)
    partial.unlink();marker.unlink()
    return {'name':path.name,'downloadedThisRun':before<identity['size'],
            'resumedFromBytes':before,'size':identity['size'],'sha256':identity['sha256']}


class Runner:
    def __init__(self, env, notify=print):
        self.env, self.notify = env, notify
        self.mutations = 0
        self.upload_attempts = 0

    def gh(self, argv):
        if argv[:2] == ['gh', 'api'] and ('--method' not in argv or argv[argv.index('--method')+1]=='GET'):
            return self.api_get(argv)
        result = subprocess.run(argv, env=self.env, capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=45)
        if result.returncode:
            raise RuntimeError(f'GitHub 命令失败（退出码 {result.returncode}）；不记录原始响应或认证数据')
        return result.stdout

    def auth_config(self):
        token = self.gh(['gh', 'auth', 'token', '--hostname', 'github.com']).strip()
        if not token or re.search(r'[\s"\\]', token):
            raise RuntimeError('既有 GitHub 登录不可用；不记录认证内容')
        return f'header = "Authorization: Bearer {token}"\n'

    def api_get(self, argv):
        endpoint=argv[2]
        if not endpoint.startswith('repos/') or '://' in endpoint or '\n' in endpoint:
            raise RuntimeError('只支持明确仓库的 GitHub REST 读取')
        paginate='--paginate' in argv
        pages=[]
        auth=self.auth_config()
        try:
            for page in range(1,101):
                suffix=('&' if '?' in endpoint else '?')+f'page={page}' if paginate else ''
                url='https://api.github.com/'+endpoint+suffix
                command=[CURL,'--config','-','--http1.1','--compressed','--silent','--show-error',
                    '--connect-timeout','10','--max-time','90','--header','Accept: application/vnd.github+json',
                    '--write-out','\n%{http_code}',url]
                result=subprocess.run(command,input=auth,env=self.env,capture_output=True,text=True,
                    encoding='utf-8',errors='replace',timeout=95)
                body,_,status=result.stdout.rpartition('\n')
                if result.returncode or not status.startswith('2'):
                    raise RuntimeError(f'GitHub 元数据读取失败（HTTP {status or "unknown"}，curl {result.returncode}）；不输出原始响应')
                if not paginate:
                    return body
                value=json.loads(body)
                if not isinstance(value,list):
                    raise RuntimeError('分页响应不是列表')
                pages.append(value)
                if len(value)<100:
                    return json.dumps(pages if '--slurp' in argv else [item for batch in pages for item in batch])
            raise RuntimeError('分页达到安全上限')
        finally:
            del auth

    def verify_downloads(self, repo, tag, paths, output, public_release=False):
        release=json.loads(self.gh(['gh','api',f'repos/{repo}/releases/tags/{tag}']))
        if release['draft']:raise RuntimeError('只回读正式发布')
        assets={a['name']:a for a in release['assets']}
        reports=[]
        auth='' if public_release else self.auth_config()
        try:
            for path in paths:
                asset=assets[path.name]
                expected=sha(path)
                if asset['state']!='uploaded' or asset['size']!=path.stat().st_size or asset['digest']!='sha256:'+expected:
                    raise RuntimeError('下载前远端资产身份变化')
                if public_release:
                    url=asset['browser_download_url']
                    if not url.startswith(f'https://github.com/{repo}/releases/download/{tag}/'):
                        raise RuntimeError('公开下载地址不属于目标仓库版本')
                else:
                    url=f'https://api.github.com/repos/{repo}/releases/assets/{int(asset["id"])}'
                identity={'repo':repo,'tag':tag,'assetId':asset['id'],'size':asset['size'],'sha256':expected}
                reports.append(curl_download(url,Path(output)/path.name,identity,auth,self.env,notify=self.notify))
        finally:del auth
        return reports

    def __call__(self, argv):
        if argv[:3] == ['gh', 'release', 'upload']:
            return self.upload(argv[3], Path(argv[4]), argv[argv.index('--repo') + 1])
        if '--method' in argv and argv[argv.index('--method') + 1] != 'GET':
            self.mutations += 1
        return self.gh(argv)

    def upload(self, tag, path, repo):
        expected = 'sha256:' + sha(path)
        size = path.stat().st_size
        # Drafts are not reliably returned by the tag endpoint. List them with the
        # authenticated releases API, then upload only to the exact matching draft.
        pages = json.loads(self.gh(['gh','api',f'repos/{repo}/releases?per_page=100','--paginate','--slurp']))
        matches=[r for page in pages for r in page if r.get('tag_name')==tag]
        if len(matches)!=1:raise RuntimeError('草稿版本身份不明确')
        release=matches[0]
        if not release['draft'] or any(a['name'] == path.name for a in release['assets']):
            raise RuntimeError('仅上传草稿中缺失的资产；现有资产或占位均不删除、不覆盖')
        token = self.gh(['gh', 'auth', 'token', '--hostname', 'github.com']).strip()
        if not token or re.search(r'[\s"\\]', token):
            raise RuntimeError('现有 GitHub 登录未返回有效认证；不输出认证内容')
        auth = f'header = "Authorization: Bearer {token}"\n'
        url = f"https://uploads.github.com/repos/{repo}/releases/{release['id']}/assets?name={quote(path.name, safe='')}"
        self.mutations += 1
        self.upload_attempts += 1
        try:
            measured, _ = curl_upload(url, path, auth, self.env, notify=self.notify)
        except (OSError, subprocess.SubprocessError):
            measured = {'status': 'unknown'}
        finally:
            del token, auth
        # A timeout does not prove failure. Ask GitHub before any decision to retry.
        fresh = json.loads(self.gh(['gh', 'api', f"repos/{repo}/releases/{release['id']}"]))
        found = [a for a in fresh['assets'] if a['name'] == path.name]
        if (len(found) == 1 and found[0].get('state') == 'uploaded'
                and found[0].get('size') == size and found[0].get('digest') == expected):
            return ''
        raise RuntimeError(f'上传未获完整资产确认（HTTP {measured["status"]}）；保留草稿和已有资产，不自动重传或删除')

