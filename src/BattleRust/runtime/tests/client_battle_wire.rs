#![cfg(windows)]

use std::collections::BTreeMap;
use t7r_battle_core::action::{
    Animation, AnimationEvent, Command, Direction, EquipmentItem, HurtKind, Intent, Phase,
};
use t7r_battle_core::damage::{DamageResult, Region};
use t7r_battle_runtime::{
    battle_session::{EndReason, Output, RankedScore, Score, Settlement, contacts::ContactEvent},
    client_battle_wire::{
        BATTLE_COMMAND, BotMoveWire, decode_action, encode_animation, encode_animation_for_client,
        encode_animation_for_client_clock, encode_bot_move_for_client, encode_bot_turn_for_client,
        encode_butcher_end_for_client, encode_camp_kill_update, encode_damage,
        encode_damage_for_client, encode_damage_for_client_clock, encode_dead_for_client,
        encode_death_state_for_client, encode_equipment_update_for_client,
        encode_guard_value_for_client, encode_kill_for_client, encode_score_update_for_client,
        encode_settlement_finish_for_client, encode_settlement_start_for_client,
        encode_shield_break_for_client, encode_shield_health_for_client, encode_streak_for_client,
    },
};

#[test]
fn bot_move_composite_matches_original_codec_vectors() {
    let rows = include_str!("../../../../tests/fixtures/protocol/bot-move-wire-v1.txt")
        .lines()
        .filter(|line| !line.starts_with('#'));
    let mut count = 0;
    for row in rows {
        let fields = row.split_whitespace().collect::<Vec<_>>();
        let value = BotMoveWire {
            tick_ms: fields[1].parse().unwrap(),
            client_rid: fields[2].parse().unwrap(),
            state: fields[3].parse().unwrap(),
            left_right: fields[4].parse().unwrap(),
            forward_back: fields[5].parse().unwrap(),
            current_velocity: fields[6].parse().unwrap(),
            max_velocity: fields[7].parse().unwrap(),
            position: [
                fields[8].parse().unwrap(),
                fields[9].parse().unwrap(),
                fields[10].parse().unwrap(),
            ],
            direction_yaw: fields[11].parse().unwrap(),
        };
        assert_eq!(
            fields[12].parse::<u32>().unwrap(),
            u32::from(value.client_rid)
        );
        assert_eq!((fields[13], fields[14]), ("1", "0"));
        let encoded = encode_bot_move_for_client(value).unwrap();
        assert_eq!(
            (encoded.command, encoded.body),
            (2, hex(fields[15])),
            "{}",
            fields[0]
        );
        count += 1;
    }
    assert_eq!(count, 4);
    let baseline = BotMoveWire {
        tick_ms: 250,
        client_rid: 2,
        state: 2,
        left_right: 0,
        forward_back: -1000,
        current_velocity: 3000,
        max_velocity: 25000,
        position: [1.25, 20.5, -2.],
        direction_yaw: -45,
    };
    assert!(
        encode_bot_move_for_client(BotMoveWire {
            client_rid: 0,
            ..baseline
        })
        .is_err()
    );
    assert!(
        encode_bot_move_for_client(BotMoveWire {
            tick_ms: u32::MAX,
            ..baseline
        })
        .is_err()
    );
    assert!(
        encode_bot_move_for_client(BotMoveWire {
            position: [f32::NAN, 0., 0.],
            ..baseline
        })
        .is_err()
    );
    assert!(
        encode_bot_move_for_client(BotMoveWire {
            state: 1,
            ..baseline
        })
        .is_err()
    );
}

#[test]
fn bot_turn_matches_original_codec_vector() {
    let row = include_str!("../../../../tests/fixtures/protocol/bot-turn-wire-v1.txt")
        .lines()
        .find(|line| line.starts_with("turn-toward "))
        .unwrap()
        .split_whitespace()
        .collect::<Vec<_>>();
    let wire = encode_bot_turn_for_client(
        row[1].parse().unwrap(),
        row[2].parse().unwrap(),
        row[3].parse().unwrap(),
    )
    .unwrap();
    assert_eq!((wire.command, wire.body), (2, hex(row[4])));
    assert!(encode_bot_turn_for_client(u32::MAX, 202, 0).is_err());
    assert!(encode_bot_turn_for_client(300, 0, 0).is_err());
    assert!(encode_bot_turn_for_client(300, 202, 181).is_err());
}

