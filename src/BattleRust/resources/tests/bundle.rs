use serde_json::{Value, json};
use t7r_battle_resources::ResourceBundle;

const FIXTURE: &[u8] = include_bytes!("../../../../tests/fixtures/resources/bundle-v1.json");

fn invalid(change: impl FnOnce(&mut Value)) {
    let mut value: Value = serde_json::from_slice(FIXTURE).unwrap();
    change(&mut value);
    assert!(ResourceBundle::from_slice(&serde_json::to_vec(&value).unwrap()).is_err());
}

#[test]
fn loads_python_export_without_windows_or_original_resources() {
    let bundle = ResourceBundle::from_slice(FIXTURE).unwrap();
    assert_eq!(bundle.rules_version(), 3);
    assert_eq!(bundle.resource_version(), 7);
    assert_eq!(bundle.map_id(), 1028);
    assert_eq!(
        bundle.data()["catalogue"]["heroes"]
            .as_array()
            .unwrap()
            .len(),
        2
    );
    assert_eq!(
        bundle.data()["combatProfiles"]["1101"]["701"]["attacks"]["300560"]["phases"],
        json!([180, 0, 600, 400])
    );
}

#[test]
fn validates_optional_direction_availability_without_discarding_timings() {
    let mut value: Value = serde_json::from_slice(FIXTURE).unwrap();
    for (intent, attack) in value["combatProfiles"]["1101"]["701"]["attacks"]
        .as_object_mut()
        .unwrap()
    {
        attack["enabled"] = json!(intent == "300570");
    }
    assert!(ResourceBundle::from_slice(&serde_json::to_vec(&value).unwrap()).is_ok());
    for flag in [json!(null), json!(0), json!("false")] {
        invalid(|v| v["combatProfiles"]["1101"]["701"]["attacks"]["300560"]["enabled"] = flag);
    }
    invalid(|v| {
        for attack in v["combatProfiles"]["1101"]["701"]["attacks"]
            .as_object_mut()
            .unwrap()
            .values_mut()
        {
            attack["enabled"] = json!(false);
        }
    });
}

#[test]
fn rejects_invalid_versions_and_unbound_catalogue_references() {
    invalid(|v| v["schemaVersion"] = json!(2));
    invalid(|v| v["rulesVersion"] = json!(0));
    invalid(|v| v["clientVersion"] = json!("C:/private"));
    invalid(|v| v["catalogue"]["heroes"][1]["resourceId"] = json!(1101));
    invalid(|v| v["catalogue"]["heroes"][0]["groups"][0][0] = json!(999));
    invalid(|v| v["combatProfiles"]["1101"]["701"]["weaponId"] = json!(999));
    invalid(|v| {
        v["damageProfiles"].as_object_mut().unwrap().remove("1101");
    });
    invalid(|v| v["botNames"] = json!(["same", "same"]));
    invalid(|v| v["extra"] = json!(true));
}

#[test]
fn rejects_invalid_native_lobby_catalogue_before_gateway_creation() {
    invalid(|v| v["catalogue"]["heroes"][0]["nativeGroups"][0] = json!([701]));
    invalid(|v| v["catalogue"]["heroes"][0]["nativeGroups"][0][0] = json!(702));
    invalid(|v| v["catalogue"]["heroes"][0]["nativeGroups"][7][5] = json!(2147483648_u64));
    invalid(|v| v["catalogue"]["heroes"][0]["groupIds"][0] = json!(0));
    invalid(|v| v["catalogue"]["heroes"][0]["groupIds"][1] = json!(false));
    invalid(|v| v["catalogue"]["weapons"][2]["family"] = json!(12.0));
    invalid(|v| v["catalogue"]["weapons"][2]["family"] = json!(-1));
}

#[test]
fn rejects_invalid_navigation_before_any_battle_starts() {
    invalid(|v| v["map"]["navigation"]["edges"][0][0] = json!(999));
    invalid(|v| v["map"]["navigation"]["faces"][0][1] = json!(100));
    invalid(|v| v["map"]["navigation"]["edges"][0][2] = json!(1));
    invalid(|v| v["map"]["navigation"]["edges"][0][1] = json!(2));
    invalid(|v| v["map"]["spawns"]["1"][0][1] = json!(181));
    invalid(|v| v["map"]["position"][0] = json!("nan"));
    invalid(|v| v["map"]["spawns"]["1"][1] = v["map"]["spawns"]["1"][0].clone());
    assert!(ResourceBundle::from_slice(b"{}").is_err());
    let mut trailing = FIXTURE.to_vec();
    trailing.extend_from_slice(b"{}");
    assert!(ResourceBundle::from_slice(&trailing).is_err());
}

#[test]
fn rejects_changed_action_windows_and_movement_profiles() {
    invalid(|v| {
        v["combatProfiles"]["1101"]["701"]["attacks"]["300560"]["contactEndMs"] = json!(601)
    });
    invalid(|v| v["combatProfiles"]["1101"]["701"]["attacks"]["300560"]["phases"][1] = json!(1));
    invalid(|v| v["combatProfiles"]["1101"]["701"]["movementAnimation"]["forwardSlow"] = json!(0));
    invalid(|v| v["combatProfiles"]["1101"]["701"]["dashAttack"] = json!({"enabled": true}));
    invalid(|v| v["combatProfiles"]["1101"]["701"]["hurtMs"] = json!(0));
}
