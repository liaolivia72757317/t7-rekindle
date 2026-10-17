"""Static server navigation with a read-only Havok v5 adapter for offline export."""
from functools import lru_cache
from hashlib import sha256
import math
import struct

from .vfs_resource import readResource
from . import map_navigation_data

MAGIC = bytes.fromhex("1e0db0cacefa11d0")
MAX_ITEMS = 100000
MAX_BYTES = 16 * 1024 * 1024
NAV_PATH = 'data/scene/map/lysd/anavmesh.dat'
NAV_HASH = "b92da3edc697ada355a95359d94ff3025841bf2de57833297901361452c539a3"
EDGE_FIELDS = ('a', 'b', 'oppositeEdge', 'oppositeFace')
FACE_FIELDS = ('startEdgeIndex', 'numEdges', 'numUserEdges')


class TagReader:
    def __init__(self, data):
        if len(data) > MAX_BYTES:
            raise ValueError("navigation resource exceeds size limit")
        self.data, self.offset = data, 0
        self.strings = ["", None]
        self.classes = [None]
        self.budget = 1000000

    def read(self, size):
        if size < 0 or size > len(self.data) - self.offset:
            raise ValueError(f"truncated navigation resource at {self.offset}")
        value = self.data[self.offset:self.offset + size]
        self.offset += size
        return value

    def integer(self):
        value = 0
        for shift in range(0, 35, 7):
            byte = self.read(1)[0]
            value |= (byte & 127) << shift
            if byte < 128:
                if value > 0x1FFFFFFFF:
                    break
                return -(value >> 1) if value & 1 else value >> 1
        raise ValueError("invalid navigation integer")

    def count(self, maximum=MAX_ITEMS):
        value = self.integer()
        if not 0 <= value <= maximum:
            raise ValueError("navigation count outside supported bounds")
        return value

    def string(self):
        size = self.integer()
        if size < 0:
            if not 0 < -size < len(self.strings):
                raise ValueError("invalid navigation string reference")
            value = self.strings[-size]
        else:
            if size > 256:
                raise ValueError("navigation string exceeds limit")
            value = self.read(size).decode("ascii")
            self.strings.append(value)
        if value is None:
            raise ValueError("null navigation schema name")
        return value

    def fields(self, classId, depth=0):
        if not 0 < classId < len(self.classes) or depth > 16:
            raise ValueError("invalid navigation class hierarchy")
        _, _, parent, fields = self.classes[classId]
        return (self.fields(parent, depth + 1) if parent else []) + fields

    def values(self, kind, name, count, array=False, depth=0):
        self.budget -= count
        if self.budget < 0 or depth > 16:
            raise ValueError("navigation object budget exceeded")
        if kind & 16:
            return [self.values(kind & 15, name, self.count(), True, depth + 1)
                    for _ in range(count)]
        if kind == 0:
            return [0] * count
        if kind == 1:
            return list(self.read(count))
        if kind == 2:
            if array and count and self.integer() != 4:
                raise ValueError("unsupported navigation integer array encoding")
            return [self.integer() for _ in range(count)]
        if kind in (3, 4):
            width = 1 if kind == 3 else 4
            if array and count and kind == 4 and self.integer() != width:
                raise ValueError("unsupported navigation vector dimension")
            values = list(struct.iter_unpack("<" + "f" * width, self.read(count * width * 4)))
            if not all(math.isfinite(v) for row in values for v in row):
                raise ValueError("non-finite navigation geometry")
            return [row[0] for row in values] if width == 1 else values
        if kind == 9:
            ids = [i for i, c in enumerate(self.classes) if c and c[0] == name]
            if len(ids) != 1:
                raise ValueError("unknown navigation structure")
            return self.objects(ids[0], count, depth + 1, columnar=array)
        raise ValueError("unsupported navigation field type")

    def objects(self, classId, count=1, depth=0, *, columnar=False):
        if not count:
            return []
        fields = self.fields(classId)
        mask = int.from_bytes(self.read((len(fields) + 7) // 8), "little")
        if mask >> len(fields):
            raise ValueError("invalid navigation presence mask")
        rows = [{} for _ in range(count)]
        for index, (name, kind, reference) in enumerate(fields):
            values = (self.values(kind, reference, count, array=columnar, depth=depth)
                      if mask & (1 << index) else [([] if kind & 16 else 0)] * count)
            for row, value in zip(rows, values):
                row[name] = value
        return rows

    def document(self):
        if self.read(8) != MAGIC or self.integer() != 1 or self.integer() != 5:
            raise ValueError("unsupported navigation tagfile header")
        if self.string() != "hk_2014.2.5-r1":
            raise ValueError("unsupported navigation SDK version")
        if [self.integer() for _ in range(4)] != [-10, 0, 0, 0]:
            raise ValueError("unsupported navigation v5 header layout")
        tag = self.integer()
        while tag == 2:
            if len(self.classes) > 32:
                raise ValueError("too many navigation classes")
            name, version, parent = self.string(), self.count(100), self.count(len(self.classes) - 1)
            fields = []
            for _ in range(self.count(32)):
                field, kind = self.string(), self.count(31)
                fields.append((field, kind, self.string() if kind & 15 == 9 else None))
            self.classes.append((name, version, parent, fields))
            tag = self.integer()
        if tag != 4:
            raise ValueError("navigation root object missing")
        classId = self.count(len(self.classes) - 1)
        if not classId or self.classes[classId][:2] != ("hkaiNavMesh", 14):
            raise ValueError("unsupported navigation root class")
        root = self.objects(classId)[0]
        if self.integer() != 7 or self.offset != len(self.data):
            raise ValueError("navigation resource has unconsumed objects or bytes")
        return root


def readNavigationResource(clientDirectory):
    """Offline export only; verify and parse the supported original topology."""
    from .navigation import NavMesh
    def parse(data):
        if sha256(data).hexdigest() != NAV_HASH:
            raise ValueError("navigation resource does not match the supported client")
        resource = TagReader(data).document()
        NavMesh.fromResource(resource)
        return resource
    return readResource(clientDirectory, 'map.vfs', NAV_PATH, parse)


@lru_cache(maxsize=1)
def loadNavigation(clientDirectory=''):
    """The directory argument is retained for callers; runtime never reads it."""
    from .navigation import NavMesh
    def records(fields, rows):
        if any(len(row) != len(fields) or any(type(value) is not int for value in row) for row in rows):
            raise ValueError('invalid static navigation topology row')
        return [dict(zip(fields, row)) for row in rows]
    vertices = map_navigation_data.VERTICES
    if any(len(row) != 3 or any(type(value) not in (int, float) or not math.isfinite(value)
                               for value in row) for row in vertices):
        raise ValueError('invalid static navigation vertex')
    return NavMesh.fromResource(dict(vertices=vertices,
        edges=records(EDGE_FIELDS, map_navigation_data.EDGES),
        faces=records(FACE_FIELDS, map_navigation_data.FACES), streamingSets=()))
