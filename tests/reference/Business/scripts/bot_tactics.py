"""Team support, local spacing and committed flanking routes."""
from dataclasses import dataclass
import math

from . import actors, body_collision, bot_profiles
from .navigation import NavPath

LOCAL_RANGE, JOIN_RANGE = 8., 7.
SPACING, ATTACK_RADIUS, SUPPORT_RADIUS = 1.25, 1.2, 3.1
ASSIST_RADIUS, ARC_RADIUS, ARRIVAL_RADIUS = 1.65, 2.4, .25
PLAN_MS, TARGET_DRIFT = 1200, .6
THREAT_MS, SWITCH_MS = 5000, 1200
THREAT_DECAY, MAX_THREAT, SWITCH_MARGIN = 5., 80., 1.5
CLOSE_RANGE, TARGET_BIAS_RANGE = 2.2, 4.
PURSUIT_RANGE, PURSUIT_PROGRESS = 6., .4
PURSUIT_MS, PURSUIT_MAX_MS, RETALIATE_DAMAGE = 2000, 4000, 30.
BERSERKER_SWITCH_MS, BERSERKER_SWITCH_MARGIN, BERSERKER_SWITCH_RATIO = 3000, 4., .3
BERSERKER_PURSUIT = (10., 3000, 6000)
MODES = ('approach', 'spread', 'flank', 'rear', 'support')


@dataclass(frozen=True)
class Plan:
    goal: tuple
    mode: str
    route: NavPath = None


def pursuitLimits(value):
    return BERSERKER_PURSUIT if value.get('profile') == 'berserker' else (PURSUIT_RANGE, PURSUIT_MS, PURSUIT_MAX_MS)


def validState(value):
    if 'targetSince' in value and (type(value['targetSince']) is not int or value['targetSince'] < 0):
        return False
    pursuit = value.get('pursuit')
    if 'pursuit' in value and not (
            type(pursuit) is dict and type(pursuit.get('targetId')) is int
            and 1 <= pursuit['targetId'] <= actors.MAX_ACTORS
            and all(type(pursuit.get(key)) is int for key in ('closeAt', 'progressAt'))
            and 0 <= pursuit['closeAt'] <= pursuit['progressAt']
            and type(pursuit.get('distance')) in (int, float) and 0 <= pursuit['distance'] <= pursuitLimits(value)[0]):
        return False
    threats = value.get('threats', {})
    if type(threats) is not dict or any(
            key not in {str(rid) for rid in range(1, actors.MAX_ACTORS + 1)} or type(threat) is not dict
            or type(threat.get('damage')) not in (int, float) or not 0 < threat['damage'] <= MAX_THREAT
            or type(threat.get('at')) is not int or threat['at'] < 0 for key, threat in threats.items()):
        return False
    assistance = value.get('assist')
    if 'assist' in value and not (
            type(assistance) is dict
            and all(type(assistance.get(key)) is int and 1 <= assistance[key] <= actors.MAX_ACTORS
                    for key in ('allyId', 'targetId'))
            and type(assistance.get('until')) is int and assistance['until'] >= 0
            and actors.validPosition(assistance.get('goal')) and actors.validPosition(assistance.get('targetPosition'))):
        return False
    plan = value.get('tactic')
    return 'tactic' not in value or (
        type(plan) is dict and type(plan.get('targetId')) is int and 1 <= plan['targetId'] <= actors.MAX_ACTORS
        and type(plan.get('until')) is int and plan['until'] >= 0
        and plan.get('mode') in MODES and actors.validPosition(plan.get('goal'))
        and actors.validPosition(plan.get('targetPosition')))


