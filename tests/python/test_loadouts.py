from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import importlib
import struct

import pytest

from Business.runtime import host_runtime, profile_store
from Business.scripts import app, battle, combat, combat_resources, hero_resources, loadouts, player_profile, roster, scene
from Business.scripts.codec import battle_flow, loadout_flow, weapon_flow
from loadout_fixture import catalogue, combatProfiles, damageProfiles
from Business.scripts import damage_resources
from test_battle_actions import makeFlow, sendAction, advanceAction
from test_bot_roster import multi, sample
from test_player_respawn import select, play
from test_weapon_switch import change


@pytest.fixture
def resources(monkeypatch):
    cat = catalogue()
    monkeypatch.setattr(hero_resources, 'loadCatalogue', lambda _: cat)
    monkeypatch.setattr(combat_resources, 'loadCombatProfiles', lambda _, tid: combatProfiles(tid))
    monkeypatch.setattr(damage_resources, 'loadProfile', damageProfiles)
    return cat


def configured(flow, cat):
    flow.context['clientDirectory'] = 'fixture'
    profile = player_profile.create(cat)
    profile = player_profile.change(profile, cat, formation=[1101, 2102])
    profile = player_profile.change(profile, cat, heroId=1101, slots=[dict(slot=i+1, group=i, index=0) for i in range(3)])
    flow.state['playerProfile'] = profile
    loadouts.begin(flow)
    return flow


def lobby(cat):
    flow = configured(makeFlow(), cat)
    flow.session = flow.state['sessions']['2'] = dict(role='logic', hydration=[], pending={})
    flow.connection = 2
    return flow


def setFormation(cat, ids):
    return struct.pack('>Hii', 5, 1, len(ids)) + b''.join(loadout_flow.reference(cat, tid) for tid in ids)


def weaponSlots(cat, heroId, slots):
    return struct.pack('>H', 14) + loadout_flow.reference(cat, heroId) + struct.pack('>h', len(slots)) + b''.join(
        struct.pack('>iii', slot, cat.heroes[heroId].groupIds[group] if 0 <= group < 8 else group, index)
        for slot, group, index in slots)


def test_native_lobby_four_slots_owned_cards_and_equipment(resources):
    flow = lobby(resources)
    app.handleMessage(flow, dict(command=0x22, body=b'\0\3\0'))
    body = flow.result['send'][-1]['body']
    assert struct.unpack_from('>Hi', body) == (4, 1)
    assert struct.unpack_from('>ii', body, 18) == (4, 2)
    app.handleMessage(flow, dict(command=0x28, body=struct.pack('>Hii', 1, 1, 71)))
    body = flow.result['send'][-1]['body']
    assert struct.unpack_from('>Hiibbiiii', body) == (2, 1, 71, 1, 0, 2, 2, 2, 2)
    # Each owned card contains the full 8 x 6 weapon grid, locked holes, then configured slots.
    card = loadout_flow.heroCard(flow.state['playerProfile'], resources, 1101)
    assert struct.unpack_from('>ibQiiibQ', card) == (1, 0, 1101, 1, 1, 1101, 0, 0)
    assert card[34] == 2
    assert card[51] == 1 and struct.unpack_from('>i', card, 52)[0] == 701
    assert card[51+106] == 2
    assert struct.unpack_from('>h', card, 51+48*106)[0] == 3
    assert struct.unpack_from('>bii', card, 53+48*106) == (1, 901, 0)
    assert app.validateState(flow.state)


@pytest.mark.parametrize('data', [0, 1, 0x7f, 0x80, 0xff])
def test_get_formation_ignores_native_placeholder_byte(resources, data):
    flow = lobby(resources)
    before = deepcopy(flow.state)
    app.handleMessage(flow, dict(command=0x22, body=struct.pack('>HB', 3, data)))
    assert flow.result['send'] == [dict(connection=2, command=0x22,
        body=loadout_flow.formations(before['playerProfile'], resources), reason='configured-formations')]
    assert flow.state == before


@pytest.mark.parametrize('body', [b'\0\3', b'\0\3\0\0'])
def test_get_formation_rejects_wrong_length(resources, body):
    flow = lobby(resources)
    before = deepcopy(flow.state)
    with pytest.raises(ValueError, match='invalid get-formation request'):
        app.handleMessage(flow, dict(command=0x22, body=body))
    assert flow.result['send'] == []
    assert flow.state == before


