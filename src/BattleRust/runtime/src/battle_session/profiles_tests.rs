use super::*;
use crate::battle_session::MemberKind;

fn fixture() -> Value {
    serde_json::from_slice(include_bytes!(
        "../../../../../tests/fixtures/resources/bundle-v1.json"
    ))
    .unwrap()
}

fn bundle(value: &Value) -> ResourceBundle {
    ResourceBundle::from_slice(&serde_json::to_vec(value).unwrap()).unwrap()
}

fn member() -> Member {
    Member {
        actor_id: 1,
        kind: MemberKind::Human,
        sample_source_id: 1,
        camp: 1,
        hero_id: 1101,
        weapon_id: 701,
        equipment: vec![
            action::EquipmentItem {
                slot: 1,
                weapon_id: 701,
            },
            action::EquipmentItem {
                slot: 4,
                weapon_id: 703,
            },
        ],
    }
}

#[test]
fn selected_equipment_and_fist_are_typed_without_importing_unselected_weapons() {
    let parsed = parse(&bundle(&fixture()), &member()).unwrap();
    assert_eq!(
        parsed.damage.weapons.keys().copied().collect::<Vec<_>>(),
        [701, 703, 100000]
    );
    assert_eq!(parsed.loadout.items(), member().equipment);
    assert!(parsed.loadout.profile(702).is_err());
    let main = parsed.loadout.profile(701).unwrap();
    assert_eq!(main.hand, 2);
    assert_eq!(main.attacks[0].prepare_ms, 180);
    assert_eq!(main.attacks[0].strike_ms, 600);
    assert_eq!(main.attacks[0].recovery_ms, 400);
    assert_eq!(main.guard_prepare_ms, 150);
    assert_eq!(main.guard_success_ms, 667);
    assert!(main.dash.is_none());
    let shield = parsed.loadout.profile(703).unwrap();
    assert!(shield.shield);
    assert_eq!(parsed.damage.weapons[&703].shield_max, 100);
    assert_eq!(parsed.damage.weapons[&701].hurt_ms, 400);
    let state = action::ActionState::new(1, &parsed.loadout, 701, 1).unwrap();
    assert_eq!(state.snapshot().offhand_id, 703);
    let mut fist = member();
    fist.equipment.clear();
    fist.weapon_id = FIST_ID;
    assert_eq!(
        parse(&bundle(&fixture()), &fist)
            .unwrap()
            .damage
            .weapons
            .len(),
        1
    );
}

#[test]
fn spacing_estimates_follow_equipped_weapon_traits_not_hero_identity() {
    for (weapon_id, long_pole, expected) in [(701, false, 1.1), (702, false, 1.4), (702, true, 1.7)]
    {
        let mut data = fixture();
        data["damageProfiles"]["1101"]["weapons"][weapon_id.to_string()]["longPole"] =
            long_pole.into();
        let mut selected = member();
        selected.weapon_id = weapon_id;
        selected.equipment[0].weapon_id = weapon_id;
        let parsed = parse(&bundle(&data), &selected).unwrap();
        assert_eq!(parsed.preferred_distance(weapon_id), expected);
        assert_eq!(parsed.preferred_distance(FIST_ID), 0.9);
    }
}

#[test]
fn hero_maximum_health_is_required_positive_and_wire_representable() {
    for value in [
        serde_json::json!(null),
        serde_json::json!(0),
        serde_json::json!(-1),
        serde_json::json!(1.5),
        serde_json::json!(2147483648_u32),
    ] {
        let mut data = fixture();
        data["damageProfiles"]["1101"]["maxHealth"] = value;
        assert!(ResourceBundle::from_slice(&serde_json::to_vec(&data).unwrap()).is_err());
    }
    for maximum in [1, 99, 150, i32::MAX as u32] {
        let mut data = fixture();
        data["damageProfiles"]["1101"]["maxHealth"] = maximum.into();
        assert_eq!(
            parse(&bundle(&data), &member()).unwrap().damage.max_health,
            maximum
        );
    }
}

