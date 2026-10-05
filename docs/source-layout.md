# 目录与源码范围

## 仓库布局

```text
PROJECT_ROOT/
  README.md                    # 项目介绍、当前状态和快速开始
  AGENTS.md                    # 开发规范
  SECURITY.md                  # 安全问题报告与运行边界
  LICENSE
  THIRD-PARTY.txt               # 分发依赖与许可索引
  src/
    Desktop/                   # WPF 入口、Views、ViewModels、Services
      Resources/               # 主题、图片及应用内置文档
    Core/                      # 托管契约、设置和 ABI 布局
    Runtime/
      bridge/                  # C ABI 与 Session 生命周期
      core/                    # 原生公共类型、编码和协议 framing
      launcher/                # 客户端预检、运行时适配和进程清理
      server/                  # 三通道、Journal、PythonHost
    Business/
      runtime/                 # 业务版本加载、状态和结果校验
      scripts/                 # Python 业务状态机
        codec/                 # 业务使用的协议编解码
  tests/{cpp,managed,python}/
  scripts/                     # 构建、打包、集成测试与可选工具环境脚本
  docs/                        # 使用、开发、架构及运行契约
    planning/                  # 阶段目标、验收与待办
  installer/                   # Inno Setup 定义
  .github/workflows/           # CI 与 tag 发布工作流
  T7-Rekindle.sln
  Directory.Build.props
  Managed.Common.props
  Managed.Common.targets
  Native.Common.props
  pytest.ini
```

`Runtime/` 是原生模块，`Business/runtime/` 是 Python 业务宿主，产品包中的 `python/` 是解释器文件，三者职责不同。

## 产品包布局

打包脚本读取本次 Release 构建和 `src/Business/`，生成下面的独立运行目录：

```text
产品包/
  T7-Rekindle.exe
  T7-Rekindle.exe.config
  T7.Core.dll
  T7.NativeBridge.dll
  python314.dll
  python3.dll
  python/                      # Python 标准库及随附文件
  Business/                    # 来自 src/Business/
  NLog.config
  LICENSE
  THIRD-PARTY.txt
  manifest.json
  ...                          # 托管依赖及运行时支持 DLL
```

WPF 以可执行文件所在目录作为包根。整个目录一起部署；单独的 EXE 或托管编译输出不是完整运行包。产品包不包含源码目录、测试程序或游戏客户端。

`src/Desktop/Resources/CHANGELOG.md`、`THANKS.md` 和 `THIRD-PARTY-NOTICES.txt` 嵌入启动器，在“关于”页离线展示。它们不要求作为散落的 Markdown 文件放在程序旁边。

## 生成目录

| 路径 | 用途 |
| --- | --- |
| `artifacts/bin/<Project>/<Platform>/<Configuration>/net48/` | 托管构建输出 |
| `artifacts/obj/<Project>/` | 托管还原及中间文件 |
| `artifacts/native/bin/<Platform>/<Configuration>/` | 原生 DLL、测试程序和链接产物 |
| `artifacts/native/obj/<Project>/<Platform>/<Configuration>/` | 原生中间文件 |
| `artifacts/package/` | 默认产品包目录，也是安装器的输入 |
| `artifacts/test-results/` | 自动化测试和构建诊断报告 |
| `artifacts/tmp/` | 自动化测试生成的临时运行环境 |
| `dist/` | 安装器及便携 ZIP |
| `.local/` | 可选工具链、缓存和个人工作资料，不随源码提供 |

构建输出、临时资料和本地工具均由 `.gitignore` 排除。正常源码构建不依赖维护者已有的 `.local/` 内容；工具准备方式见[开发与交付](development.md)。

脚本根据自身位置定位仓库根，显式传入的相对输出路径则相对调用者的工作目录解析。文档中的命令统一从仓库根目录执行。

## 源码、用户数据与外部资源

公开源码只包含产品实现、合成测试、开发脚本和文档。原游戏客户端及资源由用户单独保存；抓包、转储、账号、票据和个人配置不进入仓库或产品包。

运行后的设置和诊断文件位于 `%LOCALAPPDATA%/T7-Rekindle`，与源码及安装目录分开。具体位置见[使用说明](requirements.md#设置与诊断资料)，保存格式与保留规则见[运行契约](runtime-contracts.md)。
