"""Minimal fixed TDR packets proven for the M3 login path."""

from __future__ import annotations

import struct
from dataclasses import dataclass

VERSION_CHECK_REQUEST = 0x64
VERSION_CHECK_RESPONSE = 0x65
SYNC_LOGIN_RESPONSE = 0x01
LOGIN_RESPONSE = 0x02
LOGIN_RESPONSE_HAS_USER = 0x01
TPDU_MAGIC = 0x55
TPDU_VERSION = 0x0E
TPDU_COMMAND_NONE = 0x00
TPDU_COMMAND_CHANGE_SESSION_KEY = 0x01
TPDU_COMMAND_AUTH = 0x03
TPDU_COMMAND_PLAIN = 0x05
TPDU_COMMAND_RELAY = 0x06
TPDU_COMMAND_SYNACK = 0x09
TPDU_COMMAND_MBA_VERIFY_REQUEST = 0x0B
TPDU_COMMAND_CLOSE = 0x0D
TPDU_AUTH_NONE = 0
TPDU_AUTH_QQ_V1 = 1
TPDU_AUTH_QQ_V2 = 2
TPDU_AUTH_QQ_UNIFIED = 3
APPLICATION_PAYLOAD_RAW = 0

_VERSION_REQUEST = struct.Struct(">Hiiii")
_VERSION_RESPONSE = struct.Struct(">Hi")
_SYNC_LOGIN_RESPONSE = struct.Struct(">HIQiQ")
_SH_PACKAGE_HEADER = struct.Struct(">HQ")
_TPDU_BASE = struct.Struct(">BBBBii")
_UINT8 = struct.Struct(">B")
_UINT16 = struct.Struct(">H")
_UINT32 = struct.Struct(">I")
_INT16 = struct.Struct(">h")
_INT32 = struct.Struct(">i")
_INT8 = struct.Struct(">b")

_QUEST_DATA_ZERO_SIZE = 144
_USER_BASIC_INFO_SIZE = 296
_PVE_DATA_ZERO_SIZE = 2
_HERO_PRODUCE_DATA_ZERO_SIZE = 8


@dataclass(frozen=True, slots=True)
class VersionInfo:
    major: int
    minor: int
    revision: int
    build: int


@dataclass(frozen=True, slots=True)
class SyncLoginResponse:
    user_id: int
    mid: int
    is_reconnect: int
    fight_room_id: int


@dataclass(frozen=True, slots=True)
class MinimalLoginIdentity:
    user_id: int
    user_name: bytes
    user_image_id: int = 0
    gm_privilege: int = 0
    level: int = 1
    url: bytes = b""


@dataclass(frozen=True, slots=True)
class TpduBase:
    command: int
    enc_header_length: int
    header_length: int
    body_length: int


@dataclass(frozen=True, slots=True)
class TpduFrame:
    base: TpduBase
    extension: bytes
    body: bytes


@dataclass(frozen=True, slots=True)
class TpduQueueInfo:
    position: int
    maximum: int
    wait_seconds: int


@dataclass(frozen=True, slots=True)
class TpduQqAuthInfo:
    uin: int
    signature: bytes
    secondary_signature: bytes


@dataclass(frozen=True, slots=True)
class TpduQqUnifiedAuthInfo:
    uin: int
    signature_info: bytes


@dataclass(frozen=True, slots=True)
class TpduAuthInfo:
    enc_method: int
    service_id: int
    auth_type: int
    auth_data: TpduQqAuthInfo | TpduQqUnifiedAuthInfo | None


@dataclass(frozen=True, slots=True)
class TpduRelayInfo:
    enc_method: int
    relay_type: int
    old_position: int
    encrypted_identity: bytes


def _pack(packet: struct.Struct, values: tuple[int, ...], field_names: tuple[str, ...]) -> bytes:
    try:
        return packet.pack(*values)
    except struct.error as error:
        failing_fields = ", ".join(f"{name}={value}" for name, value in zip(field_names, values, strict=True))
        raise ValueError(f"packet field is outside its wire range: {failing_fields}") from error


