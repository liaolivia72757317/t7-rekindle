use std::collections::HashSet;

use serde::Deserialize;

use crate::{protocol::ErrorCode, room::MAP_ID};

/// A lobby scenario selection, distinct from the underlying map asset number.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct LobbyMap(u32);

impl TryFrom<u32> for LobbyMap {
    type Error = ErrorCode;

    fn try_from(value: u32) -> Result<Self, Self::Error> {
        if value == 0 || value > i32::MAX as u32 {
            return Err(ErrorCode::UnsupportedMap);
        }
        Ok(Self(value))
    }
}

impl LobbyMap {
    pub fn value(self) -> u32 {
        self.0
    }
}

pub struct MapCatalog {
    maps: HashSet<LobbyMap>,
}

impl Default for MapCatalog {
    fn default() -> Self {
        Self {
            maps: HashSet::from([LobbyMap(1003), LobbyMap(MAP_ID)]),
        }
    }
}

impl MapCatalog {
    pub fn from_json(data: &[u8]) -> Result<Self, &'static str> {
        #[derive(Deserialize)]
        #[serde(rename_all = "camelCase", deny_unknown_fields)]
        struct Input {
            map_ids: Vec<u32>,
        }

        if data.len() > 32768 {
            return Err("map catalog exceeds 32 KiB");
        }
        let input: Input = serde_json::from_slice(data).map_err(|_| "invalid map catalog JSON")?;
        if input.map_ids.is_empty() || input.map_ids.len() > 4096 {
            return Err("map catalog must contain 1..4096 maps");
        }
        let mut maps = HashSet::new();
        for value in input.map_ids {
            let map = LobbyMap::try_from(value).map_err(|_| "invalid map catalog identifier")?;
            if !maps.insert(map) {
                return Err("duplicate map catalog identifier");
            }
        }
        Ok(Self { maps })
    }

    pub fn resolve(&self, value: u32) -> Result<LobbyMap, ErrorCode> {
        let map = LobbyMap::try_from(value)?;
        if !self.maps.contains(&map) {
            return Err(ErrorCode::UnsupportedMap);
        }
        Ok(map)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn catalog_rejects_invalid_or_ambiguous_configuration() {
        for json in [
            r#"{"mapIds":[]}"#,
            r#"{"mapIds":[0]}"#,
            r#"{"mapIds":[2147483648]}"#,
            r#"{"mapIds":[1,1]}"#,
            r#"{"mapIds":[1],"mapIds":[2]}"#,
            r#"{"mapIds":[1],"extra":true}"#,
            r#"{"mapIds":["1"]}"#,
        ] {
            assert!(MapCatalog::from_json(json.as_bytes()).is_err());
        }
        assert!(MapCatalog::from_json(&vec![b' '; 32769]).is_err());
        let too_many = serde_json::json!({"mapIds": (1..=4097).collect::<Vec<_>>()});
        assert!(MapCatalog::from_json(&serde_json::to_vec(&too_many).unwrap()).is_err());
    }
}
