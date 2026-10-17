from copy import deepcopy
import json
import math
from unittest.mock import patch

import pytest

from battle_replay import ReplaySession, decodeValue, encodeValue, runCase
from replay_fixtures import cases, resources


def test_trace_values_preserve_binary_and_integer_boundaries():
    value = {'body': b'\x00\xff', 'time': 2**64 - 1, 'negative': -(2**63),
             'nested': [None, True, 1.25, '\u4e2d\u6587']}
    assert decodeValue(json.loads(json.dumps(encodeValue(value)))) == value


@pytest.mark.parametrize('value', [math.nan, math.inf, {1: 'key'}, {'$bytes': '00'}])
def test_trace_values_reject_ambiguous_or_nonportable_values(value):
    with pytest.raises((TypeError, ValueError)):
        encodeValue(value)


@pytest.mark.parametrize('value', [{'$bytes': 'zz'}, {'$bytes': '0'}, {'$bytes': 1}])
def test_trace_binary_requires_valid_hex(value):
    with pytest.raises(ValueError):
        decodeValue(value)


def test_twenty_actor_cases_replay_deterministically_without_mutating_inputs():
    with resources():
        suite = cases()
        before = deepcopy(suite)
        for case in suite:
            first = runCase(case)
            second = runCase(case)
            assert first['steps'] == second['steps']
            assert first['name'] == case['name']
            assert all(row['dispatchMs'] >= 0 for row in first['timings'])
            assert not [step for step in first['steps'] if step['status'] == 'rejected']
        assert suite == before
    assert any(case['actorCount'] == 20 for case in suite)


def test_heartbeat_echo_and_action_messages_have_golden_outputs():
    with resources():
        result = runCase(next(case for case in cases() if case['name'] == 'twenty-actor-action'))
    heartbeat = result['steps'][0]['send'][0]
    assert heartbeat['command'] == 29
    assert heartbeat['body'] == {'$bytes': '000200000000075bcd1500000007'}
    assert any(packet['command'] == 4 for step in result['steps'] for packet in step['send'])
    assert all(len(step['state']['sessions']['1']['bots']) == 19 for step in result['steps'])


@pytest.mark.parametrize('failure', ['dead-target', 'send-capacity', 'invalid-input'])
def test_failed_transition_keeps_committed_state_and_timer_ledger(failure):
    with resources():
        case = next(case for case in cases() if case['name'] == 'twenty-actor-action')
        session = ReplaySession(case)
        step = deepcopy(case['steps'][0])
        if failure == 'dead-target':
            step['commitConnections'] = []
        elif failure == 'send-capacity':
            step['sendCapacity'] = 0
        else:
            step['event']['body'] = {'$bytes': '00'}
        before = session.snapshot()
        actual, _ = session.dispatch(step)
        assert actual['status'] == 'rejected'
        assert actual['send'] == []
        assert session.snapshot() == before


def test_trace_rejects_time_reversal_and_unpublished_world_version():
    with resources():
        case = next(case for case in cases() if case['name'] == 'twenty-actor-action')
        session = ReplaySession(case)
        session.dispatch(case['steps'][0])
        step = deepcopy(case['steps'][0])
        step['atMs'] -= 1
        with pytest.raises(ValueError, match='monotonic'):
            session.dispatch(step)
        step = deepcopy(case['steps'][0])
        step['worldVersion'] = 999
        with pytest.raises(ValueError, match='world version'):
            session.dispatch(step)


def test_contact_replay_preserves_late_delivery_and_duplicate_batch_semantics():
    with resources():
        result = runCase(next(case for case in cases() if case['name'] == 'twenty-actor-contact'))
    health = [step['state']['sessions']['1']['bots']['2']['health'] for step in result['steps']]
    assert 0 < health[0] < 100
    assert health[0] == health[1]
    assert result['steps'][1]['send'] == []