def nearbyAllies(flow, nav, snapshot, actorId, *, globalRange=False):
    source = actors.pose(snapshot, actorId)['position']
    camp = actors.data(flow, actorId)['camp']
    result = []
    for rid in actors.ids(flow.session):
        if rid == actorId or not actors.active(flow, rid) or actors.data(flow, rid).get('camp', 1) != camp:
            continue
        pose = actors.pose(snapshot, rid)
        if pose is None:
            continue
        if globalRange:
            result.append((rid, pose['position']))
            continue
        if abs(pose['position'][2] - source[2]) > 1.:
            continue
        distance = math.dist(source[:2], pose['position'][:2])
        if distance > LOCAL_RANGE:
            continue
        path = nav.findPath(source, pose['position'], radius=0)
        length = sum(math.dist(a[:2], b[:2]) for a, b in zip(path.points, path.points[1:]))
        if path.status == 'ready' and length <= distance + .05:
            result.append((rid, pose['position']))
    return result


def allyReport(flow, snapshot, allyId, position):
    ally = actors.data(flow, allyId)
    if allyId == 1:
        if ally.get('action', {}).get('index', -1) < 0 and flow.now >= ally.get('hurtUntil', 0):
            return None
        targetId = actors.pose(snapshot, allyId).get('targetId')
        pose = actors.pose(snapshot, targetId) if targetId in actors.ids(flow.session) else None
        target = pose['position'] if pose is not None else None
        limit = 2.2
    else:
        targetId = ally.get('opponentId')
        target = ally.get('lastSeenPosition') if flow.now < ally.get('searchUntil', 0) else None
        limit = JOIN_RANGE
    if (targetId not in actors.ids(flow.session) or target is None or not actors.active(flow, targetId)
            or actors.data(flow, targetId).get('camp', 1) == ally.get('camp', 1)
            or math.dist(position, target) > limit):
        return None
    return targetId, target


def assist(flow, nav, actorId):
    value = actors.data(flow, actorId)
    value.pop('assist', None)
    snapshot = actors.world(flow)
    source = actors.pose(snapshot, actorId)
    if source is None or not actors.active(flow, actorId):
        return None
    origin = source['position']
    acrossMap = value.get('profile') != 'berserker'
    allies = nearbyAllies(flow, nav, snapshot, actorId, globalRange=acrossMap)
    reservations = [actors.data(flow, rid)[key]['goal'] for rid, _ in allies for key in ('tactic', 'assist')
                    if actors.data(flow, rid).get(key, {}).get('until', 0) > flow.now]
    spacing = [position for _, position in allies] + reservations
    bodies = body_collision.obstacles(flow, actorId, snapshot)
    side = value.get('lane', 0) or (1 if actorId % 2 else -1)
    candidates = []
    for allyId, position in allies:
        report = allyReport(flow, snapshot, allyId, position)
        if report is None:
            continue
        targetId, target = report
        axis = math.atan2(target[1] - position[1], target[0] - position[0])
        for degrees in (60 * side, -60 * side, 120 * side, -120 * side, 180):
            goal = groundGoal(nav, pointAt(position, axis + math.radians(degrees), ASSIST_RADIUS))
            if goal is None or any(abs(goal[2] + value.get('heightOffset', 0) - other[2]) < body_collision.HEIGHT
                                   and math.dist(goal[:2], other[:2]) < SPACING for other in spacing):
                continue
            candidates.append((math.dist(origin, goal), allyId, targetId, target, goal, position))
    for distance, allyId, targetId, target, goal, position in sorted(candidates, key=lambda item: item[0]):
        route = nav.findPath(origin, goal)
        length = sum(math.dist(a, b) for a, b in zip(route.points, route.points[1:]))
        if route.status != 'ready' or (not acrossMap and length > distance + 4):
            continue
        elevated = [(*point[:2], point[2] + value.get('heightOffset', 0)) for point in route.points]
        if any(math.dist(body_collision.clip(a, b, bodies, slide=False), b) > .01
               for a, b in zip(elevated, elevated[1:])):
            continue
        value['assist'] = dict(allyId=allyId, targetId=targetId, goal=list(goal),
                               targetPosition=list(target), until=flow.now + PLAN_MS)
        value.update(roamAnchor=list(position), roamCount=max(1, value.get('roamCount', 0)))
        value.pop('roamGoal', None)
        value.pop('roamWaitUntil', None)
        return Plan(goal, 'assist', route)
    return None


