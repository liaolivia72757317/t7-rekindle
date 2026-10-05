import importlib.util
import json
from pathlib import Path
from unittest.mock import patch
import xml.etree.ElementTree as ET

import pytest


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


@pytest.mark.parametrize("configuration", ["Debug", "Release"])
def test_msbuild_receives_selected_configuration(configuration):
    build = load_build()
    with patch.object(build.subprocess, "run") as run:
        build.run_msbuild(ROOT / "src/Desktop/T7.Desktop.csproj", "x64",
                          Path("MSBuild.exe"), None, configuration)
    assert f"/p:Configuration={configuration}" in run.call_args.args[0]


@pytest.mark.parametrize("project,count", [("all", 7), ("native", 1),
                                         ("native-tests", 4), ("managed", 3)])
@pytest.mark.parametrize("configuration", [None, "Debug", "Release"])
def test_build_configuration_reaches_all_projects(monkeypatch, project, count, configuration):
    build = load_build()
    arguments = ["build.py", "--project", project]
    if configuration:
        arguments += ["--configuration", configuration]
    monkeypatch.setattr(build.sys, "argv", arguments)
    for name in ("locate_msbuild", "check_python", "check_managed_sdk", "check_wpf_targeting_pack"):
        monkeypatch.setattr(build, name, lambda *args, **kwargs: Path("TOOLCHAIN"))
    with patch.object(build, "run_msbuild") as run, patch.object(build, "run_native_tests") as tests:
        build.main()
    selected = configuration or "Release"
    assert len(run.call_args_list) == count
    assert all(call.args[-1] == selected for call in run.call_args_list)
    if project in ("all", "native-tests"):
        tests.assert_called_once_with(Path("TOOLCHAIN"), selected)
    else:
        tests.assert_not_called()


def test_invalid_configuration_stops_before_toolchain_discovery(monkeypatch):
    build = load_build()
    monkeypatch.setattr(build.sys, "argv", ["build.py", "--configuration", "invalid"])
    with patch.object(build, "locate_msbuild") as locate, pytest.raises(SystemExit) as error:
        build.main()
    assert error.value.code == 2
    locate.assert_not_called()


@pytest.mark.parametrize("configuration", ["Debug", "Release"])
def test_native_tests_run_from_selected_output(tmp_path, configuration):
    build = load_build()
    build.ROOT = tmp_path
    with patch.object(build, "prepare_runtime_fixture", return_value=tmp_path / "fixture"), \
            patch.object(build, "remove_tree") as remove, patch.object(build.subprocess, "run") as run:
        build.run_native_tests(tmp_path / "python", configuration)
    assert [Path(call.args[0][0]) for call in run.call_args_list] == [
        tmp_path / "artifacts/native/bin/x64" / configuration / name
        for name in ("T7.NativeTests.exe", "T7.BridgeTests.exe", "T7.RuntimeTests.exe")]
    remove.assert_called_once_with(tmp_path / "fixture")


def test_native_tests_support_matching_solution_configurations():
    namespace = {"m": "http://schemas.microsoft.com/developer/msbuild/2003"}
    solution = (ROOT / "T7-Rekindle.sln").read_text(encoding="utf-8")
    for name in ("T7.NativeTests", "T7.BridgeTests", "T7.RuntimeTests"):
        project = ET.parse(ROOT / "tests/cpp" / f"{name}.vcxproj").getroot()
        configurations = {item.get("Include") for item in project.findall(".//m:ProjectConfiguration", namespace)}
        assert configurations == {"Debug|x64", "Release|x64"}
        guid = project.findtext(".//m:ProjectGuid", namespaces=namespace)
        for configuration in ("Debug", "Release"):
            for mapping in ("ActiveCfg", "Build.0"):
                assert f"{guid}.{configuration}|x64.{mapping} = {configuration}|x64" in solution


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


def test_desktop_declares_the_x64_solution_platform():
    project = ET.parse(ROOT / "src/Desktop/T7.Desktop.csproj").getroot()
    platforms = project.findtext("PropertyGroup/Platforms", default="AnyCPU").split(";")
    assert "x64" in platforms
    assert project.findtext("PropertyGroup/PlatformTarget") == "x64"


def test_each_managed_project_has_a_complete_nuget_lock_file():
    for project in (ROOT / "src/Core", ROOT / "src/Desktop", ROOT / "tests/managed"):
        lock = json.loads((project / "packages.lock.json").read_text(encoding="utf-8"))
        for dependencies in lock["dependencies"].values():
            for dependency in dependencies.values():
                if dependency["type"] != "Project":
                    assert len(dependency["contentHash"]) == 88


def test_managed_sdk_is_pinned_to_the_portable_toolchain():
    sdk = json.loads((ROOT / "global.json").read_text(encoding="utf-8"))["sdk"]
    assert sdk["rollForward"] == "disable"
    assert sdk["allowPrerelease"] is False
    portable = (ROOT / "scripts/Use-ManagedTools.ps1").read_text(encoding="utf-8")
    assert f"'dotnet-{sdk['version']}'" in portable
    assert f"'sdk\\{sdk['version']}\\Sdks'" in portable


def test_ci_installs_the_pinned_sdk_before_building():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    steps = workflow.split("      - name: ")
    sdk_steps = [step for step in steps if "uses: actions/setup-dotnet@" in step]
    assert len(sdk_steps) == 1
    assert "\n          global-json-file: global.json\n" in sdk_steps[0]
    assert "dotnet-version:" not in sdk_steps[0]
    setup = workflow.index("uses: actions/setup-dotnet@")
    assert workflow.index("uses: actions/checkout@") < setup
    assert setup < workflow.index("python scripts/build.py --project native-tests")
    assert setup < workflow.index("python scripts/build.py --project managed")


def test_managed_sdk_check_accepts_portable_sdk_without_system_dotnet(tmp_path):
    build = load_build()
    build.ROOT = tmp_path
    version = json.loads((ROOT / "global.json").read_text(encoding="utf-8"))["sdk"]["version"]
    (tmp_path / "global.json").write_text(json.dumps({"sdk": {"version": version}}), encoding="utf-8")
    sdk = tmp_path / f".local/toolchains/dotnet-{version}/sdk/{version}/Sdks/Microsoft.NET.Sdk/Sdk/Sdk.props"
    sdk.parent.mkdir(parents=True)
    sdk.touch()
    with patch.object(build.shutil, "which", return_value=None):
        build.check_managed_sdk(tmp_path / "MSBuild/Current/Bin/MSBuild.exe")


def test_targeting_pack_check_accepts_portable_reference_assemblies(tmp_path):
    build = load_build()
    build.ROOT = tmp_path
    assembly = tmp_path / ".local/toolchains/net48-reference-assemblies/build/.NETFramework/v4.8/PresentationFramework.dll"
    with patch.object(build.os, "environ", {}), patch.object(Path, "is_file", autospec=True) as is_file:
        is_file.side_effect = lambda path: path == assembly
        build.check_wpf_targeting_pack()
