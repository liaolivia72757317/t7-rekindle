//! Deterministic melee adjudication over sampled evidence; no movement simulation.

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RuleError {
    InvalidEvidence,
    InvalidRules,
    Overflow,
}

impl std::fmt::Display for RuleError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(match self {
            Self::InvalidEvidence => "invalid combat evidence",
            Self::InvalidRules => "invalid combat rules",
            Self::Overflow => "combat calculation overflow",
        })
    }
}

impl std::error::Error for RuleError {}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DamageType {
    Slash,
    Pierce,
    Blunt,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Region {
    Head,
    Torso,
    Legs,
    /// A weapon or scene contact has no observed body region.
    None,
}

impl Region {
    pub fn from_contact_code(
        code: u8,
        target_id: u32,
        defence_weapon_id: u32,
    ) -> Result<Self, RuleError> {
        let region = match code {
            0 => Self::None,
            1 => Self::Head,
            2 => Self::Torso,
            3 => Self::Legs,
            _ => return Err(RuleError::InvalidEvidence),
        };
        if !region.valid_for_contact(target_id, defence_weapon_id) {
            return Err(RuleError::InvalidEvidence);
        }
        Ok(region)
    }

    pub fn valid_for_contact(self, target_id: u32, defence_weapon_id: u32) -> bool {
        (target_id != 0 || defence_weapon_id == 0)
            && (target_id == 0 || defence_weapon_id != 0 || self != Self::None)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Cleave {
    None,
    Low,
    Medium,
    High,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Trait {
    Counter,
    Light,
    ChargeMastery,
    Desperate,
    AssassinLow,
    AssassinMedium,
    AssassinHigh,
    ShieldBreak,
    HeavyLow,
    HeavyMedium,
    HeavyHigh,
    HeavySpecial,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Traits(u16);

impl Traits {
    pub fn contains(self, value: Trait) -> bool {
        self.0 & (1 << value as u8) != 0
    }
}

impl FromIterator<Trait> for Traits {
    fn from_iter<T: IntoIterator<Item = Trait>>(values: T) -> Self {
        Self(
            values
                .into_iter()
                .fold(0, |bits, value| bits | (1 << value as u8)),
        )
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Rules {
    pub armor_scale: [f64; 3],
    pub region_scale: [f64; 3],
    pub phase_points: Vec<[f64; 2]>,
    pub charge_max: f64,
    pub charge_tail: f64,
    pub charge_decay_ms: u32,
    pub friendly_scale: [f64; 3],
    pub cleave_scale: [Vec<f64>; 4],
    pub speed_min: f64,
    pub speed_max: f64,
    pub counter: f64,
    pub light: f64,
    pub charge_mastery: f64,
    pub desperate: f64,
    pub desperate_health: f64,
    pub assassin: [f64; 3],
    pub shield_breaking: f64,
}

impl Default for Rules {
    fn default() -> Self {
        Self {
            armor_scale: [1.0, 0.6, 0.35],
            region_scale: [1.5, 1.0, 0.75],
            phase_points: vec![[0.0, 0.25], [0.35, 1.0], [0.65, 1.0], [1.0, 0.25]],
            charge_max: 1.5,
            charge_tail: 1.1,
            charge_decay_ms: 400,
            friendly_scale: [1.0, 0.4, 0.05],
            cleave_scale: [
                vec![1.0],
                vec![1.0, 0.5],
                vec![1.0, 0.65, 0.4],
                vec![1.0, 0.8, 0.6, 0.4],
            ],
            speed_min: 0.5,
            speed_max: 1.5,
            counter: 1.2,
            light: 1.15,
            charge_mastery: 1.25,
            desperate: 1.2,
            desperate_health: 0.3,
            assassin: [1.1, 1.2, 1.3],
            shield_breaking: 1.5,
        }
    }
}

impl Rules {
    pub fn validate(&self) -> Result<(), RuleError> {
        let scalar = [
            self.charge_max,
            self.charge_tail,
            self.speed_min,
            self.speed_max,
            self.counter,
            self.light,
            self.charge_mastery,
            self.desperate,
            self.desperate_health,
            self.shield_breaking,
        ];
        if !scalar
            .into_iter()
            .chain(self.armor_scale)
            .chain(self.region_scale)
            .chain(self.friendly_scale)
            .chain(self.assassin)
            .chain(self.phase_points.iter().flatten().copied())
            .chain(self.cleave_scale.iter().flatten().copied())
            .all(nonnegative)
            || self.speed_min > self.speed_max
            || self.desperate_health > 1.0
            || self.charge_decay_ms == 0
            || self.phase_points.is_empty()
            || self
                .phase_points
                .windows(2)
                .any(|pair| pair[0][0] >= pair[1][0])
            || self.cleave_scale.iter().any(Vec::is_empty)
        {
            return Err(RuleError::InvalidRules);
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Hit {
    pub base: f64,
    pub damage_type: DamageType,
    pub health: u32,
    pub armor: f64,
    pub region: Region,
    pub held_ms: f64,
    pub peak_ms: f64,
    pub phase: f64,
    pub source_velocity: [f64; 3],
    pub target_velocity: [f64; 3],
    pub weapon_velocity: [f64; 3],
    pub continuous: bool,
    pub traits: Traits,
    pub buff_multiplier: f64,
    pub health_ratio: f64,
    pub airborne: bool,
    pub target_attacking: bool,
    pub long_pole: bool,
    pub down_attack: bool,
    pub rear: bool,
    pub friendly: bool,
    pub allies: u32,
    pub enemy_index: usize,
    pub cleave: Cleave,
    pub shield_health: Option<u32>,
    pub damage_scale: f64,
}

impl Default for Hit {
    fn default() -> Self {
        Self {
            base: 0.0,
            damage_type: DamageType::Slash,
            health: 100,
            armor: 0.0,
            region: Region::Torso,
            held_ms: 0.0,
            peak_ms: 600.0,
            phase: 0.5,
            source_velocity: [0.0; 3],
            target_velocity: [0.0; 3],
            weapon_velocity: [0.0; 3],
            continuous: false,
            traits: Traits::default(),
            buff_multiplier: 1.0,
            health_ratio: 1.0,
            airborne: false,
            target_attacking: false,
            long_pole: false,
            down_attack: false,
            rear: false,
            friendly: false,
            allies: 0,
            enemy_index: 0,
            cleave: Cleave::None,
            shield_health: None,
            damage_scale: 1.0,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Event {
    Hit,
    Kill,
    Block,
    ShieldBreak,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Factor {
    Base,
    Armor,
    Region,
    Charge,
    Phase,
    Speed,
    Traits,
    Friendly,
    Cleave,
    ShieldBreaking,
    Guard,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct DamageResult {
    pub hp_damage: u32,
    pub shield_damage: u32,
    pub head: bool,
    pub decay: bool,
    pub interrupt: bool,
    pub events: Vec<Event>,
    pub factors: Vec<(Factor, f64)>,
    pub movement_ms: u32,
    pub knockdown: bool,
}

pub fn calculate(hit: &Hit, rules: &Rules) -> Result<DamageResult, RuleError> {
    rules.validate()?;
    if ![
        hit.base,
        hit.armor,
        hit.held_ms,
        hit.peak_ms,
        hit.phase,
        hit.buff_multiplier,
        hit.health_ratio,
        hit.damage_scale,
    ]
    .into_iter()
    .all(nonnegative)
        || hit.phase > 1.0
        || hit.health_ratio > 1.0
        || hit.peak_ms == 0.0
        || !hit
            .source_velocity
            .into_iter()
            .chain(hit.target_velocity)
            .chain(hit.weapon_velocity)
            .all(f64::is_finite)
    {
        return Err(RuleError::InvalidEvidence);
    }
    let cleave = &rules.cleave_scale[hit.cleave as usize];
    if hit.friendly || hit.enemy_index >= cleave.len() || hit.health == 0 {
        return Ok(DamageResult::default());
    }
    let mut charge = charge_unchecked(hit.held_ms, hit.peak_ms, rules);
    if hit.traits.contains(Trait::ChargeMastery) {
        charge = 1.0 + (charge - 1.0) * rules.charge_mastery;
    }
    let mut trait_scale = hit.buff_multiplier;
    if hit.traits.contains(Trait::Counter)
        && hit.long_pole
        && hit.down_attack
        && hit.target_attacking
    {
        trait_scale *= rules.counter;
    }
    if hit.traits.contains(Trait::Light) && hit.airborne {
        trait_scale *= rules.light;
    }
    if hit.traits.contains(Trait::Desperate) && hit.health_ratio <= rules.desperate_health {
        trait_scale *= rules.desperate;
    }
    if hit.rear {
        for (index, name) in [
            Trait::AssassinLow,
            Trait::AssassinMedium,
            Trait::AssassinHigh,
        ]
        .into_iter()
        .enumerate()
        {
            if hit.traits.contains(name) {
                trait_scale *= rules.assassin[index];
            }
        }
    }
    let shield = hit.shield_health.is_some();
    let factors = vec![
        (Factor::Base, hit.base),
        (
            Factor::Armor,
            if shield {
                1.0
            } else {
                100.0 / (100.0 + hit.armor * rules.armor_scale[hit.damage_type as usize])
            },
        ),
        (
            Factor::Region,
            if shield || hit.region == Region::None {
                1.0
            } else {
                rules.region_scale[hit.region as usize]
            },
        ),
        (Factor::Charge, charge),
        (Factor::Phase, interpolate(&rules.phase_points, hit.phase)),
        (
            Factor::Speed,
            speed_unchecked(
                hit.source_velocity,
                hit.target_velocity,
                hit.weapon_velocity,
                hit.continuous,
                rules,
            )?,
        ),
        (Factor::Traits, trait_scale),
        (
            Factor::Friendly,
            rules.friendly_scale[hit.allies.min(2) as usize],
        ),
        (Factor::Cleave, cleave[hit.enemy_index]),
        (
            Factor::ShieldBreaking,
            if shield && hit.traits.contains(Trait::ShieldBreak) {
                rules.shield_breaking
            } else {
                1.0
            },
        ),
        (Factor::Guard, hit.damage_scale),
    ];
    if factors.iter().any(|(_, value)| !value.is_finite()) {
        return Err(RuleError::Overflow);
    }
    // Preserve Python math.prod's left-to-right multiplication and half-up rounding.
    let product = factors
        .iter()
        .fold(1.0, |amount, (_, factor)| amount * factor);
    if !product.is_finite() {
        return Err(RuleError::Overflow);
    }
    let amount = (product + 0.5).floor().max(1.0);
    if let Some(health) = hit.shield_health {
        let loss = amount.min(f64::from(health)) as u32;
        let mut events = vec![Event::Block];
        if loss == health {
            events.push(Event::ShieldBreak);
        }
        Ok(DamageResult {
            shield_damage: loss,
            events,
            factors,
            ..Default::default()
        })
    } else {
        let loss = amount.min(f64::from(hit.health)) as u32;
        let mut events = vec![Event::Hit];
        if loss == hit.health {
            events.push(Event::Kill);
        }
        Ok(DamageResult {
            hp_damage: loss,
            head: hit.region == Region::Head,
            decay: hit.allies > 0,
            interrupt: hit.allies == 0,
            events,
            factors,
            ..Default::default()
        })
    }
}

pub fn charge_multiplier(held_ms: f64, peak_ms: f64, rules: &Rules) -> Result<f64, RuleError> {
    rules.validate()?;
    if !nonnegative(held_ms) || !nonnegative(peak_ms) || peak_ms == 0.0 {
        return Err(RuleError::InvalidEvidence);
    }
    checked(charge_unchecked(held_ms, peak_ms, rules))
}

pub fn phase_multiplier(phase: f64, rules: &Rules) -> Result<f64, RuleError> {
    rules.validate()?;
    if !nonnegative(phase) || phase > 1.0 {
        return Err(RuleError::InvalidEvidence);
    }
    checked(interpolate(&rules.phase_points, phase))
}

pub fn speed_multiplier(
    source: [f64; 3],
    target: [f64; 3],
    weapon: [f64; 3],
    continuous: bool,
    rules: &Rules,
) -> Result<f64, RuleError> {
    rules.validate()?;
    if !source
        .into_iter()
        .chain(target)
        .chain(weapon)
        .all(f64::is_finite)
    {
        return Err(RuleError::InvalidEvidence);
    }
    speed_unchecked(source, target, weapon, continuous, rules)
}

fn charge_unchecked(held_ms: f64, peak_ms: f64, rules: &Rules) -> f64 {
    interpolate(
        &[
            [0.0, 1.0],
            [peak_ms, rules.charge_max],
            [
                peak_ms + f64::from(rules.charge_decay_ms),
                rules.charge_tail,
            ],
        ],
        held_ms,
    )
}

fn interpolate(points: &[[f64; 2]], value: f64) -> f64 {
    if value <= points[0][0] {
        return points[0][1];
    }
    for pair in points.windows(2) {
        let [[left, low], [right, high]] = [pair[0], pair[1]];
        if value <= right {
            return low + (high - low) * (value - left) / (right - left);
        }
    }
    points[points.len() - 1][1]
}

fn speed_unchecked(
    source: [f64; 3],
    target: [f64; 3],
    weapon: [f64; 3],
    continuous: bool,
    rules: &Rules,
) -> Result<f64, RuleError> {
    if !continuous {
        return Ok(1.0);
    }
    let baseline = std::array::from_fn::<_, 3, _>(|index| weapon[index] - source[index]);
    let squared = compensated_sum(baseline.map(|value| value * value))?;
    if squared < 0.01 {
        return Ok(1.0);
    }
    let projected = compensated_sum(std::array::from_fn(|index| {
        (source[index] - target[index]) * baseline[index]
    }))? / squared;
    Ok(checked(1.0 + projected)?.clamp(rules.speed_min, rules.speed_max))
}

fn compensated_sum(values: [f64; 3]) -> Result<f64, RuleError> {
    // Python 3.14 sum uses compensated accumulation for floating-point inputs.
    let (mut sum, mut compensation) = (0.0_f64, 0.0_f64);
    for value in values {
        let next = checked(sum + value)?;
        compensation += if sum.abs() >= value.abs() {
            (sum - next) + value
        } else {
            (value - next) + sum
        };
        sum = next;
    }
    checked(sum + compensation)
}

fn checked(value: f64) -> Result<f64, RuleError> {
    if value.is_finite() {
        Ok(value)
    } else {
        Err(RuleError::Overflow)
    }
}

fn nonnegative(value: f64) -> bool {
    value.is_finite() && value >= 0.0
}
