use t7r_battle_core::{
    action::{Direction, Intent, Phase},
    ai::{
        ActionObservation, ActorSnapshot, AttackTimings, ContactObservation, ContactState,
        DecisionContext, DecisionKind, Profile, choose_decision,
    },
};

const DIRECTIONS: [Direction; 4] = [
    Direction::Up,
    Direction::Right,
    Direction::Down,
    Direction::Left,
];

fn actors() -> (ActorSnapshot, ActorSnapshot) {
    let source = ActorSnapshot {
        id: 2,
        camp: 1,
        alive: true,
        blocks_movement: true,
        position: [0.; 3],
        heading: 0.,
        health: 100,
        preferred_distance: 1.7,
        shield_equipped: false,
        action: ActionObservation::idle(),
        hurt: false,
        contact: ContactObservation {
            target_id: Some(1),
            state: ContactState::Clear,
        },
    };
    let target = ActorSnapshot {
        id: 1,
        camp: 2,
        position: [1.9, 0., 0.],
        heading: 180.,
        contact: ContactObservation::unavailable(),
        ..source.clone()
    };
    (source, target)
}

fn context(profile: Profile, seed: u32) -> DecisionContext {
    DecisionContext {
        now_ms: 1000,
        profile,
        tempo: 100,
        seed,
        last_attack: Some(Direction::Down),
        react_at_ms: Some(0),
        observed_since_ms: 0,
        phase_observed_since_ms: 0,
    }
}

#[test]
fn restricted_directions_apply_to_attack_pressure_and_counterattack() {
    let (source, mut target) = actors();
    for profile in [
        Profile::Aggressive,
        Profile::Balanced,
        Profile::Defensive,
        Profile::Berserker,
    ] {
        for (phase, intent, expected) in [
            (Phase::Hold, Intent::Guard(Direction::Down), "attack"),
            (
                Phase::Hold,
                Intent::Attack(Direction::Up),
                if profile == Profile::Aggressive {
                    "pressure"
                } else {
                    "attack"
                },
            ),
            (
                Phase::Recovery,
                Intent::Attack(Direction::Up),
                "counterattack",
            ),
        ] {
            target.action.intent = Some(intent);
            target.action.phase = phase;
            target.action.phase_due_ms = Some(2000);
            for allowed in [[false, false, true, false], [true, true, false, true]] {
                for seed in 0..128 {
                    let timings = AttackTimings {
                        windup_ms: [1, 1, 600, 1],
                        allowed,
                    };
                    let decision =
                        choose_decision(&context(profile, seed), &source, &target, &timings)
                            .unwrap();
                    assert_eq!(decision.reason, expected);
                    let Some(Intent::Attack(direction)) = decision.intent else {
                        panic!("expected attack")
                    };
                    assert!(allowed[DIRECTIONS.iter().position(|&d| d == direction).unwrap()]);
                }
            }
        }
    }
}

#[test]
fn all_direction_weapons_still_use_all_four_attacks() {
    let (source, target) = actors();
    for (index, direction) in DIRECTIONS.into_iter().enumerate() {
        let mut timings = AttackTimings {
            windup_ms: [900; 4],
            allowed: [true; 4],
        };
        timings.windup_ms[index] = 1;
        let mut ctx = context(Profile::Berserker, 7);
        ctx.last_attack = None;
        assert_eq!(
            choose_decision(&ctx, &source, &target, &timings)
                .unwrap()
                .intent,
            Some(Intent::Attack(direction))
        );
    }
}

#[test]
fn no_attack_directions_means_wait_not_an_invented_attack() {
    let (source, target) = actors();
    let decision = choose_decision(
        &context(Profile::Berserker, 7),
        &source,
        &target,
        &AttackTimings {
            windup_ms: [100; 4],
            allowed: [false; 4],
        },
    )
    .unwrap();
    assert_eq!(decision.kind, DecisionKind::Wait);
    assert_eq!(decision.reason, "attack-unavailable");
    assert_eq!(decision.intent, None);
}
