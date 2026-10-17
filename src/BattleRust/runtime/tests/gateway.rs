#![cfg(windows)]

#[path = "gateway/clock.rs"]
mod clock_tests;
#[path = "gateway/command_adaptation.rs"]
mod command_adaptation;
#[path = "gateway/leave.rs"]
mod leave_tests;
#[path = "gateway/native_roster.rs"]
mod native_roster;

use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    io::{Read, Write},
    net::TcpStream,
    sync::Arc,
    thread,
    time::{Duration, Instant},
};
use t7r_battle_core::{
    action::{ActionEvent, Phase},
    battle_semantics::{PlayerIntent, SampledActor, SourceStamp, WorldSample},
    damage::Region,
};
use t7r_battle_resources::ResourceBundle;
use t7r_battle_runtime::gateway::Gateway;
use t7r_battle_runtime::{
    battle_session::{Config, EquipmentItem, Member, MemberKind, Output, contacts::ContactEvent},
    client_battle_wire::{
        ClientBattleMessage, encode_animation_for_client, encode_settlement_finish_for_client,
        encode_settlement_start_for_client,
    },
    semantic_adapter::{BoundContact, ContactSample, MotionSample, SourceBinding},
};

fn gateway() -> Gateway {
    Gateway::new(Arc::new(
        ResourceBundle::from_slice(include_bytes!(
            "../../../../tests/fixtures/resources/bundle-v1.json"
        ))
        .unwrap(),
    ))
}

#[test]
fn resource_preparation_precedes_listeners_and_is_scoped_to_session() {
    let mut invalid: Value = serde_json::from_slice(include_bytes!(
        "../../../../tests/fixtures/resources/bundle-v1.json"
    ))
    .unwrap();
    invalid["rules"]["guard"]["resetValue"] = json!(6);
    let resources = ResourceBundle::from_slice(&serde_json::to_vec(&invalid).unwrap()).unwrap();
    let mut rejected = Gateway::new(Arc::new(resources));
    assert!(rejected.start(1).is_err());
    let snapshot = rejected.snapshot().unwrap();
    assert!(!snapshot.running);
    assert_eq!(snapshot.ports, [0; 3]);
    assert_eq!(snapshot.epoch, 0);
    assert!(!rejected.last_error().unwrap().is_empty());
    assert!(rejected.prepared_resources().is_none());
    rejected.stop().unwrap();

    let mut gateway = gateway();
    assert!(gateway.prepared_resources().is_none());
    gateway.start(1).unwrap();
    let resources = gateway.prepared_resources().unwrap().clone();
    assert_eq!(resources.camps()[0].candidates().groups().len(), 14);
    gateway.stop().unwrap();
    assert!(gateway.prepared_resources().is_none());
    gateway.start(2).unwrap();
    assert!(!Arc::ptr_eq(
        &resources,
        gateway.prepared_resources().unwrap()
    ));
    gateway.stop().unwrap();
}
fn hex(value: &str) -> Vec<u8> {
    value
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
        .collect()
}
fn corpus(name: &str) -> (u16, Vec<u8>, Vec<u8>) {
    let row = include_str!("../../../../tests/fixtures/protocol/gateway-v1.txt")
        .lines()
        .find(|line| line.starts_with(&format!("{name} ")))
        .unwrap()
        .split_whitespace()
        .collect::<Vec<_>>();
    (row[1].parse().unwrap(), hex(row[2]), hex(row[3]))
}
fn room_body(name: &str) -> Vec<u8> {
    let row = include_str!("../../../../tests/fixtures/protocol/room-entry-wire-v1.txt")
        .lines()
        .find(|line| line.starts_with(&format!("{name} ")))
        .unwrap()
        .split_whitespace()
        .collect::<Vec<_>>();
    hex(row[1])
}
fn room_uplink(name: &str) -> Vec<u8> {
    let row = include_str!("../../../../tests/fixtures/protocol/room-entry-uplink-v1.txt")
        .lines()
        .find(|line| line.starts_with(&format!("{name} ")))
        .unwrap()
        .split_whitespace()
        .collect::<Vec<_>>();
    hex(row[3])
}
fn connect(port: u16) -> TcpStream {
    let mut stream = TcpStream::connect(("127.0.0.1", port)).unwrap();
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    stream
        .set_write_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    let mut auth = [0; 29];
    auth[..3].copy_from_slice(&[0x55, 0x0e, 3]);
    auth[23] = 3;
    stream.write_all(&auth[..7]).unwrap();
    stream.write_all(&auth[7..]).unwrap();
    let mut reply = [0; 48];
    stream.read_exact(&mut reply).unwrap();
    assert_eq!(reply, t7r_battle_core::protocol::CHANGE_KEY_FRAME);
    stream
}
fn read_wire_message(stream: &mut TcpStream) -> (u16, u64, Vec<u8>) {
    let mut head = [0; 12];
    stream.read_exact(&mut head).unwrap();
    assert_eq!(&head[..8], &[0x55, 0x0e, 5, 0, 0, 0, 0, 12]);
    let length = u32::from_be_bytes(head[8..].try_into().unwrap()) as usize;
    assert!(length < 4096);
    let mut body = vec![0; length];
    stream.read_exact(&mut body).unwrap();
    assert_eq!(body[0], 0);
    assert_eq!(&body[3..11], &[0; 8], "original outer server_time");
    (
        u16::from_be_bytes(body[1..3].try_into().unwrap()),
        u64::from_be_bytes(body[3..11].try_into().unwrap()),
        body[11..].to_vec(),
    )
}
fn read_any_message(stream: &mut TcpStream) -> (u16, u64, Vec<u8>) {
    loop {
        let message = read_wire_message(stream);
        if message.0 == 8 && message.2.starts_with(&[0, 11]) {
            assert_eq!(&message.2[10..], &[0, 0, 0, 0, 0, 0, 0, 0, 1]);
            continue;
        }
        return message;
    }
}
fn read_message(stream: &mut TcpStream, command: u16) -> (u64, Vec<u8>) {
    let (actual, at, body) = read_any_message(stream);
    assert_eq!(actual, command);
    (at, body)
}
fn read_body(stream: &mut TcpStream, command: u16) -> Vec<u8> {
    read_message(stream, command).1
}
fn read_without_startup(stream: &mut TcpStream) -> (u16, u64, Vec<u8>) {
    loop {
        let (command, at, body) = read_any_message(stream);
        let selector = u16::from_be_bytes(body[..2].try_into().unwrap());
        if command == 10
            && (matches!(selector, 0x65 | 0x66 | 0x194 | 0xc8)
                || selector == 0x67 && body[18..22] == 2_i32.to_be_bytes())
        {
            continue;
        }
        return (command, at, body);
    }
}
fn connect_instance(port: u16) -> TcpStream {
    connect_instance_with_rid(port, 1)
}
fn connect_instance_with_rid(port: u16, client_rid: u16) -> TcpStream {
    connect_instance_on_map(port, client_rid, 1028)
}
fn connect_instance_on_map(port: u16, client_rid: u16, map_id: i32) -> TcpStream {
    let mut stream = connect(port);
    let startup = include_str!("../../../../tests/fixtures/protocol/instance-start-wire-v1.txt")
        .lines()
        .next()
        .unwrap()
        .split_whitespace()
        .collect::<Vec<_>>();
    let mut expected_login = hex(startup[2]);
    expected_login[6..14].copy_from_slice(&u64::from(client_rid).to_be_bytes());
    assert_eq!(read_body(&mut stream, 44), expected_login);
    let update = read_body(&mut stream, 10);
    assert_eq!(&update[..2], &[0, 0x64]);
    assert_eq!(&update[2..10], &update[18..26]);
    assert!(u64::from_be_bytes(update[2..10].try_into().unwrap()) > 0);
    assert_eq!(
        i32::from_be_bytes(update[26..30].try_into().unwrap()),
        map_id
    );
    stream
}
fn wait(mut ready: impl FnMut() -> bool) {
    let began = Instant::now();
    while !ready() {
        assert!(began.elapsed() < Duration::from_secs(5));
        thread::sleep(Duration::from_millis(5));
    }
}
fn request(gateway: &Gateway) -> Value {
    wait(|| gateway.snapshot().unwrap().pending_requests > 0);
    let mut mailbox = gateway.mailbox().lock().unwrap();
    assert!(mailbox.read(0).is_err());
    serde_json::from_slice(&mailbox.read(4096).unwrap().unwrap()).unwrap()
}
fn response(request: &Value) -> Vec<u8> {
    serde_json::to_vec(
        &json!({"version": 1, "epoch": request["epoch"], "requestId": request["requestId"],
        "success": true, "identity": {"displayName": "测试玩家", "portrait": 7, "level": 100}}),
    )
    .unwrap()
}

fn match_config(epoch: u64) -> (Config, SourceBinding) {
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
    (
        Config {
            epoch,
            started_at_ms: 1000,
            duration_ms: 120_000,
            kill_target: 10,
            seed: 42,
            members,
        },
        SourceBinding {
            epoch,
            source_id: 1,
            actor_rids: BTreeMap::from([(1, 1), (2, 2)]),
        },
    )
}

fn twenty_member_config(epoch: u64, duration_ms: u64) -> (Config, SourceBinding) {
    let (mut config, mut binding) = match_config(epoch);
    config.duration_ms = duration_ms;
    config.members = (1..=20)
        .map(|actor_id| Member {
            actor_id,
            kind: if actor_id == 1 {
                MemberKind::Human
            } else {
                MemberKind::Bot
            },
            sample_source_id: 1,
            camp: if (2..=11).contains(&actor_id) { 2 } else { 1 },
            hero_id: 1101,
            weapon_id: 701,
            equipment: vec![EquipmentItem {
                slot: 1,
                weapon_id: 701,
            }],
        })
        .collect();
    binding.actor_rids = (1..=20)
        .map(|actor_id| (actor_id, (actor_id + 100) as u16))
        .collect();
    (config, binding)
}

#[test]
fn battle_start_rejects_zero_and_duplicate_client_rids_without_consuming_epoch() {
    let mut gateway = gateway();
    gateway.start(1).unwrap();
    for actor_rids in [
        BTreeMap::from([(1, 0), (2, 2)]),
        BTreeMap::from([(1, 7), (2, 7)]),
    ] {
        let (config, mut binding) = match_config(7);
        binding.actor_rids = actor_rids;
        assert!(gateway.start_battle(config, binding).is_err());
    }
    let (config, binding) = match_config(7);
    gateway.start_battle(config, binding).unwrap();
    gateway.stop().unwrap();
}

