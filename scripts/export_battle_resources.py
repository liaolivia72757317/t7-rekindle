"""Export the current battle rules and selected client catalogue to a portable JSON bundle."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests/reference'), str(ROOT / 'tests/reference/Business/vendor')]

from Business.scripts import (bot_names, client_resources, combat_resources, damage_resources,
    damage_rules, damage_state, guard_rules, hero_resources, map_data, map_navigation_data,
    navmesh_resource, spawn_resource)
from Business.scripts.navigation import NavMesh

SCHEMA_VERSION = 1
MAX_BYTES = 64 * 1024 * 1024


def encodeBundle(value):
    data = (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')
    if len(data) > MAX_BYTES:
        raise ValueError('battle resource bundle exceeds size limit')
    return data


def buildBundle(clientDirectory, rulesVersion, resourceVersion, clientVersion):
    if any(type(value) is not int or not 0 < value <= 0xffffffff for value in (rulesVersion, resourceVersion)):
        raise ValueError('rules/resource version must be a positive u32')
    if type(clientVersion) is not str or re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', clientVersion) is None:
        raise ValueError('client version must be a portable identifier')
    directory = Path(clientDirectory)
    if not directory.is_absolute():
        raise ValueError('selected absolute Bin directory required')
    client_resources.checkResources(str(directory))
    catalogue = hero_resources.loadCatalogue(str(directory))
    combat, damage = {}, {}
    for heroId, hero in sorted(catalogue.heroes.items()):
        ids = {hero_resources.FIST_ID, *(tid for group in hero.groups for tid in group if tid)}
        profiles = combat_resources.loadCombatProfiles(str(directory), heroId)
        combat[str(heroId)] = {str(tid): profiles[tid] for tid in sorted(ids)}
        if any(not combat_resources.validProfile(profile) for profile in combat[str(heroId)].values()):
            raise ValueError('invalid combat profile')
        damage[str(heroId)] = damage_resources.loadProfile(heroId, ids)
    navigation = dict(vertices=map_navigation_data.VERTICES, edges=map_navigation_data.EDGES,
                      faces=map_navigation_data.FACES)
    NavMesh.fromResource(dict(vertices=navigation['vertices'], streamingSets=[],
        edges=[dict(zip(navmesh_resource.EDGE_FIELDS, row)) for row in navigation['edges']],
        faces=[dict(zip(navmesh_resource.FACE_FIELDS, row)) for row in navigation['faces']]))
    value = dict(schemaVersion=SCHEMA_VERSION, rulesVersion=rulesVersion, resourceVersion=resourceVersion,
        clientVersion=clientVersion, catalogue=dict(
            heroes=[asdict(hero) for _, hero in sorted(catalogue.heroes.items())],
            weapons=[asdict(weapon) for _, weapon in sorted(catalogue.weapons.items())]),
        combatProfiles=combat, damageProfiles=damage,
        rules=dict(damage=asdict(damage_rules.DEFAULT_RULES), guard=asdict(guard_rules.DEFAULT_RULES),
                   damageBuffs=damage_state.BUFFS),
        botNames=bot_names.loadNames(str(directory)),
        map=dict(resourceId=map_data.RESOURCE_ID, name=map_data.MAP_NAME,
                 position=map_data.POSITION, enemyPosition=map_data.ENEMY_POSITION,
                 spawns=spawn_resource.loadSpawns(str(directory)),
                 respawns=spawn_resource.loadRespawns(str(directory)), navigation=navigation))
    # Normalize integer keys and tuples to the same values the Rust reader receives.
    return json.loads(encodeBundle(value))


def writeBundle(path, value):
    encoded = encodeBundle(value)
    with Path(path).open('xb') as output:
        output.write(encoded)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client-directory', type=Path, required=True, help='selected absolute Bin directory, read only')
    parser.add_argument('--output', type=Path, required=True, help='new JSON file; existing files are not overwritten')
    parser.add_argument('--rules-version', type=int, required=True)
    parser.add_argument('--resource-version', type=int, required=True)
    parser.add_argument('--client-version', required=True, help='compatibility identifier, not a filesystem path')
    args = parser.parse_args()
    value = buildBundle(args.client_directory, args.rules_version, args.resource_version, args.client_version)
    writeBundle(args.output, value)
    print(json.dumps(dict(schemaVersion=SCHEMA_VERSION, mapId=value['map']['resourceId'],
                          heroes=len(value['catalogue']['heroes']), bytes=len(encodeBundle(value)))))


if __name__ == '__main__':
    main()
