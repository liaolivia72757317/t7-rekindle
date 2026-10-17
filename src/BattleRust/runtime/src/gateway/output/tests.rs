use super::*;
use crate::battle_session::{SampledPose, contacts::ContactEvent};
use t7r_battle_core::{
    action::{Animation, AnimationEvent, EquipmentItem, Phase},
    ai::{BotOutput, MovementIntent},
    battle_semantics::state::{MatchState, StateActor},
    damage::{DamageResult, Region},
    navigation::SpawnReference,
};

fn respawn_fixture() -> (SceneRoster, Identity, ClientOutputState, BTreeMap<u32, u16>) {
    let actor = |actor_id, client_rid, camp| super::super::scene_actor_wire::LocalSceneActor {
        actor_id,
        client_rid,
        camp,
        hero_id: 1101,
        max_health: 100,
        position: [0., 0., 0.],
        heading: 0.,
        equipment: if actor_id == 1 {
            vec![
                EquipmentItem {
                    slot: 1,
                    weapon_id: 701,
                },
                EquipmentItem {
                    slot: 2,
                    weapon_id: 703,
                },
            ]
        } else {
            vec![EquipmentItem {
                slot: 1,
                weapon_id: 701,
            }]
        },
        weapon_id: 701,
        offhand_id: 0,
    };
    (
        SceneRoster {
            local: actor(1, 101, 1),
            bots: vec![actor(2, 202, 2)],
            bot_names: vec!["Fixture0".into()],
            kill_target: 10,
            guard_max: 5.,
            local_shields: vec![(703, 100)],
        },
        Identity {
            name: b"Player".to_vec(),
            portrait: 7,
            level: 100,
        },
        ClientOutputState {
            training: false,
            scores: BTreeMap::from([(1, Score::default()), (2, Score::default())]),
            camps: BTreeMap::from([(1, 1), (2, 2)]),
            kill_target: 10,
            bots: BTreeSet::from([2]),
            bot_motion: BTreeMap::new(),
        },
        BTreeMap::from([(1, 101), (2, 202)]),
    )
}

#[test]
fn local_shield_break_projects_durability_disarm_and_hurt_in_original_order() {
    let (roster, identity, current, rids) = respawn_fixture();
    let mut state = match &respawn_batch(1, [5., 6., 0.])[1] {
        Output::State(state) => state.clone(),
        _ => unreachable!(),
    };
    let actor = &mut state.actors[0];
    actor.life = 1;
    actor.life_at_ms = 0;
    actor.spawn_position = [0.; 3];
    actor.spawn_heading = 0.;
    actor.phase = 9;
    actor.offhand_id = 0;
    actor.shields[0].1 = 0;
    let break_animation = t7r_battle_core::action::AnimationEvent {
        actor_id: 1,
        sequence: 8,
        at_ms: 1300,
        duration_ms: 1000,
        target_id: Some(2),
        animation: Animation::Hurt(t7r_battle_core::action::HurtKind::ShieldBreak),
    };
    let batch = [
        Output::Shield {
            actor_id: 1,
            weapon_id: 703,
            health: 0,
        },
        Output::Action(ActionEvent::Animation(break_animation)),
        Output::State(state.clone()),
    ];
    let context = OutputContext {
        at_ms: 1300,
        actor_poses: BTreeMap::new(),
    };
    let (messages, _) = project_client_messages_with_scene(
        &current,
        &batch,
        &context,
        Some(-1000),
        Some(5000),
        Some(1),
        &rids,
        Some(SceneProjection {
            roster: &roster,
            identity: &identity,
        }),
    )
    .unwrap();
    assert_eq!(
        messages.iter().map(|item| item.command).collect::<Vec<_>>(),
        [5, 5, 5, 4]
    );
    assert_eq!(
        messages[0],
        client_battle_wire::encode_shield_health_for_client(703, 2, 0, 100).unwrap()
    );
    assert_eq!(
        messages[1],
        client_battle_wire::encode_shield_break_for_client(1, 703, &rids).unwrap()
    );
    assert_eq!(
        messages[2],
        client_battle_wire::encode_equipment_update_for_client(
            1,
            &roster.local.equipment,
            701,
            0,
            6300,
            &rids,
        )
        .unwrap()
    );
    assert_eq!(
        messages[3],
        client_battle_wire::encode_animation_for_client_clock(&break_animation, -1000, &rids,)
            .unwrap()
    );
    state.actors[0].shields[0].1 = 1;
    let mut invalid = batch.to_vec();
    invalid[2] = Output::State(state);
    assert!(
        project_client_messages_with_scene(
            &current,
            &invalid,
            &context,
            Some(-1000),
            Some(5000),
            Some(1),
            &rids,
            Some(SceneProjection {
                roster: &roster,
                identity: &identity
            }),
        )
        .is_err()
    );

    let mut intact = match &batch[2] {
        Output::State(state) => state.clone(),
        _ => unreachable!(),
    };
    intact.actors[0].shields[0].1 = 70;
    intact.actors[0].offhand_id = 703;
    let (remaining, _) = project_client_messages_with_scene(
        &current,
        &[
            Output::Shield {
                actor_id: 1,
                weapon_id: 703,
                health: 70,
            },
            Output::State(intact),
        ],
        &context,
        Some(-1000),
        Some(5000),
        Some(1),
        &rids,
        Some(SceneProjection {
            roster: &roster,
            identity: &identity,
        }),
    )
    .unwrap();
    assert_eq!(
        remaining,
        vec![client_battle_wire::encode_shield_health_for_client(703, 2, 70, 100).unwrap()]
    );
}