def test_death_and_respawn_replay_preserves_score_and_life_boundaries():
    with resources():
        result = runCase(next(case for case in cases() if case['name'] == 'twenty-actor-respawn'))
    dead = result['steps'][0]['state']['sessions']['1']['bots']['2']
    revived = result['steps'][-1]['state']['sessions']['1']['bots']['2']
    assert dead['health'] == 0 and dead['life']['state'] == 'dead'
    assert revived['health'] == 100 and revived['life']['state'] == 'spawning'
    assert any(packet['reason'] == 'bot-respawn-add' for packet in result['steps'][-1]['send'])


def test_world_is_retained_but_contacts_are_only_exposed_to_world_events():
    with resources():
        suite = cases()
        case = next(case for case in suite if case['name'] == 'twenty-actor-contact')
        heartbeat = deepcopy(next(case for case in suite if case['name'] == 'twenty-actor-action')['steps'][0])
        heartbeat['atMs'] = case['steps'][0]['atMs'] + 1
        session = ReplaySession(case)
        seen = []
        original = session.runtime.dispatchNative

        def dispatch(event, state, context):
            seen.append(deepcopy(context['botWorld']))
            return original(event, state, context)

        with patch.object(session.runtime, 'dispatchNative', dispatch):
            session.dispatch(case['steps'][0])
            session.dispatch(heartbeat)
            session.dispatch(case['steps'][1])
        assert [len(world['contactJournal']['events']) for world in seen] == [1, 0, 1]
        assert seen[0]['actors'] == seen[1]['actors'] == seen[2]['actors']


def test_round_replay_freezes_twenty_scores_and_drops_old_connection_timers():
    with resources():
        result = runCase(next(case for case in cases() if case['name'] == 'twenty-actor-settlement-restart'))
    steps = result['steps']
    assert steps[0]['state']['settlement']['reason'] == 'timeout'
    assert len(steps[0]['state']['settlement']['members']) == 20
    assert len([packet for packet in steps[0]['send'] if packet['reason'] == 'settlement-score']) == 20
    assert any(packet['reason'] == 'instance-finish-game' for packet in steps[1]['send'])
    assert steps[2]['timers'] == {}
    restarted = steps[5]['state']
    assert 'settlement' not in restarted and '1' not in restarted['sessions']
    assert len(restarted['sessions']['2']['bots']) == 19
    assert restarted['sessions']['2']['instanceStartedAt'] == 7041
    assert steps[6]['state'] == restarted and steps[6]['send'] == []


def test_title_replay_waits_for_matching_confirmation_and_ignores_old_epoch():
    with resources():
        steps = runCase(next(case for case in cases() if case['name'] == 'title-confirmation-and-epoch'))['steps']
    assert steps[1]['titleRequest'] == dict(requestId=22, epoch=1, titleId=0, connection=1)
    assert steps[1]['state']['titles']['equipped'] == 1008 and steps[1]['send'] == []
    assert steps[2]['state']['titles']['equipped'] == 0
    assert steps[3]['state']['titles'] == dict(epoch=2, owned=[], equipped=0)
    assert steps[4]['state'] == steps[3]['state'] and steps[4]['send'] == []


def test_profile_replay_preserves_save_failure_and_revision_semantics():
    with resources():
        case = next(case for case in cases() if case['name'] == 'profile-save-and-rollback')
        steps = runCase(case)['steps']
    assert all(step['status'] == 'committed' for step in steps)
    assert steps[0]['profile']['formation'] == [2102, 1101]
    assert steps[0]['profile']['revision'] == 1
    assert steps[1]['profile'] == steps[0]['profile']
    assert steps[2]['profile'] == steps[1]['profile']
    assert steps[2]['state']['playerProfile'] == steps[1]['state']['playerProfile']
    assert [packet['reason'] for packet in steps[2]['send']] == ['loadout-save-failed']
    assert steps[3]['profile'] == steps[2]['profile']
    assert steps[4]['profile']['formation'] == [1101]
    assert steps[4]['profile']['revision'] == 2
