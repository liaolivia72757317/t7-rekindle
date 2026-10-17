use t7r_battle_core::{
    battle_protocol::BattleFrame,
    battle_semantics::{
        MatchAborted, MatchEnded, PlayerIntent, SampledActor, SourceStamp, WorldSample,
        evidence::{ContactBatch, MotionAttempt},
        state::MatchState,
    },
};

fn evidence_round_trip(name: &str, payload: &[u8]) -> Result<Vec<u8>, Box<dyn std::error::Error>> {
    Ok(match name {
        "motion_false" | "motion_true" => MotionAttempt::decode(payload)?.encode()?,
        "contacts" | "watermark" => ContactBatch::decode(payload)?.encode()?,
        _ => return Err("unknown evidence vector".into()),
    })
}

fn valid_payload(name: &str, payload: &[u8]) -> bool {
    match name {
        "world" => WorldSample::decode(payload).is_ok(),
        "intent" => PlayerIntent::decode(payload).is_ok(),
        "abort" => MatchAborted::decode(payload).is_ok(),
        "ended" => MatchEnded::decode(payload).is_ok(),
        "motion_false" | "motion_true" => MotionAttempt::decode(payload).is_ok(),
        "contacts" | "watermark" => ContactBatch::decode(payload).is_ok(),
        "state_snapshot" | "state_delta" => MatchState::decode(payload).is_ok(),
        _ => false,
    }
}

fn bytes(hex: &str) -> Vec<u8> {
    hex.as_bytes()
        .chunks_exact(2)
        .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
        .collect()
}

#[test]
fn state_health_is_an_absolute_signed_wire_value_not_a_percentage() {
    for name in ["state_snapshot", "state_delta"] {
        let line = include_str!("../../../../tests/fixtures/protocol/battle-semantic-v1.txt")
            .lines()
            .find(|line| line.starts_with(&format!("{name} ")))
            .unwrap();
        let frame = BattleFrame::decode(&bytes(line.split_once(' ').unwrap().1)[4..]).unwrap();
        let offset = if name == "state_delta" { 64 } else { 60 };
        for health in [
            0_u32,
            99,
            150,
            i32::MAX as u32,
            i32::MAX as u32 + 1,
            u32::MAX,
        ] {
            let mut payload = frame.payload.clone();
            payload[offset..offset + 4].copy_from_slice(&health.to_be_bytes());
            if health > i32::MAX as u32 {
                assert!(MatchState::decode(&payload).is_err());
            } else {
                let decoded = MatchState::decode(&payload).unwrap();
                assert_eq!(decoded.actors[0].health, health);
                assert_eq!(decoded.encode().unwrap(), payload);
            }
        }
    }
}

#[test]
fn semantic_vectors_match_existing_frame_transport() {
    for line in include_str!("../../../../tests/fixtures/protocol/battle-semantic-v1.txt").lines() {
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let (name, hex) = line.split_once(' ').unwrap();
        let wire = bytes(hex);
        let frame = BattleFrame::decode(&wire[4..]).unwrap();
        assert_eq!(frame.encode().unwrap(), wire);
        let payload = match name {
            "world" => {
                assert_eq!(frame.kind, 6);
                let world = WorldSample::decode(&frame.payload).unwrap();
                assert_eq!(
                    world.stamp,
                    SourceStamp {
                        epoch: 7,
                        source_id: 9,
                        batch_id: 1,
                        at_ms: 1000
                    }
                );
                assert_eq!(world.actors[0].client_rid, 1);
                world.encode().unwrap()
            }
            "intent" => {
                assert_eq!(frame.kind, 11);
                let intent = PlayerIntent::decode(&frame.payload).unwrap();
                assert_eq!((intent.actor_id, intent.command, intent.flags), (1, 1, 5));
                intent.encode().unwrap()
            }
            "abort" => {
                assert_eq!(frame.kind, 12);
                let abort = MatchAborted::decode(&frame.payload).unwrap();
                assert_eq!((abort.epoch, abort.at_ms, abort.reason), (7, 1001, 1));
                abort.encode().unwrap()
            }
            "ended" => {
                assert_eq!(frame.kind, 8);
                let ended = MatchEnded::decode(&frame.payload).unwrap();
                assert_eq!((ended.epoch, ended.ended_at_ms, ended.result), (7, 1001, 1));
                assert_eq!((ended.members[0].actor_id, ended.members[0].points), (1, 5));
                ended.encode().unwrap()
            }
            "motion_false" | "motion_true" => {
                assert_eq!(frame.kind, 13);
                let motion = MotionAttempt::decode(&frame.payload).unwrap();
                assert_eq!(motion.actor_id, 1);
                assert_eq!(motion.attempted_forward_at_ms != 0, name == "motion_true");
                evidence_round_trip(name, &frame.payload).unwrap()
            }
            "contacts" | "watermark" => {
                assert_eq!(frame.kind, 14);
                let batch = ContactBatch::decode(&frame.payload).unwrap();
                assert_eq!(batch.events.len(), usize::from(name == "contacts"));
                if name == "contacts" {
                    assert_eq!(
                        (batch.events[0].actor_id, batch.events[0].target_client_rid),
                        (1, 2)
                    );
                    assert_eq!(batch.events[0].occurred_at_ms, 999.5);
                }
                evidence_round_trip(name, &frame.payload).unwrap()
            }
            "state_snapshot" | "state_delta" => {
                assert_eq!(frame.kind, 7);
                let state = MatchState::decode(&frame.payload).unwrap();
                assert_eq!(
                    (state.epoch, state.revision),
                    (7, if name == "state_delta" { 2 } else { 1 })
                );
                assert_eq!(
                    state.actors.len(),
                    if name == "state_delta" { 1 } else { 2 }
                );
                assert_eq!(state.actors[0].shields[0].0, 300);
                assert_eq!(
                    state.actors[0].shields[0].1,
                    if name == "state_delta" { 50 } else { 60 }
                );
                if name == "state_snapshot" {
                    assert_eq!(
                        (state.actors[1].life, state.actors[1].respawn_at_ms),
                        (2, 5950)
                    );
                }
                state.encode().unwrap()
            }
            _ => panic!("unexpected semantic vector"),
        };
        assert_eq!(payload, frame.payload, "{name}");
    }
}

