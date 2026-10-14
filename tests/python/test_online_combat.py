from copy import deepcopy
import json
import struct
import pytest
from Business.scripts.codec import battle_flow
from test_online_actions import running
from test_custom_scene import MATCH, instance


def lives(local=100, peer=80):
    return [dict(actorId=1, health=local, kills=int(peer == 0), deaths=int(local == 0), streak=0),
            dict(actorId=2, health=peer, kills=int(local == 0), deaths=int(peer == 0), streak=0)]


def event(effect, sequence=1):
    return dict(roomId='room-test', matchId=MATCH, eventSeq=sequence, serverTimeMs=3500, effect=effect)


def effect(attacker=1, target=2, parried=False, health=None):
    return dict(type='contact', attacker=attacker, target=target, actionSeq=3, weaponId=1030411,
        parried=parried, damage=0 if parried else 20, position=[1.,0.,1.5], normal=[-1.,0.,0.],
        direction=[1.,0.,0.], material=4, lives=health or lives())


@pytest.mark.parametrize('weapon_contact', [False, True])
def test_native_contact_only_reports_local_actor_and_never_locally_awards_damage(monkeypatch, spawnPoints, weapon_contact):
    client = running(monkeypatch, spawnPoints, peer=True)
    client.context['nowMs'] = 1000
    epoch = client.state['sessions']['2']['instanceStartedAt']
    hit = dict(ready=True, hit=True, actorId=1, targetId=2, entity=101, targetEntity=102,
        state=238, seq=3, targetState=2, targetSeq=1, phaseAt=900, targetPhaseAt=800,
        occurredAt=950., along=.5, sourcePosition=[0.,0.,.5], targetPosition=[1.2,0.,.5],
        sourceHeading=0., targetHeading=180., position=[.9,0.,1.5], normal=[-1.,0.,0.], direction=[1.,0.,0.], material=4)
    hit['weaponContact'] = weapon_contact
    client.context['botWorld'] = dict(ready=True, sampledAt=1000, instanceStartedAt=epoch,
        actors={'1':dict(ready=True, entity=101, position=[0.,0.,.5], heading=0.)},
        contactJournal=dict(throughAt=1000, batchId=1, frames=10, dropped=0,
            events=[hit, {**hit, 'actorId':2, 'targetId':1, 'entity':102, 'targetEntity':101}]))
    result = client.event('world')
    request = next(json.loads(v['request']) for v in result['gateway'] if 'contactInput' in v['request'])
    contact = request['command']
    assert contact['payload']['contact']['targetActorId'] == 2
    assert contact['payload']['contact']['elapsedMs'] == 50
    assert contact['payload']['contact'].get('weaponContact', False) == weapon_contact
    assert not result['send']
    client.context['botWorld']['contactJournal']['batchId'] = 2
    assert not client.event('world').get('gateway')
    if weapon_contact:
        client.reply(int(request['requestId']), 'contactAccepted', matchId=MATCH, inputSeq=1, accepted=False)
        client.context['botWorld']['contactJournal'].update(batchId=3, events=[{**hit, 'weaponContact': False}])
        result = client.event('world')
        body = next(json.loads(v['request'])['command'] for v in result['gateway'] if 'contactInput' in v['request'])
        assert body['payload']['inputSeq'] == 2 and not body['payload']['contact'].get('weaponContact', False)


