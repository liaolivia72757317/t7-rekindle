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
ARTIFACTS = None


def artifact_root() -> Path:
    return ARTIFACTS if ARTIFACTS is not None else ROOT / "artifacts"


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
               f"/p:Platform={platform}", f"/p:T7RArtifactsRoot={artifact_root()}/", "/v:minimal"]
    if project.suffix == ".csproj":
        command.extend(["/restore", "/p:RestoreLockedMode=true"])
    if python_home is not None:
        command.insert(-1, f"/p:PythonHome={python_home}")
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def prepare_runtime_fixture(python_home: Path) -> Path:
    artifacts = artifact_root() / "tmp"
    artifacts.mkdir(parents=True, exist_ok=True)
    fixture = Path(tempfile.mkdtemp(prefix="runtime-fixture-", dir=artifacts))
    try:
        shutil.copytree(ROOT / "tests/reference/Business", fixture / "Business",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        import importlib.util
        spec = importlib.util.spec_from_file_location('vfs_fixture', ROOT / 'tests/python/vfs_fixture.py')
        resources = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(resources)
        resources.writeClient(fixture / 'synthetic-client')
        previous_path = list(sys.path)
        try:
            sys.path[:0] = [str(ROOT / 'tests/reference'), str(ROOT / 'tests/reference/Business/vendor'), str(ROOT / 'tests/python')]
            from loadout_fixture import writeResources, writeStaticCombatProfiles, writeStaticDamageProfiles
            writeResources(fixture / 'synthetic-client')
            writeStaticCombatProfiles(fixture / 'Business/scripts/combat_profile_data.py')
            writeStaticDamageProfiles(fixture / 'Business/scripts/damage_data.py')
            resources.writeStaticMapData(fixture / 'Business/scripts')
        finally:
            sys.path[:] = previous_path
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
        runtime_names = ("python314.dll", "python3.dll", "vcruntime140.dll", "vcruntime140_1.dll")
        extensions = python_home / "DLLs" if (python_home / "DLLs").is_dir() else python_home
        # Match the packaged runtime layout, including stdlib extension dependencies.
        for source in extensions.iterdir():
            if source.is_file() and source.suffix.lower() in (".pyd", ".dll") and source.name not in runtime_names:
                shutil.copy2(source, python_root / source.name)
        for name in runtime_names:
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
    binary = artifact_root() / "native/bin/x64" / configuration
    fixture = prepare_runtime_fixture(python_home)
    environment = os.environ.copy()
    environment["PATH"] = str(python_home) + os.pathsep + environment.get("PATH", "")
    environment["LOCALAPPDATA"] = str(fixture / "state")
    try:
        for name in ("T7R.NativeTests.exe", "T7R.BridgeTests.exe"):
            subprocess.run([str(binary / name)], cwd=ROOT, env=environment, check=True)
        subprocess.run([str(binary / "T7R.RuntimeTests.exe"), str(fixture)],
                       cwd=ROOT, env=environment, check=True)
    finally:
        remove_tree(fixture)


def run_battle_tests(configuration: str = "Release") -> None:
    artifacts = artifact_root()
    (artifacts / 'tmp').mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='rust-host-', dir=artifacts / 'tmp') as temporary:
        fixture = Path(temporary)
        client = fixture / 'synthetic-client'
        (client / 'Bin').mkdir(parents=True)
        (client / 'vfs').mkdir()
        (client / 'update.lst').write_text('1.0.0\n', encoding='utf-8')
        shutil.copy2(ROOT / 'tests/fixtures/resources/client-cache-v1.vfs', client / 'vfs/data1.vfs')
        rust = artifacts / 'rust/x86_64-pc-windows-msvc' / ('release' if configuration == 'Release' else 'debug') / 't7r_battle_ffi.dll'
        shutil.copy2(rust, fixture / rust.name)
        harness = artifacts / 'bin/T7R.BattleHarness/x64' / configuration / 'net48/T7R.BattleHarness.exe'
        subprocess.run([str(harness), str(fixture)], cwd=ROOT, check=True)


