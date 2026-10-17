from pathlib import Path
import struct

from Business.scripts import battle
from Business.scripts.codec import attr_flow, battle_flow
from Business.scripts import hit_reactions, weapons


FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures/protocol/battle-action-wire-v1.txt'
DAMAGE_FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures/protocol/battle-damage-wire-v1.txt'


def animation_vectors():
    values = [('idle', battle_flow.ACT_STATE_IDLE, None),
              ('weapon', weapons.CHANGE_STATE, None)]
    for name, intent in [('up', 300560), ('right', 300550), ('down', 300570), ('left', 300540)]:
        for phase, state in zip(('prepare', 'hold', 'strike', 'recovery'), battle.ATTACK_CHAINS[intent]):
            values.append((f'attack-{name}-{phase}', state, None))
        values.extend(((f'attack-{name}-parried', battle.PARRIED_STATES[intent], 1),
                       (f'attack-{name}-terrain', battle.TERRAIN_REBOUND_STATES[intent], 0)))
    for name, intent in [('up', 300580), ('right', 300590), ('down', 300600), ('left', 300610)]:
        values.extend(((f'guard-{name}-prepare', battle.BLOCK_CHAINS[intent][0], None),
                       (f'guard-{name}-hold', battle.BLOCK_CHAINS[intent][1], None),
                       (f'guard-{name}-success', battle.PARRY_SUCCESS_STATES[intent], 1)))
    values.extend((('shield-prepare', 95, None), ('shield-hold', 309, None),
                   ('shield-success', 111, 1), ('dash-strike', battle.DASH_CHAIN[1], None),
                   ('dash-parried', battle.PARRIED_STATES[battle.DASH_ATTACK], 1),
                   ('dash-terrain', battle.TERRAIN_REBOUND_STATES[battle.DASH_ATTACK], 0),
                   ('hurt-hit', hit_reactions.HURT_STATE, 1),
                   ('hurt-knockdown', hit_reactions.KNOCKDOWN_STATE, 1),
                   ('hurt-guard-break', 360, 1), ('hurt-guard-break-knockdown', 362, 1),
                   ('hurt-exhaustion', hit_reactions.EXHAUSTION_STATE, 1),
                   ('hurt-shield-break', 41, 1)))
    for name, state, target in values:
        fields = dict(instance_id=2, seq_no=37, state=state, state_change_ms=245, state_time_ms=400)
        body = (battle_flow.encode_battle_state_sync_simple(**fields) if target is None
                else battle_flow.encode_battle_state_sync(**fields, target_rid=target))
        yield name, state, target, body


def test_animation_wire_vectors_match_original_business_codec():
    actual = [line.split() for line in FIXTURE.read_text(encoding='ascii').splitlines()
              if line and not line.startswith('#')]
    expected = [[name, str(state), '-' if target is None else str(target), body.hex()]
                for name, state, target, body in animation_vectors()]
    assert actual == expected


def test_client_action_request_layout_is_original_eleven_bytes():
    intents = (*battle.ATTACK_CHAINS, *battle.GUARD_CHAINS, battle.DASH_ATTACK,
               battle.ATTACK_RELEASE, battle.BLOCK_RELEASE, battle.SHIELD_RELEASE)
    for intent in intents:
        body = struct.pack('>HIib', 1, 1 << 8, intent, 0)
        assert len(body) == 11
        assert struct.unpack_from('>Iib', body, 2) == (1 << 8, intent, 0)


def damage_vectors():
    cases = (
        ('human-hit', 1, 2, 100, 80, False, False, True, False, -1, 0),
        ('human-head', 1, 2, 100, 0, True, True, True, False, 3, -1),
        ('bot-hit-human', 2, 1, 100, 95, False, False, False, False, 0, 1),
        ('guard-break', 1, 2, 100, 90, False, False, False, True, 2, 0),
    )
    for name, attacker, target, previous, health, head, decay, interrupt, guard_break, _body_index, material in cases:
        health_body = (battle_flow.encode_battle_data_update_notify(result_type=1, result_data=health)
                       if target == 1 else attr_flow.encode_health_update(
                           entity_rid=target, health=health, server_time_ms=1300))
        yield name, 0, 4 if target == 1 else 15, health_body
        yield name, 1, 4, battle_flow.encode_battle_hit_point_notify(
            hit_pos=(1.5, 20., .5), hit_direction=(1., 0., 0.), hit_normal=(0., 0., 1.),
            material_id=material, ignore_hurt=not (interrupt or guard_break),
            attacker_mid=attacker, target_mid=target, attacker_weapon_id=701,
            target_weapon_id=0, server_time_ms=1250)
        if attacker == 1:
            yield name, 2, 4, battle_flow.encode_battle_result(
                attacker_rid=attacker, target_rid=target, attacker_seq_no=37,
                target_life_state=1 if health == 0 else 0, attack_weapon_id=701,
                target_result_type=6 if decay else 2, target_result_data=health - previous,
                is_critical=head, target_rb_index=1 if head else 2)


def test_damage_wire_vectors_match_original_business_codecs():
    actual = [line.split() for line in DAMAGE_FIXTURE.read_text(encoding='ascii').splitlines()
              if line and not line.startswith('#')]
    expected = [[name, str(index), str(command), body.hex()]
                for name, index, command, body in damage_vectors()]
    assert actual == expected
