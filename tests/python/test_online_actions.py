from copy import deepcopy
import json
import struct

import pytest

from Business.scripts import online_scene, online_actions
from Business.scripts.codec import battle_flow
from test_custom_scene import start, load, instance, MATCH


def running(monkeypatch, spawnPoints, peer=False):
    monkeypatch.setattr(online_scene, 'loadSpawns', lambda _: spawnPoints)
    client, loading, view = start(peer)
    client.reply(4, 'clientView', view=view)
    load(client)
    client.reply(5, 'room', room={**loading, 'phase': 'countdown', 'revision': 3})
    client.reply(None, 'roomChanged', room={**loading, 'phase': 'running', 'revision': 4})
    return client


def action_event(actor=1, event=1, sequence=2, state=235):
    return dict(roomId='room-test', matchId=MATCH, actorId=actor, eventSeq=event,
                actionSeq=sequence, state=state, serverTimeMs=3000)


@pytest.mark.parametrize('intent', online_actions.INTENTS)
def test_inputs_go_to_the_existing_gateway_and_only_server_events_animate(intent, monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints)
    result = instance(client, command=4, body=struct.pack('>HIib', 1, 0, intent, 0))
    assert not result['send'] and not result['timers']
    request = result['gateway'][0]
    assert request['connection'] == 1
    assert json.loads(request['request'])['command'] == {'type': 'actionInput', 'payload': {
        'matchId': MATCH, 'inputSeq': 1, 'intent': online_actions.INTENTS[intent]}}
    assert not client.reply(6, 'actionAccepted', matchId=MATCH, inputSeq=1)['send']
    result = client.reply(None, 'actionChanged', action=action_event())
    assert len(result['send']) == 1 and result['send'][0]['connection'] == 2
    update = battle_flow.decode_battle_state_sync_simple(result['send'][0]['body'])
    assert (update.instance_id, update.seq_no, update.state) == (1, 2, 235)
    assert 'battle-action-phase' not in client.state['sessions']['2']['pending']


def test_peer_action_mapping_and_actor_sequences_are_independent(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    result = client.reply(None, 'actionChanged', action=action_event(actor=2, state=308))
    update = battle_flow.decode_battle_state_sync_simple(result['send'][0]['body'])
    assert (update.instance_id, update.seq_no, update.state) == (2, 2, 308)
    result = client.reply(None, 'actionChanged', action=action_event(event=2))
    assert battle_flow.decode_battle_state_sync_simple(result['send'][0]['body']).instance_id == 1


@pytest.mark.parametrize('field,value', [('matchId', 'other'), ('roomId', 'other'), ('actorId', 2),
                                        ('eventSeq', 2), ('actionSeq', 3), ('state', 999)])
def test_mismatched_or_missing_events_do_not_commit(field, value, monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints)
    event = {**action_event(), field: value}
    before = deepcopy(client.state)
    with pytest.raises(ValueError):
        client.reply(None, 'actionChanged', action=event)
    assert client.state == before


def test_old_scene_input_and_events_cannot_resume_combat_after_exit(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints)
    instance(client, command=0x2c, body=struct.pack('>Hb', 2, 1))
    result = instance(client, command=4, body=struct.pack('>HIib', 1, 0, 300560, 0))
    assert not result.get('gateway') and not result['send']
    assert not client.reply(None, 'actionChanged', action=action_event())['send']


def test_pending_action_rejection_after_cancellation_preserves_exit(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints)
    instance(client, command=4, body=struct.pack('>HIib', 1, 0, 300560, 0))
    instance(client, command=0x2c, body=struct.pack('>Hb', 2, 1))
    due = client.state['sessions']['2']['pending']['logout']
    result = client.reply(6, 'error', code='wrongPhase')
    assert result['gateway'] == [{'connection': 1, 'op': 'ack', 'requestId': '6', 'request': ''}]
    assert client.state['sessions']['2']['pending']['logout'] == due
