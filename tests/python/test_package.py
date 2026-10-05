import importlib.util
import json
from pathlib import Path
import zipfile
import pytest
from python_runtime_fixture import python_archive, python_dll


ROOT = Path(__file__).resolve().parents[2]


def load_package_module():
    spec = importlib.util.spec_from_file_location("t7_rekindle_package", ROOT / "scripts/package.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("business_directory", ["Business", "BUSINESS"])
def test_package_manifest_excludes_client(tmp_path, business_directory):
    module = load_package_module()
    source = tmp_path / "source"
    (source / "artifacts/native/bin/x64/Release").mkdir(parents=True)
    (source / "artifacts/bin/T7.Desktop/x64/Release/net48").mkdir(parents=True)
    (source / "src/Business/scripts").mkdir(parents=True)
    (source / "src/Business/scripts/__init__.py").write_text("API_VERSION=1\nSTATE_VERSION=1\n", encoding="utf-8")
    stale = source / "artifacts/bin/T7.Desktop/x64/Release/net48" / business_directory
    (stale / "scripts/__pycache__").mkdir(parents=True)
    (stale / "scripts/__init__.py").write_text("stale = True\n", encoding="utf-8")
    (stale / "scripts/removed.py").write_text("stale = True\n", encoding="utf-8")
    (stale / "scripts/__pycache__/removed.pyc").write_bytes(b"stale bytecode")
    (source / "artifacts/native/bin/x64/Release/T7.NativeBridge.dll").write_bytes(b"bridge")
    (source / "artifacts/bin/T7.Desktop/x64/Release/net48/T7-Rekindle.exe").write_bytes(b"desktop")
    (source / "artifacts/bin/T7.Desktop/x64/Release/net48/T7.Core.dll").write_bytes(b"core")
    (source / "artifacts/bin/T7.Desktop/x64/Release/net48/T7.NativeBridge.dll").write_bytes(b"bridge")
    harness = source / "artifacts/bin/T7.ManagedHarness/x64/Release/net48"
    harness.mkdir(parents=True)
    (harness / "T7.ManagedHarness.exe").write_bytes(b"test-only")
    (source / "artifacts/bin/T7.Desktop/x64/Release/net48/runtimes").mkdir()
    (source / "artifacts/bin/T7.Desktop/x64/Release/net48/runtimes/managed.dll").write_bytes(b"managed")
    (source / "THIRD-PARTY.txt").write_text("notice\n", encoding="utf-8")
    for relative in ("src/Runtime", "scripts", "tests", ".local", "client", "work"):
        directory = source / relative
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "fixture.txt").write_text("not a product file", encoding="utf-8")
    module.ROOT = source
    output = module.package(tmp_path / "output")
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schemaVersion"] == 1
    assert manifest["product"] == "T7-Rekindle"
    assert manifest["architecture"] == "x64"
    assert manifest["rawWireCaptureDefault"] is False
    assert manifest["clientIncluded"] is False
    assert "T7.NativeBridge.dll" in manifest["files"]
    assert "T7.Core.dll" in manifest["files"]
    assert "T7.ManagedHarness.exe" not in manifest["files"]
    assert "runtimes/managed.dll" in manifest["files"]
    assert "Business/scripts/__init__.py" in manifest["files"]
    assert (output / "Business/scripts/__init__.py").read_bytes() == (source / "src/Business/scripts/__init__.py").read_bytes()
    assert not (output / "Business/scripts/removed.py").exists()
    assert not (output / "Business/scripts/__pycache__").exists()
    for name in ("src", "scripts", "tests", ".local", "client", "work"):
        assert not (output / name).exists()


def test_archive_escape_is_rejected(tmp_path):
    module = load_package_module()
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as value:
        value.writestr("../outside.txt", "bad")
    destination = tmp_path / "python"
    destination.mkdir()
    with zipfile.ZipFile(archive) as value:
        try:
            module.safe_archive_members(value, destination)
        except ValueError:
            pass
        else:
            raise AssertionError("archive traversal was accepted")

    absolute = tmp_path / "absolute.zip"
    with zipfile.ZipFile(absolute, "w") as value:
        value.writestr("C:/outside.txt", "bad")
    with zipfile.ZipFile(absolute) as value:
        try:
            module.safe_archive_members(value, destination)
        except ValueError:
            pass
        else:
            raise AssertionError("absolute archive member was accepted")


