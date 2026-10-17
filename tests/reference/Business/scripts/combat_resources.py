"""Server-owned combat profiles, plus table readers used only by the offline exporter."""
from copy import deepcopy
from functools import lru_cache
import math
import struct

from . import dash_resources, hero_resources, movement_animation
from .dash_resources import rows, readSelected

PARAM_PATH = 'data/table/s_battle_param_cli.bin'
STATE_PATH = 'data/table/s_act_state_cli.bin'
CHARACTER_PATH = 'data/table/s_res_havok_character_create_clt.bin'
DIRECTIONS = {300560: 1, 300550: 4, 300570: 2, 300540: 3}


def validProfile(value):
    if (type(value) is not dict or type(value.get('weaponId')) is not int or value['weaponId'] <= 0
            or type(value.get('weaponCategory')) is not int or not 0 <= value['weaponCategory'] <= 32767
            or type(value.get('hand')) is not int or value['hand'] not in (1, 2, 3)
            or type(value.get('shield')) is not bool or type(value.get('changeMs')) is not int):
        return False
    if value['shield']:
        return value['hand'] == 1 and value.get('attacks') == {} and value['changeMs'] == 0
    attacks, movement, dash = value.get('attacks'), value.get('movementAnimation'), value.get('dashAttack')
    guard = value.get('guard')
    if (not 0 < value['changeMs'] <= 65535 or type(attacks) is not dict
            or set(attacks) != {str(key) for key in DIRECTIONS} or type(movement) is not dict
            or movement.get('weaponId') != value['weaponId']
            or not all(type(movement.get(key)) in (float, int) and math.isfinite(movement[key]) and movement[key] > 0
                       for key in ('forwardSlow', 'lateralSlow', 'backwardSlow', 'forwardSprint'))
            or type(dash) is not dict or (dash and not dash_resources.validProfile(dash))
            or type(guard) is not dict or set(guard) != {'prepareMs', 'successMs'}
            or any(type(n) is not int or not 0 < n <= 65535 for n in guard.values())):
        return False
    if 'hurtMs' in value and (type(value['hurtMs']) is not int or not 0 < value['hurtMs'] <= 65535):
        return False
    for attack in attacks.values():
        if type(attack) is not dict:
            return False
        if 'enabled' in attack and type(attack['enabled']) is not bool:
            return False
        phases = attack.get('phases')
        if (type(phases) is not list or len(phases) != 4 or phases[1] != 0
                or any(type(n) is not int or not 0 <= n <= 65535 for n in phases)
                or any(phases[i] == 0 for i in (0, 2, 3))
                or any(type(attack.get(key)) is not int or not 1 <= attack[key] <= 65535 for key in ('parried', 'rebound'))
                or any(type(attack.get(key)) is not int for key in ('contactStartMs', 'contactEndMs'))
                or not 0 <= attack['contactStartMs'] < attack['contactEndMs'] <= phases[2]):
            return False
    return any(attack.get('enabled', True) for attack in attacks.values())


def parameters(data):
    result = {}
    for row in rows(data, 6412):
        key = struct.unpack_from('<5H', row)
        count = struct.unpack_from('<H', row, 10)[0]
        if count > 50:
            raise ValueError('battle parameter count exceeds record')
        fields = {}
        for i in range(count):
            name = hero_resources.text(row, 12 + i * 128, 64)
            if name:
                if name in fields:
                    raise ValueError(f'duplicate parameter {key}: {name}')
                fields[name] = hero_resources.text(row, 76 + i * 128, 64)
        result.setdefault(key, []).append(fields)
    return result


def selected(table, key):
    values = table.get(key, [])
    if not values:
        raise ValueError(f'missing battle parameter key {key}')
    if any(value != values[0] for value in values[1:]):
        raise ValueError(f'ambiguous battle parameter key {key}')
    return values[0]


def number(fields, key, *, minimum=0):
    value = fields.get(key)
    if type(value) is not str or not value.isascii() or not value.isdecimal() or not minimum <= int(value) <= 65535:
        raise ValueError(f'missing or invalid action parameter {key}')
    return int(value)


def attackProfile(table, category):
    result = {}
    for intent, direction in DIRECTIONS.items():
        fields = selected(table, (2, 0, category, direction, 2))
        prepare, process, finish = [number(fields, '[持续时间]' + phase, minimum=1)
                                    for phase in ('预备', '过程', '结束')]
        start, end = [number(fields, '[碰撞时间]' + phase) for phase in ('开始', '结束')]
        # The original melee rows use 0/0; retain the existing process-phase contact policy.
        if start == end == 0:
            end = process
        if not 0 <= start < end <= process:
            raise ValueError('contact window outside weapon process phase')
        result[str(intent)] = dict(phases=[prepare, 0, process, finish],
            parried=number(fields, '[持续时间]被招架', minimum=1),
            rebound=number(fields, '[持续时间]被反弹', minimum=1), contactStartMs=start, contactEndMs=end)
    return result