#[test]
fn staged_battle_starts_its_clock_at_activation_and_rejects_old_epoch() {
    let mut gateway = gateway();
    gateway.start(1).unwrap();
    let (mut config, binding) = match_config(7);
    config.duration_ms = 200;
    gateway.start_battle_staged(config, binding).unwrap();
    assert!(gateway.battle_preparation_open().unwrap());
    assert_eq!(
        gateway.battle_clock_ms().unwrap_err().kind(),
        std::io::ErrorKind::WouldBlock
    );
    assert_eq!(
        gateway
            .submit_battle_world(&match_world(7))
            .unwrap_err()
            .kind(),
        std::io::ErrorKind::WouldBlock
    );
    thread::sleep(Duration::from_millis(250));
    assert!(gateway.read_battle_outputs().unwrap().is_none());
    assert!(gateway.activate_battle(6).is_err());
    gateway.activate_battle(7).unwrap();
    assert!(!gateway.battle_preparation_open().unwrap());
    assert!(!gateway.reserve_battle_rearm().unwrap());
    assert!(gateway.battle_clock_ms().unwrap() < 1200);
    assert!(gateway.activate_battle(7).is_err());
    gateway.submit_battle_world(&match_world(7)).unwrap();
    let mut ended = false;
    wait(|| {
        if let Some(batch) = gateway.read_battle_outputs().unwrap() {
            ended |= batch.iter().any(|event| matches!(event, Output::Ended(_)));
        }
        ended
    });
    gateway.stop_battle().unwrap();
    let (config, binding) = match_config(8);
    gateway.start_battle_staged(config, binding).unwrap();
    assert!(gateway.activate_battle(7).is_err());
    gateway.stop().unwrap();
}

#[test]
fn staged_output_waits_for_both_native_clock_bindings() {
    let mut gateway = gateway();
    gateway.start(1).unwrap();
    let (config, binding) = match_config(7);
    gateway.start_battle_staged(config, binding).unwrap();
    gateway.activate_battle(7).unwrap();
    gateway.submit_battle_world(&match_world(7)).unwrap();
    assert!(gateway.dispatch_next_battle_output().unwrap().is_none());
    gateway.bind_battle_client_clock(7, -1000).unwrap();
    assert!(gateway.dispatch_next_battle_output().unwrap().is_none());
    gateway.bind_battle_server_clock(7, 5000).unwrap();
    let mut dispatched = false;
    wait(|| {
        dispatched |= gateway.dispatch_next_battle_output().unwrap().is_some();
        dispatched
    });
    gateway.stop().unwrap();
}

fn match_world(epoch: u64) -> Vec<u8> {
    WorldSample {
        stamp: SourceStamp {
            epoch,
            source_id: 1,
            batch_id: 1,
            at_ms: 1000,
        },
        actors: (1..=2)
            .map(|actor_id| SampledActor {
                actor_id,
                client_rid: actor_id as u16,
                sequence: 1,
                entity: actor_id as u64 + 100,
                position: [actor_id as f64, 20., 0.],
                heading: if actor_id == 1 { 0. } else { 180. },
                contact_target_id: 0,
                contact_state: 0,
            })
            .collect(),
    }
    .encode()
    .unwrap()
}

#[test]
fn bound_battle_accepts_native_motion_and_contact_watermark_without_replaying_batches() {
    let mut gateway = gateway();
    gateway.start(1).unwrap();
    let (config, binding) = match_config(7);
    gateway.start_battle(config, binding).unwrap();
    gateway.submit_battle_world(&match_world(7)).unwrap();
    let stamp = SourceStamp {
        epoch: 7,
        source_id: 1,
        batch_id: 1,
        at_ms: 1000,
    };
    let motion = MotionSample {
        stamp,
        world_batch_id: 1,
        actor_id: 2,
        client_rid: 2,
        entity: 102,
        position: [2., 20., 0.],
        heading: 180.,
        attempted_forward_at_ms: 999,
    };
    let mut wrong_rid = motion.clone();
    wrong_rid.client_rid = 1;
    assert!(gateway.submit_battle_motion(wrong_rid).is_err());
    let mut wrong_pose = motion.clone();
    wrong_pose.position[0] = 3.;
    let error = gateway.submit_battle_motion(wrong_pose).unwrap_err();
    assert_eq!(gateway.last_error().unwrap(), error.to_string());
    assert_eq!(error.to_string(), "AI motion differs from sampled world");
    let mut wrong_world = motion.clone();
    wrong_world.world_batch_id = 2;
    assert!(gateway.submit_battle_motion(wrong_world).is_err());
    gateway.submit_battle_motion(motion.clone()).unwrap();
    assert!(gateway.submit_battle_motion(motion).is_err());

    let contact = ContactEvent {
        actor_id: 1,
        target_id: 0,
        source_entity: 101,
        target_entity: 0,
        sequence: 1,
        phase_at_ms: 1000,
        target_intent: None,
        target_phase: Phase::Idle,
        target_phase_at_ms: 1000.,
        target_observed_at_ms: None,
        source_observed_at_ms: None,
        occurred_at_ms: 1000.,
        along: 0.5,
        defence_weapon_id: 0,
        region: Region::Torso,
        body_index: -1,
        source_position: [1., 20., 0.],
        target_position: [1., 20., 0.],
        source_heading: 0.,
        target_heading: 0.,
        source_velocity: [0.; 3],
        target_velocity: [0.; 3],
        weapon_velocity: [0.; 3],
        velocity_ready: false,
        airborne: false,
        target_airborne: false,
        position: [1., 20., 0.],
        normal: [0., 0., 1.],
        direction: [1., 0., 0.],
        material: 0,
    };
    let contacts = ContactSample {
        stamp,
        world_batch_id: 1,
        dropped: 0,
        events: vec![BoundContact {
            client_rid: 1,
            target_client_rid: 0,
            event: contact,
        }],
    };
    let mut wrong_target = contacts.clone();
    wrong_target.events[0].target_client_rid = 2;
    assert!(gateway.submit_battle_contacts(wrong_target).is_err());
    let mut dropped = contacts.clone();
    dropped.dropped = 1;
    assert!(gateway.submit_battle_contacts(dropped).is_err());
    let mut invalid_geometry = contacts.clone();
    invalid_geometry.events[0].event.normal = [0.; 3];
    assert!(gateway.submit_battle_contacts(invalid_geometry).is_err());
    let mut stale_epoch = contacts.clone();
    stale_epoch.stamp.epoch = 6;
    assert!(gateway.submit_battle_contacts(stale_epoch).is_err());
    gateway.submit_battle_contacts(contacts.clone()).unwrap();
    assert!(gateway.submit_battle_contacts(contacts).is_err());
    gateway.stop().unwrap();
}

#[test]
fn closed_battle_output_preserves_the_worker_failure_in_gateway_diagnostics() {
    let mut gateway = gateway();
    gateway.start(1).unwrap();
    let (config, binding) = match_config(7);
    gateway.start_battle(config, binding).unwrap();
    // Fill the queue, then leave it undrained for the complete retry budget.
    let started = Instant::now();
    let deadline = started + Duration::from_secs(12);
    while gateway.battle_clock_ms().is_ok() {
        assert!(
            Instant::now() < deadline,
            "worker did not stop after its output retry deadline"
        );
        thread::sleep(Duration::from_millis(20));
    }
    assert!(started.elapsed() >= Duration::from_secs(5));
    loop {
        match gateway.read_battle_outputs() {
            Ok(Some(_)) => continue,
            Ok(None) => panic!("stopped worker has neither output nor an error"),
            Err(error) => {
                assert_eq!(error.kind(), std::io::ErrorKind::TimedOut);
                assert_eq!(
                    error.to_string(),
                    "battle timer output backpressure exceeded 5 seconds"
                );
                assert_eq!(gateway.last_error().unwrap(), error.to_string());
                break;
            }
        }
    }
    gateway.stop().unwrap();
}

#[test]
fn bound_battle_rejects_stale_batches_and_restarts_without_orphaned_owner() {
    let mut gateway = gateway();
    assert!(
        gateway
            .start_battle(match_config(7).0, match_config(7).1)
            .is_err()
    );
    gateway.start(1).unwrap();
    let (config, binding) = match_config(7);
    gateway.start_battle(config, binding).unwrap();
    assert!(
        gateway
            .start_battle(match_config(8).0, match_config(8).1)
            .is_err()
    );
    let sample = match_world(7);
    gateway.submit_battle_world(&sample).unwrap();
    assert!(gateway.submit_battle_world(&sample).is_err());
    let intent = PlayerIntent {
        stamp: SourceStamp {
            epoch: 7,
            source_id: 1,
            batch_id: 1,
            at_ms: 1000,
        },
        actor_id: 1,
        client_rid: 1,
        command: 1,
        flags: 0,
    }
    .encode()
    .unwrap();
    gateway.submit_battle_player(&intent).unwrap();
    assert!(gateway.submit_battle_player(&intent).is_err());
    let deadline = Instant::now() + Duration::from_secs(2);
    let mut action_seen = false;
    while !action_seen && Instant::now() < deadline {
        if let Some(batch) = gateway.read_battle_outputs().unwrap() {
            action_seen = batch
                .iter()
                .any(|output| matches!(output, Output::Action(_)));
        } else {
            thread::sleep(Duration::from_millis(5));
        }
    }
    assert!(action_seen);
    gateway.stop_battle().unwrap();
    assert!(gateway.submit_battle_world(&sample).is_err());
    assert!(
        gateway
            .start_battle(match_config(7).0, match_config(7).1)
            .is_err()
    );
    let (config, binding) = match_config(8);
    gateway.start_battle(config, binding).unwrap();
    assert!(gateway.submit_battle_world(&sample).is_err());
    gateway.submit_battle_world(&match_world(8)).unwrap();
    gateway.stop().unwrap();
    assert!(!gateway.snapshot().unwrap().running);
    gateway.start(2).unwrap();
    let (config, binding) = match_config(9);
    gateway.start_battle(config, binding).unwrap();
    gateway.stop().unwrap();
}

#[test]
fn instance_connection_binds_original_action_to_the_running_battle_and_closes_on_stop() {
    let mut gateway = gateway();
    let state = gateway.start(1).unwrap();
    let (config, binding) = match_config(7);
    gateway.start_battle(config, binding).unwrap();
    gateway.submit_battle_world(&match_world(7)).unwrap();
    let mut instance = connect_instance(state.ports[2]);
    let row = include_str!("../../../../tests/fixtures/protocol/battle-instance-wire-v1.txt")
        .lines()
        .find(|row| row.starts_with("attack-up "))
        .unwrap()
        .split_whitespace()
        .collect::<Vec<_>>();
    let frame = hex(row[1]);
    instance.write_all(&frame).unwrap();
    let mut action_seen = false;
    wait(|| {
        if let Some(batch) = gateway.read_battle_outputs().unwrap() {
            action_seen |= batch.iter().any(|event| {
                matches!(
                    event,
                    Output::Action(ActionEvent::Animation(animation)) if animation.actor_id == 1
                )
            });
        }
        action_seen
    });
    let at_ms = gateway.battle_clock_ms().unwrap();
    let player = |batch_id, command| {
        PlayerIntent {
            stamp: SourceStamp {
                epoch: 7,
                source_id: 1,
                batch_id,
                at_ms,
            },
            actor_id: 1,
            client_rid: 1,
            command,
            flags: 0,
        }
        .encode()
        .unwrap()
    };
    assert!(gateway.submit_battle_player(&player(1, 11)).is_err());
    gateway.submit_battle_player(&player(2, 11)).unwrap();
    let mut duplicate = connect(state.ports[2]);
    assert_eq!(duplicate.read(&mut [0; 1]).unwrap(), 0);
    gateway.stop_battle().unwrap();
    assert_eq!(instance.read(&mut [0; 1]).unwrap(), 0);
    let (config, binding) = match_config(8);
    gateway.start_battle(config, binding).unwrap();
    gateway.submit_battle_world(&match_world(8)).unwrap();
    let mut rematch = connect_instance(state.ports[2]);
    rematch.write_all(&frame).unwrap();
    wait(|| {
        if let Some(batch) = gateway.read_battle_outputs().unwrap() {
            return batch.iter().any(|event| {
                matches!(
                    event,
                    Output::Action(ActionEvent::Animation(animation)) if animation.actor_id == 1
                )
            });
        }
        false
    });
    rematch.write_all(&frame).unwrap();
    assert_eq!(rematch.read(&mut [0; 1]).unwrap(), 0);
    gateway.stop().unwrap();
}

