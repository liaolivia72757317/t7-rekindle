"""Bounded framing for the verified client-to-server TPDU commands."""

from __future__ import annotations

import struct

from .protocol import (
    TPDU_AUTH_NONE,
    TPDU_AUTH_QQ_UNIFIED,
    TPDU_AUTH_QQ_V1,
    TPDU_AUTH_QQ_V2,
    TPDU_COMMAND_AUTH,
    TPDU_COMMAND_CLOSE,
    TPDU_COMMAND_MBA_VERIFY_REQUEST,
    TPDU_COMMAND_NONE,
    TPDU_COMMAND_RELAY,
    TPDU_COMMAND_SYNACK,
    TpduBase,
    decode_tpdu_base,
)

_TPDU_BASE_SIZE = 12
_UINT8_SIZE = 1
_UINT16 = struct.Struct(">H")
_UINT32_SIZE = 4
_INT32 = struct.Struct(">i")
_CLIENT_TO_SERVER_COMMANDS = frozenset(
    {
        TPDU_COMMAND_NONE,
        TPDU_COMMAND_AUTH,
        TPDU_COMMAND_RELAY,
        TPDU_COMMAND_SYNACK,
        TPDU_COMMAND_MBA_VERIFY_REQUEST,
        TPDU_COMMAND_CLOSE,
    }
)


def _auth_extension_wire_length(data: bytes, offset: int) -> int | None:
    prefix_size = 3 * _INT32.size
    available = len(data) - offset
    if available < prefix_size:
        return None
    auth_type = _INT32.unpack_from(data, offset + 2 * _INT32.size)[0]
    if auth_type == TPDU_AUTH_NONE:
        return prefix_size
    if auth_type in (TPDU_AUTH_QQ_V1, TPDU_AUTH_QQ_V2):
        signature_length_offset = offset + prefix_size + _UINT32_SIZE
        if len(data) <= signature_length_offset:
            return None
        signature_length = data[signature_length_offset]
        if signature_length > 128:
            raise ValueError(
                f"TPDU AUTH signature exceeds its 128-byte maximum: {signature_length} bytes"
            )
        secondary_length_offset = signature_length_offset + _UINT8_SIZE + signature_length
        if len(data) <= secondary_length_offset:
            return None
        secondary_length = data[secondary_length_offset]
        if secondary_length > 64:
            raise ValueError(
                "TPDU AUTH secondary_signature exceeds its 64-byte maximum: "
                f"{secondary_length} bytes"
            )
        return (
            prefix_size
            + _UINT32_SIZE
            + _UINT8_SIZE
            + signature_length
            + _UINT8_SIZE
            + secondary_length
        )
    if auth_type == TPDU_AUTH_QQ_UNIFIED:
        signature_length_offset = offset + prefix_size + _UINT32_SIZE
        if len(data) <= signature_length_offset:
            return None
        return prefix_size + _UINT32_SIZE + _UINT8_SIZE + data[signature_length_offset]
    raise ValueError(f"unsupported AuthType {auth_type}")


def _extension_wire_length(data: bytes, base: TpduBase, offset: int) -> int | None:
    if base.command in (TPDU_COMMAND_NONE, TPDU_COMMAND_CLOSE):
        return 0
    if base.command == TPDU_COMMAND_AUTH:
        return _auth_extension_wire_length(data, offset)
    if base.command == TPDU_COMMAND_RELAY:
        prefix_size = 3 * _INT32.size
        length_offset = offset + prefix_size
        if len(data) < length_offset + _INT32.size:
            return None
        encrypted_identity_length = _INT32.unpack_from(data, length_offset)[0]
        if encrypted_identity_length < 0 or encrypted_identity_length > 64:
            raise ValueError(
                "TPDU RELAY encrypted_identity length must be between 0 and 64 bytes, "
                f"got {encrypted_identity_length}"
            )
        return prefix_size + _INT32.size + encrypted_identity_length
    if base.command == TPDU_COMMAND_SYNACK:
        if len(data) <= offset:
            return None
        encrypted_info_length = data[offset]
        if encrypted_info_length > 128:
            raise ValueError(
                "TPDU SYNACK encrypted_info exceeds its 128-byte maximum: "
                f"{encrypted_info_length} bytes"
            )
        return _UINT8_SIZE + encrypted_info_length
    if base.command == TPDU_COMMAND_MBA_VERIFY_REQUEST:
        if len(data) < offset + _UINT16.size:
            return None
        mibao_length = _UINT16.unpack_from(data, offset)[0]
        if mibao_length > 4096:
            raise ValueError(
                f"TPDU MiBao encrypted_buffer exceeds its 4096-byte maximum: {mibao_length} bytes"
            )
        return _UINT16.size + mibao_length
    raise ValueError(f"TPDU uplink expected a client-to-server command, got 0x{base.command:02X}")


