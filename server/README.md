# T7 Rekindle 认证服务

Java 25 单体服务，普通账户 API 与 Thymeleaf 管理页面同进程、同 Jar。
同时提供公告管理与启动器首页公告接口。
账户称号由本服务统一管理，启动器负责同步至本地游戏。
本阶段不连接启动器本地登录/逻辑/实例通道，不包含房间或战斗业务。

## 管理界面

管理页面使用 Tabler 1.6.1 浅色主题、双层顶部导航和最大 1320 px 居中容器；窄屏折叠导航，宽表格仅局部横向滚动。设计变量、组件行为和验收项目见[管理 UI 规范](../docs/planning/admin-ui.md)与[验收表](../docs/planning/admin-ui-checklist.md)。

- 登录后默认进入首页 `/admin`（首次登录仍先修改密码），点击品牌或“首页”可返回管理概览。
- 顶部导航依次为首页、服务维护、玩家管理、系统管理、组件验收。分组内使用二级菜单，内容页提供面包屑；窄屏折叠导航。
- 玩家管理与系统管理下的“操作记录”分别展示“建设中”，暂不实现日志分类。现有全部操作记录 `/admin/audit` 及首页最近操作保持可用。
- 首页展示全量账户总数、已启用与在线账户数、启动器当前发布公告、QQ 群校验状态和最近 5 条操作记录。在线统计与账户列表使用相同的凭证、账户状态及连接租约规则；公告展示已发布快照，不展示未发布草稿。
- 概览在打开或刷新页面时更新，不自动轮询；QQ 凭据状态表示最近检查结果，首页不触发群站请求。管理页时间按浏览器时区和语言区域格式显示，未启用 JavaScript 时保留带时区的 ISO 原值。

