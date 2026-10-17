from pathlib import Path
import struct


ROOT = Path(__file__).resolve().parents[1] / "fixtures/protocol"


def vectors():
    result = {}
    for line in (ROOT / "battle-contacts-v4.txt").read_text(encoding="utf-8").splitlines():
        name, text = line.split()
        wire = bytearray.fromhex(text)
        struct.pack_into(">H", wire, 40, 5)
        if name == "contacts":
            wire[40 + 82] = 5  # Up guard, no shield proxy.
            struct.pack_into(">IBb", wire, 40 + 116, 701, 0, -1)
            wire[40 + 123] |= 8
        result[name] = wire.hex()
    return result


def test_v5_preserves_layout_and_adds_explicit_guard_volume_origin():
    actual = dict(line.split() for line in (ROOT / "battle-contacts-v5.txt").read_text(encoding="utf-8").splitlines())
    assert actual == vectors()
    wire = bytes.fromhex(actual["contacts"])
    assert len(wire) == 40 + 52 + 296
    assert struct.unpack_from(">IBb", wire, 40 + 116) == (701, 0, -1)
    assert wire[40 + 123] & 8