#[test]
fn native_movement_reports_do_not_close_the_instance_or_replace_world_sampling() {
    let mut gateway = gateway();
    let ports = gateway.start(1).unwrap().ports;
    let (config, binding) = match_config(7);
    gateway.start_battle(config, binding).unwrap();
    let mut instance = connect_instance(ports[2]);
    let rows = include_str!("../../../../tests/fixtures/protocol/native-movement-uplink-v1.txt")
        .lines()
        .map(|line| line.split_whitespace().collect::<Vec<_>>())
        .collect::<Vec<_>>();
    for row in &rows {
        instance.write_all(&hex(row[4])).unwrap();
    }
    let (command, _, body) = read_without_startup(&mut instance);
    assert_eq!(command, 10);
    assert_eq!(&body[..2], &[0, 0x64]);
    assert_eq!(gateway.snapshot().unwrap().connection_errors, 0);
    gateway.stop().unwrap();
}

#[test]
fn instance_battle_hero_container_uses_the_bound_platform_profile() {
    let mut gateway = gateway();
    let ports = gateway.start(1).unwrap().ports;
    let (config, binding) = match_config(7);
    gateway.start_battle(config, binding).unwrap();
    let mut instance = connect_instance(ports[2]);
    let row = include_str!("../../../../tests/fixtures/protocol/instance-container-v1.txt")
        .split_whitespace()
        .collect::<Vec<_>>();
    instance.write_all(&hex(row[4])).unwrap();
    let pending = request(&gateway);
    assert_eq!(pending["operation"], "readProfile");
    let response = serde_json::to_vec(&json!({"version":1,"epoch":pending["epoch"],
        "requestId":pending["requestId"],"success":true,
        "profile":{"schemaVersion":1,"revision":0,"formation":[1101],"weapons":{
            "1101":[{"slot":1,"group":0,"index":0},{"slot":2,"group":2,"index":0}],
            "2102":[{"slot":1,"group":0,"index":0},{"slot":2,"group":2,"index":0}]}}}))
    .unwrap();
    gateway
        .mailbox()
        .lock()
        .unwrap()
        .complete(&response)
        .unwrap();
    let (command, _, body) = read_without_startup(&mut instance);
    assert_eq!(command, 0x29);
    let frozen = include_str!("../../../../tests/fixtures/protocol/loadout-v1.txt")
        .lines()
        .find(|line| line.starts_with("container-2 "))
        .unwrap()
        .split_whitespace()
        .nth(4)
        .unwrap();
    let mut expected = hex(frozen.split_once(':').unwrap().1);
    expected[..2].copy_from_slice(&0x66_u16.to_be_bytes());
    expected[6..10].copy_from_slice(&7_i32.to_be_bytes());
    assert_eq!(body, expected);
    gateway.stop().unwrap();
}

#[test]
fn observed_original_no_reply_reports_leave_the_instance_connected() {
    let mut gateway = gateway();
    let ports = gateway.start(1).unwrap().ports;
    let (config, binding) = match_config(7);
    gateway.start_battle(config, binding).unwrap();
    let mut instance = connect_instance(ports[2]);
    for line in
        include_str!("../../../../tests/fixtures/protocol/native-no-reply-uplink-v1.txt").lines()
    {
        let row = line.split_whitespace().collect::<Vec<_>>();
        instance.write_all(&hex(row[4])).unwrap();
    }
    let (command, _, body) = read_without_startup(&mut instance);
    assert_eq!(command, 10);
    assert_eq!(&body[..2], &[0, 0x64]);
    assert_eq!(gateway.snapshot().unwrap().connection_errors, 0);
    gateway.stop().unwrap();
}

#[test]
fn instance_startup_streams_five_original_scene_phases_before_battle_entry() {
    let mut gateway = gateway();
    let state = gateway.start(1).unwrap();
    let (config, binding) = match_config(7);
    gateway.start_battle(config, binding).unwrap();
    let mut instance = connect_instance(state.ports[2]);
    let mut previous = 0;
    for selector in [0x65_u16, 0x66, 0x67, 0x194, 0xc8] {
        let body = read_body(&mut instance, 10);
        assert_eq!(u16::from_be_bytes(body[..2].try_into().unwrap()), selector);
        if selector != 0x194 {
            let at_ms = u64::from_be_bytes(body[2..10].try_into().unwrap());
            assert!(at_ms > 0 && at_ms >= previous);
            previous = at_ms;
        }
    }
    gateway.stop_battle().unwrap();
    assert_eq!(instance.read(&mut [0; 1]).unwrap(), 0);
    gateway.stop().unwrap();
}

#[test]
fn instance_camp_response_matches_the_complete_original_runtime_frame() {
    let mut gateway = gateway();
    let state = gateway.start(1).unwrap();
    let (config, binding) = match_config(7);
    gateway.start_battle_staged(config, binding).unwrap();
    let mut instance = connect(state.ports[2]);
    read_body(&mut instance, 44);
    read_body(&mut instance, 10);
    let choice = include_str!("../../../../tests/fixtures/protocol/instance-control-uplink-v1.txt")
        .lines()
        .find(|line| line.starts_with("camp-choice "))
        .unwrap()
        .split_whitespace()
        .collect::<Vec<_>>();
    instance.write_all(&hex(choice[3])).unwrap();
    let expected = include_str!("../../../../tests/fixtures/protocol/gateway-downlink-v1.txt")
        .lines()
        .find(|line| line.starts_with("camp-choice "))
        .unwrap()
        .split_whitespace()
        .nth(2)
        .map(hex)
        .unwrap();
    let mut actual = vec![0; expected.len()];
    instance.read_exact(&mut actual).unwrap();
    assert_eq!(actual, expected);
    gateway.stop().unwrap();
}

#[test]
fn instance_refresh_and_hero_transition_round_trip_over_original_encrypted_channel() {
    let mut gateway = gateway();
    let state = gateway.start(1).unwrap();
    let (config, binding) = match_config(7);
    gateway.start_battle(config, binding).unwrap();
    let mut instance = connect_instance(state.ports[2]);
    for line in
        include_str!("../../../../tests/fixtures/protocol/instance-control-uplink-v1.txt").lines()
    {
        let row = line.split_whitespace().collect::<Vec<_>>();
        instance.write_all(&hex(row[3])).unwrap();
        let command = match row[0] {
            "camp-choice" => 35,
            "vision-list" => 14,
            _ => 10,
        };
        let body = read_body(&mut instance, command);
        if row[0] == "instance-refresh" {
            assert_eq!(&body[..2], &[0, 0x64]);
            let at_ms = u64::from_be_bytes(body[2..10].try_into().unwrap());
            let started_at_ms = u64::from_be_bytes(body[18..26].try_into().unwrap());
            assert!(started_at_ms > 0 && started_at_ms <= at_ms);
            let score = read_body(&mut instance, 23);
            assert_eq!(
                score,
                t7r_battle_runtime::client_battle_wire::encode_camp_kill_update(
                    at_ms,
                    1,
                    [0, 0],
                    Some(10)
                )
                .unwrap()
                .body
            );
        } else if row[0] == "camp-choice" {
            assert_eq!(body, hex("000200000000000000010000000000000000"));
        } else if row[0] == "vision-list" {
            assert_eq!(
                body,
                hex("0006fffffff90001000000020000000200000000000000010000000000000002")
            );
        } else {
            assert_eq!(body, hex("000bfe"));
        }
    }
    gateway.stop().unwrap();
}

