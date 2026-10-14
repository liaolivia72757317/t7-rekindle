"""Melee damage tuning from body contact height and sampled closing speed."""
import math

from . import actors, body_collision

HEAD_HEIGHT = 1.5
LEG_HEIGHT = .6
HEAD_MULTIPLIER = 1.5
LEG_MULTIPLIER = .75
SPEED_BONUS_PER_MPS = .05
MAX_SPEED_BONUS = .5
MAX_SAMPLE_MS = 100
MAX_ACTOR_SPEED = 25.


def sample(flow):
    raw = actors.world(flow)
    if raw is None:
        return
    for rid in actors.ids(flow.session):
        pose = actors.pose(raw, rid)
        if pose is None:
            continue
        data = actors.data(flow, rid)
        previous = data.get('damageMotion')
        current = dict(position=list(pose['position']), at=raw['sampledAt'],
                       epoch=raw['instanceStartedAt'], velocity=[0., 0., 0.])
        if previous is not None and previous['epoch'] == current['epoch']:
            elapsed = current['at'] - previous['at']
            if elapsed <= 0:
                continue
            if 0 < elapsed <= MAX_SAMPLE_MS and previous['at'] >= data.get('contactLifeAt', 0):
                velocity = [(b - a) * 1000 / elapsed for a, b in zip(previous['position'], current['position'])]
                if math.hypot(*velocity) <= MAX_ACTOR_SPEED:
                    current['velocity'] = velocity
        data['damageMotion'] = current


def validState(data):
    value = data.get('damageMotion')
    return value is None or (type(value) is dict
        and all(type(value.get(key)) is int and value[key] >= 0 for key in ('at', 'epoch'))
        and actors.validPosition(value.get('position')) and actors.validPosition(value.get('velocity'))
        and math.hypot(*value['velocity']) <= MAX_ACTOR_SPEED)


def calculate(flow, attacker, target, snapshot, position, base):
    feet = snapshot[actors.positionName(snapshot, target)]
    height = position[2] - feet[2]
    # Out-of-body contacts cannot establish a body region.
    part = ('head' if height >= HEAD_HEIGHT else 'legs' if height < LEG_HEIGHT else 'body') \
        if 0 <= height <= body_collision.HEIGHT else 'body'
    multiplier = {'head': HEAD_MULTIPLIER, 'body': 1., 'legs': LEG_MULTIPLIER}[part]
    motions = [actors.data(flow, rid).get('damageMotion') for rid in (attacker, target)]
    closing = 0.
    at = getattr(flow, 'contactAt', snapshot['sampledAt'])
    if all(value is not None and value['epoch'] == snapshot['instanceStartedAt']
           and 0 <= value['at'] - at <= MAX_SAMPLE_MS for value in motions):
        source = snapshot[actors.positionName(snapshot, attacker)]
        delta = [b - a for a, b in zip(source, feet)]
        distance = math.hypot(*delta)
        if distance > .01:
            closing = max(0., sum((a - b) * axis / distance
                                 for a, b, axis in zip(motions[0]['velocity'], motions[1]['velocity'], delta)))
    bonus = min(MAX_SPEED_BONUS, closing * SPEED_BONUS_PER_MPS)
    return max(1, math.floor(base * multiplier * (1 + bonus) + .5)), part, closing
