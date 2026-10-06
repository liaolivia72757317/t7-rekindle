# 运行契约

本文面向修改 Desktop、Core、Runtime 或 Business 的开发者，定义当前模块交互、资源生命周期和持久化格式。用户操作与诊断入口见[使用说明](requirements.md)，组件关系见[架构说明](architecture.md)。

## NativeBridge ABI

导出函数使用 `extern "C" __cdecl`，当前 ABI 版本为 1。参数结构使用 `#pragma pack(push, 8)`，首两个字段固定为 `abiVersion` 和 `structSize`。跨边界只使用固定宽度整数、UTF-8 byte buffer 和不透明 session handle；不传递 STL、异常、`PyObject*` 或内部句柄。

[头文件](../src/Runtime/bridge/T7NativeBridge.h)定义参数和返回值，[导出表](../src/Runtime/bridge/T7NativeBridge.def)固定以下 13 个符号：

| 用途 | 导出函数 |
| --- | --- |
| ABI 与会话 | `t7_native_get_abi`、`t7_native_create`、`t7_native_release` |
| 提交操作 | `t7_native_submit_check`、`t7_native_submit_start`、`t7_native_submit_start_named`、`t7_native_submit_start_options`、`t7_native_submit_stop`、`t7_native_cancel` |
| 查询结果 | `t7_native_get_snapshot`、`t7_native_get_operation`、`t7_native_get_error`、`t7_native_read_logs` |

路径输入在提交函数返回前复制，拒绝 NUL、非法 UTF-8、相对路径和超过 32768 bytes 的输入。带名称的启动入口接收 UTF-8 名称，再按客户端 GBK 规则校验；原启动入口和 ABI 1 结构保持兼容。玩家名称随会话配置传入 Python 状态，不通过全局常量或客户端文件传递。

`t7_native_submit_start_options` 接收 48 字节的 `T7NativeStartOptions`，前 40 字节保留 `T7NativeStartArgs` 布局，头部 `structSize` 填完整结构大小。`flags` 的 bit 0 控制跳过启动动画；未知位或非零 `reserved` 被拒绝。两个旧启动入口仍默认播放动画。选项随提交操作复制，不在会话执行中读取启动器设置文件。

输出由调用者提供 buffer；`required` 表示不含终止符的 UTF-8 byte 数，日志 buffer 不足时不消费 cursor。操作提交与完成是不同事件，调用者通过 operation 和 session snapshot 查询结果。

托管侧使用 `LoadLibraryEx` 的绝对包路径和安全搜索 flags，解析固定导出函数。SafeHandle 释放只发起 native cleanup 请求；worker 可能仍在 DLL 内执行，因此桌面进程生命周期内不调用 `FreeLibrary`。

WPF 通过 `INativeBridge` 接口调用运行时，正式装配使用 `NativeBridgeService`。托管测试可注入 fake 实现，不改变 C ABI。

## 生命周期与清理

启动主路径为：

```text
Idle → Checking → StartingRuntime → StartingClient → AdaptingClient → Running
```

- 启动取消进入 `Cancelling`，然后按 `StoppingClient → StoppingRuntime → Idle` 清理。
- 错误先进入 `FailedCleaning`，只有本次创建的客户端、Python 和 listener 都确认清理后才发布 `Failed`。
- 清理仍有错误时保持 `FailedCleaning` 和不可重启状态，只允许再次请求 Stop 重试清理。
- operation 在本轮清理尝试结束后进入终态；托管层可结束当前等待并发起 Stop 重试，但仍以 `cleanupComplete=false` 阻止关闭和再次启动。
- `Running` 不代表客户端已完成游戏内登录或场景加载。

每个 `Session` 只有一个生命周期 worker，操作按提交顺序串行。`cancel` 直接设置当前 operation 的原子取消标志并唤醒 worker，不排在阻塞的 Start 后面。`SafeHandle.ReleaseHandle` 只提交非阻塞释放请求，由 native worker 持有内部引用完成清理。

客户端由 Bootstrap 和 Job Object 管理。运行时移动适配由 MovementOverlay 管理，撤销入口后再释放远程页；先确认客户端已停止，再关闭 Python 和 listener。Python 只由业务 worker 初始化、调用和关闭，业务代码被视为可信代码而非隔离沙箱。

端点注入分别分配记录、分组和候选容器，使用客户端原有的对齐分配器并保留其计数；超出 SSO 容量的字符串使用客户端 CRT 的 `operator new` / `operator delete`。分配、释放在客户端运行时执行，避免挂起持有堆锁的线程；发布前重新挂起并核对所有者与原向量。分配返回空指针时回收部分分配并恢复计数，发布回滚后释放未转交的内存，发布成功后由客户端析构释放。调用异常或超时沿用会话终止清理。`VirtualAllocEx` 仅承载临时调用代码与结果，不作为客户端容器或字符串的存储；调用线程退出前不回收其代码页。真实未处理异常仍按失败上报。

MovementOverlay 只在挂起状态安装或撤销三个入口：资源路径别名、XML 缓冲区容量和解析前处理。三个签名全部匹配后才写入；局部失败先恢复入口，再释放代码页。只有匹配的移动资源和显式启用的登录片头资源额外预留 4096 字节，变换后的 XML 写入客户端文档自身的内存池，不另持有跨文档的资源缓冲区。

解析前断点由 DebugClient 的原有事件线程处理，限定为本次主进程和已安装的断点地址；线程上下文、文件名、长度及 XML 结构检查失败均沿用会话失败清理。非目标资源走原路径。正常停止先等待调试线程和客户端退出，再清除 overlay 所有权；原 VFS、磁盘资源和其他进程保持不变。

