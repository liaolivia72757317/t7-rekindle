use super::*;
use crate::battle_session::{Output, training::Snapshot};

const REQUESTS: &str =
    include_str!("../../../../../tests/fixtures/protocol/training-uplink-v1.txt");

fn connect(port: u16) -> TcpStream {
    let mut stream = TcpStream::connect((Ipv4Addr::LOCALHOST, port)).unwrap();
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
    stream
}

fn read_until(stream: &mut TcpStream, command: u16, selector: u16) -> Vec<u8> {
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        assert!(
            Instant::now() < deadline,
            "missing training response {command}/{selector}"
        );
        let mut head = [0; 12];
        stream.read_exact(&mut head).unwrap();
        assert_eq!(&head[..8], &[0x55, 0x0e, 5, 0, 0, 0, 0, 12]);
        let length = u32::from_be_bytes(head[8..].try_into().unwrap()) as usize;
        assert!(length < 4096);
        let mut body = vec![0; length];
        stream.read_exact(&mut body).unwrap();
        let received = u16::from_be_bytes(body[1..3].try_into().unwrap());
        let data = &body[11..];
        assert!(
            !(received == 10 && matches!(data[..2], [0, 102] | [0, 103])),
            "training received an ordinary round timer"
        );
        assert!(
            !(received == 54 && data.starts_with(&[0, 1]) && data[14..18] == 5_i32.to_be_bytes()),
            "training received a manual hero-selection state"
        );
        assert_ne!(
            received, 68,
            "NPC menu should open through native interaction, not automatically"
        );
        if received == command && data[..2] == selector.to_be_bytes() {
            return data.to_vec();
        }
    }
}

fn stage(gateway: &mut Gateway, map: u32, epoch: u64) {
    gateway.select_match_map(map).unwrap();
    let (hero_id, weapon_id) = match map {
        6006 => (10001, 1030111),
        6009 => (10002, 1040511),
        _ => (1101, 701),
    };
    gateway
        .start_battle_staged(
            Config {
                epoch,
                started_at_ms: 1000,
                duration_ms: 120_000,
                kill_target: 1,
                seed: 42,
                members: (1..=2)
                    .map(|id| Member {
                        actor_id: id,
                        sample_source_id: 1,
                        camp: if id == 1 { 2 } else { 1 },
                        kind: if id == 1 {
                            MemberKind::Human
                        } else {
                            MemberKind::Bot
                        },
                        hero_id,
                        weapon_id,
                        equipment: vec![EquipmentItem { slot: 1, weapon_id }],
                    })
                    .collect(),
            },
            SourceBinding {
                epoch,
                source_id: 1,
                actor_rids: BTreeMap::from([(1, 1), (2, 2)]),
            },
        )
        .unwrap();
}

fn prepare_request(gateway: &mut Gateway, map: u32, epoch: u64) {
    let deadline = Instant::now() + Duration::from_secs(5);
    let request: Value = loop {
        assert!(
            Instant::now() < deadline,
            "missing prepareMatch: {:?}",
            gateway.last_error()
        );
        if let Some(bytes) = gateway.mailbox().lock().unwrap().read(4096).unwrap() {
            break serde_json::from_slice(&bytes).unwrap();
        }
        thread::sleep(Duration::from_millis(5));
    };
    assert_eq!(request["operation"], "prepareMatch");
    assert_eq!(request["mapIds"], json!([map]));
    assert!(gateway.reserve_battle_rearm().unwrap());
    gateway.stop_battle().unwrap();
    stage(gateway, map, epoch);
    let response = json!({"version":1,"epoch":request["epoch"],"requestId":request["requestId"],
        "success":true,"mapId":map});
    assert!(
        gateway
            .mailbox()
            .lock()
            .unwrap()
            .complete(&serde_json::to_vec(&response).unwrap())
            .unwrap()
    );
}

fn pump_until(gateway: &mut Gateway, predicate: impl Fn(&Output) -> bool) -> Output {
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        assert!(
            Instant::now() < deadline,
            "missing battle output: {:?}",
            gateway.last_error()
        );
        if let Some(batch) = gateway.dispatch_next_battle_output().unwrap()
            && let Some(output) = batch.into_iter().find(&predicate)
        {
            return output;
        }
        thread::sleep(Duration::from_millis(5));
    }
}