#[test]
fn broken_bot_shield_broadcasts_disarm_without_local_durability_update() {
    let (mut roster, identity, current, rids) = respawn_fixture();
    roster.bots[0].equipment.push(EquipmentItem {
        slot: 3,
        weapon_id: 703,
    });
    let mut state = match &respawn_batch(2, [8., 9., 0.])[1] {
        Output::State(state) => state.clone(),
        _ => unreachable!(),
    };
    let actor = &mut state.actors[0];
    actor.life = 1;
    actor.life_at_ms = 0;
    actor.spawn_position = [0.; 3];
    actor.spawn_heading = 0.;
    actor.shield_count = 1;
    actor.shields[0] = (703, 0);
    let (messages, _) = project_client_messages_with_scene(
        &current,
        &[
            Output::Shield {
                actor_id: 2,
                weapon_id: 703,
                health: 0,
            },
            Output::State(state),
        ],
        &OutputContext {
            at_ms: 1300,
            actor_poses: BTreeMap::new(),
        },
        Some(-1000),
        Some(5000),
        Some(1),
        &rids,
        Some(SceneProjection {
            roster: &roster,
            identity: &identity,
        }),
    )
    .unwrap();
    assert_eq!(messages.len(), 2);
    assert_eq!(
        messages[0],
        client_battle_wire::encode_shield_break_for_client(2, 703, &rids).unwrap()
    );
    assert_eq!(
        messages[1],
        client_battle_wire::encode_equipment_update_for_client(
            2,
            &roster.bots[0].equipment,
            701,
            0,
            6300,
            &rids,
        )
        .unwrap()
    );
}

fn respawn_batch(actor_id: u32, position: [f64; 3]) -> [Output; 2] {
    let spawn = SpawnReference {
        position,
        heading: 45.,
    };
    [
        Output::Life {
            actor_id,
            state: Life::Spawning {
                spawned_at_ms: 1300,
                spawn,
            },
        },
        Output::State(MatchState {
            mode: 1,
            epoch: 7,
            revision: 3,
            base_revision: 2,
            at_ms: 1300,
            actors: vec![StateActor {
                actor_id,
                camp: if actor_id == 1 { 1 } else { 2 },
                kind: if actor_id == 1 { 1 } else { 2 },
                life: 5,
                intent: 0,
                phase: 0,
                flags: 0,
                sequence: 8,
                duration_ms: 0,
                hero_id: 1101,
                health: 100,
                guard: 5.,
                score: [0; 4],
                weapon_id: 701,
                offhand_id: 0,
                main_weapon_id: 701,
                shield_count: u8::from(actor_id == 1),
                shields: if actor_id == 1 {
                    [(703, 50), (0, 0), (0, 0), (0, 0)]
                } else {
                    [(0, 0); 4]
                },
                action_changed_at_ms: 0,
                life_at_ms: 1300,
                respawn_at_ms: 0,
                spawn_position: position,
                spawn_heading: 45.,
            }],
        }),
    ]
}

