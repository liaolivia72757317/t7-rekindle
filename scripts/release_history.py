"""Publish mutable release notes and the backward-compatible stable index."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

from release_metadata import normalize_base_url, parse_version


STABLE_KEY = "updates/stable.json"
LATEST_NAMES = {"installer": "T7-Rekindle-Setup.exe", "portable": "T7-Rekindle-windows-x64.zip"}


def read_stable(store, directory: Path) -> dict | None:
    path = directory / "previous-stable.json"
    if not store.download(STABLE_KEY, path):
        return None
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 1:
        raise ValueError("Existing stable feed has an unsupported schemaVersion.")
    parse_version(manifest.get("version", ""))
    if any(not isinstance(manifest.get(kind), dict) for kind in LATEST_NAMES):
        raise ValueError("Existing stable feed is missing asset metadata.")
    indexed_versions(manifest)
    return manifest


def sorted_versions(versions) -> list[str]:
    return sorted(set(versions), key=lambda tag: (parse_version(tag), tag), reverse=True)


def indexed_versions(manifest: dict | None) -> list[str]:
    if manifest is None:
        return []
    versions = manifest.get("versions", [manifest["version"]])
    if not isinstance(versions, list) or any(not isinstance(tag, str) for tag in versions):
        raise ValueError("Existing stable versions must be an array of tags.")
    result = sorted_versions([*versions, manifest["version"]])
    if any(parse_version(tag) > parse_version(manifest["version"]) for tag in result):
        raise ValueError("Existing stable versions exceed the installable target.")
    return result


def write_notes(key: str, summary: str, store, base_url: str, directory: Path, verify) -> None:
    if not isinstance(summary, str):
        raise ValueError("Release notes must be text.")
    path = directory / "changelog.md"
    path.write_bytes(summary.encode("utf-8"))
    store.upload(key, path, content_type="text/markdown; charset=utf-8", cache_control="no-store")
    verify(normalize_base_url(base_url) + "/" + quote(key, safe="/"), summary)


def seed_legacy_notes(previous, store, base_url, directory, verify, exclude=None) -> None:
    if previous is not None and "versions" not in previous and previous["version"] != exclude:
        write_notes(f"releases/{previous['version']}/changelog.md", previous.get("summary") or "",
                    store, base_url, directory, verify)


def write_stable(manifest: dict, store, directory: Path) -> None:
    path = directory / "stable.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    store.upload(STABLE_KEY, path, content_type="application/json; charset=utf-8", cache_control="no-store")


def backfill_notes(releases: list[dict], store, base_url: str, directory: Path, verify_notes) -> None:
    previous = read_stable(store, directory)
    if previous is None:
        raise ValueError("Publish a stable release before backfilling notes.")
    target = parse_version(previous["version"])
    selected = {}
    for release in releases:
        if release.get("draft") is not False or release.get("prerelease") is not False:
            continue
        tag = release.get("tag_name", "")
        try:
            version = parse_version(tag)
        except ValueError:
            print(f"Skipped unsupported release tag: {tag!r}.")
            continue
        if version <= target:
            selected[tag] = release.get("body") or ""
    seed_legacy_notes(previous, store, base_url, directory, verify_notes)
    for tag in sorted_versions(selected):
        write_notes(f"releases/{tag}/changelog.md", selected[tag], store, base_url, directory, verify_notes)
    summary = selected.get(previous["version"], previous.get("summary") or "")
    write_notes("latest/changelog.md", summary, store, base_url, directory, verify_notes)
    manifest = {**previous, "summary": summary,
                "versions": sorted_versions([*indexed_versions(previous), *selected])}
    write_stable(manifest, store, directory)
    print(f"Published notes for {len(selected)} releases; stable target remains {previous['version']}.")