def threatDamage(value, actorId, now):
    threat = value.get('threats', {}).get(str(actorId))
    if threat is None or not 0 <= now - threat['at'] < THREAT_MS:
        return 0.
    return max(0., threat['damage'] - THREAT_DECAY * (now - threat['at']) / 1000)


def recentThreats(flow, actorId):
    value = actors.data(flow, actorId)
    return {key: threat for key, threat in value.get('threats', {}).items()
            if int(key) in actors.ids(flow.session) and actors.active(flow, int(key))
            and actors.data(flow, int(key)).get('camp', 1) != value['camp']
            and threatDamage(value, int(key), flow.now) > 0}


def noticeDamage(flow, actorId, attackerId, damage):
    value = actors.data(flow, actorId)
    if damage <= 0 or actors.data(flow, attackerId).get('camp', 1) == value['camp']:
        return
    threats = recentThreats(flow, actorId)
    amount = min(MAX_THREAT, threatDamage(value, attackerId, flow.now) + damage)
    value['threats'] = {**threats, str(attackerId): dict(damage=amount, at=flow.now)}
    value['senseAt'] = 0


def pursuitActive(value, targetId, now):
    pursuit = value.get('pursuit', {})
    _, idleMs, maximumMs = pursuitLimits(value)
    return (pursuit.get('targetId') == targetId and targetId is not None
            and now < pursuit['closeAt'] + maximumMs and now < pursuit['progressAt'] + idleMs)


def trackPursuit(flow, actorId, targetId, distance):
    value = actors.data(flow, actorId)
    if distance <= CLOSE_RANGE:
        value['pursuit'] = dict(targetId=targetId, closeAt=flow.now, progressAt=flow.now, distance=distance)
        return
    if distance > pursuitLimits(value)[0] or not pursuitActive(value, targetId, flow.now):
        value.pop('pursuit', None)
        return
    pursuit = value['pursuit']
    closing = pursuit['distance'] - distance >= PURSUIT_PROGRESS
    # Widening resets the distance probe, never the no-progress deadline.
    if closing or distance > pursuit['distance']:
        value['pursuit'] = {**pursuit, 'distance': distance,
                            'progressAt': flow.now if closing else pursuit['progressAt']}


def selectTarget(flow, nav, snapshot, actorId, candidates, current):
    value = actors.data(flow, actorId)
    berserker = value.get('profile') == 'berserker'
    switchMs = BERSERKER_SWITCH_MS if berserker else SWITCH_MS
    if 'threats' in value:
        value['threats'] = recentThreats(flow, actorId)
    if not candidates:
        return None
    if current in candidates and (value.get('action', {}).get('index', -1) >= 0
                                  or 'targetSince' in value and flow.now < value['targetSince'] + switchMs):
        return current
    source = actors.pose(snapshot, actorId)['position']
    if berserker and current not in candidates:
        return min(candidates, key=lambda rid: (math.dist(source, actors.pose(snapshot, rid)['position']), rid))
    allies = [] if berserker else nearbyAllies(flow, nav, snapshot, actorId)
    distances, loads = {}, {}
    for targetId in candidates:
        target = actors.pose(snapshot, targetId)['position']
        distances[targetId] = math.dist(source, target)
        loads[targetId] = sum(
            math.dist(position, target) <= JOIN_RANGE
            and (actors.data(flow, rid).get('opponentId') == targetId or math.dist(position, target) <= 2.2)
            for rid, position in allies)
    scores = {rid: distances[rid] + 3 * max(0, loads[rid] - 1)
              - min(6., .15 * threatDamage(value, rid, flow.now)) for rid in candidates}
    if current in scores:
        bias = 2 * max(0., min(1., (TARGET_BIAS_RANGE - distances[current]) / (TARGET_BIAS_RANGE - CLOSE_RANGE)))
        if actors.data(flow, current).get('health', 100) <= 20:
            bias *= 2
        scores[current] -= bias
    protected = (current in scores and CLOSE_RANGE < distances[current] <= pursuitLimits(value)[0]
                 and pursuitActive(value, current, flow.now))
    eligible = [rid for rid in candidates if not protected or rid == current
                or threatDamage(value, rid, flow.now) >= RETALIATE_DAMAGE]
    best = min(eligible, key=lambda rid: (scores[rid], rid))
    margin = max(BERSERKER_SWITCH_MARGIN, distances[current] * BERSERKER_SWITCH_RATIO) if berserker else SWITCH_MARGIN
    return current if current in scores and scores[current] <= scores[best] + margin else best


