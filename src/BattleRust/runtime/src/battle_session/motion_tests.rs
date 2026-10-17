use super::*;

fn world_input(sourced: bool, batch_id: u64, at_ms: u64) -> Input {
    let actors = (1..=3)
        .map(|id| {
            (
                id,
                Pose {
                    entity: u64::from(id) + 100,
                    position: [f64::from(id), 20., 0.],
                    heading: 0.,
                    sequence: 1,
                    contact: ai::ContactObservation::unavailable(),
                },
            )
        })
        .collect();
    if sourced {
        Input::SourceWorld(Arc::new(SourceWorld {
            epoch: 7,
            source_id: 1,
            batch_id,
            sampled_at_ms: at_ms,
            actors,
        }))
    } else {
        Input::World(Arc::new(World {
            epoch: 7,
            sampled_at_ms: at_ms,
            actors,
        }))
    }
}

fn fixture(sourced: bool) -> BattleSession {
    let bundle = ResourceBundle::from_slice(include_bytes!(
        "../../../../../tests/fixtures/resources/bundle-v1.json"
    ))
    .unwrap();
    let mut session = BattleSession::new(
        &bundle,
        Arc::new(BattleResources::prepare(&bundle).unwrap()),
        Config {
            epoch: 7,
            started_at_ms: 0,
            duration_ms: 10000,
            kill_target: 10,
            seed: 42,
            members: (1..=3)
                .map(|actor_id| Member {
                    actor_id,
                    kind: if actor_id == 1 {
                        MemberKind::Human
                    } else {
                        MemberKind::Bot
                    },
                    sample_source_id: 1,
                    camp: if actor_id == 1 { 1 } else { 2 },
                    hero_id: 1101,
                    weapon_id: 701,
                    equipment: vec![EquipmentItem {
                        slot: 1,
                        weapon_id: 701,
                    }],
                })
                .collect(),
        },
    )
    .unwrap();
    session
        .dispatch(7, 100, world_input(sourced, 1, 100), |_| Ok(()))
        .unwrap();
    session
}

fn motion(actor_id: u32, at_ms: u64) -> ai::MotionAttempt {
    ai::MotionAttempt {
        actor_id,
        entity: u64::from(actor_id) + 100,
        sampled_at_ms: at_ms,
        position: [f64::from(actor_id), 20., 0.],
        heading: 0.,
        attempted_forward: true,
    }
}

fn motion_input(sourced: bool, attempt: ai::MotionAttempt) -> Input {
    if sourced {
        Input::SourceMotionAttempt {
            source_id: 1,
            attempt,
        }
    } else {
        Input::MotionAttempt(attempt)
    }
}

#[test]
fn motion_freshness_boundary_uses_250ms_and_ignores_251ms() {
    for sourced in [false, true] {
        for delay in [250, 251] {
            let mut session = fixture(sourced);
            let before = session.state.ai.state(2).unwrap().clone();
            session
                .dispatch(
                    7,
                    100 + delay,
                    motion_input(sourced, motion(2, 100)),
                    |outputs| {
                        assert!(outputs.is_empty());
                        Ok(())
                    },
                )
                .unwrap();
            assert_eq!(
                session.state.ai.state(2).unwrap() == &before,
                delay > 250,
                "sourced={sourced}, delay={delay}"
            );
        }
    }
}

#[test]
fn motion_expiring_between_bots_does_not_stop_next_world_or_ai_tick() {
    let mut session = fixture(true);
    let before = session.state.ai.state(3).unwrap().clone();
    for (actor_id, now) in [(2, 350), (3, 351)] {
        session
            .dispatch(
                7,
                now,
                motion_input(true, motion(actor_id, 100)),
                |_| Ok(()),
            )
            .unwrap();
    }
    assert_eq!(session.state.ai.state(3).unwrap(), &before);
    session
        .dispatch(7, 352, world_input(true, 2, 352), |_| Ok(()))
        .unwrap();
    session
        .dispatch(7, 352, motion_input(true, motion(3, 352)), |_| Ok(()))
        .unwrap();
    assert_ne!(session.state.ai.state(3).unwrap(), &before);
    session.dispatch(7, 352, Input::Tick, |_| Ok(())).unwrap();
    assert!(session.settlement().is_none());
}

#[test]
fn expired_motion_does_not_clear_previous_ai_observation() {
    let mut session = fixture(true);
    session
        .dispatch(7, 100, motion_input(true, motion(2, 100)), |_| Ok(()))
        .unwrap();
    let before = session.state.ai.state(2).unwrap().clone();
    let expired = ai::MotionAttempt {
        attempted_forward: false,
        ..motion(2, 100)
    };
    session
        .dispatch(7, 351, motion_input(true, expired), |_| Ok(()))
        .unwrap();
    assert_eq!(session.state.ai.state(2).unwrap(), &before);
}

#[test]
fn expired_motion_still_rejects_inconsistent_or_unbound_evidence() {
    let valid = motion(2, 100);
    for sourced in [false, true] {
        let mut session = fixture(sourced);
        let before = session.state.ai.state(2).unwrap().clone();
        for attempt in [
            ai::MotionAttempt {
                entity: 999,
                ..valid
            },
            ai::MotionAttempt {
                position: [9., 20., 0.],
                ..valid
            },
            ai::MotionAttempt {
                heading: 90.,
                ..valid
            },
            ai::MotionAttempt {
                sampled_at_ms: 99,
                ..valid
            },
            ai::MotionAttempt {
                sampled_at_ms: 352,
                ..valid
            },
            motion(1, 100),
            motion(4, 100),
        ] {
            let error = session
                .dispatch(7, 351, motion_input(sourced, attempt), |_| Ok(()))
                .unwrap_err();
            assert_eq!(error.kind(), io::ErrorKind::InvalidData);
            assert_eq!(session.state.ai.state(2).unwrap(), &before);
            assert_eq!(session.state.now_ms, 100);
        }
        assert!(
            session
                .dispatch(6, 351, motion_input(sourced, valid), |_| Ok(()))
                .is_err()
        );
        assert!(
            session
                .dispatch(
                    7,
                    351,
                    Input::SourceMotionAttempt {
                        source_id: 2,
                        attempt: valid
                    },
                    |_| Ok(())
                )
                .is_err()
        );
    }
}
