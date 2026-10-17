"""Resource-bundle respawn prewarm results shared with the native runtime."""
import json
from pathlib import Path

from Business.scripts import map_data, navmesh_resource, navigation, spawn_navigation

ROOT = Path(__file__).resolve().parents[1] / 'fixtures/resources'


def prepared():
    bundle = json.loads((ROOT / 'bundle-v1.json').read_text(encoding='utf-8'))
    data = bundle['map']['navigation']
    nav = navigation.NavMesh.fromResource(dict(
        vertices=data['vertices'], streamingSets=0,
        edges=[dict(zip(('a', 'b', 'oppositeEdge', 'oppositeFace'), row)) for row in data['edges']],
        faces=[dict(zip(('startEdgeIndex', 'numEdges', 'numUserEdges'), row)) for row in data['faces']]))
    return dict(schemaVersion=1, camps=[
        spawn_navigation.candidates(nav,
            tuple((tuple(p), h) for p, h in bundle['map']['respawns'][str(camp)]),
            tuple(bundle['map']['spawns'][str(camp)][0][0]))
        for camp in (1, 2)])


def test_prepared_bundle_matches_python():
    expected = json.loads((ROOT / 'prepared-v1.json').read_text(encoding='utf-8'))
    assert expected == json.loads(json.dumps(prepared(), allow_nan=False))


def prepared_map():
    nav = navmesh_resource.loadNavigation()
    return dict(schemaVersion=1, camps=[
        spawn_navigation.candidates(nav, map_data.RESPAWNS[camp], map_data.SPAWNS[camp][0][0])
        for camp in (1, 2)])


def test_prepared_frozen_map_matches_python():
    expected = json.loads((ROOT / 'prepared-map-v1.json').read_text(encoding='utf-8'))
    assert expected == json.loads(json.dumps(prepared_map(), allow_nan=False))
