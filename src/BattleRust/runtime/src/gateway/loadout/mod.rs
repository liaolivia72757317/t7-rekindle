mod codec;
mod formation;
mod profile;
#[cfg(test)]
mod tests;

use super::platform::{PlatformMailbox, profile_request};
use crate::client_battle_wire::ClientBattleMessage;
pub(super) use formation::RoundFormation;
pub(crate) use profile::Catalogue;
use profile::{Profile, invalid};
use serde_json::json;
use std::{
    collections::BTreeSet,
    io,
    sync::{Arc, Mutex},
};
use t7r_battle_core::protocol::Message;

const INVALID: i32 = 630001;
const SAVE_FAILED: i32 = 630000;
type Replies = Vec<(u16, Vec<u8>)>;

pub(super) async fn instance_container(
    body: &[u8],
    cat: &Catalogue,
    mailbox: Arc<Mutex<PlatformMailbox>>,
) -> io::Result<Vec<ClientBattleMessage>> {
    if body.len() != 10 || body[..2] != 0x65_u16.to_be_bytes() {
        return Err(invalid());
    }
    let (container, sequence) = codec::container_request(body)?;
    let bodies = if container == 2 {
        let response = profile_request(mailbox, "readProfile", json!({})).await?;
        let profile = Profile::parse(&response["profile"], cat)?;
        codec::containers(&profile, cat, container, sequence)
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

pub(super) async fn reply(
    message: &Message,
    cat: &Catalogue,
    mailbox: Arc<Mutex<PlatformMailbox>>,
) -> io::Result<Option<Replies>> {
    let body = &message.body;
    let Some(bytes) = body.get(..2) else {
        return Ok(None);
    };
    let selector = u16::from_be_bytes(bytes.try_into().expect("selector"));
    if message.command == 0x20 && selector == 1 {
        let response = profile_request(mailbox, "readProfile", json!({})).await?;
        let profile = Profile::parse(&response["profile"], cat)?;
        return if profile.formation.is_empty() {
            Ok(Some(vec![(
                0x20,
                super::room::match_start_response(body, INVALID)?,
            )]))
        } else {
            Ok(None)
        };
    }
    if message.command == 0x28 && selector == 1 {
        let (container, sequence) = codec::container_request(body)?;
        if container != 1 && container != 2 {
            return Ok(Some(vec![(
                0x28,
                codec::empty_container(container, sequence),
            )]));
        }
        let response = profile_request(mailbox, "readProfile", json!({})).await?;
        let profile = Profile::parse(&response["profile"], cat)?;
        return Ok(Some(
            codec::containers(&profile, cat, container, sequence)
                .into_iter()
                .map(|body| (0x28, body))
                .collect(),
        ));
    }
    if message.command != 0x22 || ![3, 5, 8, 14].contains(&selector) {
        return Ok(None);
    }
    if selector == 3 {
        if body.len() != 3 {
            return Err(invalid());
        }
        let response = profile_request(mailbox, "readProfile", json!({})).await?;
        let profile = Profile::parse(&response["profile"], cat)?;
        return Ok(Some(vec![(0x22, codec::formations(&profile, cat))]));
    }
    if selector == 8 {
        return Ok(Some(vec![(
            0x22,
            codec::result(
                8,
                if body == &[0, 8, 0, 0, 0, 1] {
                    0
                } else {
                    INVALID
                },
            ),
        )]));
    }
    let decoded = if selector == 5 {
        codec::decode_formation(body, cat)
    } else {
        codec::decode_weapons(body, cat)
    };
    let request = match decoded {
        Ok(value) => value,
        Err(_) => return Ok(Some(vec![(0x22, codec::result(selector, INVALID))])),
    };
    let hero = request["heroId"].as_i64().map(|id| id as i32);
    let operation = if selector == 5 {
        "setFormation"
    } else {
        "setWeapons"
    };
    let response = profile_request(mailbox, operation, request).await?;
    if response["success"] == false {
        let error = if response["error"] == "invalidProfile" {
            INVALID
        } else {
            SAVE_FAILED
        };
        return Ok(Some(vec![(0x22, codec::result(selector, error))]));
    }
    let previous = Profile::parse(&response["previous"], cat)?;
    let profile = Profile::parse(&response["profile"], cat)?;
    if profile.revision < previous.revision || profile.revision > previous.revision + 1 {
        return Err(invalid());
    }
    let mut replies = vec![(0x22, codec::result(selector, 0))];
    if let Some(id) = hero {
        replies.push((0x22, codec::weapon_notify(&profile, cat, id)));
    }
    let changed = if let Some(id) = hero {
        BTreeSet::from([id])
    } else {
        previous
            .formation
            .iter()
            .copied()
            .collect::<BTreeSet<_>>()
            .symmetric_difference(&profile.formation.iter().copied().collect())
            .copied()
            .collect()
    };
    for id in changed {
        replies.push((0x28, codec::hero_update(&profile, cat, id)));
    }
    replies.extend(
        codec::containers(&profile, cat, 2, 0)
            .into_iter()
            .map(|body| (0x28, body)),
    );
    replies.push((0x22, codec::formations(&profile, cat)));
    Ok(Some(replies))
}