fn hex(value: &str) -> Vec<u8> {
    value
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
        .collect()
}

fn outcome(name: &str) -> (u16, Vec<u8>) {
    let row = include_str!("../../../../tests/fixtures/protocol/battle-outcome-wire-v1.txt")
        .lines()
        .find(|line| line.starts_with(&format!("{name} ")))
        .unwrap()
        .split_whitespace()
        .collect::<Vec<_>>();
    (row[1].parse().unwrap(), hex(row[2]))
}

fn shield_wire(name: &str) -> (u16, Vec<u8>) {
    let row = include_str!("../../../../tests/fixtures/protocol/battle-shield-wire-v1.txt")
        .lines()
        .find(|line| line.starts_with(&format!("{name} ")))
        .unwrap()
        .split_whitespace()
        .collect::<Vec<_>>();
    (row[1].parse().unwrap(), hex(row[2]))
}

#[test]
fn shield_break_and_equipment_messages_match_original_codec() {
    let rids = BTreeMap::from([(1, 101), (2, 202)]);
    let items = [
        EquipmentItem {
            slot: 1,
            weapon_id: 701,
        },
        EquipmentItem {
            slot: 3,
            weapon_id: 703,
        },
    ];
    let health = encode_shield_health_for_client(703, 3, 0, 100).unwrap();
    assert_eq!((health.command, health.body), shield_wire("shield-health"));
    let broken = encode_shield_break_for_client(1, 703, &rids).unwrap();
    assert_eq!((broken.command, broken.body), shield_wire("shield-break"));
    let update = encode_equipment_update_for_client(1, &items, 701, 0, 6300, &rids).unwrap();
    assert_eq!(
        (update.command, update.body),
        shield_wire("equipment-update")
    );
    assert!(encode_shield_health_for_client(703, 3, 101, 100).is_err());
    assert!(encode_shield_break_for_client(3, 703, &rids).is_err());
    assert!(encode_equipment_update_for_client(1, &items, 702, 0, 6300, &rids).is_err());
    let empty = encode_equipment_update_for_client(1, &[], 100000, 0, 6300, &rids).unwrap();
    assert_eq!(&empty.body[10..12], &1_i16.to_be_bytes());
    assert_eq!(&empty.body[12..19], &[0, 1, 134, 160, 13, 0, 1]);
}

