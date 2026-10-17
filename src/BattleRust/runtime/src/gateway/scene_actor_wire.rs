use super::{platform::Identity, scene_vision_wire};
use crate::{
    client_battle_wire::{self, ClientBattleMessage},
    gbk,
};
use std::{collections::BTreeSet, io};
use t7r_battle_core::action::EquipmentItem;

pub(super) const LOCAL_USER_ID: u32 = 10_000;

#[derive(Clone)]
pub(super) struct LocalSceneActor {
    pub actor_id: u32,
    pub client_rid: u16,
    pub camp: u8,
    pub hero_id: u32,
    pub max_health: u32,
    pub position: [f64; 3],
    pub heading: f64,
    pub equipment: Vec<EquipmentItem>,
    pub weapon_id: u32,
    pub offhand_id: u32,
}

#[derive(Clone)]
pub(super) struct SceneRoster {
    pub local: LocalSceneActor,
    pub bots: Vec<LocalSceneActor>,
    pub bot_names: Vec<String>,
    pub kill_target: u32,
    pub guard_max: f64,
    pub local_shields: Vec<(u32, u32)>,
}

struct Appearance<'a> {
    user_id: u32,
    name: &'a [u8],
    portrait: i32,
    level: i32,
    state: i32,
    start_pattern: i32,
}

pub(super) fn camp_confirmation(
    now_ms: u64,
    map_id: u32,
    roster: &SceneRoster,
    identity: &Identity,
) -> io::Result<Vec<ClientBattleMessage>> {
    let actor = &roster.local;
    if now_ms == 0
        || actor.actor_id == 0
        || actor.client_rid == 0
        || !(1..=2).contains(&actor.camp)
        || actor.hero_id == 0
    {
        return Err(invalid("invalid local actor identity"));
    }
    let mut actor_ids = BTreeSet::from([actor.actor_id]);
    let mut client_rids = BTreeSet::from([actor.client_rid]);
    for bot in &roster.bots {
        if !(2..=t7r_battle_core::MAX_BATTLE_ACTORS as u32).contains(&bot.actor_id)
            || bot.client_rid == 0
            || !matches!(bot.camp, 1 | 2)
            || !actor_ids.insert(bot.actor_id)
            || !client_rids.insert(bot.client_rid)
        {
            return Err(invalid("invalid bot scene member"));
        }
    }
    let bot_names = bot_names(&roster.bot_names, &identity.name, &roster.bots)?;
    let mut messages = Vec::with_capacity(5 + roster.bots.len() * 2);
    messages.push(ClientBattleMessage {
        command: 0x23,
        body: [4_u16.to_be_bytes().as_slice(), &0_i32.to_be_bytes()].concat(),
    });
    messages.push(scene_vision_wire::local_actor(actor, identity)?);
    messages.push(actor_basic(
        now_ms,
        actor,
        Appearance {
            user_id: LOCAL_USER_ID,
            name: &identity.name,
            portrait: identity.portrait,
            level: identity.level,
            state: 4,
            start_pattern: super::scene_wire::start_pattern(map_id),
        },
    )?);
    messages.push(camp_exchange(now_ms, actor));
    for (bot, name) in roster.bots.iter().zip(bot_names) {
        let user_id = 9_999_u32
            .checked_add(bot.actor_id)
            .ok_or_else(|| invalid("bot user id overflow"))?;
        messages.push(actor_basic(
            now_ms,
            bot,
            Appearance {
                user_id,
                name: &name,
                portrait: 7,
                level: 1,
                state: 8,
                start_pattern: super::scene_wire::start_pattern(map_id),
            },
        )?);
        messages.push(camp_exchange(now_ms, bot));
    }
    messages.push(client_battle_wire::encode_camp_kill_update(
        now_ms,
        1,
        [0, 0],
        Some(roster.kill_target),
    )?);
    Ok(messages)
}

