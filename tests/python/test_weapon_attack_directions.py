"""Native input columns, not animation availability, restrict melee directions."""
import importlib.util
from pathlib import Path
import struct

import pytest

from Business.scripts import combat_resources
from loadout_fixture import table

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('combat_export', ROOT / 'scripts/export_combat_profiles.py')
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


def weapon(resource_id, column):
    row = bytearray(2829)
    struct.pack_into('<I', row, 0, resource_id)
    struct.pack_into('<H', row, 0x808, column)
    return row


def key_row(condition, messages):
    row = bytearray(318)
    struct.pack_into('<IH8I', row, 0, 7, 1, condition, 0, 0, 0, 0, 0, 0, 0)
    for column, message in messages.items():
        struct.pack_into('<II', row, 38 + 8 * (column - 1), message, 1)
    return row


def test_native_input_columns_exclude_mounted_only_attacks():
    weapons = table(2829, [weapon(701, 8), weapon(702, 12), weapon(703, 14)])
    keys = table(318, [
        key_row(12, {8: 300570, 12: 300560, 14: 300570}),
        key_row(12, {12: 300540, 14: 300540}),
        key_row(12, {12: 300550, 14: 300550}),
        key_row(12, {14: 300560}),
        key_row(11, {8: 300560, 12: 300570}),
        key_row(12, {8: 700250}),
    ])
    assert exporter.attackDirections(weapons, keys) == {
        701: {300570}, 702: {300540, 300550, 300560},
        703: {300540, 300550, 300560, 300570},
    }


@pytest.mark.parametrize('column', [0, 36])
def test_unmapped_weapon_has_no_invented_directions(column):
    assert exporter.attackDirections(table(2829, [weapon(701, column)]),
        table(318, [key_row(12, {1: 300560})]))[701] == set()


@pytest.mark.parametrize('weapon_id,expected', [
    (1040511, {300570}), (1040531, {300570}), (1041521, {300570}),
    (1041131, {300540, 300550, 300560}), (1040931, {300540, 300550, 300560}),
    (1040731, {300540, 300550, 300560, 300570}),
    (1041433, {300540, 300550, 300560, 300570}),
    (1030411, {300540, 300550, 300560, 300570}),
])
def test_frozen_profiles_retain_native_weapon_restrictions(weapon_id, expected):
    from Business.scripts.combat_profile_data import HERO_WEAPONS
    found = False
    for hero_id, weapons in HERO_WEAPONS.items():
        if weapon_id not in weapons:
            continue
        found = True
        profile = combat_resources.loadCombatProfiles('', hero_id)[weapon_id]
        assert {int(message) for message, attack in profile['attacks'].items()
                if attack['enabled']} == expected
    assert found