def test_authority_health_and_damage_float_use_each_clients_local_mapping(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    result = client.reply(None, 'combatChanged', update=event(effect()))
    assert any(p['reason']=='online-health-peer' for p in result['send'])
    feedback = next(p for p in result['send'] if p['reason']=='online-contact-effect')
    assert battle_flow.decode_battle_hit_point_notify(feedback['body']).target_mid == 2
    assert any(p['reason']=='online-damage' for p in result['send'])
    assert client.state['sessions']['2']['onlinePeers']['2']['health'] == 80
    before=deepcopy(client.state)
    with pytest.raises(ValueError):client.reply(None, 'combatChanged', update=event(effect()))
    assert client.state==before


def test_real_parry_emits_dedicated_sound_and_weapon_contact(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    result=client.reply(None,'combatChanged',update=event(effect(parried=True,health=lives(peer=100))))
    feedback=next(p for p in result['send'] if p['reason']=='online-contact-effect')
    assert battle_flow.decode_battle_hit_point_notify(feedback['body']).target_weapon_id==1030411
    assert any(p['body'][:2]==b'\0\x28' and p['reason']=='online-parry-sound' for p in result['send'])
    assert not any(p['reason']=='online-damage' for p in result['send'])


def test_death_waits_for_rust_ready_selection_and_play_then_recreates_actor(monkeypatch, spawnPoints):
    client=running(monkeypatch,spawnPoints,peer=True)
    result=client.reply(None,'combatChanged',update=event(effect(attacker=2,target=1,health=lives(local=0,peer=100))))
    assert any(p['reason']=='online-observer' for p in result['send'])
    assert not client.state['sessions']['2']['groundEnabled']
    result=instance(client,command=0x36,body=struct.pack('>HHb',0x64,1,0))
    assert any(p['reason']=='online-respawn-not-ready' for p in result['send'])
    client.reply(None,'combatChanged',update=event(dict(type='respawnReady',actorId=1),2))
    result=instance(client,command=0x36,body=struct.pack('>Hi',0x32,1))
    assert any(p['reason']=='actor-choose-hero-result-zero' for p in result['send'])
    result=instance(client,command=0x36,body=struct.pack('>HHb',0x64,1,0))
    assert json.loads(result['gateway'][0]['request'])['command']['type']=='respawn'
    client.reply(6,'respawnAccepted',matchId=MATCH)
    result=client.reply(None,'combatChanged',update=event(dict(type='respawned',actorId=1,lives=lives(peer=100)),3))
    assert any(p['reason']=='online-respawn-add' for p in result['send'])
    assert client.state['sessions']['2']['groundEnabled'] and client.state['sessions']['2']['onlineCombat']['health']==100


def test_scores_first_blood_camp_counts_and_streaks_follow_authoritative_kills(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    first = lives(peer=0)
    first[0]['streak'] = 1
    result = client.reply(None, 'combatChanged', update=event(effect(health=first)))
    updates = [p for p in result['send'] if p['reason'] == 'online-score']
    assert len(updates) == 2
    assert sum(struct.unpack_from('>i', p['body'], 18)[0] == 730000 for p in updates) == 1
    assert any(p['reason'] == 'online-camp-score' for p in result['send'])
    assert not any(p['reason'] == 'online-kill-streak' for p in result['send'])
    restored = deepcopy(first)
    restored[1]['health'] = 100
    client.reply(None, 'combatChanged', update=event(dict(type='respawned', actorId=2, lives=restored), 2))
    second = deepcopy(first)
    second[0].update(kills=2, streak=2)
    second[1]['deaths'] = 2
    result = client.reply(None, 'combatChanged', update=event(effect(health=second), 3))
    assert all(struct.unpack_from('>i', p['body'], 18)[0] == 0 for p in result['send'] if p['reason'] == 'online-score')
    banner = next(p for p in result['send'] if p['reason'] == 'online-kill-streak')
    assert struct.unpack_from('>ii', banner['body'], 18) == (710020, 2)


@pytest.mark.parametrize('field,value',[('actorId',9),('matchId','other'),('eventSeq',2)])
def test_combat_events_cannot_cross_the_frozen_roster_or_sequence(field,value,monkeypatch,spawnPoints):
    client=running(monkeypatch,spawnPoints,peer=True);update=event(effect())
    if field=='actorId':update['effect']['attacker']=value
    else:update[field]=value
    before=deepcopy(client.state)
    with pytest.raises(ValueError):client.reply(None,'combatChanged',update=update)
    assert client.state==before
