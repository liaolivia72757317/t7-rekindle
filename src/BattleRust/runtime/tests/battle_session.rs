use std::{collections::BTreeMap, io, sync::Arc};
#[path = "battle_session/health.rs"]
mod health;
#[path = "battle_session/weapon_guard.rs"]
mod weapon_guard;
use t7r_battle_core::{
    action::{ActionEvent, Animation, Command, Direction, HurtKind, Intent, Phase, TimerKind},
    ai::{ContactObservation, ContactState, MotionAttempt},
    damage::Region,
};
use t7r_battle_resources::ResourceBundle;
use t7r_battle_runtime::{
    battle_resources::BattleResources,
    battle_session::{
        BattleSession, Config, Input, Life, Member, MemberKind, Output, Pose, SourceWorld, World,
        contacts::{ContactBatch, ContactEvent},
    },
};

fn bundle() -> ResourceBundle {
    ResourceBundle::from_slice(include_bytes!(
        "../../../../tests/fixtures/resources/bundle-v1.json"
    ))
    .unwrap()
}
fn member(id: u32) -> Member {
    Member {
        actor_id: id,
        kind: if id == 1 {
            MemberKind::Human
        } else {
            MemberKind::Bot
        },
        sample_source_id: 1,
        camp: if id == 1 { 1 } else { 2 },
        hero_id: 1101,
        weapon_id: 701,
        equipment: vec![t7r_battle_core::action::EquipmentItem {
            slot: 1,
            weapon_id: 701,
        }],
    }
}
fn session() -> BattleSession {
    let bundle = bundle();
    let prepared = Arc::new(BattleResources::prepare(&bundle).unwrap());
    BattleSession::new(
        &bundle,
        prepared,
        Config {
            epoch: 7,
            started_at_ms: 0,
            duration_ms: 1200000,
            kill_target: 10,
            seed: 42,
            members: vec![member(1), member(2)],
        },
    )
    .unwrap()
}

#[test]
fn source_weapon_change_rolls_back_failed_publication_and_rejects_foreign_source() {
    let mut session = session();
    let input = |source_id| Input::SourceAction {
        source_id,
        actor_id: 1,
        command: Command::ChangeSlot(13),
    };
    assert!(session.dispatch(7, 100, input(2), |_| Ok(())).is_err());
    assert!(session.dispatch(6, 100, input(1), |_| Ok(())).is_err());
    let before = session.actor(1).unwrap();
    let failed = session
        .dispatch(7, 100, input(1), |events| {
            assert!(events.iter().any(|output| matches!(
                output,
                Output::Action(ActionEvent::EquipmentChanged {
                    actor_id: 1,
                    weapon_id: 100000,
                    ..
                })
            )));
            Err(io::Error::new(
                io::ErrorKind::WouldBlock,
                "output queue full",
            ))
        })
        .unwrap_err();
    assert_eq!(failed.kind(), io::ErrorKind::WouldBlock);
    assert_eq!(session.actor(1).unwrap(), before);
    session.dispatch(7, 100, input(1), |_| Ok(())).unwrap();
    assert_eq!(session.actor(1).unwrap().action.weapon_id, 100000);
}
fn world(at: u64, source_seq: u16) -> Arc<World> {
    Arc::new(World {
        epoch: 7,
        sampled_at_ms: at,
        actors: BTreeMap::from([
            (
                1,
                Pose {
                    entity: 101,
                    position: [0., 20., 0.],
                    heading: 0.,
                    sequence: source_seq,
                    contact: ContactObservation::unavailable(),
                },
            ),
            (
                2,
                Pose {
                    entity: 102,
                    position: [1., 20., 0.],
                    heading: 180.,
                    sequence: 1,
                    contact: ContactObservation::unavailable(),
                },
            ),
        ]),
    })
}

fn submit_shared_world(session: &mut BattleSession, at: u64, sequence: u16, batch_id: u64) {
    let base = world(at, sequence);
    let mut bot = base.actors[&2].clone();
    bot.entity = 103;
    bot.position = [1., 20., 1.];
    for (source_id, actors) in [
        (1, BTreeMap::from([(1, base.actors[&1].clone()), (3, bot)])),
        (2, BTreeMap::from([(2, base.actors[&2].clone())])),
    ] {
        session
            .dispatch(
                7,
                at,
                Input::SourceWorld(Arc::new(SourceWorld {
                    epoch: 7,
                    source_id,
                    batch_id,
                    sampled_at_ms: at,
                    actors,
                })),
                publish,
            )
            .unwrap();
    }
}

fn source_world(source_id: u64, batch_id: u64, at: u64, actors: BTreeMap<u32, Pose>) -> Input {
    Input::SourceWorld(Arc::new(SourceWorld {
        epoch: 7,
        source_id,
        batch_id,
        sampled_at_ms: at,
        actors,
    }))
}
fn sourced_attack(session: &mut BattleSession, source_id: u64, actor_id: u32) {
    for command in [
        Command::Press {
            intent: Intent::Attack(Direction::Up),
            defence_down: false,
            dash_run: false,
            dash_forward: false,
        },
        Command::ReleaseAttack,
    ] {
        session
            .dispatch(
                7,
                0,
                Input::SourceAction {
                    source_id,
                    actor_id,
                    command,
                },
                publish,
            )
            .unwrap();
    }
}
fn contact(sequence: u16, phase_at: u64, at: f64) -> ContactEvent {
    ContactEvent {
        guard_volume: false,
        actor_id: 1,
        target_id: 2,
        source_entity: 101,
        target_entity: 102,
        sequence,
        phase_at_ms: phase_at,
        target_intent: None,
        target_phase: Phase::Idle,
        target_phase_at_ms: 0.,
        target_observed_at_ms: None,
        source_observed_at_ms: None,
        occurred_at_ms: at,
        along: 0.5,
        defence_weapon_id: 0,
        region: Region::Torso,
        body_index: -1,
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
        position: [0.5, 20., 1.],
        normal: [-1., 0., 0.],
        direction: [1., 0., 0.],
        material: 0,
    }
}
fn publish(_: &[Output]) -> io::Result<()> {
    Ok(())
}

#[test]
fn authoritative_state_uses_actor_events_and_periodic_recovery_checkpoints() {
    let mut battle = session();
    let mut first = Vec::new();
    battle
        .dispatch(7, 0, Input::World(world(0, 1)), |events| {
            first = events.to_vec();
            Ok(())
        })
        .unwrap();
    let state = first
        .iter()
        .find_map(|event| match event {
            Output::State(value) => Some(value),
            _ => None,
        })
        .unwrap();
    assert_eq!(
        (
            state.mode,
            state.revision,
            state.base_revision,
            state.actors.len()
        ),
        (2, 1, 0, 2)
    );

    let mut delta = Vec::new();
    battle
        .dispatch(
            7,
            10,
            Input::Action {
                actor_id: 1,
                command: Command::Press {
                    intent: Intent::Attack(Direction::Up),
                    defence_down: false,
                    dash_run: false,
                    dash_forward: false,
                },
            },
            |events| {
                delta = events.to_vec();
                Ok(())
            },
        )
        .unwrap();
    let state = delta
        .iter()
        .find_map(|event| match event {
            Output::State(value) => Some(value),
            _ => None,
        })
        .unwrap();
    assert_eq!(
        (
            state.mode,
            state.revision,
            state.base_revision,
            state.actors.len()
        ),
        (1, 2, 1, 1)
    );
    assert_eq!((state.actors[0].actor_id, state.actors[0].intent), (1, 1));

    let mut checkpoint = Vec::new();
    battle
        .dispatch(7, 1000, Input::Timers, |events| {
            checkpoint = events.to_vec();
            Ok(())
        })
        .unwrap();
    let state = checkpoint
        .iter()
        .find_map(|event| match event {
            Output::State(value) => Some(value),
            _ => None,
        })
        .unwrap();
    assert_eq!(
        (state.mode, state.base_revision, state.actors.len()),
        (2, 0, 2)
    );
    assert!(state.revision >= 2);
}

