"""Weapon-selected dash parameters and explicit animation-duration fallback."""
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
import struct

from .animation_resource import animationDurationMs
from .vfs_resource import ResourceError, readResource

WEAPON_PATH = 'data/table/s_weapon_cli.bin'
PARAM_PATH = 'data/table/s_battle_param_cli.bin'
STATE_PATH = 'data/table/s_act_state_cli.bin'
# Native states 438/439/440/443 use action type 13; type 32 is a war skill.
DASH_ACTION_TYPE = 13
DASH_ANIMATIONS = {
    1030411: 'data/actors/havok/shgameexport/animations/m01b_common_dashattack_2h_hsd_animation.hkt',
}


@dataclass(frozen=True)
class DashAttackProfile:
    enabled: bool = False
    weaponId: int = 0
    weaponCategory: int = -1
    durationMs: int = 0
    moveMs: int = 0
    speedMilli: int = 0
    contactStartMs: int = 0
    contactEndMs: int = 0
    reboundMs: int = 0
    durationSource: str = 'unavailable'
    animationPath: str = ''


@dataclass(frozen=True)
class DashResources:
    profile: DashAttackProfile
    diagnostics: tuple

    def asDict(self):
        return {'dashAttack': asdict(self.profile),
                'diagnostics': [dict(level=level, message=message) for level, message in self.diagnostics]}


def validProfile(value):
    if type(value) is not dict or type(value.get('enabled')) is not bool:
        return False
    if (type(value.get('weaponId')) is not int or not 0 <= value['weaponId'] <= 0x7fffffff
            or type(value.get('weaponCategory')) is not int or not -1 <= value['weaponCategory'] <= 32767
            or value.get('durationSource') not in ('table', 'animation', 'unavailable')
            or type(value.get('animationPath')) is not str):
        return False
    times = ('durationMs', 'moveMs', 'contactStartMs', 'contactEndMs', 'reboundMs')
    if not all(type(value.get(key)) is int and 0 <= value[key] <= 65535 for key in times):
        return False
    if type(value.get('speedMilli')) is not int or not 0 <= value['speedMilli'] <= 0x7fffffff:
        return False
    return not value['enabled'] or (
        value['weaponId'] > 0 and value['weaponCategory'] >= 0
        and value['durationSource'] != 'unavailable' and value['reboundMs'] > 0
        and 0 < value['durationMs'] and value['moveMs'] <= value['durationMs']
        and value['contactStartMs'] <= value['contactEndMs'] <= value['durationMs'])


def rows(data, width):
    if len(data) < 140:
        raise ValueError('truncated battle resource table')
    magic, version, actualWidth, count = struct.unpack_from('<4sIII', data)
    if (magic != b'MSES' or version != 7 or actualWidth != width
            or data[64:68] != b'GBK\0' or struct.unpack_from('<I', data, 132)[0] != 140
            or len(data) != 140 + count * width):
        raise ValueError('unsupported battle resource table layout')
    return (memoryview(data)[offset:offset + width] for offset in range(140, len(data), width))


def selectRow(data, width, keyFormat, key):
    matches = [row for row in rows(data, width) if struct.unpack_from(keyFormat, row) == key]
    if len(matches) != 1:
        raise ValueError(f'expected one row for key={key}, found {len(matches)}')
    return matches[0]


def weaponCategory(data, weaponId):
    category = struct.unpack_from('<H', selectRow(data, 2829, '<I', (weaponId,)), 56)[0]
    if category > 32767:
        raise ValueError('weapon category is outside the battle parameter key range')
    return category


def battleParameters(data, category):
    row = selectRow(data, 6412, '<Ihhh', (6, category, DASH_ACTION_TYPE, 2))
    count = struct.unpack_from('<H', row, 10)[0]
    if count > 50:
        raise ValueError('battle parameter count exceeds record')
    result = {}
    for index in range(count):
        rawName, rawValue = struct.unpack_from('<64s64s', row, 12 + index * 128)
        if b'\0' not in rawName or b'\0' not in rawValue:
            raise ValueError('unterminated battle parameter')
        name = rawName.split(b'\0', 1)[0].decode('gbk')
        if name:
            result[name] = None if name in result else rawValue.split(b'\0', 1)[0].decode('gbk')
    return result