def test_python_runtime_dlls_move_to_package_root(tmp_path):
    module = load_package_module()
    source = tmp_path / "source"
    (source / "artifacts/native/bin/x64/Release").mkdir(parents=True)
    (source / "artifacts/bin/T7.Desktop/x64/Release/net48").mkdir(parents=True)
    (source / "src/Business").mkdir(parents=True)
    (source / "artifacts/native/bin/x64/Release/T7.NativeBridge.dll").write_bytes(b"bridge")
    (source / "artifacts/bin/T7.Desktop/x64/Release/net48/T7-Rekindle.exe").write_bytes(b"desktop")
    (source / "THIRD-PARTY.txt").write_text("notice\n", encoding="utf-8")
    archive = tmp_path / "python.zip"
    python_archive(archive, module)
    module.ROOT = source
    output = module.package(tmp_path / "output", archive)
    assert (output / "python314.dll").read_bytes() == python_dll()
    assert not (output / "python/python314.dll").exists()


def test_archive_member_limit_is_enforced(tmp_path):
    module = load_package_module()
    archive = tmp_path / "many.zip"
    with zipfile.ZipFile(archive, "w") as value:
        for index in range(module.MAX_ARCHIVE_MEMBERS + 1):
            value.writestr(f"item-{index}.txt", b"x")
    destination = tmp_path / "python"
    destination.mkdir()
    with zipfile.ZipFile(archive) as value:
        try:
            module.safe_archive_members(value, destination)
        except ValueError:
            pass
        else:
            raise AssertionError("archive member limit was not enforced")


def test_windows_archive_name_collisions_are_rejected(tmp_path):
    module = load_package_module()
    destination = tmp_path / "python"
    destination.mkdir()
    for entries in (("Lib/module.py", "lib/MODULE.py"), ("safe.txt", "folder/NUL.txt"),
                    ("safe.txt", "folder/value:stream")):
        archive = tmp_path / (entries[1].replace("/", "-").replace(":", "-") + ".zip")
        with zipfile.ZipFile(archive, "w") as value:
            for entry in entries:
                value.writestr(entry, b"x")
        with zipfile.ZipFile(archive) as value:
            try:
                module.safe_archive_members(value, destination)
            except ValueError:
                pass
            else:
                raise AssertionError(f"unsafe Windows archive name was accepted: {entries[1]}")


def test_archive_cannot_embed_product_control_files(tmp_path):
    module = load_package_module()
    destination = tmp_path / "python"
    destination.mkdir()
    archive = tmp_path / "product-files.zip"
    with zipfile.ZipFile(archive, "w") as value:
        value.writestr("T7-Rekindle.exe", b"not python")
    with zipfile.ZipFile(archive) as value:
        try:
            module.safe_archive_members(value, destination)
        except ValueError:
            pass
        else:
            raise AssertionError("archive product control file was accepted")


def test_archive_file_directory_collisions_are_rejected(tmp_path):
    module = load_package_module()
    destination = tmp_path / "python"
    destination.mkdir()
    for entries in (("item", "item/child.py"), ("item/child.py", "item")):
        archive = tmp_path / ("collision-" + str(len(entries[0])) + ".zip")
        with zipfile.ZipFile(archive, "w") as value:
            for entry in entries:
                value.writestr(entry, b"x")
        with zipfile.ZipFile(archive) as value:
            try:
                module.safe_archive_members(value, destination)
            except ValueError:
                pass
            else:
                raise AssertionError("file/directory archive collision was accepted")


def test_archive_case_variant_hierarchy_collisions_are_rejected(tmp_path):
    module = load_package_module()
    destination = tmp_path / "python"
    destination.mkdir()
    for entries in (("Lib", "lib/module.py"), ("LIB/module.py", "lib")):
        archive = tmp_path / ("case-hierarchy-" + str(len(entries[0])) + ".zip")
        with zipfile.ZipFile(archive, "w") as value:
            value.writestr(entries[0], b"")
            value.writestr(entries[1], b"")
        with zipfile.ZipFile(archive) as value:
            try:
                module.safe_archive_members(value, destination)
            except ValueError:
                pass
            else:
                raise AssertionError("case-variant hierarchy collision was accepted")


