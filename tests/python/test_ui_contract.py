import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WPF = "http://schemas.microsoft.com/winfx/2006/xaml/presentation"
XAML = "http://schemas.microsoft.com/winfx/2006/xaml"


def _xaml(path):
    return ET.parse(path).getroot()


def _find(root, tag):
    return root.findall(".//{" + WPF + "}" + tag)


def test_main_window_has_accessible_lifecycle_contract():
    root = _xaml(ROOT / "src/Desktop/MainWindow.xaml")
    assert root.attrib["Closing"] == "OnClosing"
    assert root.attrib["Closed"] == "OnClosed"
    assert root.attrib["ResizeMode"] == "CanMinimize"
    assert root.attrib["AutomationProperties.Name"] == "T7-Rekindle 本地客户端启动器"
    tabs = _find(root, "RadioButton")
    assert [tab.attrib["Content"] for tab in tabs] == ["首页", "对战", "更新", "设置", "关于"]
    assert tabs[1].attrib["AutomationProperties.Name"] == "对战"
    assert tabs[2].attrib["AutomationProperties.Name"] == "更新"
    for path in (ROOT / "src/Desktop").rglob("*.xaml"):
        if path.parent.name == "Resources":
            continue
        for button in _find(_xaml(path), "Button"):
            assert button.attrib.get("AutomationProperties.Name"), path
            assert button.attrib.get("TabIndex") is not None, path


def test_launcher_uses_full_artwork_and_real_controls():
    root = _xaml(ROOT / "src/Desktop/MainWindow.xaml")
    named = {item.attrib.get("{" + XAML + "}Name"): item for item in root.iter()}
    assert named["BrandLogo"].attrib["Source"].endswith("/brand/logo-stacked-no-slogan-sidebar.png")
    scene = named["SceneArtwork"]
    assert scene.attrib["Source"].endswith("/art/launcher-background.png")
    assert scene.attrib["Stretch"] == "UniformToFill"
    assert scene.attrib["Grid.ColumnSpan"] == scene.attrib["Grid.RowSpan"] == "2"
    assert scene.attrib["Visibility"] == "{DynamicResource LauncherArtworkVisibility}"
    assert scene.attrib["ImageFailed"] == "OnSceneArtworkFailed"
    assert scene.attrib["IsHitTestVisible"] == "False"
    home = _xaml(ROOT / "src/Desktop/Views/LaunchPage.xaml")
    action = next(item for item in _find(home, "Button") if item.attrib.get("{" + XAML + "}Name") == "LaunchButton")
    assert action.attrib["Command"] == "{Binding MainActionCommand}"
    assert action.attrib.get("IsDefault", "False") == "False"


def test_launcher_light_palette_matches_v11_design_tokens():
    root = _xaml(ROOT / "src/Desktop/App.xaml")
    colors = {item.attrib["{" + XAML + "}Key"]: item.attrib["Color"] for item in _find(root, "SolidColorBrush")}
    assert {key: colors[key] for key in ("WindowBackgroundBrush", "TextBrush", "MutedTextBrush", "PrimaryBrush", "GoldBrush", "FocusBrush")} == {
        "WindowBackgroundBrush": "#F4F7F8", "TextBrush": "#234759", "MutedTextBrush": "#536E81",
        "PrimaryBrush": "#324F60", "GoldBrush": "#B48A46", "FocusBrush": "#90621E",
    }


def test_launcher_starts_update_checks_after_showing_main_window():
    source = (ROOT / "src/Desktop/App.xaml.cs").read_text(encoding="utf-8-sig")
    assert source.index("window.Show();") < source.index("viewModel.StartUpdateChecks(DateTime.UtcNow);")


