"""Sample the local native pose; apply only Rust-approved peer poses."""
from . import online_limits
import math

from . import actors, controls
from .codec import move_flow

INTERVAL_MS = 50
RENDER_INTERVAL_MS = 16
INTERPOLATION_MS = 100
MAX_SAMPLES = 32
STALE_MS = 500
RENDER_TIMER = 'online-motion-render'


def valid(session):
    value = session.get('onlineMotion')
    return value is None or (session['role'] == 'instance' and type(value) is dict
        and {'inputSeq', 'eventSeq', 'sentAt', 'actors', 'buffers'} <= set(value)
        and set(value) <= {'inputSeq', 'eventSeq', 'sentAt', 'actors', 'buffers', 'lastSent'}
        and type(value.get('inputSeq')) is int and 0 <= value['inputSeq'] <= 0xffffffff
        and type(value.get('eventSeq')) is int and 0 <= value['eventSeq'] <= 0xffffffffffffffff
        and type(value.get('sentAt')) is int and value['sentAt'] >= -INTERVAL_MS
        and type(value.get('actors')) is dict and len(value['actors']) <= online_limits.ROOM_CAPACITY
        and all(key in online_limits.ALL_RIDS and type(seq) is int and 0 < seq <= 0xffffffff
                for key, seq in value['actors'].items())
        and (type(value["buffers"]) is dict and len(value["buffers"]) <= online_limits.ROOM_CAPACITY - 1
            and all(key in online_limits.ALL_RIDS - {"1"} and type(buffer) is dict and valid_buffer(buffer)
                for key, buffer in value["buffers"].items())))


def valid_buffer(value):
    samples = value.get('samples', [])
    if type(samples) is not list or len(samples) > MAX_SAMPLES:
        return False
    previous = -1
    for sample in samples:
        if type(sample) is not dict or type(sample.get('at')) is not int or sample['at'] < previous:
            return False
        pose = sample.get('pose')
        if (type(pose) is not dict or not actors.validPosition(pose.get('position'))
                or type(pose.get('heading')) is not int or not -180 <= pose['heading'] <= 180
                or any(type(pose.get(k)) is not int or not -1 <= pose[k] <= 1 for k in ('forwardBack', 'leftRight'))
                or type(pose.get('running')) is not bool or pose['running'] and pose['forwardBack'] != -1):
            return False
        previous = sample['at']
    return (all(type(value[k]) is int for k in ('clockOffset', 'renderAt', 'renderedAt') if k in value)
            and (not samples or 'clockOffset' in value))


def ledger(session):
    return session.setdefault('onlineMotion', {'inputSeq': 0, 'eventSeq': 0,
        'sentAt': -INTERVAL_MS, 'actors': {}, 'buffers': {}})


def world_updated(flow):
    from . import custom_lobby
    match = flow.state['onlineScene']
    if match['phase'] != 3 or flow.session.get('leaving') or not controls.groundEnabled(flow):
        return
    render(flow)
    snapshot = actors.world(flow)
    pose = actors.pose(snapshot, 1)
    if pose is None or type(pose.get('heading')) not in (int, float) or not math.isfinite(pose['heading']):
        return
    state = ledger(flow.session)
    if flow.now - state['sentAt'] < INTERVAL_MS:
        return
    gateways = [(int(key), value) for key, value in flow.state['sessions'].items()
                if value.get('customLobby', {}).get('identified') and int(key) in flow.context.get('connections', [])]
    if len(gateways) != 1:
        raise ValueError('online pose requires the identified logic gateway')
    gateway = gateways[0][1]['customLobby']
    # Keep one pose in flight and leave capacity for attack, heartbeat and exit.
    if len(gateway['pending']) >= 6 or any(v['kind'] == 'pose' for v in gateway['pending'].values()):
        return
    ground = controls.groundState(flow)
    mask = max(0, ground['mask'])
    forward, lateral = ((mask >> 2) & 1) - (mask & 1), ((mask >> 1) & 1) - ((mask >> 3) & 1)
    if forward == 0 and lateral == 0:
        ground['moveStartedAt'] = None
    elif ground.get('moveStartedAt') is None:
        ground['moveStartedAt'] = flow.now
    running = (forward == -1 and ground.get('moveStartedAt') is not None
               and flow.now - ground['moveStartedAt'] >= controls.GROUND_RUN_DELAY_MS)
    value = dict(position=list(pose['position']), heading=round((pose['heading'] + 180) % 360 - 180),
                 forwardBack=forward, leftRight=lateral, running=running)
    if state.get('lastSent') == value and flow.now - state['sentAt'] < 500:
        return
    if state['inputSeq'] == 0xffffffff:
        raise ValueError('online pose sequence exhausted')
    state['inputSeq'] += 1
    state.update(sentAt=flow.now, lastSent=value)
    connection, session = flow.connection, flow.session
    try:
        flow.connection, flow.session = gateways[0]
        custom_lobby.send(flow, 'pose', {'type': 'poseInput', 'payload': {
            'matchId': match['matchId'], 'inputSeq': state['inputSeq'], 'pose': value}},
            matchId=match['matchId'], inputSeq=state['inputSeq'])
    finally:
        flow.connection, flow.session = connection, session