#[test]
fn respawn_recreates_bound_vision_and_reactivates_only_after_fresh_life_sample() {
    let (roster, identity, current, rids) = respawn_fixture();
    let scene = SceneProjection {
        roster: &roster,
        identity: &identity,
    };
    let batch = respawn_batch(1, [5., 6., 0.]);
    let context = OutputContext {
        at_ms: 1300,
        actor_poses: BTreeMap::new(),
    };
    let (messages, next) = project_client_messages_with_scene(
        &current,
        &batch,
        &context,
        Some(-1000),
        Some(5000),
        Some(1),
        &rids,
        Some(scene),
    )
    .unwrap();
    assert_eq!(
        messages
            .iter()
            .map(|message| message.command)
            .collect::<Vec<_>>(),
        [54, 54, 14, 14, 5, 4, 4, 4, 2]
    );
    assert_eq!(messages[0], scene_wire::actor_play_result(0));
    assert_eq!(messages[1], scene_wire::actor_state(101, 6, 6300).unwrap());
    assert_eq!(messages[2], scene_entry_wire::vision_del(101));
    assert_eq!(
        messages[3],
        scene_vision_wire::respawn_actor(1, [5., 6., 0.], 45., &roster, &identity).unwrap()
    );
    assert_eq!(&messages[3].body[45..47], &45_i16.to_be_bytes());
    assert_eq!(&messages[3].body[47..51], &5_f32.to_be_bytes());
    assert_eq!(&messages[3].body[51..55], &6_f32.to_be_bytes());
    assert_eq!(
        messages[4].body,
        [0, 6, 0, 0, 2, 0xbf, 0, 2, 0, 0, 0, 50, 0, 0, 0, 50]
    );
    assert_eq!(
        messages[5],
        client_battle_wire::encode_guard_value_for_client(5.).unwrap()
    );
    assert_eq!(messages[6], scene_entry_wire::idle_state(101, 8, 300));
    assert_eq!(messages[7].body, [0, 11, 0, 1, 0, 0, 0, 100]);
    assert_eq!(messages[8].body, [0, 0x1f, 0, 0, 1, 0x2c, 0, 1, 0]);
    let alive = [Output::Life {
        actor_id: 1,
        state: Life::Alive,
    }];
    let (active, _) = project_client_messages_with_scene(
        &next,
        &alive,
        &context,
        Some(-1000),
        Some(5000),
        Some(1),
        &rids,
        Some(SceneProjection {
            roster: &roster,
            identity: &identity,
        }),
    )
    .unwrap();
    assert_eq!(active, vec![scene_entry_wire::move_active(300, true)]);
    assert!(
        super::project_client_messages(
            &current,
            &batch,
            &context,
            Some(-1000),
            Some(5000),
            Some(1),
            &rids
        )
        .is_err()
    );
}

#[test]
fn bot_respawn_recreates_vision_at_authoritative_spawn_and_clears_motion() {
    let (roster, identity, mut current, rids) = respawn_fixture();
    current.bot_motion.insert(2, BotMotion::default());
    let batch = respawn_batch(2, [8., 9., 0.]);
    let (messages, next) = project_client_messages_with_scene(
        &current,
        &batch,
        &OutputContext {
            at_ms: 1300,
            actor_poses: BTreeMap::new(),
        },
        Some(-1000),
        Some(5000),
        Some(1),
        &rids,
        Some(SceneProjection {
            roster: &roster,
            identity: &identity,
        }),
    )
    .unwrap();
    assert_eq!(messages[0], scene_entry_wire::vision_del(202));
    assert_eq!(
        messages[1],
        scene_vision_wire::respawn_actor(2, [8., 9., 0.], 45., &roster, &identity).unwrap()
    );
    assert_eq!(&messages[1].body[47..51], &8_f32.to_be_bytes());
    assert_eq!(messages[2], scene_entry_wire::idle_state(202, 8, 300));
    assert_eq!(messages.len(), 3);
    assert!(!next.bot_motion.contains_key(&2));
}

#[test]
fn contact_evidence_loss_is_never_silently_projected() {
    let current = ClientOutputState {
        training: false,
        scores: BTreeMap::new(),
        camps: BTreeMap::new(),
        kill_target: 1,
        bots: BTreeSet::new(),
        bot_motion: BTreeMap::new(),
    };
    let error = project_client_messages(
        &current,
        &[Output::ContactOverflow { dropped: 1 }],
        None,
        None,
        None,
        &BTreeMap::new(),
    )
    .err()
    .expect("lost contact evidence must fail the output batch");
    assert_eq!(error.kind(), io::ErrorKind::InvalidData);
    assert!(error.to_string().contains("contact evidence loss"));
    assert!(current.scores.is_empty());
}

