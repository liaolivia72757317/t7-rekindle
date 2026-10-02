"""Shared stable-release metadata and build-time configuration."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
from urllib.parse import quote, urlsplit


VERSION_PATTERN = re.compile(r"[vV]?([0-9]+\.[0-9]+\.[0-9]+(?:\.[0-9]+)?)(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?")


def asset_names(tag: str) -> dict[str, str]:
    return {"installer": f"T7-Rekindle-{tag}-Setup.exe",
            "portable": f"T7-Rekindle-windows-x64-{tag}.zip"}


def parse_version(value: str) -> tuple[int, int, int, int]:
    match = VERSION_PATTERN.fullmatch(value or "")
    if not match:
        raise ValueError("Stable tags must use vMAJOR.MINOR.PATCH[.REVISION][+BUILD].")
    parts = tuple(int(part) for part in match[1].split("."))
    if any(part > 65534 for part in parts):
        raise ValueError("Release version components must be between 0 and 65534.")
    return (*parts, 0) if len(parts) == 3 else parts


def normalize_base_url(value: str) -> str:
    address = (value or "").strip()
    parsed = urlsplit(address)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.port not in (None, 443)
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment
            or any(character.isspace() or ord(character) < 32 for character in address)
            or "\\" in address):
        raise ValueError("R2_PUBLIC_BASE_URL must be an HTTPS origin without credentials, path, query or fragment.")
    return address.rstrip("/")


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def build_manifest(release: dict, assets: dict[str, Path], base_url: str) -> dict:
    version = release["tag_name"]
    parse_version(version)
    base_url = normalize_base_url(base_url)
    manifest = {"schemaVersion": 1, "version": version, "summary": release.get("body") or ""}
    for kind, name in asset_names(version).items():
        matches = [asset for asset in release.get("assets", []) if asset.get("name") == name]
        if len(matches) != 1 or matches[0].get("state") != "uploaded":
            raise ValueError(f"GitHub Release must contain exactly one uploaded {name}.")
        path = assets[name]
        size, digest = path.stat().st_size, file_digest(path)
        remote = matches[0]
        if size <= 0 or remote.get("size") != size:
            raise ValueError(f"GitHub asset size mismatch: {name}.")
        if remote.get("digest") and remote["digest"].lower() != "sha256:" + digest:
            raise ValueError(f"GitHub asset digest mismatch: {name}.")
        manifest[kind] = {"url": f"{base_url}/releases/{quote(version, safe='')}/{quote(name, safe='')}",
                          "size": size, "sha256": digest}
    return manifest


def prepare_build(environment: dict[str, str]) -> None:
    base_url = environment.get("R2_PUBLIC_BASE_URL", "").strip()
    version = ""
    if environment.get("GITHUB_REF_TYPE") == "tag":
        parts = parse_version(environment.get("GITHUB_REF_NAME", ""))
        version = ".".join(str(part) for part in (parts if parts[3] else parts[:3]))
        if not base_url:
            raise ValueError("R2_PUBLIC_BASE_URL is required for a release tag build.")
    if base_url:
        base_url = normalize_base_url(base_url)
    destination = environment.get("GITHUB_ENV")
    if not destination:
        raise ValueError("GITHUB_ENV is required to export build metadata.")
    with Path(destination).open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(f"T7_RELEASE_VERSION={version}\nT7_UPDATE_BASE_URL={base_url}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare-build",))
    parser.parse_args()
    try:
        prepare_build(os.environ)
    except ValueError as error:
        parser.exit(1, f"release configuration error: {error}\n")
