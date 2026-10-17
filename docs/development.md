# 开发与交付

本文说明如何在一台新准备的 Windows 机器上从源码构建、测试和制作产品包。仅使用启动器时，阅读[运行环境与使用说明](requirements.md)即可，无需安装开发工具。

下面的命令在包含 `T7-Rekindle.sln` 的仓库根目录，用 PowerShell 执行。构建脚本检查前置组件，不自动安装系统工具；首次依赖还原需要访问 NuGet，或已有完整的依赖缓存。

## 项目命名规范

社区项目全称为“铁骑·重燃（T7-Rekindle）”，简称为 `T7R`，小写技术前缀为 `t7r`。`T7` 用于指代原游戏。新增代码、工程、配置和测试遵循下表，不再以原游戏简称作为项目技术前缀。

| 对象 | 当前规范 | 迁移前形式 |
| --- | --- | --- |
| C++ 命名空间 | `t7r` 及其子命名空间 | `t7` |
| 原生类型、常量、导出 | `T7RNative*`、`T7RNB_*`、`t7r_native_*` | `T7Native*`、`T7NB_*`、`t7_native_*` |
| C# 命名空间 | `T7R.Core`、`T7R.Desktop` 及其子命名空间 | `T7.Rekindle.*` |
| 工程、库与测试程序 | `T7R.Core`、`T7R.Desktop`、`T7R.NativeBridge`、`T7R.*Tests`、`T7R.ManagedHarness` | `T7.*` |
| WPF 资源标识 | `T7R.Icon.*`、`T7R.Documents.*` | `T7.Icon.*`、`T7.Rekindle.Documents.*` |
| 跨语言资源错误标记 | `[T7R_RESOURCE:code]` | `[T7_RESOURCE:code]` |
| Python 测试对照动态包 | `t7r_rev_<随机标识>` | `t7rev_<随机标识>` |
| 自建移动资源 | `t7r_runtime_movement.btree` | `t7_runtime_movement.btree` |
| 构建变量与辅助标识 | `T7R_*`、`_T7R*`；小写辅助名称使用 `t7r_`／`t7r-` | 对应的 `T7`／`t7` 技术前缀 |

以下是明确保留项，不属于待替换的旧简称：

- **完整产品与安装身份**：`T7-Rekindle`、仓库名 `t7-rekindle`、解决方案名、主程序及其 `.config`、安装器文件名和 AppId、Mutex、注册表启动项、用户数据目录、发布文件名与下载路径。WPF 的 `/T7-Rekindle;component/...` 仍指向主程序集；Java 中 `t7rekindle` 也是完整名称。
- **v1 预览协议**：`T7PR1`、`T7PA1` 及其签名内容；`T7-Rekindle/preview/v1` 身份摘要前缀，以及 `preview-access/v1`、`preview-authority/v1` 的完整产品名 DPAPI 保护字符串。迁移技术命名不重新签发凭据、不轮换密钥。
- **原游戏与旧文件处理**：原游戏名称、协议、资源路径及数据不更名；`T7.Core.dll`、`T7.NativeBridge.dll` 仅用于旧库清理、禁止打包及相应测试；既有禁止分发名单中的 `t7.server.exe` 等名称继续保留。
- **规范与迁移验证**：命名对照、兼容格式的负例和合成旧包测试可使用对应旧名称，不作为新接口或新产品标识。

名称变更须同时更新定义与引用，包括原生导出与 C# 绑定、Python 错误与 UI 识别、XAML、程序集资源、项目引用、构建脚本、CI、测试和当前文档。按语义处理，不替换哈希、凭据、二进制数据或真实历史发布内容。保留项变更、新增例外及旧接口兼容层须先说明影响并取得确认；完成后检查命名和跨层引用的一致性。

