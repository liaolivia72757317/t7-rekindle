from copy import deepcopy
import struct

import pytest

from Business.scripts import actors, app, battle, body_collision, bot, combat, controls, player_life, roster, scene
from Business.scripts.codec import battle_flow, move_flow
from test_bot_respawn import kill, tick
from test_bot_roster import multi, sample


def choose(flow, slot=1):
    return scene.message(flow, 0x36, 0x32, struct.pack('>Hi', 0x32, slot))


def play(flow):
    return scene.message(flow, 0x36, 0x64, struct.pack('>HHb', 0x64, 1, 0))


def select(flow):
    flow.session['loaded'] = True
    kill(flow, 1)
    tick(flow, player_life.SELECT_TIMER, flow.now + 8000)
    choose(flow)


def test_player_death_cancels_only_player_actions_and_counts_one_kill(multi):
    multi.session.update(killStreak=3, kills=4, battlePoints=400)
    bot.start(multi)
    battle.startAction(multi, 300560)
    multi.later(combat.hurtTimer(1), 400)
    multi.later('ground-step', 50)
    kill(multi, 1)
    life = multi.session['life']
    assert life == dict(state='dead', epoch=100, diedAt=1000, respawnAt=9000)
    assert not actors.active(multi, 1) and not multi.session['heroChosen']
    assert multi.session['killStreak'] == 0
    assert (multi.session['kills'], multi.session['battlePoints']) == (4, 400)
    assert actors.data(multi, 2)['kills'] == 1
    assert multi.session['pending'][player_life.SELECT_TIMER] == 9000
    assert actors.PULSE_TIMER in multi.session['pending'] and bot.THINK_TIMER in multi.session['pending']
    assert not any(key in multi.session['pending'] for key in (
        'ground-step', battle.ACTION_TIMER, combat.hitTimer(1), combat.hurtTimer(1)))
    sends = multi.result['send']
    dead = next(p for p in sends if p['reason'] == 'player-dead')
    before_dead = sends[sends.index(dead) - 1]
    assert before_dead['command'] == 2
    assert before_dead['body'] == move_flow.encode_move_notify_active(
        server_tick=multi.now - multi.session['instanceStartedAt'],
        target_instance_id=1, active=0)
    assert battle_flow.decode_battle_dead_notify(dead['body']).entity_rid == 1
    assert sends.index(next(p for p in sends if p['reason'] == 'combat-kill')) < sends.index(dead)
    assert any(p['body'] == scene.wire.actorState(1000, 9) for p in sends)
    results = [p for p in sends if p['command'] == 4 and p['body'][:2] == b'\0\x09']
    assert results == []
    before = deepcopy(multi.session)
    sends.clear()
    combat.applyHit(multi, 2, 1, actors.pair(sample(multi), 2, 1))
    battle.startAction(multi, 300560)
    controls.message(multi, 2, 52, struct.pack('>Hi6Bfff', 52, 1, 1, 0, 0, 0, 0, 0, 99., 99., 0.))
    assert multi.session == before and sends == []
    assert app.validateState(multi.state)


def test_dead_player_does_not_block_or_stop_other_actors(multi):
    kill(multi, 1)
    world = sample(multi)
    assert 1 not in actors.enemies(multi, 2, world)
    assert multi.session['spawnPosition'] not in body_collision.obstacles(multi, 2, world)
    assert battle.enabled(multi)
    bot.start(multi)
    decisionAt = max(due for name, due in multi.session['pending'].items() if name.startswith(bot.THINK_TIMER))
    tick(multi, actors.PULSE_TIMER, decisionAt)
    while bot.DECISION_TIMER in multi.session['pending']:
        app.handleTimer(multi, bot.DECISION_TIMER)
    assert actors.PULSE_TIMER in multi.session['pending']
    assert actors.data(multi, 12)['initialized']
    kill(multi, 12)
    assert actors.data(multi, 12)['life']['state'] == 'dead'
    assert actors.data(multi, 2)['kills'] == 2