#[test]
fn state_revision_rolls_back_with_failed_publication() {
    let mut battle = session();
    assert!(
        battle
            .dispatch(7, 0, Input::World(world(0, 1)), |_| Err(io::Error::other(
                "full"
            )))
            .is_err()
    );
    let mut accepted = Vec::new();
    battle
        .dispatch(7, 0, Input::World(world(0, 1)), |events| {
            accepted = events.to_vec();
            Ok(())
        })
        .unwrap();
    let state = accepted
        .iter()
        .find_map(|event| match event {
            Output::State(value) => Some(value),
            _ => None,
        })
        .unwrap();
    assert_eq!((state.mode, state.revision), (2, 1));
}

#[test]
fn action_deadline_is_separate_from_ai_pulse() {
    let mut session = session();
    assert_eq!(session.next_deadline_ms(None), Some(1200000));
    session
        .dispatch(7, 0, Input::World(world(0, 1)), publish)
        .unwrap();
    let attack = Command::Start {
        intent: Intent::Attack(Direction::Up),
        released: true,
    };
    session
        .dispatch(
            7,
            0,
            Input::Action {
                actor_id: 1,
                command: attack,
            },
            publish,
        )
        .unwrap();
    let due = session.actor(1).unwrap().action.phase_due_ms.unwrap();
    assert_eq!(session.next_deadline_ms(None), Some(due));
    assert_eq!(session.next_deadline_ms(Some(due)), Some(1200000));
    let mut events = Vec::new();
    session
        .dispatch(7, due, Input::Timers, |batch| {
            events.extend_from_slice(batch);
            Ok(())
        })
        .unwrap();
    assert_eq!(session.actor(1).unwrap().action.phase, Phase::Strike);
    assert!(
        events
            .iter()
            .any(|event| matches!(event, Output::Action(_)))
    );
    assert!(!events.iter().any(|event| matches!(event, Output::Bot(_))));
}

#[test]
fn action_contact_and_publish_failure_keep_state_transactional() {
    let mut session = session();
    session
        .dispatch(7, 0, Input::World(world(0, 1)), publish)
        .unwrap();
    session
        .dispatch(
            7,
            0,
            Input::Action {
                actor_id: 1,
                command: Command::Start {
                    intent: Intent::Attack(Direction::Up),
                    released: true,
                },
            },
            publish,
        )
        .unwrap();
    let due = session.actor(1).unwrap().action.phase_due_ms.unwrap();
    session.dispatch(7, due, Input::Timers, publish).unwrap();
    let action = session.actor(1).unwrap().action;
    assert_eq!(action.phase, Phase::Strike);
    session
        .dispatch(
            7,
            due + 20,
            Input::World(world(due + 20, action.sequence)),
            publish,
        )
        .unwrap();
    let batch = ContactBatch {
        epoch: 7,
        batch_id: 1,
        through_at_ms: (due + 300) as f64,
        dropped: 0,
        events: vec![contact(action.sequence, due, (due + 300) as f64)],
    };
    let before = session.actor(2).unwrap();
    assert!(
        session
            .dispatch(7, due + 300, Input::Contacts(batch.clone()), |_| Err(
                io::Error::other("sink failed")
            ))
            .is_err()
    );
    assert_eq!(session.actor(2).unwrap().health, before.health);
    let mut outputs = Vec::new();
    session
        .dispatch(7, due + 300, Input::Contacts(batch.clone()), |events| {
            outputs.extend_from_slice(events);
            Ok(())
        })
        .unwrap();
    assert!(session.actor(2).unwrap().health < before.health);
    assert!(outputs.iter().any(|e| matches!(
        e,
        Output::Damage {
            attacker: 1,
            target: 2,
            ..
        }
    )));
    let after = session.actor(2).unwrap().health;
    session
        .dispatch(7, due + 300, Input::Contacts(batch), |events| {
            assert!(events.is_empty());
            Ok(())
        })
        .unwrap();
    assert_eq!(session.actor(2).unwrap().health, after);
    assert!(
        session
            .dispatch(8, due + 300, Input::Tick, publish)
            .is_err()
    );
    assert_eq!(session.actor(2).unwrap().health, after);
}

#[test]
fn surface_contacts_emit_client_evidence_once_per_accepted_event() {
    for ground in [true, false] {
        let mut battle = session();
        battle
            .dispatch(7, 0, Input::World(world(0, 1)), publish)
            .unwrap();
        battle
            .dispatch(
                7,
                0,
                Input::Action {
                    actor_id: 1,
                    command: Command::Start {
                        intent: Intent::Attack(Direction::Up),
                        released: true,
                    },
                },
                publish,
            )
            .unwrap();
        let due = battle.actor(1).unwrap().action.phase_due_ms.unwrap();
        battle.dispatch(7, due, Input::Timers, publish).unwrap();
        let sequence = battle.actor(1).unwrap().action.sequence;
        battle
            .dispatch(
                7,
                due + 20,
                Input::World(world(due + 20, sequence)),
                publish,
            )
            .unwrap();
        let mut event = contact(sequence, due, (due + 300) as f64);
        event.target_id = 0;
        event.target_entity = 0;
        event.normal = if ground { [0., 0., 1.] } else { [1., 0., 0.] };
        event.position = [0.5, 20., 0.];
        let batch = ContactBatch {
            epoch: 7,
            batch_id: 1,
            through_at_ms: event.occurred_at_ms,
            dropped: 0,
            events: vec![event.clone()],
        };
        let mut outputs = Vec::new();
        battle
            .dispatch(7, due + 300, Input::Contacts(batch.clone()), |events| {
                outputs.extend_from_slice(events);
                Ok(())
            })
            .unwrap();
        let hits = outputs
            .iter()
            .filter_map(|output| match output {
                Output::SurfaceHit {
                    attacker,
                    weapon_id,
                    ground: hit_ground,
                    evidence,
                } => Some((*attacker, *weapon_id, *hit_ground, evidence.as_ref())),
                _ => None,
            })
            .collect::<Vec<_>>();
        assert_eq!(hits.len(), 1);
        assert_eq!((hits[0].0, hits[0].1, hits[0].2), (1, 701, ground));
        assert_eq!(hits[0].3, &event);
        battle
            .dispatch(7, due + 300, Input::Contacts(batch), |events| {
                assert!(events.is_empty());
                Ok(())
            })
            .unwrap();
        let repeat = ContactBatch {
            epoch: 7,
            batch_id: 2,
            through_at_ms: event.occurred_at_ms + 1.,
            dropped: 0,
            events: vec![event],
        };
        battle
            .dispatch(7, due + 301, Input::Contacts(repeat), |events| {
                assert!(
                    !events
                        .iter()
                        .any(|event| matches!(event, Output::SurfaceHit { .. }))
                );
                Ok(())
            })
            .unwrap();
    }
}

