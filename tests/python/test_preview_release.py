import json
from pathlib import Path
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import release_metadata
from test_release_mirror import MemoryStore


def environment(**changes):
    return {"GITHUB_REF_TYPE": "branch", "GITHUB_REF_NAME": "main",
            "GITHUB_EVENT_NAME": "push", "T7_DEFAULT_BRANCH": "main",
            "GITHUB_RUN_ID": "101", "GITHUB_RUN_NUMBER": "12", "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_SHA": "a" * 40, **changes}


def test_build_identity_and_eligibility(tmp_path):
    for changes, expected in [({}, True), ({"GITHUB_EVENT_NAME": "workflow_dispatch"}, True),
                              ({"GITHUB_EVENT_NAME": "pull_request"}, False),
                              ({"GITHUB_REF_NAME": "feature"}, False),
                              ({"T7_DEFAULT_BRANCH": ""}, False)]:
        destination = tmp_path / "env"
        destination.write_text("", encoding="utf-8")
        release_metadata.prepare_build(environment(GITHUB_ENV=str(destination), **changes))
        values = dict(line.split("=", 1) for line in destination.read_text(encoding="utf-8").splitlines())
        build = release_metadata.package_build_metadata({**environment(**changes), **values})
        assert build["channel"] == "preview"
        assert build["runNumber"] == (12 if expected else 0)
        assert build["runAttempt"] == (1 if expected else 0)
        assert build["commitHash"] == "a" * 40


def fixture(tmp_path, number=12, attempt=1):
    from preview_release import ASSET_NAMES
    build = {"channel": "preview", "version": "0.1.0", "runId": 100 + number,
             "runNumber": number, "runAttempt": attempt, "commitHash": "a" * 40}
    folder = tmp_path / f"{number}-{attempt}"
    folder.mkdir(exist_ok=True)
    assets = {kind: folder / name for kind, name in ASSET_NAMES.items()}
    assets["installer"].write_bytes(f"installer {number}.{attempt}".encode())
    with zipfile.ZipFile(assets["portable"], "w") as archive:
        archive.writestr("manifest.json", json.dumps({"schemaVersion": 1, "release": True, "build": build}))
    return build, assets


def test_publish_verifies_assets_before_feed_and_is_idempotent(tmp_path):
    from preview_release import publish_preview
    build, assets = fixture(tmp_path)
    store = MemoryStore()
    verified = []
    def verify(url, asset):
        assert "updates/preview.json" not in store.objects
        verified.append(url)
    assert publish_preview(build, assets, store, "https://updates.example.com", tmp_path,
                           "构建说明", verify=verify)
    assert len(verified) == 2 and store.writes[-1] == "updates/preview.json"
    feed, options = store.objects["updates/preview.json"]
    feed = json.loads(feed)
    assert feed["channel"] == "preview" and feed["build"] == build
    assert feed["installer"]["url"].endswith("/previews/112/1/T7-Rekindle-Setup.exe")
    assert options["cache_control"] == "no-store"
    assert "updates/stable.json" not in store.objects
    assert publish_preview(build, assets, store, "https://updates.example.com", tmp_path,
                           "构建说明", verify=lambda *args: None)
    assets["installer"].write_bytes(b"changed")
    with pytest.raises(ValueError, match="immutable"):
        publish_preview(build, assets, store, "https://updates.example.com", tmp_path, "", verify=lambda *args: None)


def test_publication_failure_and_old_retries_do_not_advance_feed(tmp_path):
    from preview_release import publish_preview
    store = MemoryStore()
    def publish(number, attempt=1, verify=lambda *args: None):
        build, assets = fixture(tmp_path, number, attempt)
        return publish_preview(build, assets, store, "https://updates.example.com", tmp_path, "", verify=verify)
    assert publish(12)
    previous = store.objects["updates/preview.json"]
    def fail(*args):
        raise ValueError("verification failed")
    with pytest.raises(ValueError, match="verification failed"):
        publish(13, verify=fail)
    assert store.objects["updates/preview.json"] == previous
    assert not publish(11, 9)
    assert store.objects["updates/preview.json"] == previous
    assert publish(12, 2)
    assert json.loads(store.objects["updates/preview.json"][0])["build"]["runAttempt"] == 2


