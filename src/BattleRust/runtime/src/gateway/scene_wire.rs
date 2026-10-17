use crate::client_battle_wire::ClientBattleMessage;
use std::{
    collections::{BTreeMap, BTreeSet},
    io,
};

#[derive(Clone, Copy)]
pub(super) struct SceneBinding<'a> {
    pub started_at_ms: u64,
    pub map_id: u32,
    pub camp: u8,
    pub actor_rids: &'a BTreeMap<u32, u16>,
}

pub(super) const STARTUP_PHASES: usize = 5;
pub(super) const PREPARE_MS: u64 = 30_000;
pub(super) const START_MS: u64 = 5_999;

pub(super) fn start_pattern(map_id: u32) -> i32 {
    if t7r_battle_resources::training_maps::contains(map_id) {
        14
    } else {
        2
    }
}

#[link(name = "kernel32")]
unsafe extern "system" {
    #[link_name = "GetTickCount64"]
    fn get_tick_count_64() -> u64;
}

pub(super) fn native_uptime_ms() -> u64 {
    unsafe { get_tick_count_64() }
}

pub(super) fn instance_start(
    now_ms: u64,
    map_id: u32,
    client_rid: u16,
) -> io::Result<[ClientBattleMessage; 2]> {
    if client_rid == 0 {
        return Err(invalid("invalid local client RID"));
    }
    Ok([
        ClientBattleMessage {
            command: 0x2c,
            body: super::compat::sync_login_for_rid(client_rid),
        },
        instance_update(now_ms, now_ms, map_id)?,
    ])
}

fn instance_update(
    now_ms: u64,
    started_at_ms: u64,
    map_id: u32,
) -> io::Result<ClientBattleMessage> {
    let map_id = i32::try_from(map_id).map_err(|_| invalid("map id exceeds client wire range"))?;
    if started_at_ms == 0 || now_ms < started_at_ms || map_id == 0 {
        return Err(invalid("invalid instance start identity"));
    }
    let mut update = 0x64_u16.to_be_bytes().to_vec();
    update.extend_from_slice(&now_ms.to_be_bytes());
    update.extend_from_slice(&1_u64.to_be_bytes());
    update.extend_from_slice(&started_at_ms.to_be_bytes());
    update.extend_from_slice(&map_id.to_be_bytes());
    update.extend_from_slice(&start_pattern(map_id as u32).to_be_bytes());
    update.push(0);
    for _ in 0..2 {
        update.extend_from_slice(&1_u32.to_be_bytes());
        update.push(0);
    }
    update.extend_from_slice(&[0; 17]);
    Ok(ClientBattleMessage {
        command: 0x0a,
        body: update,
    })
}

pub(super) fn reply(
    command: u16,
    body: &[u8],
    now_ms: u64,
    binding: &SceneBinding<'_>,
) -> io::Result<Option<Vec<ClientBattleMessage>>> {
    if body.len() < 2 {
        return Ok(None);
    }
    let selector = u16::from_be_bytes([body[0], body[1]]);
    match (command, selector) {
        (10, 0x6a) => Ok(Some(vec![instance_update(
            now_ms,
            binding.started_at_ms,
            binding.map_id,
        )?])),
        (10, 0x0b) => {
            if body.len() != 3 {
                return Err(invalid("invalid instance hero-selection request"));
            }
            Ok(Some(vec![ClientBattleMessage {
                command: 10,
                body: body.to_vec(),
            }]))
        }
        (0x23, 1) => {
            if body.len() != 6 || !(1..=2).contains(&binding.camp) {
                return Err(invalid("invalid instance camp-choice request"));
            }
            let requested = i32::from_be_bytes(body[2..6].try_into().expect("checked length"));
            if requested != i32::from(binding.camp) {
                return Err(invalid("instance camp differs from bound battle member"));
            }
            let mut reply = 2_u16.to_be_bytes().to_vec();
            reply.extend_from_slice(&0_i32.to_be_bytes());
            reply.extend_from_slice(&requested.to_be_bytes());
            reply.extend_from_slice(&[0; 8]);
            Ok(Some(vec![ClientBattleMessage {
                command: 0x23,
                body: reply,
            }]))
        }
        (0x0e, 5) => Ok(Some(vec![vision_list(body, binding.actor_rids)?])),
        _ => Ok(None),
    }
}