def reboundDuration(data):
    value = struct.unpack_from('<i', selectRow(data, 10950, '<I', (443,)), 0x1e3e)[0]
    if not 1 <= value <= 65535:
        raise ValueError('state 443 requires a positive literal duration')
    return value


def readSelected(clientDirectory, archive, path, parser):
    loose = Path(clientDirectory).parent / path
    try:
        loose.stat()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise ResourceError('archive_read', archive, path, str(error)) from error
    else:
        raise ResourceError('unsupported_overlay', archive, path, 'loose dash resource is not supported')
    return readResource(clientDirectory, archive, path, parser)


@lru_cache(maxsize=8)
def loadDashAttack(clientDirectory, weaponId):
    diagnostics = []
    category = -1

    def report(error, field):
        diagnostics.append(('ERROR', f'{error} weaponId={weaponId} key=(6,{category},{DASH_ACTION_TYPE},2) field={field}'))

    def read(path, parser, field, archive='data1.vfs'):
        try:
            return readSelected(clientDirectory, archive, path, parser)
        except ResourceError as error:
            report(error, field)
            return None

    selected = read(WEAPON_PATH, lambda data: weaponCategory(data, weaponId), 'weaponCategory')
    category = selected if selected is not None else -1
    fields = read(PARAM_PATH, lambda data: battleParameters(data, category), 'parameters') if category >= 0 else None
    fields = fields if fields is not None else {}
    for name, value in fields.items():
        if value is None:
            report(ResourceError('content_mismatch', 'data1.vfs', PARAM_PATH, 'duplicate parameter'), name)

    def parameter(name, *, minimum=0, maximum=65535):
        raw = fields.get(name)
        if (type(raw) is str and raw.isascii() and raw.isdecimal()
                and minimum <= int(raw) <= maximum):
            return int(raw)
        if name not in fields or raw is not None:
            report(ResourceError('content_mismatch', 'data1.vfs', PARAM_PATH, 'missing or invalid parameter'), name)
        return None

    duration = parameter('[持续时间]过程', minimum=1)
    source, animationPath = 'table', ''
    if duration is None:
        source = 'unavailable'
        animationPath = DASH_ANIMATIONS.get(weaponId, '')
        if animationPath:
            duration = read(animationPath, animationDurationMs, 'animationDuration', 'actor.vfs')
            if duration is not None:
                source = 'animation'
                diagnostics.append(('WARN', f'dash-duration-fallback weaponId={weaponId} '
                                    f'key=(6,{category},{DASH_ACTION_TYPE},2) animation={animationPath} durationMs={duration}'))
        else:
            report(ResourceError('entry_missing', 'actor.vfs', '', 'no verified DashAttack animation binding'), 'animationDuration')
    move = parameter('[移动时间]')
    speed = parameter('[移动初速度]', maximum=0x7fffffff)
    start, end = parameter('[碰撞时间]开始'), parameter('[碰撞时间]结束')
    rebound = read(STATE_PATH, reboundDuration, 'reboundMs')
    available = category >= 0 and all(value is not None for value in (duration, move, speed, start, end, rebound))
    values = dict(weaponId=weaponId, weaponCategory=category, durationMs=duration or 0,
                  moveMs=move or 0, speedMilli=speed or 0, contactStartMs=start or 0,
                  contactEndMs=end or 0, reboundMs=rebound or 0, durationSource=source, animationPath=animationPath)
    if available and not validProfile(dict(enabled=True, **values)):
        report(ResourceError('content_mismatch', 'data1.vfs', PARAM_PATH,
                             'movement/contact window exceeds selected duration'), 'timeline')
        available = False
    if not available:
        diagnostics.append(('ERROR', f'dash-disabled weaponId={weaponId} key=(6,{category},{DASH_ACTION_TYPE},2): required dash parameters unavailable'))
    return DashResources(DashAttackProfile(enabled=available, **values), tuple(diagnostics))
