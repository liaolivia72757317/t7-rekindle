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
import struct
import zipfile
from release_metadata import package_build_metadata

ROOT = Path(__file__).resolve().parents[1]
MAX_ARCHIVE_MEMBERS = 20000
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
# python314.zip from https://www.python.org/ftp/python/3.14.4/python-3.14.4-embed-amd64.zip
# Bytecode magic identifies a minor release, not a patch; pin the standard library too.
PYTHON_STDLIB_SHA256 = "310e5f6ce695f0ab95782a6c4824ac86d793f4f5a79837968a15b64266e484c2"
PYTHON_FILE_VERSION = (3, 14, 4150, 1013)  # VS_FIXEDFILEINFO for CPython 3.14.4 final.
FORBIDDEN_PRODUCT_NAMES = {
    "t7.server.exe", "server.exe", "server.ini", "tiejiclient.exe", "protocalhandler.dll",
}
FORBIDDEN_ARCHIVE_NAMES = FORBIDDEN_PRODUCT_NAMES | {
    "t7-rekindle.exe", "t7.core.dll", "t7.nativebridge.dll", "manifest.json",
    "license", "third-party.txt",
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
    "T7.Core.dll",
    "CommunityToolkit.Mvvm.dll",
    "Microsoft.Bcl.AsyncInterfaces.dll",
    "Newtonsoft.Json.dll",
    "NLog.config",
    "NLog.dll",
    "System.Buffers.dll",
    "System.ComponentModel.Annotations.dll",
    "System.Memory.dll",
    "System.Numerics.Vectors.dll",
    "System.Runtime.CompilerServices.Unsafe.dll",
    "System.Threading.Tasks.Extensions.dll",
    "T7.NativeBridge.dll",
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


def validate_python_dll(path: Path) -> None:
    """Inspect PE headers and the version resource without loading the DLL."""
    data = path.read_bytes()

    def unpack(fmt, offset):
        if offset < 0 or offset + struct.calcsize(fmt) > len(data):
            raise ValueError("truncated Python PE image")
        return struct.unpack_from(fmt, data, offset)

    if data[:2] != b"MZ":
        raise ValueError("Python runtime is not a PE image")
    pe, = unpack("<I", 0x3C)
    if data[pe:pe + 4] != b"PE\0\0":
        raise ValueError("Python runtime PE signature is invalid")
    machine, sections = unpack("<HH", pe + 4)
    optional_size, = unpack("<H", pe + 20)
    optional = pe + 24
    magic, = unpack("<H", optional)
    if machine != 0x8664 or magic != 0x20B:
        raise ValueError("Python runtime must be AMD64 PE32+")
    if optional_size < 136 or unpack("<I", optional + 108)[0] < 3:
        raise ValueError("Python runtime has no resource directory")
    resource_rva, resource_size = unpack("<II", optional + 128)

    def file_offset(rva, size):
        for index in range(sections):
            section = optional + optional_size + index * 40
            address, raw_size, raw_offset = unpack("<III", section + 12)
            if address <= rva and rva - address + size <= raw_size:
                offset = raw_offset + rva - address
                if offset + size <= len(data):
                    return offset
        raise ValueError("Python PE resource points outside the image")

    resource = file_offset(resource_rva, resource_size)

    def entry(directory, wanted=None):
        if directory + 16 > resource_size:
            raise ValueError("invalid Python version resource directory")
        named, ids = unpack("<HH", resource + directory + 12)
        count = named + ids
        if directory + 16 + count * 8 > resource_size:
            raise ValueError("invalid Python version resource entries")
        for index in range(count):
            name, value = unpack("<II", resource + directory + 16 + index * 8)
            if wanted is None or name == wanted:
                return value
        raise ValueError("Python version resource is missing")

    directory = entry(0, 16)  # RT_VERSION -> name -> language -> data.
    for _ in range(2):
        if not directory & 0x80000000:
            raise ValueError("invalid Python version resource hierarchy")
        directory = entry(directory & 0x7FFFFFFF)
    if directory & 0x80000000 or directory + 16 > resource_size:
        raise ValueError("invalid Python version resource data")
    value_rva, value_size = unpack("<II", resource + directory)
    value = file_offset(value_rva, value_size)
    length, fixed_length, value_type = unpack("<HHH", value)
    key = "VS_VERSION_INFO\0".encode("utf-16le")
    fixed = (6 + len(key) + 3) & ~3
    if (data[value + 6:value + 6 + len(key)] != key or value_type != 0
            or fixed_length < 52 or length > value_size or fixed + fixed_length > length):
        raise ValueError("Python fixed version resource is invalid")
    signature, structure, major_minor, build_private = unpack("<IIII", value + fixed)
    version = (major_minor >> 16, major_minor & 0xFFFF, build_private >> 16, build_private & 0xFFFF)
    if signature != 0xFEEF04BD or structure != 0x10000 or version != PYTHON_FILE_VERSION:
        raise ValueError("Python runtime file version must be CPython 3.14.4 final")


def validate_python_runtime(output: Path) -> None:
    validate_python_dll(output / "python314.dll")
    if (output / "python3.dll").exists():
        validate_python_dll(output / "python3.dll")
    if sha256_file(output / "python/python314.zip") != PYTHON_STDLIB_SHA256:
        raise ValueError("Python standard library must match the CPython 3.14.4 AMD64 embeddable distribution")


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


def safe_archive_members(archive: zipfile.ZipFile, destination: Path) -> None:
    base = destination.resolve()
    seen = set()
    seen_files = set()
    seen_has_children = set()
    total_size = 0
    if len(archive.infolist()) > MAX_ARCHIVE_MEMBERS:
        raise ValueError("python archive contains too many members")
    for item in archive.infolist():
        name = item.filename.replace("\\", "/")
        if "\x00" in name:
            raise ValueError(f"NUL archive member: {item.filename}")
        parts = validate_windows_parts(name)
        canonical = canonical_package_path(parts)
        is_directory = item.is_dir() or name.endswith("/")
        if canonical in seen:
            raise ValueError(f"duplicate archive member: {item.filename}")
        prefixes = [canonical_package_path(parts[:index]) for index in range(1, len(parts))]
        if any(prefix in seen_files for prefix in prefixes):
            raise ValueError(f"archive member is below a file: {item.filename}")
        if not is_directory and canonical in seen_has_children:
            raise ValueError(f"archive file contains a child path: {item.filename}")
        seen.add(canonical)
        if not is_directory:
            seen_files.add(canonical)
        seen_has_children.update(prefixes)
        if parts[-1].casefold() in FORBIDDEN_ARCHIVE_NAMES:
            raise ValueError(f"forbidden archive member: {item.filename}")
        if name.startswith("/"):
            raise ValueError(f"archive member escapes python root: {item.filename}")
        mode = (item.external_attr >> 16) & 0xFFFF
        file_type = stat.S_IFMT(mode)
        if stat.S_ISLNK(mode) or file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise ValueError(f"symbolic links are not allowed in python archive: {item.filename}")
        if item.flag_bits & 1:
            raise ValueError(f"encrypted archive members are not allowed: {item.filename}")
        total_size += item.file_size
        if total_size > MAX_ARCHIVE_BYTES:
            raise ValueError("python archive expands beyond the package limit")
        target = (destination / item.filename).resolve()
        if base != target and base not in target.parents:
            raise ValueError(f"archive member escapes python root: {item.filename}")


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
        if relative.parts[0].casefold() == "business" and len(relative.parts) > 1:
            continue
        if not path.is_file() or path.suffix.lower() == ".pdb":
            continue
        if path.name.casefold() in RESERVED_PACKAGE_NAMES:
            raise ValueError(f"managed output contains package-owned file: {path.name}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        canonical = canonical_package_path(validate_windows_parts(relative.as_posix()))
        previous = existing.get(canonical)
        if previous is not None:
            if canonical == "t7.nativebridge.dll" and sha256_file(path) == sha256_file(previous):
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
    python_kind = manifest.get("python")
    archive_hash = manifest.get("pythonArchiveSha256")
    if python_kind == "external-runtime-required":
        if archive_hash is not None:
            raise ValueError("external-runtime package must not record an archive hash")
    elif python_kind == "3.14.4-amd64":
        if (not isinstance(archive_hash, str) or len(archive_hash) != 64
                or any(value not in "0123456789abcdef" for value in archive_hash)):
            raise ValueError("bundled Python archive hash is invalid")
        validate_python_runtime(output)
    else:
        raise ValueError("package manifest Python runtime policy is invalid")
    if manifest.get("clientIncluded") or manifest.get("serverProductIncluded"):
        raise ValueError("package manifest includes a client or server product")
    if require_runtime and (manifest.get("python") != "3.14.4-amd64"
                            or not (output / "python314.dll").is_file()
                            or not (output / "python" / "python314.zip").is_file()):
        raise ValueError("release package must contain the CPython 3.14.4 runtime")
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


def _package(output: Path, python_archive: Path | None = None, release: bool = False) -> Path:
    output = Path(output)
    if output.exists() or is_link_like(output):
        raise FileExistsError(f"refusing to overwrite existing output: {output}")
    output = output.resolve()
    if output.exists() or is_link_like(output):
        raise FileExistsError(f"refusing to overwrite existing output: {output}")
    if release and python_archive is None:
        raise ValueError("release packaging requires --python-archive")
    native = ROOT / "artifacts/native/bin/x64/Release/T7.NativeBridge.dll"
    desktop = ROOT / "artifacts/bin/T7.Desktop/x64/Release/net48"
    if not native.is_file() or not (desktop / "T7-Rekindle.exe").is_file():
        raise FileNotFoundError("build artifacts are missing; run scripts/build.py first")
    output.mkdir(parents=True)
    (output / "python").mkdir()
    shutil.copy2(native, output / native.name)
    copy_product_output(desktop, output)
    copy_tree(ROOT / "src/Business", output / "Business")
    if python_archive:
        with zipfile.ZipFile(python_archive) as archive:
            safe_archive_members(archive, output / "python")
            archive.extractall(output / "python")
        for name in ("python314.dll", "python3.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
            candidate = output / "python" / name
            if candidate.is_file():
                shutil.move(str(candidate), str(output / name))
        if not (output / "python314.dll").is_file() or not (output / "python/python314.zip").is_file():
            raise FileNotFoundError("python archive is missing python314.dll or python314.zip")
        validate_python_runtime(output)
        python_hash = sha256_file(python_archive)
    else:
        python_hash = None
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
        "build": package_build_metadata(os.environ),
        "product": "T7-Rekindle",
        "architecture": "x64",
        "minimumWindowsBuild": "10.0.19041",
        "requiresDotNet": "net48",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "python": "3.14.4-amd64" if python_archive else "external-runtime-required",
        "pythonArchiveSha256": python_hash,
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


def package(output: Path, python_archive: Path | None = None, release: bool = False) -> Path:
    output = Path(output).resolve()
    existed = output.exists() or output.is_symlink()
    try:
        return _package(output, python_archive, release)
    except Exception:
        if not existed and output.is_dir() and not output.is_symlink():
            remove_tree(output)
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--python-archive", type=Path)
    parser.add_argument("--release", action="store_true",
                        help="require the bundled CPython runtime and a root LICENSE")
    parser.add_argument("--verify", action="store_true",
                        help="verify an existing product directory instead of creating it")
    args = parser.parse_args()
    if args.verify:
        print(json.dumps(verify_package(args.output.resolve(), args.release, args.release), indent=2))
    else:
        print(package(args.output, args.python_archive, args.release))


if __name__ == "__main__":
    main()
