"""Deterministic local path planning on original convex navigation faces."""
from dataclasses import dataclass
import heapq
import math

EPSILON = 1e-5
HEIGHT_TOLERANCE = 1.0
CELL_SIZE = 8.0


def cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def inside(polygon, point):
    return all(cross(a, b, point) >= -EPSILON for a, b in zip(polygon, polygon[1:] + polygon[:1]))


def height(polygon, point):
    a = polygon[0]
    for b, c in zip(polygon[1:], polygon[2:]):
        area = cross(a, b, c)
        if abs(area) <= EPSILON:
            continue
        u, v = cross(a, point, c), cross(a, b, point)
        if u >= -EPSILON and v >= -EPSILON and u + v <= area + EPSILON:
            return a[2] + (u * (b[2] - a[2]) + v * (c[2] - a[2])) / area
    return None


def pullString(portals):
    """Funnel through oriented (left, right) portals; retain their corridor indices."""
    apex = left = right = portals[0][0]
    apexIndex = leftIndex = rightIndex = 0
    result, index = [(apex, 0)], 1
    while index < len(portals):
        newLeft, newRight = portals[index]
        if cross(apex, right, newRight) >= 0:
            if apex == right or cross(apex, left, newRight) < 0:
                right, rightIndex = newRight, index
            else:
                result.append((left, leftIndex))
                apex, apexIndex = left, leftIndex
                left = right = apex
                leftIndex = rightIndex = apexIndex
                index = apexIndex + 1
                continue
        if cross(apex, left, newLeft) <= 0:
            if apex == left or cross(apex, right, newLeft) > 0:
                left, leftIndex = newLeft, index
            else:
                result.append((right, rightIndex))
                apex, apexIndex = right, rightIndex
                left = right = apex
                leftIndex = rightIndex = apexIndex
                index = apexIndex + 1
                continue
        index += 1
    if result[-1][1] != len(portals) - 1:
        result.append((portals[-1][0], len(portals) - 1))
    return result


def crossing(a, b, portal):
    c, d = portal
    dx, dy = b[0] - a[0], b[1] - a[1]
    ex, ey = d[0] - c[0], d[1] - c[1]
    determinant = dx * ey - dy * ex
    if abs(determinant) <= EPSILON:
        # Collinear endpoint at a funnel corner.
        return next((p for p in (a, b) if abs(cross(c, d, p)) <= EPSILON
                     and sum((p[i] - c[i]) * (p[i] - d[i]) for i in (0, 1)) <= EPSILON), None)
    t = ((c[0] - a[0]) * ey - (c[1] - a[1]) * ex) / determinant
    u = ((c[0] - a[0]) * dy - (c[1] - a[1]) * dx) / determinant
    return (a[0] + t * dx, a[1] + t * dy, 0) if -EPSILON <= t <= 1 + EPSILON and -EPSILON <= u <= 1 + EPSILON else None


@dataclass(frozen=True)
class NavPath:
    status: str
    faces: tuple = ()
    points: tuple = ()


