use super::*;

fn uplink(name: &str) -> Vec<u8> {
    let row =
        include_str!("../../../../../tests/fixtures/protocol/command-adaptation-uplink-v1.txt")
            .lines()
            .find(|line| line.starts_with(&format!("{name} ")))
            .unwrap();
    hex(row.split_whitespace().nth(4).unwrap())
}

#[test]
fn unhandled_commands_on_every_channel_preserve_the_next_heartbeat() {
    for role in 0..3 {
        let mut gateway = gateway();
        let ports = gateway.start(1).unwrap().ports;
        let (config, binding) = match_config(7);
        gateway.start_battle(config, binding).unwrap();
        let mut stream = if role == 2 {
            connect_instance(ports[role])
        } else {
            connect(ports[role])
        };
        for name in [
            "unknown-command",
            "unknown-selector",
            "unknown-action",
            "title-set",
            "heartbeat",
        ] {
            stream.write_all(&uplink(name)).unwrap();
        }
        let response = read_without_startup(&mut stream);
        assert_eq!(response.0, 29, "unexpected reply on role {role}");
        assert_eq!(response.2, corpus("heartbeat").2);
        assert_eq!(gateway.snapshot().unwrap().connection_errors, 0);
        assert!(gateway.last_error().unwrap().is_empty());
        gateway.stop().unwrap();
    }
}

#[test]
fn malformed_supported_command_still_reports_a_protocol_error() {
    let mut gateway = gateway();
    let ports = gateway.start(1).unwrap().ports;
    let (config, binding) = match_config(7);
    gateway.start_battle(config, binding).unwrap();
    let mut stream = connect_instance(ports[2]);
    stream.write_all(&uplink("bad-weapon-length")).unwrap();
    wait(|| gateway.snapshot().unwrap().connection_errors > 0);
    assert!(gateway.last_error().unwrap().contains("weapon"));
    gateway.stop().unwrap();
}

#[test]
fn weapon_slot_request_reaches_the_bound_actor_without_disconnect() {
    let mut gateway = gateway();
    let ports = gateway.start(1).unwrap().ports;
    let (config, binding) = match_config(7);
    gateway.start_battle(config, binding).unwrap();
    let mut stream = connect_instance(ports[2]);
    stream.write_all(&uplink("weapon-fist")).unwrap();
    let mut seen = false;
    wait(|| {
        assert_eq!(
            gateway.snapshot().unwrap().connection_errors,
            0,
            "{}",
            gateway.last_error().unwrap()
        );
        if let Some(batch) = gateway.read_battle_outputs().unwrap() {
            seen |= batch.iter().any(|event| {
                matches!(
                    event,
                    Output::Action(ActionEvent::EquipmentChanged {
                        actor_id: 1,
                        weapon_id: 100000,
                        offhand_id: 0
                    })
                )
            });
        }
        seen
    });
    let at_ms = gateway.battle_clock_ms().unwrap();
    let player = |batch_id| {
        PlayerIntent {
            stamp: SourceStamp {
                epoch: 7,
                source_id: 1,
                batch_id,
                at_ms,
            },
            actor_id: 1,
            client_rid: 1,
            command: 11,
            flags: 0,
        }
        .encode()
        .unwrap()
    };
    assert!(gateway.submit_battle_player(&player(1)).is_err());
    gateway.submit_battle_player(&player(2)).unwrap();
    gateway.stop().unwrap();
}

fn weapon_wire(name: &str) -> Vec<u8> {
    let row = include_str!("../../../../../tests/fixtures/protocol/weapon-change-wire-v1.txt")
        .lines()
        .find(|line| line.starts_with(&format!("{name} ")))
        .unwrap();
    hex(row.split_whitespace().nth(1).unwrap())
}

fn pump_weapon_result(gateway: &mut Gateway) -> Vec<Output> {
    let mut outputs = Vec::new();
    wait(|| {
        assert_eq!(
            gateway.snapshot().unwrap().connection_errors,
            0,
            "{}",
            gateway.last_error().unwrap()
        );
        if let Some(batch) = gateway.dispatch_next_battle_output().unwrap()
            && batch.iter().any(|output| {
                matches!(
                    output,
                    Output::Action(ActionEvent::WeaponChangeResult { .. })
                )
            })
        {
            outputs = batch;
            return true;
        }
        false
    });
    outputs
}

