use super::*;
use crate::battle_session::{EquipmentItem, Life, Member};
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    io::{Read, Write},
    net::TcpStream,
    time::{Duration, Instant},
};

const RETRIES: &str =
    include_str!("../../../../../tests/fixtures/protocol/instance-hero-retry-uplink-v1.txt");

#[path = "initial_selection_tests.rs"]
mod initial_tests;
#[path = "refresh_connection_tests.rs"]
mod refresh_tests;
#[path = "squad_connection_tests.rs"]
mod squad_tests;
#[path = "training_connection_tests.rs"]
mod training_tests;

fn hex(value: &str) -> Vec<u8> {
    value
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
        .collect()
}

fn send(stream: &mut TcpStream, corpus: &str, name: &str) {
    let row = corpus
        .lines()
        .find(|line| line.split_whitespace().next() == Some(name))
        .unwrap();
    stream
        .write_all(&hex(row.split_whitespace().nth(3).unwrap()))
        .unwrap();
}

fn read_until(stream: &mut TcpStream, command: u16, selector: u16) -> Vec<u8> {
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        assert!(Instant::now() < deadline, "expected reply was not received");
        let mut head = [0; 12];
        stream.read_exact(&mut head).unwrap();
        assert_eq!(&head[..8], &[0x55, 0x0e, 5, 0, 0, 0, 0, 12]);
        let length = u32::from_be_bytes(head[8..].try_into().unwrap()) as usize;
        assert!(length < 4096);
        let mut body = vec![0; length];
        stream.read_exact(&mut body).unwrap();
        assert_eq!(&body[3..11], &[0; 8]);
        if body[1..3] == command.to_be_bytes() && body[11..13] == selector.to_be_bytes() {
            return body[11..].to_vec();
        }
    }
}

fn complete_request(gateway: &Gateway) {
    let deadline = Instant::now() + Duration::from_secs(5);
    let request: Value = loop {
        assert!(
            Instant::now() < deadline,
            "platform request was not received"
        );
        if let Some(bytes) = gateway.mailbox().lock().unwrap().read(4096).unwrap() {
            break serde_json::from_slice(&bytes).unwrap();
        }
        thread::sleep(Duration::from_millis(5));
    };
    let mut response = json!({"version": 1, "epoch": request["epoch"],
        "requestId": request["requestId"], "success": true});
    match request["operation"].as_str().unwrap() {
        "readIdentity" => {
            response["identity"] = json!({"displayName": "Player", "portrait": 7, "level": 100});
        }
        "readProfile" => {
            response["profile"] = json!({"schemaVersion": 1, "revision": 0,
            "formation": [1101, 2102], "weapons": {
                "1101": [{"slot": 1, "group": 0, "index": 0}, {"slot": 2, "group": 2, "index": 0}],
                    "2102": [{"slot": 3, "group": 1, "index": 0}]
            }});
        }
        operation => panic!("unexpected platform request: {operation}"),
    }
    assert!(
        gateway
            .mailbox()
            .lock()
            .unwrap()
            .complete(&serde_json::to_vec(&response).unwrap())
            .unwrap()
    );
}

fn loaded_instance(sender: Option<crate::battle_worker::BattleSender>) -> (Gateway, TcpStream) {
    loaded_instance_with_camp(sender, 2)
}

fn loaded_instance_with_camp(
    sender: Option<crate::battle_worker::BattleSender>,
    bot_camp: u8,
) -> (Gateway, TcpStream) {
    let resources = ResourceBundle::from_slice(include_bytes!(
        "../../../../../tests/fixtures/resources/bundle-v1.json"
    ))
    .unwrap();
    loaded_instance_with_resources(sender, bot_camp, resources)
}

