"""Release metadata and build-time channel identity."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import xml.etree.ElementTree as ET
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


def base_version() -> str:
    project = Path(__file__).resolve().parents[1] / "src/Desktop/T7.Desktop.csproj"
    return ET.parse(project).findtext("PropertyGroup/Version")


def is_preview_build(environment: dict[str, str]) -> bool:
    return (environment.get("GITHUB_REF_TYPE") == "branch"
            and environment.get("GITHUB_EVENT_NAME") in ("push", "workflow_dispatch")
            and bool(environment.get("T7_DEFAULT_BRANCH"))
            and environment.get("GITHUB_REF_NAME") == environment["T7_DEFAULT_BRANCH"])


def validate_preview_build(build: dict) -> dict:
    if not isinstance(build, dict) or build.get("channel") != "preview":
        raise ValueError("A preview build identity is required.")
    if not isinstance(build.get("version"), str) or not isinstance(build.get("commitHash"), str):
        raise ValueError("Preview version and commit hash must be strings.")
    parse_version(build.get("version", ""))
    for key, limit in (("runId", 2**63 - 1), ("runNumber", 2**63 - 1), ("runAttempt", 2**31 - 1)):
        if type(build.get(key)) is not int or not 0 < build[key] <= limit:
            raise ValueError(f"Invalid preview {key}.")
    if not re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", build.get("commitHash", "")):
        raise ValueError("Invalid preview commit hash.")
    return {key: build[key] for key in ("channel", "version", "runId", "runNumber", "runAttempt", "commitHash")}


def package_build_metadata(environment: dict[str, str]) -> dict:
    build = {"channel": environment.get("T7_BUILD_CHANNEL", "preview"),
             "version": environment.get("T7_RELEASE_VERSION") or environment.get("T7_BUILD_VERSION") or base_version(),
             "runId": int(environment.get("T7_PREVIEW_RUN_ID") or "0"),
             "runNumber": int(environment.get("T7_PREVIEW_RUN_NUMBER") or "0"),
             "runAttempt": int(environment.get("T7_PREVIEW_RUN_ATTEMPT") or "0"),
             "commitHash": environment.get("GITHUB_SHA", "")}
    parse_version(build["version"])
    if build["channel"] not in ("stable", "preview"):
        raise ValueError("Invalid build channel.")
    if any(build[key] != 0 for key in ("runId", "runNumber", "runAttempt")):
        validate_preview_build(build)
    return build


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
    preview = is_preview_build(environment)
    values = {"T7_RELEASE_VERSION": version, "T7_UPDATE_BASE_URL": base_url,
              "T7_BUILD_CHANNEL": "stable" if version else "preview",
              "T7_BUILD_VERSION": version or base_version(),
              "T7_PREVIEW_RUN_ID": environment.get("GITHUB_RUN_ID", "") if preview else "0",
              "T7_PREVIEW_RUN_NUMBER": environment.get("GITHUB_RUN_NUMBER", "") if preview else "0",
              "T7_PREVIEW_RUN_ATTEMPT": environment.get("GITHUB_RUN_ATTEMPT", "") if preview else "0"}
    build = package_build_metadata({**environment, **values})
    if preview:
        validate_preview_build(build)
    with Path(destination).open("a", encoding="utf-8", newline="\n") as stream:
        stream.writelines(f"{key}={value}\n" for key, value in values.items())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare-build",))
    parser.parse_args()
    try:
        prepare_build(os.environ)
    except ValueError as error:
        parser.exit(1, f"release configuration error: {error}\n")