#[test]
fn weapon_replies_equipment_and_animation_match_python_order_and_server_clock() {
    let mut gateway = gateway();
    let ports = gateway.start(1).unwrap().ports;
    let (mut config, mut binding) = match_config(7);
    config.members[0].equipment.push(EquipmentItem {
        slot: 2,
        weapon_id: 703,
    });
    binding.actor_rids = BTreeMap::from([(1, 101), (2, 202)]);
    gateway.start_battle(config, binding).unwrap();
    gateway.bind_battle_client_clock(7, -1000).unwrap();
    gateway.bind_battle_server_clock(7, 5000).unwrap();
    let mut stream = connect_instance_with_rid(ports[2], 101);
    let choice =
        include_str!("../../../../../tests/fixtures/protocol/instance-control-uplink-v1.txt")
            .lines()
            .find(|line| line.starts_with("camp-choice "))
            .unwrap();
    stream
        .write_all(&hex(choice.split_whitespace().nth(3).unwrap()))
        .unwrap();
    read_body(&mut stream, 35);
    let ready =
        include_str!("../../../../../tests/fixtures/protocol/instance-actor-import-uplink-v1.txt");
    stream
        .write_all(&hex(ready.split_whitespace().nth(3).unwrap()))
        .unwrap();
    let pending = request(&gateway);
    gateway
        .mailbox()
        .lock()
        .unwrap()
        .complete(&response(&pending))
        .unwrap();
    while read_any_message(&mut stream).0 != 23 {}

    for (request, result, equipment, weapon, offhand) in [
        (
            "weapon-fist",
            "fist-result",
            Some("fist-equipment"),
            100000,
            0,
        ),
        ("weapon-invalid", "invalid-result", None, 100000, 0),
        ("weapon-main", "busy-result", None, 100000, 0),
    ] {
        stream.write_all(&uplink(request)).unwrap();
        let batch = pump_weapon_result(&mut gateway);
        assert_eq!(
            read_without_startup(&mut stream),
            (5, 0, weapon_wire(result))
        );
        if let Some(equipment) = equipment {
            assert!(batch.iter().any(|output| matches!(output, Output::State(state)
                if state.actors.iter().any(|actor| actor.actor_id == 1 && actor.weapon_id == weapon && actor.offhand_id == offhand))));
            let mut expected = weapon_wire(equipment);
            let at_ms = batch
                .iter()
                .find_map(|output| match output {
                    Output::State(state) => Some(state.at_ms),
                    _ => None,
                })
                .unwrap();
            let len = expected.len();
            expected[len - 8..].copy_from_slice(&(at_ms + 5000).to_be_bytes());
            assert_eq!(read_without_startup(&mut stream), (5, 0, expected));
            let animation = read_without_startup(&mut stream);
            assert_eq!(
                (animation.0, &animation.2[2..4], &animation.2[6..8]),
                (4, 101_u16.to_be_bytes().as_slice(), [0, 1].as_slice())
            );
            assert_eq!(
                u32::from_be_bytes(animation.2[8..12].try_into().unwrap()) as u64,
                at_ms - 1000
            );
        } else {
            assert!(
                !batch
                    .iter()
                    .any(|output| matches!(output, Output::State(state) if state.mode == 1))
            );
        }
    }
    wait(|| {
        gateway.dispatch_next_battle_output().unwrap().is_some_and(|batch| batch.iter().any(|output|
        matches!(output, Output::Action(ActionEvent::Animation(event)) if event.actor_id == 1 && event.animation == t7r_battle_core::action::Animation::Idle)))
    });
    let idle = read_without_startup(&mut stream);
    assert_eq!((idle.0, &idle.2[6..8]), (4, [0, 2].as_slice()));
    stream.write_all(&uplink("weapon-fist-again")).unwrap();
    let batch = pump_weapon_result(&mut gateway);
    assert_eq!(
        read_without_startup(&mut stream),
        (5, 0, weapon_wire("main-result"))
    );
    let (_, _, equipment) = read_without_startup(&mut stream);
    let mut normalized = equipment.clone();
    let len = normalized.len();
    normalized[len - 8..].copy_from_slice(&6000_u64.to_be_bytes());
    assert_eq!(normalized, weapon_wire("main-equipment"));
    assert!(batch.iter().any(|output| matches!(
        output,
        Output::Action(ActionEvent::EquipmentChanged {
            actor_id: 1,
            weapon_id: 701,
            offhand_id: 703
        })
    )));
    assert_eq!(gateway.snapshot().unwrap().connection_errors, 0);
    gateway.stop().unwrap();
}

#[test]
fn gameplay_requests_before_match_activation_are_noops_not_disconnects() {
    let mut gateway = gateway();
    let ports = gateway.start(1).unwrap().ports;
    let (config, binding) = match_config(7);
    gateway.start_battle_staged(config, binding).unwrap();
    let mut stream = connect_instance(ports[2]);
    let attack = include_str!("../../../../../tests/fixtures/protocol/battle-instance-wire-v1.txt")
        .lines()
        .find(|line| line.starts_with("attack-up "))
        .unwrap();
    stream
        .write_all(&hex(attack.split_whitespace().nth(1).unwrap()))
        .unwrap();
    stream.write_all(&uplink("weapon-fist")).unwrap();
    stream
        .write_all(&uplink("heartbeat-after-gameplay"))
        .unwrap();
    assert_eq!(
        read_without_startup(&mut stream),
        (29, 0, corpus("heartbeat").2)
    );
    assert!(gateway.read_battle_outputs().unwrap().is_none());
    assert_eq!(gateway.snapshot().unwrap().connection_errors, 0);
    gateway.stop().unwrap();
}
