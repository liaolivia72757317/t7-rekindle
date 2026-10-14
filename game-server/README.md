# T7 Rekindle 游戏服务

独立 Rust 中心服务工程，当前提供最多 20 人、每队最多 10 人的自定义房间，房间状态流程允许单人开局。已有健康检查、WebSocket 开发身份、频道、分页列表、共享房间编号、建房、加入、阵营、准备、加载确认、统一倒计时、断线清理和房主移交。

产品入口为游戏客户端大厅。房主可以单人开始，其他玩家加入后需准备，再由房主开始；自动匹配暂不实现。原生网关实现大厅操作，以及当前游戏连接的 Start、场景视图、进图、真实加载确认和阶段转发。游戏大厅建房、双人进图、位置同步、近战攻防、命中扣血及战斗音效已实测。跳跃平滑与连击调整仍需游戏界面复验。浏览器页面用于协议开发验证。正式账号鉴权、服务端地图碰撞、最终结算和对局恢复尚未接入。整体方向见[中心服务架构](../docs/planning/rust-game-server.md)。

## 职责边界

`game-server/` 是中心服务：拥有房间、身份、阶段、动作期限、命中、格挡和血量；最终结算待实现。`src/Business/` 的联机模块是客户端协议适配器，负责消息转换、冻结角色映射、原生帧采样和远端显示插值；不运行本地电脑玩家或伤害裁决。C++ Runtime 提供客户端适配、采样和连接生命周期，启动器只提供启动与配置。原有 Python 离线战斗继续用于本地模式，两套裁决不同时运行。

## 构建与运行

使用 Rust 1.99.0；Windows 需要 MSVC 工具链，Linux 需要 C 编译器和链接器。工具链由 `rust-toolchain.toml` 固定，依赖版本由 `Cargo.lock` 锁定。

在仓库根目录执行：

```powershell
rustup toolchain install 1.99.0 --profile minimal --component rustfmt --component clippy --no-self-update
cargo +1.99.0 build --manifest-path game-server/Cargo.toml --locked
cargo +1.99.0 run --manifest-path game-server/Cargo.toml --locked
```

进入 `game-server/` 后 Cargo 自动选择固定工具链；在仓库根目录使用 `--manifest-path` 时显式指定 `+1.99.0`。

默认监听 `127.0.0.1:8090`，`GET /health` 和 `/ready` 提供进程就绪状态，`productionAdmission` 始终为 `false`。默认拒绝 WebSocket 接入；就绪不代表正式账号鉴权或游戏已可用。

启用本机协议实验：

```powershell
cargo +1.99.0 run --manifest-path game-server/Cargo.toml --locked -- --dev-lobby
```

用 `--listen 127.0.0.1:8091` 更换端口。开发模式拒绝所有非回环监听地址，`--dev-lobby` 的身份是自报测试标签，不是平台账号鉴权。按 Ctrl+C 关闭连接和服务。服务进程重启不会恢复房间。

公网直连使用游客票据与 TLS，部署见 [Linux 部署说明](deploy/README.md)。

大厅地图由服务端目录配置决定。默认接受洛阳死斗的场景编号 1003、1028；使用 `--lobby-maps lobby-maps.json` 加载所用客户端的地图编号集合：

```json
{"mapIds":[1028,7001,7002]}
```

7001、7002 是示例编号，实际配置应使用大厅创建请求中的地图与模式组合编号，不能使用底层地图资源编号。目录最多 4096 项、32 KiB，编号必须唯一且在 1–2147483647 之间。创建、列表、查询、加入及通知保持原编号。运行中场景适配当前支持 1003、1028，保留所选编号；其他地图开始时返回失败并保留房间。不同客户端版本或模式的显示名与编号可能不同，不能只按示例名称判断。

### 公网游客接入

启动器「设置 → 联机服务器」填写 IP 或域名与端口，启用「连接游戏服务器」，然后正常启动游戏；建房和加入仍在游戏大厅操作。无需注册、群验证或 SSH 隧道，昵称、角色和装备沿用当前公共配置。关闭联机选项并重新启动恢复本地模式。