def test_version_is_centered_in_sidebar_and_update_is_a_page():
    root = _xaml(ROOT / "src/Desktop/MainWindow.xaml")
    named = {item.attrib.get("{" + XAML + "}Name"): item for item in root.iter()}
    capsule = named["VersionCapsule"]
    assert capsule.tag == "{" + WPF + "}Button"
    assert capsule.attrib["HorizontalAlignment"] == "Center"
    assert capsule.attrib["Command"] == "{Binding ShowUpdatePageCommand}"
    assert capsule.attrib["AutomationProperties.Name"] == "{Binding About.VersionCapsuleHint}"
    assert capsule.attrib["ToolTip"] == "{Binding About.VersionCapsuleHint}"
    assert named["CapsuleVersion"].attrib["Text"] == "{Binding About.Version}"
    assert named["CapsuleVersion"].attrib["TextTrimming"] == "CharacterEllipsis"
    assert named["VersionUpdateReminder"].attrib["Visibility"] == "{Binding About.HasUpdateReminder, Converter={StaticResource BoolVisibility}}"
    assert named["VersionUpdateIcon"].attrib["Kind"] == "refresh"
    assert named["VersionUpdateLabel"].attrib["Text"] == "有更新"
    assert "UpdateButton" not in named
    update = _xaml(ROOT / "src/Desktop/Views/LauncherUpdatePage.xaml")
    assert _find(update, "FlowDocumentScrollViewer")[0].attrib["VerticalScrollBarVisibility"] == "Auto"
    assert not any(item.attrib.get("Command") in ("{Binding ShowChangelogCommand}", "{Binding ShowDownloadCommand}") for item in _find(update, "Button"))


def test_battle_page_uses_construction_design_and_existing_navigation():
    root = _xaml(ROOT / "src/Desktop/Views/MultiplayerPage.xaml")
    named = {item.attrib.get("{" + XAML + "}Name"): item for item in root.iter()}
    title = named["ConstructionTitle"]
    assert title.attrib["Text"] == "对战 · 功能建设中"
    assert title.attrib["ToolTip"] == title.attrib["Text"]
    assert title.attrib["TextTrimming"] == "CharacterEllipsis"
    assert title.attrib["Focusable"] == "True"
    scroll = named["ConstructionScroll"]
    assert scroll.attrib["VerticalScrollBarVisibility"] == "Auto"
    assert scroll.attrib["HorizontalScrollBarVisibility"] == "Disabled"
    assert title not in scroll.iter()
    assert _find(root, "DataGrid") == []
    assert _find(root, "ProgressBar") == []
    assert _find(root, "ImageBrush")[0].attrib["ImageSource"].endswith("/art/launcher-full-2560x1920.png")
    assert [button.attrib["Command"] for button in _find(root, "Button")] == [
        "{Binding ShowHomeCommand}", "{Binding About.ShowIssuesCommand}", "{Binding ShowAboutCommand}",
    ]
    assert [button.attrib["TabIndex"] for button in _find(root, "Button")] == ["8", "9", "10"]
    assert named["ConstructionFeedbackButton"].attrib["IsEnabled"] == "{Binding About.HasIssuesAddress}"
    texts = {block.attrib.get("Text") for block in _find(root, "TextBlock")}
    assert {"该功能正在建设中", "当前模块尚未完成开发，敬请期待。", "开发中",
            "功能开放后，此处将显示正式页面。"} <= texts
    assert any(item.attrib.get("Property") == "Text" and item.attrib.get("Value") == "反馈入口暂不可用"
               for item in _find(root, "Setter"))


def test_configuration_is_edited_only_in_settings_and_error_is_inline():
    home = _xaml(ROOT / "src/Desktop/Views/LaunchPage.xaml")
    assert _find(home, "TextBox") == []
    settings = _xaml(ROOT / "src/Desktop/Views/GameSettingsPage.xaml")
    fields = _find(settings, "TextBox")
    assert [item.attrib["AutomationProperties.Name"] for item in fields] == ["玩家名称", "游戏根目录"]
    assert all("UpdateSourceTrigger=Explicit" in item.attrib["Text"] for item in fields)
    assert all(item.attrib["IsReadOnly"] == "{Binding AreSessionFieldsLocked}" for item in fields)
    assert {"{Binding NameFieldError}", "{Binding DirectoryFieldError}"} <= {item.attrib.get("Text") for item in _find(settings, "TextBlock")}
    assert not any("保存" in item.attrib.get("Content", "") for item in _find(settings, "Button"))