fn loaded_instance_with_resources(
    sender: Option<crate::battle_worker::BattleSender>,
    bot_camp: u8,
    resources: ResourceBundle,
) -> (Gateway, TcpStream) {
    let mut gateway = Gateway::new(Arc::new(resources));
    let state = gateway.start(1).unwrap();
    let config = Config {
        epoch: 7,
        started_at_ms: 1000,
        duration_ms: 120_000,
        kill_target: 10,
        seed: 42,
        members: (1..=2)
            .map(|actor_id| Member {
                actor_id,
                kind: if actor_id == 1 {
                    MemberKind::Human
                } else {
                    MemberKind::Bot
                },
                sample_source_id: 1,
                camp: if actor_id == 1 { 1 } else { bot_camp },
                hero_id: 1101,
                weapon_id: 701,
                equipment: vec![EquipmentItem {
                    slot: 1,
                    weapon_id: 701,
                }],
            })
            .collect(),
    };
    gateway
        .start_battle_staged(
            config,
            SourceBinding {
                epoch: 7,
                source_id: 1,
                actor_rids: BTreeMap::from([(1, 101), (2, 202)]),
            },
        )
        .unwrap();
    if let Some(sender) = sender {
        let roster = gateway
            .instance_tx
            .borrow()
            .as_ref()
            .unwrap()
            .roster()
            .clone();
        let ingress = InstanceIngress::new(
            SourceBinding {
                epoch: 7,
                source_id: 1,
                actor_rids: BTreeMap::from([(1, 101), (2, 202)]),
            },
            roster,
            sender,
            None,
            Arc::new(tokio::sync::Mutex::new(0)),
            1028,
            120_000,
            gateway.resources.clone(),
        )
        .unwrap();
        gateway.instance_tx.send_replace(Some(Arc::new(ingress)));
    }
    let mut stream = TcpStream::connect((Ipv4Addr::LOCALHOST, state.ports[2])).unwrap();
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    stream
        .set_write_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    let mut auth = [0; 29];
    auth[..3].copy_from_slice(&[0x55, 0x0e, 3]);
    auth[23] = 3;
    stream.write_all(&auth).unwrap();
    let mut reply = [0; 48];
    stream.read_exact(&mut reply).unwrap();
    assert_eq!(reply, t7r_battle_core::protocol::CHANGE_KEY_FRAME);
    let initial = read_until(&mut stream, 10, 0x64);
    squad_tests::assert_initial_squads(&initial, bot_camp);
    send(
        &mut stream,
        include_str!("../../../../../tests/fixtures/protocol/instance-control-uplink-v1.txt"),
        "camp-choice",
    );
    read_until(&mut stream, 35, 2);
    send(
        &mut stream,
        include_str!("../../../../../tests/fixtures/protocol/instance-actor-import-uplink-v1.txt"),
        "camp-ready",
    );
    complete_request(&gateway);
    read_until(&mut stream, 23, 1);
    send(
        &mut stream,
        include_str!("../../../../../tests/fixtures/protocol/instance-load-hero-uplink-v1.txt"),
        "actor-load",
    );
    read_until(&mut stream, 10, 0x67);
    (gateway, stream)
}

fn selection_reply(stream: &mut TcpStream, result: i32) {
    let reply = read_until(stream, 54, 0x33);
    assert_eq!(&reply[2..], &result.to_be_bytes());
    if result == 0 {
        assert_eq!(
            read_until(stream, 54, 0x34),
            hex("003400000000000000650000044d00000000")
        );
    }
}

fn publish_life(gateway: &Gateway, stream: &mut TcpStream, life: Life) {
    let active = gateway.instance_tx.borrow();
    active
        .as_ref()
        .unwrap()
        .send_projected(
            vec![scene_wire::actor_state(101, 7, 9000).unwrap()],
            None,
            Some(life),
            super::refresh::RefreshUpdate::default(),
        )
        .unwrap();
    loop {
        let reply = read_until(stream, 54, 1);
        if i32::from_be_bytes(reply[14..18].try_into().unwrap()) == 7 {
            break;
        }
    }
}

#[test]
fn selection_retries_keep_instance_open_and_require_authoritative_selecting_life() {
    let (mut gateway, mut stream) = loaded_instance(None);
    send(&mut stream, RETRIES, "other-before-entry");
    complete_request(&gateway);
    assert_eq!(read_until(&mut stream, 54, 0x33), hex("003300000000"));
    assert_eq!(
        &read_until(&mut stream, 54, 0x34)[10..14],
        &2102_u32.to_be_bytes()
    );
    send(&mut stream, RETRIES, "bound-before-entry");
    selection_reply(&mut stream, 0);
    send(&mut stream, RETRIES, "enter");
    assert_eq!(read_until(&mut stream, 54, 0x65), hex("006500000000"));
    read_until(&mut stream, 10, 0x67);
    gateway.activate_battle(7).unwrap();

    publish_life(
        &gateway,
        &mut stream,
        Life::Dead {
            died_at_ms: 1000,
            respawn_at_ms: 9000,
        },
    );
    send(&mut stream, RETRIES, "choose-while-dead");
    selection_reply(&mut stream, 1);
    publish_life(&gateway, &mut stream, Life::Selecting { died_at_ms: 1000 });
    send(&mut stream, RETRIES, "other-after-death");
    selection_reply(&mut stream, 1);
    send(&mut stream, RETRIES, "play-without-choice");
    assert_eq!(read_until(&mut stream, 54, 0x65), hex("006500000001"));
    for name in ["bound-after-death", "repeat-bound"] {
        send(&mut stream, RETRIES, name);
        selection_reply(&mut stream, 1);
    }
    assert_eq!(gateway.snapshot().unwrap().connection_errors, 0);
    assert_eq!(
        gateway
            .instance_tx
            .borrow()
            .as_ref()
            .unwrap()
            .roster()
            .local
            .hero_id,
        1101
    );
    gateway.stop().unwrap();
}

