# 架构说明

本文描述当前启动器内嵌运行路径。Java 平台服务、Rust 共用核心、Windows 进程内宿主与 Linux 节点的职责和协议边界见[目标架构](planning/target-architecture.md)。

## 产品与进程关系

当前产品是 Windows x64 WPF 启动器，而不是独立部署的服务端。用户运行一个启动器，本地服务随该进程启动；游戏客户端是启动器创建并管理的独立 x86 子进程。

```text
T7-Rekindle.exe (x64, WPF / .NET Framework 4.8)
  ├─ NativeBridgeService / OfflineBattleSession：会话编排与停止清理
  ├─ T7R.Battle：本地平台身份、配置与存档
  │   └─ t7r_battle_ffi.dll：Rust 网关、资源校验、战局与 AI
  └─ T7R.NativeBridge.dll：客户端生命周期、原生采样与画面适配
      └─ 原版客户端子进程 (x86)
```

产品只有 Rust 战斗路径，不加载 Python，不依赖后端选择环境变量，不启动独立 Python 或 helper 进程。无界面 `T7R.BattleServer` 也使用同一 Rust 网关。启动器退出会结束其拥有的客户端。

## 模块职责

| 源码模块 | 职责 |
| --- | --- |
| `src/Desktop/` | WPF 展示、账号服务及会话编排；原生桥调用和采样附着 |
| `src/Core/` | UI 无关的契约、设置和 ABI 布局 |
| `src/Battle/` | 本地平台身份与配置、Rust FFI 消费、无界面宿主 |
| `src/BattleRust/` | 原协议三通道、资源缓存、权威战局、AI、称号协议转换 |
| `src/Runtime/bridge/` | 客户端会话、采样入口、画面和声音邮箱 |
| `src/Runtime/launcher/` | 客户端预检、进程启动、内存适配与清理 |
| `tests/reference/` | 仅开发使用的 Python／原生历史对照实现；不参与产品分发 |

Views 和 ViewModels 不持有 socket、Python 对象或 Win32 进程句柄。Python 构建、导出和测试工具继续保留。

- `MainWindowViewModel` 负责页面与会话呈现；`GameSettingsViewModel` 管理画面、声音和输出设备，`AnnouncementViewModel` 管理公告刷新，`AutomaticUpdateChecks` 管理更新调度。客户端环境查询通过 `IClientEnvironment` 注入。
- UI 通过 `INativeBridge.GetRuntimeStatus()` 读取运行状态，不持有 `OfflineBattleSession`。回房查询、配置触发的重建和停止排空共用 `BattleRearmCoordinator`。
- `ClientFrameRuntime` 拥有共享帧钩子及远程页；图形、声音、世界采样和称号邮箱是同级组件。`MovementOverlay` 安装固定的移动适配，不暴露未实现的功能开关。
- Rust `InstanceSession` 持有连接状态；事件循环、客户端命令和定时输出分开处理，入场阶段由 `EntryPhase` 表达。伤害增益的倍率与毫秒时长从资源的 `damageBuffs` 读取并校验。

托管战斗 DTO 使用命名枚举和不可变值对象区分动作、生命、分数、装备与接触几何；盾牌 ID 和耐久作为同一条记录传递。`BattleFrameCodec.DecodeBody` 接收已去除长度前缀的帧体。上述托管接口调整不改变线协议布局或原生 ABI；调用方直接使用新签名，不保留旧构造签名兼容层。

## 启动与业务数据流

1. Desktop 检查目录和玩家名称；NativeBridge 做客户端／设备预检，Rust 校验原资源并维护独立缓存。
2. `NativeBridgeService.StartAsync` 创建 `OfflineBattleSession`，在 Rust 中建立三个回环端口和本局战斗配置。
3. 将实际端口及战斗配置交给 NativeBridge，启动客户端后附着原生采样；失败时回收本次网关、战局与客户端。
4. Rust 网关转换原协议，平台请求由 C# 本地后端处理，战斗规则和 AI 由 Rust 执行。
5. 称号仍由账号平台决定归属；原生描述表确认后发布到 Rust，Rust 处理查询、佩戴确认、超时和连接清理。
6. 停止时解除采样，结束客户端，停止战局和网关并等待工作线程退出；清理失败保留所有权以便重试。

当前仍是单机 1 玩家＋19 AI，回房后准备下一局。共享房间、独立 MatchHost、契约 v2／ABI 2 和输出调度解耦尚未实现；不将生产 Rust 切换等同于 LAN 交付。

## 客户端运行时适配

客户端兼容基线及用户检查方法见[使用说明](requirements.md#客户端兼容性)。Bootstrap 将本地服务端点写入客户端进程；MovementOverlay 在首次恢复线程前安装与已知 hash/signature 绑定的 x86 detour。资源由原 VFS 读取，在原 XML 解析器消费之前完成三项内存适配：

- 保留在线步兵模板，仅替换武将、女武将的移动组件；碰撞、输入、动画和相机组件原样保留。
- 生成内存路由树，在 `INIT_FINISH` 时仅给本地战斗角色接入原离线慢速步兵树；虚拟路径由原加载器读取现有树，随后在内存中生成路由内容，重复加载仍有效。
- 在原出生转向动作处增加本地战斗角色分支，使用原离线转向参数组和策略；其他对象保留在线转向动作。

移动、地形接触、碰撞、上下坡和下落由客户端原控制器处理，不接入服务端网络碰撞或位置积分。本地服务负责必要的角色、重力、一次自然待机初始化和四向攻防动作状态同步；位置上报仅缓存为对象重发样本，不生成周期移动、方向位置回声或初始 STOP。普通移动与主动跳跃、蹲起的完整可见效果分开验收。

运行时适配不修改磁盘上的客户端程序、DLL、VFS、加载优先级或资源。允许按用户操作保存 `UserData.cfg` 等明确支持的用户配置，规则见[游戏配置文件](runtime-contracts.md#游戏配置文件与模式切换)。XML 缓冲区使用客户端自身的文档内存池，生命周期跟随原文档；签名或资源结构不匹配时停止本次客户端。MovementOverlay 撤销全部入口后再释放仍存活进程中的远程代码页。具体生命周期、接口和所有权见[运行契约](runtime-contracts.md)。

## 后续扩展

局域网联机需要扩展当前本地单会话模型，接入共享比赛状态和按玩家归属的世界采样。启动器尚未开放远程战斗连接入口；目标为统一客户端兼容网关、可切换的平台后端，以及 Windows 内嵌和 Linux 远端共用的 Rust 战斗核心。职责约定见[目标架构](planning/target-architecture.md)，接入与验收见[联机计划](planning/battle-network-v1.md)和 [Rust 迁移计划](planning/rust-runtime-migration.md)。

源码与产品包的位置对应关系见[目录说明](source-layout.md)，构建及验证入口见[开发与交付](development.md)。
