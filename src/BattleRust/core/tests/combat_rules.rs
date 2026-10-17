use serde_json::{Value, json};
use t7r_battle_core::{damage, guard};

#[test]
fn python_damage_and_guard_corpus_matches() {
    let rows = include_str!("../../../../tests/fixtures/rules/combat-v1.jsonl");
    for (index, line) in rows.lines().enumerate() {
        let row: Value = serde_json::from_str(line).unwrap();
        let input = &row["inputs"];
        let damage_rules = damage_rules(&row["rules"]);
        let guard_rules = guard_rules(&row["rules"]);
        let actual = match row["kind"].as_str().unwrap() {
            "damage" => {
                let result = damage::calculate(&hit(input), &damage_rules).unwrap();
                json!({"hpDamage": result.hp_damage, "shieldDamage": result.shield_damage,
                    "head": result.head, "decay": result.decay, "interrupt": result.interrupt,
                    "movementMs": result.movement_ms, "knockdown": result.knockdown,
                    "events": result.events.into_iter().map(event_name).collect::<Vec<_>>(),
                    "factors": result.factors.into_iter().map(|(factor, value)|
                        json!([factor_name(factor), value])).collect::<Vec<_>>()})
            }
            "charge" => json!(
                damage::charge_multiplier(
                    number(input, "heldMs"),
                    number(input, "peakMs"),
                    &damage_rules
                )
                .unwrap()
            ),
            "phase" => {
                json!(damage::phase_multiplier(number(input, "phase"), &damage_rules).unwrap())
            }
            "speed" => json!(
                damage::speed_multiplier(
                    vector(&input["source"]),
                    vector(&input["target"]),
                    vector(&input["weapon"]),
                    boolean(input, "continuous"),
                    &damage_rules
                )
                .unwrap()
            ),
            "performance" => {
                let context = &input["context"];
                let context = guard::AttackContext {
                    attack_grade: grade(context["attackGrade"].as_str().unwrap()),
                    held_ms: number(context, "heldMs"),
                    traits: traits(&context["traits"]),
                    long_pole: boolean(context, "longPole"),
                };
                json!(
                    guard::attack_performance(
                        &context,
                        intent(&input["intent"]),
                        boolean(input, "targetStriking"),
                        input["allies"].as_u64().unwrap() as u32,
                        &guard_rules
                    )
                    .unwrap()
                    .value()
                )
            }
            "fraction" => json!(
                guard::damage_fraction(
                    input["gap"].as_i64().unwrap() as i32,
                    boolean(input, "chamber"),
                    &guard_rules
                )
                .unwrap()
            ),
            "consumption" => json!(
                guard::consumption(
                    input["gap"].as_i64().unwrap() as i32,
                    number(input, "attackWeight"),
                    number(input, "defenceWeight"),
                    &guard_rules
                )
                .unwrap()
            ),
            "chamber" => json!(
                guard::chamber_window(
                    intent(&input["defence"]),
                    intent(&input["attack"]),
                    number(input, "elapsedMs"),
                    &guard_rules
                )
                .unwrap()
            ),
            "reaction" => {
                let reaction = guard::body_reaction(
                    guard::Grade::from_value(input["performance"].as_u64().unwrap() as u8).unwrap(),
                    match input["armorType"].as_str().unwrap() {
                        "light" => guard::ArmorType::Light,
                        "medium" => guard::ArmorType::Medium,
                        "heavy" => guard::ArmorType::Heavy,
                        _ => panic!("unknown armor"),
                    },
                    boolean(input, "airborne"),
                    boolean(input, "immune"),
                    boolean(input, "fortified"),
                    &guard_rules,
                )
                .unwrap();
                json!({"interrupt": reaction.interrupt, "movementMs": reaction.movement_ms, "knockdown": reaction.knockdown})
            }
            _ => panic!("unknown corpus kind"),
        };
        assert_json_matches(&actual, &row["expected"], index + 1);
    }
    assert_eq!(rows.lines().count(), 1086);
}

