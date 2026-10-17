from pathlib import Path
import struct


ROOT = Path(__file__).resolve().parents[1] / 'fixtures/protocol'


def vectors():
    old = dict(line.split() for line in (ROOT / 'battle-semantic-v1.txt').read_text().splitlines()
               if line and not line.startswith('#'))
    result = {}
    for name in ('contacts', 'watermark'):
        wire = bytearray.fromhex(old[name])
        struct.pack_into('>H', wire, 40, 2)
        if name == 'contacts':
            # Collision at 999.5; the observed target phase starts later, at 1000.5.
            struct.pack_into('>d', wire, 40 + 52 + 40, 1000.5)
            wire += struct.pack('>Q', 1002)
        struct.pack_into('>I', wire, 0, len(wire) - 4)
        result[name] = wire.hex()
    return result


def test_contact_v2_layout_preserves_collision_and_observation_clocks():
    actual = dict(line.split() for line in (ROOT / 'battle-contacts-v2.txt').read_text().splitlines())
    assert actual == vectors()
    wire = bytes.fromhex(actual['contacts'])
    assert len(wire) == 40 + 52 + 288
    assert struct.unpack_from('>dd', wire, 40 + 52 + 40) == (1000.5, 999.5)
    assert struct.unpack_from('>Q', wire, 40 + 52 + 280) == (1002,)
