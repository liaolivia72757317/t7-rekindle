"""Original USER_INFO (31) title subset, with zero/default career statistics."""
import struct

COMMAND = 31
USER_ID = 10000


def basicInfo(equipped=0):
    data = bytearray(296)
    struct.pack_into('>ii', data, 0, 7, 100)
    struct.pack_into('>i', data, 64, equipped)
    return bytes(data)


def userInfo(name, equipped=0):
    return struct.pack('>HiII', 2, 0, USER_ID, len(name) + 1) + name + b'\0' + basicInfo(equipped)


def statInfo(owned, equipped=0):
    if len(owned) > 64 or any(type(tid) is not int or not 0 < tid <= 0x7fffffff for tid in owned):
        raise ValueError('invalid original title array')
    # USER_STAT_INFO_SHOW_DATA_DEF: zero-length PVE arrays precede user_title;
    # inst_use_num=0 and the 22 career counters follow the counted title array.
    stats = bytes(4460) + struct.pack('>i', len(owned)) + b''.join(struct.pack('>i', tid) for tid in owned) + bytes(90)
    return struct.pack('>HiI', 4, 0, USER_ID) + basicInfo(equipped) + stats


def setResult(success):
    return struct.pack('>Hi', 6, 0 if success else 570005)