fn assert_json_matches(actual: &Value, expected: &Value, line: usize) {
    match (actual, expected) {
        (Value::Number(a), Value::Number(b)) if a.is_f64() || b.is_f64() => {
            let (a, b) = (a.as_f64().unwrap(), b.as_f64().unwrap());
            assert!(
                (a - b).abs() <= b.abs().max(1.0) * 1e-12,
                "line {line}: {a} != {b}"
            );
        }
        (Value::Array(a), Value::Array(b)) => {
            assert_eq!(a.len(), b.len(), "line {line}");
            for (a, b) in a.iter().zip(b) {
                assert_json_matches(a, b, line);
            }
        }
        (Value::Object(a), Value::Object(b)) => {
            assert_eq!(a.len(), b.len(), "line {line}");
            for (key, value) in a {
                assert_json_matches(value, &b[key], line);
            }
        }
        _ => assert_eq!(actual, expected, "line {line}"),
    }
}

fn hit(input: &Value) -> damage::Hit {
    let mut hit = damage::Hit {
        base: 100.0,
        ..Default::default()
    };
    macro_rules! field {
        ($key:literal, $field:ident, $method:ident) => {
            if let Some(value) = input.get($key) {
                hit.$field = value.$method().unwrap() as _;
            }
        };
    }
    field!("base", base, as_f64);
    field!("health", health, as_u64);
    field!("armor", armor, as_f64);
    field!("heldMs", held_ms, as_f64);
    field!("peakMs", peak_ms, as_f64);
    field!("phase", phase, as_f64);
    field!("continuous", continuous, as_bool);
    field!("buffMultiplier", buff_multiplier, as_f64);
    field!("healthRatio", health_ratio, as_f64);
    field!("airborne", airborne, as_bool);
    field!("targetAttacking", target_attacking, as_bool);
    field!("longPole", long_pole, as_bool);
    field!("downAttack", down_attack, as_bool);
    field!("rear", rear, as_bool);
    field!("friendly", friendly, as_bool);
    field!("allies", allies, as_u64);
    field!("enemyIndex", enemy_index, as_u64);
    field!("damageScale", damage_scale, as_f64);
    if let Some(value) = input.get("damageType") {
        hit.damage_type = match value.as_str().unwrap() {
            "slash" => damage::DamageType::Slash,
            "pierce" => damage::DamageType::Pierce,
            "blunt" => damage::DamageType::Blunt,
            _ => panic!("unknown damage type"),
        };
    }
    if let Some(value) = input.get("region") {
        hit.region = match value.as_str().unwrap() {
            "head" => damage::Region::Head,
            "legs" => damage::Region::Legs,
            _ => damage::Region::Torso,
        };
    }
    if let Some(value) = input.get("cleave") {
        hit.cleave = match value.as_str().unwrap() {
            "none" => damage::Cleave::None,
            "low" => damage::Cleave::Low,
            "medium" => damage::Cleave::Medium,
            "high" => damage::Cleave::High,
            _ => panic!("unknown cleave"),
        };
    }
    if let Some(value) = input.get("shieldHealth") {
        hit.shield_health = Some(value.as_u64().unwrap() as u32);
    }
    if let Some(value) = input.get("traits") {
        hit.traits = traits(value);
    }
    if let Some(value) = input.get("sourceVelocity") {
        hit.source_velocity = vector(value);
    }
    if let Some(value) = input.get("targetVelocity") {
        hit.target_velocity = vector(value);
    }
    if let Some(value) = input.get("weaponVelocity") {
        hit.weapon_velocity = vector(value);
    }
    hit
}

fn damage_rules(value: &Value) -> damage::Rules {
    let mut rules = damage::Rules::default();
    macro_rules! field {
        ($key:literal, $field:ident, $method:ident) => {
            if let Some(value) = value.get($key) {
                rules.$field = value.$method().unwrap() as _;
            }
        };
    }
    field!("chargeMax", charge_max, as_f64);
    field!("chargeTail", charge_tail, as_f64);
    field!("chargeDecayMs", charge_decay_ms, as_u64);
    field!("speedMin", speed_min, as_f64);
    field!("speedMax", speed_max, as_f64);
    field!("counter", counter, as_f64);
    field!("light", light, as_f64);
    field!("chargeMastery", charge_mastery, as_f64);
    field!("desperate", desperate, as_f64);
    field!("desperateHealth", desperate_health, as_f64);
    field!("shieldBreaking", shield_breaking, as_f64);
    if let Some(v) = value.get("armorScale") {
        rules.armor_scale = vector(v);
    }
    if let Some(v) = value.get("regionScale") {
        rules.region_scale = vector(v);
    }
    if let Some(v) = value.get("friendlyScale") {
        rules.friendly_scale = vector(v);
    }
    if let Some(v) = value.get("assassin") {
        rules.assassin = vector(v);
    }
    if let Some(v) = value.get("phasePoints") {
        rules.phase_points = v
            .as_array()
            .unwrap()
            .iter()
            .map(|point| [point[0].as_f64().unwrap(), point[1].as_f64().unwrap()])
            .collect();
    }
    if let Some(v) = value.get("cleaveScale") {
        rules.cleave_scale = std::array::from_fn(|i| {
            v[i].as_array()
                .unwrap()
                .iter()
                .map(|v| v.as_f64().unwrap())
                .collect()
        });
    }
    rules
}

