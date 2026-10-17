"""Synthetic weapon tables and animation metadata, without client assets."""
import struct


def profile(**changes):
    return dict(enabled=True, weaponId=1030411, weaponCategory=32, durationMs=2033,
                moveMs=967, speedMilli=2585, contactStartMs=0, contactEndMs=1133,
                reboundMs=933, durationSource='table', animationPath='') | changes


def table(width, rows):
    header = bytearray(140)
    struct.pack_into('<4sIII', header, 0, b'MSES', 7, width, len(rows))
    header[64:68] = b'GBK\0'
    struct.pack_into('<I', header, 132, 140)
    return bytes(header) + b''.join(rows)


def weapons(*entries):
    rows = []
    for weaponId, category in entries or ((1030411, 32),):
        row = bytearray(2829)
        struct.pack_into('<I', row, 0, weaponId)
        struct.pack_into('<H', row, 56, category)
        rows.append(row)
    return table(2829, rows)


def parameters(*, category=32, actionType=13, duration='2033', extra=(), omit=(), rows=None):
    if rows is not None:
        return table(6412, rows)
    fields = [('[持续时间]过程', duration), ('[移动时间]', '967'), ('[移动初速度]', '2585'),
              ('[碰撞时间]开始', '0'), ('[碰撞时间]结束', '1133')]
    fields = [(key, value) for key, value in fields if key not in omit] + list(extra)
    row = bytearray(6412)
    struct.pack_into('<IhhhH', row, 0, 6, category, actionType, 2, len(fields))
    for index, (key, value) in enumerate(fields):
        struct.pack_into('<64s64s', row, 12 + index * 128, key.encode('gbk'), value.encode('gbk'))
    return table(6412, [row])


def states(duration=933):
    row = bytearray(10950)
    struct.pack_into('<I', row, 0, 443)
    struct.pack_into('<i', row, 0x1e3e, duration)
    return table(10950, [row])


def tagInteger(value):
    value = (abs(value) << 1) | (value < 0)
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def tagString(value):
    data = value.encode('utf-8')
    return tagInteger(len(data)) + data


def animation(seconds=4 / 3):
    header = bytes.fromhex('1e0db0cacefa11d0') + b'\x02\x0a\x1chk_2014.2.5-r1\x15\x00\x01\x00\x14\x00'
    definition = (tagInteger(2) + tagString('hkaSplineCompressedAnimation')
                  + tagInteger(0) * 2 + tagInteger(1) + tagString('duration') + tagInteger(3))
    return header + definition + tagInteger(4) + tagInteger(1) + b'\x01' + struct.pack('<f', seconds) + tagInteger(7)