#[test]
fn instance_camp_confirmation_imports_bound_actor_from_platform_identity() {
    let mut gateway = gateway();
    let state = gateway.start(1).unwrap();
    let (config, mut binding) = match_config(7);
    binding.actor_rids = BTreeMap::from([(1, 101), (2, 202)]);
    gateway.start_battle_staged(config, binding).unwrap();
    let mut instance = connect_instance_with_rid(state.ports[2], 101);
    let choice = include_str!("../../../../tests/fixtures/protocol/instance-control-uplink-v1.txt")
        .lines()
        .find(|line| line.starts_with("camp-choice "))
        .unwrap()
        .split_whitespace()
        .collect::<Vec<_>>();
    instance.write_all(&hex(choice[3])).unwrap();
    assert_eq!(
        read_body(&mut instance, 35),
        hex("000200000000000000010000000000000000")
    );
    let ready =
        include_str!("../../../../tests/fixtures/protocol/instance-actor-import-uplink-v1.txt")
            .split_whitespace()
            .collect::<Vec<_>>();
    instance.write_all(&hex(ready[3])).unwrap();
    let pending = request(&gateway);
    assert_eq!(pending["operation"], "readIdentity");
    let identity = serde_json::to_vec(&json!({"version":1,"epoch":pending["epoch"],
        "requestId":pending["requestId"],"success":true,
        "identity":{"displayName":"Player","portrait":7,"level":100}}))
    .unwrap();
    assert!(
        gateway
            .mailbox()
            .lock()
            .unwrap()
            .complete(&identity)
            .unwrap()
    );
    let local_rows =
        include_str!("../../../../tests/fixtures/protocol/instance-actor-import-wire-v1.txt")
            .lines()
            .collect::<Vec<_>>();
    let bot_rows =
        include_str!("../../../../tests/fixtures/protocol/instance-bot-import-wire-v1.txt")
            .lines()
            .collect::<Vec<_>>();
    for row in local_rows[..4]
        .iter()
        .chain(bot_rows.iter())
        .chain(local_rows[4..].iter())
    {
        let row = row.split_whitespace().collect::<Vec<_>>();
        let mut body = read_body(&mut instance, row[1].parse().unwrap());
        if row[0] == "actor-vision" {
            assert_eq!(&body[..2], &[0, 1]);
            assert_eq!(u32::from_be_bytes(body[10..14].try_into().unwrap()), 10_000);
            assert_eq!(u64::from_be_bytes(body[14..22].try_into().unwrap()), 101);
            assert_eq!(u16::from_be_bytes(body[22..24].try_into().unwrap()), 101);
            assert_eq!(&body[body.len() - 8..], &[0; 8]);
            continue;
        }
        if row[0] != "camp-ready" {
            assert!(u64::from_be_bytes(body[2..10].try_into().unwrap()) > 0);
            body[2..10].copy_from_slice(&6000_u64.to_be_bytes());
        }
        assert_eq!(body, hex(row[2]));
    }

    loop {
        let (command, _, body) = read_any_message(&mut instance);
        assert_eq!(command, 10);
        if body[..2] == [0, 0xc8] {
            break;
        }
    }
    let load_rows =
        include_str!("../../../../tests/fixtures/protocol/instance-load-hero-uplink-v1.txt")
            .lines()
            .map(|line| line.split_whitespace().collect::<Vec<_>>())
            .collect::<Vec<_>>();
    instance.write_all(&hex(load_rows[0][3])).unwrap();
    let prepare = loop {
        let (command, _, body) = read_any_message(&mut instance);
        if command == 10 && body[..2] == [0, 0x67] {
            break body;
        }
        assert_eq!(command, 10);
    };
    assert_eq!(i32::from_be_bytes(prepare[18..22].try_into().unwrap()), 2);
    assert_eq!(
        u64::from_be_bytes(prepare[30..38].try_into().unwrap()),
        30_000
    );
    instance.write_all(&hex(load_rows[1][3])).unwrap();
    let profile_request = request(&gateway);
    assert_eq!(profile_request["operation"], "readProfile");
    let selected = read_body(&mut instance, 54);
    assert_eq!(&selected[..2], &[0, 1]);
    assert_eq!(u64::from_be_bytes(selected[6..14].try_into().unwrap()), 101);
    assert_eq!(i32::from_be_bytes(selected[14..18].try_into().unwrap()), 5);
    let selected_at = u64::from_be_bytes(selected[18..26].try_into().unwrap());
    assert!(selected_at >= u64::from_be_bytes(prepare[2..10].try_into().unwrap()));
    let profile = serde_json::to_vec(&json!({"version":1,"epoch":profile_request["epoch"],
        "requestId":profile_request["requestId"],"success":true,
        "profile":{"schemaVersion":1,"revision":0,"formation":[1101],"weapons":{
            "1101":[{"slot":1,"group":0,"index":0},{"slot":2,"group":2,"index":0}],
            "2102":[{"slot":1,"group":0,"index":0},{"slot":2,"group":2,"index":0}]}}}))
    .unwrap();
    assert!(
        gateway
            .mailbox()
            .lock()
            .unwrap()
            .complete(&profile)
            .unwrap()
    );
    assert_eq!(read_body(&mut instance, 54), hex("003300000000"));
    assert_eq!(
        read_body(&mut instance, 54),
        hex("003400000000000000650000044d00000000")
    );
    let play =
        include_str!("../../../../tests/fixtures/protocol/instance-battle-entry-uplink-v1.txt")
            .split_whitespace()
            .collect::<Vec<_>>();
    instance.write_all(&hex(play[3])).unwrap();
    assert_eq!(read_body(&mut instance, 54), hex("006500000000"));
    let local_state = read_body(&mut instance, 54);
    assert_eq!(
        u64::from_be_bytes(local_state[6..14].try_into().unwrap()),
        101
    );
    assert_eq!(
        i32::from_be_bytes(local_state[14..18].try_into().unwrap()),
        6
    );
    assert!(u64::from_be_bytes(local_state[18..26].try_into().unwrap()) >= selected_at);
    assert_eq!(
        read_body(&mut instance, 14),
        hex("0002000000010000000100000000000000650000000000000000")
    );
    let local_view = read_body(&mut instance, 14);
    assert_eq!(
        u64::from_be_bytes(local_view[14..22].try_into().unwrap()),
        101
    );
    assert_eq!(
        read_body(&mut instance, 5),
        t7r_battle_runtime::client_battle_wire::encode_shield_health_for_client(703, 2, 100, 100)
            .unwrap()
            .body
    );
    assert_eq!(read_body(&mut instance, 4), hex("003040a00000"));
    let local_ready = read_body(&mut instance, 54);
    assert_eq!(
        i32::from_be_bytes(local_ready[14..18].try_into().unwrap()),
        8
    );
    let inactive = read_body(&mut instance, 2);
    assert_eq!(&inactive[..2], &[0, 0x1f]);
    assert_eq!(&inactive[6..], &[0, 1, 0]);
    let inactive_at = u32::from_be_bytes(inactive[2..6].try_into().unwrap());
    let idle = read_body(&mut instance, 4);
    assert_eq!(&idle[..8], &[0, 3, 0, 101, 0, 1, 0, 2]);
    assert_eq!(&idle[8..12], &inactive_at.to_be_bytes());
    let bot_state = read_body(&mut instance, 54);
    assert_eq!(
        u64::from_be_bytes(bot_state[6..14].try_into().unwrap()),
        202
    );
    assert_eq!(i32::from_be_bytes(bot_state[14..18].try_into().unwrap()), 6);
    assert_eq!(
        read_body(&mut instance, 14),
        hex("0002000000010000000100000000000000ca0000000000000000")
    );
    let bot_view = read_body(&mut instance, 14);
    assert_eq!(
        u64::from_be_bytes(bot_view[14..22].try_into().unwrap()),
        202
    );
    let start = read_body(&mut instance, 10);
    assert_eq!(&start[..2], &[0, 0x67]);
    assert_eq!(i32::from_be_bytes(start[18..22].try_into().unwrap()), 3);
    assert_eq!(u64::from_be_bytes(start[30..38].try_into().unwrap()), 5_999);
    assert_eq!(
        gateway.battle_clock_ms().unwrap_err().kind(),
        std::io::ErrorKind::WouldBlock
    );
    instance
        .set_read_timeout(Some(Duration::from_secs(8)))
        .unwrap();
    let began = Instant::now();
    let game = read_body(&mut instance, 10);
    assert!(began.elapsed() >= Duration::from_millis(5_500));
    assert_eq!(&game[..2], &[0, 0x67]);
    assert_eq!(i32::from_be_bytes(game[18..22].try_into().unwrap()), 4);
    assert_eq!(
        u64::from_be_bytes(game[30..38].try_into().unwrap()),
        120_000
    );
    let game_state = read_body(&mut instance, 54);
    assert_eq!(
        i32::from_be_bytes(game_state[14..18].try_into().unwrap()),
        6
    );
    let active = read_body(&mut instance, 2);
    assert_eq!(&active[..2], &[0, 0x1f]);
    assert_eq!(&active[6..], &[0, 1, 1]);
    let active_at = u32::from_be_bytes(active[2..6].try_into().unwrap());
    let start_at = u64::from_be_bytes(start[2..10].try_into().unwrap());
    let game_at = u64::from_be_bytes(game[2..10].try_into().unwrap());
    assert_eq!(u64::from(active_at - inactive_at), game_at - start_at);
    assert!(gateway.battle_clock_ms().unwrap() >= 1000);
    gateway.stop().unwrap();
}

#[test]
fn instance_camp_confirmation_imports_nineteen_bound_bots() {
    let mut gateway = gateway();
    let state = gateway.start(1).unwrap();
    let (config, binding) = twenty_member_config(7, 120_000);
    gateway.start_battle(config, binding).unwrap();
    let mut instance = connect_instance_with_rid(state.ports[2], 101);
    let choice = include_str!("../../../../tests/fixtures/protocol/instance-control-uplink-v1.txt")
        .lines()
        .find(|line| line.starts_with("camp-choice "))
        .unwrap()
        .split_whitespace()
        .collect::<Vec<_>>();
    instance.write_all(&hex(choice[3])).unwrap();
    assert_eq!(&read_body(&mut instance, 35)[..2], &[0, 2]);
    let ready =
        include_str!("../../../../tests/fixtures/protocol/instance-actor-import-uplink-v1.txt")
            .split_whitespace()
            .collect::<Vec<_>>();
    instance.write_all(&hex(ready[3])).unwrap();
    let pending = request(&gateway);
    assert_eq!(pending["operation"], "readIdentity");
    assert!(
        gateway
            .mailbox()
            .lock()
            .unwrap()
            .complete(&response(&pending))
            .unwrap()
    );
    let mut imported = Vec::new();
    while imported.len() < 43 {
        let (command, at, body) = read_any_message(&mut instance);
        assert_eq!(at, 0);
        let selector = u16::from_be_bytes(body[..2].try_into().unwrap());
        if command == 10
            && (matches!(selector, 0x65 | 0x66 | 0x194 | 0xc8)
                || selector == 0x67 && body[18..22] == 2_i32.to_be_bytes())
        {
            continue;
        }
        imported.push((command, body));
    }
    assert_eq!(imported[0].0, 35);
    assert_eq!(&imported[0].1[..2], &[0, 4]);
    assert_eq!(imported[1].0, 14);
    assert_eq!(
        u64::from_be_bytes(imported[1].1[14..22].try_into().unwrap()),
        101
    );
    assert_eq!(imported[2].0, 10);
    assert_eq!(
        u64::from_be_bytes(imported[2].1[18..26].try_into().unwrap()),
        101
    );
    assert_eq!(imported[3].0, 35);
    for actor_id in 2_u32..=20 {
        let offset = 4 + usize::try_from(actor_id - 2).unwrap() * 2;
        assert_eq!(imported[offset].0, 10);
        assert_eq!(
            u64::from_be_bytes(imported[offset].1[18..26].try_into().unwrap()),
            u64::from(actor_id + 100)
        );
        assert_eq!(imported[offset + 1].0, 35);
        assert_eq!(
            u64::from_be_bytes(imported[offset + 1].1[14..22].try_into().unwrap()),
            u64::from(actor_id + 100)
        );
    }
    assert_eq!(imported[42].0, 23);
    assert_eq!(&imported[42].1[..2], &[0, 1]);
    let vision =
        include_str!("../../../../tests/fixtures/protocol/instance-vision-all-uplink-v1.txt")
            .split_whitespace()
            .collect::<Vec<_>>();
    instance.write_all(&hex(vision[3])).unwrap();
    let mut viewed = 0_u32;
    while viewed < 20 {
        let (command, _, body) = read_any_message(&mut instance);
        if command == 10 {
            continue;
        }
        assert_eq!(command, 14);
        assert_eq!(&body[..2], &[0, 1]);
        assert_eq!(
            u64::from_be_bytes(body[14..22].try_into().unwrap()),
            u64::from(viewed + 101)
        );
        assert_eq!(
            u32::from_be_bytes(body[10..14].try_into().unwrap()),
            viewed + 10_000
        );
        viewed += 1;
    }
    gateway.stop().unwrap();
}