def test_native_edit_publishes_ack_hero_equipment_and_formation(resources):
    flow = lobby(resources)
    old = deepcopy(flow.state['playerProfile'])
    app.handleMessage(flow, dict(command=0x22, body=weaponSlots(resources, 2102, [(4, 1, 0)])))
    assert flow.state['playerProfile']['weapons']['2102'] == [dict(slot=4, group=1, index=0)]
    assert flow.state['playerProfile']['revision'] == old['revision']+1
    assert [(p['command'], int.from_bytes(p['body'][:2], 'big')) for p in flow.result['send']] == [
        (0x22, 15), (0x22, 16), (0x28, 3), (0x28, 2), (0x22, 4)]
    flow.result['send'].clear()
    app.handleMessage(flow, dict(command=0x22, body=setFormation(resources, [2102])))
    assert flow.result['send'][0]['body'] == loadout_flow.result(5)
    assert flow.state['playerProfile']['formation'] == [2102]


def test_native_resource_group_ids_and_empty_slots(resources):
    flow = lobby(resources)
    entries = [(1, 902, 0)] + [(slot, 0, 0) for slot in range(2, 13)]
    body = struct.pack('>H', 14) + loadout_flow.reference(resources, 1101) + struct.pack('>h', 12)
    body += b''.join(struct.pack('>iii', *entry) for entry in entries)
    app.handleMessage(flow, dict(command=0x22, body=body))
    assert flow.state['playerProfile']['weapons']['1101'] == [dict(slot=1, group=1, index=0)]
    notify = flow.result['send'][1]['body']
    assert struct.unpack_from('>iii', notify, 16) == (1, 902, 0)
    assert loadout_flow.decodeWeapons(body, resources) == (1101, [dict(slot=1, group=1, index=0)])
    empty = body[:16] + b''.join(struct.pack('>iii', slot, 0, 0) for slot in range(1, 13))
    app.handleMessage(flow, dict(command=0x22, body=empty))
    assert flow.state['playerProfile']['weapons']['1101'] == []


def test_zero_based_group_is_not_a_native_resource_reference(resources):
    body = struct.pack('>H', 14) + loadout_flow.reference(resources, 1101) + struct.pack('>hiii', 1, 1, 1, 0)
    with pytest.raises(ValueError, match='weapon group'):
        loadout_flow.decodeWeapons(body, resources)


def test_four_hero_slots_preserve_order_through_instance_selection(resources, monkeypatch):
    heroes = dict(resources.heroes)
    heroes.update({tid: replace(heroes[1101], resourceId=tid) for tid in (3103, 4104)})
    cat = replace(resources, heroes=heroes)
    monkeypatch.setattr(hero_resources, 'loadCatalogue', lambda _: cat)
    flow = lobby(cat)
    ids = [4104, 2102, 3103, 1101]
    app.handleMessage(flow, dict(command=0x22, body=setFormation(cat, ids)))
    assert flow.state['playerProfile']['formation'] == ids
    assert not loadouts.message(flow, 0x20, 1, b'\0\1' + bytes(35))
    flow.session, flow.connection = flow.state['sessions']['1'], 1
    flow.session.update(loaded=True, battleEntered=False)
    loadouts.begin(flow)
    container = loadout_flow.containers(flow.session['loadoutProfile'], cat, 2, 1)[0]
    assert struct.unpack_from('>Hiibbiiii', container) == (2, 2, 1, 1, 0, 4, 4, 4, 4)
    for slot, heroId in enumerate(ids, 1):
        scene.message(flow, 0x36, 0x32, struct.pack('>Hi', 0x32, slot))
        assert flow.session['heroId'] == heroId
        assert struct.unpack_from('>i', roster.playerVision(flow), 109)[0] == heroId
    assert app.validateState(flow.state)


@pytest.mark.parametrize('slots', [[(5, 0, 0)], [(1, 0, 0), (1, 1, 0)], [(1, 7, 5)], [(1, -1, 0)],
                                  [(1, 0, 0), (2, 0, 0)]])
def test_invalid_weapon_requests_leave_state_intact(resources, slots):
    flow = lobby(resources)
    before = deepcopy(flow.state)
    app.handleMessage(flow, dict(command=0x22, body=weaponSlots(resources, 1101, slots)))
    assert flow.state == before
    assert [p['body'] for p in flow.result['send']] == [loadout_flow.result(14, loadout_flow.INVALID)]


def test_match_freezes_profile_and_rejects_empty_formation(resources):
    flow = lobby(resources)
    request = b'\0\1' + bytes(35)
    assert not loadouts.message(flow, 0x20, 1, request)
    frozen = deepcopy(flow.state['matchedProfile'])
    app.handleMessage(flow, dict(command=0x22, body=setFormation(resources, [])))
    assert flow.state['matchedProfile'] == frozen
    flow.session, flow.connection = flow.state['sessions']['1'], 1
    loadouts.begin(flow)
    assert flow.session['loadoutProfile'] == frozen and flow.session['heroId'] == 1101
    flow.session, flow.connection = flow.state['sessions']['2'], 2
    assert loadouts.message(flow, 0x20, 1, request)
    body = flow.result['send'][-1]['body']
    assert struct.unpack_from('>i', body, len(body)-201)[0] == loadout_flow.INVALID
    assert 'matchedProfile' not in flow.state


