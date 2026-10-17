use super::Member;
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};
use std::io;
use t7r_battle_core::{action, damage, guard};
use t7r_battle_resources::ResourceBundle;

#[cfg(test)]
#[path = "profiles_tests.rs"]
mod tests;

const FIST_ID: u32 = 100000;
const ATTACKS: [u32; 4] = [300560, 300550, 300570, 300540];
const DASH: u32 = 700250;
const LEGACY_HURT_MS: u16 = 400;

#[derive(Debug, Clone)]
pub struct ActorResources {
    pub loadout: action::Loadout,
    pub damage: HeroDamage,
}

impl ActorResources {
    pub(super) fn preferred_distance(&self, weapon_id: u32) -> f64 {
        let weapon = &self.damage.weapons[&weapon_id];
        let profile = self
            .loadout
            .profile(weapon_id)
            .expect("equipped weapon profile");
        // Planning estimates only; native sweeps still decide actual contact.
        if weapon_id == FIST_ID {
            0.9
        } else if weapon.long_pole {
            1.7
        } else if profile.hand == 2 {
            1.1
        } else {
            1.4
        }
    }
}

#[derive(Debug, Clone)]
pub struct HeroDamage {
    pub max_health: u32,
    pub armor: f64,
    pub armor_type: guard::ArmorType,
    pub riposte: bool,
    pub combo: bool,
    pub rage: bool,
    pub fortified: bool,
    pub siege_immunity: bool,
    pub weapons: BTreeMap<u32, WeaponDamage>,
}

#[derive(Debug, Clone)]
pub struct WeaponDamage {
    pub attacks: BTreeMap<u32, AttackDamage>,
    pub traits: damage::Traits,
    pub peak_ms: f64,
    pub long_pole: bool,
    pub cleave: damage::Cleave,
    pub attack_grade: guard::Grade,
    pub defence_grade: guard::Grade,
    pub weight: f64,
    pub shield_max: u32,
    pub hurt_ms: u16,
}

#[derive(Debug, Clone, Copy)]
pub struct AttackDamage {
    pub base: f64,
    pub damage_type: damage::DamageType,
}

pub fn parse(bundle: &ResourceBundle, member: &Member) -> io::Result<ActorResources> {
    if member.equipment.len() > 4 {
        return Err(invalid("too many selected weapon slots"));
    }
    let hero_key = member.hero_id.to_string();
    let mode = t7r_battle_resources::training_maps::mode(bundle.map_id())?;
    let data = if mode.is_some_and(|mode| mode["heroId"] == member.hero_id) {
        t7r_battle_resources::training_maps::loadouts()?
    } else {
        bundle.data()
    };
    let combat = &data["combatProfiles"][&hero_key];
    let hero = &data["damageProfiles"][&hero_key];
    if integer(&hero["heroId"])? != member.hero_id {
        return Err(invalid("battle member hero differs from resource profile"));
    }
    let traits = traits(&hero["traits"])?;
    let mut damage = HeroDamage {
        max_health: integer(&hero["maxHealth"])?,
        armor: number(&hero["armor"])?,
        armor_type: match text(&hero["armorType"])? {
            "light" => guard::ArmorType::Light,
            "medium" => guard::ArmorType::Medium,
            "heavy" => guard::ArmorType::Heavy,
            _ => return Err(invalid("invalid hero armor type")),
        },
        riposte: traits.riposte,
        combo: traits.combo,
        rage: traits.rage,
        fortified: traits.fortified,
        siege_immunity: traits.siege_immunity,
        weapons: BTreeMap::new(),
    };
    if damage.max_health == 0 || damage.max_health > i32::MAX as u32 {
        return Err(invalid("invalid hero maximum health"));
    }
    let selected: BTreeSet<_> = member
        .equipment
        .iter()
        .map(|item| item.weapon_id)
        .chain([FIST_ID])
        .collect();
    let mut profiles = Vec::with_capacity(selected.len());
    for weapon_id in selected {
        let key = weapon_id.to_string();
        let combat = &combat[&key];
        let profile = action_profile(weapon_id, combat)?;
        damage.weapons.insert(
            weapon_id,
            weapon_damage(
                weapon_id,
                &hero["weapons"][&key],
                combat,
                &profile,
                &traits.calculation,
            )?,
        );
        profiles.push(profile);
    }
    let loadout =
        action::Loadout::new(profiles, member.equipment.clone(), FIST_ID).map_err(invalid)?;
    if loadout.profile(member.weapon_id).map_err(invalid)?.shield {
        return Err(invalid("shield cannot be selected as the main weapon"));
    }
    Ok(ActorResources { loadout, damage })
}