def _unpack_exact(packet: struct.Struct, data: bytes, *, packet_name: str) -> tuple[int, ...]:
    if len(data) != packet.size:
        raise ValueError(f"{packet_name} must contain exactly {packet.size} bytes, got {len(data)}")
    return packet.unpack(data)


def _require_selector(actual: int, expected: int, *, packet_name: str) -> None:
    if actual != expected:
        raise ValueError(f"{packet_name} expected selector 0x{expected:04X}, got 0x{actual:04X}")


def _encode_referred_bytes(
    value: bytes,
    *,
    maximum_size: int,
    length_packet: struct.Struct,
    field_name: str,
) -> bytes:
    if not isinstance(value, bytes):
        raise TypeError(f"{field_name} must be bytes")
    if len(value) > maximum_size:
        raise ValueError(f"{field_name} exceeds its {maximum_size}-byte TDR storage")
    return _pack(length_packet, (len(value),), (f"{field_name} length",)) + value


def _decode_referred_bytes(
    data: bytes,
    *,
    maximum_size: int,
    length_packet: struct.Struct,
    field_name: str,
) -> bytes:
    payload, offset = _decode_referred_bytes_at(
        data,
        0,
        maximum_size=maximum_size,
        length_packet=length_packet,
        field_name=field_name,
    )
    if offset != len(data):
        raise ValueError(
            f"{field_name} must contain exactly {len(payload)} bytes after its TDR length, "
            f"got {len(data) - length_packet.size}"
        )
    return payload


def _decode_referred_bytes_at(
    data: bytes,
    offset: int,
    *,
    maximum_size: int,
    length_packet: struct.Struct,
    field_name: str,
) -> tuple[bytes, int]:
    if offset < 0 or offset + length_packet.size > len(data):
        raise ValueError(f"{field_name} is missing its {length_packet.size}-byte TDR length")
    length = length_packet.unpack_from(data, offset)[0]
    if length < 0 or length > maximum_size:
        raise ValueError(f"{field_name} has invalid TDR length {length}")
    start = offset + length_packet.size
    end = start + length
    if end > len(data):
        raise ValueError(
            f"{field_name} must contain exactly {length} bytes after its TDR length, "
            f"got {len(data) - start}"
        )
    return data[start:end], end


def _encode_tdr_string(value: bytes, *, maximum_size: int, field_name: str) -> bytes:
    if not isinstance(value, bytes):
        raise TypeError(f"{field_name} must be bytes")
    if b"\0" in value:
        raise ValueError(f"{field_name} contains an embedded NUL")
    terminated = value + b"\0"
    if len(terminated) > maximum_size:
        raise ValueError(f"{field_name} exceeds its {maximum_size}-byte TDR storage")
    return _UINT32.pack(len(terminated)) + terminated


def _decode_tdr_string(data: bytes, offset: int, *, maximum_size: int, field_name: str) -> tuple[bytes, int]:
    if offset + _UINT32.size > len(data):
        raise ValueError(f"{field_name} is missing its 4-byte TDR length")
    length = _UINT32.unpack_from(data, offset)[0]
    start = offset + _UINT32.size
    end = start + length
    if length < 1 or length > maximum_size or end > len(data):
        raise ValueError(f"{field_name} has invalid TDR length {length}")
    terminated = data[start:end]
    if terminated[-1] != 0 or b"\0" in terminated[:-1]:
        raise ValueError(f"{field_name} is not one NUL-terminated TDR string")
    return terminated[:-1], end


def encode_version_check_request(version: VersionInfo) -> bytes:
    return _pack(
        _VERSION_REQUEST,
        (VERSION_CHECK_REQUEST, version.major, version.minor, version.revision, version.build),
        ("selector", "major", "minor", "revision", "build"),
    )


def decode_version_check_request(data: bytes) -> VersionInfo:
    selector, major, minor, revision, build = _unpack_exact(
        _VERSION_REQUEST,
        data,
        packet_name="version-check request",
    )
    _require_selector(selector, VERSION_CHECK_REQUEST, packet_name="version-check request")
    return VersionInfo(major=major, minor=minor, revision=revision, build=build)


