use t7r_battle_core::{battle_protocol::BattleFrame, battle_semantics::evidence::ContactBatch};

fn fixture(text: &str) -> Vec<u8> {
    let hex = text
        .lines()
        .find(|line| line.starts_with("contacts "))
        .unwrap()
        .split_whitespace()
        .nth(1)
        .unwrap();
    let wire: Vec<_> = hex
        .as_bytes()
        .chunks_exact(2)
        .map(|v| u8::from_str_radix(std::str::from_utf8(v).unwrap(), 16).unwrap())
        .collect();
    BattleFrame::decode(&wire[4..]).unwrap().payload
}

#[test]
fn contact_region_contract_is_consistent_in_all_wire_versions() {
    for text in [
        include_str!("../../../../tests/fixtures/protocol/battle-semantic-v1.txt"),
        include_str!("../../../../tests/fixtures/protocol/battle-contacts-v2.txt"),
        include_str!("../../../../tests/fixtures/protocol/battle-contacts-v3.txt"),
    ] {
        let payload = fixture(text);
        for target in [0_u32, 2] {
            for defence in [0_u32, 701, 702] {
                for region in 0..=4_u8 {
                    let expected = region <= 3
                        && (target != 0 || defence == 0)
                        && (target == 0 || defence != 0 || region != 0);
                    let mut value = ContactBatch::decode(&payload).unwrap();
                    let event = &mut value.events[0];
                    event.target_id = target;
                    event.target_client_rid = target as u16;
                    event.target_entity = if target == 0 { 0 } else { 102 };
                    event.defence_weapon_id = defence;
                    event.region = region;
                    let target_entity = event.target_entity;
                    assert_eq!(
                        value.encode().is_ok(),
                        expected,
                        "encode {target}/{defence}/{region}"
                    );
                    let mut bytes = payload.clone();
                    bytes[56..60].copy_from_slice(&target.to_be_bytes());
                    bytes[62..64].copy_from_slice(&(target as u16).to_be_bytes());
                    bytes[72..80].copy_from_slice(&target_entity.to_be_bytes());
                    bytes[116..120].copy_from_slice(&defence.to_be_bytes());
                    bytes[120] = region;
                    let decoded = ContactBatch::decode(&bytes);
                    assert_eq!(
                        decoded.is_ok(),
                        expected,
                        "decode {target}/{defence}/{region}"
                    );
                    if expected {
                        assert_eq!(decoded.unwrap().encode().unwrap(), bytes);
                    }
                }
            }
        }
    }
}

#[test]
fn absent_body_region_has_no_body_location_multiplier() {
    use t7r_battle_core::damage::{self, Factor, Hit, Region, Rules};
    let rules = Rules {
        region_scale: [1.5, 1.2, 0.75],
        ..Rules::default()
    };
    for shield_health in [None, Some(100)] {
        let hit = Hit {
            base: 20.,
            region: Region::None,
            damage_scale: 0.5,
            shield_health,
            ..Hit::default()
        };
        let result = damage::calculate(&hit, &rules).unwrap();
        assert_eq!(
            result
                .factors
                .iter()
                .find(|(factor, _)| *factor == Factor::Region)
                .unwrap()
                .1,
            1.
        );
        assert!(!result.head);
        assert!(!hit.region.valid_for_contact(2, 0));
        assert!(hit.region.valid_for_contact(2, 701));
    }
}
