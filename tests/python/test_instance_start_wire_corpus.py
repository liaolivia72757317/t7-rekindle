from pathlib import Path

from Business.scripts import contracts
from Business.scripts.codec import protocol


ROWS = Path(__file__).resolve().parents[1] / 'fixtures/protocol/instance-start-wire-v1.txt'
PHASE_ROWS = Path(__file__).resolve().parents[1] / 'fixtures/protocol/instance-startup-wire-v1.txt'
CONTROL_ROWS = Path(__file__).resolve().parents[1] / 'fixtures/protocol/instance-control-wire-v1.txt'
UPLINK_ROWS = Path(__file__).resolve().parents[1] / 'fixtures/protocol/instance-control-uplink-v1.txt'
LIFE_ROWS = Path(__file__).resolve().parents[1] / 'fixtures/protocol/instance-life-wire-v1.txt'
BOUND_IDENTITY_ROWS = Path(__file__).resolve().parents[1] / 'fixtures/protocol/instance-bound-identity-wire-v1.txt'
ACTOR_IMPORT_ROWS = Path(__file__).resolve().parents[1] / 'fixtures/protocol/instance-actor-import-wire-v1.txt'
ACTOR_IMPORT_UPLINK_ROWS = Path(__file__).resolve().parents[1] / 'fixtures/protocol/instance-actor-import-uplink-v1.txt'
BOT_IMPORT_ROWS = Path(__file__).resolve().parents[1] / 'fixtures/protocol/instance-bot-import-wire-v1.txt'
VISION_OBJECT_ROWS = Path(__file__).resolve().parents[1] / 'fixtures/protocol/instance-vision-objects-wire-v1.txt'
VISION_ALL_UPLINK_ROWS = Path(__file__).resolve().parents[1] / 'fixtures/protocol/instance-vision-all-uplink-v1.txt'
LOAD_HERO_ROWS = Path(__file__).resolve().parents[1] / 'fixtures/protocol/instance-load-hero-wire-v1.txt'
LOAD_HERO_UPLINK_ROWS = Path(__file__).resolve().parents[1] / 'fixtures/protocol/instance-load-hero-uplink-v1.txt'
ENTRY_ROWS = Path(__file__).resolve().parents[1] / 'fixtures/protocol/instance-battle-entry-wire-v1.txt'
ENTRY_UPLINK_ROWS = Path(__file__).resolve().parents[1] / 'fixtures/protocol/instance-battle-entry-uplink-v1.txt'


def test_instance_start_matches_original_codecs():
    actual = {
        'sync-login': (44, protocol.encode_sync_login_response(
            protocol.SyncLoginResponse(10000, 1, 0, 0))),
        'instance-update': (10, contracts.instanceInfo(5000)),
    }
    expected = {}
    for line in ROWS.read_text(encoding='ascii').splitlines():
        name, command, body = line.split()
        expected[name] = (int(command), bytes.fromhex(body))
    assert actual == expected


def test_instance_sync_login_uses_bound_client_rid():
    name, command, body = BOUND_IDENTITY_ROWS.read_text(encoding='ascii').strip().split()
    actual = protocol.encode_sync_login_response(protocol.SyncLoginResponse(10000, 101, 0, 0))
    assert (name, int(command), bytes.fromhex(body)) == ('sync-login-rid101', 44, actual)


