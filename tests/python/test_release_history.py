import json
from unittest.mock import call, patch
from urllib.error import HTTPError, URLError

import pytest

from test_release_mirror import MemoryStore, load_module, publish, release_fixture


BASE = "https://downloads.example.com"
LATEST_NAMES = ("latest/T7-Rekindle-Setup.exe", "latest/T7-Rekindle-windows-x64.zip",
                "latest/changelog.md")


def feed(store):
    return json.loads(store.objects["updates/stable.json"][0])


def backfill(tmp_path, store, releases, verify_notes=lambda url, text: None):
    return load_module("release_history").backfill_notes(releases, store, BASE, tmp_path, verify_notes)


def test_latest_is_fixed_verified_and_never_replaced_by_an_older_release(tmp_path):
    store = MemoryStore()
    publish(tmp_path, store, "v0.2.0")
    publish(tmp_path, store, "v0.10.0")
    latest = {key: store.objects[key] for key in LATEST_NAMES}
    assert latest[LATEST_NAMES[0]][0] == store.objects["releases/v0.10.0/T7-Rekindle-v0.10.0-Setup.exe"][0]
    assert latest[LATEST_NAMES[1]][0] == store.objects["releases/v0.10.0/T7-Rekindle-windows-x64-v0.10.0.zip"][0]
    assert all(options["cache_control"] == "no-store" for _, options in latest.values())
    assert "latest/" not in feed(store)["installer"]["url"]
    store.writes.clear()
    publish(tmp_path, store, "v0.3.0")
    assert feed(store)["versions"] == ["v0.10.0", "v0.3.0", "v0.2.0"]
    assert all(store.objects[key] == value for key, value in latest.items())
    assert not any(key.startswith("latest/") for key in store.writes)
    assert store.writes[-1] == "updates/stable.json"


@pytest.mark.parametrize("latest_version", ["v1.0.0", "v2.0.0"])
def test_existing_assets_without_digest_metadata_preserve_the_stable_manifest(tmp_path, latest_version):
    store = MemoryStore()
    publish(tmp_path, store, "v1.0.0")
    if latest_version != "v1.0.0":
        publish(tmp_path, store, latest_version)
    previous = feed(store)
    latest = {key: store.objects[key] for key in LATEST_NAMES}
    asset_keys = [key for key in store.objects if key.startswith("releases/v1.0.0/")
                  and key.endswith((".exe", ".zip"))]
    for key in asset_keys:
        content, options = store.objects[key]
        store.objects[key] = (content, {name: value for name, value in options.items() if name != "sha256"})
    store.writes.clear()
    with patch.object(store, "download", wraps=store.download) as download:
        assert publish(tmp_path, store, "v1.0.0") == (latest_version == "v1.0.0")
        assert set(asset_keys).issubset({arguments.args[0] for arguments in download.call_args_list})
    assert feed(store) == previous
    assert all(store.objects[key] == value for key, value in latest.items())
    assert not any(key in asset_keys for key in store.writes)
    if latest_version != "v1.0.0":
        assert not any(key.startswith("latest/") for key in store.writes)


@pytest.mark.parametrize("key", [*LATEST_NAMES, "releases/v2.0.0/changelog.md"])
def test_failed_mutable_upload_preserves_the_previous_feed_and_retry_repairs_latest(tmp_path, key):
    store = MemoryStore()
    publish(tmp_path, store, "v1.0.0")
    previous = store.objects["updates/stable.json"]
    store.fail_key = key
    with pytest.raises(RuntimeError, match="upload failed"):
        publish(tmp_path, store, "v2.0.0")
    assert store.objects["updates/stable.json"] == previous
    store.fail_key = None
    publish(tmp_path, store, "v2.0.0")
    assert feed(store)["version"] == "v2.0.0"
    assert store.objects[LATEST_NAMES[0]][0].startswith(b"v2.0.0")