#[test]
fn native_capacity_bots_tick_without_simulating_positions_or_accepting_stale_world() {
    for count in [10, 20, 60] {
        let bundle = bundle();
        let prepared = Arc::new(BattleResources::prepare(&bundle).unwrap());
        let members = (1..=count).map(member).collect();
        let mut session = BattleSession::new(
            &bundle,
            prepared,
            Config {
                epoch: 7,
                started_at_ms: 0,
                duration_ms: 1200000,
                kill_target: 20,
                seed: 7,
                members,
            },
        )
        .unwrap();
        let poses = (1..=count)
            .map(|id| {
                (
                    id,
                    Pose {
                        entity: id as u64 + 100,
                        position: [id as f64, 20., 0.],
                        heading: 0.,
                        sequence: 1,
                        contact: ContactObservation::unavailable(),
                    },
                )
            })
            .collect();
        session
            .dispatch(
                7,
                0,
                Input::World(Arc::new(World {
                    epoch: 7,
                    sampled_at_ms: 0,
                    actors: poses,
                })),
                publish,
            )
            .unwrap();
        let mut outputs = Vec::new();
        session
            .dispatch(7, 100, Input::Tick, |events| {
                outputs.extend_from_slice(events);
                Ok(())
            })
            .unwrap();
        assert_eq!(
            outputs
                .iter()
                .filter(|e| matches!(e, Output::Bot(_)))
                .count(),
            (count - 1) as usize
        );
        assert_eq!(session.actor(2).unwrap().life, Life::Alive);
        let stale = Arc::new(World {
            epoch: 7,
            sampled_at_ms: 0,
            actors: BTreeMap::new(),
        });
        session
            .dispatch(7, 500, Input::World(stale), publish)
            .unwrap();
        let mut stopped = Vec::new();
        session
            .dispatch(7, 500, Input::Tick, |events| {
                stopped.extend_from_slice(events);
                Ok(())
            })
            .unwrap();
        assert_eq!(
            stopped
                .iter()
                .filter(|e| matches!(e,Output::Bot(bot) if bot.status=="world-unavailable"))
                .count(),
            (count - 1) as usize
        );
    }
}

#[test]
fn parried_contact_debits_guard_without_body_damage_and_validates_entity() {
    let mut session = session();
    session
        .dispatch(7, 0, Input::World(world(0, 1)), publish)
        .unwrap();
    session
        .dispatch(
            7,
            0,
            Input::Action {
                actor_id: 1,
                command: Command::Start {
                    intent: Intent::Attack(Direction::Up),
                    released: true,
                },
            },
            publish,
        )
        .unwrap();
    let due = session.actor(1).unwrap().action.phase_due_ms.unwrap();
    session
        .dispatch(
            7,
            due,
            Input::Action {
                actor_id: 1,
                command: Command::Timer {
                    kind: TimerKind::Phase,
                    deadline_ms: due,
                },
            },
            publish,
        )
        .unwrap();
    let action = session.actor(1).unwrap().action;
    session
        .dispatch(
            7,
            due + 10,
            Input::World(world(due + 10, action.sequence)),
            publish,
        )
        .unwrap();
    let mut event = contact(action.sequence, due, (due + 250) as f64);
    event.defence_weapon_id = 701;
    event.target_intent = Some(Intent::Guard(Direction::Up));
    event.target_phase = Phase::Hold;
    let before = session.actor(2).unwrap();
    let batch = |id, contact: ContactEvent| ContactBatch {
        epoch: 7,
        batch_id: id,
        through_at_ms: contact.occurred_at_ms,
        dropped: 0,
        events: vec![contact],
    };
    let mut wrong = event.clone();
    wrong.target_entity = 999;
    session
        .dispatch(7, due + 250, Input::Contacts(batch(1, wrong)), publish)
        .unwrap();
    assert_eq!(session.actor(2).unwrap().guard, before.guard);
    let mut output = Vec::new();
    session
        .dispatch(7, due + 250, Input::Contacts(batch(2, event)), |events| {
            output.extend_from_slice(events);
            Ok(())
        })
        .unwrap();
    assert_eq!(session.actor(2).unwrap().health, before.health);
    assert!(session.actor(2).unwrap().guard < before.guard);
    assert!(
        output
            .iter()
            .any(|value| matches!(value, Output::Guard { actor_id: 2, .. }))
    );
    assert_eq!(session.actor(1).unwrap().action.phase, Phase::Parried);
}

#[test]
fn broken_shield_disarms_and_publishes_original_hurt_state() {
    let mut raw: serde_json::Value = serde_json::from_slice(include_bytes!(
        "../../../../tests/fixtures/resources/bundle-v1.json"
    ))
    .unwrap();
    raw["damageProfiles"]["1101"]["weapons"]["703"]["shieldMax"] = serde_json::json!(1);
    let bundle = ResourceBundle::from_slice(&serde_json::to_vec(&raw).unwrap()).unwrap();
    let prepared = Arc::new(BattleResources::prepare(&bundle).unwrap());
    let mut shielded = member(2);
    shielded
        .equipment
        .push(t7r_battle_core::action::EquipmentItem {
            slot: 3,
            weapon_id: 703,
        });
    let mut session = BattleSession::new(
        &bundle,
        prepared,
        Config {
            epoch: 7,
            started_at_ms: 0,
            duration_ms: 1200000,
            kill_target: 10,
            seed: 42,
            members: vec![member(1), shielded],
        },
    )
    .unwrap();
    assert_eq!(session.actor(2).unwrap().action.offhand_id, 703);
    session
        .dispatch(7, 0, Input::World(world(0, 1)), publish)
        .unwrap();
    session
        .dispatch(
            7,
            0,
            Input::Action {
                actor_id: 1,
                command: Command::Start {
                    intent: Intent::Attack(Direction::Up),
                    released: true,
                },
            },
            publish,
        )
        .unwrap();
    let due = session.actor(1).unwrap().action.phase_due_ms.unwrap();
    session
        .dispatch(
            7,
            due,
            Input::Action {
                actor_id: 1,
                command: Command::Timer {
                    kind: TimerKind::Phase,
                    deadline_ms: due,
                },
            },
            publish,
        )
        .unwrap();
    let sequence = session.actor(1).unwrap().action.sequence;
    session
        .dispatch(
            7,
            due + 10,
            Input::World(world(due + 10, sequence)),
            publish,
        )
        .unwrap();
    let mut event = contact(sequence, due, (due + 250) as f64);
    event.defence_weapon_id = 703;
    event.target_intent = Some(Intent::Shield);
    event.target_phase = Phase::Hold;
    let mut outputs = Vec::new();
    session
        .dispatch(
            7,
            due + 250,
            Input::Contacts(ContactBatch {
                epoch: 7,
                batch_id: 1,
                through_at_ms: (due + 250) as f64,
                dropped: 0,
                events: vec![event],
            }),
            |batch| {
                outputs.extend_from_slice(batch);
                Ok(())
            },
        )
        .unwrap();
    assert!(outputs.iter().any(|value| matches!(
        value,
        Output::Shield {
            actor_id: 2,
            weapon_id: 703,
            health: 0
        }
    )));
    assert!(outputs.iter().any(|value| matches!(value,
        Output::Action(ActionEvent::Animation(animation))
            if animation.actor_id == 2
                && animation.animation == Animation::Hurt(HurtKind::ShieldBreak)
    )));
    assert_eq!(session.actor(2).unwrap().action.offhand_id, 0);
    assert_eq!(session.actor(2).unwrap().action.phase, Phase::Hurt);
    let parry_at = outputs
        .iter()
        .position(|value| {
            matches!(
                value,
                Output::Parry {
                    attacker: 1,
                    target: 2,
                    defence_weapon_id: 703,
                    ..
                }
            )
        })
        .unwrap();
    let shield_at = outputs
        .iter()
        .position(|value| {
            matches!(
                value,
                Output::Shield {
                    actor_id: 2,
                    weapon_id: 703,
                    health: 0
                }
            )
        })
        .unwrap();
    let break_at = outputs
        .iter()
        .position(|value| {
            matches!(value,
            Output::Action(ActionEvent::Animation(animation))
                if animation.actor_id == 2
                    && animation.animation == Animation::Hurt(HurtKind::ShieldBreak))
        })
        .unwrap();
    assert!(parry_at < shield_at && shield_at < break_at);
}

