from copy import deepcopy
import math

import pytest

from Business.scripts import actors, app, battle, bot, bot_footwork, combat, weapons
from Business.scripts.codec import move_flow
from Business.scripts.navigation import NavMesh
from test_bot_roster import multi
from test_bot_tactics import fight
from blade_fixture import contact, publish


def duel(flow, distance=1.9):
    fight(flow, {1: ((distance, 0, .2), 180), 2: ((0, 0, .2), 0)})
    value = actors.data(flow, 2)
    value.update(profile='aggressive', tempo=100)
    return value


def movement(flow):
    return move_flow.decode_move_bc_with_system_and_active(next(
        item['body'] for item in reversed(flow.result['send']) if item['reason'] == 'bot-move'))


def step(flow):
    flow.now += bot.MOVE_MS
    flow.context['botWorld']['sampledAt'] = flow.now
    bot.move(flow, 2)


@pytest.mark.parametrize('profile', ['aggressive', 'balanced', 'defensive'])
def test_healthy_bot_closes_inside_the_slash_reach_instead_of_circling_at_the_tip(multi, profile):
    value = duel(multi, 1.8)
    value['profile'] = profile
    pair = actors.pair(actors.world(multi), 2, 1)
    choice = bot_footwork.plan(multi, bot.navigationFor(multi), pair, 2)
    assert choice is not None and choice.mode == 'press'
    assert .8 < math.dist(choice.goal[:2], pair['player'][:2]) <= 1.3
    assert multi.session.get('health', 100) == 100


def test_attack_starts_without_cancelling_close_pursuit(multi):
    value = duel(multi)
    value['reactAt'] = multi.now
    bot.think(multi, 2)
    assert value['action']['intent'] in battle.ATTACK_CHAINS
    assert bot.MOVE_TIMER in multi.session['pending']
    previous = deepcopy(value['action'])
    deadline = multi.session['pending'][bot.ACTION_TIMER]
    step(multi)
    move = movement(multi)
    assert move.forward_back < 0 and move.current_velocity == 3000
    assert value['action'] == previous and multi.session['pending'][bot.ACTION_TIMER] == deadline
    assert value['position'] == [0, 0, .2]


@pytest.mark.parametrize('phase', range(4))
def test_attack_phases_replan_pursuit_without_restarting_the_swing(multi, phase):
    value = duel(multi, 3.5)
    battle.startAction(multi, 300550, released=phase != 1, actorId=2)
    value['action']['index'] = phase
    battle.syncState(multi, battle.ATTACK_CHAINS[300550][phase], 2)
    previous = deepcopy(value['action'])
    deadline = multi.session['pending'][bot.ACTION_TIMER]
    bot.think(multi, 2)
    assert value['path'] and value['path'][-1][0] > 1
    step(multi)
    assert movement(multi).forward_back < 0
    assert value['action'] == previous and multi.session['pending'][bot.ACTION_TIMER] == deadline
    multi.context['botWorld']['actors']['1']['position'][0] = 4.2
    multi.now += 100
    multi.context['botWorld']['sampledAt'] = multi.now
    previousGoal = list(value['path'][-1])
    bot.think(multi, 2)
    assert value['path'][-1][0] > previousGoal[0]
    assert value['action'] == previous


def test_low_health_bot_backpedals_while_starting_an_attack(multi):
    value = duel(multi, 1.2)
    value.update(health=40, reactAt=multi.now)
    bot.think(multi, 2)
    assert value['action']['intent'] in battle.ATTACK_CHAINS
    assert value['footwork']['mode'] == 'retreat'
    step(multi)
    move = movement(multi)
    assert move.direction_yaw == 0 and move.forward_back > 0
    assert move.current_velocity == 3000


@pytest.mark.parametrize('condition', ['attack', 'parry', 'hurt', 'parried', 'parry-success', 'terrain-rebound'])
def test_weapon_lock_preserves_movement_but_disables_running(multi, condition):
    value = duel(multi)
    value.update(moving=True, forwardMs=bot.RUN_DELAY_MS, path=[[5., 0, 0]], step=0, lastMoveAt=multi.now)
    if condition in ('attack', 'parry'):
        battle.startAction(multi, 300550 if condition == 'attack' else 300610, actorId=2)
    elif condition == 'hurt':
        value['hurtUntil'] = multi.now + 400
    else:
        value['action'] = dict(intent=300610 if condition == 'parry-success' else 300550,
                               index=-1, seq=1, released=True, reaction=condition)
    previous = deepcopy(value.get('action'))
    step(multi)
    assert movement(multi).current_velocity == 3000 and value['forwardMs'] == 0
    assert value.get('action') == previous and bot.MOVE_TIMER in multi.session['pending']


def test_hurt_think_can_back_off_without_starting_another_attack(multi):
    value = duel(multi, 1.2)
    value['hurtUntil'] = multi.now + combat.HURT_MS
    battle.syncState(multi, combat.HURT_STATE, 2, targetId=1)
    previous = deepcopy(value['action'])
    bot.think(multi, 2)
    assert value['status'] == 'hurt' and value['path'][-1][0] < 0
    step(multi)
    assert movement(multi).forward_back > 0 and value['action'] == previous


def test_recovery_backs_off_and_idle_can_close_again(multi):
    value = duel(multi, 1.4)
    value['profile'] = 'defensive'
    battle.startAction(multi, 300550, released=True, actorId=2)
    value['action']['index'] = 3
    bot.think(multi, 2)
    assert value['footwork']['mode'] == 'retreat'
    assert value['path'][-1][0] < -.4
    battle.resetAction(multi, 2)
    multi.context['botWorld']['actors']['1']['position'][0] = 2.4
    bot.think(multi, 2)
    assert value['footwork']['mode'] == 'press' and value['path'][-1][0] > .3