pub(super) fn vision_list(
    body: &[u8],
    actor_rids: &BTreeMap<u32, u16>,
) -> io::Result<ClientBattleMessage> {
    if body.len() != 6
        || actor_rids.is_empty()
        || actor_rids.len() > t7r_battle_core::MAX_BATTLE_ACTORS
    {
        return Err(invalid("invalid instance vision-list request or roster"));
    }
    let rids = actor_rids.values().copied().collect::<BTreeSet<_>>();
    if rids.len() != actor_rids.len() || rids.contains(&0) {
        return Err(invalid("invalid instance vision RID binding"));
    }
    let count = i32::try_from(actor_rids.len()).map_err(|_| invalid("vision roster too large"))?;
    let mut response = 6_u16.to_be_bytes().to_vec();
    response.extend_from_slice(&body[2..6]);
    response.extend_from_slice(&[0, 1]);
    response.extend_from_slice(&count.to_be_bytes());
    response.extend_from_slice(&count.to_be_bytes());
    for &rid in actor_rids.values() {
        response.extend_from_slice(&u64::from(rid).to_be_bytes());
    }
    Ok(ClientBattleMessage {
        command: 0x0e,
        body: response,
    })
}

pub(super) fn startup_phase(
    index: usize,
    now_ms: u64,
    duration_ms: u64,
) -> io::Result<Option<ClientBattleMessage>> {
    if now_ms == 0 || duration_ms == 0 {
        return Err(invalid("invalid instance round clock"));
    }
    let mut body = Vec::new();
    match index {
        0 => {
            body.extend_from_slice(&0x65_u16.to_be_bytes());
            body.extend_from_slice(&now_ms.to_be_bytes());
            body.extend_from_slice(&1_u64.to_be_bytes());
            body.extend_from_slice(&1_i32.to_be_bytes());
        }
        1 => {
            body.extend_from_slice(&0x66_u16.to_be_bytes());
            body.extend_from_slice(&now_ms.to_be_bytes());
            body.extend_from_slice(&1_u64.to_be_bytes());
            for value in [1_i32, 0, 0, 0, 0] {
                body.extend_from_slice(&value.to_be_bytes());
            }
            body.extend_from_slice(&now_ms.to_be_bytes());
            body.extend_from_slice(&0_u64.to_be_bytes());
            for value in [0_i32, 0, 2, 3] {
                body.extend_from_slice(&value.to_be_bytes());
            }
            body.extend_from_slice(&0_u64.to_be_bytes());
            body.extend_from_slice(&4_i32.to_be_bytes());
            body.extend_from_slice(&duration_ms.to_be_bytes());
            body.extend_from_slice(&0_i32.to_be_bytes());
        }
        2 => {
            body.extend_from_slice(&0x67_u16.to_be_bytes());
            body.extend_from_slice(&now_ms.to_be_bytes());
            body.extend_from_slice(&1_u64.to_be_bytes());
            body.extend_from_slice(&2_i32.to_be_bytes());
            body.extend_from_slice(&now_ms.to_be_bytes());
            body.extend_from_slice(&30_000_u64.to_be_bytes());
            body.extend_from_slice(&0_i32.to_be_bytes());
        }
        3 => {
            body.extend_from_slice(&0x194_u16.to_be_bytes());
            body.extend_from_slice(&7_u16.to_be_bytes());
            body.extend_from_slice(&[0; 32]);
        }
        4 => {
            body.extend_from_slice(&0xc8_u16.to_be_bytes());
            body.extend_from_slice(&now_ms.to_be_bytes());
            for value in [0_i32, 1, 0] {
                body.extend_from_slice(&value.to_be_bytes());
            }
        }
        _ => return Ok(None),
    }
    Ok(Some(ClientBattleMessage { command: 10, body }))
}

pub(super) fn actor_state(
    client_rid: u16,
    state: i32,
    now_ms: u64,
) -> io::Result<ClientBattleMessage> {
    member_state(10_000, client_rid, state, now_ms)
}

pub(super) fn member_state(
    user_id: u32,
    client_rid: u16,
    state: i32,
    now_ms: u64,
) -> io::Result<ClientBattleMessage> {
    if user_id == 0 || client_rid == 0 || !matches!(state, 5..=9) || now_ms == 0 {
        return Err(invalid("invalid local actor state"));
    }
    let mut body = 1_u16.to_be_bytes().to_vec();
    body.extend_from_slice(&user_id.to_be_bytes());
    body.extend_from_slice(&u64::from(client_rid).to_be_bytes());
    body.extend_from_slice(&state.to_be_bytes());
    body.extend_from_slice(&now_ms.to_be_bytes());
    Ok(ClientBattleMessage { command: 54, body })
}

pub(super) fn actor_play(body: &[u8]) -> io::Result<ClientBattleMessage> {
    if body.len() != 5 || body[..4] != [0, 0x64, 0, 1] {
        return Err(invalid("invalid actor SYSTEM PLAY request"));
    }
    Ok(actor_play_result(0))
}

pub(super) fn actor_play_result(result: i32) -> ClientBattleMessage {
    let mut body = 0x65_u16.to_be_bytes().to_vec();
    body.extend_from_slice(&result.to_be_bytes());
    ClientBattleMessage {
        command: 0x36,
        body,
    }
}

