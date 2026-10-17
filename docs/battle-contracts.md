# 战斗公共契约与资源数据包

本页记录已实现的公共帧、准入消息体、能力匹配和离线资源数据包。它们是共享战局的接入基础；产品默认且唯一使用 Rust 原客户端网关；Python 仅作开发对照，公共准入服务和共享战局接入仍待完成。

## 本机兼容网关与平台接入

`ClientGateway` 在调用进程中运行 Rust 网关，通过 `IPlatformBackend` 获取身份、读取配置和修改阵容／装备，不启动额外服务进程。当前实现 `LocalPlatformBackend`；调用者提供已按账号／游玩模式策略选定的名称、资源数据包和离线存档路径。网关不执行账号登录、不改变名称策略、不自动切换后端；本机建房、入房和匹配已接通，LAN 房间管理仍待迁移。

网关创建时先验证资源 JSON，并持有只读快照；启动时将伤害／防御规则和导航转为强类型数据，以各阵营首个出生点为锚点预热双方复活候选，成功后才绑定三个动态 `127.0.0.1` 端口。准备失败保留错误文本，不推进会话代次、不发布端口；停止后释放会话持有的预热结果。C# 将原生准备放在后台线程执行。

原协议 AUTH／method-3 编解码、版本应答、登录与同步、心跳、时间同步、空自定义数据、空总督查询及不支持重连的响应已经接通。启动延迟和响应内容沿用现有业务；`tests/fixtures/protocol/gateway-v1.txt` 由 Python、Rust 和 C# 原生链路共用。

大厅英雄目录、战斗英雄容器、阵容及装备修改通过原生消息接通 C# 存档。Rust 负责原资源引用、组 ID 和原协议编码；C# 负责配置校验及持久化。`tests/fixtures/protocol/loadout-v1.txt` 的 19 组请求覆盖读取、修改、空配置、拒绝、保存失败与重试，逐字节检查响应及发布顺序。

