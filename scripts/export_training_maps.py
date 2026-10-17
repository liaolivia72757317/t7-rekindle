"""Export the two melee training scenes without adding them to matchmaking."""
import argparse
import json
import math
from pathlib import Path
import struct
import xml.etree.ElementTree as ET

from export_match_maps import resourceText, rows, readEntry, canonicalPath
from export_match_maps import TagReader, EDGE_FIELDS, FACE_FIELDS, NavMesh
from Business.scripts.navigation import cross, inside, height, EPSILON

MAP_IDS = (6006, 6009)
OPPONENT_POINTS = {6006: 2000, 6009: 100}


def placement(node):
    position, direction = [tuple(map(float, node.find(key).get('value').split(',')))
                           for key in ('pos', 'dir')]
    return position, round(math.degrees(math.atan2(-direction[1], direction[0])))


def combatPlacements(points, mapId):
    def point(id):
        matches = [p for p in points.findall('SH_RES_POINT') if p.find('id').get('value') == str(id)]
        if len(matches) != 1:
            raise ValueError(f'missing or duplicate training arena point {id}')
        return placement(matches[0])
    # 3000 is the player teleport marker; the opponent uses the mode's target marker.
    return {1: [point(OPPONENT_POINTS[mapId])], 2: [point(3000)]}


def triangulate(vertices, polygon):
    pending, result = list(polygon), []
    area = sum(vertices[a][0] * vertices[b][1] - vertices[b][0] * vertices[a][1]
               for a, b in zip(polygon, polygon[1:] + polygon[:1]))
    winding = 1 if area > 0 else -1
    while len(pending) > 3:
        for index, b in enumerate(pending):
            a, c = pending[index - 1], pending[(index + 1) % len(pending)]
            triangle = [vertices[v][:3] for v in (a, b, c)]
            if winding * cross(*triangle) <= EPSILON:
                continue
            ccw = tuple(triangle[::winding])
            if any(inside(ccw, vertices[v]) for v in pending if v not in (a, b, c)):
                continue
            result.append([a, b, c])
            pending.pop(index)
            break
        else:
            raise ValueError('training arena face cannot be triangulated')
    if winding * cross(*(vertices[v] for v in pending)) <= EPSILON:
        raise ValueError('degenerate training arena triangle')
    return [*result, pending]


def splitFaces(raw, splits):
    edges, faces, owners, sources, remap, internal = [], [], [], [], {}, {}
    for faceId, face in enumerate(raw['faces']):
        start = face['startEdgeIndex']
        ordinals = range(start, start + face['numEdges'])
        boundary = {(raw['edges'][i]['a'], raw['edges'][i]['b']): i for i in ordinals}
        polygon = [raw['edges'][i]['a'] for i in ordinals]
        for part in splits.get(faceId, [polygon]):
            owner = len(faces)
            faces.append(dict(startEdgeIndex=len(edges), numEdges=len(part), numUserEdges=0))
            for a, b in zip(part, part[1:] + part[:1]):
                source = boundary.get((a, b))
                if source is None:
                    internal[faceId, a, b] = len(edges)
                else:
                    remap[source] = len(edges)
                sources.append((faceId, source))
                owners.append(owner)
                edges.append(dict(a=a, b=b, oppositeEdge=-1, oppositeFace=-1))
    for edge, (faceId, source) in zip(edges, sources):
        if source is None:
            opposite = internal[faceId, edge['b'], edge['a']]
        else:
            opposite = remap.get(raw['edges'][source]['oppositeEdge'], -1)
        if opposite != -1:
            edge.update(oppositeEdge=opposite, oppositeFace=owners[opposite])
    return dict(raw, edges=edges, faces=faces)


def arenaNavigation(raw, positions):
    nav = NavMesh.fromResource(raw)
    missing = [p for p in positions if nav.locate(p) is None]
    if not missing:
        return raw
    splits = {}
    for index, face in enumerate(raw['faces']):
        if nav.polygons[index]:
            continue
        polygon = [raw['edges'][i]['a'] for i in
                   range(face['startEdgeIndex'], face['startEdgeIndex'] + face['numEdges'])]
        vertices = [raw['vertices'][i] for i in polygon]
        if not any(all(min(v[d] for v in vertices) - (1 if d == 2 else 0) <= p[d]
                       <= max(v[d] for v in vertices) + (1 if d == 2 else 0)
                       for d in range(3)) for p in missing):
            continue
        parts = triangulate(raw['vertices'], polygon)
        triangles = [tuple(raw['vertices'][v][:3] for v in part) for part in parts]
        triangles = [t if cross(*t) > 0 else tuple(reversed(t)) for t in triangles]
        if any(inside(t, p) and (z := height(t, p)) is not None and abs(z - p[2]) <= 1
               for t in triangles for p in missing):
            splits[index] = parts
    # The arena's native polygon has a slight concavity. Preserve its vertices,
    # boundary and seams instead of projecting actors onto a different navigation island.
    converted = splitFaces(raw, splits)
    nav = NavMesh.fromResource(converted)
    if any(nav.locate(p) is None for p in positions):
        raise ValueError('training arena point outside native navigation')
    return converted


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
        points = ET.fromstring(readEntry(vfs / 'map.vfs', overlay + '/dserverpoint.dat').decode('gbk'))
        spawns = combatPlacements(points, mapId)
        raw = arenaNavigation(raw, [spawns[camp][0][0] for camp in (1, 2)])
        nav = NavMesh.fromResource(raw)
        if any(nav.findPath(spawns[a][0][0], spawns[b][0][0]).status != 'ready'
               for a, b in ((1, 2), (2, 1))):
            raise ValueError('training arena points are not connected')
        result.append(dict(resourceId=mapId, name=resourceText(instance, 8, 64),
            position=spawns[1][0][0], enemyPosition=spawns[2][0][0], spawns=spawns, respawns=spawns,
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
