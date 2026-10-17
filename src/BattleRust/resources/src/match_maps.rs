//! Source-owned small-battlefield maps. Each match still uses the v1 single-map bundle.
use crate::{ResourceBundle, validate};
use serde_json::Value;
use std::io;
use std::sync::OnceLock;

pub const MAP_IDS: [u32; 12] = [
    20001, 1003, 1030, 1021, 20011, 1001, 6003, 20013, 20015, 90003, 1034, 20012,
];
pub const TEAM_MAP_IDS: [u32; 6] = [1003, 1030, 1021, 1001, 6003, 1034];

fn maps() -> io::Result<&'static Vec<Value>> {
    static MAPS: OnceLock<Result<Vec<Value>, String>> = OnceLock::new();
    MAPS.get_or_init(|| {
        let maps: Vec<Value> = serde_json::from_slice(include_bytes!("../data/match-maps-v1.json"))
            .map_err(|error| error.to_string())?;
        if maps.len() != MAP_IDS.len()
            || maps
                .iter()
                .zip(MAP_IDS)
                .any(|(map, id)| map["resourceId"].as_u64() != Some(u64::from(id)))
        {
            return Err("invalid embedded match map catalogue".into());
        }
        for map in &maps {
            validate::map(map).map_err(|error| error.to_string())?;
        }
        Ok(maps)
    })
    .as_ref()
    .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error.clone()))
}

pub fn select(bundle: &ResourceBundle, map_id: u32) -> io::Result<ResourceBundle> {
    if crate::training_maps::contains(map_id) {
        return crate::training_maps::select(bundle, map_id);
    }
    if map_id == 1028 {
        let frozen: Value =
            serde_json::from_slice(include_bytes!("../data/battle-baseline-v1.json"))?;
        let mut data = bundle.data().clone();
        data["map"] = frozen["map"].clone();
        validate::map(&data["map"])?;
        return Ok(ResourceBundle { data });
    }
    let map = maps()?
        .iter()
        .find(|map| map["resourceId"].as_u64() == Some(u64::from(map_id)))
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "unsupported match map"))?;
    let mut data = bundle.data().clone();
    data["map"] = map.clone();
    Ok(ResourceBundle { data })
}