class NavMesh:
    def __init__(self, polygons, links):
        prepared = []
        for polygon in polygons:
            p = tuple(tuple(float(v) for v in vertex[:3]) for vertex in polygon)
            if not 3 <= len(p) <= 128 or any(len(v) != 3 or not all(map(math.isfinite, v)) for v in p):
                raise ValueError("invalid navigation polygon")
            area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(p, p[1:] + p[:1]))
            if abs(area) <= EPSILON:
                prepared.append(())
                continue
            p = p if area > 0 else tuple(reversed(p))
            if any(cross(p[i - 1], p[i], p[(i + 1) % len(p)]) < -EPSILON for i in range(len(p))):
                prepared.append(())
                continue
            prepared.append(p)
        self.polygons = tuple(prepared)
        self.links = tuple(tuple(sorted(link for link in row if prepared[i] and prepared[link[0]]))
                           for i, row in enumerate(links))
        self.centers = tuple(tuple(sum(v[d] for v in p) / len(p) for d in range(3)) if p else (0., 0., 0.)
                             for p in self.polygons)
        cells = {}
        for index, p in enumerate(self.polygons):
            if not p:
                continue
            bounds = [(math.floor(min(v[d] for v in p) / CELL_SIZE),
                       math.floor(max(v[d] for v in p) / CELL_SIZE)) for d in (0, 1)]
            if (bounds[0][1] - bounds[0][0] + 1) * (bounds[1][1] - bounds[1][0] + 1) > 10000:
                raise ValueError("navigation face extent exceeds limit")
            for x in range(bounds[0][0], bounds[0][1] + 1):
                for y in range(bounds[1][0], bounds[1][1] + 1):
                    cells.setdefault((x, y), []).append(index)
        self.cells = {cell: tuple(indices) for cell, indices in cells.items()}

    @classmethod
    def fromPolygons(cls, polygons):
        links, edges = [[] for _ in polygons], {}
        for index, p in enumerate(polygons):
            for a, b in zip(p, p[1:] + p[:1]):
                key = tuple(sorted((tuple(a), tuple(b))))
                if key in edges:
                    other = edges[key]
                    links[index].append((other, tuple(a), tuple(b)))
                    links[other].append((index, tuple(b), tuple(a)))
                else:
                    edges[key] = index
        return cls(polygons, links)

    @classmethod
    def fromResource(cls, resource):
        vertices, edges, faces = (resource[name] for name in ("vertices", "edges", "faces"))
        if not vertices or not edges or not faces or resource["streamingSets"]:
            raise ValueError("navigation requires a single nonempty mesh")
        polygons, links = [], [[] for _ in faces]
        owners = {}
        for index, face in enumerate(faces):
            start, count = face["startEdgeIndex"], face["numEdges"]
            if start < 0 or count < 3 or start + count > len(edges) or face["numUserEdges"]:
                raise ValueError("invalid navigation face edge range")
            p = []
            for ordinal in range(start, start + count):
                if ordinal in owners:
                    raise ValueError("navigation edge has multiple owners")
                owners[ordinal] = index
                edge = edges[ordinal]
                if not all(0 <= edge[k] < len(vertices) for k in ("a", "b")):
                    raise ValueError("navigation vertex index invalid")
                if edge["b"] != edges[start + (ordinal - start + 1) % count]["a"]:
                    raise ValueError("navigation polygon is not closed")
                p.append(vertices[edge["a"]][:3])
            polygons.append(p)
        if len(owners) != len(edges):
            raise ValueError("unowned navigation edges")
        for ordinal, edge in enumerate(edges):
            opposite, neighbor = edge["oppositeEdge"], edge["oppositeFace"]
            if opposite == -1 and neighbor == -1:
                continue
            if not 0 <= opposite < len(edges) or not 0 <= neighbor < len(faces):
                raise ValueError("navigation adjacency outside mesh")
            reverse = edges[opposite]
            if (owners[opposite] != neighbor or reverse["oppositeEdge"] != ordinal
                    or reverse["oppositeFace"] != owners[ordinal]):
                raise ValueError("navigation adjacency is not reciprocal")
            a, b = vertices[edge["a"]][:3], vertices[edge["b"]][:3]
            ra, rb = vertices[reverse["b"]][:3], vertices[reverse["a"]][:3]
            # Havok permits distinct seam vertices and small step-height differences.
            # Larger mismatches remain disconnected in this walking-only adapter.
            if any(math.dist(p[:2], q[:2]) > 0.01 or abs(p[2] - q[2]) > 0.4
                   for p, q in ((a, ra), (b, rb))):
                continue
            links[owners[ordinal]].append((neighbor, a, b))
        return cls(polygons, links)

    def locate(self, point):
        if len(point) != 3 or not all(math.isfinite(v) for v in point):
            return None
        cell = tuple(math.floor(point[d] / CELL_SIZE) for d in (0, 1))
        candidates = []
        for index in self.cells.get(cell, ()):
            p = self.polygons[index]
            if inside(p, point):
                surfaceHeight = height(p, point)
                if surfaceHeight is None:
                    continue
                difference = abs(surfaceHeight - point[2])
                if difference <= HEIGHT_TOLERANCE:
                    candidates.append((difference, index))
        return min(candidates)[1] if candidates else None

    def project(self, point):
        face = self.locate(point)
        return None if face is None else (point[0], point[1], height(self.polygons[face], point))

    def findPath(self, start, target, radius=0.35):
        first, last = self.locate(start), self.locate(target)
        if first is None or last is None:
            return NavPath("outside-mesh")
        if not math.isfinite(radius) or radius < 0:
            raise ValueError("invalid navigation agent radius")
        costs, previous, portals = {first: 0.0}, {}, {}
        queue = [(0.0, first)]
        closed = set()
        while queue:
            _, face = heapq.heappop(queue)
            if face in closed:
                continue
            if face == last:
                break
            closed.add(face)
            for neighbor, a, b in self.links[face]:
                if math.dist(a[:2], b[:2]) <= 2 * radius + EPSILON:
                    continue
                cost = costs[face] + math.dist(self.centers[face], self.centers[neighbor])
                if cost < costs.get(neighbor, math.inf):
                    costs[neighbor], previous[neighbor], portals[neighbor] = cost, face, (a, b)
                    heapq.heappush(queue, (cost + math.dist(self.centers[neighbor], self.centers[last]), neighbor))
        if last not in costs:
            return NavPath("no-path")
        chain = [last]
        while chain[-1] != first:
            chain.append(previous[chain[-1]])
        chain.reverse()
        start, target = self.project(start), self.project(target)
        corridor = [(start, start)]
        for before, after in zip(chain, chain[1:]):
            a, b = portals[after]
            margin = radius / math.dist(a[:2], b[:2])
            left = tuple(x + (y - x) * margin for x, y in zip(a, b))
            right = tuple(y + (x - y) * margin for x, y in zip(a, b))
            if cross(self.centers[before], self.centers[after], left) < 0:
                left, right = right, left
            corridor.append((left, right))
        corridor.append((target, target))
        anchors = pullString(corridor)
        points = self.routePoints(chain, corridor, anchors)
        if points is None:
            # Contracted seam portals can overlap. Keep the connected face corridor,
            # not an unchecked straight line, when funnel tightening is ambiguous.
            anchors = [(tuple((x + y) / 2 for x, y in zip(a, b)), i)
                       for i, (a, b) in enumerate(corridor)]
            points = self.routePoints(chain, corridor, anchors)
        return NavPath("ready", tuple(chain), tuple(points)) if points else NavPath("no-path")

    def routePoints(self, chain, corridor, anchors):
        points = [corridor[0][0]]
        for (a, i), (b, j) in zip(anchors, anchors[1:]):
            for k in range(i + 1, min(j + 1, len(corridor) - 1)):
                p = crossing(a, b, corridor[k])
                if p is None:
                    return None
                # Keep elevation at every crossed face, including both sides of a step.
                for face in (chain[k - 1], chain[k]):
                    if not inside(self.polygons[face], p):
                        return None
                    z = height(self.polygons[face], p)
                    if z is None:
                        return None
                    point = (p[0], p[1], z)
                    if math.dist(points[-1], point) > EPSILON:
                        points.append(point)
        points.append(corridor[-1][0])
        return points
