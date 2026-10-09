# R2 发布镜像

正式 tag 构建成功后，CI 先发布 GitHub Release，再把同一份安装器、便携 ZIP 和更新清单同步到 Cloudflare R2。启动器优先请求镜像，网络失败时尝试 GitHub；下载和安装都由用户点击触发。

## 1. 配置 Cloudflare

1. 开通 R2，创建 Standard 存储桶 `t7-rekindle-releases`，位置采用自动选择。该桶只存放公开发行文件。
2. 在桶的 **Settings → Custom Domains** 绑定自定义域名。域名对应的 zone 需在同一 Cloudflare 账户；以下用 `https://HOST` 表示你的完整 HTTPS 下载入口。等待域名状态正常，不使用 `r2.dev` 作为生产入口。
3. 创建 R2 API Token，权限选择 **Object Read & Write**，限制为该桶。保存生成的 **Access Key ID** 和 **Secret Access Key**，不是普通 Cloudflare API Token 字符串。
4. 为下载域名配置 Cache Rules：
   - `/updates/*`、`/latest/*`、`/releases/*/changelog.md`：**Bypass cache**。
   - `/releases/*.exe`、`/releases/*.zip`、`/previews/*`：**Eligible for cache**，遵循对象的 `Cache-Control`，不要设置长期错误响应缓存。
5. 保持更新入口匿名可访问。该域名不使用登录验证、浏览器挑战或要求 Referer 的防盗链；桌面客户端不需要 CORS 配置。

桶根地址不列出文件，根路径返回 404 不代表配置失败。首次上传后检查 `/updates/stable.json` 和具体产物地址。

