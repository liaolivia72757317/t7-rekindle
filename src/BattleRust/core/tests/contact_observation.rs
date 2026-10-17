use t7r_battle_core::battle_semantics::evidence::ContactBatch;

fn payload(name: &str) -> Vec<u8> {
    let row = include_str!("../../../../tests/fixtures/protocol/battle-contacts-v2.txt")
        .lines()
        .find(|line| line.starts_with(&format!("{name} ")))
        .unwrap();
    row.split_whitespace()
        .nth(1)
        .unwrap()
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
        .skip(40)
        .collect()
}

#[test]
fn contact_v2_preserves_observation_after_collision_and_watermark() {
    for name in ["contacts", "watermark"] {
        let bytes = payload(name);
        let decoded = ContactBatch::decode(&bytes).unwrap();
        assert_eq!(decoded.encode().unwrap(), bytes);
        assert_eq!(decoded.version, 2);
        if let Some(event) = decoded.events.first() {
            assert_eq!(event.occurred_at_ms, 999.5);
            assert_eq!(event.target_phase_at_ms, 1000.5);
            assert_eq!(event.target_observed_at_ms, Some(1002));
            assert!(event.target_observed_at_ms.unwrap() > decoded.stamp.at_ms);
            let mut legacy = bytes[..bytes.len() - 8].to_vec();
            legacy[..2].copy_from_slice(&1_u16.to_be_bytes());
            assert!(ContactBatch::decode(&legacy).is_err());
        }
    }
}

#[test]
fn contact_v2_rejects_impossible_observation_and_unknown_versions() {
    for observed in [0_u64, 999, 1000] {
        let mut bytes = payload("contacts");
        bytes[332..340].copy_from_slice(&observed.to_be_bytes());
        assert!(ContactBatch::decode(&bytes).is_err());
    }
    for version in [1_u16, 5] {
        let mut bytes = payload("contacts");
        bytes[..2].copy_from_slice(&version.to_be_bytes());
        assert!(ContactBatch::decode(&bytes).is_err());
    }
}

fn v3_payload(name: &str) -> Vec<u8> {
    let row = include_str!("../../../../tests/fixtures/protocol/battle-contacts-v3.txt")
        .lines()
        .find(|line| line.starts_with(&format!("{name} ")))
        .unwrap();
    row.split_whitespace()
        .nth(1)
        .unwrap()
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
        .skip(40)
        .collect()
}

#[test]
fn contact_v3_accepts_source_phase_after_collision_without_rewriting_time() {
    for name in ["contacts", "watermark"] {
        let bytes = v3_payload(name);
        let decoded = ContactBatch::decode(&bytes).unwrap();
        assert_eq!(decoded.version, 3);
        if let Some(e) = decoded.events.first() {
            assert_eq!(e.phase_at_ms, 1000);
            assert_eq!(e.occurred_at_ms, 999.5);
            assert_eq!(e.source_observed_at_ms, Some(1003));
            assert_eq!(e.target_observed_at_ms, Some(1002));
            let mut missing = decoded.clone();
            missing.events[0].source_observed_at_ms = None;
            assert!(missing.encode().is_err());
        }
        assert_eq!(decoded.encode().unwrap(), bytes);
    }
}

#[test]
fn contact_v3_rejects_impossible_source_observation_and_preserves_v2_validation() {
    for at in [0_u64, 999] {
        let mut bytes = v3_payload("contacts");
        bytes[340..348].copy_from_slice(&at.to_be_bytes());
        assert!(ContactBatch::decode(&bytes).is_err());
    }
    let bytes = v3_payload("contacts");
    let mut old = bytes[..bytes.len() - 8].to_vec();
    old[..2].copy_from_slice(&2_u16.to_be_bytes());
    assert!(ContactBatch::decode(&old).is_err());
    for len in 0..bytes.len() {
        assert!(ContactBatch::decode(&bytes[..len]).is_err());
    }
}
