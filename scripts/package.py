"""Create a clean product directory without client or server products."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
from release_metadata import package_build_metadata
from rollback_history import build_rollback_metadata

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PRODUCT_NAMES = {
    "t7.server.exe", "server.exe", "server.ini", "tiejiclient.exe", "protocalhandler.dll",
    "t7.core.dll", "t7.nativebridge.dll",
}
RESERVED_PACKAGE_NAMES = {"manifest.json", "license", "third-party.txt"}
WINDOWS_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}
WINDOWS_INVALID_NAME_CHARS = set('<>"|?*')
REQUIRED_RELEASE_FILES = {
    "T7-Rekindle.exe",
    "T7-Rekindle.exe.config",
    "T7R.Core.dll",
    "T7R.Battle.dll",
    "t7r_battle_ffi.dll",
    "news/index.html",
    "CommunityToolkit.Mvvm.dll",
    "Microsoft.Bcl.AsyncInterfaces.dll",
    "Microsoft.Web.WebView2.Core.dll",
    "Microsoft.Web.WebView2.Wpf.dll",
    "WebView2Loader.dll",
    "Newtonsoft.Json.dll",
    "NLog.config",
    "NLog.dll",
    "System.Buffers.dll",
    "System.ComponentModel.Annotations.dll",
    "System.Memory.dll",
    "System.Numerics.Vectors.dll",
    "System.Runtime.CompilerServices.Unsafe.dll",
    "System.Threading.Tasks.Extensions.dll",
    "T7R.NativeBridge.dll",
    "THIRD-PARTY.txt",
}


def is_link_like(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def reject_python_runtime(relative: Path) -> None:
    parts = tuple(part.casefold() for part in relative.parts)
    name = parts[-1]
    if (parts[0] in {"business", "python", "__pycache__"}
            or "__pycache__" in parts or relative.suffix.casefold() in {".py", ".pyc", ".pyd"}
            or name.startswith("python") and relative.suffix.casefold() in {".dll", ".exe", ".zip", "._pth"}):
        raise ValueError(f"Python runtime is not a product dependency: {relative}")


def validate_windows_parts(name: str) -> tuple[str, ...]:
    normalized = name.replace("\\", "/")
    parts = tuple(part for part in normalized.split("/") if part)
    if not parts:
        raise ValueError(f"empty package path: {name}")
    for part in parts:
        if (part in (".", "..") or part.rstrip(" .") != part or ":" in part
                or any(character in WINDOWS_INVALID_NAME_CHARS or ord(character) < 32 for character in part)):
            raise ValueError(f"unsafe Windows package path: {name}")
        if part.split(".", 1)[0].casefold() in WINDOWS_RESERVED_NAMES:
            raise ValueError(f"reserved Windows package path: {name}")
    return parts


def canonical_package_path(parts: tuple[str, ...]) -> str:
    """Return the Windows-equivalent, case-insensitive relative path."""
    return "/".join(part.casefold() for part in parts)


def validate_source_entries(source: Path, paths, label: str) -> None:
    """Reject source trees that cannot be represented consistently on Windows.

    Tests and packaging can run on a case-sensitive filesystem, while the
    product is extracted on Windows.  Detect case-only duplicates and
    file/directory hierarchy collisions before copy operations get a chance to
    silently overwrite one another.
    """
    seen = set()
    seen_files = set()
    seen_has_children = set()
    for path in sorted(paths, key=lambda value: value.relative_to(source).as_posix().casefold()):
        relative = path.relative_to(source).as_posix()
        parts = validate_windows_parts(relative)
        canonical = canonical_package_path(parts)
        if canonical in seen:
            raise ValueError(f"case-insensitive duplicate in {label}: {relative}")
        prefixes = [canonical_package_path(parts[:index]) for index in range(1, len(parts))]
        if any(prefix in seen_files for prefix in prefixes):
            raise ValueError(f"file/directory collision in {label}: {relative}")
        is_directory = path.is_dir()
        if not is_directory and canonical in seen_has_children:
            raise ValueError(f"file/directory collision in {label}: {relative}")
        seen.add(canonical)
        if not is_directory:
            seen_files.add(canonical)
        seen_has_children.update(prefixes)
        reject_python_runtime(path.relative_to(source))
        if path.name.casefold() in FORBIDDEN_PRODUCT_NAMES:
            raise ValueError(f"forbidden product file: {path.name}")


def copy_tree(source: Path, destination: Path) -> None:
    source = source.resolve(strict=True)
    paths = list(source.rglob("*"))
    for path in paths:
        if is_link_like(path):
            raise ValueError(f"symbolic links are not allowed in product sources: {path}")
    validate_source_entries(source, paths, "product sources")
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def copy_product_output(source: Path, destination: Path) -> None:
    paths = list(source.rglob("*"))
    for path in paths:
        if is_link_like(path):
            raise ValueError(f"symbolic links are not allowed in managed output: {path}")
    validate_source_entries(source, paths, "managed output")

    existing = {}
    for path in destination.rglob("*"):
        if path.is_file():
            parts = validate_windows_parts(path.relative_to(destination).as_posix())
            existing[canonical_package_path(parts)] = path
    for path in paths:
        relative = path.relative_to(source)
        if not path.is_file() or path.suffix.lower() == ".pdb":
            continue
        if path.name.casefold() in RESERVED_PACKAGE_NAMES:
            raise ValueError(f"managed output contains package-owned file: {path.name}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        canonical = canonical_package_path(validate_windows_parts(relative.as_posix()))
        previous = existing.get(canonical)
        if previous is not None:
            if canonical in ("t7r.nativebridge.dll", "t7r_battle_ffi.dll") and sha256_file(path) == sha256_file(previous):
                continue
            raise ValueError(f"package source collision: {target.relative_to(destination)}")
        if target.exists():
            raise ValueError(f"package source collision: {target.relative_to(destination)}")
        existing[canonical] = target
        shutil.copy2(path, target)


def remove_tree(path: Path) -> None:
    if not path.exists():
        return

    def onerror(function, target, _error):
        os.chmod(target, stat.S_IWRITE)
        function(target)

    shutil.rmtree(path, onerror=onerror)


def copy_fixed_file(source: Path, destination: Path) -> None:
    """Copy a package-owned file without silently replacing another source."""
    if destination.exists():
        if is_link_like(destination) or not destination.is_file() or sha256_file(source) != sha256_file(destination):
            raise ValueError(f"package source collision: {destination.name}")
        return
    shutil.copy2(source, destination)


def verify_package(output: Path, require_runtime: bool = False, require_license: bool = False) -> dict:
    """Verify a generated product directory and return its manifest."""
    output = Path(output)
    if is_link_like(output) or not output.is_dir():
        raise FileNotFoundError("package output directory is missing")
    output = output.resolve()
    manifest_path = output / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("package manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if any(type(manifest.get(key)) is not int or manifest[key] != value for key, value in build_rollback_metadata().items()):
        raise ValueError("package rollback contract does not match this build")
    if (manifest.get("schemaVersion") != 1
            or manifest.get("product") != "T7-Rekindle"
            or manifest.get("architecture") != "x64"
            or manifest.get("minimumWindowsBuild") != "10.0.19041"
            or manifest.get("requiresDotNet") != "net48"
            or manifest.get("rawWireCaptureDefault") is not False):
        raise ValueError("package manifest schema, platform or runtime policy is invalid")
    if (not isinstance(manifest.get("release"), bool)
            or not isinstance(manifest.get("licenseIncluded"), bool)):
        raise ValueError("package manifest release/license flags are invalid")
    if (require_runtime or require_license) and manifest.get("release") is not True:
        raise ValueError("release verification requires a release manifest")
    if manifest.get("battleRuntime") != "rust" or "python" in manifest or "pythonArchiveSha256" in manifest:
        raise ValueError("package must use the Rust runtime exclusively")
    if manifest.get("clientIncluded") or manifest.get("serverProductIncluded"):
        raise ValueError("package manifest includes a client or server product")
    if not (output / "t7r_battle_ffi.dll").is_file():
        raise FileNotFoundError("package is missing the Rust battle runtime")
    if require_license and (manifest.get("licenseIncluded") is not True
                            or not (output / "LICENSE").is_file()):
        raise FileNotFoundError("release package requires a root LICENSE file")
    if manifest.get("licenseIncluded") is True and not (output / "LICENSE").is_file():
        raise FileNotFoundError("manifest declares a LICENSE that is not present")
    if manifest.get("licenseIncluded") is False and (output / "LICENSE").exists():
        raise ValueError("package contains an unreported LICENSE file")
    if require_runtime:
        missing = sorted(name for name in REQUIRED_RELEASE_FILES if not (output / name).is_file())
        if missing:
            raise FileNotFoundError("release package is missing managed product files: " + ", ".join(missing))

    recorded = manifest.get("files")
    if not isinstance(recorded, dict):
        raise ValueError("package manifest files map is invalid")
    recorded_canonical = set()
    for name, digest in recorded.items():
        if not isinstance(name, str) or not isinstance(digest, str):
            raise ValueError("package manifest files map contains an invalid entry")
        parts = validate_windows_parts(name)
        canonical = canonical_package_path(parts)
        if canonical in recorded_canonical:
            raise ValueError("package manifest contains case-insensitive duplicate paths")
        recorded_canonical.add(canonical)
    actual = {}
    for path in sorted(output.rglob("*")):
        if is_link_like(path):
            raise ValueError(f"package contains a symbolic link: {path}")
        reject_python_runtime(path.relative_to(output))
        if path.name.casefold() in FORBIDDEN_PRODUCT_NAMES:
            raise ValueError(f"forbidden product file in package: {path.name}")
        if not path.is_file() or path == manifest_path:
            continue
        relative = path.relative_to(output).as_posix()
        validate_windows_parts(relative)
        actual[relative] = sha256_file(path)
    if actual != recorded:
        raise ValueError("package manifest hashes do not match product files")
    return manifest


def _package(output: Path, release: bool = False, artifacts: Path | None = None) -> Path:
    output = Path(output)
    if output.exists() or is_link_like(output):
        raise FileExistsError(f"refusing to overwrite existing output: {output}")
    output = output.resolve()
    if output.exists() or is_link_like(output):
        raise FileExistsError(f"refusing to overwrite existing output: {output}")
    artifacts = Path(artifacts) if artifacts is not None else ROOT / "artifacts"
    native = artifacts / "native/bin/x64/Release/T7R.NativeBridge.dll"
    rust = artifacts / "rust/x86_64-pc-windows-msvc/release/t7r_battle_ffi.dll"
    desktop = artifacts / "bin/T7R.Desktop/x64/Release/net48"
    if not native.is_file() or not rust.is_file() or not (desktop / "T7-Rekindle.exe").is_file():
        raise FileNotFoundError("build artifacts are missing; build the native, Rust and desktop projects first")
    output.mkdir(parents=True)
    shutil.copy2(native, output / native.name)
    shutil.copy2(rust, output / rust.name)
    copy_product_output(desktop, output)
    news = output / "news"
    news.mkdir(exist_ok=True)
    for source in (ROOT / "src/Desktop/Resources/News").glob("*"):
        if is_link_like(source):
            raise ValueError("news assets must not be symbolic links")
        if source.is_file():
            copy_fixed_file(source, news / source.name)
    license_path = ROOT / "LICENSE"
    license_included = license_path.is_file()
    if license_included and is_link_like(license_path):
        raise ValueError("root LICENSE must not be a symbolic link")
    copy_fixed_file(ROOT / "THIRD-PARTY.txt", output / "THIRD-PARTY.txt")
    if license_included:
        copy_fixed_file(license_path, output / "LICENSE")
    elif release:
        raise FileNotFoundError("release packaging requires a root LICENSE file")
    manifest = {
        "schemaVersion": 1,
        **build_rollback_metadata(),
        "build": package_build_metadata(os.environ),
        "product": "T7-Rekindle",
        "architecture": "x64",
        "minimumWindowsBuild": "10.0.19041",
        "requiresDotNet": "net48",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "battleRuntime": "rust",
        "clientIncluded": False,
        "serverProductIncluded": False,
        "rawWireCaptureDefault": False,
        "release": release,
        "licenseIncluded": license_included,
        "files": {},
    }
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            manifest["files"][path.relative_to(output).as_posix()] = sha256_file(path)
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    verify_package(output, require_runtime=release, require_license=release)
    return output


def package(output: Path, release: bool = False, artifacts: Path | None = None) -> Path:
    output = Path(output).resolve()
    existed = output.exists() or output.is_symlink()
    try:
        return _package(output, release, artifacts)
    except Exception:
        if not existed and output.is_dir() and not output.is_symlink():
            remove_tree(output)
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, help="build artifact root (default: artifacts)")
    parser.add_argument("--release", action="store_true",
                        help="require complete Rust product files and a root LICENSE")
    parser.add_argument("--verify", action="store_true",
                        help="verify an existing product directory instead of creating it")
    args = parser.parse_args()
    if args.verify:
        print(json.dumps(verify_package(args.output.resolve(), args.release, args.release), indent=2))
    else:
        print(package(args.output, args.release, args.artifacts))


if __name__ == "__main__":
    main()
