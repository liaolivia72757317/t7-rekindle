"""Publish verified default-branch CI artifacts to the R2 preview channel."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import tempfile
import zipfile

from mirror_release import AwsR2Store, IMMUTABLE_CACHE, run_command, verify_public_asset
from release_metadata import file_digest, normalize_base_url, validate_preview_build


PREVIEW_KEY = "updates/preview.json"
ASSET_NAMES = {"installer": "T7-Rekindle-Setup.exe", "portable": "T7-Rekindle-windows-x64.zip"}


def read_preview_build(portable: Path) -> dict:
    with zipfile.ZipFile(portable) as archive:
        if archive.namelist().count("manifest.json") != 1:
            raise ValueError("The portable archive must contain one package manifest.")
        manifest = json.loads(archive.read("manifest.json"))
    if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 1 or manifest.get("release") is not True:
        raise ValueError("A verified release package is required.")
    return validate_preview_build(manifest.get("build"))


def sequence(build: dict) -> tuple[int, int]:
    return build["runNumber"], build["runAttempt"]


def publish_preview(build: dict, assets: dict[str, Path], store, base_url: str, work_directory: Path,
                    summary: str, *, verify=verify_public_asset) -> bool:
    build = validate_preview_build(build)
    base_url = normalize_base_url(base_url)
    manifest = {"schemaVersion": 1, "channel": "preview", "version": "v" + build["version"].lstrip("vV"),
                "build": build, "summary": summary}
    prefix = f"previews/{build['runId']}/{build['runAttempt']}/"
    for kind, name in ASSET_NAMES.items():
        size = assets[kind].stat().st_size
        if size <= 0:
            raise ValueError("Preview assets must not be empty.")
        manifest[kind] = {"url": base_url + "/" + prefix + name, "size": size, "sha256": file_digest(assets[kind])}

    previous_path = work_directory / "previous-preview.json"
    if store.download(PREVIEW_KEY, previous_path):
        previous = json.loads(previous_path.read_text(encoding="utf-8"))
        if not isinstance(previous, dict) or previous.get("schemaVersion") != 1 or previous.get("channel") != "preview":
            raise ValueError("Existing preview feed is invalid.")
        previous_build = validate_preview_build(previous.get("build"))
        if sequence(build) < sequence(previous_build):
            return False
        if sequence(build) == sequence(previous_build):
            if build != previous_build or any(previous.get(kind) != manifest[kind] for kind in ASSET_NAMES):
                raise ValueError("An immutable preview build already exists with different content.")

    for kind, name in ASSET_NAMES.items():
        key, asset = prefix + name, manifest[kind]
        existing = store.head(key)
        if existing is not None:
            digest = existing.get("Metadata", {}).get("sha256")
            if not digest:
                existing_path = work_directory / ("existing-" + name)
                if not store.download(key, existing_path):
                    raise ValueError("An immutable preview asset disappeared.")
                digest = file_digest(existing_path)
            if existing.get("ContentLength") != asset["size"] or digest != asset["sha256"]:
                raise ValueError("An immutable preview asset has different content.")
        else:
            store.upload(key, assets[kind], content_type="application/zip" if kind == "portable" else "application/octet-stream",
                         cache_control=IMMUTABLE_CACHE, sha256=asset["sha256"])
        verify(asset["url"], asset)

    destination = work_directory / "preview.json"
    destination.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    store.upload(PREVIEW_KEY, destination, content_type="application/json; charset=utf-8", cache_control="no-store")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("dist"))
    arguments = parser.parse_args()
    try:
        for key in ("R2_ACCOUNT_ID", "R2_BUCKET", "R2_PUBLIC_BASE_URL", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
            if not os.environ.get(key, "").strip():
                raise ValueError(f"{key} is required.")
        repository = os.environ.get("GITHUB_REPOSITORY", "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("GITHUB_REPOSITORY is required.")
        assets = {kind: arguments.directory / name for kind, name in ASSET_NAMES.items()}
        build = read_preview_build(assets["portable"])
        if (str(build["runId"]) != os.environ.get("GITHUB_RUN_ID")
                or str(build["runNumber"]) != os.environ.get("GITHUB_RUN_NUMBER")
                or build["commitHash"] != os.environ.get("GITHUB_SHA")):
            raise ValueError("Preview artifacts do not belong to this workflow run.")
        subject = run_command(["git", "log", "-1", "--format=%s", build["commitHash"]]).strip()
        summary = (f"{subject}\n\n提交 `{build['commitHash']}`\n\n"
                   f"[查看 CI 构建](https://github.com/{repository}/actions/runs/{build['runId']})")
        store = AwsR2Store(os.environ["R2_ACCOUNT_ID"], os.environ["R2_BUCKET"])
        with tempfile.TemporaryDirectory(prefix="t7-preview-") as temporary:
            published = publish_preview(build, assets, store, os.environ["R2_PUBLIC_BASE_URL"], Path(temporary), summary)
        print("Published preview feed." if published else "A newer preview is already published.")
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile) as error:
        parser.exit(1, f"preview publication error: {error}\n")


if __name__ == "__main__":
    main()
