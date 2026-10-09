# 服务端第一阶段

## 落地范围

新增独立 `server/`：普通账户认证、唯一在线连接、Jar 内嵌管理后台。
启动器继续使用现有 WPF、NativeBridge、CPython 本地闭环；不修改 Native ABI、本地协议、Windows 构建与发布流程。
角色、资产、房间、Agent 和战斗功能不在本阶段。

## 确定规则

| 对象 | 规则 |
| --- | --- |
| 普通 session | 同账户可持有多个；创建起固定 30 天，无刷新令牌 |
| 在线归属 | 每账户仅一个逻辑连接；显式上线才接管，登录本身不抢占 |
| HTTP 心跳 | 15 秒一次，45 秒租约；不延长 session 有效期 |
| 被接管的 session | 仍可鉴权，但旧连接心跳失败；不自动抢回 |
| 管理员 | 独立 HttpSession，空闲 30 分钟失效，不记住登录 |
| admin 初始化 | 首次启动原子创建、控制台输出随机密码一次；首次登录强制改密 |
| 普通账户密码 | 后台创建/重置时随机生成；成功页展示一次 |

## 实现约束

- Java 25 / Spring Boot / MyBatis / MySQL 8.4 / Redis 8。
- Thymeleaf、CSS、JS 嵌入 Jar，不使用独立前端构建或 CDN。
- V001 创建 `account`、`auth_session`、`account_presence`；V002 创建 `admin_account`、`audit_log`。
- 随机 opaque token 的哈希入库，账号状态和 `auth_epoch` 在服务端复核；不使用 JWT 或 refresh 表。
- MySQL 行锁和每账户唯一在线记录保证多实例下单一归属；旧心跳及下线请求不修改新归属。
- 后台写操作与审计同事务；密码重置、禁用和撤销同时释放在线资格。
- 构建门禁：真实 MySQL/Redis 集成测试、100 请求并发接管、身份隔离、CSRF、初始化竞争、一次性密码、业务行覆盖率至少 90%、分支 100%。

## 交付入口

- [部署、迁移、恢复与测试](../../server/README.md)
- [普通账户 OpenAPI](../../server/contracts/openapi-auth-v1.yaml)
- [依赖许可清单](../../server/THIRD-PARTY.md)
- 独立服务端 CI；不自动提交、推送或部署。

后续启动器接入时再实现凭证持久化、显式上线和心跳；不复用游戏本地固定身份作为平台账户。
