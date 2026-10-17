"""Profile-based perception, last-seen search and independent lane patrols."""
import math

from . import actors, bot_tactics, spawn_navigation

PERCEPTION_MS, SEARCH_MS, SEARCH_SCAN_MS = 500, 6000, 2000
SIGHT_RANGE = {'aggressive': 18., 'balanced': 16., 'defensive': 14., 'berserker': math.inf}
NEAR_RANGE, VIEW_COS = 3., math.cos(math.radians(110))
ENGAGED_NEAR_RANGE, DISENGAGE_MARGIN = 6., 2.
ARRIVAL_RADIUS = .65


def validState(value):
    for name in ('lastSeenPosition', 'roamGoal', 'routeGoal', 'roamAnchor'):
        if name in value and not actors.validPosition(value[name]):
            return False
    for name in ('senseAt', 'searchUntil', 'roamWaitUntil', 'roamCount', 'routeSeed'):
        if name in value and (type(value[name]) is not int or value[name] < 0):
            return False
    return (value.get('routeSeed', 0) <= 0xFFFFFFFF
            and ('searchTargetId' not in value or type(value['searchTargetId']) is int
                 and 1 <= value['searchTargetId'] <= actors.MAX_ACTORS))


def straight(nav, start, target):
    path = nav.findPath(start, target, radius=0)
    return path.status == 'ready' and sum(math.dist(a[:2], b[:2]) for a, b in zip(path.points, path.points[1:])) <= math.dist(start[:2], target[:2]) + .05


def visible(nav, source, target, distanceLimit, nearRange=NEAR_RANGE):
    a, b = source['position'], target['position']
    dx, dy = b[0] - a[0], b[1] - a[1]
    distance = math.hypot(dx, dy)
    if distance > distanceLimit or abs(a[2] - b[2]) > 2.5:
        return False
    heading = source.get('heading')
    if type(heading) not in (int, float) or not math.isfinite(heading):
        return False
    if distance > nearRange and (dx * math.cos(math.radians(heading)) - dy * math.sin(math.radians(heading))) / distance < VIEW_COS:
        return False
    return straight(nav, a, b)


def remember(flow, actorId, targetId, position):
    value = actors.data(flow, actorId)
    value.update(searchTargetId=targetId, lastSeenPosition=list(position), searchUntil=flow.now + SEARCH_MS,
                 roamAnchor=list(position), roamCount=max(1, value.get('roamCount', 0)))
    value.pop('roamGoal', None)
    value.pop('roamWaitUntil', None)


def forget(value):
    for key in ('searchTargetId', 'lastSeenPosition', 'searchUntil'):
        value.pop(key, None)


def noticeHit(flow, attacker, target, snapshot, *, damage=20):
    if 'bots' not in flow.session:
        return
    if target != 1:
        bot_tactics.noticeDamage(flow, target, attacker, damage)
    for actorId, otherId in ((attacker, target), (target, attacker)):
        if actorId == 1:
            continue
        value = actors.data(flow, actorId)
        if value.get('opponentId') not in (None, otherId):
            continue
        remember(flow, actorId, otherId, snapshot[actors.positionName(snapshot, otherId)])
        value['senseAt'] = 0


