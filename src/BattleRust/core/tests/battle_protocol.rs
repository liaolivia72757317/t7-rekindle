use t7r_battle_core::battle_protocol::{
    Admission, AuthorityMode, BattleFrame, Capabilities, Requirements, decode_length,
};

fn requirements(mode: AuthorityMode) -> Requirements {
    Requirements {
        contract_version: 1,
        authority_mode: mode,
        rules_version: 3,
        resource_version: 7,
        map_id: 1028,
    }
}

#[test]
fn shared_wire_vectors_round_trip_without_reinterpreting_game_channels() {
    for line in include_str!("../../../../tests/fixtures/protocol/battle-v1.txt").lines() {
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let (name, hex) = line.split_once(' ').unwrap();
        let wire: Vec<u8> = hex
            .as_bytes()
            .chunks_exact(2)
            .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
            .collect();
        assert_eq!(decode_length(&wire[..4]).unwrap(), wire.len() - 4, "{name}");
        let frame = BattleFrame::decode(&wire[4..]).unwrap();
        assert_eq!(frame.encode().unwrap(), wire, "{name}");
        if name == "admit" {
            let admission = Admission::decode(&frame.payload).unwrap();
            assert_eq!(
                admission.requirements,
                requirements(AuthorityMode::ClientSampled)
            );
            assert_eq!(admission.ticket, b"fixture-ticket");
            assert_eq!(admission.encode().unwrap(), frame.payload);
        }
    }
}

#[test]
fn frame_and_payload_boundaries_are_checked_before_use() {
    assert!(decode_length(&[0xff; 4]).is_err());
    assert!(decode_length(&[0, 0, 0, 35]).is_err());
    let frame = BattleFrame {
        kind: 1,
        channel: 0,
        room_id: 1,
        match_id: 2,
        player_id: 3,
        sequence: u64::MAX,
        payload: vec![],
    };
    let wire = frame.encode().unwrap();
    for index in 0..4 {
        let mut body = wire[4..].to_vec();
        body[index] = 255;
        assert!(BattleFrame::decode(&body).is_err());
    }
    assert!(
        BattleFrame {
            kind: 4,
            ..frame.clone()
        }
        .encode()
        .is_err()
    );
    assert!(BattleFrame { kind: 15, ..frame }.encode().is_err());
    let admission = Admission {
        requirements: requirements(AuthorityMode::ClientSampled),
        ticket: b"fixture-ticket".to_vec(),
    };
    let body = admission.encode().unwrap();
    for length in 0..body.len() {
        assert!(Admission::decode(&body[..length]).is_err());
    }
    let mut trailing = body.clone();
    trailing.push(0);
    assert!(Admission::decode(&trailing).is_err());
    for index in [0, 1, 2, 3] {
        let mut invalid = body.clone();
        invalid[index] = 255;
        assert!(Admission::decode(&invalid).is_err());
    }
    assert!(
        Admission {
            ticket: vec![],
            ..admission.clone()
        }
        .encode()
        .is_err()
    );
    assert!(
        Admission {
            ticket: vec![0; 257],
            ..admission
        }
        .encode()
        .is_err()
    );
}

#[test]
fn authority_support_is_explicit_and_never_downgrades() {
    let required = requirements(AuthorityMode::ClientSampled);
    let capabilities = Capabilities::client_sampled(3, 7, 1028).unwrap();
    assert!(capabilities.supports(&required));
    assert!(!capabilities.supports(&requirements(AuthorityMode::ServerSimulated)));
    assert!(!capabilities.supports(&Requirements {
        rules_version: 4,
        ..required
    }));
    assert!(!capabilities.supports(&Requirements {
        resource_version: 8,
        ..required
    }));
    assert!(!capabilities.supports(&Requirements {
        map_id: 1029,
        ..required
    }));
    assert!(!capabilities.supports(&Requirements {
        contract_version: 2,
        ..required
    }));
    assert!(Capabilities::client_sampled(0, 7, 1028).is_err());
}
