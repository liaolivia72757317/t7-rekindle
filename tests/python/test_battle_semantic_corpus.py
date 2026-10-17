import struct
from pathlib import Path


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "protocol" / "battle-semantic-v1.txt"


def test_battle_semantic_v1_vectors_have_fixed_identity_and_layout():
    vectors = {}
    for line in FIXTURE.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        name, hex_bytes = line.split(" ", 1)
        wire = bytes.fromhex(hex_bytes)
        (length,) = struct.unpack_from(">I", wire)
        assert length == len(wire) - 4
        version, kind, channel, reserved, room_id, match_id, player_id, sequence = struct.unpack_from(
            ">BBBBQQQQ", wire, 4
        )
        assert (version, channel, reserved, room_id, match_id, player_id, sequence) == (1, 0, 0, 2, 3, 4, 1)
        vectors[name] = (kind, wire[40:])

    kind, world = vectors["world"]
    assert kind == 6
    assert struct.unpack_from(">HHQQQQHH", world) == (1, 0, 7, 9, 1, 1000, 1, 0)
    assert struct.unpack_from(">IHHQddddIB3s", world, 40) == (
        1, 1, 2, 3, 1.5, -2.0, 0.0, 90.0, 2, 2, b"\0" * 3
    )
    assert len(world) == 96

    kind, intent = vectors["intent"]
    assert kind == 11 and len(intent) == 44
    assert struct.unpack(">HHQQQQIHBB", intent) == (1, 0, 7, 9, 1, 1000, 1, 1, 1, 5)

    kind, abort = vectors["abort"]
    assert kind == 12 and len(abort) == 20
    assert struct.unpack(">HBBQQ", abort) == (1, 1, 0, 7, 1001)

    kind, ended = vectors["ended"]
    assert kind == 8 and len(ended) == 68
    assert struct.unpack_from(">HBBQQQIIHH", ended) == (1, 2, 1, 7, 0, 1001, 10, 8, 1, 0)
    assert struct.unpack_from(">IBBHIIIII", ended, 40) == (1, 1, 1, 0, 1, 3, 1, 5, 2)

    for name, batch_id, attempted_at in (("motion_false", 1, 0), ("motion_true", 2, 990)):
        kind, motion = vectors[name]
        assert kind == 13 and len(motion) == 100
        assert struct.unpack_from(">HHQQQQ", motion) == (1, 0, 7, 9, batch_id, 1000)
        assert struct.unpack_from(">QIHHQ4dQ", motion, 36) == (
            1, 1, 1, 0, 3, 1.5, -2.0, 0.0, 90.0, attempted_at
        )

    kind, contacts = vectors["contacts"]
    assert kind == 14 and len(contacts) == 332
    assert struct.unpack_from(">HHQQQQQIHH", contacts) == (1, 0, 7, 9, 1, 1000, 1, 0, 1, 0)
    assert struct.unpack_from(">IIHHQQHBBQdddIBbbB", contacts, 52) == (
        1, 2, 1, 2, 3, 4, 2, 5, 2, 900, 950.0, 999.5, 0.5, 0, 2, 0, -1, 1
    )
    assert struct.unpack_from(">3d", contacts, 52 + 216) == (0.0, 0.0, 1.0)

    kind, watermark = vectors["watermark"]
    assert kind == 14 and len(watermark) == 52
    assert struct.unpack(">HHQQQQQIHH", watermark) == (1, 0, 7, 9, 2, 1001, 1, 0, 0, 0)

    kind, snapshot = vectors["state_snapshot"]
    assert kind == 7 and len(snapshot) == 344
    assert struct.unpack_from(">HBBQQQQHH", snapshot) == (1, 2, 0, 7, 1, 0, 1000, 2, 0)
    assert struct.unpack_from(">I6B3H2Id", snapshot, 40) == (
        1, 1, 1, 1, 0, 0, 0, 1, 0, 0, 100, 100, 50.0
    )
    assert struct.unpack_from(">IB3sII", snapshot, 40 + 112) == (200, 1, b"\0" * 3, 300, 60)
    assert struct.unpack_from(">I6B", snapshot, 192) == (2, 2, 2, 2, 0, 0, 0)

    kind, delta = vectors["state_delta"]
    assert kind == 7 and len(delta) == 196
    assert struct.unpack_from(">HBBQQQQHH", delta) == (1, 1, 0, 7, 2, 1, 1100, 1, 0)
    assert delta[40:44] == b"\x01\0\0\0"
    assert struct.unpack_from(">I6B3H2Id", delta, 44) == (
        1, 1, 1, 1, 1, 3, 0, 3, 400, 0, 100, 90, 40.0
    )
    assert struct.unpack_from(">IB3sII", delta, 44 + 112) == (200, 1, b"\0" * 3, 300, 50)
