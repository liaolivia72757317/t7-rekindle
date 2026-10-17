"""Training keeps arena markers and their native navigation footprint."""
from copy import deepcopy
import importlib
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from Business.scripts.navigation import NavMesh


@pytest.fixture
def exporter(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'scripts'))
    return importlib.import_module('export_training_maps')


def resource(polygons):
    vertices, edges, faces, owners, lookup = [], [], [], [], {}
    for face, polygon in enumerate(polygons):
        indices = []
        for vertex in polygon:
            if vertex not in vertices:
                vertices.append(vertex)
            indices.append(vertices.index(vertex))
        faces.append(dict(startEdgeIndex=len(edges), numEdges=len(indices), numUserEdges=0))
        for a, b in zip(indices, indices[1:] + indices[:1]):
            lookup[a, b] = len(edges)
            edges.append(dict(a=a, b=b, oppositeEdge=-1, oppositeFace=-1))
            owners.append(face)
    for edge in edges:
        opposite = lookup.get((edge['b'], edge['a']))
        if opposite is not None:
            edge.update(oppositeEdge=opposite, oppositeFace=owners[opposite])
    return dict(vertices=vertices, edges=edges, faces=faces, streamingSets=[])


def boundary(raw):
    return {(edge['a'], edge['b']) for edge in raw['edges'] if edge['oppositeEdge'] == -1}


@pytest.mark.parametrize('mapId,opponent', [(6006, 2000), (6009, 100)])
def test_combat_points_use_player_3000_and_mode_opponent_not_gate_spawn(exporter, mapId, opponent):
    root = ET.Element('SH_RES_POINT_TAB')
    for id in [1000, 2000, 100, 3000, 3001]:
        point = ET.SubElement(root, 'SH_RES_POINT')
        ET.SubElement(point, 'id', value=str(id))
        ET.SubElement(point, 'pos', value=f'{id},5,9')
        ET.SubElement(point, 'dir', value='0,1,0')
    assert exporter.combatPlacements(root, mapId) == {
        1: [((float(opponent), 5., 9.), -90)], 2: [((3000., 5., 9.), -90)]}
    root.remove(root[3])
    with pytest.raises(ValueError, match='training arena point'):
        exporter.combatPlacements(root, mapId)


def test_arena_face_split_keeps_boundary_height_and_neighbor_links(exporter):
    raw = resource([
        [(0., 0., 2.), (4., .001, 2.), (8., 0., 2.), (8., 6., 2.), (0., 6., 2.)],
        [(8., 0., 2.), (12., 0., 2.), (12., 6., 2.), (8., 6., 2.)],
        [(100., 0., 2.), (104., 0., 2.), (104., 4., 2.), (100., 4., 2.)],
    ])
    original = deepcopy(raw)
    positions = [(2., 3., 2.2), (6., 3., 2.2)]
    assert NavMesh.fromResource(raw).locate(positions[0]) is None
    converted = exporter.arenaNavigation(raw, positions)
    assert raw == original
    assert converted['vertices'] == raw['vertices']
    assert boundary(converted) == boundary(raw)
    nav = NavMesh.fromResource(converted)
    assert nav.findPath(*positions).status == 'ready'
    assert nav.findPath(positions[0], (10., 3., 2.2)).status == 'ready'
    assert nav.findPath(positions[0], (102., 2., 2.2)).status == 'no-path'
    assert nav.locate((4., .0004, 2.2)) is None  # Do not fill the original concavity.
    assert nav.project(positions[0]) == (2., 3., 2.)
    assert exporter.arenaNavigation(converted, positions) == converted


def test_training_export_never_projects_missing_arena_points_to_other_ground(exporter):
    raw = resource([[(0., 0., 0.), (10., 0., 0.), (10., 10., 0.), (0., 10., 0.)]])
    assert exporter.arenaNavigation(raw, [(2., 2., .2), (8., 8., .2)]) == raw
    with pytest.raises(ValueError, match='outside native navigation'):
        exporter.arenaNavigation(raw, [(20., 20., .2)])