#[test]
fn original_client_death_kill_and_settlement_match_python_wire_bytes() {
    let rids = BTreeMap::from([(1, 101), (2, 202)]);
    let guard = encode_guard_value_for_client(37.5).unwrap();
    assert_eq!((guard.command, guard.body), outcome("guard-value"));
    assert!(encode_guard_value_for_client(-1.).is_err());
    assert!(encode_guard_value_for_client(f64::NAN).is_err());
    assert!(encode_guard_value_for_client(f64::MAX).is_err());
    let dead = encode_dead_for_client(2, 1300, &rids).unwrap();
    assert_eq!((dead.command, dead.body), outcome("dead"));
    let death_state = encode_death_state_for_client(2, 1, 38, 300, &rids).unwrap();
    assert_eq!(
        (death_state.command, death_state.body),
        outcome("death-state")
    );
    let kill = encode_kill_for_client(1, 2, 701, 1300, &rids).unwrap();
    assert_eq!((kill.command, kill.body), outcome("kill"));
    let streak = encode_streak_for_client(1, 2, 1300, &rids).unwrap();
    assert_eq!((streak.command, streak.body), outcome("streak"));
    let butcher = encode_butcher_end_for_client(1, 1300, &rids).unwrap();
    assert_eq!((butcher.command, butcher.body), outcome("butcher-end"));
    let score_update = encode_score_update_for_client(
        1,
        Score {
            kills: 2,
            deaths: 1,
            points: 200,
            streak: 0,
        },
        Score {
            kills: 3,
            deaths: 1,
            points: 300,
            streak: 1,
        },
        730000,
        1300,
        &rids,
    )
    .unwrap();
    assert_eq!(
        (score_update.command, score_update.body),
        outcome("score-update")
    );
    let camp_update = encode_camp_kill_update(1300, 1, [3, 2], Some(10)).unwrap();
    assert_eq!(
        (camp_update.command, camp_update.body),
        outcome("camp-update")
    );
    assert!(
        encode_score_update_for_client(3, Score::default(), Score::default(), 0, 1300, &rids)
            .is_err()
    );
    assert!(
        encode_score_update_for_client(
            1,
            Score {
                kills: 3,
                ..Score::default()
            },
            Score::default(),
            0,
            1300,
            &rids,
        )
        .is_err()
    );
    assert!(encode_camp_kill_update(1300, 1, [3, 2], Some(0)).is_err());

    let settlement = Settlement {
        epoch: 7,
        started_at_ms: 1000,
        ended_at_ms: 7000,
        reason: EndReason::Timeout,
        result: 1,
        camp_kills: [3, 2],
        members: vec![
            RankedScore {
                actor_id: 1,
                camp: 1,
                rank: 1,
                result: 1,
                score: Score {
                    kills: 3,
                    deaths: 2,
                    points: 300,
                    streak: 1,
                },
            },
            RankedScore {
                actor_id: 2,
                camp: 2,
                rank: 1,
                result: 2,
                score: Score {
                    kills: 2,
                    deaths: 3,
                    points: 200,
                    streak: 0,
                },
            },
        ],
    };
    let start = encode_settlement_start_for_client(&settlement, &rids).unwrap();
    assert_eq!(start.len(), 4);
    for (message, name) in start.iter().zip([
        "score-local",
        "score-bot",
        "round-result",
        "round-state-end",
    ]) {
        assert_eq!(
            (message.command, message.body.clone()),
            outcome(name),
            "{name}"
        );
    }
    assert!(encode_settlement_finish_for_client(&settlement, 1, 100, 10000, 11999, &rids).is_err());
    let finish =
        encode_settlement_finish_for_client(&settlement, 1, 100, 10000, 12000, &rids).unwrap();
    assert_eq!(
        (finish[0].command, finish[0].body.clone()),
        outcome("settle")
    );
    assert_eq!(
        (finish[1].command, finish[1].body.clone()),
        outcome("finish")
    );

    let mut quit = settlement.clone();
    quit.reason = EndReason::Quit;
    quit.result = 0;
    let messages = encode_settlement_start_for_client(&quit, &rids).unwrap();
    assert_eq!(messages.len(), 3);
    assert_eq!(&messages[2].body[30..38], &[0; 8]);
    assert!(encode_settlement_finish_for_client(&quit, 1, 100, 10000, 7000, &rids).is_ok());
    assert!(encode_dead_for_client(3, 1300, &rids).is_err());
    assert!(encode_death_state_for_client(2, 1, 0, 300, &rids).is_err());
    assert!(encode_streak_for_client(1, 1, 1300, &rids).is_err());
    assert!(encode_kill_for_client(1, 2, u32::MAX, 1300, &rids).is_err());
}

#[test]
fn client_animation_clock_offset_covers_native_instance_before_match_start() {
    let rids = BTreeMap::from([(1, 101), (2, 202)]);
    let event = AnimationEvent {
        actor_id: 2,
        sequence: 37,
        at_ms: 1245,
        duration_ms: 400,
        target_id: Some(1),
        animation: Animation::Idle,
    };
    let baseline = encode_animation_for_client(&event, 1000, &rids).unwrap();
    assert_eq!(
        encode_animation_for_client_clock(&event, -1000, &rids).unwrap(),
        baseline
    );
    let delayed = encode_animation_for_client_clock(&event, 5000, &rids).unwrap();
    assert_eq!(
        u32::from_be_bytes(delayed.body[8..12].try_into().unwrap()),
        6245
    );
    assert!(encode_animation_for_client_clock(&event, -1246, &rids).is_err());
    assert!(encode_animation_for_client_clock(&event, i64::MAX, &rids).is_err());
}

