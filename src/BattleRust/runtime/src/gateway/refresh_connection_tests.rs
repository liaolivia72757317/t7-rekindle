use super::*;
use crate::gateway::refresh::RefreshUpdate;

fn send_refresh(stream: &mut TcpStream, name: &str) {
    let row = include_str!("../../../../../tests/fixtures/protocol/instance-refresh-uplink-v1.txt")
        .lines()
        .find(|line| line.starts_with(&format!("{name} ")))
        .unwrap();
    stream
        .write_all(&hex(row.split_whitespace().nth(4).unwrap()))
        .unwrap();
}

fn read_message(stream: &mut TcpStream) -> (u16, Vec<u8>) {
    loop {
        let mut head = [0; 12];
        stream.read_exact(&mut head).unwrap();
        let length = u32::from_be_bytes(head[8..].try_into().unwrap()) as usize;
        assert!(length < 4096);
        let mut body = vec![0; length];
        stream.read_exact(&mut body).unwrap();
        assert_eq!(&body[3..11], &[0; 8]);
        let command = u16::from_be_bytes(body[1..3].try_into().unwrap());
        let selector = u16::from_be_bytes(body[11..13].try_into().unwrap());
        if command == 8
            || command == 54 && selector == 1
            || command == 10 && [0x65, 0x66, 0x194, 0xc8].contains(&selector)
        {
            continue;
        }
        return (command, body[11..].to_vec());
    }
}

fn enter(gateway: &Gateway, stream: &mut TcpStream) {
    send(stream, RETRIES, "bound-before-entry");
    complete_request(gateway);
    selection_reply(stream, 0);
    send(stream, RETRIES, "enter");
    read_until(stream, 54, 0x65);
    let start = read_until(stream, 10, 0x67);
    assert_eq!(&start[18..22], &3_i32.to_be_bytes());
}

#[test]
fn hero_ui_refresh_is_a_noop_except_before_entry_or_authoritative_selecting() {
    let (mut gateway, mut stream) = loaded_instance(None);
    send_refresh(&mut stream, "ui-before-entry");
    assert_eq!(read_until(&mut stream, 10, 11), hex("000bfe"));
    enter(&gateway, &mut stream);
    for (name, life, allowed) in [
        ("playing", Life::Alive, false),
        (
            "dead",
            Life::Dead {
                died_at_ms: 1000,
                respawn_at_ms: 9000,
            },
            false,
        ),
        ("selecting", Life::Selecting { died_at_ms: 1000 }, true),
        ("waiting", Life::Waiting { died_at_ms: 1000 }, false),
        (
            "spawning",
            Life::Spawning {
                spawned_at_ms: 9000,
                spawn: t7r_battle_core::navigation::SpawnReference {
                    position: [0.; 3],
                    heading: 0.,
                },
            },
            false,
        ),
    ] {
        publish_life(&gateway, &mut stream, life);
        send_refresh(&mut stream, &format!("ui-{name}"));
        send_refresh(&mut stream, &format!("heartbeat-{name}"));
        if allowed {
            assert_eq!(read_message(&mut stream), (10, hex("000bfe")));
        }
        assert_eq!(
            read_message(&mut stream),
            (29, hex("00020000007b00000000000001c8"))
        );
    }
    assert_eq!(gateway.snapshot().unwrap().connection_errors, 0);
    gateway.stop().unwrap();
}

#[test]
fn preparation_object_refresh_reapplies_movement_lock_before_game() {
    let (mut gateway, mut stream) = loaded_instance(None);
    enter(&gateway, &mut stream);
    send_refresh(&mut stream, "objects");
    send_refresh(&mut stream, "heartbeat-objects");
    assert_eq!(read_message(&mut stream).0, 14);
    assert_eq!(read_message(&mut stream).0, 14);
    let (command, inactive) = read_message(&mut stream);
    assert_eq!(command, 2);
    assert_eq!(&inactive[..2], &[0, 0x1f]);
    assert_eq!(&inactive[6..], &[0, 1, 0]);
    assert_eq!(read_message(&mut stream).0, 29);
    assert_eq!(gateway.snapshot().unwrap().connection_errors, 0);
    gateway.stop().unwrap();
}

#[test]
fn refresh_during_prepare_and_game_preserves_server_start_time_and_duration() {
    let (mut gateway, mut stream) = loaded_instance(None);
    enter(&gateway, &mut stream);
    send_refresh(&mut stream, "refresh-prepare");
    let update = read_message(&mut stream);
    assert_eq!((update.0, &update.1[..2]), (10, [0, 100].as_slice()));
    assert_eq!(read_message(&mut stream).0, 23);
    thread::sleep(Duration::from_secs(2));
    let game = loop {
        let state = read_until(&mut stream, 10, 0x67);
        if state[18..22] == 4_i32.to_be_bytes() {
            break state;
        }
    };
    assert_eq!(&game[18..22], &4_i32.to_be_bytes());
    let (command, active) = read_message(&mut stream);
    assert_eq!(command, 2);
    assert_eq!(&active[..2], &[0, 0x1f]);
    assert_eq!(&active[6..], &[0, 1, 1]);
    let started = u64::from_be_bytes(game[22..30].try_into().unwrap());
    for name in ["refresh-game", "refresh-game-again"] {
        send_refresh(&mut stream, name);
        assert_eq!(read_message(&mut stream).1[..2], [0, 100]);
        let (command, refreshed) = read_message(&mut stream);
        assert_eq!(command, 10);
        assert_eq!(&refreshed[18..22], &4_i32.to_be_bytes());
        assert_eq!(&refreshed[22..30], &started.to_be_bytes());
        assert_eq!(&refreshed[30..38], &120000_u64.to_be_bytes());
        assert!(u64::from_be_bytes(refreshed[2..10].try_into().unwrap()) >= started);
        assert_eq!(read_message(&mut stream).0, 23);
    }
    assert_eq!(gateway.snapshot().unwrap().connection_errors, 0);
    gateway.stop().unwrap();
}

