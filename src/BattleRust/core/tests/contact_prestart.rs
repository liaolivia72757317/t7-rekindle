use t7r_battle_core::battle_semantics::evidence::ContactBatch;

fn payload(name: &str) -> Vec<u8> {
    include_str!("../../../../tests/fixtures/protocol/battle-contacts-v4.txt")
        .lines()
        .find(|line| line.starts_with(&format!("{name} ")))
        .unwrap()
        .split_whitespace()
        .nth(1)
        .unwrap()
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
        .skip(40)
        .collect()
}

#[test]
fn v4_retains_prestart_target_phase_and_v3_layout() {
    for name in ["contacts", "watermark"] {
        let bytes = payload(name);
        let batch = ContactBatch::decode(&bytes).unwrap();
        assert_eq!(batch.version, 4);
        assert_eq!(bytes.len(), 52 + 296 * batch.events.len());
        assert_eq!(batch.encode().unwrap(), bytes);
        if let Some(e) = batch.events.first() {
            assert_eq!(e.target_phase_at_ms, -4999.);
            assert_eq!(e.phase_at_ms, 1000);
            assert_eq!(e.occurred_at_ms, 999.5);
            assert_eq!(e.target_observed_at_ms, Some(1002));
            assert_eq!(e.source_observed_at_ms, Some(1003));
        }
    }
}

#[test]
fn legacy_versions_still_reject_prestart_target_phase() {
    for version in 1_u16..=3 {
        let mut bytes = payload("contacts");
        bytes.truncate(52 + 280 + usize::from(version - 1) * 8);
        bytes[..2].copy_from_slice(&version.to_be_bytes());
        bytes[84..92].copy_from_slice(&990_u64.to_be_bytes());
        assert!(ContactBatch::decode(&bytes).is_err());
        bytes[92..100].copy_from_slice(&0_f64.to_be_bytes());
        assert!(ContactBatch::decode(&bytes).is_ok());
    }
    let mut batch = ContactBatch::decode(&payload("contacts")).unwrap();
    batch.version = 3;
    assert!(batch.encode().is_err());
}

#[test]
fn v4_keeps_event_and_observation_clock_validation() {
    let original = payload("contacts");
    for at in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY, 1003.] {
        let mut bytes = original.clone();
        bytes[92..100].copy_from_slice(&at.to_be_bytes());
        assert!(ContactBatch::decode(&bytes).is_err());
    }
    for at in [-0.25_f64, f64::NAN, f64::INFINITY, 1001.] {
        let mut bytes = original.clone();
        bytes[100..108].copy_from_slice(&at.to_be_bytes());
        assert!(ContactBatch::decode(&bytes).is_err());
    }
    for (offset, at) in [(84, 1004_u64), (332, 999), (340, 999)] {
        let mut bytes = original.clone();
        bytes[offset..offset + 8].copy_from_slice(&at.to_be_bytes());
        assert!(ContactBatch::decode(&bytes).is_err());
    }
    for length in 0..original.len() {
        assert!(ContactBatch::decode(&original[..length]).is_err());
    }
    let mut unknown = original.clone();
    unknown[..2].copy_from_slice(&5_u16.to_be_bytes());
    assert!(ContactBatch::decode(&unknown).is_err());
    for target in [false, true] {
        let mut batch = ContactBatch::decode(&original).unwrap();
        if target {
            batch.events[0].target_observed_at_ms = None;
        } else {
            batch.events[0].source_observed_at_ms = None;
        }
        assert!(batch.encode().is_err());
    }
}