fn animations() -> Vec<(String, Animation, Option<u32>)> {
    let mut values = vec![
        ("idle".into(), Animation::Idle, None),
        ("weapon".into(), Animation::WeaponChange, None),
    ];
    for (name, direction) in [
        ("up", Direction::Up),
        ("right", Direction::Right),
        ("down", Direction::Down),
        ("left", Direction::Left),
    ] {
        for (phase_name, phase, target) in [
            ("prepare", Phase::Prepare, None),
            ("hold", Phase::Hold, None),
            ("strike", Phase::Strike, None),
            ("recovery", Phase::Recovery, None),
            ("parried", Phase::Parried, Some(1)),
            ("terrain", Phase::Terrain, Some(0)),
        ] {
            values.push((
                format!("attack-{name}-{phase_name}"),
                Animation::Action {
                    intent: Intent::Attack(direction),
                    phase,
                },
                target,
            ));
        }
    }
    for (name, direction) in [
        ("up", Direction::Up),
        ("right", Direction::Right),
        ("down", Direction::Down),
        ("left", Direction::Left),
    ] {
        for (phase_name, phase, target) in [
            ("prepare", Phase::Prepare, None),
            ("hold", Phase::Hold, None),
            ("success", Phase::ParrySuccess, Some(1)),
        ] {
            values.push((
                format!("guard-{name}-{phase_name}"),
                Animation::Action {
                    intent: Intent::Guard(direction),
                    phase,
                },
                target,
            ));
        }
    }
    for (name, phase, target) in [
        ("prepare", Phase::Prepare, None),
        ("hold", Phase::Hold, None),
        ("success", Phase::ParrySuccess, Some(1)),
    ] {
        values.push((
            format!("shield-{name}"),
            Animation::Action {
                intent: Intent::Shield,
                phase,
            },
            target,
        ));
    }
    for (name, phase, target) in [
        ("strike", Phase::Strike, None),
        ("parried", Phase::Parried, Some(1)),
        ("terrain", Phase::Terrain, Some(0)),
    ] {
        values.push((
            format!("dash-{name}"),
            Animation::Action {
                intent: Intent::Dash,
                phase,
            },
            target,
        ));
    }
    for (name, kind) in [
        ("hit", HurtKind::Hit),
        ("knockdown", HurtKind::Knockdown),
        ("guard-break", HurtKind::GuardBreak),
        ("guard-break-knockdown", HurtKind::GuardBreakKnockdown),
        ("exhaustion", HurtKind::Exhaustion),
        ("shield-break", HurtKind::ShieldBreak),
    ] {
        values.push((format!("hurt-{name}"), Animation::Hurt(kind), Some(1)));
    }
    values
}

#[test]
fn all_original_action_states_match_python_wire_corpus() {
    let rows: Vec<_> =
        include_str!("../../../../tests/fixtures/protocol/battle-action-wire-v1.txt")
            .lines()
            .filter(|line| !line.starts_with('#'))
            .map(|line| line.split_whitespace().collect::<Vec<_>>())
            .collect();
    let expected = animations();
    assert_eq!(rows.len(), expected.len());
    for (row, (name, animation, target_id)) in rows.into_iter().zip(expected) {
        assert_eq!(row[0], name);
        assert_eq!(
            row[2],
            target_id.map_or("-".to_owned(), |id| id.to_string())
        );
        let event = AnimationEvent {
            actor_id: 2,
            sequence: 37,
            at_ms: 1245,
            duration_ms: 400,
            target_id,
            animation,
        };
        let result = encode_animation(&event, 1000).unwrap();
        assert_eq!(result.command, BATTLE_COMMAND);
        assert_eq!(result.body, hex(row[3]), "{name}");
    }
}

fn input(intent: i32, keys: u32, flag: u8) -> [u8; 11] {
    let mut result = [0; 11];
    result[1] = 1;
    result[2..6].copy_from_slice(&keys.to_be_bytes());
    result[6..10].copy_from_slice(&intent.to_be_bytes());
    result[10] = flag;
    result
}

