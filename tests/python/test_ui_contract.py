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
    root = _xaml(ROOT / "src/Desktop" / "MainWindow.xaml")
    assert root.attrib["{" + XAML + "}Class"] == "T7.Rekindle.Desktop.MainWindow"
    assert root.attrib["Width"] == "800"
    assert root.attrib["Height"] == "600"
    assert root.attrib["MinWidth"] == "720"
    assert root.attrib["MinHeight"] == "560"
    assert root.attrib["Closing"] == "OnClosing"
    assert root.attrib["Closed"] == "OnClosed"
    assert root.attrib["AutomationProperties.Name"] == "T7-Rekindle 本地客户端启动器"
    tabs = _find(root, "RadioButton")
    assert [tab.attrib["Content"] for tab in tabs] == ["启动", "游戏设置", "关于"]
    assert tabs[1].attrib["IsChecked"] == "{Binding IsSettingsSelected}"
    settings_page = root.find(".//{clr-namespace:T7.Rekindle.Desktop.Views}GameSettingsPage")
    assert settings_page.attrib["Visibility"] == "{Binding IsSettingsSelected, Converter={StaticResource BoolVisibility}}"
    scroll = _find(root, "ScrollViewer")[0]
    assert scroll.attrib["Grid.Row"] == "2"
    footer = _find(root, "Border")[-1]
    assert footer.attrib["Grid.Row"] == "3"
    assert any(button.attrib.get("Command") == "{Binding StartCommand}" for button in _find(footer, "Button"))

    home = _xaml(ROOT / "src/Desktop/Views/LaunchPage.xaml")
    settings = _xaml(ROOT / "src/Desktop/Views/GameSettingsPage.xaml")
    for page in (root, home, settings, _xaml(ROOT / "src/Desktop/Views/AboutPage.xaml"),
                 _xaml(ROOT / "src/Desktop/Views/UpdateDialog.xaml"), _xaml(ROOT / "src/Desktop/Views/TextDialog.xaml")):
        for button in _find(page, "Button"):
            assert button.attrib.get("AutomationProperties.Name")
            assert button.attrib.get("TabIndex") is not None


def test_directory_configuration_lives_only_on_game_settings_page():
    home = _xaml(ROOT / "src/Desktop/Views/LaunchPage.xaml")
    settings = _xaml(ROOT / "src/Desktop/Views/GameSettingsPage.xaml")
    home_inputs = _find(home, "TextBox")
    assert [item.attrib["AutomationProperties.Name"] for item in home_inputs] == ["玩家名称"]
    assert home_inputs[0].attrib["Text"] == "{Binding PlayerName, UpdateSourceTrigger=PropertyChanged}"
    settings_inputs = _find(settings, "TextBox")
    assert len(settings_inputs) == 1
    directory = settings_inputs[0]
    assert directory.attrib["AutomationProperties.Name"] == "游戏根目录"
    assert directory.attrib["Text"] == "{Binding ClientDirectory, UpdateSourceTrigger=PropertyChanged}"
    for item in (home_inputs[0], directory):
        assert item.attrib["IsReadOnly"] == "{Binding AreSessionFieldsLocked}"
    assert {button.attrib["Command"] for button in _find(settings, "Button")} == {
        "{Binding BrowseCommand}", "{Binding CheckCommand}",
    }
    assert {"{Binding DirectoryMessage}", "{Binding NoticeText}"} <= {
        block.attrib.get("Text") for block in _find(settings, "TextBlock")
    }
    assert any("上级目录" in block.attrib.get("Text", "") for block in _find(settings, "TextBlock"))


def test_launch_page_keeps_status_and_log_directory_without_inline_logs():
    home = _xaml(ROOT / "src/Desktop/Views/LaunchPage.xaml")
    buttons = _find(home, "Button")
    assert [button.attrib["Command"] for button in buttons] == [
        "{Binding OpenLogsCommand}", "{Binding ShowSettingsCommand}", "{Binding CopyLogsCommand}",
    ]
    assert buttons[0].attrib["Content"] == "打开日志目录"
    assert buttons[1].attrib["Content"] == "前往游戏设置"
    assert buttons[2].attrib["Content"] == "复制诊断信息"
    texts = {block.attrib.get("Text") for block in _find(home, "TextBlock")}
    assert {"启动状态", "{Binding StatusText}", "{Binding DiagnosticText}"} <= texts
    attributes = " ".join(value for element in home.iter() for value in element.attrib.values())
    for binding in ("ToggleLogsCommand", "LogToggleText", "LogsExpanded", "NativeLogText", "EndpointText", "StageText"):
        assert binding not in attributes


