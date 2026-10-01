"""Minimal M4 room reconnect codec recovered from sh_proto_cs."""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .method3 import Method3UplinkMessage
from .protocol import (
    encode_plain_tpdu_downlink,
    encode_raw_application_payload,
    encode_sh_package,
)

ROOM_COMMAND = 0x001E
GET_RECONNECT_INFO_REQUEST = 0x000A
GET_RECONNECT_INFO_RESPONSE = 0x000B
CREATE_REQUEST = 0x0001
CREATE_RESPONSE = 0x0002
ENTER_REQUEST = 0x0003
ENTER_RESPONSE = 0x0004
ENTER_INSTANCE_NOTIFY = 0x0009

INSTANCE_COMMAND = 0x000A
INSTANCE_ENTER = 0x0002
INSTANCE_CHOOSE_HERO_REQUEST = 0x000B
INSTANCE_UPDATE_ACTOR_BASIC_INFO = 0x0010
INSTANCE_UPDATE = 0x0064
INSTANCE_UPDATE_GAME = 0x0065
INSTANCE_UPDATE_ROUND = 0x0066
INSTANCE_UPDATE_ROUND_STATE = 0x0067
INSTANCE_START_PATTERN_DATA_NTF = 0x0194
INSTANCE_UPDATE_SPAWN_AREA_STATE = 0x00C8
INSTANCE_START_PATTERN_CUSTOM = 0x00000002
INSTANCE_START_PATTERN_PRACTICE = 0x0000000E

ACTOR_COMMAND = 0x0036
ACTOR_UPDATE_STATE = 0x0001
ACTOR_CHOOSE_HERO_REQUEST = 0x0032
ACTOR_CHOOSE_HERO_RESPONSE = 0x0033
ACTOR_CHOOSE_HERO_MESSAGE = 0x0034
ACTOR_PLAY_REQUEST = 0x0064
ACTOR_PLAY_RESPONSE = 0x0065
ACTOR_PLAY_TYPE_SYSTEM = 1

FIXED_LOCAL_USER_ID = 10000
FIXED_LOCAL_ACTOR_MID = 1
FIXED_LOCAL_HERO_RESOURCE_ID = 110001

_REQUEST = struct.Struct(">Hb")
_RESPONSE_PREFIX = struct.Struct(">HiQ")
_ROOM_COUNTS = struct.Struct(">iiii")
_RESPONSE_TAIL = struct.Struct(">iQ")


@dataclass(frozen=True, slots=True)
class RoomFlowResponse:
    command_id: int
    body: bytes
    reason: str

    def to_plain_tpdu(self, *, server_time_ms: int = 0) -> bytes:
        package = encode_sh_package(self.command_id, server_time_ms, self.body)
        return encode_plain_tpdu_downlink(encode_raw_application_payload(package))


@dataclass(frozen=True, slots=True)
class RoomCreateRequest:
    room_name: bytes
    resource_id: int


@dataclass(frozen=True, slots=True)
class RoomEnterRequest:
    room_id: int
    is_reconnect: int


def decode_room_reconnect_request(body: bytes) -> int:
    if not isinstance(body, bytes):
        raise TypeError("body must be bytes")
    if len(body) != _REQUEST.size:
        raise ValueError(
            f"room reconnect request must contain exactly {_REQUEST.size} bytes, got {len(body)}"
        )
    selector, placeholder = _REQUEST.unpack(body)
    if selector != GET_RECONNECT_INFO_REQUEST:
        raise ValueError(
            f"room reconnect request expected selector 0x{GET_RECONNECT_INFO_REQUEST:04X}, "
            f"got 0x{selector:04X}"
        )
    return placeholder


def _encode_tdr_string(value: bytes, *, maximum_size: int, field_name: str) -> bytes:
    if not isinstance(value, bytes):
        raise TypeError(f"{field_name} must be bytes")
    if b"\0" in value:
        raise ValueError(f"{field_name} must not contain an embedded NUL")
    encoded = value + b"\0"
    if len(encoded) > maximum_size:
        raise ValueError(f"{field_name} exceeds its {maximum_size}-byte TDR storage")
    return len(encoded).to_bytes(4, "big") + encoded


