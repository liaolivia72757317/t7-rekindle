use serde_json::{Value, json};
use t7r_battle_core::{damage, guard};
use t7r_battle_resources::ResourceBundle;
use t7r_battle_runtime::battle_resources::BattleResources;

fn data() -> Value {
    serde_json::from_slice(include_bytes!(
        "../../../../tests/fixtures/resources/bundle-v1.json"
    ))
    .unwrap()
}

fn bundle(data: &Value) -> ResourceBundle {
    ResourceBundle::from_slice(&serde_json::to_vec(data).unwrap()).unwrap()
}

#[test]
fn prepared_rules_and_camp_candidates_match_frozen_python() {
    let resources = BattleResources::prepare(&bundle(&data())).unwrap();
    assert_eq!(
        (
            resources.rules_version(),
            resources.resource_version(),
            resources.map_id()
        ),
        (3, 7, 1028)
    );
    assert_eq!(resources.damage_rules(), &damage::Rules::default());
    assert_eq!(resources.guard_rules(), &guard::Rules::default());
    let expected: Value = serde_json::from_str(include_str!(
        "../../../../tests/fixtures/resources/prepared-v1.json"
    ))
    .unwrap();
    assert_candidates(&resources, &expected);
}

#[test]
fn frozen_luoyang_map_prewarm_matches_python_candidate_order() {
    let baseline: Value =
        serde_json::from_str(include_str!("../../resources/data/battle-baseline-v1.json")).unwrap();
    let mut data = data();
    data["map"] = baseline["map"].clone();
    let resources = BattleResources::prepare(&bundle(&data)).unwrap();
    let expected: Value = serde_json::from_str(include_str!(
        "../../../../tests/fixtures/resources/prepared-map-v1.json"
    ))
    .unwrap();
    assert_candidates(&resources, &expected);
}

#[test]
fn every_match_map_has_reachable_respawn_candidates_for_both_camps() {
    let original = bundle(&data());
    for id in t7r_battle_resources::match_maps::MAP_IDS {
        let selected = t7r_battle_resources::match_maps::select(&original, id).unwrap();
        let prepared = BattleResources::prepare(&selected).unwrap();
        let anchor = prepared.camps()[0].spawns()[0].position;
        for camp in prepared.camps() {
            assert!(
                camp.candidates()
                    .groups()
                    .iter()
                    .any(|group| !group.is_empty()),
                "map {id}"
            );
            for point in camp.spawns().iter().chain(camp.respawns()) {
                use t7r_battle_core::navigation::{DEFAULT_RADIUS, PathStatus};
                assert_eq!(
                    prepared
                        .navigation()
                        .find_path(anchor, point.position, DEFAULT_RADIUS)
                        .unwrap()
                        .status,
                    PathStatus::Ready,
                    "map {id}, point {:?}",
                    point.position
                );
            }
        }
    }
}

fn assert_candidates(resources: &BattleResources, expected: &Value) {
    for (index, camp) in resources.camps().iter().enumerate() {
        assert_eq!(camp.spawns().len(), 10);
        assert_eq!(camp.respawns().len(), if index == 0 { 14 } else { 12 });
        let groups = expected["camps"][index].as_array().unwrap();
        assert_eq!(camp.candidates().groups().len(), groups.len());
        for (actual, expected) in camp.candidates().groups().iter().zip(groups) {
            let expected = expected.as_array().unwrap();
            assert_eq!(actual.len(), expected.len());
            for (actual, expected) in actual.iter().zip(expected) {
                for (coordinate, value) in
                    actual.position.iter().zip(expected[0].as_array().unwrap())
                {
                    assert!(
                        (coordinate - value.as_f64().unwrap()).abs() <= 1e-12,
                        "camp {index}: actual {actual:?}, expected {expected}"
                    );
                }
                assert_eq!(actual.heading, expected[1].as_f64().unwrap());
            }
        }
    }
}

