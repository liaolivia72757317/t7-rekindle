# 运行契约

本文面向修改 Desktop、Core、Runtime 或 Business 的开发者，定义当前模块交互、资源生命周期和持久化格式。用户操作与诊断入口见[使用说明](requirements.md)，组件关系见[架构说明](architecture.md)。

## NativeBridge ABI

导出函数使用 `extern "C" __cdecl`，当前 ABI 版本为 1。参数结构使用 `#pragma pack(push, 8)`，首两个字段固定为 `abiVersion` 和 `structSize`。跨边界只使用固定宽度整数、UTF-8 byte buffer 和不透明 session handle；不传递 STL、异常、`PyObject*` 或内部句柄。

[头文件](../src/Runtime/bridge/T7NativeBridge.h)定义参数和返回值，[导出表](../src/Runtime/bridge/T7NativeBridge.def)固定以下符号：

| 用途 | 导出函数 |
| --- | --- |
| ABI 与会话 | `t7_native_get_abi`、`t7_native_create`、`t7_native_release` |
| 提交操作 | `t7_native_submit_check`、`t7_native_submit_start`、`t7_native_submit_start_named`、`t7_native_submit_start_options`、`t7_native_submit_stop`、`t7_native_cancel` |
| 查询结果 | `t7_native_get_snapshot`、`t7_native_get_operation`、`t7_native_get_error`、`t7_native_read_logs` |
| 输出设备 | `t7_native_set_output_device` |
| 画面与声音 | `t7_native_get_graphics`、`t7_native_apply_graphics`、`t7_native_get_audio`、`t7_native_apply_audio` |

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

游戏主窗口从已校验的客户端对象读取，并核对窗口所属 PID；只有观察到该窗口后才启用关闭检测。窗口被销毁且连续 1 秒未出现同进程、同窗口类的替代主窗口时，自动进入 `StoppingClient`，终止本次 Job 中的残留进程并清理本地服务；最小化、隐藏和短暂窗口重建不触发清理。成功后以 `client-window-closed` 返回 `Idle`，不将主动终止产生的退出码当作崩溃；清理失败仍保持 `FailedCleaning`，清理期间拒绝新的 Start 和 Check。未托管的进程不受影响。

启动同步由 StartupGate 管理，不依赖窗口标题、弹窗文字或系统代码页。已适配的初始化失败分支不再显示原版启动提示；原初始化成功分支保持不变。首次恢复客户端前，在已校验的服务器选择入口安装断点；命中时校验本次主进程、线程上下文和网络对象，只挂起进入该入口的线程，调试事件循环和其他线程继续运行。Bootstrap 完成端点发布及回读验证后，恢复入口指令并放行所有等待线程；恢复时已排队的断点事件也须回到原入口执行。

门控等待沿用 60 秒超时，分配工作线程沿用 10 秒超时。取消、对象变化、签名不匹配或发布失败时终止本次客户端，不放行空列表选择。停止时先确认调试线程和客户端退出，再清理门控线程句柄。日志按“门控已到达 → 端点发布已验证 → 门控已释放”记录启动进度。该机制只处理启动同步，不改变协议和资源文件的既有编码约定。

端点注入分别分配记录、分组和候选容器，使用客户端原有的对齐分配器并保留其计数；超出 SSO 容量的字符串使用客户端 CRT 的 `operator new` / `operator delete`。分配、释放在客户端运行时执行，避免挂起持有堆锁的线程；发布前重新挂起并核对所有者与原向量。分配返回空指针时回收部分分配并恢复计数，发布回滚后释放未转交的内存，发布成功后由客户端析构释放。调用异常或超时沿用会话终止清理。`VirtualAllocEx` 仅承载临时调用代码与结果，不作为客户端容器或字符串的存储；调用线程退出前不回收其代码页。真实未处理异常仍按失败上报。

