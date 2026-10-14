from copy import deepcopy
import json

import pytest

from Business.scripts.codec import move_flow
from test_online_actions import running
from test_custom_scene import MATCH, instance


def sample(client, position=(1.0, 2.0, 0.5), now=100):
    client.context['nowMs'] = now
    client.context['botWorld'] = dict(ready=True, sampledAt=now,
        instanceStartedAt=client.state['sessions']['2']['instanceStartedAt'],
        actors={'1': dict(ready=True, position=list(position), heading=45.0)})
    return client.event('world')


def event(actor=2, sequence=1, event_sequence=1, **pose):
    return dict(roomId='room-test', matchId=MATCH, actorId=actor, inputSeq=sequence,
        eventSeq=event_sequence, serverTimeMs=100,
        pose=dict(position=[1.0, 2.0, 0.5], heading=45, forwardBack=-1, leftRight=0, running=False, **pose))


@pytest.mark.parametrize('reaction', (35, 240, 231, 466, 42))
def test_peer_movement_cannot_overwrite_authoritative_reaction(reaction, monkeypatch, spawnPoints):
    from test_online_actions import action_event
    client = running(monkeypatch, spawnPoints, peer=True)
    client.reply(None, 'actionChanged', action=action_event(actor=2, state=reaction))
    result = client.reply(None, 'poseChanged', update=event())
    assert not any(item['reason'] == 'online-peer-movement' for item in result['send'])
    assert any(item['reason'] == 'online-pose-reset' for item in result['send'])
    client.reply(None, 'actionChanged', action=action_event(actor=2, event=2, sequence=3, state=2))
    client.context['nowMs'] += 100
    value = event(sequence=2, event_sequence=2)
    value['serverTimeMs'] += 100
    client.reply(None, 'poseChanged', update=value)
    result = instance(client, kind='timer', name='online-motion-render')
    assert any(item['reason'] == 'online-peer-movement' for item in result['send'])