def encode_version_check_response(result: int) -> bytes:
    return _pack(
        _VERSION_RESPONSE,
        (VERSION_CHECK_RESPONSE, result),
        ("selector", "result"),
    )


def decode_version_check_response(data: bytes) -> int:
    selector, result = _unpack_exact(
        _VERSION_RESPONSE,
        data,
        packet_name="version-check response",
    )
    _require_selector(selector, VERSION_CHECK_RESPONSE, packet_name="version-check response")
    return result


def encode_sh_package(command_id: int, server_time_ms: int, body: bytes) -> bytes:
    """Encode the top-level SHPKG_CS header followed by its selected body."""

    if not isinstance(body, bytes):
        raise TypeError("body must be bytes")
    header = _pack(
        _SH_PACKAGE_HEADER,
        (command_id, server_time_ms),
        ("command_id", "server_time_ms"),
    )
    return header + body


def decode_sh_package(data: bytes) -> tuple[int, int, bytes]:
    """Decode the fixed SHPKG_CS header and retain the selected body bytes."""

    if len(data) < _SH_PACKAGE_HEADER.size:
        raise ValueError(
            f"SH package must contain at least {_SH_PACKAGE_HEADER.size} bytes, got {len(data)}"
        )
    command_id, server_time_ms = _SH_PACKAGE_HEADER.unpack_from(data)
    return command_id, server_time_ms, data[_SH_PACKAGE_HEADER.size :]


def encode_raw_application_payload(payload: bytes) -> bytes:
    """Prefix an uncompressed game application payload with its verified marker."""

    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    return bytes((APPLICATION_PAYLOAD_RAW,)) + payload


def decode_raw_application_payload(data: bytes) -> bytes:
    """Decode only the dynamically verified uncompressed application form."""

    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    if not data:
        raise ValueError("raw application payload is missing its marker")
    if data[0] != APPLICATION_PAYLOAD_RAW:
        raise ValueError(
            f"raw application payload expected marker 0x{APPLICATION_PAYLOAD_RAW:02X}, "
            f"got 0x{data[0]:02X}"
        )
    return data[1:]


def encode_sync_login_response(value: SyncLoginResponse) -> bytes:
    return _pack(
        _SYNC_LOGIN_RESPONSE,
        (
            SYNC_LOGIN_RESPONSE,
            value.user_id,
            value.mid,
            value.is_reconnect,
            value.fight_room_id,
        ),
        ("selector", "user_id", "mid", "is_reconnect", "fight_room_id"),
    )


def decode_sync_login_response(data: bytes) -> SyncLoginResponse:
    selector, user_id, mid, is_reconnect, fight_room_id = _unpack_exact(
        _SYNC_LOGIN_RESPONSE,
        data,
        packet_name="sync-login response",
    )
    _require_selector(selector, SYNC_LOGIN_RESPONSE, packet_name="sync-login response")
    return SyncLoginResponse(
        user_id=user_id,
        mid=mid,
        is_reconnect=is_reconnect,
        fight_room_id=fight_room_id,
    )


def encode_minimal_login_success(identity: MinimalLoginIdentity) -> bytes:
    """Encode the zero-filled login-success candidate proven by current metadata."""

    header = _pack(
        struct.Struct(">HiI"),
        (LOGIN_RESPONSE, LOGIN_RESPONSE_HAS_USER, identity.user_id),
        ("selector", "login command", "user_id"),
    )
    image_and_privilege = _pack(
        struct.Struct(">ib"),
        (identity.user_image_id, identity.gm_privilege),
        ("user_image_id", "gm_privilege"),
    )
    basic_prefix = _pack(
        struct.Struct(">ii"),
        (identity.user_image_id, identity.level),
        ("basic user_image_id", "level"),
    )
    return b"".join(
        (
            header,
            _encode_tdr_string(identity.user_name, maximum_size=32, field_name="user_name"),
            image_and_privilege,
            bytes(_QUEST_DATA_ZERO_SIZE),
            basic_prefix,
            bytes(_USER_BASIC_INFO_SIZE - len(basic_prefix)),
            bytes(_PVE_DATA_ZERO_SIZE + _HERO_PRODUCE_DATA_ZERO_SIZE),
            _encode_tdr_string(identity.url, maximum_size=512, field_name="url"),
        )
    )


