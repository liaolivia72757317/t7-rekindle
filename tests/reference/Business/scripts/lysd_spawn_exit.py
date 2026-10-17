"""Preserve in-flight platform exits across reloads; new respawns use battlefield ground."""
import math

from . import actors, body_collision, spawn_navigation
from .codec import move_flow

LANDING_MS, EXIT_TIMEOUT_MS = 200, 45000


def enabled(flow):
    return spawn_navigation.enabled(flow)


def begin(flow, actorId):
    value = actors.data(flow, actorId)
    if enabled(flow):
        value['spawnExit'] = dict(origin=list(value['position']), startedAt=flow.now, seeded=False)


def validState(value):
    state = value.get('spawnExit')
    if state is None:
        return True
    return (type(state) is dict and actors.validPosition(state.get('origin'))
            and type(state.get('seeded')) is bool
            and type(state.get('landed', False)) is bool
            and type(state.get('startedAt')) is int and state['startedAt'] >= 0
            and all(type(state.get(key, 0)) is int and state.get(key, 0) >= 0
                    for key in ('sampledAt', 'stableSince', 'detourUntil'))
            and all(key not in state or actors.validPosition(state[key]) for key in ('lastPosition', 'entry'))
            and ('detourHeading' not in state or type(state['detourHeading']) is int
                 and -180 <= state['detourHeading'] <= 180))


def steer(flow, actorId, moving):
    value = actors.data(flow, actorId)
    flow.send(2, move_flow.encode_move_in_air_direct_bc(
        server_tick=flow.now - flow.session['instanceStartedAt'], target_instance_id=actorId,
        direction_yaw=value['heading'], left_right=0, forward_back=-1000 if moving else 0),
        'bot-spawn-exit-steer' if moving else 'bot-spawn-exit-stop')
    value['moving'] = moving


def entryFor(flow, nav, value, anchor):
    points = tuple((tuple(point), heading) for point, heading in flow.session['respawnPoints'][str(value['camp'])])
    groups = spawn_navigation.candidates(nav, points, tuple(anchor))
    index = min(range(len(points)), key=lambda i: math.dist(points[i][0], value['spawnExit']['origin']))
    return groups[index][0][0] if groups[index] else None


def pause(flow, actorId):
    from . import bot
    state = actors.data(flow, actorId).get('spawnExit')
    if state is not None and state['seeded']:
        bot.stopMotion(flow, actorId)
        state['seeded'] = False
        state.pop('detourUntil', None)
        state.pop('detourHeading', None)


def localPatrol(flow, nav, actorId, reason):
    from . import bot
    value = actors.data(flow, actorId)
    bot.stopMotion(flow, actorId)
    value.pop('spawnExit')
    anchor = value.get('patrolAnchor', value['position'])
    if nav.findPath(value['position'], anchor).status != 'ready':
        value['patrolAnchor'] = list(value['position'])
    value['repathAt'] = 0
    flow.log("DEBUG", f"bot-spawn-return-local actor={actorId} reason={reason} position={value['position']}")


def returnToBattle(flow, nav, observed, actorId):
    value = actors.data(flow, actorId)
    state = value.get('spawnExit')
    if state is None or not state.get('landed') or flow.now < value.get('searchUntil', 0):
        return False
    return guide(flow, nav, observed, actorId)


def handle(flow, nav, observed, actorId):
    from . import bot, roster
    value = actors.data(flow, actorId)
    state = value.get('spawnExit')
    if state is None:
        return False
    if not enabled(flow):
        bot.stopMotion(flow, actorId)
        value.pop('spawnExit')
        return False
    snapshot = actors.world(flow)
    if snapshot['sampledAt'] <= state.get('sampledAt', -1):
        return not state.get('landed', False)
    position = observed['position']
    value['position'] = list(position)
    sampledAt = snapshot['sampledAt']
    previous = state.get('lastPosition')
    state.update(lastPosition=list(position), sampledAt=sampledAt)
    ground = nav.project(position)
    onGround = ground is not None and abs(position[2] - ground[2]) <= .4
    if not onGround:
        if state.get('landed'):
            bot.stopMotion(flow, actorId)
            value['initialized'] = False
        state['landed'] = False
        state.pop('stableSince', None)
    elif not state.get('landed'):
        if previous is not None and abs(position[2] - previous[2]) <= .05:
            state.setdefault('stableSince', sampledAt)
            if sampledAt - state['stableSince'] >= LANDING_MS:
                state['landed'] = True
                bot.stopMotion(flow, actorId)
                flow.log("DEBUG", f'bot-spawn-landed actor={actorId} position={position}')
        else:
            state.pop('stableSince', None)
    try:
        anchor = roster.loadSpawns(flow.context.get('clientDirectory', ''))[value['camp']][0][0]
    except (OSError, ValueError) as error:
        bot.stopMotion(flow, actorId)
        bot.status(flow, 'spawn-exit-resource-unavailable' if isinstance(error, OSError) else 'spawn-exit-resource-invalid', actorId)
        return not state.get('landed', False)
    faces = spawn_navigation.connectedFaces(nav, tuple(anchor))
    onMainGround = ground is not None and nav.locate(position) in faces
    # Ordinary on-mesh spawns need no physical exit. A platform exit requires stable landing observations.
    if not state['seeded'] and nav.locate(state['origin']) in faces and onMainGround:
        value.pop('spawnExit')
        return False
    if state.get('landed'):
        if onMainGround and nav.findPath(position, anchor).status == 'ready':
            bot.stopMotion(flow, actorId)
            value.pop('spawnExit')
            value['repathAt'] = 0
            flow.log("DEBUG", f'bot-spawn-exit-complete actor={actorId} position={position}')
        return False
    return guide(flow, nav, observed, actorId, settling=onGround)


