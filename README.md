![铁骑·重燃：雨幕古刹双雄对决](docs/assets/cover.png)

# 铁骑·重燃（T7-Rekindle）

[![CI](https://github.com/liaolivia72757317/t7-rekindle/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/liaolivia72757317/t7-rekindle/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue)](LICENSE)
[![Platform: Windows x64](https://img.shields.io/badge/Platform-Windows%20x64-0078D4)](#使用启动器)
[![Status: 开发中](https://img.shields.io/badge/Status-%E5%BC%80%E5%8F%91%E4%B8%AD-orange)](#当前状态)

铁骑·重燃是一个独立开源项目，目标是重新实现《刀锋铁骑》的服务端，让玩家通过原版客户端重回熟悉的战场。我们希望先完成本地人机对战，再逐步支持局域网联机。

项目不以营利为目的，欢迎开发者和玩家一起参与。

## 当前状态

**项目仍处于开发阶段，目前还不能进行完整的人机对战，局域网联机也尚未完成。**

项目通过 Windows x64 启动器运行，本地服务集成在启动器中。已有的代码实现主要包括：

| 部分 | 已有实现 |
| --- | --- |
| 启动器 | 界面、客户端目录检查、设置保存，以及启动、取消和结束游戏的流程。 |
| 本地服务 | 登录、逻辑和实例三条业务通道、协议编解码与认证握手。 |
| 登录与场景 | 本地身份、大厅与房间数据、阵营与选将、角色与敌方对象展示，以及准备与开局的消息流程。 |
| 移动与战斗 | 客户端运行时移动适配、基础方向与移动同步、战斗状态初始化，以及离开房间和返回大厅的消息流程。 |

这些是代码实现层面的进展，完整流程仍需要在真实客户端中验证。AI 行为、命中与伤害、死亡和胜负结算还需要继续开发。

## 路线图

### 第一阶段：完成人机对战

先在一张地图、一种固定模式下，实现完整且可以反复游玩的人机对战。

这一阶段会先完善启动、登录、大厅、建房、选将和进图流程，再完善镜头、转向、移动、跳跃、落地与碰撞。随后逐步加入 AI 的感知、移动、追击和攻击，完善战斗状态同步，以及命中、伤害、死亡、比分和胜负结算。

最终能够反复完成：

> 进入地图 → 人机战斗 → 结算 → 返回大厅 → 再开一局

### 第二阶段：支持局域网联机

人机对战稳定后，再让同一局域网内的玩家连接主机，创建或加入房间，完成身份、阵营和选将设置，准备后共同进入地图。

服务端将统一维护移动、AI、伤害和对局状态，让多个客户端看到一致的结果。同时完善玩家离开、断线处理、重复开局和网络延迟情况下的表现。

后续还计划补充本地角色、装备和进度保存、存档备份与恢复，以及更多地图和模式。具体安排以开发和测试进展为准。

## 快速开始

### 使用启动器

运行环境为 **Windows x64**，需要准备原版《刀锋铁骑》客户端。本项目不提供客户端或游戏资源。目前尚未正式发布，构建和打包方式见 [开发与交付](docs/development.md)。

1. 在“设置 → 基本设置”中点击“浏览”，选择包含 `Bin`、`Data` 和 `vfs` 的游戏根目录，然后点击“重新检查”。
2. 在同一设置页填写玩家名称，按 Enter 或移出输入框后自动保存。名称按 GBK 编码计算，最多 **31 字节**，不支持 emoji 和控制字符。
3. 检查通过后，回到首页点击“启动游戏”。首页展示已保存的配置；失败时通过“查看详情”复制诊断或打开日志目录。

**游戏运行时请保持启动器开启。** 本地服务运行在启动器内，关闭启动器或确认“结束游戏”都会结束本次游戏。建议先在游戏内正常退出，避免丢失未保存的进度。

“检查启动器更新”优先查询 R2 镜像，网络失败时尝试 GitHub 最新正式版本。跨版本升级时，更新页按版本从新到旧汇总本次升级涉及的日志；部分日志读取失败会提示缺失，不影响下载安装。点击“下载更新”后，更新页显示进度并支持暂停、继续和取消；校验完成后再由你点击“立即安装”。确认结束游戏后，启动器退出，安装程序直接覆盖当前启动器目录，仅显示安装进度，不再选择目录。预发布版和 CI 开发构建不参与更新比较。项目仓库和问题反馈入口位于“关于”页。固定下载入口与镜像配置见 [R2 发布镜像](docs/release-mirror.md)，更多操作说明见 [产品需求](docs/requirements.md)。

### 本地构建与测试

请先准备 Windows x64、VS 2022 C++ v143 / Windows SDK、CPython 3.14.4 AMD64、.NET SDK 和 net48 Targeting Pack。环境准备步骤见 [开发与交付](docs/development.md)；构建脚本只检查所需组件，不会自动安装。

在仓库根目录打开 PowerShell。如果使用便携托管工具链，请先按[便携工具链说明](docs/development.md#附可选的便携托管工具链)准备 `.local/toolchains/`。项目配置和构建脚本会自动使用该目录，不必每次加载环境脚本。直接在 VS 中构建时，按 [Visual Studio 配置](docs/development.md#在-visual-studio-中构建)一次性设置 Python 开发安装路径。

在同一个 PowerShell 会话中依次执行以下命令。每条命令成功后，再执行下一条。

```powershell
python -m pip install pytest==9.1.1
python scripts/build.py --project all
python -m pytest -q
& .\artifacts\bin\T7.ManagedHarness\x64\Release\net48\T7.ManagedHarness.exe
python scripts/integration_test.py --report artifacts/test-results/integration.json
```

完整构建会自动运行原生测试，其余测试需单独执行。合成集成报告中的 `realClient=false` 表示测试未启动真实游戏客户端。

### 本地构建后运行

`build.py` 只生成编译产物，`T7.ManagedHarness.exe` 是测试程序，不是启动器。运行前需打包，将启动器、NativeBridge、嵌入式 Python 和 Business 业务脚本组装到同一产品目录。不要直接运行 `artifacts/bin/` 中的启动器，也不要单独复制 EXE，否则可能因缺少依赖而出现“加载 NativeBridge 失败”。

完成上述构建与测试后，下载官方 [CPython 3.14.4 AMD64 embeddable ZIP](https://www.python.org/ftp/python/3.14.4/python-3.14.4-embed-amd64.zip)，保持 ZIP 格式；它与构建时使用的 Python 开发安装不同。

在仓库根目录的 PowerShell 中依次执行，每条命令成功后再执行下一条：

```powershell
$pythonArchive = Read-Host '请输入 CPython 3.14.4 AMD64 embeddable ZIP 的完整路径'
python scripts/package.py --output artifacts/package --python-archive "$pythonArchive" --release
python scripts/package.py --output artifacts/package --verify --release
& .\artifacts\package\T7-Rekindle.exe
```

打包要求输出目录尚未存在。如果 `artifacts/package/` 已存在，请先保留旧产物，或改用新的输出目录，并同步修改校验和启动命令中的路径。**重新执行 `build.py` 不会更新已有产品包；要运行最新构建，需重新打包。**

本地运行无需制作安装器。启动后按上方[使用启动器](#使用启动器)选择客户端目录、填写玩家名称并启动游戏，游戏运行期间保持启动器开启。更多打包说明见[开发与交付](docs/development.md#3-生成并运行完整产品包)。

## 参与开发与反馈

欢迎通过 Issue 反馈问题、补充复现步骤或提出建议，也欢迎提交 PR。

开始开发前，请阅读 [开发规范](AGENTS.md)、[产品需求](docs/requirements.md) 和 [架构说明](docs/architecture.md)，并结合 [任务清单](docs/planning/task-list.md) 了解当前的工作重点。涉及开发方向、新增生产依赖或公开 API 的改动，请先讨论。

提交 PR 时，请说明解决了什么问题、做了哪些修改，以及如何验证。新增功能或修复问题时，请补充相应测试并更新文档，保持代码和说明简洁、职责清晰。

提交内容应能在本项目内独立理解，不要包含其他本地工程的信息、真实凭据、内部地址或个人数据，也不要提交原游戏客户端及资源。

## 版权与许可

《刀锋铁骑》客户端及其内容的版权归腾讯游戏公司所有。本项目只开源自行实现的服务端、启动器及相关代码，不包含、也不分发原游戏客户端与资源。

项目仅适配原版客户端，不对原始客户端文件进行破解、解包或篡改。服务端地址和移动行为等适配通过运行时内存注入完成，磁盘上的客户端文件保持不变；客户端签名不匹配时，启动器会停止启动。

本项目代码采用 [MIT License](LICENSE)，使用、修改和分发遵循其条款。该许可证不适用于《刀锋铁骑》客户端及其资源；第三方依赖遵循各自的许可证，见 [第三方依赖说明](THIRD-PARTY.txt)。

## 支持与致谢

项目计划在游戏内加入感谢名单，收录开发贡献者的公开署名或昵称。署名将由本人确认，也尊重匿名意愿。这一功能尚未实现，将随游戏开发逐步加入。

## 更多文档

| 文档 | 内容 |
| --- | --- |
| [项目文档](docs/README.md) | 产品需求、架构与其他技术资料的入口。 |
| [目录与源码范围](docs/source-layout.md) | 源码、脚本、构建输出及本地资料的存放位置。 |
| [开发与交付](docs/development.md) | 工具链准备、构建、测试、打包与常见问题。 |
| [实施计划](docs/planning/implementation-plan.md) | 架构与验收要求。 |
