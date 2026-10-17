"""Resource facts must not silently become ordinary-guard geometry mappings."""
from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path
import runpy
import struct
import subprocess
import sys

import pytest

from loadout_fixture import catalogue, table, writeResources

ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def inspector():
    return runpy.run_path(str(ROOT / 'scripts/inspect_guard_geometry.py'))


def weapon(tid, category, children=()):
    row = bytearray(2829)
    struct.pack_into('<I', row, 0, tid)
    struct.pack_into('<H', row, 56, category)
    struct.pack_into('<60s', row, 330, b'fixture.entity')
    struct.pack_into('<60s', row, 930, b'FixtureRoot')
    struct.pack_into('<H', row, 0x3E2, len(children))
    for index, child in enumerate(children):
        struct.pack_into('<I', row, 0x3E4 + index * 4, child)
    struct.pack_into('<9f', row, 0x3F8, 0, 0, 1, 0, 0, 0, .1, .2, .3)
    return row


def category(tid, layer, body='AttackRB', family=1):
    row = bytearray(550)
    struct.pack_into('<HH', row, 0, tid, family)
    struct.pack_into('<H', row, 46, 2)
    struct.pack_into('<I', row, 0x34, layer)
    struct.pack_into('<64s64s', row, 0x78, body.encode('ascii'), b'Root')
    return row


def inputs():
    return [weapon(701, 2, (0, 880)), weapon(702, 32), weapon(703, 17),
            weapon(100000, 104), weapon(801, 20), weapon(802, 20),
            weapon(880, 21), weapon(901, 2, (802,))], [
        category(2, 3), category(32, 3), category(17, 6, 'DefenseRB', 12),
        category(104, 3), category(20, 4, 'DefenseRB'), category(21, 7, 'DefenseRB')]


def inspect(weapons=None, categories=None, include=()):
    defaultWeapons, defaultCategories = inputs()
    return inspector()['buildInventory'](
        table(2829, defaultWeapons if weapons is None else weapons),
        table(550, defaultCategories if categories is None else categories), catalogue(), include)


def test_inventory_preserves_missing_binding_instead_of_assigning_shared_proxy():
    report = inspect()
    rows = {row['weaponId']: row for row in report['weapons']}
    assert report['resourceFactsOnly'] is True
    assert report['selectedWeaponIds'] == [701, 702, 703, 100000]
    assert report['candidateResourceIds'] == [801, 802]
    assert 901 not in rows
    assert rows[701]['associatedWeaponIds'] == [0, 880]
    assert rows[702]['associatedWeaponIds'] == []
    assert all(row['ordinaryGuardSource'] is None for row in rows.values())
    assert rows[703]['rigidBodyName'] == 'DefenseRB'
    assert rows[703]['layerGate'] == 'passes'
    assert rows[801]['layerGate'] == 'skipped'
    assert rows[880]['layerGate'] == 'skipped'
    assert rows[801]['bodyParameters']['sizeRaw'] == pytest.approx([.1, .2, .3])
    assert report['candidateReferences'] == [
        {'resourceId': 801, 'weaponIds': []}, {'resourceId': 802, 'weaponIds': [901]}]


def test_explicit_control_and_child_closure_are_deterministic_and_not_proof():
    weapons, categories = inputs()
    before = deepcopy(weapons)
    report = inspect(weapons, categories, (901,))
    assert report == inspect(list(reversed(weapons)), list(reversed(categories)), (901, 901))
    control = next(row for row in report['weapons'] if row['weaponId'] == 901)
    assert control['associatedWeaponIds'] == [802]
    assert control['ordinaryGuardSource'] is None
    assert weapons == before


def test_missing_children_and_cycles_remain_explicit_resource_facts():
    weapons, categories = inputs()
    weapons[0] = weapon(701, 2, (880, 999))
    weapons[6] = weapon(880, 21, (701,))
    report = inspect(weapons, categories)
    assert report['missingAssociatedWeaponIds'] == [999]
    assert len({row['weaponId'] for row in report['weapons']}) == len(report['weapons'])
    assert all(row['ordinaryGuardSource'] is None for row in report['weapons'])


