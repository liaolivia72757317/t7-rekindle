use super::*;

#[test]
fn initial_choice_drives_entry_refresh_and_authoritative_state() {
    for (requests, hero_id, health, equipment, offhand) in [
        (&["other-before-entry"][..], 2102, 240, vec![(3, 702)], 0),
        (
            &["bound-before-entry"][..],
            1101,
            150,
            vec![(1, 701), (2, 703)],
            703,
        ),
        (
            &["other-before-entry", "bound-before-entry"][..],
            1101,
            150,
            vec![(1, 701), (2, 703)],
            703,
        ),
    ] {
        let mut data: Value = serde_json::from_slice(include_bytes!(
            "../../../../../tests/fixtures/resources/bundle-v1.json"
        ))
        .unwrap();
        data["damageProfiles"]["1101"]["maxHealth"] = 150.into();
        data["damageProfiles"]["2102"]["maxHealth"] = 240.into();
        let resources = ResourceBundle::from_slice(&serde_json::to_vec(&data).unwrap()).unwrap();
        let (mut gateway, mut stream) = loaded_instance_with_resources(None, 2, resources);
        for (index, request) in requests.iter().enumerate() {
            send(&mut stream, RETRIES, request);
            if index == 0 {
                complete_request(&gateway);
            }
            assert_eq!(read_until(&mut stream, 54, 0x33), hex("003300000000"));
            let chosen = read_until(&mut stream, 54, 0x34);
            let requested_hero: u32 = if *request == "other-before-entry" {
                2102
            } else {
                1101
            };
            assert_eq!(&chosen[10..14], &requested_hero.to_be_bytes());
        }
        assert_eq!(
            gateway.battle_clock_ms().unwrap_err().kind(),
            io::ErrorKind::WouldBlock
        );
        assert!(gateway.read_battle_outputs().unwrap().is_none());

        let ingress = gateway.instance_tx.borrow().as_ref().unwrap().clone();
        let mut expected = ingress.roster().local.clone();
        expected.hero_id = hero_id;
        expected.max_health = health;
        expected.weapon_id = equipment[0].1;
        expected.offhand_id = offhand;
        expected.equipment = equipment
            .into_iter()
            .map(|(slot, weapon_id)| EquipmentItem { slot, weapon_id })
            .collect();
        let identity = ingress.identity().unwrap().unwrap();
        let vision = scene_vision_wire::local_actor(&expected, &identity)
            .unwrap()
            .body;
        send(&mut stream, RETRIES, "enter");
        assert_eq!(read_until(&mut stream, 54, 0x65), hex("006500000000"));
        assert_eq!(read_until(&mut stream, 14, 1), vision);
        read_until(&mut stream, 10, 0x67);

        let refresh =
            include_str!("../../../../../tests/fixtures/protocol/instance-refresh-uplink-v1.txt")
                .lines()
                .find(|line| line.starts_with("objects "))
                .unwrap();
        stream
            .write_all(&hex(refresh.split_whitespace().nth(4).unwrap()))
            .unwrap();
        assert_eq!(read_until(&mut stream, 14, 1), vision);
        assert_eq!(gateway.snapshot().unwrap().pending_requests, 0);

        gateway.activate_battle(7).unwrap();
        let deadline = Instant::now() + Duration::from_secs(5);
        let state = loop {
            assert!(Instant::now() < deadline, "first state was not published");
            if let Some(outputs) = gateway.read_battle_outputs().unwrap()
                && let Some(state) = outputs.into_iter().find_map(|output| match output {
                    Output::State(state) => Some(state),
                    _ => None,
                })
            {
                break state;
            }
            thread::sleep(Duration::from_millis(5));
        };
        assert_eq!((state.mode, state.revision), (2, 1));
        let actor = state
            .actors
            .iter()
            .find(|actor| actor.actor_id == 1)
            .unwrap();
        assert_eq!(
            (
                actor.hero_id,
                actor.weapon_id,
                actor.offhand_id,
                actor.health
            ),
            (hero_id, expected.weapon_id, offhand, health)
        );
        assert_eq!((actor.camp, actor.life, actor.score), (1, 1, [0; 4]));
        assert_eq!(actor.shield_count, u8::from(offhand != 0));
        if offhand != 0 {
            assert_eq!(actor.shields[0], (offhand, 100));
        }
        assert_eq!(gateway.snapshot().unwrap().connection_errors, 0);
        gateway.stop().unwrap();
    }
}
