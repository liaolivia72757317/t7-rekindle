"""Synthetic resource bundle inputs shared with the Rust loader tests."""
from contextlib import ExitStack
import importlib.util
from pathlib import Path
from unittest.mock import patch

from loadout_fixture import catalogue, combatProfiles, damageProfiles

ROOT = Path(__file__).resolve().parents[2]


def exporter():
    spec = importlib.util.spec_from_file_location('t7r_resource_export', ROOT / 'scripts/export_battle_resources.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def bundle():
    module = exporter()
    spawns = {camp: tuple(((float(i), float(camp * 20), 0.), 0) for i in range(10)) for camp in (1, 2)}
    respawns = {camp: tuple(((float(i), float(camp * 25), 0.), 0)
                           for i in range(14 if camp == 1 else 12)) for camp in (1, 2)}
    with ExitStack() as stack:
        for target, name, value in (
                (module.client_resources, 'checkResources', lambda *args: None),
                (module.hero_resources, 'loadCatalogue', lambda _: catalogue()),
                (module.combat_resources, 'loadCombatProfiles', lambda _, hero: combatProfiles(hero)),
                (module.damage_resources, 'loadProfile', damageProfiles),
                (module.bot_names, 'loadNames', lambda _: tuple(f'Fixture{i}' for i in range(30))),
                (module.map_data, 'POSITION', (0., 0., 0.)),
                (module.map_data, 'ENEMY_POSITION', (1., 0., 0.)),
                (module.map_data, 'MAP_NAME', 'Fixture'),
                (module.spawn_resource, 'loadSpawns', lambda _: spawns),
                (module.spawn_resource, 'loadRespawns', lambda _: respawns),
                (module.map_navigation_data, 'VERTICES', ((-100., -100., 0.), (100., -100., 0.), (100., 100., 0.), (-100., 100., 0.))),
                (module.map_navigation_data, 'EDGES', ((0, 1, -1, -1), (1, 2, -1, -1), (2, 3, -1, -1), (3, 0, -1, -1))),
                (module.map_navigation_data, 'FACES', ((0, 4, 0),))):
            stack.enter_context(patch.object(target, name, value))
        return module.buildBundle(ROOT / 'artifacts/synthetic-client/bin', 3, 7, 'fixture-v1')