NativeBridge 提供 `T7R.NativeBridge.dll`，会话导出为 `t7r_native_*`；战斗宿主使用 `t7r_battle_ffi.dll` 的 `t7r_gateway_*` 接口，旧 C++／Python `t7r_battle_host_*` 导出已移除。结构布局仍为 ABI 1；Desktop、Core、Battle、NativeBridge 与 Rust DLL 必须整包升级，不兼容旧开发 DLL。主程序仍为 `T7-Rekindle.exe`。新包拒绝混入旧库；安装及既有设置处理成功后，仅清理安装根目录中的上述两个旧 DLL，失败返回 `20` 并停止自动启动，详见[运行契约](runtime-contracts.md#回退设置契约)。

旧构建环境迁移时，将变量名改为 `T7R_*`；已存在的 `T7R_SERVER_BASE_URL` 保持。首次使用新 CI 前，由维护者在 Actions 配置 `T7R_PREVIEW_ACCESS_PUBLIC_KEY`，沿用原公钥值；旧 `T7_*` 构建变量不再读取，不因此变更远端发布对象或凭据格式。

## 1. 准备工具链

| 组件 | 要求与获取方式 |
| --- | --- |
| 系统 | Windows 10 2004（build 19041）及以上版本 / Windows 11 x64 |
| 原生工具 | [VS 2022 Build Tools](https://learn.microsoft.com/en-us/visualstudio/releases/2022/release-history)，安装“使用 C++ 的桌面开发”、v143 工具集和 Windows SDK |
| Python（开发工具） | CPython 3.14.4 AMD64，用于构建脚本、导出与测试；仅历史原生对照测试需要头文件和导入库 |
| Rust | `src/BattleRust/rust-toolchain.toml` 固定工具链，目标 `x86_64-pc-windows-msvc` |
| 托管工具 | [.NET SDK 8.0.425](https://dotnet.microsoft.com/en-us/download/dotnet/8.0)，以及 [.NET Framework 4.8 Developer Pack](https://dotnet.microsoft.com/en-us/download/dotnet-framework/net48) |
| 测试工具 | `pytest==9.1.1`，安装命令见下方 |
| 安装器工具 | [Inno Setup 7.1.0](https://jrsoftware.org/isdl.php)，仅制作安装器时需要 |

注意区分以下组件：

- 构建脚本查找 **VS 2022 的 MSBuild**。只安装 .NET SDK 或其他代际的 Visual Studio，不满足该入口要求。
- 产品原生桥不链接 CPython；Python 只用于开发工具和 `tests/reference/` 对照测试。产品打包不需要 embeddable ZIP。
- .NET Framework Runtime 用于运行程序，Developer/Targeting Pack 用于编译；.NET SDK 是另一个独立前置。
- 只构建 managed 时仍需 VS 2022 MSBuild、.NET SDK 和 net48 Targeting Pack，但不要求 C++ workload 或 Python 开发头文件。

安装后重新打开 PowerShell，检查当前工具：

```powershell
python --version
python -c "import struct, sys; print(sys.executable); print('bits:', struct.calcsize('P') * 8)"
dotnet --list-sdks
python -m pip install pytest==9.1.1
```

Python 应显示 3.14.4、64 位；`dotnet --list-sdks` 应列出 SDK 8.0.425，而不只是 Runtime。仓库不要求预先存在构建产物或维护者的本地工具目录。

## 2. 构建与测试

依次执行，每条命令退出码为 0 后再继续：

```powershell
python scripts/build.py --project all
python -m pytest -q
& .\artifacts\bin\T7R.ManagedHarness\x64\Release\net48\T7R.ManagedHarness.exe
python scripts/integration_test.py --report artifacts/test-results/integration.json
```

| 入口 | 构建或验证范围 |
| --- | --- |
| `build.py --project all` | 全部原生、托管项目，并运行三组既有原生测试及战斗模块测试 |
| `build.py --project native` | 仅 NativeBridge，不运行测试 |
| `build.py --project native-tests` | NativeBridge、三组原生测试，并自动运行原生测试 |
| `build.py --project managed` | Core、WPF、战斗宿主与托管 harness，不自动运行 harness |
| `build.py --project battle` | NativeBridge、BattleServer 与 BattleHarness，不构建 WPF、不运行测试 |
| `build.py --project battle-tests` | Rust 战斗宿主与托管模块测试，使用独立合成资源，不启动游戏 |
| `python -m pytest -q` | Python 对照、构建、打包和静态界面契约测试 |
| `T7R.ManagedHarness.exe` | 设置、ViewModel、目录选择、主题、布局及生命周期契约 |
| `T7R.ManagedHarness.exe --native-contract-tests PACKAGE_ROOT` | 对完整包验证 C# 原生绑定、会话清理及仅有旧 DLL 时的加载失败，不启动游戏 |
| `integration_test.py` | 验证测试侧嵌入式 Python 参考实现、三通道及模拟客户端生命周期，不作为 Rust 产品验收 |

托管默认套件覆盖 Light、Dark、HighContrast 三种主题的完整尺寸／DPI 矩阵，并按测试组、主题输出开始状态、结果及毫秒耗时。命令执行器应为整个托管测试进程预留至少 **300 秒**；CI 对该步骤设置 5 分钟预算。整体执行预算与单用例的等待上限独立。

脚本默认构建 Release/x64，可用 `--configuration Debug` 构建 Debug/x64；省略参数与显式指定 `--configuration Release` 等效。该配置同时用于原生、托管项目及自动运行的原生测试，Debug/Release 产物分别存放。托管和原生编译将警告按错误处理，CI 也检查 Inno 编译警告；Python 测试使用 pytest 默认警告处理。

```powershell
python scripts/build.py --project all --configuration Debug
python scripts/build.py --project managed --configuration Debug
```

第二条命令构建托管项目及 Rust DLL，不生成 NativeBridge；需先生成相同配置的 NativeBridge。两种配置都复制 Rust DLL 和静态新闻，不复制 Python。可传 `--artifacts artifacts/runtime-check` 隔离本次产物；正式打包使用 Release。

合成集成测试也支持 `--artifacts` 和 `--configuration`，须与构建命令一致，避免测试旧产物：

```powershell
python scripts/integration_test.py --artifacts artifacts/runtime-check --configuration Debug --report artifacts/runtime-check/test-results/integration.json
```

### 服务端上下文定向验证

Rust 迁移的独立构建、回放基线及当前完成边界见 [Rust 战斗运行时迁移](planning/rust-runtime-migration.md)。当前产品默认且唯一使用 Rust，Python 只保留开发对照。共享战局和性能专项仍按迁移文档验收。

`build.py --project rust-gateway-tests --configuration Debug` 构建测试专用原生 DLL，并运行 C# 平台后端、Rust 网关与原协议 socket 的跨语言验证；不启动原游戏。此入口需要 Rust、VS 2022 C++ 与现有 .NET 工具链，不需要嵌入式 Python 开发头文件。

模块回归覆盖 `test_native_dispatch.py`、`test_defence_diagnostics.py`、`test_business_runtime.py`、`test_battle_actions.py`、`test_battle_buffer.py`、`test_battle_recovery.py`、`test_contact_journal.py` 和近战／电脑动作测试。原生宿主提供 `T7R.RuntimeTests.exe --server-context PACKAGE_ROOT`，使用构建脚本生成的合成运行目录，验证缓存身份、接触隔离、版本失效、异常提交、热更新／回滚、引用释放及事件顺序，不启动游戏客户端。

20 角色合成性能对照入口：

```powershell
python tests/python/benchmark_native_dispatch.py --output artifacts/test-results/dispatch-benchmark.json
```

默认比较当前序列化入口与对象入口，各预热 100 轮、测量 1000 轮，交替顺序重复 3 次；可使用 `--baseline-ref COMMIT` 从本仓库指定 Git revision 读取旧宿主作基线。报告包括 P50、P95、最大值和输入规模，不设置依赖机器速度的单元测试断言。此基准只测 Python 桥接及业务，不包含 Native 对象构建、队列、网络与渲染；实战需在同机、同场景和相同日志配置下，分别采集至少 100 个四方向均覆盖的完整防御样本，比较入队等待、准备至保持下发间隔和接触投递年龄。

### QQ 账号服务

构建前设置 `$env:T7R_SERVER_BASE_URL = 'https://HOST'`，地址作为 `AuthBaseUrl` 嵌入启动器，运行时不提供地址编辑入口。CI 使用同名 GitHub Actions 仓库变量 `T7R_SERVER_BASE_URL`。
Debug 构建额外允许本机回环 HTTP（如 `localhost`、`127.0.0.1`、`[::1]`），Release 始终只接受 HTTPS。
本地调试示例：

```powershell
$env:T7R_SERVER_BASE_URL = 'http://localhost:8080'
python scripts/build.py --project managed --configuration Debug
```

地址只填写服务端根地址，不追加 `/api/v1/auth`；修改后需要重新生成启动器。
首页公告也复用此服务地址，但匿名访问 `/api/v1/announcements/latest`，不依赖账号登录。启动及返回首页时获取，两次请求至少间隔 60 秒，单次超时 8 秒；无公告或请求失败时使用内置公告，不写远端公告磁盘缓存。后台发布流程见[公告管理](../server/README.md#公告管理)。
未配置时禁用账号登录并使用内置公告，本地游戏功能不受影响。QQ 登录使用 WebView2 Evergreen Runtime；安装器和登录窗口均提供缺失时的官方安装入口。
SDK、WPF 程序集和 x64 `WebView2Loader.dll` 随产品目录分发，打包检查将验证这些文件。

账号定向测试：`T7R.ManagedHarness.exe --account-tests`。服务端迁移、接口、SSE 代理设置及 QQ 实机验证见 [认证服务说明](../server/README.md#qq-扫码登录)。

公告定向测试：`T7R.ManagedHarness.exe --announcement-tests artifacts/ui-announcements`，覆盖匿名接口、刷新节流、关闭取消、离线兜底及三种主题；省略输出目录时仅执行断言。

预览授权定向测试：`T7R.ManagedHarness.exe --preview-access-tests`，覆盖签名协议、Windows 用户绑定、存量迁移、到期收尾和 PowerShell 5.1 签发互通；完整 UI 测试还覆盖授权弹窗三种主题。发布前运行 `--preview-key-check` 验证编译公钥；配置及管理员脚本见[预览渠道离线授权](preview-access.md)。

日志定向测试：`T7R.ManagedHarness.exe --log-tests artifacts/ui-log-levels`，覆盖等级过滤、中文显示、配置兼容、保存失败回滚及三种主题；省略输出目录时仅执行断言。

主题定向测试：`T7R.ManagedHarness.exe --theme-tests artifacts/ui-theme-switch`，覆盖浅色／深色即时切换、偏好持久化、保存失败恢复选择和系统高对比度优先；省略输出目录时仅执行断言。

合成集成测试依赖 `native-tests` 的输出，并生成 JSON 报告。`realClient=false` 表示未启动真实客户端；它验证所覆盖的合成流程，不代替进图、移动、AI 或完整对局验收。真实客户端与界面验收项目见[实施计划](planning/implementation-plan.md)。

界面截图可通过 `T7R.ManagedHarness.exe --render-ui artifacts/ui-v11-final` 生成；`--native-ui artifacts/ui-v11-final` 另行检查当前显示器的真实窗口边界、弹窗位置与托盘恢复。两者均使用测试数据，不启动真实游戏或修改自启动项。覆盖范围、截图索引及待实机验收项见 [UI 1.1 实施与验收](planning/ui-handoff-v1.1.md)。

客户端异常退出后，“本地服务未运行”表示会话清理后的状态，不是端口分配失败的诊断。应查看日志中的首个错误；未处理的客户端异常会记录模块名、RVA 和线程寄存器，并从最多 32 个栈槽中记录指向映像的地址候选。这些候选不是完整展开的调用栈，也不包含原始栈正文。诊断采集失败另行记录，不覆盖原始异常；分享日志前仍需检查个人信息。

声音缺失时，先将“设置 → 启动器设置 → 输出日志等级”改为“调试”，再启动游戏复现；排查结束后可恢复“信息”。`Audio diagnostics` 记录包含三个默认播放角色的设备名称、`deviceFormat`、`mixFormat`、`sysFx` 标志及音频服务状态；查询失败保留 `unknown` 和错误码，不影响启动。客户端加载 Wwise、XAudio2、AudioDevProps2 或 Nahimic 模块时，`Audio module observed` 补充文件版本和进程角色，不记录完整模块路径。`Audio settings snapshot` 仅在设置值或状态变化及应用回读时输出；同步超时补充命令、等待时长和最后设置状态。

以“调试”等级启动时，还会在客户端入口、开场动画前安装 `Audio playback trace` 内存观测包装，覆盖客户端导入的 Wwise `Init`、同步 `LoadBank`／`UnloadBank`、名称／ID 两种 `PostEvent`、`GetIDFromString` 和 `SetRTPCValue`。`Audio playback call` 记录调用线程、起止 tick、事件名称或 ID、原始返回值及成功加载的 Bank ID；重点对照 `Vox_System.bnk`、`Media_Vox_System.bnk`。`Audio announcement` 实时记录连杀通知的服务端时间、击杀者、本机玩家标志、次数和消息 ID，不受 Journal 最近 1000 行保留限制；`stage=queued` 不表示客户端已接收或已播放。

观测包装保留原调用参数、回调、返回值和 Bank 输出，不播放额外音频、不更改设备、服务、音效设置或磁盘客户端。客户端仅向有界队列写入记录，调试线程独立读取，因此声音设置邮箱超时不会停止专项采集；队列竞争、满队列或采集故障会明确输出 `coverage incomplete`，缺少记录时须先检查覆盖是否完整。该队列使用不等待的竞争策略，避免诊断等待阻塞音频线程。

覆盖范围仅为已支持客户端的上述导入调用，不包含 DLL 内部调用或其他 CG 播放链路。已有 Wwise 调试输出会标记为 `Audio engine debug output`，不强制启用或替换引擎 Monitor 回调；没有调试输出不代表没有内部错误。`PostEvent` 的非零播放 ID 仅代表请求被接受，不证明实际出声。默认端点也不是已确认的游戏实际路由；`sysFx` 不代表所有 APO 的运行状态。模块加载、采样率不同或设置邮箱超时均不单独证明声音缺失的原因。

请先切换“调试”等级并重新启动游戏，保留开场动画，再由本机玩家触发一次连杀；分别记录 CG、提示音、人声和 BGM 是否有声及发生时间。通过“查看详情 → 打开日志目录”反馈该次 `native.log`、`desktop.log`（跨日时附对应归档）。定向验证：`T7R.NativeTests.exe --audio-playback-tests`、`T7R.RuntimeTests.exe --audio-notifications` 与 `python -m pytest -q tests/python/test_kill_notifications.py`。

### 在 Visual Studio 中构建

打开解决方案，使用 x64 的 Debug 或 Release 配置。先构建 `T7R.NativeBridge`，再构建 Desktop；Desktop／BattleServer 通过 `RustRuntime.targets` 调用锁定的 Cargo 构建并复制 Rust DLL。产品不使用 `PythonHome`。

### 开发对照与运行资产

Python 历史对照代码位于 `tests/reference/Business`，仅用于回放、资源导出和黄金样本测试；原生 Python 对照宿主位于 `tests/reference/native-server`。修改这些文件不会改变产品运行逻辑。

生产逻辑修改位置为 `src/BattleRust`。静态新闻位于 `src/Desktop/Resources/News`，通过 `RuntimeAssets.targets` 复制到产品 `news/`。旧构建目录可能有遗留 Python 文件；打包会拒绝这些文件，请使用新的独立产物目录构建，不复用旧产品包。

### 依赖锁定

仓库通过根目录的 `global.json` 固定 .NET SDK 8.0.425，CI 按同一文件安装 SDK，避免默认 RuntimeIdentifier 随 SDK 版本变化而与 lock file 不一致。升级 SDK 时需同步 `Managed.Common.props` 和便携工具链脚本，并验证依赖锁定与输出路径。

托管构建通过 `RestoreLockedMode=true` 使用项目级 `packages.lock.json`。正常构建不更新依赖版本。

经讨论更新依赖后，在 VS 2022 开发者 PowerShell 中对受影响的项目运行 MSBuild Restore，显式传入 `/p:RestoreLockedMode=false /p:RestoreForceEvaluate=true`，审查 lock file 的版本、内容哈希和许可，再恢复正常锁定构建。不要通过长期关闭锁定还原来忽略不一致。

## 3. 生成并运行完整产品包

先完成原生、Rust 和托管 Release 构建及相应验证：

```powershell
python scripts/build.py --project native --artifacts artifacts/runtime-check
python scripts/build.py --project managed --artifacts artifacts/runtime-check
python scripts/package.py --artifacts artifacts/runtime-check --output artifacts/runtime-check/package --release
python scripts/package.py --output artifacts/runtime-check/package --verify --release
```

`--release` 要求完整 Rust 产品文件与许可证，不表示实机验收或正式发行已完成。产品包括启动器、NativeBridge、`T7R.Battle.dll`、`t7r_battle_ffi.dll`、静态新闻、托管依赖与许可；没有 Python／Business 运行目录。

打包检查 DLL 副本一致性、必需文件、manifest 和逐文件哈希，拒绝混入 Python DLL、扩展、脚本、字节码及旧 Business 目录。`--python-archive` 已删除，无旧运行时回退路径。

已有输出目录会使打包停止。重建前先确认并自行保留旧产物；也可指定新的输出目录，但下面的安装器定义固定读取 `artifacts/package/`。

## 4. 制作安装器和便携 ZIP

将 Inno Setup 7.1.0 的 `ISCC.exe` 所在目录加入当前终端 PATH，然后执行：

```powershell
ISCC.exe --define=MyAppVersion=0.1.0 installer/T7-Rekindle.iss
New-Item -ItemType Directory -Force dist | Out-Null
Compress-Archive -Path artifacts/package/* -DestinationPath dist/T7-Rekindle-windows-x64.zip
```

输出为：

- `dist/T7-Rekindle-Setup.exe`：按用户安装，检测 Windows/CPU 和 .NET Framework，提供简体中文目录选择与完成页。
- `dist/T7-Rekindle-windows-x64.zip`：完整产品目录的便携压缩包。

使用安装版和便携版的方法见[使用说明](requirements.md#运行环境与程序包)。两者均不包含游戏客户端，安装与卸载不处理用户客户端，也不删除用户设置或诊断资料。

### 版本与关于页

本地启动器默认版本来自 `src/Desktop/T7R.Desktop.csproj`；正式 tag 构建通过 `T7R_RELEASE_VERSION` 将同一数字版本写入程序集和安装器。本地单独设置 `MyAppVersion` 仍只影响安装器。内置版本日志由维护者更新。

CI 通过 `GITHUB_SHA` 写入 Git 提交元数据。项目文件同时生成项目名称、介绍及仓库、发布页、CI 构建和 Issues 的地址元数据。

`prepare-build` 同时生成构建渠道、数字版本及可发布预览的 CI 身份，分别写入程序集和程序包 `manifest.json` 的 `build` 对象。正式 tag 标记为 `stable`；默认分支的 push 或手动 CI 标记为可比较的 `preview`，使用 `runId`、`runNumber`、`runAttempt`、`commitHash`。其他分支及本地构建没有可比较的预览序号。用户订阅渠道与构建身份独立，首次默认正式，偏好统一保存在 `settings.json` 的 `updateChannel` 字段；旧渠道文件自动迁移，规则见[设置格式](runtime-contracts.md#设置格式)。

程序启动时、运行中每 30 分钟，以及游戏结束并完成会话清理后自动检查更新，也支持用户手动点击。所有触发共用同一个检查命令，已有检查时跳过重叠触发，不排队；手动检查和游戏结束检查不重置定时周期。检查结果不自动打开弹窗、下载或安装。

正式渠道优先匿名请求 R2 稳定清单，失败时回退 GitHub Releases `latest` API；预览渠道仅请求 R2 预览清单。每个检查请求超时为 10 秒。正式版内部比较四段数字，忽略 `+构建标识`；预览内部比较 `(runNumber, runAttempt)`，同一提交重新构建也可更新。跨渠道始终提供目标渠道最新版本，不比较版本高低。用户可在更新页下载、暂停、继续或取消；大小及 SHA-256 校验成功后，再次确认安装。安装程序等待启动器退出后覆盖当前目录，手动安装仍使用完整向导。未配置镜像地址时，正式渠道使用 GitHub，预览渠道明确提示配置缺失。配置、协议和验收见 [R2 发布镜像](release-mirror.md)。

## 5. CI 产物与正式发布

[CI 工作流](../.github/workflows/ci.yml)在分支 push、tag push、PR 和手动运行时执行构建、测试、包校验及安装器编译。成功的构建上传产品目录、安装器、便携 ZIP 和测试报告，当前保留期为 14 天。

默认分支的 push 和手动 CI 成功后另行将安装器及 ZIP 发布到 R2 预览入口，不创建 GitHub 预发布。发布作业按产物 ID 下载同一构建文件，并读取 ZIP 内冻结的构建身份；仅重跑发布不产生新构建身份。预览和正式镜像共用现有 R2 配置，凭据只提供给发布作业。

GitHub Release 的文件名为 `T7-Rekindle-{tag}-Setup.exe` 和 `T7-Rekindle-windows-x64-{tag}.zip`，`{tag}` 保留完整 tag（含 `v` 前缀和构建标识）。例如 tag `v1.2.3` 对应 `T7-Rekindle-v1.2.3-Setup.exe` 和 `T7-Rekindle-windows-x64-v1.2.3.zip`。普通 CI 产物和本地打包仍使用上文不含 tag 的名称。

**当前工作流在符合正式版本格式的 tag push 构建通过后自动创建 GitHub Release，并同步 R2 镜像。** 正式 tag 构建前需配置镜像公开域名，上传前需配置桶和凭据；镜像失败不会删除已发布的 GitHub Release，可单独补传。该流程没有集成应用签名、人工审批、真实界面或客户端验收步骤。CI 成功和 Release 页面存在都不等于已经满足正式发行标准；使用者应查看该版本的验收记录和已知限制。

正式发布前的签名、安装/卸载、真实客户端和版本信息检查，由维护者按[发行验收清单](planning/implementation-plan.md#发行验收)执行并记录。尚未完成的事项在任务清单保留，不描述成已生效的自动门禁。

## 6. 常见构建问题

| 现象 | 处理方法 |
| --- | --- |
| 找不到 VS 2022 MSBuild | 确认安装的是 VS 2022，MSBuild 组件齐全；原生构建还要求 v143 C++ workload |
| Python 版本或开发文件检查失败 | 检查当前 `python` 路径、3.14.4/64 位版本及头文件和导入库；不要使用 embeddable ZIP 作为开发安装 |
| 历史原生对照测试报 `Python.h` 缺失 | 仅对照测试需传入 `/p:PythonHome=PYTHON_HOME`；生产工程不依赖 CPython |
| 原生阶段成功，随后提示缺少 SDK 或 Targeting Pack | `--project all` 先构建和测试原生部分，再检查托管前置；补齐 .NET SDK 与 net48 Developer Pack 后重试 |
| NuGet 锁定还原失败 | 检查源访问及项目与 lock file 是否匹配；不要默认解除锁定 |
| Debug 启动时报加载 NativeBridge 失败 | 先完成 Debug/x64 原生构建，再重新生成 Desktop 和 Rust DLL；完整产品仍按 Release 流程打包 |
| 修改测试对照 Python 后产品行为不变 | 产品逻辑在 `src/BattleRust`，对照代码不进入构建产物 |
| 打包输出已存在 | 使用已确认的新目录或先保留旧产物；安装器输入路径须与产品目录一致 |

客户端目录、图形预检、名称及退出错误见[使用问题排查](requirements.md#问题排查与反馈)。反馈构建问题时提供命令、工具版本、提交号及相关错误片段，去除个人路径等信息。

## 附：可选的便携托管工具链

系统 SDK 和 Targeting Pack 可用时，无需此方案。项目配置和构建脚本支持以下固定布局：

```text
.local/toolchains/
  dotnet-8.0.425/                 # 对应 .NET SDK 的完整 Windows x64 解压目录
  net48-reference-assemblies/     # Microsoft.NETFramework.ReferenceAssemblies.net48 1.0.3
    build/.NETFramework/v4.8/
```

SDK 从 [.NET 下载页](https://dotnet.microsoft.com/en-us/download/dotnet/8.0)获取，引用程序集来自 [NuGet 包](https://www.nuget.org/packages/Microsoft.NETFramework.ReferenceAssemblies.net48/1.0.3)。准备好目录后，VS、MSBuild 和 `scripts/build.py` 自动使用便携工具链，不需要设置系统环境变量。便携 SDK 默认使用 `.local/toolchains/nuget-packages/` 缓存；已有的 `RestorePackagesPath` 或 `NUGET_PACKAGES` 配置优先。

需要在终端直接运行 `dotnet` 命令时，仍可加载：

```powershell
. .\scripts\Use-ManagedTools.ps1
```

首个点号后有空格。脚本只设置当前会话，供 `dotnet` CLI 使用；新开终端后如需该 CLI，应重新加载。它不下载组件，也不替代 VS 2022 MSBuild。自动检测和此脚本均使用上述固定布局。