#[test]
fn semantic_payloads_reject_truncation_reserved_and_invalid_identity() {
    let lines: Vec<_> = include_str!("../../../../tests/fixtures/protocol/battle-semantic-v1.txt")
        .lines()
        .filter(|line| !line.starts_with('#'))
        .collect();
    for line in lines.iter() {
        let (name, hex) = line.split_once(' ').unwrap();
        let payload = BattleFrame::decode(&bytes(hex)[4..]).unwrap().payload;
        for length in 0..payload.len() {
            let truncated = &payload[..length];
            assert!(!valid_payload(name, truncated));
        }
        let mut trailing = payload.clone();
        trailing.push(0);
        assert!(!valid_payload(name, &trailing));
        let mut future = payload.clone();
        future[1] = 5;
        assert!(!valid_payload(name, &future));
    }
    let stamp = SourceStamp {
        epoch: 7,
        source_id: 9,
        batch_id: 1,
        at_ms: 1000,
    };
    let actor = SampledActor {
        actor_id: 1,
        client_rid: 1,
        sequence: 2,
        entity: 3,
        position: [1.5, -2., 0.],
        heading: 90.,
        contact_target_id: 2,
        contact_state: 2,
    };
    assert!(
        WorldSample {
            stamp,
            actors: vec![actor, actor]
        }
        .encode()
        .is_err()
    );
    assert!(
        WorldSample {
            stamp,
            actors: vec![SampledActor {
                position: [f64::NAN, 0., 0.],
                ..actor
            }]
        }
        .encode()
        .is_err()
    );
    assert!(
        PlayerIntent {
            stamp,
            actor_id: 1,
            client_rid: 1,
            command: 11,
            flags: 1
        }
        .encode()
        .is_err()
    );
    assert!(
        MatchAborted {
            epoch: 0,
            at_ms: 0,
            reason: 1
        }
        .encode()
        .is_err()
    );
}

#[test]
fn evidence_rejects_lost_contact_and_stale_motion() {
    let vectors: std::collections::BTreeMap<_, _> =
        include_str!("../../../../tests/fixtures/protocol/battle-semantic-v1.txt")
            .lines()
            .filter(|line| !line.starts_with('#'))
            .map(|line| line.split_once(' ').unwrap())
            .collect();
    let mut contacts = BattleFrame::decode(&bytes(vectors["contacts"])[4..])
        .unwrap()
        .payload;
    contacts[47] = 1;
    assert!(
        ContactBatch::decode(&contacts).is_err(),
        "dropped evidence must fail"
    );
    contacts[47] = 0;
    contacts[51] = 1;
    assert!(
        ContactBatch::decode(&contacts).is_err(),
        "reserved byte must fail"
    );

    let mut motion = BattleFrame::decode(&bytes(vectors["motion_true"])[4..])
        .unwrap()
        .payload;
    motion[92..100].copy_from_slice(&1001_u64.to_be_bytes());
    assert!(
        MotionAttempt::decode(&motion).is_err(),
        "future attempt must fail"
    );
    motion[92..100].copy_from_slice(&749_u64.to_be_bytes());
    assert!(
        MotionAttempt::decode(&motion).is_err(),
        "stale attempt must fail"
    );
    motion[92..100].copy_from_slice(&0_u64.to_be_bytes());
    assert!(
        MotionAttempt::decode(&motion).is_ok(),
        "false attempt is complete evidence"
    );
}

#[test]
fn match_state_requires_contiguous_delta_and_valid_event_type() {
    let vectors: std::collections::BTreeMap<_, _> =
        include_str!("../../../../tests/fixtures/protocol/battle-semantic-v1.txt")
            .lines()
            .filter(|line| !line.starts_with('#'))
            .map(|line| line.split_once(' ').unwrap())
            .collect();
    let mut delta = BattleFrame::decode(&bytes(vectors["state_delta"])[4..])
        .unwrap()
        .payload;
    delta[27] = 0;
    assert!(
        MatchState::decode(&delta).is_err(),
        "base revision is mandatory"
    );
    delta[27] = 1;
    delta[40] = 2;
    assert!(
        MatchState::decode(&delta).is_err(),
        "unknown event is not skipped"
    );
}
