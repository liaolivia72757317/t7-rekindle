"""Damage uses the frozen swing and native event, not dispatch time or current equipment."""
from copy import deepcopy
from pathlib import Path
import struct

import pytest

from Business.runtime import host_runtime
from Business.scripts import actors, app, battle, combat, contact_journal, damage_resources, damage_rules, damage_state
from Business.scripts import loadouts, shields, weapons
from Business.scripts.codec import battle_flow
from test_bot_roster import multi, sample
from test_contact_journal import native
from test_melee_combat import advance, hp
from test_melee_multi_hit import attack
from test_loadouts import configured, resources
from test_weapon_switch import change

productionProfile = damage_resources.loadProfile


@pytest.fixture
def arena(multi, monkeypatch):
    monkeypatch.setattr(damage_state, 'RULES', damage_rules.DEFAULT_RULES)
    for rid in actors.ids(multi.session):
        data = actors.data(multi, rid)
        data['damageProfile'] = productionProfile(1101, (1030411, 100000))
        damage_state.reset(data)
    sample(multi, {1: ((0, 0, .2), 0), 2: ((1, 0, .2), 180)})
    return multi


def flat(flow, *, traits=(), base=40, cleave='high'):
    for rid in actors.ids(flow.session):
        value = actors.data(flow, rid)['damageProfile']
        value.update(armor=0, traits=list(traits) if rid == 1 else [])
        for weapon in value['weapons'].values():
            weapon.update(traits=[], cleave=cleave)
            for action in weapon['attacks'].values():
                action.update(base=base, damageType='slash')


def evidence(flow, attacker=1, target=2, **values):
    swing = actors.data(flow, attacker)['swing']
    at = flow.now + (swing['contactStartMs'] + swing['contactEndMs']) / 2
    event = native(flow, attacker, target, occurredAt=at)
    event.update(values)
    return event


def settle(flow, events, delay=0):
    journal = flow.context['botWorld']['contactJournal']
    journal.update(events=events, throughAt=max(event['occurredAt'] for event in events) + 1,
                   batchId=flow.session.get('contactCursor', [0, 0])[1] + 1)
    flow.now = int(journal['throughAt'] + delay)
    combat.worldUpdated(flow)


@pytest.mark.parametrize('attacker,target', [(1, 2), (2, 1)])
@pytest.mark.parametrize('region,expected', [('torso', 28), ('head', 42), ('legs', 21), ('unknown', 28)])
def test_original_armor_and_weapon_base_share_player_bot_pipeline(arena, attacker, target, region, expected):
    attack(arena, attacker)
    event = evidence(arena, attacker, target, region=region, bodyIndex=5)
    settle(arena, [event], delay=500)
    assert hp(arena, target) == 100 - expected
    if attacker == 1:
        packet = next(p for p in arena.result['send'] if p['reason'] == 'combat-damage')
        result = battle_flow.decode_battle_result(packet['body'])
        assert result.is_critical == (region == 'head')
        assert result.target_rb_index == {'head': 1, 'torso': 2, 'legs': 3}.get(region, 2)
    if region == 'unknown':
        assert any('combat-region-fallback' in log['message'] for log in arena.result['logs'])


@pytest.mark.parametrize('held', [0, 300, 600, 800, 1000, 1500])
def test_charge_starts_after_prepare_and_freezes_on_release(arena, held):
    battle.startAction(arena, 300560)
    advance(arena, battle.ACTION_TIMER)
    assert arena.session['action']['heldAt'] == arena.now
    arena.now += held
    battle.releaseAction(arena, battle.ATTACK_RELEASE)
    assert arena.session['swing']['damageContext']['heldMs'] == held
    event = evidence(arena)
    settle(arena, [event], delay=2500)
    expected = damage_rules.calculate(damage_rules.Hit(base=46, damageType='slash', armor=63, heldMs=held)).hpDamage
    assert hp(arena, 2) == 100 - expected


@pytest.mark.parametrize('intent', [300560, battle.DASH_ATTACK])
def test_early_release_and_dash_do_not_charge(arena, intent):
    attack(arena, intent=intent)
    context = arena.session['swing']['damageContext']
    assert context['heldMs'] == 0 and context['base'] == 46


def test_delayed_contact_keeps_weapon_type_rules_and_release_snapshot(arena):
    attack(arena)
    event = evidence(arena)
    before = deepcopy(arena.session['swing']['damageContext'])
    arena.session['weaponId'] = weapons.FIST_ID
    arena.session['damageProfile']['weapons']['1030411']['attacks']['300560']['base'] = 9999
    settle(arena, [event], delay=2000)
    assert hp(arena, 2) == 72
    assert arena.session['swing']['damageContext'] == before