Rust 使用 `--guest-lobby --tls-cert CERT_PEM --tls-key KEY_PEM --listen 0.0.0.0:8091` 启动。服务端支持 TLS 1.2 与 TLS 1.3。证书必须受客户端操作系统信任，并覆盖所填 IP 或域名；客户端不跳过证书校验。服务端每分钟检查证书变更，验证新证书后热加载，已有连接继续运行。`--room-capacity N` 配置 1–20 人，默认 20；队伍容量随配置调整，角色槽位硬上限来自原客户端。限制和规则集中在 `src/limits.rs`。

启动器通过 HTTPS `POST /auth/guest` 提交 `{ "displayName": "Player" }`，服务端发放 120 秒、一次性的随机票据和本次连接身份。原生网关通过 WSS 的 `Authorization: Bearer TICKET` 接入，随后 Hello 的身份与昵称必须匹配票据。客户端只保存地址与端口偏好，票据用 Windows 当前用户保护；不会交给 Python 或写入游戏消息日志。原生连接与启动器 HTTP 都直接连接服务器。

同名玩家有独立的连接身份，不会共用角色或控制权；物品没有账号持久化。游客票据只约束连接归属，不验证昵称所有权，也不是正式账号鉴权，`productionAdmission` 保持 `false`。新身份在断开后清理，房间在服务重启后不恢复。此模式面向试玩，正式账号与存档后续再设计。

接入限流、票据容量、连接数和消息队列均有上限；过载或不确定的游戏请求结果会断开连接，不自动重放建房、加入或攻击。多人位置、动作、血量、计分和复活使用每个接收端的独立角色映射与位移缓冲，队友接触不造成伤害。当前有四客户端原生协议集成测试及 20 人角色映射测试，20 人实机性能尚未验证。

### 浏览器验证建房

启用 `--dev-lobby` 后打开 `http://127.0.0.1:8090/dev`：

1. 第一个标签页以 `player-a` 连接并创建房间。
2. 第二个标签页以 `player-b` 连接，刷新列表并加入同一房间。
3. 两人选择不同阵营，加入者准备后由房主开局；房主无需准备。
4. 两个标签页分别点击“模拟加载完成”，观察统一倒计时和 Running。
5. 一人退出或关闭标签页，另一人应回到 Waiting，准备状态清除；重新加入后可以再次开局。

加载按钮仅模拟客户端回报，不启动游戏。页面展示当前房间和本端角色映射，最近协议事件只保存在当前页内存中。调试页面和静态资源仅在开发接入模式开放；默认返回 404。浏览器 WebSocket 检查同源 Origin，无 Origin 的原生工具仍可使用开发协议。

单人验证只需一个标签页：创建后直接开局，再模拟自己的加载完成。服务端只等待本局实际成员，不生成占位玩家；加载超时仍取消本局。浏览器流程只验证 Rust 房间状态，不代替游戏界面验收。

### 游戏内自定义房间实验

先重新构建原生、托管项目并打包，运行 Rust `--dev-lobby`。在 `%LOCALAPPDATA%\T7-Rekindle\custom-room.ini` 创建以下配置：

```ini
[custom-room]
serverHost=127.0.0.1
serverPort=8090
secure=false
accountId=player-a
```

按正常启动流程打开游戏，在游戏大厅中刷新自定义房间列表、创建或加入房间。原生运行时以配置的开发身份连接 `127.0.0.1`，显示名来自启动时的玩家名称；启动器不提供房间管理入口。另一条开发连接必须使用不同 accountId。删除或重命名配置后，下一次启动恢复本地模式；原型场景测试也需停用此配置。

服务器分配 userId，并在登录与房间成员回复中使用同一编号；开发身份等级固定为 100，与现有本地测试登录一致，不代表正式账户数据。编号不属于战斗 RID，服务重启后不保证相同。服务器最多保留 4096 个开发身份。同账号的显示名在当前进程内固定，修改时需要重启开发服务。游戏名称与房间名使用 GBK，必须能完整表示且分别不超过 31、63 字节。

当前限定频道 1、最多 20 人、空密码、阵营 1/2；地图由上述 Rust 大厅目录校验。房主无需准备，单人场景只有自己；多人需其他成员准备且两队都有成员。运行时适配把已提交的服务器 userId 发布到游戏登录界面，并将原界面的人数门禁交给 Rust 规则校验。适配仅在 custom-room 模式启用，检查客户端指令和资源签名，不写客户端磁盘文件。准备和取消准备请求保持原意。

