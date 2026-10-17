"""Deterministic project rules for the server-only part of melee damage."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Rules:
    armorScale: tuple = (1., .6, .35)
    regionScale: tuple = (1.5, 1., .75)
    phasePoints: tuple = ((0., .25), (.35, 1.), (.65, 1.), (1., .25))
    chargeMax: float = 1.5
    chargeTail: float = 1.1
    chargeDecayMs: int = 400
    friendlyScale: tuple = (1., .4, .05)
    cleaveScale: tuple = ((1.,), (1., .5), (1., .65, .4), (1., .8, .6, .4))
    speedMin: float = .5
    speedMax: float = 1.5
    counter: float = 1.2
    light: float = 1.15
    chargeMastery: float = 1.25
    desperate: float = 1.2
    desperateHealth: float = .3
    assassin: tuple = (1.1, 1.2, 1.3)
    shieldBreaking: float = 1.5


DEFAULT_RULES = Rules()
DAMAGE_TYPES = ('slash', 'pierce', 'blunt')
REGIONS = ('head', 'torso', 'legs')
CLEAVE_LEVELS = ('none', 'low', 'medium', 'high')


@dataclass(frozen=True)
class Hit:
    base: float
    damageType: str
    health: int = 100
    armor: float = 0
    region: str = 'torso'
    heldMs: float = 0
    peakMs: int = 600
    phase: float = .5
    sourceVelocity: tuple = (0., 0., 0.)
    targetVelocity: tuple = (0., 0., 0.)
    weaponVelocity: tuple = (0., 0., 0.)
    continuous: bool = False
    traits: tuple = ()
    buffMultiplier: float = 1
    healthRatio: float = 1
    airborne: bool = False
    targetAttacking: bool = False
    longPole: bool = False
    downAttack: bool = False
    rear: bool = False
    friendly: bool = False
    allies: int = 0
    enemyIndex: int = 0
    cleave: str = 'none'
    shieldHealth: int | None = None
    damageScale: float = 1


@dataclass(frozen=True)
class Result:
    hpDamage: int = 0
    shieldDamage: int = 0
    head: bool = False
    decay: bool = False
    interrupt: bool = False
    events: tuple = ()
    factors: tuple = ()
    movementMs: int = 0
    knockdown: bool = False


def interpolate(points, value):
    if value <= points[0][0]:
        return points[0][1]
    for (left, low), (right, high) in zip(points, points[1:]):
        if value <= right:
            return low + (high - low) * (value - left) / (right - left)
    return points[-1][1]


def chargeMultiplier(heldMs, peakMs, rules=DEFAULT_RULES):
    return interpolate(((0, 1), (peakMs, rules.chargeMax),
                        (peakMs + rules.chargeDecayMs, rules.chargeTail)), heldMs)


def phaseMultiplier(phase, rules=DEFAULT_RULES):
    return interpolate(rules.phasePoints, phase)


def speedMultiplier(source, target, weapon, continuous, rules=DEFAULT_RULES):
    if not continuous:
        return 1.
    baseline = tuple(w - s for w, s in zip(weapon, source))
    squared = sum(v * v for v in baseline)
    if squared < .01:
        return 1.
    projected = sum((s - t) * b for s, t, b in zip(source, target, baseline)) / squared
    return max(rules.speedMin, min(rules.speedMax, 1 + projected))


def calculate(hit, rules=DEFAULT_RULES):
    numbers = (hit.base, hit.armor, hit.heldMs, hit.peakMs, hit.phase, hit.buffMultiplier, hit.healthRatio, hit.damageScale)
    if (any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in numbers)
            or hit.phase > 1 or hit.healthRatio > 1 or hit.peakMs <= 0
            or hit.damageType not in DAMAGE_TYPES or hit.cleave not in CLEAVE_LEVELS
            or any(type(v) is not int or v < 0 for v in (hit.health, hit.allies, hit.enemyIndex))
            or hit.shieldHealth is not None and (type(hit.shieldHealth) is not int or hit.shieldHealth < 0)
            or any(len(v) != 3 or any(not math.isfinite(x) for x in v)
                   for v in (hit.sourceVelocity, hit.targetVelocity, hit.weaponVelocity))):
        raise ValueError('invalid damage evidence')
    cleave = rules.cleaveScale[CLEAVE_LEVELS.index(hit.cleave)]
    if hit.friendly or hit.enemyIndex >= len(cleave) or hit.health == 0:
        return Result()
    traits = set(hit.traits)
    charge = chargeMultiplier(hit.heldMs, hit.peakMs, rules)
    if 'charge_mastery' in traits:
        charge = 1 + (charge - 1) * rules.chargeMastery
    traitScale = hit.buffMultiplier
    if 'counter' in traits and hit.longPole and hit.downAttack and hit.targetAttacking:
        traitScale *= rules.counter
    if 'light' in traits and hit.airborne:
        traitScale *= rules.light
    if 'desperate' in traits and hit.healthRatio <= rules.desperateHealth:
        traitScale *= rules.desperate
    if hit.rear:
        for index, name in enumerate(('assassin_low', 'assassin_medium', 'assassin_high')):
            if name in traits:
                traitScale *= rules.assassin[index]
    region = hit.region if hit.region in REGIONS else 'torso'
    shield = hit.shieldHealth is not None
    factors = (
        ('base', hit.base),
        ('armor', 1. if shield else 100 / (100 + hit.armor * rules.armorScale[DAMAGE_TYPES.index(hit.damageType)])),
        ('region', 1. if shield else rules.regionScale[REGIONS.index(region)]),
        ('charge', charge), ('phase', phaseMultiplier(hit.phase, rules)),
        ('speed', speedMultiplier(hit.sourceVelocity, hit.targetVelocity, hit.weaponVelocity, hit.continuous, rules)),
        ('traits', traitScale), ('friendly', rules.friendlyScale[min(hit.allies, 2)]),
        ('cleave', cleave[hit.enemyIndex]),
        ('shieldBreaking', rules.shieldBreaking if shield and 'shield_break' in traits else 1.),
        ('guard', hit.damageScale),
    )
    amount = max(1, math.floor(math.prod(value for _, value in factors) + .5))
    if shield:
        loss = min(hit.shieldHealth, amount)
        events = ('block', 'shield-break') if loss == hit.shieldHealth else ('block',)
        return Result(shieldDamage=loss, events=events, factors=factors)
    loss = min(hit.health, amount)
    events = ('hit', 'kill') if loss == hit.health else ('hit',)
    return Result(hpDamage=loss, head=region == 'head', decay=hit.allies > 0,
                  interrupt=hit.allies == 0, events=events, factors=factors)
