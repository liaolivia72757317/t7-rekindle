"""Several opponents and teammates share one frozen match without RID aliasing."""
from copy import deepcopy

from Business.scripts import online_scene
from Business.scripts.codec import battle_flow, move_flow
from test_custom_scene import start, load
from test_online_actions import action_event
from test_online_motion import event


def running_roster(monkeypatch, spawnPoints):
    monkeypatch.setattr(online_scene, 'loadSpawns', lambda _: spawnPoints)
    client, loading, view = start(True)
    for actor, camp in ((3, 1), (4, 2)):
        member = deepcopy(loading['members'][1])
        member.update(accountId=f'player-{actor}', actorId=actor, camp=camp, userId=900+actor, displayName=f'Player {actor}', ready=True)
        loading['members'].append(member)
        view['actors'].append(dict(actorId=actor, localRid=12 if camp == 1 else 3, camp=camp, isLocal=False))
    client.state['sessions']['1']['customLobby']['room'] = deepcopy(loading)
    client.reply(4, 'clientView', view=view)
    load(client)
    client.reply(5, 'room', room={**loading, 'phase':'countdown', 'revision':3})
    client.reply(None, 'roomChanged', room={**loading, 'phase':'running', 'revision':4})
    return client


def test_every_actor_has_separate_objects_spawn_and_action_sequence(monkeypatch, spawnPoints):
    client = running_roster(monkeypatch, spawnPoints)
    session = client.state['sessions']['2']
    peers = session['onlinePeers']
    assert set(peers) == {'2', '3', '12'}
    assert peers['2']['position'] != peers['3']['position']
    assert session['spawnPosition'] != peers['12']['position']
    assert online_scene.ids(type('Flow', (), {'state':client.state})()) == (1, 2, 12, 3)
    for seq, (actor, rid) in enumerate(((2, 2), (3, 12), (4, 3)), 1):
        result = client.reply(None, 'actionChanged', action=action_event(actor=actor, event=seq))
        decoded = battle_flow.decode_battle_state_sync_simple(result['send'][0]['body'])
        assert decoded.instance_id == rid
    assert client.state['sessions']['2']['onlineActions']['actors'] == {'2':2, '12':2, '3':2}


def test_motion_interpolation_buffers_do_not_mix_different_peers(monkeypatch, spawnPoints):
    client = running_roster(monkeypatch, spawnPoints)
    for seq, (actor, rid, x) in enumerate(((2, 2, 2), (3, 12, 12), (4, 3, 3)), 1):
        update = event(actor=actor, event_sequence=seq)
        update['pose']['position'] = [x, 0., .5]
        result = client.reply(None, 'poseChanged', update=update)
        correction = move_flow.decode_move_direct_bc(result['send'][-1]['body'])
        assert correction.target_instance_id == rid
        assert correction.position[0] == x
    buffers = client.state['sessions']['2']['onlineMotion']['buffers']
    assert set(buffers) == {'2', '12', '3'}
    assert [buffers[str(rid)]['samples'][0]['pose']['position'][0] for rid in (2, 12, 3)] == [2, 12, 3]


def test_old_single_peer_state_is_rejected(monkeypatch, spawnPoints):
    client = running_roster(monkeypatch, spawnPoints)
    match = deepcopy(client.state['onlineScene'])
    match.pop('peers')
    match.update(remoteActorId=2, remoteCamp=2, remoteUserId=900, remoteName='Player')
    assert not online_scene.valid(match)
