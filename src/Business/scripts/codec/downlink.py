"""Strict framing for the verified server-to-client TPDU commands."""

from __future__ import annotations

from .protocol import (
    TPDU_COMMAND_CHANGE_SESSION_KEY,
    TPDU_COMMAND_PLAIN,
    TpduFrame,
    decode_plain_tpdu_downlink,
    decode_tpdu_base,
    decode_tpdu_change_session_key_extension,
    decode_tpdu_frame,
    decode_tpdu_ident_extension,
    decode_tpdu_mibao_extension,
    decode_tpdu_queue_info_extension,
    decode_tpdu_stop_extension,
    decode_tpdu_syn_extension,
    encode_tpdu_frame,
)

TPDU_COMMAND_QUEUE_INFO = 0x02
TPDU_COMMAND_IDENT = 0x04
TPDU_COMMAND_STOP = 0x07
TPDU_COMMAND_SYN = 0x08
TPDU_COMMAND_MBA_QUERY_RESPONSE = 0x0A
TPDU_COMMAND_MBA_VERIFY_RESPONSE = 0x0C

_TPDU_BASE_SIZE = 12
_SERVER_TO_CLIENT_COMMANDS = frozenset(
    {
        TPDU_COMMAND_CHANGE_SESSION_KEY,
        TPDU_COMMAND_QUEUE_INFO,
        TPDU_COMMAND_IDENT,
        TPDU_COMMAND_PLAIN,
        TPDU_COMMAND_STOP,
        TPDU_COMMAND_SYN,
        TPDU_COMMAND_MBA_QUERY_RESPONSE,
        TPDU_COMMAND_MBA_VERIFY_RESPONSE,
    }
)


def _validate_extension(frame: TpduFrame) -> None:
    command = frame.base.command
    if command == TPDU_COMMAND_CHANGE_SESSION_KEY:
        decode_tpdu_change_session_key_extension(frame.extension)
    elif command == TPDU_COMMAND_QUEUE_INFO:
        decode_tpdu_queue_info_extension(frame.extension)
    elif command == TPDU_COMMAND_IDENT:
        decode_tpdu_ident_extension(frame.extension)
    elif command == TPDU_COMMAND_PLAIN:
        if frame.extension:
            raise ValueError("plain TPDU downlink does not have an extension")
    elif command == TPDU_COMMAND_STOP:
        decode_tpdu_stop_extension(frame.extension)
    elif command == TPDU_COMMAND_SYN:
        decode_tpdu_syn_extension(frame.extension)
    elif command in (TPDU_COMMAND_MBA_QUERY_RESPONSE, TPDU_COMMAND_MBA_VERIFY_RESPONSE):
        decode_tpdu_mibao_extension(frame.extension)
    else:
        raise ValueError(
            f"TPDU downlink expected a server-to-client command, got 0x{command:02X}"
        )


def decode_tpdu_downlink_frame(data: bytes) -> TpduFrame:
    """Decode one capture-bounded S-to-C frame and validate its selected extension."""

    frame = decode_tpdu_frame(data)
    if frame.base.command not in _SERVER_TO_CLIENT_COMMANDS:
        raise ValueError(
            "TPDU downlink expected a server-to-client command, "
            f"got 0x{frame.base.command:02X}"
        )
    _validate_extension(frame)
    if frame.base.command == TPDU_COMMAND_PLAIN:
        decode_plain_tpdu_downlink(data)
    return frame


def encode_tpdu_downlink_frame(
    *,
    command: int,
    extension: bytes = b"",
    body: bytes = b"",
    enc_header_length: int = 0,
) -> bytes:
    """Encode one S-to-C frame while rejecting direction or extension mismatches."""

    data = encode_tpdu_frame(
        command=command,
        extension=extension,
        body=body,
        enc_header_length=enc_header_length,
    )
    decode_tpdu_downlink_frame(data)
    return data


def split_tpdu_downlink_stream(
    data: bytes,
    *,
    max_frame_size: int = 16 * 1024 * 1024,
) -> tuple[tuple[bytes, ...], bytes]:
    """Split complete S-to-C frames and retain one incomplete tail."""

    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    if max_frame_size < _TPDU_BASE_SIZE:
        raise ValueError(f"max_frame_size must be at least {_TPDU_BASE_SIZE}")

    frames: list[bytes] = []
    offset = 0
    while len(data) - offset >= _TPDU_BASE_SIZE:
        base = decode_tpdu_base(data[offset:offset + _TPDU_BASE_SIZE])
        if base.command not in _SERVER_TO_CLIENT_COMMANDS:
            raise ValueError(
                "TPDU downlink expected a server-to-client command, "
                f"got 0x{base.command:02X}"
            )
        if base.header_length < _TPDU_BASE_SIZE:
            raise ValueError(f"TPDU frame header_length must be at least {_TPDU_BASE_SIZE}")
        frame_size = base.header_length + base.body_length
        if frame_size > max_frame_size:
            raise ValueError(
                f"TPDU downlink frame exceeds maximum frame size {max_frame_size}: "
                f"{frame_size} bytes"
            )
        if len(data) - offset < frame_size:
            break
        frame = data[offset:offset + frame_size]
        decode_tpdu_downlink_frame(frame)
        frames.append(frame)
        offset += frame_size

    return tuple(frames), data[offset:]
