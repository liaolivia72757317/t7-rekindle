use super::*;
use crate::battle_session::{Input, Pose, World};

const SQUAD_REQUESTS: &str =
    include_str!("../../../../../tests/fixtures/protocol/instance-squad-uplink-v1.txt");

pub(super) fn assert_initial_squads(snapshot: &[u8], bot_camp: u8) {
    let expected = match bot_camp {
        1 => {
            "010028000000280001000000010000000100000000000000ca000200000000000000ca00000000000000000000006500000000"
        }
        2 => concat!(
            "0100280001000000010000000100000000000000ca000100000000000000ca00000000",
            "00280001000000010000000100000000000000650001000000000000006500000000"
        ),
        _ => panic!("invalid bot camp"),
    };
    assert_eq!(&snapshot[61..], hex(expected));
}

#[test]
fn squad_socket_initial_refresh_keeps_local_player_in_first_squad() {
    for bot_camp in [1, 2] {
        let (mut gateway, mut stream) = loaded_instance_with_camp(None, bot_camp);
        send(&mut stream, SQUAD_REQUESTS, "refresh");
        assert_initial_squads(&read_until(&mut stream, 10, 100), bot_camp);
        assert_eq!(gateway.snapshot().unwrap().connection_errors, 0);
        gateway.stop().unwrap();
    }
}

fn project_until(gateway: &mut Gateway, predicate: impl Fn(&Output) -> bool) {
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        assert!(Instant::now() < deadline, "squad output was not published");
        if let Some(outputs) = gateway.dispatch_next_battle_output().unwrap()
            && outputs.iter().any(&predicate)
        {
            return;
        }
        thread::sleep(Duration::from_millis(5));
    }
}

fn take_worker_batch(
    worker: &mut crate::battle_worker::BattleWorker,
    predicate: impl Fn(&Output) -> bool,
) -> crate::battle_worker::PublishedBatch {
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        assert!(Instant::now() < deadline, "squad input did not reach owner");
        if let Ok(batch) = worker.try_recv_contextual()
            && batch.outputs.iter().any(&predicate)
        {
            return batch;
        }
        thread::sleep(Duration::from_millis(5));
    }
}

fn send_worker_batch(gateway: &Gateway, batch: &crate::battle_worker::PublishedBatch) {
    let life = batch.outputs.iter().find_map(|o| match o {
        Output::Life { actor_id: 1, state } => Some(*state),
        _ => None,
    });
    let active = gateway.instance_tx.borrow().as_ref().unwrap().clone();
    let roster = active.roster_for_outputs(&batch.outputs).unwrap();
    let identity = active.identity().unwrap().unwrap();
    let (messages, _) = output::project_client_messages_with_scene(
        gateway.battle.as_ref().unwrap().client_output(),
        &batch.outputs,
        &batch.context,
        Some(-1000),
        Some(5000),
        Some(1),
        active.actor_rids(),
        Some(output::SceneProjection {
            roster: &roster,
            identity: &identity,
        }),
    )
    .unwrap();
    active
        .send_projected(
            messages,
            None,
            life,
            super::super::refresh::RefreshUpdate::default(),
        )
        .unwrap();
}

