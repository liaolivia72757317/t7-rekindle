# 开发与交付

本文说明如何在一台新准备的 Windows 机器上从源码构建、测试和制作产品包。仅使用启动器时，阅读[运行环境与使用说明](requirements.md)即可，无需安装开发工具。

下面的命令在包含 `T7-Rekindle.sln` 的仓库根目录，用 PowerShell 执行。构建脚本检查前置组件，不自动安装系统工具；首次依赖还原需要访问 NuGet，或已有完整的依赖缓存。

## 1. 准备工具链

| 组件 | 要求与获取方式 |
| --- | --- |
| 系统 | Windows 10 22H2 / Windows 11 x64 |
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

脚本固定构建 Release/x64；原生 Debug/Release 输出分开，Visual Studio 可另行构建 Debug。托管和原生编译将警告按错误处理，CI 也检查 Inno 编译警告；Python 测试使用 pytest 默认警告处理。

合成集成测试依赖 `native-tests` 的输出，并生成 JSON 报告。`realClient=false` 表示未启动真实客户端；它验证所覆盖的合成流程，不代替进图、移动、AI 或完整对局验收。真实客户端与界面验收项目见[实施计划](planning/implementation-plan.md)。

### 依赖锁定

仓库通过根目录的 `global.json` 固定 .NET SDK 8.0.425，CI 按同一文件安装 SDK，避免默认 RuntimeIdentifier 随 SDK 版本变化而与 lock file 不一致。升级 SDK 时需同步便携工具链脚本，并验证依赖锁定与输出路径。

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

启动器版本来自 `src/Desktop/T7.Desktop.csproj`；`MyAppVersion` 只设置安装器版本，不会自动更新程序集版本或内置版本日志。发布时需同步核对。

CI 通过 `GITHUB_SHA` 写入 Git 提交元数据。关于页读取 `RepositoryUrl`、`DownloadUrl` 的接口已存在，但当前项目文件未生成这三项元数据，因此默认显示地址缺失状态。更新检查仍是内置示例，不是发行版本查询；完善这些发布信息属于[交付待办](planning/task-list.md)。

## 5. CI 产物与正式发布

[CI 工作流](../.github/workflows/ci.yml)在分支 push、tag push、PR 和手动运行时执行构建、测试、包校验及安装器编译。成功的构建上传产品目录、安装器、便携 ZIP 和测试报告，当前保留期为 14 天。

**当前工作流在任意 tag push 的构建通过后自动创建 GitHub Release。** 它没有集成应用签名、人工审批、真实界面或客户端验收步骤。CI 成功和 Release 页面存在都不等于已经满足正式发行标准；使用者应查看该版本的验收记录和已知限制。

正式发布前的签名、安装/卸载、真实客户端和版本信息检查，由维护者按[发行验收清单](planning/implementation-plan.md#发行验收)执行并记录。尚未完成的事项在任务清单保留，不描述成已生效的自动门禁。

## 6. 常见构建问题

| 现象 | 处理方法 |
| --- | --- |
| 找不到 VS 2022 MSBuild | 确认安装的是 VS 2022，MSBuild 组件齐全；原生构建还要求 v143 C++ workload |
| Python 版本或开发文件检查失败 | 检查当前 `python` 路径、3.14.4/64 位版本及头文件和导入库；不要使用 embeddable ZIP 作为开发安装 |
| 原生阶段成功，随后提示缺少 SDK 或 Targeting Pack | `--project all` 先构建和测试原生部分，再检查托管前置；补齐 .NET SDK 与 net48 Developer Pack 后重试 |
| NuGet 锁定还原失败 | 检查源访问及项目与 lock file 是否匹配；不要默认解除锁定 |
| 托管输出缺少 NativeBridge、Python 或 Business | 先完成原生构建，再构建 managed，最后打包；不要直接运行或手工拼接零散构建文件 |
| 打包输出已存在 | 使用已确认的新目录或先保留旧产物；安装器输入路径须与产品目录一致 |

客户端目录、图形预检、名称及退出错误见[使用问题排查](requirements.md#问题排查与反馈)。反馈构建问题时提供命令、工具版本、提交号及相关错误片段，去除个人路径等信息。

## 附：可选的便携托管工具链

系统 SDK 和 Targeting Pack 可用时，无需此方案。仓库的 `scripts/Use-ManagedTools.ps1` 支持以下固定布局：

```text
.local/toolchains/
  dotnet-8.0.425/                 # 对应 .NET SDK 的完整 Windows x64 解压目录
  net48-reference-assemblies/     # Microsoft.NETFramework.ReferenceAssemblies.net48 1.0.3
    build/.NETFramework/v4.8/
```

SDK 从 [.NET 下载页](https://dotnet.microsoft.com/en-us/download/dotnet/8.0)获取，引用程序集来自 [NuGet 包](https://www.nuget.org/packages/Microsoft.NETFramework.ReferenceAssemblies.net48/1.0.3)。准备好目录后，在执行构建的同一个 PowerShell 会话中加载：

```powershell
. .\scripts\Use-ManagedTools.ps1
```

首个点号后有空格。脚本检查文件并设置当前会话的 SDK、引用程序集和 NuGet 缓存路径，不下载组件，也不替代 VS 2022 MSBuild。新开终端需重新加载；其他 SDK 布局不适用此脚本的固定路径。
