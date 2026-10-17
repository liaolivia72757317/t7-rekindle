from copy import deepcopy
import struct

import pytest

from Business.runtime import host_runtime
from Business.scripts import client_resources, combat_resources as combat, hero_resources, vfs_resource
from loadout_fixture import table, combatProfiles, writeResources
from vfs_fixture import writeClient


@pytest.fixture
def client(tmp_path):
    directory, _ = writeClient(tmp_path/'client')
    writeResources(directory.parent)
    yield directory
    combat.clearCache()
    hero_resources.loadCatalogue.cache_clear()
    vfs_resource.clearCache()


def test_offline_table_reader_retains_fallback_actions_movement_and_guard(client):
    profiles = {tid: combat.weaponProfile(str(client), tid, 1) for tid in (701, 100000)}
    assert all(combat.validProfile(p) for p in profiles.values())
    assert profiles[701]['attacks']['300540']['phases'] == [180, 0, 600, 400]
    assert profiles[701]['changeMs'] == 700 and profiles[701]['guard'] == dict(prepareMs=150, successMs=667)


@pytest.mark.parametrize('actionType,enabled', [(13, True), (32, False)])
def test_offline_dash_availability_uses_native_action_type(client, monkeypatch, actionType, enabled):
    from Business.scripts.dash_resources import DashAttackProfile, DashResources
    table, characters = combat.resourceTables(str(client))
    monkeypatch.setattr(combat, 'resourceTables', lambda _: (table | {(6, 0, 2, actionType, 2): [{}]}, characters))
    calls = []

    def load(directory, weaponId):
        calls.append(weaponId)
        return DashResources(DashAttackProfile(enabled=True, weaponId=weaponId, weaponCategory=2,
            durationMs=1200, moveMs=500, speedMilli=4000, contactStartMs=540,
            contactEndMs=720, reboundMs=933, durationSource='table'), ())

    monkeypatch.setattr(combat.dash_resources, 'loadDashAttack', load)
    result = combat.weaponProfile(str(client), 701, 1)
    assert bool(result['dashAttack']) == enabled
    assert calls == ([701] if enabled else [])


@pytest.mark.parametrize('weaponId', [1040321, 1040322])
def test_pan_feng_long_blades_have_native_dash_parameters(weaponId):
    profile = combat.loadCombatProfiles('', 2104)[weaponId]
    dash = profile['dashAttack']
    assert dash['enabled'] and dash['weaponId'] == weaponId
    assert (dash['durationMs'], dash['moveMs'], dash['speedMilli']) == (1233, 1133, 3500)
    assert (dash['contactStartMs'], dash['contactEndMs'], dash['reboundMs']) == (383, 783, 933)
    assert dash['animationPath'].endswith('m01b_common_dashattack_pa_yyd_animation.hkt')
    assert profile['timingSources']['DashAttack'] == [1233, 1233, '']


def test_runtime_combat_preflight_does_not_need_client_action_or_hero_tables(client):
    writeClient(client.parent)
    result = client_resources.checkResources(str(client))
    assert len(result['combatProfiles']) == 246
    assert result['dashAttack']['durationMs'] == 1333


def test_all_static_hero_weapon_profiles_are_valid_isolated_and_use_no_client_reads(monkeypatch):
    from Business.scripts.combat_profile_data import HERO_WEAPONS
    def unexpected(*_):
        raise AssertionError('runtime tried to read client combat resources')
    monkeypatch.setattr(combat, 'readSelected', unexpected)
    monkeypatch.setattr(hero_resources, 'loadCatalogue', unexpected)
    assert len(HERO_WEAPONS) == 36
    assert len(combat.nativeProfiles('no-client')) == 246
    for heroId, weapons in HERO_WEAPONS.items():
        profiles = combat.loadCombatProfiles('no-client', heroId)
        assert set(profiles) == set(weapons)
        assert all(combat.validProfile(profile) for profile in profiles.values())
        for profile in profiles.values():
            if profile['shield']:
                continue
            assert profile['hurtMs'] == 550
            for attack in profile['attacks'].values():
                assert attack['phases'][1] == 0
            for tableMs, animationMs, reason in profile['timingSources'].values():
                assert tableMs > 0
                assert (animationMs is not None and not reason) or (animationMs is None and reason)
    first = combat.loadCombatProfiles('', 1101)
    first[1030411]['attacks']['300560']['phases'][0] = 999
    assert combat.loadCombatProfiles('', 1101)[1030411]['attacks']['300560']['phases'] == [300, 0, 600, 533]


def test_static_animation_timings_preserve_crops_and_gender_bindings():
    male = combat.loadCombatProfiles('', 1101)
    assert male[100000]['attacks']['300560']['parried'] == 200
    assert male[1030411]['dashAttack']['reboundMs'] == 800
    assert male[1030411]['dashAttack']['durationSource'] == 'animation'
    female = combat.loadCombatProfiles('', 2102)
    assert female[1030121]['attacks']['300560']['phases'] == [333, 0, 867, 567]


def test_static_combat_profiles_round_trip_through_runtime_state():
    from Business.scripts.combat_profile_data import HERO_WEAPONS, PROFILES
    original = deepcopy(PROFILES)
    for heroId, weapons in HERO_WEAPONS.items():
        profiles = combat.loadCombatProfiles('', heroId)
        for weaponId, index in weapons.items():
            profile = profiles[weaponId]
            assert host_runtime.decode(host_runtime.encode(profile)) == profile
            for key in ('timingSources', 'windowAdjustments'):
                if key not in original[index]:
                    assert key not in profile
                    continue
                assert profile[key] == {name: list(values) for name, values in original[index][key].items()}
                for values in profile[key].values():
                    values[0] = -1
    assert PROFILES == original


def test_unknown_static_hero_is_an_explicit_error():
    with pytest.raises(ValueError, match='no static combat profiles'):
        combat.loadCombatProfiles('', -1)


def test_binding_prefers_gender_weapon_specific_row_and_rejects_ties():
    records = []
    for conditions, path in [([(1, 1)], b'generic.hkt'), ([(1, 1), (2, 32)], b'male.hkt'),
                             ([(1, 2), (2, 32)], b'female.hkt')]:
        row = bytearray(242)
        struct.pack_into('<64s', row, 8, path)
        struct.pack_into('<H', row, 200, len(conditions))
        for i, pair in enumerate(conditions):
            struct.pack_into('<II', row, 202+i*8, *pair)
        records.append(row)
    assert combat.characterBinding(table(242, records), 1, 32).endswith('/male.hkt')
    assert combat.characterBinding(table(242, records), 2, 32).endswith('/female.hkt')
    assert combat.characterBinding(table(242, records), 1, 1).endswith('/generic.hkt')
    conflicting = bytearray(records[1])
    struct.pack_into('<64s', conflicting, 8, b'conflict.hkt')
    with pytest.raises(ValueError, match='ambiguous'):
        combat.characterBinding(table(242, [*records, conflicting]), 1, 32)


@pytest.mark.parametrize('mutation', [
    lambda p: p.update(weaponId=True), lambda p: p.update(hand=9),
    lambda p: p['attacks']['300560'].update(phases=[1, 0, 0, 1]),
    lambda p: p['attacks']['300560'].update(contactEndMs=700),
    lambda p: p['movementAnimation'].update(forwardSlow=float('nan')),
    lambda p: p['guard'].update(prepareMs=0),
])
def test_invalid_action_snapshots_are_rejected(mutation):
    profile = deepcopy(combatProfiles(1101)[701])
    assert combat.validProfile(profile)
    mutation(profile)
    assert not combat.validProfile(profile)
