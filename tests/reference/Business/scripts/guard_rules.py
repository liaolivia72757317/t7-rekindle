"""Performance and guard balance. Unverified coefficients are explicit project rules."""
from dataclasses import dataclass

GRADES = ('F', 'E', 'D', 'C', 'B', 'A', 'S', 'SS')
ARMOR_TYPES = ('light', 'medium', 'heavy')
CHAMBER_DIRECTIONS = {300560: (300560, 300570), 300550: (300540,),
                      300570: (300570,), 300540: (300550,)}


@dataclass(frozen=True)
class Rules:
    # s_battle_common_para_cli: 91, 93, 94, 95, 97.
    maximum: float = 5
    recoverDelayMs: int = 2500
    recoverPerSecond: float = 2
    hitRecovery: float = 2
    resetValue: float = 2
    # Project defaults, not reconstructed original-server formulas.
    consumptionBase: float = 1
    performanceCost: float = .25
    weightCost: float = .5
    consumptionMin: float = .25
    consumptionMax: float = 2.5
    breakDamage: tuple = (.25, .5, .75)
    guardStunMs: int = 250
    exhaustionMs: int = 1000
    chamberStartMs: int = 50
    chamberEndMs: int = 200
    fullChargeMs: int = 600
    lightStunMs: int = 250
    mediumStunMs: int = 180
    knockdownMs: int = 1000


DEFAULT_RULES = Rules()


def attackPerformance(context, intent, targetStriking=False, allies=0, rules=DEFAULT_RULES):
    if allies:
        return 0
    value = GRADES.index(context['attackGrade'])
    traits = set(context['traits'])
    if context['heldMs'] >= rules.fullChargeMs:
        level = max((i for i, name in enumerate(('heavy_low', 'heavy_medium', 'heavy_high', 'heavy_special'), 1)
                     if name in traits), default=0)
        if intent == 300560:
            value += min(3, level)
        elif intent in (300540, 300550) and level == 4:
            value += 1
    if intent == 300570 and context['longPole'] and targetStriking and 'counter' in traits:
        value += 1
    return min(len(GRADES) - 1, value)


def chamberWindow(defenceIntent, attackIntent, elapsedMs, rules=DEFAULT_RULES):
    return (attackIntent in CHAMBER_DIRECTIONS.get(defenceIntent, ())
            and rules.chamberStartMs <= elapsedMs <= rules.chamberEndMs)


def damageFraction(gap, *, chamber=False, rules=DEFAULT_RULES):
    threshold = 4 if chamber else 2
    return rules.breakDamage[min(gap - threshold, len(rules.breakDamage) - 1)] if gap >= threshold else 0


def consumption(gap, attackWeight, defenceWeight, rules=DEFAULT_RULES):
    if attackWeight <= 0 or defenceWeight <= 0:
        raise ValueError('guard weights must be positive')
    amount = rules.consumptionBase + rules.performanceCost * gap + rules.weightCost * (attackWeight / defenceWeight - 1)
    return max(rules.consumptionMin, min(rules.consumptionMax, amount))


@dataclass(frozen=True)
class Reaction:
    interrupt: bool
    movementMs: int = 0
    knockdown: bool = False


def bodyReaction(performance, armorType, *, airborne=False, immune=False, fortified=False,
                 rules=DEFAULT_RULES):
    if armorType not in ARMOR_TYPES:
        raise ValueError('unknown armor type')
    interrupt = performance >= (4 if fortified else 1 if armorType == 'heavy' else 0)
    if immune or performance < 4:
        return Reaction(interrupt)
    if airborne:
        return Reaction(interrupt, rules.knockdownMs, True)
    movementMs = {'light': rules.lightStunMs, 'medium': rules.mediumStunMs, 'heavy': 0}[armorType]
    return Reaction(interrupt, movementMs)