def decode_minimal_login_success(data: bytes) -> MinimalLoginIdentity:
    """Decode only the zero-filled candidate shape emitted above."""

    fixed_header = struct.Struct(">HiI")
    if len(data) < fixed_header.size:
        raise ValueError("minimal login-success packet is truncated")
    selector, command, user_id = fixed_header.unpack_from(data)
    _require_selector(selector, LOGIN_RESPONSE, packet_name="login-success response")
    if command != LOGIN_RESPONSE_HAS_USER:
        raise ValueError(f"login-success response expected command {LOGIN_RESPONSE_HAS_USER}, got {command}")
    user_name, offset = _decode_tdr_string(data, fixed_header.size, maximum_size=32, field_name="user_name")
    fixed_identity = struct.Struct(">ib")
    if offset + fixed_identity.size > len(data):
        raise ValueError("minimal login-success identity is truncated")
    user_image_id, gm_privilege = fixed_identity.unpack_from(data, offset)
    offset += fixed_identity.size
    quest_end = offset + _QUEST_DATA_ZERO_SIZE
    if data[offset:quest_end] != bytes(_QUEST_DATA_ZERO_SIZE):
        raise ValueError("minimal login-success quest_data is not zero-filled")
    offset = quest_end
    basic_end = offset + _USER_BASIC_INFO_SIZE
    if basic_end > len(data):
        raise ValueError("minimal login-success user_basic_info is truncated")
    basic_image_id, level = struct.unpack_from(">ii", data, offset)
    if basic_image_id != user_image_id or data[offset + 8:basic_end] != bytes(_USER_BASIC_INFO_SIZE - 8):
        raise ValueError("minimal login-success user_basic_info is not the supported fixture")
    offset = basic_end
    zero_tail_size = _PVE_DATA_ZERO_SIZE + _HERO_PRODUCE_DATA_ZERO_SIZE
    if data[offset:offset + zero_tail_size] != bytes(zero_tail_size):
        raise ValueError("minimal login-success pve/hero data is not zero-filled")
    offset += zero_tail_size
    url, offset = _decode_tdr_string(data, offset, maximum_size=512, field_name="url")
    if offset != len(data):
        raise ValueError(f"minimal login-success packet has {len(data) - offset} trailing bytes")
    return MinimalLoginIdentity(user_id, user_name, user_image_id, gm_privilege, level, url)


def encode_tpdu_base(value: TpduBase) -> bytes:
    if value.header_length < 0 or value.body_length < 0:
        raise ValueError("TPDU header_length and body_length must be non-negative")
    return _pack(
        _TPDU_BASE,
        (
            TPDU_MAGIC,
            TPDU_VERSION,
            value.command,
            value.enc_header_length,
            value.header_length,
            value.body_length,
        ),
        ("magic", "version", "command", "enc_header_length", "header_length", "body_length"),
    )


def decode_tpdu_base(data: bytes) -> TpduBase:
    magic, version, command, enc_header_length, header_length, body_length = _unpack_exact(
        _TPDU_BASE,
        data,
        packet_name="TPDU base",
    )
    if magic != TPDU_MAGIC:
        raise ValueError(f"TPDU base expected magic 0x{TPDU_MAGIC:02X}, got 0x{magic:02X}")
    if version != TPDU_VERSION:
        raise ValueError(f"TPDU base expected version 0x{TPDU_VERSION:02X}, got 0x{version:02X}")
    if header_length < 0 or body_length < 0:
        raise ValueError("TPDU header_length and body_length must be non-negative")
    return TpduBase(command, enc_header_length, header_length, body_length)


def encode_tpdu_frame(
    *,
    command: int,
    extension: bytes = b"",
    body: bytes = b"",
    enc_header_length: int = 0,
) -> bytes:
    if not isinstance(extension, bytes):
        raise TypeError("extension must be bytes")
    if not isinstance(body, bytes):
        raise TypeError("body must be bytes")
    base = TpduBase(
        command=command,
        enc_header_length=enc_header_length,
        header_length=_TPDU_BASE.size + len(extension),
        body_length=len(body),
    )
    return encode_tpdu_base(base) + extension + body


