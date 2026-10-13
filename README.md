# android-update-kit

给私人安卓 App 用的「App 内检查更新」。从足迹涂色（`h516df/walkpaint`）那套实现里抽出来的，
在交办（`h516df/Jiaoban`）上跑通过。

**它做什么**：App 里一个「检查更新」页，显示当前版本 + 中文更新说明；点检查会走 GitHub 设备授权
（给你一个 8 位码，浏览器输一次），然后从**私有仓库**读发布资料、下载安装包、逐条校验，最后交给
**系统安装界面**覆盖升级。

**它不做什么**：不静默下载、不静默安装、不后台轮询、不在 APK 里放任何凭据。

## 为什么能读私有仓库又不泄露凭据

- APK 里只有一个**公开的 GitHub App client id**（不是密钥，所有项目共用）
- 用户在 GitHub 上授权一次，并**勾选要授权的仓库**；拿到的令牌只有那个仓库的只读权限
- 令牌用 Android Keystore 里的密钥加密后存在手机本地，随时能在 App 里清掉
- 仓库地址、API 地址、跳转域名全部写死；跳转不继承令牌

## 发布约定（App 认这个，不认别的）

每个 release 必须挂三个文件，缺一个 App 就当没有这个版本：

| 文件 | 内容 |
| --- | --- |
| `<前缀>-v<版本>-release-notes.json` | `{"versionName","versionCode","新增":[],"改进":[],"修复":[],"说明":[]}` |
| `<前缀>-v<版本>-universal.apk`（或 `-arm64.apk`） | 安装包 |
| `<前缀>-v<版本>-SHA256.txt` | `<sha256>  <文件名>` |

tag 必须形如 `v0.3`；`versionCode` 必须比装着的版本大；APK 必须和装着的 App **同一个签名**。

## 接入一个项目（6 步）

```powershell
python tools/install_kit.py D:\Projects\你的项目 --repo h516df/你的仓库 --package cn.你的包名 --prefix 你的前缀 --name 你的仓库名
```

脚本先核对配置、全部源文件与目标路径，再把新文件接入项目；相同文件直接跳过，已有版本说明保留。剩余步骤由实现 Agent 完成并验收，不交给用户执行。

已有 Kotlin、XML 或发布脚本与 kit 不同时，会列出冲突并在写入前停止；不提供强制覆盖选项。由 Agent 在项目分支比较、备份并合并所需变化，保留项目定制。仓库查询失败或 id 无效也会停止，不能生成 `REPO_ID = 0L` 的配置。已核实的数字 id 可用 `--repo-id` 传入。

重复接入不改相同文件的内容或修改时间；安装因磁盘/进程故障中断时可能留下部分新增文件，核对后用相同参数接续。目标若被另一个写手创建则停止，已有文件不覆盖。链接与 Windows junction 不作为写入入口；项目仍须按单写手/独立工作树规则操作。复制成功只代表工具接入，App 的授权、下载和覆盖升级仍需真实入口验收。

1. `app/build.gradle.kts`：`implementation("androidx.core:core-ktx:1.18.0")`（FileProvider 要用）
2. `AndroidManifest.xml`：加 `INTERNET`、`REQUEST_INSTALL_PACKAGES` 两个权限
3. `AndroidManifest.xml`：加 FileProvider（authorities 用 `<applicationId>.updates`，路径用 `@xml/update_paths`）
4. `app/src/main/res/xml/update_paths.xml`：`<cache-path name="update_apk" path="updates/" />`
5. `app/src/main/assets/release-notes.json`：当前版本的中文说明（和发布时挂的是同一份）
6. 界面上放一个入口：`UpdateScreen(updater, innerPadding)`，`val updater = remember { Updater(context) }`

## 发布一个版本

```powershell
python scripts\publish_release.py --target <已推送的完整提交SHA> --signer-sha256 <项目原证书公开SHA256>
```

它会核对版本号、实际 APK 包名/版本/正式属性与项目原签名，生成说明文件和 SHA256，固定远端标签到已推送提交，再上传草稿并回读校验后发布。指纹是公开证书指纹，不能传私钥或密码。

已经验收过的包用 `--no-build --prepared-apk <确切APK路径>` 复用，不把最后一次构建自动当成目标包。重复运行会核对原草稿并只上传缺少的文件；同名文件大小/摘要不一致、未知资产、标签提交不一致时停止，不删除或覆盖。已发布版本只读核对；`--verify-only` 显式禁止远端写入，并要求提供已验收包及原目标提交。历史版本核对不会把旧版提升为 latest。

验收：`python -m unittest discover -s tests -v`，只使用隔离 fixture 和模拟 GitHub，不登录账号或写真实发布；实际发布仍须按项目证据核对安装包和目标设备。

## 目录

```
kit/src/main/java/dev/h516/update/    通用代码（UpdateConfig 是唯一要改的文件）
kit/src/main/res/xml/update_paths.xml FileProvider 路径
tools/install_kit.py                  拷进新项目 + 替换配置
scripts/publish_release.py            发布（参数化版本）
```

## 注意

- **不要**把 client secret、PAT、登录 cookie、签名私钥写进代码或资源
- 升级必须同一个签名；换签名会让用户必须卸载重装（数据会丢）
- 项目接入的是源码快照；在项目现有 HANDOFF/PR 记录 kit 完整提交 SHA。更新 kit 后由项目负责人比较并合并，不能盲目覆盖已接入项目。
