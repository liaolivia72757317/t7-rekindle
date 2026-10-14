"""Frozen scene adaptation with an optional peer; room authority stays in Rust."""
from . import online_limits
import re
import struct
from . import contracts as wire, controls
from .codec import room_flow, vision_flow
from .spawn_resource import loadSpawns


def valid(value):
    if (type(value) is not dict or set(value) != {'matchId', 'mapId', 'localActorId', 'localCamp', 'localUserId', 'phase', 'peers'}
            or type(value['matchId']) is not str
            or re.fullmatch(r'[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}', value['matchId']) is None
            or type(value['mapId']) is not int or value['mapId'] not in (1003, 1028)
            or any(type(value[k]) is not int or not 0 < value[k] <= 0xffffffff for k in ('localActorId', 'localUserId'))
            or type(value['localCamp']) is not int or value['localCamp'] not in (1, 2)
            or type(value['phase']) is not int or value['phase'] not in (1, 2, 3, 4)
            or type(value['peers']) is not list or len(value['peers']) >= online_limits.ROOM_CAPACITY):
        return False
    actor_ids, slots, users = {value['localActorId']}, {1}, {value['localUserId']}
    for peer in value['peers']:
        if (type(peer) is not dict or set(peer) != {'actorId', 'localRid', 'camp', 'userId', 'name', 'spawnIndex'}
                or any(type(peer[k]) is not int or not 0 < peer[k] <= 0xffffffff for k in ('actorId', 'localRid', 'userId'))
                or type(peer['camp']) is not int or peer['camp'] not in (1, 2) or type(peer['name']) is not str
                or type(peer['spawnIndex']) is not int or not 0 <= peer['spawnIndex'] < online_limits.TEAM_CAPACITY
                or peer['localRid'] not in (range(online_limits.FRIENDLY_FIRST_RID, online_limits.ROOM_CAPACITY + 1)
                    if peer['camp'] == value['localCamp'] else range(online_limits.ENEMY_FIRST_RID, online_limits.FRIENDLY_FIRST_RID))
                or peer['actorId'] in actor_ids or peer['localRid'] in slots or peer['userId'] in users):
            return False
        actor_ids.add(peer['actorId']); slots.add(peer['localRid']); users.add(peer['userId'])
    return True


def enabled(flow):
    return 'onlineScene' in flow.state


def bindings(match):
    return match['peers']


def actor_rid(match, actor):
    if type(actor) is not int:
        return None
    if actor == match['localActorId']:
        return 1
    return next((p['localRid'] for p in bindings(match) if p['actorId'] == actor), None)


def rid_actor(match, rid):
    if rid == 1:
        return match['localActorId']
    return next((p['actorId'] for p in bindings(match) if p['localRid'] == rid), None)


def ids(flow):
    return (1, *(p['localRid'] for p in bindings(flow.state['onlineScene'])))


def peer_data(session, rid):
    return session['onlinePeers'][str(rid)]


def assemble(flow):
    match = flow.state['onlineScene']
    spawns = loadSpawns(flow.context.get('clientDirectory', ''))
    same_camp = sorted([match['localActorId'], *(p['actorId'] for p in bindings(match) if p['camp'] == match['localCamp'])])
    position, heading = spawns[match['localCamp']][same_camp.index(match['localActorId'])]
    flow.session.update(camp=match['localCamp'], spawnPosition=list(position), spawnHeading=heading)
    peers = {}
    for binding in bindings(match):
        position_peer, heading_peer = spawns[binding['camp']][binding['spawnIndex']]
        peers[str(binding['localRid'])] = dict(position=list(position_peer), heading=heading_peer,
            camp=binding['camp'], spawnPosition=list(position_peer), spawnHeading=heading_peer,
            userId=binding['userId'], name=binding['name'])
    flow.session['onlinePeers'] = peers
    controls.groundState(flow).update(position=list(position), heading=heading)


def peerVision(flow, rid):
    peer = peer_data(flow.session, rid)
    body = wire.actorVision(peer['camp'], actorName=peer['name'].encode('gbk'), runtimeMovement=True,
                            position=tuple(peer['position']), actorId=rid, userId=peer['userId'])
    return body[:45] + struct.pack('>h', peer['heading']) + body[47:]


def peerState(flow, state):
    for rid in ids(flow)[1:]:
        peer = peer_data(flow.session, rid)
        flow.send(0x36, room_flow.encode_actor_update_state(user_id=peer['userId'], actor_mid=rid, actor_state=state,
                  update_time_ms=flow.now), 'online-peer-state')


def importPeer(flow):
    for rid in ids(flow)[1:]:
        peer = peer_data(flow.session, rid)
        flow.send(0xE, peerVision(flow, rid), 'online-peer-vision-add')
        flow.send(0xA, room_flow.encode_instance_update_actor_basic_info(
            server_time_ms=flow.now, instance_id=1, actor_mid=rid, user_id=peer['userId'],
            user_name=peer['name'].encode('gbk'), user_image_id=7, level=1, actor_state=4,
            hero_resource_id=wire.HERO_ID, camp=peer['camp'], start_pattern=wire.START_PATTERN), 'online-peer-basic-info')


def applyAuthority(flow):
    match = flow.state.get('onlineScene')
    if match is None:
        return
    phase = match['phase']
    match['phase'] = phase
    connection, session = flow.connection, flow.session
    for key, value in flow.state['sessions'].items():
        if (value['role'] != 'instance' or not value.get('initialized')
                or int(key) not in flow.context.get('connections', [])):
            continue
        flow.connection, flow.session = int(key), value
        applied = value.get('onlinePhase', 1)
        if phase <= applied:
            continue
        if phase in (2, 3) and not all(value.get(k) for k in ('loaded', 'heroChosen', 'battleEntered')):
            raise ValueError('online start precedes real client readiness')
        if phase == 2:
            flow.send(0xA, wire.roundState(flow.now, 3, 3000), 'online-authority-countdown')
            peerState(flow, 8)
            flow.phase('online-countdown')
        elif phase == 3:
            flow.send(0xA, wire.roundState(flow.now, 4, wire.GAME_MS), 'online-authority-running')
            flow.send(0x36, wire.actorState(flow.now, 6, userId=flow.userId), 'online-actor-running')
            peerState(flow, 6)
            for rid in ids(flow)[1:]:
                flow.send(0xE, vision_flow.encode_vision_del_event(object_mid=rid), 'online-peer-vision-del-at-entry')
                flow.send(0xE, peerVision(flow, rid), 'online-peer-vision-add-at-entry')
            controls.enableGround(flow)
            flow.phase('online-running')
        else:
            value['roundEnded'] = True
            value['groundEnabled'] = False
            # A requested exit still needs its delayed SYNC_LOGOUT confirmation.
            for name in tuple(value.get('pending', {})):
                if name != 'logout':
                    flow.cancel(name)
            controls.activate(flow)
            flow.send(0xA, wire.roundState(flow.now, 5, 0), 'online-authority-cancelled')
            flow.phase('online-cancelled')
        value['onlinePhase'] = phase
    flow.connection, flow.session = connection, session