def test_notices_are_interactive_overlays_at_top_of_content():
    root = _xaml(ROOT / "src/Desktop/MainWindow.xaml")
    host = root.find(".//{clr-namespace:T7.Rekindle.Desktop.Views}ToastHost")
    assert host.attrib["VerticalAlignment"] == "Top"
    assert int(host.attrib["Panel.ZIndex"]) > 0
    toast = _xaml(ROOT / "src/Desktop/Views/ToastHost.xaml")
    assert _find(toast, "ItemsControl")[0].attrib["ItemsSource"] == "{Binding Notices.Visible}"
    assert any(item.attrib.get("Command") == "{Binding CloseCommand}" for item in _find(toast, "Button"))
    assert any(item.attrib.get("IsKeyboardFocusWithinChanged") for item in _find(toast, "Border"))


def test_launch_page_keeps_status_local_and_diagnostics_in_a_dialog():
    home = _xaml(ROOT / "src/Desktop/Views/LaunchPage.xaml")
    commands = {item.attrib.get("Command") for item in _find(home, "Button")}
    assert {"{Binding ShowDiagnosticsCommand}", "{Binding MainActionCommand}", "{Binding StopCommand}"} <= commands
    assert not any(item.attrib.get("Text") == "{Binding NativeLogText}" for item in _find(home, "TextBlock"))
    root = _xaml(ROOT / "src/Desktop/MainWindow.xaml")
    assert not any(item.attrib.get("Text") == "{Binding StatusText}" for item in _find(root, "TextBlock"))


def test_about_page_has_real_metadata_and_project_entry_points():
    about = _xaml(ROOT / "src/Desktop/Views/AboutPage.xaml")
    commands = {button.attrib.get("Command") for button in _find(about, "Button")}
    assert commands == {
        "{Binding CopyHashCommand}", "{Binding ShowRepositoryCommand}", "{Binding ShowIssuesCommand}",
        "{Binding ShowLicensesCommand}", "{Binding ShowThanksCommand}",
        "{Binding ShowEnvironmentInfoCommand}", "{Binding ShowClientDownloadCommand}",
    }
    named = {item.attrib.get("{" + XAML + "}Name"): item for item in about.iter()}
    support = named["ClientSupportGrid"]
    assert [button.attrib["Command"] for button in _find(support, "Button")] == [
        "{Binding ShowClientDownloadCommand}", "{Binding ShowEnvironmentInfoCommand}", "{Binding ShowIssuesCommand}",
    ]
    assert named["ClientDownloadButton"].attrib["Grid.RowSpan"] == "2"
    entry_grid = named["ProjectLinksGrid"]
    assert entry_grid.attrib["Columns"] == "3"
    assert entry_grid.attrib["Margin"] == "0,16,0,0"
    assert [button.attrib["Command"] for button in _find(entry_grid, "Button")] == [
        "{Binding ShowRepositoryCommand}", "{Binding ShowLicensesCommand}", "{Binding ShowThanksCommand}",
    ]
    assert [button.attrib["TabIndex"] for button in _find(about, "Button")] == [str(index) for index in range(8, 15)]
    assert [button.attrib["Margin"] for button in _find(entry_grid, "Button")] == ["0,0,8,0", "4,0,4,0", "8,0,0,0"]
    repository_button = _find(entry_grid, "Button")[0]
    assert repository_button.attrib["AutomationProperties.Name"] == "项目源代码仓库"
    assert _find(repository_button, "TextBlock")[0].attrib["Text"] == "项目源代码仓库"
    texts = {block.attrib.get("Text") for block in _find(about, "TextBlock")}
    assert {"关于", "{Binding ProjectName}", "{Binding ProjectDescription}", "{Binding ShortHash}"} <= texts
    assert "常用入口" not in texts
    assert named["AboutContent"].attrib["SizeChanged"] == "OnContentSizeChanged"
    assert named["AboutScroll"].attrib["HorizontalScrollBarVisibility"] == "Disabled"
    assert named["AboutScroll"].attrib["VerticalScrollBarVisibility"] == "Auto"
    assert len(_find(about, "UniformGrid")) == 1