fn actor_basic(
    now_ms: u64,
    actor: &LocalSceneActor,
    appearance: Appearance<'_>,
) -> io::Result<ClientBattleMessage> {
    let hero_id =
        i32::try_from(actor.hero_id).map_err(|_| invalid("hero id exceeds client wire range"))?;
    if appearance.name.is_empty()
        || appearance.name.len() >= 32
        || appearance.name.contains(&0)
        || appearance.level <= 0
        || appearance.portrait < 0
    {
        return Err(invalid("invalid scene actor profile"));
    }

    let mut basic = 0x10_u16.to_be_bytes().to_vec();
    basic.extend_from_slice(&now_ms.to_be_bytes());
    basic.extend_from_slice(&1_u64.to_be_bytes());
    basic.extend_from_slice(&u64::from(actor.client_rid).to_be_bytes());
    basic.extend_from_slice(&appearance.user_id.to_be_bytes());
    basic.extend_from_slice(&((appearance.name.len() + 1) as u32).to_be_bytes());
    basic.extend_from_slice(appearance.name);
    basic.push(0);
    for field in [
        appearance.portrait,
        appearance.level,
        appearance.state,
        hero_id,
        i32::from(actor.camp),
    ] {
        basic.extend_from_slice(&field.to_be_bytes());
    }
    for _ in 0..5 {
        basic.extend_from_slice(&0_i32.to_be_bytes());
    }
    basic.extend_from_slice(&hero_id.to_be_bytes());
    basic.push(0);
    for _ in 0..9 {
        basic.extend_from_slice(&0_i32.to_be_bytes());
    }
    basic.extend_from_slice(&[0; 2]);
    for _ in 0..7 {
        basic.extend_from_slice(&0_i32.to_be_bytes());
    }
    basic.extend_from_slice(&0_i32.to_be_bytes());
    basic.extend_from_slice(&appearance.start_pattern.to_be_bytes());

    Ok(ClientBattleMessage {
        command: 0x0a,
        body: basic,
    })
}

fn camp_exchange(now_ms: u64, actor: &LocalSceneActor) -> ClientBattleMessage {
    let mut camp_exchange = 6_u16.to_be_bytes().to_vec();
    camp_exchange.extend_from_slice(&now_ms.to_be_bytes());
    camp_exchange.extend_from_slice(&1_i32.to_be_bytes());
    camp_exchange.extend_from_slice(&u64::from(actor.client_rid).to_be_bytes());
    camp_exchange.extend_from_slice(&i32::from(actor.camp).to_be_bytes());
    ClientBattleMessage {
        command: 0x23,
        body: camp_exchange,
    }
}

pub(super) fn bot_names(
    names: &[String],
    player_name: &[u8],
    bots: &[LocalSceneActor],
) -> io::Result<Vec<Vec<u8>>> {
    const PREFIXES: [&str; 4] = ["[进攻]", "[全能]", "[防守]", "[疯狗]"];
    let mut available = Vec::new();
    let mut unique = BTreeSet::new();
    for name in names {
        let Ok(raw) = gbk::player_name(name) else {
            continue;
        };
        if raw == player_name || !unique.insert(raw) {
            continue;
        }
        let encoded = PREFIXES
            .iter()
            .map(|prefix| gbk::player_name(&format!("{prefix}{name}")))
            .collect::<io::Result<Vec<_>>>();
        if let Ok(encoded) = encoded
            && encoded.iter().all(|value| value != player_name)
        {
            available.push(encoded);
        }
    }
    if available.len() < bots.len() {
        return Err(invalid("insufficient distinct bot display names"));
    }
    Ok(available
        .into_iter()
        .zip(bots)
        .map(|(names, bot)| names[(bot.actor_id as usize - 2) % 4].clone())
        .collect())
}

