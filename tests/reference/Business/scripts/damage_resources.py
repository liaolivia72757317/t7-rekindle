"""Static damage profiles; client tables are read only by the offline exporter."""
import struct

from . import combat_resources, hero_resources
from .damage_rules import DAMAGE_TYPES
from .guard_rules import GRADES
from .dash_resources import DASH_ACTION_TYPE, readSelected

HERO_TRAITS = {10010051: 'light', 10010061: 'riposte', 10010071: 'counter',
               10010091: 'charge_mastery', 10050001: 'combo', 10050011: 'desperate', 10050021: 'rage'}
WEAPON_TRAITS = {100012: 'shield_break', 100121: 'assassin_low',
                 100122: 'assassin_medium', 100123: 'assassin_high',
                 100161: 'heavy_low', 100162: 'heavy_medium', 100163: 'heavy_high', 100164: 'heavy_special'}
CLEAVE_BUFFS = {100131: 'low', 100132: 'medium', 100133: 'high'}
# Relative project weights based on the bound client movement-weight classes, not kilograms.
WEIGHT_CLASSES = {10045: .5, 10047: 1., 10048: 1.5, 10049: 2., 10050: 2.5, 10055: 2.5}
FIST_BASE = 20
CHARGE_PEAK_MS = 600


def clientTable(directory, name, width):
    return readSelected(directory, 'data1.vfs', 'data/table/' + name + '.bin',
                        lambda data: hero_resources.uniqueRows(data, width))


def array(row, offset, capacity):
    count = struct.unpack_from('<H', row, offset)[0]
    if count > capacity:
        raise ValueError('damage resource reference count exceeds capacity')
    return tuple(value for value in struct.unpack_from('<' + 'I' * count, row, offset + 2) if value)


def extract(directory, heroWeapons):
    """Offsets are from the client's AS table getters, not byte-pattern guesses."""
    catalogue = hero_resources.loadCatalogue(directory)
    weapons = clientTable(directory, 's_weapon_cli', 2829)
    weaponAttrs = clientTable(directory, 's_weapon_attr', 554)
    heroes = clientTable(directory, 's_heroscard_cli', 7286)
    heroAttrs = clientTable(directory, 's_hero_attr', 420)
    buffs = clientTable(directory, 's_res_buff_cli', 832)
    features = clientTable(directory, 's_res_feature_cli', 290)
    parameters = combat_resources.resourceTables(directory)[0]
    weaponRows, heroRows, actions = {}, {}, {}
    for tid in sorted({tid for entries in heroWeapons.values() for tid in entries}):
        weapon, row = catalogue.weapons[tid], weapons[tid]
        attrId = struct.unpack_from('<I', row, 4)[0]
        attr = weaponAttrs[attrId]
        base = struct.unpack_from('<3i', attr, 0x18e)
        shield = struct.unpack_from('<i', attr, 0x1be)[0]
        bindings = array(row, 0x49a, 8)
        if any(value < 0 for value in (*base, shield)) or any(key not in buffs for key in bindings):
            raise ValueError(f'invalid weapon damage attributes: {tid}')
        # Verify the displayed melee numbers against the actual attribute operations.
        operations = {index: value for op, index, change, value in
                      (struct.unpack_from('<3hf', attr, 46 + i * 10) for i in range(32))
                      if op == 1 and change == 1 and index in (10, 11, 12)}
        if not weapon.shield and any(operations.get(10 + i) != value for i, value in enumerate(base)):
            raise ValueError(f'display and attribute damage disagree: {tid}')
        if weapon.shield and shield <= 0:
            raise ValueError(f'shield has no original durability: {tid}')
        if not weapon.shield and not any(base) and tid != hero_resources.FIST_ID:
            raise ValueError(f'weapon has no original melee damage: {tid}')
        grades = tuple(bytes(attr[offset:offset + 20]).split(b'\0')[0].decode('ascii') for offset in (0x1f6, 0x20a))
        if any(value not in GRADES and value != '' for value in grades):
            raise ValueError(f'invalid weapon performance grade: {tid}')
        weaponRows[tid] = (weapon.category, weapon.family, attrId, base, shield, bindings, *grades)
        if weapon.shield:
            continue
        actionIds = {}
        for intent, direction in combat_resources.DIRECTIONS.items():
            fields = combat_resources.selected(parameters, (2, 0, weapon.category, direction, 2))
            actionIds[intent] = int(fields['伤害ID'])
        key = (6, 0, weapon.category, DASH_ACTION_TYPE, 2)
        if key in parameters and tid != hero_resources.FIST_ID:
            actionIds[700250] = int(combat_resources.selected(parameters, key)['伤害ID'])
        if any(value <= 0 for value in actionIds.values()):
            raise ValueError('invalid original action damage identifier')
        if weapon.category in actions and actions[weapon.category] != actionIds:
            raise ValueError('ambiguous category damage identifiers')
        actions[weapon.category] = actionIds
    for heroId in sorted(heroWeapons):
        row = heroes[heroId]
        attrId = struct.unpack_from('<I', row, 0x1069)[0]
        armor = struct.unpack_from('<i', heroAttrs[attrId], 0x48)[0]
        maxHealth = struct.unpack_from('<i', heroAttrs[attrId], 0x2c)[0]
        bindings = array(row, 0x18a7, 5)
        if armor < 0 or maxHealth <= 0 or any(key not in features for key in bindings):
            raise ValueError(f'invalid hero damage attributes: {heroId}')
        heroRows[heroId] = (attrId, armor, bindings, maxHealth)
    return weaponRows, heroRows, actions


