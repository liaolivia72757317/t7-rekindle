use std::collections::BTreeMap;
use t7r_battle_core::{
    ai::{
        ActionObservation, ActorSnapshot, AttackTimings, BotConfig, ContactObservation,
        ContactState, Controller, MovementIntent, Profile, WorldObservation,
    },
    navigation::NavMesh,
};

fn fixture(
    profile: Profile,
    distance: f64,
    preferred: f64,
) -> (Controller, WorldObservation, NavMesh) {
    let mut config =
        BotConfig::from_spawns(2, 0, 1, 7, &[[10., 0., 0.]], &[[-10., 0., 0.]]).unwrap();
    config.profile = profile;
    let bot = ActorSnapshot {
        id: 2,
        camp: 1,
        alive: true,
        blocks_movement: true,
        position: [distance, 0., 0.],
        heading: 180.,
        health: 100,
        preferred_distance: preferred,
        shield_equipped: false,
        action: ActionObservation::idle(),
        hurt: false,
        contact: ContactObservation::unavailable(),
    };
    let enemy = ActorSnapshot {
        id: 1,
        camp: 2,
        position: [0.; 3],
        heading: 0.,
        ..bot.clone()
    };
    let world = WorldObservation {
        sampled_at_ms: 0,
        actors: vec![bot, enemy],
    };
    let nav = NavMesh::from_polygons(vec![vec![
        [-20., -20., 0.],
        [20., -20., 0.],
        [20., 20., 0.],
        [-20., 20., 0.],
    ]])
    .unwrap();
    (Controller::new(0, vec![config]).unwrap(), world, nav)
}

fn timings() -> BTreeMap<u32, AttackTimings> {
    BTreeMap::from([(
        2,
        AttackTimings {
            allowed: [true; 4],
            windup_ms: [180; 4],
        },
    )])
}

fn goal_distance(movement: &MovementIntent) -> f64 {
    let MovementIntent::Follow { points, .. } = movement else {
        panic!("expected a spacing route, got {movement:?}");
    };
    let goal = points.last().unwrap();
    goal[0].hypot(goal[1])
}

#[test]
fn approach_uses_weapon_spacing_instead_of_one_shared_gap() {
    for preferred in [0.9, 1.1, 1.4, 1.7] {
        let (mut ai, world, nav) = fixture(Profile::Balanced, 4., preferred);
        let result = ai.tick(0, &world, &nav, &timings()).unwrap();
        assert!((goal_distance(&result[0].movement) - preferred).abs() < 1e-9);
        assert!(result[0].action.is_none());
        assert_eq!(world.actors[0].position, [4., 0., 0.]);
    }
}

#[test]
fn all_profiles_can_make_room_for_a_long_weapon_without_defensive_retreat_rules() {
    for (profile, gap) in [
        (Profile::Aggressive, 1.6),
        (Profile::Balanced, 1.7),
        (Profile::Defensive, 1.8),
        (Profile::Berserker, 1.6),
    ] {
        let (mut ai, world, nav) = fixture(profile, 0.9, 1.7);
        let result = ai.tick(0, &world, &nav, &timings()).unwrap();
        assert!((goal_distance(&result[0].movement) - gap).abs() < 1e-9);
    }
}

#[test]
fn reaching_the_spacing_band_stops_translation_and_keeps_facing_the_enemy() {
    for preferred in [0.9, 1.1, 1.4, 1.7] {
        let (mut ai, world, nav) = fixture(Profile::Balanced, preferred + 0.1, preferred);
        let result = ai.tick(0, &world, &nav, &timings()).unwrap();
        assert_eq!(result[0].movement, MovementIntent::Turn { target: [0.; 3] });
    }
}

#[test]
fn changing_weapon_discards_the_previous_spacing_goal() {
    let (mut ai, mut world, nav) = fixture(Profile::Balanced, 4., 1.7);
    let first = ai.tick(0, &world, &nav, &timings()).unwrap();
    world.sampled_at_ms = 500;
    world.actors[0].preferred_distance = 1.1;
    let second = ai.tick(500, &world, &nav, &timings()).unwrap();
    assert!((goal_distance(&first[0].movement) - 1.7).abs() < 1e-9);
    assert!((goal_distance(&second[0].movement) - 1.1).abs() < 1e-9);
}

#[test]
fn flank_positions_use_the_same_weapon_spacing_as_direct_approaches() {
    for preferred in [1.1, 1.7] {
        let (mut ai, mut world, nav) = fixture(Profile::Balanced, 6.5, preferred);
        world.actors.push(ActorSnapshot {
            id: 3,
            position: [-1., 0., 0.],
            heading: 0.,
            hurt: true,
            ..world.actors[0].clone()
        });
        let result = ai.tick(0, &world, &nav, &timings()).unwrap();
        assert!((goal_distance(&result[0].movement) - preferred).abs() < 1e-9);
    }
}

#[test]
fn weapon_spacing_does_not_bypass_contact_or_existing_distance_gates() {
    for (distance, state) in [
        (1.7, ContactState::Blocked),
        (1.7, ContactState::Unavailable),
        (2.3, ContactState::Clear),
    ] {
        let (mut ai, world, nav) = fixture(Profile::Berserker, distance, 1.7);
        let mut world = world;
        world.actors[0].contact = ContactObservation {
            target_id: Some(1),
            state,
        };
        assert!(
            ai.tick(0, &world, &nav, &timings()).unwrap()[0]
                .action
                .is_none()
        );
    }
}

#[test]
fn invalid_spacing_is_rejected_without_changing_the_controller() {
    for preferred in [f64::NAN, f64::INFINITY, 0.0, 2.2] {
        let (mut ai, world, nav) = fixture(Profile::Balanced, 4., preferred);
        let before = ai.clone();
        assert!(ai.tick(0, &world, &nav, &timings()).is_err());
        assert_eq!(ai, before);
    }
}