#[test]
fn training_entries_stage_native_scenes_play_choose_exit_and_reenter() {
    let resources = ResourceBundle::from_slice(include_bytes!(
        "../../../../../tests/fixtures/resources/bundle-v1.json"
    ))
    .unwrap();
    let mut gateway = Gateway::new(Arc::new(resources));
    let ports = gateway.start(1).unwrap().ports;
    stage(&mut gateway, 1028, 7);
    let mut logic = connect(ports[1]);
    complete_request(&gateway);
    read_until(&mut logic, 44, 1);
    for (index, map) in [6006, 6009, 6006].into_iter().enumerate() {
        let epoch = 8 + 2 * index as u64;
        send(&mut logic, REQUESTS, &format!("create-{}", index + 1));
        prepare_request(&mut gateway, map, epoch);
        read_until(&mut logic, 30, 2);
        read_until(&mut logic, 30, 4);
        assert!(!gateway.battle_preparation_open().unwrap());
        let mut instance = connect(ports[2]);
        let initial = read_until(&mut instance, 10, 100);
        assert_eq!(&initial[26..30], &map.to_be_bytes());
        assert_eq!(&initial[30..34], &14_i32.to_be_bytes());
        let started = u64::from_be_bytes(initial[18..26].try_into().unwrap());
        gateway.bind_battle_client_clock(epoch, -1000).unwrap();
        gateway
            .bind_battle_server_clock(epoch, started as i64 - 1000)
            .unwrap();
        send(&mut instance, REQUESTS, "camp");
        let camp = read_until(&mut instance, 35, 2);
        assert_eq!(&camp[6..10], &2_i32.to_be_bytes());
        send(&mut instance, REQUESTS, "ready");
        complete_request(&gateway);
        let vision = read_until(&mut instance, 14, 1);
        let position = vision[47..59]
            .chunks_exact(4)
            .map(|bytes| f32::from_be_bytes(bytes.try_into().unwrap()))
            .collect::<Vec<_>>();
        let expected = if map == 6006 {
            [475.051_f32, 437.252, 87.4901]
        } else {
            [477.731_f32, 433.995, 87.7147]
        };
        assert_eq!(position, expected);
        assert_eq!(
            &vision[109..113],
            &(if map == 6006 { 10001_i32 } else { 10002 }).to_be_bytes()
        );
        read_until(&mut instance, 23, 1);
        send(&mut instance, REQUESTS, "vision-list");
        let list = read_until(&mut instance, 14, 6);
        assert_eq!(
            &list[16..],
            &[
                1_u64.to_be_bytes(),
                2_u64.to_be_bytes(),
                61_u64.to_be_bytes()
            ]
            .concat()
        );
        send(&mut instance, REQUESTS, "vision-objects");
        loop {
            let object = read_until(&mut instance, 14, 1);
            if object[6..10] == 3_i32.to_be_bytes() {
                assert_eq!(&object[10..18], &61_u64.to_be_bytes());
                assert_eq!(
                    &object[object.len() - 12..object.len() - 8],
                    &18_i32.to_be_bytes()
                );
                break;
            }
        }
        send(&mut instance, REQUESTS, "load");
        send(&mut instance, REQUESTS, "container");
        let container = read_until(&mut instance, 41, 102);
        assert_eq!(&container[20..24], &1_i32.to_be_bytes());
        assert_eq!(
            &container[33..41],
            &(if map == 6006 { 10001_u64 } else { 10002 }).to_be_bytes()
        );
        send(&mut instance, REQUESTS, "enter-hero");
        read_until(&mut instance, 10, 11);
        // Native training behavior uses the first mode-supplied card automatically.
        send(&mut instance, REQUESTS, "choose");
        assert_eq!(read_until(&mut instance, 54, 51), hex("003300000000"));
        send(&mut instance, REQUESTS, "play");
        assert_eq!(read_until(&mut instance, 54, 101), hex("006500000000"));
        let entered = read_until(&mut instance, 14, 1);
        assert_eq!(
            &entered[47..59],
            &vision[47..59],
            "PLAY must keep the gate-side entry position"
        );
        assert_eq!(&entered[109..113], &vision[109..113]);
        pump_until(&mut gateway, |event| {
            matches!(
                event,
                Output::Training(Snapshot {
                    state: 0,
                    score: 0,
                    show_menu: false,
                    ..
                })
            )
        });
        let idle = read_until(&mut instance, 67, 2);
        assert_eq!(&idle[2..6], &0_i32.to_be_bytes());
        assert!(
            gateway
                .mailbox()
                .lock()
                .unwrap()
                .read(4096)
                .unwrap()
                .is_none(),
            "mode hero selection must not read or save the lobby profile"
        );
        send(
            &mut instance,
            REQUESTS,
            if index == 1 { "advanced" } else { "basic" },
        );
        pump_until(&mut gateway, |event| {
            matches!(
                event,
                Output::Training(Snapshot {
                    state: 2,
                    duration_ms: 3000,
                    score: 0,
                    ..
                })
            )
        });
        let prepare = read_until(&mut instance, 67, 2);
        assert_eq!(&prepare[2..6], &2_i32.to_be_bytes());
        pump_until(&mut gateway, |event| {
            matches!(
                event,
                Output::Training(Snapshot {
                    state: 3,
                    duration_ms: 60000,
                    ..
                })
            )
        });
        let running = read_until(&mut instance, 67, 2);
        assert_eq!(&running[2..6], &3_i32.to_be_bytes());
        send(&mut instance, REQUESTS, "logout");
        pump_until(&mut gateway, |event| matches!(event, Output::Ended(_)));
        read_until(&mut instance, 44, 3);
        send(&mut instance, REQUESTS, "quit");
        read_until(&mut instance, 55, 2);
        let deadline = Instant::now() + Duration::from_secs(5);
        while !gateway.battle_returned_to_room().unwrap() {
            assert!(Instant::now() < deadline);
            thread::sleep(Duration::from_millis(5));
        }
        assert_eq!(
            gateway.snapshot().unwrap().connection_errors,
            0,
            "{:?}",
            gateway.last_error()
        );
        drop(instance);
        gateway.stop_battle().unwrap();
        stage(&mut gateway, map, epoch + 1);
    }
    gateway.stop().unwrap();
}