fn action_profile(weapon_id: u32, value: &Value) -> io::Result<action::WeaponProfile> {
    if integer(&value["weaponId"])? != weapon_id {
        return Err(invalid(
            "combat weapon identity differs from selected weapon",
        ));
    }
    let shield = boolean(&value["shield"])?;
    let mut attacks = [action::AttackProfile::default(); 4];
    if !shield {
        for (index, intent) in ATTACKS.iter().enumerate() {
            let attack = &value["attacks"][intent.to_string()];
            attacks[index] = action::AttackProfile {
                enabled: attack
                    .get("enabled")
                    .map(boolean)
                    .transpose()?
                    .unwrap_or(true),
                prepare_ms: duration(&attack["phases"][0])?,
                strike_ms: duration(&attack["phases"][2])?,
                recovery_ms: duration(&attack["phases"][3])?,
                parried_ms: duration(&attack["parried"])?,
                rebound_ms: duration(&attack["rebound"])?,
                contact_start_ms: duration(&attack["contactStartMs"])?,
                contact_end_ms: duration(&attack["contactEndMs"])?,
            };
        }
    }
    let dash = value["dashAttack"]
        .as_object()
        .ok_or_else(|| invalid("invalid dash profile"))?;
    let dash = if dash.is_empty() || !boolean(&value["dashAttack"]["enabled"])? {
        None
    } else {
        let dash = &value["dashAttack"];
        Some(action::DashProfile {
            duration_ms: duration(&dash["durationMs"])?,
            move_ms: duration(&dash["moveMs"])?,
            contact_start_ms: duration(&dash["contactStartMs"])?,
            contact_end_ms: duration(&dash["contactEndMs"])?,
            rebound_ms: duration(&dash["reboundMs"])?,
            speed_milli: integer(&dash["speedMilli"])?,
        })
    };
    let profile = action::WeaponProfile {
        weapon_id,
        shield,
        hand: u8::try_from(integer(&value["hand"])?).map_err(invalid)?,
        change_ms: duration(&value["changeMs"])?,
        attacks,
        guard_prepare_ms: if shield {
            0
        } else {
            duration(&value["guard"]["prepareMs"])?
        },
        guard_success_ms: if shield {
            0
        } else {
            duration(&value["guard"]["successMs"])?
        },
        dash,
    };
    profile.validate().map_err(invalid)?;
    Ok(profile)
}

