from copy import deepcopy

import pytest

from Business.scripts import actors, app, battle, bot, combat, weapons
from blade_fixture import contact
from test_bot_roster import multi, sample
from test_melee_combat import states


ACTOR_IDS = (1, 2, 12, 20)


def phaseTimer(flow, actorId, delay):
    name = battle.actionTimer(actorId)
    changes = [timer for timer in flow.result['timers'] if timer['event']['name'] == name]
    assert changes, f'{name} must be scheduled independently of the AI pulse'
    assert changes[-1] == {
        'id': f'{flow.connection}:{name}', 'delayMs': delay,
        'event': {'type': 'timer', 'connection': flow.connection, 'name': name},
    }
    return changes[-1]['event']


def advancePhase(flow, actorId, delay):
    event = phaseTimer(flow, actorId, delay)
    flow.now = flow.session['pending'][event['name']]
    sample(flow)
    app.handleTimer(flow, event['name'])


@pytest.mark.parametrize('actorId', ACTOR_IDS)
@pytest.mark.parametrize('intent,chain', battle.BLOCK_CHAINS.items())
@pytest.mark.parametrize('earlyRelease', [False, True])
def test_guard_release_returns_directly_to_idle_for_every_actor(multi, actorId, intent, chain, earlyRelease):
    sample(multi)
    multi.later(actors.PULSE_TIMER, bot.MOVE_MS)
    pulseDue = multi.session['pending'][actors.PULSE_TIMER]
    battle.startAction(multi, intent, actorId=actorId)
    phaseTimer(multi, actorId, 0)
    if not earlyRelease:
        advancePhase(multi, actorId, 0)
        assert battle.actionState(multi, actorId)['index'] == 1
        assert battle.actionTimer(actorId) not in multi.session['pending']
    multi.now += 1
    battle.releaseAction(multi, battle.BLOCK_RELEASE, actorId)
    expected = (chain[0], 2) if earlyRelease else (*chain[:2], 2)
    assert states(multi) == [(actorId, state) for state in expected]
    assert multi.now < pulseDue == multi.session['pending'][actors.PULSE_TIMER]
    assert battle.actionState(multi, actorId)['index'] == -1
    assert battle.actionTimer(actorId) not in multi.session['pending']
    before = deepcopy(multi.result)
    battle.releaseAction(multi, battle.BLOCK_RELEASE, actorId)
    app.handleTimer(multi, battle.actionTimer(actorId))
    assert multi.result == before


@pytest.mark.parametrize('actorId', ACTOR_IDS)
@pytest.mark.parametrize('intent', battle.BLOCK_CHAINS)
@pytest.mark.parametrize('released', [False, True])
def test_success_between_ai_pulses_does_not_wait_or_replay_guard(multi, actorId, intent, released):
    sample(multi)
    multi.later(actors.PULSE_TIMER, bot.MOVE_MS)
    battle.startAction(multi, intent, actorId=actorId)
    advancePhase(multi, actorId, 0)
    multi.now += 17
    multi.result['send'].clear()
    battle.parryReaction(multi, actorId, 2 if actorId == 1 else 1, success=True)
    if released:
        battle.releaseAction(multi, battle.BLOCK_RELEASE, actorId)
    advancePhase(multi, actorId, 0)
    expected = 2 if released else battle.BLOCK_CHAINS[intent][1]
    assert states(multi) == [(actorId, battle.PARRY_SUCCESS_STATES[intent]), (actorId, expected)]
    assert multi.now < multi.session['pending'][actors.PULSE_TIMER]
    assert 'reaction' not in battle.actionState(multi, actorId)
    assert battle.actionTimer(actorId) not in multi.session['pending']


