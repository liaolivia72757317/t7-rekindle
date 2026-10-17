//! Original NPC, DUNGEON and training-counter packets.
use crate::{
    battle_session::training::{self, Command, Snapshot},
    client_battle_wire::{self, ClientBattleMessage},
};
use std::io;

pub(super) fn decode(command: u16, body: &[u8]) -> io::Result<Option<Command>> {
    match command {
        0x44 => {
            if body.len() != 18 || body[..2] != [0, 1] {
                return Err(invalid());
            }
            Ok(Some(Command::Talk {
                npc_rid: u64::from_be_bytes(body[2..10].try_into().expect("checked NPC")),
                talk_id: i32::from_be_bytes(body[10..14].try_into().expect("checked talk")),
                option: i32::from_be_bytes(body[14..18].try_into().expect("checked option")),
            }))
        }
        0x43 => {
            if body != [0, 1, 0, 0, 0, 0] {
                return Err(invalid());
            }
            Ok(Some(Command::Dismiss))
        }
        _ => Ok(None),
    }
}

pub(super) fn project(
    snapshot: &Snapshot,
    offset_ms: i64,
    local_rid: u16,
) -> io::Result<Vec<ClientBattleMessage>> {
    let now = client_battle_wire::wire_time(snapshot.since_ms, offset_ms)?;
    let mut guide = 4_u16.to_be_bytes().to_vec();
    guide.extend_from_slice(&now.to_be_bytes());
    guide.extend_from_slice(
        &(if snapshot.map_id == 6006 {
            40000_i32
        } else {
            40002
        })
        .to_be_bytes(),
    );
    guide.extend_from_slice(&0_i32.to_be_bytes());
    let mut state = 2_u16.to_be_bytes().to_vec();
    state.extend_from_slice(&snapshot.state.to_be_bytes());
    state.extend_from_slice(&now.to_be_bytes());
    state.extend_from_slice(&snapshot.duration_ms.to_be_bytes());
    let mut messages = vec![message(11, guide), message(67, state)];
    if snapshot.state >= 2 {
        let mut counters = 2_u16.to_be_bytes().to_vec();
        counters.extend_from_slice(&now.to_be_bytes());
        counters.extend_from_slice(&u64::from(local_rid).to_be_bytes());
        counters.extend_from_slice(&snapshot.state.to_be_bytes());
        let rows = if snapshot.map_id == 6006 {
            vec![(1_i32, snapshot.score), (6, snapshot.hits)]
        } else {
            vec![
                (1_i32, snapshot.score),
                (7, snapshot.blocks),
                (8, snapshot.parries),
            ]
        };
        counters.extend_from_slice(&(rows.len() as i32).to_be_bytes());
        for (id, value) in rows {
            counters.extend_from_slice(&id.to_be_bytes());
            counters.extend_from_slice(&1_i32.to_be_bytes());
            counters.extend_from_slice(&(value as i32).to_be_bytes());
        }
        messages.push(message(11, counters));
    }
    if snapshot.show_menu {
        let mut body = 2_u16.to_be_bytes().to_vec();
        body.extend_from_slice(&training::INSTRUCTOR_RID.to_be_bytes());
        body.extend_from_slice(&snapshot.dialog_id.to_be_bytes());
        messages.push(message(68, body));
    }
    Ok(messages)
}

