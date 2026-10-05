"""Deterministic loopback fixture wire subsets; not a game simulation."""
import struct

from .codec import login_flow, room_flow, vision_flow

BASELINE_ID = "synthetic-baseline-v1"
# Standalone Python fixtures retain the server-ground baseline.  The native
# launcher adds runtimeMovement to the embedded context after the in-memory
# client overlay has been installed.
CLIENT_RUNTIME_MOVEMENT = False
RUNTIME_MOVEMENT_MODE = "client-runtime-offline-v1"
RUNTIME_HERO_IDS = (110001,)
RESOURCE_ID = 1028
START_PATTERN = 2
USER_ID = 10000
USER_LEVEL = 100
ACTOR_ID = 1
HERO_ID = 110001
HERO_IDS = (110001, 110003, 110004)
USER_NAME = "新玩家".encode("gbk")
WEAPON_ID = 1030411
POSITION = (272.451, 157.634, 0.218)
ENEMY_POSITION = (271.046, 156.857, 0.218)
# Keep the synthetic fixture's preparation window deterministic across runs.
PREPARE_MS = 30000
START_MS = 5000
GAME_MS = 1200000
GROUND_STEP_DISTANCE = 0.25
GROUND_STEP_MS = 50


def heroIds(runtimeMovement=False):
    return RUNTIME_HERO_IDS if runtimeMovement else HERO_IDS


def encodePlayerName(value):
    if type(value) is not str:
        raise TypeError("playerName must be a string")
    value = value.strip()
    if not value or any(ord(char) < 32 or 127 <= ord(char) < 160 for char in value):
        raise ValueError("playerName must be nonempty and contain no control characters")
    encoded = value.encode("gbk", errors="strict")
    if len(encoded) > 31:
        raise ValueError("playerName exceeds the 31-byte GBK limit")
    return encoded


def exact(body, length, label):
    if type(body) is not bytes or len(body) != length:
        raise ValueError(f"{label} requires exactly {length} bytes")


def roundState(now, current, duration):
    return struct.pack(">HQQiQQi", 0x67, now, 1, current, now, duration, 0)


def roundInfo(now):
    return (struct.pack(">HQQiiiiiQQiii", 0x66, now, 1,
                        1, 0, 0, 0, 0, now, 0, 0, 0, 2)
            + struct.pack(">iQiQi", 3, 0, 4, GAME_MS, 0))


def instanceInfo(now, startedAt=None):
    return room_flow.encode_minimal_instance_update(
        server_time_ms=now, instance_id=1,
        instance_start_time_ms=now if startedAt is None else startedAt,
        resource_id=RESOURCE_ID, start_pattern=START_PATTERN)


def actorInfo(now, camp, playerName=USER_NAME):
    return room_flow.encode_instance_update_actor_basic_info(
        server_time_ms=now, instance_id=1, actor_mid=ACTOR_ID, user_id=USER_ID,
        user_name=playerName, user_image_id=7, level=USER_LEVEL, actor_state=4,
        hero_resource_id=HERO_ID, camp=camp, start_pattern=START_PATTERN)


def actorState(now, current):
    return room_flow.encode_actor_update_state(
        user_id=USER_ID, actor_mid=ACTOR_ID, actor_state=current, update_time_ms=now)


def actorVision(camp, heroId=HERO_ID, actorName=USER_NAME, *, runtimeMovement=False,
                position=POSITION):
    body = vision_flow.encode_fixed_local_actor_vision_add_event(
        camp=camp, position=position, hero_resource_id=heroId, actor_name=actorName,
        level=USER_LEVEL)
    if runtimeMovement:
        if heroId != RUNTIME_HERO_IDS[0]:
            raise ValueError("runtime movement gravity is bound to local hero 110001")
        gravityOffset = 79
        if body[gravityOffset:gravityOffset + 2] != b"\0\0":
            raise ValueError("canonical VISION gravity layout changed")
        body = body[:gravityOffset] + struct.pack(">h", -10000) + body[gravityOffset + 2:]
    # CS_VISION_ACTOR's weapon count follows the fixed identity/move/avatar prefix.
    # Keep the canonical encoder and insert the listener's one 29-byte weapon entry.
    weaponCountOffset = 150
    if body[weaponCountOffset:weaponCountOffset + 2] != b"\0\0":
        raise ValueError("canonical VISION weapon-count layout changed")
    weapon = struct.pack(">iBBB", WEAPON_ID, 1, 0, 1) + bytes(22)
    return body[:weaponCountOffset] + b"\0\1" + weapon + body[weaponCountOffset + 2:]


def enemyVision():
    return vision_flow.encode_fixed_local_actor_vision_add_event(
        actor_mid=2, user_id=10001, instance_id=2, hero_resource_id=110003,
        camp=2, actor_name=b"bot", position=ENEMY_POSITION)


def battleHeroes(sequence, runtimeMovement=False):
    heroes = heroIds(runtimeMovement)
    bodies = [login_flow.encode_fixed_battle_hero_sync_item_container_response(
        sequence=sequence, position=slot, guid=slot, hero_resource_id=hero)
        for slot, hero in enumerate(heroes, 1)]
    if any(len(body) != 116 for body in bodies):
        raise ValueError("canonical BATTLE_HERO slot layout changed")
    return bodies[0][:12] + struct.pack(">4i", *([len(heroes)] * 4)) + b"".join(
        body[28:] for body in bodies)


def startPattern():
    return struct.pack(">HH8i", 0x194, 7, *([0] * 8))


def spawnArea(now):
    return struct.pack(">HQiii", 0xC8, now, 0, 1, 0)


def campExchange(now, camp):
    return struct.pack(">HQiQi", 6, now, 1, ACTOR_ID, camp)


def heartbeat(body):
    exact(body, 16, "heartbeat")
    return b"\0\2" + body[2:14]


def matchStart(body):
    if not 37 <= len(body) <= 2100:
        raise ValueError("match-start body outside evidenced 37..2100-byte bound")
    # The match-pattern union remains opaque; the source listener echoes it.
    return b"\0\2" + body[6:-1] + bytes(201)
