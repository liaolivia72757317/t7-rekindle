"""Read-only export of the twelve small-battlefield maps for local team deathmatch."""
import argparse
import json
import math
from pathlib import Path
import struct
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests/reference'), str(ROOT / 'tests/reference/Business/vendor')]
from Business.scripts.dash_resources import rows
from Business.scripts.navmesh_resource import TagReader, EDGE_FIELDS, FACE_FIELDS
from Business.scripts.navigation import NavMesh, height, EPSILON
from Business.scripts.body_collision import DIAMETER
from Business.scripts.spawn_navigation import candidates, SPACING, inset, nearest
from Business.scripts.vfs_resource import readEntry, canonicalPath

# Jieqiao's matchmaking instance is 90003; 1033 is another mode on the same scene.
MAP_IDS = (20001, 1003, 1030, 1021, 20011, 1001, 6003, 20013, 20015, 90003, 1034, 20012)


def resourceText(row, offset, size):
    raw = bytes(row[offset:offset + size])
    if b'\0' not in raw:
        raise ValueError('unterminated map resource string')
    return raw.split(b'\0', 1)[0].decode('gbk')


def placements(data, flagName):
    root = ET.fromstring(data.decode('gbk'))
    if root.tag != 'SH_RES_ACTOR_SPAWN_GROUP_TAB':
        raise ValueError('unexpected spawn resource root')
    camps = {1: [], 2: []}
    for group in root:
        def flag(name):
            value = group.find(name)
            if value is None or value.get('value') not in ('0', '1'):
                raise ValueError('invalid spawn flag: ' + name)
            return value.get('value') == '1'
        # A shared infantry/cavalry point is still an original infantry point.
        if not (flag('is_enable') and flag('is_infantry') and flag(flagName)):
            continue
        blue, red = flag('is_blue'), flag('is_red')
        if blue == red:
            raise ValueError('spawn requires exactly one camp')
        points = group.findall('points')
        if len(points) != int(group.find('point_num').get('value')):
            raise ValueError('spawn point count mismatch')
        for point in points:
            position, direction = [tuple(float(v) for v in point.find(name).get('value').split(','))
                                   for name in ('pos', 'dir')]
            if any(len(v) != 3 or any(not math.isfinite(x) or abs(x) >= 100000 for x in v)
                   for v in (position, direction)) or math.hypot(*direction[:2]) < .001:
                raise ValueError('invalid map placement')
            heading = round(math.degrees(math.atan2(-direction[1], direction[0])))
            camp = camps[1 if blue else 2]
            if position not in [p for p, _ in camp]:
                camp.append((position, heading))
    if any(not points for points in camps.values()):
        raise ValueError('map has no infantry placements for one camp')
    return camps