客户端首次连接及后续重试均打开 `logic` 通道，因此四条端点记录及其描述字符串统一使用逻辑端口 `ports[1]`，不随选择器奇偶值切换到只发送版本响应的 `login` 端口。保留两个同主机候选、原有连接超时和独立的实例端口，正常启动无需等待一次登录超时后再重试。

MovementOverlay 只在挂起状态安装或撤销三个入口：资源路径别名、XML 缓冲区容量和解析前处理。三个签名全部匹配后才写入；局部失败先恢复入口，再释放代码页。只有匹配的移动资源和显式启用的登录片头资源额外预留 4096 字节，变换后的 XML 写入客户端文档自身的内存池，不另持有跨文档的资源缓冲区。

解析前断点由 DebugClient 的原有事件线程处理，限定为本次主进程和已安装的断点地址；线程上下文、文件名、长度及 XML 结构检查失败均沿用会话失败清理。非目标资源走原路径。正常停止先等待调试线程和客户端退出，再清除 overlay 所有权；原 VFS、磁盘资源和其他进程保持不变。

路由行为树复用原移动资源，保留三个生命周期分支及其 GBK `Description`：“进入节点”“退出节点”“执行节点”。客户端按这些标识绑定分支，只有 `ID` 不足以完成解析。每个移动事件入口均检查本地单位、战斗角色及客户端玩家状态“战斗”，通过后才执行原事件内容；不在 `INIT_FINISH` 时切换到未受阶段限制的离线树。原资源路径、相机和碰撞定义保持不变。

确认出战时先发送 `IN_SCENE` 完成选将面板的关闭和角色入场，再在同一批消息中恢复 `READY_PLAY`，不等待开局倒计时。准备阶段和 `START` 倒计时期间，角色保持 `READY_PLAY`，仅相机可调整；业务层忽略位移、跳跃和蹲起报告，对象刷新也不激活移动。进入 `GAME` 后发送 `IN_SCENE` 并激活移动，不重新创建角色或重置坐标。

滚轮先交给原 UI 处理，再检查客户端已有的相机输入状态；ESC 菜单停用操作时不向游戏继续分发滚轮事件，关闭菜单后恢复。该内存适配只移除滚轮路径中跳过状态检查的分支，不改菜单自身滚动、相机缩放参数或系统输入设置。

### 模型动作

四向攻击和四向招架接收 `cmd=4 / selector=1` 的 11 字节输入，只下发现有 `STATE_SYNC_SIMPLE` 动作状态，不计算命中、伤害或位移。攻击按“准备 → 蓄势 → 松键出招 → 收招 → 待机”推进；招架保持到松键后回待机。动作阶段间隔为 200 ms，重复按下或释放不重启动作。

动作仅在 `GAME` 且角色已入场时接受，离场取消定时器。动作进度和序号保存在会话中，热重载继续当前动作；状态时间使用实例起点的相对毫秒，不读取系统日历时间。

#### 跳跃与蹲伏

Runtime 的内存路由保留原跳跃指令，并在其成功后发送本地模型事件 `Jump`；原生 `JUMP_LAND` 通知触发 `EndJump` 退出跳跃姿态，保留原落地音效，不再重入 `JumpLand` 或增加固定落地停顿。蹲伏／起身请求替换为本地 `Crouch`／`EndCrouch` 模型事件。这些分支继续受本地战斗角色和 `GAME` 状态限制，不修改客户端的重力、碰撞或位置推进。

`cmd=2 / selector=52` 的分类 `1` 更新移动快照，分类 `2`、`3` 分别只记录蹲伏、跳跃按键，不覆盖坐标、朝向或 WASD 状态。服务端不为这些输入生成位置回声、固定滞空定时器或跳跃位置积分。可见动画与组合操作仍需实机验收。

### 输入法子进程兼容

