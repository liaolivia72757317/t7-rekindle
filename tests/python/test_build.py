import importlib.util
import json
from pathlib import Path
from unittest.mock import patch
import xml.etree.ElementTree as ET

import pytest


ROOT = Path(__file__).resolve().parents[2]


def load_build():
    spec = importlib.util.spec_from_file_location("t7r_build", ROOT / "scripts/build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_managed_build_restores_in_locked_mode():
    build = load_build()
    with patch.object(build.subprocess, "run") as run:
        build.run_msbuild(ROOT / "src/Desktop/T7R.Desktop.csproj", "x64", Path("MSBuild.exe"), None)
    command = run.call_args.args[0]
    assert "/restore" in command
    assert "/p:RestoreLockedMode=true" in command


def test_native_build_does_not_restore_nuget():
    build = load_build()
    with patch.object(build.subprocess, "run") as run:
        build.run_msbuild(ROOT / "src/Runtime/T7R.NativeBridge.vcxproj", "x64", Path("MSBuild.exe"), None)
    assert "/restore" not in run.call_args.args[0]


@pytest.mark.parametrize("configuration", ["Debug", "Release"])
def test_msbuild_receives_selected_configuration(configuration):
    build = load_build()
    with patch.object(build.subprocess, "run") as run:
        build.run_msbuild(ROOT / "src/Desktop/T7R.Desktop.csproj", "x64",
                          Path("MSBuild.exe"), None, configuration)
    assert f"/p:Configuration={configuration}" in run.call_args.args[0]


@pytest.mark.parametrize("project,count", [("all", 9), ("native", 1),
                                         ("native-tests", 4), ("managed", 5),
                                         ("battle", 3), ("battle-tests", 3)])
@pytest.mark.parametrize("configuration", [None, "Debug", "Release"])
def test_build_configuration_reaches_all_projects(monkeypatch, project, count, configuration):
    build = load_build()
    arguments = ["build.py", "--project", project]
    if configuration:
        arguments += ["--configuration", configuration]
    monkeypatch.setattr(build.sys, "argv", arguments)
    for name in ("locate_msbuild", "check_python", "check_managed_sdk", "check_wpf_targeting_pack"):
        monkeypatch.setattr(build, name, lambda *args, **kwargs: Path("TOOLCHAIN"))
    with patch.object(build, "run_msbuild") as run, patch.object(build, "run_native_tests") as tests, \
            patch.object(build, "run_battle_tests") as battle_tests:
        build.main()
    selected = configuration or "Release"
    assert len(run.call_args_list) == count
    assert all(call.args[-1] == selected for call in run.call_args_list)
    if project in ("all", "native-tests"):
        tests.assert_called_once_with(Path("TOOLCHAIN"), selected)
    else:
        tests.assert_not_called()
    if project in ("all", "battle-tests"):
        battle_tests.assert_called_once_with(selected)
    else:
        battle_tests.assert_not_called()


@pytest.mark.parametrize("project", ["native", "managed", "battle", "battle-tests"])
def test_product_build_does_not_require_cpython_sdk(monkeypatch, project):
    build = load_build()
    monkeypatch.setattr(build.sys, "argv", ["build.py", "--project", project])
    with (patch.object(build, "locate_msbuild"), patch.object(build, "check_python") as python,
          patch.object(build, "check_managed_sdk"), patch.object(build, "check_wpf_targeting_pack"),
          patch.object(build, "run_msbuild"), patch.object(build, "run_battle_tests")):
        build.main()
    python.assert_not_called()


@pytest.mark.parametrize("configuration", ["Debug", "Release"])
def test_native_tests_run_from_selected_output(tmp_path, configuration):
    build = load_build()
    build.ROOT = tmp_path
    with patch.object(build, "prepare_runtime_fixture", return_value=tmp_path / "fixture"), \
            patch.object(build, "remove_tree") as remove, patch.object(build.subprocess, "run") as run:
        build.run_native_tests(tmp_path / "python", configuration)
    assert [Path(call.args[0][0]) for call in run.call_args_list] == [
        tmp_path / "artifacts/native/bin/x64" / configuration / name
        for name in ("T7R.NativeTests.exe", "T7R.BridgeTests.exe", "T7R.RuntimeTests.exe")]
    remove.assert_called_once_with(tmp_path / "fixture")


@pytest.mark.parametrize("embedded", [False, True])
def test_runtime_fixture_includes_standard_library_extensions(tmp_path, embedded):
    build = load_build()
    build.ROOT = tmp_path
    business = tmp_path / "tests/reference/Business"
    business.mkdir(parents=True)
    (business / "sample.py").write_text("", encoding="utf-8")
    (business / "scripts").mkdir()
    (business / "scripts/navmesh_resource.py").write_text('NAV_HASH = "production"\n', encoding="utf-8")
    resource_fixture = tmp_path / "tests/python/vfs_fixture.py"
    resource_fixture.parent.mkdir(parents=True)
    resource_fixture.write_bytes((ROOT / "tests/python/vfs_fixture.py").read_bytes())
    python_home = tmp_path / "toolchain"
    python_home.mkdir()
    if embedded:
        (python_home / "python314.zip").write_bytes(b"stdlib")
        extensions = python_home
    else:
        (python_home / "Lib").mkdir()
        (python_home / "Lib/socket.py").write_text("import _socket", encoding="utf-8")
        extensions = python_home / "DLLs"
        extensions.mkdir()
    for name in ("_socket.pyd", "_multiprocessing.pyd", "libcrypto-3.dll"):
        (extensions / name).write_bytes(name.encode("ascii"))
    (python_home / "python314.dll").write_bytes(b"runtime")

    fixture = build.prepare_runtime_fixture(python_home)

    for name in ("_socket.pyd", "_multiprocessing.pyd", "libcrypto-3.dll"):
        assert (fixture / "python" / name).read_bytes() == name.encode("ascii")
    assert (fixture / "python314.dll").read_bytes() == b"runtime"
    assert not (fixture / "python/python314.dll").exists()
    assert (fixture / "synthetic-client/vfs/map.vfs").read_bytes()[:4] == bytes.fromhex("9010ba22")
    assert (business / "scripts/navmesh_resource.py").read_text(encoding="utf-8") == 'NAV_HASH = "production"\n'
    assert (fixture / 'Business/scripts/navmesh_resource.py').read_bytes() == (business / 'scripts/navmesh_resource.py').read_bytes()
    for name, key, count in [('map_data.py', 'SPAWNS', 2), ('map_navigation_data.py', 'FACES', 2)]:
        namespace = {}
        exec((fixture / 'Business/scripts' / name).read_text(encoding='utf-8'), namespace)
        assert len(namespace[key]) == count


def test_native_tests_support_matching_solution_configurations():
    namespace = {"m": "http://schemas.microsoft.com/developer/msbuild/2003"}
    solution = (ROOT / "T7-Rekindle.sln").read_text(encoding="utf-8")
    for name in ("T7R.NativeTests", "T7R.BridgeTests", "T7R.RuntimeTests"):
        project = ET.parse(ROOT / "tests/cpp" / f"{name}.vcxproj").getroot()
        configurations = {item.get("Include") for item in project.findall(".//m:ProjectConfiguration", namespace)}
        assert configurations == {"Debug|x64", "Release|x64"}
        guid = project.findtext(".//m:ProjectGuid", namespaces=namespace)
        for configuration in ("Debug", "Release"):
            for mapping in ("ActiveCfg", "Build.0"):
                assert f"{guid}.{configuration}|x64.{mapping} = {configuration}|x64" in solution


def test_world_hook_projects_include_combat_lookup():
    namespace = {"m": "http://schemas.microsoft.com/developer/msbuild/2003"}
    for directory in (ROOT / "src/Runtime", ROOT / "tests/cpp"):
        for path in directory.glob("*.vcxproj"):
            project = ET.parse(path).getroot()
            sources = {item.get("Include", "").replace("\\", "/").split("/")[-1]
                       for item in project.findall(".//m:ClCompile", namespace)}
            if "WorldHookCode.cpp" in sources:
                assert "CombatProfileHookCode.cpp" in sources, path.name


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
    project = ET.parse(ROOT / "src/Desktop/T7R.Desktop.csproj").getroot()
    platforms = project.findtext("PropertyGroup/Platforms", default="AnyCPU").split(";")
    assert "x64" in platforms
    assert project.findtext("PropertyGroup/PlatformTarget") == "x64"


def test_solution_builds_native_bridge_before_desktop():
    namespace = {"m": "http://schemas.microsoft.com/developer/msbuild/2003"}
    native = ET.parse(ROOT / "src/Runtime/T7R.NativeBridge.vcxproj").getroot()
    guid = native.findtext(".//m:ProjectGuid", namespaces=namespace)
    solution = (ROOT / "T7-Rekindle.sln").read_text(encoding="utf-8")
    desktop = next(project for project in solution.split("\nEndProject\n")
                   if ' = "T7R.Desktop",' in project)
    section = "ProjectSection(ProjectDependencies) = postProject"
    assert section in desktop, "Desktop must wait for NativeBridge before copying its DLL"
    dependencies = desktop.split(section, 1)[1].split("EndProjectSection", 1)[0]
    assert f"{guid} = {guid}" in dependencies


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


def test_ci_passes_server_base_url_from_repository_variables():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    environment = workflow.split("\nenv:\n", 1)[1].split("\njobs:\n", 1)[0]
    assert "  T7R_SERVER_BASE_URL: ${{ vars.T7R_SERVER_BASE_URL }}\n" in environment


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


def test_ci_exposes_pinned_inno_setup_before_python_tests():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    setup = next(step for step in workflow.split("      - name: ")
                 if step.startswith("Set up Inno Setup 7.1.0\n"))
    export = "Add-Content -LiteralPath $env:GITHUB_PATH -Value $innoDirectory -Encoding utf8"
    assert '$innoDirectory = Join-Path $env:RUNNER_TEMP "InnoSetup"' in setup
    assert export in setup
    assert setup.index("if ($process.ExitCode -ne 0)") < setup.index(export)
    assert workflow.index("Set up Inno Setup 7.1.0") < workflow.index("Run Python tests")


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
