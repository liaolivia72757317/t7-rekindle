"""Explicit-entity health update, CS_PROTO_ATTR_UPDATE (command 15, selector 1)."""
import struct

ATTR_COMMAND = 15
ATTR_UPDATE = 1
ATTR_HEALTH = 1


def encode_health_update(*, entity_rid: int, health: int, server_time_ms: int) -> bytes:
    if type(health) is not int or not 0 <= health <= 0x7FFFFFFF:
        raise ValueError("health must fit a nonnegative int32")
    try:
        # Special mark, regeneration delay and speed are all zero.
        return struct.pack(">HQiiiiiQ", ATTR_UPDATE, entity_rid, ATTR_HEALTH,
                           health, 0, 0, 0, server_time_ms)
    except struct.error as error:
        raise ValueError("attribute update field is outside its TDR wire range") from error