def test_about_client_download_card_explains_external_source():
    about = _xaml(ROOT / "src/Desktop/Views/AboutPage.xaml")
    named = {item.attrib.get("{" + XAML + "}Name"): item for item in about.iter()}
    card = named["ClientDownloadButton"]
    assert card.attrib["Command"] == "{Binding ShowClientDownloadCommand}"
    assert card.attrib["AutomationProperties.Name"] == "客户端下载"
    assert card.attrib["ToolTip"] == "{Binding ClientDownloadAddress}"
    assert card.attrib["AutomationProperties.HelpText"] == "{Binding Text, ElementName=ClientDownloadDisclaimer}"
    assert card.attrib["TabIndex"] == "9"
    assert _find(card, "TextBlock")[0].attrib["Text"] == "客户端下载"
    disclaimer = named["ClientDownloadDisclaimer"]
    assert disclaimer in card.iter()
    assert disclaimer.attrib["Style"] == "{StaticResource MutedText}"
    assert disclaimer.attrib["TextWrapping"] == "Wrap"
    assert all(text in disclaimer.attrib["Text"] for text in ("第三方", "版权", "仅提供页面链接", "使用授权"))


def test_about_client_download_card_inherits_shared_interaction_colors():
    about = _xaml(ROOT / "src/Desktop/Views/AboutPage.xaml")
    card = next(button for button in _find(about, "Button")
                if button.attrib.get("Command") == "{Binding ShowClientDownloadCommand}")
    assert card.attrib["Style"] == "{StaticResource AboutActionCard}"
    assert "Background" not in card.attrib
    assert "BorderBrush" not in card.attrib


def test_environment_information_has_a_local_dialog_and_copy_action():
    about = _xaml(ROOT / "src/Desktop/Views/AboutPage.xaml")
    entry = next(button for button in _find(about, "Button")
                 if button.attrib.get("Command") == "{Binding ShowEnvironmentInfoCommand}")
    assert entry.attrib["AutomationProperties.Name"] == "环境信息"
    assert entry.attrib["TabIndex"] == "10"
    root = _xaml(ROOT / "src/Desktop/Views/EnvironmentInfoDialog.xaml")
    report = _find(root, "TextBox")[0]
    assert report.attrib["IsReadOnly"] == "True"
    assert report.attrib["Text"] == "{Binding Report, Mode=OneWay}"
    assert report.attrib["VerticalScrollBarVisibility"] == "Auto"
    assert report.attrib["TextWrapping"] == "Wrap"
    buttons = _find(root, "Button")
    assert {button.attrib.get("Command") for button in buttons} >= {
        "{Binding CopyCommand}", "{Binding RefreshCommand}",
    }
    assert any(button.attrib.get("IsCancel") == "True" for button in buttons)


def test_project_information_uses_the_public_repository():
    project = ET.parse(ROOT / "src/Desktop/T7.Desktop.csproj").getroot()
    assert project.findtext("PropertyGroup/Product") == "铁骑·重燃（T7-Rekindle）"
    assert project.findtext("PropertyGroup/Description") == (
        "铁骑·重燃是一个独立开源项目，目标是重新实现《刀锋铁骑》的服务端，让玩家通过原版客户端重回熟悉的战场。"
        "我们希望先完成本地人机对战，再逐步支持局域网联机。\n\n"
        "项目不以营利为目的，欢迎开发者和玩家一起参与。"
    )
    assert project.findtext("PropertyGroup/RepositoryUrl") == "https://github.com/liaolivia72757317/t7-rekindle"
    metadata = {item.attrib["Include"]: item.attrib["Value"]
                for item in project.findall("ItemGroup/AssemblyMetadata")}
    assert set(metadata) == {"RepositoryUrl", "DownloadUrl", "BuildsUrl", "IssuesUrl", "UpdateBaseUrl"}
    assert metadata["RepositoryUrl"] == "$(RepositoryUrl)"
    assert metadata["DownloadUrl"] == "$(RepositoryUrl)/releases"
    assert metadata["BuildsUrl"] == "$(RepositoryUrl)/actions/workflows/ci.yml"
    assert metadata["IssuesUrl"] == "$(RepositoryUrl)/issues"
    assert metadata["UpdateBaseUrl"] == "$(T7_UPDATE_BASE_URL)"


