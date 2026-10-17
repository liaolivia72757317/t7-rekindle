"""Small public synthetic vanguard tables, without original resource contents."""
import struct

from Business.scripts.hero_resources import Catalogue, Hero, Weapon
from types import MappingProxyType


def table(width, records):
    header = bytearray(140)
    struct.pack_into('<4sIII', header, 0, b'MSES', 7, width, len(records))
    header[64:68] = b'GBK\0'
    struct.pack_into('<I', header, 132, 140)
    return bytes(header) + b''.join(records)


def catalogue():
    empty = (0,) * 6
    groups = ((701, 0, 0, 0, 0, 0), (702, 0, 0, 0, 0, 0), (703, 0, 0, 0, 0, 0),
              empty, empty, empty, empty, empty)
    hero = lambda tid, gender: Hero(tid, '先锋' + str(tid), gender,
        groups, ((0, 0), (2, 0)), (901, 902, 903, 0, 0, 0, 0, 0), groups)
    return Catalogue(MappingProxyType({1101: hero(1101, 1), 2102: hero(2102, 2)}), MappingProxyType({
        701: Weapon(701, 2, 1, 2, '单手剑'), 702: Weapon(702, 32, 2, 3, '双手刀'),
        703: Weapon(703, 17, 12, 1, '盾'), 100000: Weapon(100000, 104, 1, 3, '空手')}))


def combatProfiles(heroId):
    cat = catalogue()
    result = {}
    for tid, weapon in cat.weapons.items():
        profile = dict(weaponId=tid, weaponCategory=weapon.category, hand=weapon.hand, shield=weapon.shield,
                       changeMs=0, attacks={}, movementAnimation={}, dashAttack={})
        if not weapon.shield:
            prepare = 180 if tid == 701 else 350
            profile.update(changeMs=700 if tid == 701 else 1100, guard=dict(prepareMs=150, successMs=667),
                attacks={str(intent): dict(phases=[prepare, 0, 600, 400], parried=750, rebound=550,
                         contactStartMs=70, contactEndMs=550) for intent in (300560, 300550, 300570, 300540)},
                movementAnimation=dict(enabled=True, weaponId=tid, forwardSlow=2.5 if heroId == 1101 else 2.75,
                                       lateralSlow=1.8, backwardSlow=1., forwardSprint=3.6, characterPath='fixture.hkt'))
        result[tid] = profile
    return result


def damageProfiles(heroId, weaponIds):
    from Business.scripts.damage_resources import weaponProfile
    result = {}
    for tid in weaponIds:
        value = weaponProfile(100000)
        value.update(weaponId=tid, shieldMax=100 if tid == 703 else 0, attackGrade='C', defenceGrade='C', weight=1.)
        result[str(tid)] = value
    return dict(heroId=heroId, armor=0, maxHealth=100, armorType='light', traits=[], weapons=result)


def writeStaticCombatProfiles(path):
    from copy import deepcopy
    profiles = combatProfiles(1101)
    profiles[1030411] = deepcopy(profiles[702])
    templates, references = [], {}
    for tid in (701, 100000, 1030411):
        profile = deepcopy(profiles[tid])
        profile.pop('weaponId')
        for key in ('movementAnimation', 'dashAttack'):
            profile[key].pop('weaponId', None)
        references[tid] = len(templates)
        templates.append(profile)
    path.write_text(f'HERO_WEAPONS = { {1101: references}!r}\nPROFILES = {templates!r}\n', encoding='utf-8')


def writeStaticDamageProfiles(path):
    weapons = {701: (2, 1, 701, (20, 0, 0), 0, (), 'D', 'D'),
               100000: (104, 1, 100000, (0, 0, 0), 0, (), '', ''),
               1030411: (32, 2, 1030411, (20, 0, 0), 0, (), 'C', 'C')}
    actions = {category: {intent: index + 1 for index, intent in enumerate((300560, 300550, 300570, 300540, 700250))}
               for category in (2, 32, 104)}
    path.write_text(f'WEAPONS = {weapons!r}\nHEROES = {{1101: (1101, 0, (), 100)}}\nACTIONS = {actions!r}\n', encoding='utf-8')