pub(super) fn instructor(map_id: u32, now_ms: u64) -> ClientBattleMessage {
    let mut body = 1_u16.to_be_bytes().to_vec();
    body.extend_from_slice(&1_i32.to_be_bytes());
    body.extend_from_slice(&3_i32.to_be_bytes()); // VISION_OBJECT_MONSTER, with NPC talk capability.
    body.extend_from_slice(&training::INSTRUCTOR_RID.to_be_bytes());
    body.extend_from_slice(&7_u32.to_be_bytes());
    body.extend_from_slice(&[0xD1, 0xB5, 0xC1, 0xB7, 0xB9, 0xD9, 0]);
    body.extend_from_slice(&(training::INSTRUCTOR_RID as u16).to_be_bytes());
    body.extend_from_slice(&517_i32.to_be_bytes());
    body.extend_from_slice(&10012_i32.to_be_bytes());
    body.extend_from_slice(&[0; 37]); // hero upgrade, capabilities, avatars
    // The instructor's original hero (10012) binds its standing graph through this weapon.
    body.extend_from_slice(&2_i16.to_be_bytes());
    for (weapon, slot, active) in [(1030531_i32, 1, 1), (100000, 13, 0)] {
        body.extend_from_slice(&weapon.to_be_bytes());
        body.extend_from_slice(&[slot, 0, active]);
        body.extend_from_slice(&[0; 22]);
    }
    super::scene_vision_wire::move_data(&mut body, 1, [472.508, 442.739, 87.4901], 66, 61, -10000);
    body.extend_from_slice(&1_u16.to_be_bytes());
    body.extend_from_slice(&now_ms.to_be_bytes());
    body.extend_from_slice(&1_i16.to_be_bytes()); // ACTOR_CURR_ACT_STATE_DATA: one Idle history entry.
    body.extend_from_slice(&2_i32.to_be_bytes());
    body.extend_from_slice(&0_i32.to_be_bytes());
    body.extend_from_slice(&0_u64.to_be_bytes());
    for value in [100_i32, 100, 100, 2, 61, 0] {
        body.extend_from_slice(&value.to_be_bytes());
    }
    body.extend_from_slice(&[0; 30]);
    body.extend_from_slice(&training::entry_dialog_id(map_id).to_be_bytes());
    body.extend_from_slice(&now_ms.to_be_bytes());
    message(14, body)
}

pub(super) fn startup(now_ms: u64, duration_ms: u64) -> io::Result<Vec<ClientBattleMessage>> {
    [0, 3, 4]
        .into_iter()
        .map(|phase| {
            // PRACTICE has no selected start-pattern union arm.
            if phase == 3 {
                return Ok(message(10, vec![1, 0x94, 0, 14]));
            }
            super::scene_wire::startup_phase(phase, now_ms, duration_ms)?.ok_or_else(invalid)
        })
        .collect()
}

/// Keep the instructor out of the combat roster and the native combat sampler.
pub(super) fn split_objects(body: &[u8]) -> io::Result<(Vec<u8>, bool)> {
    if body.len() < 7 || body[..2] != [0, 7] {
        return Err(invalid());
    }
    let count = u32::from_be_bytes(body[2..6].try_into().expect("checked count")) as usize;
    if count > 60 || body.len() != 7 + count * 8 || body[body.len() - 1] > 1 {
        return Err(invalid());
    }
    let mut rids = Vec::new();
    let mut npc = false;
    for rid in body[6..body.len() - 1].chunks_exact(8) {
        if rid == training::INSTRUCTOR_RID.to_be_bytes() {
            npc = true;
        } else {
            rids.extend_from_slice(rid);
        }
    }
    let mut actors = 7_u16.to_be_bytes().to_vec();
    actors.extend_from_slice(&((rids.len() / 8) as u32).to_be_bytes());
    actors.extend_from_slice(&rids);
    actors.push(body[body.len() - 1]);
    Ok((actors, npc))
}

