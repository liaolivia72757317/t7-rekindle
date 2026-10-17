from pathlib import Path

from Business.scripts import bot_names, contracts
from Business.scripts.codec import battle_flow, kill_flow, vision_flow
from Business.scripts.codec.method3 import encode_method3_payload, method3_padding_length
from Business.scripts.codec.protocol import encode_sh_package


def refresh_vectors():
    equipment = ({'slot': 1, 'weaponId': 701}, {'slot': 13, 'weaponId': 100000})
    def actor(mid, user, name, camp, level, hero, health, position, heading, weapon):
        return vision_flow.encode_fixed_local_actor_vision_add_event(
            actor_mid=mid, instance_id=mid, user_id=user, actor_name=name,
            camp=camp, level=level, hero_resource_id=hero, current_hp=health,
            position=position, heading=heading, collision_group=mid, gravity=-10000,
            equipment=equipment, equipped_weapon_id=weapon)
    return {
        'round-game': contracts.roundState(9000, 4, 120000, startedAt=6000),
        'camp-score': kill_flow.encode_camp_kill_update(server_time_ms=9000, instance_id=1,
            blue_kills=2, red_kills=3, kill_target=10),
        'local-vision': actor(101, 10000, b'Player', 1, 100, 2102, 37, (5., 6., 0.), 45, 100000),
        'bot-vision': actor(202, 10001, (bot_names.PREFIXES['aggressive'] + 'Fixture0').encode('gbk'),
            2, 1, 1101, 0, (8., 9., 0.), -90, 701),
        'bot-action': battle_flow.encode_battle_state_sync_simple(instance_id=202,
            seq_no=7, state=2, state_change_ms=800, state_time_ms=0),
        'bot-dead': battle_flow.encode_battle_dead_notify(entity_rid=202, server_time_ms=9000),
    }


def test_refresh_vectors_match_original_codecs():
    path = Path(__file__).resolve().parents[1] / 'fixtures/protocol/instance-refresh-wire-v1.txt'
    actual = dict(line.split() for line in path.read_text(encoding='ascii').splitlines())
    assert actual == {name: body.hex() for name, body in refresh_vectors().items()}


def test_refresh_requests_use_original_method3_frames():
    path = Path(__file__).resolve().parents[1] / 'fixtures/protocol/instance-refresh-uplink-v1.txt'
    for line in path.read_text(encoding='ascii').splitlines():
        name, seq, command, body, expected = line.split()
        body = bytes.fromhex(body)
        if name.startswith('heartbeat'):
            assert len(body) == 16 and body[:2] == b'\0\1'
        if int(command) == 14:
            assert vision_flow.decode_vision_get_objects_request(body).object_mids == (101, 202)
        plain = int(seq).to_bytes(4, 'big') + encode_sh_package(int(command), 0, body)
        cipher = encode_method3_payload(plain, bytes(16),
            random_padding=bytes(method3_padding_length(len(plain)) - 6))
        assert (bytes.fromhex('550e00040000000c') + len(cipher).to_bytes(4, 'big') + cipher).hex() == expected