#[test]
fn all_frozen_weapon_profiles_keep_direction_order_dash_and_shield_semantics() {
    let baseline: Value = serde_json::from_slice(include_bytes!(
        "../../../resources/data/battle-baseline-v1.json"
    ))
    .unwrap();
    let (mut count, mut shields, mut dashes) = (0, 0, 0);
    for (hero, profiles) in baseline["combatProfiles"].as_object().unwrap() {
        let hero_damage = &baseline["damageProfiles"][hero];
        let hero_traits = traits(&hero_damage["traits"]).unwrap();
        for (weapon, combat) in profiles.as_object().unwrap() {
            let id = weapon.parse().unwrap();
            let profile = action_profile(id, combat).unwrap();
            let damage = weapon_damage(
                id,
                &hero_damage["weapons"][weapon],
                combat,
                &profile,
                &hero_traits.calculation,
            )
            .unwrap();
            assert_eq!(damage.shield_max > 0, profile.shield);
            assert_eq!(
                damage.hurt_ms,
                combat["hurtMs"].as_u64().unwrap_or(400) as u16
            );
            if profile.shield {
                shields += 1;
                assert!(damage.attacks.is_empty());
            } else {
                for (direction, intent) in ATTACKS.into_iter().enumerate() {
                    assert_eq!(
                        profile.attacks[direction].enabled,
                        combat["attacks"][intent.to_string()]["enabled"]
                            .as_bool()
                            .unwrap()
                    );
                    assert_eq!(
                        u64::from(profile.attacks[direction].prepare_ms),
                        combat["attacks"][intent.to_string()]["phases"][0]
                    );
                    assert!(damage.attacks.contains_key(&intent));
                }
            }
            if profile.dash.is_some() {
                dashes += 1;
                assert!(damage.attacks.contains_key(&DASH));
            }
            assert_eq!(
                profile.dash.is_some(),
                !profile.shield && id != FIST_ID,
                "missing native dash: hero={hero} weapon={weapon}"
            );
            count += 1;
        }
    }
    assert_eq!(count, 286);
    assert_eq!(shields, 40);
    assert_eq!(dashes, 210);
}

#[test]
fn pan_feng_long_blades_accept_shift_dash_with_native_clocks_and_contact_window() {
    let baseline: Value = serde_json::from_slice(include_bytes!(
        "../../../resources/data/battle-baseline-v1.json"
    ))
    .unwrap();
    let profiles = &baseline["combatProfiles"]["2104"];
    let context = action::Context {
        now_ms: 1000,
        enabled: true,
        active: true,
        instance_matches: true,
        pose_ready: true,
        contact_at_ms: None,
    };
    for weapon_id in [1040321, 1040322] {
        let profile = action_profile(weapon_id, &profiles[weapon_id.to_string()]).unwrap();
        assert_eq!(
            profile.dash,
            Some(action::DashProfile {
                duration_ms: 1233,
                move_ms: 1133,
                speed_milli: 3500,
                contact_start_ms: 383,
                contact_end_ms: 783,
                rebound_ms: 933,
            })
        );
        let loadout = action::Loadout::new(
            vec![
                profile,
                action_profile(FIST_ID, &profiles[FIST_ID.to_string()]).unwrap(),
            ],
            vec![action::EquipmentItem { slot: 1, weapon_id }],
            FIST_ID,
        )
        .unwrap();
        let initial = action::ActionState::new(1, &loadout, weapon_id, 1).unwrap();
        let mut packet = 1_u16.to_be_bytes().to_vec();
        packet.extend_from_slice(&((1_u32 << 26) | (1 << 21)).to_be_bytes());
        packet.extend_from_slice(&DASH.to_be_bytes());
        packet.push(0);
        let command = crate::client_battle_wire::decode_action(4, &packet)
            .unwrap()
            .unwrap();
        let started = initial.apply(command, context, &loadout).unwrap();
        assert_eq!(started.state.snapshot().phase, action::Phase::Strike);
        assert_eq!(started.state.snapshot().phase_due_ms, Some(2233));
        assert!(started.events.iter().any(|event| matches!(
            event,
            action::ActionEvent::SwingStarted {
                intent: action::Intent::Dash,
                began_at_ms: 1000,
                contact_start_ms: 383,
                contact_end_ms: 783,
                ..
            }
        )));
        let animation = started
            .events
            .iter()
            .find_map(|event| match event {
                action::ActionEvent::Animation(value) => Some(value),
                _ => None,
            })
            .unwrap();
        let wire = crate::client_battle_wire::encode_animation(animation, 0).unwrap();
        assert_eq!(u16::from_be_bytes(wire.body[6..8].try_into().unwrap()), 439);
        assert_eq!(
            u16::from_be_bytes(wire.body[12..14].try_into().unwrap()),
            1233
        );
        let ended = started
            .state
            .apply(
                action::Command::Timer {
                    kind: action::TimerKind::Phase,
                    deadline_ms: 2233,
                },
                action::Context {
                    now_ms: 2233,
                    ..context
                },
                &loadout,
            )
            .unwrap();
        assert_eq!(ended.state.snapshot().phase, action::Phase::Idle);
    }
}

