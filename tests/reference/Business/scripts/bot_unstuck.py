"""Recover blocked routes and off-mesh poses through observed native movement."""
from dataclasses import dataclass
import math

from . import actors, body_collision, bot_tactics, spawn_navigation
from .navigation import CELL_SIZE, NavPath, height

STUCK_MS, RECOVERY_MS, RETRY_MS = 1200, 1500, 1000
SAMPLE_GAP_MS = 250
PROGRESS_DISTANCE, ESCAPE_DISTANCE, ARRIVAL_RADIUS = .2, 1.2, .25
ESCAPE_DISTANCES = (ESCAPE_DISTANCE, .8, .5)
TURNS = (90, -90, 180, 135, -135, 45, -45, 0, 112.5, -112.5, 157.5, -157.5, 67.5, -67.5, 22.5, -22.5)
MESH_HEIGHT, MESH_STEP, MESH_TURN_ANGLE = 1.5, .75, 15.
MESH_GOAL_COUNT = 8


@dataclass(frozen=True)
class Recovery:
    goal: tuple = None
    route: NavPath = None


def validState(value):
    if 'meshRecovery' in value:
        state = value['meshRecovery']
        if (type(state) is not dict or type(state.get('attempt')) is not int or state['attempt'] < 1
                or type(state.get('retryAt')) is not int or state['retryAt'] < 0
                or 'goal' not in state or state['goal'] is not None and not actors.validPosition(state['goal'])):
            return False
    if 'motionProbe' in value:
        probe = value['motionProbe']
        if (type(probe) is not dict or not actors.validPosition(probe.get('position'))
                or any(type(probe.get(key)) is not int or probe[key] < 0
                       for key in ('since', 'sampledAt', 'attemptedAt'))
                or not probe['since'] <= probe['sampledAt'] <= probe['attemptedAt']):
            return False
    if 'unstuck' not in value:
        return True
    state = value['unstuck']
    return (type(state) is dict and type(state.get('attempt')) is int and state['attempt'] >= 1
            and type(state.get('turn')) is int and 0 <= state['turn'] < len(TURNS)
            and type(state.get('heading')) in (int, float) and math.isfinite(state['heading'])
            and -180 <= state['heading'] <= 180
            and type(state.get('until')) is int and state['until'] >= 0
            and 'goal' in state and (state['goal'] is None or actors.validPosition(state['goal'])))


def observe(flow, actorId):
    """Call only when trying to translate, including body-blocked movement."""
    value = actors.data(flow, actorId)
    snapshot = actors.world(flow)
    pose = actors.pose(snapshot, actorId)
    if pose is None:
        value.pop('motionProbe', None)
        return False
    sampledAt, position = snapshot['sampledAt'], pose['position']
    previous = value.get('motionProbe')
    if (previous is None or flow.now - previous['attemptedAt'] > SAMPLE_GAP_MS
            or not 0 <= sampledAt - previous['sampledAt'] <= SAMPLE_GAP_MS
            or math.dist(position, previous['position']) >= PROGRESS_DISTANCE):
        value['motionProbe'] = dict(position=list(position), since=sampledAt,
                                    sampledAt=sampledAt, attemptedAt=flow.now)
        return False
    value['motionProbe'] = dict(previous, sampledAt=sampledAt, attemptedAt=flow.now)
    return sampledAt - previous['since'] >= STUCK_MS


def escapeRoute(flow, nav, actorId, heading, start):
    value = actors.data(flow, actorId)
    origin = value['position']
    side = value.get('lane', 0) or (1 if actorId % 2 else -1)
    bodies = body_collision.obstacles(flow, actorId, actors.world(flow))
    # Tight corners may allow only a short step, or an opening ahead of the blocked route.
    for distance in ESCAPE_DISTANCES:
        for offset in range(len(TURNS)):
            turn = (start + offset) % len(TURNS)
            angle = math.radians(-heading + TURNS[turn] * side)
            goal = bot_tactics.groundGoal(nav, bot_tactics.pointAt(origin, angle, distance))
            if goal is None:
                continue
            route = nav.findPath(origin, goal)
            length = sum(math.dist(a[:2], b[:2]) for a, b in zip(route.points, route.points[1:]))
            if route.status != 'ready' or length > math.dist(origin[:2], goal[:2]) + .05:
                continue
            elevated = [(*point[:2], point[2] + value.get('heightOffset', 0)) for point in route.points]
            if any(math.dist(body_collision.clip(a, b, bodies, slide=False), b) > .01
                   for a, b in zip(elevated, elevated[1:])):
                continue
            return turn, Recovery(goal, route)
    return start, Recovery()