def pointAt(target, angle, radius):
    return (target[0] + math.cos(angle) * radius, target[1] + math.sin(angle) * radius, target[2])


def groundGoal(nav, desired):
    point = nav.project(desired)
    if point is None:
        return None
    # Keep the body away from outer mesh edges, not just its center on a face.
    margin = body_collision.RADIUS + body_collision.SKIN
    if any(nav.project((point[0] + dx, point[1] + dy, point[2])) is None
           for dx, dy in ((margin, 0), (-margin, 0), (0, margin), (0, -margin))):
        return None
    return point


def flankRoute(nav, origin, target, goal):
    startAngle = math.atan2(origin[1] - target[1], origin[0] - target[0])
    endAngle = math.atan2(goal[1] - target[1], goal[0] - target[0])
    delta = (endAngle - startAngle + math.pi) % (2 * math.pi) - math.pi
    stops = []
    if abs(delta) > math.radians(60):
        radius = max(ARC_RADIUS, math.dist(goal[:2], target[:2]))
        count = math.ceil(abs(delta) / math.radians(40))
        stops = [pointAt(target, startAngle + delta * step / count, radius) for step in range(count + 1)]
    points = []
    for end in (*stops, goal):
        route = nav.findPath(points[-1] if points else origin, end)
        if route.status != 'ready':
            return None
        # A valid mesh path can still cut through the enemy at the center of the ring.
        if any(math.dist(body_collision.clip(a, b, [target], slide=False), b) > .01
               for a, b in zip(route.points, route.points[1:])):
            return None
        points.extend(route.points if not points else route.points[1:])
    length = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
    if length > math.dist(origin, goal) + 8:
        return None
    return NavPath('ready', points=tuple(points))


