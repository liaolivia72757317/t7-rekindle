import struct

import pytest

from Business.scripts import battle, damage_resources as damage
from Business.scripts.combat_profile_data import HERO_WEAPONS
from Business.scripts.damage_data import HEROES, WEAPONS


def test_offline_damage_export_selects_dash_instead_of_war_skill(monkeypatch):
    from loadout_fixture import catalogue
    weapon, attr, hero, heroAttr = bytearray(2829), bytearray(554), bytearray(7286), bytearray(420)
    struct.pack_into('<I', weapon, 4, 701)
    struct.pack_into('<3i', attr, 0x18e, 20, 10, 5)
    for index, amount in enumerate((20, 10, 5)):
        struct.pack_into('<3hf', attr, 46 + index * 10, 1, 10 + index, 1, amount)
    struct.pack_into('<I', hero, 0x1069, 1101)
    struct.pack_into('<i', heroAttr, 0x2c, 137)
    struct.pack_into('<i', heroAttr, 0x48, 63)
    tables = {'s_weapon_cli': {701: weapon}, 's_weapon_attr': {701: attr},
              's_heroscard_cli': {1101: hero}, 's_hero_attr': {1101: heroAttr},
              's_res_buff_cli': {}, 's_res_feature_cli': {}}
    parameters = {(2, 0, 2, direction, 2): [{'伤害ID': str(100 + direction)}] for direction in (1, 2, 3, 4)}
    parameters.update({(6, 0, 2, 13, 2): [{'伤害ID': '215'}], (6, 0, 2, 32, 2): [{'伤害ID': '999'}]})
    monkeypatch.setattr(damage.hero_resources, 'loadCatalogue', lambda _: catalogue())
    monkeypatch.setattr(damage, 'clientTable', lambda directory, name, width: tables[name])
    monkeypatch.setattr(damage.combat_resources, 'resourceTables', lambda _: (parameters, b''))
    _, heroes, actions = damage.extract('fixture', {1101: {701: 0}})
    assert heroes[1101] == (1101, 63, (), 137)
    assert actions[2][700250] == 215


def test_static_export_covers_current_roster_without_reading_client(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError('runtime damage must not read client resources')
    monkeypatch.setattr(damage, 'readSelected', unexpected)
    assert len(HEROES) == len(HERO_WEAPONS) == 36
    assert len(WEAPONS) == 120  # 119 loadout items plus fists.
    count = 0
    for heroId, weapons in HERO_WEAPONS.items():
        profile = damage.loadProfile(heroId, weapons)
        assert set(profile['weapons']) == {str(tid) for tid in weapons}
        assert profile['armorSource'] == 'client'
        for item in profile['weapons'].values():
            if item['shieldMax']:
                assert item['shieldMax'] > 0 and item['attacks'] == {}
            else:
                count += 1
                assert set(map(str, battle.ATTACK_CHAINS)) <= set(item['attacks'])
                assert all(value['base'] > 0 and value['damageId'] > 0 for value in item['attacks'].values())
    assert count == 246


def test_original_values_and_project_rules_are_distinguished():
    sword = damage.weaponProfile(1030411)
    assert sword['attacks']['300560'] == dict(base=46, damageType='slash', damageId=103040)
    assert sword['attacks']['300570'] == dict(base=23, damageType='pierce', damageId=103042)
    assert sword['attacks']['700250']['base'] == 46
    assert sword['cleave'] == 'medium'
    assert sword['baseSource'] == 'client' and sword['typeSource'] == 'project'
    fist = damage.weaponProfile(100000)
    assert fist['baseSource'] == 'project'
    assert all(value['base'] == 20 and value['damageType'] == 'blunt' for value in fist['attacks'].values())
    assert damage.loadProfile(1101, (100000, 1030411))['armor'] == 63
    assert damage.weaponProfile(1090111)['shieldMax'] == 125


def test_hero_health_uses_original_base_attribute():
    for heroId, expected in ((1001, 99), (1101, 110)):
        profile = damage.loadProfile(heroId, (100000,))
        assert profile['maxHealth'] == expected
        assert profile['healthSource'] == 'client'


@pytest.mark.parametrize('weaponId,damageId', [(1030411, 103039), (1040321, 104038), (1040322, 104038)])
def test_dash_damage_identifier_comes_from_action_type_13(weaponId, damageId):
    assert damage.weaponProfile(weaponId)['attacks']['700250']['damageId'] == damageId


def test_bound_traits_are_not_applied_globally():
    assert damage.loadProfile(1101, (100000, 1030411))['traits'] == ['light', 'combo']
    assert 'counter' in damage.loadProfile(1004, (100000,))['traits']
    assert 'riposte' not in damage.loadProfile(1004, (100000,))['traits']
    with pytest.raises(ValueError):
        damage.loadProfile(999, (100000,))
    with pytest.raises(ValueError):
        damage.loadProfile(1101, (999,))


def test_original_performance_letters_and_explicit_project_fallbacks():
    for tid, attack, defence in ((1010911, 'E', 'F'), (1030411, 'C', 'C'),
                                 (1040511, 'C', 'D'), (1090311, 'F', 'A')):
        item = damage.weaponProfile(tid)
        assert (item['attackGrade'], item['defenceGrade']) == (attack, defence)
        assert item['performanceSource'] == 'client'
        assert item['weight'] > 0 and item['weightSource'] == 'project'
    assert damage.weaponProfile(100000)['performanceSource'] == 'project'
    assert damage.loadProfile(1101, (100000,))['armorTypeSource'] == 'project'
