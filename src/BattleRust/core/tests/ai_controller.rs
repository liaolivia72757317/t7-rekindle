use std::collections::BTreeMap;
use t7r_battle_core::{
    action::{Direction, Intent, Phase},
    ai::{
        ActionObservation, ActorSnapshot, AttackTimings, BotConfig, ContactObservation,
        ContactState, Controller, DecisionContext, DecisionKind, MotionAttempt, MovementIntent,
        Profile, WorldObservation, choose_decision,
    },
    navigation::NavMesh,
};

fn actor(id: u32, camp: u8, position: [f64; 3], heading: f64, phase: Phase) -> ActorSnapshot {
    ActorSnapshot {
        id,
        camp,
        alive: true,
        blocks_movement: true,
        position,
        heading,
        health: 100,
        preferred_distance: 1.2,
        shield_equipped: false,
        hurt: false,
        action: ActionObservation {
            intent: (phase != Phase::Idle).then_some(Intent::Attack(Direction::Up)),
            phase,
            sequence: 1,
            released: false,
            phase_due_ms: None,
        },
        contact: ContactObservation::unavailable(),
    }
}
fn mesh() -> NavMesh {
    NavMesh::from_polygons(vec![vec![
        [-20., -20., 0.],
        [20., -20., 0.],
        [20., 20., 0.],
        [-20., 20., 0.],
    ]])
    .unwrap()
}

#[test]
fn shield_replaces_directional_guard_without_changing_defence_eligibility() {
    let mut source = actor(2, 1, [0.; 3], 0., Phase::Idle);
    source.contact = ContactObservation {
        target_id: Some(1),
        state: ContactState::Clear,
    };
    let mut target = actor(1, 2, [1.5, 0., 0.], 180., Phase::Prepare);
    target.contact = ContactObservation {
        target_id: Some(2),
        state: ContactState::Clear,
    };
    let timings = AttackTimings {
        allowed: [true; 4],
        windup_ms: [300; 4],
    };
    for profile in [
        Profile::Aggressive,
        Profile::Balanced,
        Profile::Defensive,
        Profile::Berserker,
    ] {
        let context = DecisionContext {
            now_ms: 1000,
            profile,
            tempo: 100,
            seed: 7,
            last_attack: None,
            react_at_ms: Some(0),
            observed_since_ms: 0,
            phase_observed_since_ms: 0,
        };
        for (incoming, guard) in [
            (Intent::Attack(Direction::Up), Direction::Up),
            (Intent::Attack(Direction::Right), Direction::Left),
            (Intent::Attack(Direction::Down), Direction::Down),
            (Intent::Attack(Direction::Left), Direction::Right),
            (Intent::Dash, Direction::Down),
        ] {
            target.action.intent = Some(incoming);
            target.action.phase = if incoming == Intent::Dash {
                Phase::Strike
            } else {
                Phase::Prepare
            };
            target.action.phase_due_ms = Some(2000);
            for shield in [false, true] {
                source.shield_equipped = shield;
                let result = choose_decision(&context, &source, &target, &timings).unwrap();
                if profile == Profile::Berserker {
                    assert_eq!(result.kind, DecisionKind::Attack);
                    continue;
                }
                assert_eq!(result.kind, DecisionKind::Parry);
                assert_eq!(
                    result.intent,
                    Some(if shield {
                        Intent::Shield
                    } else {
                        Intent::Guard(guard)
                    })
                );
                let early = DecisionContext {
                    now_ms: 0,
                    ..context
                };
                assert_eq!(
                    choose_decision(&early, &source, &target, &timings)
                        .unwrap()
                        .reason,
                    "observe-threat"
                );
                target.contact.state = ContactState::Blocked;
                assert_ne!(
                    choose_decision(&context, &source, &target, &timings)
                        .unwrap()
                        .kind,
                    DecisionKind::Parry
                );
                target.contact.state = ContactState::Clear;
            }
        }
    }
}

#[test]
fn crowded_team_uses_sampled_flank_route_without_moving_actor_positions() {
    let bot = BotConfig::from_spawns(2, 0, 1, 7, &[[-10., 0., 0.]], &[[10., 0., 0.]]).unwrap();
    let mut ai = Controller::new(0, vec![bot]).unwrap();
    let world = WorldObservation {
        sampled_at_ms: 0,
        actors: vec![
            actor(1, 1, [-1., 0., 0.], 0., Phase::Prepare),
            actor(2, 1, [6.5, 0., 0.], 180., Phase::Idle),
            actor(3, 2, [0., 0., 0.], 0., Phase::Idle),
        ],
    };
    let result = ai
        .tick(
            0,
            &world,
            &mesh(),
            &BTreeMap::from([(
                2,
                AttackTimings {
                    allowed: [true; 4],
                    windup_ms: [360, 360, 400, 360],
                },
            )]),
        )
        .unwrap();
    assert_eq!(result.len(), 1);
    assert!(
        matches!(result[0].status, "flank" | "rear" | "spread" | "support"),
        "{}",
        result[0].status
    );
    assert!(matches!(result[0].movement, MovementIntent::Follow { .. }));
    assert_eq!(world.actors[1].position, [6.5, 0., 0.]);
}