def test_eight_seconds_enters_native_selection_without_auto_respawning(multi):
    multi.session['loaded'] = True
    kill(multi, 1)
    multi.result['send'].clear()
    # Neither the native UI request nor stale hero/play messages may skip the wait.
    scene.message(multi, 0xA, 0xB, b'\0\x0b\0')
    assert not multi.result['send']
    choose(multi)
    play(multi)
    assert all(struct.unpack_from('>i', p['body'], 2)[0] != 0 for p in multi.result['send'])
    assert not multi.session['heroChosen']
    multi.result['send'].clear()
    tick(multi, player_life.SELECT_TIMER, 6000)
    assert not multi.result['send'] and multi.session['life']['state'] == 'dead'
    tick(multi, player_life.SELECT_TIMER, 8999)
    assert not multi.result['send'] and multi.session['life']['state'] == 'dead'
    tick(multi, player_life.SELECT_TIMER, 9000)
    assert multi.session['life']['state'] == 'selecting'
    assert multi.session['health'] == 0
    assert [p['body'] for p in multi.result['send']] == [scene.wire.actorState(9000, 7)]
    scene.message(multi, 0xA, 0xB, b'\0\x0b\0')
    assert multi.result['send'][-1]['reason'] == 'instance-enter-choose-hero'
    play(multi)
    assert multi.session['life']['state'] == 'selecting'
    choose(multi)
    assert multi.session['heroChosen'] and multi.session['health'] == 0


@pytest.mark.parametrize('camp', [1, 2])
def test_confirmed_hero_respawns_at_own_infantry_point_and_restores_control(multi, respawnPoints, camp):
    multi.session['camp'] = camp
    roster.assemble(multi)
    multi.session.update(kills=5, battlePoints=500)
    select(multi)
    multi.result['send'].clear()
    play(multi)
    life = multi.session['life']
    assert life['state'] == 'spawning' and multi.session['health'] == 100
    assert (tuple(multi.session['spawnPosition']), multi.session['spawnHeading']) in respawnPoints[camp]
    assert not actors.active(multi, 1)
    assert multi.session['spawnPosition'] in body_collision.obstacles(multi, 2, sample(multi))
    packets = multi.result['send']
    removed = next(p for p in packets if p['reason'] == 'player-respawn-remove')
    added = next(p for p in packets if p['reason'] == 'player-respawn-add')
    assert packets.index(removed) < packets.index(added)
    assert struct.unpack_from('>IQH', added['body'], 10) == (10000, 1, 1)
    assert struct.unpack_from('>fff', added['body'], 47) == pytest.approx(multi.session['spawnPosition'])
    assert any(p['body'] == scene.wire.actorState(multi.now, 6) for p in packets)
    assert not any(p['command'] == 0xA for p in packets)
    count = sum(p['reason'] == 'player-respawn-add' for p in packets)
    play(multi)
    assert sum(p['reason'] == 'player-respawn-add' for p in packets) == count
    # A stale corpse sample cannot unlock combat before the recreated entity arrives.
    sample(multi, {1: ((-80, -80, .2), 0)})
    tick(multi, player_life.RESPAWN_TIMER, multi.now + 50)
    assert not actors.active(multi, 1)
    sample(multi)
    tick(multi, player_life.RESPAWN_TIMER, multi.now + 50)
    assert actors.active(multi, 1) and (multi.session['kills'], multi.session['battlePoints']) == (5, 500)
    assert move_flow.decode_move_notify_active(packets[-1]['body']).active == 1
    battle.startAction(multi, 300560)
    assert battle.actionState(multi)['index'] == 0 and app.validateState(multi.state)