fn guard_rules(value: &Value) -> guard::Rules {
    let mut rules = guard::Rules::default();
    macro_rules! field {
        ($key:literal, $field:ident, $method:ident) => {
            if let Some(value) = value.get($key) {
                rules.$field = value.$method().unwrap() as _;
            }
        };
    }
    field!("consumptionBase", consumption_base, as_f64);
    field!("performanceCost", performance_cost, as_f64);
    field!("weightCost", weight_cost, as_f64);
    field!("consumptionMin", consumption_min, as_f64);
    field!("consumptionMax", consumption_max, as_f64);
    field!("chamberStartMs", chamber_start_ms, as_u64);
    field!("chamberEndMs", chamber_end_ms, as_u64);
    field!("fullChargeMs", full_charge_ms, as_u64);
    field!("lightStunMs", light_stun_ms, as_u64);
    field!("mediumStunMs", medium_stun_ms, as_u64);
    field!("knockdownMs", knockdown_ms, as_u64);
    if let Some(value) = value.get("breakDamage") {
        rules.break_damage = value
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_f64().unwrap())
            .collect();
    }
    rules
}

fn number(value: &Value, key: &str) -> f64 {
    value[key].as_f64().unwrap()
}
fn boolean(value: &Value, key: &str) -> bool {
    value[key].as_bool().unwrap()
}
fn vector(value: &Value) -> [f64; 3] {
    std::array::from_fn(|index| value[index].as_f64().unwrap())
}

fn intent(value: &Value) -> guard::AttackIntent {
    match value.as_u64().unwrap() {
        300560 => guard::AttackIntent::Up,
        300550 => guard::AttackIntent::Right,
        300570 => guard::AttackIntent::Down,
        300540 => guard::AttackIntent::Left,
        _ => guard::AttackIntent::Other,
    }
}

fn grade(value: &str) -> guard::Grade {
    guard::Grade::from_value(
        ["F", "E", "D", "C", "B", "A", "S", "SS"]
            .iter()
            .position(|v| *v == value)
            .unwrap() as u8,
    )
    .unwrap()
}

fn traits(value: &Value) -> damage::Traits {
    value
        .as_array()
        .unwrap()
        .iter()
        .map(|v| match v.as_str().unwrap() {
            "counter" => damage::Trait::Counter,
            "light" => damage::Trait::Light,
            "charge_mastery" => damage::Trait::ChargeMastery,
            "desperate" => damage::Trait::Desperate,
            "assassin_low" => damage::Trait::AssassinLow,
            "assassin_medium" => damage::Trait::AssassinMedium,
            "assassin_high" => damage::Trait::AssassinHigh,
            "shield_break" => damage::Trait::ShieldBreak,
            "heavy_low" => damage::Trait::HeavyLow,
            "heavy_medium" => damage::Trait::HeavyMedium,
            "heavy_high" => damage::Trait::HeavyHigh,
            "heavy_special" => damage::Trait::HeavySpecial,
            _ => panic!("unknown trait"),
        })
        .collect()
}

fn event_name(value: damage::Event) -> &'static str {
    match value {
        damage::Event::Hit => "hit",
        damage::Event::Kill => "kill",
        damage::Event::Block => "block",
        damage::Event::ShieldBreak => "shield-break",
    }
}

fn factor_name(value: damage::Factor) -> &'static str {
    match value {
        damage::Factor::Base => "base",
        damage::Factor::Armor => "armor",
        damage::Factor::Region => "region",
        damage::Factor::Charge => "charge",
        damage::Factor::Phase => "phase",
        damage::Factor::Speed => "speed",
        damage::Factor::Traits => "traits",
        damage::Factor::Friendly => "friendly",
        damage::Factor::Cleave => "cleave",
        damage::Factor::ShieldBreaking => "shieldBreaking",
        damage::Factor::Guard => "guard",
    }
}
