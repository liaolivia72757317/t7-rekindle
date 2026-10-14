use std::collections::HashMap;

use serde::Serialize;

use crate::protocol::ErrorCode;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(transparent)]
pub struct PlayerId(u32);

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PlayerProfile {
    pub user_id: PlayerId,
    pub display_name: String,
    pub level: u32,
}

/// Development identities last for this server process, independently of rooms.
#[derive(Default)]
pub(crate) struct Players {
    profiles: HashMap<String, PlayerProfile>,
    last_id: u32,
}

impl Players {
    pub(crate) fn identify(
        &mut self,
        account: &str,
        display_name: &str,
    ) -> Result<PlayerProfile, ErrorCode> {
        if display_name.is_empty()
            || display_name.chars().count() > 31
            || display_name.chars().any(char::is_control)
        {
            return Err(ErrorCode::InvalidDisplayName);
        }
        if let Some(profile) = self.profiles.get(account) {
            if profile.display_name != display_name {
                return Err(ErrorCode::InvalidDisplayName);
            }
            return Ok(profile.clone());
        }
        if self.profiles.len() >= 4096 {
            return Err(ErrorCode::IdentityLimit);
        }
        let id = self
            .last_id
            .checked_add(1)
            .ok_or(ErrorCode::IdentityLimit)?;
        let profile = PlayerProfile {
            user_id: PlayerId(id),
            display_name: display_name.to_owned(),
            // Match the existing local development login profile.
            level: 100,
        };
        self.profiles.insert(account.to_owned(), profile.clone());
        self.last_id = id;
        Ok(profile)
    }

    pub(crate) fn forget(&mut self, account: &str) {
        self.profiles.remove(account);
    }

    pub(crate) fn get_or_default(&mut self, account: &str) -> Result<PlayerProfile, ErrorCode> {
        if let Some(profile) = self.profiles.get(account) {
            return Ok(profile.clone());
        }
        self.identify(account, &account.chars().take(31).collect::<String>())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn profiles_are_bounded_and_failed_changes_preserve_existing_identity() {
        let mut players = Players::default();
        let original = players.identify("player-a", "Player").unwrap();
        assert_eq!(
            players.identify("player-a", ""),
            Err(ErrorCode::InvalidDisplayName)
        );
        assert_eq!(
            players.identify("player-a", "different"),
            Err(ErrorCode::InvalidDisplayName)
        );
        for number in 1..4096 {
            players.get_or_default(&format!("player-{number}")).unwrap();
        }
        assert_eq!(
            players.identify("another", "Player"),
            Err(ErrorCode::IdentityLimit)
        );
        assert_eq!(players.identify("player-a", "Player"), Ok(original));
        assert_eq!(players.profiles.len(), 4096);
    }

    #[test]
    fn exhausted_player_ids_do_not_wrap_or_register_a_partial_profile() {
        let mut players = Players {
            last_id: u32::MAX,
            ..Players::default()
        };
        assert_eq!(
            players.identify("player-a", "Player"),
            Err(ErrorCode::IdentityLimit)
        );
        assert!(players.profiles.is_empty());
    }
}
