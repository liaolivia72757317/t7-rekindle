"""The running original client uses its logic gateway for match authority."""
from copy import deepcopy
import json
import struct

import pytest

from Business.runtime.host_runtime import Runtime, encode, validateTransition
from Business.scripts import app, controls, online_scene
from Business.scripts.codec import custom_room, protocol, room_flow, leave_flow
from test_custom_lobby import Client, room, string

MATCH = '00000000-0000-4000-8000-000000000001'


def start(peer=False):
    client = Client(runtime_movement=True)
    client.identify()
    client.event('message', command=59, body=struct.pack('>Hi', 2, 1003) + string(b'room') + string(b'') + struct.pack('>i', 1))
    waiting = {**room(peer=peer), 'mapId': 1003}
    client.reply(2, 'room', room=waiting)
    result = client.event('message', command=59, body=struct.pack('>Hb', 21, 0))
    assert json.loads(result['gateway'][0]['request'])['command'] == {'type': 'start'}
    assert result['send'] == []
    loading = {**waiting, 'phase': 'loading', 'revision': 2,
               'currentMatch': {'matchId': MATCH, 'loadDeadlineMs': 60000, 'startsAtMs': None}}
    result = client.reply(3, 'room', room=loading)
    assert result['send'] == []
    command = json.loads(result['gateway'][0]['request'])['command']
    assert command == {'type': 'getClientView', 'payload': {'matchId': MATCH}}
    actors = [dict(actorId=1, localRid=1, camp=1, isLocal=True)]
    if peer:
        actors.append(dict(actorId=2, localRid=2, camp=2, isLocal=False))
    view = dict(roomId=loading['roomId'], matchId=MATCH, mapId=1003, phase='loading', revision=2,
                localActorId=1, actors=actors)
    return client, loading, view


def instance(client, kind='message', command=0, body=b'', name=''):
    event = dict(type=kind, connection=2, role='instance', command=command, body=body, name=name,
                 sequence=1, serverTimeMs=0)
    result = app.handleEvent(event, client.state, client.context)
    assert app.validateState(result['state'])
    validateTransition(result, client.context)
    client.state = result['state']
    return result


def load(client):
    client.context['connections'] = [1, 2]
    instance(client, 'connected')
    instance(client, 'authenticated')
    client.context['nowMs'] = 20
    result = instance(client, 'timer', name='instance-init')
    sync = next(item['body'] for item in result['send'] if item['command'] == 0x2c)
    assert protocol.decode_sync_login_response(sync).user_id == 700
    assert not result.get('gateway')
    instance(client, command=0x23, body=b'\0\3\0')
    assert not instance(client, command=0x36, body=b'\0\x14\0').get('gateway')
    assert not instance(client, command=0x36, body=struct.pack('>Hi', 0x32, 1)).get('gateway')
    return instance(client, command=0x36, body=struct.pack('>HHb', 0x64, 1, 0))


@pytest.mark.parametrize('peer', (False, True))
def test_start_enters_the_same_client_then_real_loading_starts_authority(peer, spawnPoints, monkeypatch):
    monkeypatch.setattr(online_scene, 'loadSpawns', lambda _: spawnPoints)
    client, loading, view = start(peer)
    result = client.reply(4, 'clientView', view=view)
    assert [item['reason'] for item in result['send']] == ['custom-room-started', 'custom-room-scene-assigned', 'custom-enter-instance-notify']
    assert result['send'][0]['body'] == custom_room.encode_result(22, 0)
    assert result['send'][2]['body'] == room_flow.encode_room_enter_instance_notify(room_id=1, user_camp=1, notify_type=0)
    result = client.event('message', command=0x1e, body=room_flow.encode_room_enter_request(room_id=1))
    assert struct.pack('>i', 1003) in result['send'][0]['body']
    result = load(client)
    assert len(result['gateway']) == 1 and result['gateway'][0]['connection'] == 1
    assert json.loads(result['gateway'][0]['request'])['command'] == {'type': 'loaded', 'payload': {'matchId': MATCH}}
    assert 'bots' not in client.state['sessions']['2']
    assert bool(client.state['sessions']['2']['onlinePeers']) is peer
    assert not instance(client, command=0x36, body=struct.pack('>HHb', 0x64, 1, 0)).get('gateway')
    countdown = {**loading, 'phase': 'countdown', 'revision': 3}
    result = client.reply(5, 'room', room=countdown)
    assert result['send'][0]['reason'] == 'online-authority-countdown'
    assert all(item['connection'] == 2 for item in result['send'])
    running = {**countdown, 'phase': 'running', 'revision': 4}
    result = client.reply(None, 'roomChanged', room=running)
    assert result['send'][0]['reason'] == 'online-authority-running'
    flow = app.Flow({'connection': 2}, client.state, client.context)
    assert controls.groundEnabled(flow)
    assert online_scene.ids(flow) == ((1, 2) if peer else (1,))


