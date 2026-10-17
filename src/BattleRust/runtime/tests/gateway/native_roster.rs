use super::*;

#[test]
fn native_capacity_imports_and_settles_every_actor() {
    for (map_id, blue, red) in [(90003, 5_u32, 5_u32), (1021, 14, 18), (20012, 30, 30)] {
        let mut data: Value = serde_json::from_slice(include_bytes!(
            "../../../../../tests/fixtures/resources/bundle-v1.json"
        ))
        .unwrap();
        data["botNames"] = json!((0..60).map(|id| format!("Fixture{id}")).collect::<Vec<_>>());
        let mut gateway = Gateway::new(Arc::new(
            ResourceBundle::from_slice(&serde_json::to_vec(&data).unwrap()).unwrap(),
        ));
        let state = gateway.start(1).unwrap();
        gateway.select_match_map(map_id).unwrap();
        let (mut config, mut binding) = twenty_member_config(7, 1200);
        let template = config.members[1].clone();
        config.members = (1..=blue + red)
            .map(|actor_id| {
                let mut member = template.clone();
                member.actor_id = actor_id;
                member.kind = if actor_id == 1 {
                    MemberKind::Human
                } else {
                    MemberKind::Bot
                };
                member.camp = if (2..=red + 1).contains(&actor_id) {
                    2
                } else {
                    1
                };
                member
            })
            .collect();
        binding.actor_rids = (1..=blue + red).map(|id| (id, (id + 100) as u16)).collect();
        gateway.start_battle(config, binding.clone()).unwrap();
        gateway.bind_battle_server_clock(7, 5000).unwrap();
        let mut instance = connect_instance_on_map(state.ports[2], 101, map_id as i32);
        let choice =
            include_str!("../../../../../tests/fixtures/protocol/instance-control-uplink-v1.txt")
                .lines()
                .find(|line| line.starts_with("camp-choice "))
                .unwrap()
                .split_whitespace()
                .collect::<Vec<_>>();
        instance.write_all(&hex(choice[3])).unwrap();
        assert_eq!(&read_body(&mut instance, 35)[..2], &[0, 2]);
        let ready = include_str!(
            "../../../../../tests/fixtures/protocol/instance-actor-import-uplink-v1.txt"
        )
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
        while imported.len() < (2 * (blue + red) + 3) as usize {
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
        for actor_id in 2_u32..=blue + red {
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
        assert_eq!(imported[(2 * (blue + red) + 2) as usize].0, 23);
        assert_eq!(&imported[(2 * (blue + red) + 2) as usize].1[..2], &[0, 1]);
        let vision =
            include_str!("../../../../../tests/fixtures/protocol/native-roster-vision-v1.txt")
                .lines()
                .find(|line| line.starts_with(&format!("{} ", blue + red)))
                .unwrap()
                .split_whitespace()
                .collect::<Vec<_>>();
        instance.write_all(&hex(vision[3])).unwrap();
        let mut viewed = 0_u32;
        while viewed < blue + red {
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
        let (command, _, inactive) = read_without_startup(&mut instance);
        assert_eq!(command, 2);
        assert_eq!(&inactive[..2], &[0, 0x1f]);
        assert_eq!(&inactive[6..], &[0, 1, 0]);
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
        assert_eq!(settlement.members.len(), (blue + red) as usize);
        assert_eq!(
            settlement
                .members
                .iter()
                .map(|member| member.actor_id)
                .collect::<std::collections::BTreeSet<_>>(),
            (1..=blue + red).collect()
        );
        settlement.started_at_ms += 5000;
        settlement.ended_at_ms += 5000;
        let scores = encode_settlement_start_for_client(&settlement, &binding.actor_rids).unwrap();
        assert_eq!(scores.len(), (blue + red + 2) as usize);
        for message in scores {
            let (command, _, body) = read_without_startup(&mut instance);
            assert_eq!((command, body), (message.command, message.body));
        }
        assert_eq!(&read_body(&mut instance, 52)[..2], &[0, 1]);
        read_body(&mut instance, 10);
        assert_eq!(gateway.snapshot().unwrap().connection_errors, 0);
        gateway.stop().unwrap();
    }
}
