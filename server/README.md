# T7 Rekindle 认证服务

Java 25 单体服务，普通账户 API 与 Thymeleaf 管理页面同进程、同 Jar。
本阶段不连接启动器本地登录/逻辑/实例通道，不包含房间或战斗业务。

## 构建与测试

需要 JDK 25、Maven 3.9+、可运行 Linux 容器的 Docker。首次测试会下载 MySQL 8.4.11、Redis 8.6.6 和 Testcontainers Ryuk 镜像。

在本目录执行：

```powershell
mvn -B -ntp clean verify
```

产物为 `target/rekindle-server-0.1.0-SNAPSHOT.jar`。测试使用独立临时数据库，不连接运行环境；Docker 不可用时测试失败，不跳过集成测试。
报告位于 `target/surefire-reports/`、`target/failsafe-reports/`、`target/site/jacoco/index.html`。业务 service 包的构建门禁为 90% 行覆盖率、100% 分支覆盖率。
打包测试通过实际 Jar 验证迁移、首次登录改密、进程重启、管理员恢复，以及 Redis/MySQL 中断时的行为。

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

默认监听 `127.0.0.1:8080`。运行启动不自动执行迁移，请先完成迁移；数据库或管理员表不可用时启动失败。
以 HTTPS 反向代理暴露服务，限制数据库和 Redis 的网络访问。`SERVER_ADDRESS`、`SERVER_PORT` 可调整监听。
默认管理员 Cookie 为 Secure。仅本机 HTTP 调试时设置 `$env:COOKIE_SECURE = 'false'`，部署环境保持默认值。
应用不信任任意转发头；代理部署按其网络策略限制登录流量，应用 IP 限流默认看到直连代理地址。

4. 首次成功启动会在控制台输出 `admin` 的随机密码。访问 `/admin/login`，首次登录必须改密，之后重新登录。
   同一数据库后续启动不重置密码、不重复输出。控制台可能被部署平台收集，应限制启动日志访问。
5. 在“账户与会话”创建普通账户。系统生成的密码只在成功页面显示一次；丢失时重新生成。

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
- MySQL 是唯一在线归属的权威数据源；加锁顺序为账户、session、在线记录。后台写操作在此之前锁定管理员记录并复核其权限版本。
- 后台身份独立，Cookie 不用于普通账户 API；Bearer token 不用于后台。管理员空闲 30 分钟退出，进程重启后重新登录。多实例后台需要粘性会话；普通账户认证和在线状态不需要。

完整请求/响应见 [OpenAPI](contracts/openapi-auth-v1.yaml)。不提供 refresh 或普通账户自行注册接口。

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
