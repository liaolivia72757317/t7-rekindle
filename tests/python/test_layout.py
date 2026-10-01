from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest


ROOT = Path(__file__).resolve().parents[2]
MSBUILD = {"ms": "http://schemas.microsoft.com/developer/msbuild/2003"}


@pytest.mark.parametrize("module", ["Core", "Desktop", "Runtime", "Business"])
def test_product_sources_live_under_src(module):
    assert (ROOT / "src" / module).is_dir()
    assert not (ROOT / module).exists()


@pytest.mark.parametrize("script", ["build.py", "package.py", "integration_test.py"])
def test_script_entrypoints_work_outside_repository(script, tmp_path):
    assert not (ROOT / script).exists()
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script), "--help"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout


def test_native_outputs_are_configuration_scoped_and_not_overridden():
    root = ET.parse(ROOT / "Native.Common.props").getroot()
    output = root.findtext("ms:PropertyGroup/ms:OutDir", namespaces=MSBUILD)
    intermediate = root.findtext("ms:PropertyGroup/ms:IntDir", namespaces=MSBUILD)
    assert output == "$(MSBuildThisFileDirectory)artifacts\\native\\bin\\$(Platform)\\$(Configuration)\\"
    assert intermediate == "$(MSBuildThisFileDirectory)artifacts\\native\\obj\\$(ProjectName)\\$(Platform)\\$(Configuration)\\"
    projects = [ROOT / "src/Runtime/T7.NativeBridge.vcxproj"]
    projects.extend((ROOT / "tests/cpp").glob("*.vcxproj"))
    for project in projects:
        document = ET.parse(project).getroot()
        assert document.find(".//ms:OutDir", MSBUILD) is None, project
        assert document.find(".//ms:IntDir", MSBUILD) is None, project


def test_local_material_is_excluded_from_source_control():
    rules = set((ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())
    assert {"/.local/", "/.claude/", "/artifacts/", "/dist/", "work/"} <= rules
    assert {"src/Business/data/", "src/Business/cache/", "src/Runtime/data/"} <= rules
