import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import patch
from urllib.parse import quote

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_stable_version_and_build_configuration(tmp_path):
    metadata = load_module("release_metadata")
    assert metadata.parse_version("v0.10.0") > metadata.parse_version("v0.2.0")
    assert metadata.parse_version("V1.2.3+build.1") == metadata.parse_version("1.2.3.0")
    for value in ("v1.2", "v1.2.3-rc.1", "v65535.0.0", "v1.2.3+", "../v1.2.3"):
        with pytest.raises(ValueError):
            metadata.parse_version(value)
    environment = tmp_path / "github-env"
    metadata.prepare_build({"GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": "v1.2.3.4+ci.1",
                            "R2_PUBLIC_BASE_URL": "https://downloads.example.com/",
                            "GITHUB_ENV": str(environment)})
    assert environment.read_text(encoding="utf-8").splitlines() == [
        "T7_RELEASE_VERSION=1.2.3.4", "T7_UPDATE_BASE_URL=https://downloads.example.com"]
    with pytest.raises(ValueError, match="R2_PUBLIC_BASE_URL"):
        metadata.prepare_build({"GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": "v1.2.3",
                                "GITHUB_ENV": str(environment)})
    for address in ("http://example.com", "https://user@example.com", "https://example.com/path",
                    "https://example.com?token=value", "https://example.com:444"):
        with pytest.raises(ValueError):
            metadata.normalize_base_url(address)


class MemoryStore:
    def __init__(self):
        self.objects = {}
        self.writes = []
        self.fail_key = None

    def download(self, key, destination):
        if key not in self.objects:
            return False
        destination.write_bytes(self.objects[key][0])
        return True

    def head(self, key):
        if key not in self.objects:
            return None
        data, options = self.objects[key]
        return {"ContentLength": len(data), "Metadata": {"sha256": options.get("sha256", "")}}

    def upload(self, key, source, *, content_type, cache_control, sha256=None):
        if key == self.fail_key:
            raise RuntimeError("upload failed")
        options = {"content_type": content_type, "cache_control": cache_control, "sha256": sha256}
        self.objects[key] = (source.read_bytes(), options)
        self.writes.append(key)


def release_fixture(tmp_path, version="v1.2.3"):
    directory = tmp_path / version
    directory.mkdir(exist_ok=True)
    assets = {}
    for name in (f"T7-Rekindle-{version}-Setup.exe", f"T7-Rekindle-windows-x64-{version}.zip"):
        path = directory / name
        path.write_bytes((version + name).encode())
        assets[name] = path
    release = {"tag_name": version, "draft": False, "prerelease": False, "body": "发布说明",
               "assets": [{"name": name, "state": "uploaded", "size": path.stat().st_size}
                          for name, path in assets.items()]}
    return release, assets


def publish(tmp_path, store, version="v1.2.3", verify=None):
    mirror = load_module("mirror_release")
    release, assets = release_fixture(tmp_path, version)
    return mirror.publish_release(release, assets, store, "https://downloads.example.com",
                                  tmp_path, verify or (lambda url, asset: None),
                                  verify_notes=lambda url, text: None)


def test_assets_verified_before_manifest_and_retry_is_idempotent(tmp_path):
    store = MemoryStore()
    verified = []

    def verify(url, asset):
        assert "updates/stable.json" not in store.writes
        assert url.startswith(("https://downloads.example.com/releases/v1.2.3/",
                               "https://downloads.example.com/latest/"))
        assert len(asset["sha256"]) == 64 and asset["size"] > 0
        verified.append(url)

    assert publish(tmp_path, store, verify=verify)
    assert len(verified) == 4
    assert store.writes[-1] == "updates/stable.json"
    content, options = store.objects["updates/stable.json"]
    manifest = json.loads(content)
    assert manifest["schemaVersion"] == 1 and manifest["version"] == "v1.2.3"
    assert manifest["summary"] == "发布说明" and options["cache_control"] == "no-store"
    assert manifest["installer"]["url"].endswith("/v1.2.3/T7-Rekindle-v1.2.3-Setup.exe")
    assert manifest["portable"]["url"].endswith("/v1.2.3/T7-Rekindle-windows-x64-v1.2.3.zip")
    assert "immutable" in store.objects[store.writes[0]][1]["cache_control"]
    store.writes.clear()
    assert publish(tmp_path, store)
    assert not any(key.startswith("releases/") and key.endswith((".exe", ".zip")) for key in store.writes)