def test_managed_output_cannot_replace_native_bridge(tmp_path):
    module = load_package_module()
    source = tmp_path / "managed"
    destination = tmp_path / "package"
    source.mkdir()
    destination.mkdir()
    (source / "T7.NativeBridge.dll").write_bytes(b"stale")
    (destination / "T7.NativeBridge.dll").write_bytes(b"native")
    try:
        module.copy_product_output(source, destination)
    except ValueError:
        pass
    else:
        raise AssertionError("managed output replaced the selected native bridge")


def test_identical_native_bridge_copy_is_not_a_collision(tmp_path):
    module = load_package_module()
    source = tmp_path / "managed"
    destination = tmp_path / "package"
    source.mkdir()
    destination.mkdir()
    (source / "T7.NativeBridge.dll").write_bytes(b"native")
    (destination / "T7.NativeBridge.dll").write_bytes(b"native")
    module.copy_product_output(source, destination)
    assert (destination / "T7.NativeBridge.dll").read_bytes() == b"native"


def test_failed_package_does_not_leave_partial_output(tmp_path):
    module = load_package_module()
    source = tmp_path / "source"
    (source / "artifacts/native/bin/x64/Release").mkdir(parents=True)
    (source / "artifacts/bin/T7.Desktop/x64/Release/net48").mkdir(parents=True)
    (source / "src/Business").mkdir(parents=True)
    (source / "artifacts/native/bin/x64/Release/T7.NativeBridge.dll").write_bytes(b"bridge")
    (source / "artifacts/bin/T7.Desktop/x64/Release/net48/T7-Rekindle.exe").write_bytes(b"desktop")
    (source / "THIRD-PARTY.txt").write_text("notice\n", encoding="utf-8")
    archive = tmp_path / "incomplete.zip"
    with zipfile.ZipFile(archive, "w") as value:
        value.writestr("python314.zip", b"stdlib")
    module.ROOT = source
    output = tmp_path / "output"
    try:
        module.package(output, archive)
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("incomplete Python archive was accepted")
    assert not output.exists()


def test_forbidden_product_names_are_case_insensitive(tmp_path):
    module = load_package_module()
    source = tmp_path / "source"
    (source / "artifacts/native/bin/x64/Release").mkdir(parents=True)
    (source / "artifacts/bin/T7.Desktop/x64/Release/net48").mkdir(parents=True)
    (source / "src/Business").mkdir(parents=True)
    (source / "artifacts/native/bin/x64/Release/T7.NativeBridge.dll").write_bytes(b"bridge")
    (source / "artifacts/bin/T7.Desktop/x64/Release/net48/T7-Rekindle.exe").write_bytes(b"desktop")
    (source / "artifacts/bin/T7.Desktop/x64/Release/net48/SeRvEr.ExE").write_bytes(b"server")
    (source / "THIRD-PARTY.txt").write_text("notice\n", encoding="utf-8")
    module.ROOT = source
    try:
        module.package(tmp_path / "output")
    except ValueError:
        pass
    else:
        raise AssertionError("case-variant server product name was accepted")


def test_manifest_hashes_are_verified(tmp_path):
    module = load_package_module()
    source = tmp_path / "source"
    (source / "artifacts/native/bin/x64/Release").mkdir(parents=True)
    (source / "artifacts/bin/T7.Desktop/x64/Release/net48").mkdir(parents=True)
    (source / "src/Business").mkdir(parents=True)
    (source / "artifacts/native/bin/x64/Release/T7.NativeBridge.dll").write_bytes(b"bridge")
    (source / "artifacts/bin/T7.Desktop/x64/Release/net48/T7-Rekindle.exe").write_bytes(b"desktop")
    (source / "THIRD-PARTY.txt").write_text("notice\n", encoding="utf-8")
    module.ROOT = source
    output = module.package(tmp_path / "output")
    assert module.verify_package(output)["product"] == "T7-Rekindle"
    (output / "T7-Rekindle.exe").write_bytes(b"modified")
    try:
        module.verify_package(output)
    except ValueError:
        pass
    else:
        raise AssertionError("modified package file was not detected")


