use std::collections::BTreeMap;
use t7r_battle_core::{
    action::{Command, Direction, Intent},
    battle_protocol::BattleFrame,
};
use t7r_battle_runtime::{
    battle_session::{EndReason, Input, RankedScore, Score, Settlement},
    semantic_adapter,
};

fn vector(name: &str) -> Vec<u8> {
    let line = include_str!("../../../../tests/fixtures/protocol/battle-semantic-v1.txt")
        .lines()
        .find(|line| line.starts_with(name))
        .unwrap();
    let (_, hex) = line.split_once(' ').unwrap();
    let bytes: Vec<_> = hex
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
        .collect();
    BattleFrame::decode(&bytes[4..]).unwrap().payload
}

#[test]
fn semantic_ingress_maps_to_source_bound_session_inputs() {
    let binding = semantic_adapter::SourceBinding {
        epoch: 7,
        source_id: 9,
        actor_rids: BTreeMap::from([(1, 1)]),
    };
    let world_input = semantic_adapter::world_input(&vector("world"), &binding, 0).unwrap();
    assert_eq!(world_input.stamp.batch_id, 1);
    let Input::SourceWorld(world) = world_input.input else {
        panic!("expected source world")
    };
    assert_eq!(
        (
            world.epoch,
            world.source_id,
            world.batch_id,
            world.sampled_at_ms
        ),
        (7, 9, 1, 1000)
    );
    assert_eq!(world.actors[&1].position, [1.5, -2., 0.]);
    assert_eq!(world.actors[&1].contact.target_id, Some(2));
    let action_input = semantic_adapter::player_input(&vector("intent"), &binding, 0).unwrap();
    let Input::PlayerAction {
        stamp,
        actor_id,
        command,
    } = action_input.input
    else {
        panic!("expected source action")
    };
    assert_eq!((stamp.source_id, actor_id), (9, 1));
    assert_eq!((stamp.batch_id, stamp.at_ms), (1, 1000));
    assert_eq!(
        command,
        Command::Press {
            intent: Intent::Attack(Direction::Up),
            defence_down: true,
            dash_run: false,
            dash_forward: true
        }
    );
    assert!(semantic_adapter::world_input(&vector("world"), &binding, 1).is_err());
    assert!(semantic_adapter::player_input(&vector("intent"), &binding, 1).is_err());
    let wrong_source = semantic_adapter::SourceBinding {
        source_id: 10,
        ..binding
    };
    assert!(semantic_adapter::world_input(&vector("world"), &wrong_source, 0).is_err());
    assert!(semantic_adapter::player_input(&vector("intent"), &wrong_source, 0).is_err());
}

#[test]
fn settlement_output_keeps_canonical_result_vector() {
    let settlement = Settlement {
        epoch: 7,
        started_at_ms: 0,
        ended_at_ms: 1001,
        reason: EndReason::Timeout,
        result: 1,
        camp_kills: [10, 8],
        members: vec![RankedScore {
            actor_id: 1,
            camp: 1,
            rank: 1,
            result: 1,
            score: Score {
                kills: 3,
                deaths: 1,
                points: 5,
                streak: 2,
            },
        }],
    };
    assert_eq!(
        semantic_adapter::ended_payload(&settlement).unwrap(),
        vector("ended")
    );
}

#[test]
fn public_contact_versions_preserve_observation_bounds() {
    for (text, version) in [
        (
            include_str!("../../../../tests/fixtures/protocol/battle-semantic-v1.txt"),
            1,
        ),
        (
            include_str!("../../../../tests/fixtures/protocol/battle-contacts-v2.txt"),
            2,
        ),
        (
            include_str!("../../../../tests/fixtures/protocol/battle-contacts-v3.txt"),
            3,
        ),
        (
            include_str!("../../../../tests/fixtures/protocol/battle-contacts-v4.txt"),
            4,
        ),
        (
            include_str!("../../../../tests/fixtures/protocol/battle-contacts-v5.txt"),
            5,
        ),
    ] {
        let line = text.lines().find(|l| l.starts_with("contacts ")).unwrap();
        let bytes: Vec<_> = line
            .split_whitespace()
            .nth(1)
            .unwrap()
            .as_bytes()
            .chunks_exact(2)
            .map(|p| u8::from_str_radix(std::str::from_utf8(p).unwrap(), 16).unwrap())
            .skip(40)
            .collect();
        let sample = t7r_battle_runtime::semantic_adapter::ContactSample::decode(&bytes).unwrap();
        assert_eq!(sample.events[0].event.guard_volume, version == 5);
        assert_eq!(sample.events[0].event.occurred_at_ms, 999.5);
        assert_eq!(
            sample.events[0].event.source_observed_at_ms,
            (version >= 3).then_some(1003)
        );
        assert_eq!(
            sample.events[0].event.target_observed_at_ms,
            (version >= 2).then_some(1002)
        );
        assert_eq!(
            sample.events[0].event.target_phase_at_ms,
            if version >= 4 {
                -4999.
            } else if version >= 2 {
                1000.5
            } else {
                950.
            }
        );
    }
}

#[test]
fn weapon_contact_without_body_region_remains_weapon_evidence() {
    for (intent, phase, weapon) in [(5, 2, 701_u32), (5, 8, 701), (1, 1, 701), (9, 2, 702)] {
        let mut bytes = vector("contacts");
        bytes[82] = intent;
        bytes[83] = phase;
        bytes[116..120].copy_from_slice(&weapon.to_be_bytes());
        bytes[120] = 0;
        bytes[121] = 255; // No body index for a weapon contact.
        let sample = semantic_adapter::ContactSample::decode(&bytes).unwrap();
        let event = &sample.events[0].event;
        assert_eq!(event.defence_weapon_id, weapon);
        assert_eq!(event.body_index, -1);
        assert_eq!(event.region, t7r_battle_core::damage::Region::None);
        bytes[116..120].fill(0);
        assert!(
            semantic_adapter::ContactSample::decode(&bytes).is_err(),
            "an unknown body region must not become a torso hit"
        );
    }
}