fn project_client_messages(
    current: &ClientOutputState,
    batch: &[Output],
    client_time_offset_ms: Option<i64>,
    server_time_offset_ms: Option<i64>,
    local_actor_id: Option<u32>,
    actor_rids: &BTreeMap<u32, u16>,
) -> io::Result<(Vec<ClientBattleMessage>, ClientOutputState)> {
    let at_ms = batch
        .iter()
        .find_map(|output| match output {
            Output::State(state) => Some(state.at_ms),
            Output::Ended(settlement) => Some(settlement.ended_at_ms),
            _ => None,
        })
        .unwrap_or(0);
    super::project_client_messages(
        current,
        batch,
        &OutputContext {
            at_ms,
            actor_poses: BTreeMap::new(),
        },
        client_time_offset_ms,
        server_time_offset_ms,
        local_actor_id,
        actor_rids,
    )
}

#[test]
fn damage_and_animation_keep_batch_order_and_distinct_wire_clocks() {
    let damage = Output::Damage {
        attacker: 1,
        target: 2,
        sequence: 7,
        health: 80,
        previous_health: 100,
        weapon_id: 701,
        evidence: Box::new(ContactEvent {
            guard_volume: false,
            actor_id: 1,
            target_id: 2,
            source_entity: 101,
            target_entity: 102,
            sequence: 7,
            phase_at_ms: 1200,
            target_intent: None,
            target_phase: Phase::Idle,
            target_phase_at_ms: 1200.,
            target_observed_at_ms: None,
            source_observed_at_ms: None,
            occurred_at_ms: 1250.5,
            along: 0.5,
            defence_weapon_id: 0,
            region: Region::Torso,
            body_index: 0,
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
            position: [1., 20., 0.],
            normal: [0., 0., 1.],
            direction: [1., 0., 0.],
            material: 0,
        }),
        guard_break: false,
        result: DamageResult {
            hp_damage: 20,
            ..DamageResult::default()
        },
    };
    let animation = Output::Action(ActionEvent::Animation(AnimationEvent {
        actor_id: 2,
        sequence: 8,
        at_ms: 1300,
        duration_ms: 200,
        target_id: Some(1),
        animation: Animation::Idle,
    }));
    let state = Output::State(MatchState {
        mode: 1,
        epoch: 7,
        revision: 2,
        base_revision: 1,
        at_ms: 1300,
        actors: Vec::new(),
    });
    let rids = BTreeMap::from([(1, 101), (2, 202)]);
    let current = ClientOutputState {
        training: false,
        scores: BTreeMap::from([(1, Score::default()), (2, Score::default())]),
        camps: BTreeMap::from([(1, 1), (2, 2)]),
        kill_target: 10,
        bots: BTreeSet::from([2]),
        bot_motion: BTreeMap::new(),
    };
    let batch = [
        damage.clone(),
        Output::Guard {
            actor_id: 1,
            value: 37.5,
        },
        Output::Guard {
            actor_id: 2,
            value: 20.,
        },
        animation,
        state,
    ];
    assert!(project_client_messages(&current, &batch, None, Some(5000), Some(1), &rids).is_err());
    assert!(project_client_messages(&current, &batch, Some(-1000), None, Some(1), &rids).is_err());
    assert!(
        project_client_messages(
            &current,
            &batch[..1],
            Some(-1000),
            Some(5000),
            Some(1),
            &rids
        )
        .is_err()
    );
    let (messages, _) =
        project_client_messages(&current, &batch, Some(-1000), Some(5000), Some(1), &rids).unwrap();
    assert_eq!(
        messages
            .iter()
            .map(|message| message.command)
            .collect::<Vec<_>>(),
        [15, 4, 4, 4, 4]
    );
    assert_eq!(
        u64::from_be_bytes(messages[0].body[30..38].try_into().unwrap()),
        6300
    );
    assert_eq!(
        u64::from_be_bytes(messages[1].body[79..87].try_into().unwrap()),
        6250
    );
    assert_eq!(
        messages[3].body,
        client_battle_wire::encode_guard_value_for_client(37.5)
            .unwrap()
            .body
    );
    assert_eq!(
        u32::from_be_bytes(messages[4].body[8..12].try_into().unwrap()),
        300
    );
    assert!(
        project_client_messages(&current, &batch, Some(-1000), Some(5000), None, &rids).is_err()
    );
    let mut evidence = match &damage {
        Output::Damage { evidence, .. } => (**evidence).clone(),
        _ => unreachable!(),
    };
    evidence.defence_weapon_id = 703;
    let parry = Output::Parry {
        outcome: crate::battle_session::ParryOutcome::Blocked,
        attacker: 1,
        target: 2,
        weapon_id: 701,
        defence_weapon_id: 703,
        evidence: Box::new(evidence),
    };
    let (feedback, _) = project_client_messages(
        &current,
        std::slice::from_ref(&parry),
        None,
        Some(5000),
        None,
        &rids,
    )
    .unwrap();
    assert_eq!(
        feedback,
        client_battle_wire::parry::encode_parry_for_client_clock(&parry, 5000, &rids,).unwrap()
    );
    assert!(matches!(damage, Output::Damage { evidence, .. } if evidence.occurred_at_ms == 1250.5));
}