def _decode_tdr_string(
    body: bytes,
    *,
    offset: int,
    maximum_size: int,
    field_name: str,
) -> tuple[bytes, int]:
    if len(body) - offset < 4:
        raise ValueError(f"{field_name} is missing its 4-byte TDR length")
    encoded_size = int.from_bytes(body[offset : offset + 4], "big")
    if encoded_size < 1 or encoded_size > maximum_size:
        raise ValueError(f"{field_name} TDR length must be in 1..{maximum_size}")
    end = offset + 4 + encoded_size
    if end > len(body):
        raise ValueError(f"{field_name} TDR bytes are truncated")
    encoded = body[offset + 4 : end]
    if encoded[-1] != 0 or b"\0" in encoded[:-1]:
        raise ValueError(f"{field_name} must contain one trailing NUL")
    return encoded[:-1], end


def decode_room_create_request(body: bytes) -> RoomCreateRequest:
    if not isinstance(body, bytes):
        raise TypeError("body must be bytes")
    if len(body) < 2 or int.from_bytes(body[:2], "big") != CREATE_REQUEST:
        raise ValueError(f"room create request expected selector 0x{CREATE_REQUEST:04X}")
    room_name, offset = _decode_tdr_string(
        body,
        offset=2,
        maximum_size=64,
        field_name="room_name",
    )
    if len(body) - offset != 4:
        raise ValueError("room create request must end with one int32 resource_id")
    return RoomCreateRequest(
        room_name,
        int.from_bytes(body[offset:], "big", signed=True),
    )


def encode_room_enter_request(*, room_id: int, is_reconnect: int = 0) -> bytes:
    try:
        return struct.pack(">HQi", ENTER_REQUEST, room_id, is_reconnect)
    except struct.error as error:
        raise ValueError("room enter request field is outside its TDR wire range") from error


def decode_room_enter_request(body: bytes) -> RoomEnterRequest:
    if not isinstance(body, bytes):
        raise TypeError("body must be bytes")
    if len(body) != 14:
        raise ValueError(f"room enter request must contain exactly 14 bytes, got {len(body)}")
    selector, room_id, is_reconnect = struct.unpack(">HQi", body)
    if selector != ENTER_REQUEST:
        raise ValueError(f"room enter request expected selector 0x{ENTER_REQUEST:04X}")
    return RoomEnterRequest(room_id, is_reconnect)


def encode_room_enter_response(
    *,
    room_id: int,
    result: int,
    is_reconnect: int,
    room_name: bytes,
    resource_id: int,
    sync_url: bytes = b"",
    sync_operator: int = 0,
    prefer: int = -1,
    room_user_count: int = 1,
    blue_user_count: int = 1,
    red_user_count: int = 0,
    local_id: int = 0,
) -> bytes:
    """Encode the fixed-room subset of CS_PROTO_ROOM_ENTER_RSP."""

    try:
        encoded_sync_urls = b"\0\0\0\0"
        if sync_url:
            encoded_sync_urls = b"".join(
                (
                    struct.pack(">ii", 1, sync_operator),
                    _encode_tdr_string(sync_url, maximum_size=128, field_name="sync_url"),
                )
            )
        return b"".join(
            (
                struct.pack(">HQii", ENTER_RESPONSE, room_id, result, 0),
                struct.pack(">iQ", is_reconnect, room_id),
                _encode_tdr_string(room_name, maximum_size=64, field_name="room_name"),
                encoded_sync_urls,
                struct.pack(">iiii", prefer, resource_id, room_user_count, blue_user_count),
                struct.pack(">ii", red_user_count, local_id),
            )
        )
    except struct.error as error:
        raise ValueError("room enter response field is outside its TDR wire range") from error


def encode_room_enter_instance_notify(*, room_id: int, user_camp: int, notify_type: int) -> bytes:
    try:
        return struct.pack(">HQib", ENTER_INSTANCE_NOTIFY, room_id, user_camp, notify_type)
    except struct.error as error:
        raise ValueError("room enter-instance notify field is outside its TDR wire range") from error


def encode_instance_enter(*, server_time_ms: int) -> bytes:
    """Encode E_CS_PROTO_INSTANCE_ENTER_INSTANCE."""

    try:
        return struct.pack(">HQ", INSTANCE_ENTER, server_time_ms)
    except struct.error as error:
        raise ValueError("instance enter field is outside its TDR wire range") from error


