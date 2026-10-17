"""Playable vanguards and their native weapon-group references from client tables."""
from dataclasses import dataclass
from functools import lru_cache
import struct
from types import MappingProxyType

from .dash_resources import rows, readSelected

HERO_PATH = 'data/table/s_heroscard_cli.bin'
UNLOCK_PATH = 'data/table/s_res_herocard_unlock_cli.bin'
GROUP_PATH = 'data/table/s_res_weapon_unlock_cli.bin'
WEAPON_PATH = 'data/table/s_weapon_cli.bin'
CATEGORY_PATH = 'data/table/s_weapon_category_cli.bin'
DEFAULT_HERO = 1101
FIST_ID = 100000


def text(row, offset, size):
    raw = bytes(row[offset:offset + size])
    if b'\0' not in raw:
        raise ValueError('unterminated client resource string')
    return raw.split(b'\0', 1)[0].decode('gbk')


def uniqueRows(data, width, keyFormat='<I'):
    result = {}
    for row in rows(data, width):
        key = struct.unpack_from(keyFormat, row)[0]
        if key in result:
            raise ValueError(f'duplicate resource key {key}')
        result[key] = row
    return result


@dataclass(frozen=True)
class Weapon:
    resourceId: int
    category: int
    family: int
    hand: int
    name: str

    @property
    def shield(self):
        return self.family == 12


@dataclass(frozen=True)
class Hero:
    resourceId: int
    name: str
    gender: int
    groups: tuple
    defaults: tuple
    groupIds: tuple
    nativeGroups: tuple

    def weapon(self, group, index):
        if (type(group) is not int or type(index) is not int
                or not 0 <= group < 8 or not 0 <= index < 6):
            raise ValueError('weapon group/index outside native array')
        value = self.groups[group][index]
        if not value:
            raise ValueError('weapon is outside this hero\'s available melee/shield groups')
        return value


@dataclass(frozen=True)
class Catalogue:
    heroes: object
    weapons: object

    def position(self, heroId):
        return tuple(self.heroes).index(heroId) + 1

    def reference(self, guid, position):
        if guid not in self.heroes or self.position(guid) != position:
            raise ValueError('hero reference does not match its container position')
        return guid


def parseCatalogue(heroData, unlockData, groupData, weaponData, categoryData):
    heroRows = uniqueRows(heroData, 7286)
    groupRows = uniqueRows(groupData, 1434)
    categoryRows = uniqueRows(categoryData, 550, '<H')
    weapons, unknownCategories = {}, {}
    weaponRows = uniqueRows(weaponData, 2829)
    for tid, row in weaponRows.items():
        category = struct.unpack_from('<H', row, 56)[0]
        if category not in categoryRows:
            unknownCategories[tid] = category
            continue
        spec = categoryRows[category]
        family, hand = struct.unpack_from('<H', spec, 2)[0], struct.unpack_from('<H', spec, 46)[0]
        if family in (1, 2, 3, 12) or tid == FIST_ID:
            weapons[tid] = Weapon(tid, category, family, hand, text(row, 16, 40))
    playable = set()
    for row in rows(unlockData, 300):
        enabled = struct.unpack_from('<H', row, 36)[0]
        if enabled not in (0, 1):
            raise ValueError('invalid hero unlock visibility')
        if enabled:
            heroId = struct.unpack_from('<I', row, 42)[0]
            if heroId not in heroRows:
                raise ValueError('unlock graph references missing hero')
            if text(heroRows[heroId], 163, 12) == '先锋':
                playable.add(heroId)
    heroes = {}
    for heroId in sorted(playable):
        row = heroRows[heroId]
        if struct.unpack_from('<H', row, 4209)[0] != 8:
            raise ValueError('unsupported hero weapon-group count')
        groups, defaults, groupIds, nativeGroups = [], [], [], []
        for group in range(8):
            groupId, initial, _ = struct.unpack_from('<III', row, 4211 + group * 12)
            values = [0] * 6
            nativeValues = [0] * 6
            if groupId:
                if groupId not in groupRows:
                    raise ValueError('hero references missing weapon group')
                entry = groupRows[groupId]
                count = struct.unpack_from('<H', entry, 64)[0]
                if count > 6:
                    raise ValueError('weapon styles exceed native group capacity')
                for index in range(count):
                    tid = struct.unpack_from('<I', entry, 66 + index * 228)[0]
                    if tid and (tid not in weaponRows or tid in unknownCategories):
                        raise ValueError(f'hero {heroId} references unavailable weapon {tid}')
                    nativeValues[index] = tid
                    if tid in weapons:
                        values[index] = tid
                        if tid == initial:
                            defaults.append((group, index))
            groups.append(tuple(values))
            groupIds.append(groupId)
            nativeGroups.append(tuple(nativeValues))
        if not any(tid and not weapons[tid].shield for group in groups for tid in group):
            raise ValueError(f'playable vanguard {heroId} has no melee weapon')
        heroes[heroId] = Hero(heroId, text(row, 4, 30), row[34], tuple(groups), tuple(defaults),
                             tuple(groupIds), tuple(nativeGroups))
    if not heroes or DEFAULT_HERO not in heroes or FIST_ID not in weapons:
        raise ValueError('vanguard catalogue lacks default hero or fists')
    return Catalogue(MappingProxyType(heroes), MappingProxyType(weapons))


@lru_cache(maxsize=1)
def loadCatalogue(clientDirectory):
    return parseCatalogue(*(readSelected(clientDirectory, 'data1.vfs', path, lambda data: data)
                            for path in (HERO_PATH, UNLOCK_PATH, GROUP_PATH, WEAPON_PATH, CATEGORY_PATH)))
