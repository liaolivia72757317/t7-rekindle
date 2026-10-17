from pathlib import Path

from Business.scripts.codec import weapon_flow


ROWS = Path(__file__).resolve().parents[1] / 'fixtures/protocol/battle-shield-wire-v1.txt'


def test_shield_wire_corpus_matches_original_client_codec():
    expected = {
        'shield-health': weapon_flow.encode_shield_health(
            weapon_id=703, weapon_slot=3, health=0, maximum=100),
        'shield-break': weapon_flow.encode_shield_break(actor_id=101, weapon_id=703),
        'equipment-update': weapon_flow.encode_equipment_update(
            actor_id=101, items=[{'weaponId': 701, 'slot': 1}, {'weaponId': 703, 'slot': 3},
                                 {'weaponId': 100000, 'slot': 13}],
            equipped_weapon_id=701, offhand_id=0, now_ms=6300),
    }
    actual = {}
    for line in ROWS.read_text(encoding='ascii').splitlines():
        name, command, body = line.split()
        assert int(command) == weapon_flow.WEAPON_COMMAND
        actual[name] = bytes.fromhex(body)
    assert actual == expected
