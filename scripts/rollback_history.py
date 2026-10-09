"""Record installable history separately from the latest-version feeds."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib.parse import quote
import zipfile

from release_metadata import normalize_base_url, parse_version, validate_preview_build


def build_rollback_metadata() -> dict:
    root = Path(__file__).resolve().parents[1]
    core = (root / "src/Core/Settings.cs").read_text(encoding="utf-8-sig")
    installer = (root / "installer/T7-Rekindle.iss").read_text(encoding="utf-8-sig")
    schema = re.search(r"const int CurrentVersion = (\d+);", core)
    target = re.search(r"#define RollbackSettingsSchemaVersion (\d+)", installer)
    if not schema or not target or schema[1] != target[1]:
        raise ValueError("Installer settings schema must match SettingsSchema.CurrentVersion.")
    return {"rollbackProtocol": 1, "settingsSchemaVersion": int(schema[1])}


def read_rollback_metadata(portable: Path, channel: str, version: str, build=None) -> dict | None:
    with zipfile.ZipFile(portable) as archive:
        if archive.namelist().count("manifest.json") != 1:
            raise ValueError("The portable archive must contain one package manifest.")
        package = json.loads(archive.read("manifest.json"))
    if not isinstance(package, dict):
        raise ValueError("Invalid package manifest.")
    if "rollbackProtocol" not in package:
        return None
    schema = package.get("settingsSchemaVersion")
    if (type(package["rollbackProtocol"]) is not int or package["rollbackProtocol"] != 1
            or type(schema) is not int or not 0 < schema <= 2**31 - 1
            or package.get("schemaVersion") != 1 or package.get("release") is not True):
        raise ValueError("Unsupported rollback package contract.")
    identity = package.get("build", {})
    if (not isinstance(identity, dict) or identity.get("channel") != channel or parse_version(identity.get("version")) != parse_version(version)
            or (channel == "preview" and validate_preview_build(identity) != build)):
        raise ValueError("Rollback package identity does not match the published release.")
    return {"settingsSchemaVersion": schema}


def entry_identity(entry: dict, channel: str, base_url: str) -> tuple:
    if not isinstance(entry, dict):
        raise ValueError("Invalid history entry.")
    version = entry.get("version")
    if not isinstance(version, str):
        raise ValueError("Invalid history version.")
    parsed = parse_version(version)
    schema = entry.get("settingsSchemaVersion")
    if type(schema) is not int or not 0 < schema <= 2**31 - 1 or not isinstance(entry.get("summary"), str):
        raise ValueError("Invalid history settings schema or summary.")
    if not isinstance(entry.get("publishedAt"), str):
        raise ValueError("Invalid history timestamp.")
    published = datetime.fromisoformat(entry["publishedAt"])
    if published.tzinfo is None:
        raise ValueError("History timestamps must include a time zone.")
    if channel == "preview":
        build = validate_preview_build(entry.get("build"))
        if parse_version(build["version"]) != parsed:
            raise ValueError("History build version mismatch.")
        path = f"previews/{build['runId']}/{build['runAttempt']}/T7-Rekindle-Setup.exe"
        identity = (build["runId"], build["runAttempt"])
    else:
        path = f"releases/{quote(version, safe='')}/{quote('T7-Rekindle-' + version + '-Setup.exe', safe='')}"
        identity = (version,)
    asset = entry.get("installer", {})
    if (not isinstance(asset, dict) or asset.get("url") != base_url + "/" + path or type(asset.get("size")) is not int
            or asset["size"] <= 0 or not isinstance(asset.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-fA-F]{64}", asset["sha256"])):
        raise ValueError("Invalid history installer metadata.")
    return identity


def publish_history(manifest: dict, metadata: dict | None, channel: str, store,
                    base_url: str, directory: Path) -> None:
    if metadata is None:
        return
    base_url = normalize_base_url(base_url)
    key = f"updates/{channel}-history.json"
    path = directory / f"{channel}-history.json"
    entries = {}
    if store.download(key, path):
        previous = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(previous, dict) or previous.get("schemaVersion") != 1
                or previous.get("channel") != channel or not isinstance(previous.get("entries"), list)):
            raise ValueError("Invalid existing rollback history.")
        for entry in previous["entries"]:
            identity = entry_identity(entry, channel, base_url)
            if identity in entries:
                raise ValueError("Duplicate history identity.")
            entries[identity] = entry
    entry = {"version": manifest["version"], "summary": manifest["summary"],
             "installer": manifest["installer"], **metadata,
             "publishedAt": datetime.now(timezone.utc).isoformat()}
    if channel == "preview":
        entry["build"] = manifest["build"]
    identity = entry_identity(entry, channel, base_url)
    if identity in entries:
        previous = entries[identity]
        if any(previous.get(field) != entry.get(field) for field in ("installer", "build", "settingsSchemaVersion")):
            raise ValueError("An immutable history entry has different content.")
        entry["publishedAt"] = previous["publishedAt"]
    entries[identity] = entry
    def order(value):
        return ((value["build"]["runNumber"], value["build"]["runAttempt"]) if channel == "preview"
                else (parse_version(value["version"]), value["version"]))
    history = {"schemaVersion": 1, "channel": channel,
               "entries": sorted(entries.values(), key=order, reverse=True)}
    path.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    store.upload(key, path, content_type="application/json; charset=utf-8", cache_control="no-store")
