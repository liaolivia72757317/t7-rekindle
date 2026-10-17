from pathlib import Path
import struct


ROOT = Path(__file__).resolve().parents[1] / "fixtures/protocol"


def vectors():
    result = {}
    for line in (ROOT / "battle-contacts-v3.txt").read_text(encoding="utf-8").splitlines():
        name, text = line.split()
        wire = bytearray.fromhex(text)
        struct.pack_into(">H", wire, 40, 4)
        if name == "contacts":
            struct.pack_into(">d", wire, 40 + 52 + 40, -4999.0)
        result[name] = wire.hex()
    return result


def test_v4_preserves_prestart_target_phase_without_changing_v3_layout():
    actual = dict(line.split() for line in (ROOT / "battle-contacts-v4.txt").read_text(encoding="utf-8").splitlines())
    assert actual == vectors()
    wire = bytes.fromhex(actual["contacts"])
    assert len(wire) == 40 + 52 + 296
    assert struct.unpack_from(">Qdd", wire, 40 + 52 + 32) == (1000, -4999.0, 999.5)
    assert struct.unpack_from(">QQ", wire, 40 + 52 + 280) == (1002, 1003)
