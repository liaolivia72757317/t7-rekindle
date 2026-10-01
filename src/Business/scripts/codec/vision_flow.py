"""Minimal M4 VISION codec for importing one fixed local actor."""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass


VISION_COMMAND = 0x000E
VISION_ADD_EVENT = 0x0001
VISION_DEL_EVENT = 0x0002
VISION_GET_VISION_LIST_REQUEST = 0x0005
VISION_LIST_RESPONSE = 0x0006
VISION_GET_OBJECTS_REQUEST = 0x0007
VISION_OBJECT_ACTOR = 0x00000001

INSTANCE_START_PATTERN_PRACTICE = 0x0000000E
FIXED_LOCAL_USER_ID = 10000
FIXED_LOCAL_ACTOR_MID = 1
FIXED_LOCAL_INSTANCE_ID = 1
FIXED_LOCAL_HERO_RESOURCE_ID = 110001


@dataclass(frozen=True, slots=True)
class VisionObjectsRequest:
    object_mids: tuple[int, ...]
    force_get: int


def _encode_tdr_string(value: bytes, *, maximum_size: int, field_name: str) -> bytes:
    if not isinstance(value, bytes):
        raise TypeError(f"{field_name} must be bytes")
    if b"\0" in value:
        raise ValueError(f"{field_name} must not contain an embedded NUL")
    encoded = value + b"\0"
    if len(encoded) > maximum_size:
        raise ValueError(f"{field_name} exceeds its {maximum_size}-byte TDR storage")
    return len(encoded).to_bytes(4, "big") + encoded


def decode_vision_list_request(body: bytes) -> int:
    """Decode C→S GET_VISION_LIST_REQ and return its signed sequence number."""

    if not isinstance(body, bytes):
        raise TypeError("body must be bytes")
    if len(body) != 6:
        raise ValueError(
            f"vision-list request must contain exactly 6 bytes, got {len(body)}"
        )
    selector, sequence = struct.unpack(">Hi", body)
    if selector != VISION_GET_VISION_LIST_REQUEST:
        raise ValueError(
            "vision-list request expected selector "
            f"0x{VISION_GET_VISION_LIST_REQUEST:04X}"
        )
    return sequence


def encode_vision_list_response(
    *,
    sequence: int,
    object_mids: tuple[int, ...],
) -> bytes:
    """Encode one complete VISION_LIST_RSP part."""

    if not isinstance(object_mids, tuple):
        raise TypeError("object_mids must be a tuple")
    if len(object_mids) > 1000:
        raise ValueError("vision-list response cannot contain more than 1000 objects")
    try:
        return b"".join(
            (
                struct.pack(
                    ">Hibbii",
                    VISION_LIST_RESPONSE,
                    sequence,
                    0,
                    1,
                    len(object_mids),
                    len(object_mids),
                ),
                b"".join(struct.pack(">Q", object_mid) for object_mid in object_mids),
            )
        )
    except struct.error as error:
        raise ValueError("vision-list response field is outside its TDR wire range") from error


def decode_vision_get_objects_request(body: bytes) -> VisionObjectsRequest:
    """Decode C→S GET_OBJECTS_REQ with its exact refer-count boundary."""

    if not isinstance(body, bytes):
        raise TypeError("body must be bytes")
    if len(body) < 7:
        raise ValueError("vision get-objects request is truncated")
    selector, object_count = struct.unpack(">Hi", body[:6])
    if selector != VISION_GET_OBJECTS_REQUEST:
        raise ValueError(
            "vision get-objects request expected selector "
            f"0x{VISION_GET_OBJECTS_REQUEST:04X}"
        )
    if object_count < 0 or object_count > 50:
        raise ValueError("vision get-objects count must be in 0..50")
    expected_size = 7 + object_count * 8
    if len(body) != expected_size:
        raise ValueError(
            "vision get-objects request must contain exactly "
            f"{expected_size} bytes for {object_count} objects, got {len(body)}"
        )
    offset = 6
    object_mids = tuple(
        struct.unpack_from(">Q", body, offset + index * 8)[0]
        for index in range(object_count)
    )
    force_get = struct.unpack_from(">b", body, offset + object_count * 8)[0]
    if force_get not in (0, 1):
        raise ValueError("vision get-objects force_get must be 0 or 1")
    return VisionObjectsRequest(object_mids, force_get)