def decode_instance_choose_hero_request(body: bytes) -> int:
    """Decode C→S INSTANCE CHOOSE_HERO_REQ and return its placeholder byte."""

    if not isinstance(body, bytes):
        raise TypeError("body must be bytes")
    if len(body) != 3:
        raise ValueError(
            f"instance choose-hero request must contain exactly 3 bytes, got {len(body)}"
        )
    selector, placeholder = struct.unpack(">Hb", body)
    if selector != INSTANCE_CHOOSE_HERO_REQUEST:
        raise ValueError(
            "instance choose-hero request expected selector "
            f"0x{INSTANCE_CHOOSE_HERO_REQUEST:04X}"
        )
    return placeholder


def encode_instance_choose_hero_message(*, placeholder: int = 0) -> bytes:
    """Echo the server-side INSTANCE choose-hero transition message."""

    try:
        return struct.pack(">Hb", INSTANCE_CHOOSE_HERO_REQUEST, placeholder)
    except struct.error as error:
        raise ValueError(
            "instance choose-hero message field is outside its TDR wire range"
        ) from error


def encode_minimal_instance_update(
    *,
    server_time_ms: int,
    instance_id: int,
    instance_start_time_ms: int,
    resource_id: int,
    start_pattern: int = INSTANCE_START_PATTERN_CUSTOM,
) -> bytes:
    """Encode the no-actors/no-active-game subset of CS_PROTO_SI_UPDATE_INST.

    TDR omits the 500-entry actor storage after actor_num=0, both squad
    structures after if_support_squad=0, and game/round/state after
    is_game_start=0. Empty clan strings retain their trailing NUL.
    """

    try:
        return b"".join(
            (
                struct.pack(
                    ">HQQQiiB",
                    INSTANCE_UPDATE,
                    server_time_ms,
                    instance_id,
                    instance_start_time_ms,
                    resource_id,
                    start_pattern,
                    0,
                ),
                _encode_tdr_string(b"", maximum_size=32, field_name="blue_clan_name"),
                _encode_tdr_string(b"", maximum_size=32, field_name="red_clan_name"),
                struct.pack(">iiiiB", 0, 0, 0, 0, 0),
            )
        )
    except struct.error as error:
        raise ValueError("instance update field is outside its TDR wire range") from error


def encode_instance_update_game_started(
    *,
    server_time_ms: int,
    instance_id: int,
    instance_start_time_ms: int,
    resource_id: int,
    start_pattern: int = INSTANCE_START_PATTERN_CUSTOM,
    max_round_cnt: int = 1,
    round_start_time_ms: int = 1,
    round_state_start_time_ms: int = 1,
) -> bytes:
    """Encode UPDATE_INST with is_game_start=1 and a minimal game/round/state.

    All variable arrays (actor_list, state_list, area_list, script_item_info,
    squad data) serialize as just their 4-byte counts because their refer
    fields stay zero, matching the TDR refer-counted wire layout.
    """

    try:
        return b"".join(
            (
                struct.pack(
                    ">HQQQiiB",
                    INSTANCE_UPDATE,
                    server_time_ms,
                    instance_id,
                    instance_start_time_ms,
                    resource_id,
                    start_pattern,
                    0,
                ),
                _encode_tdr_string(b"", maximum_size=32, field_name="blue_clan_name"),
                _encode_tdr_string(b"", maximum_size=32, field_name="red_clan_name"),
                # actor_list.actor_num=0, is_game_start=1, red/blue_combat_point=0,
                # if_support_squad=0
                struct.pack(">iiiiB", 0, 1, 0, 0, 0),
                # CS_SI_GAME.max_round_cnt
                struct.pack(">i", max_round_cnt),
                # CS_SI_ROUND: counts + timestamps + camp data + empty lists
                struct.pack(
                    ">iiiiiQQiiii",
                    1,  # curr_round_cnt
                    0,  # draw_round_cnt
                    0,  # blue_win_round_cnt
                    0,  # red_win_round_cnt
                    0,  # round_result
                    round_start_time_ms,
                    0,  # duration_time_ms
                    0,  # blue_data.reborn_num
                    0,  # red_data.reborn_num
                    0,  # state_list.state_num
                    0,  # area_list.area_num
                ),
                # CS_SI_ROUND_STATE: state + timestamps + empty script items
                struct.pack(
                    ">iQQi",
                    0,  # curr_state
                    round_state_start_time_ms,
                    0,  # duration_time_ms
                    0,  # script_item_info.item_num
                ),
            )
        )
    except struct.error as error:
        raise ValueError(
            "instance game-started update field is outside its TDR wire range"
        ) from error


