"""Export the two melee training scenes without adding them to matchmaking."""
import argparse
import json
import math
from pathlib import Path
import struct
import xml.etree.ElementTree as ET

from export_match_maps import resourceText, rows, readEntry, canonicalPath
from export_match_maps import TagReader, EDGE_FIELDS, FACE_FIELDS, NavMesh, battlefieldPlacements

MAP_IDS = (6006, 6009)


def placement(node):
    position, direction = [tuple(map(float, node.find(key).get('value').split(',')))
                           for key in ('pos', 'dir')]
    return position, round(math.degrees(math.atan2(-direction[1], direction[0])))


def exportMaps(vfs):
    archive = vfs / 'data1.vfs'
    instances = {struct.unpack_from('<I', row)[0]: row for row in
                 rows(readEntry(archive, 'data/table/s_res_instance_cfg_cli.bin'), 2997)}
    scenes = {struct.unpack_from('<I', row)[0]: row for row in
              rows(readEntry(archive, 'data/table/s_res_map_cli.bin'), 292)}
    result = []
    for mapId in MAP_IDS:
        instance = instances[mapId]
        scene = scenes[struct.unpack_from('<I', instance, 148)[0]]
        geometry = canonicalPath(resourceText(scene, 4, 128)).rsplit('/', 1)[0]
        overlay = canonicalPath(resourceText(scene, 132, 128)).rsplit('/', 1)[0]
        raw = TagReader(readEntry(vfs / 'map.vfs', geometry + '/anavmesh.dat')).document()
        groups = ET.fromstring(readEntry(vfs / 'map.vfs', overlay + '/dactorspawngroup.dat').decode('gbk'))
        player = next(placement(group.find('points')) for group in groups
                      if all(group.find(flag).get('value') == '1'
                             for flag in ('is_enable', 'is_red', 'is_infantry', 'is_born')))
        points = ET.fromstring(readEntry(vfs / 'map.vfs', overlay + '/dserverpoint.dat').decode('gbk'))
        opponent = next(placement(point) for point in points.findall('SH_RES_POINT')
                        if point.find('id').get('value') == '3000')
        nav = NavMesh.fromResource(raw)
        # These are combat/AI placements, not the player's initial gate-side entry.
        # Native entry and return points are exported separately with the mode loadout.
        references = {1: [opponent], 2: [player]}
        spawns, respawns = battlefieldPlacements(nav, references, references)
        result.append(dict(resourceId=mapId, name=resourceText(instance, 8, 64),
            position=spawns[1][0][0], enemyPosition=spawns[2][0][0], spawns=spawns, respawns=respawns,
            navigation=dict(vertices=[v[:3] for v in raw['vertices']],
                edges=[[e[k] for k in EDGE_FIELDS] for e in raw['edges']],
                faces=[[f[k] for k in FACE_FIELDS] for f in raw['faces']])))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client-directory', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.client_directory.is_absolute():
        parser.error('--client-directory must be absolute')
    maps = exportMaps(args.client_directory.parent / 'vfs')
    with args.output.open('x', encoding='utf-8', newline='\n') as output:
        json.dump(maps, output, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        output.write('\n')