路由行为树复用原移动资源，保留三个生命周期分支及其 GBK `Description`：“进入节点”“退出节点”“执行节点”。客户端按这些标识绑定分支，只有 `ID` 不足以完成解析。每个移动事件入口均检查本地单位、战斗角色及客户端玩家状态“战斗”，通过后才执行原事件内容；不在 `INIT_FINISH` 时切换到未受阶段限制的离线树。原资源路径、相机和碰撞定义保持不变。

准备阶段和 `START` 倒计时期间，角色保持 `READY_PLAY`，仅相机可调整；业务层忽略位移、跳跃和蹲起报告，对象刷新也不激活移动。进入 `GAME` 后发送 `IN_SCENE` 并激活移动，不重新创建角色或重置坐标。

### 跳过启动动画

点击“启动游戏”时固定本次 `SkipStartupAnimation` 值。启用后，复用解析前入口，仅处理 `../data/btree/流程_登陆.btree`：校验 `43506` 选择节点及其两个完整分支，将发送 `GeASEventTitleMovieDone` 的跳过分支移至加载 `TITLEMOVIE` 的播放分支之前。客户端继续执行原有登录完成逻辑，其他节点、GBK 编码和空白字节保持不变。实现见 [StartupAnimation.cpp](../src/Runtime/launcher/StartupAnimation.cpp)。

关闭时不拦截该资源；启动中或运行中切换选项只影响下次启动。新手关片头、地图加载、VFS 优先级和磁盘资源均不变；目标 XML 结构不匹配时终止本次启动并沿用会话清理。

## 服务就绪与端口

`Server::start` 先在 `127.0.0.1:0` 绑定三个 listener，通过 `getsockname` 保存实际端口，再启动 IO loop 和 Python business loop。

只有 Python `Runtime.create` 成功且 IO loop 已启动后才发布服务就绪。Bootstrap、业务 context 和 room 的 `sync_url` 使用同一份实际端口，不把请求绑定时的端口 `0` 暴露给客户端。

## 日志与保留规则

用户数据根目录为 `%LOCALAPPDATA%/T7-Rekindle`。托管日志位于 `logs/desktop.log`，原生诊断位于 `logs/native.log`；会话 Journal 与业务 revision 记录位于 `data/`。

`desktop.log` 与 `native.log` 均按本地日期轮转，最多保留 7 个归档；原生日志在跨日后的首次写入时归档为 `native.YYYY-MM-DD.N.log`，日期取文件最后写入日，序号用于避免重名，归档成功后按最后写入时间清理最旧记录。Journal 转入诊断日志时保留记录生成时的时间、级别与来源，磁盘 JSONL 格式不变。

单条原生内存日志上限为 8192 字节，按 UTF-8 字符边界截断并附加 `[truncated]`；磁盘原始记录不受此单条上限影响。

内存日志通过单调 cursor 暴露有界记录。轮转导致 cursor 早于最早记录时，返回 `gap` 和新的 earliest cursor；小 buffer 返回所需容量并保持 cursor 不变。UI 轮询在后台进行，WPF 线程只更新绑定状态。

磁盘 Journal 按 64 MiB 分段，每次运行最多保留 160 段。已结束运行最多保留 16 次，Journal 与 revisions 合计最多 10 GiB；每次 Start/Stop 按时间从旧到新裁剪，活动运行通过文件锁排除，删除失败记录诊断。内存日志与磁盘记录分开，不保证无限期保留。

原始 socket bytes 默认不写入磁盘，`wireLength` 始终表示实际收发长度。`captured=false` 时，`rawFile`、`offset`、`wireSha256` 为 `null`。`Config.captureWire` 是代码中的显式诊断开关，不是启动器的用户配置项。磁盘写失败后丢弃待写队列并停止写线程，诊断行数仍限制在 1000 行。

## 设置格式

设置文件是 `%LOCALAPPDATA%/T7-Rekindle/settings.json`，备份为同目录的 `settings.json.bak`。JSON 使用 camelCase 字段名：

| 字段 | 默认值 | 含义与约束 |
| --- | --- | --- |
| `schemaVersion` | `1` | 当前仅接受版本 1 |
| `clientDirectory` | `""` | 已验证的客户端目录；非空时须为完整路径，UTF-8 编码不超过 32768 bytes，不含 NUL 或换行 |
| `playerName` | `"新玩家"` | 玩家名称；非空时按 GBK 名称规则校验，最多 31 字节 |
| `windowWidth` | `1200` | schema 接受 480–4096；窗口实际尺寸由当前工作区决定 |
| `windowHeight` | `900` | schema 接受 320–4096；小工作区不强制此下限 |
| `darkTheme` | `false` | 深色主题偏好；系统高对比度优先 |
| `minimizeToTray` | `false` | 检测到本次受管理游戏进程后收起到托盘 |
| `startWithWindows` | `false` | 当前用户登录时打开启动器，不启动游戏；对应 HKCU Run 中的 `T7-Rekindle` 项 |
| `skipStartupAnimation` | `false` | 下次启动时跳过登录片头；不跳过新手关过场或地图加载 |

首次启动或旧设置缺少名称字段时使用“新玩家”，已保存的名称保持不变。设置允许空目录和空名称，开始游戏前仍须完成验证。旧配置缺少启动偏好字段时使用 `false`。设置读取拒绝未知字段和 JSON 尾随内容。

写入使用同目录临时文件、`Flush(true)` 和原子替换；替换已有文件时保留上一份为备份。读取损坏配置时保留原文件、尝试备份并反馈警告；两者均不可用时加载默认设置。具体实现见 [UserSettings / SettingsSchema](../src/Core/Settings.cs) 和 [SettingsService](../src/Desktop/Services/SettingsService.cs)。