def _round_info_bytes(*, round_start_time_ms: int) -> bytes:
    return struct.pack(
        ">iiiiiQQii",
        1,  # curr_round_cnt
        0,  # draw_round_cnt
        0,  # blue_win_round_cnt
        0,  # red_win_round_cnt
        0,  # round_result
        round_start_time_ms,
        300000,  # duration_time_ms (5 min round)
        0,  # blue_data.reborn_num
        0,  # red_data.reborn_num
    ) + struct.pack(
        ">iiQ",
        1,  # state_list.state_num
        4,  # state_info[0].state_id = E_SI_ROUND_STATE_GAME
        300000,  # state_info[0].duration_time_ms
    ) + struct.pack(
        ">i",
        0,  # area_list.area_num
    )


def _round_state_bytes(*, round_state_start_time_ms: int, curr_state: int = 0) -> bytes:
    return struct.pack(
        ">iQQi",
        curr_state,  # E_SI_ROUND_STATE_GAME = 4
        round_state_start_time_ms,
        300000,  # duration_time_ms (5 min GAME)
        0,  # script_item_info.item_num
    )


def encode_instance_update_game(
    *,
    server_time_ms: int,
    instance_id: int,
    max_round_cnt: int = 1,
) -> bytes:
    """Encode CS_PROTO_SI_UPDATE_GAME (selector 0x0065)."""
    try:
        return struct.pack(">HQQi", INSTANCE_UPDATE_GAME, server_time_ms, instance_id, max_round_cnt)
    except struct.error as error:
        raise ValueError("instance update-game field is outside its TDR wire range") from error


def encode_instance_update_round(
    *,
    server_time_ms: int,
    instance_id: int,
    round_start_time_ms: int = 1,
) -> bytes:
    """Encode CS_PROTO_SI_UPDATE_ROUND (selector 0x0066)."""
    try:
        return struct.pack(">HQQ", INSTANCE_UPDATE_ROUND, server_time_ms, instance_id) + _round_info_bytes(
            round_start_time_ms=round_start_time_ms
        )
    except struct.error as error:
        raise ValueError("instance update-round field is outside its TDR wire range") from error


def encode_instance_update_round_state(
    *,
    server_time_ms: int,
    instance_id: int,
    round_state_start_time_ms: int = 1,
    curr_state: int = 0,
) -> bytes:
    """Encode CS_PROTO_SI_UPDATE_ROUND_STATE (selector 0x0067)."""
    try:
        return struct.pack(">HQQ", INSTANCE_UPDATE_ROUND_STATE, server_time_ms, instance_id) + _round_state_bytes(
            round_state_start_time_ms=round_state_start_time_ms,
            curr_state=curr_state,
        )
    except struct.error as error:
        raise ValueError("instance update-round-state field is outside its TDR wire range") from error


def encode_instance_start_pattern_data_ntf(
    *,
    start_pattern: int = INSTANCE_START_PATTERN_CUSTOM,
) -> bytes:
    """Encode CS_PROTO_START_PATTERN_DATA_NTF (selector 0x0194), CUSTOM arm."""
    try:
        return struct.pack(">HH", INSTANCE_START_PATTERN_DATA_NTF, start_pattern) + struct.pack(
            ">Qii", 0, 0, 1
        ) + b"\x00"
    except struct.error as error:
        raise ValueError("start-pattern-data-ntf field is outside its TDR wire range") from error