def decode_tpdu_frame(data: bytes) -> TpduFrame:
    if len(data) < _TPDU_BASE.size:
        raise ValueError(f"TPDU frame must contain at least {_TPDU_BASE.size} bytes")
    base = decode_tpdu_base(data[:_TPDU_BASE.size])
    if base.header_length < _TPDU_BASE.size:
        raise ValueError(f"TPDU frame header_length must be at least {_TPDU_BASE.size}")
    expected_size = base.header_length + base.body_length
    if len(data) != expected_size:
        raise ValueError(f"TPDU frame must contain exactly {expected_size} bytes, got {len(data)}")
    return TpduFrame(
        base=base,
        extension=data[_TPDU_BASE.size:base.header_length],
        body=data[base.header_length:],
    )


def encode_unencrypted_tpdu_uplink_body(sequence: int, application_body: bytes) -> bytes:
    if not isinstance(application_body, bytes):
        raise TypeError("application_body must be bytes")
    return _pack(_UINT32, (sequence,), ("sequence",)) + application_body


def decode_unencrypted_tpdu_uplink_body(data: bytes) -> tuple[int, bytes]:
    if len(data) < _UINT32.size:
        raise ValueError("unencrypted TPDU uplink body is missing its 4-byte sequence")
    return _UINT32.unpack_from(data)[0], data[_UINT32.size:]


def encode_unencrypted_tpdu_uplink_frame(
    *,
    command: int,
    sequence: int,
    application_body: bytes,
    extension: bytes = b"",
) -> bytes:
    if not isinstance(extension, bytes):
        raise TypeError("extension must be bytes")
    body = encode_unencrypted_tpdu_uplink_body(sequence, application_body)
    base = TpduBase(
        command=command,
        enc_header_length=_UINT32.size,
        header_length=0,
        body_length=len(body),
    )
    return encode_tpdu_base(base) + extension + body


def decode_unencrypted_tpdu_uplink_frame(data: bytes) -> TpduFrame:
    if len(data) < _TPDU_BASE.size:
        raise ValueError(f"TPDU uplink frame must contain at least {_TPDU_BASE.size} bytes")
    base = decode_tpdu_base(data[:_TPDU_BASE.size])
    if base.enc_header_length != _UINT32.size:
        raise ValueError(f"unencrypted TPDU uplink frame requires EncHeadLen={_UINT32.size}")
    if base.header_length != 0:
        raise ValueError("unencrypted TPDU uplink frame requires HeadLen=0")
    if base.body_length < _UINT32.size:
        raise ValueError("unencrypted TPDU uplink frame body must contain its 4-byte sequence")
    actual_header_length = len(data) - base.body_length
    if actual_header_length < _TPDU_BASE.size:
        raise ValueError(
            f"TPDU uplink frame declares {base.body_length} body bytes but only "
            f"contains {len(data) - _TPDU_BASE.size} bytes after its base"
        )
    return TpduFrame(
        base=base,
        extension=data[_TPDU_BASE.size:actual_header_length],
        body=data[actual_header_length:],
    )


def encode_tpdu_auth_extension(value: TpduAuthInfo) -> bytes:
    prefix = _pack(
        struct.Struct(">iii"),
        (value.enc_method, value.service_id, value.auth_type),
        ("EncMethod", "ServiceID", "AuthType"),
    )
    if value.auth_type == TPDU_AUTH_NONE:
        if value.auth_data is not None:
            raise TypeError("TPDU AuthType 0 requires auth_data=None")
        return prefix
    if value.auth_type in (TPDU_AUTH_QQ_V1, TPDU_AUTH_QQ_V2):
        if not isinstance(value.auth_data, TpduQqAuthInfo):
            raise TypeError(f"TPDU AuthType {value.auth_type} requires TpduQqAuthInfo")
        return (
            prefix
            + _pack(_UINT32, (value.auth_data.uin,), ("Uin",))
            + _encode_referred_bytes(
                value.auth_data.signature,
                maximum_size=128,
                length_packet=_UINT8,
                field_name="TPDU AUTH signature",
            )
            + _encode_referred_bytes(
                value.auth_data.secondary_signature,
                maximum_size=64,
                length_packet=_UINT8,
                field_name="TPDU AUTH secondary_signature",
            )
        )
    if value.auth_type == TPDU_AUTH_QQ_UNIFIED:
        if not isinstance(value.auth_data, TpduQqUnifiedAuthInfo):
            raise TypeError("TPDU AuthType 3 requires TpduQqUnifiedAuthInfo")
        return (
            prefix
            + _pack(_UINT32, (value.auth_data.uin,), ("Uin",))
            + _encode_referred_bytes(
                value.auth_data.signature_info,
                maximum_size=255,
                length_packet=_UINT8,
                field_name="TPDU AUTH signature_info",
            )
        )
    raise ValueError(f"unsupported AuthType {value.auth_type}")


