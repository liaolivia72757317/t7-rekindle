"""Freeze current source-owned battle data for the native resource assembler."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests/reference'), str(ROOT / 'tests/reference/Business/vendor')]

from Business.scripts import (combat_profile_data, combat_resources, damage_resources,
    damage_rules, damage_state, guard_rules, map_data, map_navigation_data,
    navmesh_resource, spawn_resource)
from Business.scripts.navigation import NavMesh

MAX_BYTES = 64 * 1024 * 1024


def encodeBaseline(value):
    data = (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                       separators=(',', ':')) + '\n').encode('utf-8')
    if len(data) > MAX_BYTES:
        raise ValueError('battle baseline exceeds size limit')
    return data


def buildBaseline():
    combat, damage = {}, {}
    for hero_id in sorted(combat_profile_data.HERO_WEAPONS):
        profiles = combat_resources.loadCombatProfiles('', hero_id)
        combat[str(hero_id)] = {str(tid): profile for tid, profile in sorted(profiles.items())}
        damage[str(hero_id)] = damage_resources.loadProfile(hero_id, profiles)
    navigation = dict(vertices=map_navigation_data.VERTICES, edges=map_navigation_data.EDGES,
                      faces=map_navigation_data.FACES)
    NavMesh.fromResource(dict(vertices=navigation['vertices'], streamingSets=[],
        edges=[dict(zip(navmesh_resource.EDGE_FIELDS, row)) for row in navigation['edges']],
        faces=[dict(zip(navmesh_resource.FACE_FIELDS, row)) for row in navigation['faces']]))
    value = dict(combatProfiles=combat, damageProfiles=damage,
        rules=dict(damage=asdict(damage_rules.DEFAULT_RULES), guard=asdict(guard_rules.DEFAULT_RULES),
                   damageBuffs=damage_state.BUFFS),
        map=dict(resourceId=map_data.RESOURCE_ID, name=map_data.MAP_NAME,
                 position=map_data.POSITION, enemyPosition=map_data.ENEMY_POSITION,
                 spawns=spawn_resource.loadSpawns(), respawns=spawn_resource.loadRespawns(),
                 navigation=navigation))
    return json.loads(encodeBaseline(value))


def writeBaseline(path, value):
    encoded = encodeBaseline(value)
    with Path(path).open('xb') as output:
        output.write(encoded)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True,
                        help='new internal baseline JSON file; existing files are not overwritten')
    args = parser.parse_args()
    value = buildBaseline()
    writeBaseline(args.output, value)
    print(json.dumps(dict(heroes=len(value['combatProfiles']), mapId=value['map']['resourceId'],
                          bytes=len(encodeBaseline(value)))))


if __name__ == '__main__':
    main()