def resourceTables():
    hero = bytearray(7286)
    struct.pack_into('<I', hero, 0, 1101)
    hero[4:8] = '先锋'.encode('gbk')
    hero[34] = 1
    hero[163:167] = '先锋'.encode('gbk')
    struct.pack_into('<HIII', hero, 4209, 8, 900, 701, 0)
    npc = bytearray(hero)
    struct.pack_into('<I', npc, 0, 9900)
    unlock = bytearray(300)
    struct.pack_into('<HII', unlock, 36, 1, 0, 1101)
    group = bytearray(1434)
    struct.pack_into('<I', group, 0, 900)
    struct.pack_into('<HI', group, 64, 6, 701)
    weapons, categories = [], []
    for tid, category in ((701, 2), (100000, 104)):
        weapon = bytearray(2829)
        struct.pack_into('<I', weapon, 0, tid)
        struct.pack_into('<H', weapon, 56, category)
        weapons.append(weapon)
        spec = bytearray(550)
        struct.pack_into('<HH', spec, 0, category, 1)
        struct.pack_into('<H', spec, 46, 2)
        categories.append(spec)
    return (table(7286, [hero, npc]), table(300, [unlock]), table(1434, [group]),
            table(2829, weapons), table(550, categories))


def writeResources(root):
    from movement_animation_fixture import character
    from vfs_fixture import archiveBytes, NAME_PATH, nameTable
    from Business.scripts import hero_resources as heroes, combat_resources as combat
    entries = [(NAME_PATH, nameTable([('npc', f'测试电脑{i}') for i in range(30)]), 3)]
    entries += [('../'+path, value, 3) for path, value in zip(
        (heroes.HERO_PATH, heroes.UNLOCK_PATH, heroes.GROUP_PATH, heroes.WEAPON_PATH, heroes.CATEGORY_PATH),
        resourceTables())]
    records = []
    for category in (2, 104):
        for direction in (1, 2, 3, 4):
            row = bytearray(6412)
            fields = {'[持续时间]预备': '180', '[持续时间]过程': '600', '[持续时间]结束': '400',
                      '[持续时间]被招架': '750', '[持续时间]被反弹': '550', '[碰撞时间]开始': '70', '[碰撞时间]结束': '550'}
            struct.pack_into('<6H', row, 0, 2, 0, category, direction, 2, len(fields))
            for i, (name, value) in enumerate(fields.items()):
                struct.pack_into('<64s64s', row, 12+i*128, name.encode('gbk'), value.encode('ascii'))
            records.append(row)
    row = bytearray(6412)
    struct.pack_into('<6H', row, 0, 1, 1, 0, 0, 0, 1)
    struct.pack_into('<64s64s', row, 12, '[持续时间]换出武器'.encode('gbk'), b'700')
    records.append(row)
    entries.append(('../'+combat.PARAM_PATH, table(6412, records), 3))
    binding = bytearray(242)
    struct.pack_into('<64s', binding, 8, b'fixture.hkt')
    struct.pack_into('<HII', binding, 200, 1, 1, 1)
    entries.append(('../'+combat.CHARACTER_PATH, table(242, [binding]), 3))
    states = []
    for state, duration in ((95, 150), (111, 667)):
        row = bytearray(10950)
        struct.pack_into('<I', row, 0, state)
        struct.pack_into('<i', row, 0x1e3e, duration)
        states.append(row)
    entries.append(('../'+combat.STATE_PATH, table(10950, states), 3))
    (root/'vfs/data1.vfs').write_bytes(archiveBytes(entries))
    (root/'vfs/actor.vfs').write_bytes(archiveBytes([
        ('../data/actors/havok/shgameexport/characters/fixture.hkt', character(), 3)]))