#[test]
fn malformed_selection_cause_survives_followup_output_disconnect() {
    let (mut gateway, mut stream) = loaded_instance(None);
    send(&mut stream, RETRIES, "invalid-position");
    let deadline = Instant::now() + Duration::from_secs(5);
    while gateway.snapshot().unwrap().connection_errors == 0 {
        assert!(Instant::now() < deadline);
        thread::sleep(Duration::from_millis(5));
    }
    assert!(
        gateway
            .last_error()
            .unwrap()
            .contains("invalid actor hero-selection request")
    );
    let result =
        gateway.send_battle_client_messages(7, vec![scene_wire::actor_choose_hero_result(1)]);
    assert_eq!(
        gateway.battle_result(result).unwrap_err().kind(),
        io::ErrorKind::BrokenPipe
    );
    assert!(
        gateway
            .last_error()
            .unwrap()
            .contains("invalid actor hero-selection request")
    );
    gateway.stop().unwrap();
    gateway.start(2).unwrap();
    assert!(gateway.last_error().unwrap().is_empty());
    gateway.stop().unwrap();
}

#[test]
fn respawn_selection_reaches_worker_and_recreates_the_selected_hero_and_equipment() {
    use crate::{
        battle_session::{Input, Pose, World, selecting_fixture},
        battle_worker::BattleWorker,
    };
    let mut worker = BattleWorker::start(selecting_fixture()).unwrap();
    let sender = worker.sender();
    let (mut gateway, mut stream) = loaded_instance(Some(sender.clone()));
    send(&mut stream, RETRIES, "bound-before-entry");
    complete_request(&gateway);
    selection_reply(&mut stream, 0);
    send(&mut stream, RETRIES, "enter");
    read_until(&mut stream, 54, 0x65);
    read_until(&mut stream, 10, 0x67);
    publish_life(&gateway, &mut stream, Life::Selecting { died_at_ms: 1000 });
    send(&mut stream, RETRIES, "other-after-death");
    assert_eq!(read_until(&mut stream, 54, 0x33), hex("003300000000"));
    let rows =
        include_str!("../../../../../tests/fixtures/protocol/instance-respawn-hero-wire-v1.txt")
            .lines()
            .map(|line| line.split_whitespace().collect::<Vec<_>>())
            .collect::<Vec<_>>();
    assert_eq!(read_until(&mut stream, 54, 0x34), hex(rows[0][2]));
    assert_eq!(gateway.snapshot().unwrap().pending_requests, 0);

    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_time()
        .build()
        .unwrap();
    let at_ms = sender.clock_ms().unwrap();
    runtime
        .block_on(sender.submit(
            7,
            Input::World(Arc::new(World {
                epoch: 7,
                sampled_at_ms: at_ms,
                actors: BTreeMap::from([(
                    1,
                    Pose {
                        entity: 11,
                        position: [0.; 3],
                        heading: 0.,
                        sequence: 1,
                        contact: t7r_battle_core::ai::ContactObservation::unavailable(),
                    },
                )]),
            })),
        ))
        .unwrap();
    send(&mut stream, RETRIES, "play-without-choice");
    let deadline = Instant::now() + Duration::from_secs(5);
    let (batch, life) = loop {
        assert!(Instant::now() < deadline, "selected hero did not respawn");
        if let Ok(batch) = worker.try_recv_contextual() {
            let life = batch.outputs.iter().find_map(|output| match output {
                Output::Life {
                    actor_id: 1,
                    state: state @ Life::Spawning { .. },
                } => Some(*state),
                _ => None,
            });
            if let Some(life) = life {
                break (batch, life);
            }
        }
        thread::sleep(Duration::from_millis(5));
    };
    let active = gateway.instance_tx.borrow().as_ref().unwrap().clone();
    let roster = active.roster_for_outputs(&batch.outputs).unwrap();
    assert_eq!(roster.local.hero_id, 2102);
    assert_eq!(roster.local.weapon_id, 702);
    assert_eq!(
        roster.local.equipment,
        [EquipmentItem {
            slot: 3,
            weapon_id: 702
        }]
    );
    assert!(roster.local_shields.is_empty());
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
            Some(life),
            super::refresh::RefreshUpdate::default(),
        )
        .unwrap();
    assert_eq!(read_until(&mut stream, 54, 0x65), hex("006500000000"));
    let mut vision = read_until(&mut stream, 14, 1);
    vision[45..47].copy_from_slice(&45_i16.to_be_bytes());
    for (bytes, value) in vision[47..59].chunks_exact_mut(4).zip([5_f32, 6., 0.]) {
        bytes.copy_from_slice(&value.to_be_bytes());
    }
    assert_eq!(vision, hex(rows[1][2]));
    assert_eq!(gateway.snapshot().unwrap().connection_errors, 0);
    gateway.stop().unwrap();
    worker.stop().unwrap();
}