#[test]
fn instance_output_batches_are_bounded_ordered_and_scoped_to_one_match() {
    let mut gateway = gateway();
    let state = gateway.start(1).unwrap();
    let (config, binding) = match_config(7);
    gateway.start_battle(config, binding).unwrap();
    assert!(
        gateway
            .send_battle_client_messages(
                6,
                vec![ClientBattleMessage {
                    command: 12,
                    body: vec![0, 1],
                }]
            )
            .is_err()
    );
    assert!(gateway.send_battle_client_messages(7, Vec::new()).is_err());
    gateway
        .send_battle_client_messages(
            7,
            vec![
                ClientBattleMessage {
                    command: 12,
                    body: vec![0, 1, 2],
                },
                ClientBattleMessage {
                    command: 23,
                    body: vec![3, 4, 5],
                },
            ],
        )
        .unwrap();
    let mut instance = connect_instance(state.ports[2]);
    assert_eq!(read_body(&mut instance, 12), [0, 1, 2]);
    assert_eq!(read_body(&mut instance, 23), [3, 4, 5]);
    gateway
        .send_battle_client_messages(
            7,
            vec![ClientBattleMessage {
                command: 4,
                body: vec![9, 8],
            }],
        )
        .unwrap();
    assert_eq!(read_body(&mut instance, 4), [9, 8]);
    gateway.stop_battle().unwrap();
    assert_eq!(instance.read(&mut [0; 1]).unwrap(), 0);
    let (config, binding) = match_config(8);
    gateway.start_battle(config, binding).unwrap();
    assert!(
        gateway
            .send_battle_client_messages(
                7,
                vec![ClientBattleMessage {
                    command: 4,
                    body: vec![7],
                }]
            )
            .is_err()
    );
    gateway
        .send_battle_client_messages(
            8,
            vec![ClientBattleMessage {
                command: 12,
                body: vec![6],
            }],
        )
        .unwrap();
    let mut rematch = connect_instance(state.ports[2]);
    assert_eq!(read_body(&mut rematch, 12), [6]);
    gateway.stop().unwrap();
}

#[test]
fn instance_output_rejects_queue_saturation_without_dropping_an_accepted_batch() {
    let mut gateway = gateway();
    gateway.start(1).unwrap();
    let (config, binding) = match_config(7);
    gateway.start_battle(config, binding).unwrap();
    for index in 0..32 {
        gateway
            .send_battle_client_messages(
                7,
                vec![ClientBattleMessage {
                    command: 12,
                    body: vec![index],
                }],
            )
            .unwrap();
    }
    let error = gateway
        .send_battle_client_messages(
            7,
            vec![ClientBattleMessage {
                command: 12,
                body: vec![99],
            }],
        )
        .unwrap_err();
    assert_eq!(error.kind(), std::io::ErrorKind::WouldBlock);
    let port = gateway.snapshot().unwrap().ports[2];
    let mut instance = connect_instance(port);
    for index in 0..32 {
        assert_eq!(read_body(&mut instance, 12), [index]);
    }
    gateway.stop().unwrap();
}

#[test]
fn client_clock_binding_is_one_time_and_scoped_to_the_match_epoch() {
    let mut gateway = gateway();
    gateway.start(1).unwrap();
    assert!(gateway.bind_battle_client_clock(7, -1000).is_err());
    assert!(gateway.bind_battle_server_clock(7, 5000).is_err());
    let (config, binding) = match_config(7);
    gateway.start_battle(config, binding).unwrap();
    assert!(gateway.bind_battle_client_clock(6, -1000).is_err());
    gateway.bind_battle_client_clock(7, -1000).unwrap();
    gateway.bind_battle_client_clock(7, -1000).unwrap();
    assert!(gateway.bind_battle_client_clock(7, -999).is_err());
    assert!(gateway.bind_battle_server_clock(6, 5000).is_err());
    gateway.bind_battle_server_clock(7, 5000).unwrap();
    gateway.bind_battle_server_clock(7, 5000).unwrap();
    assert!(gateway.bind_battle_server_clock(7, 5001).is_err());
    gateway.stop_battle().unwrap();
    assert!(gateway.bind_battle_client_clock(7, -1000).is_err());
    assert!(gateway.bind_battle_server_clock(7, 5000).is_err());
    let (config, binding) = match_config(8);
    gateway.start_battle(config, binding).unwrap();
    assert!(gateway.bind_battle_client_clock(7, -1000).is_err());
    gateway.bind_battle_client_clock(8, 6000).unwrap();
    assert!(gateway.bind_battle_server_clock(7, 5000).is_err());
    gateway.bind_battle_server_clock(8, 7000).unwrap();
    gateway.stop().unwrap();
}

#[test]
fn battle_action_output_reaches_original_instance_wire_without_losing_the_worker_batch() {
    let mut gateway = gateway();
    let state = gateway.start(1).unwrap();
    let (config, binding) = match_config(7);
    gateway.start_battle(config, binding).unwrap();
    gateway.bind_battle_client_clock(7, -1000).unwrap();
    gateway.submit_battle_world(&match_world(7)).unwrap();
    let mut instance = connect_instance(state.ports[2]);
    let row = include_str!("../../../../tests/fixtures/protocol/battle-instance-wire-v1.txt")
        .lines()
        .find(|row| row.starts_with("attack-up "))
        .unwrap()
        .split_whitespace()
        .collect::<Vec<_>>();
    instance.write_all(&hex(row[1])).unwrap();

    let mut expected = Vec::new();
    wait(|| {
        if let Some(batch) = gateway.dispatch_next_battle_output().unwrap() {
            expected.extend(batch.iter().filter_map(|event| {
                match event {
                    Output::Action(ActionEvent::Animation(animation)) => Some(
                        encode_animation_for_client(
                            animation,
                            1000,
                            &BTreeMap::from([(1, 1), (2, 2)]),
                        )
                        .unwrap(),
                    ),
                    _ => None,
                }
            }));
        }
        !expected.is_empty()
    });
    for message in expected {
        assert_eq!(read_body(&mut instance, message.command), message.body);
    }
    gateway.stop().unwrap();
}

#[test]
fn sampled_ground_contact_reaches_original_instance_hit_point_wire() {
    sampled_surface_contact_reaches_original_instance_hit_point_wire(true);
}

#[test]
fn sampled_terrain_contact_reaches_original_instance_hit_point_wire() {
    sampled_surface_contact_reaches_original_instance_hit_point_wire(false);
}

fn sampled_surface_contact_reaches_original_instance_hit_point_wire(ground: bool) {
    let mut gateway = gateway();
    let ports = gateway.start(1).unwrap().ports;
    let (config, binding) = match_config(7);
    gateway.start_battle(config, binding).unwrap();
    gateway.bind_battle_client_clock(7, -1000).unwrap();
    gateway.bind_battle_server_clock(7, 5000).unwrap();
    let mut instance = connect_instance(ports[2]);
    let mut world = WorldSample::decode(&match_world(7)).unwrap();
    world.stamp.at_ms = gateway.battle_clock_ms().unwrap();
    gateway
        .submit_battle_world(&world.encode().unwrap())
        .unwrap();
    for (batch_id, command) in [(1, 1), (2, 11)] {
        let intent = PlayerIntent {
            stamp: SourceStamp {
                epoch: 7,
                source_id: 1,
                batch_id,
                at_ms: gateway.battle_clock_ms().unwrap(),
            },
            actor_id: 1,
            client_rid: 1,
            command,
            flags: 0,
        };
        gateway
            .submit_battle_player(&intent.encode().unwrap())
            .unwrap();
    }
    let mut swing = None;
    wait(|| {
        if let Some(batch) = gateway.dispatch_next_battle_output().unwrap() {
            swing = batch.iter().find_map(|output| match output {
                Output::Action(ActionEvent::SwingStarted {
                    actor_id: 1,
                    sequence,
                    weapon_id,
                    began_at_ms,
                    contact_start_ms,
                    contact_end_ms,
                    ..
                }) => Some((
                    *sequence,
                    *weapon_id,
                    *began_at_ms,
                    *contact_start_ms,
                    *contact_end_ms,
                )),
                _ => None,
            });
        }
        swing.is_some()
    });
    let (sequence, weapon_id, began_at, contact_start, contact_end) = swing.unwrap();
    assert!(contact_end > contact_start);
    let occurred_at = began_at + u64::from((contact_start + contact_end) / 2);
    wait(|| gateway.battle_clock_ms().unwrap() > occurred_at);

    world.stamp.batch_id = 2;
    world.stamp.at_ms = gateway.battle_clock_ms().unwrap();
    world.actors[0].sequence = sequence;
    gateway
        .submit_battle_world(&world.encode().unwrap())
        .unwrap();
    let evidence = ContactEvent {
        actor_id: 1,
        target_id: 0,
        source_entity: 101,
        target_entity: 0,
        sequence,
        phase_at_ms: began_at,
        target_intent: None,
        target_phase: Phase::Idle,
        target_phase_at_ms: 0.,
        target_observed_at_ms: None,
        source_observed_at_ms: None,
        occurred_at_ms: occurred_at as f64,
        along: 0.5,
        defence_weapon_id: 0,
        region: Region::Torso,
        body_index: -1,
        source_position: [1., 20., 0.],
        target_position: [1., 20., 0.],
        source_heading: 0.,
        target_heading: 0.,
        source_velocity: [0.; 3],
        target_velocity: [0.; 3],
        weapon_velocity: [0.; 3],
        velocity_ready: false,
        airborne: false,
        target_airborne: false,
        position: [1., 20., 0.],
        normal: if ground { [0., 0., 1.] } else { [1., 0., 0.] },
        direction: [1., 0., 0.],
        material: if ground { 2 } else { 4 },
    };
    gateway
        .submit_battle_contacts(ContactSample {
            stamp: SourceStamp {
                epoch: 7,
                source_id: 1,
                batch_id: 1,
                at_ms: gateway.battle_clock_ms().unwrap(),
            },
            world_batch_id: 2,
            dropped: 0,
            events: vec![BoundContact {
                client_rid: 1,
                target_client_rid: 0,
                event: evidence,
            }],
        })
        .unwrap();
    let mut projected = false;
    wait(|| {
        if let Some(batch) = gateway.dispatch_next_battle_output().unwrap() {
            projected |= batch.iter().any(|output| {
                matches!(
                    output,
                    Output::SurfaceHit {
                        attacker: 1,
                        ground: hit_ground,
                        ..
                    } if *hit_ground == ground
                )
            });
        }
        projected
    });
    let mut hit = None;
    for _ in 0..64 {
        let (command, _, body) = read_any_message(&mut instance);
        if command == 4 && body.starts_with(&27_u16.to_be_bytes()) {
            hit = Some(body);
            break;
        }
    }
    let hit = hit.expect("surface-hit message was not sent to the client");
    assert_eq!(hit.len(), 93);
    assert_eq!(u64::from_be_bytes(hit[51..59].try_into().unwrap()), 1);
    assert_eq!(
        i32::from_be_bytes(hit[59..63].try_into().unwrap()),
        weapon_id as i32
    );
    assert_eq!(u64::from_be_bytes(hit[63..71].try_into().unwrap()), 0);
    assert_eq!(
        u64::from_be_bytes(hit[79..87].try_into().unwrap()),
        occurred_at + 5000
    );
    gateway.stop().unwrap();
}