def test_failed_upload_or_public_verification_preserves_previous_feed(tmp_path):
    store = MemoryStore()
    publish(tmp_path, store, "v1.0.0")
    previous = store.objects["updates/stable.json"]
    store.fail_key = "releases/v1.2.3/T7-Rekindle-windows-x64-v1.2.3.zip"
    with pytest.raises(RuntimeError, match="upload failed"):
        publish(tmp_path, store)
    assert store.objects["updates/stable.json"] == previous
    store.fail_key = None

    def fail_verification(url, asset):
        raise ValueError("digest mismatch")

    with pytest.raises(ValueError, match="digest mismatch"):
        publish(tmp_path, store, verify=fail_verification)
    assert store.objects["updates/stable.json"] == previous


def test_backfill_does_not_downgrade_and_changed_assets_are_rejected(tmp_path):
    store = MemoryStore()
    publish(tmp_path, store, "v2.0.0")
    previous = store.objects["updates/stable.json"]
    assert not publish(tmp_path, store, "v1.0.0")
    manifest = json.loads(store.objects["updates/stable.json"][0])
    assert manifest["version"] == "v2.0.0"
    assert manifest["installer"] == json.loads(previous[0])["installer"]
    assert manifest["versions"] == ["v2.0.0", "v1.0.0"]
    previous = store.objects["updates/stable.json"]
    mirror = load_module("mirror_release")
    release, assets = release_fixture(tmp_path, "v2.0.0")
    assets["T7-Rekindle-v2.0.0-Setup.exe"].write_bytes(b"changed")
    release["assets"][0]["size"] = 7
    with pytest.raises(ValueError, match="immutable"):
        mirror.publish_release(release, assets, store, "https://downloads.example.com",
                               tmp_path, lambda url, asset: None, verify_notes=lambda url, text: None)
    assert store.objects["updates/stable.json"] == previous


def test_draft_prerelease_and_unverified_github_assets_are_not_published(tmp_path):
    mirror = load_module("mirror_release")
    for field in ("draft", "prerelease"):
        release, assets = release_fixture(tmp_path)
        release[field] = True
        store = MemoryStore()
        assert not mirror.publish_release(release, assets, store, "https://downloads.example.com",
                                          tmp_path, lambda url, asset: None, verify_notes=lambda url, text: None)
        assert not store.writes
    release, assets = release_fixture(tmp_path)
    release["assets"][0]["digest"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="GitHub"):
        mirror.publish_release(release, assets, MemoryStore(), "https://downloads.example.com",
                               tmp_path, lambda url, asset: None, verify_notes=lambda url, text: None)


@pytest.mark.parametrize("tag", ["v1.2.3", "1.2.3", "V1.2.3.4+build.1"])
def test_mirror_preserves_full_tag_in_asset_names(tmp_path, tag):
    store = MemoryStore()
    assert publish(tmp_path, store, tag)
    manifest = json.loads(store.objects["updates/stable.json"][0])
    for kind, name in {"installer": f"T7-Rekindle-{tag}-Setup.exe",
                       "portable": f"T7-Rekindle-windows-x64-{tag}.zip"}.items():
        assert f"releases/{tag}/{name}" in store.objects
        assert manifest[kind]["url"] == (
            f"https://downloads.example.com/releases/{quote(tag, safe='')}/{quote(name, safe='')}"
        )


def test_mirror_downloads_tagged_release_assets(tmp_path, monkeypatch):
    mirror = load_module("mirror_release")
    tag = "V1.2.3.4+build.1"
    release, assets = release_fixture(tmp_path, tag)
    monkeypatch.setattr(sys, "argv", ["mirror_release.py", "--tag", tag])
    for name, value in {"GITHUB_REPOSITORY": "example/project", "R2_ACCOUNT_ID": "0" * 32,
                        "R2_BUCKET": "release-fixture", "R2_PUBLIC_BASE_URL": "https://downloads.example.com",
                        "AWS_ACCESS_KEY_ID": "TOKEN", "AWS_SECRET_ACCESS_KEY": "TOKEN", "GH_TOKEN": "TOKEN"}.items():
        monkeypatch.setenv(name, value)
    with patch.object(mirror, "run_command", side_effect=[json.dumps(release), ""]) as command, \
            patch.object(mirror, "publish_release") as publish_mock:
        mirror.main()
        arguments = command.call_args.args[0]
        patterns = [arguments[index + 1] for index, value in enumerate(arguments) if value == "--pattern"]
        assert patterns == list(assets)
        assert arguments[-2:] == ["--", tag]
        assert set(publish_mock.call_args.args[1]) == set(assets)