每次启动读取 Windows IMM 与用户/系统 TSF 注册信息，覆盖 x86、x64 注册表视图并处理 WOW64 模块路径。仅当主游戏或已验证游戏组件实际加载注册模块时，才启用本次会话的兼容模式；模块卸载不撤销，下次启动重新判定。注册信息读取或路径解析失败时记录 `WARNING` 并保持原有严格子进程策略，不使用不完整快照。

输入法身份判定仅依据 IMM/TSF 注册模块，不增加签名、证书、文件哈希或安装目录校验。兼容模式允许本次进程树中的其他未知子进程，但不将这些进程标记为“已验证输入法程序”；它们必须属于本次 Job。已知游戏组件即使在兼容模式下也必须匹配原路径及 SHA-256，同名但不匹配时仍结束会话。

兼容子进程不执行游戏专用 DLL 适配，已知禁用模块检查仍适用于所有进程。兼容子进程非零退出或发生未处理异常时记录 `WARNING`；异常只终止该子进程，不影响游戏会话。调试、终止或清理失败仍走会话失败清理。停止、取消或主进程退出时回收本次 Job 内的全部后代，不处理启动前已存在的外部进程。日志记录进程角色、PID、模块名和注册来源，不记录完整个人路径或命令行。

### 跳过启动动画

点击“启动游戏”时固定本次 `SkipStartupAnimation` 值。启用后，复用解析前入口，仅处理 `../data/btree/流程_登陆.btree`：校验 `43506` 选择节点及其两个完整分支，将发送 `GeASEventTitleMovieDone` 的跳过分支移至加载 `TITLEMOVIE` 的播放分支之前。客户端继续执行原有登录完成逻辑，其他节点、GBK 编码和空白字节保持不变。实现见 [StartupAnimation.cpp](../src/Runtime/launcher/StartupAnimation.cpp)。

关闭时不拦截该资源；启动中或运行中切换选项只影响下次启动。新手关片头、地图加载、VFS 优先级和磁盘资源均不变；目标 XML 结构不匹配时终止本次启动并沿用会话清理。

## 游戏配置文件与模式切换

输出设备是启动偏好，独立于游戏画面配置，保存在 `settings.json` 的 `outputDeviceId`：空字符串表示系统默认，否则使用 D3D9 `DeviceIdentifier` 的 GUID（D 格式），不保存可能变化的枚举序号。设备列表仅包含 HAL PS/VS 3.0 达标的适配器；已保存但未检测到的设备保留并提示，不代选其他设备。

`t7_native_set_output_device` 复制 16 字节 GUID，全零表示默认；后续 Check/Start 命令各自固定该标识，操作执行期间拒绝修改。启动时重新解析设备序号并检查能力。显式选择时，在客户端首次恢复执行前校验并安装渲染初始化与分辨率枚举入口，使两者使用同一适配器；默认选择保持原入口不变。首次画面快照就绪时回读渲染器设备序号和 GUID，确认一致后才报告已确认，磁盘二进制不变。

游戏未运行时，“设置 → 游戏设置”直接读取客户端 `Bin/../Data/UserData/UserData.cfg`，保存后下次启动游戏生效。此文件为 `PropertySheet Version="100"` XML，画面与声音值位于 `Record Name="Config"`，缺少记录值时沿用 `Header` 默认值；旧配置缺少 `Swoosh` 时视为 `0`，保存画面设置时补齐类型声明。

离线保存只更新对应组的字段，保留其他设置、记录、注释、XML 编码和 BOM；不修改 `PlayerConfig.cfg` 或其他用户配置。使用同目录临时文件、`Flush(true)` 和原子替换，旧文件保留为 `UserData.cfg.bak`。保存前回读并比较该组原值，发现冲突时同步文件中的最新值，不覆盖外部修改。文件缺失、格式无效或写入失败会在设置组内提示，不以默认值覆盖损坏文件。

