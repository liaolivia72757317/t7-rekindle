"""Deterministic loopback fixture wire subsets; not a game simulation."""
import struct

from .codec import login_flow, room_flow, vision_flow
from .map_data import ENEMY_POSITION, POSITION, RESOURCE_ID

BASELINE_ID = "synthetic-baseline-v1"
# Standalone Python fixtures retain the server-ground baseline.  The native
# launcher adds runtimeMovement to the embedded context after the in-memory
# client overlay has been installed.
CLIENT_RUNTIME_MOVEMENT = False
RUNTIME_MOVEMENT_MODE = "client-runtime-offline-v1"
RUNTIME_HERO_IDS = (110001,)
START_PATTERN = 2
USER_ID = 10000
USER_LEVEL = 100
ACTOR_ID = 1
BOT_ID = 2
HERO_ID = 110001
HERO_IDS = (110001, 110003, 110004)
USER_NAME = "新玩家".encode("gbk")
WEAPON_ID = 1030411
# Keep the synthetic fixture's preparation window deterministic across runs.
PREPARE_MS = 30000
# The native HUD truncates seconds, then waits one second before its first tick.
# Just under six seconds gives 5..1 without showing 6 at the exact start boundary.
START_MS = 5999
GAME_MS = 1200000
KILL_TARGET = 60
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


def roundState(now, current, duration, *, startedAt=None):
    return struct.pack(">HQQiQQi", 0x67, now, 1, current, now if startedAt is None else startedAt, duration, 0)


def roundInfo(now, duration=GAME_MS):
    return (struct.pack(">HQQiiiiiQQiii", 0x66, now, 1,
                        1, 0, 0, 0, 0, now, 0, 0, 0, 2)
            + struct.pack(">iQiQi", 3, 0, 4, duration, 0))


def roundResult(now, result):
    return struct.pack(">HQQii", 0x68, now, 1, result, 1)


def instanceInfo(now, startedAt=None):
    return room_flow.encode_minimal_instance_update(
        server_time_ms=now, instance_id=1,
        instance_start_time_ms=now if startedAt is None else startedAt,
        resource_id=RESOURCE_ID, start_pattern=START_PATTERN)


def actorInfo(now, camp, playerName=USER_NAME, heroId=HERO_ID):
    return room_flow.encode_instance_update_actor_basic_info(
        server_time_ms=now, instance_id=1, actor_mid=ACTOR_ID, user_id=USER_ID,
        user_name=playerName, user_image_id=7, level=USER_LEVEL, actor_state=4,
        hero_resource_id=heroId, camp=camp, start_pattern=START_PATTERN)


def actorState(now, current):
    return room_flow.encode_actor_update_state(
        user_id=USER_ID, actor_mid=ACTOR_ID, actor_state=current, update_time_ms=now)


def actorVision(camp, heroId=HERO_ID, actorName=USER_NAME, *, runtimeMovement=False,
                position=POSITION, actorId=ACTOR_ID, userId=USER_ID, currentHp=100,
                heading=0, equipment=None, weaponId=WEAPON_ID, offhandId=0):
    return vision_flow.encode_fixed_local_actor_vision_add_event(
        camp=camp, position=position, hero_resource_id=heroId, actor_name=actorName,
        level=USER_LEVEL if actorId == ACTOR_ID else 1,
        actor_mid=actorId, instance_id=actorId, user_id=userId, current_hp=currentHp,
        heading=heading, collision_group=actorId if runtimeMovement else 0,
        gravity=-10000 if runtimeMovement else 0,
        equipment=[dict(slot=1, weaponId=WEAPON_ID)] if equipment is None else equipment,
        equipped_weapon_id=weaponId, offhand_id=offhandId)


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


def campExchange(now, camp, *, actorId=ACTOR_ID):
    return struct.pack(">HQiQi", 6, now, 1, actorId, camp)


def heartbeat(body):
    exact(body, 16, "heartbeat")
    return b"\0\2" + body[2:14]


def matchStart(body):
    if not 37 <= len(body) <= 2100:
        raise ValueError("match-start body outside evidenced 37..2100-byte bound")
    # The match-pattern union remains opaque; the source listener echoes it.
    return b"\0\2" + body[6:-1] + bytes(201)