@pytest.mark.parametrize('actorId', ACTOR_IDS)
@pytest.mark.parametrize('intent', [*battle.ATTACK_CHAINS, battle.DASH_ATTACK])
def test_weapon_phases_keep_exact_shared_durations_without_ai_ticks(multi, actorId, intent):
    sample(multi)
    started = multi.now
    battle.startAction(multi, intent, released=True, actorId=actorId)
    dash = battle.actionState(multi, actorId).get('dash')
    chain = battle.attackChain(intent)
    indexes = [1] if intent == battle.DASH_ATTACK else [0, *range(battle.strikeIndex(intent), len(chain))]
    for index in indexes:
        assert states(multi)[-1] == (actorId, chain[index])
        advancePhase(multi, actorId, battle.phaseMs(intent, index, dash=dash))
    assert states(multi) == [(actorId, chain[index]) for index in indexes] + [(actorId, 2)]
    assert multi.now == started + sum(battle.phaseMs(intent, index, dash=dash) for index in indexes)
    assert battle.actionTimer(actorId) not in multi.session['pending']


@pytest.mark.parametrize('actorId', [2, 12, 20])
def test_stopping_a_bot_cancels_its_native_phase_and_late_event(multi, actorId):
    sample(multi)
    battle.startAction(multi, 300560, released=True, actorId=actorId)
    multi.now += 1
    bot.stop(multi, actorId)
    phaseTimer(multi, actorId, -1)
    before = deepcopy(multi.result)
    multi.now += 1000
    app.handleTimer(multi, battle.actionTimer(actorId))
    assert multi.result == before
    assert battle.actionState(multi, actorId)['index'] == -1


@pytest.mark.parametrize('intent', [300560, 300580])
def test_current_phase_deadline_survives_hot_reload(multi, intent):
    sample(multi)
    battle.startAction(multi, intent, released=intent in battle.ATTACK_CHAINS, actorId=20)
    multi.later(actors.PULSE_TIMER, bot.MOVE_MS)
    state = app.migrateState(app.STATE_VERSION, multi.state)
    restored = app.Flow({'connection': 1}, state, multi.context)
    name = battle.actionTimer(20)
    assert restored.session['pending'][name] == multi.session['pending'][name]
    assert not restored.result['timers']  # Current native timers survive revision replacement.
    restored.now = max(state['sessions']['1']['pending'].values()) + bot.MOVE_MS
    sample(restored)
    app.handleTimer(restored, actors.PULSE_TIMER)
    assert states(restored) == []
    assert name in restored.session['pending']
    app.handleTimer(restored, name)
    expected = 238 if intent == 300560 else 223
    assert states(restored) == [(20, expected)]
    if intent == 300560:
        phaseTimer(restored, 20, battle.phaseMs(intent, 2))
    before = deepcopy(restored.result['send'])
    app.handleTimer(restored, battle.actionTimer(20))
    assert restored.result['send'] == before


@pytest.mark.parametrize('actorId', [2, 12, 20])
@pytest.mark.parametrize('profile', ['aggressive', 'balanced', 'defensive'])
def test_defensive_success_releases_without_racing_the_next_think(multi, actorId, profile):
    placements = {1: ((0., 0., .2), 0), actorId: ((1.5, 0., .2), 180)}
    sample(multi, placements)
    actors.data(multi, actorId)['profile'] = profile
    battle.startAction(multi, 300610, actorId=actorId)
    advancePhase(multi, actorId, 0)
    releaseName = actors.timerName(bot.RELEASE_TIMER, actorId)
    multi.later(releaseName, 850)
    battle.startAction(multi, 300550, released=True)
    advancePhase(multi, 1, battle.phaseMs(300550, 0))
    snapshot = sample(multi, placements)
    multi.result['send'].clear()
    interception = dict(contact(actorId, defenceWeaponId=weapons.equipped(actors.data(multi, actorId))),
                        occurredAt=multi.now)
    assert combat.parry(multi, 1, actorId, actors.pair(snapshot, 1, actorId), contact=interception)
    assert states(multi) == [(1, 246), (actorId, 234)]
    action = battle.actionState(multi, actorId)
    assert action['released'] == (profile == 'defensive')
    assert (releaseName in multi.session['pending']) == (profile != 'defensive')
    advancePhase(multi, actorId, 0)
    assert states(multi)[-1] == (actorId, 2 if profile == 'defensive' else 226)
    assert action['index'] == (-1 if profile == 'defensive' else 1)
