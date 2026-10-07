"""Mirror existing GitHub Release assets to R2, publishing the stable feed last."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from release_metadata import asset_names, build_manifest, file_digest, normalize_base_url, parse_version
from release_history import (LATEST_NAMES, STABLE_KEY, backfill_notes, indexed_versions, read_stable,
                             seed_legacy_notes, sorted_versions, write_notes, write_stable)


IMMUTABLE_CACHE = "public, max-age=31536000, immutable"


def run_command(arguments: list[str], *, allow_missing: bool = False) -> str | None:
    result = subprocess.run(arguments, capture_output=True, text=True, encoding="utf-8", check=False)
    if result.returncode:
        if allow_missing and re.search(r"\((404|NoSuchKey|NotFound)\)", result.stderr):
            return None
        raise RuntimeError(f"{arguments[0]} failed ({result.returncode}): {result.stderr.strip()}")
    return result.stdout


class AwsR2Store:
    def __init__(self, account_id: str, bucket: str):
        if not re.fullmatch(r"[0-9a-fA-F]{32}", account_id):
            raise ValueError("R2_ACCOUNT_ID must be a Cloudflare account ID.")
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
            raise ValueError("R2_BUCKET is invalid.")
        self.bucket = bucket
        self.prefix = ["aws", "--endpoint-url", f"https://{account_id}.r2.cloudflarestorage.com",
                       "--region", "auto", "--no-cli-pager", "--cli-connect-timeout", "10",
                       "--cli-read-timeout", "30"]

    def head(self, key: str) -> dict | None:
        result = run_command([*self.prefix, "s3api", "head-object", "--bucket", self.bucket,
                              "--key", key], allow_missing=True)
        return json.loads(result) if result is not None else None

    def download(self, key: str, destination: Path) -> bool:
        return run_command([*self.prefix, "s3api", "get-object", "--bucket", self.bucket,
                            "--key", key, str(destination)], allow_missing=True) is not None

    def upload(self, key: str, source: Path, *, content_type: str, cache_control: str,
               sha256: str | None = None) -> None:
        arguments = [*self.prefix, "s3", "cp", str(source), f"s3://{self.bucket}/{key}",
                     "--only-show-errors", "--content-type", content_type, "--cache-control", cache_control]
        if sha256:
            arguments.extend(["--metadata", f"sha256={sha256}"])
        run_command(arguments)


class HttpsRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        address = urlsplit(new_url)
        if address.scheme != "https" or address.username is not None or address.password is not None:
            raise ValueError("Public download redirected outside HTTPS.")
        return super().redirect_request(request, file, code, message, headers, new_url)


def verify_public_asset(url: str, asset: dict) -> None:
    opener = build_opener(HttpsRedirects())
    for attempt in range(3):
        try:
            request = Request(url, headers={"User-Agent": "T7-Rekindle-release-mirror",
                                            "Accept-Encoding": "identity"})
            with opener.open(request, timeout=30) as response:
                digest, size = hashlib.sha256(), 0
                while block := response.read(1024 * 1024):
                    size += len(block)
                    if size > asset["size"]:
                        raise ValueError("Public asset exceeds the expected size.")
                    digest.update(block)
            if size != asset["size"] or digest.hexdigest() != asset["sha256"]:
                raise ValueError("Public asset size or digest mismatch.")
            return
        except (URLError, TimeoutError, ConnectionError) as error:
            if attempt == 2 or (isinstance(error, HTTPError) and error.code < 500
                                and error.code not in (408, 429)):
                raise
            time.sleep(2 ** attempt)


def verify_public_text(url: str, text: str) -> None:
    expected = text.encode("utf-8")
    opener = build_opener(HttpsRedirects())
    for attempt in range(3):
        try:
            request = Request(url, headers={"User-Agent": "T7-Rekindle-release-mirror",
                                            "Accept-Encoding": "identity", "Cache-Control": "no-cache"})
            with opener.open(request, timeout=30) as response:
                if response.read(len(expected) + 1) != expected:
                    raise ValueError("Public release notes differ from the uploaded text.")
            return
        except (URLError, TimeoutError, ConnectionError) as error:
            if attempt == 2 or (isinstance(error, HTTPError) and error.code < 500
                                and error.code not in (408, 429)):
                raise
            time.sleep(2 ** attempt)


def publish_release(release: dict, assets: dict[str, Path], store, base_url: str,
                    work_directory: Path, verify=verify_public_asset, *, verify_notes=verify_public_text) -> bool:
    if release.get("draft") or release.get("prerelease"):
        print("Draft and prerelease versions are excluded from the stable channel.")
        return False
    if release.get("draft") is not False or release.get("prerelease") is not False:
        raise ValueError("GitHub Release publication state is missing.")
    manifest = build_manifest(release, assets, base_url)
    version = manifest["version"]
    names = asset_names(version)
    previous = read_stable(store, work_directory)
    comparison = 1 if previous is None else (
        (parse_version(version) > parse_version(previous["version"]))
        - (parse_version(version) < parse_version(previous["version"])))
    if comparison == 0:
        for kind in names:
            if any(previous[kind].get(field) != manifest[kind][field] for field in ("size", "sha256")):
                raise ValueError("Equivalent stable versions have different immutable assets.")
    for kind, name in names.items():
        key, asset = f"releases/{version}/{name}", manifest[kind]
        existing = store.head(key)
        if existing is not None:
            digest = existing.get("Metadata", {}).get("sha256")
            if not digest:
                existing_path = work_directory / f"existing-{name}"
                if not store.download(key, existing_path):
                    raise ValueError("An existing immutable asset disappeared during verification.")
                digest = file_digest(existing_path)
            if existing.get("ContentLength") != asset["size"] or digest != asset["sha256"]:
                raise ValueError(f"Refusing to overwrite an immutable asset: {key}. Use a new tag.")
        else:
            store.upload(key, assets[name], content_type="application/zip" if kind == "portable"
                         else "application/octet-stream", cache_control=IMMUTABLE_CACHE, sha256=asset["sha256"])
        verify(asset["url"], asset)
        print(f"Verified {key} ({asset['size']} bytes).")

    seed_legacy_notes(previous, store, base_url, work_directory, verify_notes, exclude=version)
    write_notes(f"releases/{version}/changelog.md", manifest["summary"],
                store, base_url, work_directory, verify_notes)
    if comparison >= 0:
        for kind, name in LATEST_NAMES.items():
            key, asset = "latest/" + name, manifest[kind]
            store.upload(key, assets[names[kind]], content_type="application/zip" if kind == "portable"
                         else "application/octet-stream", cache_control="no-store", sha256=asset["sha256"])
            verify(normalize_base_url(base_url) + "/" + key, asset)
        write_notes("latest/changelog.md", manifest["summary"], store, base_url, work_directory, verify_notes)
    result = {**(manifest if comparison >= 0 else previous),
              "versions": sorted_versions([*indexed_versions(previous), version])}
    write_stable(result, store, work_directory)
    print(f"Published stable feed for {result['version']} with {len(result['versions'])} versions.")
    return comparison >= 0


def list_releases(repository: str) -> list[dict]:
    pages = json.loads(run_command(["gh", "api", "--paginate", "--slurp",
                                   f"repos/{repository}/releases?per_page=100"]))
    if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
        raise ValueError("GitHub returned an invalid release list.")
    releases = [release for page in pages for release in page]
    if any(not isinstance(release, dict) for release in releases):
        raise ValueError("GitHub returned an invalid release entry.")
    return releases


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--tag")
    mode.add_argument("--backfill-notes", action="store_true")
    arguments = parser.parse_args()
    try:
        arguments.tag = arguments.tag or os.environ.get("RELEASE_TAG", "").strip()
        if arguments.backfill_notes and arguments.tag:
            raise ValueError("Choose either a Release tag or --backfill-notes.")
        if not arguments.tag and not arguments.backfill_notes:
            raise ValueError("A Release tag is required.")
        repository = os.environ.get("GITHUB_REPOSITORY", "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("GITHUB_REPOSITORY is required.")
        for name in ("R2_ACCOUNT_ID", "R2_BUCKET", "R2_PUBLIC_BASE_URL",
                     "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "GH_TOKEN"):
            if not os.environ.get(name, "").strip():
                raise ValueError(f"{name} is required.")
        base_url = normalize_base_url(os.environ["R2_PUBLIC_BASE_URL"])
        store = AwsR2Store(os.environ["R2_ACCOUNT_ID"], os.environ["R2_BUCKET"])
        if arguments.backfill_notes:
            with tempfile.TemporaryDirectory(prefix="t7-r2-notes-") as temporary:
                backfill_notes(list_releases(repository), store, base_url, Path(temporary), verify_public_text)
            return
        release = json.loads(run_command(["gh", "api",
                                         f"repos/{repository}/releases/tags/{quote(arguments.tag, safe='')}"]))
        if release.get("tag_name") != arguments.tag:
            raise ValueError("GitHub returned a different release tag.")
        if release.get("draft") or release.get("prerelease"):
            print("Draft and prerelease versions are excluded from the stable channel.")
            return
        parse_version(arguments.tag)
        names = asset_names(arguments.tag)
        with tempfile.TemporaryDirectory(prefix="t7-r2-release-") as temporary:
            directory = Path(temporary)
            command = ["gh", "release", "download", "--repo", repository, "--dir", str(directory)]
            for name in names.values():
                command.extend(["--pattern", name])
            run_command([*command, "--", arguments.tag])
            assets = {name: directory / name for name in names.values()}
            publish_release(release, assets, store, base_url, directory)
    except (OSError, RuntimeError, ValueError) as error:
        parser.exit(1, f"release mirror error: {error}\n")


if __name__ == "__main__":
    main()
