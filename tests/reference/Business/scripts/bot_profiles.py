"""Combat priorities and per-actor pacing, independent of weapon animation timing."""
from dataclasses import dataclass, replace

from . import actors


@dataclass(frozen=True)
class Profile:
    attackDelayMs: int
    observationMs: int
    guardMs: int
    counterMs: int
    canDefend: bool = True


PROFILES = {
    'aggressive': Profile(120, 240, 300, 120),
    'balanced': Profile(220, 150, 450, 150),
    'defensive': Profile(400, 90, 650, 90),
    'berserker': Profile(0, 240, 0, 0, canDefend=False),
}


def get(value):
    profile = PROFILES[value.get('profile', 'balanced')]
    tempo = value.get('tempo', 100) / 100
    return replace(profile, attackDelayMs=round(profile.attackDelayMs * tempo),
                   observationMs=round(profile.observationMs * tempo), guardMs=round(profile.guardMs * tempo),
                   counterMs=round(profile.counterMs * tempo))


def rhythm(flow, actorId, minimum, maximum):
    """Draw only when committing a choice; each actor owns a replayable sequence."""
    value = actors.data(flow, actorId)
    seed = value.get('rhythmSeed', (flow.session['instanceStartedAt'] ^ actorId * 2654435761 ^ 0xA341316C) & 0xFFFFFFFF)
    seed = (1664525 * seed + 1013904223) & 0xFFFFFFFF
    value['rhythmSeed'] = seed
    return minimum + (seed >> 8) % (maximum - minimum + 1)


def vary(flow, actorId, duration, low, high):
    return rhythm(flow, actorId, round(duration * low), round(duration * high))


def initialize(value, actorId, ordinal, spawns, seed):
    own = spawns[value['camp']]
    other = spawns[3 - value['camp']]
    centers = [tuple(sum(point[0][d] for point in group) / len(group) for d in range(3))
               for group in (own, other)]
    dx, dy = centers[1][0] - centers[0][0], centers[1][1] - centers[0][1]
    distance = max(1., (dx * dx + dy * dy) ** .5)
    lane = (ordinal + ordinal // len(PROFILES)) % 3 - 1
    individuality = (seed ^ (actorId * 2654435761)) & 0xFFFFFFFF
    along = ((individuality >> 8) % 9 - 4) / distance
    side = (lane * 10 + (individuality % 5 - 2)) / distance
    anchor = [(centers[0][0] + centers[1][0]) / 2 + dx * along - dy * side,
              (centers[0][1] + centers[1][1]) / 2 + dy * along + dx * side,
              (centers[0][2] + centers[1][2]) / 2]
    value.update(profile=tuple(PROFILES)[(actorId - 2) % len(PROFILES)], tempo=85 + individuality % 31,
                 lane=lane, patrolAnchor=anchor, routeSeed=individuality, rhythmSeed=individuality ^ 0xA341316C)


def validState(value):
    return (type(value.get('profile', 'balanced')) is str and value.get('profile', 'balanced') in PROFILES
            and type(value.get('tempo', 100)) is int and 85 <= value.get('tempo', 100) <= 115
            and type(value.get('lane', 0)) is int and value.get('lane', 0) in (-1, 0, 1)
            and type(value.get('rhythmSeed', 0)) is int and 0 <= value.get('rhythmSeed', 0) <= 0xFFFFFFFF
            and ('patrolAnchor' not in value or actors.validPosition(value['patrolAnchor'])))