fn lethal_session(kill_target: u32) -> BattleSession {
    lethal_session_with_members(kill_target, vec![member(1), member(2)])
}

fn lethal_session_with_members(kill_target: u32, members: Vec<Member>) -> BattleSession {
    let mut raw: serde_json::Value = serde_json::from_slice(include_bytes!(
        "../../../../tests/fixtures/resources/bundle-v1.json"
    ))
    .unwrap();
    raw["damageProfiles"]["1101"]["weapons"]["701"]["attacks"]["300560"]["base"] =
        serde_json::json!(500);
    let bundle = ResourceBundle::from_slice(&serde_json::to_vec(&raw).unwrap()).unwrap();
    let prepared = Arc::new(BattleResources::prepare(&bundle).unwrap());
    BattleSession::new(
        &bundle,
        prepared,
        Config {
            epoch: 7,
            started_at_ms: 0,
            duration_ms: 1200000,
            kill_target,
            seed: 42,
            members,
        },
    )
    .unwrap()
}

#[test]
fn shared_roster_distinguishes_human_and_bot_life_and_ai() {
    let mut second = member(2);
    second.kind = MemberKind::Human;
    second.sample_source_id = 2;
    let mut third = member(3);
    third.camp = 2;
    let mut session = lethal_session_with_members(10, vec![member(1), second, third]);
    submit_shared_world(&mut session, 0, 1, 1);
    let mut outputs = Vec::new();
    session
        .dispatch(7, 0, Input::Tick, |batch| {
            outputs.extend_from_slice(batch);
            Ok(())
        })
        .unwrap();
    assert_eq!(
        outputs
            .iter()
            .filter_map(|event| match event {
                Output::Bot(bot) => Some(bot.actor_id),
                _ => None,
            })
            .collect::<Vec<_>>(),
        [3]
    );
    assert!(
        session
            .dispatch(
                7,
                0,
                Input::SourceSpawn {
                    source_id: 1,
                    actor_id: 3
                },
                publish
            )
            .is_err()
    );

    let died = lethal_contact_with_mode(&mut session, true);
    assert_eq!(
        session.actor(2).unwrap().life,
        Life::Dead {
            died_at_ms: died,
            respawn_at_ms: died + 8000,
        }
    );
    let due = died + 8000;
    submit_shared_world(&mut session, due, 1, 4);
    session.dispatch(7, due, Input::Timers, publish).unwrap();
    assert!(matches!(
        session.actor(2).unwrap().life,
        Life::Selecting { died_at_ms } if died_at_ms == died
    ));
    session
        .dispatch(
            7,
            due,
            Input::SourceSpawn {
                source_id: 2,
                actor_id: 2,
            },
            publish,
        )
        .unwrap();
    let Life::Spawning { spawn, .. } = session.actor(2).unwrap().life else {
        panic!("human member did not select a respawn");
    };
    let mut next = world(due + 1, 1).actors[&2].clone();
    next.position = spawn.position;
    session
        .dispatch(
            7,
            due + 1,
            Input::SourceWorld(Arc::new(SourceWorld {
                epoch: 7,
                source_id: 2,
                batch_id: 5,
                sampled_at_ms: due + 1,
                actors: BTreeMap::from([(2, next)]),
            })),
            publish,
        )
        .unwrap();
    assert_eq!(session.actor(2).unwrap().life, Life::Alive);
}

#[test]
fn source_batches_keep_actor_freshness_and_ownership_independent() {
    let mut second = member(2);
    second.kind = MemberKind::Human;
    second.sample_source_id = 2;
    let mut session = lethal_session_with_members(10, vec![member(1), second, member(3)]);
    submit_shared_world(&mut session, 0, 1, 1);
    let human = world(300, 1).actors[&2].clone();
    session
        .dispatch(
            7,
            300,
            source_world(2, 2, 300, BTreeMap::from([(2, human.clone())])),
            publish,
        )
        .unwrap();
    let mut outputs = Vec::new();
    session
        .dispatch(7, 300, Input::Tick, |batch| {
            outputs.extend_from_slice(batch);
            Ok(())
        })
        .unwrap();
    assert!(outputs.iter().any(|event| {
        matches!(event, Output::Bot(bot) if bot.actor_id == 3 && bot.status == "world-unavailable")
    }));
    assert!(
        session
            .dispatch(7, 300, Input::World(world(300, 1)), publish)
            .is_err()
    );
    assert!(
        session
            .dispatch(
                7,
                301,
                source_world(2, 3, 301, BTreeMap::from([(3, human)])),
                publish
            )
            .is_err()
    );

    let mut bot = world(300, 1).actors[&2].clone();
    bot.entity = 103;
    bot.position = [1., 20., 1.];
    session
        .dispatch(
            7,
            300,
            source_world(1, 2, 300, BTreeMap::from([(3, bot.clone())])),
            publish,
        )
        .unwrap();
    let motion = |sampled_at_ms, position| MotionAttempt {
        actor_id: 3,
        entity: 103,
        sampled_at_ms,
        position,
        heading: 180.,
        attempted_forward: true,
    };
    session
        .dispatch(
            7,
            300,
            Input::SourceMotionAttempt {
                source_id: 1,
                attempt: motion(300, bot.position),
            },
            publish,
        )
        .unwrap();
    let mut changed = bot;
    changed.position = [1.2, 20., 1.];
    let batch = source_world(1, 3, 301, BTreeMap::from([(3, changed.clone())]));
    assert!(
        session
            .dispatch(7, 301, batch.clone(), |_| Err(io::Error::other(
                "sink full"
            )))
            .is_err()
    );
    assert!(
        session
            .dispatch(
                7,
                301,
                Input::SourceMotionAttempt {
                    source_id: 1,
                    attempt: motion(301, changed.position)
                },
                publish
            )
            .is_err()
    );
    session.dispatch(7, 301, batch, publish).unwrap();
    session
        .dispatch(
            7,
            301,
            Input::SourceMotionAttempt {
                source_id: 1,
                attempt: motion(301, changed.position),
            },
            publish,
        )
        .unwrap();
    assert!(
        session
            .dispatch(7, 301, source_world(1, 2, 301, BTreeMap::new()), publish)
            .is_err()
    );
}

#[test]
fn shared_sources_bind_actions_motion_and_spawn_to_their_owner() {
    let mut second = member(2);
    second.kind = MemberKind::Human;
    second.sample_source_id = 2;
    let mut session = lethal_session_with_members(10, vec![member(1), second, member(3)]);
    submit_shared_world(&mut session, 0, 1, 1);
    let action = Command::Press {
        intent: Intent::Attack(Direction::Up),
        defence_down: false,
        dash_run: false,
        dash_forward: false,
    };
    for input in [
        Input::Action {
            actor_id: 1,
            command: action,
        },
        Input::SourceAction {
            source_id: 2,
            actor_id: 1,
            command: action,
        },
        Input::SourceAction {
            source_id: 1,
            actor_id: 3,
            command: action,
        },
        Input::SourceAction {
            source_id: 1,
            actor_id: 1,
            command: Command::Cancel,
        },
        Input::RequestHumanSpawn { actor_id: 2 },
        Input::SourceSpawn {
            source_id: 1,
            actor_id: 2,
        },
    ] {
        assert!(session.dispatch(7, 0, input, publish).is_err());
    }
    assert_eq!(session.actor(1).unwrap().action.phase, Phase::Idle);
    assert_eq!(session.actor(2).unwrap().action.phase, Phase::Idle);
    session
        .dispatch(
            7,
            0,
            Input::SourceAction {
                source_id: 2,
                actor_id: 2,
                command: action,
            },
            publish,
        )
        .unwrap();

    let bot = world(0, 1).actors[&2].clone();
    let attempt = MotionAttempt {
        actor_id: 3,
        entity: 103,
        sampled_at_ms: 0,
        position: [1., 20., 1.],
        heading: bot.heading,
        attempted_forward: true,
    };
    assert!(
        session
            .dispatch(7, 0, Input::MotionAttempt(attempt), publish)
            .is_err()
    );
    assert!(
        session
            .dispatch(
                7,
                0,
                Input::SourceMotionAttempt {
                    source_id: 2,
                    attempt
                },
                publish
            )
            .is_err()
    );
    session
        .dispatch(
            7,
            0,
            Input::SourceMotionAttempt {
                source_id: 1,
                attempt,
            },
            publish,
        )
        .unwrap();
}