fn message(command: u16, body: Vec<u8>) -> ClientBattleMessage {
    ClientBattleMessage { command, body }
}
fn invalid() -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, "invalid training request")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn instructor_vision_has_equipped_weapon_and_idle_history() {
        let view = instructor(6006, 5_000);
        let body = &view.body;
        // MONSTER: name, inst_id, res_id, hero_data, weapon_data, move_data, act_state_data.
        let name_length = u32::from_be_bytes(body[18..22].try_into().unwrap()) as usize;
        let weapons_at = 22 + name_length + 2 + 4 + 41;
        let count =
            u16::from_be_bytes(body[weapons_at..weapons_at + 2].try_into().unwrap()) as usize;
        assert_eq!(count, 2, "instructor needs its native weapon and fists");
        let equipment = &body[weapons_at + 2..weapons_at + 2 + count * 29];
        assert_eq!(&equipment[..4], &1030531_u32.to_be_bytes());
        assert_eq!(&equipment[4..7], &[1, 0, 1]);
        assert_eq!(&equipment[29..33], &100000_u32.to_be_bytes());
        let action = weapons_at + 2 + count * 29 + 62;
        assert_eq!(&body[action..action + 2], &1_u16.to_be_bytes());
        assert_eq!(&body[action + 2..action + 10], &5000_u64.to_be_bytes());
        assert_eq!(&body[action + 10..action + 12], &1_i16.to_be_bytes());
        assert_eq!(&body[action + 12..action + 16], &2_i32.to_be_bytes());
        assert_eq!(&body[action + 16..action + 20], &0_i32.to_be_bytes());
        assert_eq!(body.len(), action + 20 + 8 + 24 + 30 + 4 + 8);
        assert_eq!(
            &body[body.len() - 12..body.len() - 8],
            &training::DIALOG_ID.to_be_bytes()
        );
    }

    #[test]
    fn training_startup_has_no_ordinary_round_timer() {
        let messages = startup(1000, 120_000).unwrap();
        assert!(
            messages.iter().all(|message| message.command != 10
                || !matches!(message.body[..2], [0, 102] | [0, 103]))
        );
        assert!(
            messages
                .iter()
                .any(|message| message.body == [1, 0x94, 0, 14])
        );
    }

    #[test]
    fn training_request_validation_is_exact() {
        let mut body = vec![0, 1];
        body.extend_from_slice(&61_u64.to_be_bytes());
        body.extend_from_slice(&1_i32.to_be_bytes());
        body.extend_from_slice(&1_i32.to_be_bytes());
        assert_eq!(
            decode(68, &body).unwrap(),
            Some(Command::Talk {
                npc_rid: 61,
                talk_id: 1,
                option: 1
            })
        );
        for end in 0..body.len() {
            assert!(decode(68, &body[..end]).is_err());
        }
        body.push(0);
        assert!(decode(68, &body).is_err());
        assert_eq!(
            decode(67, &[0, 1, 0, 0, 0, 0]).unwrap(),
            Some(Command::Dismiss)
        );
        assert!(decode(67, &[0, 1, 0, 0, 0, 3]).is_err());
        assert_eq!(decode(10, &[0, 1]).unwrap(), None);
    }

    #[test]
    fn training_submenus_use_native_npc_talk_notifications() {
        for dialog_id in [3, 4] {
            let mut snapshot = Snapshot {
                map_id: 6009,
                state: 0,
                since_ms: 100,
                duration_ms: 0,
                hits: 0,
                blocks: 0,
                parries: 0,
                score: 0,
                dialog_id,
                show_menu: true,
            };
            let messages = project(&snapshot, 5000, 1).unwrap();
            let menu = messages.last().unwrap();
            assert_eq!(menu.command, 68);
            assert_eq!(
                menu.body,
                [
                    2_u16.to_be_bytes().as_slice(),
                    61_u64.to_be_bytes().as_slice(),
                    dialog_id.to_be_bytes().as_slice(),
                ]
                .concat()
            );
            snapshot.show_menu = false;
            assert!(
                project(&snapshot, 5000, 1)
                    .unwrap()
                    .iter()
                    .all(|m| m.command != 68)
            );
        }
    }

    #[test]
    fn result_projects_only_current_scores_using_native_counters() {
        let snapshot = Snapshot {
            map_id: 6009,
            state: 4,
            since_ms: 100,
            duration_ms: 0,
            hits: 0,
            blocks: 2,
            parries: 1,
            score: 30,
            dialog_id: 3,
            show_menu: false,
        };
        let messages = project(&snapshot, 5000, 1).unwrap();
        assert_eq!(
            messages.iter().map(|m| m.command).collect::<Vec<_>>(),
            vec![11, 67, 11]
        );
        assert_eq!(&messages[1].body[2..6], &4_i32.to_be_bytes());
        assert_eq!(&messages[1].body[6..14], &5100_u64.to_be_bytes());
        let counters = &messages[2].body;
        assert_eq!(counters.len(), 26 + 3 * 12);
        assert_eq!(&counters[22..26], &3_i32.to_be_bytes());
        let ids = counters[26..]
            .chunks_exact(12)
            .map(|r| i32::from_be_bytes(r[..4].try_into().unwrap()))
            .collect::<Vec<_>>();
        assert_eq!(ids, vec![1, 7, 8]);
    }
}