def receive(flow, value):
    from .custom_lobby import integer
    match = flow.state.get('onlineScene')
    room = flow.session['customLobby'].get('room')
    if match is None or value['matchId'] != match['matchId'] or room is not None and value['roomId'] != room['roomId']:
        raise ValueError('online pose belongs to another scene')
    if match['phase'] == 4:
        return
    if match['phase'] != 3 or room is None:
        raise ValueError('online pose precedes Running')
    actor = integer(value['actorId'], 1, 0xffffffff)
    from . import online_scene
    rid = online_scene.actor_rid(match, actor)
    if rid is None:
        raise ValueError('online pose actor is outside the frozen roster')
    event_sequence = integer(value['eventSeq'], 1, 0xffffffffffffffff)
    sequence = integer(value['inputSeq'], 1, 0xffffffff)
    server_time = integer(value['serverTimeMs'], 0, 0xffffffffffffffff)
    pose = value['pose']
    if not actors.validPosition(pose['position']):
        raise ValueError('invalid online position')
    heading = integer(pose['heading'], -180, 180)
    forward, lateral = integer(pose['forwardBack'], -1, 1), integer(pose['leftRight'], -1, 1)
    running = pose['running']
    if type(running) is not bool or running and forward != -1:
        raise ValueError('invalid online movement gait')
    instances = [(int(key), session) for key, session in flow.state['sessions'].items()
        if session['role'] == 'instance' and session.get('initialized') and not session.get('leaving')
        and int(key) in flow.context.get('connections', [])]
    if len(instances) != 1:
        raise ValueError('online pose requires one live instance')
    connection, session = instances[0]
    state = ledger(session)
    if event_sequence != state['eventSeq'] + 1 or sequence <= state['actors'].get(str(rid), 0):
        raise ValueError('online pose sequence has a gap or replay')
    state['eventSeq'], state['actors'][str(rid)] = event_sequence, sequence
    if rid == 1:
        return
    state = state.setdefault('buffers', {}).setdefault(str(rid), {})
    samples = state.setdefault('samples', [])
    if samples and server_time < samples[-1]['at']:
        raise ValueError('online pose clock moved backwards')
    state['clockOffset'] = min(state.get('clockOffset', flow.now - server_time), flow.now - server_time)
    sample = dict(at=server_time, pose=pose)
    if samples and abs(pose['position'][2] - samples[-1]['pose']['position'][2]) > .01:
        # Continue correcting a vertical hold when input has no horizontal axes.
        state['verticalUntil'] = flow.now + STALE_MS
    if not samples or server_time - samples[-1]['at'] > STALE_MS or math.dist(
            pose['position'], samples[-1]['pose']['position']) > 8:
        samples[:] = [sample]
        state.pop('peerMovement', None)
        state.pop('renderAt', None)
        emit(flow, connection, session, pose, 'online-pose-reset', rid, state)
    else:
        samples.append(sample)
        del samples[:-MAX_SAMPLES]
    current_connection, current_session = flow.connection, flow.session
    try:
        flow.connection, flow.session = connection, session
        if RENDER_TIMER not in session['pending']:
            flow.later(RENDER_TIMER, RENDER_INTERVAL_MS)
    finally:
        flow.connection, flow.session = current_connection, current_session