#[test]
fn shared_contact_batches_validate_attacker_owner_and_keep_source_cursors() {
    let mut second = member(2);
    second.kind = MemberKind::Human;
    second.sample_source_id = 2;
    let mut session = lethal_session_with_members(10, vec![member(1), second]);
    for (source_id, actor_id) in [(1, 1), (2, 2)] {
        let pose = world(0, 1).actors[&actor_id].clone();
        session
            .dispatch(
                7,
                0,
                source_world(source_id, 1, 0, BTreeMap::from([(actor_id, pose)])),
                publish,
            )
            .unwrap();
    }
    let batch = |batch_id, through_at_ms, events| ContactBatch {
        epoch: 7,
        batch_id,
        through_at_ms,
        dropped: 0,
        events,
    };
    let wrong_owner = Input::SourceContacts {
        source_id: 2,
        batch: batch(1, 1., vec![contact(1, 0, 1.)]),
    };
    assert!(session.dispatch(7, 1, wrong_owner, publish).is_err());
    assert!(
        session
            .dispatch(7, 1, Input::Contacts(batch(1, 1., vec![])), publish)
            .is_err()
    );
    session
        .dispatch(
            7,
            1,
            Input::SourceContacts {
                source_id: 1,
                batch: batch(1, 1., vec![]),
            },
            publish,
        )
        .unwrap();
    session
        .dispatch(
            7,
            2,
            Input::SourceContacts {
                source_id: 2,
                batch: batch(1, 2., vec![]),
            },
            publish,
        )
        .unwrap();
    // A source retry is idempotent even after another source advances the shared watermark.
    session
        .dispatch(
            7,
            2,
            Input::SourceContacts {
                source_id: 1,
                batch: batch(1, 1., vec![]),
            },
            publish,
        )
        .unwrap();
    assert!(
        session
            .dispatch(
                7,
                2,
                Input::SourceContacts {
                    source_id: 1,
                    batch: batch(2, 1., vec![])
                },
                publish
            )
            .is_err()
    );
    assert!(
        session
            .dispatch(
                7,
                2,
                Input::SourceContacts {
                    source_id: 1,
                    batch: batch(2, 2., vec![contact(1, 0, 1.)])
                },
                publish
            )
            .is_err()
    );
    session
        .dispatch(
            7,
            2,
            Input::SourceContacts {
                source_id: 1,
                batch: batch(2, 2., vec![]),
            },
            publish,
        )
        .unwrap();
}

#[test]
fn single_source_contact_streams_do_not_mix_sourced_and_legacy_batches() {
    let batch = ContactBatch {
        epoch: 7,
        batch_id: 1,
        through_at_ms: 0.,
        dropped: 0,
        events: vec![],
    };
    let mut legacy = session();
    legacy
        .dispatch(7, 0, Input::Contacts(batch.clone()), publish)
        .unwrap();
    assert!(
        legacy
            .dispatch(
                7,
                0,
                Input::SourceContacts {
                    source_id: 1,
                    batch: batch.clone()
                },
                publish
            )
            .is_err()
    );

    let mut sourced = session();
    sourced
        .dispatch(
            7,
            0,
            Input::SourceContacts {
                source_id: 1,
                batch: batch.clone(),
            },
            publish,
        )
        .unwrap();
    assert!(
        sourced
            .dispatch(7, 0, Input::Contacts(batch), publish)
            .is_err()
    );
}

#[test]
fn shared_contacts_wait_for_all_source_watermarks_and_retry_failed_publication() {
    let mut second = member(2);
    second.kind = MemberKind::Human;
    second.sample_source_id = 2;
    let mut session = lethal_session_with_members(10, vec![member(1), second]);
    for (source_id, actor_id) in [(1, 1), (2, 2)] {
        let pose = world(0, 1).actors[&actor_id].clone();
        session
            .dispatch(
                7,
                0,
                source_world(source_id, 1, 0, BTreeMap::from([(actor_id, pose)])),
                publish,
            )
            .unwrap();
    }
    sourced_attack(&mut session, 1, 1);
    let due = session.actor(1).unwrap().action.phase_due_ms.unwrap();
    session.dispatch(7, due, Input::Timers, publish).unwrap();
    let sequence = session.actor(1).unwrap().action.sequence;
    for (source_id, actor_id) in [(1, 1), (2, 2)] {
        let pose = world(due + 10, sequence).actors[&actor_id].clone();
        session
            .dispatch(
                7,
                due + 10,
                source_world(source_id, 2, due + 10, BTreeMap::from([(actor_id, pose)])),
                publish,
            )
            .unwrap();
    }
    let at = due + 250;
    session
        .dispatch(
            7,
            at,
            Input::SourceContacts {
                source_id: 1,
                batch: ContactBatch {
                    epoch: 7,
                    batch_id: 1,
                    through_at_ms: at as f64,
                    dropped: 0,
                    events: vec![contact(sequence, due, at as f64)],
                },
            },
            publish,
        )
        .unwrap();
    assert_eq!(session.actor(2).unwrap().health, 100);
    let empty = Input::SourceContacts {
        source_id: 2,
        batch: ContactBatch {
            epoch: 7,
            batch_id: 1,
            through_at_ms: at as f64,
            dropped: 0,
            events: vec![],
        },
    };
    assert!(
        session
            .dispatch(7, at, empty.clone(), |_| Err(io::Error::other(
                "sink failed"
            )))
            .is_err()
    );
    assert_eq!(session.actor(2).unwrap().health, 100);
    session.dispatch(7, at, empty, publish).unwrap();
    assert_eq!(session.actor(2).unwrap().health, 0);
}