pub(super) fn round_start(now_ms: u64) -> io::Result<ClientBattleMessage> {
    round_state(now_ms, 3, START_MS)
}

pub(super) fn round_game(now_ms: u64, duration_ms: u64) -> io::Result<ClientBattleMessage> {
    round_state(now_ms, 4, duration_ms)
}

pub(super) fn refresh_game(
    now_ms: u64,
    started_at_ms: u64,
    duration_ms: u64,
) -> io::Result<ClientBattleMessage> {
    if started_at_ms == 0 || started_at_ms > now_ms {
        return Err(invalid("invalid GAME refresh clock"));
    }
    let mut message = round_game(now_ms, duration_ms)?;
    message.body[22..30].copy_from_slice(&started_at_ms.to_be_bytes());
    Ok(message)
}

pub(super) fn actor_load_ok(body: &[u8], now_ms: u64) -> io::Result<ClientBattleMessage> {
    if body != [0, 0x14, 0] {
        return Err(invalid("invalid actor load confirmation"));
    }
    round_state(now_ms, 2, PREPARE_MS)
}

pub(super) fn actor_choose_hero(
    body: &[u8],
    actor_rid: u16,
    hero_id: u32,
    expected_position: i32,
) -> io::Result<[ClientBattleMessage; 2]> {
    if body.len() != 6 || body[..2] != [0, 0x32] || actor_rid == 0 {
        return Err(invalid("invalid actor hero-selection request"));
    }
    let position = i32::from_be_bytes(body[2..6].try_into().expect("checked length"));
    if position <= 0 || position != expected_position {
        return Err(invalid("selected hero differs from bound battle member"));
    }
    let hero_id =
        i32::try_from(hero_id).map_err(|_| invalid("hero id exceeds client wire range"))?;
    let mut chosen = 0x34_u16.to_be_bytes().to_vec();
    chosen.extend_from_slice(&u64::from(actor_rid).to_be_bytes());
    chosen.extend_from_slice(&hero_id.to_be_bytes());
    chosen.extend_from_slice(&0_i32.to_be_bytes());
    Ok([
        actor_choose_hero_result(0),
        ClientBattleMessage {
            command: 0x36,
            body: chosen,
        },
    ])
}

pub(super) fn actor_choose_hero_result(result: i32) -> ClientBattleMessage {
    let mut body = 0x33_u16.to_be_bytes().to_vec();
    body.extend_from_slice(&result.to_be_bytes());
    ClientBattleMessage {
        command: 0x36,
        body,
    }
}

fn round_state(now_ms: u64, state: i32, duration_ms: u64) -> io::Result<ClientBattleMessage> {
    if now_ms == 0 || duration_ms == 0 || !matches!(state, 2..=5) {
        return Err(invalid("invalid instance round state"));
    }
    let mut body = 0x67_u16.to_be_bytes().to_vec();
    body.extend_from_slice(&now_ms.to_be_bytes());
    body.extend_from_slice(&1_u64.to_be_bytes());
    body.extend_from_slice(&state.to_be_bytes());
    body.extend_from_slice(&now_ms.to_be_bytes());
    body.extend_from_slice(&duration_ms.to_be_bytes());
    body.extend_from_slice(&0_i32.to_be_bytes());
    Ok(ClientBattleMessage { command: 10, body })
}

