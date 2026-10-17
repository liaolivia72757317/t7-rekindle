"""Freeze attack evidence and compute damage plus performance-based hit reactions."""
from copy import deepcopy
from dataclasses import asdict, replace
import math

from . import actors, battle, damage_resources, damage_rules, hero_resources, weapons, guard_rules, guard_state

RULES = damage_rules.DEFAULT_RULES
BUFFS = {'riposte': (1.2, 3000), 'combo': (1.2, 3000), 'rage': (1.2, 5000)}


def profile(data):
    if 'damageProfile' not in data:
        ids = {item['weaponId'] for item in weapons.inventory(data)}
        data['damageProfile'] = damage_resources.loadProfile(data.get('heroId', hero_resources.DEFAULT_HERO), ids)
    return data['damageProfile']


def reset(data):
    selected = profile(data)
    data['shieldHealth'] = {tid: value['shieldMax'] for tid, value in selected['weapons'].items() if value['shieldMax']}
    data['damageBuffs'] = {}
    guard_state.reset(data)
    data.pop('movementUntil', None)
    data.pop('defenceInterruptedAt', None)


def capture(data, action, now):
    selected = profile(data)
    weaponId = action.get('weaponId', weapons.equipped(data))
    weapon = selected['weapons'][str(weaponId)]
    intent = action['intent']
    attack = weapon['attacks'][str(intent)]
    rules = {key: [list(item) if type(item) is tuple else item for item in value]
             if type(value) is tuple else value for key, value in asdict(RULES).items()}
    return dict(**deepcopy(attack), traits=sorted(set(selected['traits'] + weapon['traits'])),
                heldMs=0 if intent == battle.DASH_ATTACK else action.get('heldMs', 0),
                peakMs=weapon['peakMs'], longPole=weapon['longPole'], cleave=weapon['cleave'],
                attackGrade=weapon['attackGrade'], weight=weapon['weight'],
                guardRules={key: list(value) if type(value) is tuple else value for key, value in asdict(guard_state.RULES).items()},
                buffs=deepcopy(data.get('damageBuffs', {})), rules=rules, capturedAt=now)


def grant(flow, actorId, name, at):
    data = actors.data(flow, actorId)
    if name not in profile(data)['traits']:
        return
    multiplier, duration = BUFFS[name]
    value = dict(since=at, until=at + duration, multiplier=multiplier)
    current = data.get('damageBuffs', {})
    if current.get(name, {}).get('since', -1) <= at:
        data['damageBuffs'] = {**current, name: value}
    # A delayed hit may precede a later swing that has already entered Strike.
    # Correct only later snapshots; never add a proc to the swing that produced it.
    swings = [*data.get('contactSwings', []), *([data['swing']] if 'swing' in data else [])]
    for swing in swings:
        context = swing.get('damageContext')
        if (context and at < context['capturedAt'] < value['until']
                and context['buffs'].get(name, {}).get('since', -1) <= at):
            swing['damageContext'] = {**context, 'buffs': {**context['buffs'], name: deepcopy(value)}}


def friendly(flow, attacker, target, swing):
    if actors.data(flow, attacker).get('camp', 1) != actors.data(flow, target).get('camp', 1):
        return False
    swing['hits'] = [*swing['hits'], target]
    swing['allies'] = swing.get('allies', 0) + 1
    return True


def canHit(swing):
    context = swing['damageContext']
    index = damage_rules.CLEAVE_LEVELS.index(context['cleave'])
    return swing.get('enemies', 0) < len(context['rules']['cleaveScale'][index])


def targetStriking(defender, contact=None):
    if contact is not None and 'targetState' in contact:
        return contact['targetState'] in {chain[2] for chain in battle.ATTACK_CHAINS.values()} | {battle.DASH_CHAIN[1]}
    action = defender.get('action', {})
    return bool(battle.attackChain(action.get('intent')) and action.get('index') == battle.strikeIndex(action['intent'])
                and not action.get('reaction'))