@pytest.mark.parametrize('mutation,match', [
    ('count', 'associated weapon count'), ('duplicate', 'duplicate resource key'),
    ('nan', 'nonfinite'), ('string', 'unterminated'), ('category', 'missing category')])
def test_malformed_selected_resource_fails_explicitly(mutation, match):
    weapons, categories = inputs()
    if mutation == 'count':
        struct.pack_into('<H', weapons[0], 0x3E2, 6)
    elif mutation == 'duplicate':
        weapons.append(weapons[0])
    elif mutation == 'nan':
        struct.pack_into('<f', weapons[0], 0x410, float('nan'))
    elif mutation == 'string':
        weapons[0][930:990] = b'x' * 60
    else:
        struct.pack_into('<H', weapons[0], 56, 999)
    with pytest.raises(ValueError, match=match):
        inspect(weapons, categories)


def test_unknown_control_is_not_silently_omitted():
    with pytest.raises(ValueError, match='missing requested weapon'):
        inspect(include=(999,))


@pytest.mark.parametrize('offset,value', [(4, 8), (8, 2830), (132, 0)])
def test_unsupported_table_layout_is_rejected(offset, value):
    weapons, categories = inputs()
    data = bytearray(table(2829, weapons))
    struct.pack_into('<I', data, offset, value)
    with pytest.raises(ValueError, match='table layout'):
        inspector()['buildInventory'](bytes(data), table(550, categories), catalogue())


def test_report_does_not_overwrite_or_include_machine_paths(tmp_path):
    report = inspect()
    path = tmp_path / 'inventory.json'
    inspector()['writeInventory'](path, report)
    before = path.read_bytes()
    assert json.loads(before) == report
    assert str(ROOT).encode() not in before
    with pytest.raises(FileExistsError):
        inspector()['writeInventory'](path, report)
    assert path.read_bytes() == before


def test_template_keeps_header_and_entity_bindings_separate():
    data = '''<?xml version="1.0" encoding="gb2312"?>
    <GeEntitySheet><Header><GeWeapon><BTree Value="shared.btree"/></GeWeapon>
    <GeAttachable><FollowScale Value="true"/></GeAttachable></Header>
    <Entity Name="示例"><GeWeapon><BTree Value="*.*"/></GeWeapon>
    <GeAttachable><FollowScale Value="false"/></GeAttachable></Entity></GeEntitySheet>'''.encode('gbk')
    result = inspector()['templateBindings'](data, '示例')
    assert result['header']['GeWeapon']['BTree'] == 'shared.btree'
    assert result['entity']['GeWeapon']['BTree'] == '*.*'
    assert result['header']['GeAttachable']['FollowScale'] == 'true'
    assert result['entity']['GeAttachable']['FollowScale'] == 'false'
    with pytest.raises(ValueError, match='entity binding'):
        inspector()['templateBindings'](data, 'missing')


def test_cli_reads_selected_vfs_and_keeps_client_files_unchanged(tmp_path):
    client = tmp_path / 'client'
    (client / 'vfs').mkdir(parents=True)
    writeResources(client)
    (client / 'Bin').mkdir()
    archive = client / 'vfs/data1.vfs'
    before = archive.read_bytes()
    output = tmp_path / 'inventory.json'
    command = [sys.executable, str(ROOT / 'scripts/inspect_guard_geometry.py'),
               '--client-directory', str(client / 'Bin'), '--output', str(output)]
    result = subprocess.run(command, text=True, capture_output=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text(encoding='utf-8'))['selectedWeaponIds'] == [701, 100000]
    assert archive.read_bytes() == before
    command[-1] = str(client / 'unexpected.json')
    result = subprocess.run(command, text=True, capture_output=True, encoding='utf-8')
    assert result.returncode != 0
    assert not (client / 'unexpected.json').exists()
    command[-1] = str(tmp_path / 'overlay.json')
    (client / 'data/table').mkdir(parents=True)
    (client / 'data/table/s_weapon_cli.bin').write_bytes(b'loose override')
    result = subprocess.run(command, text=True, capture_output=True, encoding='utf-8')
    assert result.returncode != 0
    assert 'unsupported_overlay' in result.stderr
    assert not (tmp_path / 'overlay.json').exists()