fn weapon_damage(
    weapon_id: u32,
    value: &Value,
    combat: &Value,
    action: &action::WeaponProfile,
    hero_traits: &[damage::Trait],
) -> io::Result<WeaponDamage> {
    if integer(&value["weaponId"])? != weapon_id {
        return Err(invalid(
            "damage weapon identity differs from selected weapon",
        ));
    }
    let mut attacks = BTreeMap::new();
    for (intent, attack) in value["attacks"]
        .as_object()
        .ok_or_else(|| invalid("missing weapon damage attacks"))?
    {
        let id = intent.parse::<u32>().map_err(invalid)?;
        if id.to_string() != *intent || !(ATTACKS.contains(&id) || id == DASH) {
            return Err(invalid("unknown weapon damage intent"));
        }
        attacks.insert(
            id,
            AttackDamage {
                base: number(&attack["base"])?,
                damage_type: match text(&attack["damageType"])? {
                    "slash" => damage::DamageType::Slash,
                    "pierce" => damage::DamageType::Pierce,
                    "blunt" => damage::DamageType::Blunt,
                    _ => return Err(invalid("invalid weapon damage type")),
                },
            },
        );
    }
    if !action.shield
        && (ATTACKS.iter().any(|intent| !attacks.contains_key(intent))
            || action.dash.is_some() && !attacks.contains_key(&DASH))
    {
        return Err(invalid(
            "weapon damage attacks do not cover enabled actions",
        ));
    }
    let weapon_traits = traits(&value["traits"])?;
    if weapon_traits.riposte
        || weapon_traits.combo
        || weapon_traits.rage
        || weapon_traits.fortified
        || weapon_traits.siege_immunity
    {
        return Err(invalid("hero-only trait appears in a weapon profile"));
    }
    let peak_ms = number(&value["peakMs"])?;
    let weight = number(&value["weight"])?;
    let shield_max = integer(&value["shieldMax"])?;
    if peak_ms == 0. || weight == 0. || action.shield != (shield_max > 0) {
        return Err(invalid("invalid weapon damage bounds or shield identity"));
    }
    // The original schema permits missing Hurt timing and explicitly uses 400 ms.
    let hurt_ms = match combat.get("hurtMs") {
        Some(value) => duration(value)?,
        None => LEGACY_HURT_MS,
    };
    if hurt_ms == 0 {
        return Err(invalid("invalid hurt duration"));
    }
    Ok(WeaponDamage {
        attacks,
        traits: hero_traits
            .iter()
            .copied()
            .chain(weapon_traits.calculation)
            .collect(),
        peak_ms,
        long_pole: boolean(&value["longPole"])?,
        cleave: match text(&value["cleave"])? {
            "none" => damage::Cleave::None,
            "low" => damage::Cleave::Low,
            "medium" => damage::Cleave::Medium,
            "high" => damage::Cleave::High,
            _ => return Err(invalid("invalid weapon cleave level")),
        },
        attack_grade: grade(&value["attackGrade"])?,
        defence_grade: grade(&value["defenceGrade"])?,
        weight,
        shield_max,
        hurt_ms,
    })
}

#[derive(Default)]
struct Traits {
    calculation: Vec<damage::Trait>,
    riposte: bool,
    combo: bool,
    rage: bool,
    fortified: bool,
    siege_immunity: bool,
}

fn traits(value: &Value) -> io::Result<Traits> {
    let mut parsed = Traits::default();
    for value in value
        .as_array()
        .ok_or_else(|| invalid("missing damage traits"))?
    {
        let item = match text(value)? {
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
            "riposte" => {
                parsed.riposte = true;
                continue;
            }
            "combo" => {
                parsed.combo = true;
                continue;
            }
            "rage" => {
                parsed.rage = true;
                continue;
            }
            "fortified" => {
                parsed.fortified = true;
                continue;
            }
            "siege_immunity" => {
                parsed.siege_immunity = true;
                continue;
            }
            _ => return Err(invalid("unknown damage trait")),
        };
        parsed.calculation.push(item);
    }
    Ok(parsed)
}

fn grade(value: &Value) -> io::Result<guard::Grade> {
    match text(value)? {
        "F" => Ok(guard::Grade::F),
        "E" => Ok(guard::Grade::E),
        "D" => Ok(guard::Grade::D),
        "C" => Ok(guard::Grade::C),
        "B" => Ok(guard::Grade::B),
        "A" => Ok(guard::Grade::A),
        "S" => Ok(guard::Grade::S),
        "SS" => Ok(guard::Grade::SS),
        _ => Err(invalid("invalid weapon performance grade")),
    }
}

fn integer(value: &Value) -> io::Result<u32> {
    value
        .as_u64()
        .and_then(|value| u32::try_from(value).ok())
        .ok_or_else(|| invalid("expected u32 profile field"))
}
fn duration(value: &Value) -> io::Result<u16> {
    u16::try_from(integer(value)?).map_err(invalid)
}
fn number(value: &Value) -> io::Result<f64> {
    value
        .as_f64()
        .filter(|value| value.is_finite() && *value >= 0.)
        .ok_or_else(|| invalid("expected nonnegative finite profile field"))
}
fn text(value: &Value) -> io::Result<&str> {
    value
        .as_str()
        .ok_or_else(|| invalid("expected profile string"))
}
fn boolean(value: &Value) -> io::Result<bool> {
    value
        .as_bool()
        .ok_or_else(|| invalid("expected profile boolean"))
}
fn invalid(error: impl std::fmt::Display) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, error.to_string())
}
