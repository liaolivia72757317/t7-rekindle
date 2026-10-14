import pytest

from Business.scripts import actors, combat, damage
from Business.scripts.codec import battle_flow
from test_melee_combat import arena, hp, swing


@pytest.mark.parametrize('height,expected', [(.2, 15), (.6, 20), (1., 20), (1.5, 30), (1.8, 30), (3., 20)])
def test_body_region_changes_actual_health(arena, height, expected):
    swing(arena)
    raw = actors.world(arena)
    pair = actors.pair(raw, 1, 2)
    feet = pair[actors.positionName(pair, 2)]
    contact = dict(position=[feet[0], feet[1], feet[2] + height],
                   direction=[1., 0., 0.], normal=[-1., 0., 0.])
    combat.applyHit(arena, 1, 2, pair, contact=contact)
    assert hp(arena, 2) == 100 - expected
    packet = next(p for p in arena.result['send'] if p['reason'] == 'combat-hit-point')
    part = 1 if 1.5 <= height <= 1.8 else 3 if 0 <= height < .6 else 2
    assert battle_flow.decode_battle_hit_point_notify(packet['body']).hit_part == part
    result = next(p for p in arena.result['send'] if p['reason'] == 'combat-damage')
    assert battle_flow.decode_battle_result(result['body']).target_rb_index == part


@pytest.mark.parametrize('source,target,expected', [([5, 0, 0], [5, 0, 0], 20),
    ([5, 0, 0], [0, 0, 0], 25), ([0, 0, 0], [-5, 0, 0], 25),
    ([-5, 0, 0], [0, 0, 0], 20), ([0, 5, 0], [0, 0, 0], 20),
    ([25, 0, 0], [-25, 0, 0], 30)])
def test_closing_speed_and_cap(arena, source, target, expected):
    raw = actors.world(arena)
    pair = actors.pair(raw, 1, 2)
    for rid, velocity in ((1, source), (2, target)):
        actors.data(arena, rid)['damageMotion'] = dict(position=[0, 0, 0],
            at=pair['sampledAt'], epoch=pair['instanceStartedAt'], velocity=velocity)
    feet = pair[actors.positionName(pair, 2)]
    result = damage.calculate(arena, 1, 2, pair, [feet[0], feet[1], feet[2] + 1], 20)
    assert result[0] == expected


def test_samples_ignore_teleports_stale_frames_and_new_life(arena):
    damage.sample(arena)
    raw = actors.world(arena)
    raw['sampledAt'] += 50
    arena.now += 50
    raw['player'][0] += .25
    damage.sample(arena)
    assert arena.session['damageMotion']['velocity'] == [5., 0., 0.]
    damage.sample(arena)
    assert arena.session['damageMotion']['velocity'] == [5., 0., 0.]
    for elapsed, displacement in ((50, 100), (200, .25)):
        raw['sampledAt'] += elapsed
        arena.now += elapsed
        raw['player'][0] += displacement
        damage.sample(arena)
        assert arena.session['damageMotion']['velocity'] == [0., 0., 0.]
    arena.session['contactLifeAt'] = raw['sampledAt'] + 1
    raw['sampledAt'] += 50
    arena.now += 50
    raw['player'][0] += .25
    damage.sample(arena)
    assert arena.session['damageMotion']['velocity'] == [0., 0., 0.]


def test_stale_motion_does_not_add_damage(arena):
    raw = actors.world(arena)
    pair = actors.pair(raw, 1, 2)
    for rid in (1, 2):
        actors.data(arena, rid)['damageMotion'] = dict(position=[0, 0, 0],
            at=pair['sampledAt'] - 200, epoch=pair['instanceStartedAt'], velocity=[5, 0, 0])
    assert damage.calculate(arena, 1, 2, pair, [1.5, 0., 1.2], 20)[0] == 20