def test_instance_choose_vision_and_weapon_switch_follow_snapshot(resources):
    flow = configured(makeFlow(loaded=True, battleEntered=False), resources)
    scene.message(flow, 0x29, 0x65, struct.pack('>Hii', 0x65, 2, 9))
    expected = loadout_flow.containers(flow.session['loadoutProfile'], resources, 2, 9, selector=0x66)[0]
    assert flow.result['send'][-1]['body'] == expected
    scene.message(flow, 0x36, 0x32, struct.pack('>Hi', 0x32, 2))
    assert flow.session['heroId'] == 2102
    assert flow.session['weaponProfiles']['701']['movementAnimation']['forwardSlow'] == 2.75
    vision = roster.playerVision(flow)
    assert struct.unpack_from('>i', vision, 109)[0] == 2102
    count = struct.unpack_from('>h', vision, 150)[0]
    assert count == 3
    assert [struct.unpack_from('>iBBB', vision, 152+i*29) for i in range(count)] == [
        (701, 1, 0, 1), (703, 2, 0, 1), (100000, 13, 0, 0)]
    assert app.validateState(flow.state)


def test_multi_weapon_change_draw_and_contact_times(resources, multi):
    flow = configured(multi, resources)
    change(flow, 2)
    assert (flow.session['weaponId'], flow.session['offhandId']) == (702, 0)
    assert flow.session['pending'][battle.ACTION_TIMER] == flow.now+1100
    advanceAction(flow)
    sendAction(flow, 300560)
    assert flow.session['pending'][battle.ACTION_TIMER] == flow.now+350
    sync = battle_flow.decode_battle_state_sync_simple(flow.result['send'][-1]['body'])
    assert sync.state_time_ms == 350
    sendAction(flow, battle.ATTACK_RELEASE)
    advanceAction(flow)
    swing = flow.session['swing']
    assert combat.contactWindow(swing) == (70, 550)
    assert swing['weaponProfile']['weaponId'] == 702
    assert flow.session['pending'][battle.ACTION_TIMER] == flow.now+600
    # Action and delayed hit snapshots do not borrow subsequently changed catalogue objects.
    flow.session['weaponProfiles']['702']['attacks']['300560']['phases'][2] = 601
    assert swing['weaponProfile']['attacks']['300560']['phases'][2] == 600
    battle.terrainReaction(flow, 1)
    assert flow.session['pending'][battle.ACTION_TIMER] == flow.now+550
    assert app.validateState(flow.state)


def test_fists_restore_last_primary_and_shield_pairing(resources):
    flow = configured(makeFlow(), resources)
    change(flow, 2); advanceAction(flow)
    change(flow, 13); advanceAction(flow)
    assert flow.session['weaponId'] == 100000 and flow.session['offhandId'] == 0
    change(flow, 13); advanceAction(flow)
    assert flow.session['weaponId'] == 702
    change(flow, 1)
    assert flow.session['offhandId'] == 703
    assert struct.unpack('>Hiii', flow.result['send'][-3]['body']) == (2, 0, 701, 703)


def test_shield_uses_native_guard_states_and_releases(resources):
    flow = configured(makeFlow(), resources)
    sendAction(flow, battle.SHIELD_GUARD)
    assert flow.session['action']['intent'] == battle.SHIELD_GUARD
    assert flow.session['pending'][battle.ACTION_TIMER] == flow.now+150
    advanceAction(flow)
    assert flow.session['action']['index'] == 1 and battle.ACTION_TIMER not in flow.session['pending']
    assert battle_flow.decode_battle_state_sync_simple(flow.result['send'][-1]['body']).state == 309
    sendAction(flow, battle.SHIELD_RELEASE)
    assert flow.session['action']['index'] == -1
    change(flow, 2); advanceAction(flow)
    sendAction(flow, battle.SHIELD_GUARD)
    assert flow.session['action']['index'] == -1


