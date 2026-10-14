from copy import deepcopy
import struct

import pytest

from Business.runtime import host_runtime
from Business.scripts import actors, app, bot_life, combat, contracts as wire, player_life, roster, scene
from Business.scripts.codec import kill_flow
from test_bot_roster import multi, sample
from test_contact_journal import native
from test_melee_combat import advance
from test_melee_multi_hit import attack, cluster
from test_round_timeout import clear, finish, settle


KILL_TARGET_KEY = -1610395699  # int32 representation of the kill_total name key.


def start(flow):
    scene.begin(flow)
    flow.session.update(battleEntered=True, heroChosen=True, loaded=True)
    scene.startRound(flow)
    flow.now = flow.session['pending']['round-game']
    app.handleTimer(flow, 'round-game')
    sample(flow)
    clear(flow)
    return flow


@pytest.fixture
def targetGame(multi):
    multi.context['gameDurationMs'] = 60000
    return start(multi)


def lethalHit(flow, attacker, target):
    actors.data(flow, target)['health'] = combat.DAMAGE
    combat.applyHit(flow, attacker, target, actors.pair(sample(flow), attacker, target))


def scriptItems(flow):
    packet = next(p for p in reversed(flow.result['send']) if p['reason'] == 'combat-camp-score')
    selector, _, mid, count = struct.unpack_from('>HQQi', packet['body'])
    assert (selector, mid) == (1, 1)
    assert len(packet['body']) == 22 + 12 * count
    return dict(struct.iter_unpack('>iq', packet['body'][22:]))


@pytest.mark.parametrize('target', [1, 60, 2**31 - 1])
def test_target_wire_uses_signed_name_key_and_int64_value(target):
    body = kill_flow.encode_camp_kill_update(server_time_ms=123, instance_id=1,
                                            blue_kills=59, red_kills=7, kill_target=target)
    assert body == struct.pack('>HQQi', 1, 123, 1, 3) + struct.pack(
        '>iqiqiq', 7346418, 59, 206159, 7, KILL_TARGET_KEY, target)


@pytest.mark.parametrize('value', [0, -1, True, 1.5, '60', 2**31])
def test_invalid_wire_target_is_rejected(value):
    with pytest.raises(ValueError, match='target'):
        kill_flow.encode_camp_kill_update(server_time_ms=0, instance_id=1,
                                          blue_kills=0, red_kills=0, kill_target=value)


@pytest.mark.parametrize('command,body', [(10, b'\0\x6a'), (35, b'\0\3\0')])
def test_new_round_and_refresh_publish_the_frozen_target(targetGame, command, body):
    assert wire.KILL_TARGET == targetGame.session['killTarget'] == 60
    scene.message(targetGame, command, int.from_bytes(body[:2], 'big'), body)
    assert scriptItems(targetGame) == {7346418: 0, 206159: 0, KILL_TARGET_KEY: 60}
    assert app.validateState(targetGame.state)


def test_config_change_only_affects_new_round_and_drives_both_hud_and_victory(targetGame, monkeypatch):
    monkeypatch.setattr(wire, 'KILL_TARGET', 3)
    scene.message(targetGame, 10, 106, b'\0j')
    assert scriptItems(targetGame)[KILL_TARGET_KEY] == 60
    saved = host_runtime.decode(host_runtime.encode(targetGame.state))
    assert app.migrateState(app.STATE_VERSION, saved) == targetGame.state
    start(targetGame)
    assert targetGame.session['killTarget'] == 3
    for target in (2, 3, 4):
        lethalHit(targetGame, 1, target)
        assert scriptItems(targetGame)[KILL_TARGET_KEY] == 3
        assert bool(targetGame.session.get('roundEnded')) == (target == 4)
    assert targetGame.state['settlement']['killTarget'] == 3
    assert targetGame.state['settlement']['campKills'] == {'1': 3, '2': 0}


