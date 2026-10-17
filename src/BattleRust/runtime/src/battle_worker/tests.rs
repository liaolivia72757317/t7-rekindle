use super::*;
use crate::{
    battle_resources::BattleResources,
    battle_session::{Config, Member, MemberKind, Pose, World},
};
use std::collections::BTreeMap;
use t7r_battle_core::{action::EquipmentItem, ai::ContactObservation};
use t7r_battle_resources::ResourceBundle;

fn session(duration_ms: u64) -> BattleSession {
    let bundle = ResourceBundle::from_slice(include_bytes!(
        "../../../../../tests/fixtures/resources/bundle-v1.json"
    ))
    .unwrap();
    let resources = Arc::new(BattleResources::prepare(&bundle).unwrap());
    let members = (1..=2)
        .map(|actor_id| Member {
            actor_id,
            kind: if actor_id == 1 {
                MemberKind::Human
            } else {
                MemberKind::Bot
            },
            sample_source_id: 1,
            camp: actor_id as u8,
            hero_id: 1101,
            weapon_id: 701,
            equipment: vec![EquipmentItem {
                slot: 1,
                weapon_id: 701,
            }],
        })
        .collect();
    BattleSession::new(
        &bundle,
        resources,
        Config {
            epoch: 7,
            started_at_ms: 0,
            duration_ms,
            kill_target: 10,
            seed: 42,
            members,
        },
    )
    .unwrap()
}

#[test]
fn owner_keeps_input_uncommitted_when_output_batch_is_full() {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap();
    runtime.block_on(async {
        let mut worker =
            BattleWorker::start_with_limits(session(10000), 1, 1024 * 1024, 10000, false).unwrap();
        let sender = worker.sender();
        assert!(sender.clock_ms().unwrap() < 10000);
        assert!(sender.submit(7, Input::Tick).await.is_ok());
        let error = sender.submit(7, Input::Quit).await.unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::WouldBlock);
        assert!(
            worker
                .recv()
                .await
                .unwrap()
                .iter()
                .any(|event| { matches!(event, Output::Bot(_)) })
        );
        sender.submit(7, Input::Quit).await.unwrap();
        let batch = worker.recv().await.unwrap();
        assert!(batch.iter().any(|event| matches!(event, Output::Ended(_))));
        worker.stop().unwrap();
        assert_eq!(
            sender.submit(7, Input::Tick).await.unwrap_err().kind(),
            io::ErrorKind::BrokenPipe
        );
    });
}

#[test]
fn saturated_timer_output_is_retried_and_stop_remains_responsive() {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap();
    runtime.block_on(async {
        for drain in [true, false] {
            let mut worker =
                BattleWorker::start_with_limits(session(100), 1, 1024 * 1024, 10000, false)
                    .unwrap();
            worker.sender().submit(7, Input::Tick).await.unwrap();
            tokio::time::sleep(Duration::from_millis(200)).await;
            assert!(
                worker.is_running(),
                "full output queue stopped the timer owner"
            );
            let error = tokio::time::timeout(
                Duration::from_secs(1),
                worker.sender().submit(7, Input::Quit),
            )
            .await
            .unwrap()
            .unwrap_err();
            assert_eq!(error.kind(), io::ErrorKind::WouldBlock);
            if drain {
                worker.recv().await.unwrap();
                let events = tokio::time::timeout(Duration::from_secs(2), worker.recv())
                    .await
                    .unwrap()
                    .unwrap();
                assert_eq!(
                    events
                        .iter()
                        .filter(|event| matches!(event, Output::Ended(_)))
                        .count(),
                    1
                );
            }
            let stopped_at = Instant::now();
            worker.stop().unwrap();
            assert!(stopped_at.elapsed() < Duration::from_secs(1));
        }
    });
}

