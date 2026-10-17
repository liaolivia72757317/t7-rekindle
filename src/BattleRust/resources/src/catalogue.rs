use crate::table::{invalid, rows, text, u16_at, u32_at, unique_rows};
use crate::vfs::{ClientResources, ResourceError};
use serde::Serialize;
use std::collections::{BTreeMap, BTreeSet};
use std::io;

pub const HERO_PATH: &str = "data/table/s_heroscard_cli.bin";
pub const UNLOCK_PATH: &str = "data/table/s_res_herocard_unlock_cli.bin";
pub const GROUP_PATH: &str = "data/table/s_res_weapon_unlock_cli.bin";
pub const WEAPON_PATH: &str = "data/table/s_weapon_cli.bin";
pub const CATEGORY_PATH: &str = "data/table/s_weapon_category_cli.bin";
pub const DEFAULT_HERO: u32 = 1101;
pub const FIST_ID: u32 = 100000;

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct Weapon {
    pub resource_id: u32,
    pub category: u16,
    pub family: u16,
    pub hand: u16,
    pub name: String,
}

impl Weapon {
    pub fn shield(&self) -> bool {
        self.family == 12
    }
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct Hero {
    pub resource_id: u32,
    pub name: String,
    pub gender: u8,
    pub groups: [[u32; 6]; 8],
    pub defaults: Vec<(usize, usize)>,
    pub group_ids: [u32; 8],
    pub native_groups: [[u32; 6]; 8],
}

impl Hero {
    pub fn weapon(&self, group: usize, index: usize) -> io::Result<u32> {
        self.groups
            .get(group)
            .and_then(|row| row.get(index))
            .copied()
            .filter(|&id| id != 0)
            .ok_or_else(|| invalid("weapon is outside this hero's available melee/shield groups"))
    }
}

#[derive(Debug, Clone)]
pub struct Catalogue {
    heroes: BTreeMap<u32, Hero>,
    weapons: BTreeMap<u32, Weapon>,
}

impl Catalogue {
    pub fn load(resources: &mut ClientResources) -> Result<Self, ResourceError> {
        let tables = [
            HERO_PATH,
            UNLOCK_PATH,
            GROUP_PATH,
            WEAPON_PATH,
            CATEGORY_PATH,
        ]
        .map(|path| resources.read("data1.vfs", path));
        let [heroes, unlocks, groups, weapons, categories] = tables;
        Self::parse(&heroes?, &unlocks?, &groups?, &weapons?, &categories?).map_err(|error| {
            ResourceError::new("content_mismatch", "data1.vfs", "catalogue", error)
        })
    }

    pub fn parse(
        hero_data: &[u8],
        unlock_data: &[u8],
        group_data: &[u8],
        weapon_data: &[u8],
        category_data: &[u8],
    ) -> io::Result<Self> {
        let hero_rows = unique_rows(hero_data, 7286, false)?;
        let group_rows = unique_rows(group_data, 1434, false)?;
        let category_rows = unique_rows(category_data, 550, true)?;
        let weapon_rows = unique_rows(weapon_data, 2829, false)?;
        let (mut weapons, mut unknown_categories) = (BTreeMap::new(), BTreeSet::new());
        for (&id, row) in &weapon_rows {
            let category = u16_at(row, 56);
            let Some(spec) = category_rows.get(&u32::from(category)) else {
                unknown_categories.insert(id);
                continue;
            };
            let (family, hand) = (u16_at(spec, 2), u16_at(spec, 46));
            if matches!(family, 1 | 2 | 3 | 12) || id == FIST_ID {
                weapons.insert(
                    id,
                    Weapon {
                        resource_id: id,
                        category,
                        family,
                        hand,
                        name: text(row, 16, 40)?,
                    },
                );
            }
        }
        let mut playable = BTreeSet::new();
        for row in rows(unlock_data, 300)? {
            let enabled = u16_at(row, 36);
            if enabled > 1 {
                return Err(invalid("invalid hero unlock visibility"));
            }
            if enabled == 1 {
                let id = u32_at(row, 42);
                let hero = hero_rows
                    .get(&id)
                    .ok_or_else(|| invalid("unlock graph references missing hero"))?;
                if text(hero, 163, 12)? == "先锋" {
                    playable.insert(id);
                }
            }
        }
        let mut heroes = BTreeMap::new();
        for id in playable {
            let row = hero_rows[&id];
            if u16_at(row, 4209) != 8 {
                return Err(invalid("unsupported hero weapon-group count"));
            }
            let mut hero = Hero {
                resource_id: id,
                name: text(row, 4, 30)?,
                gender: row[34],
                groups: [[0; 6]; 8],
                defaults: Vec::new(),
                group_ids: [0; 8],
                native_groups: [[0; 6]; 8],
            };
            for group in 0..8 {
                let (group_id, initial) = (
                    u32_at(row, 4211 + group * 12),
                    u32_at(row, 4215 + group * 12),
                );
                hero.group_ids[group] = group_id;
                if group_id == 0 {
                    continue;
                }
                let entry = group_rows
                    .get(&group_id)
                    .ok_or_else(|| invalid("hero references missing weapon group"))?;
                let count = usize::from(u16_at(entry, 64));
                if count > 6 {
                    return Err(invalid("weapon styles exceed native group capacity"));
                }
                for index in 0..count {
                    let weapon = u32_at(entry, 66 + index * 228);
                    if weapon != 0
                        && (!weapon_rows.contains_key(&weapon)
                            || unknown_categories.contains(&weapon))
                    {
                        return Err(invalid(format!(
                            "hero {id} references unavailable weapon {weapon}"
                        )));
                    }
                    hero.native_groups[group][index] = weapon;
                    if weapons.contains_key(&weapon) {
                        hero.groups[group][index] = weapon;
                        if weapon == initial {
                            hero.defaults.push((group, index));
                        }
                    }
                }
            }
            if !hero.groups.iter().flatten().any(|weapon| {
                *weapon != 0 && weapons.get(weapon).is_some_and(|weapon| !weapon.shield())
            }) {
                return Err(invalid(format!(
                    "playable vanguard {id} has no melee weapon"
                )));
            }
            heroes.insert(id, hero);
        }
        if !heroes.contains_key(&DEFAULT_HERO) || !weapons.contains_key(&FIST_ID) {
            return Err(invalid("vanguard catalogue lacks default hero or fists"));
        }
        Ok(Self { heroes, weapons })
    }

    pub fn heroes(&self) -> &BTreeMap<u32, Hero> {
        &self.heroes
    }
    pub fn weapons(&self) -> &BTreeMap<u32, Weapon> {
        &self.weapons
    }

    pub fn position(&self, hero_id: u32) -> io::Result<usize> {
        self.heroes
            .keys()
            .position(|&id| id == hero_id)
            .map(|index| index + 1)
            .ok_or_else(|| invalid("hero is outside the catalogue"))
    }

    pub fn reference(&self, hero_id: u32, position: usize) -> io::Result<u32> {
        if self.position(hero_id)? != position {
            return Err(invalid(
                "hero reference does not match its container position",
            ));
        }
        Ok(hero_id)
    }

    /// The existing bundle catalogue schema; no new compatibility format is introduced.
    pub fn to_json(&self) -> serde_json::Value {
        serde_json::json!({"heroes": self.heroes.values().collect::<Vec<_>>(), "weapons": self.weapons.values().collect::<Vec<_>>()})
    }
}
