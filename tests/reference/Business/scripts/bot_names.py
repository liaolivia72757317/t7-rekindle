"""Read NPC display names from the selected client's AI name table."""
from functools import lru_cache
import random
import struct

from .contracts import encodePlayerName
from .vfs_resource import readResource

NAME_PATH = 'data/table/sh_res_ai_name_cli.bin'
HEADER_SIZE, RECORD_SIZE = 140, 68
PREFIXES = {'aggressive': '[进攻]', 'balanced': '[全能]', 'defensive': '[防守]', 'berserker': '[疯狗]'}


def parseNames(data):
    if len(data) < HEADER_SIZE:
        raise ValueError("truncated AI name table")
    magic, version, width, count = struct.unpack_from('<4sIII', data)
    if (magic != b'MSES' or version != 7 or width != RECORD_SIZE or not 1 <= count <= 4096
            or data[64:68] != b'GBK\0' or struct.unpack_from('<I', data, 132)[0] != HEADER_SIZE
            or len(data) != HEADER_SIZE + count * width):
        raise ValueError("unsupported AI name table layout")
    names = []
    for offset in range(HEADER_SIZE, len(data), width):
        _, category, raw = struct.unpack_from('<I32s32s', data, offset)
        if category.split(b'\0', 1)[0] != b'npc':
            continue
        if b'\0' not in raw:
            raise ValueError("unterminated AI name")
        name = raw.split(b'\0', 1)[0].decode('gbk').strip()
        encodePlayerName(name)
        if name not in names:
            names.append(name)
    if not names:
        raise ValueError("AI name table has no NPC names")
    return tuple(names)


@lru_cache(maxsize=1)
def loadNames(clientDirectory):
    return readResource(clientDirectory, 'data1.vfs', NAME_PATH, parseNames)


def assignNames(names, count, seed, playerName):
    available = [name for name in names if name != playerName and all(
        prefix + name != playerName and len((prefix + name).encode('gbk')) <= 31 for prefix in PREFIXES.values())]
    if len(available) < count:
        raise ValueError("not enough distinct client NPC names for this roster")
    return random.Random(seed).sample(available, count)
