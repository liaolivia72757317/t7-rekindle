from pathlib import Path
import struct

from Business.scripts import contracts
from Business.scripts.codec import room_flow
from Business.scripts.codec.method3 import encode_method3_payload, method3_padding_length


ROWS = Path(__file__).resolve().parents[1] / 'fixtures/protocol/room-entry-wire-v1.txt'
UPLINK_ROWS = Path(__file__).resolve().parents[1] / 'fixtures/protocol/room-entry-uplink-v1.txt'
REMATCH_ROWS = Path(__file__).resolve().parents[1] / 'fixtures/protocol/room-rematch-uplink-v1.txt'
LEAVE_ROWS = Path(__file__).resolve().parents[1] / 'fixtures/protocol/leave-wire-v1.txt'


def test_room_entry_corpus_matches_existing_business_codecs():
    name = b'RoomName1'
    create = struct.pack('>HI', 1, len(name) + 1) + name + b'\0' + struct.pack('>i', 1028)
    match = struct.pack('>HiHiiiiIIII', 1, 0, 1, 1, 0, 0, 0, 1, 1028, 0, 0) + b'\0'
    expected = [
        ('create-request', create),
        ('create-result', room_flow.encode_room_create_response(
            result=0, room_id=1, room_name=name, resource_id=1028)),
        ('enter-result', room_flow.encode_room_enter_response(
            room_id=1, result=0, is_reconnect=0, room_name=name, resource_id=1028,
            sync_url=b'127.0.0.1:21003', prefer=0)),
        ('enter-request', room_flow.encode_room_enter_request(room_id=1)),
        ('match-request', match),
        ('match-result', contracts.matchStart(match)),
        ('match-notify', struct.pack('>Hi', 3, 0)),
        ('instance-notify', room_flow.encode_room_enter_instance_notify(
            room_id=1, user_camp=1, notify_type=0)),
    ]
    actual = [tuple(line.split()) for line in ROWS.read_text(encoding='ascii').splitlines()
              if line and not line.startswith('#')]
    assert actual == [(key, body.hex()) for key, body in expected]


def test_room_entry_uplink_corpus_uses_original_method3_codec():
    bodies = {name: bytes.fromhex(value) for name, value in
              (line.split() for line in ROWS.read_text(encoding='ascii').splitlines()
               if line and not line.startswith('#'))}
    vectors = [('create', 1, 0x1e, bodies['create-request']),
               ('match', 2, 0x20, bodies['match-request']),
               ('enter', 1, 0x1e, bodies['enter-request'])]
    expected = []
    for name, sequence, command, body in vectors:
        plain = struct.pack('>IHQ', sequence, command, 0) + body
        padding = bytes(range(method3_padding_length(len(plain)) - 6))
        encoded = encode_method3_payload(plain, bytes(16), random_padding=padding)
        frame = bytes.fromhex('550e00040000000c') + struct.pack('>I', len(encoded)) + encoded
        expected.append((name, str(command), str(sequence), frame.hex()))
    actual = [tuple(line.split()) for line in UPLINK_ROWS.read_text(encoding='ascii').splitlines()
              if line and not line.startswith('#')]
    assert actual == expected


def test_room_rematch_uplink_keeps_monotonic_logic_sequences():
    room = dict(line.split() for line in ROWS.read_text(encoding='ascii').splitlines()
                if line and not line.startswith('#'))
    leave = dict(line.split() for line in LEAVE_ROWS.read_text(encoding='ascii').splitlines())
    vectors = [('quit', 3, 0x37, bytes.fromhex(leave['user-group-quit-request'])),
               ('enter', 4, 0x1e, bytes.fromhex(room['enter-request'])),
               ('match', 5, 0x20, bytes.fromhex(room['match-request']))]
    expected = []
    for name, sequence, command, body in vectors:
        plain = struct.pack('>IHQ', sequence, command, 0) + body
        padding = bytes(method3_padding_length(len(plain)) - 6)
        encoded = encode_method3_payload(plain, bytes(16), random_padding=padding)
        frame = bytes.fromhex('550e00040000000c') + struct.pack('>I', len(encoded)) + encoded
        expected.append((name, str(sequence), str(command), frame.hex()))
    actual = [tuple(line.split()) for line in REMATCH_ROWS.read_text(encoding='ascii').splitlines()]
    assert actual == expected


def test_match_map_corpus_uses_native_disjoint_checked_and_unchecked_arrays():
    maps = {20001, 1003, 1030, 1021, 20011, 1001, 6003, 20013, 20015, 90003, 1034, 20012}
    hidden = {1013, 1018}
    for row in ROWS.with_name('match-maps-v1.txt').read_text(encoding='ascii').splitlines():
        if row.startswith('#'):
            continue
        name, body, wire = row.split()
        body = bytes.fromhex(body)
        checked_count = struct.unpack_from('>I', body, 24)[0]
        checked = set(struct.unpack_from(f'>{checked_count}I', body, 28))
        offset = 28 + checked_count * 4
        unchecked_count = struct.unpack_from('>I', body, offset)[0]
        unchecked = set(struct.unpack_from(f'>{unchecked_count}I', body, offset + 4))
        pool = {1003, 1030, 1021, 1001, 6003, 1034} if name == 'team' else maps | hidden
        assert checked.isdisjoint(unchecked) and checked | unchecked == pool
        if name != 'team':
            assert hidden <= checked
        visible = checked - hidden
        assert len(visible) == {'single': 1, 'subset': 2, 'all': 12, 'empty': 0, 'jieqiao': 1, 'team': 6}[name]
        assert checked_count == len(checked)
        if name == 'jieqiao':
            assert visible == {90003}
        plain = struct.pack('>IHQ', 1, 0x20, 0) + body
        encoded = encode_method3_payload(plain, bytes(16),
            random_padding=bytes(method3_padding_length(len(plain)) - 6))
        frame = bytes.fromhex('550e00040000000c') + struct.pack('>I', len(encoded)) + encoded
        assert frame.hex() == wire