def test_about_page_starts_with_information_card_without_heading():
    about = _xaml(ROOT / "src/Desktop/Views/AboutPage.xaml")
    content = about.find("{" + WPF + "}StackPanel")
    assert content is not None
    assert content.findall("{" + WPF + "}TextBlock") == []
    card = content[0]
    assert card.tag == "{" + WPF + "}Border"
    assert card.attrib["Style"] == "{StaticResource Card}"
    assert {"启动器版本", "Git 提交", "GitHub"} <= {
        block.attrib.get("Text") for block in _find(card, "TextBlock")
    }


def test_project_information_uses_the_public_repository():
    project = ET.parse(ROOT / "src/Desktop/T7.Desktop.csproj").getroot()
    assert project.findtext("PropertyGroup/RepositoryUrl") == "https://github.com/liaolivia72757317/t7-rekindle"
    metadata = {item.attrib["Include"]: item.attrib["Value"]
                for item in project.findall("ItemGroup/AssemblyMetadata")}
    assert set(metadata) == {"RepositoryUrl", "DownloadUrl", "BuildsUrl", "IssuesUrl", "UpdateBaseUrl"}
    assert metadata["RepositoryUrl"] == "$(RepositoryUrl)"
    assert metadata["DownloadUrl"] == "$(RepositoryUrl)/releases"
    assert metadata["UpdateBaseUrl"] == "$(T7_UPDATE_BASE_URL)"


def test_update_dialog_keeps_heading_and_actions_outside_scrollable_content():
    root = _xaml(ROOT / "src/Desktop/Views/UpdateDialog.xaml")
    scroll = _find(root, "ScrollViewer")[0]
    assert scroll.attrib["Grid.Row"] == "1"
    assert scroll.attrib["VerticalScrollBarVisibility"] == "Auto"
    assert not _find(scroll, "Button")
    footer = _find(root, "Border")[-1]
    assert footer.attrib["Grid.Row"] == "2"
    assert [button.attrib["{" + XAML + "}Name"] for button in _find(footer, "Button")] == [
        "LaterButton", "CancelDownloadButton", "DownloadButton"]
    assert _find(footer, "Button")[0].attrib["IsCancel"] == "True"
    assert _find(root, "ProgressBar")[0].attrib["Value"] == "{Binding Percent, Mode=OneWay}"
    assert _find(footer, "Button")[1].attrib["Command"] == "{Binding CancelDownloadCommand}"


def test_interface_copy_has_no_prototype_annotations():
    files = list((ROOT / "src/Desktop").rglob("*.xaml"))
    files += [ROOT / "src/Desktop" / name for name in (
        "ViewModels/AboutViewModel.cs", "Services/LauncherInformation.cs",
        "Views/UpdateDialog.xaml.cs", "Resources/CHANGELOG.md", "Resources/THANKS.md",
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
    assert _find(root, "Button")[0].attrib["Grid.Row"] == "2"


def test_windows_share_font_fallback_and_pixel_aligned_text():
    resources = _xaml(ROOT / "src/Desktop/Resources/Controls.xaml")
    fonts = {font.attrib["{" + XAML + "}Key"]: font.text for font in _find(resources, "FontFamily")}
    assert fonts["UiFontFamily"] == "Microsoft YaHei UI, Microsoft YaHei, Segoe UI"
    assert fonts["CodeFontFamily"] == "Consolas, Microsoft YaHei UI, Microsoft YaHei"
    for name in ("MainWindow.xaml", "Views/UpdateDialog.xaml", "Views/TextDialog.xaml"):
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
    assert "MinVersion=10.0.19045" in text
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


def test_inno_version_accepts_a_release_override_with_a_local_default():
    text = (ROOT / "installer" / "T7-Rekindle.iss").read_text(encoding="utf-8")
    assert '#ifndef MyAppVersion\n#define MyAppVersion "0.1.0"\n#endif' in text
    assert 'AppVersion={#MyAppVersion}' in text
    assert r'AppMutex=Local\T7-Rekindle.Desktop' in text
    project = ET.parse(ROOT / "src/Desktop/T7.Desktop.csproj").getroot()
    assert project.findtext("PropertyGroup/AssemblyVersion") == "$(T7_RELEASE_VERSION)"
    assert project.findtext("PropertyGroup/FileVersion") == "$(T7_RELEASE_VERSION)"


def test_app_reacts_to_high_contrast_changes():
    text = (ROOT / "src/Desktop" / "App.xaml.cs").read_text(encoding="utf-8")
    assert "SystemParameters.StaticPropertyChanged += OnSystemParametersChanged" in text
    assert "Theme.HighContrast.xaml" in text
    assert "SystemParameters.StaticPropertyChanged -= OnSystemParametersChanged" in text


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
        "t7_native_submit_stop",
        "t7_native_cancel",
        "t7_native_get_snapshot",
        "t7_native_get_operation",
        "t7_native_get_error",
        "t7_native_read_logs",
    ]
