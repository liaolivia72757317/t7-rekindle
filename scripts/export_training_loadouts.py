"""Export native melee training heroes, equipment and gate-side entry points."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import struct
import xml.etree.ElementTree as ET

from export_training_maps import placement, resourceText, readEntry, canonicalPath
from export_combat_profiles import attackDirections, applyDirections, applyTimings, hurtTiming
from export_combat_profiles import combat, hero_resources, AnimationTimings, KEY_MSG_PATH
from Business.scripts import damage_resources, damage_data, combat_profile_data

MODES = {6006: 10001, 6009: 10002}


def export(directory):
    archive = directory.parent / 'vfs/data1.vfs'
    table = lambda path, width: hero_resources.uniqueRows(readEntry(archive, path), width)
    heroes = table(hero_resources.HERO_PATH, 7286)
    groups = table(hero_resources.GROUP_PATH, 1434)
    instances = table('data/table/s_res_instance_cfg_cli.bin', 2997)
    scenes = table('data/table/s_res_map_cli.bin', 292)
    catalogue = hero_resources.loadCatalogue(str(directory))
    allowed = attackDirections(readEntry(archive, hero_resources.WEAPON_PATH),
                               readEntry(archive, KEY_MSG_PATH))
    result = dict(schemaVersion=1, modes={}, catalogue=dict(heroes=[], weapons=[]),
                  combatProfiles={}, damageProfiles={})
    animations, selected = {}, {}
    hurtMs = hurtTiming(str(directory))
    for mapId, heroId in MODES.items():
        row = heroes[heroId]
        if struct.unpack_from('<H', row, 4209)[0] != 8:
            raise ValueError('invalid training weapon group count')
        native, groupIds, defaults = [], [], []
        for index in range(8):
            groupId, weaponId, _ = struct.unpack_from('<III', row, 4211 + index * 12)
            values = [0] * 6
            if groupId:
                group = groups[groupId]
                count = struct.unpack_from('<H', group, 64)[0]
                if count > 6:
                    raise ValueError('invalid training weapon group capacity')
                for i in range(count):
                    values[i] = struct.unpack_from('<I', group, 66 + i * 228)[0]
                defaults.append((index, values.index(weaponId)))
            groupIds.append(groupId)
            native.append(values)
        hero = dict(resourceId=heroId, name=resourceText(row, 4, 30), gender=row[34],
                    groups=native, nativeGroups=native, groupIds=groupIds, defaults=defaults)
        result['catalogue']['heroes'].append(hero)
        equipment = [dict(slot=i + 1, weaponId=native[g][s]) for i, (g, s) in enumerate(defaults)]
        weaponId = next(item['weaponId'] for item in equipment
                        if not catalogue.weapons[item['weaponId']].shield)
        selected[heroId] = {100000, *(value for group in native for value in group if value)}
        profiles = {}
        for tid in sorted(selected[heroId]):
            profile = applyDirections(combat.weaponProfile(str(directory), tid, hero['gender']), allowed[tid])
            if not profile['shield']:
                path = profile['movementAnimation']['characterPath']
                if path not in animations:
                    animations[path] = AnimationTimings(str(directory), path)
                profile = applyTimings(profile, animations[path], hurtMs)
            profiles[str(tid)] = profile
        result['combatProfiles'][str(heroId)] = profiles
        scene = scenes[struct.unpack_from('<I', instances[mapId], 148)[0]]
        overlay = canonicalPath(resourceText(scene, 132, 128)).rsplit('/', 1)[0]
        def xml(name):
            return ET.fromstring(readEntry(directory.parent / 'vfs/map.vfs',
                                          overlay + '/' + name).decode('gbk'))
        entry = next(placement(group.find('points')) for group in xml('dactorspawngroup.dat')
                     if all(group.find(flag).get('value') == '1'
                            for flag in ('is_enable', 'is_red', 'is_infantry', 'is_born')))
        returned = next(placement(point) for point in xml('dserverpoint.dat').findall('SH_RES_POINT')
                        if point.find('id').get('value') == '3001')
        result['modes'][str(mapId)] = dict(heroId=heroId, weaponId=weaponId, equipment=equipment,
                                          entrySpawn=entry, returnSpawn=returned)
    result['catalogue']['weapons'] = [asdict(catalogue.weapons[tid])
                                      for tid in sorted(set.union(*selected.values()))]
    # Reuse the damage exporter conversion with these source-table rows only.
    weapons, heroRows, actions = damage_resources.extract(str(directory), selected)
    old = damage_data.WEAPONS, damage_data.HEROES, damage_data.ACTIONS, combat_profile_data.HERO_WEAPONS
    try:
        damage_data.WEAPONS, damage_data.HEROES, damage_data.ACTIONS = weapons, heroRows, actions
        combat_profile_data.HERO_WEAPONS = selected
        result['damageProfiles'] = {str(h): damage_resources.loadProfile(h, ids)
                                    for h, ids in selected.items()}
    finally:
        damage_data.WEAPONS, damage_data.HEROES, damage_data.ACTIONS, combat_profile_data.HERO_WEAPONS = old
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client-directory', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.client_directory.is_absolute():
        parser.error('--client-directory must be absolute')
    value = export(args.client_directory)
    with args.output.open('x', encoding='utf-8', newline='\n') as output:
        json.dump(value, output, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        output.write('\n')