#[test]
fn sampled_bot_route_reaches_original_instance_wire_with_bound_rid_and_pose() {
    let mut gateway = gateway();
    let state = gateway.start(1).unwrap();
    let (config, mut binding) = match_config(7);
    binding.actor_rids = BTreeMap::from([(1, 101), (2, 202)]);
    gateway.start_battle(config, binding).unwrap();
    gateway.bind_battle_client_clock(7, -1000).unwrap();
    let mut instance = connect_instance_with_rid(state.ports[2], 101);
    let world = WorldSample {
        stamp: SourceStamp {
            epoch: 7,
            source_id: 1,
            batch_id: 1,
            at_ms: 1000,
        },
        actors: [(1, 101, 0., 0.), (2, 202, 4., 180.)]
            .into_iter()
            .map(|(actor_id, client_rid, x, heading)| SampledActor {
                actor_id,
                client_rid,
                sequence: 1,
                entity: u64::from(actor_id) + 100,
                position: [x, 20., 0.],
                heading,
                contact_target_id: 0,
                contact_state: 0,
            })
            .collect(),
    }
    .encode()
    .unwrap();
    gateway.submit_battle_world(&world).unwrap();

    let mut bot_moved = false;
    wait(|| {
        if let Some(batch) = gateway.dispatch_next_battle_output().unwrap() {
            bot_moved = batch.iter().any(|output| {
                matches!(output, Output::Bot(bot) if matches!(bot.movement, t7r_battle_core::ai::MovementIntent::Follow { .. }))
            });
        }
        bot_moved
    });
    let body = read_body(&mut instance, 2);
    assert_eq!(u16::from_be_bytes(body[6..8].try_into().unwrap()), 202);
    assert_eq!(f32::from_be_bytes(body[19..23].try_into().unwrap()), 4.);
    assert_eq!(f32::from_be_bytes(body[23..27].try_into().unwrap()), 20.);
    gateway.stop().unwrap();
}

#[test]
fn timeout_settlement_start_and_finish_reach_instance_with_bound_identity_and_clock() {
    let mut gateway = gateway();
    let state = gateway.start(1).unwrap();
    let (mut config, mut binding) = match_config(7);
    config.duration_ms = 1500;
    binding.actor_rids = BTreeMap::from([(1, 101), (2, 202)]);
    gateway.start_battle(config, binding.clone()).unwrap();
    gateway.bind_battle_server_clock(7, 5000).unwrap();
    let mut instance = connect_instance_with_rid(state.ports[2], 101);
    assert!(!gateway.battle_returned_to_room().unwrap());
    instance
        .set_read_timeout(Some(Duration::from_secs(8)))
        .unwrap();
    let camp = include_str!("../../../../tests/fixtures/protocol/instance-control-uplink-v1.txt")
        .lines()
        .find(|line| line.starts_with("camp-choice "))
        .unwrap()
        .split_whitespace()
        .collect::<Vec<_>>();
    instance.write_all(&hex(camp[3])).unwrap();
    assert_eq!(&read_body(&mut instance, 35)[..2], &[0, 2]);
    let ready =
        include_str!("../../../../tests/fixtures/protocol/instance-actor-import-uplink-v1.txt")
            .split_whitespace()
            .collect::<Vec<_>>();
    instance.write_all(&hex(ready[3])).unwrap();
    let pending = request(&gateway);
    assert!(
        gateway
            .mailbox()
            .lock()
            .unwrap()
            .complete(&response(&pending))
            .unwrap()
    );
    let mut imported = 0;
    while imported < 7 {
        let (command, _, body) = read_any_message(&mut instance);
        let selector = u16::from_be_bytes(body[..2].try_into().unwrap());
        if command == 10 && matches!(selector, 0x65 | 0x66 | 0x67 | 0x194 | 0xc8) {
            continue;
        }
        imported += 1;
    }

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
    settlement.started_at_ms += 5000;
    settlement.ended_at_ms += 5000;
    let expected = encode_settlement_start_for_client(&settlement, &binding.actor_rids).unwrap();
    assert_eq!(
        expected
            .iter()
            .map(|message| message.command)
            .collect::<Vec<_>>(),
        [12, 12, 10, 10]
    );
    for message in expected {
        loop {
            let (command, _, body) = read_any_message(&mut instance);
            let selector = u16::from_be_bytes(body[..2].try_into().unwrap());
            if command == 10
                && (matches!(selector, 0x65 | 0x66 | 0x194 | 0xc8)
                    || selector == 0x67 && body[18..22] == 2_i32.to_be_bytes())
            {
                continue;
            }
            assert_eq!(command, message.command);
            assert_eq!(body, message.body);
            break;
        }
    }
    let settle = read_body(&mut instance, 52);
    let finish = read_body(&mut instance, 10);
    let finish_at = u64::from_be_bytes(finish[2..10].try_into().unwrap());
    let expected_finish = encode_settlement_finish_for_client(
        &settlement,
        1,
        100,
        10_000,
        finish_at,
        &binding.actor_rids,
    )
    .unwrap();
    assert_eq!(settle, expected_finish[0].body);
    assert_eq!(finish, expected_finish[1].body);
    let leave = include_str!("../../../../tests/fixtures/protocol/leave-uplink-v1.txt")
        .lines()
        .map(|line| line.split_whitespace().collect::<Vec<_>>())
        .collect::<Vec<_>>();
    instance.write_all(&hex(leave[0][3])).unwrap();
    let leave_body = read_body(&mut instance, 10);
    assert_eq!(&leave_body[..2], &[0, 3]);
    assert_eq!(&leave_body[10..18], &1_u64.to_be_bytes());
    assert_eq!(&leave_body[18..26], &1_u64.to_be_bytes());
    let logout = read_body(&mut instance, 44);
    assert_eq!(&logout[..2], &[0, 3]);
    assert_eq!(&logout[10..], &[0, 0, 0, 0, 1]);
    assert!(!gateway.battle_returned_to_room().unwrap());
    instance.write_all(&hex(leave[1][3])).unwrap();
    let quit = read_body(&mut instance, 55);
    let expected_quit = include_str!("../../../../tests/fixtures/protocol/leave-wire-v1.txt")
        .lines()
        .find(|line| line.starts_with("user-group-quit-response "))
        .unwrap()
        .split_whitespace()
        .nth(1)
        .unwrap();
    assert_eq!(quit, hex(expected_quit));
    wait(|| gateway.battle_returned_to_room().unwrap());
    gateway.stop_battle().unwrap();
    assert!(gateway.battle_returned_to_room().is_err());
    let (config, binding) = match_config(8);
    gateway.start_battle_staged(config, binding).unwrap();
    assert!(!gateway.battle_returned_to_room().unwrap());
    gateway.stop().unwrap();
    assert!(gateway.battle_returned_to_room().is_err());
}

#[test]
fn twenty_member_natural_settlement_projects_every_score_then_finishes() {
    let mut gateway = gateway();
    let state = gateway.start(1).unwrap();
    let (config, binding) = twenty_member_config(7, 1200);
    gateway.start_battle(config, binding.clone()).unwrap();
    gateway.bind_battle_server_clock(7, 5000).unwrap();
    let mut instance = connect_instance_with_rid(state.ports[2], 101);
    instance
        .set_read_timeout(Some(Duration::from_secs(8)))
        .unwrap();
    let camp = include_str!("../../../../tests/fixtures/protocol/instance-control-uplink-v1.txt")
        .lines()
        .find(|line| line.starts_with("camp-choice "))
        .unwrap()
        .split_whitespace()
        .collect::<Vec<_>>();
    instance.write_all(&hex(camp[3])).unwrap();
    assert_eq!(&read_body(&mut instance, 35)[..2], &[0, 2]);
    let ready =
        include_str!("../../../../tests/fixtures/protocol/instance-actor-import-uplink-v1.txt")
            .split_whitespace()
            .collect::<Vec<_>>();
    instance.write_all(&hex(ready[3])).unwrap();
    let pending = request(&gateway);
    assert!(
        gateway
            .mailbox()
            .lock()
            .unwrap()
            .complete(&response(&pending))
            .unwrap()
    );
    for _ in 0..43 {
        read_without_startup(&mut instance);
    }
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
    assert_eq!(settlement.members.len(), 20);
    settlement.started_at_ms += 5000;
    settlement.ended_at_ms += 5000;
    let expected = encode_settlement_start_for_client(&settlement, &binding.actor_rids).unwrap();
    assert_eq!(expected.len(), 22);
    for message in expected {
        let (command, _, body) = read_without_startup(&mut instance);
        assert_eq!(command, message.command);
        assert_eq!(body, message.body);
    }
    let settle = read_body(&mut instance, 52);
    let finish = read_body(&mut instance, 10);
    let finish_at = u64::from_be_bytes(finish[2..10].try_into().unwrap());
    let expected_finish = encode_settlement_finish_for_client(
        &settlement,
        1,
        100,
        10_000,
        finish_at,
        &binding.actor_rids,
    )
    .unwrap();
    assert_eq!(settle, expected_finish[0].body);
    assert_eq!(finish, expected_finish[1].body);
    gateway.stop().unwrap();
}

#[test]
fn manual_logout_ends_the_bound_match_before_leaving_instance() {
    let mut gateway = gateway();
    let state = gateway.start(1).unwrap();
    let (mut config, binding) = match_config(7);
    config.duration_ms = 120_000;
    gateway.start_battle(config, binding).unwrap();
    gateway.bind_battle_server_clock(7, 5000).unwrap();
    let mut instance = connect_instance(state.ports[2]);
    let camp = include_str!("../../../../tests/fixtures/protocol/instance-control-uplink-v1.txt")
        .lines()
        .find(|line| line.starts_with("camp-choice "))
        .unwrap()
        .split_whitespace()
        .collect::<Vec<_>>();
    instance.write_all(&hex(camp[3])).unwrap();
    assert_eq!(&read_body(&mut instance, 35)[..2], &[0, 2]);
    let ready =
        include_str!("../../../../tests/fixtures/protocol/instance-actor-import-uplink-v1.txt")
            .split_whitespace()
            .collect::<Vec<_>>();
    instance.write_all(&hex(ready[3])).unwrap();
    let pending = request(&gateway);
    assert!(
        gateway
            .mailbox()
            .lock()
            .unwrap()
            .complete(&response(&pending))
            .unwrap()
    );
    let mut imported = 0;
    while imported < 7 {
        let (command, _, body) = read_any_message(&mut instance);
        let selector = u16::from_be_bytes(body[..2].try_into().unwrap());
        if command == 10
            && (matches!(selector, 0x65 | 0x66 | 0x194 | 0xc8)
                || selector == 0x67 && body[18..22] == 2_i32.to_be_bytes())
        {
            continue;
        }
        imported += 1;
    }
    let leave = include_str!("../../../../tests/fixtures/protocol/leave-uplink-v1.txt")
        .lines()
        .next()
        .unwrap()
        .split_whitespace()
        .collect::<Vec<_>>();
    instance.write_all(&hex(leave[3])).unwrap();
    let mut ended = false;
    wait(|| {
        if let Some(batch) = gateway.dispatch_next_battle_output().unwrap() {
            ended |= batch.into_iter().any(|output| {
                matches!(output, Output::Ended(value) if value.reason == t7r_battle_runtime::battle_session::EndReason::Quit)
            });
        }
        ended
    });
    let mut received = Vec::new();
    while received.len() < 7 {
        let (command, _, body) = read_any_message(&mut instance);
        let selector = u16::from_be_bytes(body[..2].try_into().unwrap());
        if command == 10
            && (matches!(selector, 0x65 | 0x66 | 0x194 | 0xc8)
                || selector == 0x67 && body[18..22] == 2_i32.to_be_bytes())
        {
            continue;
        }
        received.push((command, selector));
    }
    assert_eq!(
        received,
        [
            (12, 1),
            (12, 1),
            (10, 0x67),
            (52, 1),
            (10, 0x69),
            (10, 3),
            (44, 3)
        ]
    );
    assert!(!gateway.battle_returned_to_room().unwrap());
    let quit = include_str!("../../../../tests/fixtures/protocol/leave-uplink-v1.txt")
        .lines()
        .nth(1)
        .unwrap()
        .split_whitespace()
        .collect::<Vec<_>>();
    instance.write_all(&hex(quit[3])).unwrap();
    assert_eq!(&read_body(&mut instance, 55)[..2], &[0, 2]);
    wait(|| gateway.battle_returned_to_room().unwrap());
    gateway.stop().unwrap();
}