def evaluate(flow, attacker, target, snapshot, *, swing=None, contact=None, intent=None, weaponId=None, shieldHealth=None,
             damageScale=1):
    source, defender = actors.data(flow, attacker), actors.data(flow, target)
    action = battle.actionState(flow, attacker)
    intent = intent or action['intent'] or 300560
    weaponId = action.get('weaponId', weapons.equipped(source)) if weaponId is None else weaponId
    if swing is None:
        candidate = source.get('swing')
        if candidate and candidate['weaponId'] == weaponId and candidate['intent'] == intent:
            swing = candidate
    at = (contact or {}).get('occurredAt', getattr(flow, 'contactAt', flow.now))
    context = (swing or {}).get('damageContext') or capture(source, dict(action, intent=intent, weaponId=weaponId), flow.now)
    phase = .5
    if swing:
        start, end = swing['contactStartMs'], swing['contactEndMs']
        began = (contact or {}).get('phaseAt', swing['beganAt'])
        phase = max(0., min(1., (at - began - start) / (end - start)))
    sourcePosition = snapshot[actors.positionName(snapshot, attacker)]
    targetPosition = snapshot[actors.positionName(snapshot, target)]
    targetHeading = snapshot.get(actors.positionName(snapshot, target) + 'Heading', 0)
    from .combat import facingCosine
    rear = facingCosine(targetPosition, sourcePosition, targetHeading) <= -.5
    region = (contact or {}).get('region', 'unknown')
    if region not in damage_rules.REGIONS:
        flow.log('DEBUG', f'combat-region-fallback attacker={attacker} target={target} region={region}')
    buffMultiplier = math.prod(value['multiplier'] for value in context['buffs'].values()
                               if value['since'] < context['capturedAt'] and at < value['until'])
    evidence = contact or {}
    targetAttacking = targetStriking(defender, contact)
    result = damage_rules.calculate(damage_rules.Hit(
        base=context['base'], damageType=context['damageType'], health=defender['health'],
        armor=profile(defender)['armor'], region=region, heldMs=context['heldMs'], peakMs=context['peakMs'], phase=phase,
        sourceVelocity=tuple(evidence.get('sourceVelocity', (0., 0., 0.))),
        targetVelocity=tuple(evidence.get('targetVelocity', (0., 0., 0.))),
        weaponVelocity=tuple(evidence.get('weaponVelocity', (0., 0., 0.))),
        continuous=evidence.get('velocityReady', False), airborne=evidence.get('airborne', False),
        traits=tuple(context['traits']), buffMultiplier=buffMultiplier, healthRatio=source['health'] / 100,
        longPole=context['longPole'], downAttack=intent == 300570, rear=rear, targetAttacking=targetAttacking,
        allies=(swing or {}).get('allies', 0), enemyIndex=(swing or {}).get('enemies', 0),
        cleave=context['cleave'], shieldHealth=shieldHealth, damageScale=damageScale), damage_rules.Rules(**context['rules']))
    if shieldHealth is None:
        rules = guard_rules.Rules(**context['guardRules'])
        performance = guard_rules.attackPerformance(context, intent, targetAttacking, (swing or {}).get('allies', 0), rules)
        selected = profile(defender)
        reaction = guard_rules.bodyReaction(performance, selected['armorType'], airborne=evidence.get('targetAirborne', False),
            immune='siege_immunity' in selected['traits'], fortified='fortified' in selected['traits'], rules=rules)
        result = replace(result, interrupt=reaction.interrupt, movementMs=reaction.movementMs, knockdown=reaction.knockdown)
    flow.log('DEBUG', f'combat-damage attacker={attacker} target={target} weapon={weaponId} at={at} '
             f'type={context["damageType"]} region={region} hp={result.hpDamage} shield={result.shieldDamage} '
             + ' '.join(f'{key}={value:.4f}' for key, value in result.factors))
    return result


