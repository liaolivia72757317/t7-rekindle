from damage_fixture import NORMAL_DAMAGE
from copy import deepcopy
from pathlib import Path
import math
import struct
import xml.etree.ElementTree as ET

import pytest

from Business.runtime import host_runtime
from Business.scripts import actors, app, battle, body_collision, bot, combat, roster, scene, spawn_navigation, spawn_resource
from Business.scripts.codec import battle_flow, move_flow
from test_bot_roster import multi, sample, spawnXml
from test_melee_combat import advance, swing
from blade_fixture import contact, publish


def respawnTimer(rid):
    return actors.timerName('bot-respawn', rid)


def kill(flow, target=2):
    attacker = 1 if actors.data(flow, target)['camp'] != flow.session['camp'] else 2
    actors.data(flow, target)['health'] = NORMAL_DAMAGE
    world = sample(flow)
    combat.applyHit(flow, attacker, target, actors.pair(world, attacker, target))


def tick(flow, name, now, *, fresh=True):
    flow.now = now
    if fresh:
        flow.context['botWorld']['sampledAt'] = now
    app.handleTimer(flow, name)


def test_respawn_resource_uses_only_enabled_infantry_revive_points():
    root = spawnXml()
    points = spawn_resource.parseRespawns(ET.tostring(root))
    assert {camp: len(values) for camp, values in points.items()} == {1: 14, 2: 12}
    assert points[1][0] == ((0., 20., .2), -90)
    assert points[2][-1] == ((11., 40., .2), 90)
    root[2].find('is_enable').set('value', '0')
    with pytest.raises(ValueError):
        spawn_resource.parseRespawns(ET.tostring(root))


@pytest.mark.parametrize('mutation', ['camp', 'count', 'direction', 'nonfinite', 'overlap'])
def test_invalid_respawn_resource_is_rejected(mutation):
    root = spawnXml()
    group = root[2]
    if mutation == 'camp':
        group.find('is_red').set('value', '1')
    elif mutation == 'count':
        group.find('point_num').set('value', '13')
    elif mutation == 'direction':
        group.find('points/dir').set('value', '0,0,0')
    elif mutation == 'nonfinite':
        group.find('points/pos').set('value', 'nan,0,0')
    else:
        group.findall('points')[1].find('pos').set('value', '0,20,0.2')
    with pytest.raises(ValueError):
        spawn_resource.parseRespawns(ET.tostring(root))


def test_lethal_hit_stops_only_dead_bot_and_schedules_exactly_five_seconds(multi):
    value = actors.data(multi, 2)
    value.update(moving=True, initialized=True, heightOffset=.2, forwardMs=500)
    bot.start(multi)
    for rid in (2, 3):
        battle.startAction(multi, 300560, released=True, actorId=rid)
    multi.later(bot.MOVE_TIMER, 50)
    multi.later(bot.RELEASE_TIMER, 300)
    multi.later(combat.hurtTimer(2), 400)
    kill(multi)
    assert value['health'] == 0 and value['status'] == 'dead'
    assert not value['moving'] and not value['path'] and value['forwardMs'] == 0
    assert multi.session['pending'][respawnTimer(2)] == multi.now + 5000
    assert value['life']['respawnAt'] == multi.now + 5000
    assert not any(name in multi.session['pending'] for name in (
        bot.THINK_TIMER, bot.MOVE_TIMER, bot.RELEASE_TIMER, battle.actionTimer(2),
        combat.hitTimer(2), combat.hurtTimer(2)))
    assert battle.actionTimer(3) in multi.session['pending']
    assert actors.PULSE_TIMER in multi.session['pending']
    notifications = [p for p in multi.result['send'] if p['command'] == 4 and p['body'][:2] == b'\0\x0a']
    assert len(notifications) == 1
    sends = multi.result['send']
    stops = [packet for packet in sends if packet['reason'] == 'bot-stop']
    assert len(stops) == 1
    stopped = move_flow.decode_move_bc_with_system_and_active(stops[0]['body'])
    assert (stopped.target_instance_id, stopped.state) == (2, 1)
    assert stopped.position == pytest.approx(value['position'])
    assert sends.index(stops[0]) < next(i for i, packet in enumerate(sends)
                                       if packet['reason'] == 'combat-kill') < sends.index(notifications[0])
    assert battle_flow.decode_battle_dead_notify(notifications[0]['body']).entity_rid == 2
    assert value['action']['state'] == 42
    results = [p for p in multi.result['send'] if p['command'] == 4 and p['body'][:2] == b'\0\x09']
    assert len(results) == 1
    result = battle_flow.decode_battle_result(results[0]['body'])
    assert (result.target_rid, result.target_result_data, result.target_life_state) == (
        2, -NORMAL_DAMAGE, battle_flow.LIFE_STATE_KILL)
    assert multi.result['send'].index(results[0]) < multi.result['send'].index(notifications[0])
    assert app.validateState(multi.state)


