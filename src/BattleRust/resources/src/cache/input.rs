use crate::ResourceBundle;
use crate::bot_names;
use crate::catalogue::{CATEGORY_PATH, Catalogue, GROUP_PATH, HERO_PATH, UNLOCK_PATH, WEAPON_PATH};
use crate::frozen::FrozenData;
use crate::vfs::{ClientResources, ResourceError};
use sha2::{Digest, Sha256};
use std::path::Path;
use std::sync::OnceLock;

const PATHS: [&str; 6] = [
    HERO_PATH,
    UNLOCK_PATH,
    GROUP_PATH,
    WEAPON_PATH,
    CATEGORY_PATH,
    bot_names::NAME_PATH,
];

pub(super) struct Snapshot {
    tables: [Vec<u8>; 6],
}

impl Snapshot {
    pub fn read(client: &Path) -> Result<Self, ResourceError> {
        let mut resources = ClientResources::new(client)?;
        resources.check_layout()?;
        let mut tables = Vec::with_capacity(PATHS.len());
        for path in PATHS {
            tables.push(resources.read("data1.vfs", path)?);
        }
        resources.check_layout()?;
        Ok(Self {
            tables: tables.try_into().expect("six input tables"),
        })
    }

    pub fn identity(&self, key: &str) -> String {
        static BASELINE: OnceLock<[u8; 32]> = OnceLock::new();
        let baseline = BASELINE.get_or_init(|| {
            Sha256::digest(include_bytes!("../../data/battle-baseline-v1.json")).into()
        });
        self.identity_with_baseline(key, baseline)
    }

    fn identity_with_baseline(&self, key: &str, baseline: &[u8; 32]) -> String {
        let mut digest = Sha256::new();
        field(&mut digest, b"T7R.resource-cache.inputs.v1");
        field(&mut digest, key.as_bytes());
        field(&mut digest, baseline);
        for (path, data) in PATHS.iter().zip(&self.tables) {
            field(&mut digest, path.as_bytes());
            field(&mut digest, data);
        }
        hex(&digest.finalize())
    }

    pub fn build(
        &self,
        rules: u32,
        resources: u32,
        client_version: &str,
    ) -> Result<ResourceBundle, ResourceError> {
        let failure =
            |error| ResourceError::new("content_mismatch", "data1.vfs", "battle baseline", error);
        let [heroes, unlocks, groups, weapons, categories, names] = &self.tables;
        let catalogue =
            Catalogue::parse(heroes, unlocks, groups, weapons, categories).map_err(failure)?;
        let names = bot_names::parse_names(names).map_err(failure)?;
        FrozenData::embedded()
            .map_err(failure)?
            .assemble(&catalogue, &names, rules, resources, client_version)
            .map_err(failure)
    }
}

pub(super) fn field(digest: &mut Sha256, bytes: &[u8]) {
    digest.update((bytes.len() as u64).to_le_bytes());
    digest.update(bytes);
}

pub(super) fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

pub(super) fn digest(bytes: &[u8]) -> String {
    hex(&Sha256::digest(bytes))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn identity_binds_the_frozen_baseline_and_every_input() {
        let mut snapshot = Snapshot {
            tables: std::array::from_fn(|index| vec![index as u8]),
        };
        let identity = snapshot.identity_with_baseline("client", &[0; 32]);
        assert_ne!(
            identity,
            snapshot.identity_with_baseline("client", &[1; 32])
        );
        assert_ne!(
            identity,
            snapshot.identity_with_baseline("another", &[0; 32])
        );
        for index in 0..6 {
            snapshot.tables[index][0] ^= 1;
            assert_ne!(
                identity,
                snapshot.identity_with_baseline("client", &[0; 32])
            );
            snapshot.tables[index][0] ^= 1;
        }
    }
}