#[test]
fn shared_contacts_settle_in_event_time_not_arrival_order() {
    let mut second = member(2);
    second.kind = MemberKind::Human;
    second.sample_source_id = 2;
    let mut session = lethal_session_with_members(10, vec![member(1), second]);
    for (source_id, actor_id) in [(1, 1), (2, 2)] {
        let pose = world(0, 1).actors[&actor_id].clone();
        session
            .dispatch(
                7,
                0,
                source_world(source_id, 1, 0, BTreeMap::from([(actor_id, pose)])),
                publish,
            )
            .unwrap();
        sourced_attack(&mut session, source_id, actor_id);
    }
    let due = session.actor(1).unwrap().action.phase_due_ms.unwrap();
    session.dispatch(7, due, Input::Timers, publish).unwrap();
    let sequence = session.actor(1).unwrap().action.sequence;
    assert_eq!(sequence, session.actor(2).unwrap().action.sequence);
    for (source_id, actor_id) in [(1, 1), (2, 2)] {
        let mut pose = world(due + 10, sequence).actors[&actor_id].clone();
        pose.sequence = sequence;
        session
            .dispatch(
                7,
                due + 10,
                source_world(source_id, 2, due + 10, BTreeMap::from([(actor_id, pose)])),
                publish,
            )
            .unwrap();
    }
    let at = due + 250;
    let mut second_hit = contact(sequence, due, at as f64);
    second_hit.actor_id = 2;
    second_hit.target_id = 1;
    second_hit.source_entity = 102;
    second_hit.target_entity = 101;
    second_hit.source_position = [1., 20., 0.];
    second_hit.target_position = [0., 20., 0.];
    second_hit.source_heading = 180.;
    second_hit.target_heading = 0.;
    second_hit.normal = [1., 0., 0.];
    second_hit.direction = [-1., 0., 0.];
    let first_hit = contact(sequence, due, (at + 1) as f64);
    for (source_id, event) in [(1, first_hit), (2, second_hit)] {
        session
            .dispatch(
                7,
                at + 1,
                Input::SourceContacts {
                    source_id,
                    batch: ContactBatch {
                        epoch: 7,
                        batch_id: 1,
                        through_at_ms: (at + 1) as f64,
                        dropped: 0,
                        events: vec![event],
                    },
                },
                publish,
            )
            .unwrap();
    }
    assert_eq!(session.actor(1).unwrap().health, 0);
    assert_eq!(session.actor(2).unwrap().health, 100);
}

fn lethal_contact(session: &mut BattleSession) -> u64 {
    lethal_contact_with_mode(session, false)
}

fn lethal_contact_with_mode(session: &mut BattleSession, sourced: bool) -> u64 {
    if sourced {
        submit_shared_world(session, 0, 1, 2);
    } else {
        session
            .dispatch(7, 0, Input::World(world(0, 1)), publish)
            .unwrap();
    }
    if sourced {
        sourced_attack(session, 1, 1);
    } else {
        session
            .dispatch(
                7,
                0,
                Input::Action {
                    actor_id: 1,
                    command: Command::Start {
                        intent: Intent::Attack(Direction::Up),
                        released: true,
                    },
                },
                publish,
            )
            .unwrap();
    }
    let due = session.actor(1).unwrap().action.phase_due_ms.unwrap();
    session.dispatch(7, due, Input::Timers, publish).unwrap();
    let sequence = session.actor(1).unwrap().action.sequence;
    if sourced {
        submit_shared_world(session, due + 10, sequence, 3);
    } else {
        session
            .dispatch(
                7,
                due + 10,
                Input::World(world(due + 10, sequence)),
                publish,
            )
            .unwrap();
    }
    let at = due + 250;
    let batch = ContactBatch {
        epoch: 7,
        batch_id: 1,
        through_at_ms: at as f64,
        dropped: 0,
        events: vec![contact(sequence, due, at as f64)],
    };
    let input = if sourced {
        Input::SourceContacts {
            source_id: 1,
            batch,
        }
    } else {
        Input::Contacts(batch)
    };
    session.dispatch(7, at, input, publish).unwrap();
    if sourced {
        session
            .dispatch(
                7,
                at,
                Input::SourceContacts {
                    source_id: 2,
                    batch: ContactBatch {
                        epoch: 7,
                        batch_id: 1,
                        through_at_ms: at as f64,
                        dropped: 0,
                        events: vec![],
                    },
                },
                publish,
            )
            .unwrap();
    }
    at
}

#[test]
fn lethal_contact_settles_once_at_kill_target() {
    let mut session = lethal_session(1);
    let at = lethal_contact(&mut session);
    let settlement = session.settlement().unwrap();
    assert_eq!(
        settlement.reason,
        t7r_battle_runtime::battle_session::EndReason::KillTarget
    );
    assert_eq!(settlement.ended_at_ms, at);
    assert_eq!(settlement.camp_kills, [1, 0]);
    assert_eq!(
        session.actor(2).unwrap().life,
        Life::Dead {
            died_at_ms: at,
            respawn_at_ms: at + 5000
        }
    );
    let previous = settlement.clone();
    session
        .dispatch(7, at + 5000, Input::Tick, |events| {
            assert!(events.is_empty());
            Ok(())
        })
        .unwrap();
    assert_eq!(session.settlement(), Some(&previous));
}

#[test]
fn bot_respawns_only_after_fresh_native_pose_confirms_selected_spawn() {
    let mut session = lethal_session(10);
    let died = lethal_contact(&mut session);
    assert!(session.settlement().is_none());
    let death_sequence = session.actor(2).unwrap().action.sequence % u16::MAX + 1;
    let due = died + 5000;
    session
        .dispatch(7, due, Input::World(world(due, 1)), publish)
        .unwrap();
    session.dispatch(7, due, Input::Tick, publish).unwrap();
    let Life::Spawning { spawn, .. } = session.actor(2).unwrap().life else {
        panic!("bot did not enter sampled spawn confirmation");
    };
    session
        .dispatch(7, due, Input::World(world(due, 1)), publish)
        .unwrap();
    assert!(matches!(
        session.actor(2).unwrap().life,
        Life::Spawning { .. }
    ));
    let mut next = (*world(due + 1, 1)).clone();
    next.actors.get_mut(&2).unwrap().position = spawn.position;
    session
        .dispatch(7, due + 1, Input::World(Arc::new(next)), publish)
        .unwrap();
    assert_eq!(session.actor(2).unwrap().life, Life::Alive);
    assert_eq!(session.actor(2).unwrap().health, 100);
    assert_eq!(session.actor(2).unwrap().action.sequence, death_sequence);
}

#[test]
fn native_motion_during_death_and_spawn_is_valid_but_does_not_drive_ai() {
    let mut session = lethal_session(10);
    let died = lethal_contact(&mut session);
    for at in [died, died + 5000] {
        session
            .dispatch(7, at, Input::World(world(at, 1)), publish)
            .unwrap();
        session.dispatch(7, at, Input::Timers, publish).unwrap();
        let before = session.actor(2).unwrap();
        assert_ne!(before.life, Life::Alive);
        for attempted_forward in [false, true] {
            let attempt = MotionAttempt {
                actor_id: 2,
                entity: 102,
                sampled_at_ms: at,
                position: [1., 20., 0.],
                heading: 180.,
                attempted_forward,
            };
            session
                .dispatch(
                    7,
                    at,
                    Input::SourceMotionAttempt {
                        source_id: 1,
                        attempt,
                    },
                    publish,
                )
                .unwrap();
            let mut wrong = attempt;
            wrong.entity += 1;
            assert!(
                session
                    .dispatch(
                        7,
                        at,
                        Input::SourceMotionAttempt {
                            source_id: 1,
                            attempt: wrong,
                        },
                        publish
                    )
                    .is_err()
            );
            assert!(
                session
                    .dispatch(
                        7,
                        at,
                        Input::SourceMotionAttempt {
                            source_id: 2,
                            attempt,
                        },
                        publish
                    )
                    .is_err()
            );
            assert_eq!(session.actor(2).unwrap(), before);
        }
    }
    let Life::Spawning { spawn, .. } = session.actor(2).unwrap().life else {
        panic!("bot did not reach spawn confirmation");
    };
    let at = died + 5001;
    let mut fresh = (*world(at, 1)).clone();
    fresh.actors.get_mut(&2).unwrap().position = spawn.position;
    session
        .dispatch(7, at, Input::World(Arc::new(fresh)), publish)
        .unwrap();
    assert_eq!(session.actor(2).unwrap().life, Life::Alive);
    session
        .dispatch(
            7,
            at,
            Input::MotionAttempt(MotionAttempt {
                actor_id: 2,
                entity: 102,
                sampled_at_ms: at,
                position: spawn.position,
                heading: 180.,
                attempted_forward: false,
            }),
            publish,
        )
        .unwrap();
}

