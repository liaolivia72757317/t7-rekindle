from copy import deepcopy
from pathlib import Path

import pytest

from Business.scripts import app
from Business.scripts.codec.method3 import encode_method3_payload, method3_padding_length
from Business.scripts.codec.protocol import encode_sh_package
from Business.scripts.codec import weapon_flow
from test_runtime_movement import make_runtime_flow


FIXTURES = Path(__file__).resolve().parents[1] / 'fixtures/protocol'


def test_command_uplink_vectors_use_original_method3_codec():
    for line in (FIXTURES / 'command-adaptation-uplink-v1.txt').read_text(encoding='ascii').splitlines():
        _, sequence, command, body, expected = line.split()
        plain = int(sequence).to_bytes(4, 'big') + encode_sh_package(int(command), 0, bytes.fromhex(body))
        cipher = encode_method3_payload(plain, bytes(16),
                                       random_padding=bytes(method3_padding_length(len(plain)) - 6))
        frame = bytes.fromhex('550e00040000000c') + len(cipher).to_bytes(4, 'big') + cipher
        assert frame.hex() == expected


@pytest.mark.parametrize('role', ['login', 'logic', 'instance'])
def test_python_unknown_commands_preserve_state_and_emit_only_bounded_metadata(role):
    flow = make_runtime_flow()
    flow.session['role'] = role
    before = deepcopy(flow.state)
    for index in range(70):
        event = dict(command=65000, sequence=index + 1, serverTimeMs=0,
                     body=index.to_bytes(2, 'big') + b'private-content')
        app.handleMessage(flow, event)
        app.handleMessage(flow, event)
    assert flow.result['send'] == []
    assert len(flow.result['logs']) == 64
    assert len(flow.session.pop('unhandled')) == 64
    assert flow.state == before
    assert all('private-content' not in log['message'] for log in flow.result['logs'])


def test_weapon_reply_vectors_match_original_codec():
    items = [dict(slot=1, weaponId=701), dict(slot=2, weaponId=703), dict(slot=13, weaponId=100000)]
    expected = {
        'fist-result': weapon_flow.encode_change_response(0, 100000),
        'busy-result': weapon_flow.encode_change_response(620000, 100000),
        'invalid-result': weapon_flow.encode_change_response(620001, 100000),
        'main-result': weapon_flow.encode_change_response(0, 701, 703),
        'fist-equipment': weapon_flow.encode_equipment_update(actor_id=101, items=items,
            equipped_weapon_id=100000, now_ms=6000),
        'main-equipment': weapon_flow.encode_equipment_update(actor_id=101, items=items,
            equipped_weapon_id=701, offhand_id=703, now_ms=6000),
    }
    actual = dict(line.split() for line in (FIXTURES / 'weapon-change-wire-v1.txt').read_text(encoding='ascii').splitlines())
    assert actual == {name: body.hex() for name, body in expected.items()}
