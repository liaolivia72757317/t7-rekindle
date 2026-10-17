use super::{DamageBuffRule, DamageBuffRules, array, invalid, number};
use serde_json::Value;
use std::io;
use t7r_battle_core::{damage, guard};

pub(super) fn damage(value: &Value) -> io::Result<damage::Rules> {
    fields(
        value,
        &[
            "armorScale",
            "regionScale",
            "phasePoints",
            "chargeMax",
            "chargeTail",
            "chargeDecayMs",
            "friendlyScale",
            "cleaveScale",
            "speedMin",
            "speedMax",
            "counter",
            "light",
            "chargeMastery",
            "desperate",
            "desperateHealth",
            "assassin",
            "shieldBreaking",
        ],
    )?;
    let phases = array(&value["phasePoints"])?;
    if phases.len() < 2 {
        return Err(invalid("damage phase curve requires at least two points"));
    }
    let cleave = array(&value["cleaveScale"])?;
    if cleave.len() != 4 {
        return Err(invalid("expected four cleave levels"));
    }
    let mut cleave_scale: [Vec<f64>; 4] = std::array::from_fn(|_| Vec::new());
    for (index, row) in cleave.iter().enumerate() {
        let values = array(row)?;
        if values.len() != index + 1 {
            return Err(invalid("invalid cleave level size"));
        }
        cleave_scale[index] = values.iter().map(number).collect::<io::Result<_>>()?;
    }
    let rules = damage::Rules {
        armor_scale: fixed(&value["armorScale"])?,
        region_scale: fixed(&value["regionScale"])?,
        phase_points: phases.iter().map(fixed).collect::<io::Result<_>>()?,
        charge_max: number(&value["chargeMax"])?,
        charge_tail: number(&value["chargeTail"])?,
        charge_decay_ms: millis(&value["chargeDecayMs"])?,
        friendly_scale: fixed(&value["friendlyScale"])?,
        cleave_scale,
        speed_min: number(&value["speedMin"])?,
        speed_max: number(&value["speedMax"])?,
        counter: number(&value["counter"])?,
        light: number(&value["light"])?,
        charge_mastery: number(&value["chargeMastery"])?,
        desperate: number(&value["desperate"])?,
        desperate_health: number(&value["desperateHealth"])?,
        assassin: fixed(&value["assassin"])?,
        shield_breaking: number(&value["shieldBreaking"])?,
    };
    rules.validate().map_err(invalid)?;
    Ok(rules)
}

pub(super) fn guard(value: &Value) -> io::Result<guard::Rules> {
    fields(
        value,
        &[
            "maximum",
            "recoverDelayMs",
            "recoverPerSecond",
            "hitRecovery",
            "resetValue",
            "consumptionBase",
            "performanceCost",
            "weightCost",
            "consumptionMin",
            "consumptionMax",
            "breakDamage",
            "guardStunMs",
            "exhaustionMs",
            "chamberStartMs",
            "chamberEndMs",
            "fullChargeMs",
            "lightStunMs",
            "mediumStunMs",
            "knockdownMs",
        ],
    )?;
    let break_damage = fixed::<3>(&value["breakDamage"])?;
    if break_damage.iter().any(|n| *n <= 0.) || millis(&value["fullChargeMs"])? == 0 {
        return Err(invalid("invalid guard break scale or full charge duration"));
    }
    let rules = guard::Rules {
        maximum: number(&value["maximum"])?,
        recover_delay_ms: millis(&value["recoverDelayMs"])?,
        recover_per_second: number(&value["recoverPerSecond"])?,
        hit_recovery: number(&value["hitRecovery"])?,
        reset_value: number(&value["resetValue"])?,
        consumption_base: number(&value["consumptionBase"])?,
        performance_cost: number(&value["performanceCost"])?,
        weight_cost: number(&value["weightCost"])?,
        consumption_min: number(&value["consumptionMin"])?,
        consumption_max: number(&value["consumptionMax"])?,
        break_damage: break_damage.to_vec(),
        guard_stun_ms: millis(&value["guardStunMs"])?,
        exhaustion_ms: millis(&value["exhaustionMs"])?,
        chamber_start_ms: millis(&value["chamberStartMs"])?,
        chamber_end_ms: millis(&value["chamberEndMs"])?,
        full_charge_ms: millis(&value["fullChargeMs"])?,
        light_stun_ms: millis(&value["lightStunMs"])?,
        medium_stun_ms: millis(&value["mediumStunMs"])?,
        knockdown_ms: millis(&value["knockdownMs"])?,
    };
    rules.validate().map_err(invalid)?;
    Ok(rules)
}

fn fields(value: &Value, keys: &[&str]) -> io::Result<()> {
    let object = value
        .as_object()
        .ok_or_else(|| invalid("expected rule object"))?;
    if object.len() != keys.len() || keys.iter().any(|key| !object.contains_key(*key)) {
        return Err(invalid("unexpected rule parameter set"));
    }
    Ok(())
}

pub(super) fn damage_buffs(value: &Value) -> io::Result<DamageBuffRules> {
    fields(value, &["riposte", "combo", "rage"])?;
    let parse = |value: &Value| -> io::Result<DamageBuffRule> {
        let values = array(value)?;
        if values.len() != 2 {
            return Err(invalid("expected buff multiplier and duration"));
        }
        let multiplier = number(&values[0])?;
        let duration_ms = millis(&values[1])?;
        if multiplier <= 0. || duration_ms == 0 {
            return Err(invalid("buff multiplier and duration must be positive"));
        }
        Ok(DamageBuffRule {
            multiplier,
            duration_ms,
        })
    };
    Ok(DamageBuffRules {
        riposte: parse(&value["riposte"])?,
        combo: parse(&value["combo"])?,
        rage: parse(&value["rage"])?,
    })
}

fn fixed<const N: usize>(value: &Value) -> io::Result<[f64; N]> {
    let values = array(value)?;
    if values.len() != N {
        return Err(invalid("invalid rule array extent"));
    }
    let mut output = [0.; N];
    for (target, source) in output.iter_mut().zip(values) {
        *target = number(source)?;
    }
    Ok(output)
}

fn millis(value: &Value) -> io::Result<u32> {
    value
        .as_u64()
        .and_then(|v| u32::try_from(v).ok())
        .ok_or_else(|| invalid("expected u32 rule duration"))
}
