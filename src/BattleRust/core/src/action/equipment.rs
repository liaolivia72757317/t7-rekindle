use super::{ActionError, EquipmentItem, WeaponProfile};
use std::collections::{BTreeMap, BTreeSet};
use std::sync::Arc;

#[derive(Debug, Clone)]
pub struct Loadout {
    profiles: BTreeMap<u32, Arc<WeaponProfile>>,
    items: Vec<EquipmentItem>,
    pub(super) fist_id: u32,
}

impl Loadout {
    pub fn new(
        profiles: Vec<WeaponProfile>,
        equipment: Vec<EquipmentItem>,
        fist_id: u32,
    ) -> Result<Self, ActionError> {
        if fist_id == 0 || equipment.len() > 4 {
            return Err(ActionError::InvalidLoadout);
        }
        let mut mapped = BTreeMap::new();
        for profile in profiles {
            profile.validate()?;
            if mapped
                .insert(profile.weapon_id, Arc::new(profile))
                .is_some()
            {
                return Err(ActionError::InvalidLoadout);
            }
        }
        let (mut slots, mut ids) = (BTreeSet::new(), BTreeSet::from([fist_id]));
        for item in &equipment {
            if !(1..=4).contains(&item.slot)
                || !slots.insert(item.slot)
                || (item.weapon_id != fist_id && !ids.insert(item.weapon_id))
                || !mapped.contains_key(&item.weapon_id)
            {
                return Err(ActionError::InvalidLoadout);
            }
        }
        if mapped.keys().copied().collect::<BTreeSet<_>>() != ids
            || mapped[&fist_id].shield
            || equipment
                .iter()
                .filter(|item| mapped[&item.weapon_id].shield)
                .count()
                > 1
        {
            return Err(ActionError::InvalidLoadout);
        }
        Ok(Self {
            profiles: mapped,
            items: equipment,
            fist_id,
        })
    }

    pub fn profile(&self, weapon_id: u32) -> Result<&WeaponProfile, ActionError> {
        self.profiles
            .get(&weapon_id)
            .map(Arc::as_ref)
            .ok_or(ActionError::InvalidLoadout)
    }

    pub fn items(&self) -> &[EquipmentItem] {
        &self.items
    }
    pub fn fist_id(&self) -> u32 {
        self.fist_id
    }

    pub(super) fn snapshot(&self, weapon_id: u32) -> Result<Arc<WeaponProfile>, ActionError> {
        self.profiles
            .get(&weapon_id)
            .cloned()
            .ok_or(ActionError::InvalidLoadout)
    }

    pub(super) fn offhand(
        &self,
        weapon_id: u32,
        broken_shields: &BTreeSet<u32>,
    ) -> Result<u32, ActionError> {
        if self.profile(weapon_id)?.hand != 2 || weapon_id == self.fist_id {
            return Ok(0);
        }
        Ok(self
            .items
            .iter()
            .find(|item| {
                self.profiles[&item.weapon_id].shield && !broken_shields.contains(&item.weapon_id)
            })
            .map_or(0, |item| item.weapon_id))
    }

    pub(super) fn select(
        &self,
        slot: i16,
        current: u32,
        main: u32,
    ) -> Result<Option<u32>, ActionError> {
        let target = if slot == 13 {
            Some(if current == self.fist_id {
                main
            } else {
                self.fist_id
            })
        } else {
            self.items
                .iter()
                .find(|item| i16::from(item.slot) == slot)
                .map(|item| item.weapon_id)
        };
        let Some(target) = target else {
            return Ok(None);
        };
        if !self.profile(target)?.shield {
            return Ok(Some(target));
        }
        let mut candidates: Vec<_> = self
            .items
            .iter()
            .filter(|item| self.profiles[&item.weapon_id].hand == 2)
            .collect();
        candidates.sort_by_key(|item| item.slot);
        Ok([current, main]
            .into_iter()
            .chain(candidates.iter().map(|item| item.weapon_id))
            .find(|weapon| candidates.iter().any(|item| item.weapon_id == *weapon)))
    }
}