@pytest.mark.parametrize('camp', [1, 2])
@pytest.mark.parametrize('attacker,target', [(1, 2), (12, 2), (2, 1), (2, 12)])
def test_sixtieth_camp_kill_finishes_after_death_before_respawn(targetGame, camp, attacker, target):
    flow = targetGame
    flow.session['camp'] = camp
    roster.assemble(flow)
    winner = actors.data(flow, attacker)['camp']
    teammate = 13 if attacker in (1, 12) else 3
    actors.data(flow, teammate).update(kills=58, battlePoints=5800)
    firstTarget = next(rid for rid in actors.ids(flow.session)
                       if actors.data(flow, rid)['camp'] != winner and rid != target)
    lethalHit(flow, attacker, firstTarget)
    assert not flow.session.get('roundEnded')
    assert sum(actors.data(flow, rid)['kills'] for rid in actors.ids(flow.session)
               if actors.data(flow, rid)['camp'] == winner) == 59
    clear(flow)
    lethalHit(flow, attacker, target)
    snapshot = flow.state['settlement']
    assert (snapshot['reason'], snapshot['result'], snapshot['killTarget']) == ('kill-target', winner, 60)
    assert snapshot['campKills'] == {str(winner): 60, str(3 - winner): 0}
    assert len(snapshot['members']) == 20
    assert all(m['result'] == (1 if m['camp'] == winner else 2) for m in snapshot['members'])
    reasons = [p['reason'] for p in flow.result['send']]
    death = 'player-dead' if target == 1 else 'bot-dead'
    assert reasons.index('combat-camp-score') < reasons.index(death) < reasons.index('settlement-score')
    assert reasons[-2:] == ['instance-round-result', 'instance-round-state-end']
    assert 'player-observer' not in reasons
    assert 'instance-settlement' not in reasons and 'instance-finish-game' not in reasons
    assert flow.result['send'][-2]['body'] == struct.pack('>HQQii', 0x68, flow.now, 1, winner, 1)
    assert flow.session['roundEnded'] and not flow.session['groundEnabled']
    assert flow.session['pending'] == {'round-finish': flow.now + 5000}
    assert [(t['id'], t['delayMs']) for t in flow.result['timers'] if t['delayMs'] >= 0] == [
        ('1:round-finish', 5000)]
    settle(flow)
    assert [p['reason'] for p in flow.result['send']][-2:] == ['instance-settlement', 'instance-finish-game']
    assert struct.unpack_from('>i', flow.result['send'][-1]['body'], 18)[0] == winner
    assert struct.unpack_from('>i', flow.result['send'][-2]['body'], len(flow.result['send'][-2]['body']) - 4)[0] == (
        1 if winner == camp else 2)
    assert not flow.session['pending']
    assert app.validateState(flow.state)


def test_dead_members_still_contribute_and_overshoot_also_finishes(targetGame):
    lethalHit(targetGame, 2, 12)
    actors.data(targetGame, 12).update(kills=60, battlePoints=6000)
    lethalHit(targetGame, 1, 2)
    snapshot = targetGame.state['settlement']
    assert snapshot['campKills'] == {'1': 61, '2': 1} and snapshot['result'] == 1
    assert next(m for m in snapshot['members'] if m['rid'] == 12)['kills'] == 60
    assert scriptItems(targetGame)[7346418] == 61


def test_friendly_bot_can_win_while_player_is_waiting_to_respawn(targetGame):
    lethalHit(targetGame, 2, 1)
    life = deepcopy(targetGame.session['life'])
    assert 'player-select' in targetGame.session['pending']
    actors.data(targetGame, 12)['kills'] = 59
    clear(targetGame)
    lethalHit(targetGame, 12, 3)
    assert targetGame.state['settlement']['result'] == 1
    assert targetGame.session['life'] == life
    assert targetGame.session['pending'] == {'round-finish': targetGame.now + 5000}
    assert targetGame.state['phase'] == 'settlement'
    settle(targetGame)
    assert targetGame.session['life'] == life and not targetGame.session['pending']
    assert app.validateState(targetGame.state)


