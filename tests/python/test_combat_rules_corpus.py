"""Cross-language damage/guard vectors frozen from the existing Python rules."""
from dataclasses import asdict, replace
import itertools
import json
from pathlib import Path

from Business.scripts import damage_rules as damage, guard_rules as guard

CORPUS = Path(__file__).resolve().parents[1] / 'fixtures/rules/combat-v1.jsonl'


def vectors():
    rows = []

    def add(kind, inputs, expected, rules=None):
        row = dict(kind=kind, inputs=inputs, expected=expected)
        if rules:
            row['rules'] = rules
        rows.append(row)

    def hit(**changes):
        value = replace(damage.Hit(base=100, damageType='slash'), **changes)
        add('damage', changes, asdict(damage.calculate(value)))

    hit()
    for kind in damage.DAMAGE_TYPES:
        for armor in (0, 100, 10000):
            hit(damageType=kind, armor=armor)
    for region in (*damage.REGIONS, 'unknown'):
        hit(region=region, health=1000)
    for held in (0, 1, 300, 599.99, 600, 800, 1000, 10000):
        add('charge', dict(heldMs=held, peakMs=600), damage.chargeMultiplier(held, 600))
        hit(heldMs=held, health=1000)
    for phase in (0, .175, .35, .5, .65, .825, 1):
        add('phase', dict(phase=phase), damage.phaseMultiplier(phase))
        hit(phase=phase)
    speeds = (
        ((0., 0., 0.), (0., 0., 0.), (10., 0., 0.)),
        ((5., 0., 0.), (0., 0., 0.), (15., 0., 0.)),
        ((0., 0., 0.), (5., 0., 0.), (10., 0., 0.)),
        ((0., 5., 0.), (0., 0., 0.), (10., 5., 0.)),
        ((4., 0., 0.), (4., 0., 0.), (14., 0., 0.)),
        ((10., 0., 0.), (0., 0., 0.), (10., 0., 0.)),
        ((0., 0., 0.), (0., -1., 0.), (.1, 0., 0.)),
        ((0., 0., 0.), (-1e16, -1., 1e16), (1., 1., 1.)),
        ((-1.2, 4.3, -.5), (2., -.3, 1.4), (3.7, 5.2, -.8)),
    )
    for source, target, weapon in speeds:
        for continuous in (False, True):
            add('speed', dict(source=source, target=target, weapon=weapon, continuous=continuous),
                damage.speedMultiplier(source, target, weapon, continuous))
            hit(sourceVelocity=source, targetVelocity=target, weaponVelocity=weapon, continuous=continuous)
    for level in damage.CLEAVE_LEVELS:
        for index in range(5):
            hit(cleave=level, enemyIndex=index)
    for allies in (0, 1, 2, 7):
        hit(allies=allies)
    for changes in (dict(friendly=True), dict(health=0), dict(base=0), dict(base=2.5),
                    dict(base=2.499999999), dict(base=2.500000001), dict(base=1000, health=7),
                    dict(damageScale=0), dict(damageScale=.25), dict(buffMultiplier=0),
                    dict(buffMultiplier=1.2 * 1.2, health=1000)):
        hit(**changes)
    for trait, changes in (
            ('counter', dict(longPole=True, downAttack=True, targetAttacking=True)),
            ('counter', dict(longPole=False, downAttack=True, targetAttacking=True)),
            ('counter', dict(longPole=True, downAttack=False, targetAttacking=True)),
            ('counter', dict(longPole=True, downAttack=True, targetAttacking=False)),
            ('light', dict(airborne=True)), ('light', dict(airborne=False)),
            ('charge_mastery', dict(heldMs=600)), ('charge_mastery', dict(heldMs=1000)),
            ('desperate', dict(healthRatio=.3)), ('desperate', dict(healthRatio=.31)),
            ('assassin_low', dict(rear=True)), ('assassin_medium', dict(rear=True)),
            ('assassin_high', dict(rear=True)), ('assassin_high', dict(rear=False))):
        hit(traits=[trait], health=1000, **changes)
    hit(traits=['light', 'light'], airborne=True, health=1000)
    hit(traits=['assassin_high', 'assassin_low', 'assassin_medium'], rear=True, health=1000)
    hit(traits=['counter', 'light', 'charge_mastery', 'desperate', 'assassin_high'],
        longPole=True, downAttack=True, targetAttacking=True, airborne=True, rear=True,
        heldMs=800, healthRatio=.2, armor=12.5, health=1000, buffMultiplier=1.2)
    for shield in (0, 30, 1000):
        hit(armor=500, region='head', shieldHealth=shield)
        hit(armor=500, region='head', shieldHealth=shield, traits=['shield_break'])

    for gap in range(-7, 8):
        for chamber in (False, True):
            add('fraction', dict(gap=gap, chamber=chamber), guard.damageFraction(gap, chamber=chamber))
        for attack, defence in ((1, 1), (2, 1), (1, 2), (1, 3), (3, 1)):
            add('consumption', dict(gap=gap, attackWeight=attack, defenceWeight=defence),
                guard.consumption(gap, attack, defence))
    intents = (300560, 300550, 300570, 300540, 0)
    for defence, attack, elapsed in itertools.product(intents, intents, (-1, 49, 50, 125.5, 200, 201)):
        add('chamber', dict(defence=defence, attack=attack, elapsedMs=elapsed),
            guard.chamberWindow(defence, attack, elapsed))
    for grade, intent, held, traits in itertools.product(guard.GRADES, intents, (599, 600),
            ([], ['heavy_low'], ['heavy_medium'], ['heavy_high'], ['heavy_special'],
             ['heavy_low', 'heavy_high', 'heavy_special', 'heavy_special'])):
        context = dict(attackGrade=grade, heldMs=held, traits=traits, longPole=False)
        add('performance', dict(context=context, intent=intent, targetStriking=False, allies=0),
            guard.attackPerformance(context, intent))
    for long_pole, striking, allies in itertools.product((False, True), (False, True), (0, 1, 2)):
        context = dict(attackGrade='C', heldMs=0, traits=['counter'], longPole=long_pole)
        add('performance', dict(context=context, intent=300570, targetStriking=striking, allies=allies),
            guard.attackPerformance(context, 300570, striking, allies))
    for performance, armor, airborne, immune, fortified in itertools.product(range(8), guard.ARMOR_TYPES,
            (False, True), (False, True), (False, True)):
        add('reaction', dict(performance=performance, armorType=armor, airborne=airborne,
            immune=immune, fortified=fortified), asdict(guard.bodyReaction(performance, armor,
                airborne=airborne, immune=immune, fortified=fortified)))

    custom_damage = dict(armorScale=[.8, .5, .2], regionScale=[1.25, 1.1, .8],
        phasePoints=[[0., .1], [.2, .8], [.5, 1.2], [1., .5]], chargeMax=1.7,
        chargeTail=1.2, chargeDecayMs=250, friendlyScale=[1., .3, .1],
        cleaveScale=[[1.], [1., .7], [1., .8, .5], [1., .9, .7, .5]],
        speedMin=.75, speedMax=1.25, counter=1.4, light=1.3, chargeMastery=1.5,
        desperate=1.4, desperateHealth=.4, assassin=[1.2, 1.4, 1.6], shieldBreaking=1.8)
    for changes in (dict(armor=75., region='head', heldMs=725, phase=.7, health=1000),
                    dict(heldMs=1000, traits=['charge_mastery', 'light'], airborne=True, health=1000),
                    dict(cleave='high', enemyIndex=2, allies=1, shieldHealth=1000, traits=['shield_break'])):
        add('damage', changes, asdict(damage.calculate(replace(damage.Hit(100, 'slash'), **changes),
            damage.Rules(**custom_damage))), custom_damage)
    custom_guard = dict(consumptionBase=.8, performanceCost=.3, weightCost=.7,
        consumptionMin=.1, consumptionMax=3., breakDamage=[.2, .4, .6],
        chamberStartMs=25, chamberEndMs=150, fullChargeMs=500,
        lightStunMs=300, mediumStunMs=200, knockdownMs=1200)
    rules = guard.Rules(**custom_guard)
    add('consumption', dict(gap=1, attackWeight=2, defenceWeight=3), guard.consumption(1, 2, 3, rules), custom_guard)
    add('fraction', dict(gap=4, chamber=False), guard.damageFraction(4, rules=rules), custom_guard)
    add('chamber', dict(defence=300560, attack=300570, elapsedMs=25), guard.chamberWindow(300560, 300570, 25, rules), custom_guard)
    for armor in guard.ARMOR_TYPES:
        add('reaction', dict(performance=4, armorType=armor, airborne=False, immune=False, fortified=False),
            asdict(guard.bodyReaction(4, armor, rules=rules)), custom_guard)
    return rows


def test_combat_rules_match_frozen_cross_language_corpus():
    expected = '\n'.join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
                         for row in vectors()) + '\n'
    assert CORPUS.read_text(encoding='utf-8') == expected
