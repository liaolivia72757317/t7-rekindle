# R2 发布镜像

正式 tag 构建成功后，CI 先发布 GitHub Release，再把同一份安装器、便携 ZIP 和更新清单同步到 Cloudflare R2。启动器优先请求镜像，网络失败时尝试 GitHub；下载和安装都由用户点击触发。

## 1. 配置 Cloudflare

1. 开通 R2，创建 Standard 存储桶 `t7-rekindle-releases`，位置采用自动选择。该桶只存放公开发行文件。
2. 在桶的 **Settings → Custom Domains** 绑定自定义域名。域名对应的 zone 需在同一 Cloudflare 账户；以下用 `https://HOST` 表示你的完整 HTTPS 下载入口。等待域名状态正常，不使用 `r2.dev` 作为生产入口。
3. 创建 R2 API Token，权限选择 **Object Read & Write**，限制为该桶。保存生成的 **Access Key ID** 和 **Secret Access Key**，不是普通 Cloudflare API Token 字符串。
4. 为下载域名配置 Cache Rules：
   - `/updates/*`：**Bypass cache**。
   - `/releases/*`：**Eligible for cache**，遵循对象的 `Cache-Control`，不要设置覆盖源站的长期错误响应缓存。
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
- 镜像失败时，GitHub Release 保留。修复配置后，在 Actions 重跑失败任务，或手动运行 **Mirror release to R2**，填写已发布的 tag；无需重建或重新创建 Release。草稿、预发布 Release 不更新稳定通道。
- 不同 tag 的镜像任务按桶串行运行；旧版本补传不会回退最新清单。同一版本的产物保持不可变，内容不同需使用新 tag，不覆盖已有对象。
- 工作流只上传两个发行文件和清单，不自动删除历史产物。

对象布局：

```text
releases/<tag>/T7-Rekindle-<tag>-Setup.exe
releases/<tag>/T7-Rekindle-windows-x64-<tag>.zip
updates/stable.json
```

两个文件均通过自定义域名下载验证大小和 SHA-256 后，才发布清单。版本文件使用 `public, max-age=31536000, immutable`；清单使用 `no-store`。

清单协议为 `schemaVersion: 1`，包含字符串 `version`、`summary`，以及 `installer`、`portable` 两个对象。每个对象包含 HTTPS `url`、正整数字节数 `size`、64 位十六进制 `sha256`。`version` 保留原始 tag，`summary` 来自 GitHub Release 正文。

## 4. 启动器行为

- 程序启动时、运行中每 30 分钟，以及游戏结束并完成会话清理后自动检查更新，也支持手动检查。所有触发复用同一逻辑，已有检查时跳过重叠触发，不排队。
- 检查先读取 R2 清单，10 秒超时；镜像不可用或清单无效时查询 GitHub 最新正式版本，并显示来源切换说明。检查结果不自动打开弹窗、下载或安装。
- 更新页的版本日志显示本次检查返回的 `summary` 和对应的 `version`，回退 GitHub 时使用 Release 正文和 tag；无论是否有新版本都展示发布说明。尚未检查、检查失败或暂无正式版本时显示相应提示，不使用内嵌日志替代。
- 点击“下载更新”后，更新页显示来源、百分比和已下载大小，并提供“暂停下载／继续下载”和“取消下载”。暂停保留本次下载数据，等待继续期间不计入网络读取超时；已挂起的连接或读取也冻结计时，继续后使用剩余超时预算。切换页面不停止下载。下载、暂停或准备安装期间跳过新的更新检查。
- 连接或连续无数据读取超过 30 秒视为网络失败；R2 下载中断后，从 GitHub 重新下载同一版本，不跨版本拼接文件。
- 点击“取消下载”或退出启动器会停止请求，清理本次 `.part` 文件；重试从头下载。磁盘错误、用户取消或校验失败不触发自动换源。
- 完整安装包保存在 `%LOCALAPPDATA%\T7-Rekindle\updates` 下；仅大小和 SHA-256 均通过后显示“立即安装”。SHA-256 校验用于检查传输完整性，不等同于发行者数字签名。
- 点击安装后，启动器复用现有会话退出确认和清理，结束当前游戏并保存设置，再将当前启动器目录和进程 ID 交给安装程序并退出。安装程序等待该进程退出后直接覆盖写入，跳过目录选择等向导页面，仅显示安装进度；等待超过 30 秒或检查退出状态失败时停止安装，不写入目标目录。拒绝退出或启动安装程序失败时保留启动器。
- 启动器内更新使用安装版，在当前目录覆盖同名文件并新增文件，不清空目录，也不删除新版未包含的旧文件。手动运行安装包仍显示完整向导，可选择目录。
- GitHub 旧 Release 缺少可用的 SHA-256 时，保留手动发布页入口。便携版也可通过版本化 ZIP 手动更新。

尚未内置镜像地址的旧启动器，首次需手动安装新版本。下载域名应长期保持可用；修改仓库变量不会改变已发布客户端内置的域名。

## 5. 上线验收与用量

1. 发布测试版本，确认 GitHub 与 R2 两端文件 SHA-256 相同，清单返回 JSON 且无缓存命中。
2. 从较旧版本检查并下载；屏蔽 GitHub 时 R2 路径仍可独立完成。
3. 模拟 R2 故障，确认 GitHub 备用源保持同版本、进度和取消功能。
4. 测试取消下载、关闭弹窗、校验错误及安装前拒绝退出；均不运行未确认的安装包。
5. 在国内实际网络测试域名、更新清单和安装包；海外 CI 探测只验证发布链路。

R2 Standard 当前每月包含 10 GB-month 存储、100 万次 Class A、1,000 万次 Class B 免费用量，直接出站流量免费；超额按量计费。免费额度不是消费硬上限，定期检查用量和账户可用的通知设置。长期保留产物会持续累计存储占用。具体以 [R2 价格页](https://developers.cloudflare.com/r2/pricing/) 为准。
