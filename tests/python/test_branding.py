import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
LEGACY_BRAND = re.compile(r"t7[. _-]*public", re.IGNORECASE)
GENERATED_DIRECTORIES = {
    ".git", ".pytest_cache", ".vs", ".idea", "__pycache__", "artifacts", "dist",
    "packages", "local", ".local", ".claude", "client", "work", "extracted",
}
RUNTIME_DIRECTORIES = {"src/Business/data", "src/Business/cache", "src/Runtime/data"}
TEXT_SUFFIXES = {
    ".cs", ".csproj", ".cpp", ".h", ".vcxproj", ".props", ".sln",
    ".xaml", ".config", ".manifest", ".iss", ".py", ".json",
    ".md", ".txt", ".yml", ".yaml",
}


def test_project_sources_and_filenames_have_no_legacy_brand():
    offenders = []
    for directory, directories, files in ROOT.walk():
        directories[:] = [
            name for name in directories
            if name not in GENERATED_DIRECTORIES
            and (directory / name).relative_to(ROOT).as_posix() not in RUNTIME_DIRECTORIES
        ]
        for name in files:
            path = directory / name
            relative = path.relative_to(ROOT).as_posix()
            if LEGACY_BRAND.search(relative):
                offenders.append(relative)
            if path.suffix in TEXT_SUFFIXES or name in {"LICENSE", ".gitignore"}:
                if LEGACY_BRAND.search(path.read_text(encoding="utf-8-sig")):
                    offenders.append(relative)
    assert not offenders, sorted(set(offenders))


def test_launcher_and_installer_share_the_product_name():
    project = ET.parse(ROOT / "src/Desktop/T7.Desktop.csproj").getroot()
    assert project.findtext("PropertyGroup/AssemblyName") == "T7-Rekindle"
    assert project.findtext("PropertyGroup/RootNamespace") == "T7.Rekindle.Desktop"
    lock = json.loads((ROOT / "tests/managed/packages.lock.json").read_text(encoding="utf-8"))
    assert lock["dependencies"][".NETFramework,Version=v4.8"]["t7-rekindle"]["type"] == "Project"
    assert (ROOT / "T7-Rekindle.sln").is_file()
    installer = (ROOT / "installer/T7-Rekindle.iss").read_text(encoding="utf-8")
    assert '#define MyAppName "T7-Rekindle"' in installer
    assert '#define MyAppExeName "T7-Rekindle.exe"' in installer
    assert "OutputBaseFilename=T7-Rekindle-Setup\n" in installer
    assert "DefaultDirName={localappdata}\\Programs\\T7-Rekindle\n" in installer


def test_ui_and_user_data_paths_share_the_product_name():
    window = ET.parse(ROOT / "src/Desktop/MainWindow.xaml").getroot()
    assert window.attrib["Title"] == "T7-Rekindle"
    assert window.attrib["AutomationProperties.Name"] == "T7-Rekindle 本地客户端启动器"
    assert '"T7-Rekindle"' in (ROOT / "src/Desktop/Services/SettingsService.cs").read_text(encoding="utf-8")
    assert "/T7-Rekindle/logs/desktop.log" in (ROOT / "src/Desktop/NLog.config").read_text(encoding="utf-8")
    for relative in ("src/Runtime/bridge/Session.cpp", "src/Runtime/server/Server.cpp"):
        assert 'L"T7-Rekindle"' in (ROOT / relative).read_text(encoding="utf-8")


def test_release_publishes_the_current_installer_filename():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert '"installer/T7-Rekindle.iss"' in workflow
    assert "path: dist/T7-Rekindle-Setup.exe\n" in workflow
    assert "path: dist/T7-Rekindle-windows-x64.zip\n" in workflow
    release = workflow.split("\n  release:\n", 1)[1].split("\n  mirror:\n", 1)[0]
    assert "RELEASE_TAG: ${{ github.ref_name }}" in release
    assert 'Rename-Item -LiteralPath "dist/T7-Rekindle-Setup.exe"' in release
    assert '-NewName "T7-Rekindle-${env:RELEASE_TAG}-Setup.exe"' in release
    assert '"dist/T7-Rekindle-${env:RELEASE_TAG}-Setup.exe"' in release
    assert release.count('"dist/T7-Rekindle-windows-x64-${env:RELEASE_TAG}.zip"') == 2