- CSS / JavaScript / 图标全部随 Jar 本地提供，不依赖 CDN、在线字体或 Node 构建。版本与许可见[管理界面资源](THIRD-PARTY.md#管理界面资源)。
- 登录后从“组件验收”进入 `/admin/ui-components`，检查按钮、表单、表格、确认框、抽屉、通知与空/错/加载状态；该页不读取或修改业务数据。
- 表单提供校验、重复提交锁定及未保存提示；公告、称号和群配置提交失败保留输入。网络中断时先确认服务端是否已保存，再决定是否重试。
- 公告正文使用本地 EasyMDE 编辑器，提供中文工具栏、预览和字符计数；保存仍提交原始 Markdown。预览按启动器支持的标题、段落、列表和行内格式展示，HTML、图片与链接保持文本；不启用图片上传、在线拼写检查或浏览器草稿存储。
- 账户/公告等列表保持原有固定分页，不新增业务排序、每页数量或批量操作接口。一次性密码的生成、消费与退出清除语义不变。

仅执行管理界面及相邻安全契约的定向测试（无需 Docker）：

```powershell
mvn -B -ntp "-Dtest=AdminUiTest,SecurityFiltersTest,WebErrorsTest,SecretResultsTest" test
node --test src/test/js/announcement-preview.test.cjs
```

## 构建与测试

需要 JDK 25、Maven 3.9+、可运行 Linux 容器的 Docker。首次测试会下载 MySQL 8.4.11、Redis 8.6.6 和 Testcontainers Ryuk 镜像。

在本目录执行：

```powershell
mvn -B -ntp clean verify
```

产物为 `target/rekindle-server-0.1.0-SNAPSHOT.jar`。测试使用独立临时数据库，不连接运行环境；Docker 不可用时测试失败，不跳过集成测试。
报告位于 `target/surefire-reports/`、`target/failsafe-reports/`、`target/site/jacoco/index.html`。业务 service 包的构建门禁为 90% 行覆盖率、100% 分支覆盖率。
打包测试通过实际 Jar 验证迁移、首次登录改密、进程重启、管理员恢复，以及 Redis/MySQL 中断时的行为。

## 容器镜像

Server CI 在 `mvn clean verify` 通过后构建并检查 `t7r-server:ci` 镜像。默认分支推送通过后发布 `ghcr.io/OWNER/t7r-server:latest` 和 `:sha-COMMIT`；`v*` 标签推送通过后发布版本标签和 `:sha-COMMIT`。PR 与其他分支只验证，不发布；发布任务使用内置 `GITHUB_TOKEN`，无需额外仓库凭据。首次推送后在 GitHub Packages 的 `t7r-server` → Package settings → Change visibility 中设为 **Public**；在此之前，CI 的匿名访问检查会失败，设置后重新运行即可。

本地构建：

```powershell
mvn -B -ntp clean verify
docker build --pull -t t7r-server:ci .
```

构建上下文只发送已构建的 Jar；运行镜像使用 Java 25 JRE，进程以非 root 用户运行，容器内监听 `0.0.0.0:8080`。发布任务检查 Jar 不包含本地 `application-local.yml`；本地构建的 Jar 若包含该文件，仅用于本地验证。启动时仍需按下文注入数据库、Redis 等环境变量，并先执行数据库迁移；可通过 `docker run t7r-server:ci --migrate` 向入口命令传递迁移参数。

### Podman Compose 单节点运行

[`podman compose`](https://docs.podman.io/en/latest/markdown/podman-compose.1.html) 通过已安装的 Compose provider 运行本目录的 [`compose.yaml`](compose.yaml)。MySQL 8.4 与 Redis 8 是可选的本地服务，默认不启动；也可以使用远程中间件。HTTPS 反向代理需另行准备，Server 的 `8080` 端口仅映射到宿主机 `127.0.0.1:8080`。本地 MySQL 和 Redis 使用具名卷保存数据，不向宿主机发布端口。

在宿主机创建仅部署账号可读的 `/etc/t7r/migration.env` 和 `/etc/t7r/server.env`，分别填入以下配置；`HOST` 是容器可达的数据库或 Redis 地址，各 `TOKEN` 应使用不同的实际值。启用 QQ 群成员校验时，`QQ_GROUP_COOKIE_KEY` 使用固定的 4–10 字符字符串。

```dotenv
# /etc/t7r/migration.env
DB_URL=jdbc:mysql://HOST:3306/rekindle?connectionTimeZone=UTC&forceConnectionTimeZoneToSession=true
MIGRATION_USER=rekindle_migration
MIGRATION_PASSWORD=TOKEN
```

```dotenv
# /etc/t7r/server.env
DB_URL=jdbc:mysql://HOST:3306/rekindle?connectionTimeZone=UTC&forceConnectionTimeZoneToSession=true
DB_USER=rekindle_runtime
DB_PASSWORD=TOKEN
REDIS_HOST=HOST
REDIS_PORT=6379
REDIS_PASSWORD=TOKEN
QQ_GROUP_COOKIE_KEY=TOKEN
COOKIE_SECURE=true
```

使用本地中间件时，创建仅部署账号可读的 `/etc/t7r/mysql.env` 和 `/etc/t7r/redis.env`：

```dotenv
# /etc/t7r/mysql.env
MYSQL_ROOT_PASSWORD=TOKEN
```

```dotenv
# /etc/t7r/redis.env
REDIS_PASSWORD=TOKEN
```

Redis 的 `TOKEN` 与 `server.env` 中的 `REDIS_PASSWORD` 一致。启动本地中间件后，按 [`deploy/database.sql.example`](deploy/database.sql.example) 创建数据库、迁移账号和运行账号；本地 MySQL 的 SQL 账号来源 `HOST` 可填 `%`，容器不向宿主机发布数据库端口。本地连接时，将两个环境文件中的数据库地址设为 `mysql:3306`，将 `server.env` 的 `REDIS_HOST` 设为 `redis`。仅使用远程中间件时，跳过本地中间件启动，`HOST` 填容器可达的远程地址；MySQL 和 Redis 可分别选择本地或远程。

在本目录执行，先迁移再启动；镜像使用 CI 发布的固定 `sha-` 标签，避免部署时隐式切换版本：

```bash
export T7R_SERVER_IMAGE=ghcr.io/OWNER/t7r-server:sha-COMMIT
# 仅对选择本地运行的中间件执行；也可只启动 mysql 或 redis。
podman compose --profile local-mysql --profile local-redis up -d mysql redis
# 按 deploy/database.sql.example 完成数据库初始化后：
podman compose run --rm migrate
podman compose up -d server
curl -fsS http://127.0.0.1:8080/actuator/health
podman compose logs server
```

首次启动日志包含随机管理员密码，需限制日志访问。`podman compose` 需要安装 `docker-compose` 或 `podman-compose` 作为 provider；若使用 rootful Podman 并需要宿主机重启后自动恢复，启用 `podman-restart.service`。

## 首次部署

1. 准备 MySQL 8.4 和独立 Redis 8 实例。根据 `deploy/database.sql.example` 创建数据库、迁移账号和仅有 DML 权限的运行账号，使用不同随机密码。
2. 设置迁移环境变量，然后显式运行 Flyway：

```powershell
$env:DB_URL = 'jdbc:mysql://HOST:PORT/rekindle?connectionTimeZone=UTC&forceConnectionTimeZoneToSession=true'
$env:MIGRATION_USER = 'rekindle_migration'
$env:MIGRATION_PASSWORD = Read-Host '迁移账号密码' -MaskInput
java -jar target/rekindle-server-0.1.0-SNAPSHOT.jar --migrate
Remove-Item Env:MIGRATION_PASSWORD
```

3. 使用运行账号启动。示例的 `Read-Host -MaskInput` 需要 PowerShell 7：

```powershell
$env:DB_USER = 'rekindle_runtime'
$env:DB_PASSWORD = Read-Host '运行账号密码' -MaskInput
$env:REDIS_HOST = 'HOST'
$env:REDIS_PORT = 'PORT'
$env:REDIS_PASSWORD = Read-Host 'Redis 密码' -MaskInput
java -jar target/rekindle-server-0.1.0-SNAPSHOT.jar
```

默认监听 `127.0.0.1:8080`。运行启动默认不自动执行迁移（`spring.flyway.enabled=false`），请先完成迁移；数据库或管理员表不可用时启动失败。
以 HTTPS 反向代理暴露服务，限制数据库和 Redis 的网络访问。`SERVER_ADDRESS`、`SERVER_PORT` 可调整监听。
默认管理员 Cookie 为 Secure。仅本机 HTTP 调试时设置 `$env:COOKIE_SECURE = 'false'`，部署环境保持默认值。
应用不信任任意转发头；代理部署按其网络策略限制登录流量，应用 IP 限流默认看到直连代理地址。

4. 首次成功启动会在控制台输出 `admin` 的随机密码。访问 `/admin/login`，首次登录必须改密，之后重新登录。
   同一数据库后续启动不重置密码、不重复输出。控制台可能被部署平台收集，应限制启动日志访问。
5. 在“账户与会话”创建普通账户。系统生成的密码只在成功页面显示一次；丢失时重新生成。

## 本地自动迁移

本地开发可显式启用启动迁移，Flyway 会先执行 `db/migration` 中的脚本，再初始化管理员：

```powershell
java -jar target/rekindle-server-0.1.0-SNAPSHOT.jar --spring.flyway.enabled=true
```

自动迁移默认使用 `spring.datasource` 的数据库和账号，账号需具备建表等 DDL 权限。
若在 `application-local.yml` 中设置 `spring.flyway.enabled=true`，启动时还需添加 `--spring.profiles.active=local`。
部署环境仍建议使用前述独立迁移账号执行 `--migrate`，运行账号保留 DML 权限。

## 公告管理

在后台“公告管理”新建公告，填写标题、摘要和 Markdown 正文，保存草稿后进入编辑页。

- 标题最多 120、摘要最多 500、正文最多 65,536 个 UTF-16 代码单元，均为必填项。标题、摘要按纯文本展示；正文支持启动器已有的标题、段落、列表、粗体、斜体和行内代码，不执行 HTML 或加载图片。
- “保存草稿”只更新编辑稿，已发布内容保持不变。“发布公告”在确认后一次性保存当前表单内容、替换发布快照并刷新发布时间。
- 多条公告可以同时处于已发布状态，首页只取发布时间最新的一条；同一时间按公告 ID 倒序确定顺序。重新发布也会更新发布时间。
- “下线公告”停止对外展示该条公告，保留编辑稿和最新发布快照。首页回到其他已发布公告中的最新一条，全部下线则使用启动器内置公告。
- 新建、保存、发布、下线与操作记录在同一事务提交。编辑页输入校验失败保留表单内容；没有删除、定时发布和历次修订归档功能。

`GET /api/v1/announcements/latest` 无需登录，返回完整发布快照；无已发布公告返回 `204`。完整字段见 [OpenAPI](contracts/openapi-auth-v1.yaml)。该接口不依赖账号凭据、群成员状态或 Redis；数据库异常沿用 `503 SERVICE_UNAVAILABLE`。

启动器复用构建时的 `T7R_SERVER_BASE_URL`。窗口显示后及返回首页时异步获取，两次请求至少相隔 60 秒，单次超时 8 秒。读取期间先展示当前内容，成功后整体更新标题、摘要和正文；未配置地址、无公告、网络失败、旧服务 `404` 或无效响应均使用原内置公告，不写远端公告磁盘缓存，也不阻塞本地游戏。

升级顺序：先通过现有 `--migrate` 流程执行 `V005__announcements.sql`，再部署服务端和启动器。迁移不自动创建公告；旧启动器继续使用静态公告，新启动器连接旧服务时自动使用内置公告。

## 称号管理

先执行迁移至 `V007__custom_titles.sql`，再配套部署服务端、启动器及 NativeBridge。目录预置原游戏 `1007 首测元老`、`1008 内测精英` 和社区 `1000000 重燃共建者`，迁移不授予称号、不补发历史奖励；旧启动器不支持自定义称号。

- 后台“称号管理”新增或编辑社区称号的名称、介绍、排序与展示开关；ID 自动分配，原游戏名称保持不变。目录最多 64 条，停用保留历史记录。
- 选择称号的“发放与撤销”，按登录名前缀查找账户并查看持有状态；账户详情的“称号管理”仍可操作。所有写操作保留审计记录。
- “重燃共建者”及其他自定义称号由维护者确认贡献后手动授予，不增加战斗属性。
- 2026 年 10 月（北京时间）功能上线后的成功上线请求自动授予“内测精英”，心跳不触发发放。
- 所有称号永久有效；新授予且未佩戴时自动佩戴，关闭展示保留持有权并卸下。

完整使用、同步和客户端约束见[账户称号](../docs/account-titles.md)，API 见 [OpenAPI](contracts/openapi-auth-v1.yaml)。

## 管理员恢复

拥有本机和数据库运维权限时，使用运行环境执行：

```powershell
java -jar target/rekindle-server-0.1.0-SNAPSHOT.jar --reset-admin --confirm-reset-admin
```

该命令不启动 HTTP 服务。它更换随机密码、使现有管理员会话失效并要求首次改密，明文仅输出到本次控制台。
适用于初始密码丢失或“数据库已提交但输出前进程退出”。普通启动不承担恢复职责。

也可使用 `scripts/server.ps1 -Action Build|Test|Migrate|Start|ResetAdmin`，重置额外传 `-ConfirmResetAdmin`。

## 认证与在线状态

- 普通账户 session 从创建起固定有效 30 天，32 随机字节 token 仅在登录时返回，数据库保存 SHA-256 哈希。
- 同账户可保留多个有效 session。登录不下线其他连接；`POST /api/v1/auth/online` 显式接管。
- 每次上线创建独立 `connectionId`。心跳 15 秒一次，在线租约 45 秒，最多延续至 session 到期。
- 被接管或租约过期后，心跳返回 409。客户端停止自动心跳，不自动抢回；用户明确上线时重新调用上线接口。
- 下线保留 session；退出登录撤销当前 session。禁用、重置密码、撤销全部 session 使用 `auth_epoch` 使旧凭证失效，并清空在线记录。
- MySQL 是唯一在线归属的权威数据源；加锁顺序为账户、session、在线记录。涉及称号写入或显式上线时，先锁称号目录；后台写操作在所有业务锁之前锁定管理员记录并复核其权限版本。
- 后台身份独立，Cookie 不用于普通账户 API；Bearer token 不用于后台。管理员空闲 30 分钟退出，进程重启后重新登录。多实例后台需要粘性会话；普通账户认证和在线状态不需要。

完整请求/响应见 [OpenAPI](contracts/openapi-auth-v1.yaml)。不提供 refresh；密码账户由管理端创建，QQ 账户在身份、群资格及首次游戏名称均验证成功后创建。

## 账户游戏名称

- `gameName` 独立于登录名、QQ 昵称。去除首尾空白后全局区分大小写唯一，按 GBK 编码最多 31 字节；禁止控制字符、emoji、欧元符号、私用区及 GBK 不支持的字符。禁用账户仍占用名称。
- 升级顺序为执行 `V008__account_game_names.sql` 迁移、部署服务端、更新启动器。旧账户保留 ID 与数据，名称为 `NULL`，不自动使用登录名或 QQ 昵称填充。旧服务端未返回有效名称时，新启动器提示升级，不接受为已登录状态。
- 新用户 QQ 登录首次返回 `409 REGISTRATION_REQUIRED`，启动器要求设置游戏名称，再携带名称及仅保存在内存的 QQ Cookie 提交原登录接口。重新验证身份和群资格后，账户、名称及 Session 在同一事务中创建。取消设置不创建账户；已成功提交的账户不因关窗而回滚。
- 旧账户通过 `PUT /api/v1/auth/game-name` 初始化，重复提交同名幂等；普通用户已设置后不可更名。未设置名称的 Session 仅能读取自身身份、读取或初始化名称、退出登录；其他认证接口返回 `409 REGISTRATION_REQUIRED`。
- 管理后台创建账户时必须填写游戏名称；账户详情可改名，冲突返回 `GAME_NAME_EXISTS`，修改与审计日志在同一事务中提交。
- `GET /api/v1/auth/game-name` 无需在线资格或群成员校验。启动器登录后每 30 秒独立同步，重新上线前刷新；离线仍锁定为服务端名称，网络失败保留最近确认值。会话失效则退出登录。重启仍需联网验证缓存凭据，不以本地缓存直接恢复登录。
- 未登录时名称可编辑，首次默认“新玩家”；退出登录保留最后名称。注册弹窗期间暂停启动游戏，启动中或运行中继续锁定名称编辑。运行中同步的新名称只用于下次启动，不修改已运行游戏。

## QQ 扫码登录

启动器使用 WebView2 InPrivate 展示 QQ 互联官方登录页面，扫码后只上传验证地址作用域内的 Cookie 和 Windows `MachineGuid`。
`POST /api/v1/auth/qq/sessions` 在事务外调用固定的 `get_nick` 地址，成功且已提交有效游戏名称后，在事务内创建或更新 QQ 账号并签发本应用 Session。
用户 QQ 互联 Cookie 不写入数据库、磁盘或日志；后续心跳和推送仅使用本应用 Bearer Token 与 `X-Machine-Id`。

- QQ 账号 `login_name`、`qq_number` 均采用 QQ 号字符串；昵称每次扫码更新，头像使用 QQ 头像 URL。
- 已有同名密码账号返回 `QQ_ACCOUNT_CONFLICT`，不自动改名或绑定。管理端禁止新建纯数字密码账号，已有数字密码账号仍可登录。
- QQ 账号不支持密码登录或重置密码；禁用、撤销与会话管理复用管理端。
- 新机器扫码登录撤销其他机器的 Session，并通知其在线连接；同机器保留原 Session，但仍只有一个在线连接。
- 应用凭证固定有效 30 天，启动器用 DPAPI CurrentUser 保存；正常关闭只下线，退出账号清理本地凭据并尽力注销，失败不显示额外提示。
- 未登录、断网或被踢下线均不停止本地游戏。网络异常保留凭据；租约过期后由用户点击“重新上线”，不自动接管。

### QQ 群成员校验

默认关闭。开启后，QQ 扫码登录、恢复登录和重新上线会校验目标群成员身份；在线账号由心跳触发复查，成功结果缓存 15 分钟。普通密码账号不受影响。

部署前运行新增的 Flyway 迁移，并为所有服务实例设置同一个 `QQ_GROUP_COOKIE_KEY`。该值接受 **4–10 个字符的字符串**（按 Unicode 码点计数，保留首尾空格），无需 Base64 编码。服务端使用 PBKDF2-HMAC-SHA256（固定用途盐、600,000 次迭代）稳定派生 256 位加密密钥；相同配置在重启及多实例间保持一致。例如在 PowerShell 中注入当前进程环境，将 `TOKEN` 替换为实际配置：

```powershell
$env:QQ_GROUP_COOKIE_KEY = 'TOKEN'
```

将配置字符串保存到部署平台的 Secret 配置中，与数据库分别保管；重启时复用同一值，不要每次启动重新生成。建议使用 10 位随机组合，派生后的 256 位长度不代表原始短字符串具有同等安全强度。配置丢失或更换后，原凭据需重新扫码；从旧 Base64 密钥配置切换时也需重新扫码。

访问后台“QQ 群校验”：

1. 点击“扫码登录群站”，使用群主或管理员 QQ 扫码并在手机确认。
2. 从返回的可管理群列表选择目标群；普通成员群不展示，也不接受任意群号。
3. 勾选“启用入群校验”并保存。服务端复核管理权限后，原子保存目标群与 AES-256-GCM 加密 Cookie。

重新扫码期间原配置继续生效，待选群凭据保留 10 分钟。后台提供凭据检查与独立关闭开关；关闭不依赖群站可用性。Cookie 的作用域、绝对过期时间及更新一并保存，不向浏览器返回明文。每 30 分钟低频健康检查；实际成员查询也会更新凭据健康状态。健康检查不保证续期，失效后在后台重新扫码。

- 确认未入群返回 `403 QQ_GROUP_NOT_MEMBER`，`message` 包含目标群号，并记录拒绝日志。新用户不创建账户或 Session；已有账户更新 QQ 资料。已有在线账号下线但保留原 Session，加入后可重新上线。
- 群站超时、管理员凭据或权限异常返回 `503 QQ_GROUP_UNAVAILABLE`，阻止新登录、恢复和重新上线。已有合法在线连接继续续租，故障恢复后再判断；账户禁用、Session 撤销与过期仍正常执行。
- 故障重试间隔为 1 分钟。换群、开关和保存新管理员凭据使旧验证缓存失效。
- 账户列表展示启用／禁用、在线／离线和最近成功接受的心跳时间；开启检测时另显示当前配置的最近真实成员查询时间及结果。命中缓存不更新时间，离线不清除时间，未检测与普通密码账号分别显示“尚未检测”和“不适用”。时间按浏览器时区显示，存储和接口保持 UTC。

群站网页接口及扫码协议可能变化。上线验收应通过部署服务器的后台实际扫码、选群，再用已入群及未入群账号验证。自动化测试使用合成凭据，不写入真实 Cookie 或群号。

### QQ 接口上线验证

`get_nick` 的响应只有昵称，QQ 号来自 `uin`；有 `p_uin` 时要求二者一致。这不是 QQ OAuth 接口，也没有返回稳定身份字段的公开契约。
上线前必须用本人测试账号确认：有效 Cookie 从部署服务器访问成功；缺失或过期凭据失败；交叉替换两个测试账号的 `uin`、`p_uin` 与凭据时拒绝错误绑定。
昵称相同不表示同一账号。若接口不校验 QQ 号与凭据的绑定，或部署服务器访问被风控拦截，停止开放该登录入口，不以客户端自报 QQ 号签发凭证。
自动化测试采用合成响应，不代替上述真实接口验收；不要将真实 Cookie 放入测试样例或日志。

### 即时踢下线与多实例

`GET /api/v1/auth/online/{connectionId}/events` 返回 SSE，初始 `ready` 事件确认连接，`kicked` 包含目标 `sessionId`、`connectionId` 和原因。
异机登录、在线接管、管理员撤销与禁用在事务提交后通知；本实例直接投递，其他实例通过现有 Redis Pub/Sub 接收。
事件不持久化、不补发。Redis 推送故障不改变已提交的会话状态，心跳和 SSE 重连验证负责兜底。
心跳 15 秒、租约 45 秒；SSE 每 15 秒保活但不续租。启动器 SSE 按 1、2、5、10、30 秒退避重连原连接，不自动抢回在线资格。

Nginx 在现有 HTTPS 站点内为此路径设置流式转发（将地址替换为部署配置）：

```nginx
location ~ ^/api/v1/auth/online/[^/]+/events$ {
    proxy_pass http://HOST:PORT;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 75s;
}
```

发布顺序：执行 `V003__qq_authentication.sql` 迁移、部署服务端、设置 `T7R_SERVER_BASE_URL=https://HOST` 后构建启动器。
数据库备份和原有密码登录兼容要求保持不变；不要回滚或重写已执行的迁移。

## 运行行为

- 密码使用 Argon2id（19 MiB、2 次迭代、并行度 1）；不记录密码和 token。
- 两类登录分别限流：同登录名每 5 分钟 10 次、同来源 IP 每分钟 30 次（计所有尝试），Redis Lua 原子计数。
  可通过 `rekindle.login-limit.account-attempts`、`rekindle.login-limit.ip-attempts` 调整。限流响应为 429，`Retry-After: 300`。
- Redis 故障使新密码登录返回 503；已有 session 鉴权、心跳和退出只依赖 MySQL。
- MySQL 故障时不接受未经确认的鉴权或在线变更。业务不可用返回 503，不使用缓存放行。
- `/actuator/health` 不公开内部细节。其他运维端点默认拒绝外部访问。
- 管理表单启用 CSRF，写操作带审计。账户查询按登录名前缀筛选，列表每页 50 条。
- 一次性密码结果只在管理员的内存会话中保存，5 分钟过期、首次 GET 消费；每会话最多暂存 10 份。
- 停止进程前完成请求排空。升级前备份数据库，先迁移再启动。回滚 Jar 前确认旧版本兼容当前 schema；不自动逆向删除迁移。

## 项目边界

`domain` 为不可变记录，`persistence` 为 MyBatis 映射，`service` 管理事务和业务，`web/config` 管理 HTTP、模板和安全链。
依赖和许可见 [THIRD-PARTY.md](THIRD-PARTY.md)。运行凭据用环境变量注入，不写入版本库。
