"""Settle real weapon interceptions; body contacts never enter this path."""
from . import actors, battle, bot, contracts as wire, damage_state, guard_rules, guard_state, hit_reactions, shields, weapons
from .codec import battle_flow

GUARD_STATES = {chain[1]: intent for intent, chain in battle.GUARD_CHAINS.items()}
GUARD_STATES.update({state: intent for intent, state in battle.PARRY_SUCCESS_STATES.items()})
GUARD_STATES[111] = battle.SHIELD_GUARD
CHAMBER_STATES = {chain[0]: intent for intent, chain in battle.ATTACK_CHAINS.items()}


def defenceMode(data, contact, attackIntent, rules):
    action = data.get('action', {})
    historical = 'targetState' in contact
    at = contact['occurredAt']
    phaseAt = contact.get('targetPhaseAt', action.get('changedAt', at))
    if phaseAt < data.get('defenceInterruptedAt', -1) <= at:
        return None
    if historical:
        state = contact['targetState']
        intent = GUARD_STATES.get(state, CHAMBER_STATES.get(state))
        chamber = state in CHAMBER_STATES
    else:
        intent = action.get('intent')
        chamber = intent in battle.ATTACK_CHAINS and action.get('index') == 0 and not action.get('reaction')
        if not chamber and (intent not in battle.GUARD_CHAINS or action.get('released')
                or not (action.get('index') == 1 or action.get('reaction') == 'parry-success')):
            return None
    if intent is None or chamber and not guard_rules.chamberWindow(intent, attackIntent, at - phaseAt, rules):
        return None
    weaponId = contact['defenceWeaponId']
    selected = damage_state.profile(data)['weapons'].get(str(weaponId))
    if selected is None or bool(selected['shieldMax']) != (intent == battle.SHIELD_GUARD):
        return None
    if not historical and weaponId != (data.get('offhandId', 0) if intent == battle.SHIELD_GUARD else weapons.equipped(data)):
        return None
    if selected['shieldMax'] and shields.durability(data, weaponId) <= 0:
        return None
    return intent, chamber, selected


def feedback(flow, attacker, defender, contact, weaponId):
    defenderWeapon = contact['defenceWeaponId']
    flow.send(battle_flow.BATTLE_COMMAND, battle_flow.encode_battle_hit_point_notify(
        hit_pos=tuple(contact['position']), hit_direction=tuple(contact['direction']), hit_normal=tuple(contact['normal']),
        material_id=contact['material'], ignore_hurt=True, attacker_mid=attacker, target_mid=defender,
        attacker_weapon_id=weaponId, target_weapon_id=defenderWeapon, server_time_ms=int(contact['occurredAt'])),
        'combat-parry-hit')
    flow.send(battle_flow.BATTLE_COMMAND, battle_flow.encode_battle_defense_notify(
        attacker_mid=attacker, defender_mid=defender, attacker_weapon_id=weaponId,
        target_weapon_id=defenderWeapon, server_time_ms=int(contact['occurredAt'])), 'combat-parry-sound')


def parry(flow, attacker, defender, snapshot, *, contact=None, attackIntent=None, defenceIntent=None, dash=None,
          weaponId=None, swing=None):
    from . import combat
    if not contact or not contact.get('defenceWeaponId'):
        return False
    source, target = actors.data(flow, attacker), actors.data(flow, defender)
    attack = battle.actionState(flow, attacker)
    intent = attack['intent'] if attackIntent is None else attackIntent
    weaponId = weapons.equipped(source) if weaponId is None else weaponId
    context = (swing or {}).get('damageContext') or damage_state.capture(source, dict(attack, intent=intent, weaponId=weaponId), flow.now)
    rules = guard_rules.Rules(**context['guardRules'])
    mode = defenceMode(target, contact, intent, rules)
    if 'targetState' in contact and wire.ACTOR_ID in (attacker, defender):
        flow.log('DEBUG', f"combat-guard attacker={attacker} target={defender} attack={intent} "
                 f"targetState={contact['targetState']} targetSeq={contact['targetSeq']} "
                 f"targetPhaseAt={contact['targetPhaseAt']} occurredAt={contact['occurredAt']} dispatchAt={flow.now} "
                 f"contactAgeMs={flow.now - contact['occurredAt']} reason={'success' if mode else 'not-holding'}")
    if mode is None:
        return False
    guardIntent, chamber, guardWeapon = mode
    performance = guard_rules.attackPerformance(context, intent, damage_state.targetStriking(target, contact),
                                                (swing or {}).get('allies', 0), rules)
    defence = guard_rules.GRADES.index(guardWeapon['attackGrade' if chamber else 'defenceGrade'])
    gap = performance - defence
    fraction = guard_rules.damageFraction(gap, chamber=chamber, rules=rules)
    at = contact['occurredAt']
    feedback(flow, attacker, defender, contact, weaponId)
    if fraction:
        # A performance break is one reduced body hit, not another meter debit or a shield spill.
        if swing is not None:
            swing['hits'] = [*swing['hits'], defender]
        combat.applyHit(flow, attacker, defender, snapshot, contact=contact, intent=intent, weaponId=weaponId,
                        swing=swing, damageScale=fraction, guardBreak=True)
        flow.log('DEBUG', f'combat-guard-break attacker={attacker} target={defender} gap={gap} chamber={chamber} scale={fraction}')
        return False  # The striking weapon can continue to later targets, never hit this target twice.

    shieldResult = None
    if guardIntent == battle.SHIELD_GUARD:
        shieldResult = damage_state.evaluate(flow, attacker, defender, snapshot, swing=swing, contact=contact,
            intent=intent, weaponId=weaponId, shieldHealth=shields.durability(target, contact['defenceWeaponId']))
    battle.parryReaction(flow, attacker, defender, success=False, intent=intent, dash=dash, weaponId=weaponId)
    current = battle.actionState(flow, defender)
    stillHolding = (current['intent'] == guardIntent and not current['released']
                    and (current['index'] == 1 or current.get('reaction') == 'parry-success'))
    if not chamber and stillHolding:
        battle.parryReaction(flow, defender, attacker, success=True, intent=guardIntent,
                             minimumMs=rules.guardStunMs if gap == 1 else 0)
    cost = guard_rules.consumption(gap, context['weight'], guardWeapon['weight'], rules)
    exhausted = guard_state.consume(flow, defender, cost, at)
    guard_state.restore(flow, attacker, at)
    if exhausted:
        hit_reactions.hurt(flow, defender, attacker, rules.exhaustionMs,
                           state=hit_reactions.EXHAUSTION_STATE, movementMs=rules.exhaustionMs)
    if defender != wire.ACTOR_ID:
        bot.parrySucceeded(flow, defender)
    damage_state.grant(flow, defender, 'riposte', at)
    if shieldResult is not None:
        shields.consume(flow, defender, contact['defenceWeaponId'], shieldResult.shieldDamage, attacker)
    flow.log('DEBUG', f'combat-parry attacker={attacker} target={defender} attack={intent} defence={guardIntent} '
             f'gap={gap} chamber={chamber} cost={cost} exhausted={exhausted}')
    return True
