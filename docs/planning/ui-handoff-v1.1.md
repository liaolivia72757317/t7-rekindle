# UI 实施与验收

现有界面沿用 UI 1.1，对战页采用 UI 1.2 的“功能建设中”设计。设计版本不改变启动器业务版本。保留 WPF/net48、现有启动会话与更新器，不引入生产依赖。

## 实施范围

| 区域 | 设计约束 | 验证依据 |
| --- | --- | --- |
| 窗口 | 1200×900 DIP 首选尺寸，固定大小、随工作区收敛；侧栏五项导航、居中版本胶囊 | 原生布局、工作区计算与跨屏检查 |
| 版本胶囊 | [完整版本号与外置更新角标定稿](version-capsule.md)；正式版盾牌、预览版烧瓶，类型取已安装构建 | 四状态、长版本、DPI、主题、点击区域与渠道独立性 |
| 资源 | 43 个同源线性图标，完整窗口与首页底图；面板遮罩独立 | 资源契约、WPF 渲染、隐藏 UI 的背景图 |
| 首页 | 单张默认美术、内置公告标题与摘要、已保存字段、右下启动状态与操作 | 公告全文弹窗、缺配置、就绪、启动、运行、结束、失败、取消/退出 |
| 对战 | 功能建设中状态卡、开发中标识，以及返回首页、问题反馈、关于项目三个入口 | 正确导航、反馈入口不可用、会话不受影响、小工作区滚动 |
| 更新 | 检查状态独立；版本与时间真实；当前版本日志内滚动 | 未检查、检查中、最新、新版、无发布、失败及现有下载/安装测试 |
| 设置 | 基本设置/启动偏好；编辑结束校验，静默保存；失败保留草稿、开关恢复 | IME/Enter/失焦、过期回调、异常保存、长路径 |
| 反馈 | 四种 Toast、最多两条、去重与队列、暂停/关闭、20 条近期记录 | 时间控制与键盘/焦点测试；错误不自动关闭 |
| 弹窗 | 统一阅读/诊断/确认；默认取消，焦点回归 | 结束游戏、剪贴板失败、长日志与许可证全文 |
| 系统偏好 | 本次进程运行后收托盘、恢复；登录只启动启动器 | 系统服务测试与 Windows 检查 |

## 资源来源

- 功能图标：T7 UI Outline 1.0，24×24，描边 1.75；`Resources/Icons.xaml` 保留交付的 43×6 DrawingImage，包含 UI 1.2 新增的 `construction`。
- `Icon` 控件复用同一几何路径，并继承主题/交互状态颜色，不另造图标。
- 版本胶囊单独使用 Lucide 的 `shield-check`、`flask-conical` 原始路径，描边 1.8；样式与几何集中于 `Resources/VersionCapsule.xaml`，许可随启动器嵌入。不替换现有功能图标族。
- 窗口背景 `launcher-background.png`：1476×1065 原图“静谧山门与远山墨韵”，以 `UniformToFill` 等比例铺满窗口。
- 对战页状态卡背景 `launcher-full-2560x1920.png`：原生 1448×1086 补绘场景的插值导出。
- 首页轮播图 `home-banner.png`：1989×790 原图“刀锋再起：熟悉的武将，久违的交锋。”，画面为赵云与夏侯惇对阵，展示区随宽度保持原图比例，小窗口可滚动查看完整内容；图片自带标题，不叠加旧标题或遮罩，深色及高对比度主题保留文字回退。
- 正式品牌图标沿用仓库资源。不导入历史整页截图、示例数据、字体或外部工程配置。

## 实现入口与状态绑定

| 文件 | 职责 |
| --- | --- |
| `src/Desktop/MainWindow.xaml`、`Resources/Navigation.xaml` | 五项导航、窗口层次、页面与控件样式 |
| `src/Desktop/Views/` | 六个页面视图、统一图标、Toast、阅读/诊断/确认弹窗 |
| `src/Desktop/ViewModels/MainWindowViewModel.*` | 启动会话、字段提交、偏好持久化、诊断入口 |
| `src/Desktop/ViewModels/AboutViewModel.cs`、`NoticeCenter.cs` | 更新检查状态、限量通知队列与会话历史 |
| `src/Desktop/Services/WindowLayoutController.cs`、`TrayService.cs`、`WindowsStartupService.cs` | 当前屏幕工作区、托盘恢复、当前用户登录启动 |
| `src/Core/Settings.cs` | 默认窗口尺寸及两个向后兼容的布尔偏好 |
| `tests/managed/LauncherDesignTests.cs`、`LauncherToastTests.cs`、`UiInteractionTests.cs`、`NativeWindowUiTests.cs` | 布局、交互、故障与原生窗口检查 |
| `tests/python/test_ui_v11.py`、`test_ui_contract.py` | 资源与界面结构契约 |