def test_respawn_uses_round_configuration_despite_lobby_edits(resources, multi):
    flow = configured(multi, resources)
    flow.session.update(loaded=True, battleEntered=True)
    select(flow)
    scene.message(flow, 0x36, 0x32, struct.pack('>Hi', 0x32, 2))
    flow.state['playerProfile'] = player_profile.change(flow.state['playerProfile'], resources,
        formation=[1101], heroId=2102, slots=[dict(slot=1, group=1, index=0)])
    sample(flow)
    play(flow)
    assert flow.session['life']['state'] == 'spawning'
    assert flow.session['heroId'] == 2102 and flow.session['weaponId'] == 701 and flow.session['offhandId'] == 703
    packet = next(p['body'] for p in flow.result['send'] if p['reason'] == 'player-respawn-add')
    assert struct.unpack_from('>i', packet, 109)[0] == 2102
    assert app.validateState(flow.state)


def test_save_is_committed_before_success_and_failure_rolls_back(resources, tmp_path, monkeypatch):
    from types import SimpleNamespace
    flow = lobby(resources)
    runtime = object.__new__(host_runtime.Runtime)
    runtime.active = SimpleNamespace(name='Business.scripts', module=app)
    context = dict(flow.context, connections=[1, 2], playerProfilePath=str(tmp_path/'player-profile.json'))
    state = host_runtime.encode(flow.state)
    event = dict(type='message', connection=2, command=0x22, body=setFormation(resources, [2102]))
    saved = runtime.dispatchNative(event, state, context)
    disk, _ = profile_store.load(context['playerProfilePath'], player_profile.validate)
    assert disk['formation'] == [2102] and saved[1][0]['body'] == loadout_flow.result(5)
    event['body'] = setFormation(resources, [1101])
    def fail(*args):
        raise OSError('fixture disk failure')
    monkeypatch.setattr(profile_store, 'save', fail)
    rejected = runtime.dispatchNative(event, saved[0], context)
    assert rejected[0] == saved[0]
    assert [p['body'] for p in rejected[1]] == [loadout_flow.result(5, loadout_flow.SAVE_FAILED)]
    assert rejected[3][0]['level'] == 'ERROR'


def test_instance_initialization_commits_static_combat_profiles(monkeypatch):
    from types import SimpleNamespace
    cat = catalogue()
    groups = ((1030411, 0, 0, 0, 0, 0),) + ((0,) * 6,) * 7
    cat = replace(cat,
        heroes={1101: replace(cat.heroes[1101], groups=groups, nativeGroups=groups, defaults=((0, 0),))},
        weapons={1030411: replace(cat.weapons[702], resourceId=1030411), 100000: cat.weapons[100000]})
    monkeypatch.setattr(hero_resources, 'loadCatalogue', lambda _: cat)
    runtime = object.__new__(host_runtime.Runtime)
    runtime.active = SimpleNamespace(name='Business.scripts', module=app)
    state = app.createState({})
    state['playerProfile'] = player_profile.create(cat)
    state['matchedProfile'] = loadouts.freeze(state['playerProfile'])
    encoded = host_runtime.encode(state)
    for now, event in (
        (0, dict(type='connected', connection=1, role='instance')),
        (0, dict(type='authenticated', connection=1)),
        (20, dict(type='timer', connection=1, name='instance-init')),
    ):
        result = runtime.dispatchNative(event, encoded,
            dict(nowMs=now, connections=[1], clientDirectory='fixture'))
        encoded = result[0]
    state = host_runtime.decode(encoded)
    assert state['phase'] == 'instance-data-sent'
    assert 'matchedProfile' not in state
    assert state['sessions']['1']['weaponId'] == 1030411
    assert set(state['sessions']['1']['weaponProfiles']) == {'100000', '1030411'}
    assert [packet['reason'] for packet in result[1]] == [
        'instance-sync-login-rsp-after-auth', 'instance-minimal-update-after-auth']
    assert {host_runtime.decode(timer[2])['name'] for timer in result[2]} == {'instance-game-info', 'time-sync'}


def test_restart_loads_saved_profile_and_reload_keeps_round_snapshot(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2] / 'tests/reference/Business/scripts'
    context = dict(playerProfilePath=str(tmp_path/'player-profile.json'), clientDirectory='fixture', nowMs=0)
    cat = catalogue()
    saved = player_profile.change(player_profile.create(cat), cat, formation=[2102, 1101])
    profile_store.save(context['playerProfilePath'], saved, None)
    runtime = host_runtime.Runtime(root, tmp_path/'revisions')
    try:
        resources = importlib.import_module(runtime.active.name+'.hero_resources')
        monkeypatch.setattr(resources, 'loadCatalogue', lambda _: cat)
        state = runtime.create(host_runtime.encode(context))
        assert host_runtime.decode(state)['playerProfile'] == saved
        runtime.prepare()
        assert runtime.switch(state) == state
    finally:
        runtime.close()