def encode_instance_update_spawn_area_state(
    *,
    server_time_ms: int = 1,
    area_id: int = 0,
    is_enable: int = 1,
    area_state: int = 0,
) -> bytes:
    """Encode CS_PROTO_SI_UPDATE_SPAWN_AREA_STATE (selector 0x00C8)."""
    try:
        return struct.pack(
            ">HQiii",
            INSTANCE_UPDATE_SPAWN_AREA_STATE,
            server_time_ms,
            area_id,
            is_enable,
            area_state,
        )
    except struct.error as error:
        raise ValueError("spawn-area-state field is outside its TDR wire range") from error


def encode_instance_update_with_local_actor(
    *,
    server_time_ms: int,
    instance_id: int,
    instance_start_time_ms: int,
    resource_id: int,
    actor_mid: int,
    user_id: int,
    user_name: bytes,
    user_image_id: int,
    level: int,
    actor_state: int,
    hero_resource_id: int,
    camp: int,
    start_pattern: int = INSTANCE_START_PATTERN_PRACTICE,
) -> bytes:
    """Encode UPDATE_INST with one fixed local actor and omitted optional storage.

    The actor subset follows CS_PROTO_INSTANCE_ACTOR_BASIC_INFO. Scores and
    weapons use zero counts, the fixed hero-data payload uses the selected
    hero resource as herocard_id, and PRACTICE has no selected union arm.
    """

    encoded_user_name = _encode_tdr_string(
        user_name,
        maximum_size=32,
        field_name="user_name",
    )
    try:
        actor = b"".join(
            (
                struct.pack(
                    ">QI",
                    actor_mid,
                    user_id,
                ),
                encoded_user_name,
                struct.pack(
                    ">iiiiii",
                    user_image_id,
                    level,
                    actor_state,
                    hero_resource_id,
                    camp,
                    0,
                ),
                struct.pack(">i", 0),
                struct.pack(">iiI", 0, 0, 0),
                struct.pack(">ibi" + "i" * 8, hero_resource_id, 0, 0, *([0] * 8)),
                struct.pack(">h", 0),
                struct.pack(">iiiiiii", *([0] * 7)),
                struct.pack(">ii", 0, start_pattern),
            )
        )
        return b"".join(
            (
                struct.pack(
                    ">HQQQiiB",
                    INSTANCE_UPDATE,
                    server_time_ms,
                    instance_id,
                    instance_start_time_ms,
                    resource_id,
                    start_pattern,
                    0,
                ),
                _encode_tdr_string(b"", maximum_size=32, field_name="blue_clan_name"),
                _encode_tdr_string(b"", maximum_size=32, field_name="red_clan_name"),
                struct.pack(">i", 1),
                actor,
                struct.pack(">iiiB", 0, 0, 0, 0),
            )
        )
    except struct.error as error:
        raise ValueError("instance actor update field is outside its TDR wire range") from error


def encode_instance_update_actor_basic_info(
    *,
    server_time_ms: int,
    instance_id: int,
    actor_mid: int,
    user_id: int,
    user_name: bytes,
    user_image_id: int,
    level: int,
    actor_state: int,
    hero_resource_id: int,
    camp: int,
    start_pattern: int = INSTANCE_START_PATTERN_PRACTICE,
) -> bytes:
    """Encode the incremental actor import message used after UPDATE_INST.

    ``E_CS_PROTO_INSTANCE_UPDATE_ACTOR_BASIC_INFO`` carries server time,
    instance id, and the same actor record used by ``CS_SI_ACTOR_LIST``.
    Keeping actor import separate lets UPDATE_INST establish the instance and
    camp/loading pipeline before the client constructs the local actor.
    """

    encoded_user_name = _encode_tdr_string(
        user_name,
        maximum_size=32,
        field_name="user_name",
    )
    try:
        actor = b"".join(
            (
                struct.pack(">QI", actor_mid, user_id),
                encoded_user_name,
                struct.pack(
                    ">iiiiii",
                    user_image_id,
                    level,
                    actor_state,
                    hero_resource_id,
                    camp,
                    0,
                ),
                struct.pack(">i", 0),
                struct.pack(">iiI", 0, 0, 0),
                struct.pack(">ibi" + "i" * 8, hero_resource_id, 0, 0, *([0] * 8)),
                struct.pack(">h", 0),
                struct.pack(">iiiiiii", *([0] * 7)),
                struct.pack(">ii", 0, start_pattern),
            )
        )
        return struct.pack(">HQQ", INSTANCE_UPDATE_ACTOR_BASIC_INFO, server_time_ms, instance_id) + actor
    except struct.error as error:
        raise ValueError("instance actor-basic field is outside its TDR wire range") from error