#[test]
fn squad_socket_create_join_leave_and_refresh_use_authoritative_roster() {
    let (mut gateway, mut stream) = loaded_instance_with_camp(None, 1);
    send(&mut stream, SQUAD_REQUESTS, "create-before-game");
    assert_eq!(
        read_until(&mut stream, 64, 2)[2..],
        810001_i32.to_be_bytes()
    );
    send(&mut stream, RETRIES, "bound-before-entry");
    complete_request(&gateway);
    selection_reply(&mut stream, 0);
    send(&mut stream, RETRIES, "enter");
    read_until(&mut stream, 54, 0x65);
    read_until(&mut stream, 10, 0x67);
    thread::sleep(Duration::from_millis(1500));
    loop {
        let state = read_until(&mut stream, 10, 0x67);
        if state[18..22] == 4_i32.to_be_bytes() {
            break;
        }
    }
    gateway.bind_battle_client_clock(7, 0).unwrap();
    gateway.bind_battle_server_clock(7, 5000).unwrap();
    for (name, selector, result) in [
        ("join-bots", 8, 810004_i32),
        ("leave", 11, 0),
        ("create", 2, 0),
        ("create-duplicate", 2, 810004),
        ("dismiss", 5, 0),
    ] {
        send(&mut stream, SQUAD_REQUESTS, name);
        project_until(&mut gateway, |output| {
            matches!(output, Output::Squad { .. })
        });
        assert_eq!(
            read_until(&mut stream, 64, selector)[2..],
            result.to_be_bytes()
        );
    }
    send(&mut stream, SQUAD_REQUESTS, "refresh");
    let snapshot = read_until(&mut stream, 10, 100);
    assert_eq!(
        &snapshot[61..],
        &hex("010028000000280001000000010000000100000000000000ca000100000000000000ca00000000")
    );
    assert_eq!(gateway.snapshot().unwrap().connection_errors, 0);
    gateway.stop().unwrap();
}

#[test]
fn squad_socket_spawn_reaches_worker_and_uses_the_bound_teammate_position() {
    let mut worker =
        crate::battle_worker::BattleWorker::start(crate::battle_session::selecting_squad_fixture())
            .unwrap();
    let sender = worker.sender();
    let (mut gateway, mut stream) = loaded_instance_with_camp(Some(sender.clone()), 1);
    send(&mut stream, RETRIES, "bound-before-entry");
    complete_request(&gateway);
    selection_reply(&mut stream, 0);
    send(&mut stream, RETRIES, "enter");
    read_until(&mut stream, 54, 101);
    read_until(&mut stream, 10, 103);
    publish_life(&gateway, &mut stream, Life::Selecting { died_at_ms: 1000 });
    send(&mut stream, RETRIES, "other-after-death");
    assert_eq!(read_until(&mut stream, 54, 51)[2..], 0_i32.to_be_bytes());
    send(&mut stream, SQUAD_REQUESTS, "squad-stale");
    let rejected = take_worker_batch(&mut worker, |o| matches!(o, Output::SpawnRejected { .. }));
    send_worker_batch(&gateway, &rejected);
    assert_eq!(
        read_until(&mut stream, 54, 101)[2..],
        810014_i32.to_be_bytes()
    );
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_time()
        .build()
        .unwrap();
    let now = sender.clock_ms().unwrap();
    runtime
        .block_on(
            sender.submit(
                7,
                Input::World(Arc::new(World {
                    epoch: 7,
                    sampled_at_ms: now,
                    actors: (1..=2)
                        .map(|id| {
                            (
                                id,
                                Pose {
                                    entity: u64::from(id),
                                    position: [0., 20., 0.],
                                    heading: 90.,
                                    sequence: 1,
                                    contact: t7r_battle_core::ai::ContactObservation::unavailable(),
                                },
                            )
                        })
                        .collect(),
                })),
            ),
        )
        .unwrap();
    send(&mut stream, SQUAD_REQUESTS, "squad-spawn");
    let batch = take_worker_batch(&mut worker, |o| {
        matches!(
            o,
            Output::Life {
                actor_id: 1,
                state: Life::Spawning { .. }
            }
        )
    });
    let life = batch
        .outputs
        .iter()
        .find_map(|o| match o {
            Output::Life { actor_id: 1, state } => Some(*state),
            _ => None,
        })
        .unwrap();
    assert!(matches!(life, Life::Spawning { spawn, .. } if spawn.position == [6., 20., 0.]));
    send_worker_batch(&gateway, &batch);
    assert_eq!(read_until(&mut stream, 54, 101)[2..], 0_i32.to_be_bytes());
    let vision = read_until(&mut stream, 14, 1);
    assert_eq!(&vision[47..59], &hex("40c0000041a0000000000000"));
    assert_eq!(gateway.snapshot().unwrap().connection_errors, 0);
    gateway.stop().unwrap();
    worker.stop().unwrap();
}
