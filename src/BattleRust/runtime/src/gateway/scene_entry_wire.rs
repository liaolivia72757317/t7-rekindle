use super::{platform::Identity, scene_actor_wire::SceneRoster, scene_vision_wire, scene_wire};
use crate::client_battle_wire::{self, ClientBattleMessage};
use std::io;

pub(super) fn battle_entry(
    now_ms: u64,
    instance_started_at_ms: u64,
    roster: &SceneRoster,
    identity: &Identity,
) -> io::Result<Vec<ClientBattleMessage>> {
    let elapsed = now_ms
        .checked_sub(instance_started_at_ms)
        .and_then(|value| u32::try_from(value).ok())
        .ok_or_else(|| invalid("battle entry is outside instance clock"))?;
    if roster.bots.len() >= t7r_battle_core::MAX_BATTLE_ACTORS || roster.local.client_rid == 0 {
        return Err(invalid("invalid battle entry roster"));
    }
    let mut request = 7_u16.to_be_bytes().to_vec();
    request.extend_from_slice(
        &i32::try_from(1 + roster.bots.len())
            .expect("bounded roster")
            .to_be_bytes(),
    );
    for rid in
        std::iter::once(roster.local.client_rid).chain(roster.bots.iter().map(|bot| bot.client_rid))
    {
        request.extend_from_slice(&u64::from(rid).to_be_bytes());
    }
    request.push(0);
    let mut views = scene_vision_wire::objects(&request, roster, identity)?.into_iter();
    let local = views
        .next()
        .ok_or_else(|| invalid("local VISION view is missing"))?;
    let mut messages = Vec::with_capacity(8 + roster.bots.len() * 3);
    messages.push(scene_wire::actor_state(roster.local.client_rid, 6, now_ms)?);
    messages.push(vision_del(roster.local.client_rid));
    messages.push(local);
    if roster.local_shields.len() > 4 {
        return Err(invalid("too many local shields at battle entry"));
    }
    for &(weapon_id, health) in &roster.local_shields {
        let slot = roster
            .local
            .equipment
            .iter()
            .find(|item| item.weapon_id == weapon_id)
            .ok_or_else(|| invalid("battle-entry shield has no local equipment slot"))?
            .slot;
        messages.push(client_battle_wire::encode_shield_health_for_client(
            weapon_id, slot, health, health,
        )?);
    }
    messages.push(client_battle_wire::encode_guard_value_for_client(
        roster.guard_max,
    )?);
    messages.push(scene_wire::actor_state(roster.local.client_rid, 8, now_ms)?);
    // Keep native direction input current while the controller waits for GAME.
    messages.push(move_active(elapsed, false));
    messages.push(idle_state(roster.local.client_rid, 1, elapsed));
    for bot in &roster.bots {
        let user_id = 9_999_u32
            .checked_add(bot.actor_id)
            .ok_or_else(|| invalid("bot user id overflow"))?;
        messages.push(scene_wire::member_state(
            user_id,
            bot.client_rid,
            6,
            now_ms,
        )?);
        messages.push(vision_del(bot.client_rid));
        messages.push(
            views
                .next()
                .ok_or_else(|| invalid("bot VISION view is missing"))?,
        );
    }
    if views.next().is_some() {
        return Err(invalid("battle entry VISION count mismatch"));
    }
    Ok(messages)
}

pub(super) fn vision_del(client_rid: u16) -> ClientBattleMessage {
    let mut body = 2_u16.to_be_bytes().to_vec();
    body.extend_from_slice(&1_i32.to_be_bytes());
    body.extend_from_slice(&1_i32.to_be_bytes());
    body.extend_from_slice(&u64::from(client_rid).to_be_bytes());
    body.extend_from_slice(&0_u64.to_be_bytes());
    ClientBattleMessage { command: 14, body }
}

pub(super) fn idle_state(client_rid: u16, sequence: u16, elapsed_ms: u32) -> ClientBattleMessage {
    let mut body = 3_u16.to_be_bytes().to_vec();
    for field in [client_rid, sequence, 2] {
        body.extend_from_slice(&field.to_be_bytes());
    }
    body.extend_from_slice(&elapsed_ms.to_be_bytes());
    body.extend_from_slice(&0_u16.to_be_bytes());
    ClientBattleMessage { command: 4, body }
}

