"""Close-range locomotion independent of weapon actions and their recovery timers."""
import math

from . import actors, battle, body_collision, bot_profiles, bot_tactics

ENGAGE_RANGE = 6.
STEP_MIN_MS, STEP_MAX_MS, TARGET_DRIFT = 350, 900, .4
GAPS = {'aggressive': 1.1, 'balanced': 1.2, 'defensive': 1.3, 'berserker': 1.1}
# Saved sessions may still contain a circling step; the next plan discards it.
MODES = ('press', 'retreat', 'circle')


def validState(value):
    if 'pressAt' in value and (type(value['pressAt']) is not int or value['pressAt'] < 0):
        return False
    step = value.get('stepPlan')
    if 'stepPlan' in value and not (
            type(step) is dict and type(step.get('targetId')) is int and 1 <= step['targetId'] <= actors.MAX_ACTORS
            and type(step.get('until')) is int and step['until'] >= 0 and step.get('mode') in MODES
            and type(step.get('gap')) in (int, float) and 0 < step['gap'] < ENGAGE_RANGE
            and type(step.get('side')) is int and step['side'] in (-1, 1)
            and actors.validPosition(step.get('goal')) and actors.validPosition(step.get('targetPosition'))):
        return False
    footwork = value.get('footwork')
    return 'footwork' not in value or (
        type(footwork) is dict and type(footwork.get('targetId')) is int
        and 1 <= footwork['targetId'] <= actors.MAX_ACTORS and footwork.get('mode') in MODES)


def lockReason(value):
    """Weapon locks limit choosing another action, not movement."""
    action = value.get('action', {})
    if 'hurtUntil' in value:
        return 'hurt'
    if action.get('reaction'):
        return action['reaction']
    if action.get('index', -1) >= 0:
        return 'parry' if action.get('intent') in battle.BLOCK_CHAINS else 'attack'
    return None


def plan(flow, nav, snapshot, actorId):
    value = actors.data(flow, actorId)
    origin, target = snapshot['bot'], snapshot['player']
    distance = math.dist(origin[:2], target[:2])
    if distance > ENGAGE_RANGE or abs(origin[2] - target[2]) > 1:
        value.pop('stepPlan', None)
        return None
    action = value.get('action', {})
    profile = value.get('profile', 'balanced')
    canDefend = bot_profiles.PROFILES[profile].canDefend
    gap = GAPS[profile]
    if canDefend and value.get('health', 100) <= 40:
        gap = max(gap, 1.85)
    recovering = (action.get('intent') in battle.ATTACK_CHAINS and action.get('index') == 3)
    if canDefend and ('hurtUntil' in value or action.get('reaction') or recovering
            or action.get('intent') in battle.BLOCK_CHAINS and action.get('index', -1) >= 0):
        gap = max(gap, 1.85 if profile == 'aggressive' else 2.05)
    mode = 'press' if distance > gap + .2 else 'retreat' if distance < gap - .2 else None
    if mode is None or not canDefend and mode != 'press':
        value.pop('stepPlan', None)
        return None
    bodies = body_collision.obstacles(flow, actorId, actors.world(flow))

    def routeTo(goal):
        route = nav.findPath(origin, goal)
        length = sum(math.dist(a[:2], b[:2]) for a, b in zip(route.points, route.points[1:]))
        if route.status != 'ready' or length > math.dist(origin[:2], goal[:2]) + .05:
            return None
        elevated = [(*point[:2], point[2] + value.get('heightOffset', 0)) for point in route.points]
        if any(math.dist(body_collision.clip(a, b, bodies, slide=False), b) > .01
               for a, b in zip(elevated, elevated[1:])):
            return None
        return route

    previous, targetId = value.get('stepPlan', {}), snapshot.get('targetId', 1)
    if (previous.get('targetId') == targetId and flow.now < previous['until'] and previous['gap'] == gap
            and math.dist(previous['targetPosition'], target) <= TARGET_DRIFT
            and mode == previous['mode']):
        if math.dist(origin[:2], previous['goal'][:2]) <= .25:
            return None
        route = routeTo(previous['goal'])
        if route is not None:
            return bot_tactics.Plan(tuple(previous['goal']), previous['mode'], route)
    value.pop('stepPlan', None)
    angle = math.atan2(origin[1] - target[1], origin[0] - target[0]) if distance > .001 else (
        math.pi - math.radians(value['heading']))
    if previous.get('targetId') == targetId:
        side = previous['side'] * (-1 if bot_profiles.rhythm(flow, actorId, 0, 3) == 0 else 1)
    else:
        side = 1 if bot_profiles.rhythm(flow, actorId, 0, 1) else -1
    offsets = (0, 35 * side, -35 * side, 70 * side, -70 * side)
    for degrees in offsets:
        goal = bot_tactics.groundGoal(nav, bot_tactics.pointAt(target, angle + math.radians(degrees), gap))
        if goal is None or math.dist(origin[:2], goal[:2]) <= .25:
            continue
        route = routeTo(goal)
        if route is None:
            continue
        value['stepPlan'] = dict(targetId=targetId, mode=mode, goal=list(goal), targetPosition=list(target), gap=gap,
                                 side=side if degrees == 0 else (1 if degrees > 0 else -1),
                                 until=flow.now + bot_profiles.rhythm(flow, actorId, STEP_MIN_MS, STEP_MAX_MS))
        return bot_tactics.Plan(goal, mode, route)
    return None


def facingTarget(flow, actorId):
    targetId = actors.data(flow, actorId).get('footwork', {}).get('targetId')
    if targetId is None or not actors.active(flow, targetId):
        return None
    snapshot = actors.world(flow)
    target, source = actors.pose(snapshot, targetId), actors.pose(snapshot, actorId)
    if target is None or source is None or math.dist(source['position'], target['position']) > ENGAGE_RANGE + 1:
        return None
    return target['position']