def test_backpedal_near_mesh_edge_uses_a_reachable_side_step(multi, monkeypatch):
    nav = NavMesh.fromPolygons([[(0, -5, 0), (10, -5, 0), (10, 5, 0), (0, 5, 0)]])
    monkeypatch.setattr(bot, 'navigationFor', lambda flow: nav)
    fight(multi, {1: ((1.5, 0, .2), 180), 2: ((.4, 0, .2), 0)})
    value = actors.data(multi, 2)
    value.update(health=40, reactAt=multi.now)
    bot.think(multi, 2)
    assert value['path']
    assert all(nav.project(point) is not None for point in value['path'])
    assert abs(value['path'][-1][1]) > .3


@pytest.mark.parametrize('loss', ['missing', 'dead', 'stale', 'epoch'])
def test_combat_movement_does_not_follow_invalid_target_samples(multi, loss):
    value = duel(multi, 1.2)
    value.update(health=40, reactAt=multi.now)
    bot.think(multi, 2)
    step(multi)
    world = multi.context['botWorld']
    if loss == 'missing': world['actors']['1']['ready'] = False
    if loss == 'dead': multi.session['life'] = dict(state='dead')
    if loss == 'stale': world['sampledAt'] -= 1000
    if loss == 'epoch': world['instanceStartedAt'] += 1
    multi.result['send'].clear()
    bot.move(multi, 2)
    assert not value['moving'] and bot.MOVE_TIMER not in multi.session['pending']
    assert 'footwork' not in value
    if loss == 'epoch':
        assert not multi.result['send']


def test_moving_combat_state_survives_reload_and_stops_on_leave(multi):
    value = duel(multi, 1.2)
    value.update(health=40, reactAt=multi.now)
    bot.think(multi, 2)
    assert value.get('footwork') is not None
    original = deepcopy(multi.state)
    assert app.migrateState(app.STATE_VERSION, original) == multi.state
    bot.stop(multi, 2)
    assert 'footwork' not in value and not value['path']


def test_successful_parry_keeps_both_bots_moving_without_preserving_their_swings(multi):
    world = fight(multi, {2: ((0, 0, .2), 0), 12: ((1.5, 0, .2), 180)}, target=12)
    battle.startAction(multi, 300610, actorId=12)
    app.handleTimer(multi, battle.actionTimer(12))
    battle.startAction(multi, 300550, released=True, actorId=2)
    for rid in (2, 12):
        actors.data(multi, rid).update(moving=True, forwardMs=bot.RUN_DELAY_MS)
        multi.later(actors.timerName(bot.MOVE_TIMER, rid), bot.MOVE_MS)
    interception = dict(contact(12, defenceWeaponId=weapons.equipped(actors.data(multi, 12))),
                        occurredAt=multi.now)
    assert combat.parry(multi, 2, 12, actors.pair(world, 2, 12), contact=interception)
    for rid, reaction in ((2, 'parried'), (12, 'parry-success')):
        value = actors.data(multi, rid)
        assert value['moving'] and value['forwardMs'] == bot.RUN_DELAY_MS
        assert actors.timerName(bot.MOVE_TIMER, rid) in multi.session['pending']
        assert value['action']['reaction'] == reaction and value['action']['index'] == -1
        bot.broadcast(multi, True, actorId=rid)
        move = move_flow.decode_move_bc_with_system_and_active(multi.result['send'][-1]['body'])
        assert move.current_velocity == round(1000 * bot.JOG_SPEED)
        assert not any(item['reason'] == 'bot-stop' for item in multi.result['send'])


def test_pursuit_can_land_a_swing_while_both_actors_keep_moving(multi):
    value = duel(multi)
    value['reactAt'] = multi.now
    placements = {1: ([1.9, 0, .2], 180), 2: ([0, 0, .2], 0)}
    movingStrike = False
    for tick in range(45):
        fight(multi, placements)
        publish(multi, 2, [contact(1)])
        multi.result['send'].clear()
        if tick % 2 == 0:
            bot.think(multi, 2)
        due = sorted((at, name) for name, at in multi.session['pending'].items() if at <= multi.now)
        for _, name in due:
            app.handleTimer(multi, name)
        moves = [move_flow.decode_move_bc_with_system_and_active(item['body'])
                 for item in multi.result['send'] if item['reason'] == 'bot-move']
        if moves:
            move = moves[-1]
            movingStrike |= value['action'].get('index') == 2
            yaw = math.radians(move.direction_yaw)
            dx = -move.forward_back * math.cos(yaw) + move.left_right * math.sin(yaw)
            dy = move.forward_back * math.sin(yaw) + move.left_right * math.cos(yaw)
            length = math.hypot(dx, dy)
            scale = move.current_velocity / 1000 * .05 / length
            origin = placements[2][0]
            placements[2] = ([origin[0] + dx * scale, origin[1] + dy * scale, .2], move.direction_yaw)
        else:
            placements[2] = (placements[2][0], value['heading'])
        placements[1] = ([placements[1][0][0] + .05, 0, .2], 180)
        multi.now += 50
    assert movingStrike and placements[2][0][0] > 1
    assert multi.session['health'] < 100
    assert app.validateState(multi.state)


@pytest.mark.parametrize('field,invalid', [('targetId', 0), ('mode', 'invalid')])
def test_footwork_state_validation(multi, field, invalid):
    value = duel(multi)
    bot.think(multi, 2)
    assert app.validateState(multi.state)
    value['footwork'][field] = invalid
    assert not app.validateState(multi.state)