def test_legacy_feed_is_seeded_and_markdown_corrections_update_latest(tmp_path):
    store = MemoryStore()
    publish(tmp_path, store, "v1.0.0")
    legacy = feed(store)
    del legacy["versions"]
    _, options = store.objects["updates/stable.json"]
    store.objects["updates/stable.json"] = (json.dumps(legacy).encode(), options)
    del store.objects["releases/v1.0.0/changelog.md"]
    publish(tmp_path, store, "v2.0.0")
    assert store.objects["releases/v1.0.0/changelog.md"][0].decode() == legacy["summary"]
    release, assets = release_fixture(tmp_path, "v2.0.0")
    notes = "## 修正说明\n\n- 保留 **Markdown**。\n"
    release["body"] = notes
    verified = []
    load_module("mirror_release").publish_release(release, assets, store, BASE, tmp_path,
        lambda url, asset: None, verify_notes=lambda url, text: verified.append((url, text)))
    assert feed(store)["summary"] == notes
    assert feed(store)["versions"] == ["v2.0.0", "v1.0.0"]
    for key in ("releases/v2.0.0/changelog.md", "latest/changelog.md"):
        assert store.objects[key][0] == notes.encode("utf-8")
        assert (BASE + "/" + key, notes) in verified


def test_latest_public_verification_failure_does_not_commit_the_new_feed(tmp_path):
    store = MemoryStore()
    publish(tmp_path, store, "v1.0.0")
    previous = store.objects["updates/stable.json"]
    def verify(url, asset):
        if "/latest/" in url:
            raise ValueError("latest verification failed")
    with pytest.raises(ValueError, match="latest verification"):
        publish(tmp_path, store, "v2.0.0", verify)
    assert store.objects["updates/stable.json"] == previous


def test_public_notes_verification_checks_exact_utf8_and_extra_bytes():
    mirror = load_module("mirror_release")
    for content, valid in [("## 更正\n".encode(), True), (b"", False), ("## 更正\nextra".encode(), False)]:
        with patch.object(mirror, "build_opener") as opener, patch.object(mirror.time, "sleep") as sleep:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.read.return_value = content
            if valid:
                mirror.verify_public_text(BASE + "/latest/changelog.md", "## 更正\n")
            else:
                with pytest.raises(ValueError, match="notes differ"):
                    mirror.verify_public_text(BASE + "/latest/changelog.md", "## 更正\n")
            assert response.read.call_args.args == (len("## 更正\n".encode()) + 1,)
            assert opener.return_value.open.call_count == 1
            sleep.assert_not_called()


@pytest.mark.parametrize("stage", ["open", "read"])
@pytest.mark.parametrize("failure_factory", [
    lambda: HTTPError(BASE, 500, "temporary failure", None, None),
    lambda: HTTPError(BASE, 503, "temporary failure", None, None),
    lambda: HTTPError(BASE, 408, "temporary failure", None, None),
    lambda: HTTPError(BASE, 429, "temporary failure", None, None),
    lambda: URLError("temporary connection failure"), lambda: TimeoutError("temporary timeout"),
    lambda: ConnectionResetError("temporary reset"),
], ids=["http-500", "http-503", "http-408", "http-429", "url-error", "timeout", "connection-reset"])
def test_public_notes_verification_retries_transient_errors(failure_factory, stage):
    mirror = load_module("mirror_release")
    failure = failure_factory()
    text = "## 更新说明\n"
    with patch.object(mirror, "build_opener") as opener, patch.object(mirror.time, "sleep") as sleep:
        open_request = opener.return_value.open
        response = open_request.return_value.__enter__.return_value
        response.read.return_value = text.encode("utf-8")
        operation = open_request if stage == "open" else response.read
        operation.side_effect = [failure, failure, operation.return_value]
        mirror.verify_public_text(BASE + "/latest/changelog.md", text)
        assert open_request.call_count == 3
        assert sleep.call_args_list == [call(1), call(2)]


@pytest.mark.parametrize("stage", ["open", "read"])
def test_public_notes_verification_stops_after_three_transient_failures(stage):
    mirror = load_module("mirror_release")
    failure = HTTPError(BASE, 503, "temporary failure", None, None)
    with patch.object(mirror, "build_opener") as opener, patch.object(mirror.time, "sleep") as sleep:
        open_request = opener.return_value.open
        operation = open_request if stage == "open" else open_request.return_value.__enter__.return_value.read
        operation.side_effect = failure
        with pytest.raises(HTTPError) as caught:
            mirror.verify_public_text(BASE + "/latest/changelog.md", "更新说明")
        assert caught.value is failure
        assert open_request.call_count == 3
        assert sleep.call_args_list == [call(1), call(2)]