#[test]
fn original_action_input_decodes_to_semantic_commands_and_rejects_bad_layout() {
    for (wire, direction) in [
        (300560, Direction::Up),
        (300550, Direction::Right),
        (300570, Direction::Down),
        (300540, Direction::Left),
    ] {
        assert_eq!(
            decode_action(4, &input(wire, 1 << 8, 1)).unwrap(),
            Some(Command::Press {
                intent: Intent::Attack(direction),
                defence_down: true,
                dash_run: false,
                dash_forward: false,
            })
        );
    }
    for (wire, direction) in [
        (300580, Direction::Up),
        (300590, Direction::Right),
        (300600, Direction::Down),
        (300610, Direction::Left),
    ] {
        assert!(matches!(
            decode_action(4, &input(wire, 0, 0)).unwrap(),
            Some(Command::Press { intent: Intent::Guard(value), .. }) if value == direction
        ));
    }
    assert!(matches!(
        decode_action(4, &input(300130, 0, 0)).unwrap(),
        Some(Command::Press {
            intent: Intent::Shield,
            ..
        })
    ));
    assert_eq!(
        decode_action(4, &input(700250, (1 << 26) | (1 << 21), 0)).unwrap(),
        Some(Command::Press {
            intent: Intent::Dash,
            defence_down: false,
            dash_run: true,
            dash_forward: true,
        })
    );
    assert_eq!(
        decode_action(4, &input(300020, 0, 0)).unwrap(),
        Some(Command::ReleaseAttack)
    );
    for release in [300620, 300140] {
        assert_eq!(
            decode_action(4, &input(release, 0, 0)).unwrap(),
            Some(Command::ReleaseGuard)
        );
    }
    assert_eq!(decode_action(4, &input(12345, 0, 0)).unwrap(), None);
    assert_eq!(decode_action(5, &input(300560, 0, 0)).unwrap(), None);
    assert_eq!(decode_action(4, &[0, 2]).unwrap(), None);
    assert!(decode_action(4, &[0]).is_err());
    assert!(decode_action(4, &input(300560, 0, 2)).is_err());
    assert!(decode_action(4, &input(300560, 0, 0)[..10]).is_err());
}

#[test]
fn animation_encoding_checks_actor_phase_and_instance_clock() {
    let mut event = AnimationEvent {
        actor_id: 2,
        sequence: 37,
        at_ms: 1245,
        duration_ms: 400,
        target_id: None,
        animation: Animation::Idle,
    };
    assert!(encode_animation(&event, 1246).is_err());
    event.actor_id = u32::from(u16::MAX) + 1;
    assert!(encode_animation(&event, 1000).is_err());
    event.actor_id = 2;
    event.animation = Animation::Action {
        intent: Intent::Dash,
        phase: Phase::Prepare,
    };
    assert!(encode_animation(&event, 1000).is_err());
}

#[test]
fn client_output_uses_bound_rids_without_changing_authoritative_actor_ids() {
    let actor_rids = BTreeMap::from([(1, 101), (2, 202)]);
    let animation = AnimationEvent {
        actor_id: 1,
        sequence: 37,
        at_ms: 1245,
        duration_ms: 400,
        target_id: Some(2),
        animation: Animation::Hurt(HurtKind::Hit),
    };
    let expected = AnimationEvent {
        actor_id: 101,
        target_id: Some(202),
        ..animation
    };
    assert_eq!(
        encode_animation_for_client(&animation, 1000, &actor_rids).unwrap(),
        encode_animation(&expected, 1000).unwrap()
    );
    assert_eq!(animation.actor_id, 1);
    assert_eq!(animation.target_id, Some(2));
    let terrain = AnimationEvent {
        target_id: Some(0),
        ..animation
    };
    let terrain_wire = AnimationEvent {
        actor_id: 101,
        ..terrain
    };
    assert_eq!(
        encode_animation_for_client(&terrain, 1000, &actor_rids).unwrap(),
        encode_animation(&terrain_wire, 1000).unwrap()
    );

    let output = damage_output(DamageCase {
        name: "mapped",
        attacker: 1,
        target: 2,
        previous_health: 100,
        health: 80,
        head: false,
        decay: false,
        interrupt: true,
        guard_break: false,
        body_index: 0,
        material: 0,
    });
    let mut projected = output.clone();
    if let Output::Damage {
        attacker,
        target,
        evidence,
        ..
    } = &mut projected
    {
        *attacker = 101;
        *target = 202;
        evidence.actor_id = 101;
        evidence.target_id = 202;
    }
    assert_eq!(
        encode_damage_for_client(&output, 1, 1300, &actor_rids).unwrap(),
        encode_damage(&projected, 101, 1300).unwrap()
    );
    assert_eq!(
        encode_damage_for_client(&output, 2, 1300, &actor_rids).unwrap(),
        encode_damage(&projected, 202, 1300).unwrap()
    );
    assert!(matches!(
        output,
        Output::Damage {
            attacker: 1,
            target: 2,
            ..
        }
    ));

    assert!(encode_animation_for_client(&animation, 1000, &BTreeMap::from([(1, 101)])).is_err());
    assert!(encode_damage_for_client(&output, 1, 1300, &BTreeMap::from([(1, 101)])).is_err());
    let duplicate = BTreeMap::from([(1, 101), (2, 101)]);
    assert!(encode_animation_for_client(&animation, 1000, &duplicate).is_err());
    assert!(encode_damage_for_client(&output, 1, 1300, &duplicate).is_err());
}

