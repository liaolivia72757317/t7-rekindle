"""One local player's formation and per-hero native weapon-slot selections."""
from copy import deepcopy

from .hero_resources import DEFAULT_HERO, FIST_ID

SCHEMA_VERSION = 1
FORMATION_SIZE = 4


def defaultWeapons(hero, catalogue):
    choices = list(hero.defaults)
    if not any(not catalogue.weapons[hero.weapon(*choice)].shield for choice in choices):
        choices.insert(0, next((g, i) for g, group in enumerate(hero.groups)
                               for i, tid in enumerate(group) if tid and not catalogue.weapons[tid].shield))
    choices.sort(key=lambda choice: catalogue.weapons[hero.weapon(*choice)].shield)
    return [dict(slot=i + 1, group=group, index=index) for i, (group, index) in enumerate(choices[:4])]


def create(catalogue):
    return dict(schemaVersion=SCHEMA_VERSION, revision=0, formation=[DEFAULT_HERO],
                weapons={str(tid): defaultWeapons(hero, catalogue) for tid, hero in catalogue.heroes.items()})


def validate(value, catalogue=None):
    if (type(value) is not dict or set(value) != {'schemaVersion', 'revision', 'formation', 'weapons'}
            or type(value['schemaVersion']) is not int or value['schemaVersion'] != SCHEMA_VERSION
            or type(value['revision']) is not int or not 0 <= value['revision'] < 0xffffffffffffffff
            or type(value['formation']) is not list or len(value['formation']) > FORMATION_SIZE
            or type(value['weapons']) is not dict or len(value['weapons']) > 500):
        return False
    ids = value['formation']
    if any(type(tid) is not int or tid <= 0 for tid in ids) or len(set(ids)) != len(ids):
        return False
    for key, slots in value['weapons'].items():
        if (type(key) is not str or not key.isascii() or not key.isdecimal() or str(int(key)) != key
                or type(slots) is not list or len(slots) > 4):
            return False
        occupied, selected, weaponIds, shields = set(), set(), set(), 0
        for item in slots:
            if (type(item) is not dict or set(item) != {'slot', 'group', 'index'}
                    or any(type(n) is not int for n in item.values())
                    or not 1 <= item['slot'] <= 4 or not 0 <= item['group'] < 8 or not 0 <= item['index'] < 6
                    or item['slot'] in occupied or (item['group'], item['index']) in selected):
                return False
            occupied.add(item['slot'])
            selected.add((item['group'], item['index']))
            if catalogue is not None:
                try:
                    tid = catalogue.heroes[int(key)].weapon(item['group'], item['index'])
                    if tid in weaponIds:
                        return False
                    weaponIds.add(tid)
                    shields += catalogue.weapons[tid].shield
                except (KeyError, ValueError):
                    return False
        if shields > 1:
            return False
    if any(str(tid) not in value['weapons'] for tid in ids):
        return False
    return catalogue is None or set(value['weapons']) == {str(tid) for tid in catalogue.heroes}


def change(profile, catalogue, *, formation=None, heroId=None, slots=None):
    candidate = deepcopy(profile)
    if formation is not None:
        candidate['formation'] = list(formation)
    if heroId is not None:
        candidate['weapons'][str(heroId)] = sorted(deepcopy(slots), key=lambda item: item['slot'])
    if not validate(candidate, catalogue):
        raise ValueError('invalid formation or weapon configuration')
    if candidate != profile:
        candidate['revision'] += 1
    return candidate


def equipment(profile, catalogue, heroId):
    hero = catalogue.heroes[heroId]
    return [dict(slot=item['slot'], weaponId=hero.weapon(item['group'], item['index']))
            for item in profile['weapons'][str(heroId)]]


def initialWeapon(items, catalogue):
    return next((item['weaponId'] for item in sorted(items, key=lambda v: v['slot'])
                 if not catalogue.weapons[item['weaponId']].shield), FIST_ID)


def offhand(items, catalogue, weaponId):
    if catalogue.weapons[weaponId].hand != 2 or weaponId == FIST_ID:
        return 0
    return next((item['weaponId'] for item in items if catalogue.weapons[item['weaponId']].shield), 0)
