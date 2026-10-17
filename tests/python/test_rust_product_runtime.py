"""The shipped runtime must not depend on a Python installation or opt-in flag."""
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]


def test_native_product_does_not_embed_cpython():
    project = ROOT / "src/Runtime/T7R.NativeBridge.vcxproj"
    content = project.read_text(encoding="utf-8")
    assert "python314.lib" not in content
    assert "PythonHost" not in content
    assert "server\\Server.cpp" not in content
    for path in (ROOT / "src/Runtime").rglob("*.cpp"):
        assert "Python.h" not in path.read_text(encoding="utf-8"), path


def test_launcher_uses_rust_without_environment_switch():
    session = (ROOT / "src/Desktop/Services/OfflineBattleSession.cs").read_text(encoding="utf-8")
    view = (ROOT / "src/Desktop/ViewModels/MainWindowViewModel.Session.cs").read_text(encoding="utf-8")
    assert "T7R_RUST_BATTLE_DEV" not in session + view
    assert "DevelopmentModeEnabled" not in session + view
    trace = (ROOT / "src/BattleRust/runtime/src/gateway/trace.rs").read_text(encoding="utf-8")
    assert "T7R_RUST_BATTLE_DEV" not in trace


def test_desktop_includes_rust_and_no_python_copy_targets():
    project = (ROOT / "src/Desktop/T7R.Desktop.csproj").read_text(encoding="utf-8")
    assert "DebugRuntime.targets" not in project
    assert "BusinessScripts.targets" not in project
    targets = (ROOT / "src/Desktop/RuntimeAssets.targets").read_text(encoding="utf-8")
    ET.fromstring(targets)
    assert "RustRuntime.targets" in targets
    assert "t7r_battle_ffi.dll" in (ROOT / "RustRuntime.targets").read_text(encoding="utf-8")
    assert "PythonHome" not in targets


def test_business_oracle_is_test_only():
    assert not (ROOT / "src/Business").exists()
    assert (ROOT / "tests/reference/Business/scripts/__init__.py").is_file()
    assert (ROOT / "src/Desktop/Resources/News/index.html").is_file()
