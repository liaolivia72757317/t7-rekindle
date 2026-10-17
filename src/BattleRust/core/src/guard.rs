//! Guard performance and reaction rules independent of client packet identifiers.

pub use crate::damage::RuleError;
use crate::damage::{Trait, Traits};

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Grade {
    F,
    E,
    D,
    C,
    B,
    A,
    S,
    SS,
}

impl Grade {
    pub fn value(self) -> u8 {
        self as u8
    }

    pub fn from_value(value: u8) -> Result<Self, RuleError> {
        [
            Self::F,
            Self::E,
            Self::D,
            Self::C,
            Self::B,
            Self::A,
            Self::S,
            Self::SS,
        ]
        .get(usize::from(value))
        .copied()
        .ok_or(RuleError::InvalidEvidence)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ArmorType {
    Light,
    Medium,
    Heavy,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AttackIntent {
    Up,
    Right,
    Down,
    Left,
    Other,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Rules {
    pub maximum: f64,
    pub recover_delay_ms: u32,
    pub recover_per_second: f64,
    pub hit_recovery: f64,
    pub reset_value: f64,
    pub consumption_base: f64,
    pub performance_cost: f64,
    pub weight_cost: f64,
    pub consumption_min: f64,
    pub consumption_max: f64,
    pub break_damage: Vec<f64>,
    pub guard_stun_ms: u32,
    pub exhaustion_ms: u32,
    pub chamber_start_ms: u32,
    pub chamber_end_ms: u32,
    pub full_charge_ms: u32,
    pub light_stun_ms: u32,
    pub medium_stun_ms: u32,
    pub knockdown_ms: u32,
}

impl Default for Rules {
    fn default() -> Self {
        Self {
            maximum: 5.0,
            recover_delay_ms: 2500,
            recover_per_second: 2.0,
            hit_recovery: 2.0,
            reset_value: 2.0,
            consumption_base: 1.0,
            performance_cost: 0.25,
            weight_cost: 0.5,
            consumption_min: 0.25,
            consumption_max: 2.5,
            break_damage: vec![0.25, 0.5, 0.75],
            guard_stun_ms: 250,
            exhaustion_ms: 1000,
            chamber_start_ms: 50,
            chamber_end_ms: 200,
            full_charge_ms: 600,
            light_stun_ms: 250,
            medium_stun_ms: 180,
            knockdown_ms: 1000,
        }
    }
}

impl Rules {
    pub fn validate(&self) -> Result<(), RuleError> {
        if ![
            self.maximum,
            self.recover_per_second,
            self.hit_recovery,
            self.reset_value,
            self.consumption_base,
            self.performance_cost,
            self.weight_cost,
            self.consumption_min,
            self.consumption_max,
        ]
        .into_iter()
        .chain(self.break_damage.iter().copied())
        .all(|v| v.is_finite() && v >= 0.0)
            || self.maximum <= 0.0
            || self.reset_value > self.maximum
            || self.consumption_min > self.consumption_max
            || self.chamber_start_ms > self.chamber_end_ms
            || self.break_damage.is_empty()
            || self.break_damage.iter().any(|value| *value > 1.0)
        {
            return Err(RuleError::InvalidRules);
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct AttackContext {
    pub attack_grade: Grade,
    pub held_ms: f64,
    pub traits: Traits,
    pub long_pole: bool,
}

pub fn attack_performance(
    context: &AttackContext,
    intent: AttackIntent,
    target_striking: bool,
    allies: u32,
    rules: &Rules,
) -> Result<Grade, RuleError> {
    rules.validate()?;
    if !context.held_ms.is_finite() || context.held_ms < 0.0 {
        return Err(RuleError::InvalidEvidence);
    }
    if allies > 0 {
        return Ok(Grade::F);
    }
    let mut value = context.attack_grade.value();
    if context.held_ms >= f64::from(rules.full_charge_ms) {
        let level = [
            Trait::HeavyLow,
            Trait::HeavyMedium,
            Trait::HeavyHigh,
            Trait::HeavySpecial,
        ]
        .into_iter()
        .enumerate()
        .filter(|(_, name)| context.traits.contains(*name))
        .map(|(index, _)| (index + 1) as u8)
        .max()
        .unwrap_or(0);
        if intent == AttackIntent::Up {
            value += level.min(3);
        } else if matches!(intent, AttackIntent::Left | AttackIntent::Right) && level == 4 {
            value += 1;
        }
    }
    if intent == AttackIntent::Down
        && context.long_pole
        && target_striking
        && context.traits.contains(Trait::Counter)
    {
        value += 1;
    }
    Grade::from_value(value.min(7))
}

pub fn chamber_window(
    defence: AttackIntent,
    attack: AttackIntent,
    elapsed_ms: f64,
    rules: &Rules,
) -> Result<bool, RuleError> {
    rules.validate()?;
    if !elapsed_ms.is_finite() {
        return Err(RuleError::InvalidEvidence);
    }
    Ok(chamber_direction_matches(defence, attack)
        && f64::from(rules.chamber_start_ms) <= elapsed_ms
        && elapsed_ms <= f64::from(rules.chamber_end_ms))
}

pub fn chamber_direction_matches(defence: AttackIntent, attack: AttackIntent) -> bool {
    matches!(
        (defence, attack),
        (AttackIntent::Up, AttackIntent::Up | AttackIntent::Down)
            | (AttackIntent::Right, AttackIntent::Left)
            | (AttackIntent::Down, AttackIntent::Down)
            | (AttackIntent::Left, AttackIntent::Right)
    )
}

pub fn damage_fraction(gap: i32, chamber: bool, rules: &Rules) -> Result<f64, RuleError> {
    rules.validate()?;
    let threshold = if chamber { 4 } else { 2 };
    Ok(if gap >= threshold {
        rules.break_damage[((gap - threshold) as usize).min(rules.break_damage.len() - 1)]
    } else {
        0.0
    })
}

pub fn consumption(
    gap: i32,
    attack_weight: f64,
    defence_weight: f64,
    rules: &Rules,
) -> Result<f64, RuleError> {
    rules.validate()?;
    if !attack_weight.is_finite()
        || !defence_weight.is_finite()
        || attack_weight <= 0.0
        || defence_weight <= 0.0
    {
        return Err(RuleError::InvalidEvidence);
    }
    let amount = rules.consumption_base
        + rules.performance_cost * f64::from(gap)
        + rules.weight_cost * (attack_weight / defence_weight - 1.0);
    if !amount.is_finite() {
        return Err(RuleError::Overflow);
    }
    Ok(amount.clamp(rules.consumption_min, rules.consumption_max))
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Reaction {
    pub interrupt: bool,
    pub movement_ms: u32,
    pub knockdown: bool,
}

pub fn body_reaction(
    performance: Grade,
    armor: ArmorType,
    airborne: bool,
    immune: bool,
    fortified: bool,
    rules: &Rules,
) -> Result<Reaction, RuleError> {
    rules.validate()?;
    let interrupt = performance.value()
        >= if fortified {
            4
        } else if armor == ArmorType::Heavy {
            1
        } else {
            0
        };
    if immune || performance < Grade::B {
        return Ok(Reaction {
            interrupt,
            ..Default::default()
        });
    }
    if airborne {
        return Ok(Reaction {
            interrupt,
            movement_ms: rules.knockdown_ms,
            knockdown: true,
        });
    }
    let movement_ms = match armor {
        ArmorType::Light => rules.light_stun_ms,
        ArmorType::Medium => rules.medium_stun_ms,
        ArmorType::Heavy => 0,
    };
    Ok(Reaction {
        interrupt,
        movement_ms,
        knockdown: false,
    })
}
