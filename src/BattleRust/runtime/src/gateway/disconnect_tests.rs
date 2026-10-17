use super::*;
use std::net::Shutdown;

fn wait_instance_closed(gateway: &Gateway) {
    let deadline = Instant::now() + Duration::from_secs(5);
    while gateway.snapshot().unwrap().active_connections != 0 {
        assert!(Instant::now() < deadline, "instance did not close");
        thread::sleep(Duration::from_millis(5));
    }
}

fn close_instance(gateway: &Gateway, stream: &TcpStream) {
    stream.shutdown(Shutdown::Write).unwrap();
    wait_instance_closed(gateway);
    let state = gateway.snapshot().unwrap();
    assert!(state.running && !state.failed);
    assert_eq!(state.connection_errors, 0);
    assert!(gateway.last_error().unwrap().is_empty());
    assert!(!gateway.battle_returned_to_room().unwrap());
}

#[test]
fn clean_instance_disconnect_accepts_late_output_and_stops_without_error() {
    let (mut gateway, stream) = loaded_instance(None);
    close_instance(&gateway, &stream);

    for _ in 0..65 {
        let result =
            gateway.send_battle_client_messages(7, vec![scene_wire::actor_choose_hero_result(1)]);
        gateway.battle_result(result).unwrap();
    }
    assert!(gateway.last_error().unwrap().is_empty());
    assert_eq!(gateway.snapshot().unwrap().connection_errors, 0);
    gateway.stop_battle().unwrap();
    gateway.stop().unwrap();
    gateway.stop().unwrap();
    let state = gateway.snapshot().unwrap();
    assert!(!state.running && !state.failed);
    assert_eq!(state.ports, [0; 3]);
    gateway.start(2).unwrap();
    assert!(gateway.last_error().unwrap().is_empty());
    gateway.stop().unwrap();
}

#[test]
fn protocol_instance_close_accepts_late_output_without_error() {
    let (mut gateway, mut stream) = loaded_instance(None);
    stream
        .write_all(&[0x55, 0x0e, 13, 0, 0, 0, 0, 12, 0, 0, 0, 0])
        .unwrap();
    wait_instance_closed(&gateway);
    let result =
        gateway.send_battle_client_messages(7, vec![scene_wire::actor_choose_hero_result(1)]);
    gateway.battle_result(result).unwrap();
    assert!(gateway.last_error().unwrap().is_empty());
    assert_eq!(gateway.snapshot().unwrap().connection_errors, 0);
    assert!(!gateway.battle_returned_to_room().unwrap());
    gateway.stop().unwrap();
}

#[test]
fn truncated_instance_frame_preserves_transport_failure_after_disconnect() {
    let (mut gateway, mut stream) = loaded_instance(None);
    stream.write_all(&[0x55, 0x0e]).unwrap();
    stream.shutdown(Shutdown::Write).unwrap();
    wait_instance_closed(&gateway);
    assert_eq!(gateway.snapshot().unwrap().connection_errors, 1);
    let original = gateway.last_error().unwrap();
    assert!(!original.is_empty());
    let result =
        gateway.send_battle_client_messages(7, vec![scene_wire::actor_choose_hero_result(1)]);
    assert_eq!(
        gateway.battle_result(result).unwrap_err().kind(),
        io::ErrorKind::BrokenPipe
    );
    assert!(gateway.last_error().unwrap().contains(&original));
    gateway.stop().unwrap();
}

#[test]
fn clean_instance_disconnect_consumes_published_battle_output() {
    let (mut gateway, stream) = loaded_instance(None);
    close_instance(&gateway, &stream);
    gateway.bind_battle_client_clock(7, -1000).unwrap();
    gateway.bind_battle_server_clock(7, 5000).unwrap();
    gateway.battle.as_mut().unwrap().activate().unwrap();

    let deadline = Instant::now() + Duration::from_secs(5);
    let pending = loop {
        if let Some(batch) = gateway.battle.as_mut().unwrap().pending_output().unwrap() {
            break batch.to_vec();
        }
        assert!(Instant::now() < deadline, "missing battle output");
        thread::sleep(Duration::from_millis(5));
    };
    assert!(
        pending
            .iter()
            .any(|output| matches!(output, Output::State(_)))
    );
    assert_eq!(
        gateway.dispatch_next_battle_output().unwrap().unwrap(),
        pending
    );
    assert!(
        gateway
            .battle
            .as_mut()
            .unwrap()
            .take_pending_output()
            .is_none()
    );
    assert!(gateway.last_error().unwrap().is_empty());
    gateway.stop().unwrap();
}

#[test]
fn clean_instance_disconnect_preserves_output_validation_errors() {
    let (mut gateway, stream) = loaded_instance(None);
    close_instance(&gateway, &stream);
    let result = gateway.send_battle_client_messages(
        7,
        vec![ClientBattleMessage {
            command: 54,
            body: vec![0; t7r_battle_core::protocol::MAX_FRAME],
        }],
    );
    assert_eq!(
        gateway.battle_result(result).unwrap_err().kind(),
        io::ErrorKind::InvalidData
    );
    assert!(
        gateway
            .last_error()
            .unwrap()
            .contains("frame exceeds protocol limit")
    );
    gateway.stop().unwrap();
}