def test_release_verification_requires_release_manifest(tmp_path):
    module = load_package_module()
    source = tmp_path / "source"
    (source / "artifacts/native/bin/x64/Release").mkdir(parents=True)
    (source / "artifacts/bin/T7.Desktop/x64/Release/net48").mkdir(parents=True)
    (source / "src/Business").mkdir(parents=True)
    (source / "artifacts/native/bin/x64/Release/T7.NativeBridge.dll").write_bytes(b"bridge")
    (source / "artifacts/bin/T7.Desktop/x64/Release/net48/T7-Rekindle.exe").write_bytes(b"desktop")
    (source / "THIRD-PARTY.txt").write_text("notice\n", encoding="utf-8")
    module.ROOT = source
    output = module.package(tmp_path / "output")
    try:
        module.verify_package(output, require_runtime=True, require_license=True)
    except ValueError:
        pass
    else:
        raise AssertionError("development manifest passed release verification")


def test_release_packaging_requires_runtime_and_license(tmp_path):
    module = load_package_module()
    source = tmp_path / "source"
    (source / "artifacts/native/bin/x64/Release").mkdir(parents=True)
    (source / "artifacts/bin/T7.Desktop/x64/Release/net48").mkdir(parents=True)
    (source / "src/Business").mkdir(parents=True)
    (source / "artifacts/native/bin/x64/Release/T7.NativeBridge.dll").write_bytes(b"bridge")
    (source / "artifacts/bin/T7.Desktop/x64/Release/net48/T7-Rekindle.exe").write_bytes(b"desktop")
    (source / "THIRD-PARTY.txt").write_text("notice\n", encoding="utf-8")
    module.ROOT = source
    try:
        module.package(tmp_path / "output", release=True)
    except ValueError:
        pass
    else:
        raise AssertionError("release package without Python archive was accepted")
    assert not (tmp_path / "output").exists()

    archive = tmp_path / "python.zip"
    python_archive(archive, module)
    try:
        module.package(tmp_path / "output-with-runtime", archive, release=True)
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("release package without LICENSE was accepted")
    assert not (tmp_path / "output-with-runtime").exists()


def test_release_packaging_accepts_complete_fixture(tmp_path):
    module = load_package_module()
    source = tmp_path / "source"
    (source / "artifacts/native/bin/x64/Release").mkdir(parents=True)
    (source / "artifacts/bin/T7.Desktop/x64/Release/net48").mkdir(parents=True)
    (source / "src/Business").mkdir(parents=True)
    (source / "artifacts/native/bin/x64/Release/T7.NativeBridge.dll").write_bytes(b"bridge")
    for name in module.REQUIRED_RELEASE_FILES:
        if name in {"T7.NativeBridge.dll", "THIRD-PARTY.txt"}:
            continue
        (source / "artifacts/bin/T7.Desktop/x64/Release/net48" / name).write_bytes(name.encode("utf-8"))
    (source / "THIRD-PARTY.txt").write_text("notice\n", encoding="utf-8")
    (source / "LICENSE").write_text("license\n", encoding="utf-8")
    archive = tmp_path / "python.zip"
    python_archive(archive, module)
    module.ROOT = source
    output = module.package(tmp_path / "output", archive, release=True)
    manifest = module.verify_package(output, require_runtime=True, require_license=True)
    assert manifest["release"] is True
    assert manifest["licenseIncluded"] is True


@pytest.mark.parametrize("content", [b"python", python_dll(0x14C), python_dll(version=(3, 14, 5150, 1013)), python_dll()[:700]])
def test_python_runtime_rejects_invalid_pe_architecture_or_version(tmp_path, content):
    module = load_package_module()
    dll = tmp_path / "python314.dll"
    dll.write_bytes(content)
    with pytest.raises(ValueError):
        module.validate_python_dll(dll)


def test_python_runtime_rejects_other_standard_library(tmp_path):
    module = load_package_module()
    (tmp_path / "python").mkdir()
    (tmp_path / "python314.dll").write_bytes(python_dll())
    (tmp_path / "python/python314.zip").write_bytes(b"wrong patch release")
    with pytest.raises(ValueError, match="standard library"):
        module.validate_python_runtime(tmp_path)