#[test]
fn sampled_contact_allows_bot_decision_to_start_authoritative_action() {
    let mut session = session();
    let mut initial = (*world(0, 1)).clone();
    initial.actors.get_mut(&2).unwrap().contact = ContactObservation {
        target_id: Some(1),
        state: ContactState::Clear,
    };
    session
        .dispatch(7, 0, Input::World(Arc::new(initial.clone())), publish)
        .unwrap();
    session.dispatch(7, 0, Input::Tick, publish).unwrap();
    initial.sampled_at_ms = 500;
    session
        .dispatch(7, 500, Input::World(Arc::new(initial)), publish)
        .unwrap();
    let mut output = Vec::new();
    session
        .dispatch(7, 500, Input::Tick, |events| {
            output.extend_from_slice(events);
            Ok(())
        })
        .unwrap();
    assert!(output.iter().any(|event| matches!(
        event,
        Output::Bot(bot) if bot.actor_id == 2 && bot.action.is_some()
    )));
    assert_ne!(session.actor(2).unwrap().action.phase, Phase::Idle);
}

#[test]
fn battle_session_accepts_only_motion_attempts_matching_the_fresh_world() {
    let mut session = session();
    session
        .dispatch(7, 0, Input::World(world(0, 1)), publish)
        .unwrap();
    session.dispatch(7, 0, Input::Tick, publish).unwrap();
    for at in (0..=1250).step_by(250) {
        session
            .dispatch(7, at, Input::World(world(at, 1)), publish)
            .unwrap();
        session
            .dispatch(
                7,
                at,
                Input::MotionAttempt(MotionAttempt {
                    actor_id: 2,
                    entity: 102,
                    sampled_at_ms: at,
                    position: [1., 20., 0.],
                    heading: 180.,
                    attempted_forward: true,
                }),
                publish,
            )
            .unwrap();
    }
    let mut outputs = Vec::new();
    session
        .dispatch(7, 1250, Input::Tick, |events| {
            outputs.extend_from_slice(events);
            Ok(())
        })
        .unwrap();
    assert!(outputs.iter().any(|value| matches!(
        value,
        Output::Bot(bot) if bot.actor_id == 2 && bot.status == "unstuck"
    )));
    let before = session.actor(2).unwrap();
    assert!(
        session
            .dispatch(
                7,
                1250,
                Input::MotionAttempt(MotionAttempt {
                    actor_id: 2,
                    entity: 102,
                    sampled_at_ms: 1250,
                    position: [2., 20., 0.],
                    heading: 180.,
                    attempted_forward: true,
                }),
                publish,
            )
            .is_err()
    );
    assert!(
        session
            .dispatch(
                7,
                1250,
                Input::MotionAttempt(MotionAttempt {
                    actor_id: 2,
                    entity: 999,
                    sampled_at_ms: 1250,
                    position: [1., 20., 0.],
                    heading: 180.,
                    attempted_forward: true,
                }),
                publish,
            )
            .is_err()
    );
    assert_eq!(session.actor(2).unwrap(), before);
}

#[test]
fn guard_break_keeps_its_distinct_original_hurt_animation() {
    let mut raw: serde_json::Value = serde_json::from_slice(include_bytes!(
        "../../../../tests/fixtures/resources/bundle-v1.json"
    ))
    .unwrap();
    raw["damageProfiles"]["1101"]["weapons"]["701"]["attackGrade"] = serde_json::json!("SS");
    let bundle = ResourceBundle::from_slice(&serde_json::to_vec(&raw).unwrap()).unwrap();
    let prepared = Arc::new(BattleResources::prepare(&bundle).unwrap());
    let mut session = BattleSession::new(
        &bundle,
        prepared,
        Config {
            epoch: 7,
            started_at_ms: 0,
            duration_ms: 1200000,
            kill_target: 10,
            seed: 42,
            members: vec![member(1), member(2)],
        },
    )
    .unwrap();
    session
        .dispatch(7, 0, Input::World(world(0, 1)), publish)
        .unwrap();
    session
        .dispatch(
            7,
            0,
            Input::Action {
                actor_id: 1,
                command: Command::Start {
                    intent: Intent::Attack(Direction::Up),
                    released: true,
                },
            },
            publish,
        )
        .unwrap();
    let due = session.actor(1).unwrap().action.phase_due_ms.unwrap();
    session
        .dispatch(
            7,
            due,
            Input::Action {
                actor_id: 1,
                command: Command::Timer {
                    kind: TimerKind::Phase,
                    deadline_ms: due,
                },
            },
            publish,
        )
        .unwrap();
    let sequence = session.actor(1).unwrap().action.sequence;
    session
        .dispatch(
            7,
            due + 10,
            Input::World(world(due + 10, sequence)),
            publish,
        )
        .unwrap();
    let mut event = contact(sequence, due, (due + 250) as f64);
    event.defence_weapon_id = 701;
    event.target_intent = Some(Intent::Guard(Direction::Up));
    event.target_phase = Phase::Hold;
    let mut output = Vec::new();
    session
        .dispatch(
            7,
            due + 250,
            Input::Contacts(ContactBatch {
                epoch: 7,
                batch_id: 1,
                through_at_ms: (due + 250) as f64,
                dropped: 0,
                events: vec![event],
            }),
            |events| {
                output.extend_from_slice(events);
                Ok(())
            },
        )
        .unwrap();
    assert!(output.iter().any(|event| matches!(
        event,
        Output::Action(ActionEvent::Animation(animation))
            if animation.actor_id == 2
                && animation.animation == Animation::Hurt(HurtKind::GuardBreak)
    )));
}

#[test]
fn observed_contact_keeps_collision_time_without_granting_future_defence() {
    let mut body_health = None;
    for (intent, phase, weapon) in [
        (None, Phase::Idle, 0),
        (Some(Intent::Attack(Direction::Up)), Phase::Strike, 0),
        (Some(Intent::Guard(Direction::Up)), Phase::Hold, 701),
        (Some(Intent::Guard(Direction::Up)), Phase::ParrySuccess, 701),
        (Some(Intent::Attack(Direction::Up)), Phase::Prepare, 701),
    ] {
        let mut battle = session();
        battle
            .dispatch(7, 0, Input::World(world(0, 1)), publish)
            .unwrap();
        battle
            .dispatch(
                7,
                0,
                Input::Action {
                    actor_id: 1,
                    command: Command::Start {
                        intent: Intent::Attack(Direction::Up),
                        released: true,
                    },
                },
                publish,
            )
            .unwrap();
        let due = battle.actor(1).unwrap().action.phase_due_ms.unwrap();
        battle.dispatch(7, due, Input::Timers, publish).unwrap();
        let sequence = battle.actor(1).unwrap().action.sequence;
        let at = due + 250;
        battle
            .dispatch(7, at, Input::World(world(at, sequence)), publish)
            .unwrap();
        let mut event = contact(sequence, due, at as f64);
        event.target_intent = intent;
        event.target_phase = phase;
        event.target_phase_at_ms = (at + 1) as f64;
        event.target_observed_at_ms = Some(at + 16);
        event.defence_weapon_id = weapon;
        let batch = |e: ContactEvent| ContactBatch {
            epoch: 7,
            batch_id: 1,
            through_at_ms: at as f64,
            dropped: 0,
            events: vec![e],
        };
        let before = battle.actor(2).unwrap();
        // Future observations and inconsistent observation bounds roll back without consuming the batch.
        for observed in [None, Some(at - 1), Some(at), Some(at + 17)] {
            let mut bad = event.clone();
            bad.target_observed_at_ms = observed;
            assert!(
                battle
                    .dispatch(7, at + 16, Input::Contacts(batch(bad)), publish)
                    .is_err()
            );
            assert_eq!(battle.actor(2).unwrap().health, before.health);
        }
        assert!(
            battle
                .dispatch(7, at + 16, Input::Contacts(batch(event.clone())), |_| {
                    Err(io::Error::other("publication failed"))
                })
                .is_err()
        );
        assert_eq!(battle.actor(2).unwrap().health, before.health);
        let mut outputs = Vec::new();
        battle
            .dispatch(
                7,
                at + 16,
                Input::Contacts(batch(event.clone())),
                |events| {
                    outputs.extend_from_slice(events);
                    Ok(())
                },
            )
            .unwrap();
        assert!(outputs.iter().all(|o| !matches!(o, Output::Parry { .. })));
        assert_ne!(battle.actor(1).unwrap().action.phase, Phase::Parried);
        if weapon == 0 {
            let health = battle.actor(2).unwrap().health;
            assert!(health < before.health);
            if let Some(expected) = body_health {
                assert_eq!(
                    health, expected,
                    "future strike must not change the collision damage"
                );
            }
            body_health = Some(health);
        } else {
            // A weapon overlap is not converted into a fabricated body hit.
            assert_eq!(battle.actor(2).unwrap().guard, before.guard);
            assert_eq!(battle.actor(2).unwrap().health, before.health);
        }
        let after = battle.actor(2).unwrap().health;
        battle
            .dispatch(7, at + 16, Input::Contacts(batch(event)), publish)
            .unwrap();
        assert_eq!(battle.actor(2).unwrap().health, after);
    }
}

