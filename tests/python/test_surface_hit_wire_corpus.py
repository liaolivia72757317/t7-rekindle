from pathlib import Path

from Business.scripts.codec import battle_flow


ROWS = Path(__file__).resolve().parents[1] / "fixtures/protocol/surface-hit-wire-v1.txt"


def triplet(value):
    return tuple(float(part) for part in value.split(","))


def test_ground_and_terrain_hit_points_match_original_client_codec():
    for line in ROWS.read_text(encoding="ascii").splitlines():
        name, actor, rid, weapon, occurred, position, direction, normal, material, body = line.split()
        assert name in ("ground", "terrain") and int(actor) in (1, 2)
        actual = battle_flow.encode_battle_hit_point_notify(
            hit_pos=triplet(position), hit_direction=triplet(direction), hit_normal=triplet(normal),
            material_id=int(material), ignore_hurt=True, attacker_mid=int(rid), target_mid=0,
            attacker_weapon_id=int(weapon), target_weapon_id=0,
            server_time_ms=int(float(occurred)) + 5000)
        assert actual.hex() == body
