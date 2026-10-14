"""Send local native contacts to Rust and render shared combat decisions."""
from . import online_limits
import math
from . import actors, contact_journal, contracts as wire, controls, online_scene, roster
from .codec import attr_flow, battle_flow, kill_flow, room_flow, vision_flow

STRIKES = frozenset((238, 244, 250, 256))


def ledger(session):
    return session.setdefault('onlineCombat', dict(inputSeq=0, eventSeq=0, seen=[], health=100, ready=False))


def valid(session):
    value = session.get('onlineCombat')
    return value is None or (session['role'] == 'instance' and type(value) is dict
        and all(type(value.get(k)) is int and 0 <= value[k] <= limit for k, limit in (
            ('inputSeq', 0xffffffff), ('eventSeq', 0xffffffffffffffff), ('health', 100)))
        and type(value.get('ready')) is bool and type(value.get('seen')) is list and len(value['seen']) <= 64
        and all(type(key) is str and len(key) <= 64 for key in value['seen'])
        and valid_scores(value.get('scores', {})))


def valid_scores(values):
    return (type(values) is dict and len(values) <= online_limits.ROOM_CAPACITY
        and all(key in online_limits.ALL_RIDS and type(value) is dict and set(value) == {'kills', 'deaths', 'streak'}
            and all(type(n) is int and 0 <= n <= kill_flow.MAX_SCORE for n in value.values())
            for key, value in values.items()))


def send(flow, kind, command, **metadata):
    from . import custom_lobby
    gateways = [(int(k), s) for k, s in flow.state['sessions'].items()
        if s.get('customLobby', {}).get('identified') and int(k) in flow.context.get('connections', [])]
    if len(gateways) != 1:
        raise ValueError('online combat requires the identified gateway')
    if len(gateways[0][1]['customLobby']['pending']) >= 6:
        return False
    connection, session = flow.connection, flow.session
    try:
        flow.connection, flow.session = gateways[0]
        custom_lobby.send(flow, kind, command, **metadata)
    finally:
        flow.connection, flow.session = connection, session
    return True


def world_updated(flow):
    match = flow.state['onlineScene']
    if match['phase'] != 3 or flow.session.get('leaving') or not ledger(flow.session)['health']:
        return
    raw = flow.context.get('botWorld', {})
    journal = raw.get('contactJournal')
    if type(journal) is not dict or raw.get('instanceStartedAt') != flow.session['instanceStartedAt']:
        return
    through = journal.get('throughAt')
    if (type(through) not in (int, float) or not math.isfinite(through) or not 0 <= through <= flow.now
            or type(journal.get('batchId')) is not int or journal['batchId'] < 0
            or type(journal.get('events')) is not list or len(journal['events']) > 8192
            or not all(contact_journal.valid(event, through) for event in journal['events'])):
        raise ValueError('invalid online native contact journal')
    state = ledger(flow.session)
    identity = [raw['instanceStartedAt'], journal['batchId']]
    if state.get('cursor') == identity:
        return
    local = raw.get('actors', {}).get('1', {})
    for event in sorted(journal['events'], key=lambda e: (e['occurredAt'], e['along'])):
        if (event['actorId'] != 1 or event['state'] not in STRIKES or event['entity'] != local.get('entity')
                or event['targetId'] not in (0, *online_scene.ids(flow)[1:]) or event['occurredAt'] < event['phaseAt']
                or event['occurredAt'] - event['phaseAt'] > 600):
            continue
        target = 0 if event['targetId'] == 0 else online_scene.rid_actor(match, event['targetId'])
        if target is None:
            continue
        key = f"{event['seq']}:{target}:{int(event.get('weaponContact', False))}"
        if key in state['seen']:
            continue
        if state['inputSeq'] == 0xffffffff:
            raise ValueError('online contact sequence exhausted')
        sequence = state['inputSeq'] + 1
        report = dict(actionSeq=event['seq'], state=event['state'],
            elapsedMs=int(event['occurredAt'] - event['phaseAt']), targetActorId=target,
            position=event['position'], direction=event['direction'], normal=event['normal'], material=event['material'])
        if event.get('weaponContact', False):
            report['weaponContact'] = True
        if not send(flow, 'contact', dict(type='contactInput', payload=dict(
                matchId=match['matchId'], inputSeq=sequence, contact=report)),
                matchId=match['matchId'], inputSeq=sequence):
            return
        state['inputSeq'] = sequence
        state['seen'] = [*state['seen'], key][-64:]
        age = int(flow.now - event['occurredAt'])
        flow.log('INFO', f'online-contact-report input={sequence} action={event["seq"]} state={event["state"]} target={target} sampleAgeMs={age}')
    state['cursor'] = identity


def can_choose(flow):
    return flow.session.get('onlineCombat', {}).get('ready') is True


def message(flow, command, selector, body):
    if flow.session['role'] != 'instance' or ledger(flow.session)['health']:
        return False
    if command == 0x36 and selector == 0x64:
        room_flow.decode_actor_play_request(body)
        if not can_choose(flow) or not flow.session.get('heroChosen'):
            flow.send(command, room_flow.encode_actor_play_response(result=1), 'online-respawn-not-ready')
        elif not flow.session.get('respawnPending'):
            match = flow.state['onlineScene']
            if send(flow, 'respawn', dict(type='respawn', payload=dict(matchId=match['matchId'])), matchId=match['matchId']):
                flow.session['respawnPending'] = True
        return True
    return False


