import json
import zipfile

import pytest

from test_release_mirror import MemoryStore, release_fixture
from test_preview_release import fixture as preview_fixture


BASE = "https://downloads.example.com"


def enable_rollback(path, build):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(zipfile.ZipInfo("manifest.json"), json.dumps({
            "schemaVersion": 1, "release": True, "build": build,
            "rollbackProtocol": 1, "settingsSchemaVersion": 1}))


def stable(tmp_path, store, version):
    from mirror_release import publish_release
    release, assets = release_fixture(tmp_path, version)
    enable_rollback(assets[f"T7-Rekindle-windows-x64-{version}.zip"],
                    {"channel": "stable", "version": version})
    for asset in release["assets"]:
        asset["size"] = assets[asset["name"]].stat().st_size
    return publish_release(release, assets, store, BASE, tmp_path,
                           lambda *args: None, verify_notes=lambda *args: None)


def history(store, channel):
    data, options = store.objects[f"updates/{channel}-history.json"]
    assert options["cache_control"] == "no-store"
    return json.loads(data)["entries"]


def test_stable_history_is_sorted_idempotent_and_does_not_downgrade(tmp_path):
    store = MemoryStore()
    assert stable(tmp_path, store, "v2.0.0")
    assert not stable(tmp_path, store, "v1.0.0")
    entries = history(store, "stable")
    assert [entry["version"] for entry in entries] == ["v2.0.0", "v1.0.0"]
    assert all(entry["settingsSchemaVersion"] == 1 for entry in entries)
    stable(tmp_path, store, "v2.0.0")
    assert history(store, "stable") == entries
    assert json.loads(store.objects["updates/stable.json"][0])["version"] == "v2.0.0"
    assert store.writes[-1] == "updates/stable.json"


def test_preview_old_task_adds_history_without_moving_latest(tmp_path):
    from preview_release import publish_preview
    store = MemoryStore()
    for number, attempt in [(12, 2), (11, 1), (12, 1)]:
        build, assets = preview_fixture(tmp_path, number, attempt)
        enable_rollback(assets["portable"], build)
        assert publish_preview(build, assets, store, BASE, tmp_path, "说明", verify=lambda *args: None) == (attempt == 2)
    entries = history(store, "preview")
    assert [(entry["build"]["runNumber"], entry["build"]["runAttempt"]) for entry in entries] == [(12, 2), (12, 1), (11, 1)]
    assert json.loads(store.objects["updates/preview.json"][0])["build"]["runAttempt"] == 2


def test_legacy_packages_are_not_added_to_history(tmp_path):
    from test_release_mirror import publish
    from preview_release import publish_preview
    store = MemoryStore()
    publish(tmp_path, store)
    build, assets = preview_fixture(tmp_path)
    publish_preview(build, assets, store, BASE, tmp_path, "", verify=lambda *args: None)
    assert not any("history" in key for key in store.objects)


def test_history_upload_failure_preserves_latest_and_retry_repairs(tmp_path):
    store = MemoryStore()
    stable(tmp_path, store, "v1.0.0")
    previous = store.objects["updates/stable.json"]
    store.fail_key = "updates/stable-history.json"
    with pytest.raises(RuntimeError, match="upload failed"):
        stable(tmp_path, store, "v2.0.0")
    assert store.objects["updates/stable.json"] == previous
    store.fail_key = None
    stable(tmp_path, store, "v2.0.0")
    assert len(history(store, "stable")) == 2


def test_corrupt_history_is_not_overwritten(tmp_path):
    store = MemoryStore()
    stable(tmp_path, store, "v1.0.0")
    key = "updates/stable-history.json"
    data, options = store.objects[key]
    value = json.loads(data)
    value["entries"][0]["installer"]["url"] = "https://foreign.example/Setup.exe"
    store.objects[key] = (json.dumps(value).encode(), options)
    previous = store.objects[key]
    with pytest.raises(ValueError):
        stable(tmp_path, store, "v2.0.0")
    assert store.objects[key] == previous


def test_build_contract_matches_settings_and_installer():
    from rollback_history import build_rollback_metadata
    assert build_rollback_metadata() == {"rollbackProtocol": 1, "settingsSchemaVersion": 1}


@pytest.mark.parametrize("change", [
    {"rollbackProtocol": True}, {"rollbackProtocol": 2}, {"settingsSchemaVersion": 0},
    {"settingsSchemaVersion": True}, {"settingsSchemaVersion": 2**31},
    {"build": {"channel": "stable", "version": "v9.0.0"}}, {"build": None},
])
def test_invalid_package_contract_is_rejected(tmp_path, change):
    from rollback_history import read_rollback_metadata
    path = tmp_path / "package.zip"
    package = {"schemaVersion": 1, "release": True, "rollbackProtocol": 1,
               "settingsSchemaVersion": 1, "build": {"channel": "stable", "version": "v1.0.0"}, **change}
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(package))
    with pytest.raises(ValueError):
        read_rollback_metadata(path, "stable", "v1.0.0")


def test_failed_verification_does_not_record_history(tmp_path):
    from preview_release import publish_preview
    store = MemoryStore()
    build, assets = preview_fixture(tmp_path)
    enable_rollback(assets["portable"], build)
    def fail(*args):
        raise ValueError("verification failed")
    with pytest.raises(ValueError, match="verification failed"):
        publish_preview(build, assets, store, BASE, tmp_path, "", verify=fail)
    assert "updates/preview-history.json" not in store.objects
    assert "updates/preview.json" not in store.objects
