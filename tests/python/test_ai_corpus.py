"""Portable AI vectors produced by the existing Python decision/profile rules."""
from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from Business.scripts import battle, bot_decision, bot_profiles

CORPUS = Path(__file__).resolve().parents[1] / 'fixtures/ai/decisions-v1.json'


def evaluate(case):
    context = case['context']
    source, target = case['source'], case['target']
    value = dict(camp=2, profile=context['profile'], tempo=context['tempo'],
                 decisionSeed=context['seed'], reactAt=context['reactAt'], action=source['action'],
                 observedPlayer=dict(seenAt=context['observedSince'], phaseSeenAt=context['phaseObservedSince']))
    if context['lastAttack']:
        value['lastAttack'] = context['lastAttack']
    if source['hurt']:
        value['hurtUntil'] = context['now'] + 1
    flow = SimpleNamespace(now=context['now'], session=dict(instanceStartedAt=0, camp=1,
        action=target['action'], bots={'2': value}, pending={}))
    if target['due'] is not None:
        flow.session['pending'][battle.actionTimer(1)] = target['due']

    def terrain(actor):
        return {'target': dict(ready=actor['contact'] != 'unavailable', hit=actor['contact'] == 'blocked',
            material=4, position=[.7, 0, 1.2], normal=[-1, 0, 0], direction=[1, 0, 0])}

    snapshot = dict(bot=source['position'], player=target['position'], botHeading=source['heading'],
        playerHeading=target['heading'], sourceId=2, targetId=1, terrain=[terrain(target), terrain(source)])
    with patch.object(battle, 'phaseMs', side_effect=lambda intent, _: case['windups'][list(battle.ATTACK_CHAINS).index(intent)]):
        return asdict(bot_decision.choose(flow, snapshot))


@pytest.mark.parametrize('case', json.loads(CORPUS.read_text(encoding='utf-8'))['cases'], ids=lambda case: case['name'])
def test_decisions_match_python(case):
    assert evaluate(case) == case['expected']


def test_profile_initialization_matches_python():
    corpus = json.loads(CORPUS.read_text(encoding='utf-8'))
    spawns = {1: tuple((point, 0) for point in corpus['spawns']['1']),
              2: tuple((point, 180) for point in corpus['spawns']['2'])}
    for case in corpus['profiles']:
        value = {'camp': case['camp']}
        bot_profiles.initialize(value, case['actorId'], case['ordinal'], spawns, case['seed'])
        assert json.loads(json.dumps(value)) == case['expected']
