"""Per-life shield durability; breaking blocks the entire triggering strike."""
from . import actors, battle, contracts as wire, damage_state, weapons
from .codec import weapon_flow

# s_act_state_cli: 被破盾, duration 1000 ms.
BREAK_STATE, BREAK_MS = 41, 1000


def durability(data, weaponId):
    damage_state.profile(data)
    if 'shieldHealth' not in data:
        damage_state.reset(data)
    return data['shieldHealth'][str(weaponId)]


def publish(flow, actorId, weaponId):
    if actorId != wire.ACTOR_ID:
        return  # Native shield HP notification is local-player-only, without an actor RID.
    data = actors.data(flow, actorId)
    maximum = damage_state.profile(data)['weapons'][str(weaponId)]['shieldMax']
    slot = next(item['slot'] for item in weapons.inventory(data) if item['weaponId'] == weaponId)
    flow.send(weapon_flow.WEAPON_COMMAND, weapon_flow.encode_shield_health(
        weapon_id=weaponId, weapon_slot=slot, health=durability(data, weaponId), maximum=maximum), 'combat-shield-health')


def publishAll(flow):
    for weaponId in flow.session.get('shieldHealth', {}):
        publish(flow, wire.ACTOR_ID, int(weaponId))


def consume(flow, actorId, weaponId, damage, attacker):
    from . import combat
    data = actors.data(flow, actorId)
    remaining = max(0, durability(data, weaponId) - damage)
    data['shieldHealth'] = {**data['shieldHealth'], str(weaponId): remaining}
    publish(flow, actorId, weaponId)
    if remaining:
        return
    data['offhandId'] = 0
    flow.send(weapon_flow.WEAPON_COMMAND, weapon_flow.encode_shield_break(actor_id=actorId, weapon_id=weaponId),
              'combat-shield-break')
    flow.send(weapon_flow.WEAPON_COMMAND, weapon_flow.encode_equipment_update(actor_id=actorId,
        items=weapons.inventory(data), equipped_weapon_id=weapons.equipped(data), offhand_id=0, now_ms=flow.now),
        'combat-shield-disabled')
    battle.resetAction(flow, actorId)
    data['hurtUntil'] = flow.now + BREAK_MS
    battle.syncState(flow, BREAK_STATE, actorId, targetId=attacker, durationMs=BREAK_MS)
    flow.later(combat.hurtTimer(actorId), BREAK_MS)
