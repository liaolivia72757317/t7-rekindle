"""Frozen navigation/spawn cases shared with the portable Rust core."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from Business.scripts.navigation import NavMesh
from Business.scripts import spawn_navigation

CORPUS = Path(__file__).resolve().parents[1] / 'fixtures/navigation/navigation-v1.json'


def evaluate(case):
    if 'resource' in case:
        nav = NavMesh.fromResource(case['resource'])
    elif 'links' in case:
        links = [[(link[0], tuple(link[1]), tuple(link[2])) for link in row] for row in case['links']]
        nav = NavMesh(case['polygons'], links)
    else:
        nav = NavMesh.fromPolygons(case['polygons'])
    output = {'locate': [nav.locate(point) for point in case.get('locate', [])],
              'project': [nav.project(point) for point in case.get('locate', [])], 'paths': []}
    for query in case.get('paths', []):
        path = nav.findPath(query['start'], query['target'], query.get('radius', .35))
        output['paths'].append({'status': path.status, 'faces': path.faces, 'points': path.points})
    if 'spawn' in case:
        spawn = case['spawn']
        refs = tuple((tuple(point), heading) for point, heading in spawn['references'])
        output['spawn'] = spawn_navigation.candidates(nav, refs, tuple(spawn['anchor']))
        if 'choose' in case:
            output['choose'] = [evaluate_choice(nav, refs, tuple(spawn['anchor']), query) for query in case['choose']]
    return json.loads(json.dumps(output))


def evaluate_choice(nav, references, anchor, query):
    from Business.scripts import actors, body_collision, bot, bot_life, roster
    flow = SimpleNamespace(session={'respawnPoints': {'1': references}}, context={}, log=lambda *_: None)
    value = {'camp': 1, 'life': {'diedAt': query['diedAt']}}
    statuses = []
    with (patch.object(actors, 'data', return_value=value),
          patch.object(bot, 'navigationFor', return_value=nav),
          patch.object(roster, 'loadSpawns', return_value={1: ((anchor, 0),)}),
          patch.object(body_collision, 'obstacles', return_value=query['neighbors']),
          patch.object(bot, 'status', side_effect=lambda _, status, *_args: statuses.append(status))):
        result = bot_life.chooseSpawn(flow, query['actorId'], {})
    if result is None:
        return {'status': statuses[-1]}
    groups = spawn_navigation.candidates(nav, references, anchor)
    return {'status': 'ready', 'reference': next(i for i, group in enumerate(groups) if result in group), 'spawn': result}


@pytest.mark.parametrize('case', json.loads(CORPUS.read_text(encoding='utf-8'))['cases'], ids=lambda case: case['name'])
def test_navigation_corpus_matches_existing_python(case):
    assert evaluate(case) == case['expected']