#[test]
fn rules_are_read_from_the_bundle_not_replaced_by_defaults() {
    let mut data = data();
    data["rules"]["damage"]["chargeMax"] = json!(1.75);
    data["rules"]["guard"]["recoverPerSecond"] = json!(3.25);
    data["rules"]["damageBuffs"]["combo"] = json!([1.75, 2345]);
    let resources = BattleResources::prepare(&bundle(&data)).unwrap();
    assert_eq!(resources.damage_rules().charge_max, 1.75);
    assert_eq!(resources.guard_rules().recover_per_second, 3.25);
    assert_eq!(resources.damage_buff_rules().combo.multiplier, 1.75);
    assert_eq!(resources.damage_buff_rules().combo.duration_ms, 2345);
}

#[test]
fn semantic_rule_failures_are_rejected_before_opening_a_session() {
    for (kind, field, invalid) in [
        ("damage", "speedMin", json!(2)),
        ("damage", "chargeDecayMs", json!(0)),
        ("damage", "phasePoints", json!([[0, 0.25], [0, 1]])),
        ("damage", "cleaveScale", json!([[1], [1], [1], [1]])),
        ("damage", "phasePoints", json!([[0, 1]])),
        ("damage", "armorScale", json!([1, 2])),
        ("damage", "light", json!(true)),
        ("guard", "recoverDelayMs", json!(2.5)),
        ("guard", "resetValue", json!(6)),
        ("guard", "breakDamage", json!([0, 0.5, 0.75])),
        ("guard", "breakDamage", json!([0.5, 0.75])),
        ("guard", "fullChargeMs", json!(0)),
        ("guard", "chamberStartMs", json!(201)),
        ("guard", "recoverPerSecond", json!(-1)),
    ] {
        let mut data = data();
        data["rules"][kind][field] = invalid;
        assert!(
            BattleResources::prepare(&bundle(&data)).is_err(),
            "{kind}.{field}"
        );
    }
    for kind in ["damage", "guard"] {
        let mut data = data();
        data["rules"][kind]["unknown"] = json!(1);
        assert!(BattleResources::prepare(&bundle(&data)).is_err());
    }
}

#[test]
fn invalid_damage_buffs_are_rejected_before_opening_a_session() {
    for invalid in [
        json!([0, 3000]),
        json!([-1, 3000]),
        json!([1.2, 0]),
        json!([1.2, -1]),
        json!([1.2, 2.5]),
        json!([1.2]),
        json!([1.2, 3000, 1]),
        json!([true, 3000]),
    ] {
        let mut data = data();
        data["rules"]["damageBuffs"]["combo"] = invalid.clone();
        assert!(
            BattleResources::prepare(&bundle(&data)).is_err(),
            "{invalid}"
        );
    }
    let mut data = data();
    data["rules"]["damageBuffs"]["unknown"] = json!([1.2, 3000]);
    assert!(BattleResources::prepare(&bundle(&data)).is_err());
    data["rules"]["damageBuffs"]
        .as_object_mut()
        .unwrap()
        .remove("unknown");
    data["rules"]["damageBuffs"]
        .as_object_mut()
        .unwrap()
        .remove("rage");
    assert!(BattleResources::prepare(&bundle(&data)).is_err());
}

#[test]
fn empty_candidate_groups_preserve_reference_order_without_fabricated_positions() {
    let mut data = data();
    for point in data["map"]["navigation"]["vertices"]
        .as_array_mut()
        .unwrap()
    {
        point[2] = json!(100);
    }
    let resources = BattleResources::prepare(&bundle(&data)).unwrap();
    assert!(
        resources
            .camps()
            .iter()
            .all(|camp| camp.candidates().groups().iter().all(Vec::is_empty))
    );
    assert_eq!(resources.camps()[0].candidates().groups().len(), 14);
    assert_eq!(resources.camps()[1].candidates().groups().len(), 12);
}
