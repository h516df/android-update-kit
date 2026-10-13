#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""接入安卓更新组件；全量预检，保留已有定制，重复接入不重写相同文件。"""
import argparse
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
KIT = Path(__file__).resolve().parents[1]
KIT_SRC = KIT / 'kit/src/main/java/dev/h516/update'


def positive_repo_id(value):
    if not re.fullmatch(r'[0-9]{1,19}', value) or not 0 < int(value) <= 9223372036854775807:
        raise SystemExit('仓库 id 必须是正整数；请核对仓库查询结果，尚未写入项目。')
    return str(int(value))


def repo_id_of(repo):
    try:
        out = subprocess.run(
            ['gh', 'api', f'repos/{repo}', '--jq', '.id'],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SystemExit('无法查询仓库 id；由 Agent 检查 gh 授权或传入已核实的 --repo-id，尚未写入项目。') from None
    if out.returncode != 0:
        raise SystemExit('仓库查询失败；由 Agent 核对仓库和 gh 授权，尚未写入项目。')
    return positive_repo_id(out.stdout.decode('utf-8', errors='replace').strip())


def assert_plain_path(path):
    # 不跟随软链接、Windows junction 或其他重解析入口写到意外目录。
    for part in reversed((path, *path.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise SystemExit(f'不写入链接或重解析路径：{part}')


def install_plan(project, repo, repo_id, package, prefix, name):
    sources = sorted(KIT_SRC.glob('*.kt'))
    if not sources or not any(p.name == 'UpdateConfig.kt' for p in sources):
        raise SystemExit('kit 源码不完整，尚未写入项目。')
    plan = {}
    for source in sources:
        text = source.read_text(encoding='utf-8')
        if source.name == 'UpdateConfig.kt':
            replacements = {
                'const val REPO = "OWNER/REPO"': f'const val REPO = "{repo}"',
                'const val REPO_ID = 0L': f'const val REPO_ID = {repo_id}L',
                'const val REPO_NAME = "REPO"': f'const val REPO_NAME = "{name}"',
                'const val PACKAGE = "com.example.app"': f'const val PACKAGE = "{package}"',
                'const val APK_PREFIX = "App"': f'const val APK_PREFIX = "{prefix}"',
            }
            for old, new in replacements.items():
                if text.count(old) != 1:
                    raise SystemExit('kit 配置模板不匹配，尚未写入项目。')
                text = text.replace(old, new)
        plan[project / 'app/src/main/java/dev/h516/update' / source.name] = text.encode('utf-8')
    for relative in ('kit/src/main/res/xml/update_paths.xml',
                     'scripts/publish_release.py', 'scripts/release_remote.py'):
        target = relative.removeprefix('kit/src/main/')
        if relative.startswith('kit/'):
            target = 'app/src/main/' + target
        plan[project / target] = (KIT / relative).read_bytes()
    notes = project / 'app/src/main/assets/release-notes.json'
    assert_plain_path(notes)
    if notes.exists():
        if not notes.is_file():
            raise SystemExit(f'版本说明目标不是文件：{notes}')
    else:
        plan[notes] = (KIT / 'kit/src/main/assets/release-notes.json').read_bytes()
    return plan


def preflight(plan):
    conflicts = []
    for target, data in plan.items():
        assert_plain_path(target)
        if target.exists() and (not target.is_file() or target.read_bytes() != data):
            conflicts.append(str(target))
        if any(p.exists() and not p.is_dir() for p in target.parents):
            conflicts.append(str(target))
    if conflicts:
        raise SystemExit('发现已有文件与 kit 不同，未写入项目。由 Agent 在项目分支比较并合并，保留定制：\n' + '\n'.join(conflicts))


def write_new(target, data):
    assert_plain_path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    # 完整临时文件以同目录硬链接发布；目标若被别人创建，绝不覆盖。
    descriptor, temporary = tempfile.mkstemp(prefix='.update-kit-', dir=target.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        assert_plain_path(target)
        os.link(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', help='安卓项目根目录')
    parser.add_argument('--repo', required=True, help='例如 h516df/Jiaoban')
    parser.add_argument('--package', required=True, help='applicationId，例如 cn.jiaoban')
    parser.add_argument('--prefix', required=True, help='发布资产前缀，例如 Jiaoban')
    parser.add_argument('--name', default='', help='授权页上的仓库名，默认取仓库后半段')
    parser.add_argument('--repo-id', default='', help='已核实的仓库数字 id，默认自动查')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]+/[A-Za-z0-9_][A-Za-z0-9_.-]*', args.repo):
        parser.error('--repo 必须是 owner/repository')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+', args.package):
        parser.error('--package 必须是有效的 applicationId')
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', args.prefix):
        parser.error('--prefix 必须是安全的资产文件名前缀')
    name = args.name or args.repo.split('/')[1]
    if any(c in name for c in '"\\$') or any(ord(c) < 32 or ord(c) == 127 for c in name):
        parser.error('--name 含有不支持的配置字符')
    project = Path(os.path.abspath(args.project))
    assert_plain_path(project)
    assert_plain_path(project / 'app')
    if not (project / 'app').is_dir():
        raise SystemExit('这不像一个安卓项目（没有 app/ 目录）：' + str(project))
    repo_id = positive_repo_id(args.repo_id) if args.repo_id else repo_id_of(args.repo)
    plan = install_plan(project, args.repo, repo_id, args.package, args.prefix, name)
    preflight(plan)  # 所有冲突、配置和源文件检查均在第一次写入前完成。
    created = 0
    try:
        for target, data in plan.items():
            assert_plain_path(target)
            if target.exists():
                if not target.is_file() or target.read_bytes() != data:
                    raise OSError(f'预检后文件发生变化：{target}')
                continue
            write_new(target, data)
            created += 1
    except OSError as error:
        raise SystemExit(f'接入中断：{error}。本次已新增 {created} 个文件；已有文件未覆盖。由 Agent 核对后接续，不强制覆盖。') from None
    print(f'代码和脚本已接入：{project}（新增 {created}，相同文件跳过；原有版本说明保留）', flush=True)
    print('以下接入工作由实现 Agent 继续完成并构建验收：', flush=True)
    print('1. 核对 Gradle 的 androidx.core:core-ktx 依赖（示例 1.18.0），沿用项目兼容版本')
    print('2. Manifest 加 INTERNET、REQUEST_INSTALL_PACKAGES 和 FileProvider')
    print(f'3. FileProvider authorities={args.package}.updates，paths 指向 @xml/update_paths')
    print('4. release-notes.json 使用项目当前版本的中文说明')
    print('5. 接入 UpdateScreen / Updater，实测授权、下载、校验和系统覆盖升级')
    print('6. 发布复用项目验收包，使用 scripts/publish_release.py；复制成功不代表 App 已验收')
    return 0


if __name__ == '__main__':
    sys.exit(main())