@pytest.mark.parametrize('attacker', [1, 2])
def test_legacy_contact_sweep_stops_at_first_winning_kill(targetGame, attacker):
    flow = targetGame
    _, targets, _ = cluster(flow, attacker)
    actors.data(flow, attacker)['kills'] = 59
    for rid in targets:
        actors.data(flow, rid)['health'] = combat.DAMAGE
    attack(flow, attacker)
    clear(flow)
    advance(flow, combat.hitTimer(attacker))
    assert [actors.data(flow, rid)['health'] for rid in targets] == [0, combat.DAMAGE, combat.DAMAGE]
    assert actors.data(flow, attacker)['kills'] == 60
    assert flow.session['pending'] == {'round-finish': flow.now + 5000}
    assert [(t['id'], t['delayMs']) for t in flow.result['timers'] if t['delayMs'] >= 0] == [
        ('1:round-finish', 5000)]
    assert sum(p['reason'] == 'combat-kill' for p in flow.result['send']) == 1


@pytest.mark.parametrize('firstCamp,sameTime', [(1, False), (2, False), (1, True)])
def test_native_batch_freezes_first_winner_in_existing_contact_order(targetGame, firstCamp, sameTime):
    flow = targetGame
    for attacker, target in ((1, 2), (3, 12)):
        actors.data(flow, attacker)['kills'] = 59
        actors.data(flow, target)['health'] = combat.DAMAGE
        attack(flow, attacker)
    blue = native(flow, attacker=1, target=2, occurredAt=flow.now + (10 if firstCamp == 1 else 20))
    red = native(flow, attacker=3, target=12, occurredAt=flow.now + (20 if firstCamp == 1 else 10))
    if sameTime:
        red['occurredAt'] = blue['occurredAt']
    flow.context['botWorld']['contactJournal'].update(events=[red, blue], throughAt=flow.now + 40)
    flow.now += 100
    clear(flow)
    combat.worldUpdated(flow)
    assert flow.state['settlement']['result'] == firstCamp
    assert flow.state['settlement']['campKills'] == {str(firstCamp): 60, str(3 - firstCamp): 59}
    assert actors.data(flow, 12 if firstCamp == 1 else 2)['health'] == combat.DAMAGE
    assert sum(p['reason'] == 'combat-kill' for p in flow.result['send']) == 1
    assert flow.session['pending'] == {'round-finish': flow.now + 5000}
    assert not hasattr(flow, 'contactAt')
    assert app.validateState(flow.state)


@pytest.mark.parametrize('delay,reason,result', [(-1, 'kill-target', 1), (0, 'timeout', 3), (1, 'timeout', 3)])
def test_deadline_precedes_even_an_earlier_but_late_delivered_winning_contact(targetGame, delay, reason, result):
    flow = targetGame
    deadline = flow.session['pending']['round-end']
    flow.session['kills'] = actors.data(flow, 3)['kills'] = 59
    actors.data(flow, 2)['health'] = combat.DAMAGE
    flow.now = deadline - 1000
    sample(flow)
    attack(flow)
    native(flow, attacker=1, target=2)
    outcome = app.handleEvent({'type': 'world', 'connection': 1}, flow.state,
                             dict(flow.context, nowMs=deadline + delay, connections=[1]))
    assert outcome['state']['settlement']['reason'] == reason
    assert outcome['state']['settlement']['result'] == result
    assert outcome['state']['settlement']['campKills']['1'] == (60 if delay < 0 else 59)
    assert outcome['state']['sessions']['1']['pending'] == {'round-finish': deadline + delay + 5000}