def validate_fixed_local_actor_identity(*, sync_user_id: int, sync_mid: int) -> None:
    """Require the login identity used by the fixed M4 actor fixture."""

    if sync_user_id != FIXED_LOCAL_USER_ID or sync_mid != FIXED_LOCAL_ACTOR_MID:
        raise ValueError(
            "fixed local actor requires SYNC_LOGIN user_id=10000 and mid=1"
        )


def encode_actor_update_state(
    *,
    user_id: int,
    actor_mid: int,
    actor_state: int,
    update_time_ms: int,
) -> bytes:
    """Encode S→C ACTOR UPDATE_STATE in declaration order."""

    try:
        return struct.pack(
            ">HIQiQ",
            ACTOR_UPDATE_STATE,
            user_id,
            actor_mid,
            actor_state,
            update_time_ms,
        )
    except struct.error as error:
        raise ValueError("actor update state field is outside its TDR wire range") from error


def decode_actor_choose_hero_request(body: bytes) -> int:
    """Decode C→S ACTOR CHOOSE_HERO_REQ and return its hero slot."""

    if not isinstance(body, bytes):
        raise TypeError("body must be bytes")
    if len(body) != 6:
        raise ValueError(
            f"actor choose-hero request must contain exactly 6 bytes, got {len(body)}"
        )
    selector, hero_position = struct.unpack(">Hi", body)
    if selector != ACTOR_CHOOSE_HERO_REQUEST:
        raise ValueError(
            f"actor choose-hero request expected selector 0x{ACTOR_CHOOSE_HERO_REQUEST:04X}"
        )
    return hero_position


def encode_actor_choose_hero_response(*, result: int = 0) -> bytes:
    """Encode S→C ACTOR CHOOSE_HERO_RSP."""

    try:
        return struct.pack(">Hi", ACTOR_CHOOSE_HERO_RESPONSE, result)
    except struct.error as error:
        raise ValueError("actor choose-hero response field is outside its TDR wire range") from error


def encode_actor_choose_hero_message(
    *,
    server_time_ms: int,
    actor_mid: int,
    hero_resource_id: int,
    hero_badge: int,
) -> bytes:
    """Encode S→C ACTOR CHOOSE_HERO_MSG in recovered declaration order.

    ``server_time_ms`` remains a validated compatibility argument for existing callers;
    the recovered message payload itself is ``mid + hero_resource_id + hero_badge``.
    """

    try:
        struct.pack(">Q", server_time_ms)
        return struct.pack(
            ">HQii",
            ACTOR_CHOOSE_HERO_MESSAGE,
            actor_mid,
            hero_resource_id,
            hero_badge,
        )
    except struct.error as error:
        raise ValueError("actor choose-hero message field is outside its TDR wire range") from error


def decode_actor_play_request(body: bytes) -> int:
    """Decode the fixed SYSTEM ACTOR PLAY_REQ and return its placeholder byte."""

    if not isinstance(body, bytes):
        raise TypeError("body must be bytes")
    if len(body) != 5:
        raise ValueError(f"actor play request must contain exactly 5 bytes, got {len(body)}")
    selector, play_type, placeholder = struct.unpack(">HHb", body)
    if selector != ACTOR_PLAY_REQUEST:
        raise ValueError(
            f"actor play request expected selector 0x{ACTOR_PLAY_REQUEST:04X}"
        )
    if play_type != ACTOR_PLAY_TYPE_SYSTEM:
        raise ValueError("actor play request expected SYSTEM type 1")
    return placeholder


def encode_actor_play_response(*, result: int = 0) -> bytes:
    """Encode S→C ACTOR PLAY_RSP."""

    try:
        return struct.pack(">Hi", ACTOR_PLAY_RESPONSE, result)
    except struct.error as error:
        raise ValueError("actor play response field is outside its TDR wire range") from error


