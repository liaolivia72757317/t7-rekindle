"""Luoyang battlefield respawns within a horizontal radius of own-camp reference points."""
from functools import lru_cache
import math

from . import body_collision, map_data
from .navigation import EPSILON, cross, height, inside

RESOURCE_IDS = frozenset((map_data.RESOURCE_ID,))
MAX_DISTANCE = 32.0
MAX_CANDIDATES = 8
GROUND_OFFSET = .2
MARGIN = body_collision.DIAMETER / 2 + body_collision.SKIN
SPACING = body_collision.DIAMETER + body_collision.SKIN


def enabled(flow):
    return flow.session.get('resourceId', map_data.RESOURCE_ID) in RESOURCE_IDS


def inset(polygon):
    """Clip a convex face to keep the actor center away from its edges."""
    result = tuple(point[:2] for point in polygon)
    for a, b in zip(polygon, polygon[1:] + polygon[:1]):
        length = math.dist(a[:2], b[:2])
        if length <= EPSILON:
            continue
        clipped = []
        for p, q in zip(result, result[1:] + result[:1]):
            dp, dq = cross(a, b, p) / length - MARGIN, cross(a, b, q) / length - MARGIN
            if dp >= 0:
                clipped.append(p)
            if (dp >= 0) != (dq >= 0):
                fraction = dp / (dp - dq)
                clipped.append(tuple(x + (y - x) * fraction for x, y in zip(p, q)))
        result = tuple(clipped)
        if len(result) < 3:
            return ()
    return result


def nearest(polygon, point):
    if inside(polygon, point):
        return tuple(point[:2])
    choices = []
    for a, b in zip(polygon, polygon[1:] + polygon[:1]):
        length2 = sum((b[i] - a[i]) ** 2 for i in (0, 1))
        if length2 <= EPSILON ** 2:
            continue
        fraction = max(0., min(1., sum((point[i] - a[i]) * (b[i] - a[i]) for i in (0, 1)) / length2))
        choices.append(tuple(a[i] + (b[i] - a[i]) * fraction for i in (0, 1)))
    return min(choices, key=lambda p: math.dist(p, point[:2])) if choices else None


@lru_cache(maxsize=4)
def connectedFaces(nav, anchor):
    start = nav.locate(anchor)
    if start is None:
        return frozenset()
    found, pending = {start}, [start]
    while pending:
        for neighbor, a, b in nav.links[pending.pop()]:
            if neighbor not in found and math.dist(a[:2], b[:2]) > body_collision.DIAMETER + EPSILON:
                found.add(neighbor)
                pending.append(neighbor)
    return frozenset(found)


@lru_cache(maxsize=4)
def candidates(nav, points, anchor):
    """Return separate, reachable ground positions per reference without changing resource data."""
    faces = connectedFaces(nav, anchor)
    interiors = {face: inset(nav.polygons[face]) for face in sorted(faces)}
    groups = []
    for position, heading in points:
        nearby = set()
        for face, polygon in interiors.items():
            if not polygon:
                continue
            closest = nearest(polygon, position)
            center = tuple(sum(p[i] for p in polygon) / len(polygon) for i in (0, 1))
            proposals = [closest, center]
            if closest is not None and math.dist(closest, position[:2]) < SPACING:
                for angle in range(0, 360, 45):
                    angle = math.radians(angle)
                    proposals.append(nearest(polygon, (position[0] + math.cos(angle), position[1] + math.sin(angle))))
            for xy in proposals:
                if xy is None or math.dist(xy, position[:2]) > MAX_DISTANCE + EPSILON:
                    continue
                if any(math.dist(xy, original[:2]) < SPACING for original, _ in points):
                    continue
                ground = height(nav.polygons[face], xy)
                if ground is not None:
                    nearby.add((*xy, ground + GROUND_OFFSET))
        ordered = sorted(nearby, key=lambda point: (math.dist(point[:2], position[:2]), abs(point[2] - position[2]), point))
        selected = []
        for point in ordered:
            if (all(math.dist(point[:2], other[:2]) >= SPACING for other, _ in selected)
                    and nav.findPath(point, anchor).status == 'ready'):
                selected.append((point, heading))
                if len(selected) == MAX_CANDIDATES:
                    break
        groups.append(tuple(selected))
    return tuple(groups)
