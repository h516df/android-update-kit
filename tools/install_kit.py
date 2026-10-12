#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 update kit 拷进一个安卓项目，并把配置换成这个项目自己的。

用法：
    python tools/install_kit.py D:\\Projects\\某项目 --repo h516df/Jiaoban --package cn.jiaoban \
        --prefix Jiaoban --name Jiaoban
"""
import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
KIT = Path(__file__).resolve().parents[1]
KIT_SRC = KIT / "kit" / "src" / "main" / "java" / "dev" / "h516" / "update"


def repo_id_of(repo: str) -> str:
    try:
        out = subprocess.run(
            ["gh", "api", f"repos/{repo}", "--jq", ".id"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60,
        )
        value = out.stdout.decode().strip()
        return value if value.isdigit() else "0"
    except Exception:
        return "0"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("project", help="安卓项目根目录")
    parser.add_argument("--repo", required=True, help="例如 h516df/Jiaoban")
    parser.add_argument("--package", required=True, help="applicationId，例如 cn.jiaoban")
    parser.add_argument("--prefix", required=True, help="发布资产前缀，例如 Jiaoban")
    parser.add_argument("--name", default="", help="授权页上勾选的仓库名，默认取仓库后半段")
    parser.add_argument("--repo-id", default="", help="仓库数字 id，默认自动查")
    args = parser.parse_args()

    project = Path(args.project).resolve()
    if not (project / "app").is_dir():
        raise SystemExit("这不像一个安卓项目（没有 app/ 目录）：" + str(project))
    name = args.name or args.repo.split("/")[-1]
    repo_id = args.repo_id or repo_id_of(args.repo)

    dst = project / "app" / "src" / "main" / "java" / "dev" / "h516" / "update"
    dst.mkdir(parents=True, exist_ok=True)
    for src in KIT_SRC.glob("*.kt"):
        text = src.read_text(encoding="utf-8")
        if src.name == "UpdateConfig.kt":
            text = (
                text.replace('const val REPO = "OWNER/REPO"', f'const val REPO = "{args.repo}"')
                .replace("const val REPO_ID = 0L", f"const val REPO_ID = {repo_id}L")
                .replace('const val REPO_NAME = "REPO"', f'const val REPO_NAME = "{name}"')
                .replace('const val PACKAGE = "com.example.app"', f'const val PACKAGE = "{args.package}"')
                .replace('const val APK_PREFIX = "App"', f'const val APK_PREFIX = "{args.prefix}"')
            )
        (dst / src.name).write_text(text, encoding="utf-8")

    xml_dir = project / "app" / "src" / "main" / "res" / "xml"
    xml_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(KIT / "kit/src/main/res/xml/update_paths.xml", xml_dir / "update_paths.xml")

    assets_dir = project / "app" / "src" / "main" / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    notes = assets_dir / "release-notes.json"
    if not notes.is_file():
        shutil.copyfile(KIT / "kit/src/main/assets/release-notes.json", notes)
        print("已放入示例 assets/release-notes.json，记得改成这个项目的版本说明", flush=True)

    scripts = project / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(KIT / "scripts/publish_release.py", scripts / "publish_release.py")
    shutil.copyfile(KIT / "scripts/release_remote.py", scripts / "release_remote.py")

    print("代码和脚本已拷入：%s" % project, flush=True)
    print("\n还要手工做这几件事：", flush=True)
    print("1. app/build.gradle.kts 加：implementation(\"androidx.core:core-ktx:1.18.0\")", flush=True)
    print("2. AndroidManifest.xml 加权限：INTERNET、REQUEST_INSTALL_PACKAGES", flush=True)
    print("3. AndroidManifest.xml 加 FileProvider，authorities=%s.updates，paths 指向 @xml/update_paths" % args.package, flush=True)
    print("4. app/src/main/assets/release-notes.json 改成这个项目当前版本的中文说明", flush=True)
    print("5. 界面上加入口：val updater = remember { Updater(context) } / UpdateScreen(updater, innerPadding)", flush=True)
    print("6. 发布用：python scripts\\publish_release.py", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
