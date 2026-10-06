"""Command-line interface for deterministic, read-only M3 static analysis."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from . import __version__
from .capture import build_method3_capture_manifest
from .errors import M3AnalysisError
from .minidump import inspect_minidump
from .tdr import find_tdr_blocks, parse_tdr_enum, parse_tdr_macro, parse_tdr_struct, parse_tdr_union

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CLIENT_ROOT = PROJECT_ROOT / "client"


def _display_path(path: Path) -> str:
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return path.name


def _load_image(path: Path) -> tuple[Path, str, bytes, str]:
    resolved = path.resolve()
    source = _display_path(resolved)
    if not resolved.is_file():
        raise M3AnalysisError("image is not a regular file", source=source, offset=0)
    data = resolved.read_bytes()
    return resolved, source, data, hashlib.sha256(data).hexdigest()


def _write_json(path: Path, value: dict[str, object]) -> None:
    resolved = path.resolve()
    if resolved == CLIENT_ROOT or resolved.is_relative_to(CLIENT_ROOT):
        raise ValueError("refusing to write output inside the read-only client directory")
    if resolved.exists():
        raise ValueError(f"refusing to overwrite existing output: {resolved}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved.with_name(f".{resolved.name}.tmp")
    if temporary.exists():
        raise ValueError(f"temporary output already exists: {temporary}")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as output:
            json.dump(value, output, ensure_ascii=False, indent=2, sort_keys=True)
            output.write("\n")
        os.rename(temporary, resolved)
    finally:
        temporary.unlink(missing_ok=True)


def _write_bytes(path: Path, value: bytes) -> None:
    resolved = path.resolve()
    if resolved == CLIENT_ROOT or resolved.is_relative_to(CLIENT_ROOT):
        raise ValueError("refusing to write output inside the read-only client directory")
    if resolved.exists():
        raise ValueError(f"refusing to overwrite existing output: {resolved}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved.with_name(f".{resolved.name}.tmp")
    if temporary.exists():
        raise ValueError(f"temporary output already exists: {temporary}")
    try:
        with temporary.open("xb") as output:
            output.write(value)
        os.rename(temporary, resolved)
    finally:
        temporary.unlink(missing_ok=True)


def _run_discover(arguments: argparse.Namespace) -> int:
    _, source, data, image_sha256 = _load_image(arguments.image)
    blocks = find_tdr_blocks(data, source=source)
    result: dict[str, object] = {
        "schemaVersion": 1,
        "tool": {"name": "tools.m3", "version": __version__},
        "image": {"path": source, "size": len(data), "sha256": image_sha256},
        "tdrBlocks": [block.to_dict() for block in blocks],
    }
    _write_json(arguments.output, result)
    return 0


def _run_enum(arguments: argparse.Namespace) -> int:
    _, source, data, image_sha256 = _load_image(arguments.image)
    matching_blocks = [
        block for block in find_tdr_blocks(data, source=source) if block.metalib_name == arguments.metalib
    ]
    if len(matching_blocks) != 1:
        raise M3AnalysisError(
            f"expected one metalib named {arguments.metalib!r}, found {len(matching_blocks)}",
            source=source,
            offset=0,
        )
    block = matching_blocks[0]
    enum = parse_tdr_enum(block, enum_name=arguments.enum_name, item_prefix=arguments.item_prefix)
    result: dict[str, object] = {
        "schemaVersion": 1,
        "tool": {"name": "tools.m3", "version": __version__},
        "image": {"path": source, "size": len(data), "sha256": image_sha256},
        "tdrBlock": block.to_dict(),
        "enum": enum.to_dict(),
    }
    _write_json(arguments.output, result)
    return 0


def _run_macro(arguments: argparse.Namespace) -> int:
    _, source, data, image_sha256 = _load_image(arguments.image)
    matching_blocks = [
        block for block in find_tdr_blocks(data, source=source) if block.metalib_name == arguments.metalib
    ]
    if len(matching_blocks) != 1:
        raise M3AnalysisError(
            f"expected one metalib named {arguments.metalib!r}, found {len(matching_blocks)}",
            source=source,
            offset=0,
        )
    block = matching_blocks[0]
    macro = parse_tdr_macro(block, macro_name=arguments.macro_name)
    result: dict[str, object] = {
        "schemaVersion": 1,
        "tool": {"name": "tools.m3", "version": __version__},
        "image": {"path": source, "size": len(data), "sha256": image_sha256},
        "tdrBlock": block.to_dict(),
        "macro": macro.to_dict(),
    }
    _write_json(arguments.output, result)
    return 0


def _run_struct(arguments: argparse.Namespace) -> int:
    _, source, data, image_sha256 = _load_image(arguments.image)
    matching_blocks = [
        block for block in find_tdr_blocks(data, source=source) if block.metalib_name == arguments.metalib
    ]
    if len(matching_blocks) != 1:
        raise M3AnalysisError(
            f"expected one metalib named {arguments.metalib!r}, found {len(matching_blocks)}",
            source=source,
            offset=0,
        )
    block = matching_blocks[0]
    struct_value = parse_tdr_struct(block, struct_name=arguments.struct_name)
    result: dict[str, object] = {
        "schemaVersion": 1,
        "tool": {"name": "tools.m3", "version": __version__},
        "image": {"path": source, "size": len(data), "sha256": image_sha256},
        "tdrBlock": block.to_dict(),
        "struct": struct_value.to_dict(),
    }
    _write_json(arguments.output, result)
    return 0


def _run_union(arguments: argparse.Namespace) -> int:
    _, source, data, image_sha256 = _load_image(arguments.image)
    matching_blocks = [
        block for block in find_tdr_blocks(data, source=source) if block.metalib_name == arguments.metalib
    ]
    if len(matching_blocks) != 1:
        raise M3AnalysisError(
            f"expected one metalib named {arguments.metalib!r}, found {len(matching_blocks)}",
            source=source,
            offset=0,
        )
    block = matching_blocks[0]
    union_value = parse_tdr_union(block, union_name=arguments.union_name)
    result: dict[str, object] = {
        "schemaVersion": 1,
        "tool": {"name": "tools.m3", "version": __version__},
        "image": {"path": source, "size": len(data), "sha256": image_sha256},
        "tdrBlock": block.to_dict(),
        "union": union_value.to_dict(),
    }
    _write_json(arguments.output, result)
    return 0


def _run_extract_tdr(arguments: argparse.Namespace) -> int:
    _, source, data, _ = _load_image(arguments.image)
    matching_blocks = [
        block for block in find_tdr_blocks(data, source=source) if block.metalib_name == arguments.metalib
    ]
    if len(matching_blocks) != 1:
        raise M3AnalysisError(
            f"expected one metalib named {arguments.metalib!r}, found {len(matching_blocks)}",
            source=source,
            offset=0,
        )
    _write_bytes(arguments.output, matching_blocks[0].data)
    return 0


def _run_inspect_dump(arguments: argparse.Namespace) -> int:
    _, source, data, dump_sha256 = _load_image(arguments.dump)
    result: dict[str, object] = {
        "schemaVersion": 1,
        "tool": {"name": "tools.m3", "version": __version__},
        "dump": {"path": source, "size": len(data), "sha256": dump_sha256},
        "inspection": inspect_minidump(data, source=source),
    }
    _write_json(arguments.output, result)
    return 0


def _run_decode_capture(arguments: argparse.Namespace) -> int:
    _, capture_source, capture_data, _ = _load_image(arguments.capture)
    _, payload_source, payload, _ = _load_image(arguments.payload)
    try:
        session_key = bytes.fromhex(arguments.session_key_hex)
    except ValueError as error:
        raise ValueError("session key must be hexadecimal") from error
    result = build_method3_capture_manifest(
        capture_data,
        payload,
        session_key=session_key,
        max_frame_size=arguments.max_frame_size,
    )
    result["tool"] = {"name": "tools.m3", "version": __version__}
    result["capture"]["path"] = capture_source
    result["payload"]["path"] = payload_source
    _write_json(arguments.output, result)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.m3")
    subparsers = parser.add_subparsers(dest="command", required=True)

    discover = subparsers.add_parser("discover", help="find bounded embedded TDR metalibs")
    discover.add_argument("--image", required=True, type=Path)
    discover.add_argument("--output", required=True, type=Path)
    discover.set_defaults(handler=_run_discover)

    enum = subparsers.add_parser("enum", help="recover one TDR enum and its declaration-order values")
    enum.add_argument("--image", required=True, type=Path)
    enum.add_argument("--metalib", required=True)
    enum.add_argument("--enum", required=True, dest="enum_name")
    enum.add_argument("--item-prefix", required=True)
    enum.add_argument("--output", required=True, type=Path)
    enum.set_defaults(handler=_run_enum)

    macro = subparsers.add_parser("macro", help="recover one exact global TDR macro value")
    macro.add_argument("--image", required=True, type=Path)
    macro.add_argument("--metalib", required=True)
    macro.add_argument("--macro", required=True, dest="macro_name")
    macro.add_argument("--output", required=True, type=Path)
    macro.set_defaults(handler=_run_macro)

    struct_parser = subparsers.add_parser("struct", help="recover one TDR struct and its field types")
    struct_parser.add_argument("--image", required=True, type=Path)
    struct_parser.add_argument("--metalib", required=True)
    struct_parser.add_argument("--struct", required=True, dest="struct_name")
    struct_parser.add_argument("--output", required=True, type=Path)
    struct_parser.set_defaults(handler=_run_struct)

    union_parser = subparsers.add_parser("union", help="recover one TDR union and its selector mapping")
    union_parser.add_argument("--image", required=True, type=Path)
    union_parser.add_argument("--metalib", required=True)
    union_parser.add_argument("--union", required=True, dest="union_name")
    union_parser.add_argument("--output", required=True, type=Path)
    union_parser.set_defaults(handler=_run_union)

    extract_tdr = subparsers.add_parser("extract-tdr", help="copy one exact embedded TDR block")
    extract_tdr.add_argument("--image", required=True, type=Path)
    extract_tdr.add_argument("--metalib", required=True)
    extract_tdr.add_argument("--output", required=True, type=Path)
    extract_tdr.set_defaults(handler=_run_extract_tdr)

    inspect_dump = subparsers.add_parser(
        "inspect-dump",
        help="extract bounded x86 minidump metadata and stack address candidates",
    )
    inspect_dump.add_argument("--dump", required=True, type=Path)
    inspect_dump.add_argument("--output", required=True, type=Path)
    inspect_dump.set_defaults(handler=_run_inspect_dump)

    decode_capture = subparsers.add_parser(
        "decode-capture",
        help="validate and decode a bounded loopback method-3 uplink capture",
    )
    decode_capture.add_argument("--capture", required=True, type=Path)
    decode_capture.add_argument("--payload", required=True, type=Path)
    decode_capture.add_argument("--output", required=True, type=Path)
    decode_capture.add_argument("--session-key-hex", default="00" * 16)
    decode_capture.add_argument("--max-frame-size", type=int, default=16 * 1024 * 1024)
    decode_capture.set_defaults(handler=_run_decode_capture)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        return arguments.handler(arguments)
    except (M3AnalysisError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