开始后，同一网关请求本端场景视图，将场景入口与实例地址交给当前游戏。只有实例完成加载、选角、参战后才回报 Loaded；Rust 广播 Countdown、Running 后实例才倒计时和解锁移动。超时、退出和断线取消场景并清理成员。网关不启动第二个客户端，也不建立第二条同账号连接。

Running 阶段支持当前龙牙刀的四向攻击与格挡：按下和松开经网关提交 Rust，由服务器推进准备、保持、释放与收势并向本局成员广播。下一次攻击需完成当前出手，再至少收势 100ms；可以缓存按下/松开输入，格挡在出手结束后可立即接续。网关只转换动画消息，不处理离线伤害；位移通过最多 20Hz 的位置样本和接收端 100ms 插值缓冲同步；原生刀刃接触经同一网关交给 Rust 校验、去重和判定，向本局成员同步血量、受击、格挡反弹、死亡和复活；同时更新击杀、计分板及连杀播报。原版动画按消息接收时间播放，弱网下的跨端时间校准仍需验证。游戏内退出保留原版离场与退出确认消息，清理 Rust 成员后返回大厅；需实测退出后再次建房。

网关使用异步 WinHTTP WebSocket，每个原版逻辑连接拥有一条 Rust 连接；不借用启动器的房间连接。FIFO 顺序发送，最多 8 个请求同时等待回复，每 1 秒测量往返延迟，单个已发送请求 8 秒超时。失败、断开、非法回复或结果不确定时关闭原版逻辑连接，并由 Rust 清理成员；不会自动重发建房或加入。业务转换不直接联网，请求与回复确认仅在 Python 状态和输出成功提交后发布。

Windows 原生集成验收（先构建 native-tests 并运行独立、空房间的 Rust 开发服务）：

```powershell
$env:T7_GATEWAY_TEST_PORT = '8090'
py -3.14 -m pytest tests/python/test_native_room_gateway.py -q
Remove-Item Env:T7_GATEWAY_TEST_PORT
```

测试使用隔离的原生测试进程，通过游戏格式的真实 TCP 请求操作同一 Rust 服务，验证登录编号、频道、跨端房间操作、真实加载消息、统一开局、攻防动作、实际接触上传、命中扣血、格挡音效消息和退出确认。它不启动原版游戏，不代替实际界面验收。

## 开发协议

连接 `ws://127.0.0.1:8090/ws`，首先发送 Hello：

```json
{"protocolVersion":1,"requestId":"1","command":{"type":"hello","payload":{"accountId":"player-a"}}}
```

创建房间：

```json
{"protocolVersion":1,"requestId":"2","command":{"type":"createRoom","payload":{"name":"测试房间","mapId":1028}}}
```

回复包含同一 `requestId` 和 `event`；创建成功时 `event.type` 为 `room`，`event.payload.room.roomId` 是服务端分配的字符串 ID。第二条连接先用 `player-b` Hello，再将以下 `ROOM_ID` 替换为该 ID：

```json
{"protocolVersion":1,"requestId":"2","command":{"type":"joinRoom","payload":{"roomId":"ROOM_ID"}}}
```

