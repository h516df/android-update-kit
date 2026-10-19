#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布一个版本：出正式包 -> 核对版本号 -> 生成中文更新说明和 SHA256 -> 挂 GitHub Release。

仓库名和资产前缀从项目里的 UpdateConfig.kt 读，不在这里重复写一遍。

用法：
    python scripts/publish_release.py            # 构建 + 发布
    python scripts/publish_release.py --no-build # 用现成 APK
    python scripts/publish_release.py --dry-run  # 只准备文件
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from release_remote import publish_verified
from release_transport import Runner, local_proxy
import os
TRANSPORT = Runner(local_proxy(os.environ))

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
APK = ROOT / "app" / "build" / "outputs" / "apk" / "release" / "app-release.apk"
NOTES = ROOT / "app" / "src" / "main" / "assets" / "release-notes.json"
CONFIG = ROOT / "app" / "src" / "main" / "java" / "dev" / "h516" / "update" / "UpdateConfig.kt"
GRADLE = ROOT / ("gradlew.bat" if sys.platform.startswith("win") else "gradlew")
DIST = ROOT / "dist"
HEADINGS = ["新增", "改进", "修复", "说明"]


def run(argv, timeout=2400):
    if argv and argv[0] == "gh":
        return TRANSPORT(argv)
    proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    out = proc.stdout.decode("utf-8", "replace")
    if proc.returncode != 0:
        print(out[-3000:], flush=True)
        raise SystemExit("命令失败：" + " ".join(str(a) for a in argv))
    return out


def config():
    text = CONFIG.read_text(encoding="utf-8")
    repo = re.search(r'const val REPO = "([^"]+)"', text).group(1)
    prefix = re.search(r'const val APK_PREFIX = "([^"]+)"', text).group(1)
    return repo, prefix


def version_from_gradle():
    text = (ROOT / "app" / "build.gradle.kts").read_text(encoding="utf-8")
    name = re.search(r'versionName = "([^"]+)"', text)
    code = re.search(r"versionCode = (\d+)", text)
    if not name or not code:
        raise SystemExit("在 app/build.gradle.kts 里找不到 versionName/versionCode")
    return name.group(1), int(code.group(1))


def verify_apk(path, version, code, signer):
    import os
    sdk = Path(os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT', ''))
    candidates = sorted((sdk / 'build-tools').glob('*'), reverse=True)
    tools = next((p for p in candidates if (p / ('aapt.exe' if sys.platform.startswith('win') else 'aapt')).is_file()), None)
    if tools is None:
        raise SystemExit('找不到当前 Android SDK build-tools')
    config_text = CONFIG.read_text(encoding='utf-8')
    package = re.search(r'const val PACKAGE\s*=\s*"([^"\n]+)"', config_text).group(1)
    badging = run([str(tools / ('aapt.exe' if sys.platform.startswith('win') else 'aapt')), 'dump', 'badging', str(path)])
    if f"package: name='{package}' versionCode='{code}' versionName='{version}'" not in badging or 'application-debuggable' in badging:
        raise SystemExit('安装包的包名、版本或正式属性不匹配')
    signature = run([str(tools / ('apksigner.bat' if sys.platform.startswith('win') else 'apksigner')), 'verify', '--print-certs', str(path)])
    certificates = re.findall(r'Signer #\d+ certificate SHA-256 digest: ([a-fA-F0-9]{64})', signature)
    if {value.lower() for value in certificates} != {signer}:
        raise SystemExit('项目原签名不匹配，拒绝发布')


def markdown(notes, sha):
    lines = [f"## {notes['versionName']} 更新", "", "**覆盖安装即可，旧数据保留。**", ""]
    for heading in HEADINGS:
        items = notes.get(heading) or []
        if not items:
            continue
        lines.append(f"### {heading}")
        lines.extend(f"- {item}" for item in items)
        lines.append("")
    lines.append(f"SHA256: `{sha}`")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--public-release", action="store_true", help="显式选择既有公开发行仓库；不修改可见性")
    parser.add_argument("--verify-only", action="store_true", help="只读核对已发布版本，禁止远端写入")
    parser.add_argument("--target", help="已经推送的完整提交 SHA")
    parser.add_argument("--prepared-apk", type=Path, help="已经验收的正式包；--no-build 时必填")
    parser.add_argument("--signer-sha256", help="项目原签名证书的公开 SHA256 指纹")
    args = parser.parse_args()
    if args.no_build and args.prepared_apk is None:
        raise SystemExit("--no-build 必须指定 --prepared-apk，避免误用最后一次构建")
    if args.verify_only and not args.no_build:
        raise SystemExit("只读核对必须提供 --no-build --prepared-apk")
    if not args.dry_run and not re.fullmatch(r'[a-f0-9]{40}', args.target or ''):
        raise SystemExit("必须指定已推送的完整 --target SHA")
    if not args.dry_run and not re.fullmatch(r'[a-fA-F0-9]{64}', args.signer_sha256 or ''):
        raise SystemExit("必须提供项目原签名公开指纹 --signer-sha256")
    if not args.dry_run and not args.verify_only:
        if run(['git','rev-parse','HEAD']).strip() != args.target or run(['git','status','--porcelain']).strip():
            raise SystemExit("发布只允许已提交且干净的确切工作区")

    repo, prefix = config()
    version, code = version_from_gradle()
    notes = json.loads(NOTES.read_text(encoding="utf-8"))
    if notes["versionName"] != version or int(notes["versionCode"]) != code:
        raise SystemExit(
            "更新说明和代码里的版本号不一致：build.gradle.kts 是 %s(%d)，release-notes.json 是 %s(%s)"
            % (version, code, notes["versionName"], notes["versionCode"])
        )

    if not args.no_build:
        print("== 出正式包 ==", flush=True)
        run([str(GRADLE), "-p", str(ROOT), ":app:assembleRelease", "--console=plain"])
    apk = args.prepared_apk if args.no_build else APK
    if not apk.is_file():
        raise SystemExit("没有正式包：" + str(apk))

    if not args.dry_run:
        verify_apk(apk, version, code, args.signer_sha256.lower())
    sha = hashlib.sha256(apk.read_bytes()).hexdigest()
    out = DIST / f"v{version}"
    out.mkdir(parents=True, exist_ok=True)
    apk_out = out / f"{prefix}-v{version}-universal.apk"
    notes_out = out / f"{prefix}-v{version}-release-notes.json"
    sha_out = out / f"{prefix}-v{version}-SHA256.txt"
    shutil.copyfile(apk, apk_out)
    shutil.copyfile(NOTES, notes_out)
    sha_out.write_text(f"{sha}  {apk_out.name}\n", encoding="utf-8")

    for f in (apk_out, notes_out, sha_out):
        print("  %s  %d bytes" % (f.name, f.stat().st_size), flush=True)
    print("  sha256=%s" % sha, flush=True)
    if args.dry_run:
        return 0

    title = "v%s｜%s" % (version, (notes.get("新增") or ["小改动"])[0][:24])
    body = markdown(notes, sha)
    receipt = publish_verified(repo, f'v{version}', args.target, title, body,
        [apk_out, notes_out, sha_out], out, run, verify_only=args.verify_only, public_release=args.public_release)
    print(json.dumps(receipt, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