#[test]
fn pose_only_refresh_batch_uses_the_bounded_queue_and_hides_local_corpse() {
    let (mut gateway, mut stream) = loaded_instance(None);
    let active = gateway.instance_tx.borrow().clone().unwrap();
    let context = crate::battle_session::OutputContext {
        at_ms: 1000,
        actor_poses: BTreeMap::from([(
            1,
            crate::battle_session::SampledPose {
                position: [5., 6., 0.],
                heading: 45.,
                sampled_at_ms: 1000,
            },
        )]),
    };
    let update = RefreshUpdate::project(&[], &context, Some(active.roster()), None, None);
    assert!(update.heap_bytes() > 0);
    active.send_projected(vec![], None, None, update).unwrap();
    // A message in the same queue is a deterministic barrier, not a sleep.
    publish_life(&gateway, &mut stream, Life::Alive);
    send_refresh(&mut stream, "objects");
    let (command, vision) = read_message(&mut stream);
    assert_eq!(command, 14);
    assert_eq!(&vision[45..47], &45_i16.to_be_bytes());
    let position: Vec<_> = vision[47..59]
        .chunks_exact(4)
        .map(|bytes| f32::from_be_bytes(bytes.try_into().unwrap()))
        .collect();
    assert_eq!(position, [5., 6., 0.]);
    assert_eq!(read_message(&mut stream).0, 14);
    let (command, inactive) = read_message(&mut stream);
    assert_eq!(command, 2);
    assert_eq!(&inactive[..2], &[0, 0x1f]);
    assert_eq!(&inactive[6..], &[0, 1, 0]);
    publish_life(
        &gateway,
        &mut stream,
        Life::Dead {
            died_at_ms: 1000,
            respawn_at_ms: 9000,
        },
    );
    send_refresh(&mut stream, "objects-again");
    send_refresh(&mut stream, "heartbeat-objects");
    let (command, bot) = read_message(&mut stream);
    assert_eq!(command, 14);
    assert_eq!(&bot[14..22], &202_u64.to_be_bytes());
    let (command, inactive) = read_message(&mut stream);
    assert_eq!(command, 2);
    assert_eq!(&inactive[..2], &[0, 0x1f]);
    assert_eq!(&inactive[6..], &[0, 1, 0]);
    assert_eq!(read_message(&mut stream).0, 29);
    assert_eq!(gateway.snapshot().unwrap().connection_errors, 0);
    gateway.stop().unwrap();
}

#[test]
fn refresh_enqueue_failure_retains_the_authoritative_batch_for_retry() {
    let (mut gateway, _stream) = loaded_instance(None);
    let old = gateway.instance_tx.borrow().clone().unwrap();
    let replacement = Arc::new(
        InstanceIngress::new(
            SourceBinding {
                epoch: 7,
                source_id: 1,
                actor_rids: BTreeMap::from([(1, 101), (2, 202)]),
            },
            old.roster().clone(),
            gateway.battle.as_ref().unwrap().sender(),
            None,
            Arc::new(tokio::sync::Mutex::new(0)),
            1028,
            120000,
            gateway.resources.clone(),
        )
        .unwrap(),
    );
    replacement
        .bind_identity(old.identity().unwrap().unwrap())
        .unwrap();
    gateway.instance_tx.send_replace(Some(replacement.clone()));
    for _ in 0..32 {
        replacement
            .send(vec![scene_wire::actor_choose_hero_result(1)])
            .unwrap();
    }
    gateway.bind_battle_client_clock(7, -1000).unwrap();
    gateway.bind_battle_server_clock(7, 5000).unwrap();
    gateway.battle.as_mut().unwrap().activate().unwrap();
    let deadline = Instant::now() + Duration::from_secs(3);
    loop {
        match gateway.dispatch_next_battle_output() {
            Err(error) => {
                assert_eq!(error.kind(), io::ErrorKind::WouldBlock);
                break;
            }
            Ok(None) => assert!(Instant::now() < deadline),
            Ok(Some(_)) => panic!("full queue consumed a refresh transaction"),
        }
        thread::sleep(Duration::from_millis(5));
    }
    let pending = gateway
        .battle
        .as_mut()
        .unwrap()
        .pending_output()
        .unwrap()
        .unwrap()
        .to_vec();
    assert!(
        pending
            .iter()
            .any(|event| matches!(event, Output::State(_)))
    );
    let mut receiver = replacement.take_output_receiver().unwrap();
    for _ in 0..32 {
        receiver.try_recv().unwrap();
    }
    assert_eq!(
        gateway.dispatch_next_battle_output().unwrap().unwrap(),
        pending
    );
    let batch = receiver.try_recv().unwrap();
    assert!(batch.messages.is_empty());
    assert!(!batch.refresh.is_empty());
    assert!(receiver.try_recv().is_err());
    gateway.stop().unwrap();
}
