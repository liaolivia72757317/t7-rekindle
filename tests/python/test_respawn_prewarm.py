from functools import lru_cache

import pytest

from Business.scripts import client_resources, spawn_navigation
from Business.scripts.navigation import NavMesh


def test_resource_preflight_prewarms_exact_runtime_keys_for_both_camps(tmp_path, monkeypatch):
    navs = [NavMesh.fromPolygons([[(0, 0, 0), (20, 0, 0), (20, 20, 0), (0, 20, 0)]])
            for _ in range(2)]
    current = [navs[0]]
    spawns = {1: (((2., 2., .2), 0),), 2: (((18., 18., .2), 180),)}
    respawns = {1: (((-1., 5., .2), 0),), 2: (((21., 15., .2), 180),)}
    monkeypatch.setattr(client_resources, 'loadSpawns', lru_cache()(lambda _: spawns))
    monkeypatch.setattr(client_resources, 'loadRespawns', lru_cache()(lambda _: respawns))
    monkeypatch.setattr(client_resources, 'loadNavigation', lru_cache()(lambda _: current[0]))
    monkeypatch.setattr(client_resources, 'loadNames', lru_cache()(lambda _: tuple(f'NPC{i}' for i in range(30))))
    monkeypatch.setattr(client_resources, 'checkLayout', lambda _: None)
    monkeypatch.setattr(client_resources.combat_resources, 'nativeProfiles', lambda _: [])
    directory = str(tmp_path / 'Bin')
    try:
        for nav in navs:
            current[0] = nav
            client_resources.checkResources(directory)
            before = spawn_navigation.candidates.cache_info()
            assert before.currsize == 2 and before.misses == 2
            for camp in (1, 2):
                groups = spawn_navigation.candidates(nav, respawns[camp], spawns[camp][0][0])
                assert any(groups)
            after = spawn_navigation.candidates.cache_info()
            assert after.hits == before.hits + 2 and after.misses == before.misses
    finally:
        spawn_navigation.candidates.cache_clear()
        spawn_navigation.connectedFaces.cache_clear()


def test_failed_resource_preflight_does_not_keep_previous_respawn_cache(tmp_path, monkeypatch):
    nav = NavMesh.fromPolygons([[(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0)]])
    spawn_navigation.candidates(nav, (((-1., 5., .2), 0),), (2., 2., .2))

    def fail(_):
        raise ValueError('invalid resource layout')

    monkeypatch.setattr(client_resources, 'checkLayout', fail)
    with pytest.raises(ValueError, match='invalid resource layout'):
        client_resources.checkResources(str(tmp_path / 'Bin'))
    assert spawn_navigation.candidates.cache_info().currsize == 0
    assert spawn_navigation.connectedFaces.cache_info().currsize == 0