官方文档：[公开桶与自定义域名](https://developers.cloudflare.com/r2/buckets/public-buckets/)、[R2 凭据](https://developers.cloudflare.com/r2/api/tokens/)。

## 2. 配置 GitHub Actions

在仓库 **Settings → Secrets and variables → Actions** 中添加：

| 类型 | 名称 | 值 |
| --- | --- | --- |
| Variable | `R2_ACCOUNT_ID` | Cloudflare Account ID |
| Variable | `R2_BUCKET` | `t7-rekindle-releases` |
| Variable | `R2_PUBLIC_BASE_URL` | `https://HOST`，无路径、查询参数或凭据 |
| Secret | `R2_ACCESS_KEY_ID` | R2 Access Key ID |
| Secret | `R2_SECRET_ACCESS_KEY` | R2 Secret Access Key |

密钥仅在镜像工作流中映射为 AWS CLI 环境变量；客户端只内置公开域名。不要把密钥写进源码、配置文件或 Issue。

正式 tag 构建要求配置公开域名，镜像任务要求完整凭据。普通分支和本地构建可以不设置镜像地址，此时保持 GitHub 更新模式。

## 3. 发布与补传

- 正式 tag 使用 `v主版本.次版本.修订号`，也支持第四段数字和 `+构建标识`；每段数字为 0—65534。CI 将相同的数字版本写入程序集和安装器，构建标识不参与版本排序。预发布 tag 不属于该 CI 的正式发布格式。
- 发布 tag 使用 annotated tag（可签名），注释保存基于实际变更梳理并确认的中文 Markdown 更新日志；可通过 [create-tag 技能](../.agents/skills/create-tag/SKILL.md) 完成。CI 使用 `--notes-from-tag` 将注释作为 Release 正文，再同步到 R2 清单的 `summary`，不使用 GitHub 自动生成说明。轻量 tag 会回退使用提交信息，不用于此日志流程。
- 推送正式 tag 后，CI 发布 GitHub Release，随后调用 [R2 镜像工作流](../.github/workflows/r2-mirror.yml)。它通过 GitHub CLI 下载已发布产物，再使用 [AWS CLI / S3 API](https://developers.cloudflare.com/r2/examples/aws/aws-cli/) 上传。
- 镜像失败时，GitHub Release 保留。修复配置后，在 Actions 重跑失败任务，或手动运行 **Mirror release to R2**，填写已发布的 tag，保持 `backfill_notes` 关闭；无需重建或重新创建 Release。草稿、预发布 Release 不更新稳定通道。
- 不同 tag 的镜像任务按桶串行运行；旧版本补传只扩展日志列表，不回退目标版本或覆盖 `latest/`。同一版本的安装包保持不可变，内容不同需使用新 tag；日志允许根据 Release 正文重新同步更正。
- 正常发布先上传并验证版本化安装包和日志，再覆盖并验证 `latest/`，最后发布稳定清单。不自动删除历史产物。

对象布局：

```text
releases/<tag>/T7-Rekindle-<tag>-Setup.exe
releases/<tag>/T7-Rekindle-windows-x64-<tag>.zip
releases/<tag>/changelog.md
latest/T7-Rekindle-Setup.exe
latest/T7-Rekindle-windows-x64.zip
latest/changelog.md
updates/stable.json
```

版本化安装包和 `latest/` 安装包均通过自定义域名验证大小和 SHA-256，日志验证 UTF-8 正文一致后，才发布清单。版本化安装包使用 `public, max-age=31536000, immutable`；清单、日志和 `latest/` 使用 `no-store`。

清单协议保持 `schemaVersion: 1`，包含字符串 `version`、`summary`，以及 `installer`、`portable` 两个对象。每个对象包含 HTTPS `url`、正整数字节数 `size`、64 位十六进制 `sha256`。顶层字段仍描述最新可安装版本，下载地址仍指向版本化目录，旧客户端可继续使用。

新增 `versions` 字符串数组，例如 `["v0.3.0", "v0.2.2", "v0.2.1"]`，保留原始 tag，去重后按数字版本降序排列。每条日志位于对应目录的 `changelog.md`，保存 GitHub Release 正文；顶层 `summary` 仍是目标版本的单版说明。

### 固定下载入口

网站和分享链接可使用 `https://HOST/latest/T7-Rekindle-Setup.exe`、`https://HOST/latest/T7-Rekindle-windows-x64.zip` 和 `https://HOST/latest/changelog.md`。发布新版或重跑当前目标版本时覆盖这三个对象；启动器继续使用版本化地址，避免更新过程中下载内容变化。

`latest/` 按文件覆盖，不是整目录原子切换。发布中或失败后可能暂时混合版本；失败不会提交新稳定清单，重跑同一版本可修复。此入口不提供目录浏览页。

### 补齐历史日志

部署新发布流程和缓存规则后，先重跑当前最新正式版镜像，初始化固定入口；再手动运行 **Mirror release to R2**，清空 `tag` 并开启 `backfill_notes`。两种模式互斥。

日志补齐要求已有有效稳定清单，分页读取 GitHub Releases，仅同步不高于镜像目标版本的正式版日志，不下载或重传安装包。全部日志验证成功后合并版本列表；目标版本日志更正也同步到顶层 `summary` 和 `latest/changelog.md`。空正文保留版本记录，不将草稿、预发布或格式不支持的 tag 加入列表。

本地配置与工作流相同的环境变量后，可运行 `python scripts/mirror_release.py --backfill-notes`；此模式要求 `RELEASE_TAG` 为空。补齐完成并核对固定入口和版本列表后，再发布支持日志汇总的新客户端。

## 4. 启动器行为

### 预览渠道发布

默认分支成功的 push／手动 CI 将同一次构建的安装器与便携 ZIP 发布到 R2，不创建 GitHub 预发布。PR、其他分支、tag、失败或取消的构建不更新预览入口。复用上述 R2 配置；缺少发布配置时作业明确失败。

```text
previews/<runId>/<runAttempt>/T7-Rekindle-Setup.exe
previews/<runId>/<runAttempt>/T7-Rekindle-windows-x64.zip
updates/preview.json
```

预览清单使用 `schemaVersion: 1`、`channel: "preview"`，含数字 `version` 的字符串表示、`summary`、`build` 及与正式清单相同格式的 `installer`、`portable`。`build` 包含 `channel`、数字版本字符串 `version`、正整数 `runId`、`runNumber`、`runAttempt` 和 `commitHash`，与 ZIP 中程序包清单、程序集身份一致。说明包含本次提交标题和 CI 链接。

预览版本显示为 `v{基础版本}p{runNumber}.{runAttempt}`，例如 `v0.1.0p128.2`。显示文本不附带提交哈希，Git 提交仍在关于页单独展示；清单与程序集中的数字版本和构建身份不变。

按 `(runNumber, runAttempt)` 判断新旧；同一提交重新构建也可产生新版本，仅重试发布使用原产物身份。发布串行执行，先验证两个不可变产物的公开大小及 SHA-256，再写入 `no-store` 清单；失败保留原清单，旧任务补发不回退目标。预览安装器没有 GitHub 备用地址。历史 R2 产物暂不自动清理。

### 渠道选择与安装

- “设置 → 启动器设置”选择正式版（默认）或预览版，统一保存到 `settings.json` 的 `updateChannel` 字段，旧渠道配置自动迁移。保存后清除旧渠道状态并立即检查；检查、下载（含暂停）、安装期间禁用切换。迁移与回退约束见[设置格式](runtime-contracts.md#设置格式)。
- 当前构建身份来自程序集，不随用户设置变化。预览转正式时任何正式版均视为更新；正式转预览同样提供最新可用预览。安装后恢复同渠道比较。本地及其他分支构建没有可比预览身份时，提供所选渠道最新版本。
- 预览仅请求 `updates/preview.json`；404 显示暂无预览构建，其他错误保留明确失败状态，不回退正式渠道。预览及跨渠道仅显示目标版本说明，正式渠道内部继续按以下规则汇总日志。

- 程序启动时、运行中每 30 分钟，以及游戏结束并完成会话清理后自动检查更新，也支持手动检查。所有触发复用同一逻辑，已有检查时跳过重叠触发，不排队。
- 正式渠道先读取 R2 清单，10 秒超时；镜像不可用或清单无效时查询 GitHub 最新正式版本，并显示来源切换说明。检查结果不自动打开弹窗、下载或安装。
- 有新版时，更新页按版本从新到旧展示 `当前版本 < 日志版本 ≤ 目标版本` 的全部日志；已是最新版或本地版本更高时，只展示目标版本说明。沿用四段数字比较并忽略构建标识，当前版本无需出现在列表中。
- R2 日志最多并发读取 4 个。GitHub 回退仍先确定最新正式版，再分页读取历史正文，使用相同的版本筛选规则；不依赖接口列表的排列顺序。
- R2 列表缺失、无效或部分日志加载失败时，通过一次分页 GitHub 历史查询补齐，保留原安装信息。仍缺失的已知版本显示错误，历史列表不完整时显示整体提示；目标版本可使用 `summary`。日志失败不影响下载安装，后续检查重试。
- 日志按版本标题分组并保留 Markdown；空正文显示未填写说明。尚未检查、检查失败或暂无正式版本时显示相应提示，不使用内嵌日志替代。
- 点击“下载更新”后，更新页显示来源、百分比和已下载大小，并提供“暂停下载／继续下载”和“取消下载”。暂停保留本次下载数据，等待继续期间不计入网络读取超时；已挂起的连接或读取也冻结计时，继续后使用剩余超时预算。切换页面不停止下载。下载、暂停或准备安装期间跳过新的更新检查。
- 连接或连续无数据读取超过 30 秒视为网络失败；正式渠道 R2 下载中断后，从 GitHub 重新下载同一版本，不跨版本拼接文件。预览下载失败由用户重试。
- 点击“取消下载”或退出启动器会停止请求，清理本次 `.part` 文件；重试从头下载。磁盘错误、用户取消或校验失败不触发自动换源。
- 完整安装包保存在 `%LOCALAPPDATA%\T7-Rekindle\updates` 下；仅大小和 SHA-256 均通过后显示“立即安装”。SHA-256 校验用于检查传输完整性，不等同于发行者数字签名。
- 点击安装后，启动器复用现有会话退出确认和清理，结束当前游戏并保存设置，再将当前启动器目录和进程 ID 交给安装程序并退出。安装程序等待该进程退出后直接覆盖写入，跳过目录选择等向导页面，仅显示安装进度；等待超过 30 秒或检查退出状态失败时停止安装，不写入目标目录。拒绝退出或启动安装程序失败时保留启动器。
- 启动器内更新使用安装版，在当前目录覆盖同名文件并新增文件，不清空目录，也不删除新版未包含的旧文件。手动运行安装包仍显示完整向导，可选择目录。
- GitHub 旧 Release 缺少可用的 SHA-256 时，保留手动发布页入口。便携版也可通过版本化 ZIP 手动更新。

尚未内置镜像地址的旧启动器，首次需手动安装新版本。下载域名应长期保持可用；修改仓库变量不会改变已发布客户端内置的域名。

### 历史版本回退

新发布流程增加 `updates/stable-history.json` 和 `updates/preview-history.json`，均使用 `no-store`，由现有 `/updates/*` 缓存规则覆盖。最新版本清单和日志索引协议不变。历史列表独立加载，正式版安装包保留同版本 GitHub 备用地址，预览版仍仅使用 R2。

历史清单结构为 `schemaVersion: 1`、`channel: "stable" | "preview"`、`entries: [...]`。每条记录包含 `version`、首次入表的 UTC `publishedAt`、单版 `summary`、正整数 `settingsSchemaVersion`、原有格式的 `installer`；预览记录还包含完整 `build`。正式版按数字版本降序，预览版按 `(runNumber, runAttempt)` 降序。`publishedAt` 在重跑时保留，说明允许更正，安装包、构建身份和设置契约不可变。

只有便携包清单明确声明 `rollbackProtocol: 1`、构建身份一致且安装器／便携包均验证成功的版本才加入历史。没有协议标记的旧包仍可按原流程发布，但不补录历史。较旧预览任务补发时可以上传其不可变产物并增加历史，不改变最新版本入口；发布失败可幂等重跑，不以空列表覆盖损坏的历史清单，不自动清理历史。

安装器使用 `/ROLLBACK=1 /RESETSETTINGS=0|1 /UPDATECHANNEL=stable|preview`，同时要求现有 `/LAUNCHERPID`。普通更新不传回退参数。设置备份与恢复见[运行契约](runtime-contracts.md#回退设置契约)。安装仍覆盖同名文件并新增文件，不清空安装目录、不删除目标版本未包含的文件。

先部署支持协议的安装器与发布脚本，再验证客户端历史入口。每个渠道至少需要两个支持协议的构建，才可完成同渠道回退验收。可运行 `T7.ManagedHarness.exe --rollback-tests`，以及 `python -m pytest -q tests/python/test_rollback_history.py tests/python/test_installer_update.py`；安装集成测试使用临时目录和隔离注册表项。

## 5. 上线验收与用量

1. 发布测试版本，确认 GitHub 与 R2 两端文件 SHA-256 相同，清单返回 JSON 且无缓存命中。
2. 从较旧版本检查并下载；屏蔽 GitHub 时 R2 路径仍可独立完成。
3. 模拟 R2 故障，确认 GitHub 备用源保持同版本、进度和取消功能。
4. 测试取消下载、关闭弹窗、校验错误及安装前拒绝退出；均不运行未确认的安装包。
5. 在国内实际网络测试域名、更新清单和安装包；海外 CI 探测只验证发布链路。
6. 验证跨版本日志及 GitHub 回退结果；模拟单篇日志缺失，确认提示缺失但仍可下载安装。
7. 发布新版后检查三个固定入口的内容和缓存头；补传旧版后确认固定入口及顶层目标版本没有回退。

R2 Standard 当前每月包含 10 GB-month 存储、100 万次 Class A、1,000 万次 Class B 免费用量，直接出站流量免费；超额按量计费。免费额度不是消费硬上限，定期检查用量和账户可用的通知设置。长期保留产物会持续累计存储占用。具体以 [R2 价格页](https://developers.cloudflare.com/r2/pricing/) 为准。
