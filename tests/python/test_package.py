import importlib.util
import json
from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def load_package_module():
    spec = importlib.util.spec_from_file_location("t7r_rekindle_package", ROOT / "scripts/package.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def package_source(tmp_path):
    module = load_package_module()
    source = tmp_path / "source"
    native = source / "artifacts/native/bin/x64/Release"
    rust = source / "artifacts/rust/x86_64-pc-windows-msvc/release"
    desktop = source / "artifacts/bin/T7R.Desktop/x64/Release/net48"
    news = source / "src/Desktop/Resources/News"
    for directory in (native, rust, desktop, news):
        directory.mkdir(parents=True)
    (native / "T7R.NativeBridge.dll").write_bytes(b"bridge")
    (rust / "t7r_battle_ffi.dll").write_bytes(b"rust")
    for name in module.REQUIRED_RELEASE_FILES - {"THIRD-PARTY.txt", "news/index.html"}:
        value = b"bridge" if name == "T7R.NativeBridge.dll" else b"rust" if name == "t7r_battle_ffi.dll" else name.encode()
        (desktop / name).write_bytes(value)
    (news / "index.html").write_text("<title>战场快讯</title>", encoding="utf-8")
    (source / "THIRD-PARTY.txt").write_text("notice", encoding="utf-8")
    (source / "LICENSE").write_text("license", encoding="utf-8")
    module.ROOT = source
    return module, source, desktop


def test_package_manifest_excludes_python_and_client(package_source, tmp_path):
    module, source, _ = package_source
    for name in ("tests/reference/Business/runtime/host_runtime.py", "src/Runtime/private.cpp"):
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not shipped", encoding="utf-8")
    output = module.package(tmp_path / "product", release=True)
    manifest = module.verify_package(output, require_runtime=True, require_license=True)
    assert manifest["battleRuntime"] == "rust"
    assert "python" not in manifest and "pythonArchiveSha256" not in manifest
    assert not manifest["clientIncluded"] and not manifest["serverProductIncluded"]
    assert manifest["minimumWindowsBuild"] == "10.0.19041"
    assert manifest["rawWireCaptureDefault"] is False
    assert "t7r_battle_ffi.dll" in manifest["files"]
    assert "T7R.Battle.dll" in manifest["files"]
    assert (output / "news/index.html").is_file()
    assert not (output / "Business").exists() and not (output / "tests").exists()


@pytest.mark.parametrize("name", ["python314.dll", "PyThOn3.DLL", "python/python314.zip",
    "Business/scripts/old.py", "BUSINESS/scripts/old.py", "__pycache__/old.pyc", "module.pyd",
    "SeRvEr.ExE", "T7.CoRe.DlL", "t7.NATIVEbridge.dll"])
def test_forbidden_product_files_are_rejected(package_source, tmp_path, name):
    module, _, desktop = package_source
    path = desktop / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"old runtime")
    output = tmp_path / "product"
    with pytest.raises(ValueError):
        module.package(output)
    assert not output.exists()


def test_manifest_hashes_are_verified(package_source, tmp_path):
    module, _, _ = package_source
    output = module.package(tmp_path / "product")
    (output / "T7-Rekindle.exe").write_bytes(b"changed")
    with pytest.raises(ValueError, match="hashes"):
        module.verify_package(output)


def test_rehashed_python_is_still_rejected(package_source, tmp_path):
    module, _, _ = package_source
    output = module.package(tmp_path / "product")
    extra = output / "python314.dll"
    extra.write_bytes(b"python")
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"][extra.name] = module.sha256_file(extra)
    (output / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="Python runtime"):
        module.verify_package(output)


def test_release_verification_requires_release_manifest(package_source, tmp_path):
    module, _, _ = package_source
    output = module.package(tmp_path / "product")
    with pytest.raises(ValueError, match="release manifest"):
        module.verify_package(output, require_runtime=True, require_license=True)


@pytest.mark.parametrize("missing", ["LICENSE", "artifacts/rust/x86_64-pc-windows-msvc/release/t7r_battle_ffi.dll",
    "artifacts/bin/T7R.Desktop/x64/Release/net48/T7R.Battle.dll"])
def test_release_requires_runtime_and_license(package_source, tmp_path, missing):
    module, source, _ = package_source
    (source / missing).unlink()
    output = tmp_path / "product"
    with pytest.raises(FileNotFoundError):
        module.package(output, release=True)
    assert not output.exists()


def test_existing_output_is_preserved(package_source, tmp_path):
    module, _, _ = package_source
    output = module.package(tmp_path / "product")
    original = (output / "manifest.json").read_bytes()
    with pytest.raises(FileExistsError):
        module.package(output)
    assert (output / "manifest.json").read_bytes() == original


def test_managed_output_cannot_replace_native_bridge(tmp_path):
    module = load_package_module()
    source = tmp_path / "managed"
    destination = tmp_path / "package"
    source.mkdir()
    destination.mkdir()
    (source / "T7R.NativeBridge.dll").write_bytes(b"stale")
    (destination / "T7R.NativeBridge.dll").write_bytes(b"native")
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
    (source / "T7R.NativeBridge.dll").write_bytes(b"native")
    (destination / "T7R.NativeBridge.dll").write_bytes(b"native")
    module.copy_product_output(source, destination)
    assert (destination / "T7R.NativeBridge.dll").read_bytes() == b"native"
