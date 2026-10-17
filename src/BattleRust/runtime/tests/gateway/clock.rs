use super::*;

fn clock_uplink(name: &str) -> Vec<u8> {
    hex(
        include_str!("../../../../../tests/fixtures/protocol/gateway-clock-v1.txt")
            .lines()
            .find(|line| line.starts_with(&format!("{name} ")))
            .unwrap()
            .split_whitespace()
            .nth(2)
            .unwrap(),
    )
}

fn server_time(body: &[u8]) -> u64 {
    u64::from_be_bytes(body[2..10].try_into().unwrap())
}

#[test]
fn all_channels_unify_to_the_instance_server_timeline() {
    let mut gateway = gateway();
    let state = gateway.start(1).unwrap();
    let (config, binding) = match_config(7);
    gateway.start_battle_staged(config, binding).unwrap();
    let mut login = connect(state.ports[0]);
    read_body(&mut login, 1);
    let mut logic = connect(state.ports[1]);
    let identity = request(&gateway);
    gateway
        .mailbox()
        .lock()
        .unwrap()
        .complete(&response(&identity))
        .unwrap();
    read_body(&mut logic, 1);
    read_body(&mut logic, 44);
    let mut instance = connect(state.ports[2]);
    read_body(&mut instance, 44);
    let instance_at = server_time(&read_body(&mut instance, 10));
    for stream in [&mut login, &mut logic, &mut instance] {
        stream.write_all(&clock_uplink("unify")).unwrap();
        let (command, _, body) = read_without_startup(stream);
        assert_eq!(command, 8);
        assert_eq!(&body[..2], &[0, 10]);
        assert_eq!(&body[10..], &[0; 8]);
        let at = server_time(&body);
        assert!(
            at >= instance_at && at - instance_at < 5000,
            "unify clock {at} differs from instance clock {instance_at}"
        );
    }
    gateway.stop().unwrap();
}

#[test]
fn throttled_instance_clock_requests_do_not_disconnect_or_block_the_next_command() {
    let mut gateway = gateway();
    let state = gateway.start(1).unwrap();
    let (config, binding) = match_config(7);
    gateway.start_battle_staged(config, binding).unwrap();
    let mut instance = connect_instance(state.ports[2]);
    let burst = [
        clock_uplink("unify"),
        clock_uplink("unify-throttled"),
        clock_uplink("heartbeat"),
    ]
    .concat();
    instance.write_all(&burst).unwrap();
    assert_eq!(read_without_startup(&mut instance).0, 8);
    let (command, _, body) = read_without_startup(&mut instance);
    assert_eq!(command, 29);
    assert_eq!(body, corpus("heartbeat").2);
    assert_eq!(gateway.snapshot().unwrap().connection_errors, 0);
    gateway.stop().unwrap();
}

#[test]
fn instance_periodically_syncs_before_and_after_activation_and_stops_on_rematch() {
    let mut gateway = gateway();
    let state = gateway.start(1).unwrap();
    for epoch in [7, 8] {
        let (config, binding) = match_config(epoch);
        gateway.start_battle_staged(config, binding).unwrap();
        let mut instance = connect(state.ports[2]);
        read_body(&mut instance, 44);
        let started_at = server_time(&read_body(&mut instance, 10));
        for selector in [0x65_u16, 0x66, 0x67, 0x194, 0xc8] {
            let body = read_body(&mut instance, 10);
            assert_eq!(&body[..2], &selector.to_be_bytes());
        }
        let (command, _, first) = read_wire_message(&mut instance);
        assert_eq!(command, 8);
        assert_eq!(&first[..2], &[0, 11]);
        assert_eq!(&first[10..], &[0, 0, 0, 0, 0, 0, 0, 0, 1]);
        assert!(server_time(&first) >= started_at + 1900);
        assert_eq!(
            gateway.battle_clock_ms().unwrap_err().kind(),
            std::io::ErrorKind::WouldBlock
        );
        gateway.activate_battle(epoch).unwrap();
        let (command, _, second) = read_wire_message(&mut instance);
        assert_eq!(command, 8);
        assert_eq!(&second[..2], &[0, 11]);
        assert_eq!(&second[10..], &first[10..]);
        assert!(server_time(&second) >= server_time(&first) + 1900);
        assert!(gateway.battle_clock_ms().unwrap() >= 2900);
        gateway.stop_battle().unwrap();
        assert_eq!(instance.read(&mut [0; 1]).unwrap(), 0);
    }
    assert_eq!(gateway.snapshot().unwrap().connection_errors, 0);
    gateway.stop().unwrap();
}