- 首页只读已保存设置，启动状态来自已有会话快照；正常退出、结束和取消回到“准备就绪”。结束操作只针对本次启动的进程。
- 设置在 Enter 或失焦后提交，输入法组合期间不提交；目录异步校验忽略过期结果。保存失败保留草稿，首页继续显示上次保存值；偏好持久化失败回滚开关和系统注册。
- 更新检查独立于游戏启动，最近检测时间为实际尝试时间，当前版本始终来自程序集。检查结果不自动打开弹窗，下载与安装仍走现有更新器，安装前再次确认。
- Toast 只接收实际结果，不显示持续进度；错误持续显示，可手动关闭。字段错误优先内联，离开对应页面后的失败才进入通知。

## 真实能力与占位

- 首页展示内置公告的标题与摘要，点击“阅读全文”打开可滚动、可复制的正文弹窗。正文位于 `src/Desktop/Resources/ANNOUNCEMENT.md`，随启动器打包，无需联网；标题与摘要位于 `MainWindowViewModel.Presentation.cs`。只有一张首页美术，不展示无效轮播箭头。
- 当前没有房间服务，对战页显示“功能建设中”，不展示房间列表或连接操作；未新增直连、创建或房间协议。进入页面不触发启动、更新检查或自动提示，问题反馈复用项目现有 Issues 地址。
- 版本、版本日志、许可证与仓库链接使用项目已有内容，不导入设计中的模拟信息。

## 验证记录

- 修改前：托管构建与现有 managed harness 通过。
- 新增 UI 1.1 资源/导航/编辑契约测试，修改前四项均失败，证实现状与新设计存在差异。
- 2026-10-03：托管构建、完整 managed harness 和 137 项 Python 测试通过；WPF 绑定警告作为测试失败处理。
- 六视图覆盖 100%、125%、150%、175%、200% 渲染倍率、1200×640 与 928×460 内容区；同时生成浅色、深色和高对比度截图。
- 隐藏实际 WPF 窗口的 UI 图层后导出 `background-ui-hidden.png`，确认背景为连续场景，没有控件残留或矩形孔洞。
- 交互测试覆盖长路径、过期校验、异常保存、偏好回滚、剪贴板失败、诊断脱敏、默认取消、更新结果和 Toast 队列/暂停/去重/持续错误。
- Windows 真实 HWND 检查通过：1920×1032 工作区得到 1200×900 DIP 窗口，1080×1872 工作区得到 1048×900 DIP 窗口；两屏均为 96 DPI。托盘隐藏/恢复及弹窗相对主窗口居中通过。

复现命令（仓库根目录，PowerShell）：

```powershell
python scripts/build.py --project managed
python -m pytest -q
& .\artifacts\bin\T7.ManagedHarness\x64\Release\net48\T7.ManagedHarness.exe --render-ui artifacts/ui-v11-final
& .\artifacts\bin\T7.ManagedHarness\x64\Release\net48\T7.ManagedHarness.exe --native-ui artifacts/ui-v11-final
```

截图位于 `artifacts/ui-v11-final/`，深色与高对比度分别位于其 `Dark/`、`HighContrast/` 子目录；原生结果为 `native-window.json`。关键文件包括 `home-running.png`、`settings-validation-error.png`、`update-state-failed.png`、`toast-error.png`、`confirm-end-game.png` 和 `diagnostics-copy-failed.png`。

### Windows 实机复现步骤

以下是验收步骤和通过标准，不依赖维护者本机的历史截图或 JSON。`artifacts/` 中的报告由命令生成，不随仓库发布。

