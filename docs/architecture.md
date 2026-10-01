# 架构说明

## 产品与进程关系

当前产品是 Windows x64 WPF 启动器，而不是独立部署的服务端。用户运行一个启动器，本地服务随该进程启动；游戏客户端是启动器创建并管理的独立 x86 子进程。

```text
T7-Rekindle.exe (x64, WPF / .NET Framework 4.8)
  ├─ Core：托管契约、设置模型和 ABI 布局
  └─ NativeBridgeService：加载原生模块并调用 C ABI
      └─ T7.NativeBridge.dll (C++17，与启动器同一进程)
          ├─ Session：会话状态、操作队列和清理
          ├─ Server：登录 / 逻辑 / 实例三条本地通道
          ├─ PythonHost：嵌入式 CPython 3.14.4 与 Business
          └─ Bootstrap：客户端启动、运行时适配和 Job Object
              └─ 原版客户端子进程 (x86)
```

产品不生成独立 Server、Python 或 helper 进程。启动器退出会结束其拥有的客户端，因此它不是启动游戏后即可关闭的下载器或入口工具。

## 模块职责

| 源码模块 | 职责与依赖 |
| --- | --- |
| `src/Desktop/` | WPF 页面、ViewModel、目录选择、设置与日志服务；引用 Core，通过 NativeBridgeService 调用原生模块 |
| `src/Core/` | UI 无关的状态、设置、托管接口和 ABI 布局；不引用 WPF，也不加载原生 DLL |
| `src/Runtime/bridge/` | 对外 C ABI 与 Session 生命周期 |
| `src/Runtime/server/` | 网络传输、定时器、Journal 和 Python owner |
| `src/Runtime/launcher/` | 客户端预检、进程创建、运行时适配和清理 |
| `src/Runtime/core/` | 原生公共路径、编码、配置与协议 framing |
| `src/Business/runtime/` | Python 业务版本加载、状态迁移和结果校验 |
| `src/Business/scripts/` | 登录、房间、场景与移动等业务状态转换，以及实际使用的协议 codec |

Views 和 ViewModels 不持有 socket、Python 对象或 Win32 进程句柄。原生模块不通过回调直接操作 WPF；桌面端在后台轮询状态和日志，再更新绑定。

## 启动与业务数据流

1. Desktop 校验目录结构和玩家名称，向 NativeBridge 提交异步操作。
2. 原生预检核对客户端基线和图形能力。
3. Server 在 `127.0.0.1` 上建立三个动态端口，准备 IO loop 和嵌入式 Python 业务。
4. Bootstrap 创建挂起的客户端并加入带 `KILL_ON_JOB_CLOSE` 的 Job Object，进行运行时适配和启动处理。
5. 客户端连接本地通道；原生层负责收发和 framing，Python 业务处理事件并返回状态、待发送消息、定时器和日志。
6. 会话结束时，先处理客户端及其运行时资源，再关闭 Python 和 listener。清理失败会阻止下一次启动。

登录通道提供本地身份与登录消息，逻辑通道处理大厅、房间和选将，实例通道承载场景、移动及战斗相关消息。三条通道共享本次会话配置，包括玩家名称和实际分配的端口。

## 客户端运行时适配

客户端兼容基线及用户检查方法见[使用说明](requirements.md#客户端兼容性)。Bootstrap 将本地服务端点写入客户端进程；MovementOverlay 安装与已知 hash/signature 绑定的 x86 detour，将匹配的在线实体表请求路由到客户端已有的离线实体表。

地形、重力、跳跃和蹲起依赖客户端已有碰撞、控制器、行为树及离线资源。当前业务生成本地角色的移动配置与相关消息，不在服务端积分、回放或伪造客户端位置；这不等于已实现多人权威移动或完整战斗。

适配不修改磁盘上的客户端文件、VFS 或资源。MovementOverlay 管理入口字节、路由状态和远程内存，撤销入口后再释放仍存活进程中的远程页。具体生命周期、接口和所有权见[运行契约](runtime-contracts.md)。

## 后续扩展

局域网联机属于下一阶段，需要扩展当前本地单会话模型，并设计多玩家身份、连接方式和服务端统一维护的对局状态。当前产品没有远程地址、端口或独立服务端配置入口；规划见[实施计划](planning/implementation-plan.md)。

源码与产品包的位置对应关系见[目录说明](source-layout.md)，构建及验证入口见[开发与交付](development.md)。