#[test]
fn source_observation_retains_early_geometry_without_poisoning_valid_hits() {
    let mut expected_health = None;
    for (include_early, include_valid) in [(true, false), (false, true), (true, true)] {
        let mut battle = session();
        battle
            .dispatch(7, 0, Input::World(world(0, 1)), publish)
            .unwrap();
        battle
            .dispatch(
                7,
                0,
                Input::Action {
                    actor_id: 1,
                    command: Command::Start {
                        intent: Intent::Attack(Direction::Up),
                        released: true,
                    },
                },
                publish,
            )
            .unwrap();
        let due = battle.actor(1).unwrap().action.phase_due_ms.unwrap();
        battle.dispatch(7, due, Input::Timers, publish).unwrap();
        let sequence = battle.actor(1).unwrap().action.sequence;
        let at = due + 250;
        let now = at + 16;
        battle
            .dispatch(7, now, Input::World(world(now, sequence)), publish)
            .unwrap();
        let mut early = contact(sequence, due, (due - 1) as f64);
        early.source_observed_at_ms = Some(now);
        early.target_observed_at_ms = Some(now - 1);
        let mut valid = early.clone();
        valid.occurred_at_ms = at as f64;
        let mut events = Vec::new();
        if include_early {
            events.push(early.clone());
        }
        if include_valid {
            events.push(valid);
        }
        let batch = ContactBatch {
            epoch: 7,
            batch_id: 1,
            through_at_ms: at as f64,
            dropped: 0,
            events,
        };
        let before = battle.actor(2).unwrap().health;
        for bound in [due - 2, due - 1, now + 1] {
            let mut bad = batch.clone();
            let mut e = early.clone();
            e.source_observed_at_ms = Some(bound);
            bad.events = vec![e];
            assert!(
                battle
                    .dispatch(7, now, Input::Contacts(bad), publish)
                    .is_err()
            );
        }
        assert!(
            battle
                .dispatch(7, now, Input::Contacts(batch.clone()), |_| {
                    Err(io::Error::other("publication failed"))
                })
                .is_err()
        );
        assert_eq!(battle.actor(2).unwrap().health, before);
        let mut outputs = Vec::new();
        battle
            .dispatch(7, now, Input::Contacts(batch.clone()), |events| {
                outputs.extend_from_slice(events);
                Ok(())
            })
            .unwrap();
        let hits: Vec<_> = outputs
            .iter()
            .filter_map(|output| match output {
                Output::Damage { evidence, .. } => Some(evidence),
                _ => None,
            })
            .collect();
        assert_eq!(hits.len(), usize::from(include_valid));
        let after = battle.actor(2).unwrap().health;
        if include_valid {
            assert!(after < before);
            assert_eq!(hits[0].occurred_at_ms, at as f64);
            assert_eq!(hits[0].phase_at_ms, due);
            assert_eq!(hits[0].source_observed_at_ms, Some(now));
            if let Some(expected) = expected_health {
                assert_eq!(after, expected);
            }
            expected_health = Some(after);
        } else {
            assert_eq!(after, before);
        }
        battle
            .dispatch(7, now, Input::Contacts(batch), publish)
            .unwrap();
        assert_eq!(battle.actor(2).unwrap().health, after);
    }
}

#[test]
fn native_phase_window_is_not_the_server_animation_dispatch_time() {
    for phase_offset in [-1_i64, 1, 17, 230] {
        let mut battle = session();
        battle
            .dispatch(7, 0, Input::World(world(0, 1)), publish)
            .unwrap();
        battle
            .dispatch(
                7,
                0,
                Input::Action {
                    actor_id: 1,
                    command: Command::Start {
                        intent: Intent::Attack(Direction::Up),
                        released: true,
                    },
                },
                publish,
            )
            .unwrap();
        let due = battle.actor(1).unwrap().action.phase_due_ms.unwrap();
        battle.dispatch(7, due, Input::Timers, publish).unwrap();
        let sequence = battle.actor(1).unwrap().action.sequence;
        let native_phase = due.checked_add_signed(phase_offset).unwrap();
        let at = native_phase + 250;
        let now = at + 16;
        battle
            .dispatch(7, now, Input::World(world(now, sequence)), publish)
            .unwrap();
        let mut hit = contact(sequence, native_phase, at as f64);
        hit.source_observed_at_ms = Some(now);
        hit.target_observed_at_ms = Some(now);
        let mut wrong_sequence = hit.clone();
        wrong_sequence.sequence += 1;
        let mut wrong_entity = hit.clone();
        wrong_entity.source_entity += 100;
        let mut early = hit.clone();
        early.occurred_at_ms = native_phase as f64 - 0.5;
        let mut invalid_batch = ContactBatch {
            epoch: 7,
            batch_id: 1,
            through_at_ms: at as f64,
            dropped: 0,
            events: vec![wrong_sequence, wrong_entity, early],
        };
        let before = battle.actor(2).unwrap().health;
        battle
            .dispatch(7, now, Input::Contacts(invalid_batch.clone()), publish)
            .unwrap();
        assert_eq!(battle.actor(2).unwrap().health, before);
        invalid_batch.batch_id = 2;
        invalid_batch.events = vec![hit.clone()];
        assert!(
            battle
                .dispatch(7, now, Input::Contacts(invalid_batch.clone()), |_| {
                    Err(io::Error::other("publication failed"))
                })
                .is_err()
        );
        assert_eq!(battle.actor(2).unwrap().health, before);
        let mut damage = Vec::new();
        battle
            .dispatch(7, now, Input::Contacts(invalid_batch.clone()), |events| {
                damage.extend(events.iter().filter_map(|e| match e {
                    Output::Damage { evidence, .. } => Some(evidence.clone()),
                    _ => None,
                }));
                Ok(())
            })
            .unwrap();
        assert_eq!(damage, vec![Box::new(hit)]);
        let after = battle.actor(2).unwrap().health;
        assert!(after < before);
        battle
            .dispatch(7, now, Input::Contacts(invalid_batch), publish)
            .unwrap();
        assert_eq!(battle.actor(2).unwrap().health, after);
    }
}