def encode_room_reconnect_response(
    *,
    result: int,
    room_id: int = 0,
    room_name: bytes = b"",
    resource_id: int = 0,
    room_user_count: int = 0,
    blue_user_count: int = 0,
    red_user_count: int = 0,
    user_camp: int = 0,
    expired_time_ms: int = 0,
) -> bytes:
    """Encode selector 0x000B and its fixed CS_PROTO_ROOM_GET_RECONNECT_INFO_RSP."""

    try:
        return b"".join(
            (
                _RESPONSE_PREFIX.pack(GET_RECONNECT_INFO_RESPONSE, result, room_id),
                _encode_tdr_string(room_name, maximum_size=64, field_name="room_name"),
                _ROOM_COUNTS.pack(
                    resource_id,
                    room_user_count,
                    blue_user_count,
                    red_user_count,
                ),
                _RESPONSE_TAIL.pack(user_camp, expired_time_ms),
            )
        )
    except struct.error as error:
        raise ValueError("room reconnect response field is outside its TDR wire range") from error


def encode_room_create_response(
    *,
    result: int,
    room_id: int,
    room_name: bytes,
    resource_id: int,
    room_user_count: int = 1,
    blue_user_count: int = 1,
    red_user_count: int = 0,
) -> bytes:
    try:
        return b"".join(
            (
                struct.pack(">HiQ", CREATE_RESPONSE, result, room_id),
                _encode_tdr_string(room_name, maximum_size=64, field_name="room_name"),
                _ROOM_COUNTS.pack(
                    resource_id,
                    room_user_count,
                    blue_user_count,
                    red_user_count,
                ),
            )
        )
    except struct.error as error:
        raise ValueError("room create response field is outside its TDR wire range") from error


def build_room_flow_responses(
    message: Method3UplinkMessage,
    *,
    reconnect_result: int = 0,
    create_result: int = 0,
    send_enter_response_after_create: bool = False,
    room_sync_url: bytes = b"",
    send_enter_instance_notify: bool = False,
    respond_to_enter: bool = True,
) -> tuple[RoomFlowResponse, ...]:
    if message.command_id != ROOM_COMMAND or len(message.body) < 2:
        return ()
    selector = int.from_bytes(message.body[:2], "big")
    if selector == GET_RECONNECT_INFO_REQUEST:
        decode_room_reconnect_request(message.body)
        return (
            RoomFlowResponse(
                ROOM_COMMAND,
                encode_room_reconnect_response(result=reconnect_result),
                (
                    "room-reconnect-result-zero"
                    if reconnect_result == 0
                    else "room-reconnect-result-nonzero"
                ),
            ),
        )
    if selector == CREATE_REQUEST:
        request = decode_room_create_request(message.body)
        responses = [
            RoomFlowResponse(
                ROOM_COMMAND,
                encode_room_create_response(
                    result=create_result,
                    room_id=1,
                    room_name=request.room_name,
                    resource_id=request.resource_id,
                ),
                "room-create-result-zero" if create_result == 0 else "room-create-result-nonzero",
            )
        ]
        if create_result == 0 and send_enter_response_after_create:
            responses.append(
                RoomFlowResponse(
                    ROOM_COMMAND,
                    encode_room_enter_response(
                        room_id=1,
                        result=0,
                        is_reconnect=0,
                        room_name=request.room_name,
                        resource_id=request.resource_id,
                        sync_url=room_sync_url,
                    ),
                    "room-enter-result-zero-after-create",
                )
            )
        if create_result == 0 and send_enter_instance_notify:
            responses.append(
                RoomFlowResponse(
                    ROOM_COMMAND,
                    encode_room_enter_instance_notify(room_id=1, user_camp=1, notify_type=0),
                    "room-match-enter-instance-notify-after-create",
                )
            )
        return tuple(responses)
    if selector == ENTER_REQUEST and respond_to_enter:
        request = decode_room_enter_request(message.body)
        return (
            RoomFlowResponse(
                ROOM_COMMAND,
                encode_room_enter_response(
                    room_id=request.room_id,
                    result=0,
                    is_reconnect=request.is_reconnect,
                    room_name=b"RoomName1",
                    resource_id=11,
                    sync_url=room_sync_url,
                ),
                "room-enter-result-zero",
            ),
        )
    return ()