#[test]
fn owner_fires_match_deadline_without_external_tick() {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap();
    runtime.block_on(async {
        let mut worker = BattleWorker::start(session(40)).unwrap();
        let batch = tokio::time::timeout(Duration::from_secs(2), worker.recv())
            .await
            .unwrap()
            .unwrap();
        assert!(batch.iter().any(|event| {
            matches!(event, Output::Ended(settlement) if settlement.ended_at_ms >= 40)
        }));
        worker.stop().unwrap();
    });
}

#[test]
fn paused_owner_starts_its_deadline_only_after_activation() {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap();
    runtime.block_on(async {
        let mut worker = BattleWorker::start_paused(session(40)).unwrap();
        let sender = worker.sender();
        assert_eq!(
            sender.clock_ms().unwrap_err().kind(),
            io::ErrorKind::WouldBlock
        );
        assert_eq!(
            sender.submit(7, Input::Tick).await.unwrap_err().kind(),
            io::ErrorKind::WouldBlock
        );
        tokio::time::sleep(Duration::from_millis(100)).await;
        assert!(matches!(
            worker.try_recv(),
            Err(tokio::sync::mpsc::error::TryRecvError::Empty)
        ));
        worker.activate().unwrap();
        assert!(sender.clock_ms().unwrap() < 40);
        assert!(worker.activate().is_err());
        let batch = tokio::time::timeout(Duration::from_secs(2), worker.recv())
            .await
            .unwrap()
            .unwrap();
        assert!(batch.iter().any(|event| {
            matches!(event, Output::Ended(settlement) if settlement.ended_at_ms >= 40)
        }));
        worker.stop().unwrap();

        let mut stopped_before_start = BattleWorker::start_paused(session(40)).unwrap();
        let stale_activation = stopped_before_start.activator().unwrap();
        stopped_before_start.stop().unwrap();
        assert!(!stopped_before_start.is_running());
        assert!(stale_activation.activate().is_err());
    });
}

#[test]
fn queued_bot_output_retains_the_pose_used_by_its_decision() {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap();
    runtime.block_on(async {
        let mut worker =
            BattleWorker::start_with_limits(session(10000), 32, 1024 * 1024, 10000, false).unwrap();
        let sender = worker.sender();
        let world = |position: f64, sampled_at_ms| {
            Input::World(Arc::new(World {
                epoch: 7,
                sampled_at_ms,
                actors: BTreeMap::from([
                    (
                        1,
                        Pose {
                            entity: 101,
                            position: [0., 20., 0.],
                            heading: 0.,
                            sequence: 1,
                            contact: ContactObservation::unavailable(),
                        },
                    ),
                    (
                        2,
                        Pose {
                            entity: 102,
                            position: [position, 20., 0.],
                            heading: 180.,
                            sequence: 1,
                            contact: ContactObservation::unavailable(),
                        },
                    ),
                ]),
            }))
        };
        sender
            .submit(7, world(1., sender.clock_ms().unwrap()))
            .await
            .unwrap();
        sender.submit(7, Input::Tick).await.unwrap();
        sender
            .submit(7, world(9., sender.clock_ms().unwrap()))
            .await
            .unwrap();
        sender.submit(7, Input::Tick).await.unwrap();
        let first = worker.try_recv_contextual().unwrap();
        assert!(
            first
                .outputs
                .iter()
                .any(|event| matches!(event, Output::State(_)))
        );
        let decision = worker.try_recv_contextual().unwrap();
        assert!(
            decision
                .outputs
                .iter()
                .any(|event| matches!(event, Output::Bot(_)))
        );
        assert_eq!(decision.context.actor_poses[&2].position, [1., 20., 0.]);
        let newer = worker.try_recv_contextual().unwrap();
        assert!(
            newer
                .outputs
                .iter()
                .any(|event| matches!(event, Output::Bot(_)))
        );
        assert_eq!(newer.context.actor_poses[&2].position, [9., 20., 0.]);
        worker.stop().unwrap();
    });
}
