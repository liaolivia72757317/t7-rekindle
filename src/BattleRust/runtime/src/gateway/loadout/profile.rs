use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};
use std::io;
use t7r_battle_resources::ResourceBundle;

pub(super) fn invalid() -> io::Error {
    io::Error::new(
        io::ErrorKind::InvalidData,
        "invalid lobby profile or catalogue",
    )
}
fn array(value: &Value) -> io::Result<&Vec<Value>> {
    value.as_array().ok_or_else(invalid)
}
fn int(value: &Value) -> io::Result<i32> {
    value
        .as_u64()
        .and_then(|id| i32::try_from(id).ok())
        .ok_or_else(invalid)
}
fn integers(value: &Value) -> io::Result<Vec<i32>> {
    array(value)?.iter().map(int).collect()
}
fn groups(value: &Value) -> io::Result<[[i32; 6]; 8]> {
    array(value)?
        .iter()
        .map(|row| integers(row)?.try_into().map_err(|_| invalid()))
        .collect::<io::Result<Vec<_>>>()?
        .try_into()
        .map_err(|_| invalid())
}

pub(crate) struct Catalogue {
    pub(super) heroes: BTreeMap<i32, Hero>,
    shields: BTreeSet<i32>,
}
#[derive(Clone)]
pub(super) struct Hero {
    pub groups: [[i32; 6]; 8],
    pub native: [[i32; 6]; 8],
    pub group_ids: [i32; 8],
}
impl Catalogue {
    pub(super) fn is_shield(&self, weapon_id: i32) -> bool {
        self.shields.contains(&weapon_id)
    }

    pub fn from_bundle(bundle: &ResourceBundle) -> io::Result<Self> {
        Self::from_data(&bundle.data()["catalogue"])
    }

    pub(super) fn from_data(data: &Value) -> io::Result<Self> {
        let mut heroes = BTreeMap::new();
        for hero in array(&data["heroes"])? {
            let id = int(&hero["resourceId"])?;
            let hero = Hero {
                groups: groups(&hero["groups"])?,
                native: groups(&hero["nativeGroups"])?,
                group_ids: integers(&hero["groupIds"])?
                    .try_into()
                    .map_err(|_| invalid())?,
            };
            for group in 0..8 {
                for index in 0..6 {
                    let weapon = hero.groups[group][index];
                    if weapon != 0
                        && (hero.group_ids[group] == 0 || hero.native[group][index] != weapon)
                    {
                        return Err(invalid());
                    }
                }
            }
            if id == 0 || heroes.insert(id, hero).is_some() {
                return Err(invalid());
            }
        }
        let shields = array(&data["weapons"])?
            .iter()
            .filter(|weapon| weapon["family"].as_u64() == Some(12))
            .map(|weapon| int(&weapon["resourceId"]))
            .collect::<io::Result<_>>()?;
        Ok(Self { heroes, shields })
    }
    pub fn position(&self, hero: i32) -> i32 {
        (self
            .heroes
            .keys()
            .position(|id| *id == hero)
            .expect("validated hero")
            + 1) as i32
    }
    pub fn reference(&self, hero: u64, position: i32) -> io::Result<i32> {
        let id = i32::try_from(hero).map_err(|_| invalid())?;
        if !self.heroes.contains_key(&id) || self.position(id) != position {
            return Err(invalid());
        }
        Ok(id)
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(super) struct Slot {
    pub slot: u8,
    pub group: usize,
    pub index: usize,
}

#[derive(Debug, PartialEq, Eq)]
pub(super) struct Profile {
    pub revision: u64,
    pub formation: Vec<i32>,
    pub weapons: BTreeMap<i32, Vec<Slot>>,
}
impl Profile {
    pub fn parse(data: &Value, catalogue: &Catalogue) -> io::Result<Self> {
        if data.as_object().ok_or_else(invalid)?.len() != 4
            || data["schemaVersion"].as_u64() != Some(1)
        {
            return Err(invalid());
        }
        let revision = data["revision"]
            .as_u64()
            .filter(|id| *id < u64::MAX)
            .ok_or_else(invalid)?;
        let formation = integers(&data["formation"])?;
        if formation.len() > 4
            || formation.iter().collect::<BTreeSet<_>>().len() != formation.len()
            || formation
                .iter()
                .any(|id| !catalogue.heroes.contains_key(id))
        {
            return Err(invalid());
        }
        let selections = data["weapons"].as_object().ok_or_else(invalid)?;
        if selections.len() != catalogue.heroes.len() {
            return Err(invalid());
        }
        let mut weapons = BTreeMap::new();
        for (id, hero) in &catalogue.heroes {
            let items = array(selections.get(&id.to_string()).ok_or_else(invalid)?)?;
            if items.len() > 4 {
                return Err(invalid());
            }
            let mut occupied = BTreeSet::new();
            let mut selected = BTreeSet::new();
            let mut slots = Vec::new();
            for item in items {
                if item.as_object().ok_or_else(invalid)?.len() != 3 {
                    return Err(invalid());
                }
                let slot = int(&item["slot"])?;
                let group = int(&item["group"])? as usize;
                let index = int(&item["index"])? as usize;
                if !(1..=4).contains(&slot) || group >= 8 || index >= 6 || !occupied.insert(slot) {
                    return Err(invalid());
                }
                let weapon = hero.groups[group][index];
                if weapon == 0 || !selected.insert(weapon) {
                    return Err(invalid());
                }
                slots.push(Slot {
                    slot: slot as u8,
                    group,
                    index,
                });
            }
            if selected.intersection(&catalogue.shields).count() > 1 {
                return Err(invalid());
            }
            weapons.insert(*id, slots);
        }
        Ok(Self {
            revision,
            formation,
            weapons,
        })
    }
}