def test_update_page_reuses_header_actions_without_a_result_dialog():
    root = _xaml(ROOT / "src/Desktop/Views/LauncherUpdatePage.xaml")
    buttons = {button.attrib.get("{" + XAML + "}Name"): button for button in _find(root, "Button")}
    assert set(buttons) == {"UpdateButton", "CancelDownloadButton"}
    assert buttons["UpdateButton"].attrib["Command"] == "{Binding UpdateActionCommand}"
    assert buttons["UpdateButton"].attrib["Style"] == "{StaticResource PrimaryButton}"
    assert buttons["UpdateButton"].attrib["AutomationProperties.Name"] == "{Binding UpdateButtonText}"
    assert buttons["CancelDownloadButton"].attrib["Command"] == "{Binding UpdateDownload.CancelDownloadCommand}"
    assert buttons["CancelDownloadButton"].attrib["Style"] == "{StaticResource {x:Type Button}}"
    parents = {child: parent for parent in root.iter() for child in parent}
    assert parents[buttons["UpdateButton"]] is parents[buttons["CancelDownloadButton"]]
    body = _find(root, "ScrollViewer")[0]
    assert not _find(body, "Button")
    progress = next(item for item in _find(root, "ProgressBar")
                    if item.attrib.get("{" + XAML + "}Name") == "DownloadProgress")
    assert progress.attrib["Value"] == "{Binding Percent, Mode=OneWay}"
    assert not (ROOT / "src/Desktop/Views/UpdateDialog.xaml").exists()
    assert not (ROOT / "src/Desktop/Views/UpdateDialog.xaml.cs").exists()


def test_interface_copy_has_no_prototype_annotations():
    files = list((ROOT / "src/Desktop").rglob("*.xaml"))
    files += [ROOT / "src/Desktop" / name for name in (
        "ViewModels/AboutViewModel.cs", "Services/LauncherInformation.cs",
        "ViewModels/UpdateDownloadViewModel.cs", "Resources/CHANGELOG.md", "Resources/THANKS.md",
    )]
    for path in files:
        text = path.read_text(encoding="utf-8-sig")
        for annotation in ("示例", "占位", "演示", "暂为", "尚未接入", "尚未连接", "尚未收录", "不代表真实"):
            assert annotation not in text, (path.relative_to(ROOT), annotation)


def test_text_dialog_keeps_markdown_and_plain_text_views_separate():
    root = _xaml(ROOT / "src/Desktop/Views/TextDialog.xaml")
    viewer = _find(root, "FlowDocumentScrollViewer")[0]
    assert viewer.attrib["IsToolBarVisible"] == "False"
    assert viewer.attrib["IsSelectionEnabled"] == "True"
    assert viewer.attrib["VerticalScrollBarVisibility"] == "Auto"
    assert viewer.attrib["AutomationProperties.Name"]
    host = next(border for border in _find(root, "Border") if border.attrib.get("{" + XAML + "}Name") == "MarkdownHost")
    assert host.attrib["Visibility"] == "Collapsed"
    assert host.attrib["Grid.Row"] == "1"
    assert _find(root, "TextBox")[0].attrib["IsReadOnly"] == "True"
    close = next(item for item in _find(root, "Button") if item.attrib.get("{" + XAML + "}Name") == "CloseButton")
    assert close.attrib["IsCancel"] == "True"


