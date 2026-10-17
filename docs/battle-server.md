# 战斗运行时与独立宿主

`T7R.BattleServer` 是 Windows x64／.NET Framework 4.8 无界面宿主，通过 `NativeBattleHost` 复用与启动器相同的 Rust 网关和战局。不加载 NativeBridge／Python，不创建游戏进程，也不依赖 WPF 或 Java 登录。

当前仍是单客户端战局模型，不是多人节点。无界面宿主没有原客户端采样，不能单独驱动完整人机对局；共享 MatchHost、LAN 和 Linux 节点见[目标架构](planning/target-architecture.md)。

## 构建与模块验证

```powershell
python scripts/build.py --project battle --artifacts artifacts/battle-check
python scripts/build.py --project battle-tests --artifacts artifacts/battle-check
```

构建使用固定 Rust 工具链及现有 VS／.NET 工具链。模块测试使用合成 VFS、独立缓存与状态目录，覆盖默认 Rust 宿主的启动、端口、协议、重复启动、停止和资源失败，不需要 CPython SDK。

## 运行

```powershell
& .\artifacts\bin\T7R.BattleServer\x64\Release\net48\T7R.BattleServer.exe `
  --package-root 'PACKAGE_ROOT' `
  --client-directory 'CLIENT_ROOT\Bin' `
  --state-root 'STATE_ROOT'
```

`PACKAGE_ROOT` 包含同版本 `t7r_battle_ffi.dll`；控制台目录包含 `T7R.Core.dll`、`T7R.Battle.dll` 和 exe.config。客户端根目录需提供 `update.lst` 与原始 VFS。`STATE_ROOT` 保存独立 `battle-cache` 和 `player-profile.json`。

`NativeBattleHost.Start` 等待资源加载和三个回环端口就绪；重复启动报错。`GetSnapshot`／`GetError` 返回状态与诊断；`Stop`／`Dispose` 停止战局和网关并等待线程退出。`Ctrl+C` 停止控制台宿主，错误返回非零退出码。旧 `t7r_battle_host_*` C++／Python ABI 已删除，不保留转接层。

## BattleProtocol v1 帧基础

`BattleFrame` 表达与实现无关的消息，`BattleConnection` 负责有界可靠传输。
当前已有原协议三通道中继组件，尚未接入准入处理器、启动器和共享比赛状态；
`Admit` 消息体与能力匹配已定义，见[公共契约](battle-contracts.md#帧与准入)；`WorldSample`、`PlayerIntent`、`MatchEnded` 和 `MatchAborted` 已有语义消息体及黄金样本，但准入处理器、接触证据、增量权威输出及实机接入仍待完成。

所有整数使用大端序。长度前缀为 4 字节，值为其后内容长度，不含前缀自身。

| 帧体偏移 | 大小 | 字段 |
| --- | --- | --- |
| 0 | 1 | 协议版本，当前为 1 |
| 1 | 1 | `BattleMessageKind` |
| 2 | 1 | `BattleChannel`：0 无角色，1 login，2 logic，3 instance |
| 3 | 1 | 保留，必须为 0 |
| 4 | 8 | roomId |
| 12 | 8 | matchId |
| 20 | 8 | playerId |
| 28 | 8 | sequence |
| 36 | 可变 | payload |

消息编号：1 Admit、2 Admitted、3 OpenChannel、4 GameData、5 CloseChannel、
6 WorldSample、7 MatchState、8 MatchEnded、9 Ping、10 Pong、
11 PlayerIntent、12 MatchAborted。
仅 OpenChannel、GameData、CloseChannel 携带非零原协议角色。

- 帧体范围为 36 字节至 4 MiB，先检查长度再分配接收缓冲区。
- 单连接一个读者；写入串行，最多 16 个待写帧、8 MiB 待写字节（包含长度前缀）。
- 截断、非法帧或进行中的 I/O 取消关闭连接；纯排队阶段的写取消不破坏连接。
- `ForLan` 接收局域网双向流；`ForOnline` 仅接收已经完成 TLS 握手的 `SslStream`。
  握手调用方必须使用证书链和主机名校验，不配置跳过验证的回调。
- 传输层不把帧中的 playerId 当作已认证身份；准入、房间绑定、序号去重由接入层负责。

模块测试覆盖 ABI、原协议 AUTH、Python owner 冲突、重启、资源错误、分片与连续帧、
发送背压、取消、非法长度及 TCP 回环收发；真实双客户端对战是后续独立验收门槛。

### 原协议三通道中继

`BattleGameRelay` 接收一个已经准入的 `BattleConnection`、固定的 roomId/matchId/playerId，
以及按 login/logic/instance 打开私有双向流的工厂。身份由房间分配提供，不从首帧或旧 AUTH 推断。

| 消息 | payload | 处理 |
| --- | --- | --- |
| OpenChannel | 空 | 打开对应原协议通道；每个角色在本次连接中最多打开一次 |
| GameData | 1–65536 字节 | 原样转发字节流；不假设 TCP 分段等于游戏消息边界 |
| CloseChannel | 空 | 关闭对应通道；原协议流 EOF 也会生成该消息 |

中继阶段双向序号各自从 1 开始并严格递增。错房间、错比赛、错玩家、重复序号、
未打开的通道和非法消息会结束中继；它不接受再次准入或世界采样消息。
关闭通道后已经在途的数据丢弃，重复关闭幂等，重新打开仍拒绝。
三个回传读取器各保留至多一个 64 KiB 数据块，序号分配和网络写入保持相同顺序。
停止后等待 `RunAsync` 完成，再停止原生宿主，以保证所有读取器退出。

模块测试使用真实 TCP，覆盖三通道字节回传、身份隔离、重放、EOF、取消、后端失败，
以及经 BattleProtocol 到原生宿主三个端口的旧 AUTH 握手。
中继目前仅是节点侧组件；启动器本地监听/地址桥接、原生连接与玩家关联、角色消息转换和
共享 MatchState 尚未接入。仅把多个原游戏连接转发给宿主不等于同局多人战斗。

## 角色视图与比赛计时器

`BattleActorMap` 将比赛角色映射为接收方视图：本机人类 RID 1、敌方 RID 2–11、
友方 RID 12–20，0 保留为场景/无目标。映射按全局 actorId 排序，具有双向唯一性，
不受名单顺序影响；同一 playerId 只对应一个人类角色，AI 的 playerId 为 0。
该工具尚未替换原协议消息中的角色字段，接入时必须同时处理目标引用和结算。

原有连接计时器保持不变。共享比赛逻辑可使用以下 transition：

```python
{
    "id": "match:7:round-end",
    "delayMs": 1000,
    "event": {
        "type": "timer", "connection": 0, "scope": "match",
        "matchId": 7, "name": "round-end"
    }
}
```

新增计时器的 matchId 必须等于提交后状态根节点的 `matchId`；名称必须匹配 ID。
`delayMs=-1` 可取消旧局计时器。比赛计时器不随单个连接断开而消失，比赛结束应显式取消；
已经入队的旧局事件在调用业务 handler 前丢弃。动作计时器仍从提交时点计算持续时间，
并保持既有输入/世界事件的因果顺序。停止宿主会清空全部计时器。

对应验证为 `test_match_timers.py` 和 `T7R.RuntimeTests.exe --server-context PACKAGE_ROOT`。
现有单机业务仍发出连接计时器；共用 MatchState 和采样汇总尚待接入。