#[test]
fn hero_flags_and_weapon_traits_are_combined_without_losing_buff_flags() {
    let mut value = fixture();
    value["damageProfiles"]["1101"]["traits"] = serde_json::json!([
        "light",
        "riposte",
        "combo",
        "rage",
        "fortified",
        "siege_immunity"
    ]);
    value["damageProfiles"]["1101"]["weapons"]["701"]["traits"] =
        serde_json::json!(["heavy_high", "shield_break"]);
    let parsed = parse(&bundle(&value), &member()).unwrap();
    assert!(
        parsed.damage.riposte
            && parsed.damage.combo
            && parsed.damage.rage
            && parsed.damage.fortified
            && parsed.damage.siege_immunity
    );
    let traits = parsed.damage.weapons[&701].traits;
    assert!(traits.contains(damage::Trait::Light));
    assert!(traits.contains(damage::Trait::HeavyHigh));
    assert!(traits.contains(damage::Trait::ShieldBreak));
    assert!(
        !parsed.damage.weapons[&FIST_ID]
            .traits
            .contains(damage::Trait::HeavyHigh)
    );
}

#[test]
fn invalid_selected_equipment_fails_instead_of_using_another_profile() {
    let bundle = bundle(&fixture());
    let mut members = Vec::new();
    let mut changed = member();
    changed.hero_id = 9999;
    members.push(changed);
    let mut changed = member();
    changed.weapon_id = 702;
    members.push(changed);
    let mut changed = member();
    changed.weapon_id = 703;
    members.push(changed);
    let mut changed = member();
    changed.equipment[0].slot = 0;
    members.push(changed);
    let mut changed = member();
    changed.equipment[0].weapon_id = 9999;
    members.push(changed);
    let mut changed = member();
    changed.equipment[1].slot = 1;
    members.push(changed);
    let mut changed = member();
    changed.equipment[1].weapon_id = 701;
    members.push(changed);
    for member in members {
        assert!(parse(&bundle, &member).is_err(), "{member:?}");
    }
}

#[test]
fn missing_or_unsupported_damage_values_are_explicit_errors() {
    for key in [
        "peakMs",
        "weight",
        "longPole",
        "cleave",
        "attackGrade",
        "defenceGrade",
        "shieldMax",
        "traits",
        "attacks",
    ] {
        let mut value = fixture();
        value["damageProfiles"]["1101"]["weapons"]["701"]
            .as_object_mut()
            .unwrap()
            .remove(key);
        assert!(parse(&bundle(&value), &member()).is_err(), "missing {key}");
    }
    for (key, bad) in [
        ("peakMs", Value::from(0)),
        ("weight", Value::from(-1)),
        ("longPole", Value::from(1)),
        ("cleave", Value::from("unknown")),
        ("attackGrade", Value::from("SSS")),
        ("defenceGrade", Value::from("")),
        ("traits", serde_json::json!(["unknown"])),
        ("shieldMax", Value::from(1)),
    ] {
        let mut value = fixture();
        value["damageProfiles"]["1101"]["weapons"]["701"][key] = bad;
        assert!(parse(&bundle(&value), &member()).is_err(), "invalid {key}");
    }
    let mut value = fixture();
    value["damageProfiles"]["1101"]["weapons"]["701"]["attacks"]
        .as_object_mut()
        .unwrap()
        .remove("300560");
    assert!(parse(&bundle(&value), &member()).is_err());
    let mut value = fixture();
    value["damageProfiles"]["1101"]["weapons"]["701"]["attacks"]["300560"]["damageType"] =
        Value::from("unknown");
    assert!(parse(&bundle(&value), &member()).is_err());
    let mut value = fixture();
    value["damageProfiles"]["1101"]["armorType"] = Value::from("unknown");
    assert!(parse(&bundle(&value), &member()).is_err());
}
