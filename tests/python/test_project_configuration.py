import json
import os
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest

from test_build import load_build


ROOT = Path(__file__).resolve().parents[2]
SDK_VERSION = json.loads((ROOT / "global.json").read_text(encoding="utf-8"))["sdk"]["version"]


@pytest.fixture(scope="module")
def msbuild():
    if os.name != "nt":
        pytest.skip("MSBuild configuration tests require Windows")
    try:
        return load_build().locate_msbuild(require_native=False)
    except RuntimeError as error:
        pytest.skip(str(error))


def write_sdk(directory, root, marker):
    sdk = directory / "Microsoft.NET.Sdk/Sdk"
    sdk.mkdir(parents=True)
    for suffix in ("props", "targets"):
        project = ET.Element("Project")
        properties = ET.SubElement(project, "PropertyGroup")
        ET.SubElement(properties, f"TestSdk{suffix.title()}").text = marker
        if suffix == "props":
            ET.SubElement(project, "Import", Project=str(root / "Directory.Build.props"))
        ET.ElementTree(project).write(sdk / f"Sdk.{suffix}", encoding="utf-8")


@pytest.fixture
def configuration(tmp_path):
    for name in ("Directory.Build.props", "Managed.Common.props", "Managed.Common.targets"):
        shutil.copy2(ROOT / name, tmp_path / name)
    project = tmp_path / "src/Core/T7.Core.csproj"
    project.parent.mkdir(parents=True)
    shutil.copy2(ROOT / "src/Core/T7.Core.csproj", project)
    fallback = tmp_path / "system-sdk"
    write_sdk(fallback, tmp_path, "system")
    environment = {"MSBuildSDKsPath": str(fallback),
                   "DOTNET_MSBUILD_SDK_RESOLVER_SDKS_DIR": str(fallback)}
    return tmp_path, project, environment


def evaluate(msbuild, configuration, *arguments):
    root, project, overrides = configuration
    cleared = {"MSBUILDSDKSPATH", "MSBUILDENABLEWORKLOADRESOLVER", "PYTHONHOME",
               "TARGETFRAMEWORKROOTPATH", "NUGET_PACKAGES", "RESTOREPACKAGESPATH"}
    environment = {key: value for key, value in os.environ.items() if key.upper() not in cleared
                   and not key.upper().startswith("DOTNET_MSBUILD_SDK_RESOLVER_")}
    environment.update(overrides)
    properties = ("TestSdkProps,TestSdkTargets,PythonHome,TargetFrameworkRootPath,"
                  "RestorePackagesPath,RestoreLockedMode,MSBuildEnableWorkloadResolver")
    result = subprocess.run(
        [str(msbuild), str(project), "/nologo", "/nr:false", f"-getProperty:{properties}", *arguments],
        cwd=root, env=environment, capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)["Properties"]


def test_portable_sdk_is_selected_before_environment_sdk(msbuild, configuration):
    root, _, _ = configuration
    sdk = root / f".local/toolchains/dotnet-{SDK_VERSION}/sdk/{SDK_VERSION}/Sdks"
    write_sdk(sdk, root, "portable")
    values = evaluate(msbuild, configuration)
    assert values["TestSdkProps"] == values["TestSdkTargets"] == "portable"
    assert values["MSBuildEnableWorkloadResolver"] == "false"
    assert values["RestoreLockedMode"] == "true"
    assert Path(values["RestorePackagesPath"]) == root / ".local/toolchains/nuget-packages"


def test_projects_fall_back_to_installed_sdk_without_local_toolchain(msbuild, configuration):
    values = evaluate(msbuild, configuration)
    assert values["TestSdkProps"] == values["TestSdkTargets"] == "system"
    assert values["TargetFrameworkRootPath"] == ""
    assert values["RestorePackagesPath"] == ""


def test_local_python_setting_and_msbuild_override(msbuild, configuration):
    root, _, _ = configuration
    local = root / ".local/Build.props"
    local.parent.mkdir()
    local.write_text(
        '<Project><PropertyGroup><PythonHome Condition="\'$(PythonHome)\' == \'\'">'
        'LOCAL_PYTHON</PythonHome></PropertyGroup></Project>', encoding="utf-8",
    )
    assert evaluate(msbuild, configuration)["PythonHome"] == "LOCAL_PYTHON"
    assert evaluate(msbuild, configuration, "/p:PythonHome=COMMAND_PYTHON")["PythonHome"] == "COMMAND_PYTHON"


def test_portable_targeting_pack_preserves_explicit_settings(msbuild, configuration):
    root, _, environment = configuration
    framework = root / ".local/toolchains/net48-reference-assemblies/build"
    assembly = framework / ".NETFramework/v4.8/PresentationFramework.dll"
    assembly.parent.mkdir(parents=True)
    assembly.touch()
    assert Path(evaluate(msbuild, configuration)["TargetFrameworkRootPath"]) == framework
    environment["TargetFrameworkRootPath"] = "EXPLICIT_FRAMEWORK"
    assert evaluate(msbuild, configuration)["TargetFrameworkRootPath"] == "EXPLICIT_FRAMEWORK"


def test_portable_sdk_preserves_explicit_package_cache(msbuild, configuration):
    root, _, environment = configuration
    sdk = root / f".local/toolchains/dotnet-{SDK_VERSION}/sdk/{SDK_VERSION}/Sdks"
    write_sdk(sdk, root, "portable")
    environment["NUGET_PACKAGES"] = "EXPLICIT_CACHE"
    assert evaluate(msbuild, configuration)["RestorePackagesPath"] == ""
    values = evaluate(msbuild, configuration, "/p:RestorePackagesPath=COMMAND_CACHE")
    assert values["RestorePackagesPath"] == "COMMAND_CACHE"


def test_managed_projects_use_matching_explicit_sdk_imports():
    for path in ("src/Core/T7.Core.csproj", "src/Desktop/T7.Desktop.csproj",
                 "tests/managed/T7.ManagedHarness.csproj"):
        project = ET.parse(ROOT / path).getroot()
        assert "Sdk" not in project.attrib
        assert project[0].get("Project") == r"..\..\Managed.Common.props"
        assert project[-1].get("Project") == r"..\..\Managed.Common.targets"
        assert project.findtext("PropertyGroup/TargetFramework") == "net48"
