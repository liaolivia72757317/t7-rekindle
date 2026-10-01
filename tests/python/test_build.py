import importlib.util
import json
from pathlib import Path
from unittest.mock import patch
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]


def load_build():
    spec = importlib.util.spec_from_file_location("t7_build", ROOT / "scripts/build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_managed_build_restores_in_locked_mode():
    build = load_build()
    with patch.object(build.subprocess, "run") as run:
        build.run_msbuild(ROOT / "src/Desktop/T7.Desktop.csproj", "x64", Path("MSBuild.exe"), None)
    command = run.call_args.args[0]
    assert "/restore" in command
    assert "/p:RestoreLockedMode=true" in command


def test_native_build_does_not_restore_nuget():
    build = load_build()
    with patch.object(build.subprocess, "run") as run:
        build.run_msbuild(ROOT / "src/Runtime/T7.NativeBridge.vcxproj", "x64", Path("MSBuild.exe"), None)
    assert "/restore" not in run.call_args.args[0]


def test_managed_msbuild_discovery_does_not_require_vc(tmp_path):
    build = load_build()
    build.MSBUILD_CANDIDATES = ()
    build.VSWHERE = tmp_path / "vswhere.exe"
    build.VSWHERE.touch()
    expected = tmp_path / "VS/MSBuild/Current/Bin/MSBuild.exe"
    expected.parent.mkdir(parents=True)
    expected.touch()
    for native in (False, True):
        with patch.object(build.subprocess, "check_output", return_value=str(tmp_path / "VS")) as query:
            assert build.locate_msbuild(require_native=native) == expected
        assert ("Microsoft.VisualStudio.Component.VC.Tools.x86.x64" in query.call_args.args[0]) == native


def test_managed_directories_are_project_scoped_and_docs_are_optional():
    root = ET.parse(ROOT / "Directory.Build.props").getroot()
    values = {node.tag: node.text for node in root.iter()}
    assert "$(MSBuildProjectName)" in values["BaseOutputPath"]
    assert "$(MSBuildProjectName)" in values["BaseIntermediateOutputPath"]
    assert values.get("GenerateDocumentationFile") != "true"
    assert values["RestoreLockedMode"] == "true"


def test_each_managed_project_has_a_complete_nuget_lock_file():
    for project in (ROOT / "src/Core", ROOT / "src/Desktop", ROOT / "tests/managed"):
        lock = json.loads((project / "packages.lock.json").read_text(encoding="utf-8"))
        for dependencies in lock["dependencies"].values():
            for dependency in dependencies.values():
                if dependency["type"] != "Project":
                    assert len(dependency["contentHash"]) == 88