fn invalid(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn camp_confirmation_matches_original_actor_import_codecs() {
        let identity = Identity {
            name: b"Player".to_vec(),
            portrait: 7,
            level: 100,
        };
        let actor = LocalSceneActor {
            actor_id: 1,
            client_rid: 101,
            camp: 1,
            hero_id: 1101,
            max_health: 100,
            position: [1.25, 20.5, -2.],
            heading: 45.,
            equipment: vec![EquipmentItem {
                slot: 1,
                weapon_id: 701,
            }],
            weapon_id: 701,
            offhand_id: 0,
        };
        let roster = SceneRoster {
            local: actor.clone(),
            bots: vec![],
            bot_names: vec![],
            kill_target: 10,
            guard_max: 5.,
            local_shields: vec![],
        };
        let actual = camp_confirmation(6000, 1028, &roster, &identity).unwrap();
        let rows = include_str!(
            "../../../../../tests/fixtures/protocol/instance-actor-import-wire-v1.txt"
        )
        .lines()
        .map(|line| line.split_whitespace().collect::<Vec<_>>())
        .collect::<Vec<_>>();
        assert_eq!(actual.len(), rows.len());
        for (message, row) in actual.iter().zip(rows) {
            assert_eq!(message.command, row[1].parse::<u16>().unwrap());
            assert_eq!(
                message
                    .body
                    .iter()
                    .map(|byte| format!("{byte:02x}"))
                    .collect::<String>(),
                row[2]
            );
        }
        let bot = LocalSceneActor {
            actor_id: 2,
            client_rid: 202,
            camp: 2,
            hero_id: 1101,
            max_health: 100,
            position: [0., 40., 0.],
            heading: 0.,
            equipment: vec![EquipmentItem {
                slot: 1,
                weapon_id: 701,
            }],
            weapon_id: 701,
            offhand_id: 0,
        };
        let with_bot = SceneRoster {
            bots: vec![bot.clone()],
            bot_names: vec!["Fixture0".into()],
            ..roster.clone()
        };
        let imported = camp_confirmation(6000, 1028, &with_bot, &identity).unwrap();
        assert_eq!(imported.len(), 7);
        for (message, row) in imported[4..6].iter().zip(
            include_str!("../../../../../tests/fixtures/protocol/instance-bot-import-wire-v1.txt")
                .lines(),
        ) {
            let row = row.split_whitespace().collect::<Vec<_>>();
            assert_eq!(message.command, row[1].parse::<u16>().unwrap());
            assert_eq!(
                message
                    .body
                    .iter()
                    .map(|byte| format!("{byte:02x}"))
                    .collect::<String>(),
                row[2]
            );
        }
        assert_eq!(imported[6], actual[4]);
        assert!(
            camp_confirmation(
                6000,
                1028,
                &SceneRoster {
                    bot_names: vec![],
                    ..with_bot.clone()
                },
                &identity
            )
            .is_err()
        );
        assert!(
            camp_confirmation(
                6000,
                1028,
                &SceneRoster {
                    bots: vec![LocalSceneActor {
                        client_rid: 101,
                        ..bot
                    }],
                    ..with_bot
                },
                &identity
            )
            .is_err()
        );
        assert!(
            camp_confirmation(
                6000,
                1028,
                &SceneRoster {
                    local: LocalSceneActor {
                        client_rid: 0,
                        ..actor.clone()
                    },
                    ..roster.clone()
                },
                &identity
            )
            .is_err()
        );
        assert!(
            camp_confirmation(
                6000,
                1028,
                &SceneRoster {
                    local: LocalSceneActor {
                        camp: 3,
                        ..actor.clone()
                    },
                    ..roster.clone()
                },
                &identity
            )
            .is_err()
        );
        assert!(
            camp_confirmation(
                6000,
                1028,
                &SceneRoster {
                    local: LocalSceneActor {
                        hero_id: u32::MAX,
                        ..actor.clone()
                    },
                    ..roster.clone()
                },
                &identity
            )
            .is_err()
        );
        let invalid_name = Identity {
            name: vec![b'X'; 32],
            ..identity
        };
        assert!(camp_confirmation(6000, 1028, &roster, &invalid_name).is_err());
    }
}
