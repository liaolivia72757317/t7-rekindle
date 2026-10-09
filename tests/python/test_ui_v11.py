"""UI 1.1 resource and navigation contracts (not runtime business fixtures)."""
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESKTOP = ROOT / "src/Desktop"
WPF = "http://schemas.microsoft.com/winfx/2006/xaml/presentation"
XAML = "http://schemas.microsoft.com/winfx/2006/xaml"


def test_v11_uses_one_complete_vector_icon_family():
    root = ET.parse(DESKTOP / "Resources/Icons.xaml").getroot()
    icons = root.findall(f"{{{WPF}}}DrawingImage")
    assert len(icons) == 43 * 6
    keys = {item.attrib[f"{{{XAML}}}Key"] for item in icons}
    assert {f"T7.Icon.construction.{color}" for color in ("Ink", "White", "Muted", "Success", "Warning", "Danger")} <= keys
    assert all(p.attrib["Thickness"] == "1.75" for p in root.iter(f"{{{WPF}}}Pen"))


def test_v11_shell_has_five_sidebar_entries_and_a_fixed_window():
    root = ET.parse(DESKTOP / "MainWindow.xaml").getroot()
    assert root.attrib["Width"] == "1200"
    assert root.attrib["Height"] == "900"
    assert root.attrib["ResizeMode"] == "CanMinimize"
    entries = root.findall(f".//{{{WPF}}}RadioButton")
    assert [item.attrib["Content"] for item in entries] == [
        "首页", "对战", "更新", "设置", "关于"
    ]


def test_sidebar_brand_reclaims_caption_space_without_duplicate_title():
    root = ET.parse(DESKTOP / "MainWindow.xaml").getroot()
    named = {item.attrib.get(f"{{{XAML}}}Name"): item for item in root.iter()}
    sidebar = named["Sidebar"]
    assert sidebar.attrib["Grid.Row"] == "0"
    assert sidebar.attrib["Grid.RowSpan"] == "2"
    assert named["SidebarBrandRow"].attrib["Height"] == "180"
    logo = named["BrandLogo"]
    assert logo.attrib["Source"].endswith("/brand/logo-stacked-no-slogan-sidebar.png")
    assert logo.attrib["Margin"] == "24,15,24,15"
    assert logo.attrib["Stretch"] == "Uniform"
    assert logo.attrib["RenderTransformOrigin"] == "0.5,0.5"
    scale = logo.find(f"{{{WPF}}}Image.RenderTransform/{{{WPF}}}ScaleTransform")
    assert scale.attrib["ScaleX"] == scale.attrib["ScaleY"] == "1.05"
    assert logo.attrib["HorizontalAlignment"] == "Center"
    assert logo.attrib["VerticalAlignment"] == "Center"
    assert logo.attrib["IsHitTestVisible"] == "False"
    assert not root.findall(f"./{{{WPF}}}Grid/{{{WPF}}}StackPanel/{{{WPF}}}Image")
    assert not root.findall(f"./{{{WPF}}}Grid/{{{WPF}}}StackPanel/{{{WPF}}}TextBlock")
    shell = "clr-namespace:System.Windows.Shell;assembly=PresentationFramework"
    assert root.find(f".//{{{shell}}}WindowChrome").attrib["CaptionHeight"] == "60"
    captions = root.find(f"./{{{WPF}}}Grid/{{{WPF}}}StackPanel")
    assert captions.attrib["Grid.Column"] == "1"
    assert captions.attrib[f"{{{shell}}}WindowChrome.IsHitTestVisibleInChrome"] == "True"
    assert [button.attrib.get("Click") for button in captions.findall(f"{{{WPF}}}Button")] == [
        "OnMinimize", "OnCloseClick"
    ]


def test_sidebar_brand_asset_uses_trimmed_rgba_source():
    asset = DESKTOP / "Resources/Assets/brand/logo-stacked-no-slogan-sidebar.png"
    with asset.open("rb") as stream:
        header = stream.read(26)
    assert header[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">IIBB", header[16:26]) == (1062, 962, 8, 6)


def test_v11_runtime_does_not_use_legacy_icons_or_cutout_backgrounds():
    text = "\n".join(path.read_text(encoding="utf-8-sig") for path in DESKTOP.rglob("*.xaml")
                     if path.name != "Icons.xaml")
    assert "launcher-background.png" in text
    assert "launcher-full-2560x1920.png" in text
    assert "home-banner.png" in text
    for forbidden in ("scene-complete.png", "Assets/icons/", "reference/", "↗", "→"):
        assert forbidden not in text


def test_home_banner_uses_embedded_artwork_without_duplicate_title():
    root = ET.parse(DESKTOP / "Views/LaunchPage.xaml").getroot()
    named = {item.attrib.get(f"{{{XAML}}}Name"): item for item in root.iter()}
    artwork = named["HeroArtwork"]
    assert artwork.attrib["Source"] == "/T7-Rekindle;component/Resources/Assets/art/home-banner.png"
    assert artwork.attrib["Stretch"] == "UniformToFill"
    assert artwork.attrib["Visibility"] == "{DynamicResource LauncherArtworkVisibility}"
    assert named["HeroPanel"].attrib["AutomationProperties.Name"] == "刀锋再起：熟悉的武将，久违的交锋。"
    fallback = named["HeroFallback"]
    style = fallback.find(f"{{{WPF}}}StackPanel.Style/{{{WPF}}}Style")
    setter = style.find(f"{{{WPF}}}Setter")
    assert setter.attrib == {"Property": "Visibility", "Value": "Collapsed"}
    trigger = style.find(f"{{{WPF}}}Style.Triggers/{{{WPF}}}DataTrigger")
    assert trigger.attrib == {"Binding": "{Binding Visibility, ElementName=HeroArtwork}", "Value": "Collapsed"}
    assert trigger.find(f"{{{WPF}}}Setter").attrib == {"Property": "Visibility", "Value": "Visible"}
    assert list(named["HeroPanel"].iter(f"{{{WPF}}}TextBlock")) == list(fallback.iter(f"{{{WPF}}}TextBlock"))
    assert not named["HeroPanel"].findall(f".//{{{WPF}}}Border")
    with (DESKTOP / "Resources/Assets/art/home-banner.png").open("rb") as stream:
        header = stream.read(26)
    assert header[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">IIBB", header[16:26]) == (1989, 790, 8, 2)


def test_v11_settings_commit_editing_instead_of_saving_every_keystroke():
    root = ET.parse(DESKTOP / "Views/GameSettingsPage.xaml").getroot()
    fields = root.findall(f".//{{{WPF}}}TextBox")
    assert len(fields) == 2
    assert all("UpdateSourceTrigger=Explicit" in field.attrib["Text"] for field in fields)
    assert all(field.attrib.get("LostKeyboardFocus") for field in fields)
    assert all(field.attrib.get("PreviewKeyDown") for field in fields)
    tabs = root.findall(f".//{{{WPF}}}TabItem")
    assert [tab.attrib["Header"] for tab in tabs] == ["基本设置", "启动器设置", "游戏设置"]
    assert all(tab.attrib["AutomationProperties.Name"] == tab.attrib["Header"] for tab in tabs)
    switches = tabs[2].findall(f".//{{{WPF}}}CheckBox")
    assert len(switches) == 1
    assert switches[0].attrib["AutomationProperties.Name"] == "跳过启动动画"
    assert switches[0].attrib["Style"] == "{StaticResource PreferenceSwitch}"
    assert switches[0].attrib["IsChecked"] == "{Binding SkipStartupAnimation}"
