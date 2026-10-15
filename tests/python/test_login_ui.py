"""Approved account surfaces stay native and independent of design reference files."""
import xml.etree.ElementTree as ET
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESKTOP = ROOT / "src/Desktop"
WPF = "{http://schemas.microsoft.com/winfx/2006/xaml/presentation}"
XAML = "{http://schemas.microsoft.com/winfx/2006/xaml}"


def named(path):
    return {node.get(XAML + "Name"): node for node in ET.parse(path).iter()}


def test_titlebar_account_entry_is_temporarily_collapsed():
    account = named(DESKTOP / "MainWindow.xaml")["TitlebarAccount"]
    assert account.get("Visibility") == "Collapsed"
    assert account.get("DataContext") == "{Binding Account}"


def test_account_menu_keeps_identity_row_and_title_entry():
    nodes = named(DESKTOP / "Views/AccountPanel.xaml")
    assert nodes["AccountMenu"].get("Width") == "280"
    assert nodes["AccountMenu"].get("Padding") == "12"
    assert nodes["AccountNumber"].get("TextTrimming", "None") == "None"
    assert nodes["AccountNickname"].get("TextTrimming") == "CharacterEllipsis"
    root = ET.parse(DESKTOP / "Views/AccountPanel.xaml")
    buttons = list(root.iter(WPF + "Button"))
    assert [button.get(XAML + 'Name') for button in buttons] == ['LogoutButton', 'TitlesButton']
    assert nodes['LogoutButton'].get("AutomationProperties.Name") == "退出登录"
    assert nodes['LogoutButton'].get("Content") is None
    assert nodes['TitlesButton'].get('Grid.Row') == '1'
    assert nodes['TitlesButton'].get('Click') == 'OnTitles'
    assert nodes['TitlesButton'].get('AutomationProperties.Name') == '我的称号'


def test_login_page_has_no_duplicate_chrome_or_eager_webview():
    root = ET.parse(DESKTOP / "Views/QqLoginDialog.xaml")
    assert root.getroot().get("Width") == "636"
    assert root.getroot().get("Height") == "378"
    assert root.getroot().get("WindowStyle") == "None"
    assert not any("WebView2" in node.tag for node in root.iter())
    nodes = named(DESKTOP / "Views/QqLoginDialog.xaml")
    assert nodes["ReloadButton"].get("AutomationProperties.Name") == "重载登录页"
    assert nodes["CancelButton"].get("AutomationProperties.Name") == "取消登录"
    assert nodes["DetectRuntime"].get("Click") == "OnDetectRuntime"
    source = (DESKTOP / "Views/QqLoginDialog.xaml.cs").read_text(encoding="utf-8-sig")
    assert "new WebView2CompositionControl" in source
    assert "Reload()" in source


def test_login_surface_resources_are_separate_from_existing_page_theme():
    root = ET.parse(DESKTOP / "Resources/LoginPalette.xaml")
    values = {node.get(XAML + "Key"): node for node in root.getroot()}
    assert values["LoginPaperBrush"].get("Color") == "#FCFBF7"
    assert values["LoginTitlebarBrush"][0].get("Color") == "#FDFDFA"
    assert values["LoginMenuBrush"][0].get("Color") == "#FFFEFB"
    assert values["AccountOnlineDotBrush"].get("Color") == "#3B7253"
    assert values["AccountOfflineDotBrush"].get("Color") == "#5C6E7A"
    assert "WindowBackgroundBrush" not in values


def test_login_ui_uses_no_reference_avatar_or_qr_assets():
    for relative in ("Views/AccountPanel.xaml", "Views/QqLoginDialog.xaml",
                     "Resources/LoginPalette.xaml", "Resources/LoginControls.xaml"):
        source = (DESKTOP / relative).read_text(encoding="utf-8-sig")
        assert re.search(r'Text="[0-9]{5,}"', source) is None
        for forbidden in ("qq-avatar-reference", "qq-page-reference", "C:\\Users\\"):
            assert forbidden not in source


def test_account_loading_animation_tracks_server_wait_and_reduced_motion():
    nodes = named(DESKTOP / "Views/AccountPanel.xaml")
    for name in ("GuestLoadingIndicator", "MemberLoadingIndicator"):
        indicator = nodes[name]
        assert indicator.tag == WPF + "ProgressBar"
        assert indicator.get("Style") == "{StaticResource AccountLoadingIndicator}"
        assert indicator.get("AutomationProperties.Name") == "{Binding Status}"
    styles = ET.parse(DESKTOP / "Resources/AccountCapsule.xaml")
    spinner = next(node for node in styles.getroot()
                   if node.get(XAML + "Key") == "AccountLoadingIndicator")
    source = ET.tostring(spinner, encoding="unicode")
    assert "IsWaitingForServer" in source
    assert "SystemParameters.ClientAreaAnimationKey" in source
    assert 'Property="IsVisible" Value="True"' in source
    assert 'RepeatBehavior="Forever"' in source
    assert "RemoveStoryboard" in source
