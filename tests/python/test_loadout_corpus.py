"""Frozen lobby traffic from the existing Python profile and wire implementation."""
from pathlib import Path
import struct

from Business.scripts import player_profile
from Business.scripts.codec import loadout_flow as wire, login_flow
from Business.scripts.codec.method3 import encode_method3_payload, method3_padding_length
from loadout_fixture import catalogue

CORPUS = Path(__file__).resolve().parents[1] / 'fixtures/protocol/loadout-v1.txt'


def vectors():
    cat = catalogue()
    profile = player_profile.create(cat)
    result = []

    def add(name, command, body, replies):
        plain = struct.pack('>IHQ', 1, command, 0) + body
        encoded = encode_method3_payload(plain, bytes(16),
            random_padding=bytes(range(method3_padding_length(len(plain)) - 6)))
        incoming = bytes.fromhex('550e00040000000c') + struct.pack('>I', len(encoded)) + encoded
        result.append((name, str(command), body.hex(), incoming.hex(),
                       ','.join(f'{cmd}:{data.hex()}' for cmd, data in replies)))

    def formation(ids):
        return struct.pack('>Hii', 5, 1, len(ids)) + b''.join(wire.reference(cat, tid) for tid in ids)

    def change(name, body, *, ids=None, hero=None, slots=None):
        nonlocal profile
        previous = profile
        profile = player_profile.change(profile, cat, formation=ids, heroId=hero, slots=slots)
        replies = [(0x22, wire.result(5 if ids is not None else 14))]
        if hero is not None:
            replies.append((0x22, wire.weaponNotify(profile, cat, hero)))
        changed = {hero} if hero is not None else set(previous['formation']).symmetric_difference(profile['formation'])
        replies.extend((0x28, wire.heroUpdate(profile, cat, tid)) for tid in sorted(changed))
        replies.extend((0x28, data) for data in wire.containers(profile, cat, 2, 0))
        replies.append((0x22, wire.formations(profile, cat)))
        add(name, 0x22, body, replies)

    for kind in (1, 2, 3):
        replies = wire.containers(profile, cat, kind, -17) if kind < 3 else [login_flow.encode_empty_item_container_response(kind, -17)]
        add(f'container-{kind}', 0x28, struct.pack('>Hii', 1, kind, -17), [(0x28, data) for data in replies])
    add('formations', 0x22, bytes.fromhex('0003ff'), [(0x22, wire.formations(profile, cat))])
    for number in (1, 2):
        add(f'select-{number}', 0x22, struct.pack('>Hi', 8, number), [(0x22, wire.result(8, 0 if number == 1 else wire.INVALID))])
    change('formation-change', formation([2102, 1101]), ids=[2102, 1101])
    change('formation-noop', formation([2102, 1101]), ids=[2102, 1101])
    equipment = struct.pack('>HQihiii', 14, 2102, 2, 1, 1, 902, 0)
    change('weapons-change', equipment, hero=2102, slots=[dict(slot=1, group=1, index=0)])
    for name, body, selector in (
            ('duplicate-formation', formation([1101, 1101]), 5),
            ('invalid-reference', struct.pack('>HiiQi', 5, 1, 1, 2102, 1), 5),
            ('invalid-slot', struct.pack('>HQihiii', 14, 2102, 2, 1, 5, 902, 0), 14),
            ('duplicate-weapon', struct.pack('>HQih6i', 14, 2102, 2, 2, 1, 902, 0, 2, 902, 0), 14),
            ('truncated-formation', b'\x00\x05', 5)):
        add(name, 0x22, body, [(0x22, wire.result(selector, wire.INVALID))])
    change('formation-empty', formation([]), ids=[])
    add('save-failed', 0x22, formation([1101]), [(0x22, wire.result(5, wire.SAVE_FAILED))])
    add('after-failure', 0x22, bytes.fromhex('000300'), [(0x22, wire.formations(profile, cat))])
    change('save-retry', formation([1101]), ids=[1101])
    change('weapons-empty', struct.pack('>HQih9i', 14, 1101, 1, 3, 1, 0, -1, 2, 0, 0, 0, 0, 0), hero=1101, slots=[])
    return result


def test_loadout_vectors_match_existing_business_codecs():
    actual = [tuple(line.split()) for line in CORPUS.read_text(encoding='ascii').splitlines() if line and not line.startswith('#')]
    assert actual == vectors()
