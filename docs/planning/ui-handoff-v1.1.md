# UI 实施与验收

现有界面沿用 UI 1.1，对战页采用 UI 1.2 的“功能建设中”设计。设计版本不改变启动器业务版本。保留 WPF/net48、现有启动会话与更新器，不引入生产依赖。

## 实施范围

| 区域 | 设计约束 | 验证依据 |
| --- | --- | --- |
| 窗口 | 1200×900 DIP 首选尺寸，固定大小、随工作区收敛；侧栏五项导航、居中版本胶囊 | 原生布局、工作区计算与跨屏检查 |
| 资源 | 43 个同源线性图标，完整窗口与首页底图；面板遮罩独立 | 资源契约、WPF 渲染、隐藏 UI 的背景图 |
| 首页 | 单张默认美术、单条公告区域、已保存字段、右下启动状态与操作 | 缺配置、就绪、启动、运行、结束、失败、取消/退出 |
| 对战 | 功能建设中状态卡、开发中标识，以及返回首页、问题反馈、关于项目三个入口 | 正确导航、反馈入口不可用、会话不受影响、小工作区滚动 |
| 更新 | 检查状态独立；版本与时间真实；当前版本日志内滚动 | 未检查、检查中、最新、新版、无发布、失败及现有下载/安装测试 |
| 设置 | 基本设置/启动偏好；编辑结束校验，静默保存；失败保留草稿、开关恢复 | IME/Enter/失焦、过期回调、异常保存、长路径 |
| 反馈 | 四种 Toast、最多两条、去重与队列、暂停/关闭、20 条近期记录 | 时间控制与键盘/焦点测试；错误不自动关闭 |
| 弹窗 | 统一阅读/诊断/确认；默认取消，焦点回归 | 结束游戏、剪贴板失败、长日志与许可证全文 |
| 系统偏好 | 本次进程运行后收托盘、恢复；登录只启动启动器 | 系统服务测试与 Windows 检查 |

## 资源来源

- 功能图标：T7 UI Outline 1.0，24×24，描边 1.75；`Resources/Icons.xaml` 保留交付的 43×6 DrawingImage，包含 UI 1.2 新增的 `construction`。
- `Icon` 控件复用同一几何路径，并继承主题/交互状态颜色，不另造图标。
- 窗口背景 `launcher-full-2560x1920.png`：原生 1448×1086 补绘场景的插值导出。
- 首页背景 `hero-full-2560x1026.png`：原生 1115×447 局部修复图的插值导出；不包含控件。
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

- 当前没有公告来源，显示“暂无公告”；只有一张首页美术，不展示无效轮播箭头。
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

### Windows 实机补充验证（2026-10-04～05）

通过 `scripts/package.py` 生成并校验完整测试包后运行实际启动器；仅运行 managed 输出目录会缺少 NativeBridge 所依赖的 Python 运行时。

| 检查 | 实际结果与证据 |
| --- | --- |
| 辅助功能树 | 发现设置 Tab 内容未暴露给 UI Automation；补齐 WPF 的 `PART_SelectedContentHost`，新增递归检查输入框的回归测试。修复后实际进程可访问名称、目录输入框和偏好开关 |
| 混合 DPI | 1920×1080 主屏分别设为 125%、150%、175%，1080×1920 副屏保持 100%；窗口工作区、弹窗居中与托盘恢复检查均通过 |
| 实际进程跨屏 | 完整测试包完成 150%→100%→150% 往返，副屏窗口为 1048×900 DIP；检查的是 Windows 实际 HWND，而非渲染倍率 |
| 任务栏工作区 | 175% 下切换真实“自动隐藏任务栏”，工作区高度 996→1080→996 px，实际窗口高度 938→1022→938 px，始终位于工作区内 |
| 中文输入法 | 通过系统输入注入操作真实中文输入法，记录候选窗口；组合输入和候选确认不落盘，输入法占用的 Enter 不提交字段，随后 Enter 与失焦正确保存 |
| 当前用户自启动项 | 在实际设置页开启/关闭，验证 HKCU Run 项指向当前完整包的启动器且不带参数；开关关闭后移除注册项，设置持久化一致 |
| 回归与恢复 | 完整托管 harness、137 项 Python 测试及 `git diff --check` 通过；临时缩放、任务栏状态和输入模式已恢复，测试启动器已退出，原始设置及其备份已恢复，自启动项保持原状 |

补充证据位于 `artifacts/ui-device-check-20261004/`：`mixed-dpi-125/`、`mixed-dpi-150/`、`mixed-dpi-175/` 中的 `native-window.json`，以及 `live-cross-screen.json`、`taskbar-result.json`、`ime-result.json`、`startup-result.json`、`display-restored.json`、`session-restored.json`。实际界面截图为 `preferences-native-150.png`、`preferences-native-secondary-100.png`、`update-native-175.png`；`ime-active-candidates.png` 只保留公开测试内容，未包含本机路径。

### 待实机验收

- Windows 注销、重新登录后只启动启动器、不启动游戏；目前已验证实际注册与移除，尚未观察登录触发。
- 真实游戏的启动/退出沿用项目发行验收。界面与托盘测试使用测试桥接器，不启动真实游戏。