def decode_tpdu_auth_extension(data: bytes) -> TpduAuthInfo:
    prefix = struct.Struct(">iii")
    if len(data) < prefix.size:
        raise ValueError(f"TPDU AUTH extension must contain at least {prefix.size} bytes")
    enc_method, service_id, auth_type = prefix.unpack_from(data)
    offset = prefix.size
    auth_data: TpduQqAuthInfo | TpduQqUnifiedAuthInfo | None
    if auth_type == TPDU_AUTH_NONE:
        auth_data = None
    elif auth_type in (TPDU_AUTH_QQ_V1, TPDU_AUTH_QQ_V2):
        if offset + _UINT32.size > len(data):
            raise ValueError("TPDU AUTH QQ data is missing its 4-byte Uin")
        uin = _UINT32.unpack_from(data, offset)[0]
        offset += _UINT32.size
        signature, offset = _decode_referred_bytes_at(
            data,
            offset,
            maximum_size=128,
            length_packet=_UINT8,
            field_name="TPDU AUTH signature",
        )
        secondary_signature, offset = _decode_referred_bytes_at(
            data,
            offset,
            maximum_size=64,
            length_packet=_UINT8,
            field_name="TPDU AUTH secondary_signature",
        )
        auth_data = TpduQqAuthInfo(uin, signature, secondary_signature)
    elif auth_type == TPDU_AUTH_QQ_UNIFIED:
        if offset + _UINT32.size > len(data):
            raise ValueError("TPDU AUTH QQUNIFIED data is missing its 4-byte Uin")
        uin = _UINT32.unpack_from(data, offset)[0]
        offset += _UINT32.size
        signature_info, offset = _decode_referred_bytes_at(
            data,
            offset,
            maximum_size=255,
            length_packet=_UINT8,
            field_name="TPDU AUTH signature_info",
        )
        auth_data = TpduQqUnifiedAuthInfo(uin, signature_info)
    else:
        raise ValueError(f"unsupported AuthType {auth_type}")
    if offset != len(data):
        raise ValueError(f"TPDU AUTH extension has {len(data) - offset} trailing bytes")
    return TpduAuthInfo(enc_method, service_id, auth_type, auth_data)


def encode_tpdu_initial_auth_frame(value: TpduAuthInfo) -> bytes:
    extension = encode_tpdu_auth_extension(value)
    base = TpduBase(
        command=TPDU_COMMAND_AUTH,
        enc_header_length=0,
        header_length=_TPDU_BASE.size + len(extension),
        body_length=0,
    )
    return encode_tpdu_base(base) + extension


def decode_tpdu_initial_auth_frame(data: bytes) -> TpduAuthInfo:
    if len(data) < _TPDU_BASE.size:
        raise ValueError(f"TPDU initial AUTH frame must contain at least {_TPDU_BASE.size} bytes")
    base = decode_tpdu_base(data[:_TPDU_BASE.size])
    if base.command != TPDU_COMMAND_AUTH:
        raise ValueError(
            f"TPDU initial AUTH frame expected command 0x{TPDU_COMMAND_AUTH:02X}, "
            f"got 0x{base.command:02X}"
        )
    if base.enc_header_length != 0:
        raise ValueError("TPDU initial AUTH frame requires EncHeadLen=0")
    if base.header_length < _TPDU_BASE.size:
        raise ValueError(f"TPDU initial AUTH frame HeadLen must be at least {_TPDU_BASE.size}")
    if base.body_length != 0:
        raise ValueError("TPDU initial AUTH frame requires BodyLen=0")
    if len(data) != base.header_length:
        raise ValueError(
            f"TPDU initial AUTH frame must contain exactly {base.header_length} bytes, got {len(data)}"
        )
    return decode_tpdu_auth_extension(data[_TPDU_BASE.size:base.header_length])


