# 运行契约

本文面向修改 Desktop、Core、Runtime 或 Business 的开发者，定义当前模块交互、资源生命周期和持久化格式。用户操作与诊断入口见[使用说明](requirements.md)，组件关系见[架构说明](architecture.md)。

## NativeBridge ABI

导出函数使用 `extern "C" __cdecl`，当前 ABI 版本为 1。参数结构使用 `#pragma pack(push, 8)`，首两个字段固定为 `abiVersion` 和 `structSize`。跨边界只使用固定宽度整数、UTF-8 byte buffer 和不透明 session handle；不传递 STL、异常、`PyObject*` 或内部句柄。

[头文件](../src/Runtime/bridge/T7NativeBridge.h)定义参数和返回值，[导出表](../src/Runtime/bridge/T7NativeBridge.def)固定以下 12 个符号：

| 用途 | 导出函数 |
| --- | --- |
| ABI 与会话 | `t7_native_get_abi`、`t7_native_create`、`t7_native_release` |
| 提交操作 | `t7_native_submit_check`、`t7_native_submit_start`、`t7_native_submit_start_named`、`t7_native_submit_stop`、`t7_native_cancel` |
| 查询结果 | `t7_native_get_snapshot`、`t7_native_get_operation`、`t7_native_get_error`、`t7_native_read_logs` |

路径输入在提交函数返回前复制，拒绝 NUL、非法 UTF-8、相对路径和超过 32768 bytes 的输入。带名称的启动入口接收 UTF-8 名称，再按客户端 GBK 规则校验；原启动入口和 ABI 1 结构保持兼容。玩家名称随会话配置传入 Python 状态，不通过全局常量或客户端文件传递。

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

## 服务就绪与端口

`Server::start` 先在 `127.0.0.1:0` 绑定三个 listener，通过 `getsockname` 保存实际端口，再启动 IO loop 和 Python business loop。

只有 Python `Runtime.create` 成功且 IO loop 已启动后才发布服务就绪。Bootstrap、业务 context 和 room 的 `sync_url` 使用同一份实际端口，不把请求绑定时的端口 `0` 暴露给客户端。

## 日志与保留规则

用户数据根目录为 `%LOCALAPPDATA%/T7-Rekindle`。托管日志位于 `logs/desktop.log`，原生诊断位于 `logs/native.log`；会话 Journal 与业务 revision 记录位于 `data/`。

内存日志通过单调 cursor 暴露有界记录。轮转导致 cursor 早于最早记录时，返回 `gap` 和新的 earliest cursor；小 buffer 返回所需容量并保持 cursor 不变。UI 轮询在后台进行，WPF 线程只更新绑定状态。

磁盘 Journal 按 64 MiB 分段，每次运行最多保留 160 段。已结束运行最多保留 16 次，Journal 与 revisions 合计最多 10 GiB；每次 Start/Stop 按时间从旧到新裁剪，活动运行通过文件锁排除，删除失败记录诊断。内存日志与磁盘记录分开，不保证无限期保留。

原始 socket bytes 默认不写入磁盘，`wireLength` 始终表示实际收发长度。`captured=false` 时，`rawFile`、`offset`、`wireSha256` 为 `null`。`Config.captureWire` 是代码中的显式诊断开关，不是启动器的用户配置项。磁盘写失败后丢弃待写队列并停止写线程，诊断行数仍限制在 1000 行。

## 设置格式

设置文件是 `%LOCALAPPDATA%/T7-Rekindle/settings.json`，备份为同目录的 `settings.json.bak`。JSON 使用 camelCase 字段名：

| 字段 | 默认值 | 含义与约束 |
| --- | --- | --- |
| `schemaVersion` | `1` | 当前仅接受版本 1 |
| `clientDirectory` | `""` | 已验证的客户端目录；非空时须为完整路径，UTF-8 编码不超过 32768 bytes，不含 NUL 或换行 |
| `playerName` | `""` | 玩家名称；非空时按 GBK 名称规则校验，最多 31 字节 |
| `windowWidth` | `800` | schema 接受 480–4096；当前界面最小宽度为 720 |
| `windowHeight` | `600` | schema 接受 320–4096；当前界面最小高度为 560 |
| `darkTheme` | `false` | 深色主题偏好；系统高对比度优先 |

空目录和空名称用于首次启动或兼容缺少名称的旧设置，开始游戏前仍须完成验证。设置读取拒绝未知字段和 JSON 尾随内容。

写入使用同目录临时文件、`Flush(true)` 和原子替换；替换已有文件时保留上一份为备份。读取损坏配置时保留原文件、尝试备份并反馈警告；两者均不可用时加载默认设置。具体实现见 [UserSettings / SettingsSchema](../src/Core/Settings.cs) 和 [SettingsService](../src/Desktop/Services/SettingsService.cs)。
