from pathlib import Path

from Business.scripts.codec import battle_flow


ROWS = Path(__file__).resolve().parents[1] / 'fixtures/protocol/battle-parry-wire-v1.txt'


def test_parry_feedback_corpus_matches_original_client_codec():
    expected = {
        'parry-hit': battle_flow.encode_battle_hit_point_notify(
            hit_pos=(1.5, 20., .5), hit_direction=(1., 0., 0.), hit_normal=(0., 0., 1.),
            material_id=0, ignore_hurt=True, attacker_mid=101, target_mid=202,
            attacker_weapon_id=701, target_weapon_id=703, server_time_ms=6250),
        'parry-sound': battle_flow.encode_battle_defense_notify(
            attacker_mid=101, defender_mid=202, attacker_weapon_id=701,
            target_weapon_id=703, server_time_ms=6250),
    }
    actual = {}
    for line in ROWS.read_text(encoding='ascii').splitlines():
        name, command, body = line.split()
        assert int(command) == battle_flow.BATTLE_COMMAND
        actual[name] = bytes.fromhex(body)
    assert actual == expected
