//! Embedded source-owned rules and map data; this is not a writable cache format.
use crate::catalogue::{Catalogue, FIST_ID};
use crate::table::invalid;
use crate::vfs::{ClientResources, ResourceError};
use crate::{MAX_BYTES, ResourceBundle, SCHEMA_VERSION, bot_names, validate};
use serde_json::{Map, Value, json};
use std::collections::BTreeSet;
use std::io;
use std::path::Path;

pub struct FrozenData {
    data: Value,
}

impl FrozenData {
    pub fn embedded() -> io::Result<Self> {
        let data: Value = serde_json::from_slice(include_bytes!("../data/battle-baseline-v1.json"))
            .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
        let fields = ["combatProfiles", "damageProfiles", "rules", "map"];
        if data
            .as_object()
            .is_none_or(|value| value.len() != fields.len())
        {
            return Err(invalid("unexpected embedded battle data fields"));
        }
        for field in fields {
            object(&data[field])?;
        }
        Ok(Self { data })
    }

    pub fn assemble(
        &self,
        catalogue: &Catalogue,
        names: &[String],
        rules_version: u32,
        resource_version: u32,
        client_version: &str,
    ) -> io::Result<ResourceBundle> {
        metadata(rules_version, resource_version, client_version)?;
        bot_names::validate_roster(names, "")?;
        let (mut combat, mut damage) = (Map::new(), Map::new());
        let all_combat = object(&self.data["combatProfiles"])?;
        let all_damage = object(&self.data["damageProfiles"])?;
        for (&hero_id, hero) in catalogue.heroes() {
            let key = hero_id.to_string();
            let profiles = all_combat
                .get(&key)
                .ok_or_else(|| invalid(format!("unsupported frozen battle hero {hero_id}")))?;
            let profiles = object(profiles)?;
            let hero_damage = all_damage
                .get(&key)
                .ok_or_else(|| invalid(format!("unsupported frozen damage hero {hero_id}")))?;
            let damage_profiles = object(&hero_damage["weapons"])?;
            let owned = hero
                .groups
                .iter()
                .flatten()
                .copied()
                .filter(|id| *id != 0)
                .chain([FIST_ID])
                .collect::<BTreeSet<_>>();
            let (mut selected, mut selected_damage) = (Map::new(), Map::new());
            for weapon_id in owned {
                let weapon = weapon_id.to_string();
                let missing = || {
                    invalid(format!(
                        "unsupported frozen battle weapon hero={hero_id} weapon={weapon_id}"
                    ))
                };
                selected.insert(
                    weapon.clone(),
                    profiles.get(&weapon).ok_or_else(missing)?.clone(),
                );
                selected_damage.insert(
                    weapon.clone(),
                    damage_profiles.get(&weapon).ok_or_else(missing)?.clone(),
                );
            }
            let mut hero_damage = object(hero_damage)?.clone();
            hero_damage.insert("weapons".into(), Value::Object(selected_damage));
            combat.insert(key.clone(), Value::Object(selected));
            damage.insert(key, Value::Object(hero_damage));
        }
        let data = json!({
            "schemaVersion": SCHEMA_VERSION,
            "rulesVersion": rules_version,
            "resourceVersion": resource_version,
            "clientVersion": client_version,
            "catalogue": catalogue.to_json(),
            "combatProfiles": combat,
            "damageProfiles": damage,
            "rules": self.data["rules"],
            "botNames": names,
            "map": self.data["map"],
        });
        validate::bundle(&data)?;
        validate::require(
            serde_json::to_vec(&data)?.len() <= MAX_BYTES,
            "bundle exceeds size limit",
        )?;
        Ok(ResourceBundle { data })
    }

    /// Reads catalogue and names only; combat profiles and map remain source-owned.
    pub fn load_client(
        &self,
        client_directory: &Path,
        rules_version: u32,
        resource_version: u32,
        client_version: &str,
    ) -> Result<ResourceBundle, ResourceError> {
        let failure = |error| ResourceError::new("content_mismatch", "", "battle baseline", error);
        metadata(rules_version, resource_version, client_version).map_err(failure)?;
        let mut resources = ClientResources::new(client_directory)?;
        resources.check_layout()?;
        let catalogue = Catalogue::load(&mut resources)?;
        let names = bot_names::load_names(&mut resources)?;
        self.assemble(
            &catalogue,
            &names,
            rules_version,
            resource_version,
            client_version,
        )
        .map_err(failure)
    }
}

fn object(value: &Value) -> io::Result<&Map<String, Value>> {
    value
        .as_object()
        .ok_or_else(|| invalid("expected embedded battle data object"))
}

fn metadata(rules_version: u32, resource_version: u32, client_version: &str) -> io::Result<()> {
    if rules_version == 0
        || resource_version == 0
        || client_version.is_empty()
        || client_version.len() > 64
        || !client_version
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || b"_.-".contains(&c))
    {
        return Err(invalid("invalid battle bundle version identifiers"));
    }
    Ok(())
}