def test_windows_share_font_fallback_and_pixel_aligned_text():
    resources = _xaml(ROOT / "src/Desktop/Resources/Controls.xaml")
    fonts = {font.attrib["{" + XAML + "}Key"]: font.text for font in _find(resources, "FontFamily")}
    assert fonts["UiFontFamily"] == "Microsoft YaHei UI, Microsoft YaHei, Segoe UI"
    assert fonts["CodeFontFamily"] == "Consolas, Microsoft YaHei UI, Microsoft YaHei"
    for name in ("MainWindow.xaml", "Views/TextDialog.xaml"):
        window = _xaml(ROOT / "src/Desktop" / name)
        assert window.attrib["FontFamily"] == "{StaticResource UiFontFamily}"
        assert window.attrib["Language"] == "zh-CN"
        assert window.attrib["TextOptions.TextFormattingMode"] == "Display"
        assert window.attrib["UseLayoutRounding"] == "True"
        assert window.attrib["SnapsToDevicePixels"] == "True"


def test_theme_dictionaries_define_all_runtime_brushes():
    required = {
        "WindowBackgroundBrush",
        "PanelBackgroundBrush",
        "PrimaryBrush",
        "TextBrush",
        "ControlBackgroundBrush",
        "ControlBorderBrush",
        "ButtonForegroundBrush",
        "MutedTextBrush", "DividerBrush", "HoverBrush", "DisabledBrush",
        "SuccessBrush", "DangerBrush", "AccentSoftBrush", "PrimaryHoverBrush",
    }
    for name in ("App.xaml", "Resources/Theme.Dark.xaml", "Resources/Theme.HighContrast.xaml"):
        root = _xaml(ROOT / "src/Desktop" / name)
        keys = {
            item.attrib["{" + XAML + "}Key"]
            for item in root.findall(".//{" + WPF + "}SolidColorBrush")
        }
        assert required <= keys, (name, required - keys)


def test_default_brushes_do_not_shadow_merged_themes():
    root = _xaml(ROOT / "src/Desktop/App.xaml")
    resources = root.find("{" + WPF + "}Application.Resources/{" + WPF + "}ResourceDictionary")
    assert resources is not None
    assert resources.findall("{" + WPF + "}SolidColorBrush") == []
    merged = resources.find("{" + WPF + "}ResourceDictionary.MergedDictionaries")
    assert merged is not None
    assert _find(merged, "SolidColorBrush")


def test_manifest_declares_per_monitor_dpi_and_non_elevated_execution():
    root = ET.parse(ROOT / "src/Desktop" / "app.manifest").getroot()
    xml = (ROOT / "src/Desktop" / "app.manifest").read_text(encoding="utf-8")
    assert 'requestedExecutionLevel level="asInvoker"' in xml
    assert "PerMonitorV2" in xml
    assert "true/pm" in xml
    assert root.tag.endswith("assembly")


def test_inno_contract_is_x64_per_user_and_does_not_ship_client_assets():
    text = (ROOT / "installer" / "T7-Rekindle.iss").read_text(encoding="utf-8")
    assert "ArchitecturesAllowed=x64os\n" in text
    assert "ArchitecturesInstallIn64BitMode=x64os\n" in text
    assert "MinVersion=10.0.19041" in text
    assert "PrivilegesRequired=lowest" in text
    assert "DefaultDirName={localappdata}\\Programs\\T7-Rekindle" in text
    assert "DisableDirPage=no" in text
    assert '[Languages]\nName: "chinesesimp"; MessagesFile: "compiler:Languages\\ChineseSimplified.isl"' in text
    assert 'Name: "desktopicon"; Description: "添加桌面快捷方式"' in text
    assert 'Tasks: desktopicon' in text
    assert 'Description: "运行 T7-Rekindle"; Flags: postinstall' in text
    assert 'Description: "打开安装目录"; Flags: postinstall shellexec' in text
    assert 'Source: "..\\artifacts\\package\\*"' in text
    lowered = text.casefold()
    for forbidden in ("t7.server.exe", "server.ini", "tiejiclient.exe"):
        assert forbidden not in lowered


