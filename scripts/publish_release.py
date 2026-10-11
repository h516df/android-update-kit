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

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
APK = ROOT / "app" / "build" / "outputs" / "apk" / "release" / "app-release.apk"
NOTES = ROOT / "app" / "src" / "main" / "assets" / "release-notes.json"
CONFIG = ROOT / "app" / "src" / "main" / "java" / "dev" / "h516" / "update" / "UpdateConfig.kt"
GRADLE = ROOT / ("gradlew.bat" if sys.platform.startswith("win") else "gradlew")
DIST = ROOT / "dist"
HEADINGS = ["新增", "改进", "修复", "说明"]


def run(argv, timeout=2400):
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
    args = parser.parse_args()

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
    if not APK.is_file():
        raise SystemExit("没有正式包：" + str(APK))

    sha = hashlib.sha256(APK.read_bytes()).hexdigest()
    out = DIST / f"v{version}"
    out.mkdir(parents=True, exist_ok=True)
    apk_out = out / f"{prefix}-v{version}-universal.apk"
    notes_out = out / f"{prefix}-v{version}-release-notes.json"
    sha_out = out / f"{prefix}-v{version}-SHA256.txt"
    shutil.copyfile(APK, apk_out)
    shutil.copyfile(NOTES, notes_out)
    sha_out.write_text(f"{sha}  app-release.apk\n", encoding="utf-8")

    for f in (apk_out, notes_out, sha_out):
        print("  %s  %d bytes" % (f.name, f.stat().st_size), flush=True)
    print("  sha256=%s" % sha, flush=True)
    if args.dry_run:
        return 0

    title = "v%s｜%s" % (version, (notes.get("新增") or ["小改动"])[0][:24])
    body = markdown(notes, sha)
    existing = subprocess.run(
        ["gh", "release", "view", f"v{version}", "--repo", repo],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0
    if existing:
        run(["gh", "release", "upload", f"v{version}", str(apk_out), str(notes_out), str(sha_out),
             "--repo", repo, "--clobber"])
        run(["gh", "release", "edit", f"v{version}", "--repo", repo, "--title", title, "--notes", body])
    else:
        run(["gh", "release", "create", f"v{version}", str(apk_out), str(notes_out), str(sha_out),
             "--repo", repo, "--title", title, "--notes", body])
    print("已发布 v%s 到 %s" % (version, repo), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