def actionType(base, intent, *, fist=False):
    """Project mapping: down thrust prefers pierce; swings/dash prefer slash, then blunt."""
    if fist:
        return 'blunt'
    preferences = (1, 2, 0) if intent == 300570 else (0, 2, 1)
    return next(DAMAGE_TYPES[index] for index in preferences if base[index] > 0)


def weaponProfile(weaponId):
    from .damage_data import WEAPONS, ACTIONS
    category, family, attrId, base, shield, bindings, attackGrade, defenceGrade = WEAPONS[weaponId]
    attacks = {}
    if not shield:
        for intent, damageId in ACTIONS[category].items():
            kind = actionType(base, intent, fist=weaponId == hero_resources.FIST_ID)
            amount = FIST_BASE if weaponId == hero_resources.FIST_ID else base[DAMAGE_TYPES.index(kind)]
            attacks[str(intent)] = dict(base=amount, damageType=kind, damageId=damageId)
    return dict(weaponId=weaponId, attrId=attrId, baseSource='project' if weaponId == hero_resources.FIST_ID else 'client',
                typeSource='project', peakSource='project', peakMs=CHARGE_PEAK_MS,
                attackGrade=attackGrade or 'F', defenceGrade=defenceGrade or 'F',
                performanceSource='client' if attackGrade or defenceGrade else 'project',
                weight=max((WEIGHT_CLASSES[key] for key in bindings if key in WEIGHT_CLASSES), default=1.),
                weightSource='project',
                longPole=family == 3, attacks=attacks, shieldMax=shield,
                cleave=next((CLEAVE_BUFFS[key] for key in bindings if key in CLEAVE_BUFFS), 'none'),
                traits=[WEAPON_TRAITS[key] for key in bindings if key in WEAPON_TRAITS], bindings=list(bindings))


def loadProfile(heroId, weaponIds):
    from .damage_data import HEROES
    from .combat_profile_data import HERO_WEAPONS
    if heroId not in HEROES or any(tid not in HERO_WEAPONS[heroId] for tid in weaponIds):
        raise ValueError('missing static hero/weapon damage profile')
    attrId, armor, bindings, maxHealth = HEROES[heroId]
    return dict(heroId=heroId, attrId=attrId, armor=armor, armorSource='client',
                maxHealth=maxHealth, healthSource='client',
                armorType='light' if armor <= 55 else 'medium' if armor <= 75 else 'heavy', armorTypeSource='project',
                traits=[HERO_TRAITS[key] for key in bindings if key in HERO_TRAITS], bindings=list(bindings),
                weapons={str(tid): weaponProfile(tid) for tid in sorted(weaponIds)})