pub(super) fn move_active(elapsed_ms: u32, active: bool) -> ClientBattleMessage {
    let mut body = 0x1f_u16.to_be_bytes().to_vec();
    body.extend_from_slice(&elapsed_ms.to_be_bytes());
    body.extend_from_slice(&1_u16.to_be_bytes());
    body.push(u8::from(active));
    ClientBattleMessage { command: 2, body }
}

fn invalid(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

#[cfg(test)]
mod tests {
    use super::super::scene_actor_wire::LocalSceneActor;
    use super::*;
    use t7r_battle_core::action::EquipmentItem;

    fn hex(bytes: &[u8]) -> String {
        bytes.iter().map(|byte| format!("{byte:02x}")).collect()
    }

    #[test]
    fn play_and_entry_match_original_wire_and_bound_rids() {
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
            local: actor,
            bots: vec![LocalSceneActor {
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
            }],
            bot_names: vec!["Fixture0".into()],
            kill_target: 10,
            guard_max: 5.,
            local_shields: vec![],
        };
        let entry = battle_entry(9000, 5000, &roster, &identity).unwrap();
        assert_eq!(entry.len(), 10);
        let rows = include_str!(
            "../../../../../tests/fixtures/protocol/instance-battle-entry-wire-v1.txt"
        )
        .lines()
        .map(|line| line.split_whitespace().collect::<Vec<_>>())
        .collect::<Vec<_>>();
        for (index, name) in [
            (0, "local-in-scene"),
            (1, "local-vision-del"),
            (3, "guard-value"),
            (4, "local-ready"),
            (5, "local-move-inactive"),
            (6, "local-idle"),
            (7, "bot-in-scene"),
            (8, "bot-vision-del"),
        ] {
            let row = rows.iter().find(|row| row[0] == name).unwrap();
            assert_eq!(entry[index].command, row[1].parse::<u16>().unwrap());
            assert_eq!(hex(&entry[index].body), row[2]);
        }
        let player_view = include_str!(
            "../../../../../tests/fixtures/protocol/instance-actor-import-wire-v1.txt"
        )
        .lines()
        .find(|row| row.starts_with("actor-vision "))
        .unwrap()
        .split_whitespace()
        .collect::<Vec<_>>();
        assert_eq!(hex(&entry[2].body), player_view[2]);
        let bot_view = include_str!(
            "../../../../../tests/fixtures/protocol/instance-vision-objects-wire-v1.txt"
        )
        .lines()
        .find(|row| row.starts_with("bot-vision "))
        .unwrap()
        .split_whitespace()
        .collect::<Vec<_>>();
        assert_eq!(hex(&entry[9].body), bot_view[2]);
        for (name, message) in [
            (
                "play-response",
                scene_wire::actor_play(&[0, 0x64, 0, 1, 0]).unwrap(),
            ),
            ("round-start", scene_wire::round_start(9000).unwrap()),
            ("round-game", scene_wire::round_game(15000, 120000).unwrap()),
            (
                "game-local-state",
                scene_wire::actor_state(101, 6, 15000).unwrap(),
            ),
            ("game-move-active", move_active(10000, true)),
        ] {
            let row = rows.iter().find(|row| row[0] == name).unwrap();
            assert_eq!(message.command, row[1].parse::<u16>().unwrap());
            assert_eq!(hex(&message.body), row[2]);
        }
        assert!(scene_wire::actor_play(&[0, 0x64, 0, 2, 0]).is_err());
        assert!(scene_wire::actor_play(&[0, 0x64, 0, 1]).is_err());
        assert_eq!(hex(&scene_wire::actor_play_result(1).body), "006500000001");
        assert_eq!(
            hex(&scene_wire::actor_choose_hero_result(1).body),
            "003300000001"
        );
        assert_eq!(hex(&move_active(300, false).body), "001f0000012c000100");
        assert_eq!(hex(&move_active(300, true).body), "001f0000012c000101");
        let mut shielded = roster.clone();
        shielded.local.equipment.push(EquipmentItem {
            slot: 2,
            weapon_id: 703,
        });
        shielded.local_shields.push((703, 50));
        let with_shield = battle_entry(9000, 5000, &shielded, &identity).unwrap();
        assert_eq!(with_shield.len(), entry.len() + 1);
        assert_eq!(
            hex(&with_shield[3].body),
            "0006000002bf00020000003200000032"
        );
        assert_eq!(with_shield[4], entry[3]);
        assert_eq!(with_shield[5..], entry[4..]);
        assert!(battle_entry(4999, 5000, &roster, &identity).is_err());
    }
}
