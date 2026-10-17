"""Resource preflight shared by explicit checks and runtime startup."""
from pathlib import Path

from . import combat_resources, hero_resources, spawn_navigation
from .bot_names import NAME_PATH, assignNames, loadNames
from .dash_resources import DashAttackProfile
from .navmesh_resource import NAV_PATH, loadNavigation
from .spawn_resource import SPAWN_PATH, loadRespawns, loadSpawns
from .vfs_resource import ResourceError, clearCache

BASE_ARCHIVES = frozenset(('actor.vfs', 'data1.vfs', 'map.vfs', 'model.vfs'))


def checkLayout(root):
    try:
        for path in sorted((root / 'vfs').iterdir()):
            name = path.name.lower()
            if ((path.suffix.lower() == '.vfs' and name not in BASE_ARCHIVES)
                    or name.endswith('remove.txt')):
                raise ResourceError('unsupported_overlay', path.name, '', '覆盖布局尚未支持')
        for resource in ('data/config/vfsconfig.cfg', SPAWN_PATH, NAV_PATH, NAME_PATH):
            try:
                (root / resource).stat()
            except FileNotFoundError:
                continue
            raise ResourceError('unsupported_overlay', '', resource, '覆盖布局尚未支持')
    except OSError as error:
        raise ResourceError('archive_read', 'vfs', '', str(error)) from error


def checkResources(clientDirectory, playerName=''):
    directory = Path(clientDirectory)
    if not directory.is_absolute():
        raise ResourceError('archive_read', '', '', 'selected absolute Bin directory required')
    clearCache()
    spawn_navigation.candidates.cache_clear()
    spawn_navigation.connectedFaces.cache_clear()
    loaders = (loadSpawns, loadRespawns, loadNavigation, loadNames)
    for loader in loaders:
        loader.cache_clear()
    hero_resources.loadCatalogue.cache_clear()
    combat_resources.clearCache()
    checkLayout(directory.parent)
    for loader in loaders:
        loader(clientDirectory)
    try:
        assignNames(loadNames(clientDirectory), 19, 0, playerName)
    except ValueError as error:
        raise ResourceError('content_mismatch', 'data1.vfs', NAME_PATH, str(error)) from error
    nav, spawns, respawns = loadNavigation(clientDirectory), loadSpawns(clientDirectory), loadRespawns(clientDirectory)
    # First respawn must only check current occupancy, not scan the entire map.
    for camp in (1, 2):
        spawn_navigation.candidates(nav, respawns[camp], spawns[camp][0][0])
    selected = combat_resources.defaultProfile()
    dash = selected['dashAttack'] or vars(DashAttackProfile(weaponId=selected['weaponId']))
    return {'dashAttack': dash, 'movementAnimation': selected['movementAnimation'],
            'combatProfiles': combat_resources.nativeProfiles(clientDirectory), 'diagnostics': []}
