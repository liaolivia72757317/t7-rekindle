use super::*;
use crate::battle_session::{EquipmentItem, Member, PreparedHero};
use t7r_battle_resources::ResourceBundle;

pub(in crate::gateway) struct RoundFormation {
    profile: Profile,
    choices: Vec<Arc<PreparedHero>>,
    training_catalogue: Option<Catalogue>,
}

impl RoundFormation {
    pub(in crate::gateway) fn training(
        bundle: &ResourceBundle,
        bound: &Member,
    ) -> io::Result<Self> {
        let data = t7r_battle_resources::training_maps::loadouts()?;
        let mode =
            t7r_battle_resources::training_maps::mode(bundle.map_id())?.ok_or_else(invalid)?;
        if mode["heroId"] != bound.hero_id || mode["weaponId"] != bound.weapon_id {
            return Err(invalid());
        }
        let catalogue = Catalogue::from_data(&data["catalogue"])?;
        let hero_id = bound.hero_id as i32;
        let mut weapons = serde_json::Map::new();
        for hero in data["catalogue"]["heroes"].as_array().ok_or_else(invalid)? {
            let slots = hero["defaults"]
                .as_array()
                .ok_or_else(invalid)?
                .iter()
                .enumerate()
                .map(|(index, pair)| json!({"slot": index + 1, "group": pair[0], "index": pair[1]}))
                .collect::<Vec<_>>();
            weapons.insert(
                hero["resourceId"].as_u64().ok_or_else(invalid)?.to_string(),
                json!(slots),
            );
        }
        let profile = Profile::parse(
            &json!({"schemaVersion": 1, "revision": 0,
            "formation": [hero_id], "weapons": weapons}),
            &catalogue,
        )?;
        let mut formation = Self::prepare(profile, &catalogue, bundle, bound)?;
        formation.training_catalogue = Some(catalogue);
        Ok(formation)
    }

    pub(in crate::gateway) async fn read(
        mailbox: Arc<Mutex<PlatformMailbox>>,
        catalogue: &Catalogue,
        bundle: &ResourceBundle,
        bound: &Member,
    ) -> io::Result<Self> {
        let response = profile_request(mailbox, "readProfile", json!({})).await?;
        if response["success"] != true {
            return Err(io::Error::other("platform profile request failed"));
        }
        Self::prepare(
            Profile::parse(&response["profile"], catalogue)?,
            catalogue,
            bundle,
            bound,
        )
    }

    fn prepare(
        profile: Profile,
        catalogue: &Catalogue,
        bundle: &ResourceBundle,
        bound: &Member,
    ) -> io::Result<Self> {
        if !profile.formation.contains(&(bound.hero_id as i32)) {
            return Err(invalid());
        }
        let choices = profile
            .formation
            .iter()
            .map(|&hero_id| {
                let hero = &catalogue.heroes[&hero_id];
                let mut equipment = profile.weapons[&hero_id]
                    .iter()
                    .map(|slot| EquipmentItem {
                        slot: slot.slot,
                        weapon_id: hero.groups[slot.group][slot.index] as u32,
                    })
                    .collect::<Vec<_>>();
                equipment.sort_by_key(|item| item.slot);
                let weapon_id = equipment
                    .iter()
                    .find(|item| !catalogue.is_shield(item.weapon_id as i32))
                    .map_or(100000, |item| item.weapon_id);
                PreparedHero::new(
                    bundle,
                    Member {
                        hero_id: hero_id as u32,
                        weapon_id,
                        equipment,
                        ..bound.clone()
                    },
                )
                .map(Arc::new)
            })
            .collect::<io::Result<Vec<_>>>()?;
        Ok(Self {
            profile,
            choices,
            training_catalogue: None,
        })
    }

    pub(in crate::gateway) fn position(&self, hero_id: u32) -> io::Result<i32> {
        self.profile
            .formation
            .iter()
            .position(|&id| u32::try_from(id) == Ok(hero_id))
            .map(|index| index as i32 + 1)
            .ok_or_else(invalid)
    }

    pub(in crate::gateway) fn choice(&self, position: i32) -> Option<Arc<PreparedHero>> {
        usize::try_from(position)
            .ok()?
            .checked_sub(1)
            .and_then(|index| self.choices.get(index))
            .cloned()
    }

    pub(in crate::gateway) fn container(
        &self,
        body: &[u8],
        catalogue: &Catalogue,
    ) -> io::Result<Vec<ClientBattleMessage>> {
        let (container, sequence) = codec::container_request(body)?;
        let catalogue = self.training_catalogue.as_ref().unwrap_or(catalogue);
        let bodies = if container == 2 {
            codec::containers(&self.profile, catalogue, container, sequence)
        } else {
            vec![codec::empty_container(container, sequence)]
        };
        Ok(bodies
            .into_iter()
            .map(|mut body| {
                body[..2].copy_from_slice(&0x66_u16.to_be_bytes());
                ClientBattleMessage {
                    command: 0x29,
                    body,
                }
            })
            .collect())
    }
}