def test_instance_actor_import_matches_original_codecs():
    import struct
    from Business.scripts.codec import room_flow, vision_flow, kill_flow

    actual = {
        'camp-ready': (35, struct.pack('>Hi', 4, 0)),
        'actor-vision': (14, vision_flow.encode_fixed_local_actor_vision_add_event(
            actor_mid=101, user_id=10000, instance_id=101, hero_resource_id=1101,
            camp=1, level=100, actor_name=b'Player', position=(1.25, 20.5, -2.),
            current_hp=100, maximum_hp=100, current_stamina=100, heading=45,
            collision_group=101, gravity=-10000,
            equipment=({'slot': 1, 'weaponId': 701}, {'slot': 13, 'weaponId': 100000}),
            equipped_weapon_id=701, offhand_id=0)),
        'actor-basic': (10, room_flow.encode_instance_update_actor_basic_info(
            server_time_ms=6000, instance_id=1, actor_mid=101, user_id=10000,
            user_name=b'Player', user_image_id=7, level=100, actor_state=4,
            hero_resource_id=1101, camp=1, start_pattern=2)),
        'camp-exchange': (35, struct.pack('>HQiQi', 6, 6000, 1, 101, 1)),
        'camp-score': (23, kill_flow.encode_camp_kill_update(
            server_time_ms=6000, instance_id=1, blue_kills=0, red_kills=0, kill_target=10)),
    }
    expected = {}
    for line in ACTOR_IMPORT_ROWS.read_text(encoding='ascii').splitlines():
        name, command, body = line.split()
        expected[name] = (int(command), bytes.fromhex(body))
    assert actual == expected


def test_instance_actor_import_uplink_matches_original_encryption():
    from Business.scripts.codec.method3 import encode_method3_payload, method3_padding_length
    from Business.scripts.codec.protocol import encode_sh_package

    name, sequence, body, frame = ACTOR_IMPORT_UPLINK_ROWS.read_text(encoding='ascii').strip().split()
    clear = int(sequence).to_bytes(4, 'big') + encode_sh_package(35, 0, bytes.fromhex(body))
    padding = bytes(method3_padding_length(len(clear)) - 6)
    cipher = encode_method3_payload(clear, bytes(16), random_padding=padding)
    encoded = bytes.fromhex('550e00040000000c') + len(cipher).to_bytes(4, 'big') + cipher
    assert name == 'camp-ready' and encoded.hex() == frame


def test_instance_bot_member_import_matches_original_codecs():
    import struct
    from Business.scripts.codec import room_flow

    actual = {
        'bot-basic': (10, room_flow.encode_instance_update_actor_basic_info(
            server_time_ms=6000, instance_id=1, actor_mid=202, user_id=10001,
            user_name='[进攻]Fixture0'.encode('gbk'), user_image_id=7, level=1,
            actor_state=8, hero_resource_id=1101, camp=2, start_pattern=2)),
        'bot-camp-exchange': (35, struct.pack('>HQiQi', 6, 6000, 1, 202, 2)),
    }
    expected = {}
    for line in BOT_IMPORT_ROWS.read_text(encoding='ascii').splitlines():
        name, command, body = line.split()
        expected[name] = (int(command), bytes.fromhex(body))
    assert actual == expected


def test_instance_vision_objects_match_original_codecs():
    from Business.scripts.codec import vision_flow

    rows = [line.split() for line in VISION_OBJECT_ROWS.read_text(encoding='ascii').splitlines()]
    assert rows[0][0:2] == ['request', '14']
    request = vision_flow.decode_vision_get_objects_request(bytes.fromhex(rows[0][2]))
    assert request.object_mids == (101, 202) and request.force_get == 0
    bot = vision_flow.encode_fixed_local_actor_vision_add_event(
        actor_mid=202, user_id=10001, instance_id=202, hero_resource_id=1101,
        camp=2, level=1, actor_name='[进攻]Fixture0'.encode('gbk'),
        position=(0., 40., 0.), current_hp=100, maximum_hp=100, current_stamina=100,
        heading=0, collision_group=202, gravity=-10000,
        equipment=({'slot': 1, 'weaponId': 701}, {'slot': 13, 'weaponId': 100000}),
        equipped_weapon_id=701, offhand_id=0)
    assert rows[1][0:2] == ['bot-vision', '14'] and bot.hex() == rows[1][2]