def test_publication_uses_identity_frozen_in_archive(tmp_path):
    from preview_release import read_preview_build
    build, assets = fixture(tmp_path)
    assert read_preview_build(assets["portable"]) == build
    with zipfile.ZipFile(assets["portable"], "w") as archive:
        archive.writestr("manifest.json", json.dumps({"schemaVersion": 1, "release": True,
                                                    "build": {**build, "channel": "stable"}}))
    with pytest.raises(ValueError):
        read_preview_build(assets["portable"])


def test_preview_job_is_default_branch_only_and_separate_from_stable():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    preview = workflow.split("\n  preview:\n", 1)[1]
    assert "needs: build" in preview and "github.event.repository.default_branch" in preview
    assert "github.ref_type == 'branch'" in preview and "github.event_name == 'push'" in preview
    assert "github.event_name == 'workflow_dispatch'" in preview
    assert "cancel-in-progress: false" in preview and "queue: max" in preview
    assert "artifact-ids:" in preview and "scripts/preview_release.py" in preview
    assert "gh release create" not in preview


@pytest.mark.parametrize("key,value", [("runId", 0), ("runNumber", -1), ("runAttempt", True),
                                     ("runAttempt", 2**31), ("commitHash", "invalid"),
                                     ("commitHash", None), ("version", 123), ("channel", "stable")])
def test_invalid_preview_identity_is_rejected(tmp_path, key, value):
    build, _ = fixture(tmp_path)
    with pytest.raises(ValueError):
        release_metadata.validate_preview_build({**build, key: value})


@pytest.mark.parametrize("failure", ["previews/113/1/T7-Rekindle-windows-x64.zip", "updates/preview.json"])
def test_upload_failure_preserves_previous_feed(tmp_path, failure):
    from preview_release import publish_preview
    store = MemoryStore()
    for number in (12, 13):
        build, assets = fixture(tmp_path, number)
        if number == 12:
            publish_preview(build, assets, store, "https://updates.example.com", tmp_path, "", verify=lambda *args: None)
            previous = store.objects["updates/preview.json"]
            store.fail_key = failure
        else:
            with pytest.raises(RuntimeError, match="upload failed"):
                publish_preview(build, assets, store, "https://updates.example.com", tmp_path, "", verify=lambda *args: None)
            assert store.objects["updates/preview.json"] == previous


def test_retry_publication_keeps_original_build_attempt(tmp_path, monkeypatch):
    import preview_release
    build, assets = fixture(tmp_path)
    for key, value in {"R2_ACCOUNT_ID": "0" * 32, "R2_BUCKET": "example-bucket",
                       "R2_PUBLIC_BASE_URL": "https://updates.example.com",
                       "AWS_ACCESS_KEY_ID": "fixture", "AWS_SECRET_ACCESS_KEY": "fixture",
                       "GITHUB_REPOSITORY": "example/project", "GITHUB_RUN_ID": str(build["runId"]),
                       "GITHUB_RUN_NUMBER": str(build["runNumber"]), "GITHUB_RUN_ATTEMPT": "9",
                       "GITHUB_SHA": build["commitHash"]}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(sys, "argv", ["preview_release.py", "--directory", str(assets["portable"].parent)])
    monkeypatch.setattr(preview_release, "AwsR2Store", lambda *args: MemoryStore())
    monkeypatch.setattr(preview_release, "run_command", lambda *args: "构建说明")
    published = []
    monkeypatch.setattr(preview_release, "publish_preview", lambda identity, *args: published.append(identity) or True)
    preview_release.main()
    assert published == [build] and published[0]["runAttempt"] == 1