fn invalid(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn instance_start_matches_original_codec_and_rejects_invalid_map() {
        let rows =
            include_str!("../../../../../tests/fixtures/protocol/instance-start-wire-v1.txt")
                .lines()
                .map(|line| line.split_whitespace().collect::<Vec<_>>())
                .collect::<Vec<_>>();
        let actual = instance_start(5000, 1028, 1).unwrap();
        assert_eq!(rows.len(), actual.len());
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
        assert!(instance_start(0, 1028, 1).is_err());
        assert!(instance_start(5000, 0, 1).is_err());
        assert!(instance_start(5000, u32::MAX, 1).is_err());
        assert!(instance_start(5000, 1028, 0).is_err());
        let bound = instance_start(5000, 1028, 101).unwrap();
        let row = include_str!(
            "../../../../../tests/fixtures/protocol/instance-bound-identity-wire-v1.txt"
        )
        .split_whitespace()
        .collect::<Vec<_>>();
        assert_eq!(bound[0].command, row[1].parse::<u16>().unwrap());
        assert_eq!(
            bound[0]
                .body
                .iter()
                .map(|byte| format!("{byte:02x}"))
                .collect::<String>(),
            row[2]
        );
    }

    #[test]
    fn scheduled_instance_phases_match_original_codecs() {
        let rows =
            include_str!("../../../../../tests/fixtures/protocol/instance-startup-wire-v1.txt")
                .lines()
                .map(|line| line.split_whitespace().collect::<Vec<_>>())
                .collect::<Vec<_>>();
        assert_eq!(rows.len(), 5);
        for (index, row) in rows.iter().enumerate() {
            let message = startup_phase(index, 5000, 120_000).unwrap().unwrap();
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
        assert!(startup_phase(5, 5000, 120_000).unwrap().is_none());
        assert!(startup_phase(0, 0, 120_000).is_err());
        assert!(startup_phase(0, 5000, 0).is_err());
    }

    #[test]
    fn instance_refresh_and_hero_selection_match_original_codecs() {
        let rows =
            include_str!("../../../../../tests/fixtures/protocol/instance-control-wire-v1.txt")
                .lines()
                .map(|line| line.split_whitespace().collect::<Vec<_>>())
                .collect::<Vec<_>>();
        assert_eq!(rows.len(), 4);
        let rids = BTreeMap::from([(1, 101), (2, 202)]);
        let binding = SceneBinding {
            started_at_ms: 5000,
            map_id: 1028,
            camp: 1,
            actor_rids: &rids,
        };
        for row in rows {
            let request = row[2]
                .as_bytes()
                .chunks_exact(2)
                .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
                .collect::<Vec<_>>();
            let message = reply(row[1].parse().unwrap(), &request, 6000, &binding)
                .unwrap()
                .unwrap()
                .pop()
                .unwrap();
            assert_eq!(message.command, row[1].parse::<u16>().unwrap());
            assert_eq!(
                message
                    .body
                    .iter()
                    .map(|byte| format!("{byte:02x}"))
                    .collect::<String>(),
                row[3]
            );
        }
        assert!(reply(10, &[0, 0x0b], 6000, &binding).is_err());
        assert!(reply(10, &[0, 0x6a], 4999, &binding).is_err());
        assert!(reply(0x23, &[0, 1, 0, 0, 0, 2], 6000, &binding).is_err());
        assert!(reply(0x23, &[0, 1, 0, 0, 0, 1, 0], 6000, &binding).is_err());
        assert!(reply(0x0e, &[0, 5, 0], 6000, &binding).is_err());
        let invalid_camp = SceneBinding { camp: 3, ..binding };
        assert!(reply(0x23, &[0, 1, 0, 0, 0, 1], 6000, &invalid_camp).is_err());
        let duplicate = BTreeMap::from([(1, 101), (2, 101)]);
        let invalid_roster = SceneBinding {
            actor_rids: &duplicate,
            ..binding
        };
        assert!(reply(0x0e, &[0, 5, 0, 0, 0, 1], 6000, &invalid_roster).is_err());
    }

    #[test]
    fn local_actor_life_states_match_original_codec() {
        for line in
            include_str!("../../../../../tests/fixtures/protocol/instance-life-wire-v1.txt").lines()
        {
            let row = line.split_whitespace().collect::<Vec<_>>();
            let message = actor_state(
                row[2].parse().unwrap(),
                row[3].parse().unwrap(),
                row[4].parse().unwrap(),
            )
            .unwrap();
            assert_eq!(message.command, row[1].parse::<u16>().unwrap());
            assert_eq!(
                message
                    .body
                    .iter()
                    .map(|byte| format!("{byte:02x}"))
                    .collect::<String>(),
                row[5]
            );
        }
        assert!(actor_state(0, 7, 9300).is_err());
        assert!(actor_state(101, 4, 9300).is_err());
    }

    #[test]
    fn actor_load_and_hero_selection_match_original_codecs() {
        let rows =
            include_str!("../../../../../tests/fixtures/protocol/instance-load-hero-wire-v1.txt")
                .lines()
                .map(|line| line.split_whitespace().collect::<Vec<_>>())
                .collect::<Vec<_>>();
        let hero = actor_choose_hero(&[0, 0x32, 0, 0, 0, 1], 101, 1101, 1).unwrap();
        let messages = [
            actor_load_ok(&[0, 0x14, 0], 5000).unwrap(),
            actor_state(101, 5, 6000).unwrap(),
            hero[0].clone(),
            hero[1].clone(),
        ];
        assert_eq!(rows.len(), messages.len());
        for (message, row) in messages.iter().zip(rows) {
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
        assert!(actor_load_ok(&[0, 0x14, 1], 5000).is_err());
        assert!(actor_load_ok(&[0, 0x14, 0, 0], 5000).is_err());
        assert!(actor_choose_hero(&[0, 0x32, 0, 0, 0, 2], 101, 1101, 1).is_err());
        assert!(actor_choose_hero(&[0, 0x32, 0, 0, 0, 1], 0, 1101, 1).is_err());
    }
}
