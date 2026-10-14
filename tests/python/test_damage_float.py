import struct

import pytest

from Business.scripts import actors, app, battle, combat, contracts as wire
from Business.scripts.codec import battle_flow
from blade_fixture import contact, publish
from test_bot_roster import multi, sample
from test_contact_journal import native
from test_melee_combat import advance, arena, hp
from test_melee_multi_hit import attack, cluster


def damagePackets(flow):
    return [packet for packet in flow.result['send'] if packet['command'] == battle_flow.BATTLE_COMMAND
            and packet['body'][:2] == struct.pack('>H', battle_flow.BATTLE_RESULT)]


@pytest.mark.parametrize('attacker', [1, 2])
@pytest.mark.parametrize('intent', [*battle.ATTACK_CHAINS, battle.DASH_ATTACK])
@pytest.mark.parametrize('health', [100, 20, 7])
def test_only_local_hits_send_actual_negative_hp_delta_before_death(arena, attacker, intent, health):
    target = 3 - attacker
    actors.data(arena, target)['health'] = health
    attack(arena, attacker, intent)
    sequence = battle.actionState(arena, attacker)['seq']
    advance(arena, combat.hitTimer(attacker))

    packets = damagePackets(arena)
    assert len(packets) == (1 if attacker == wire.ACTOR_ID else 0)
    damage = min(health, combat.DASH_DAMAGE if intent == battle.DASH_ATTACK else combat.DAMAGE)
    lifeState = battle_flow.LIFE_STATE_KILL if health == damage else battle_flow.LIFE_STATE_NONE
    assert hp(arena, target) == health - damage
    assert hp(arena, attacker) == combat.MAX_HEALTH
    if packets:
        body = packets[0]['body']
        result = battle_flow.decode_battle_result(body)
        assert result == battle_flow.BattleResult(attacker, target, sequence, lifeState, wire.WEAPON_ID,
                                                battle_flow.RESULT_DATA_DELTA_HP, -damage, battle_flow.HIT_PART_BODY)
        assert len(body) == 38
        assert struct.unpack_from('>HQQHbbh', body) == (9, attacker, target, sequence, battle_flow.HIT_PART_BODY, lifeState, 0)
        assert struct.unpack_from('>hhi', body, 24) == (1, 2, -damage)
        assert struct.unpack_from('>ibb', body, 32) == (wire.WEAPON_ID, 0, 0)

    if health == damage:
        death = next(packet for packet in arena.result['send'] if packet['command'] == 4
                     and packet['body'][:2] == struct.pack('>H', battle_flow.DEAD_NOTIFY))
        if packets:
            assert arena.result['send'].index(packets[0]) < arena.result['send'].index(death)
        combat.applyHit(arena, attacker, target, arena.context['botWorld'])
        assert damagePackets(arena) == packets


@pytest.mark.parametrize('attacker', [1, 2, 12])
@pytest.mark.parametrize('intent', [*battle.ATTACK_CHAINS, battle.DASH_ATTACK])
def test_multi_hit_only_shows_local_damage_once_per_enemy(multi, attacker, intent):
    _, targets, ally = cluster(multi, attacker)
    attack(multi, attacker, intent)
    sequence = battle.actionState(multi, attacker)['seq']
    advance(multi, combat.hitTimer(attacker))
    for _ in range(2):
        advance(multi, combat.hitTimer(attacker))

    results = [battle_flow.decode_battle_result(packet['body']) for packet in damagePackets(multi)]
    damage = combat.DASH_DAMAGE if intent == battle.DASH_ATTACK else combat.DAMAGE
    assert [result.target_rid for result in results] == (list(targets) if attacker == wire.ACTOR_ID else [])
    assert all(result.attacker_rid == attacker and result.attacker_seq_no == sequence
               and result.target_result_type == battle_flow.RESULT_DATA_DELTA_HP
               and result.target_result_data == -damage for result in results)
    assert hp(multi, ally) == combat.MAX_HEALTH
    assert all(hp(multi, target) == combat.MAX_HEALTH - damage for target in targets)


@pytest.mark.parametrize('case', ['prepare', 'miss', 'parry', 'terrain', 'friendly'])
def test_non_damaging_contacts_do_not_send_damage_numbers(arena, case):
    if case == 'prepare':
        battle.startAction(arena, 300560)
    else:
        if case == 'miss':
            publish(arena, 1, [])
        elif case == 'parry':
            battle.startAction(arena, 300580, actorId=2)
            advance(arena, battle.actionTimer(2))
        elif case == 'terrain':
            publish(arena, 1, [contact(0, material=4)])
        elif case == 'friendly':
            actors.data(arena, 2)['camp'] = actors.data(arena, 1).get('camp', 1)
        attack(arena)
        advance(arena, combat.hitTimer(1))
    combat.worldUpdated(arena)
    assert hp(arena, 1) == hp(arena, 2) == combat.MAX_HEALTH
    assert damagePackets(arena) == []


@pytest.mark.parametrize('intent', [300560, battle.DASH_ATTACK])
@pytest.mark.parametrize('attacker,target', [(1, 2), (2, 1), (2, 12), (12, 2)])
def test_delayed_native_contact_only_shows_local_damage_with_original_sequence(multi, intent, attacker, target):
    sample(multi)
    attack(multi, attacker, intent)
    event = native(multi, attacker=attacker, target=target, occurredAt=multi.now + 100)
    multi.now += battle.phaseMs(intent, battle.strikeIndex(intent), dash=battle.actionState(multi, attacker).get('dash')) + 10
    app.handleTimer(multi, battle.actionTimer(attacker))
    assert battle.actionState(multi, attacker)['seq'] != event['seq']

    combat.worldUpdated(multi)
    combat.worldUpdated(multi)
    packets = damagePackets(multi)
    damage = 40 if intent == battle.DASH_ATTACK else 20
    assert hp(multi, target) == combat.MAX_HEALTH - damage
    assert len(packets) == (1 if attacker == wire.ACTOR_ID else 0)
    if packets:
        result = battle_flow.decode_battle_result(packets[0]['body'])
        assert (result.attacker_rid, result.target_rid, result.attacker_seq_no) == (attacker, target, event['seq'])
        assert result.target_result_data == -damage
