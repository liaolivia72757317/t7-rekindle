"""Trusted embedded business packages; no transport or persistent PyObject state."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import math
import shutil
from pathlib import Path
import sys
import uuid

MAX_BYTES = 8 * 1024 * 1024
API_VERSION = 1


def _pack(value, depth=0):
    if depth > 32:
        raise ValueError("state nesting exceeds 32")
    if value is None or type(value) in (bool, str):
        return [type(value).__name__, value]
    if type(value) is int:
        if not -(1 << 63) <= value < (1 << 64):
            raise ValueError("integer outside signed/unsigned 64-bit range")
        return ["int", value]
    if type(value) is float and math.isfinite(value):
        return ["float", value]
    if type(value) is bytes:
        return ["bytes", base64.b64encode(value).decode("ascii")]
    if type(value) is list:
        return ["list", [_pack(item, depth + 1) for item in value]]
    if type(value) is dict and all(type(key) is str for key in value):
        return ["dict", {key: _pack(item, depth + 1) for key, item in value.items()}]
    raise TypeError(f"unsupported state type: {type(value).__name__}")


def _unpack(value):
    kind, data = value
    if kind == "bytes":
        return base64.b64decode(data, validate=True)
    if kind == "list":
        return [_unpack(item) for item in data]
    if kind == "dict":
        return {key: _unpack(item) for key, item in data.items()}
    if kind not in ("NoneType", "bool", "str", "int", "float"):
        raise ValueError("unknown state tag")
    return data


def encode(value):
    result = json.dumps(_pack(value), ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    if len(result) > MAX_BYTES:
        raise ValueError("state exceeds 8 MiB")
    return result


def decode(value):
    if len(value) > MAX_BYTES:
        raise ValueError("state exceeds 8 MiB")
    result = _unpack(json.loads(value))
    _pack(result)
    return result


def _forget(name):
    for key in list(sys.modules):
        if key == name or key.startswith(name + "."):
            del sys.modules[key]


class Revision:
    def __init__(self, root, cache):
        root = Path(root).resolve(strict=True)
        files = sorted(root.rglob("*.py"))
        if not (root / "__init__.py").is_file() or len(files) > 128:
            raise ValueError("business package requires __init__.py; maximum 128 modules")
        contents = []
        total = 0
        digest = hashlib.sha256()
        for path in files:
            if path.is_symlink() or root not in path.resolve().parents:
                raise ValueError("script link escapes package")
            data = path.read_bytes()
            total += len(data)
            if total > MAX_BYTES:
                raise ValueError("business package exceeds 8 MiB")
            relative = path.relative_to(root).as_posix()
            compile(data, relative, "exec")
            digest.update(relative.encode() + b"\0" + data + b"\0")
            contents.append((relative, data))
        self.version = digest.hexdigest()
        self.name = "t7rev_" + uuid.uuid4().hex
        self.cache = Path(cache).resolve()
        self.path = self.cache / self.name
        self.path.mkdir(parents=True, exist_ok=False)
        for relative, data in contents:
            target = self.path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        spec = importlib.util.spec_from_file_location(
            self.name, self.path / "__init__.py", submodule_search_locations=[str(self.path)])
        self.module = importlib.util.module_from_spec(spec)
        sys.modules[self.name] = self.module
        try:
            spec.loader.exec_module(self.module)
            if type(self.module.API_VERSION) is not int or self.module.API_VERSION != API_VERSION:
                raise ValueError("script API version mismatch")
            if type(self.module.STATE_VERSION) is not int or self.module.STATE_VERSION < 1:
                raise ValueError("invalid state version")
            for name in ("createState", "handleEvent", "validateState", "migrateState", "selfTest"):
                if not callable(getattr(self.module, name, None)):
                    raise ValueError(f"missing script entrypoint: {name}")
            if self.module.selfTest() is not True:
                raise ValueError("script selfTest did not pass")
        except BaseException:
            self.close()
            raise

    def close(self):
        _forget(self.name)
        # Only this revision's generated directory is eligible for removal.
        if self.path.exists():
            if self.path.resolve().parent != self.cache or self.path.name != self.name:
                raise ValueError("revision cleanup path mismatch")
            shutil.rmtree(self.path)


def _integer(value, minimum, maximum, field):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"invalid {field}")


def validateTransition(result, context):
    if type(result) is not dict or set(result) != {"state", "send", "timers", "logs"}:
        raise ValueError("transition requires state/send/timers/logs")
    for field in ("send", "timers", "logs"):
        if type(result[field]) is not list or len(result[field]) > 256:
            raise ValueError(f"invalid transition {field}")
    total = 0
    live = set(context.get("connections", []))
    for item in result["send"]:
        if type(item) is not dict or set(item) != {"connection", "command", "body", "reason"}:
            raise ValueError("invalid send fields")
        if item["connection"] not in live:
            raise ValueError("send targets a non-live connection")
        _integer(item["connection"], 1, (1 << 63) - 1, "connection")
        _integer(item["command"], 0, 65535, "command")
        if type(item["body"]) is not bytes or len(item["body"]) > 1024 * 1024 - 23:
            raise ValueError("invalid application body")
        if type(item["reason"]) is not str or len(item["reason"]) > 256:
            raise ValueError("invalid send reason")
        total += len(item["body"])
    if total > 2 * 1024 * 1024:
        raise ValueError("transition sends exceed 2 MiB")
    for timer in result["timers"]:
        if type(timer) is not dict or set(timer) != {"id", "delayMs", "event"}:
            raise ValueError("invalid timer fields")
        if type(timer["id"]) is not str or not 1 <= len(timer["id"]) <= 128:
            raise ValueError("invalid timer id")
        _integer(timer["delayMs"], -1, 86400000, "timer delay")
        if type(timer["event"]) is not dict or timer["event"].get("type") != "timer":
            raise ValueError("timers must dispatch timer events")
        if timer["event"].get("connection") not in live:
            raise ValueError("timer targets a non-live connection")
        _integer(timer["event"]["connection"], 1, (1 << 63) - 1, "timer connection")
        encode(timer["event"])
    for log in result["logs"]:
        if type(log) is not str or len(log) > 4096:
            raise ValueError("invalid business log")
    encode(result["state"])


class Runtime:
    def __init__(self, root, cache):
        self.root, self.cache = root, cache
        self.active = Revision(root, cache)
        self.previous = None
        self.candidate = None
        self.cleanupWarnings = []

    def retire(self, revision):
        try:
            revision.close()
        except (OSError, ValueError) as error:
            _forget(revision.name)
            self.cleanupWarnings.append(f"revision cleanup: {error}")

    def diagnostics(self):
        result, self.cleanupWarnings = self.cleanupWarnings, []
        return result

    def phase(self, state):
        value = decode(state)
        label = value.get("phase", "") if type(value) is dict else ""
        return label[:128] if type(label) is str else ""

    def version(self):
        return self.active.version

    def prepare(self):
        candidate = Revision(self.root, self.cache)
        if self.candidate:
            self.retire(self.candidate)
        self.candidate = candidate
        return candidate.version

    def create(self, context):
        value = self.active.module.createState(decode(context))
        if self.active.module.validateState(value) is not True:
            raise ValueError("initial state rejected")
        return encode(value)

    def dispatch(self, event, state, context):
        contextValue = decode(context)
        result = self.active.module.handleEvent(decode(event), decode(state), contextValue)
        validateTransition(result, contextValue)
        if self.active.module.validateState(result["state"]) is not True:
            raise ValueError("business state rejected")
        return (encode(result["state"]), result["send"],
                [(item["id"], item["delayMs"], encode(item["event"]), item["event"]["connection"]) for item in result["timers"]],
                result["logs"])

    def switch(self, state, rollback=False):
        target = self.previous if rollback else self.candidate
        if target is None:
            raise ValueError("no checked candidate/previous revision")
        value = decode(state)
        if target.module.STATE_VERSION != self.active.module.STATE_VERSION:
            value = target.module.migrateState(self.active.module.STATE_VERSION, value)
        if target.module.validateState(value) is not True:
            raise ValueError("reload state rejected")
        encoded = encode(value)
        obsolete = None if rollback else self.previous
        old = self.active
        self.active = target
        self.previous = old
        if not rollback:
            self.candidate = None
        if obsolete and obsolete is not target:
            self.retire(obsolete)
        return encoded

    def close(self):
        for revision in (self.active, self.previous, self.candidate):
            if revision:
                self.retire(revision)