#[test]
fn fatal_batch_projects_original_kill_score_death_order_transactionally() {
    let rids = BTreeMap::from([(1, 101), (2, 202)]);
    let current = ClientOutputState {
        training: false,
        scores: BTreeMap::from([(1, Score::default()), (2, Score::default())]),
        camps: BTreeMap::from([(1, 1), (2, 2)]),
        kill_target: 10,
        bots: BTreeSet::from([2]),
        bot_motion: BTreeMap::new(),
    };
    let damage = Output::Damage {
        attacker: 1,
        target: 2,
        sequence: 7,
        health: 0,
        previous_health: 100,
        weapon_id: 701,
        evidence: Box::new(ContactEvent {
            guard_volume: false,
            actor_id: 1,
            target_id: 2,
            source_entity: 101,
            target_entity: 102,
            sequence: 7,
            phase_at_ms: 1200,
            target_intent: None,
            target_phase: Phase::Idle,
            target_phase_at_ms: 1200.,
            target_observed_at_ms: None,
            source_observed_at_ms: None,
            occurred_at_ms: 1250.5,
            along: 0.5,
            defence_weapon_id: 0,
            region: Region::Torso,
            body_index: 0,
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
            position: [1., 20., 0.],
            normal: [0., 0., 1.],
            direction: [1., 0., 0.],
            material: 0,
        }),
        guard_break: false,
        result: DamageResult {
            hp_damage: 100,
            ..DamageResult::default()
        },
    };
    let state = Output::State(MatchState {
        mode: 1,
        epoch: 7,
        revision: 2,
        base_revision: 1,
        at_ms: 1300,
        actors: vec![StateActor {
            actor_id: 2,
            camp: 2,
            kind: 2,
            life: 2,
            intent: 0,
            phase: 0,
            flags: 0,
            sequence: 8,
            duration_ms: 0,
            hero_id: 1101,
            health: 0,
            guard: 0.,
            score: [0, 1, 0, 0],
            weapon_id: 701,
            offhand_id: 0,
            main_weapon_id: 701,
            shield_count: 0,
            shields: [(0, 0); 4],
            action_changed_at_ms: 0,
            life_at_ms: 1300,
            respawn_at_ms: 6300,
            spawn_position: [0.; 3],
            spawn_heading: 0.,
        }],
    });
    let batch = vec![
        damage,
        Output::Score {
            actor_id: 1,
            score: Score {
                kills: 1,
                points: 100,
                streak: 1,
                ..Score::default()
            },
        },
        Output::Life {
            actor_id: 2,
            state: Life::Dead {
                died_at_ms: 1300,
                respawn_at_ms: 6300,
            },
        },
        Output::Score {
            actor_id: 2,
            score: Score {
                deaths: 1,
                ..Score::default()
            },
        },
        Output::Kill {
            attacker: 1,
            target: 2,
            first_blood: true,
            butcher_ended: false,
        },
        state,
    ];
    let (messages, next) =
        project_client_messages(&current, &batch, Some(-1000), Some(5000), Some(1), &rids).unwrap();
    assert_eq!(
        messages
            .iter()
            .map(|message| message.command)
            .collect::<Vec<_>>(),
        [15, 4, 4, 10, 12, 12, 23, 4, 4]
    );
    assert_eq!(
        u16::from_be_bytes(messages[7].body[6..8].try_into().unwrap()),
        42
    );
    assert_eq!(
        u16::from_be_bytes(messages[7].body[4..6].try_into().unwrap()),
        9
    );
    assert_eq!(
        u32::from_be_bytes(messages[7].body[8..12].try_into().unwrap()),
        300
    );
    assert_eq!(
        u64::from_be_bytes(messages[8].body[10..18].try_into().unwrap()),
        6300
    );
    assert_eq!(current.scores[&1].kills, 0);
    assert_eq!(next.scores[&1].kills, 1);
    // Respawn owns the reserved death sequence. Later actions must retain
    // that same sequence space when native contacts return to the core.
    let action = t7r_battle_core::action::AnimationEvent {
        actor_id: 2,
        sequence: 10,
        at_ms: 6600,
        duration_ms: 400,
        target_id: None,
        animation: t7r_battle_core::action::Animation::Action {
            intent: t7r_battle_core::action::Intent::Attack(t7r_battle_core::action::Direction::Up),
            phase: Phase::Strike,
        },
    };
    let (after_respawn, _) = project_client_messages(
        &next,
        &[Output::Action(ActionEvent::Animation(action))],
        Some(-1000),
        Some(5000),
        Some(1),
        &rids,
    )
    .unwrap();
    assert_eq!(
        u16::from_be_bytes(after_respawn[0].body[4..6].try_into().unwrap()),
        action.sequence
    );
    assert!(
        project_client_messages(&next, &batch, Some(-1000), Some(5000), Some(1), &rids).is_err()
    );
    assert_eq!(sequence_with_offset(u16::MAX, 1).unwrap(), 1);
    assert!(sequence_with_offset(0, 1).is_err());

    let (walk, moving_state) = super::project_client_messages(
        &current,
        &[Output::Bot(BotOutput {
            actor_id: 2,
            target_id: Some(1),
            status: "approach",
            action: None,
            movement: MovementIntent::Follow {
                points: vec![[4., 20., 0.]],
                look_at: None,
            },
        })],
        &OutputContext {
            at_ms: 1200,
            actor_poses: BTreeMap::from([(
                2,
                SampledPose {
                    position: [3., 20., 0.],
                    heading: 0.,
                    sampled_at_ms: 1190,
                },
            )]),
        },
        Some(-1000),
        None,
        Some(1),
        &rids,
    )
    .unwrap();
    assert_eq!(walk.len(), 1);
    let (moving_death, after_death) = project_client_messages(
        &moving_state,
        &batch,
        Some(-1000),
        Some(5000),
        Some(1),
        &rids,
    )
    .unwrap();
    let stop_index = moving_death
        .iter()
        .position(|message| message.command == 2 && message.body.get(8) == Some(&1))
        .expect("moving bot must stop before death");
    let kill_index = moving_death
        .iter()
        .position(|message| message.command == 10)
        .unwrap();
    assert!(stop_index < kill_index);
    assert_eq!(
        f32::from_be_bytes(moving_death[stop_index].body[19..23].try_into().unwrap()),
        1.
    );
    let (repeated_stop, _) = super::project_client_messages(
        &after_death,
        &[Output::Bot(BotOutput {
            actor_id: 2,
            target_id: None,
            status: "stopped",
            action: None,
            movement: MovementIntent::Stop,
        })],
        &OutputContext {
            at_ms: 1400,
            actor_poses: BTreeMap::new(),
        },
        Some(-1000),
        None,
        Some(1),
        &rids,
    )
    .unwrap();
    assert!(repeated_stop.is_empty());

    let mut local_victim = batch.clone();
    if let Some(Output::State(state)) = local_victim.last_mut() {
        state.actors[0].kind = 1;
    }
    let mut victim_state = current.clone();
    victim_state.bots = BTreeSet::from([1]);
    let (victim_messages, _) = project_client_messages(
        &victim_state,
        &local_victim,
        Some(-1000),
        Some(5000),
        Some(2),
        &rids,
    )
    .unwrap();
    assert_eq!(
        victim_messages[victim_messages.len() - 3],
        scene_entry_wire::move_active(300, false)
    );
    assert_eq!(
        victim_messages.last(),
        Some(&scene_wire::actor_state(202, 9, 6300).unwrap())
    );
}