| 命令 type | payload | 约束 |
| --- | --- | --- |
| `gatewayHello` | `accountId`、`displayName` | 原生开发网关身份；显示名固定到服务进程结束，沿用 Hello 的账号接入约束 |
| `hello` | `accountId` | 1–36 个 ASCII 字母、数字、`-` 或 `_`；同标签只能有一条连接 |
| `ping` | 省略 | 返回进程启动后的 `serverTimeMs` |
| `listRooms` | 省略 | 返回房间快照列表 |
| `listChannels` | 省略 | 当前只提供频道 1 |
| `listRoomPage` | `channelId`、`start`、`limit` | 频道 1，偏移从 0 开始，每页 1–20 条，按创建顺序返回摘要 |
| `getRoomListing` | `channelId`、`roomNumber` | 查询房间摘要，已销毁或不存在时拒绝 |
| `createCustomRoom` | `name`、`mapId`、`channelId`、可选 `password` | 创建 Waiting 房间，密码仅支持空字符串 |
| `joinCustomRoom` | `roomGuid`、可选 `password` | 按服务端共享编号加入；密码仅支持空字符串 |
| `createRoom` | `name`、`mapId` | 地图需在服务端大厅目录中；房名 1–32 字符，无控制字符 |
| `joinRoom` | `roomId` | 仅 Waiting 房间，容量由部署规则决定，一个连接身份最多一个房间 |
| `leaveRoom` | 省略 | 退出并清理归属，空房销毁 |
| `setCamp` | `camp` | 1 或 2；变更取消全房准备 |
| `ready` | `ready` | 布尔值，仅 Waiting |
| `start` | 省略 | 房主无需准备，可单人开局；有其他玩家时，其必须准备且与房主阵营相反；当前场景适配支持 1003、1028 |
| `loaded` | `matchId` | 当前 Loading 对局；本局全部实际成员确认后开始 3 秒倒计时 |
| `getClientView` | `matchId` | 读取本人的当前对局映射，仅 Loading、Countdown 或 Running；接收身份取自连接 |
| `actionInput` | `matchId`、`inputSeq`、`intent` | 仅 Running；inputSeq 为递增正 u32，角色从连接身份确定，当前只接受四向攻防和对应松键 |
| `poseInput` | `matchId`、`inputSeq`、`pose` | 仅 Running；独立递增正 u32；位置与移动意图通过连接身份绑定，按 50ms 合并广播 |

成员变化通过无 `requestId` 的 `roomChanged` 事件通知房内连接；`revision` 单调递增。成员或阵营变化取消准备。Loading 超过 60 秒取消开局；离开或断线会中止 Loading、Countdown 或 Running 并回到 Waiting。保留成员可重新加入、准备、开局，每局获得新的 matchId。重连恢复和胜负结算尚未实现。

### 自定义房间目录

所有创建和加入命令复用同一个目录与成员归属。`listRoomPage` 返回 `roomPage`，`payload.page` 包含 channelId、start、total 和 rooms；每条摘要含 roomId、roomGuid、roomNumber、channelId、name、mapId、ownerAccountId、ownerDisplayName、memberCount、capacity、phase、revision 和 joinable。创建和加入成功仍返回完整 `room`，成员变化沿用 `roomChanged`。`getRoomListing` 返回 `roomListing` 和 `payload.room` 摘要。

房间快照新增 roomGuid、roomNumber、channelId。roomId 仍为 UUID；roomGuid 是独立的正整数目录键，在 JSON 中用十进制字符串传输，不截断 UUID。roomNumber 是递增的 u32 显示编号，当前目录键使用同一分配序号。编号在房主移交及对局阶段切换时保持不变，空房销毁后失效，服务进程内不复用；分配溢出时拒绝建房。重启不保留编号归属，网关断开后必须清除缓存并重新查询。房间编号不是账号、角色编号或鉴权凭证。

例如第二条连接查询并加入第一页中的房间，将 `ROOM_GUID` 替换为返回的字符串：

```json
{"protocolVersion":1,"requestId":"2","command":{"type":"listRoomPage","payload":{"channelId":1,"start":0,"limit":20}}}
{"protocolVersion":1,"requestId":"3","command":{"type":"joinCustomRoom","payload":{"roomGuid":"ROOM_GUID"}}}
```

满员及已开局房间仍出现在列表中，joinable 为 false；加入时由房间规则再次校验。start 超出总数返回空页及实际 total；每次查询读取当时的目录，刷新应从第 0 条开始。未知频道、非法分页和非空密码明确报错，不预留成员或创建房间。

每连接 `requestId` 是递增正整数的十进制字符串，不允许前导零。最近 32 条请求缓存原回复；相同 ID 的不同内容报 `requestConflict`，已淘汰的旧 ID 报 `staleRequest`，不会再次执行。错误使用 `event.type=error` 与稳定的 `payload.code`，完整枚举见 [protocol.rs](src/protocol.rs)。