#[test]
fn controller_rejects_invalid_tick_without_advancing_targets_or_random_seeds() {
    let bot = BotConfig::from_spawns(2, 0, 1, 7, &[[-10., 0., 0.]], &[[10., 0., 0.]]).unwrap();
    let mut ai = Controller::new(0, vec![bot]).unwrap();
    let before = ai.clone();
    let world = WorldObservation {
        sampled_at_ms: 0,
        actors: vec![actor(2, 1, [0., 0., 0.], 0., Phase::Idle)],
    };
    assert!(ai.tick(0, &world, &mesh(), &BTreeMap::new()).is_err());
    assert_eq!(ai, before);
}

#[test]
fn bot_without_visible_enemy_assists_teammate_using_sampled_target() {
    let own = [[-10., 0., 0.]];
    let other = [[10., 0., 0.]];
    let mut ai = Controller::new(
        0,
        vec![
            BotConfig::from_spawns(2, 0, 1, 7, &own, &other).unwrap(),
            BotConfig::from_spawns(3, 1, 1, 7, &own, &other).unwrap(),
        ],
    )
    .unwrap();
    let mut world = WorldObservation {
        sampled_at_ms: 0,
        actors: vec![
            actor(2, 1, [-12., 0., 0.], 180., Phase::Idle),
            actor(3, 1, [-1., 0., 0.], 0., Phase::Idle),
            actor(4, 2, [0., 0., 0.], 180., Phase::Idle),
        ],
    };
    let timings = BTreeMap::from([
        (
            2,
            AttackTimings {
                allowed: [true; 4],
                windup_ms: [360; 4],
            },
        ),
        (
            3,
            AttackTimings {
                allowed: [true; 4],
                windup_ms: [360; 4],
            },
        ),
    ]);
    ai.tick(0, &world, &mesh(), &timings).unwrap();
    assert_eq!(ai.state(3).unwrap().target_id, Some(4));
    world.sampled_at_ms = 500;
    let result = ai.tick(500, &world, &mesh(), &timings).unwrap();
    assert_eq!(result[0].status, "assist");
    assert_eq!(result[0].target_id, None);
    assert!(matches!(result[0].movement, MovementIntent::Follow { .. }));
    assert_eq!(world.actors[0].position, [-12., 0., 0.]);
}

#[test]
fn escape_requires_explicit_forward_attempts_and_fresh_position_samples() {
    let bot = BotConfig::from_spawns(2, 0, 1, 7, &[[-10., 0., 0.]], &[[10., 0., 0.]]).unwrap();
    let mut ai = Controller::new(0, vec![bot]).unwrap();
    let mut world = WorldObservation {
        sampled_at_ms: 0,
        actors: vec![actor(2, 1, [0., 0., 0.], 0., Phase::Idle)],
    };
    let timings = BTreeMap::from([(
        2,
        AttackTimings {
            allowed: [true; 4],
            windup_ms: [360; 4],
        },
    )]);
    ai.tick(0, &world, &mesh(), &timings).unwrap();
    for at in (0..=1250).step_by(250) {
        ai.observe_motion(
            at,
            MotionAttempt {
                actor_id: 2,
                entity: 102,
                sampled_at_ms: at,
                position: [0., 0., 0.],
                heading: 0.,
                attempted_forward: true,
            },
        )
        .unwrap();
    }
    world.sampled_at_ms = 1250;
    let result = ai.tick(1250, &world, &mesh(), &timings).unwrap();
    assert_eq!(result[0].status, "unstuck");
    let MovementIntent::Follow { points, .. } = &result[0].movement else {
        panic!("expected sampled escape route");
    };
    let goal = *points.last().unwrap();
    assert_eq!(world.actors[0].position, [0., 0., 0.]);
    world.actors[0].position = goal;
    world.sampled_at_ms = 1500;
    assert_ne!(
        ai.tick(1500, &world, &mesh(), &timings).unwrap()[0].status,
        "unstuck"
    );

    let before = ai.clone();
    assert!(
        ai.observe_motion(
            1500,
            MotionAttempt {
                actor_id: 2,
                entity: 102,
                sampled_at_ms: 1501,
                position: goal,
                heading: 0.,
                attempted_forward: true,
            },
        )
        .is_err()
    );
    assert_eq!(ai, before);
    for at in (1500..=2750).step_by(250) {
        ai.observe_motion(
            at,
            MotionAttempt {
                actor_id: 2,
                entity: 102,
                sampled_at_ms: at,
                position: goal,
                heading: 0.,
                attempted_forward: true,
            },
        )
        .unwrap();
    }
    ai.observe_motion(
        2750,
        MotionAttempt {
            actor_id: 2,
            entity: 102,
            sampled_at_ms: 2750,
            position: goal,
            heading: 0.,
            attempted_forward: false,
        },
    )
    .unwrap();
    world.sampled_at_ms = 2750;
    assert_ne!(
        ai.tick(2750, &world, &mesh(), &timings).unwrap()[0].status,
        "unstuck"
    );
}