#[test]
fn failed_animation_enqueue_preserves_the_complete_worker_batch_for_retry() {
    let mut gateway = gateway();
    let state = gateway.start(1).unwrap();
    let (config, binding) = match_config(7);
    gateway.start_battle(config, binding).unwrap();
    gateway.submit_battle_world(&match_world(7)).unwrap();
    for index in 0..32 {
        gateway
            .send_battle_client_messages(
                7,
                vec![ClientBattleMessage {
                    command: 12,
                    body: vec![index],
                }],
            )
            .unwrap();
    }
    let intent = PlayerIntent {
        stamp: SourceStamp {
            epoch: 7,
            source_id: 1,
            batch_id: 1,
            at_ms: 1000,
        },
        actor_id: 1,
        client_rid: 1,
        command: 1,
        flags: 0,
    }
    .encode()
    .unwrap();
    gateway.submit_battle_player(&intent).unwrap();
    let mut needs_clock = false;
    wait(|| {
        if let Err(error) = gateway.dispatch_next_battle_output() {
            assert_eq!(error.kind(), std::io::ErrorKind::InvalidInput);
            needs_clock = true;
        }
        needs_clock
    });
    gateway.bind_battle_client_clock(7, -1000).unwrap();
    let mut rejected = false;
    wait(|| {
        if let Err(error) = gateway.dispatch_next_battle_output() {
            assert_eq!(error.kind(), std::io::ErrorKind::WouldBlock);
            rejected = true;
        }
        rejected
    });
    let mut instance = connect_instance(state.ports[2]);
    for index in 0..32 {
        assert_eq!(read_body(&mut instance, 12), [index]);
    }
    let retained = gateway.dispatch_next_battle_output().unwrap().unwrap();
    let expected = retained
        .iter()
        .filter_map(|event| match event {
            Output::Action(ActionEvent::Animation(animation)) => Some(
                encode_animation_for_client(animation, 1000, &BTreeMap::from([(1, 1), (2, 2)]))
                    .unwrap(),
            ),
            _ => None,
        })
        .collect::<Vec<_>>();
    assert!(!expected.is_empty());
    for message in expected {
        assert_eq!(read_body(&mut instance, message.command), message.body);
    }
    gateway.stop().unwrap();
}

#[test]
fn three_loopback_channels_platform_identity_and_shared_compatibility_vectors() {
    let mut gateway = gateway();
    let state = gateway.start(1).unwrap();
    assert!(state.running);
    assert_eq!(
        (state.rules_version, state.resource_version, state.map_id),
        (3, 7, 1028)
    );
    let mut login = connect(state.ports[0]);
    assert_eq!(read_body(&mut login, 1), corpus("version").2);
    let mut logic = connect(state.ports[1]);
    let request = request(&gateway);
    assert_eq!(request["operation"], "readIdentity");
    assert!(
        gateway
            .mailbox()
            .lock()
            .unwrap()
            .complete(&response(&request))
            .unwrap()
    );
    assert_eq!(read_body(&mut logic, 1), corpus("login").2);
    assert_eq!(read_body(&mut logic, 0x2c), corpus("sync").2);
    for name in ["heartbeat", "custom", "governor", "reconnect"] {
        let (command, input, expected) = corpus(name);
        logic.write_all(&input).unwrap();
        assert_eq!(read_body(&mut logic, command), expected, "{name}");
    }
    let mut instance = connect(state.ports[2]);
    assert_eq!(instance.read(&mut [0; 1]).unwrap(), 0);
    wait(|| gateway.snapshot().unwrap().connection_errors == 1);
    assert!(gateway.last_error().unwrap().contains("battle backend"));
    gateway.stop().unwrap();
    assert_eq!(gateway.snapshot().unwrap().active_connections, 0);
    assert_eq!(gateway.snapshot().unwrap().ports, [0; 3]);
}

#[test]
fn logic_burst_backpressures_without_losing_requests_or_replies() {
    let mut gateway = gateway();
    let state = gateway.start(1).unwrap();
    let mut logic = connect(state.ports[1]);
    let identity = request(&gateway);
    gateway
        .mailbox()
        .lock()
        .unwrap()
        .complete(&response(&identity))
        .unwrap();
    assert_eq!(read_body(&mut logic, 1), corpus("login").2);
    assert_eq!(read_body(&mut logic, 0x2c), corpus("sync").2);

    let requests = ["heartbeat", "custom", "governor", "reconnect"]
        .into_iter()
        .cycle()
        .take(256)
        .map(corpus)
        .collect::<Vec<_>>();
    let burst = requests
        .iter()
        .flat_map(|(_, input, _)| input.iter().copied())
        .collect::<Vec<_>>();
    logic.write_all(&burst).unwrap();
    for (command, _, expected) in requests {
        assert_eq!(read_body(&mut logic, command), expected);
    }
    assert_eq!(gateway.snapshot().unwrap().connection_errors, 0);
    gateway.stop().unwrap();
}

fn complete_lobby_profile(gateway: &Gateway, formation: &[i32]) {
    let pending = request(gateway);
    assert_eq!(pending["operation"], "readProfile");
    let response = serde_json::to_vec(&json!({"version":1,"epoch":pending["epoch"],
        "requestId":pending["requestId"],"success":true,
        "profile":{"schemaVersion":1,"revision":0,"formation":formation,"weapons":{
            "1101":[{"slot":1,"group":0,"index":0},{"slot":2,"group":2,"index":0}],
            "2102":[{"slot":1,"group":0,"index":0},{"slot":2,"group":2,"index":0}]}}}))
    .unwrap();
    assert!(
        gateway
            .mailbox()
            .lock()
            .unwrap()
            .complete(&response)
            .unwrap()
    );
}

#[test]
fn empty_formation_rejects_matching_without_closing_or_reserving_the_lobby() {
    let mut gateway = gateway();
    let state = gateway.start(1).unwrap();
    let (config, binding) = match_config(7);
    gateway.start_battle_staged(config, binding).unwrap();
    let mut logic = connect(state.ports[1]);
    let identity = request(&gateway);
    gateway
        .mailbox()
        .lock()
        .unwrap()
        .complete(&response(&identity))
        .unwrap();
    read_body(&mut logic, 1);
    read_body(&mut logic, 0x2c);
    logic.write_all(&room_uplink("match")).unwrap();
    complete_lobby_profile(&gateway, &[]);
    let mut expected = room_body("match-result");
    let result_offset = expected.len() - 201;
    expected[result_offset..result_offset + 4].copy_from_slice(&630001_i32.to_be_bytes());
    assert_eq!(read_body(&mut logic, 0x20), expected);
    assert!(gateway.battle_preparation_open().unwrap());
    let (command, request, response) = corpus("heartbeat");
    logic.write_all(&request).unwrap();
    assert_eq!(read_body(&mut logic, command), response);
    assert_eq!(gateway.snapshot().unwrap().connection_errors, 0);
    gateway.stop().unwrap();
}

#[test]
fn original_logic_direct_match_from_lobby_opens_staged_instance() {
    let mut gateway = gateway();
    let state = gateway.start(1).unwrap();
    let (config, binding) = match_config(7);
    gateway.start_battle_staged(config, binding).unwrap();
    let mut logic = connect(state.ports[1]);
    let identity = request(&gateway);
    assert!(
        gateway
            .mailbox()
            .lock()
            .unwrap()
            .complete(&response(&identity))
            .unwrap()
    );
    assert_eq!(read_body(&mut logic, 1), corpus("login").2);
    assert_eq!(read_body(&mut logic, 0x2c), corpus("sync").2);

    logic.write_all(&room_uplink("match")).unwrap();
    complete_lobby_profile(&gateway, &[1101]);
    assert_eq!(read_body(&mut logic, 0x20), room_body("match-result"));
    assert_eq!(read_body(&mut logic, 0x20), room_body("match-notify"));
    assert_eq!(read_body(&mut logic, 0x1e), room_body("instance-notify"));
    logic.write_all(&room_uplink("enter")).unwrap();
    let entered = read_body(&mut logic, 0x1e);
    let url = format!("127.0.0.1:{}\0", state.ports[2]).into_bytes();
    assert!(entered.windows(url.len()).any(|window| window == url));
    assert_eq!(&entered[..2], &[0, 4]);
    let mut instance = connect_instance(state.ports[2]);
    gateway.stop_battle().unwrap();
    assert_eq!(instance.read(&mut [0; 1]).unwrap(), 0);
    gateway.stop().unwrap();
}

