# 大安装包网络中断后的交付接续

本流程给执行 Agent 使用。用户仍只需下载和使用，不需要搬日志、接手机或配置令牌。

## 先读本次状态，再接续

共享入口完成本地资产校验后，在任何远端操作前原子更新 `release-state/audit.json` 为 `auditStatus=in_progress`，替换旧成功；正常完成为 completed，远端或下载异常为 failed。in_progress 只表示未取得终态，不能证明进程仍在运行。接续以本次命令结果、当前资产身份及该回执为准，不单独复用旧 publication.json 的成功；本地资产校验失败不代表旧发行失效，也不能以旧回执声称当前文件已通过。

下载失败时，回执保留已完成的 downloads、publicationStatus、failedDownload 和实际变更/上传次数，下一步为 resume_verified_downloads；已有完整文件仍核对摘要后复用，归属一致的部分文件沿原 Range 接续。publicationStatus=published 不等于下载完成，另看 downloadVerificationRequested 与 downloadVerificationComplete。下载前查询失败时 failedDownload 可以为空，应先恢复读取；回执不写原始异常、认证或临时签名地址。

发布阶段失败时 nextAction=inspect_remote_side_effects_before_retry；先核对远端，再决定是否继续。此改进不增加自动上传重试、覆盖、删除或后台续跑。仍复用原清单与输出目录，保留原签名和已有部分文件。

先使用共享入口的默认 curl；确认远端副作用后可显式改用 `--transport urllib`。同一路径反复整包中断时，停止从头碰运气，不把 TLS 实现差异当成已证实根因。本机保留已验收 APK、草稿身份和传输记录，改用下面的仓库内交付路径。不得清理无关历史文件或更改 VPN。

## 前置核对

- 应用 PR 已按依赖审查合并，最终源码 SHA、版本、原包名、原证书、适用架构、秘密与许可证检查已有证据。沿用原签名与受保护配置；不把签名资料传云。
- 本机 APK 与中文说明、校验文件均有确定 SHA256；版本标签固定到最终源码提交，不能固定到运输分支。
- 原仓库的可见性不变。只向该仓库传送本次本来就要发布的安装包与必要校验资料，不带 keystore、密码、token、local.properties 或用户数据。
- 从最终源码提交新建自己负责的隔离 worktree 和临时分支。未知改动不复制、不覆盖；分支不合入 main。

## 按确认点传输

1. 在临时分支的 `.release-delivery/` 将已签名 APK 按 **4 MiB** 分段，记录顺序、每段字节数和 SHA256，以及完整 APK 的大小和 SHA256。
2. **在添加任何资产前**建立该目录的 `.gitattributes`，内容为 `* -text`。Windows 的换行转换会破坏 JSON 说明或校验文件的字节一致性，二进制段也必须原样传递。
3. 每段单独提交并向自己负责的临时分支正常快进推送。使用 `[skip ci]` 只抑制运输段的无收益重复构建，不用于跳过应用 PR 的必需检查。记录已经确认的远端提交。
4. 推送返回不确定结果时，先查询该分支远端 SHA；已经到达的提交直接接续。仅当远端仍是已知上一提交或首次尚无分支时，才可有限重传这一小段。出现其他写手的提交立即保留现场，禁止 force push。
5. 全部段到齐后，最后一个提交才加入必要的元数据、共享发布模块和云端校验步骤。运输期间不启动完整 App 测试或重新构建。若既有构建工作流会重复执行，仅在这个未合并的运输分支调整其触发方式；正式分支保持原状。

## 云端只验包和发布

通用示例在 `templates/verify-carried-release.py`。复制到运输目录，旁边放相同 kit 提交的 `release_assets.py`、`release_remote.py`、`release_transport.py`，以及已有 `release.json`。示例对任何项目都从元数据读取身份，不含本次三个 App 的硬编码。

`delivery.json` 包含本机验收报告字段 `sourceCommit`、`apk`、`bytes`、`sha256`、`packageName`、`versionName`、`versionCode`、`certificateSha256`、`nativeAbis`，并补充：

```json
{
  "branch": "自己负责的临时分支名",
  "workflow": ".github/workflows/local-signed-release.yml",
  "publicRelease": false,
  "buildToolsVersion": "36.0.0",
  "notes": "与发布清单一致的说明文件名",
  "chunks": [{"name": "apk.part000", "bytes": 4194304, "sha256": "该段64位摘要"}]
}
```

公开原仓库明确使用 `publicRelease: true`，私有原仓库使用 false。构建工具版本与本机验签版本一致，不能随意选择最新预览工具。云端逐段校验、重组后再核对完整 SHA256，使用 SDK 的 apksigner 和 aapt2 复验原证书、v2 签名、包名、版本、正式属性、架构及 APK 内说明。任一项不符就停止，绝不通过换签名来修正。

云端调用已有共享发布状态机，只补草稿里缺失且一致的资产，保留未知占位；已发布版本只读。随后实际下载全部发布附件，核对大小与摘要，并保存对应 run、运输提交、最终源码提交和下载回执。校验脚本包含必须实际下载的断言，不能以元数据回读或缓存代替。

工作流只对自己的运输分支触发，固定原仓库身份。checkout 使用固定 action SHA、`fetch-depth: 0` 和 `persist-credentials: false`。发布 job 使用 GitHub 自带、限本仓库并随 job 到期的 `GITHUB_TOKEN`，仅授予该 job 所需的 `contents: write`；不修改仓库全局权限，不复制本机登录凭据或签名材料。详见 [GitHub 的令牌边界](https://docs.github.com/en/actions/concepts/security/github_token)与[工作流最小权限说明](https://docs.github.com/en/actions/tutorials/authenticate-with-github_token)。

确认正式 Release、原标签、三个附件及实际下载回执后，再核对并删除自己创建且远端 SHA 未变化的临时运输分支。不能把运输分支合入正式源码，不能删除其他分支、Release、资产或历史文件来腾配额。

## 证据边界

云端确实下载到发布文件，证明该路径的下载和字节一致性；还须单独检查 App 更新源的名称、版本、校验和授权约定。公开下载使用匿名请求；私有下载使用原仓库授权。手机授权、安装确认与真实数据保留未执行时须明示，不能把本流程成功报成手机验收成功。

该路径在足迹涂色与留册交付中使用本地原签名包，不迁移私钥，不使用 Actions artifact 存储配额。
