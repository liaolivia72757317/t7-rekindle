import struct

import pytest

from Business.scripts import app, contracts as wire
from Business.scripts.codec import protocol


def test_login_starts_at_level_100():
    context = {"nowMs": 0}
    result = app.handleEvent(
        {"type": "connected", "connection": 1, "role": "logic"},
        app.createState(context), context)
    result = app.handleEvent(
        {"type": "authenticated", "connection": 1}, result["state"], context)
    timer = result["timers"][0]
    result = app.handleEvent(
        timer["event"], result["state"], {"nowMs": timer["delayMs"]})

    identity = protocol.decode_minimal_login_success(result["send"][0]["body"])
    assert identity.level == 100


@pytest.mark.parametrize("camp", [1, 2])
@pytest.mark.parametrize("player_name", [wire.USER_NAME, b"Rekindler"])
def test_instance_actor_starts_at_level_100(camp, player_name):
    body = wire.actorInfo(1000, camp, player_name)
    name_offset = struct.calcsize(">HQQQI")
    name_size = struct.unpack_from(">I", body, name_offset)[0]
    level_offset = name_offset + 4 + name_size + 4

    assert struct.unpack_from(">i", body, level_offset)[0] == 100


@pytest.mark.parametrize("camp", [1, 2])
@pytest.mark.parametrize("hero_id,runtime_movement", [
    (110001, False), (110003, False), (110004, False), (110001, True),
])
def test_local_actor_vision_keeps_level_100(camp, hero_id, runtime_movement):
    body = wire.actorVision(camp, hero_id, runtimeMovement=runtime_movement)
    # Level follows the VISION header, identity, movement data and camp flags.
    level_offset = 10 + 14 + 62 + 6

    assert struct.unpack_from(">i", body, level_offset)[0] == 100


def test_enemy_level_is_unchanged():
    body = wire.enemyVision()
    assert struct.unpack_from(">i", body, 92)[0] == 1