@pytest.mark.parametrize('allies,losses', [(0, [40, 32, 24, 16, 0]), (1, [16, 13, 10, 6, 0]), (2, [2, 2, 1, 1, 0])])
def test_friendly_contacts_are_unique_decay_and_do_not_interrupt_heavy_armor(arena, allies, losses):
    flat(arena)
    for rid in range(2, 7):
        actors.data(arena, rid)['damageProfile']['armorType'] = 'heavy'
    attack(arena)
    events = [evidence(arena, target=rid, along=index / 10) for index, rid in enumerate(range(12, 12 + allies))]
    if events:
        events.append(deepcopy(events[0]))
    events += [evidence(arena, target=rid, along=.3 + index / 10) for index, rid in enumerate(range(2, 7))]
    actions = {rid: deepcopy(battle.actionState(arena, rid)) for rid in range(2, 7)}
    settle(arena, events)
    assert [100 - hp(arena, rid) for rid in range(2, 7)] == losses
    assert arena.session['swing']['allies'] == allies
    assert all(hp(arena, rid) == 100 for rid in range(12, 12 + allies))
    if allies:
        assert all(actors.data(arena, rid).get('action', {}) == actions[rid] for rid in range(2, 7))
        assert all('hurtUntil' not in actors.data(arena, rid) for rid in range(2, 7))
        packets = [p for p in arena.result['send'] if p['reason'] == 'combat-damage']
        assert all(battle_flow.decode_battle_result(p['body']).target_result_type == 6 for p in packets)


@pytest.mark.parametrize('continuous,expected', [(True, 69), (False, 46)])
def test_event_speed_and_airborne_use_retained_evidence(arena, continuous, expected):
    flat(arena, traits=('light',))
    attack(arena)
    event = evidence(arena, airborne=True, velocityReady=continuous,
                     sourceVelocity=[1., 0., 0.], targetVelocity=[-1., 0., 0.], weaponVelocity=[3., 0., 0.])
    settle(arena, [event], delay=4000)
    assert hp(arena, 2) == 100 - expected


def test_hit_proc_applies_only_to_next_swing_and_expires_at_event_time(arena):
    flat(arena, traits=('combo',))
    attack(arena)
    event = evidence(arena)
    settle(arena, [event])
    assert hp(arena, 2) == 60 and arena.session['swing']['damageContext']['buffs'] == {}
    first = arena.session['damageBuffs']['combo']
    attack(arena)
    event = evidence(arena, target=3)
    settle(arena, [event], delay=5000)
    assert hp(arena, 3) == 52  # Delivery after expiry must not expire an earlier hit.
    assert arena.session['damageBuffs']['combo']['until'] == event['occurredAt'] + 3000
    assert arena.session['damageBuffs']['combo']['multiplier'] == first['multiplier']
    attack(arena)
    event = evidence(arena, target=4)
    settle(arena, [event])
    assert hp(arena, 4) == 60


def test_late_proc_corrects_later_snapshot_but_not_producing_swing(arena):
    flat(arena, traits=('combo',))
    attack(arena)
    first = arena.session['swing']
    event = evidence(arena)
    arena.now = int(event['occurredAt'] + 10)
    attack(arena)
    current = arena.session['swing']
    damage_state.grant(arena, 1, 'combo', event['occurredAt'])
    assert first['damageContext']['buffs'] == {}
    assert current['damageContext']['buffs']['combo']['since'] == event['occurredAt']


def test_natural_one_hit_kill_reports_actual_hp_and_awards_once(arena):
    flat(arena, base=1000)
    actors.data(arena, 2)['health'] = 7
    attack(arena)
    event = evidence(arena, region='head')
    settle(arena, [event, deepcopy(event)])
    assert hp(arena, 2) == 0 and arena.session['kills'] == 1
    result = battle_flow.decode_battle_result(next(p['body'] for p in arena.result['send'] if p['reason'] == 'combat-damage'))
    assert result.target_result_data == -7 and result.target_life_state == battle_flow.LIFE_STATE_KILL


def test_shield_break_absorbs_hit_disables_switch_reuse_and_resets_on_new_life(multi, resources):
    configured(multi, resources)
    sample(multi, {1: ((0, 0, .2), 0), 2: ((1, 0, .2), 180)})
    multi.session['shieldHealth']['703'] = 5
    battle.startAction(multi, battle.SHIELD_GUARD)
    advance(multi, battle.ACTION_TIMER)
    attack(multi, 2)
    event = evidence(multi, 2, 1, targetState=battle.GUARD_CHAINS[battle.SHIELD_GUARD][1], region='head',
                     defenceWeaponId=multi.session['offhandId'])
    settle(multi, [event])
    assert hp(multi, 1) == 100 and multi.session['shieldHealth']['703'] == 0
    assert multi.session['offhandId'] == 0
    statePacket = next(p['body'] for p in reversed(multi.result['send']) if p['reason'] == 'battle-model-state-41')
    assert struct.unpack_from('>H', statePacket, 6)[0] == shields.BREAK_STATE
    hpPacket = next(p['body'] for p in multi.result['send'] if p['reason'] == 'combat-shield-health')
    assert struct.unpack('>Hihii', hpPacket) == (6, 703, 3, 0, 100)
    assert next(p['body'] for p in multi.result['send'] if p['reason'] == 'combat-shield-break') == struct.pack('>HQi', 5, 1, 703)
    advance(multi, combat.hurtTimer(1))
    change(multi, 2)
    advance(multi, battle.ACTION_TIMER)
    change(multi, 3)
    assert multi.session['offhandId'] == 0 and multi.session['shieldHealth']['703'] == 0
    loadouts.resetWeapons(multi)
    assert multi.session['shieldHealth']['703'] == 100 and multi.session['offhandId'] == 703
    assert app.validateState(multi.state)