def encode_tpdu_relay_extension(value: TpduRelayInfo) -> bytes:
    return (
        _pack(
            struct.Struct(">iii"),
            (value.enc_method, value.relay_type, value.old_position),
            ("EncMethod", "RelayType", "OldPos"),
        )
        + _encode_referred_bytes(
            value.encrypted_identity,
            maximum_size=64,
            length_packet=_INT32,
            field_name="TPDU RELAY encrypted_identity",
        )
    )


def decode_tpdu_relay_extension(data: bytes) -> TpduRelayInfo:
    prefix = struct.Struct(">iii")
    if len(data) < prefix.size:
        raise ValueError(f"TPDU RELAY extension must contain at least {prefix.size} bytes")
    enc_method, relay_type, old_position = prefix.unpack_from(data)
    encrypted_identity = _decode_referred_bytes(
        data[prefix.size:],
        maximum_size=64,
        length_packet=_INT32,
        field_name="TPDU RELAY encrypted_identity",
    )
    return TpduRelayInfo(enc_method, relay_type, old_position, encrypted_identity)


def encode_tpdu_mibao_extension(encrypted_buffer: bytes) -> bytes:
    return _encode_referred_bytes(
        encrypted_buffer, maximum_size=4096, length_packet=_UINT16, field_name="TPDU MiBao encrypted_buffer",
    )


def decode_tpdu_mibao_extension(data: bytes) -> bytes:
    return _decode_referred_bytes(
        data, maximum_size=4096, length_packet=_UINT16, field_name="TPDU MiBao extension",
    )


def encode_tpdu_syn_extension(encrypted_info: bytes) -> bytes:
    return _encode_referred_bytes(
        encrypted_info,
        maximum_size=128,
        length_packet=_UINT8,
        field_name="TPDU SYN encrypted_info",
    )


def decode_tpdu_syn_extension(data: bytes) -> bytes:
    return _decode_referred_bytes(
        data,
        maximum_size=128,
        length_packet=_UINT8,
        field_name="TPDU SYN extension",
    )


def encode_tpdu_synack_extension(encrypted_info: bytes) -> bytes:
    return _encode_referred_bytes(
        encrypted_info,
        maximum_size=128,
        length_packet=_UINT8,
        field_name="TPDU SYNACK encrypted_info",
    )


def decode_tpdu_synack_extension(data: bytes) -> bytes:
    return _decode_referred_bytes(
        data,
        maximum_size=128,
        length_packet=_UINT8,
        field_name="TPDU SYNACK extension",
    )


def encode_tpdu_ident_extension(encrypted_identity: bytes) -> bytes:
    return _encode_referred_bytes(
        encrypted_identity,
        maximum_size=64,
        length_packet=_INT32,
        field_name="TPDU IDENT encrypted_identity",
    )


def decode_tpdu_ident_extension(data: bytes) -> bytes:
    return _decode_referred_bytes(
        data,
        maximum_size=64,
        length_packet=_INT32,
        field_name="TPDU IDENT extension",
    )


def encode_tpdu_change_session_key_extension(key_type: int, encrypted_key: bytes) -> bytes:
    key = _encode_referred_bytes(
        encrypted_key,
        maximum_size=128,
        length_packet=_INT16,
        field_name="TPDU CHGSKEY encrypted_key",
    )
    return _pack(_INT16, (key_type,), ("TPDU CHGSKEY key_type",)) + key


