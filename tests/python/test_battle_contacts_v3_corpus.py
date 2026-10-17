from pathlib import Path
import struct


ROOT = Path(__file__).resolve().parents[1] / "fixtures/protocol"


def vectors():
    old = dict(line.split() for line in (ROOT / "battle-contacts-v2.txt").read_text(encoding="utf-8").splitlines())
    result = {}
    for name, text in old.items():
        wire = bytearray.fromhex(text)
        struct.pack_into(">H", wire, 40, 3)
        if name == "contacts":
            struct.pack_into(">Q", wire, 40 + 52 + 32, 1000)
            wire += struct.pack(">Q", 1003)
        struct.pack_into(">I", wire, 0, len(wire) - 4)
        result[name] = wire.hex()
    return result


def test_v3_distinguishes_collision_source_and_target_observations():
    actual = dict(line.split() for line in (ROOT / "battle-contacts-v3.txt").read_text(encoding="utf-8").splitlines())
    assert actual == vectors()
    wire = bytes.fromhex(actual["contacts"])
    assert len(wire) == 40 + 52 + 296
    assert struct.unpack_from(">Qdd", wire, 40 + 52 + 32) == (1000, 1000.5, 999.5)
    assert struct.unpack_from(">QQ", wire, 40 + 52 + 280) == (1002, 1003)
