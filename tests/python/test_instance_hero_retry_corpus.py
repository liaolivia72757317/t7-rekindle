from pathlib import Path

from Business.scripts.codec import room_flow
from Business.scripts.codec.method3 import (
    decode_method3_uplink_frame, encode_method3_payload, method3_padding_length,
)
from Business.scripts.codec.protocol import encode_sh_package


CORPUS = Path(__file__).resolve().parents[1] / "fixtures/protocol/instance-hero-retry-uplink-v1.txt"


def test_selection_retries_use_original_requests_and_method3_frames():
    expected = {
        "other-before-entry": 2, "bound-before-entry": 1, "enter": None,
        "choose-while-dead": 1, "other-after-death": 2, "play-without-choice": None,
        "bound-after-death": 1, "repeat-bound": 1, "invalid-position": 0,
    }
    rows = [line.split() for line in CORPUS.read_text(encoding="ascii").splitlines()]
    assert [row[0] for row in rows] == list(expected)
    assert [int(row[1]) for row in rows] == list(range(8, 17))
    for name, sequence, body_hex, frame_hex in rows:
        body = bytes.fromhex(body_hex)
        if expected[name] is None:
            room_flow.decode_actor_play_request(body)
        else:
            assert room_flow.decode_actor_choose_hero_request(body) == expected[name]
        plain = int(sequence).to_bytes(4, "big") + encode_sh_package(54, 0, body)
        cipher = encode_method3_payload(
            plain, bytes(16), random_padding=bytes(method3_padding_length(len(plain)) - 6))
        frame = bytes.fromhex("550e00040000000c") + len(cipher).to_bytes(4, "big") + cipher
        assert frame.hex() == frame_hex
        message = decode_method3_uplink_frame(frame, bytes(16))
        assert (message.sequence, message.command_id, message.body) == (int(sequence), 54, body)
    assert room_flow.encode_actor_choose_hero_response(result=1).hex() == "003300000001"


def test_respawn_hero_and_equipment_match_original_codecs():
    from Business.scripts.codec import vision_flow

    path = CORPUS.with_name("instance-respawn-hero-wire-v1.txt")
    rows = [line.split() for line in path.read_text(encoding="ascii").splitlines()]
    chosen = room_flow.encode_actor_choose_hero_message(
        server_time_ms=5000, actor_mid=101, hero_resource_id=2102, hero_badge=0)
    vision = vision_flow.encode_fixed_local_actor_vision_add_event(
        actor_mid=101, user_id=10000, instance_id=101, hero_resource_id=2102,
        camp=1, level=100, actor_name=b"Player", position=(5., 6., 0.),
        current_hp=100, maximum_hp=100, current_stamina=100, heading=45,
        collision_group=101, gravity=-10000,
        equipment=({"slot": 3, "weaponId": 702}, {"slot": 13, "weaponId": 100000}),
        equipped_weapon_id=702, offhand_id=0)
    assert rows == [["chosen-hero", "54", chosen.hex()], ["respawn-vision", "14", vision.hex()]]
