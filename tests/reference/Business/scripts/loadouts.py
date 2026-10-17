"""Shared lobby profile and immutable per-round equipment selection."""
from copy import deepcopy
import struct

from . import hero_resources, player_profile
from .codec import loadout_flow, login_flow


def catalogue(flow):
    return hero_resources.loadCatalogue(flow.context['clientDirectory'])


def freeze(profile):
    return {**deepcopy(profile), 'weapons': {str(tid): deepcopy(profile['weapons'][str(tid)])
                                          for tid in profile['formation']}}


def begin(flow):
    if 'playerProfile' not in flow.state:
        return
    profile = flow.state.pop('matchedProfile', None) or freeze(flow.state['playerProfile'])
    if not profile['formation']:
        raise ValueError('cannot enter an instance with an empty formation')
    flow.session['loadoutProfile'] = profile
    select(flow, 1)


def select(flow, position):
    profile = flow.session.get('loadoutProfile')
    if profile is None:
        return False
    if type(position) is not int or not 1 <= position <= len(profile['formation']):
        raise ValueError('selected slot is not in this round\'s formation')
    from .combat_resources import loadCombatProfiles
    from . import damage_resources, damage_state
    cat = catalogue(flow)
    heroId = profile['formation'][position - 1]
    items = player_profile.equipment(profile, cat, heroId)
    profiles = loadCombatProfiles(flow.context['clientDirectory'], heroId)
    weaponId = player_profile.initialWeapon(items, cat)
    flow.session.update(heroId=heroId, equipment=items, mainWeaponId=weaponId, weaponId=weaponId,
                        offhandId=player_profile.offhand(items, cat, weaponId),
                        weaponProfiles={str(tid): deepcopy(profiles[tid]) for tid in
                                        {hero_resources.FIST_ID, *(i['weaponId'] for i in items)}})
    flow.session['damageProfile'] = damage_resources.loadProfile(heroId,
        {hero_resources.FIST_ID, *(i['weaponId'] for i in items)})
    damage_state.reset(flow.session)
    return True


def resetWeapons(flow):
    from . import damage_state
    damage_state.reset(flow.session)
    if 'equipment' not in flow.session:
        flow.session.pop('weaponId', None)
        return
    cat = catalogue(flow)
    weaponId = player_profile.initialWeapon(flow.session['equipment'], cat)
    flow.session.update(weaponId=weaponId, mainWeaponId=weaponId,
                        offhandId=player_profile.offhand(flow.session['equipment'], cat, weaponId))


def syncInstance(flow, sequence):
    profile = flow.session.get('loadoutProfile')
    if profile is None:
        return False
    for body in loadout_flow.containers(profile, catalogue(flow), 2, sequence, selector=0x66):
        flow.send(0x29, body, 'instance-configured-battle-heroes')
    return True


def publish(flow, previous, heroId=None):
    profile, cat = flow.state['playerProfile'], catalogue(flow)
    changed = ({heroId} if heroId is not None else
               set(previous['formation']).symmetric_difference(profile['formation']))
    for tid in sorted(changed):
        flow.send(0x28, loadout_flow.heroUpdate(profile, cat, tid), 'configured-hero-update')
    for body in loadout_flow.containers(profile, cat, 2, 0):
        flow.send(0x28, body, 'configured-battle-heroes')
    flow.send(0x22, loadout_flow.formations(profile, cat), 'configured-formations')


def message(flow, command, selector, body):
    if 'playerProfile' not in flow.state or flow.session['role'] != 'logic':
        return False
    profile = flow.state['playerProfile']
    if command == 0x20 and selector == 1:
        if not profile['formation']:
            # START_RES echoes the request pattern; nonzero result leaves the lobby intact.
            from . import contracts
            response = contracts.matchStart(body)
            flow.send(command, response[:-201] + struct.pack('>i', loadout_flow.INVALID) + response[-197:],
                      'match-empty-formation')
            return True
        flow.state['matchedProfile'] = freeze(profile)
        return False
    if command == 0x28 and selector == 1:
        container, sequence = login_flow.decode_item_container_request(body)
        if container not in (1, 2):
            return False
        for response in loadout_flow.containers(profile, catalogue(flow), container, sequence):
            flow.send(command, response, 'configured-hero-container')
        flow.phase('lobby-data-sent')
        return True
    if command != 0x22 or selector not in (3, 5, 8, 14):
        return False
    cat = catalogue(flow)
    if selector == 3:
        # The native request's data byte is a placeholder, not a zero sentinel.
        if len(body) != 3:
            raise ValueError('invalid get-formation request')
        flow.send(command, loadout_flow.formations(profile, cat), 'configured-formations')
        return True
    if selector == 8:
        valid = len(body) == 6 and struct.unpack_from('>i', body, 2)[0] == 1
        flow.send(command, loadout_flow.result(selector, 0 if valid else loadout_flow.INVALID), 'formation-selection')
        return True
    try:
        heroId = None
        if selector == 5:
            candidate = player_profile.change(profile, cat, formation=loadout_flow.decodeFormation(body, cat))
        else:
            heroId, slots = loadout_flow.decodeWeapons(body, cat)
            candidate = player_profile.change(profile, cat, heroId=heroId, slots=slots)
    except ValueError as error:
        flow.send(command, loadout_flow.result(selector, loadout_flow.INVALID), 'loadout-rejected')
        flow.log('WARN', f'loadout-rejected: {error}')
        return True
    flow.state['playerProfile'] = candidate
    flow.send(command, loadout_flow.result(selector), 'loadout-saved')
    if heroId is not None:
        flow.send(command, loadout_flow.weaponNotify(candidate, cat, heroId), 'configured-weapon-slots')
    publish(flow, profile, heroId)
    return True


def saveFailed(event, state, context, error):
    from .app import Flow
    flow = Flow(event, deepcopy(state), context)
    selector = int.from_bytes(event['body'][:2], 'big')
    flow.send(0x22, loadout_flow.result(selector, loadout_flow.SAVE_FAILED), 'loadout-save-failed')
    flow.log('ERROR', f'武将配置保存失败，已保留原配置：{type(error).__name__}')
    return flow.result
