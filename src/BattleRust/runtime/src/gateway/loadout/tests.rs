use super::{
    codec,
    profile::{Catalogue, Profile},
};
use serde_json::{Value, json};
use t7r_battle_resources::ResourceBundle;

fn catalogue() -> Catalogue {
    Catalogue::from_bundle(
        &ResourceBundle::from_slice(include_bytes!(
            "../../../../../../tests/fixtures/resources/bundle-v1.json"
        ))
        .unwrap(),
    )
    .unwrap()
}
fn data() -> Value {
    json!({"schemaVersion":1,"revision":0,"formation":[1101],"weapons":{
        "1101":[{"slot":1,"group":0,"index":0},{"slot":2,"group":2,"index":0}],
        "2102":[{"slot":1,"group":0,"index":0},{"slot":2,"group":2,"index":0}]}})
}
fn hex(text: &str) -> Vec<u8> {
    text.as_bytes()
        .chunks_exact(2)
        .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
        .collect()
}
fn int(bytes: &[u8]) -> i32 {
    i32::from_be_bytes(bytes.try_into().unwrap())
}

#[test]
fn default_lobby_encoding_matches_python_corpus() {
    let cat = catalogue();
    let profile = Profile::parse(&data(), &cat).unwrap();
    for line in include_str!("../../../../../../tests/fixtures/protocol/loadout-v1.txt")
        .lines()
        .skip(1)
        .take(6)
    {
        let row: Vec<_> = line.split_whitespace().collect();
        let body = hex(row[2]);
        let actual = match row[0] {
            "container-1" | "container-2" => {
                let (container, sequence) = codec::container_request(&body).unwrap();
                codec::containers(&profile, &cat, container, sequence).remove(0)
            }
            "container-3" => codec::empty_container(3, -17),
            "formations" => codec::formations(&profile, &cat),
            "select-1" => codec::result(8, 0),
            "select-2" => codec::result(8, super::INVALID),
            _ => unreachable!(),
        };
        assert_eq!(actual, hex(row[4].split_once(':').unwrap().1), "{}", row[0]);
    }
}

#[test]
fn container_chunking_and_unsupported_native_groups_preserve_client_layout() {
    let mut cat = catalogue();
    let mut profile = Profile::parse(&data(), &cat).unwrap();
    for id in 3000..3049 {
        cat.heroes.insert(id, cat.heroes[&1101].clone());
        profile.weapons.insert(id, profile.weapons[&1101].clone());
    }
    let replies = codec::containers(&profile, &cat, 1, 123);
    assert_eq!(replies.len(), 2);
    assert_eq!(&replies[0][10..12], &[2, 0]);
    assert_eq!(&replies[1][10..12], &[2, 1]);
    assert_eq!(int(&replies[0][20..24]), 51);
    assert_eq!(int(&replies[0][24..28]), 50);
    assert_eq!(int(&replies[1][24..28]), 1);
    assert_eq!(
        replies[0].len(),
        28 + 50 * codec::hero_card(&profile, &cat, 1101).len()
    );
    cat.heroes.get_mut(&1101).unwrap().native[3][4] = 900001;
    let card = codec::hero_card(&profile, &cat, 1101);
    let offset = 34 + 17 + (3 * 6 + 4) * 106;
    assert_eq!(card[offset], 2);
    assert_eq!(int(&card[offset + 1..offset + 5]), 900001);
}

#[test]
fn malformed_backend_profiles_fail_without_panics_or_partial_wire_output() {
    let cat = catalogue();
    let mut examples = vec![Value::Null, json!({})];
    let mut bad = data();
    bad["weapons"].as_object_mut().unwrap().remove("1101");
    bad["weapons"]["9999"] = json!([]);
    examples.push(bad);
    let mut bad = data();
    bad["revision"] = json!(u64::MAX);
    examples.push(bad);
    let mut bad = data();
    bad["revision"] = json!(1.0);
    examples.push(bad);
    let mut bad = data();
    bad["formation"] = json!([1101, 1101]);
    examples.push(bad);
    let mut bad = data();
    bad["weapons"]["1101"][0]["group"] = json!(8);
    examples.push(bad);
    let mut bad = data();
    bad["weapons"]["1101"][0]["index"] = json!(5);
    examples.push(bad);
    let mut bad = data();
    bad["weapons"]["1101"][1]["group"] = json!(0);
    examples.push(bad);
    let mut bad = data();
    bad["weapons"]["1101"][0]["extra"] = json!(0);
    examples.push(bad);
    for value in examples {
        assert!(Profile::parse(&value, &cat).is_err(), "{value}");
    }
    let mut precise = data();
    precise["revision"] = json!(9007199254740993_u64);
    assert_eq!(
        Profile::parse(&precise, &cat).unwrap().revision,
        9007199254740993
    );
}

#[test]
fn lobby_request_lengths_and_native_references_are_validated_before_indexing() {
    let cat = catalogue();
    let formation = hex("00050000000100000001000000000000044d00000001");
    let weapons = hex("000e0000000000000836000000020001000000010000038600000000");
    for size in 0..formation.len() {
        assert!(codec::decode_formation(&formation[..size], &cat).is_err());
    }
    for size in 0..weapons.len() {
        assert!(codec::decode_weapons(&weapons[..size], &cat).is_err());
    }
    assert_eq!(
        codec::decode_formation(&formation, &cat).unwrap(),
        json!({"formation":[1101]})
    );
    assert_eq!(
        codec::decode_weapons(&weapons, &cat).unwrap(),
        json!({"heroId":2102,"slots":[{"slot":1,"group":1,"index":0}]})
    );
    let mut extra = formation.clone();
    extra.push(0);
    assert!(codec::decode_formation(&extra, &cat).is_err());
    let mut wrong_reference = weapons.clone();
    wrong_reference[13] = 1;
    assert!(codec::decode_weapons(&wrong_reference, &cat).is_err());
    let mut negative = weapons;
    negative[14..16].copy_from_slice(&(-1_i16).to_be_bytes());
    assert!(codec::decode_weapons(&negative, &cat).is_err());
}
