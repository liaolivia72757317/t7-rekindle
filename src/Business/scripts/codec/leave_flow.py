"""Leave-room codecs: SYNC_LOGOUT, instance leave/finish, USER_GROUP quit."""

from __future__ import annotations

import struct

SYNC_LOGIN_COMMAND = 0x002C
SYNC_LOGOUT_REQUEST = 0x0002
SYNC_LOGOUT_RESPONSE = 0x0003
INSTANCE_COMMAND = 0x000A
INSTANCE_LEAVE_INSTANCE = 0x0003
INSTANCE_FINISH_GAME = 0x0069
FINISH_GAME_QUALIFYING_LIST_COUNT = 4
FINISH_GAME_BODY_SIZE = 118
USER_GROUP_COMMAND = 0x0037
USER_GROUP_QUIT_REQUEST = 0x0001
USER_GROUP_QUIT_RESPONSE = 0x0002
USER_GROUP_TYPE_END_SETTLE_ALL_ACTOR = 3
USER_GROUP_QUIT_REQUEST_SIZE = 14
USER_GROUP_QUIT_RESPONSE_SIZE = 18

_REQUEST = struct.Struct(">Hb")
_RESPONSE = struct.Struct(">HQiB")
_LEAVE_INSTANCE = struct.Struct(">HQQQ")
_FINISH_GAME = struct.Struct(">HQQi")
_QUALIFYING_LIST = struct.Struct(">i5I")
_EMPTY_QUALIFYING_LIST = _QUALIFYING_LIST.pack(0, 0, 0, 0, 0, 0)
_QUIT_REQUEST = struct.Struct(">HQi")
_QUIT_RESPONSE = struct.Struct(">HQii")


def decode_sync_logout_request(body: bytes) -> int:
    if not isinstance(body, bytes):
        raise TypeError("body must be bytes")
    if len(body) != _REQUEST.size:
        raise ValueError(
            f"sync logout request must contain exactly {_REQUEST.size} bytes, got {len(body)}"
        )
    selector, back_data = _REQUEST.unpack(body)
    if selector != SYNC_LOGOUT_REQUEST:
        raise ValueError(
            f"sync logout request expected selector 0x{SYNC_LOGOUT_REQUEST:04X}, "
            f"got 0x{selector:04X}"
        )
    return back_data


def encode_sync_logout_response(
    *,
    server_time_ms: int,
    reason: int = 0,
    back_data: int = 0,
) -> bytes:
    if not isinstance(server_time_ms, int) or isinstance(server_time_ms, bool):
        raise TypeError("server_time_ms must be int")
    if not isinstance(reason, int) or isinstance(reason, bool):
        raise TypeError("reason must be int")
    if not isinstance(back_data, int) or isinstance(back_data, bool):
        raise TypeError("back_data must be int")
    if server_time_ms < 0 or server_time_ms > 0xFFFFFFFFFFFFFFFF:
        raise ValueError("server_time_ms is outside uint64")
    if reason < -0x80000000 or reason > 0x7FFFFFFF:
        raise ValueError("reason is outside int32")
    if back_data < 0 or back_data > 0xFF:
        raise ValueError("back_data is outside uint8")
    return _RESPONSE.pack(SYNC_LOGOUT_RESPONSE, server_time_ms, reason, back_data)


def encode_instance_leave_instance(
    *,
    server_time_ms: int,
    rid: int = 1,
    inst_mid: int = 1,
) -> bytes:
    for name, value in (
        ("server_time_ms", server_time_ms),
        ("rid", rid),
        ("inst_mid", inst_mid),
    ):
        if not isinstance(value, int) or isinstance(value, bool):
            raise TypeError(f"{name} must be int")
        if value < 0 or value > 0xFFFFFFFFFFFFFFFF:
            raise ValueError(f"{name} is outside uint64")
    return _LEAVE_INSTANCE.pack(
        INSTANCE_LEAVE_INSTANCE,
        server_time_ms,
        rid,
        inst_mid,
    )