def test_respawn_retries_stale_world_and_occupied_points(multi, respawnPoints):
    select(multi)
    multi.context['botWorld']['sampledAt'] -= 251
    play(multi)
    assert multi.session['life']['state'] == 'waiting'
    assert multi.session['pending'][player_life.RESPAWN_TIMER] == multi.now + 50
    placements = {rid: (point, heading) for rid, (point, heading) in enumerate(respawnPoints[1], 2)}
    sample(multi, placements)
    tick(multi, player_life.RESPAWN_TIMER, multi.now + 50)
    assert multi.session['health'] == 0
    placements[2] = ((-70, -70, .2), 0)
    sample(multi, placements)
    tick(multi, player_life.RESPAWN_TIMER, multi.now + 50)
    assert multi.session['health'] == 100
    assert tuple(multi.session['spawnPosition']) == respawnPoints[1][0][0]


@pytest.mark.parametrize('error', [ValueError('invalid resource'), OSError('missing resource')])
def test_respawn_resource_failure_waits_without_falling_back(multi, monkeypatch, error):
    select(multi)
    multi.session.pop('respawnPoints')
    def fail(directory):
        raise error
    monkeypatch.setattr(roster, 'loadRespawns', fail)
    play(multi)
    tick(multi, player_life.RESPAWN_TIMER, multi.now + 50)
    assert multi.session['health'] == 0
    assert multi.session['life']['state'] == 'waiting'
    assert not any(p['reason'] == 'player-respawn-add' for p in multi.result['send'])
    assert sum(log["message"].startswith('player-respawn-resource-') for log in multi.result['logs']) == 1


def test_unknown_hero_never_uses_infantry_spawn_or_changes_the_visible_roster(multi):
    select(multi)
    with pytest.raises(ValueError):
        choose(multi, 2)
    assert multi.session['heroId'] == 110001
    multi.session['heroId'] = 999999
    play(multi)
    assert multi.session['health'] == 0 and multi.session['life']['state'] == 'waiting'


def test_lifecycle_timer_ignores_another_instance_epoch(multi):
    kill(multi, 1)
    multi.session['instanceStartedAt'] += 1
    multi.result['send'].clear()
    tick(multi, player_life.SELECT_TIMER, 9000)
    assert multi.result['send'] == [] and multi.session['life']['state'] == 'dead'


def test_player_death_refresh_reload_leave_and_new_round(multi):
    kill(multi, 1)
    before = deepcopy(multi.state)
    assert app.migrateState(app.STATE_VERSION, before) == before
    assert app.migrateState(10, app.createState({})) == app.createState({})
    multi.result['send'].clear()
    scene.message(multi, 0xE, 7, struct.pack('>HiQb', 7, 1, 1, 1))
    assert multi.session['health'] == 0 and multi.session['pending'][player_life.SELECT_TIMER] == 9000
    assert not any(p['reason'] == 'combat-kill' for p in multi.result['send'])
    assert move_flow.decode_move_notify_active(multi.result['send'][-1]['body']).active == 0
    scene.leave(multi, struct.pack('>Hb', 2, 0))
    multi.result['send'].clear()
    tick(multi, player_life.SELECT_TIMER, 9000)
    assert not multi.result['send'] and set(multi.session['pending']) == {'logout'}
    combat.initialize(multi)
    assert actors.active(multi, 1) and multi.session['health'] == 100


@pytest.mark.parametrize('field,value', [('state', 'alive'), ('epoch', -1), ('diedAt', True),
                                        ('respawnAt', 9001)])
def test_invalid_player_lifecycle_is_rejected(multi, field, value):
    kill(multi, 1)
    multi.session['life'][field] = value
    assert not app.validateState(multi.state)


def test_reload_preserves_deadline_of_existing_five_second_life(multi):
    kill(multi, 1)
    multi.session['life']['respawnAt'] = 6000
    multi.session['pending'][player_life.SELECT_TIMER] = 6000
    before = deepcopy(multi.state)
    assert app.migrateState(app.STATE_VERSION, before) == before
    tick(multi, player_life.SELECT_TIMER, 5999)
    assert multi.session['life']['state'] == 'dead'
    tick(multi, player_life.SELECT_TIMER, 6000)
    assert multi.session['life']['state'] == 'selecting'