def sense(flow, nav, snapshot, actorId):
    value = actors.data(flow, actorId)
    source = actors.pose(snapshot, actorId)
    if source is None or not actors.active(flow, actorId):
        value.pop('pursuit', None)
        return None
    current = value.get('opponentId')
    tracked = current or value.get('searchTargetId')
    if tracked is not None and not actors.active(flow, tracked):
        forget(value)
        tracked = None
        value['senseAt'] = 0
    elif flow.now >= value.get('searchUntil', flow.now + 1):
        tracked = None
    limit = SIGHT_RANGE[value.get('profile', 'balanced')]
    globalSearch = value.get('profile') == 'berserker'

    def perceived(candidate, *, engaged=False):
        if globalSearch:
            return nav.findPath(source['position'], candidate['position']).status == 'ready'
        return visible(nav, source, candidate, limit + (DISENGAGE_MARGIN if engaged else 0),
                       ENGAGED_NEAR_RANGE if engaged else NEAR_RANGE)

    target = actors.pose(snapshot, tracked) if tracked is not None else None
    retained = target is not None and perceived(target, engaged=True)
    if retained:
        bot_tactics.trackPursuit(flow, actorId, tracked, math.dist(source['position'], target['position']))
    else:
        value.pop('pursuit', None)
    if flow.now < value.get('senseAt', 0):
        if current is None and not retained:
            return None
        if current == tracked and retained:
            pair = actors.pair(snapshot, actorId, current)
            remember(flow, actorId, current, pair['player'])
            return pair
    value['senseAt'] = flow.now + PERCEPTION_MS
    candidates = actors.enemies(flow, actorId, snapshot)
    visibleIds = [rid for rid in candidates if (retained if rid == tracked else
                  perceived(actors.pose(snapshot, rid)))]
    targetId = bot_tactics.selectTarget(flow, nav, snapshot, actorId, visibleIds, current if globalSearch else tracked)
    if targetId != current:
        value.pop('observedPlayer', None)
        value.pop('routeGoal', None)
        value.pop('tactic', None)
        value.pop('assist', None)
        value.pop('footwork', None)
        value.pop('stepPlan', None)
        value.pop('pressAt', None)
        value.pop('pursuit', None)
        value.update(reactAt=None, repathAt=0, path=[], step=0)
        if targetId is None:
            value.pop('targetSince', None)
        else:
            value['targetSince'] = flow.now
        damage = bot_tactics.threatDamage(value, targetId, flow.now)
        flow.log("DEBUG", f'bot-target actor={actorId} from={current} to={targetId} damage={damage:.1f}')
    value['opponentId'] = targetId
    if targetId is None:
        return None
    pair = actors.pair(snapshot, actorId, targetId)
    bot_tactics.trackPursuit(flow, actorId, targetId, math.dist(pair['bot'], pair['player']))
    remember(flow, actorId, targetId, pair['player'])
    return pair


def randomStep(value):
    seed = (1664525 * value.get('routeSeed', 1) + 1013904223) & 0xFFFFFFFF
    value['routeSeed'] = seed
    return seed


def reachableGoal(nav, origin, desired):
    projected = nav.project(desired)
    faces = spawn_navigation.connectedFaces(nav, tuple(origin))
    candidates = ([projected] if projected is not None else []) + sorted(
        (nav.centers[index] for index in sorted(faces)),
        key=lambda point: math.dist(point, desired))[:6]
    for candidate in candidates:
        if math.dist(candidate, desired) > 18 or math.dist(candidate[:2], origin[:2]) <= ARRIVAL_RADIUS:
            continue
        path = nav.findPath(origin, candidate)
        if path.status == 'ready':
            return list(candidate)
    return None


def localGoal(nav, origin, seed):
    for radius in (5., 2., 1.):
        for offset in range(0, 360, 45):
            desired = bot_tactics.pointAt(origin, math.radians(seed % 360 + offset), radius)
            point = bot_tactics.groundGoal(nav, desired)
            if point is not None and nav.findPath(origin, point).status == 'ready':
                return list(point)
    return None


def goal(flow, nav, actorId):
    value = actors.data(flow, actorId)
    origin = value['position']
    if flow.now < value.get('searchUntil', 0) and 'lastSeenPosition' in value:
        position = value['lastSeenPosition']
        approaching = flow.now < value['searchUntil'] - SEARCH_SCAN_MS and math.dist(origin, position) > ARRIVAL_RADIUS
        return (list(position) if approaching else None), 'search'
    forget(value)
    if flow.now < value.get('roamWaitUntil', 0):
        return None, 'patrol-wait'
    current = value.get('roamGoal')
    if current is not None and math.dist(origin[:2], current[:2]) <= ARRIVAL_RADIUS:
        value.pop('roamGoal')
        current = None
    if current is not None:
        return current, 'advance' if value.get('roamCount', 0) == 1 else 'patrol'
    count = value.get('roamCount', 0)
    anchor = value.get('roamAnchor', value.setdefault('patrolAnchor', list(origin)))
    desired = list(anchor)
    if count:
        seed = randomStep(value)
        angle = math.radians(seed % 360)
        radius = 5 + (seed >> 12) % 9
        desired[0] += math.cos(angle) * radius
        desired[1] += math.sin(angle) * radius
    target = reachableGoal(nav, origin, desired)
    if target is None:
        target = localGoal(nav, origin, randomStep(value))
    if target is None:
        value['roamWaitUntil'] = flow.now + 1000
        return None, 'no-path'
    value.update(roamGoal=target, roamCount=count + 1)
    return target, 'advance' if count == 0 else 'patrol'