def test_finished_target_round_rejects_late_damage_death_and_timers(targetGame):
    flow = targetGame
    flow.session['kills'] = 59
    pending = dict(flow.session['pending'])
    lethalHit(flow, 1, 2)
    before = deepcopy(flow.state)
    clear(flow)
    pair = actors.pair(sample(flow), 1, 3)
    combat.applyHit(flow, 1, 3, pair)
    bot_life.die(flow, 3, 1, actors.data(flow, 3)['position'])
    player_life.die(flow, 3, flow.session['spawnPosition'])
    combat.worldUpdated(flow)
    scene.finishRound(flow)
    flow.now += 100000
    for timer in (*pending, 'player-select', 'bot-respawn'):
        app.handleTimer(flow, timer)
    assert flow.state == before and not flow.result['send'] and not flow.result['timers']
    restored = app.migrateState(app.STATE_VERSION, host_runtime.decode(host_runtime.encode(flow.state)))
    assert restored == before
    scene.leave(flow, b'\0\2\0')
    assert flow.state['settlement'] == before['settlement']
    assert sum(p['reason'] == 'instance-finish-game' for p in flow.result['send']) == 1
    assert flow.session['pending'] == {'logout': flow.now + 250}


def test_old_live_round_keeps_time_only_rule_until_new_instance(multi):
    multi.session['battleClock'] = {'durationMs': 60000, 'startedAt': multi.now}
    multi.later('round-end', 60000)
    deadline = multi.session['pending']['round-end']
    multi.session['kills'] = actors.data(multi, 2)['kills'] = 70
    original = deepcopy(multi.state)
    restored = app.migrateState(13, multi.state)
    assert app.STATE_VERSION == 22 and restored == original
    flow = app.Flow({'connection': 1}, restored, multi.context)
    lethalHit(flow, 1, 2)
    assert not flow.session.get('roundEnded') and KILL_TARGET_KEY not in scriptItems(flow)
    assert flow.session['pending']['round-end'] == deadline
    finish(flow)
    settle(flow)
    assert flow.state['settlement']['reason'] == 'timeout' and 'killTarget' not in flow.state['settlement']
    oldFinished = deepcopy(flow.state)
    assert app.migrateState(13, oldFinished) == oldFinished
    context = dict(flow.context, nowMs=flow.now)
    closed = app.handleEvent({'type': 'closed', 'connection': 1}, flow.state, context)
    connected = app.handleEvent({'type': 'connected', 'connection': 1, 'role': 'instance'}, closed['state'], context)
    flow = app.Flow({'connection': 1}, connected['state'], context)
    start(flow)
    assert flow.session['killTarget'] == 60 and 'settlement' not in flow.state


@pytest.mark.parametrize('value', [None, 0, -1, True, 1.5, '60', 2**31])
def test_invalid_target_state_and_configuration_are_rejected(targetGame, monkeypatch, value):
    targetGame.session['killTarget'] = value
    assert not app.validateState(targetGame.state)
    monkeypatch.setattr(wire, 'KILL_TARGET', value)
    with pytest.raises(ValueError, match='target'):
        scene.begin(targetGame)
    assert not targetGame.result['send']


@pytest.mark.parametrize('mutation', ['missing-target', 'invalid-target', 'unreached-target', 'no-start', 'draw', 'quit'])
def test_invalid_target_settlement_is_rejected(targetGame, mutation):
    targetGame.session['kills'] = 59
    lethalHit(targetGame, 1, 2)
    snapshot = targetGame.state['settlement']
    if mutation == 'missing-target':
        snapshot.pop('killTarget')
    elif mutation in ('invalid-target', 'unreached-target'):
        snapshot['killTarget'] = True if mutation == 'invalid-target' else 61
    elif mutation == 'no-start':
        snapshot.pop('startedAt')
    else:
        snapshot['result'] = 3 if mutation == 'draw' else 0
        for member in snapshot['members']:
            member['result'] = snapshot['result']
    assert not app.validateState(targetGame.state)
