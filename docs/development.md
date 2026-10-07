# 开发与交付

本文说明如何在一台新准备的 Windows 机器上从源码构建、测试和制作产品包。仅使用启动器时，阅读[运行环境与使用说明](requirements.md)即可，无需安装开发工具。

下面的命令在包含 `T7-Rekindle.sln` 的仓库根目录，用 PowerShell 执行。构建脚本检查前置组件，不自动安装系统工具；首次依赖还原需要访问 NuGet，或已有完整的依赖缓存。

## 1. 准备工具链

| 组件 | 要求与获取方式 |
| --- | --- |
| 系统 | Windows 10 2004（build 19041）及以上版本 / Windows 11 x64 |
| 原生工具 | [VS 2022 Build Tools](https://learn.microsoft.com/en-us/visualstudio/releases/2022/release-history)，安装“使用 C++ 的桌面开发”、v143 工具集和 Windows SDK |
| Python | [CPython 3.14.4](https://www.python.org/downloads/release/python-3144/) 的 Windows installer (64-bit)，包含头文件、导入库、运行时 DLL 和标准库 |
| 托管工具 | [.NET SDK 8.0.425](https://dotnet.microsoft.com/en-us/download/dotnet/8.0)，以及 [.NET Framework 4.8 Developer Pack](https://dotnet.microsoft.com/en-us/download/dotnet-framework/net48) |
| 测试工具 | `pytest==9.1.1`，安装命令见下方 |
| 安装器工具 | [Inno Setup 7.1.0](https://jrsoftware.org/isdl.php)，仅制作安装器时需要 |

注意区分以下组件：

- 构建脚本查找 **VS 2022 的 MSBuild**。只安装 .NET SDK 或其他代际的 Visual Studio，不满足该入口要求。
- 原生构建严格要求当前 `python` 为 CPython **3.14.4 AMD64**；不会自动切换解释器。开发安装中需有 `Include/Python.h`、`libs/python314.lib` 和 `python314.dll`。
- Python 的 embeddable ZIP 用于打包，不替代开发安装。
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
& .\artifacts\bin\T7.ManagedHarness\x64\Release\net48\T7.ManagedHarness.exe
python scripts/integration_test.py --report artifacts/test-results/integration.json
```

| 入口 | 构建或验证范围 |
| --- | --- |
| `build.py --project all` | 全部原生、托管项目，并自动运行三组原生测试 |
| `build.py --project native` | 仅 NativeBridge，不运行测试 |
| `build.py --project native-tests` | NativeBridge、三组原生测试，并自动运行原生测试 |
| `build.py --project managed` | Core、WPF、托管 harness，不自动运行 harness |
| `python -m pytest -q` | Python 业务、构建、打包和静态界面契约测试 |
| `T7.ManagedHarness.exe` | 设置、ViewModel、目录选择、主题、布局及生命周期契约 |
| `integration_test.py` | 使用独立原生宿主验证嵌入式 Python、三通道及模拟客户端生命周期 |

脚本默认构建 Release/x64，可用 `--configuration Debug` 构建 Debug/x64；省略参数与显式指定 `--configuration Release` 等效。该配置同时用于原生、托管项目及自动运行的原生测试，Debug/Release 产物分别存放。托管和原生编译将警告按错误处理，CI 也检查 Inno 编译警告；Python 测试使用 pytest 默认警告处理。

```powershell
python scripts/build.py --project all --configuration Debug
python scripts/build.py --project managed --configuration Debug
```

第二条命令仅构建托管项目；它不生成 NativeBridge 或准备 Python 运行时。正式打包和 `integration_test.py` 仍使用 Release 产物，Debug 构建不替代发行前的 Release 构建与验证。

合成集成测试依赖 `native-tests` 的输出，并生成 JSON 报告。`realClient=false` 表示未启动真实客户端；它验证所覆盖的合成流程，不代替进图、移动、AI 或完整对局验收。真实客户端与界面验收项目见[实施计划](planning/implementation-plan.md)。

界面截图可通过 `T7.ManagedHarness.exe --render-ui artifacts/ui-v11-final` 生成；`--native-ui artifacts/ui-v11-final` 另行检查当前显示器的真实窗口边界、弹窗位置与托盘恢复。两者均使用测试数据，不启动真实游戏或修改自启动项。覆盖范围、截图索引及待实机验收项见 [UI 1.1 实施与验收](planning/ui-handoff-v1.1.md)。

客户端异常退出后，“本地服务未运行”表示会话清理后的状态，不是端口分配失败的诊断。应查看日志中的首个错误；未处理的客户端异常会记录模块名、RVA 和线程寄存器，并从最多 32 个栈槽中记录指向映像的地址候选。这些候选不是完整展开的调用栈，也不包含原始栈正文。诊断采集失败另行记录，不覆盖原始异常；分享日志前仍需检查个人信息。

### 在 Visual Studio 中构建

使用 VS 2022 IDE 时，先准备上述工具链。Python 开发安装路径只需配置一次：

1. 执行 `python -c "import sys; print(sys.base_prefix)"`，确认该目录属于 CPython 3.14.4 AMD64，包含 `Include/Python.h` 和 `libs/python314.lib`。
2. 创建仓库根目录下的 `.local/Build.props`，将下面的 `PYTHON_HOME` 替换为该目录；已有文件时只补充属性，不覆盖其他设置。

```xml
<Project>
  <PropertyGroup>
    <PythonHome Condition="'$(PythonHome)' == ''">PYTHON_HOME</PythonHome>
  </PropertyGroup>
</Project>
```

`Directory.Build.props` 自动加载此本机配置，原生项目通过 `Native.Common.props` 使用头文件和导入库路径。环境变量 `PythonHome` 和 MSBuild `/p:PythonHome=...` 仍可覆盖示例中的默认值。`.local/` 已被 Git 忽略，不要将个人绝对路径写入共享项目文件。

托管项目通过 `Managed.Common.props` / `Managed.Common.targets` 成对导入 SDK：优先使用下方约定的便携 SDK，目录不存在时使用系统 SDK。便携 net48 引用程序集也会自动检测；显式设置的 `TargetFrameworkRootPath` 和 NuGet 缓存路径保持优先。WPF 支持仍由 Desktop 项目的 `UseWPF` 启用。

配置完成后直接打开 `T7-Rekindle.sln`，选择 Debug/x64 或 Release/x64 生成；已经打开的解决方案需重新加载，无需从环境脚本启动 VS。此配置只解决构建，运行完整启动器仍需按下一节打包。

### Python 脚本同步与生效

`src/Business` 是 Python 代码的唯一维护位置。Desktop 构建会将其中的 `.py` 文件复制到当前配置输出目录的 `Business`，包括 `runtime` 和 `scripts`，并保留相对目录。VS 与命令行共用这套规则；只修改 Python 也会触发输出更新，无需改动 C# 或手工复制。

VS 快速最新检查和 C# 增量编译保持启用；仅关闭 Desktop 的复制加速，确保同步时执行校验与清理。设计时构建不修改输出目录。

输出副本是构建产物：同名脚本会被源码覆盖，源中已删除或重命名的旧 `.py` 和遗留 `.pyc` 会被清理，其他文件不受此规则影响。同步失败会使构建失败；不要继续启动旧产物。首次采用自动同步前，应备份输出目录中尚未合入源码的手工改动。

运行时从启动器所在目录读取 `Business`，并在启动游戏会话时建立固定脚本快照。正确流程是：修改源码 → 保存 → 构建当前配置 → 结束旧游戏会话 → 重新启动游戏。仅重新匹配、只改输出副本或构建另一个配置，都不保证当前会话使用新代码。直接双击已有 EXE 不会触发构建同步。

排查时核对正在运行的 EXE 路径、其旁边的 `Business` 文件和现有诊断记录中的 `scriptVersion`。验证脚本行为应从实际输出目录加载，而不只运行源码测试。这里的同步不安装 Python，也不替代完整产品包的运行环境准备。

### 依赖锁定

仓库通过根目录的 `global.json` 固定 .NET SDK 8.0.425，CI 按同一文件安装 SDK，避免默认 RuntimeIdentifier 随 SDK 版本变化而与 lock file 不一致。升级 SDK 时需同步 `Managed.Common.props` 和便携工具链脚本，并验证依赖锁定与输出路径。

托管构建通过 `RestoreLockedMode=true` 使用项目级 `packages.lock.json`。正常构建不更新依赖版本。

经讨论更新依赖后，在 VS 2022 开发者 PowerShell 中对受影响的项目运行 MSBuild Restore，显式传入 `/p:RestoreLockedMode=false /p:RestoreForceEvaluate=true`，审查 lock file 的版本、内容哈希和许可，再恢复正常锁定构建。不要通过长期关闭锁定还原来忽略不一致。

## 3. 生成并运行完整产品包

先完成原生、托管 Release 构建及测试。另行下载官方 [CPython 3.14.4 AMD64 embeddable ZIP](https://www.python.org/ftp/python/3.14.4/python-3.14.4-embed-amd64.zip)，保持 ZIP 格式。

```powershell
$pythonArchive = Read-Host '请输入 CPython 3.14.4 AMD64 embeddable ZIP 的完整路径'
python scripts/package.py --output artifacts/package --python-archive "$pythonArchive" --release
python scripts/package.py --output artifacts/package --verify --release
& .\artifacts\package\T7-Rekindle.exe
```

`--release` 要求包内包含完整运行时、托管依赖和许可证，**不表示程序已签名或正式发行验收已完成**。产品目录包含启动器、NativeBridge、Python、Business、依赖 DLL 和许可文件；完整布局见[目录说明](source-layout.md#产品包布局)。

打包只读取 Desktop 的托管输出和原生 Release 输出，不读取测试工程。校验包含 NativeBridge 副本一致性、binding redirects、CPython 架构和版本、固定标准库内容、manifest 及逐文件哈希；校验过程不加载或执行输入 DLL。

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

本地启动器默认版本来自 `src/Desktop/T7.Desktop.csproj`；正式 tag 构建通过 `T7_RELEASE_VERSION` 将同一数字版本写入程序集和安装器。本地单独设置 `MyAppVersion` 仍只影响安装器。内置版本日志由维护者更新。

CI 通过 `GITHUB_SHA` 写入 Git 提交元数据。项目文件同时生成项目名称、介绍及仓库、发布页、CI 构建和 Issues 的地址元数据。

`prepare-build` 同时生成构建渠道、数字版本及可发布预览的 CI 身份，分别写入程序集和程序包 `manifest.json` 的 `build` 对象。正式 tag 标记为 `stable`；默认分支的 push 或手动 CI 标记为可比较的 `preview`，使用 `runId`、`runNumber`、`runAttempt`、`commitHash`。其他分支及本地构建没有可比较的预览序号。用户订阅渠道与构建身份独立，首次默认正式，偏好保存在 `update-settings.json`。

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
| VS 报 `Python.h` 缺失 | 按上方 VS 配置设置 `.local/Build.props` 中的 `PythonHome`，然后重新加载项目 |
| 原生阶段成功，随后提示缺少 SDK 或 Targeting Pack | `--project all` 先构建和测试原生部分，再检查托管前置；补齐 .NET SDK 与 net48 Developer Pack 后重试 |
| NuGet 锁定还原失败 | 检查源访问及项目与 lock file 是否匹配；不要默认解除锁定 |
| 托管输出缺少 NativeBridge 或 Python | 先完成相同配置的原生构建；完整产品仍按 Release 流程打包，不手工拼接零散运行时文件 |
| Python 修改后仍是旧行为 | 核对 EXE 所在配置，构建 Desktop 确认 Business 同步成功，再结束并重新启动游戏会话 |
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
