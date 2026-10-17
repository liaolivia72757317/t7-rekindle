import importlib
from pathlib import Path

import pytest

from Business.runtime.host_runtime import Runtime
from Business.scripts import bot_names, client_resources, combat_resources, dash_resources, movement_animation, navmesh_resource, spawn_resource, vfs_resource
from dash_fixture import parameters, states, weapons
from movement_animation_fixture import character
from vfs_fixture import NAME_PATH, NAV_PATH, SPAWN_PATH, archiveBytes, nameTable, writeClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    directory, _ = writeClient(tmp_path / 'client')
    # Native profile enumeration is covered by test_combat_resources.py.
    monkeypatch.setattr(combat_resources, 'nativeProfiles', lambda _: [])
    yield directory
    for loader in (bot_names.loadNames, navmesh_resource.loadNavigation,
                   spawn_resource.loadSpawns, spawn_resource.loadRespawns, dash_resources.loadDashAttack):
        loader.cache_clear()
    vfs_resource.clearCache()


def test_preflight_repacked_resources_are_read_only(client):
    root = client.parent
    files = list((root / 'vfs').iterdir())
    before = {path: path.read_bytes() for path in files}
    client_resources.checkResources(str(client))
    assert before == {path: path.read_bytes() for path in files}
    writeClient(root, padding=197, level=1)
    client_resources.checkResources(str(client))
    assert len(bot_names.loadNames(str(client))) == 30


def test_missing_client_combat_tables_use_server_owned_static_configuration(client):
    result = client_resources.checkResources(str(client))
    assert result['dashAttack']['enabled']
    assert result['dashAttack']['durationMs'] == 1333
    assert result['movementAnimation']['enabled']
    assert result['diagnostics'] == []


def test_recheck_client_combat_table_changes_do_not_replace_static_profiles(client):
    before = client_resources.checkResources(str(client))
    names = nameTable([('npc', f'测试名字{i}') for i in range(30)])
    (client.parent / 'vfs/data1.vfs').write_bytes(archiveBytes([
        (NAME_PATH, names, 3), ('../' + dash_resources.WEAPON_PATH, weapons(), 3),
        ('../' + dash_resources.PARAM_PATH, parameters(), 3), ('../' + dash_resources.STATE_PATH, states(), 3)]))
    (client.parent / 'vfs/actor.vfs').write_bytes(archiveBytes([
        ('../' + movement_animation.CHARACTERS[(110001, 1030411)], character(), 3)]))
    result = client_resources.checkResources(str(client))
    assert result == before


@pytest.mark.parametrize('overlay', ['vfs/MapPatch.vfs', 'vfs/extra.vfs', 'vfs/MapPatch2Remove.txt',
                                   'Data/Config/VFSConfig.cfg', SPAWN_PATH[3:], NAV_PATH[3:], NAME_PATH[3:]])
def test_actual_overlays_are_rejected(client, overlay):
    path = client.parent / overlay
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('fixture', encoding='utf-8')
    with pytest.raises(vfs_resource.ResourceError, match='unsupported_overlay.*覆盖布局尚未支持'):
        client_resources.checkResources(str(client))


def test_manifest_references_and_version_are_not_compatibility_decisions(client):
    client_resources.checkResources(str(client))  # No update.lst is required.
    (client.parent / 'update.lst').write_text('v1.2.3.4Beta\nvfs/MapPatch.vfs\nvfs/MapPatch2Remove.txt\n', encoding='utf-8')
    client_resources.checkResources(str(client))
    (client.parent / 'vfs/data1.vfs').write_bytes(b'bad archive, same client version')
    with pytest.raises(vfs_resource.ResourceError, match='directory_invalid'):
        client_resources.checkResources(str(client))


def test_client_name_content_validation_retained(client):
    (client.parent / 'vfs/data1.vfs').write_bytes(archiveBytes([(NAME_PATH, b'unsupported content', 3)]))
    bot_names.loadNames.cache_clear()
    with pytest.raises(vfs_resource.ResourceError, match='content_mismatch') as error:
        bot_names.loadNames(str(client))
    assert error.value.__cause__ is not None
    assert NAME_PATH[3:] in str(error.value)


@pytest.mark.parametrize('missing', [False, True])
def test_preflight_uses_static_map_even_when_client_map_archive_changes(client, monkeypatch, missing):
    archive = client.parent / 'vfs/map.vfs'
    if missing:
        archive.unlink()
    else:
        archive.write_bytes(b'not a map archive')
    readEntry = vfs_resource.readEntry
    def onlyNames(path, entry):
        assert path.name == 'data1.vfs' and entry == bot_names.NAME_PATH
        return readEntry(path, entry)
    monkeypatch.setattr(vfs_resource, 'readEntry', onlyNames)
    client_resources.checkResources(str(client))
    assert spawn_resource.loadSpawns(str(client))[1][0] == ((272.451, 157.634, 0.217519), -116)
    assert len(navmesh_resource.loadNavigation(str(client)).polygons) == 1803


def test_recheck_drops_cached_success_and_can_retry(client):
    client_resources.checkResources(str(client))
    archive = client.parent / 'vfs/data1.vfs'
    names = tuple(f'新名字{i}' for i in range(30))
    archive.write_bytes(archiveBytes([(NAME_PATH, nameTable([('npc', name) for name in names]), 1)], padding=10))
    client_resources.checkResources(str(client))
    assert bot_names.loadNames(str(client)) == names
    archive.unlink()
    with pytest.raises(vfs_resource.ResourceError, match='archive_read'):
        client_resources.checkResources(str(client))
    writeClient(client.parent)
    client_resources.checkResources(str(client))


def test_revision_preflight_uses_same_resource_loaders_and_retires_cache(client, tmp_path, monkeypatch):
    business = Path(__file__).resolve().parents[2] / 'tests/reference/Business'
    runtime = Runtime(business / 'scripts', tmp_path / 'revisions')
    revision = runtime.active.path
    try:
        nav = importlib.import_module(runtime.active.name + '.navmesh_resource')
        for name in ('map_data.py', 'map_navigation_data.py'):
            assert (revision / name).read_bytes() == (business / 'scripts' / name).read_bytes()
        combat = importlib.import_module(runtime.active.name + '.combat_resources')
        monkeypatch.setattr(combat, 'nativeProfiles', lambda _: [])
        result = runtime.checkResources(str(client))
        assert result['dashAttack']['durationMs'] == 1333 and not result['diagnostics']
        assert nav.loadNavigation.cache_info().currsize == 1
        (client.parent / 'vfs/map.vfs').unlink()
        assert runtime.checkResources(str(client)) == result
        (client.parent / 'vfs/data1.vfs').unlink()
        with pytest.raises(ValueError, match='archive_read'):
            runtime.checkResources(str(client))
    finally:
        runtime.close()
    assert not revision.exists()


def test_relative_directory_is_rejected():
    with pytest.raises(vfs_resource.ResourceError, match='absolute Bin'):
        client_resources.checkResources('relative/Bin')


@pytest.mark.parametrize('names,player', [(['测试名字'], ''),
                                       ([f'测试名字{i}' for i in range(19)], '测试名字0'),
                                       (['名字超出加前缀后上限的长度测试' + str(i) for i in range(19)], '')])
def test_unusable_name_pool_is_rejected_before_launch(client, names, player):
    (client.parent / 'vfs/data1.vfs').write_bytes(archiveBytes([
        (NAME_PATH, nameTable([('npc', name) for name in names]), 3)]))
    with pytest.raises(vfs_resource.ResourceError, match='content_mismatch.*sh_res_ai_name_cli.bin'):
        client_resources.checkResources(str(client), player)