def guide(flow, nav, observed, actorId, *, settling=False):
    from . import bot, roster
    value = actors.data(flow, actorId)
    state = value['spawnExit']
    position = observed['position']
    snapshot = actors.world(flow)
    if flow.now - state['startedAt'] >= EXIT_TIMEOUT_MS:
        if state.get('landed'):
            localPatrol(flow, nav, actorId, 'timeout')
            return False
        steer(flow, actorId, False)
        bot.status(flow, 'spawn-exit-timeout', actorId)
        return True
    if 'entry' not in state:
        try:
            anchor = roster.loadSpawns(flow.context.get('clientDirectory', ''))[value['camp']][0][0]
            entry = entryFor(flow, nav, value, anchor)
        except (OSError, ValueError):
            if state.get('landed'):
                localPatrol(flow, nav, actorId, 'resource-unavailable')
                return False
            bot.stopMotion(flow, actorId)
            bot.status(flow, 'spawn-exit-resource-unavailable', actorId)
            return True
        if entry is None:
            if state.get('landed'):
                localPatrol(flow, nav, actorId, 'no-entry')
                return False
            bot.stopMotion(flow, actorId)
            bot.status(flow, 'spawn-exit-no-entry', actorId)
            return True
        state['entry'] = list(entry)
        flow.log("DEBUG", f"bot-spawn-exit actor={actorId} origin={state['origin']} observed={position} entry={state['entry']}")
    entry = state['entry']
    desiredHeading = round(math.degrees(math.atan2(-(entry[1] - position[1]), entry[0] - position[0])))
    if math.dist(entry[:2], position[:2]) < body_collision.DIAMETER:
        desiredHeading = value['heading']
    probe = observed.get('terrain', {}).get('forward', {})
    terrainReady = probe.get('ready') is True and type(probe.get('hit')) is bool
    if terrainReady and probe['hit'] and flow.now >= state.get('detourUntil', 0):
        state.update(detourHeading=round((observed['heading'] + (90 if actorId % 2 else -90) + 180) % 360 - 180),
                     detourUntil=flow.now + 800)
    if flow.now < state.get('detourUntil', 0):
        desiredHeading = state['detourHeading']
    remaining = bot.turnToward(flow, desiredHeading, actorId)
    angle = math.radians(value['heading'])
    desired = [position[0] + .4 * math.cos(angle), position[1] - .4 * math.sin(angle), position[2]]
    bodyClear = math.dist(body_collision.clip(position, desired, body_collision.obstacles(flow, actorId, snapshot)), desired) < .001
    moving = (terrainReady and not probe['hit'] and bodyClear and remaining <= 15
              and abs(bot.headingDelta(observed['heading'], value['heading'])) <= 15 and not settling)
    if not state['seeded']:
        bot.stopMotion(flow, actorId)
        # Runtime omits this command's position correction, retaining native contact and gravity.
        flow.send(2, move_flow.encode_move_bc_with_system_and_active(
            server_tick=flow.now - flow.session['instanceStartedAt'], target_instance_id=actorId,
            position=tuple(position), direction_yaw=value['heading'], state=2, left_right=0, forward_back=0,
            current_velocity=3000, max_velocity=3000, acceleration=0, system_group=actorId, active=1), 'bot-spawn-exit-start')
        state['seeded'] = True
    steer(flow, actorId, moving)
    prefix = 'spawn-return-' if state.get('landed') else 'spawn-exit-'
    bot.status(flow, prefix + ('landing' if settling else 'moving' if moving else 'waiting'), actorId)
    return True