#[derive(Clone, Copy)]
struct DamageCase {
    name: &'static str,
    attacker: u32,
    target: u32,
    previous_health: u32,
    health: u32,
    head: bool,
    decay: bool,
    interrupt: bool,
    guard_break: bool,
    body_index: i8,
    material: i8,
}

fn damage_output(case: DamageCase) -> Output {
    let DamageCase {
        attacker,
        target,
        previous_health,
        health,
        head,
        decay,
        interrupt,
        guard_break,
        body_index,
        material,
        ..
    } = case;
    Output::Damage {
        attacker,
        target,
        sequence: 37,
        health,
        previous_health,
        weapon_id: 701,
        evidence: Box::new(ContactEvent {
            guard_volume: false,
            actor_id: attacker,
            target_id: target,
            source_entity: 101,
            target_entity: 102,
            sequence: 37,
            phase_at_ms: 1000,
            target_intent: None,
            target_phase: Phase::Idle,
            target_phase_at_ms: 1000.,
            target_observed_at_ms: None,
            source_observed_at_ms: None,
            occurred_at_ms: 1250.5,
            along: 0.5,
            defence_weapon_id: 0,
            region: if head { Region::Head } else { Region::Torso },
            body_index,
            source_position: [0., 20., 0.],
            target_position: [1., 20., 0.],
            source_heading: 0.,
            target_heading: 180.,
            source_velocity: [0.; 3],
            target_velocity: [0.; 3],
            weapon_velocity: [0.; 3],
            velocity_ready: false,
            airborne: false,
            target_airborne: false,
            position: [1.5, 20., 0.5],
            normal: [0., 0., 1.],
            direction: [1., 0., 0.],
            material,
        }),
        guard_break,
        result: DamageResult {
            hp_damage: previous_health - health,
            head,
            decay,
            interrupt,
            ..DamageResult::default()
        },
    }
}

#[test]
fn damage_feedback_uses_collision_part_not_rigid_body_index() {
    for (region, part) in [
        (Region::None, 0),
        (Region::Head, 1),
        (Region::Torso, 2),
        (Region::Legs, 3),
    ] {
        for body_index in [-1, 0, 1, 2, 5, 127] {
            let mut output = damage_output(DamageCase {
                name: "body-region",
                attacker: 1,
                target: 2,
                previous_health: 100,
                health: 80,
                head: region == Region::Head,
                decay: false,
                interrupt: true,
                guard_break: false,
                body_index,
                material: 0,
            });
            if let Output::Damage { evidence, .. } = &mut output {
                evidence.region = region;
                evidence.defence_weapon_id = if region == Region::None { 702 } else { 0 };
            }
            let before = output.clone();
            let rids = BTreeMap::from([(1, 101), (2, 202)]);
            let messages = encode_damage_for_client(&output, 1, 1300, &rids).unwrap();
            let result = messages.last().unwrap();
            assert_eq!(&result.body[..2], &9_u16.to_be_bytes());
            assert_eq!(result.body[20], part, "{region:?}/{body_index}");
            assert_eq!(output, before);
        }
    }
}