def plan(flow, nav, actorId, heading=None):
    """Keep an escape goal until arrival or timeout; None means ordinary movement."""
    value = actors.data(flow, actorId)
    previous = value.get('unstuck')
    if previous is None and heading is None:
        return None
    if previous is not None:
        goal = previous['goal']
        if goal is not None and math.dist(value['position'][:2], goal[:2]) <= ARRIVAL_RADIUS:
            value.pop('unstuck')
            value.pop('motionProbe', None)
            value['repathAt'] = 0
            return None
        if flow.now < previous['until']:
            return Recovery(tuple(goal) if goal is not None else None)
    heading = previous['heading'] if previous is not None else heading
    start = (previous['turn'] + 1) % len(TURNS) if previous is not None else 0
    turn, recovery = escapeRoute(flow, nav, actorId, heading, start)
    attempt = previous['attempt'] + 1 if previous is not None else 1
    value['unstuck'] = dict(attempt=attempt, heading=heading, turn=turn,
                            goal=list(recovery.goal) if recovery.goal is not None else None,
                            until=flow.now + (RECOVERY_MS if recovery.goal is not None else RETRY_MS))
    for key in ('motionProbe', 'tactic', 'assist', 'footwork', 'stepPlan', 'routeGoal', 'roamGoal'):
        value.pop(key, None)
    value.update(path=[], step=0, repathAt=0, forwardMs=0)
    mode = 'escape' if recovery.goal is not None else 'wait'
    distance = math.dist(value['position'][:2], recovery.goal[:2]) if recovery.goal is not None else 0.
    flow.log("DEBUG", f'bot-stuck actor={actorId} attempt={attempt} mode={mode} '
                              f'turn={TURNS[turn]} distance={distance:.2f}')
    return recovery


def meshGoals(nav, position):
    """Nearest body-clear entries, searching spatial cells rather than face centers."""
    def cellDistance(cell):
        return math.hypot(*(max(c * CELL_SIZE - p, 0., p - (c + 1) * CELL_SIZE)
                            for c, p in zip(cell, position[:2])))

    probes = [position] + [bot_tactics.pointAt(position, math.radians(angle), MESH_STEP)
                           for angle in range(0, 360, 45)]
    candidates, visited, nearestDistance = set(), set(), math.inf
    for cell in sorted(nav.cells, key=cellDistance):
        if cellDistance(cell) > nearestDistance + MESH_STEP:
            break
        for face in nav.cells[cell]:
            if face in visited:
                continue
            visited.add(face)
            interior = spawn_navigation.inset(nav.polygons[face])
            if not interior:
                continue
            for probe in probes:
                xy = spawn_navigation.nearest(interior, probe)
                if xy is None or math.dist(position[:2], xy) <= ARRIVAL_RADIUS:
                    continue
                z = height(nav.polygons[face], xy)
                if z is None or abs(z - position[2]) > MESH_HEIGHT:
                    continue
                point = (*xy, z)
                candidates.add(point)
                nearestDistance = min(nearestDistance, math.dist(position, point))
    selected = []
    for point in sorted(candidates, key=lambda p: (math.dist(position, p), p)):
        if (math.dist(position, point) <= nearestDistance + MESH_STEP
                and all(math.dist(point, other) >= PROGRESS_DISTANCE for other in selected)):
            selected.append(point)
            if len(selected) == MESH_GOAL_COUNT:
                break
    return selected