def battlefieldPlacements(nav, born, revive):
    if sum(map(len, born.values())) > 60:
        raise ValueError("native roster exceeds actor capacity")
    remaining = {i for i, polygon in enumerate(nav.polygons) if polygon}
    components = []
    while remaining:
        start = min(remaining)
        found, pending = {start}, [start]
        while pending:
            for neighbor, a, b in nav.links[pending.pop()]:
                if neighbor not in found and math.dist(a[:2], b[:2]) > DIAMETER + EPSILON:
                    found.add(neighbor)
                    pending.append(neighbor)
        remaining.difference_update(found)
        components.append(found)

    def project(point, faces, interiors):
        if nav.locate(point) in faces:
            return point
        choices = []
        for index, polygon in interiors.items():
            xy = nearest(polygon, point)
            if xy is not None:
                z = height(nav.polygons[index], xy)
                if z is not None:
                    choices.append((*xy, z + .2))
        return min(choices, key=lambda p: (math.dist(p, point), p)) if choices else None

    # Siege platforms and ships can be separate navigation islands. For team
    # deathmatch use shared reachable ground closest to the original camps.
    options = []
    for faces in components:
        interiors = {i: polygon for i in sorted(faces) if (polygon := inset(nav.polygons[i]))}
        if not interiors:
            continue
        anchors = [project(born[camp][0][0], faces, interiors) for camp in (1, 2)]
        distance = sum(math.dist(anchors[camp - 1], born[camp][0][0]) for camp in (1, 2))
        options.append((distance, min(faces), faces, interiors))
    for _, _, faces, interiors in sorted(options):
        starts, respawns, occupied, revived = {}, {}, [], []
        for camp in (1, 2):
            required = len(born[camp])
            references = []
            for position, heading in born[camp] + revive[camp]:
                point = project(position, faces, interiors)
                if point is not None and math.dist(point[:2], position[:2]) <= 32:
                    references.append((point, heading))
            selected = []
            def reachable(point):
                anchor = starts[1][0][0] if starts else (selected[0][0] if selected else point)
                return (nav.findPath(anchor, point).status == 'ready'
                        and nav.findPath(point, anchor).status == 'ready')
            for point in references:
                if reachable(point[0]) and all(math.dist(point[0][:2], other[0][:2]) >= SPACING for other in selected + occupied):
                    selected.append(point)
                if len(selected) == required:
                    break
            if not selected:
                break
            while len(selected) < required:
                before = len(selected)
                for group in candidates(nav, tuple(selected), selected[0][0]):
                    for point in group:
                        if (reachable(point[0])
                                and any(math.dist(point[0][:2], p[:2]) <= 32 for p, _ in born[camp] + revive[camp])
                                and all(math.dist(point[0][:2], other[0][:2]) >= SPACING for other in selected + occupied)):
                            selected.append(point)
                            if len(selected) == required:
                                break
                    if len(selected) == required:
                        break
                if len(selected) == before:
                    break
            if len(selected) != required:
                break
            starts[camp] = selected
            occupied.extend(selected)
            # The same reachable ground is used after death, not an isolated
            # original siege platform. Keep the camp's original revive order.
            respawns[camp] = []
            for position, heading in revive[camp]:
                point = project(position, faces, interiors)
                if point is not None and math.dist(point[:2], position[:2]) <= 32:
                    if reachable(point) and all(math.dist(point[:2], p[:2]) >= SPACING for p, _ in respawns[camp] + revived):
                        respawns[camp].append((point, heading))
            if not respawns[camp]:
                respawns[camp] = [point for point in selected
                    if all(math.dist(point[0][:2], p[:2]) >= SPACING for p, _ in revived)]
            if not respawns[camp]:
                starts.pop(camp)
                break
            revived.extend(respawns[camp])
        if len(starts) == 2 and nav.findPath(starts[1][0][0], starts[2][0][0]).status == 'ready':
            return starts, respawns
    raise ValueError('map has insufficient shared reachable ground for both camps')


def exportMaps(vfs):
    archive = vfs / 'data1.vfs'
    instances = {struct.unpack_from('<I',r)[0]: r for r in
                 rows(readEntry(archive, 'data/table/s_res_instance_cfg_cli.bin'), 2997)}
    maps = {struct.unpack_from('<I',r)[0]: r for r in
            rows(readEntry(archive, 'data/table/s_res_map_cli.bin'), 292)}
    result = []
    for resourceId in MAP_IDS:
        instance = instances[resourceId]
        scene = maps[struct.unpack_from('<I', instance, 148)[0]]
        navBase = canonicalPath(resourceText(scene, 4, 128)).rsplit('/', 1)[0]
        spawnBase = canonicalPath(resourceText(scene, 132, 128)).rsplit('/', 1)[0]
        raw = TagReader(readEntry(vfs / 'map.vfs', navBase + '/anavmesh.dat')).document()
        nav = NavMesh.fromResource(raw)
        data = readEntry(vfs / 'map.vfs', spawnBase + '/dactorspawngroup.dat')
        try:
            spawns, respawns = battlefieldPlacements(nav, placements(data, 'is_born'), placements(data, 'is_revive'))
        except ValueError as error:
            raise ValueError(f'map {resourceId}: {error}') from error
        result.append(dict(resourceId=resourceId, name=resourceText(instance, 8, 64),
            position=spawns[1][0][0], enemyPosition=spawns[2][0][0], spawns=spawns, respawns=respawns,
            navigation=dict(vertices=[v[:3] for v in raw['vertices']],
                edges=[[e[k] for k in EDGE_FIELDS] for e in raw['edges']],
                faces=[[f[k] for k in FACE_FIELDS] for f in raw['faces']])) )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client-directory', type=Path, required=True, help='absolute client Bin directory')
    parser.add_argument('--output', type=Path, required=True, help='new JSON file')
    args = parser.parse_args()
    if not args.client_directory.is_absolute():
        parser.error('--client-directory must be absolute')
    data = exportMaps(args.client_directory.parent / 'vfs')
    with args.output.open('x', encoding='utf-8', newline='\n') as out:
        json.dump(data, out, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        out.write('\n')
    print('maps=' + str(len(data)))


if __name__ == '__main__':
    main()