def timer(flow, name):
    if name != RENDER_TIMER:
        return False
    match = flow.state.get('onlineScene')
    if match is None or match['phase'] != 3 or flow.session.get('leaving'):
        return True
    render(flow)
    value = ledger(flow.session)
    buffers = value.get('buffers', {}).values()
    if any(buffer.get('samples') and (buffer.get('renderAt', 0) < buffer['samples'][-1]['at']
            or flow.now - buffer['clockOffset'] - buffer['samples'][-1]['at'] <= STALE_MS + INTERPOLATION_MS
                and (buffer['samples'][-1]['pose']['forwardBack'] or buffer['samples'][-1]['pose']['leftRight'])
            or flow.now <= buffer.get('verticalUntil', -1)) for buffer in buffers):
        flow.later(RENDER_TIMER, RENDER_INTERVAL_MS)
    return True


def render(flow):
    value = ledger(flow.session)
    for key, buffer in value.get('buffers', {}).items():
        render_peer(flow, int(key), buffer)


def render_peer(flow, rid, state):
    samples = state.get('samples', [])
    if not samples or flow.now - state.get('renderedAt', -RENDER_INTERVAL_MS) < RENDER_INTERVAL_MS:
        return
    at = max(state.get('renderAt', samples[0]['at']), flow.now - state['clockOffset'] - INTERPOLATION_MS)
    state['renderAt'] = at
    while len(samples) > 2 and samples[1]['at'] <= at:
        samples.pop(0)
    before, after = samples[0], samples[1] if len(samples) > 1 else samples[0]
    if at >= after['at']:
        pose = dict(after['pose'])
    else:
        span = after['at'] - before['at']
        fraction = max(0.0, min(1.0, (at - before['at']) / span)) if span else 1.0
        pose = dict(before['pose'])
        pose['position'] = [a + (b - a) * fraction for a, b in zip(before['pose']['position'], after['pose']['position'])]
        delta = (after['pose']['heading'] - before['pose']['heading'] + 180) % 360 - 180
        pose['heading'] = round((before['pose']['heading'] + delta * fraction + 180) % 360 - 180)
    if at - samples[-1]['at'] > STALE_MS:
        pose.update(forwardBack=0, leftRight=0, running=False)
    emit(flow, flow.connection, flow.session, pose, 'online-pose-interpolated', rid, state)


def emit(flow, connection, session, pose, reason, rid, state):
    from . import online_scene
    tick = flow.now - session['instanceStartedAt']
    if not 0 <= tick <= controls.MAX_MOVE_TICK:
        raise ValueError('online pose time outside instance')
    forward, lateral, running = pose['forwardBack'], pose['leftRight'], pose['running']
    heading = pose['heading']
    movement = (forward, lateral, running)
    reacting = str(rid) in session.get('onlineActions', {}).get('reacting', [])
    if reacting:
        # Resume with the latest gait only after the next authoritative action state.
        state.pop('peerMovement', None)
    if not reacting and state.get('peerMovement') != list(movement):
        state['peerMovement'] = list(movement)
        moving = forward != 0 or lateral != 0
        walk = (controls.GROUND_RUN_STATES if running else controls.GROUND_WALK_STATES).get((forward, lateral), 1)
        body = move_flow.encode_move_bc_with_system_and_active(server_tick=tick, target_instance_id=rid,
            position=tuple(pose['position']), direction_yaw=heading, state=walk,
            forward_back=forward * 1000, left_right=lateral * 1000,
            current_velocity=(5000 if running else 3000) if moving else 0,
            max_velocity=25000 if moving else 0, system_group=2, active=1, acceleration=0)
        flow.result['send'].append(dict(connection=connection, command=2, body=body, reason='online-peer-movement'))
    state['renderedAt'] = flow.now
    if (state.get('renderedPose') == pose and forward == lateral == 0
            and flow.now > state.get('verticalUntil', -1)):
        return
    # Native locomotion continues between corrections at the same local gait speed.
    state['renderedPose'] = pose
    body = move_flow.encode_move_direct_bc(server_tick=tick, target_instance_id=rid,
        direction_yaw=heading, position=tuple(pose['position']))
    flow.result['send'].append(dict(connection=connection, command=2, body=body,
        reason=reason))
    online_scene.peer_data(session, rid).update(position=list(pose['position']), heading=heading)