def test_r2_workflow_is_reusable_manual_and_follows_release():
    workflow = (ROOT / ".github/workflows/r2-mirror.yml").read_text(encoding="utf-8")
    assert "workflow_call:" in workflow and "workflow_dispatch:" in workflow
    assert "contents: read" in workflow and "cancel-in-progress: false" in workflow
    assert "queue: max" in workflow
    assert "R2_SECRET_ACCESS_KEY" in workflow and "python scripts/mirror_release.py" in workflow
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "needs: release" in ci and "uses: ./.github/workflows/r2-mirror.yml" in ci
    assert "python scripts/release_metadata.py prepare-build" in ci


def test_release_uses_tag_notes_instead_of_generated_notes():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    release = workflow.split("\n  release:\n", 1)[1].split("\n  mirror:\n", 1)[0]
    assert "gh release create" in release and "--verify-tag" in release
    assert "--notes-from-tag" in release
    assert "--generate-notes" not in release
    command = release.split("gh release create", 1)[1].split()
    assert "--repo" not in command and "-R" not in command


def test_release_checks_out_tags_before_downloading_artifacts():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    release = workflow.split("\n  release:\n", 1)[1].split("\n  mirror:\n", 1)[0]
    checkouts = [step for step in release.split("\n      - name:") if "uses: actions/checkout@" in step]
    assert len(checkouts) == 1
    assert "fetch-depth: 0" in checkouts[0]
    assert "persist-credentials: false" in checkouts[0]
    assert release.index("uses: actions/checkout@") < release.index("uses: actions/download-artifact@")


def test_mirror_preserves_markdown_release_notes(tmp_path):
    mirror = load_module("mirror_release")
    release, assets = release_fixture(tmp_path)
    notes = "## 新增功能\n- 支持保存启动配置。\n\n## 问题修复\n- 修复配置读取失败时的提示。\n"
    release = {**release, "body": notes}
    store = MemoryStore()
    assert mirror.publish_release(release, assets, store, "https://downloads.example.com",
                                  tmp_path, lambda url, asset: None, verify_notes=lambda url, text: None)
    assert json.loads(store.objects["updates/stable.json"][0])["summary"] == notes


def test_branch_build_can_omit_the_mirror(tmp_path):
    metadata = load_module("release_metadata")
    environment = tmp_path / "github-env"
    metadata.prepare_build({"GITHUB_REF_TYPE": "branch", "GITHUB_ENV": str(environment)})
    assert environment.read_text(encoding="utf-8") == "T7_RELEASE_VERSION=\nT7_UPDATE_BASE_URL=\n"


def test_missing_upload_configuration_stops_before_external_calls(monkeypatch, capsys):
    mirror = load_module("mirror_release")
    monkeypatch.setattr(sys, "argv", ["mirror_release.py", "--tag", "v1.2.3"])
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/project")
    monkeypatch.delenv("R2_ACCOUNT_ID", raising=False)
    with patch.object(mirror, "run_command") as command:
        with pytest.raises(SystemExit) as failure:
            mirror.main()
        assert failure.value.code == 1
        command.assert_not_called()
    assert "R2_ACCOUNT_ID is required" in capsys.readouterr().err


def test_s3_commands_use_scoped_endpoint_cache_metadata_and_no_credentials(tmp_path):
    mirror = load_module("mirror_release")
    store = mirror.AwsR2Store("0" * 32, "release-fixture")
    source = tmp_path / "stable.json"
    source.write_text("{}", encoding="utf-8")
    with patch.object(mirror, "run_command", return_value=None) as command:
        assert store.head("missing") is None
        assert not store.download("missing", tmp_path / "missing.json")
        store.upload("updates/stable.json", source, content_type="application/json", cache_control="no-store")
        arguments = command.call_args.args[0]
        assert arguments[arguments.index("--endpoint-url") + 1] == "https://" + "0" * 32 + ".r2.cloudflarestorage.com"
        assert arguments[arguments.index("--cache-control") + 1] == "no-store"
        assert arguments[arguments.index("--region") + 1] == "auto"
        assert "--access-key" not in arguments and "--secret-key" not in arguments
