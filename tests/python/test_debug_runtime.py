"""Build assets for both configurations, with no interpreter or script copy path."""
import os
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET
import pytest
from test_build import load_build

ROOT = Path(__file__).resolve().parents[2]

@pytest.mark.skipif(os.name != "nt", reason="MSBuild asset copying is Windows-only")
@pytest.mark.parametrize("configuration", ["Debug", "Release"])
def test_copy_rust_and_native_without_python(tmp_path, configuration):
    artifacts = tmp_path / "artifacts"
    profile = "release" if configuration == "Release" else "debug"
    for path in [artifacts / f"native/bin/x64/{configuration}/T7R.NativeBridge.dll",
                 artifacts / f"rust/x86_64-pc-windows-msvc/{profile}/t7r_battle_ffi.dll"]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(path.name.encode())
    root = ET.Element("Project")
    props = ET.SubElement(root, "PropertyGroup")
    for key, value in {"RepositoryRoot": str(ROOT) + "/", "T7RArtifactsRoot": str(artifacts) + "/",
                       "Configuration": configuration, "PlatformTarget": "x64", "OutputPath": str(tmp_path / "out")}.items():
        ET.SubElement(props, key).text = value
    ET.SubElement(root, "Import", Project=str(ROOT / "src/Desktop/RuntimeAssets.targets"))
    project = tmp_path / "assets.proj"
    ET.ElementTree(root).write(project, encoding="utf-8")
    result = subprocess.run([str(load_build().locate_msbuild(False)), str(project), "/nologo",
        "/t:CopyRustRuntime;CopyNativeBridge", "/v:minimal"], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["T7R.NativeBridge.dll", "t7r_battle_ffi.dll"]