def receive(flow, value):
    from .custom_lobby import integer
    match = flow.state['onlineScene']
    room = flow.session['customLobby'].get('room')
    if value['matchId'] != match['matchId'] or room is not None and value['roomId'] != room['roomId']:
        raise ValueError('online combat belongs to another scene')
    if match['phase'] == 4:
        return
    if match['phase'] != 3 or room is None:
        raise ValueError('online combat precedes Running')
    instances = [(int(k), s) for k, s in flow.state['sessions'].items() if s['role'] == 'instance'
        and s.get('initialized') and not s.get('leaving') and int(k) in flow.context.get('connections', [])]
    if len(instances) != 1:
        raise ValueError('online combat requires one instance')
    connection, session = flow.connection, flow.session
    try:
        flow.connection, flow.session = instances[0]
        state = ledger(flow.session)
        sequence = integer(value['eventSeq'], 1, 0xffffffffffffffff)
        if sequence != state['eventSeq'] + 1:
            raise ValueError('online combat event gap or replay')
        integer(value['serverTimeMs'], 0, 0xffffffffffffffff)
        state['eventSeq'] = sequence
        effect = value['effect']
        kind = effect['type']
        if kind == 'contact':
            contact(flow, effect)
        elif kind == 'respawnReady':
            if rid(flow, effect['actorId']) == 1:
                if state['health']:
                    raise ValueError('alive actor cannot select a respawn')
                state['ready'] = True
                flow.send(0x36, wire.actorState(flow.now, 7, userId=flow.userId), 'online-respawn-select-hero')
        elif kind == 'respawned':
            respawn(flow, rid(flow, effect['actorId']), effect['lives'])
        else:
            raise ValueError('unsupported combat effect')
    finally:
        flow.connection, flow.session = connection, session


def rid(flow, actor):
    match = flow.state['onlineScene']
    result = online_scene.actor_rid(match, actor)
    if result is None:
        raise ValueError('combat actor outside frozen roster')
    return result


def health(flow, lives):
    from .custom_lobby import integer
    if type(lives) is not list or len(lives) != len(online_scene.ids(flow)):
        raise ValueError('invalid combat roster')
    values = {}
    for life in lives:
        actor = rid(flow, life['actorId'])
        if actor in values:
            raise ValueError('duplicate combat actor')
        values[actor] = integer(life['health'], 0, 100)
        for key in ('kills', 'deaths', 'streak'):
            integer(life[key], 0, kill_flow.MAX_SCORE)
    for actor, hp in values.items():
        if actor == 1:
            ledger(flow.session)['health'] = hp
            flow.session['health'] = hp
            flow.send(4, battle_flow.encode_battle_data_update_notify(
                result_type=battle_flow.RESULT_DATA_HP, result_data=hp), 'online-health-local')
        else:
            online_scene.peer_data(flow.session, actor)['health'] = hp
            flow.send(15, attr_flow.encode_health_update(entity_rid=actor, health=hp,
                server_time_ms=flow.now), 'online-health-peer')
    return values


def scores(flow, lives):
    state = ledger(flow.session)
    previous = state.get('scores', {})
    current = {str(rid(flow, life['actorId'])): dict(kills=life['kills'], deaths=life['deaths'], streak=life['streak'])
        for life in lives}
    first_blood = not any(value['kills'] for value in previous.values())
    camp_kills = {1: 0, 2: 0}
    match = flow.state['onlineScene']
    for key, totals in current.items():
        actor = int(key)
        before = previous.get(key, dict(kills=0, deaths=0, streak=0))
        camp_kills[match['localCamp'] if actor == 1 else online_scene.peer_data(flow.session, actor)['camp']] += totals['kills']
        if totals['kills'] != before['kills'] or totals['deaths'] != before['deaths']:
            flow.send(kill_flow.SCORE_COMMAND, kill_flow.encode_score_update_event(server_time_ms=flow.now,
                entity_rid=actor, previous_kills=before['kills'], kills=totals['kills'],
                previous_deaths=before['deaths'], deaths=totals['deaths'],
                previous_points=min(kill_flow.MAX_SCORE, before['kills'] * 100),
                points=min(kill_flow.MAX_SCORE, totals['kills'] * 100),
                stat_message_id=kill_flow.FIRST_BLOOD_MESSAGE_ID if first_blood and totals['kills'] > before['kills'] else 0),
                'online-score')
        if totals['streak'] > before['streak'] and totals['streak'] >= 2:
            flow.send(kill_flow.SCORE_COMMAND, kill_flow.encode_score_stat_message(server_time_ms=flow.now,
                entity_rid=actor, count=totals['streak']), 'online-kill-streak')
        if before['streak'] >= kill_flow.BUTCHER_STREAK and totals['deaths'] > before['deaths']:
            winner = next(int(k) for k in current if current[k]['kills'] > previous.get(k, {}).get('kills', 0))
            flow.send(kill_flow.SCORE_COMMAND, kill_flow.encode_butcher_killer_message(server_time_ms=flow.now,
                entity_rid=winner), 'online-butcher-killer')
    if current != previous:
        flow.send(kill_flow.SCRIPT_COMMAND, kill_flow.encode_camp_kill_update(server_time_ms=flow.now,
            instance_id=1, blue_kills=min(kill_flow.MAX_SCORE, camp_kills[1]),
            red_kills=min(kill_flow.MAX_SCORE, camp_kills[2])), 'online-camp-score')
    state['scores'] = current


