"""Deterministic decoding for bounded M3 loopback capture receipts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from .method3 import METHOD3_IV, decode_method3_uplink_frame
from .protocol import TPDU_COMMAND_NONE, decode_tpdu_base
from .tpdu_stream import split_tpdu_uplink_stream

DEFAULT_MAX_FRAME_SIZE = 16 * 1024 * 1024


def _require_int(value: object, *, field: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"capture {field} must be an integer >= {minimum}")
    return value


def _require_mapping(value: object, *, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"capture {field} must be an object")
    return value


def _capture_object(capture_data: bytes) -> Mapping[str, object]:
    if not isinstance(capture_data, bytes):
        raise TypeError("capture_data must be bytes")
    try:
        value = json.loads(capture_data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("capture receipt must be valid UTF-8 JSON") from error
    return _require_mapping(value, field="root")


def _frame_record(
    frame: bytes,
    *,
    connection_index: int,
    frame_index: int,
    session_key: bytes,
) -> dict[str, object]:
    base = decode_tpdu_base(frame[:12])
    record: dict[str, object] = {
        "connectionIndex": connection_index,
        "frameIndex": frame_index,
        "length": len(frame),
        "command": base.command,
        "wireSha256": hashlib.sha256(frame).hexdigest(),
    }
    if base.command == TPDU_COMMAND_NONE:
        message = decode_method3_uplink_frame(frame, session_key)
        record.update(
            {
                "sequence": message.sequence,
                "commandId": message.command_id,
                "commandIdHex": f"0x{message.command_id:04X}",
                "serverTimeMs": message.server_time_ms,
                "bodyHex": message.body.hex(),
            }
        )
        if len(message.body) >= 2:
            selector = int.from_bytes(message.body[:2], "big")
            record.update(
                {
                    "selector": selector,
                    "selectorHex": f"0x{selector:04X}",
                }
            )
    return record


def build_method3_capture_manifest(
    capture_data: bytes,
    payload: bytes,
    *,
    session_key: bytes = bytes(16),
    max_frame_size: int = DEFAULT_MAX_FRAME_SIZE,
) -> dict[str, object]:
    """Validate a listener receipt and decode its per-connection method-3 uplink."""

    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    if not isinstance(session_key, bytes) or len(session_key) != 16:
        raise ValueError("session_key must contain exactly 16 bytes")
    if max_frame_size < 12:
        raise ValueError("max_frame_size must be at least 12")

    capture = _capture_object(capture_data)
    if capture.get("schemaVersion") != 1:
        raise ValueError("capture schemaVersion must be 1")
    declared_length = _require_int(capture.get("payloadLength"), field="payloadLength")
    if declared_length != len(payload):
        raise ValueError(
            f"capture payloadLength is {declared_length}, but payload contains {len(payload)} bytes"
        )
    payload_sha256 = hashlib.sha256(payload).hexdigest()
    if capture.get("payloadSha256") != payload_sha256:
        raise ValueError("capture payloadSha256 does not match the selected payload")

    sessions_value = capture.get("sessions")
    if not isinstance(sessions_value, list) or not sessions_value:
        raise ValueError("capture sessions must be a non-empty array")

    connections: list[dict[str, object]] = []
    expected_offset = 0
    for expected_index, session_value in enumerate(sessions_value):
        session = _require_mapping(session_value, field=f"sessions[{expected_index}]")
        connection_index = _require_int(
            session.get("index"),
            field=f"sessions[{expected_index}].index",
        )
        if connection_index != expected_index:
            raise ValueError(
                f"capture sessions must use consecutive indices; expected {expected_index}, "
                f"got {connection_index}"
            )
        payload_offset = _require_int(
            session.get("payloadOffset"),
            field=f"sessions[{expected_index}].payloadOffset",
        )
        payload_length = _require_int(
            session.get("payloadLength"),
            field=f"sessions[{expected_index}].payloadLength",
        )
        if payload_offset != expected_offset:
            raise ValueError(
                f"capture sessions must cover payload contiguously; expected offset "
                f"{expected_offset}, got {payload_offset}"
            )
        payload_end = payload_offset + payload_length
        if payload_end > len(payload):
            raise ValueError(
                f"capture sessions[{expected_index}] ends past payload length {len(payload)}"
            )
        stream = payload[payload_offset:payload_end]
        frames, remainder = split_tpdu_uplink_stream(stream, max_frame_size=max_frame_size)
        connections.append(
            {
                "connectionIndex": connection_index,
                "payloadOffset": payload_offset,
                "streamLength": payload_length,
                "streamSha256": hashlib.sha256(stream).hexdigest(),
                "frameCount": len(frames),
                "remainderLength": len(remainder),
                "remainderSha256": hashlib.sha256(remainder).hexdigest() if remainder else None,
                "frames": [
                    _frame_record(
                        frame,
                        connection_index=connection_index,
                        frame_index=frame_index,
                        session_key=session_key,
                    )
                    for frame_index, frame in enumerate(frames)
                ],
            }
        )
        expected_offset = payload_end
    if expected_offset != len(payload):
        raise ValueError(
            f"capture sessions cover {expected_offset} bytes, but payload contains {len(payload)} bytes"
        )

    return {
        "schemaVersion": 1,
        "capture": {
            "size": len(capture_data),
            "sha256": hashlib.sha256(capture_data).hexdigest(),
        },
        "payload": {
            "size": len(payload),
            "sha256": payload_sha256,
        },
        "crypto": {
            "method": 3,
            "sessionKeySha256": hashlib.sha256(session_key).hexdigest(),
            "ivHex": METHOD3_IV.hex(),
        },
        "connections": connections,
    }