def split_tpdu_uplink_stream(
    data: bytes,
    *,
    max_frame_size: int,
) -> tuple[tuple[bytes, ...], bytes]:
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    if max_frame_size < _TPDU_BASE_SIZE:
        raise ValueError(f"max_frame_size must be at least {_TPDU_BASE_SIZE}")

    frames: list[bytes] = []
    offset = 0
    while len(data) - offset >= _TPDU_BASE_SIZE:
        base = decode_tpdu_base(data[offset:offset + _TPDU_BASE_SIZE])

        is_initial_auth = (
            base.command == TPDU_COMMAND_AUTH
            and base.enc_header_length == 0
            and base.body_length == 0
        )
        if is_initial_auth:
            if base.header_length < _TPDU_BASE_SIZE:
                raise ValueError(
                    f"initial client AUTH frame HeadLen must be at least {_TPDU_BASE_SIZE}"
                )
            frame_size = base.header_length
            if frame_size > max_frame_size:
                raise ValueError(
                    f"TPDU uplink frame exceeds maximum frame size {max_frame_size}: {frame_size} bytes"
                )
            if len(data) - offset < frame_size:
                break
            extension_length = _auth_extension_wire_length(
                data,
                offset + _TPDU_BASE_SIZE,
            )
            if extension_length is None:
                break
            declared_extension_length = base.header_length - _TPDU_BASE_SIZE
            if extension_length != declared_extension_length:
                raise ValueError(
                    "initial client AUTH frame extension length does not match HeadLen: "
                    f"declared {declared_extension_length}, encoded {extension_length}"
                )
            frames.append(data[offset:offset + frame_size])
            offset += frame_size
            continue

        if base.command not in _CLIENT_TO_SERVER_COMMANDS:
            raise ValueError(
                f"TPDU uplink expected a client-to-server command, got 0x{base.command:02X}"
            )
        if base.enc_header_length != _UINT32_SIZE:
            raise ValueError(f"client TPDU uplink frame requires EncHeadLen={_UINT32_SIZE}")
        if base.body_length < _UINT32_SIZE:
            raise ValueError("client TPDU uplink body must contain its 4-byte sequence")

        if base.header_length not in (0, _TPDU_BASE_SIZE):
            raise ValueError("client TPDU uplink frame requires HeadLen=0 or HeadLen=12")

        if base.header_length == _TPDU_BASE_SIZE:
            if base.command not in (TPDU_COMMAND_NONE, TPDU_COMMAND_CLOSE):
                raise ValueError(
                    "client encrypted TPDU frame with HeadLen=12 requires NONE or CLOSE command"
                )
            frame_size = _TPDU_BASE_SIZE + base.body_length
            if frame_size > max_frame_size:
                raise ValueError(
                    f"TPDU uplink frame exceeds maximum frame size {max_frame_size}: {frame_size} bytes"
                )
            if len(data) - offset < frame_size:
                break
            frames.append(data[offset:offset + frame_size])
            offset += frame_size
            continue

        minimum_frame_size = _TPDU_BASE_SIZE + base.body_length
        if minimum_frame_size > max_frame_size:
            raise ValueError(
                f"TPDU uplink frame exceeds maximum frame size {max_frame_size}: "
                f"at least {minimum_frame_size} bytes"
            )

        extension_length = _extension_wire_length(data, base, offset + _TPDU_BASE_SIZE)
        if extension_length is None:
            break
        frame_size = minimum_frame_size + extension_length
        if frame_size > max_frame_size:
            raise ValueError(
                f"TPDU uplink frame exceeds maximum frame size {max_frame_size}: {frame_size} bytes"
            )
        if len(data) - offset < frame_size:
            break
        frames.append(data[offset:offset + frame_size])
        offset += frame_size

    return tuple(frames), data[offset:]