1. 按[开发与交付](../development.md#1-准备工具链)准备工具链和官方 CPython 3.14.4 AMD64 embeddable ZIP。使用独立 Windows 测试账户，记录提交号、系统版本、显示器分辨率、缩放、任务栏状态和输入法。混合 DPI 检查需要两台显示器。
2. 在仓库根目录依次执行以下命令，每条退出码为 0 后再继续。完整包包含 NativeBridge 所需的 Python 运行时，不从 managed 输出目录运行实际启动器。

```powershell
git rev-parse HEAD
python scripts/build.py --project all
python -m pytest -q
& .\artifacts\bin\T7.ManagedHarness\x64\Release\net48\T7.ManagedHarness.exe
$run = Join-Path 'artifacts' ('ui-device-check-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$pythonArchive = Read-Host '请输入 CPython 3.14.4 AMD64 embeddable ZIP 的完整路径'
python scripts/package.py --output "$run/package" --python-archive "$pythonArchive" --release
python scripts/package.py --output "$run/package" --verify --release
& "$run\package\T7-Rekindle.exe"
```

3. 在 Windows 显示设置中将主屏依次设为 125%、150%、175%，副屏保持 100%。每次设置后执行下方命令，输入当前主屏缩放百分比。报告记录实际 HWND 的 DPI、工作区与窗口尺寸；`mixedDpi` 应为 `true`，边界、弹窗居中和托盘断言均应通过。此入口使用测试桥接器，不启动真实游戏。

```powershell
$scale = Read-Host '当前主屏缩放百分比（125、150 或 175）'
& .\artifacts\bin\T7.ManagedHarness\x64\Release\net48\T7.ManagedHarness.exe --native-ui "$run/mixed-dpi-$scale"
Get-Content -LiteralPath "$run/mixed-dpi-$scale/native-window.json" -Encoding UTF8
```

4. 对第 2 步启动的完整包执行以下人工检查；截图保存到本次 `$run` 目录，结果记录实际环境和观察值，而不是沿用固定像素数。

| 检查 | 操作与通过标准 |
| --- | --- |
| 辅助功能树 | 用 Windows SDK 的 Inspect.exe（UI Automation 模式）展开设置页的各个 Tab；名称、目录输入框和偏好开关均可按名称访问，并能通过键盘聚焦、编辑或切换 |
| 实际进程跨屏 | 主屏为 150%、副屏为 100% 时，将完整包窗口从主屏拖至副屏再拖回；两端截图中窗口均收敛在工作区内，导航与内容完整可用 |
| 任务栏工作区 | 主屏为 175% 时依次关闭、开启、关闭 Windows 的“自动隐藏任务栏”；每次截图检查窗口仍在当前工作区内、内容可滚动访问 |
| 中文输入法 | 使用下方后台监测命令，输入公开测试名称并截图真实候选窗口；组合输入、候选确认和输入法占用的 Enter 不改变已保存名称，随后普通 Enter 或失焦才保存 |
| 当前用户自启动项 | 在设置页开启登录启动开关，用下方命令检查 Run 项仅包含带引号的本次完整包 EXE 路径、无额外参数；关闭后该项消失，重启启动器后开关仍为关闭 |

输入法检查前启动监测，操作期间保持输入框焦点；检查结束后停止任务并保存输出，避免切换到终端读取设置时意外触发失焦保存：

```powershell
$imeMonitor = Start-Job {
    $settings = Join-Path $env:LOCALAPPDATA 'T7-Rekindle/settings.json'
    while ($true) {
        if (Test-Path -LiteralPath $settings) {
            $name = (Get-Content -LiteralPath $settings -Raw -Encoding UTF8 | ConvertFrom-Json).playerName
            '{0:o} {1}' -f (Get-Date), $name
        }
        Start-Sleep -Milliseconds 200
    }
}
# 完成输入法操作后执行：
Stop-Job $imeMonitor
Receive-Job $imeMonitor | Set-Content -LiteralPath "$run/ime-values.txt" -Encoding UTF8
Remove-Job $imeMonitor

# 分别在开启和关闭登录启动后读取；关闭时该属性应为空：
(Get-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run').'T7-Rekindle'
```

5. 退出测试启动器，恢复原有缩放、任务栏和输入模式，确认测试账户的登录启动项已关闭。公开验收记录应包含提交号、环境、步骤、预期与实际结果；只附脱敏后的截图或报告，不发布个人目录和原始设置文件。

### 待实机验收

- Windows 注销、重新登录后只启动启动器、不启动游戏；注册项读写检查不覆盖登录触发。
- 真实游戏的启动/退出沿用项目发行验收。界面与托盘测试使用测试桥接器，不启动真实游戏。