离线时每秒回读文件，未变化的配置不影响编辑草稿；外部修改只更新变化的设置组。游戏启动或结束期间暂停编辑，运行后仅使用内存同步，不因内存尚未就绪或同步失败而回退写文件。游戏退出并完成清理后重新读取文件。切换模式或客户端目录不自动提交未保存草稿；目录尚在编辑、验证中时禁用保存。检测到同目录中非本次启动器托管的游戏进程时暂停文件编辑，不附加其他进程。

## 画面设置同步

“设置 → 游戏设置”提供分辨率、显示模式、画质、视野距离、视野雾、死亡物理效果、垂直同步、帧数限制和刀光。编辑只改变草稿，点击“保存设置”才写入当前数据源。“初始化设置”也需要保存，“撤销修改”恢复配置文件或运行中游戏的当前值。

分辨率选项与游戏一样枚举输出设备的 D3D9 32-bit 显示模式，合并不同刷新率的重复尺寸并保留枚举顺序，不使用固定预设列表。未运行时使用当前选择，运行时使用本次启动固定的设备；运行中修改输出设备只影响下次启动。进入文件编辑或运行时同步、离线切换输出设备时重新枚举；当前配置中的自定义分辨率仍可显示，读取失败时仅保留当前值并提示，不影响其他画面选项的保存。

`t7_native_get_graphics` 返回独立的 64-byte 快照（ABI 1），`t7_native_apply_graphics` 接收同结构的值和期望 `revision`，仅排队而不阻塞 UI。状态为 `Unavailable / Ready / Applying / Failed / Conflict`。内存模式下，游戏未就绪、停止中或同步失败时禁用编辑；只操作本次启动器拥有的进程。

启动前安装帧线程邮箱，配置查询及应用均在原游戏线程执行并保留寄存器、FPU/SSE 状态。应用使用原画面设置处理函数，由游戏更新渲染状态并保存当前用户配置，不修改客户端磁盘二进制。`ConfigLevel` 与五档画质反向映射，帧数限制对应 60 / 200；启用垂直同步时沿用游戏的同步限帧行为。

显示模式读取渲染器已生效的模式和实际窗口样式，而非仅依赖配置中的 `FullScreen`。从全屏切回窗口时，先在游戏线程恢复窗口，再执行原设置流程；只有渲染模式、窗口样式及恢复状态均确认成功后才报告已应用。设备未就绪或切换失败时不以配置保存成功代替应用确认。普通窗口内设置修改和声音设置保存不主动恢复窗口。

原生工作线程以 100 ms 间隔推进读请求，启动器每 250 ms 读取缓存。游戏内保存后自动回读，不触发反向写入；新值到来时替换未保存草稿并提示。提交时核对 revision，游戏线程执行前再次比较当前原始配置；发生冲突只回读新值，不覆盖游戏。画面设置保存在游戏配置中，不在 `settings.json` 再保存一份或启动时自动覆盖。

## 声音设置同步

“设置 → 游戏设置”中的声音设置提供背景音乐、游戏音效的独立开关和 0–100% 音量。关闭声音保留原音量，不修改 Windows 系统音量。编辑、保存、初始化、撤销和冲突处理与画面设置一致；两类设置各自保存，不互相覆盖。

`t7_native_get_audio` / `t7_native_apply_audio` 使用独立的 40-byte 快照（ABI 1），音量以 0–1 浮点数传递，静音标记为 0 / 1。声音邮箱复用原游戏帧线程入口，但有独立请求、revision 和失败状态。调用原音乐、音效设置函数更新声音引擎，再以仅声音变更标记调用原保存函数；不触发分辨率或显示模式变更。

回读使用游戏当前声音状态，因此游戏内调整音量或静音时即可同步，不必等到保存。启动器不在 `settings.json` 保存第二份声音配置，也不自动回写游戏内变更。

## 服务就绪与端口

`Server::start` 先在 `127.0.0.1:0` 绑定三个 listener，通过 `getsockname` 保存实际端口，再启动 IO loop 和 Python business loop。