def test_instance_vision_all_uplink_matches_original_encryption():
    from Business.scripts.codec import vision_flow
    from Business.scripts.codec.method3 import encode_method3_payload, method3_padding_length
    from Business.scripts.codec.protocol import encode_sh_package

    name, sequence, body, frame = VISION_ALL_UPLINK_ROWS.read_text(encoding='ascii').strip().split()
    request = vision_flow.decode_vision_get_objects_request(bytes.fromhex(body))
    assert name == 'vision-all' and request.object_mids == tuple(range(101, 121))
    clear = int(sequence).to_bytes(4, 'big') + encode_sh_package(14, 0, bytes.fromhex(body))
    cipher = encode_method3_payload(clear, bytes(16),
                                    random_padding=bytes(method3_padding_length(len(clear)) - 6))
    encoded = bytes.fromhex('550e00040000000c') + len(cipher).to_bytes(4, 'big') + cipher
    assert encoded.hex() == frame


def test_instance_load_and_hero_selection_match_original_codecs():
    from Business.scripts.codec import room_flow

    actual = {
        'load-prepare': (10, contracts.roundState(5000, 2, contracts.PREPARE_MS)),
        'select-state': (54, room_flow.encode_actor_update_state(
            user_id=10000, actor_mid=101, actor_state=5, update_time_ms=6000)),
        'hero-result': (54, room_flow.encode_actor_choose_hero_response()),
        'hero-message': (54, room_flow.encode_actor_choose_hero_message(
            server_time_ms=6000, actor_mid=101, hero_resource_id=1101, hero_badge=0)),
    }
    expected = {}
    for line in LOAD_HERO_ROWS.read_text(encoding='ascii').splitlines():
        name, command, body = line.split()
        expected[name] = (int(command), bytes.fromhex(body))
    assert actual == expected


def test_instance_load_and_hero_uplink_match_original_encryption():
    from Business.scripts.codec.method3 import encode_method3_payload, method3_padding_length
    from Business.scripts.codec.protocol import encode_sh_package

    for line in LOAD_HERO_UPLINK_ROWS.read_text(encoding='ascii').splitlines():
        name, sequence, body, frame = line.split()
        clear = int(sequence).to_bytes(4, 'big') + encode_sh_package(54, 0, bytes.fromhex(body))
        cipher = encode_method3_payload(clear, bytes(16),
                                        random_padding=bytes(method3_padding_length(len(clear)) - 6))
        encoded = bytes.fromhex('550e00040000000c') + len(cipher).to_bytes(4, 'big') + cipher
        assert name in ('actor-load', 'hero-select') and encoded.hex() == frame


def test_instance_battle_entry_matches_original_codecs():
    from Business.scripts.codec import battle_flow, move_flow, room_flow, vision_flow

    state = lambda user, mid, value, at: room_flow.encode_actor_update_state(
        user_id=user, actor_mid=mid, actor_state=value, update_time_ms=at)
    actual = {
        'play-response': (54, room_flow.encode_actor_play_response()),
        'local-in-scene': (54, state(10000, 101, 6, 9000)),
        'local-vision-del': (14, vision_flow.encode_vision_del_event(object_mid=101)),
        'guard-value': (4, battle_flow.encode_parry_value_notify(value=5)),
        'local-ready': (54, state(10000, 101, 8, 9000)),
        'local-move-inactive': (2, move_flow.encode_move_notify_active(
            server_tick=4000, target_instance_id=1, active=0)),
        'local-idle': (4, battle_flow.encode_battle_state_sync_simple(
            instance_id=101, seq_no=1, state=2, state_change_ms=4000, state_time_ms=0)),
        'bot-in-scene': (54, state(10001, 202, 6, 9000)),
        'bot-vision-del': (14, vision_flow.encode_vision_del_event(object_mid=202)),
        'round-start': (10, contracts.roundState(9000, 3, contracts.START_MS)),
        'round-game': (10, contracts.roundState(15000, 4, 120000)),
        'game-local-state': (54, state(10000, 101, 6, 15000)),
        'game-move-active': (2, move_flow.encode_move_notify_active(
            server_tick=10000, target_instance_id=1, active=1)),
    }
    expected = {}
    for line in ENTRY_ROWS.read_text(encoding='ascii').splitlines():
        name, command, body = line.split()
        expected[name] = (int(command), bytes.fromhex(body))
    assert actual == expected