def _encode_move_data(
    *,
    active: int,
    position: tuple[float, float, float],
) -> bytes:
    if not isinstance(position, tuple) or len(position) != 3:
        raise TypeError("position must be a tuple of three floats")
    if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in position):
        raise ValueError("position values must be finite numbers")
    return struct.pack(
        ">bbB" + "h" * 10 + "fffIfffih" + "b" * 5,
        0,
        active,
        0,
        *([0] * 10),
        *position,
        0,
        0.0,
        0.0,
        0.0,
        0,
        0,
        *([0] * 5),
    )


def encode_fixed_local_actor_vision_add_event(
    *,
    server_time_ms: int = 0,
    actor_mid: int = FIXED_LOCAL_ACTOR_MID,
    user_id: int = FIXED_LOCAL_USER_ID,
    instance_id: int = FIXED_LOCAL_INSTANCE_ID,
    hero_resource_id: int = FIXED_LOCAL_HERO_RESOURCE_ID,
    camp: int = 1,
    level: int = 1,
    actor_name: bytes = b"local",
    position: tuple[float, float, float] = (0.0, 0.0, 0.0),
    current_hp: int = 100,
    maximum_hp: int = 100,
    current_stamina: int = 100,
) -> bytes:
    """Encode ADD_EVENT with one schema-valid local ACTOR object.

    Variable-count weapon, action-history, buff, feature and skill-book arrays
    are intentionally empty. PRACTICE selects no start-pattern union arm. The
    identity and hero values are local fixture values, not recovered originals.
    """

    legion_name = _encode_tdr_string(
        b"",
        maximum_size=32,
        field_name="legion_name",
    )
    encoded_actor_name = _encode_tdr_string(
        actor_name,
        maximum_size=32,
        field_name="actor_name",
    )
    try:
        actor = b"".join(
            (
                struct.pack(">IQH", user_id, actor_mid, instance_id),
                _encode_move_data(active=1, position=position),
                struct.pack(">ibbiii", camp, 1, 0, level, 0, 0),
                legion_name,
                struct.pack(">ibi" + "i" * 8, hero_resource_id, 0, 0, *([0] * 8)),
                struct.pack(">h", 0),
                struct.pack(">HQhQ", 0, 0, 0, 0),
                struct.pack(">iii", current_hp, maximum_hp, current_stamina),
                struct.pack(">IQH", 0, 0, 0),
                struct.pack(">iii", 0, 0, 0),
                struct.pack(">" + "h" * 5, *([0] * 5)),
                encoded_actor_name,
                struct.pack(">i", 0),
                struct.pack(">Q", 0),
                struct.pack(">iiii", 0, 0, 0, 0),
                _encode_move_data(active=0, position=(0.0, 0.0, 0.0)),
                struct.pack(">ibi", 0, 0, 0),
                struct.pack(">" + "i" * 7, *([0] * 7)),
                struct.pack(">h", 0),
                struct.pack(">h", 0),
                struct.pack(">i", INSTANCE_START_PATTERN_PRACTICE),
            )
        )
        return b"".join(
            (
                struct.pack(">Hii", VISION_ADD_EVENT, 1, VISION_OBJECT_ACTOR),
                actor,
                struct.pack(">Q", server_time_ms),
            )
        )
    except struct.error as error:
        raise ValueError("vision actor field is outside its TDR wire range") from error


def encode_vision_del_event(
    *,
    object_mid: int = FIXED_LOCAL_ACTOR_MID,
    object_type: int = VISION_OBJECT_ACTOR,
    server_time_ms: int = 0,
) -> bytes:
    """Encode VISION DEL_EVENT for one object.

    Wire is selector 2 + object_num:i32 + one
    CS_PROTO_VISION_DEL_OBJECT_INFO (object_type:i32 + object_mid:u64)
    + svr_time:u64.
    """

    if object_type != VISION_OBJECT_ACTOR:
        raise ValueError(f"object_type must be ACTOR(1), got {object_type}")
    try:
        return struct.pack(
            ">HiiQQ",
            VISION_DEL_EVENT,
            1,
            object_type,
            object_mid,
            server_time_ms,
        )
    except struct.error as error:
        raise ValueError("vision del field is outside its TDR wire range") from error