只有 Python `Runtime.create` 成功且 IO loop 已启动后才发布服务就绪。Bootstrap、业务 context 和 room 的 `sync_url` 使用同一份实际端口，不把请求绑定时的端口 `0` 暴露给客户端。

登录通道认证完成后等待 1 秒发送版本响应；逻辑通道认证完成后等待 1 秒发送登录成功响应，再等待 1 秒发送登录同步。这三段固定间隔统一使用 Business 的 `STARTUP_STEP_DELAY_MS`，不包含客户端加载和连接耗时。重复认证不重新计时，未到期或已消费的定时器不发送消息；实例初始化和对局倒计时保持独立。

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
| `outputDeviceId` | `""` | 系统默认输出设备；显式选择保存 D3D9 DeviceIdentifier 的 GUID，下一次启动生效 |
| `updateChannel` | `"stable"` | 更新订阅渠道，仅接受 `stable`（正式版）或 `preview`（预览版），与当前构建身份独立 |

首次启动或旧设置缺少名称字段时使用“新玩家”，已保存的名称保持不变。设置允许空目录和空名称，开始游戏前仍须完成验证。旧配置缺少启动偏好字段时使用 `false`。设置读取拒绝未知字段和 JSON 尾随内容。

旧配置缺少 `updateChannel` 时，读取同目录的 `update-settings.json`，无效时尝试其 `.bak`，将渠道与其余设置一起原子保存到 `settings.json`；两份旧渠道配置均无效时使用正式渠道并提示。已有 `updateChannel` 的主配置或恢复备份优先，不再导入旧渠道文件。迁移不删除旧文件，完成后不再维护它们；迁移写入失败会提示并保留原文件，后续读取重试。主配置和备份都损坏时，不以迁移为由覆盖原文件。回退到尚不识别 `updateChannel` 的旧版启动器时，该版本可能拒绝此配置。

写入使用同目录临时文件、`Flush(true)` 和原子替换；替换已有文件时保留上一份为备份。读取损坏配置时保留原文件、尝试备份并反馈警告；两者均不可用时加载默认设置。具体实现见 [UserSettings / SettingsSchema](../src/Core/Settings.cs) 和 [SettingsService](../src/Desktop/Services/SettingsService.cs)。

### 回退设置契约

新程序包声明 `rollbackProtocol: 1` 和 `settingsSchemaVersion`。打包验证设置 schema 与 `SettingsSchema.CurrentVersion`、安装器中的 `RollbackSettingsSchemaVersion` 一致。后续增加旧版不接受的字段或改变字段含义时，必须提升设置 schema 并实现对应的正常升级迁移；不得仅依据版本号猜测兼容性。

回退安装器等待启动器退出后，将 `settings.json`、`settings.json.bak`、`update-settings.json`、`update-settings.json.bak` 中实际存在的文件复制到 `%LOCALAPPDATA%\T7-Rekindle\settings-backups\<operationId>.backup`。`backup.ini` 记录原始文件存在状态、本项目 HKCU Run 登录启动项及安装目录。备份失败停止安装；已有备份不覆盖、不自动删除。

schema 相同时保留设置；不同时，程序安装成功后才清除上述四份配置并写入目标 schema 与选中的 `updateChannel`，其余字段由目标版本默认值初始化，同时移除本项目登录启动项。普通安装和普通更新不执行此重置。重置失败尝试恢复全部配置与启动项，显示备份位置并停止自动启动；该流程不提供程序目录的原子恢复。

手动恢复设置时，先确认 `backup.ini` 的 `installation.complete=1`，安装与备份 schema 兼容的启动器并完全退出，再按文件存在记录将原本存在的文件复制回设置目录，移除原本不存在的同名文件。登录启动项可在启动器设置中重新启用；不要将高版本设置直接恢复给不兼容的旧版。备份仅保存在本机，不随发布上传。