动作 intent 为 `attackUp/Right/Down/Left`、`blockUp/Right/Down/Left`（以完整驼峰名称传输）、`attackRelease`、`blockRelease`。`actionAccepted` 只确认对应 matchId 与 inputSeq；实际阶段由无 requestId 的 `actionChanged` 推送，其中 action 包含 roomId、matchId、actorId、eventSeq、actionSeq、state、serverTimeMs。eventSeq 在本局递增，actionSeq 在每角色范围 1–65535 循环。重复输入拒绝为 staleInput，重放已缓存请求只返回确认，不重复广播动作。

### 接触、血量与复活

`contactInput` 携带 matchId、递增 inputSeq 和 contact。contact 包含 actionSeq、state、elapsedMs、targetActorId（0 表示地形）、position、normal、direction、material 和可选的 weaponContact。攻击者从已识别连接推导，不接受客户端提交攻击者或伤害值。`contactAccepted` 返回 matchId、inputSeq、accepted；accepted=false 表示未通过接触校验，不产生伤害，也不会断开连接。

服务端校验本局 Running、角色存活、动作历史与出手窗口、接触向量、双方最近一秒内的位置、距离与朝向。同一出手对同一目标最多结算一次；动作结束后最多接受 600ms 的报告延迟。原生适配额外检测双方真实武器线段的相交：连续样本间隔不超过 100ms，最多细分 32 次，接触容差 6cm。武器接触独立标记为 weaponContact，只能触发方向和面向匹配的格挡，不能直接授予人物伤害；被拒绝的武器接触不吞掉后续身体接触。方向匹配且面向攻击者的格挡产生反弹与短暂格挡反馈；击中地面仅播放接触效果，击中墙面反弹。当前龙牙刀每次有效命中造成 20 点伤害，初始血量 100。

`combatChanged.payload.update` 包含 roomId、matchId、eventSeq、serverTimeMs 和 effect。effect 为 contact（判定和完整血量/击杀/死亡/连杀快照）、respawnReady（死亡后 8 秒开放选择）或 respawned（恢复血量）。死亡后原版选角与参战按钮通过 `respawn` 请求服务器；服务器确认后网关重建对象并恢复操作。重新生成对象先于其待机动作，防止动作作用于旧对象。

Business 只把权威结果映射为原版消息：接触特效、专用格挡音效、血量、伤害飘字、受击和死亡状态、击杀播报、个人及阵营计分、连杀提示。计分显示按权威击杀数换算每次 100 分，不在网关计算命中或授予击杀。当前近战使用统一受击动作与基础伤害，尚未区分头部、躯干和腿部。

这是基于原生客户端接触证据的对打原型。Rust 尚不加载地图碰撞或刀刃几何，粗略校验不能证明每一次客户端报告确实碰撞；弱网中的格挡按服务器当前状态判断，尚无历史位姿回溯。

### 主线功能接入情况

| 功能 | 当前状态 |
| --- | --- |
| 客户端资源解析、移动动画、格挡辅助、音效与渲染修复 | 已同步主线实现，本地和联机共用原生适配 |
| 四向攻击、格挡、反弹、受击、血量、死亡和手动复活 | Rust 决定结果，网关向本局成员转换原版消息；需真实游戏对打验收 |
| 击杀、个人/阵营计分、首杀与连杀提示 | 使用 Rust 快照更新原版界面 |
| 主武器/拳头切换、冲刺攻击 | 主线本地实现已同步，Rust 联机路径尚未实现 |
| 击杀目标、限时胜负、最终结算与 5 秒结算展示 | 主线本地实现已同步，Rust 联机结算尚未实现 |
| 机器人策略、节奏与寻路 | 主线本地实现已同步；真人房间不启动机器人 |
| 远端跳跃 | 已增加竖直插值样本间隙的持续校正，空中状态和实际平滑度仍待验收 |
| 远端首页公告及后台发布管理 | 已同步主线，独立于游戏内左侧新闻页面 |

### 角色编号

账号标识、角色资源 ID、房间角色编号和客户端对象编号是不同概念。`actorId` 在房间内分配，不永久绑定账号；对局中的引用需要同时携带 matchId。RID 是原客户端运行时对象编号，现有本地玩家固定为 1。