#[test]
fn original_logic_room_match_notification_opens_the_staged_instance() {
    let mut gateway = gateway();
    let state = gateway.start(1).unwrap();
    let (config, binding) = match_config(7);
    gateway.start_battle_staged(config, binding).unwrap();
    assert!(gateway.battle_preparation_open().unwrap());
    let mut logic = connect(state.ports[1]);
    let identity = request(&gateway);
    assert!(
        gateway
            .mailbox()
            .lock()
            .unwrap()
            .complete(&response(&identity))
            .unwrap()
    );
    assert_eq!(read_body(&mut logic, 1), corpus("login").2);
    assert_eq!(read_body(&mut logic, 0x2c), corpus("sync").2);

    logic.write_all(&room_uplink("create")).unwrap();
    assert_eq!(read_body(&mut logic, 0x1e), room_body("create-result"));
    let entered = read_body(&mut logic, 0x1e);
    let url = format!("127.0.0.1:{}\0", state.ports[2]).into_bytes();
    assert!(entered.windows(url.len()).any(|window| window == url));
    assert_eq!(&entered[..2], &[0, 4]);
    assert!(gateway.battle_preparation_open().unwrap());
    logic.write_all(&room_uplink("match")).unwrap();
    complete_lobby_profile(&gateway, &[1101]);
    assert_eq!(read_body(&mut logic, 0x20), room_body("match-result"));
    assert!(!gateway.battle_preparation_open().unwrap());
    assert!(!gateway.reserve_battle_rearm().unwrap());
    assert_eq!(read_body(&mut logic, 0x20), room_body("match-notify"));
    assert_eq!(read_body(&mut logic, 0x1e), room_body("instance-notify"));
    let mut instance = connect_instance(state.ports[2]);
    gateway.stop_battle().unwrap();
    assert_eq!(instance.read(&mut [0; 1]).unwrap(), 0);

    let (config, binding) = match_config(8);
    gateway.start_battle_staged(config, binding).unwrap();
    assert!(gateway.battle_preparation_open().unwrap());
    let mut next_logic = connect(state.ports[1]);
    let identity = request(&gateway);
    assert!(
        gateway
            .mailbox()
            .lock()
            .unwrap()
            .complete(&response(&identity))
            .unwrap()
    );
    assert_eq!(read_body(&mut next_logic, 1), corpus("login").2);
    assert_eq!(read_body(&mut next_logic, 0x2c), corpus("sync").2);
    next_logic.write_all(&room_uplink("enter")).unwrap();
    let entered = read_body(&mut next_logic, 0x1e);
    assert!(entered.windows(url.len()).any(|window| window == url));
    next_logic.write_all(&room_uplink("match")).unwrap();
    complete_lobby_profile(&gateway, &[1101]);
    assert_eq!(read_body(&mut next_logic, 0x20), room_body("match-result"));
    assert_eq!(read_body(&mut next_logic, 0x20), room_body("match-notify"));
    assert_eq!(
        read_body(&mut next_logic, 0x1e),
        room_body("instance-notify")
    );
    let mut next_instance = connect_instance(state.ports[2]);
    gateway.stop_battle().unwrap();
    assert_eq!(next_instance.read(&mut [0; 1]).unwrap(), 0);

    let (config, binding) = match_config(9);
    gateway.start_battle_staged(config, binding).unwrap();
    assert!(!gateway.battle_returned_to_room().unwrap());
    let rematch = include_str!("../../../../tests/fixtures/protocol/room-rematch-uplink-v1.txt")
        .lines()
        .map(|line| line.split_whitespace().collect::<Vec<_>>())
        .collect::<Vec<_>>();
    logic.write_all(&hex(rematch[0][3])).unwrap();
    let quit_response = include_str!("../../../../tests/fixtures/protocol/leave-wire-v1.txt")
        .lines()
        .find(|line| line.starts_with("user-group-quit-response "))
        .unwrap()
        .split_whitespace()
        .nth(1)
        .unwrap();
    assert_eq!(read_body(&mut logic, 0x37), hex(quit_response));
    assert!(!gateway.battle_returned_to_room().unwrap());
    logic.write_all(&hex(rematch[1][3])).unwrap();
    assert_eq!(&read_body(&mut logic, 0x1e)[..2], &[0, 4]);
    logic.write_all(&hex(rematch[2][3])).unwrap();
    complete_lobby_profile(&gateway, &[1101]);
    assert_eq!(read_body(&mut logic, 0x20), room_body("match-result"));
    assert_eq!(read_body(&mut logic, 0x20), room_body("match-notify"));
    assert_eq!(read_body(&mut logic, 0x1e), room_body("instance-notify"));
    let mut third_instance = connect_instance(state.ports[2]);
    gateway.stop_battle().unwrap();
    assert_eq!(third_instance.read(&mut [0; 1]).unwrap(), 0);
    gateway.stop().unwrap();
}

#[test]
fn rearm_reservation_wins_over_a_late_match_or_instance_binding() {
    let mut gateway = gateway();
    let state = gateway.start(1).unwrap();
    let (config, binding) = match_config(7);
    gateway.start_battle_staged(config, binding).unwrap();
    let mut logic = connect(state.ports[1]);
    let identity = request(&gateway);
    assert!(
        gateway
            .mailbox()
            .lock()
            .unwrap()
            .complete(&response(&identity))
            .unwrap()
    );
    assert_eq!(read_body(&mut logic, 1), corpus("login").2);
    assert_eq!(read_body(&mut logic, 0x2c), corpus("sync").2);
    logic.write_all(&room_uplink("create")).unwrap();
    assert_eq!(read_body(&mut logic, 0x1e), room_body("create-result"));
    assert_eq!(&read_body(&mut logic, 0x1e)[..2], &[0, 4]);
    assert!(gateway.reserve_battle_rearm().unwrap());
    assert!(!gateway.reserve_battle_rearm().unwrap());
    assert!(!gateway.battle_preparation_open().unwrap());
    logic.write_all(&room_uplink("match")).unwrap();
    complete_lobby_profile(&gateway, &[1101]);
    assert_eq!(logic.read(&mut [0; 1]).unwrap(), 0);
    let mut instance = connect(state.ports[2]);
    assert_eq!(instance.read(&mut [0; 1]).unwrap(), 0);
    gateway.stop_battle().unwrap();
    let (config, binding) = match_config(8);
    gateway.start_battle_staged(config, binding).unwrap();
    assert!(gateway.battle_preparation_open().unwrap());
    gateway.stop().unwrap();
}

#[test]
fn room_creation_without_a_staged_battle_closes_instead_of_announcing_an_instance() {
    let mut gateway = gateway();
    let state = gateway.start(1).unwrap();
    let mut logic = connect(state.ports[1]);
    let identity = request(&gateway);
    assert!(
        gateway
            .mailbox()
            .lock()
            .unwrap()
            .complete(&response(&identity))
            .unwrap()
    );
    read_body(&mut logic, 1);
    read_body(&mut logic, 0x2c);
    logic.write_all(&room_uplink("create")).unwrap();
    assert_eq!(logic.read(&mut [0; 1]).unwrap(), 0);
    wait(|| gateway.snapshot().unwrap().connection_errors == 1);
    assert!(gateway.last_error().unwrap().contains("no staged battle"));
    gateway.stop().unwrap();
}

#[test]
fn stale_duplicate_invalid_and_cancelled_platform_responses_do_not_mutate_new_sessions() {
    let mut gateway = gateway();
    let state = gateway.start(41).unwrap();
    let _logic = connect(state.ports[1]);
    let old = request(&gateway);
    let mut bad: Value = serde_json::from_slice(&response(&old)).unwrap();
    bad["identity"]["displayName"] = json!("😀");
    assert!(
        gateway
            .mailbox()
            .lock()
            .unwrap()
            .complete(&serde_json::to_vec(&bad).unwrap())
            .is_err()
    );
    assert_eq!(gateway.snapshot().unwrap().pending_requests, 1);
    gateway.stop().unwrap();
    assert!(
        !gateway
            .mailbox()
            .lock()
            .unwrap()
            .complete(&response(&old))
            .unwrap()
    );
    assert!(gateway.start(41).is_err());
    let state = gateway.start(42).unwrap();
    assert!(gateway.start(43).is_err());
    let mut logic = connect(state.ports[1]);
    let new = request(&gateway);
    assert!(
        !gateway
            .mailbox()
            .lock()
            .unwrap()
            .complete(&response(&old))
            .unwrap()
    );
    assert!(
        gateway
            .mailbox()
            .lock()
            .unwrap()
            .complete(&response(&new))
            .unwrap()
    );
    assert!(
        !gateway
            .mailbox()
            .lock()
            .unwrap()
            .complete(&response(&new))
            .unwrap()
    );
    assert_eq!(read_body(&mut logic, 1), corpus("login").2);
    gateway.stop().unwrap();
    gateway.stop().unwrap();
}

#[test]
fn disconnect_cancels_platform_wait_and_stop_closes_partial_reads() {
    let mut gateway = gateway();
    let state = gateway.start(1).unwrap();
    let logic = connect(state.ports[1]);
    let pending = request(&gateway);
    drop(logic);
    wait(|| gateway.snapshot().unwrap().pending_requests == 0);
    assert!(
        !gateway
            .mailbox()
            .lock()
            .unwrap()
            .complete(&response(&pending))
            .unwrap()
    );
    let mut partial = TcpStream::connect(("127.0.0.1", state.ports[0])).unwrap();
    partial.write_all(&[0x55, 0x0e]).unwrap();
    let began = Instant::now();
    gateway.stop().unwrap();
    assert!(began.elapsed() < Duration::from_secs(2));
    assert_eq!(gateway.snapshot().unwrap().active_connections, 0);
    assert!(TcpStream::connect(("127.0.0.1", state.ports[0])).is_err());
}

#[test]
fn backend_failure_closes_connection_without_login_success() {
    let mut gateway = gateway();
    let state = gateway.start(1).unwrap();
    let mut logic = connect(state.ports[1]);
    let request = request(&gateway);
    let failure = serde_json::to_vec(&json!({"version": 1, "epoch": request["epoch"],
        "requestId": request["requestId"], "success": false}))
    .unwrap();
    assert!(
        gateway
            .mailbox()
            .lock()
            .unwrap()
            .complete(&failure)
            .unwrap()
    );
    assert_eq!(logic.read(&mut [0; 1]).unwrap(), 0);
    wait(|| gateway.snapshot().unwrap().connection_errors == 1);
    gateway.stop().unwrap();
}

#[test]
fn disconnect_and_receive_backpressure_cancel_lobby_wait_without_reordering_heartbeats() {
    let mut gateway = gateway();
    let state = gateway.start(1).unwrap();
    for saturate in [false, true] {
        let mut logic = connect(state.ports[1]);
        let identity = request(&gateway);
        gateway
            .mailbox()
            .lock()
            .unwrap()
            .complete(&response(&identity))
            .unwrap();
        read_body(&mut logic, 1);
        read_body(&mut logic, 0x2c);
        let row: Vec<_> = include_str!("../../../../tests/fixtures/protocol/loadout-v1.txt")
            .lines()
            .find(|line| line.starts_with("formations "))
            .unwrap()
            .split_whitespace()
            .collect();
        logic.write_all(&hex(row[3])).unwrap();
        let pending = request(&gateway);
        assert_eq!(pending["operation"], "readProfile");
        let began = Instant::now();
        if saturate {
            let burst = corpus("heartbeat").1.repeat(33);
            logic.write_all(&burst).unwrap();
            // Heartbeats remain behind the pending platform operation. Brief
            // saturation waits; an unresolved operation eventually times out.
            logic
                .set_read_timeout(Some(Duration::from_millis(100)))
                .unwrap();
            let error = logic.read(&mut [0; 1]).unwrap_err();
            assert!(matches!(
                error.kind(),
                std::io::ErrorKind::TimedOut | std::io::ErrorKind::WouldBlock
            ));
            logic
                .set_read_timeout(Some(Duration::from_secs(10)))
                .unwrap();
            assert_eq!(logic.read(&mut [0; 1]).unwrap(), 0);
        }
        drop(logic);
        wait(|| {
            gateway.snapshot().unwrap().pending_requests == 0
                && gateway.snapshot().unwrap().active_connections == 0
        });
        assert!(began.elapsed() < Duration::from_secs(if saturate { 8 } else { 2 }));
        assert!(
            !gateway
                .mailbox()
                .lock()
                .unwrap()
                .complete(&response(&pending))
                .unwrap()
        );
    }
    assert_eq!(gateway.snapshot().unwrap().connection_errors, 1);
    assert!(gateway.last_error().unwrap().contains("timed out"));
    gateway.stop().unwrap();
}
