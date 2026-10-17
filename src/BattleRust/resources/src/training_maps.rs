//! Training scenes are selected by room creation, never by the matchmaking pool.
use crate::{ResourceBundle, validate};
use serde_json::Value;
use std::{io, sync::OnceLock};

pub const MAP_IDS: [u32; 2] = [6006, 6009];

pub fn contains(map_id: u32) -> bool {
    MAP_IDS.contains(&map_id)
}

/// Training-only cards never enter the saved lobby catalogue.
pub fn loadouts() -> io::Result<&'static Value> {
    static LOADOUTS: OnceLock<Result<Value, String>> = OnceLock::new();
    LOADOUTS
        .get_or_init(|| {
            serde_json::from_slice(include_bytes!("../data/training-loadouts-v1.json"))
                .map_err(|error| error.to_string())
        })
        .as_ref()
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error.clone()))
}

pub fn mode(map_id: u32) -> io::Result<Option<&'static Value>> {
    if !contains(map_id) {
        return Ok(None);
    }
    loadouts()?["modes"]
        .get(map_id.to_string())
        .map(Some)
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "missing training mode"))
}

pub(crate) fn select(bundle: &ResourceBundle, map_id: u32) -> io::Result<ResourceBundle> {
    static MAPS: OnceLock<Result<Vec<Value>, String>> = OnceLock::new();
    let maps = MAPS
        .get_or_init(|| {
            let maps: Vec<Value> =
                serde_json::from_slice(include_bytes!("../data/training-maps-v1.json"))
                    .map_err(|error| error.to_string())?;
            if maps.len() != MAP_IDS.len()
                || maps
                    .iter()
                    .zip(MAP_IDS)
                    .any(|(map, id)| map["resourceId"] != id)
            {
                return Err("invalid training map catalogue".into());
            }
            for map in &maps {
                validate::map(map).map_err(|error| error.to_string())?;
            }
            Ok(maps)
        })
        .as_ref()
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error.clone()))?;
    let map = maps
        .iter()
        .find(|map| map["resourceId"] == map_id)
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "unsupported training map"))?;
    let mut data = bundle.data().clone();
    data["map"] = map.clone();
    Ok(ResourceBundle { data })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn training_loadouts_match_native_cards_without_changing_the_lobby_catalogue() {
        let bundle = ResourceBundle::from_slice(include_bytes!(
            "../../../../tests/fixtures/resources/bundle-v1.json"
        ))
        .unwrap();
        let loadouts = loadouts().unwrap();
        let mut validation = bundle.data().clone();
        for field in ["combatProfiles", "damageProfiles"] {
            validation[field]
                .as_object_mut()
                .unwrap()
                .extend(loadouts[field].as_object().unwrap().clone());
        }
        validation["catalogue"]["heroes"]
            .as_array_mut()
            .unwrap()
            .extend(loadouts["catalogue"]["heroes"].as_array().unwrap().clone());
        for weapon in loadouts["catalogue"]["weapons"].as_array().unwrap() {
            let weapons = validation["catalogue"]["weapons"].as_array_mut().unwrap();
            if !weapons
                .iter()
                .any(|existing| existing["resourceId"] == weapon["resourceId"])
            {
                weapons.push(weapon.clone());
            }
        }
        validate::bundle(&validation).unwrap();
        for (map_id, hero_id, weapon_id) in [(6006, 10001, 1030111), (6009, 10002, 1040511)] {
            let selected = select(&bundle, map_id).unwrap();
            assert_eq!(selected.data()["catalogue"], bundle.data()["catalogue"]);
            let mode = mode(map_id).unwrap().unwrap();
            assert_eq!(mode["heroId"], hero_id);
            assert_eq!(mode["weaponId"], weapon_id);
            assert_eq!(
                mode["equipment"],
                serde_json::json!([{"slot": 1, "weaponId": weapon_id}])
            );
            assert_ne!(mode["entrySpawn"], selected.data()["map"]["spawns"]["2"][0]);
        }
        assert!(mode(1028).unwrap().is_none());
    }

    #[test]
    fn training_maps_are_separate_and_have_one_player_and_one_opponent() {
        let baseline = ResourceBundle::from_slice(include_bytes!(
            "../../../../tests/fixtures/resources/bundle-v1.json"
        ))
        .unwrap();
        for id in MAP_IDS {
            assert!(!crate::match_maps::MAP_IDS.contains(&id));
            assert!(!crate::match_maps::TEAM_MAP_IDS.contains(&id));
            let selected = crate::match_maps::select(&baseline, id).unwrap();
            assert_eq!(selected.map_id(), id);
            for camp in ["1", "2"] {
                assert_eq!(
                    selected.data()["map"]["spawns"][camp]
                        .as_array()
                        .unwrap()
                        .len(),
                    1
                );
            }
            ResourceBundle::from_slice(&serde_json::to_vec(selected.data()).unwrap()).unwrap();
        }
        assert!(select(&baseline, 6007).is_err());
    }
}
