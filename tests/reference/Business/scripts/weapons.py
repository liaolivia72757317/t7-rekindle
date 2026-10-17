"""The client's hand-toggle and primary-slot requests; no keyboard interception."""
from . import actors, contracts as wire
from .codec import weapon_flow

FIST_ID = 100000
CHANGE_STATE = 1
# s_battle_param_cli: [持续时间]换出武器, keys (1,2,0,0,0) / (1,1,0,0,0).
CHANGE_DURATION_MS = {wire.WEAPON_ID: 1000, FIST_ID: 800}


def equipped(data):
    return data.get('weaponId', wire.WEAPON_ID)


def profile(data, weaponId=None):
    weaponId = equipped(data) if weaponId is None else weaponId
    if 'weaponProfile' in data:
        return data['weaponProfile']
    return data.get('weaponProfiles', {}).get(str(weaponId))


def inventory(data):
    return [*data.get('equipment', [dict(slot=1, weaponId=wire.WEAPON_ID)]),
            dict(slot=weapon_flow.FIST_SLOT, weaponId=FIST_ID)]


def offhand(data, weaponId):
    selected = profile(data, weaponId)
    if not selected or selected['hand'] != 2 or weaponId == FIST_ID:
        return 0
    return next((item['weaponId'] for item in data['equipment']
                 if profile(data, item['weaponId'])['shield']
                 and data.get('shieldHealth', {}).get(str(item['weaponId']), 1) > 0), 0)


def shieldPrimary(data):
    candidates = [item['weaponId'] for item in sorted(data['equipment'], key=lambda item: item['slot'])
                  if profile(data, item['weaponId'])['hand'] == 2]
    for weaponId in (equipped(data), data.get('mainWeaponId'), *candidates):
        if weaponId in candidates:
            return weaponId
    return None


def changeMs(data):
    selected = profile(data)
    return selected['changeMs'] if selected else CHANGE_DURATION_MS[equipped(data)]


def validState(data):
    from .combat_resources import validProfile
    if type(equipped(data)) is not int:
        return False
    if 'weaponProfile' in data:
        selected = data['weaponProfile']
        return validProfile(selected) and selected['weaponId'] == equipped(data) and not selected['shield']
    if 'weaponProfiles' not in data:
        return equipped(data) in (wire.WEAPON_ID, FIST_ID) and 'equipment' not in data
    profiles, items = data['weaponProfiles'], data.get('equipment')
    if (type(profiles) is not dict or not 1 <= len(profiles) <= 5
            or not all(validProfile(p) and str(p['weaponId']) == key for key, p in profiles.items())
            or type(items) is not list or len(items) > 4
            or any(type(item) is not dict or set(item) != {'slot', 'weaponId'}
                   or type(item['slot']) is not int or not 1 <= item['slot'] <= 4
                   or type(item['weaponId']) is not int or str(item['weaponId']) not in profiles for item in items)):
        return False
    ids = {item['weaponId'] for item in items}
    return (len(ids) == len(items) == len({item['slot'] for item in items})
            and set(profiles) == {str(tid) for tid in (FIST_ID, *ids)}
            and sum(profiles[str(tid)]['shield'] for tid in ids) <= 1
            and str(equipped(data)) in profiles and not profile(data)['shield']
            and data.get('mainWeaponId') in (ids | {FIST_ID})
            and not profiles[str(data['mainWeaponId'])]['shield']
            and type(data.get('offhandId')) is int and data['offhandId'] == offhand(data, equipped(data)))


def message(flow, command, selector, body):
    from . import battle
    if (command != weapon_flow.WEAPON_COMMAND or selector != weapon_flow.CHANGE_REQUEST
            or flow.session['role'] != 'instance'):
        return False
    slot = weapon_flow.decode_change_request(body)
    current = equipped(flow.session)
    action = flow.session.get('action', {})
    result = 0
    if not battle.enabled(flow) or not actors.active(flow, wire.ACTOR_ID):
        return True
    items = inventory(flow.session)
    target = next((item['weaponId'] for item in items if item['slot'] == slot), None)
    if slot == weapon_flow.FIST_SLOT and current == FIST_ID:
        target = flow.session.get('mainWeaponId', wire.WEAPON_ID)
    selected = profile(flow.session, target) if target is not None else None
    if target is None:
        result = weapon_flow.INVALID_SLOT
    elif selected and selected['shield']:
        target = shieldPrimary(flow.session)
        if target is None:
            return True
    if not result and ('hurtUntil' in flow.session or action.get('index', -1) >= 0 or action.get('reaction')):
        result = weapon_flow.CANNOT_CHANGE
    left = offhand(flow.session, target) if not result else flow.session.get('offhandId', 0)
    flow.send(command, weapon_flow.encode_change_response(result, current if result else target, left), 'weapon-change-response')
    if result or target == current:
        return True
    battle.resetAction(flow)
    flow.session['weaponId'] = target
    if 'equipment' in flow.session:
        flow.session['offhandId'] = left
        if target != FIST_ID:
            flow.session['mainWeaponId'] = target
    battle.actionState(flow).update(index=0, weaponChange=True)
    flow.send(command, weapon_flow.encode_equipment_update(actor_id=wire.ACTOR_ID, items=items,
              equipped_weapon_id=target, offhand_id=left, now_ms=flow.now), 'weapon-use-update')
    battle.syncState(flow, CHANGE_STATE)
    flow.later(battle.ACTION_TIMER, changeMs(flow.session))
    return True
