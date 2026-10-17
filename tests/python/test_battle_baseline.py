"""The native embedded baseline must remain identical to current source-owned rules."""
from contextlib import ExitStack
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch

import pytest

from Business.scripts import combat_resources, damage_data, hero_resources

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / 'src/BattleRust/resources/data/battle-baseline-v1.json'


def script(name):
    spec = importlib.util.spec_from_file_location('t7r_' + name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def catalogue():
    heroes, weapons = {}, {}
    for hero_id, weapon_id, gender in ((1101, 1030411, 1), (2102, 1030121, 2)):
        groups = ((weapon_id, 0, 0, 0, 0, 0),) + ((0,) * 6,) * 7
        heroes[hero_id] = hero_resources.Hero(hero_id, 'Fixture' + str(hero_id), gender, groups,
            ((0, 0),), (hero_id * 10, 0, 0, 0, 0, 0, 0, 0), groups)
        profiles = combat_resources.loadCombatProfiles('', hero_id)
        for tid in (weapon_id, hero_resources.FIST_ID):
            profile = profiles[tid]
            weapons[tid] = hero_resources.Weapon(tid, profile['weaponCategory'], damage_data.WEAPONS[tid][1],
                profile['hand'], 'Fixture' + str(tid))
    return hero_resources.Catalogue(MappingProxyType(heroes), MappingProxyType(weapons))


def test_embedded_baseline_matches_current_sources_without_runtime_reads(monkeypatch):
    module = script('export_battle_baseline')

    def unexpected(*args, **kwargs):
        raise AssertionError('baseline export must not read a client installation')

    monkeypatch.setattr(module.combat_resources, 'readSelected', unexpected)
    monkeypatch.setattr(module.damage_resources, 'readSelected', unexpected)
    value = module.buildBaseline()
    assert module.encodeBaseline(value) == BASELINE.read_bytes()
    assert set(value) == {'combatProfiles', 'damageProfiles', 'rules', 'map'}
    assert len(value['combatProfiles']) == 36
    assert sum(map(len, value['combatProfiles'].values())) == 286
    assert len(BASELINE.read_bytes()) < 2 * 1024 * 1024
    assert str(ROOT).encode() not in BASELINE.read_bytes()


def test_baseline_selection_matches_existing_bundle_exporter():
    baseline = script('export_battle_baseline').buildBaseline()
    module = script('export_battle_resources')
    selected = catalogue()
    names = tuple(f'Fixture{i}' for i in range(30))
    with ExitStack() as stack:
        for target, name, value in (
                (module.client_resources, 'checkResources', lambda *args: None),
                (module.hero_resources, 'loadCatalogue', lambda _: selected),
                (module.bot_names, 'loadNames', lambda _: names)):
            stack.enter_context(patch.object(target, name, value))
        bundle = module.buildBundle(ROOT / 'artifacts/synthetic-client/Bin', 3, 7, 'fixture-v1')
    for field in ('map', 'rules'):
        assert bundle[field] == baseline[field]
    for hero_id, hero in selected.heroes.items():
        key = str(hero_id)
        owned = {str(hero_resources.FIST_ID), *(str(tid) for group in hero.groups for tid in group if tid)}
        assert bundle['combatProfiles'][key] == {tid: baseline['combatProfiles'][key][tid] for tid in owned}
        damage = deepcopy(baseline['damageProfiles'][key])
        damage['weapons'] = {tid: damage['weapons'][tid] for tid in owned}
        assert bundle['damageProfiles'][key] == damage


def test_export_is_deterministic_and_never_overwrites(tmp_path):
    module = script('export_battle_baseline')
    value = module.buildBaseline()
    before = deepcopy(value)
    path = tmp_path / 'baseline.json'
    module.writeBaseline(path, value)
    assert path.read_bytes() == BASELINE.read_bytes()
    assert module.encodeBaseline(value) == module.encodeBaseline(module.buildBaseline())
    with pytest.raises(FileExistsError):
        module.writeBaseline(path, value)
    assert value == before


def test_export_rejects_nonfinite_values_before_creating_output(tmp_path):
    module = script('export_battle_baseline')
    value = module.buildBaseline()
    value['map']['position'][0] = float('nan')
    path = tmp_path / 'baseline.json'
    with pytest.raises(ValueError):
        module.writeBaseline(path, value)
    assert not path.exists()


def test_frozen_data_is_plain_portable_json():
    raw = BASELINE.read_bytes()
    assert not raw.startswith(b'\xef\xbb\xbf')
    assert raw.endswith(b'\n')
    assert json.loads(raw)['map']['resourceId'] == 1028