def decode_tpdu_change_session_key_extension(data: bytes) -> tuple[int, bytes]:
    if len(data) < _INT16.size:
        raise ValueError("TPDU CHGSKEY extension is missing its 2-byte key type")
    key_type = _INT16.unpack_from(data)[0]
    encrypted_key = _decode_referred_bytes(
        data[_INT16.size:],
        maximum_size=128,
        length_packet=_INT16,
        field_name="TPDU CHGSKEY encrypted_key",
    )
    return key_type, encrypted_key


def encode_zero_length_change_session_key_downlink_candidate() -> bytes:
    """Build the shortest CHGSKEY frame for a connection configured with method 0."""

    extension = encode_tpdu_change_session_key_extension(0, b"")
    base = TpduBase(
        command=TPDU_COMMAND_CHANGE_SESSION_KEY,
        enc_header_length=0,
        header_length=_TPDU_BASE.size + len(extension),
        body_length=0,
    )
    return encode_tpdu_base(base) + extension


def decode_zero_length_change_session_key_downlink_candidate(data: bytes) -> None:
    expected_size = _TPDU_BASE.size + _INT16.size * 2
    if len(data) != expected_size:
        raise ValueError(f"zero-length TPDU CHGSKEY candidate must contain exactly {expected_size} bytes")
    base = decode_tpdu_base(data[:_TPDU_BASE.size])
    if base != TpduBase(TPDU_COMMAND_CHANGE_SESSION_KEY, 0, expected_size, 0):
        raise ValueError("zero-length TPDU CHGSKEY candidate has an unexpected base header")
    if decode_tpdu_change_session_key_extension(data[_TPDU_BASE.size:]) != (0, b""):
        raise ValueError("zero-length TPDU CHGSKEY candidate has an unexpected extension")


def encode_tpdu_queue_info_extension(value: TpduQueueInfo) -> bytes:
    return _pack(
        struct.Struct(">iii"),
        (value.position, value.maximum, value.wait_seconds),
        ("position", "maximum", "wait_seconds"),
    )


def decode_tpdu_queue_info_extension(data: bytes) -> TpduQueueInfo:
    position, maximum, wait_seconds = _unpack_exact(
        struct.Struct(">iii"),
        data,
        packet_name="TPDU QUEINFO extension",
    )
    return TpduQueueInfo(position, maximum, wait_seconds)


def encode_tpdu_stop_extension(reason: int) -> bytes:
    return _pack(_INT32, (reason,), ("TPDU STOP reason",))


def decode_tpdu_stop_extension(data: bytes) -> int:
    return _unpack_exact(_INT32, data, packet_name="TPDU STOP extension")[0]


def split_tpdu_uplink_stream(data: bytes, *, max_frame_size: int = 16 * 1024 * 1024) -> tuple[tuple[bytes, ...], bytes]:
    from .tpdu_stream import split_tpdu_uplink_stream as split_stream

    return split_stream(data, max_frame_size=max_frame_size)


def encode_plain_tpdu_downlink(application_body: bytes) -> bytes:
    if not isinstance(application_body, bytes):
        raise TypeError("application_body must be bytes")
    base = TpduBase(
        command=TPDU_COMMAND_PLAIN,
        enc_header_length=0,
        header_length=_TPDU_BASE.size,
        body_length=len(application_body),
    )
    return encode_tpdu_base(base) + application_body


def decode_plain_tpdu_downlink(data: bytes) -> bytes:
    if len(data) < _TPDU_BASE.size:
        raise ValueError(f"plain TPDU downlink must contain at least {_TPDU_BASE.size} bytes")
    base = decode_tpdu_base(data[:_TPDU_BASE.size])
    if base.command != TPDU_COMMAND_PLAIN:
        raise ValueError(f"plain TPDU downlink expected command 0x{TPDU_COMMAND_PLAIN:02X}, got 0x{base.command:02X}")
    if base.enc_header_length != 0 or base.header_length != _TPDU_BASE.size:
        raise ValueError("plain TPDU downlink requires EncHeadLen=0 and HeadLen=12")
    expected_size = base.header_length + base.body_length
    if len(data) != expected_size:
        raise ValueError(f"plain TPDU downlink must contain exactly {expected_size} bytes, got {len(data)}")
    return data[base.header_length:]