#[test]
fn local_human_selection_uses_native_server_clock_and_bound_rid() {
    let rids = BTreeMap::from([(1, 101)]);
    let current = ClientOutputState {
        training: false,
        scores: BTreeMap::from([(1, Score::default())]),
        camps: BTreeMap::from([(1, 1)]),
        kill_target: 10,
        bots: BTreeSet::new(),
        bot_motion: BTreeMap::new(),
    };
    let batch = [
        Output::Life {
            actor_id: 1,
            state: Life::Selecting { died_at_ms: 1300 },
        },
        Output::State(MatchState {
            mode: 1,
            epoch: 7,
            revision: 3,
            base_revision: 2,
            at_ms: 4300,
            actors: Vec::new(),
        }),
    ];
    let (messages, _) =
        project_client_messages(&current, &batch, None, Some(5000), Some(1), &rids).unwrap();
    assert_eq!(
        messages,
        vec![scene_wire::actor_state(101, 7, 9300).unwrap()]
    );
    assert!(project_client_messages(&current, &batch, None, None, Some(1), &rids).is_err());
}

#[test]
fn settlement_start_uses_native_server_clock_without_mutating_authoritative_output() {
    use crate::battle_session::{EndReason, RankedScore, Settlement};

    let rids = BTreeMap::from([(1, 101), (2, 202)]);
    let mut current = ClientOutputState {
        training: false,
        scores: BTreeMap::from([(1, Score::default()), (2, Score::default())]),
        camps: BTreeMap::from([(1, 1), (2, 2)]),
        kill_target: 10,
        bots: BTreeSet::from([2]),
        bot_motion: BTreeMap::new(),
    };
    let settlement = Settlement {
        epoch: 7,
        started_at_ms: 1000,
        ended_at_ms: 2000,
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
                    streak: 0,
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
    for member in &settlement.members {
        current.scores.insert(member.actor_id, member.score);
    }
    let batch = [Output::Ended(settlement.clone())];
    let mut stale = settlement.clone();
    stale.members[0].score.kills -= 1;
    assert!(
        project_client_messages(
            &current,
            &[Output::Ended(stale)],
            None,
            Some(5000),
            Some(1),
            &rids,
        )
        .is_err()
    );
    assert!(project_client_messages(&current, &batch, None, None, Some(1), &rids).is_err());
    let (messages, next) =
        project_client_messages(&current, &batch, None, Some(5000), Some(1), &rids).unwrap();
    let mut wire_settlement = settlement.clone();
    wire_settlement.started_at_ms += 5000;
    wire_settlement.ended_at_ms += 5000;
    assert_eq!(
        messages,
        client_battle_wire::encode_settlement_start_for_client(&wire_settlement, &rids).unwrap()
    );
    assert_eq!(
        messages
            .iter()
            .map(|message| message.command)
            .collect::<Vec<_>>(),
        [12, 12, 10, 10]
    );
    assert_eq!(settlement.ended_at_ms, 2000);
    assert_eq!(next.scores[&1], current.scores[&1]);
}

#[test]
fn bot_movement_uses_its_published_pose_and_stages_run_stop_state() {
    let rids = BTreeMap::from([(1, 101), (2, 202)]);
    let current = ClientOutputState {
        training: false,
        scores: BTreeMap::from([(1, Score::default()), (2, Score::default())]),
        camps: BTreeMap::from([(1, 1), (2, 2)]),
        kill_target: 10,
        bots: BTreeSet::from([2]),
        bot_motion: BTreeMap::new(),
    };
    let follow = Output::Bot(BotOutput {
        actor_id: 2,
        target_id: Some(1),
        status: "approach",
        action: None,
        movement: MovementIntent::Follow {
            points: vec![[1.25, 20.5, -2.], [3.25, 20.5, -2.]],
            look_at: None,
        },
    });
    let first_context = OutputContext {
        at_ms: 1300,
        actor_poses: BTreeMap::from([(
            2,
            SampledPose {
                position: [1.25, 20.5, -2.],
                heading: -45.,
                sampled_at_ms: 1250,
            },
        )]),
    };
    let (walk, next) = super::project_client_messages(
        &current,
        std::slice::from_ref(&follow),
        &first_context,
        Some(-1000),
        None,
        Some(1),
        &rids,
    )
    .unwrap();
    assert_eq!(walk.len(), 1);
    assert_eq!(walk[0].command, 2);
    assert_eq!(
        u32::from_be_bytes(walk[0].body[2..6].try_into().unwrap()),
        300
    );
    assert_eq!(
        u16::from_be_bytes(walk[0].body[6..8].try_into().unwrap()),
        202
    );
    assert_eq!(walk[0].body[8], 2);
    assert_eq!(
        i16::from_be_bytes(walk[0].body[31..33].try_into().unwrap()),
        -9
    );
    assert!(current.bot_motion.is_empty());

    let run_context = OutputContext {
        at_ms: 2300,
        actor_poses: BTreeMap::from([(
            2,
            SampledPose {
                position: [2., 20.5, -2.],
                heading: -9.,
                sampled_at_ms: 2250,
            },
        )]),
    };
    let (run, next) = super::project_client_messages(
        &next,
        std::slice::from_ref(&follow),
        &run_context,
        Some(-1000),
        None,
        Some(1),
        &rids,
    )
    .unwrap();
    assert_eq!(run.len(), 1);
    assert_eq!(run[0].body[8], 10);
    assert_eq!(
        i32::from_be_bytes(run[0].body[13..17].try_into().unwrap()),
        5000
    );
    let stop_context = OutputContext {
        at_ms: 2400,
        actor_poses: BTreeMap::new(),
    };
    let stop = Output::Bot(BotOutput {
        actor_id: 2,
        target_id: None,
        status: "world-unavailable",
        action: None,
        movement: MovementIntent::Stop,
    });
    let (fresh_stop, _) = super::project_client_messages(
        &next,
        std::slice::from_ref(&stop),
        &OutputContext {
            at_ms: 2400,
            actor_poses: BTreeMap::from([(
                2,
                SampledPose {
                    position: [2.25, 20.5, -2.],
                    heading: 30.,
                    sampled_at_ms: 2390,
                },
            )]),
        },
        Some(-1000),
        None,
        Some(1),
        &rids,
    )
    .unwrap();
    assert_eq!(
        i16::from_be_bytes(fresh_stop[0].body[31..33].try_into().unwrap()),
        30
    );
    let (stopped, next) = super::project_client_messages(
        &next,
        &[stop],
        &stop_context,
        Some(-1000),
        None,
        Some(1),
        &rids,
    )
    .unwrap();
    assert_eq!(stopped.len(), 1);
    assert_eq!(stopped[0].body[8], 1);
    assert_eq!(
        u32::from_be_bytes(stopped[0].body[2..6].try_into().unwrap()),
        1400
    );
    let again = Output::Bot(BotOutput {
        actor_id: 2,
        target_id: None,
        status: "idle",
        action: None,
        movement: MovementIntent::Stop,
    });
    let (repeated, _) = super::project_client_messages(
        &next,
        &[again],
        &OutputContext {
            at_ms: 2500,
            actor_poses: BTreeMap::new(),
        },
        Some(-1000),
        None,
        Some(1),
        &rids,
    )
    .unwrap();
    assert!(repeated.is_empty());

    let stale = OutputContext {
        at_ms: 2600,
        actor_poses: first_context.actor_poses,
    };
    assert!(
        super::project_client_messages(
            &current,
            &[follow],
            &stale,
            Some(-1000),
            None,
            Some(1),
            &rids,
        )
        .is_err()
    );
}

#[test]
fn bot_turn_uses_sampled_heading_and_original_direction_packet() {
    let rids = BTreeMap::from([(1, 101), (2, 202)]);
    let current = ClientOutputState {
        training: false,
        scores: BTreeMap::from([(1, Score::default()), (2, Score::default())]),
        camps: BTreeMap::from([(1, 1), (2, 2)]),
        kill_target: 10,
        bots: BTreeSet::from([2]),
        bot_motion: BTreeMap::new(),
    };
    let output = Output::Bot(BotOutput {
        actor_id: 2,
        target_id: Some(1),
        status: "turning",
        action: None,
        movement: MovementIntent::Turn {
            target: [3.25, 20.5, -2.],
        },
    });
    let context = OutputContext {
        at_ms: 1300,
        actor_poses: BTreeMap::from([(
            2,
            SampledPose {
                position: [1.25, 20.5, -2.],
                heading: -45.,
                sampled_at_ms: 1250,
            },
        )]),
    };
    let (messages, _) = super::project_client_messages(
        &current,
        &[output],
        &context,
        Some(-1000),
        None,
        Some(1),
        &rids,
    )
    .unwrap();
    assert_eq!(
        messages,
        vec![client_battle_wire::encode_bot_turn_for_client(300, 202, -9).unwrap()]
    );
}

#[test]
fn bot_follows_only_after_turning_toward_the_route() {
    let rids = BTreeMap::from([(1, 101), (2, 202)]);
    let current = ClientOutputState {
        training: false,
        scores: BTreeMap::from([(1, Score::default()), (2, Score::default())]),
        camps: BTreeMap::from([(1, 1), (2, 2)]),
        kill_target: 10,
        bots: BTreeSet::from([2]),
        bot_motion: BTreeMap::new(),
    };
    let output = Output::Bot(BotOutput {
        actor_id: 2,
        target_id: Some(1),
        status: "approach",
        action: None,
        movement: MovementIntent::Follow {
            points: vec![[0., 20., 0.], [3., 20., 0.]],
            look_at: None,
        },
    });
    let context = OutputContext {
        at_ms: 1300,
        actor_poses: BTreeMap::from([(
            2,
            SampledPose {
                position: [0., 20., 0.],
                heading: 180.,
                sampled_at_ms: 1250,
            },
        )]),
    };
    let (messages, _) = super::project_client_messages(
        &current,
        &[output],
        &context,
        Some(-1000),
        None,
        Some(1),
        &rids,
    )
    .unwrap();
    assert_eq!(
        messages,
        vec![client_battle_wire::encode_bot_turn_for_client(300, 202, 144).unwrap()]
    );
}