`getClientView` 返回 `event.type=clientView`，`payload.view` 包含 roomId、matchId、mapId、revision、phase、加载期限、开始时间、localActorId 和 actors。每条 actor 包含 actorId、localRid、camp 和 isLocal；单人只有自己（RID 1），双人为不同阵营，各端自己为 RID 1、对方为 RID 2，camp 保持全局阵营值。游戏大厅网关支持单人场景；启动器场景原型仍要求双人。

例如 A 的房间角色编号为 1，B 为 2：A 端映射 `actorId 1→RID 1、actorId 2→RID 2`，B 端映射 `actorId 2→RID 1、actorId 1→RID 2`。B 离开后重新加入会获得新的房间角色编号，其本端仍使用 RID 1。读取接口不接受自报 accountId、actorId 或 RID，不改变共享房间状态。

[client_view.rs](src/client_view.rs) 用不同 Rust 类型表示 ActorId 和 LocalRid，并提供双向转换及对局检查。视图是不可变映射快照，网关在对局取消或变化时必须丢弃它；动作与位置输入都独立复核身份、阶段和当前对局。原生大厅网关在运行中的客户端冻结该映射。

### 位置同步

`poseInput.payload.pose` 包含 `position`（三个 f32 坐标）、`heading`（-180–180 度整数）、`forwardBack` 和 `leftRight`（-1、0、1）、`running`（布尔值，只有向前移动允许）。返回 `poseAccepted` 与原 matchId/inputSeq。该输入序列与攻击序列相互独立。

Rust 按实际成员推导角色编号，检查有限坐标、坐标绝对值小于 100000、移动参数和位移预算。位移上限使用接收间隔、40 米/秒及 2 米短时余量；不接受客户端时间。每角色只保留最新待广播位置，每 50ms 广播 `poseChanged.payload.update`，包含 roomId、matchId、actorId、全局 eventSeq、采样 inputSeq、serverTimeMs 和 pose。合并允许采样序号跳号，事件序号必须连续；请求重放只返回原确认。

网关最多一个位置请求在途，收到自己位置的广播不校正本地运动。对方通过 MOVE selector 4 更新位置与朝向，移动意图变化通过 selector 38 更新。退出清除本局全部样本。该阶段依赖原客户端物理和首个位置报告，尚无服务端出生校验、地图碰撞或穿墙检测，速度检查不能替代完整移动权威。

上行 JSON 限制 4096 字节；最多 128 条连接，每连接每秒 64 个入站帧。10 秒未完成 Hello、30 秒无入站帧、输入拥塞或慢连接输出队列满时断开并清理房间。房间目录最多 64 个房间，命令与输出队列均有界。

## Rust 状态设计

`Room<S>` 组合共享房间数据和阶段数据；`Room<Waiting>` 才能加入、准备和开局。`start(self)` 消费旧房间并返回 `Room<Loading>`，失败返回原房间及错误；加载完成转为 `Room<Countdown>`，到时转为 `Room<Running>`。

目录中的 `RoomState` 持有各阶段的具体类型，协议 `Phase` 仅用于快照。Running 组合独立的 battle、motion 和 combat 状态，动作使用准备、保持、释放与收势枚举，位置保留每角色最新样本；退出丢弃本局动作、位置和待发事件。状态不使用继承、共享可变阶段标志或可任意调用的阶段 setter。网络协调任务独占目录，通过有界消息处理请求，房间规则不访问 socket。当前轻量原型共用一个协调任务和 50 ms 定时器；每 tick 每角色最多推进一阶段，延迟后不突发完整动作。几何与持续战斗负载接入前再拆分独立房间任务。

## 验证

```powershell
cargo +1.99.0 fmt --manifest-path game-server/Cargo.toml --check
cargo +1.99.0 clippy --manifest-path game-server/Cargo.toml --locked --all-targets -- -D warnings
cargo +1.99.0 test --manifest-path game-server/Cargo.toml --locked
```

覆盖房间权限和不变量、失败转换保留状态、准备取消、旧对局拒绝、加载超时、断线销毁、重复请求、默认拒绝接入、浏览器同源限制和真实 WebSocket 连接的共享流程；角色映射测试覆盖双端视角、阵营保留和重新加入后的新编号。文档编译失败测试证明 Loading 房间不能调用准备操作。这些测试不启动原版游戏，不代替双客户端实机验收。