def plan(flow, nav, pair, actorId):
    value = actors.data(flow, actorId)
    snapshot = actors.world(flow)
    if ('bots' not in flow.session or snapshot is None or 'actors' not in snapshot
            or not actors.active(flow, actorId) or not actors.active(flow, pair['targetId'])
            or math.dist(pair['bot'], pair['player']) > LOCAL_RANGE):
        value.pop('tactic', None)
        return None
    origin, target, targetId = pair['bot'], pair['player'], pair['targetId']
    allies = nearbyAllies(flow, nav, snapshot, actorId)
    supporters = [(rid, position) for rid, position in allies if math.dist(position, target) <= JOIN_RANGE
                  and (rid == 1 and math.dist(position, target) <= 2.2
                       or rid != 1 and actors.data(flow, rid).get('opponentId') in (None, targetId))]
    crowded = any(math.dist(origin[:2], position[:2]) < SPACING for _, position in allies)
    if not supporters and not crowded:
        value.pop('tactic', None)
        return None
    anchorId, anchor = min([(actorId, origin), *supporters], key=lambda item: (math.dist(item[1], target), item[0]))
    axis = math.atan2(anchor[1] - target[1], anchor[0] - target[0])
    bodies = body_collision.obstacles(flow, actorId, snapshot)
    reservations = [actors.data(flow, rid)['tactic']['goal'] for rid, _ in allies
                    if actors.data(flow, rid).get('tactic', {}).get('until', 0) > flow.now]

    def available(point, *, reserveFront=True):
        actual = (*point[:2], point[2] + value.get('heightOffset', 0))
        if any(abs(actual[2] - other[2]) < body_collision.HEIGHT
               and math.dist(actual[:2], other[:2]) < body_collision.DIAMETER + .1 for other in bodies):
            return False
        spacing = [position for _, position in allies] + reservations
        if reserveFront and anchorId != actorId:
            spacing.append(pointAt(target, axis, ATTACK_RADIUS))
        return all(abs(actual[2] - other[2]) >= body_collision.HEIGHT
                   or math.dist(actual[:2], other[:2]) >= SPACING for other in spacing)

    previous = value.get('tactic', {})
    if (previous.get('targetId') == targetId and previous.get('until', 0) > flow.now
            and math.dist(previous['targetPosition'], target) <= TARGET_DRIFT
            and (previous['mode'] == 'support'
                 or math.dist(previous['goal'][:2], target[:2]) <= ATTACK_RADIUS + ARRIVAL_RADIUS)
            and available(previous['goal'], reserveFront=False)):
        moving = math.dist(origin[:2], previous['goal'][:2]) > ARRIVAL_RADIUS
        repath = moving and (not value['path'] or flow.now >= value['repathAt']
                             or math.dist(value.get('routeGoal', previous['goal']), previous['goal']) > .5)
        route = flankRoute(nav, origin, target, previous['goal']) if repath else None
        if not repath or route is not None:
            return Plan(tuple(previous['goal']), previous['mode'], route)

    side = value.get('lane', 0) or (1 if actorId % 2 else -1)
    heading = pair.get('playerHeading')
    rearWeight = {'aggressive': 3., 'balanced': 1.5, 'defensive': .5, 'berserker': 0.}[value.get('profile', 'balanced')]
    candidates = []
    for radius in (ATTACK_RADIUS, SUPPORT_RADIUS):
        for degrees in (0, 60 * side, -60 * side, 120 * side, -120 * side, 180):
            angle = axis + math.radians(degrees)
            goal = groundGoal(nav, pointAt(target, angle, radius))
            if goal is None or not available(goal):
                continue
            facing = math.cos(angle + math.radians(heading)) if type(heading) in (float, int) and math.isfinite(heading) else 0
            mode = ('support' if radius == SUPPORT_RADIUS else
                    'rear' if anchorId != actorId and facing < -.35 else
                    'flank' if anchorId != actorId else 'spread' if crowded else 'approach')
            score = math.dist(origin, goal) + (rearWeight * facing if anchorId != actorId else 0)
            if previous.get('targetId') == targetId and math.dist(previous['goal'], goal) < .45:
                score -= 2
            candidates.append((score + (100 if mode == 'support' else 0), goal, mode))
    for _, goal, mode in sorted(candidates, key=lambda item: item[0]):
        route = flankRoute(nav, origin, target, goal)
        if route is not None:
            value['tactic'] = dict(targetId=targetId, until=flow.now + bot_profiles.rhythm(flow, actorId, 800, 1600), goal=list(goal),
                                   targetPosition=list(target), mode=mode)
            value['repathAt'] = 0
            return Plan(goal, mode, route)
    value.pop('tactic', None)
    return None


def facingTarget(flow, actorId):
    value = actors.data(flow, actorId)
    assistance = value.get('assist')
    if assistance is not None:
        snapshot = actors.world(flow)
        if (flow.now >= assistance['until'] or actors.pose(snapshot, assistance['allyId']) is None
                or not actors.active(flow, assistance['allyId']) or not actors.active(flow, assistance['targetId'])):
            return None
        return assistance['targetPosition']
    targetId = value.get('tactic', {}).get('targetId')
    if targetId is None or not actors.active(flow, targetId):
        return None
    snapshot = actors.world(flow)
    target, source = actors.pose(snapshot, targetId), actors.pose(snapshot, actorId)
    if target is None or source is None or math.dist(source['position'], target['position']) > LOCAL_RANGE:
        return None
    return target['position']