def contact(flow, effect):
    from .custom_lobby import integer
    attacker = rid(flow, effect['attacker'])
    target = 0 if effect['target'] is None else rid(flow, effect['target'])
    if attacker == target or type(effect['parried']) is not bool:
        raise ValueError('invalid combat outcome')
    sequence = integer(effect['actionSeq'], 1, 0xffff)
    weapon = integer(effect['weaponId'], 1)
    damage = integer(effect['damage'], 0, 100)
    material = integer(effect['material'], -1, 7)
    for key in ('position', 'normal', 'direction'):
        if not actors.validPosition(effect[key]):
            raise ValueError('invalid combat contact vector')
    if any(not .9 <= math.hypot(*effect[key]) <= 1.1 for key in ('normal', 'direction')):
        raise ValueError('invalid combat contact normal')
    values = health(flow, effect['lives'])
    flow.send(4, battle_flow.encode_battle_hit_point_notify(hit_pos=tuple(effect['position']),
        hit_direction=tuple(effect['direction']), hit_normal=tuple(effect['normal']), material_id=material,
        ignore_hurt=effect['parried'] or target == 0, attacker_mid=attacker, target_mid=target,
        attacker_weapon_id=weapon, target_weapon_id=weapon if effect['parried'] else 0,
        server_time_ms=flow.now), 'online-contact-effect')
    if effect['parried']:
        flow.send(4, battle_flow.encode_battle_defense_notify(attacker_mid=attacker, defender_mid=target,
            attacker_weapon_id=weapon, target_weapon_id=weapon, server_time_ms=flow.now), 'online-parry-sound')
    elif target and damage:
        killed = values[target] == 0
        if attacker == 1:
            flow.send(4, battle_flow.encode_battle_result(attacker_rid=attacker, target_rid=target,
                attacker_seq_no=sequence, target_life_state=battle_flow.LIFE_STATE_KILL if killed else battle_flow.LIFE_STATE_NONE,
                attack_weapon_id=weapon, target_result_type=battle_flow.RESULT_DATA_DELTA_HP,
                target_result_data=-damage), 'online-damage')
        if killed:
            flow.send(kill_flow.INSTANCE_COMMAND, kill_flow.encode_kill_event(server_time_ms=flow.now,
                source_rid=attacker, target_rid=target, weapon_tid=weapon), 'online-kill')
            flow.send(4, battle_flow.encode_battle_dead_notify(entity_rid=target, server_time_ms=flow.now), 'online-dead')
            if target == 1:
                flow.session['heroChosen'] = False
                flow.session['groundEnabled'] = False
                ledger(flow.session)['ready'] = False
                controls.activate(flow)
                flow.send(0x36, wire.actorState(flow.now, 9, userId=flow.userId), 'online-observer')
    scores(flow, effect['lives'])
    flow.log('DEBUG', f'online-contact attacker={attacker} target={target} parried={effect["parried"]} damage={damage}')


def respawn(flow, actor, lives):
    reacting = flow.session.get('onlineActions', {}).get('reacting', [])
    if str(actor) in reacting:
        reacting.remove(str(actor))
    match = flow.state['onlineScene']
    source = flow.session if actor == 1 else online_scene.peer_data(flow.session, actor)
    position, heading = source['spawnPosition'], source['spawnHeading']
    health(flow, lives)
    scores(flow, lives)
    if actor == 1:
        flow.session.pop('respawnPending', None)
        ledger(flow.session).update(ready=False, seen=[])
        flow.session.update(spawnPosition=list(position), spawnHeading=heading)
        controls.groundState(flow).update(position=list(position), heading=heading, mask=0, moveStartedAt=None)
        flow.send(0x36, room_flow.encode_actor_play_response(), 'online-respawn-play')
        flow.send(0x36, wire.actorState(flow.now, 6, userId=flow.userId), 'online-respawn-in-scene')
    else:
        source.update(position=list(position), heading=heading)
        motion = flow.session.get('onlineMotion', {})
        motion = motion.get('buffers', {}).get(str(actor), {})
        for key in ('samples', 'renderedPose', 'peerMovement', 'renderAt'):
            motion.pop(key, None)
    flow.send(0xe, vision_flow.encode_vision_del_event(object_mid=actor), 'online-respawn-remove')
    flow.send(0xe, roster.playerVision(flow) if actor == 1 else online_scene.peerVision(flow, actor), 'online-respawn-add')
    if actor == 1:
        controls.enableGround(flow)