def meshPlan(flow, nav, actorId):
    value = actors.data(flow, actorId)
    position = value['position']
    bodies = body_collision.obstacles(flow, actorId, actors.world(flow))
    goals = [point for point in meshGoals(nav, position)
             if math.dist(body_collision.clip(position, (*point[:2], position[2]), bodies, slide=False),
                          (*point[:2], position[2])) < body_collision.SKIN]
    previous = value.get('meshRecovery', {})
    attempt = previous.get('attempt', 0) + 1
    previousGoal = previous.get('goal')
    index = next((i + 1 for i, point in enumerate(goals)
                  if previousGoal is not None and math.dist(point, previousGoal) < PROGRESS_DISTANCE), 0)
    goal = list(goals[index % len(goals)]) if goals else None
    state = dict(attempt=attempt, goal=goal, retryAt=flow.now + RETRY_MS)
    value['meshRecovery'] = state
    value.pop('motionProbe', None)
    mode = 'move' if goal is not None else 'wait'
    flow.log("DEBUG", f'bot-mesh-recovery actor={actorId} attempt={attempt} mode={mode} '
                              f'position={position} goal={goal}')
    return state


def returnToMesh(flow, nav, observed, actorId):
    """Steer to the nearest entry; only a fresh native pose can complete recovery."""
    from . import bot
    value = actors.data(flow, actorId)
    position = observed['position']
    value['position'] = list(position)
    state = value.get('meshRecovery')
    if state is not None and bot_tactics.groundGoal(nav, position) is not None:
        bot.stopMotion(flow, actorId)
        value.update(repathAt=0, senseAt=0)
        flow.log("DEBUG", f'bot-mesh-returned actor={actorId} position={position}')
        return False
    if state is None:
        bot.stopMotion(flow, actorId)
        for key in ('tactic', 'stepPlan', 'routeGoal', 'roamGoal', 'observedPlayer', 'pursuit'):
            value.pop(key, None)
        value.update(reactAt=None, repathAt=0)
        state = meshPlan(flow, nav, actorId)
    elif state['goal'] is None and flow.now >= state['retryAt']:
        state = meshPlan(flow, nav, actorId)
    goal = state['goal']
    moveTimer = actors.timerName(bot.MOVE_TIMER, actorId)
    if goal is None:
        if value['moving']:
            bot.broadcast(flow, False, actorId=actorId)
        flow.cancel(moveTimer)
        bot.status(flow, 'outside-mesh', actorId)
        return True
    dx, dy = goal[0] - position[0], goal[1] - position[1]
    distance = math.hypot(dx, dy)
    heading = math.degrees(math.atan2(-dy, dx))
    remaining = bot.turnToward(flow, heading, actorId)
    actualHeading = observed.get('heading')
    aligned = (remaining <= MESH_TURN_ANGLE and type(actualHeading) in (int, float)
               and math.isfinite(actualHeading) and abs(bot.headingDelta(actualHeading, heading)) <= MESH_TURN_ANGLE)
    probe = observed.get('terrain', {}).get('forward', {})
    terrainReady = probe.get('ready') is True and type(probe.get('hit')) is bool
    if not aligned or not terrainReady:
        value.pop('motionProbe', None)
    # Ordinary waypoint tolerance can stop before the body has cleared the mesh edge.
    retry = distance <= body_collision.SKIN or aligned and terrainReady and observe(flow, actorId)
    if retry:
        meshPlan(flow, nav, actorId)
    travel = min(distance, bot.JOG_SPEED * bot.MOVE_MS / 1000)
    desired = (position[0] + dx * travel / distance, position[1] + dy * travel / distance, position[2]) if distance else position
    bodies = body_collision.obstacles(flow, actorId, actors.world(flow))
    bodyClear = math.dist(body_collision.clip(position, desired, bodies, slide=False), desired) < body_collision.SKIN
    contact = probe.get('position')
    # The forward ray reaches 2.2 m; a wall beyond this step must not block reentry.
    terrainClear = terrainReady and (not probe['hit'] or actors.validPosition(contact)
                                    and math.dist(position[:2], contact[:2]) > travel + body_collision.RADIUS + body_collision.SKIN)
    moving = not retry and aligned and terrainClear and bodyClear
    value['forwardMs'] = 0
    bot.broadcast(flow, moving, heading, actorId)
    bot.status(flow, 'mesh-return' if moving else 'mesh-return-wait', actorId)
    flow.later(moveTimer, bot.MOVE_MS)
    return True
