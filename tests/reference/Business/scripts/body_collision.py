"""Body clearance for spawn placement and AI steering, never position correction."""
import math

from . import actors

RADIUS, HEIGHT = .35, 1.8
DIAMETER = 2 * RADIUS
SKIN = .001


def validState(session):
    value = session.get('bodyPlayer')
    return value is None or (type(value) is dict and actors.validPosition(value.get('position'))
                             and type(value.get('sampledAt')) is int and value['sampledAt'] >= 0)


def contact(start, delta, obstacle):
    x, y, z = (start[i] - obstacle[i] for i in range(3))
    dx, dy, dz = delta
    squared = x * x + y * y
    radial = x * dx + y * dy
    horizontal = dx * dx + dy * dy
    if squared < DIAMETER ** 2 - 1e-9 and abs(z) < HEIGHT - 1e-9:
        # Existing overlaps may leave, but may not cross deeper through a body.
        if (radial > 1e-9 or squared < 1e-9 and horizontal > 1e-9
                or horizontal < 1e-12 and abs(z + dz) > abs(z) + 1e-9):
            return None
        normal = [x, y, 0] if squared > 1e-9 else [-dx, -dy, 0]
        length = math.hypot(*normal[:2])
        return (0., [v / length for v in normal]) if length > 1e-9 else None
    if horizontal < 1e-12:
        if squared >= DIAMETER ** 2:
            return None
        radialIn, radialOut = -math.inf, math.inf
    else:
        discriminant = radial ** 2 - horizontal * (squared - DIAMETER ** 2)
        if discriminant <= 0:
            return None
        root = math.sqrt(discriminant)
        radialIn, radialOut = (-radial - root) / horizontal, (-radial + root) / horizontal
    if abs(dz) < 1e-12:
        if abs(z) >= HEIGHT:
            return None
        verticalIn, verticalOut = -math.inf, math.inf
    else:
        verticalIn, verticalOut = sorted(((-HEIGHT - z) / dz, (HEIGHT - z) / dz))
    enter, leave = max(0., radialIn, verticalIn), min(1., radialOut, verticalOut)
    if enter >= leave - 1e-9:
        return None
    normal = ([0., 0., 1. if z + dz * enter > 0 else -1.] if verticalIn > radialIn else
              [(x + dx * enter) / DIAMETER, (y + dy * enter) / DIAMETER, 0.])
    return enter, normal


def clip(start, end, obstacles, *, slide=True):
    position, target = list(start), list(end)
    for _ in range(3):
        delta = [b - a for a, b in zip(position, target)]
        distance = math.dist(position, target)
        if distance < 1e-9:
            break
        contacts = [result for point in obstacles if (result := contact(position, delta, point)) is not None]
        if not contacts:
            return target
        fraction, normal = min(contacts, key=lambda result: result[0])
        fraction = max(0., fraction - SKIN / distance)
        position = [a + d * fraction for a, d in zip(position, delta)]
        if not slide:
            return position
        remainder = [d * (1 - fraction) for d in delta]
        into = min(0., sum(d * n for d, n in zip(remainder, normal)))
        target = [a + d - into * n for a, d, n in zip(position, remainder, normal)]
    return position


def obstacles(flow, actorId, snapshot):
    result = []
    for rid in actors.ids(flow.session):
        if rid == actorId:
            continue
        if not actors.active(flow, rid):
            value = actors.data(flow, rid)
            if value['life']['state'] == 'spawning':
                result.append(list(value['spawnPosition'] if rid == 1 else value['position']))
            continue
        pose = actors.pose(snapshot, rid)
        if pose is None:
            continue
        result.append(list(pose['position']))
    return result


def navigable(nav, start, end, heightOffset):
    a, b = list(start), list(end)
    a[2] -= heightOffset
    b[2] -= heightOffset
    path = nav.findPath(a, b, radius=RADIUS)
    if path.status != 'ready':
        return None
    length = sum(math.dist(p[:2], q[:2]) for p, q in zip(path.points, path.points[1:]))
    if length > math.dist(a[:2], b[:2]) + .01:
        return None
    projected = nav.project(b)
    return [projected[0], projected[1], projected[2] + heightOffset] if projected is not None else None


def moveBot(flow, nav, actorId, start, desired):
    snapshot = actors.world(flow)
    if snapshot is None:
        return list(start)
    neighbors = obstacles(flow, actorId, snapshot)
    value = actors.data(flow, actorId)
    clipped = clip(start, desired, neighbors)
    if math.dist(clipped, desired) < SKIN:
        return clipped
    candidates = [clipped]
    dx, dy = desired[0] - start[0], desired[1] - start[1]
    # A shared right-side preference lets approaching actors pass one another.
    for degrees in (-45, 45, -90, 90):
        angle = math.radians(degrees)
        candidate = [start[0] + dx * math.cos(angle) - dy * math.sin(angle),
                     start[1] + dx * math.sin(angle) + dy * math.cos(angle), desired[2]]
        candidates.append(clip(start, candidate, neighbors))
    valid = [point for candidate in candidates
             if (point := navigable(nav, start, candidate, value.get('heightOffset', 0))) is not None
             and math.dist(clip(start, point, neighbors), point) < SKIN]
    return max(valid, key=lambda point: math.dist(start, desired) - math.dist(point, desired),
               default=list(start))