def test_state_roundtrip_and_old_active_round_rejection(arena):
    attack(arena)
    assert app.validateState(arena.state)
    state = host_runtime.decode(host_runtime.encode(arena.state))
    assert app.migrateState(19, state) == arena.state
    state['sessions']['1']['initialized'] = True
    saved = deepcopy(state)
    for oldVersion in (17, 18):
        with pytest.raises(ValueError, match='restart Runtime'):
            app.migrateState(oldVersion, state)
    assert state == saved
    lobby = app.createState({})
    assert app.migrateState(17, lobby) == lobby


@pytest.mark.parametrize('changes', [dict(bodyIndex=128), dict(region='face'), dict(velocityReady=1),
    dict(airborne=None), dict(sourceVelocity=[float('nan'), 0, 0])])
def test_invalid_native_evidence_rejected(arena, changes):
    attack(arena)
    event = evidence(arena, **changes)
    assert not contact_journal.valid(event, event['occurredAt'] + 1)


def test_old_runtime_evidence_requires_restart(arena):
    attack(arena)
    event = evidence(arena)
    arena.context['botWorld']['contactJournal'].pop('damageEvidenceVersion')
    with pytest.raises(ValueError, match='restart Runtime'):
        settle(arena, [event])


def test_failed_hot_reload_keeps_old_revision_and_state(arena, tmp_path):
    runtime = host_runtime.Runtime(Path(__file__).resolve().parents[2] / 'tests/reference/Business/scripts', tmp_path)
    try:
        runtime.active.module.STATE_VERSION = 17
        old = runtime.active
        runtime.prepare()
        encoded = host_runtime.encode(arena.state)
        with pytest.raises(ValueError, match='restart Runtime'):
            runtime.switch(encoded)
        assert runtime.active is old and runtime.previous is None
        assert host_runtime.decode(encoded) == arena.state
    finally:
        runtime.close()


def test_bound_riposte_on_block_buffs_the_next_attack(arena):
    flat(arena, traits=('riposte',))
    battle.startAction(arena, 300580)
    advance(arena, battle.ACTION_TIMER)
    attack(arena, 2)
    event = evidence(arena, 2, 1, targetState=battle.GUARD_CHAINS[300580][1], defenceWeaponId=1030411)
    settle(arena, [event])
    assert hp(arena, 1) == 100 and arena.session['damageBuffs']['riposte']['multiplier'] == 1.2
    battle.releaseAction(arena, battle.BLOCK_RELEASE)
    advance(arena, battle.ACTION_TIMER)
    attack(arena)
    event = evidence(arena, target=3)
    settle(arena, [event])
    assert hp(arena, 3) == 52


def test_bound_rage_and_combo_multiply_on_the_following_attack(arena):
    flat(arena, traits=('combo', 'rage'))
    actors.data(arena, 2)['health'] = 7
    attack(arena)
    event = evidence(arena)
    settle(arena, [event])
    assert arena.session['damageBuffs']['rage']['until'] == event['occurredAt'] + 5000
    attack(arena)
    event = evidence(arena, target=3)
    settle(arena, [event])
    assert hp(arena, 3) == 42


@pytest.mark.parametrize('fps,delay', [(30, 0), (60, 200), (144, 2000)])
def test_same_motion_and_event_phase_have_frame_rate_independent_damage(arena, fps, delay):
    flat(arena)
    seconds = 1 / fps
    attack(arena)
    event = evidence(arena, velocityReady=True, sourceVelocity=[seconds / seconds, 0., 0.],
                     targetVelocity=[-seconds / seconds, 0., 0.], weaponVelocity=[3 * seconds / seconds, 0., 0.])
    settle(arena, [event], delay=delay)
    assert hp(arena, 2) == 40


def test_shield_damage_uses_break_trait_but_not_body_or_armor(multi, resources, monkeypatch):
    monkeypatch.setattr(damage_state, 'RULES', damage_rules.DEFAULT_RULES)
    configured(multi, resources)
    multi.session['damageProfile']['armor'] = 900
    actors.data(multi, 2)['damageProfile']['weapons']['1030411']['traits'] = ['shield_break']
    sample(multi, {1: ((0, 0, .2), 0), 2: ((1, 0, .2), 180)})
    battle.startAction(multi, battle.SHIELD_GUARD)
    advance(multi, battle.ACTION_TIMER)
    attack(multi, 2)
    event = evidence(multi, 2, 1, targetState=battle.GUARD_CHAINS[battle.SHIELD_GUARD][1], region='head',
                     defenceWeaponId=multi.session['offhandId'])
    settle(multi, [event])
    assert hp(multi, 1) == 100 and multi.session['shieldHealth']['703'] == 70
    assert multi.session['offhandId'] == 703
    assert not any(p['reason'] == 'combat-shield-break' for p in multi.result['send'])