def test_inno_relaunches_after_silent_install_and_keeps_the_manual_checkbox():
    text = (ROOT / "installer/T7-Rekindle.iss").read_text(encoding="utf-8")
    entries = text.split("[Run]", 1)[1].split("[Code]", 1)[0].splitlines()
    launch_entries = [entry for entry in entries if 'Filename: "{app}\\{#MyAppExeName}"' in entry]
    assert len(launch_entries) == 1
    flags = set(launch_entries[0].split("Flags:", 1)[1].split(";", 1)[0].split())
    assert {"postinstall", "nowait"} <= flags
    assert not {"skipifsilent", "unchecked"} & flags
    folder_entry = next(entry for entry in entries if 'Filename: "{app}";' in entry)
    assert "skipifsilent" in folder_entry


def test_inno_version_accepts_a_release_override_with_a_local_default():
    text = (ROOT / "installer" / "T7-Rekindle.iss").read_text(encoding="utf-8")
    assert '#ifndef MyAppVersion\n#define MyAppVersion "0.1.0"\n#endif' in text
    assert 'AppVersion={#MyAppVersion}' in text
    assert r'AppMutex=Local\T7-Rekindle.Desktop' in text
    project = ET.parse(ROOT / "src/Desktop/T7.Desktop.csproj").getroot()
    assert project.findtext("PropertyGroup/AssemblyVersion") == "$(T7_RELEASE_VERSION)"
    assert project.findtext("PropertyGroup/FileVersion") == "$(T7_RELEASE_VERSION)"


def test_in_app_update_installs_in_place_after_launcher_exit():
    service = (ROOT / "src/Desktop/Services/UpdateInstallationService.cs").read_text(encoding="utf-8")
    window = (ROOT / "src/Desktop/MainWindow.xaml.cs").read_text(encoding="utf-8")
    installer = (ROOT / "installer/T7-Rekindle.iss").read_text(encoding="utf-8")
    for argument in ("/SILENT", "/SP-", "/NORESTART", "/DIR=", "/LAUNCHERPID="):
        assert argument in service
    assert "UpdateInstallationService.CreateStartInfo(path, AppContext.BaseDirectory" in window
    assert "Process.GetCurrentProcess()" in window
    assert "{param:LAUNCHERPID|}" in installer
    assert "WaitForSingleObject" in installer
    assert "Result := WaitForLauncherExit();" in installer
    assert "[InstallDelete]" not in installer


def test_app_reacts_to_high_contrast_changes():
    text = (ROOT / "src/Desktop" / "App.xaml.cs").read_text(encoding="utf-8")
    assert "SystemParameters.StaticPropertyChanged += OnSystemParametersChanged" in text
    assert "Theme.HighContrast.xaml" in text
    assert "SystemParameters.StaticPropertyChanged -= OnSystemParametersChanged" in text


def test_native_startup_failure_is_logged_before_display():
    text = (ROOT / "src/Desktop/App.xaml.cs").read_text(encoding="utf-8")
    failure = text.split("catch (Exception error)", 1)[1].split("return;", 1)[0]
    assert 'new LogService().Error("启动原生运行时失败", error);' in failure
    assert failure.index(".Error(") < failure.index("MessageBox.Show(")


def test_managed_build_enables_binding_redirect_output():
    root = ET.parse(ROOT / "Directory.Build.props").getroot()
    values = {element.tag: (element.text or "").strip() for element in root.iter()}
    assert values["AutoGenerateBindingRedirects"] == "true"
    assert values["GenerateBindingRedirectsOutputType"] == "true"


def test_native_bridge_exports_are_explicit_and_versioned():
    text = (ROOT / "src/Runtime" / "bridge" / "T7NativeBridge.def").read_text(encoding="utf-8")
    lines = [line.strip() for line in text.splitlines()]
    assert lines[0] == "LIBRARY T7.NativeBridge.dll"
    assert lines[1] == "EXPORTS"
    exports = [line for line in lines[2:] if line]
    assert exports == [
        "t7_native_get_abi",
        "t7_native_create",
        "t7_native_release",
        "t7_native_submit_check",
        "t7_native_submit_start",
        "t7_native_submit_start_named",
        "t7_native_submit_start_options",
        "t7_native_submit_stop",
        "t7_native_cancel",
        "t7_native_get_snapshot",
        "t7_native_get_operation",
        "t7_native_get_error",
        "t7_native_read_logs",
    ]
