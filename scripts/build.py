"""Unified x64 build entry point; never installs missing system components."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
VSWHERE = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft Visual Studio/Installer/vswhere.exe"
MSBUILD_CANDIDATES = (
    Path(r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\MSBuild.exe"),
    Path(r"C:\Program Files\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\MSBuild.exe"),
)


def fail(message: str) -> "NoReturn":
    raise RuntimeError(message)


def locate_msbuild(require_native: bool = True) -> Path:
    if VSWHERE.is_file():
        requirements = ["Microsoft.Component.MSBuild"]
        if require_native:
            requirements.append("Microsoft.VisualStudio.Component.VC.Tools.x86.x64")
        installation = subprocess.check_output(
            [str(VSWHERE), "-latest", "-products", "*", "-version", "[17.0,18.0)",
             "-requires", *requirements, "-property", "installationPath"],
            text=True,
        ).strip()
        candidate = Path(installation) / "MSBuild/Current/Bin/MSBuild.exe"
        if candidate.is_file():
            return candidate
    else:
        for candidate in MSBUILD_CANDIDATES:
            if candidate.is_file():
                return candidate
    workload = " with the v143 C++ workload" if require_native else ""
    fail(f"VS 2022 MSBuild{workload} was not found")


def check_python() -> Path:
    if sys.version_info[:3] != (3, 14, 4) or sys.maxsize <= 2**32:
        fail("CPython 3.14.4 AMD64 is required; no automatic Python installation is performed")
    home = Path(sys.base_prefix)
    if not (home / "Include/Python.h").is_file():
        fail(f"Python headers missing: {home / 'Include/Python.h'}")
    if not (home / "libs/python314.lib").is_file():
        fail(f"Python import library missing: {home / 'libs/python314.lib'}")
    if not (home / "python314.dll").is_file():
        fail(f"CPython runtime DLL missing: {home / 'python314.dll'}")
    if not ((home / "python314.zip").is_file() or (home / "Lib").is_dir()):
        fail(f"CPython standard library missing: {home / 'python314.zip'} or {home / 'Lib'}")
    return home


def check_wpf_targeting_pack() -> None:
    roots = [
        ROOT / ".local/toolchains/net48-reference-assemblies/build/.NETFramework/v4.8",
        Path(r"C:\Program Files (x86)\Reference Assemblies\Microsoft\Framework\.NETFramework\v4.8"),
        Path(r"C:\Program Files\Reference Assemblies\Microsoft\Framework\.NETFramework\v4.8"),
    ]
    if os.environ.get("TargetFrameworkRootPath"):
        roots.insert(0, Path(os.environ["TargetFrameworkRootPath"]) / ".NETFramework/v4.8")
    if not any((root / "PresentationFramework.dll").is_file() for root in roots):
        fail(".NET Framework 4.8 WPF targeting pack is missing; install it separately and rerun build.py")


def check_managed_sdk(msbuild: Path) -> None:
    sdk = msbuild.parent.parent / "Sdks/Microsoft.NET.Sdk/Sdk/Sdk.props"
    version = json.loads((ROOT / "global.json").read_text(encoding="utf-8"))["sdk"]["version"]
    portable_sdk = ROOT / f".local/toolchains/dotnet-{version}/sdk/{version}/Sdks/Microsoft.NET.Sdk/Sdk/Sdk.props"
    dotnet = shutil.which("dotnet")
    if not sdk.is_file() and not portable_sdk.is_file() and not dotnet:
        fail(".NET SDK / Microsoft.NET.Sdk targets are missing; install a supported .NET SDK separately")


def run_msbuild(project: Path, platform: str, msbuild: Path, python_home: Path | None,
                configuration: str = "Release") -> None:
    env = {key.upper(): value for key, value in os.environ.items()}
    command = [str(msbuild), str(project), "/nologo", "/m", f"/p:Configuration={configuration}",
               f"/p:Platform={platform}", "/v:minimal"]
    if project.suffix == ".csproj":
        command.extend(["/restore", "/p:RestoreLockedMode=true"])
    if python_home is not None:
        command.insert(-1, f"/p:PythonHome={python_home}")
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def prepare_runtime_fixture(python_home: Path) -> Path:
    artifacts = ROOT / "artifacts/tmp"
    artifacts.mkdir(parents=True, exist_ok=True)
    fixture = Path(tempfile.mkdtemp(prefix="runtime-fixture-", dir=artifacts))
    try:
        shutil.copytree(ROOT / "src/Business", fixture / "Business",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        python_root = fixture / "python"
        python_root.mkdir()
        archive = python_home / "python314.zip"
        library = python_home / "Lib"
        if archive.is_file():
            shutil.copy2(archive, python_root / archive.name)
        elif library.is_dir():
            shutil.copytree(library, python_root / "Lib",
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            fail("CPython standard library archive or Lib directory is missing")
        for name in ("python314.dll", "python3.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
            source = python_home / name
            if source.is_file():
                shutil.copy2(source, fixture / name)
        return fixture
    except Exception:
        remove_tree(fixture)
        raise


def remove_tree(path: Path) -> None:
    """Remove a generated fixture, including read-only CPython files."""
    if not path.exists():
        return

    def onerror(function, target, _error):
        os.chmod(target, stat.S_IWRITE)
        function(target)

    shutil.rmtree(path, onerror=onerror)


def run_native_tests(python_home: Path, configuration: str = "Release") -> None:
    binary = ROOT / "artifacts/native/bin/x64" / configuration
    fixture = prepare_runtime_fixture(python_home)
    environment = os.environ.copy()
    environment["PATH"] = str(python_home) + os.pathsep + environment.get("PATH", "")
    environment["LOCALAPPDATA"] = str(fixture / "state")
    try:
        for name in ("T7.NativeTests.exe", "T7.BridgeTests.exe"):
            subprocess.run([str(binary / name)], cwd=ROOT, env=environment, check=True)
        subprocess.run([str(binary / "T7.RuntimeTests.exe"), str(fixture)],
                       cwd=ROOT, env=environment, check=True)
    finally:
        remove_tree(fixture)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", choices=("all", "native", "native-tests", "managed"), default="all")
    parser.add_argument("--configuration", choices=("Debug", "Release"), default="Release")
    args = parser.parse_args()
    msbuild = locate_msbuild(require_native=args.project != "managed")
    python_home = check_python() if args.project in ("all", "native", "native-tests") else None
    if args.project in ("all", "native", "native-tests"):
        run_msbuild(ROOT / "src/Runtime/T7.NativeBridge.vcxproj", "x64", msbuild, python_home, args.configuration)
    if args.project in ("all", "native-tests"):
        run_msbuild(ROOT / "tests/cpp/T7.NativeTests.vcxproj", "x64", msbuild, python_home, args.configuration)
        run_msbuild(ROOT / "tests/cpp/T7.BridgeTests.vcxproj", "x64", msbuild, python_home, args.configuration)
        run_msbuild(ROOT / "tests/cpp/T7.RuntimeTests.vcxproj", "x64", msbuild, python_home, args.configuration)
        run_native_tests(python_home, args.configuration)
    if args.project in ("all", "managed"):
        check_managed_sdk(msbuild)
        check_wpf_targeting_pack()
        run_msbuild(ROOT / "src/Core/T7.Core.csproj", "x64", msbuild, python_home, args.configuration)
        run_msbuild(ROOT / "src/Desktop/T7.Desktop.csproj", "x64", msbuild, python_home, args.configuration)
        run_msbuild(ROOT / "tests/managed/T7.ManagedHarness.csproj", "x64", msbuild, python_home, args.configuration)
    print(f"build completed: {args.configuration}/x64")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print(f"build error: {error}", file=sys.stderr)
        raise SystemExit(1)