def run_rust(project: str, configuration: str) -> None:
    cargo = shutil.which('cargo')
    if not cargo:
        fail('Cargo was not found; install the pinned Rust toolchain separately')
    workspace = ROOT / 'src/BattleRust'
    environment = dict(os.environ, CARGO_TARGET_DIR=str(artifact_root() / 'rust'))
    profile = ['--release'] if configuration == 'Release' else []
    if project == 'rust-tests':
        commands = [[cargo, 'fmt', '--all', '--check'],
                    [cargo, 'clippy', '--locked', '--workspace', '--all-targets', *profile, '--', '-D', 'warnings'],
                    [cargo, 'test', '--locked', '--workspace', *profile]]
    else:
        commands = [[cargo, 'build', '--locked', '--workspace', *profile]]
    for command in commands:
        subprocess.run(command, cwd=workspace, env=environment, check=True)
    if project == 'rust-bridge-tests':
        msbuild = locate_msbuild(require_native=True)
        run_msbuild(ROOT / 'tests/cpp/T7R.RustBridgeTests.vcxproj', 'x64', msbuild, None, configuration)
        binary = artifact_root() / 'native/bin/x64' / configuration / 'T7R.RustBridgeTests.exe'
        library = artifact_root() / 'rust/x86_64-pc-windows-msvc' / ('release' if configuration == 'Release' else 'debug') / 't7r_battle_ffi.dll'
        subprocess.run([str(binary)], cwd=ROOT, env=dict(environment, T7R_BATTLE_FFI_DLL=str(library)), check=True)
    if project == 'rust-gateway-tests':
        msbuild = locate_msbuild(require_native=True)
        check_managed_sdk(msbuild)
        run_msbuild(ROOT / 'tests/cpp/T7R.RustGatewayFixture.vcxproj', 'x64', msbuild, None, configuration)
        run_msbuild(ROOT / 'tests/battle/T7R.BattleHarness.csproj', 'x64', msbuild, None, configuration)
        binary = artifact_root() / 'bin/T7R.BattleHarness/x64' / configuration / 'net48/T7R.BattleHarness.exe'
        library = artifact_root() / 'native/bin/x64' / configuration / 'T7R.RustGatewayFixture.dll'
        subprocess.run([str(binary), '--rust-gateway', str(library)], cwd=ROOT, check=True)
        direct = artifact_root() / 'rust/x86_64-pc-windows-msvc' / ('release' if configuration == 'Release' else 'debug') / 't7r_battle_ffi.dll'
        subprocess.run([str(binary), '--rust-gateway', str(direct)], cwd=ROOT, check=True)


def main() -> None:
    global ARTIFACTS
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, help="independent build output root")
    parser.add_argument("--project", choices=("all", "native", "native-tests", "managed", "battle", "battle-tests",
                                              "rust", "rust-tests", "rust-bridge-tests", "rust-gateway-tests"), default="all")
    parser.add_argument("--configuration", choices=("Debug", "Release"), default="Release")
    args = parser.parse_args()
    ARTIFACTS = args.artifacts.resolve() if args.artifacts else None
    if args.project.startswith('rust'):
        run_rust(args.project, args.configuration)
        print(f"Rust build completed: {args.configuration}/x64")
        return
    msbuild = locate_msbuild(require_native=args.project != "managed")
    python_home = check_python() if args.project in ("all", "native-tests") else None
    if args.project in ("all", "native", "native-tests", "battle", "battle-tests"):
        run_msbuild(ROOT / "src/Runtime/T7R.NativeBridge.vcxproj", "x64", msbuild, None, args.configuration)
    if args.project in ("all", "native-tests"):
        run_msbuild(ROOT / "tests/cpp/T7R.NativeTests.vcxproj", "x64", msbuild, python_home, args.configuration)
        run_msbuild(ROOT / "tests/cpp/T7R.BridgeTests.vcxproj", "x64", msbuild, python_home, args.configuration)
        run_msbuild(ROOT / "tests/cpp/T7R.RuntimeTests.vcxproj", "x64", msbuild, python_home, args.configuration)
        run_native_tests(python_home, args.configuration)
    if args.project in ("all", "managed", "battle", "battle-tests"):
        check_managed_sdk(msbuild)
        if args.project in ("all", "managed"):
            check_wpf_targeting_pack()
            run_msbuild(ROOT / "src/Core/T7R.Core.csproj", "x64", msbuild, python_home, args.configuration)
            run_msbuild(ROOT / "src/Desktop/T7R.Desktop.csproj", "x64", msbuild, python_home, args.configuration)
            run_msbuild(ROOT / "tests/managed/T7R.ManagedHarness.csproj", "x64", msbuild, python_home, args.configuration)
        run_msbuild(ROOT / "src/BattleServer/T7R.BattleServer.csproj", "x64", msbuild, python_home, args.configuration)
        run_msbuild(ROOT / "tests/battle/T7R.BattleHarness.csproj", "x64", msbuild, python_home, args.configuration)
    if args.project in ("all", "battle-tests"):
        run_battle_tests(args.configuration)
    print(f"build completed: {args.configuration}/x64")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print(f"build error: {error}", file=sys.stderr)
        raise SystemExit(1)
