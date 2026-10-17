"""Per-life guard meter, advanced on the native contact watermark."""
import math

from . import actors, contracts as wire, guard_rules
from .codec import battle_flow

RULES = guard_rules.DEFAULT_RULES


def reset(data):
    data['guardMeter'] = dict(value=RULES.maximum, at=0, lastBlockAt=0)


def meter(data):
    if 'guardMeter' not in data:
        reset(data)
    return data['guardMeter']


def publish(flow, actorId):
    if actorId == wire.ACTOR_ID:
        flow.send(battle_flow.BATTLE_COMMAND, battle_flow.encode_parry_value_notify(
            value=meter(actors.data(flow, actorId))['value']), 'combat-guard-value')


def advance(data, at):
    previous = meter(data)
    at = max(at, previous['at'])
    elapsed = max(0, at - max(previous['at'], previous['lastBlockAt'] + RULES.recoverDelayMs))
    value = min(RULES.maximum, previous['value'] + elapsed * RULES.recoverPerSecond / 1000)
    data['guardMeter'] = {**previous, 'value': value, 'at': at}
    return value != previous['value']


def consume(flow, actorId, amount, at):
    data = actors.data(flow, actorId)
    advance(data, at)
    previous = meter(data)
    remaining = max(0, previous['value'] - amount)
    exhausted = remaining <= 1e-9
    data['guardMeter'] = dict(value=RULES.resetValue if exhausted else remaining,
                             at=previous['at'], lastBlockAt=max(at, previous['lastBlockAt']))
    publish(flow, actorId)
    return exhausted


def restore(flow, actorId, at):
    data = actors.data(flow, actorId)
    advance(data, at)
    previous = meter(data)
    data['guardMeter'] = {**previous, 'value': min(RULES.maximum, previous['value'] + RULES.hitRecovery)}
    publish(flow, actorId)


def update(flow, at):
    for actorId in actors.ids(flow.session):
        if actors.active(flow, actorId) and advance(actors.data(flow, actorId), at):
            publish(flow, actorId)


def validState(data):
    value = data.get('guardMeter')
    return value is None or (type(value) is dict
        and all(type(value.get(key)) in (int, float) and math.isfinite(value[key]) and value[key] >= 0
                for key in ('value', 'at', 'lastBlockAt'))
        and value['value'] <= RULES.maximum and value['lastBlockAt'] <= value['at'])
