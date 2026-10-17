"""Read duration metadata from the supported Havok animation tagfile format."""
import math
import struct


HEADER = bytes.fromhex('1e0db0cacefa11d0') + b'\x02\x0a\x1chk_2014.2.5-r1\x15\x00\x01\x00\x14\x00'


class AnimationReader:
    def __init__(self, data):
        if not data.startswith(HEADER):
            raise ValueError('unsupported animation tagfile header')
        self.data, self.position = data, len(HEADER)
        self.strings = [None, '', 'hk_2014.2.5-r1']
        self.classes, self.names = [None], {}
        while self.position < len(data) and data[self.position] == 4:
            self.integer()
            name, version, parent, count = self.string(), self.integer(), self.integer(), self.integer()
            if (not name or name in self.names or version < 0 or not 0 <= parent < len(self.classes)
                    or not 0 <= count <= 1024):
                raise ValueError('invalid animation class definition')
            fields = list(self.classes[parent][1]) if parent else []
            for _ in range(count):
                field, kind = self.string(), self.integer()
                size = self.integer() if kind & 32 else None
                subtype = self.string() if kind & 15 in (8, 9) else None
                if not field or kind < 0 or (size is not None and not 0 <= size <= 1024):
                    raise ValueError('invalid animation member definition')
                fields.append((field, kind, size, subtype))
            info = (name, fields)
            self.classes.append(info)
            self.names[name] = info

    def take(self, count):
        if not 0 <= count <= len(self.data) - self.position:
            raise ValueError('truncated animation tagfile')
        result = self.data[self.position:self.position + count]
        self.position += count
        return result

    def integer(self):
        value = 0
        for shift in range(0, 70, 7):
            byte = self.take(1)[0]
            value |= (byte & 127) << shift
            if byte < 128:
                return -(value >> 1) if value & 1 else value >> 1
        raise ValueError('invalid animation integer')

    def string(self):
        length = self.integer()
        if length <= 0:
            if -length >= len(self.strings):
                raise ValueError('invalid animation string reference')
            return self.strings[-length]
        value = self.take(length).decode('utf-8')
        self.strings.append(value)
        return value

    def members(self, info, count=1, bulk=False):
        mask = int.from_bytes(self.take((len(info[1]) + 7) // 8), 'little')
        if mask >> len(info[1]):
            raise ValueError('invalid animation member mask')
        rows = [dict(className=info[0]) for _ in range(count)]
        for index, (name, kind, size, subtype) in enumerate(info[1]):
            if mask & (1 << index):
                values = self.values(kind, size, subtype, count, bulk)
                for row, value in zip(rows, values):
                    row[name] = value
        return rows

    def values(self, kind, size, subtype, count, bulk=False):
        if kind & 16:
            return [self.array(kind & ~16, size, subtype, self.integer()) for _ in range(count)]
        if kind & 32:
            return [self.values(kind & ~32, None, subtype, size) for _ in range(count)]
        if kind == 0:
            return [None] * count
        if kind == 1:
            return list(self.take(count))
        if kind in (2, 8):
            if kind == 2 and bulk and self.integer() != 4:
                raise ValueError('unsupported animation integer array encoding')
            return [self.integer() for _ in range(count)]
        if kind == 3:
            return list(struct.unpack('<' + 'f' * count, self.take(count * 4)))
        if kind in (4, 5, 6, 7):
            width = (kind - 3) * 4
            return [struct.unpack('<' + 'f' * width, self.take(width * 4)) for _ in range(count)]
        if kind == 9 and subtype in self.names:
            return self.members(self.names[subtype], count, bulk)
        if kind == 10:
            return [self.string() for _ in range(count)]
        raise ValueError('unsupported animation member type')

    def array(self, kind, size, subtype, count):
        if not 0 <= count <= min(len(self.data), 1000000):
            raise ValueError('invalid animation array extent')
        if not count:
            return []
        if kind == 1:
            self.take(count)
            return None  # Compressed animation bytes do not contribute duration metadata.
        return self.values(kind, size, subtype, count, True)

    def objects(self):
        result = [None]
        while True:
            tag = self.integer()
            if tag == 7:
                if self.position != len(self.data):
                    raise ValueError('trailing animation data')
                return result
            if tag != 4:
                raise ValueError('unsupported animation object tag')
            classId = self.integer()
            if not 0 < classId < len(self.classes):
                raise ValueError('invalid animation class reference')
            result.extend(self.members(self.classes[classId]))

    def durations(self):
        supported = ('hkaSplineCompressedAnimation', 'hkaInterleavedUncompressedAnimation', 'hkaQuantizedAnimation')
        return [value.get('duration') for value in self.objects()[1:] if value['className'] in supported]


class ClipReader(AnimationReader):
    """Animation assets encode bulk vector widths; character/behavior assets do not."""

    def values(self, kind, size, subtype, count, bulk=False):
        if kind & 32 and not kind & 16:
            return [self.values(kind & ~32, None, subtype, size, True) for _ in range(count)]
        if count and bulk and kind in (4, 5, 6, 7):
            width, declaredWidth = self.integer(), (kind - 3) * 4
            if not 0 < width <= declaredWidth:
                raise ValueError('unsupported animation bulk vector width')
            return [struct.unpack('<' + 'f' * width, self.take(width * 4))
                    + (0.,) * (declaredWidth - width) for _ in range(count)]
        if count and bulk and kind == 3:
            raise ValueError('unsupported animation bulk scalar real encoding')
        return super().values(kind, size, subtype, count, bulk)


def animationDurationMs(data):
    durations = ClipReader(data).durations()
    if len(durations) != 1:
        raise ValueError('expected one supported animation clip')
    seconds = durations[0]
    if type(seconds) is not float or not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('invalid animation duration')
    millis = math.floor(seconds * 1000 + .5)
    if not 1 <= millis <= 65535:
        raise ValueError('animation duration outside state-time range')
    return millis