#[test]
fn authoritative_damage_preserves_python_health_contact_result_order_and_bytes() {
    let cases = [
        DamageCase {
            name: "human-hit",
            attacker: 1,
            target: 2,
            previous_health: 100,
            health: 80,
            head: false,
            decay: false,
            interrupt: true,
            guard_break: false,
            body_index: -1,
            material: 0,
        },
        DamageCase {
            name: "human-head",
            attacker: 1,
            target: 2,
            previous_health: 100,
            health: 0,
            head: true,
            decay: true,
            interrupt: true,
            guard_break: false,
            body_index: 3,
            material: -1,
        },
        DamageCase {
            name: "bot-hit-human",
            attacker: 2,
            target: 1,
            previous_health: 100,
            health: 95,
            head: false,
            decay: false,
            interrupt: false,
            guard_break: false,
            body_index: 0,
            material: 1,
        },
        DamageCase {
            name: "guard-break",
            attacker: 1,
            target: 2,
            previous_health: 100,
            health: 90,
            head: false,
            decay: false,
            interrupt: false,
            guard_break: true,
            body_index: 2,
            material: 0,
        },
    ];
    let rows: Vec<_> =
        include_str!("../../../../tests/fixtures/protocol/battle-damage-wire-v1.txt")
            .lines()
            .filter(|line| !line.starts_with('#'))
            .map(|line| line.split_whitespace().collect::<Vec<_>>())
            .collect();
    let mut cursor = 0;
    for case in cases {
        let output = damage_output(case);
        let messages = encode_damage(&output, 1, 1300).unwrap();
        assert_eq!(messages.len(), if case.attacker == 1 { 3 } else { 2 });
        for (index, message) in messages.iter().enumerate() {
            let row = &rows[cursor];
            assert_eq!(row[0], case.name);
            assert_eq!(row[1], index.to_string());
            assert_eq!(
                message.command,
                row[2].parse::<u16>().unwrap(),
                "{}",
                case.name
            );
            assert_eq!(message.body, hex(row[3]), "{}", case.name);
            cursor += 1;
        }
    }
    assert_eq!(cursor, rows.len());
    let mut invalid = damage_output(cases[0]);
    if let Output::Damage { evidence, .. } = &mut invalid {
        evidence.target_id = 3;
    }
    assert!(encode_damage(&invalid, 1, 1300).is_err());
}

#[test]
fn damage_projection_uses_native_server_clock_without_mutating_match_output() {
    let rids = BTreeMap::from([(1, 101), (2, 202)]);
    let output = damage_output(DamageCase {
        name: "clock",
        attacker: 1,
        target: 2,
        previous_health: 100,
        health: 80,
        head: false,
        decay: false,
        interrupt: true,
        guard_break: false,
        body_index: 0,
        material: 0,
    });
    let mut expected = output.clone();
    if let Output::Damage { evidence, .. } = &mut expected {
        evidence.occurred_at_ms = 6250.;
    }
    assert_eq!(
        encode_damage_for_client_clock(&output, 1, 1300, 5000, &rids).unwrap(),
        encode_damage_for_client(&expected, 1, 6300, &rids).unwrap()
    );
    assert_eq!(
        encode_damage_for_client_clock(&output, 2, 1300, 5000, &rids).unwrap(),
        encode_damage_for_client(&expected, 2, 6300, &rids).unwrap()
    );
    if let Output::Damage { evidence, .. } = &output {
        assert_eq!(evidence.occurred_at_ms, 1250.5);
    }
    assert!(encode_damage_for_client_clock(&output, 1, 1300, -1251, &rids).is_err());
    assert!(encode_damage_for_client_clock(&output, 1, 1300, i64::MAX, &rids).is_err());
}

#[test]
fn health_notifications_preserve_absolute_values_above_one_hundred() {
    use t7r_battle_runtime::client_battle_wire::encode_health_value_for_client;
    for health in [0, 99, 150, i32::MAX as u32] {
        let message = encode_health_value_for_client(health).unwrap();
        assert_eq!(&message.body[4..8], &health.to_be_bytes());
    }
    assert!(encode_health_value_for_client(i32::MAX as u32 + 1).is_err());
    let output = damage_output(DamageCase {
        name: "hero-health",
        attacker: 1,
        target: 2,
        previous_health: 150,
        health: 130,
        head: false,
        decay: false,
        interrupt: false,
        guard_break: false,
        body_index: 0,
        material: 0,
    });
    let local = encode_damage(&output, 2, 1300).unwrap();
    assert_eq!(&local[0].body[4..8], &130_u32.to_be_bytes());
    let remote = encode_damage(&output, 1, 1300).unwrap();
    assert_eq!(&remote[0].body[14..18], &130_u32.to_be_bytes());
}