def validState(data):
    if not guard_state.validState(data) or ('movementUntil' in data and
            (type(data['movementUntil']) is not int or data['movementUntil'] < 0)) or (
            'defenceInterruptedAt' in data and not number(data['defenceInterruptedAt'])):
        return False
    shields, buffs = data.get('shieldHealth', {}), data.get('damageBuffs', {})
    if type(shields) is not dict or type(buffs) is not dict or any(name not in BUFFS for name in buffs):
        return False
    selected = data.get('damageProfile')
    if selected is not None:
        if (type(selected) is not dict or type(selected.get('armor')) is not int or selected['armor'] < 0
                or selected.get('armorType') not in guard_rules.ARMOR_TYPES
                or type(selected.get('weapons')) is not dict or type(selected.get('traits')) is not list):
            return False
        if not all(type(tid) is str and type(item) is dict and type(item.get('shieldMax')) is int
                   and item['shieldMax'] >= 0 and type(item.get('attacks')) is dict
                   and type(item.get('traits')) is list and item.get('cleave') in damage_rules.CLEAVE_LEVELS
                   and item.get('attackGrade') in guard_rules.GRADES and item.get('defenceGrade') in guard_rules.GRADES
                   and number(item.get('weight')) and item['weight'] > 0
                   and all(type(attack) is dict and attack.get('damageType') in damage_rules.DAMAGE_TYPES
                           and number(attack.get('base')) for attack in item['attacks'].values())
                   for tid, item in selected['weapons'].items()):
            return False
        maxima = {tid: item['shieldMax'] for tid, item in selected['weapons'].items() if item['shieldMax']}
        if set(shields) != set(maxima) or any(type(hp) is not int or not 0 <= hp <= maxima[tid] for tid, hp in shields.items()):
            return False
    elif shields or buffs:
        return False
    return validBuffs(buffs)


def number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def validBuffs(buffs):
    return (type(buffs) is dict and all(name in BUFFS and type(value) is dict
            and all(number(value.get(key)) for key in ('since', 'until', 'multiplier'))
            and value['until'] > value['since'] for name, value in buffs.items()))


def validContext(context):
    if (type(context) is not dict or context.get('damageType') not in damage_rules.DAMAGE_TYPES
            or context.get('cleave') not in damage_rules.CLEAVE_LEVELS
            or not all(number(context.get(key)) for key in ('base', 'heldMs', 'peakMs', 'capturedAt'))
            or context['peakMs'] <= 0 or type(context.get('longPole')) is not bool
            or context.get('attackGrade') not in guard_rules.GRADES or not number(context.get('weight')) or context['weight'] <= 0
            or not validGuardRules(context.get('guardRules'))
            or type(context.get('traits')) is not list or not all(type(item) is str for item in context['traits'])
            or not validBuffs(context.get('buffs')) or type(context.get('rules')) is not dict):
        return False
    rules = context['rules']
    if set(rules) != set(asdict(RULES)):
        return False
    for key, value in rules.items():
        if key in ('armorScale', 'regionScale', 'friendlyScale', 'assassin'):
            if type(value) is not list or len(value) != 3 or not all(number(item) for item in value):
                return False
        elif key in ('phasePoints', 'cleaveScale'):
            if type(value) is not list or (len(value) != 4 if key == 'cleaveScale' else len(value) < 2):
                return False
            if any(type(row) is not list or len(row) != (2 if key == 'phasePoints' else i + 1)
                   or not all(number(item) for item in row) for i, row in enumerate(value)):
                return False
        elif not number(value):
            return False
    return rules['chargeDecayMs'] > 0 and rules['speedMin'] <= rules['speedMax']


def validGuardRules(value):
    defaults = asdict(guard_state.RULES)
    if type(value) is not dict or set(value) != set(defaults):
        return False
    for key, item in value.items():
        if key == 'breakDamage':
            if type(item) not in (list, tuple) or len(item) != 3 or not all(number(v) and 0 < v <= 1 for v in item):
                return False
        elif not number(item) or key.endswith('Ms') and type(item) is not int:
            return False
    return (value['consumptionMin'] <= value['consumptionMax'] and value['fullChargeMs'] > 0
            and value['chamberStartMs'] <= value['chamberEndMs'])