@pytest.mark.parametrize("status", [400, 403, 404])
def test_public_notes_verification_does_not_retry_permanent_http_errors(status):
    mirror = load_module("mirror_release")
    failure = HTTPError(BASE, status, "permanent failure", None, None)
    with patch.object(mirror, "build_opener") as opener, patch.object(mirror.time, "sleep") as sleep:
        opener.return_value.open.side_effect = failure
        with pytest.raises(HTTPError) as caught:
            mirror.verify_public_text(BASE + "/latest/changelog.md", "更新说明")
        assert caught.value is failure
        assert opener.return_value.open.call_count == 1
        sleep.assert_not_called()


def test_history_backfill_only_uploads_notes_and_updates_target_summary(tmp_path):
    store = MemoryStore()
    publish(tmp_path, store, "v2.0.0")
    original = feed(store)
    store.writes.clear()
    releases = [{"tag_name": tag, "body": text, "draft": False, "prerelease": False}
                for tag, text in [("v0.2.0", "旧版"), ("v0.10.0", ""), ("v2.0.0", "更正"),
                                  ("v3.0.0", "尚未镜像"), ("v2.1.0-rc.1", "预发布")]]
    releases += [{"tag_name": "v1.0.0", "body": "草稿", "draft": True, "prerelease": False}]
    backfill(tmp_path, store, releases)
    result = feed(store)
    assert result["versions"] == ["v2.0.0", "v0.10.0", "v0.2.0"]
    assert result["version"] == original["version"] and result["installer"] == original["installer"]
    assert result["summary"] == "更正"
    assert store.objects["latest/changelog.md"][0].decode() == "更正"
    assert store.objects["releases/v0.10.0/changelog.md"][0] == b""
    assert all(key.endswith(("changelog.md", "stable.json")) for key in store.writes)
    assert store.writes[-1] == "updates/stable.json"


def test_backfill_requires_an_existing_feed_and_does_not_commit_failed_notes(tmp_path):
    store = MemoryStore()
    with pytest.raises(ValueError, match="stable"):
        backfill(tmp_path, store, [])
    assert not store.writes
    publish(tmp_path, store)
    previous = store.objects["updates/stable.json"]
    release, _ = release_fixture(tmp_path, "v1.0.0")
    def fail(url, text):
        raise ValueError("notes verification failed")
    with pytest.raises(ValueError, match="notes verification"):
        backfill(tmp_path, store, [release], fail)
    assert store.objects["updates/stable.json"] == previous


def test_github_history_reads_all_pages_and_backfill_mode_skips_asset_downloads(tmp_path, monkeypatch):
    mirror = load_module("mirror_release")
    releases = [{"tag_name": "v1.0.0"}, {"tag_name": "v2.0.0"}]
    with patch.object(mirror, "run_command", return_value=json.dumps([[releases[0]], [releases[1]]])) as command:
        assert mirror.list_releases("example/project") == releases
        assert "--paginate" in command.call_args.args[0] and "--slurp" in command.call_args.args[0]
    monkeypatch.setattr("sys.argv", ["mirror_release.py", "--backfill-notes"])
    monkeypatch.delenv("RELEASE_TAG", raising=False)
    for name, value in {"GITHUB_REPOSITORY": "example/project", "R2_ACCOUNT_ID": "0" * 32,
                        "R2_BUCKET": "release-fixture", "R2_PUBLIC_BASE_URL": BASE,
                        "AWS_ACCESS_KEY_ID": "TOKEN", "AWS_SECRET_ACCESS_KEY": "TOKEN", "GH_TOKEN": "TOKEN"}.items():
        monkeypatch.setenv(name, value)
    with patch.object(mirror, "list_releases", return_value=releases), \
            patch.object(mirror, "backfill_notes") as operation, patch.object(mirror, "run_command") as command:
        mirror.main()
        operation.assert_called_once()
        command.assert_not_called()


def test_backfill_and_tag_modes_are_mutually_exclusive(monkeypatch):
    mirror = load_module("mirror_release")
    monkeypatch.setattr("sys.argv", ["mirror_release.py", "--backfill-notes", "--tag", "v1.0.0"])
    with patch.object(mirror, "run_command") as command, pytest.raises(SystemExit) as failure:
        mirror.main()
    assert failure.value.code != 0
    command.assert_not_called()
    monkeypatch.setattr("sys.argv", ["mirror_release.py", "--backfill-notes"])
    monkeypatch.setenv("RELEASE_TAG", "v1.0.0")
    with patch.object(mirror, "run_command") as command, pytest.raises(SystemExit) as failure:
        mirror.main()
    assert failure.value.code == 1
    command.assert_not_called()
