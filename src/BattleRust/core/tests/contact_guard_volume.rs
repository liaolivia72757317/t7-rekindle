use t7r_battle_core::battle_semantics::evidence::ContactBatch;

fn batch() -> ContactBatch {
    let line = include_str!("../../../../tests/fixtures/protocol/battle-contacts-v5.txt")
        .lines()
        .find(|line| line.starts_with("contacts "))
        .unwrap();
    let bytes: Vec<_> = line
        .split_whitespace()
        .nth(1)
        .unwrap()
        .as_bytes()
        .chunks_exact(2)
        .map(|v| u8::from_str_radix(std::str::from_utf8(v).unwrap(), 16).unwrap())
        .skip(40)
        .collect();
    let mut batch = ContactBatch::decode(&bytes).unwrap();
    batch.version = 5;
    let event = &mut batch.events[0];
    event.flags |= 8;
    event.region = 0;
    event.body_index = -1;
    event.defence_weapon_id = 701;
    event.target_intent = 5;
    batch
}

#[test]
fn guard_volume_v5_preserves_layout_clock_and_explicit_origin() {
    let batch = batch();
    let bytes = batch.encode().unwrap();
    assert_eq!(bytes.len(), 52 + 296);
    assert_eq!(ContactBatch::decode(&bytes).unwrap(), batch);
    assert_eq!(batch.events[0].target_phase_at_ms, -4999.);
    let mut legacy = batch.clone();
    legacy.version = 4;
    assert!(legacy.encode().is_err());
    legacy.events[0].flags &= !8;
    assert!(legacy.encode().is_ok());
}

#[test]
fn guard_volume_v5_rejects_body_scene_shield_and_unknown_flags() {
    for field in 0..6 {
        let mut batch = batch();
        let event = &mut batch.events[0];
        match field {
            0 => event.defence_weapon_id = 0,
            1 => event.region = 2,
            2 => event.body_index = 0,
            3 => event.target_intent = 9,
            4 => event.flags |= 16,
            _ => {
                event.target_id = 0;
                event.target_client_rid = 0;
            }
        }
        assert!(batch.encode().is_err(), "field {field}");
    }
}