def test_sampling_uses_native_pose_coalesces_inflight_and_reserves_gateway_capacity(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    result = sample(client)
    assert not result['send'] and not result['timers']
    command = json.loads(result['gateway'][0]['request'])['command']
    assert command['type'] == 'poseInput' and command['payload']['inputSeq'] == 1
    assert command['payload']['pose']['position'] == [1.0, 2.0, 0.5]
    assert not sample(client, (2.0, 2.0, 0.5), 200).get('gateway')
    client.reply(6, 'poseAccepted', matchId=MATCH, inputSeq=1)
    result = sample(client, (3.0, 2.0, 0.5), 210)
    assert json.loads(result['gateway'][0]['request'])['command']['payload']['pose']['position'][0] == 3.0
    client.reply(7, 'poseAccepted', matchId=MATCH, inputSeq=2)
    assert not sample(client, (4.0, 2.0, 0.5), 230).get('gateway')


def test_peer_positions_and_heading_are_applied_but_local_echo_never_moves_the_player(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    result = client.reply(None, 'poseChanged', update=event())
    move = move_flow.decode_move_bc_with_system_and_active(result['send'][0]['body'])
    correction = move_flow.decode_move_direct_bc(result['send'][1]['body'])
    assert (move.target_instance_id, move.forward_back, move.state) == (2, -1000, 2)
    assert (move.current_velocity, move.max_velocity) == (3000, 25000)
    assert (correction.target_instance_id, correction.position, correction.direction_yaw) == (2, (1.0, 2.0, 0.5), 45)
    result = client.reply(None, 'poseChanged', update=event(sequence=3, event_sequence=2))
    assert not result['send']
    result = client.reply(None, 'poseChanged', update=event(actor=1, event_sequence=3))
    assert not result['send']


def peer_sample(client, time, position, heading=45, sequence=1):
    client.context['nowMs'] = time
    value = event(sequence=sequence, event_sequence=sequence)
    value['serverTimeMs'] = time - 1000
    value['pose'].update(position=list(position), heading=heading)
    return client.reply(None, 'poseChanged', update=value)


def rendered(client, time):
    client.context['nowMs'] = time
    result = client.event('world')
    return [move_flow.decode_move_direct_bc(p['body']) for p in result['send'] if p['body'][:2] == b'\0\4']


def test_peer_interpolation_uses_server_timeline_and_shortest_yaw_arc(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    peer_sample(client, 1000, (0, 0, 0), heading=170)
    assert not peer_sample(client, 1050, (1, 0, 1), heading=-170, sequence=2)['send']
    assert rendered(client, 1100)[0].position == (0, 0, 0)
    correction = rendered(client, 1125)[0]
    assert correction.position == (.5, 0, .5) and correction.direction_yaw == -180
    assert not rendered(client, 1130)
    assert rendered(client, 1150)[0].position == (1, 0, 1)
    assert rendered(client, 1200)[0].position == (1, 0, 1)


def test_peer_stall_stops_without_extrapolation_and_large_jump_resets(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    peer_sample(client, 1000, (0, 0, 0))
    client.context['nowMs'] = 1700
    result = client.event('world')
    move = move_flow.decode_move_bc_with_system_and_active(result['send'][0]['body'])
    assert move.state == 1 and move.current_velocity == 0
    result = peer_sample(client, 1800, (15, 0, 0), sequence=2)
    assert move_flow.decode_move_direct_bc(result['send'][-1]['body']).position == (15, 0, 0)
    assert len(client.state['sessions']['2']['onlineMotion']['buffers']['2']['samples']) == 1


def test_interpolation_buffer_is_bounded_and_discarded_on_exit(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    for sequence in range(1, 50):
        peer_sample(client, 1000 + sequence * 50, (sequence / 10, 0, 0), sequence=sequence)
    assert len(client.state['sessions']['2']['onlineMotion']['buffers']['2']['samples']) == 32
    instance(client, command=0x2c, body=b'\0\2\1')
    assert not rendered(client, 4000)


@pytest.mark.parametrize('field,value', [('matchId', 'old'), ('roomId', 'other'), ('actorId', 3),
    ('inputSeq', 0), ('eventSeq', 2)])
def test_unrelated_replayed_and_out_of_order_poses_do_not_commit(field, value, monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    update = event(); update[field] = value
    before = deepcopy(client.state)
    with pytest.raises(ValueError): client.reply(None, 'poseChanged', update=update)
    assert client.state == before


def test_stale_world_and_exit_cannot_send_or_apply_poses(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    sample(client)
    client.reply(6, 'poseAccepted', matchId=MATCH, inputSeq=1)
    client.context['nowMs'] = 1000
    assert not client.event('world').get('gateway')
    instance(client, command=0x2c, body=b'\0\2\1')
    assert not sample(client, now=1100).get('gateway')
    assert not client.reply(None, 'poseChanged', update=event())['send']


def test_pose_bounds_rejection_is_acknowledged_without_interrupting_combat(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints)
    sample(client)
    result = client.reply(6, 'error', code='invalidPose')
    assert result['gateway'][0]['op'] == 'ack'
    assert client.state['onlineScene']['phase'] == 3


def test_stationary_jump_keeps_correcting_height_when_the_buffer_briefly_runs_out(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    for seq, time, height in ((1, 1000, .5), (2, 1050, 1.0)):
        client.context['nowMs'] = time
        value = event(sequence=seq, event_sequence=seq)
        value['serverTimeMs'] = time - 1000
        value['pose'].update(position=[0., 0., height], forwardBack=0)
        client.reply(None, 'poseChanged', update=value)
    assert rendered(client, 1150)[0].position == (0, 0, 1)
    assert rendered(client, 1170)[0].position == (0, 0, 1)
    assert rendered(client, 1200)[0].position == (0, 0, 1)
    assert not rendered(client, 1700)