def test_corpse_cannot_act_take_damage_block_bodies_or_remain_cached_target(multi):
    kill(multi)
    value = actors.data(multi, 2)
    live = actors.data(multi, 12)
    live.update(opponentId=2, senseAt=multi.now + 300)
    world = sample(multi, {2: ((1, 0, .2), 180), 12: ((0, 0, .2), 0)})
    assert 2 not in actors.enemies(multi, 12, world)
    assert [1, 0, .2] not in body_collision.obstacles(multi, 12, world)
    assert bot.world(multi, 12) is None
    before = deepcopy(value)
    multi.result['send'].clear()
    battle.startAction(multi, 300560, actorId=2)
    bot.think(multi, 2)
    bot.move(multi, 2)
    combat.applyHit(multi, 1, 2, actors.pair(world, 1, 2))
    combat.applyHit(multi, 2, 1, actors.pair(world, 2, 1))
    assert value == before
    assert multi.session['health'] == 100
    assert multi.result['send'] == []


@pytest.mark.parametrize('camp', [1, 2])
@pytest.mark.parametrize('rid', [2, 5, 12, 13])
def test_five_second_respawn_uses_own_camp_and_preserves_identity(multi, respawnPoints, camp, rid):
    multi.session['camp'] = camp
    roster.assemble(multi)
    value = actors.data(multi, rid)
    identity = {key: value[key] for key in ('name', 'camp', 'profile', 'tempo', 'lane')}
    kill(multi, rid)
    due = multi.session['pending'][respawnTimer(rid)]
    multi.result['send'].clear()
    tick(multi, respawnTimer(rid), due - 1)
    assert value['health'] == 0 and not multi.result['send']
    tick(multi, respawnTimer(rid), due)
    assert value['health'] == 100 and value['life']['state'] == 'spawning'
    references = respawnPoints[identity['camp']]
    assert tuple(value['position']) not in [point for point, _ in references]
    assert min(math.dist(value['position'][:2], point[:2]) for point, _ in references) <= 32
    assert value['heading'] in [heading for _, heading in references]
    assert bot.navigationFor(multi).project(value['position']) is not None
    assert 'spawnExit' not in value
    assert all(value[key] == expected for key, expected in identity.items())
    packets = multi.result['send']
    removed = next(p for p in packets if p['reason'] == 'bot-respawn-remove')
    added = next(p for p in packets if p['reason'] == 'bot-respawn-add')
    assert packets.index(removed) < packets.index(added)
    assert struct.unpack_from('>HiiQ', removed['body']) == (2, 1, 1, rid)
    assert struct.unpack_from('>IQH', added['body'], 10) == (10000 + rid - 1, rid, rid)
    assert struct.unpack_from('>fff', added['body'], 47) == pytest.approx(value['position'])
    count = len(packets)
    app.handleTimer(multi, respawnTimer(rid))
    assert len(packets) == count
    # The in-flight old corpse pose must not pull the newly created actor back.
    tick(multi, actors.timerName(bot.THINK_TIMER, rid), due + 100)
    assert value['life']['state'] == 'spawning' and not value['initialized']
    multi.now += 100
    sample(multi)
    app.handleTimer(multi, actors.timerName(bot.THINK_TIMER, rid))
    assert 'life' not in value and value['initialized']
    assert app.validateState(multi.state)


def test_same_pulse_respawns_reserve_distinct_free_points(multi, respawnPoints):
    for rid in (*range(12, 21), *range(2, 12)):
        kill(multi, rid)
    due = multi.now + 5000
    sample(multi)
    multi.result = dict(state=multi.state, send=[], timers=[], logs=[])
    for rid in range(2, 21):
        tick(multi, respawnTimer(rid), due)
    values = list(actors.bots(multi.session).values())
    positions = [value['position'] for value in values]
    assert all(value['health'] == 100 for value in values)
    assert all(math.dist(a, b) >= body_collision.DIAMETER for i, a in enumerate(positions) for b in positions[i+1:])
    assert all(len(multi.result[key]) <= 256 for key in ('send', 'timers', 'logs'))
    assert all(t['event']['name'] == actors.PULSE_TIMER for t in multi.result['timers'])