@pytest.mark.parametrize('mutation', ('match', 'map', 'rid', 'actor', 'phase'))
def test_unrelated_scene_view_has_no_successful_handoff(mutation):
    client, _, view = start()
    before = deepcopy(client.state)
    if mutation == 'match': view['matchId'] = '00000000-0000-4000-8000-000000000002'
    elif mutation == 'map': view['mapId'] = 1028
    elif mutation == 'rid': view['actors'][0]['localRid'] = 2
    elif mutation == 'actor': view['actors'][0]['actorId'] = 4
    else: view['phase'] = 'running'
    with pytest.raises(ValueError):
        client.reply(4, 'clientView', view=view)
    assert client.state == before and 'onlineScene' not in client.state


def test_timeout_cancels_loaded_handoff_and_cannot_send_late_loaded(spawnPoints, monkeypatch):
    monkeypatch.setattr(online_scene, 'loadSpawns', lambda _: spawnPoints)
    client, loading, view = start()
    client.reply(4, 'clientView', view=view)
    client.context['connections'] = [1, 2]
    instance(client, 'connected'); instance(client, 'authenticated')
    client.context['nowMs'] = 20
    instance(client, 'timer', name='instance-init')
    waiting = {**loading, 'phase': 'waiting', 'revision': 3, 'currentMatch': None}
    result = client.reply(None, 'roomChanged', room=waiting)
    assert any(item['reason'] == 'custom-scene-cancelled-leave' for item in result['send'])
    assert client.state['sessions']['2']['leaving'] and client.state['onlineScene']['phase'] == 4
    client.context['connections'] = [1]
    result = instance(client, 'closed')
    assert 'onlineScene' not in client.state
    assert json.loads(result['gateway'][0]['request'])['command'] == {'type': 'leaveRoom'}
    client.reply(5, 'left', roomId=loading['roomId'])
    assert 'room' not in client.state['sessions']['1']['customLobby']


def test_logic_disconnect_cancels_existing_instance(spawnPoints, monkeypatch):
    monkeypatch.setattr(online_scene, 'loadSpawns', lambda _: spawnPoints)
    client, _, view = start()
    client.reply(4, 'clientView', view=view)
    load(client)
    client.context['connections'] = [2]
    result = client.event('closed')
    assert result.get('gateway', []) == []
    assert client.state['onlineScene']['phase'] == 4
    assert client.state['sessions']['2']['leaving']


def test_exit_keeps_logout_confirmation_across_rust_leave_reply(spawnPoints, monkeypatch):
    monkeypatch.setattr(online_scene, 'loadSpawns', lambda _: spawnPoints)
    client, loading, view = start()
    client.reply(4, 'clientView', view=view)
    load(client)
    client.reply(5, 'room', room={**loading, 'phase': 'countdown', 'revision': 3})
    client.reply(None, 'roomChanged', room={**loading, 'phase': 'running', 'revision': 4})
    result = instance(client, command=0x2c, body=struct.pack('>Hb', 2, 1))
    assert any(item['reason'] == 'instance-leave-instance' for item in result['send'])
    assert json.loads(result['gateway'][0]['request'])['command'] == {'type': 'leaveRoom'}
    due = client.state['sessions']['2']['pending']['logout']
    client.reply(6, 'left', roomId=loading['roomId'])
    assert client.state['sessions']['2']['pending'] == {'logout': due}
    client.context['nowMs'] = due
    result = instance(client, 'timer', name='logout')
    assert result['send'][0]['body'] == leave_flow.encode_sync_logout_response(server_time_ms=due, back_data=1)
    assert not result.get('gateway')
    client.context['connections'] = [1]
    instance(client, 'closed')
    assert 'onlineScene' not in client.state


def test_early_or_unassigned_instance_cannot_create_an_offline_scene():
    client = Client(runtime_movement=True); client.identify()
    client.context['connections'] = [1, 2]
    with pytest.raises(ValueError, match='allocated loading'):
        instance(client, 'connected')


def test_ui_identity_is_only_published_after_committed_gateway_identification():
    client = Client(runtime_movement=True)
    assert Runtime.playerId(None, encode(client.state)) == 0
    client.identify()
    assert Runtime.playerId(None, encode(client.state)) == 700


def test_rust_start_failure_keeps_waiting_and_has_no_scene():
    client, _, _ = start()
    # Rewind to the owner's unanswered Start transaction.
    client.state.pop('onlineScene', None)
    gateway = client.state['sessions']['1']['customLobby']
    gateway['pending'] = {'3': {'kind': 'start'}}
    gateway['room'] = {**room(), 'mapId': 1003}
    result = client.reply(3, 'error', code='notReady')
    assert result['send'][0]['body'] == custom_room.encode_result(22, 1)
    assert not any(item['op'] == 'send' for item in result['gateway'])
    assert gateway['room']['phase'] == 'waiting' and 'onlineScene' not in client.state