def test_instance_actor_play_uplink_matches_original_encryption():
    from Business.scripts.codec.method3 import encode_method3_payload, method3_padding_length
    from Business.scripts.codec.protocol import encode_sh_package

    name, sequence, body, frame = ENTRY_UPLINK_ROWS.read_text(encoding='ascii').strip().split()
    assert name == 'actor-play' and bytes.fromhex(body) == bytes.fromhex('0064000100')
    clear = int(sequence).to_bytes(4, 'big') + encode_sh_package(54, 0, bytes.fromhex(body))
    cipher = encode_method3_payload(clear, bytes(16),
                                    random_padding=bytes(method3_padding_length(len(clear)) - 6))
    encoded = bytes.fromhex('550e00040000000c') + len(cipher).to_bytes(4, 'big') + cipher
    assert encoded.hex() == frame


def test_instance_startup_phases_match_original_codecs():
    from Business.scripts.codec import room_flow

    actual = {
        'game-info': (10, room_flow.encode_instance_update_game(server_time_ms=5000, instance_id=1)),
        'round-info': (10, contracts.roundInfo(5000, 120000)),
        'round-prepare': (10, contracts.roundState(5000, 2, contracts.PREPARE_MS)),
        'start-pattern': (10, contracts.startPattern()),
        'spawn-area': (10, contracts.spawnArea(5000)),
    }
    expected = {}
    for line in PHASE_ROWS.read_text(encoding='ascii').splitlines():
        name, command, body = line.split()
        expected[name] = (int(command), bytes.fromhex(body))
    assert actual == expected


def test_instance_control_responses_match_original_codecs():
    import struct
    from Business.scripts.codec import room_flow, vision_flow

    actual = {
        'instance-refresh': (10, b'\0j', contracts.instanceInfo(6000, 5000)),
        'choose-hero-transition': (10, b'\0\x0b\xfe',
                                   room_flow.encode_instance_choose_hero_message(placeholder=-2)),
        'camp-choice': (35, struct.pack('>Hi', 1, 1), struct.pack('>Hiiii', 2, 0, 1, 0, 0)),
        'vision-list': (14, struct.pack('>Hi', 5, -7),
                        vision_flow.encode_vision_list_response(sequence=-7, object_mids=(101, 202))),
    }
    expected = {}
    for line in CONTROL_ROWS.read_text(encoding='ascii').splitlines():
        name, command, request, response = line.split()
        expected[name] = (int(command), bytes.fromhex(request), bytes.fromhex(response))
    assert actual == expected


def test_instance_control_uplink_matches_original_encryption():
    from Business.scripts.codec.method3 import encode_method3_payload, method3_padding_length
    from Business.scripts.codec.protocol import encode_sh_package

    for line in UPLINK_ROWS.read_text(encoding='ascii').splitlines():
        name, sequence, body, frame = line.split()
        command = 35 if name == 'camp-choice' else 14 if name == 'vision-list' else 10
        clear = int(sequence).to_bytes(4, 'big') + encode_sh_package(command, 0, bytes.fromhex(body))
        padding = bytes(method3_padding_length(len(clear)) - 6)
        cipher = encode_method3_payload(clear, bytes(16), random_padding=padding)
        encoded = bytes.fromhex('550e00040000000c') + len(cipher).to_bytes(4, 'big') + cipher
        assert encoded.hex() == frame, name


def test_local_actor_life_states_match_original_codec():
    from Business.scripts.codec import room_flow

    for line in LIFE_ROWS.read_text(encoding='ascii').splitlines():
        name, command, rid, state, at_ms, body = line.split()
        actual = room_flow.encode_actor_update_state(
            user_id=10000, actor_mid=int(rid), actor_state=int(state), update_time_ms=int(at_ms))
        assert (int(command), actual.hex()) == (54, body), name