def test_respawn_waits_for_fresh_world_and_ignores_other_instance(multi):
    kill(multi)
    due = multi.now + 5000
    multi.result['send'].clear()
    tick(multi, respawnTimer(2), due, fresh=False)
    assert actors.data(multi, 2)['health'] == 0 and not multi.result['send']
    assert multi.session['pending'][respawnTimer(2)] > due
    multi.context['botWorld']['instanceStartedAt'] += 1
    tick(multi, respawnTimer(2), due + 50)
    assert actors.data(multi, 2)['health'] == 0 and not multi.result['send']


def test_occupied_respawns_wait_then_choose_the_free_own_camp_point(multi, respawnPoints, monkeypatch):
    kill(multi)
    points = tuple(((point[0] + .8, point[1], point[2]), heading)
                   for point, heading in respawnPoints[actors.data(multi, 2)['camp']])
    monkeypatch.setattr(spawn_navigation, 'candidates', lambda nav, references, anchor: tuple((point,) for point in points))
    blockers = (1, *range(3, 14))
    placements = dict(zip(blockers, points))
    due = multi.now + 5000
    sample(multi, placements)
    multi.result['send'].clear()
    tick(multi, respawnTimer(2), due)
    assert actors.data(multi, 2)['health'] == 0 and not multi.result['send']
    assert multi.session['pending'][respawnTimer(2)] == due + 50
    placements[1] = ((-50, -50, .2), 0)
    sample(multi, placements)
    tick(multi, respawnTimer(2), due + 50)
    assert actors.data(multi, 2)['position'] == list(points[0][0])
    assert actors.data(multi, 2)['health'] == 100


@pytest.mark.parametrize('attacker,target', [(1, 2), (2, 12), (12, 2)])
def test_actual_contact_kills_and_corpse_does_not_consume_next_swing(multi, attacker, target):
    actors.data(multi, target)['health'] = NORMAL_DAMAGE
    other = 3 if target == 2 else 13
    placements = {attacker: ((0, 0, .2), 0), target: ((1, 0, .2), 180), other: ((1.8, 0, .2), 180)}
    sample(multi, placements)
    publish(multi, attacker, [contact(target)])
    swing(multi, attacker)
    advance(multi, combat.hitTimer(attacker))
    assert actors.data(multi, target)['life']['state'] == 'dead'
    deadline = multi.session['pending'][respawnTimer(target)]
    sample(multi, placements)
    publish(multi, attacker, [contact(target), contact(other)])
    swing(multi, attacker)
    advance(multi, combat.hitTimer(attacker))
    assert actors.data(multi, other)['health'] == 80
    assert multi.session['pending'][respawnTimer(target)] == deadline
    assert sum(p['reason'] == 'bot-dead' for p in multi.result['send']) == 1


@pytest.mark.parametrize('key,value', [('state', 'alive'), ('epoch', -1), ('diedAt', 'now'),
                                       ('respawnAt', 0)])
def test_invalid_life_state_is_rejected(multi, key, value):
    kill(multi)
    actors.data(multi, 2)['life'][key] = value
    assert not app.validateState(multi.state)


def test_old_revision_requires_restart_and_current_spawning_state_can_reload(multi):
    with pytest.raises(ValueError, match='restart Runtime'):
        app.migrateState(6, multi.state)
    kill(multi)
    tick(multi, respawnTimer(2), multi.now + 5000)
    assert actors.data(multi, 2)['life']['state'] == 'spawning'
    assert app.validateState(multi.state)
    assert app.migrateState(app.STATE_VERSION, multi.state) == multi.state
    actors.data(multi, 2)['health'] = 'invalid'
    assert not app.validateState(multi.state)


def test_death_state_and_deadline_survive_reload_and_vision_refresh_then_leave(multi, tmp_path):
    kill(multi)
    original = deepcopy(multi.state)
    runtime = host_runtime.Runtime(Path(__file__).resolve().parents[2] / 'tests/reference/Business/scripts', tmp_path / 'revisions')
    try:
        runtime.prepare()
        assert host_runtime.decode(runtime.switch(host_runtime.encode(original))) == original
    finally:
        runtime.close()
    due = multi.session['pending'][respawnTimer(2)]
    multi.result['send'].clear()
    scene.message(multi, 0xE, 7, struct.pack('>HiQb', 7, 1, 2, 1))
    assert any(p['command'] == 4 and p['body'][:2] == b'\0\x0a' for p in multi.result['send'])
    assert actors.data(multi, 2)['health'] == 0
    assert multi.session['pending'][respawnTimer(2)] == due
    scene.leave(multi, struct.pack('>Hb', 2, 0))
    multi.result['send'].clear()
    tick(multi, respawnTimer(2), due)
    assert multi.result['send'] == [] and set(multi.session['pending']) == {'logout'}