def characterBinding(data, gender, category):
    selectors = {1: gender, 2: category, 3: 2, 4: 1}
    candidates = []
    for row in rows(data, 242):
        count = struct.unpack_from('<H', row, 200)[0]
        if count > 5:
            raise ValueError('character condition count exceeds record')
        conditions = [struct.unpack_from('<II', row, 202 + i * 8) for i in range(count)]
        conditions = [(key, value) for key, value in conditions if key]
        if conditions and all(selectors.get(key) == value for key, value in conditions):
            candidates.append((len(conditions), hero_resources.text(row, 8, 64)))
    if not candidates:
        raise ValueError(f'missing character binding gender={gender} category={category}')
    priority = max(n for n, _ in candidates)
    names = {name for n, name in candidates if n == priority}
    if len(names) != 1:
        raise ValueError('ambiguous character binding')
    return 'data/actors/havok/shgameexport/characters/' + names.pop().lower()


@lru_cache(maxsize=1)
def resourceTables(directory):
    return (parameters(readSelected(directory, 'data1.vfs', PARAM_PATH, lambda data: data)),
            readSelected(directory, 'data1.vfs', CHARACTER_PATH, lambda data: data))


@lru_cache(maxsize=1)
def shieldTimings(directory):
    def parse(data):
        values = {}
        for row in rows(data, 10950):
            state = struct.unpack_from('<I', row)[0]
            if state in (95, 111):
                duration = struct.unpack_from('<i', row, 0x1e3e)[0]
                if state in values or not 0 < duration <= 65535:
                    raise ValueError('invalid shield guard duration')
                values[state] = duration
        if set(values) != {95, 111}:
            raise ValueError('missing shield guard states')
        return dict(prepareMs=values[95], successMs=values[111])
    return readSelected(directory, 'data1.vfs', STATE_PATH, parse)


@lru_cache(maxsize=128)
def movementProfile(directory, gender, category):
    _, characters = resourceTables(directory)
    path = characterBinding(characters, gender, category)
    return readSelected(directory, 'actor.vfs', path, movement_animation.characterSpeeds) | {'characterPath': path}


@lru_cache(maxsize=256)
def weaponProfile(directory, weaponId, gender):
    catalogue = hero_resources.loadCatalogue(directory)
    weapon = catalogue.weapons[weaponId]
    table, _ = resourceTables(directory)
    result = dict(weaponId=weaponId, weaponCategory=weapon.category, hand=weapon.hand,
                  shield=weapon.shield, attacks={}, changeMs=0, movementAnimation={}, dashAttack={})
    if weapon.shield:
        return result
    result['attacks'] = attackProfile(table, weapon.category)
    result['guard'] = shieldTimings(directory)
    fields = dict(selected(table, (1, weapon.family, 0, 0, 0)))
    if (1, 0, weapon.category, 0, 0) in table:
        fields.update(selected(table, (1, 0, weapon.category, 0, 0)))
    result['changeMs'] = number(fields, '[持续时间]换出武器', minimum=1)
    result['movementAnimation'] = movementProfile(directory, gender, weapon.category) | {'weaponId': weaponId}
    if (6, 0, weapon.category, dash_resources.DASH_ACTION_TYPE, 2) in table and weaponId != hero_resources.FIST_ID:
        result['dashAttack'] = dash_resources.loadDashAttack(directory, weaponId).asDict()['dashAttack']
    return result


def loadCombatProfiles(directory, heroId):
    from .combat_profile_data import HERO_WEAPONS, PROFILES
    if heroId not in HERO_WEAPONS:
        raise ValueError(f'hero has no static combat profiles: {heroId}')
    result = {}
    for tid, index in HERO_WEAPONS[heroId].items():
        profile = deepcopy(PROFILES[index]) | {'weaponId': tid}
        # State snapshots use lists; retain the exporter's evidence tuples in the static table.
        for key in ('timingSources', 'windowAdjustments'):
            if key in profile:
                profile[key] = {name: list(values) for name, values in profile[key].items()}
        for key in ('movementAnimation', 'dashAttack'):
            if profile[key]:
                profile[key]['weaponId'] = tid
        if not validProfile(profile):
            raise ValueError(f'invalid static combat profile hero={heroId} weapon={tid}')
        result[tid] = profile
    return result


def defaultProfile():
    return loadCombatProfiles('', hero_resources.DEFAULT_HERO)[1030411]


def nativeProfiles(directory):
    from .combat_profile_data import HERO_WEAPONS
    result = []
    for heroId in HERO_WEAPONS:
        for tid, profile in sorted(loadCombatProfiles(directory, heroId).items()):
            if not validProfile(profile):
                raise ValueError(f'invalid combat profile hero={heroId} weapon={tid}')
            if not profile['shield']:
                dash = profile['dashAttack'] or vars(dash_resources.DashAttackProfile(weaponId=tid))
                result.append(dict(heroId=heroId, movementAnimation=profile['movementAnimation'], dashAttack=dash))
    return result


def clearCache():
    for loader in (resourceTables, shieldTimings, movementProfile, weaponProfile):
        loader.cache_clear()
