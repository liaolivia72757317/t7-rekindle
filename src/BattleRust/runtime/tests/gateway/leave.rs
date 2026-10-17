use super::*;
use t7r_battle_runtime::battle_session::{EndReason, Settlement};

fn leave_uplink(name: &str) -> Vec<u8> {
    hex(
        include_str!("../../../../../tests/fixtures/protocol/leave-uplink-v1.txt")
            .lines()
            .find(|line| line.starts_with(&format!("{name} ")))
            .unwrap()
            .split_whitespace()
            .nth(3)
            .unwrap(),
    )
}

fn start(duration_ms: u64, clock_lead_ms: u64) -> (Gateway, TcpStream, SourceBinding, u64) {
    let mut gateway = gateway();
    let state = gateway.start(1).unwrap();
    let (mut config, binding) = match_config(7);
    config.duration_ms = duration_ms;
    gateway
        .start_battle_staged(config, binding.clone())
        .unwrap();
    let mut instance = connect(state.ports[2]);
    read_body(&mut instance, 44);
    let update = read_body(&mut instance, 10);
    let native_at = u64::from_be_bytes(update[2..10].try_into().unwrap());
    let offset = native_at + clock_lead_ms - 1000;
    gateway.bind_battle_client_clock(7, -1000).unwrap();
    gateway
        .bind_battle_server_clock(7, i64::try_from(offset).unwrap())
        .unwrap();
    gateway.activate_battle(7).unwrap();
    (gateway, instance, binding, offset)
}

fn settlement_start(
    gateway: &mut Gateway,
    instance: &mut TcpStream,
    binding: &SourceBinding,
    offset: u64,
) -> Settlement {
    let mut ended = None;
    wait(|| {
        if let Some(batch) = gateway.dispatch_next_battle_output().unwrap() {
            ended = batch.into_iter().find_map(|output| match output {
                Output::Ended(value) => Some(value),
                _ => None,
            });
        }
        ended.is_some()
    });
    let mut settlement = ended.unwrap();
    settlement.started_at_ms += offset;
    settlement.ended_at_ms += offset;
    for message in encode_settlement_start_for_client(&settlement, &binding.actor_rids).unwrap() {
        let (command, _, body) = read_without_startup(instance);
        assert_eq!(command, message.command);
        assert_eq!(body, message.body);
    }
    settlement
}

fn finish_and_return(gateway: &Gateway, instance: &mut TcpStream, settlement: &Settlement) {
    let identity = request(gateway);
    assert!(
        gateway
            .mailbox()
            .lock()
            .unwrap()
            .complete(&response(&identity))
            .unwrap()
    );
    let settle = read_body(instance, 52);
    assert_eq!(&settle[..2], &[0, 1]);
    assert_eq!(
        &settle[settle.len() - 4..],
        &i32::from(settlement.members[0].result).to_be_bytes()
    );
    let finish = read_body(instance, 10);
    assert_eq!(&finish[..2], &[0, 0x69]);
    assert_eq!(&finish[18..22], &i32::from(settlement.result).to_be_bytes());
    assert!(u64::from_be_bytes(finish[2..10].try_into().unwrap()) >= settlement.ended_at_ms);
    assert_eq!(&read_body(instance, 10)[..2], &[0, 3]);
    let logout = read_body(instance, 44);
    assert_eq!(&logout[..2], &[0, 3]);
    assert_eq!(&logout[10..], &[0, 0, 0, 0, 1]);
    assert!(!gateway.battle_returned_to_room().unwrap());
    instance
        .write_all(&leave_uplink("user-group-quit"))
        .unwrap();
    assert_eq!(&read_body(instance, 55)[..2], &[0, 2]);
    wait(|| gateway.battle_returned_to_room().unwrap());
    assert_eq!(gateway.snapshot().unwrap().connection_errors, 0);
    assert!(gateway.last_error().unwrap().is_empty());
}

#[test]
fn manual_logout_with_a_leading_projected_clock_preserves_the_exit_handshake() {
    // Exaggerate clock quantization so the regression does not depend on scheduling.
    let (mut gateway, mut instance, binding, offset) = start(120_000, 2000);
    let began = Instant::now();
    instance.write_all(&leave_uplink("sync-logout")).unwrap();
    let settlement = settlement_start(&mut gateway, &mut instance, &binding, offset);
    assert_eq!(settlement.reason, EndReason::Quit);
    finish_and_return(&gateway, &mut instance, &settlement);
    assert!(began.elapsed() < Duration::from_secs(4));
    gateway.stop_battle().unwrap();
    let (config, binding) = match_config(8);
    gateway.start_battle_staged(config, binding).unwrap();
    assert!(!gateway.battle_returned_to_room().unwrap());
    let _next_instance = connect_instance(gateway.snapshot().unwrap().ports[2]);
    gateway.stop().unwrap();
}

#[test]
fn manual_logout_during_the_natural_settlement_hold_keeps_the_frozen_result() {
    let (mut gateway, mut instance, binding, offset) = start(100, 0);
    let settlement = settlement_start(&mut gateway, &mut instance, &binding, offset);
    assert_eq!(settlement.reason, EndReason::Timeout);
    assert_eq!(settlement.result, 3);
    let began = Instant::now();
    instance.write_all(&leave_uplink("sync-logout")).unwrap();
    finish_and_return(&gateway, &mut instance, &settlement);
    assert!(began.elapsed() < Duration::from_secs(4));
    gateway.stop().unwrap();
}