原客户端的固定用户 ID `10000`、MID `1` 仅在兼容编码中使用，不当作平台账号或战局资格。`instance` 仅在已预置战局时接受连接，并将已绑定玩家命令送入 Rust 裁决；无战局仍拒绝建立战斗连接。三个通道的未适配业务指令均记录后忽略，不回复、不改变战局、不关闭连接。Rust 本机房间与结算回房已有协议环回测试；隔离开发路径已完成连续两局自然结算、回房重开及正常停机的[单机功能验收](planning/rust-runtime-migration.md#单机功能闭环验收记录)，完整性能关卡与默认生产切换仍待完成。

首次读取局内英雄容器或初次选将时，冻结本局阵容与装备；后续容器请求及复活选将使用同一快照，不重新读取大厅配置。首次选将经私有有界入口交由尚未启动比赛计时的 `BattleWorker` 检查比赛代次及来源，按所选槽位原子更新武将、装备、动作、伤害配置与满血状态；成功后才发 `CHOOSE_HERO_RSP(result=0)` 及选将消息。首次入场 VISION、武器、护盾及刷新均使用该选择，开局首个权威状态包含准备阶段最后一次成功选择，不发布中间选择状态。死亡后选将仍须处于 `Selecting` 状态，保留阵营、角色 ID、死亡状态及计分；权威状态发布失败则整批回滚，成功后才发选将成功消息。复活 VISION、武器和护盾按同批权威武将与冻结装备投影，不使用排队期间较新的选择覆盖旧批次。不在阵容内或尚未进入选将阶段的请求返回 `result=1` 并保留连接；报文长度错误、非正数槽位仍是协议错误。

原生 `MOVE_FAST_RUN_REQ`（command `2`、selector `63`）正文为一个开始／停止字节，值为 `0` 或 `1`。兼容层校验后沿用原运行时的不回包行为，不将其当成战斗冲刺意图或另行模拟移动。开发采样旁的 `.gateway.log` 记录选将槽位、应答结果及连接结束原因；网关错误快照同时保留本次启动后的首个连接／工作线程失败，避免后续输出断开掩盖原因，重新启动网关时清空。

### Python 指令适配对照

以 `Business/scripts/app.py` 及其 `scene`、`loadouts`、`controls`、`weapons`、`battle`、`titles` 分发为范围。下表数字均为十进制；“已接入”表示请求已到对应处理器，不等同于实机验收完成。

| 通道／功能 | command / selector | Rust 接入与边界 |
| --- | --- | --- |
| 通用心跳、时间同步 | `29/1`、`8/9` | 已接入；限频时间请求不回包，比赛与客户端时钟通过绑定偏移转换 |
| 大厅自定义数据、总督查询 | `1/104`、`66/48` | 已接入原空响应 |
| 大厅物品／英雄容器 | `40/1` | 已接入配置及空容器响应 |
| 大厅阵容查询、修改、选择及装备修改 | `34/3,5,8,14` | 已接入 C# 平台事务；校验／保存失败返回原结果码 |
| 建房、入房、重连 | `30/1,3,10` | 已接入；重连返回原失败响应，不伪造恢复 |
| 匹配 | `32/1` | 已接入预置战局与延迟通知；不另起隐式战局 |
| 退出战局、退出用户组 | `44/2`、`55/1` | instance 退出及 logic／instance 用户组应答已接入 |
| 战局刷新、选将界面切换 | `10/106,11` | 补发当前 GAME 起点、时长及阵营绝对比分，不重置倒计时；入场后仅权威 `Selecting` 阶段允许切换选将界面 |
| 阵营选择、确认并导入角色 | `35/1,3` | 已接入；阵营取绑定成员，不接受客户端改变归属 |
| 可见对象列表、对象查询 | `14/5,7` | 按 RID 恢复最近已发布采样位姿、权威武将／装备／血量；不重建本地死亡、选将及等待复活角色；电脑补发原动作时间线及死亡通知，不另行模拟移动 |
| 局内英雄容器 | `41/101` | 已接入本局冻结阵容；其他容器按原空响应 |
| 载入完成、选将、入场／复活 | `54/20,50,100` | 已接入；初次入场限预置武将，复活支持冻结阵容内换将 |
| 原生朝向／按键／快跑上报 | `2/3,52,63` | 已校验并保持无回包；位姿以 NativeBridge 采样为准，不重复模拟移动 |
| 攻击、防御、盾防、冲刺及释放 | `4/1` | 已接入已知 intent；未知 intent 记录后忽略 |
| 换武器／收刀与拔刀 | `5/1` | 已接入 `ChangeSlot`；权威结果→装备更新→动作动画，忙碌及无效槽位返回 `620000/620001`，死亡或未开局时无效果 |
| 账号称号查询与设置 | `31/1,3,5` | 尚未接入账号称号服务；记录后忽略，不伪造拥有权或设置成功 |
| 其他主命令／子命令 | 任意 | 沿用 Python 的未处理策略：记录后忽略，不推测响应 |

换武器使用现有私有 `SourceAction`、连接绑定的来源和角色及共享玩家批次游标，不改变公共 `PlayerIntent v1`。槽位按原有有符号 `i16` 解码，由核心判定有效性；成功切换的装备消息使用同批权威状态、RID 映射及战斗服时间，动画使用客户端比赛时钟偏移。游戏尚未激活时，攻击和换武器请求无效果，不因暂停时钟关闭连接。

主动刷新缓存与客户端输出共用有界批次队列，消息发送后才应用对应状态及采样上下文；包括无可见报文的权威状态更新，队满时保留完整待发事务供重试。缓存随 instance 连接及比赛代次销毁，不跨局复用。复活位置优先采用权威出生点，拒绝用上一次生命的采样覆盖；电脑动作补发沿用已发送的序号、变化时间、时长及目标，不重播伤害或重新计分。移动激活时间通过已绑定的服务器／客户端时钟偏移转换，比赛时钟起点不取客户端自报时间。准备阶段不补发 GAME，结算后不重新开启场景刷新。

`instance-refresh-wire-v1.txt` 与 `instance-refresh-uplink-v1.txt` 冻结 Python 原编解码黄金样本；Rust 定向测试覆盖血量／装备／位姿恢复、死亡过滤、五种生命阶段的选将界面限制、准备与 GAME 刷新、旧生命采样隔离和发布失败重试。

未适配日志标记为 `phase=unhandled-ignored`，只含通道、主命令、子命令和长度，不记录正文。与 Python 一致，每个连接按主／子命令去重，最多记录 64 种；开发模式写入显式采样路径旁的 `.gateway.log`，其他宿主写入标准错误流。未知指令不增加连接错误计数。缺少子命令、已支持报文长度／字段损坏、重复或过期 instance 帧、未绑定身份、队列背压、真实 I/O 失败仍按原错误路径处理，不使用全局捕获掩盖。

同一开发日志的 `match-bound`、`match-game-started`、`logout-sent` 记录比赛代次及服务器时间；`settlement-start-sent`／`settlement-finish-sent` 在实际发送成功后记录代次、结束原因、结果、成员数和起止时间。`quit` 与自然结束的 `timeout`／`kill-target` 分开，结合下一代比赛开始及连接记录核对自然结算、回房和再次开局，不把客户端退出请求本身当作自然结算证据。

`command-adaptation-uplink-v1.txt` 和 `weapon-change-wire-v1.txt` 由 Python 原编解码验证；Rust 定向测试覆盖三个通道的未知指令后继续心跳、暂停比赛输入、换武器应答及装备／动画顺序、RID／时钟转换和损坏报文诊断。表中未对齐项须继续补齐后再判断完整兼容性。

### 异步平台邮箱

Rust 向 C# 投递 UTF-8 JSON 请求；请求最大 4096 字节，响应最大 1 MiB，`version=1`。标识及配置 revision 使用精确的 u64，不经浮点数转换。

```json
{"version":1,"epoch":1,"requestId":1,"operation":"readIdentity"}
```

```json
{"version":1,"epoch":1,"requestId":1,"success":true,"identity":{"displayName":"测试玩家","portrait":7,"level":100}}
```

身份失败响应保留版本、代次和请求标识，设置 `success=false`，省略 `identity`，关闭对应原协议连接；C# `LastBackendError` 和原生错误快照保留诊断。名称遵守既有 GBK、控制字符及 31 字节限制，头像为非负 i32，等级为正 i32。

配置操作沿用同一请求封装：

| operation | 附加请求字段 | 成功响应字段 |
| --- | --- | --- |
| readProfile | 无 | profile |
| setFormation | formation：有序英雄资源 ID 数组 | previous、profile |
| setWeapons | heroId、slots；每项为 slot、group、index | previous、profile |

`profile` 与 `previous` 使用下节的存档结构，均为不可变快照。`group`、`index` 是资源数组的零基索引，不是原协议组 ID；Rust 在边界转换。修改成功后，网关依次发送原协议结果、装备通知（仅装备修改）、受影响英雄更新、战斗英雄容器、阵容。

修改失败返回 `success=false` 和 `error`：`invalidProfile` 映射为原协议 `630001`，其余后端失败映射为 `630000`；不发送配置更新。读取失败、超时或无效后端快照关闭连接，不伪造空配置。停止或断开后丢弃响应，不撤销已经完成的持久化提交；后续会话重新读取存档。

- 一个句柄每次启动必须使用更大的非零 `epoch`；停止、断开或超时后清理请求，重复及旧代次响应返回 `STALE`，不更新新会话。
- 网关最多 32 条原协议连接和 32 个待完成平台请求；login／logic 每连接接收队列最多 32 条／1 MiB，发送队列最多 16 帧／4 MiB。队列暂满时暂停相应生产者，单次入队最多等待 5 秒，以 TCP 背压吸收正常突发；等待超时、队列关闭或单条超限时明确结束连接，不丢弃输入后继续假报成功。战局输入和完整输出批次沿用各自的有界拒绝／重试规则。
- C# 最多并发处理 32 个平台调用；单连接消息仍由 Rust 按序处理，本地存档更新另行串行提交，慢请求不阻塞其他连接的异步调用。
- AUTH、写入和平台等待上限均为 5 秒；C# 平台调用使用 4 秒取消期限，后端实现须响应取消。存档进入原子写入前检查取消，已提交的写入返回真实结果。心跳不提升优先级绕过已有请求和发送顺序。
- 原协议下行的 `SHPKG_CS.server_time` 固定为 `0`，与 NativeBridge `Server::commit` 一致；正文中的原生实例、动作和结算时间仍按各自语义编码，与外层时间独立。`gateway-downlink-v1.txt` 为 Python／Rust 共用的完整封包黄金样本。
- 断开会取消所属平台等待；停止先取消托管请求，再同步等待 Rust socket／任务退出，随后结束托管泵，最后释放库。

### 离线配置存档

`LocalPlatformBackend` 保留现有 `schemaVersion=1` JSON 格式，上限 1 MiB；不写账号在线配置、启动器设置或客户端资源。读取缺失的存档使用原默认阵容和默认装备，不立即落盘。

| 字段 | 约束 |
| --- | --- |
| schemaVersion | 整数 1 |
| revision | 0 至 u64 最大值减 1；真实修改递增，重复保存不递增、不写盘 |
| formation | 最多 4 个不同的已知英雄 ID，保留顺序；大厅允许为空 |
| weapons | 必须覆盖当前目录全部英雄，键为规范十进制 ID；每个英雄最多 4 个选择 |
| slot、group、index | slot 为 1–4，group 为 0–7，index 为 0–5；格子和武器不重复，最多一面盾，禁止选择未开放条目 |

读取依次尝试主文件和 `.bak`，通过结构及目录校验后才接纳；使用备份时通过 `ProfileWarnings` 报告，读取过程保留原文件。主文件与备份均无效时返回错误，不覆盖为默认配置。

保存先验证完整候选快照，再将上次有效配置写入备份，最后原子替换主文件。临时文件与目标同目录，写入后 flush；主文件替换成功后才发布内存快照和协议成功响应。写入失败保持原内存配置与 revision，后续重试只提交一次。存档路径由调用者明确指定，离线与在线后端不自动同步配置。

### 进程内 C ABI

私有状态 `T7RGW_BUSY=8` 表示本次请求尚未提交，可保留原 payload、批次号和时间戳重试；不改变已有值或结构布局，Rust、NativeBridge、C# 仍须整包配套升级。NativeBridge 对同次采样的世界、电脑位姿及接触提交共享最多 5 秒重试预算，已接受部分不重放；最终接触水位单独限时。C# 输出泵遇到 BUSY 保留 Rust 中的完整待发批次，连续 5 秒未成功才报错，成功或 EMPTY 清除计时。未知状态、队列关闭、非法证据和执行超时不当作 BUSY；执行超时可能已提交，禁止自动重放。

Rust 定时器／AI tick 的输出遇到暂满时回滚事务，固定原事件时间重试，成功前不推进截止点或处理后续输入。等待期间及时以 WouldBlock 退回尚未提交的请求，释放 FFI 网关锁供输出泵排空；重试最多 5 秒并响应停止。超时仍结束战局且保留明确原因，不把丢弃输出当作成功。

`src/BattleRust/ffi/include/t7r_runtime.h` 定义新增私有 `t7r_gateway_*` 接口：资源验证与创建、启动、停止、释放、快照、读取请求、完成请求及读取错误。`t7r_gateway_create_local` 另接收 `clientDirectory`、`cacheDirectory`、`rulesVersion`、`resourceVersion`、`clientVersion`，返回句柄及缓存命中标志；创建过程只读原客户端目录，缓存目录必须在其安装目录外。新增 `t7r_gateway_battle_start`／`submit`／`stop`，在同一网关句柄内持有单场 Rust 战局，借用固定布局配置与 kind 6／11 语义消息，停止时同步回收战局线程；再次开局须增加比赛 epoch。私有 `submit_motion`／`submit_contacts` 使用固定布局的原生证据，要求已绑定来源、RID 和最近一次世界批次；接触丢失、重复批次及旧比赛均明确拒绝。生产 NativeBridge 已通过这些私有入口提交采样；它们不是新增公共 BattleFrame 消息。NativeBridge 与 Rust FFI 的通用接口保持 ABI 1；私有 `T7RBattleConfig` 使用配置 ABI 2，包含 60 个成员槽，大小为 3176 字节，`memberCount` 决定实际名单长度。旧配置 ABI 1 明确拒绝，C#、NativeBridge 和 Rust FFI 须配套构建；旧 C++／Python `t7r_battle_host_*` 接口已删除。

句柄是进程内 u64 标识，不是可解引用的地址；已释放句柄返回错误。56 字节快照使用固定宽度字段，包含会话代次、运行状态、三端口、连接与请求计数、错误计数及资源版本。输入只在调用期间借用，不保留调用方指针；输出由调用方分配，长度不含终止符。小缓冲区探测不消费请求，不允许异常跨 C ABI。

| 状态码 | 含义 |
| --- | --- |
| 0 / OK | 成功 |
| 1 / INVALID_ARGUMENT | 参数、JSON 或结构布局无效 |
| 2 / INVALID_HANDLE | 句柄不存在或已释放 |
| 3 / INVALID_STATE | 重复启动或代次未递增 |
| 4 / BUFFER_TOO_SMALL | 返回所需长度，保留数据 |
| 5 / EMPTY | 当前没有平台请求 |
| 6 / STALE | 请求已失效或属于旧会话 |
| 7 / INTERNAL_ERROR | 宿主内部失败 |

`rust-gateway-tests` 把同一 Rust staticlib 链入测试专用 DLL，验证 C# → C ABI → Rust → 原协议 socket 全链路；该 DLL 不进入产品包，不依赖嵌入式 Python。

## 帧与准入

BattleProtocol v1 沿用 [36 字节帧头与长度限制](battle-server.md#battleprotocol-v1-帧基础)，所有整数使用大端序。C# 的 `BattleFrameCodec` 与 Rust 的 `battle_protocol::BattleFrame` 共用 `tests/fixtures/protocol/battle-v1.txt`；现有消息编号及原协议中继行为不变。

`Admit` 使用 `channel=0`，帧头中的 `roomId`、`matchId` 和 `playerId` 来自房间分配。消息体如下：

| 偏移 | 类型 | 内容 |
| --- | --- | --- |
| 0 | u16 | 业务契约版本，当前为 1 |
| 2 | u8 | 权威模式：1 为客户端采样，2 为服务端模拟 |
| 3 | u8 | 保留，必须为 0 |
| 4 | u32 | rulesVersion，非零 |
| 8 | u32 | resourceVersion，非零 |
| 12 | u32 | mapId，非零 |
| 16 | u16 | 凭据字节数，范围 1–256 |
| 18 | byte[] | 不透明准入凭据，长度须精确匹配，不允许尾随数据 |

帧版本与业务契约版本独立。未知版本、模式、保留位或非法长度均返回解析错误。凭据是字节序列，不按文本编码转换；示例语料中的凭据仅为合成测试数据。

C# 的 `BattleCapabilities.ClientSampled` 与 Rust 的 `Capabilities::client_sampled` 只声明客户端采样能力。权威模式、规则、资源、地图均须匹配；支持解析模式 2 不代表具备该能力，不自动降级。

编解码成功不代表认证成功。实际接入层仍须校验凭据、期限、身份归属、比赛绑定及序号，禁止从原游戏 AUTH 或客户端自报身份推断准入资格。当前 codec 不创建房间或比赛，也不签发、消费凭据。

### 采样与接触证据语义消息

保留帧 kind 1–10 的编号及行为，新增 kind 11 `PlayerIntent`、12 `MatchAborted`、13 `MotionAttempt`、14 `ContactBatch`；语义消息均使用 `channel=0`，不是原协议 `GameData`。`tests/fixtures/protocol/battle-semantic-v1.txt` 提供完整帧黄金样本，由 Rust 与 C# 编解码器共用。以下偏移相对于 payload；整数大端，`f64` 为 IEEE 754 binary64 大端位序，保留位必须为零，未知版本拒绝。

| 消息 | 长度与字段 |
| --- | --- |
| `WorldSample` / kind 6 | 40 字节头：`version:u16=1`、`reserved:u16=0`、`epoch:u64`、`sourceId:u64`、`batchId:u64`、`sampledAtMs:u64`、`count:u16`、`reserved:u16=0`；后接 `count` 个 56 字节角色，最多 60 个 |
| 样本角色 | `actorId:u32`、`clientRid:u16`、`sequence:u16`、`entity:u64`、`positionXYZ:f64×3`、`heading:f64`、`contactTargetActorId:u32`、`contactState:u8`、`reserved:u8×3=0` |
| `MatchState` / kind 7 | 40 字节头：`version:u16=1`、`mode:u8`、`reserved:u8=0`、`epoch:u64`、`revision:u64`、`baseRevision:u64`、`atMs:u64`、`count:u16`、`reserved:u16=0`；`mode=1` 后接 1–60 个 156 字节角色变更事件，`mode=2` 后接 1–60 个 152 字节角色快照 |
| 角色变更事件 | `eventType:u8=1`（`ActorStateChanged`）、`reserved:u8×3=0`、152 字节角色状态；一个 delta 批次内角色不重复 |
| 角色状态 | `actorId:u32`；`camp,kind,life,intent,phase,flags:u8`；`sequence,durationMs,reserved:u16`；`heroId,health:u32`；`guard:f64`；`kills,deaths,points,streak:u32`；`weaponId,offhandId:u32`；`actionChangedAtMs,lifeAtMs,respawnAtMs:u64`；`spawnPositionXYZ,spawnHeading:f64`；`mainWeaponId:u32`；`shieldCount:u8`、`reserved:u8×3=0`、4 组 `shieldWeaponId,shieldHealth:u32` |
| `PlayerIntent` / kind 11 | 共 44 字节：上述来源头的前 36 字节，后接 `actorId:u32`、`clientRid:u16`、`command:u8`、`flags:u8` |
| `MotionAttempt` / kind 13 | 共 100 字节：来源头 36 字节、`worldBatchId:u64`、`actorId:u32`、`clientRid:u16`、`reserved:u16=0`、`entity:u64`、`positionXYZ:f64×3`、`heading:f64`、`attemptedForwardAtMs:u64` |
| `ContactBatch` / kind 14 | 52 字节头：来源头 36 字节、`worldBatchId:u64`、`dropped:u32=0`、`count:u16`、`reserved:u16=0`；后接最多 8192 个 280 字节接触证据；零事件仍是有效的时间水位推进 |
| 接触证据 | `actorId:u32`、`targetId:u32`、`clientRid:u16`、`targetClientRid:u16`、`sourceEntity:u64`、`targetEntity:u64`、`sequence:u16`、`targetIntent:u8`、`targetPhase:u8`、`phaseAtMs:u64`、`targetPhaseAtMs:f64`、`occurredAtMs:f64`、`along:f64`、`defenceWeaponId:u32`、`region:u8`、`bodyIndex:i8`、`material:i8`、`flags:u8`、8 个 XYZ `f64×3` 向量、`sourceHeading:f64`、`targetHeading:f64` |
| `MatchAborted` / kind 12 | 共 20 字节：`version:u16=1`、`reason:u8`、`reserved:u8=0`、`epoch:u64`、`atMs:u64` |
| `MatchEnded` / kind 8 | 40 字节头：`version:u16=1`、`reason:u8`、`result:u8`、`epoch:u64`、`startedAtMs:u64`、`endedAtMs:u64`、`campOneKills:u32`、`campTwoKills:u32`、`count:u16`、`reserved:u16=0`；后接 1–60 个 28 字节成员 |
| 结算成员 | `actorId:u32`、`camp:u8`、`result:u8`、`reserved:u16=0`、`rank:u32`、`kills:u32`、`deaths:u32`、`points:u32`、`streak:u32` |

`epoch`、`sourceId`、`batchId` 均非零。角色 ID 范围 1–60；同批不得重复 `actorId` 或 `clientRid`；实体非零。坐标必须有限且绝对值小于 100000，朝向为有限的 -180° 至 180°。`contactState` 为 0 不可用、1 阻挡、2 通畅；状态 1/2 须带非零目标角色 ID。空世界样本是合法的来源心跳，不表示其他来源的角色已被清除。

时间字段以战斗服本局单调毫秒时钟为唯一裁决基准，不是 UTC，也不是客户端系统时间；接入层在投递前按每场、每来源绑定的一次性锚点把原生 `GetTickCount64` 运行时间换算为比赛时钟：`matchAt = matchAnchor + (nativeAt - nativeAnchor)`，负差值也须做有界减法。整数时间检查上下溢；接触的浮点毫秒保留小数部分。每场固定锚点，不随批次重置；`epoch` 由比赛配置分配，不直接采用原生 `instanceStartedAt`。本机可以选相同数值的锚点形成恒等换算，跨机来源必须各自校时。

Windows 本机通过 `QueryInterruptTimePrecise` 在读取战斗服时钟前后取样，采用后端点向上取整到毫秒作为原生锚点，跨度超过 50 ms 则拒绝绑定；不使用低精度 tick 的中点，以免把随后正常样本换算为未来时间。若首个样本换算后早于开局，等待下一新鲜样本，不伪造时间。该校时只适用于同机；跨机接入仍待实现按比赛代次、来源绑定的往返校时与漂移／误差边界处理，不共享绝对 uptime，不直接用收包时间代替接触发生时间，已发布水位不得因重校而回退。

原客户端兼容时钟与 Rust 比赛时钟分开：登录、大厅和 `instance` 的 `8/10` 校时应答均采用与实例更新一致的服务器 uptime；`instance` 从建立连接起每 2 秒发送原协议 `8/11`（`>HQQB`：11、服务器 uptime、0、1），覆盖准备与比赛阶段，停止该局即取消。原生动作 `phaseTick` 依赖这条客户端校时链路，缺失时可能保留客户端启动相对时间；不得把该数值冒充 uptime、截断负时间或静默丢弃接触。外层 `server_time` 仍为 0，比赛裁决仍只使用映射后的 Rust 单调时钟。兼容校时报文不替代跨机来源校时。

坐标沿用当前地图的三维世界单位，X/Y 为地面平面、Z 为高度；heading 为度。`batchId` 在各来源、各消息类型内独立递增，帧 `sequence` 则在连接方向内独立递增。来源和角色归属由接入层确定，消息自报值仅用于一致性检查。

### 接触观测 v2

仅 `ContactBatch` 的 payload 版本新增 2；帧版本、kind 14、52 字节批头和 v1 的 280 字节事件布局不变。v2 每条事件在偏移 280 追加 `targetObservedAtMs:u64`，事件长 288 字节；空批也显式携带版本。`battle-contacts-v2.txt` 是 C#／Rust／Python 共用的完整帧黄金样本。

- `occurredAtMs`：扫掠插值得到的碰撞发生时间，保持小数精度，决定水位覆盖与裁决顺序。
- `targetPhaseAtMs`：记录中的目标动作相位开始时间，不是观测时间。
- `targetObservedAtMs`：接入端完成读取冻结目标状态时的时间上界，不声称是精确的游戏帧采样时刻，也不是服务端收包或处理时刻。
- v2 要求 `occurredAtMs <= targetObservedAtMs`、`targetPhaseAtMs <= targetObservedAtMs`；来源相位仍须不晚于碰撞。观测上界可以晚于本批碰撞水位；战局接收时另查其不晚于权威比赛当前时间。来源、RID、代次、批次、容量与发布失败回滚规则不变。
- 目标相位若晚于碰撞，保留证据但不提前获得格挡、弹反或正在攻击的伤害修正；武器重叠不冒充身体命中。不修改碰撞时间、不钳位相位时间，也不以丢弃接触来通过校验。
- v1 解码保留观测字段缺省（C# `null`／Rust `None`），继续执行 `targetPhaseAtMs <= occurredAtMs`，不自动补造观测时间或升级版本。原 C ABI 接触结构及专用入口保持 v1；既有 `t7r_gateway_battle_submit` 的 kind 14 按 payload 版本解析，不改 C ABI 名称。

### 攻击者状态观测 v3

`ContactBatch` payload 版本 3 在 v2 事件偏移 288 追加 `sourceObservedAtMs:u64`，每事件 296 字节，批头仍为 52 字节；目标观测字段仍在偏移 280。v1／v2 的布局和严格校验保留，未知版本拒绝。`battle-contacts-v3.txt` 提供含相位开始前几何接触与空水位的跨语言黄金样本。

`sourceObservedAtMs` 是完成读取冻结攻击者状态的上界，与目标观测时间分别校验。v3 要求 `phaseAtMs <= sourceObservedAtMs`、`occurredAtMs <= sourceObservedAtMs`，两侧观测上界均不晚于战斗服当前比赛时钟；不要求来源相位先于几何碰撞。完整批次保留这类事件，由现有权威攻击窗口 `phaseAtMs + contactStartMs .. phaseAtMs + contactEndMs` 决定命中资格。窗口之前的几何重叠不产生伤害、格挡或命中去重标记，不中止整批，同批窗口内的有效接触仍正常裁决。不钳位、不改写碰撞或相位时间。

原生 `phaseAtMs` 与服务器动画发令时间是不同的事件，不要求逐毫秒相等。接触须绑定本局、来源、实体及攻击序号；窗口偏移和伤害规则由服务器保存的该次攻击决定，窗口起点取映射后的原生相位时间，仍检查生命代次与中断截止时间。死亡或出生确认期间的移动尝试继续校验来源和对应新鲜位姿，但不更新 AI 脱困状态，也不因此中止战局。

原客户端死亡动画使用最后一个存活动作之后的序号；战局复活时承接该序号，随后动作下发与原生接触返回使用同一序号空间，输出层不累加独立的死亡偏移。

Windows 读取冻结接触缓冲区后，用 `QueryInterruptTimePrecise / 10000` 同时记录来源与目标观测上界；它与原生 tick 同属包含休眠的单调 uptime，按既有固定锚点映射到 Rust 比赛时钟，而非使用客户端墙上时间。JSONL 增量增加 `sourceObservedAtUptimeMs`／`targetObservedAtUptimeMs`，`capturedAtUptimeMs` 也用精确 uptime。旧 JSONL 缺少某侧观测字段时，离线证据验证明确使用较晚的批次 `capturedAtUptimeMs` 作为该侧保守上界；这不修改原始记录，不向 v1／v2 消息补造字段或放宽其校验。跨机来源仍须建立自己的时钟映射，不能直接比较各客户端 uptime。

### 开局前目标阶段 v4

`ContactBatch` payload 版本 4 沿用 v3 的 52 字节批头和 296 字节事件布局，仅扩展 `targetPhaseAtMs:f64` 的取值范围：允许有限负值表示准备阶段已开始、开局后仍被观测到的目标动作阶段。此时间仍按固定锚点换算，并保留 `targetPhaseAtMs <= targetObservedAtMs`；不钳位为零，不重启目标阶段，也不改变格挡／弹反窗口的时长计算。原生缺省阶段时间 `0` 仍保留为 `0`。

来源 `phaseAtMs:u64`、碰撞 `occurredAtMs`、两侧观测时间和接触水位的原有校验不变；目标历史阶段不等于历史碰撞，也不免除本局、来源、实体、生命代次与攻击序号检查。v4 必须携带两侧观测上界。v1／v2／v3 与旧 C ABI 入口继续拒绝负目标阶段时间，不自动降级或丢弃事件；未知版本拒绝。`battle-contacts-v4.txt` 提供跨语言黄金样本。

NativeBridge 当前经 kind 14 提交 v4，C++、Rust 与 C# 须配套升级；旧消费端明确拒绝 v4。原生时间失败日志记录字段名、原始值、映射值、锚点及比赛／角色标识，以区分真实事件越界和阶段时间问题。JSONL 仍记录非负原生 uptime，录制格式及其离线校验不变。

`MotionAttempt.attemptedForwardAtMs=0` 明确表示本次新鲜电脑位姿对应的前移尝试为假，不表示漏采；非零时间不得晚于采样时间且距采样不超过 250 ms。`ContactBatch.stamp.atMs` 是本批覆盖至的接触时间水位；事件的 `occurredAtMs` 不得晚于水位。仅在原生 `contactBatchId` 连续增长、且 `contactsReady=true` 时生成新批。首个可用世界批次建立绑定后，`ready=false` 只停止世界位姿和电脑移动提交，不丢弃冻结缓冲区中的接触证据；这些批次仍按原始采样时间推进接触水位，并引用最近接受的世界批次。批次停滞或接触尚未就绪时不推进水位，真实跳号、倒退和接触丢失仍拒收。`worldBatchId` 指向同来源最近接受的世界批次，接入层须检查一致性。接触向量依次是源／目标位姿、源／目标／武器速度、接触点、单位法线和单位方向；`flags` 位 0／1／2 分别为速度可用、源角色空中、目标角色空中。`targetIntent` 为 0 无、1–4 方向攻击、5–8 方向防御、9 盾牌、10 冲刺；`targetPhase` 为 0 空闲、1 准备、2 持续、3 出招、4 收招、5 换武器、6 被格挡、7 地形、8 格挡成功、9 受击；`region` 为 0 无身体部位、1 头、2 躯干、3 腿。`targetId=0` 表示场景接触，此时目标 RID 和 `defenceWeaponId` 均为 0。所有事件还须经过角色归属和核心战局校验。

有目标角色时，`defenceWeaponId=0` 表示身体接触，必须有 `region=1..3`；`defenceWeaponId!=0` 表示武器／盾牌接触，允许 `region=0`，也保留既有 `region=1..3` 输入。此规则适用于 v1／v2／v3／v4、C# 编解码、Rust 语义转换及私有 C ABI。无身体部位的证据在裁决层保持 `Region::None`，不补造躯干部位或身体命中；涉及武器破防／盾牌伤害时不附加身体部位倍率。武器与身体接触继续按各自证据和先后顺序裁决。

kind 14 解码失败时，`t7r_gateway_error` 保留解码原因及可读取的 `batchId`；后续错误仍保留网关首个失败原因，避免清理错误覆盖接触拒收原因。非法批次仍整批拒收，不通过改写接触类型来接受。

Windows 原生接入会把一个采集批次中跨多个帧的接触先放入有界缓存，以**上次可用帧时间减去原生扫掠最大 100 ms**作为可证明完整的语义水位；时间戳相同的连续帧不推进水位。接入层可合并多个原生批次再发一个语义批次，语义 `batchId` 因而独立于原生 `contactBatchId`。停止或转场前必须显式冲刷已采集证据，冲刷失败则中止该局，不能把未投递的证据视作已结算。

`PlayerIntent.command` 1–4 为上／右／下／左攻击按下，5–8 为对应方向防御按下，9 盾牌按下，10 冲刺按下，11 松开攻击，12 松开防御。`flags` 位 0 为防御键按下、位 1 为跑动、位 2 为前移；松开命令的 flags 必须为 0。`MatchAborted.reason` 1 为采样来源断开、2 为输入队列溢出、3 为输出队列溢出、4 为内部失败；它是中止通知，不得伪装为正常 `MatchEnded` 结算。

`MatchEnded.reason` 1 为退出、2 为超时、3 为击杀目标；`result` 0 为退出、1／2 为获胜阵营、3 为平局。退出与结果 0 必须同时出现。成员分数按比赛内统一 `actorId` 排列，各阵营的名次从 1 开始，成员 result 沿用现有战局核心的 0 退出／1 胜／2 负／3 平语义。`endedAtMs` 不早于 `startedAtMs`；成员 ID 不重复。

`MatchState` 是权威**可观察状态**，不是客户端移动／碰撞或 Rust 内部摆动、buff 的复制。`mode=1` 的一帧是原子增量事务：每个 `ActorStateChanged` 完整替换该角色的可观察状态；`revision=baseRevision+1`。`mode=2` 是整个已配置角色名册的恢复快照，`baseRevision=0`；初次投递生成修订 1，此后活动战局在距上次快照至少 1000 ms 的下一次调度产生快照，即使无变化也可重复同一修订。客户端只对同一 `epoch` 且 `baseRevision` 等于当前修订的 delta 整批应用；缺口、迟到或重复 delta 不得部分应用，等待新快照恢复。快照可取代旧状态，但接入层须校验完整名册和已绑定 `actorId`。`MatchEnded` 仍由 kind 8 单独承载，结束批次中的状态更新先于结算通知发布；效果与原客户端报文由输出兼容层处理。

增量以公开字段值变化为准，角色 `Arc` 地址变化不产生业务修订。防御计量器独立存储；空接触批次仍推进水位、恢复计量器并清理已过期挥刀历史，但没有公开状态变化且未到检查点时，不复制全员角色、不发布状态或增加 revision。真实防御恢复仍发布状态；发布失败同时回滚计量器、历史、水位和修订。

角色 `kind` 1 人类、2 电脑；`life` 1 存活、2 死亡待复活、3 选将、4 等待、5 出生确认中；`intent` 0 无、1–4 攻击、5–8 防御、9 盾、10 冲刺；`phase` 0–9 与接触证据定义相同；`flags` 位 0 为动作已松开。`lifeAtMs` 在死亡／选将／等待时为死亡时间，出生确认中为出生时间，存活为 0；仅死亡态携带非零 `respawnAtMs`，仅出生确认中携带出生参考点和朝向。盾牌槽按武器 ID 升序，未用槽全零。角色状态不包含来源或本地 RID，输出接入层在每条连接上按已绑定映射转换。

这些消息只定义字节格式，不构成准入或可信采样。接入层仍须用已认证连接绑定 `roomId`／`matchId`／`playerId`、`sourceId` 与 `actorId`，映射每个客户端自己的 `clientRid`，校验严格递增的帧序号及来源批次、比赛代次和时钟。重复／迟到批次不得重放，旧比赛消息不得推进新比赛；发送背压应拒绝整批并中止或保留待重试状态，不能静默丢失接触。kind 13／14 目前只完成公共编解码；`MatchState` 已由战局所有者生成，但公共帧接入尚未完成，生产 NativeBridge／网关目前使用私有进程内入口。

Rust `semantic_adapter` 在产生 `BattleSession` 输入前检查调用方提供的比赛代次、来源、角色到客户端 RID 的绑定和上一个已接受批次，并保留原始来源标记供接入层提交游标。该纯转换器不从客户端消息推导绑定，也不拥有准入、帧序号或发送队列；私有战局入口已调用它提交 kind 6／11，并在成功后推进对应批次。单人战局的 `instance` 原协议连接现可从已绑定的战局取得来源和角色身份，将原客户端动作请求规范化为 kind 11 语义后提交同一工作线程；该连接已按 actorId→RID 映射发送已支持的原客户端战斗输出；来源断线中止与 LAN 接入仍需接线。结算转换器将已有 `Settlement` 逐字段编码为 kind 8 消息体，不新增结算规则。

## 资源数据包 v1

数据包是单个 UTF-8 JSON 文件，上限 64 MiB，不包含可执行代码、客户端程序或本机目录。格式版本变化通过 `schemaVersion` 区分；规则和资源版本由维护者分配，作为兼容标识，不作为真实性证明。

| 字段 | 内容 |
| --- | --- |
| schemaVersion | 当前为 1；未知顶层字段或格式版本报错 |
| rulesVersion、resourceVersion | 非零 u32，分别标识规则与资源集合 |
| clientVersion | 1–64 个 ASCII 字母、数字、下划线、点或连字符组成的兼容标识 |
| catalogue | `heroes` 与 `weapons`；包含角色、原资源 ID、8×6 武器组、默认选择和原协议组映射 |
| combatProfiles | 按英雄和武器 ID 索引的动作阶段、接触窗口、移动、格挡及冲刺参数 |
| damageProfiles | 按英雄 ID 索引的护甲、武器伤害属性和特征 |
| rules | 当前伤害、防御和伤害增益参数，不重新计算或调整规则 |
| botNames | 当前资源提供的候选 AI 名称 |
| map | 地图 ID、名称、初始位置、双方出生／复活点及导航拓扑 |

JSON 对象中的资源 ID 使用十进制字符串。向量为三项数组，位置沿用原坐标与长度单位，朝向为角度；导出保留现有数值精度。

导航数据保持原索引：`vertices` 为 `(x,y,z)`，`edges` 为 `(a,b,oppositeEdge,oppositeFace)`，`faces` 为 `(startEdgeIndex,numEdges,numUserEdges)`。边界边的两个邻接索引均为 `-1`；当前不接入用户边或跨块导航。

### 离线导出

从当前项目规则和受支持客户端的只读目录生成新文件，不覆盖既有输出：

```powershell
python scripts/export_battle_resources.py --client-directory 'TARGET\bin' --output 'BUNDLE.json' --rules-version 1 --resource-version 1 --client-version 'CLIENT_VERSION'
```

将 `TARGET` 替换为客户端所在的绝对目录。该工具先执行现有资源预检；战斗、伤害、地图和导航使用项目中已冻结的数据，英雄目录和 AI 名称使用所选客户端资源。原资源内的相对逻辑路径可以用于兼容映射，不写入来源机器的绝对路径。

上述 Python 入口保留为开发参照。Rust 原生导出使用同一数据包格式，在 `src/BattleRust` 目录执行：

```powershell
cargo run --locked -p t7r-battle-resources -- export --client-directory 'TARGET\bin' --output 'BUNDLE.json' --rules-version 1 --resource-version 1 --client-version 'CLIENT_VERSION'
```

`FrozenData::load_client` 只读所选目录的 VFS 英雄／武器表和 AI 名称，检查已知归档布局并拒绝覆盖层；战斗、伤害和地图来自项目冻结数据。不支持的英雄或武器直接报错，不回退为其他配置。输出使用新建语义，既有文件不覆盖。CLI 是开发验证入口，不是玩家启动时新增的服务进程；Linux 目标宿主只加载数据包，不读取玩家目录；导出结果不自动上传或加入发布包。

离线进程内调用可用 `ClientGateway.CreateLocalAsync(LocalResourceOptions, IPlatformBackend)` 自动准备资源。选定绝对原客户端目录、独立的绝对缓存目录和非零版本后，Rust 对冻结数据及实际所需客户端资源计算身份；命中时仍验证目录布局，内容改变或缓存损坏时重建。缓存文件以临时写入、文件锁与原子替换保护，不修改客户端安装内容。`WasResourceCacheHit` 只表示此次资源命中，不表示战局启动或战斗通道就绪。

原生内嵌的 `resources/data/battle-baseline-v1.json` 保存 36 名英雄、286 条英雄／武器绑定及冻结规则和地图。它由 `scripts/export_battle_baseline.py --output NEW_BASELINE.json` 从本仓库已有冻结源码生成，不读取客户端、不包含本机路径；更新时需核对差异和 Python 一致性测试。它是内部只读数据，不新增对外存档或缓存格式。

只读资源适配入口：

- `vfs::Archive`／`ClientResources` 校验尾目录、条目边界、分块 zlib 和逻辑路径；只保留读取句柄与索引，不解包到客户端目录。
- `catalogue::Catalogue` 和 `bot_names` 解析原 MSES／GBK 表，保留原支持武器、默认选择、空槽与锁定格行为。
- `spawn::read_placements` 解析原出生／复活 XML；`navmesh::read_navigation` 按现有固定 SHA-256 校验并解析 Havok v5。二者用于显式检查或导出，不替换运行时的冻结地图；也不将运行时加载扩展为必须读取原地图归档。

### Rust 预检

在 `src/BattleRust` 目录执行，将 `BUNDLE_PATH` 替换为数据包的绝对路径；这样沿用仓库锁定的工具链、目标与 `artifacts/rust` 输出配置：

```powershell
cargo run --locked -p t7r-battle-resources -- 'BUNDLE_PATH'
```

成功打印地图、规则和资源版本，失败返回非零退出码。库入口为 `ResourceBundle::load`、`from_reader` 和 `from_slice`；加载结果只提供只读访问，不创建战斗线程或修改配置。

预检覆盖大小、格式和版本、目录与配置引用一致性、动作时长及接触窗口、出生点和导航索引／双向邻接。该预检不执行伤害、寻路或物理模拟，完整业务行为仍由后续 Rust 核心及回放回归验证。

运行时 `BattleResources::prepare` 在上述结构预检后解析规则参数、构建导航并预热复活候选；规则未知字段、错误维度、非有限数值及非法区间均显式报错，不替换为默认值。JSON 启用精确浮点往返解析，距离及求和保持现有数值行为，避免微小舍入差异改变候选顺序。预热不假设实时占用，开战后 `SpawnCandidates::choose` 只按原轮转和角色间距检查已过滤的邻居位置，区分无候选与全部占用。

`core::damage`、`guard`、`guard_meter`、`action`、`ai`、`navigation` 不读取原协议、Windows 内存或 JSON；原始输入引用在兼容层转换。`BattleSession` 接收带 epoch 的世界、动作、原生移动尝试、接触、定时与出生请求，输出动作、电脑意图、伤害、防御值、击杀、生命状态、计分与结算等语义事件。`MotionAttempt` 只能引用当前新鲜世界样本中的相同角色、实体代次、位置和朝向，并明确标识原生端是否尝试前移；AI 不根据期望路线臆测卡路。客户端位置与接触是观测，不由 Rust 推算运动或碰撞；批次按接触时间结算，重复批次不重复伤害，输出发布失败不提交状态。该模块已通过本机 `instance` 兼容通道接入显式开发启动路径；真实客户端整局表现仍待验收。

`BattleWorker` 在独占线程持有一场 `BattleSession`，用有界输入与输出批次、100 ms AI 脉冲和独立动作／生命／比赛期限驱动它；`Timers` 仅执行到期规则，不额外触发 AI 决策。输出队列满时整批拒绝，状态不提交；停机等待线程退出。网关已能以私有接口启停它并提交语义位姿与玩家动作；原生位姿、接触水位和移动尝试已在本机开发路径接线，`instance` 已覆盖进场、动作、已定义战斗输出、结算与回房；完整实机验证和 LAN 来源断线处理仍待完成。

`Member.kind` 为 `Human` 或 `Bot`，`sample_source_id` 为非零采样来源；每名人类持有不同来源，电脑绑定到其中一名人类的来源。AI 名册、受击仇恨与复活规则依角色类型选择，不以比赛角色 ID 是否为 1 判断。

| 战局输入 | 数据与约束 |
| --- | --- |
| `SourceWorld` | `epoch`、`source_id`、递增 `batch_id`、`sampled_at_ms`、角色到 `Pose` 的映射；只接纳归该来源的角色，逐来源记录游标，逐角色记录新鲜度，再合并世界快照。未出现在新批次中的角色不会被刷新。 |
| `SourceAction` | `source_id`、人类 `actor_id`、动作命令；来源必须拥有该人类角色，仅接受 `Press`、`ReleaseAttack`、`ReleaseGuard` 和 `ChangeSlot`。定时、受击、取消等内部命令不作为玩家输入；兼容层仅从受支持的原客户端报文生成玩家命令。 |
| `PlayerAction` | 公共玩家意图及原协议动作／换武器入口保留完整 `SourceStamp` 到战局所有者。所有者验证比赛、来源、角色、递增批次及时间；输入时间须介于开局（且不早于当前生命创建）和处理时刻之间，同角色时间允许相同毫秒但不倒退。动作按处理时间执行，不回溯，不设最大迟到窗口；业务游标仅在处理和整批发布成功后提交。 |
| `SourceMotionAttempt` | `source_id`、电脑角色的 `MotionAttempt`；来源必须拥有该电脑，且实体、位置、朝向和采样时间必须与其新鲜位置一致。 |
| `SourceContacts` | `source_id`、`ContactBatch`；批内所有攻击者必须归该来源。每来源批次序号及 `through_at_ms` 递增，新事件时间须晚于该来源上一水位；无接触也要推进水位。单批最多 8192 条，待裁决缓存最多 16384 条；所有来源水位均覆盖的事件才按时间排序裁决，重复批次不会重复结算。 |
| `SourceSpawn` | `source_id`、人类 `actor_id`；仅为对应人类请求复活选点。 |

`World`、`Action`、`MotionAttempt`、`Contacts`、`RequestHumanSpawn` 与角色 1 的 `RequestPlayerSpawn` 保留为单来源战局入口；多来源战局拒绝这些无来源输入，接触批次也不能混用有来源与无来源形式。`Timers`、`Tick` 和 `Quit` 为战局宿主输入，不直接映射客户端消息。来源 ID 由已认证的战局连接映射提供，不能采用客户端自报值。单来源接触水位已由原生只读采集生产；多来源断线处理、LAN 接入和双客户端实测仍待实现。

当前时间下界检查不替代显式 `lifeId`。统一业务契约 v2、通用 Rust ABI 2、选将／出生公共命令和比赛／网关分离仍待迁移；本节的现有版本未因此升级。FFI 注册表只用于查找和移除句柄，输入处理与停止等待使用各句柄自己的锁；释放会封闭该句柄，已取得引用的并发调用也不会重新启动已释放对象。

Windows 原客户端兼容层 `client_battle_wire` 已实现 `BATTLE` selector 1 的 11 字节动作请求解析，以及 `AnimationEvent` 到 selector 3／4 状态消息的编码；`tests/fixtures/protocol/battle-action-wire-v1.txt` 用 49 个原 Python 编码结果逐字节验证方向动作、防御、冲刺、受击和破防。伤害输出保留受击前血量、武器和原生接触证据，按血量更新、命中点、本地攻击者结果的顺序编码；`battle-damage-wire-v1.txt` 对照原 Python 编码器验证正常命中、暴击、被攻击及破防。角色标识、动画序号及比赛相对时间在编码前检查范围。此适配已在本机 `instance` 通道接收动作并发送受支持的权威输出；完整原客户端表现仍需实机核对。

## 模块验证

- Python 导出与 Rust 加载共用 `tests/fixtures/resources/bundle-v1.json`，全部使用合成资源。
- C# 战斗 harness 验证公共帧、准入编解码、能力不匹配和缓冲区所有权。
- Rust 测试覆盖同一语料、非法长度、未知模式、资源引用和导航边界。
- 资源定向回归覆盖只读 VFS、分块解压、MSES 表、GBK、目录组装与冻结数据；`test_battle_baseline.py` 验证内嵌数据与当前 Python 源码一致。
- 网关验证三通道、异步身份与配置、逐字节响应、目录分包、背压边界、GBK 拒绝、失败响应、旧代次隔离、取消、重启及句柄释放。
- C# 存档及原生链路覆盖保存、重复保存、写入失败回滚、非法配置、备份恢复、精确 revision 和并发更新；Python 回放与协议样本保留原行为参照。

这些验证不代替双客户端共享战局、Linux 实机运行或完整性能验收，进度见 [Rust 运行时迁移](planning/rust-runtime-migration.md)。

## 产品运行时与称号接线

产品只分发并加载 `t7r_battle_ffi.dll`，没有 Python 运行时选择或回退。`NativeBridgeService.StartAsync`／`CheckAsync` 和无界面 `NativeBattleHost` 统一使用 Rust 资源校验，构建输出及 manifest 不再包含 Python 或 Business。

称号使用私有 `t7r_gateway_publish_titles`／`t7r_gateway_title_request`；请求结构 32 字节，字段为 ABI／大小、requestId、epoch、titleId 和保留位。C# 等原生说明表应用确认后再发布账号目录，Rust 按原协议顺序发送佩戴结果、基本信息和称号数组；10 秒超时、断线、epoch 变化均清理待确认请求。`titles-v1.txt` 由测试对照编码器生成并由 C# socket 测试逐字节验证。

公共语义仍为 v1，接触证据保留现有 payload 版本，Rust FFI 的通用接口保持 ABI 1，仅私有战局配置升级为 ABI 2。公共契约 v2／通用 ABI 2、独立 MatchHost、共享房间和双客户端验收仍属后续架构工作。