def encode_si_finish_game(
    *,
    server_time_ms: int,
    inst_mid: int = 1,
    game_result: int = 0,
    local_user_id: int | None = None,
) -> bytes:
    if not isinstance(server_time_ms, int) or isinstance(server_time_ms, bool):
        raise TypeError("server_time_ms must be int")
    if not isinstance(inst_mid, int) or isinstance(inst_mid, bool):
        raise TypeError("inst_mid must be int")
    if not isinstance(game_result, int) or isinstance(game_result, bool):
        raise TypeError("game_result must be int")
    if local_user_id is not None and (
        not isinstance(local_user_id, int) or isinstance(local_user_id, bool)
    ):
        raise TypeError("local_user_id must be int")
    if server_time_ms < 0 or server_time_ms > 0xFFFFFFFFFFFFFFFF:
        raise ValueError("server_time_ms is outside uint64")
    if inst_mid < 0 or inst_mid > 0xFFFFFFFFFFFFFFFF:
        raise ValueError("inst_mid is outside uint64")
    if game_result < -0x80000000 or game_result > 0x7FFFFFFF:
        raise ValueError("game_result is outside int32")
    if local_user_id is not None and (
        local_user_id < 0 or local_user_id > 0xFFFFFFFF
    ):
        raise ValueError("local_user_id is outside uint32")
    first_camp = (
        _QUALIFYING_LIST.pack(1, local_user_id, 0, 0, 0, 0)
        if local_user_id is not None
        else _EMPTY_QUALIFYING_LIST
    )
    body = _FINISH_GAME.pack(
        INSTANCE_FINISH_GAME,
        server_time_ms,
        inst_mid,
        game_result,
    ) + first_camp + (
        _EMPTY_QUALIFYING_LIST * (FINISH_GAME_QUALIFYING_LIST_COUNT - 1)
    )
    if len(body) != FINISH_GAME_BODY_SIZE:
        raise ValueError(
            f"si finish game must contain exactly {FINISH_GAME_BODY_SIZE} bytes, "
            f"got {len(body)}"
        )
    return body


def decode_user_group_quit_request(body: bytes) -> tuple[int, int]:
    if not isinstance(body, bytes):
        raise TypeError("body must be bytes")
    if len(body) != USER_GROUP_QUIT_REQUEST_SIZE:
        raise ValueError(
            f"user group quit request must contain exactly "
            f"{USER_GROUP_QUIT_REQUEST_SIZE} bytes, got {len(body)}"
        )
    selector, guid, group_type = _QUIT_REQUEST.unpack(body)
    if selector != USER_GROUP_QUIT_REQUEST:
        raise ValueError(
            f"user group quit request expected selector "
            f"0x{USER_GROUP_QUIT_REQUEST:04X}, got 0x{selector:04X}"
        )
    return guid, group_type


def encode_user_group_quit_response(
    *,
    user_group_guid: int,
    user_group_type: int,
    result: int = 0,
) -> bytes:
    for name, value in (
        ("user_group_guid", user_group_guid),
        ("user_group_type", user_group_type),
        ("result", result),
    ):
        if not isinstance(value, int) or isinstance(value, bool):
            raise TypeError(f"{name} must be int")
    if user_group_guid < 0 or user_group_guid > 0xFFFFFFFFFFFFFFFF:
        raise ValueError("user_group_guid is outside uint64")
    if user_group_type < -0x80000000 or user_group_type > 0x7FFFFFFF:
        raise ValueError("user_group_type is outside int32")
    if result < -0x80000000 or result > 0x7FFFFFFF:
        raise ValueError("result is outside int32")
    body = _QUIT_RESPONSE.pack(
        USER_GROUP_QUIT_RESPONSE,
        user_group_guid,
        user_group_type,
        result,
    )
    if len(body) != USER_GROUP_QUIT_RESPONSE_SIZE:
        raise ValueError(
            f"user group quit response must contain exactly "
            f"{USER_GROUP_QUIT_RESPONSE_SIZE} bytes, got {len(body)}"
        )
    return body
