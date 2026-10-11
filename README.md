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

脚本会把源码拷进去、替换好配置、并把发布脚本放进 `scripts\`，然后打印剩下的手工步骤（就下面这几条）：

1. `app/build.gradle.kts`：`implementation("androidx.core:core-ktx:1.18.0")`（FileProvider 要用）
2. `AndroidManifest.xml`：加 `INTERNET`、`REQUEST_INSTALL_PACKAGES` 两个权限
3. `AndroidManifest.xml`：加 FileProvider（authorities 用 `<applicationId>.updates`，路径用 `@xml/update_paths`）
4. `app/src/main/res/xml/update_paths.xml`：`<cache-path name="update_apk" path="updates/" />`
5. `app/src/main/assets/release-notes.json`：当前版本的中文说明（和发布时挂的是同一份）
6. 界面上放一个入口：`UpdateScreen(updater, innerPadding)`，`val updater = remember { Updater(context) }`

## 发布一个版本

```powershell
python scripts\publish_release.py
```

它会核对 `build.gradle.kts` 和 `assets/release-notes.json` 的版本号是否一致（不一致直接报错），
出正式包，生成说明文件和 SHA256，然后挂 release。

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
- 这个 kit 的代码是从项目里拷出来的一份，改了 kit 记得同步回项目（或者重新跑一次 install_kit）
