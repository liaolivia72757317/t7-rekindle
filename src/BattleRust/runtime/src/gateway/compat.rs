use super::platform::Identity;
use crate::client_battle_wire::ClientBattleMessage;
use std::io;
use t7r_battle_core::protocol::{Message, heartbeat};

pub fn login(identity: &Identity) -> Vec<u8> {
    let mut bytes = Vec::with_capacity(500);
    bytes.extend_from_slice(&2_u16.to_be_bytes());
    bytes.extend_from_slice(&1_i32.to_be_bytes());
    bytes.extend_from_slice(&10000_u32.to_be_bytes());
    bytes.extend_from_slice(&((identity.name.len() + 1) as u32).to_be_bytes());
    bytes.extend_from_slice(&identity.name);
    bytes.push(0);
    bytes.extend_from_slice(&identity.portrait.to_be_bytes());
    bytes.push(0);
    bytes.resize(bytes.len() + 144, 0);
    bytes.extend_from_slice(&identity.portrait.to_be_bytes());
    bytes.extend_from_slice(&identity.level.to_be_bytes());
    bytes.resize(bytes.len() + 296 - 8 + 2 + 8, 0);
    bytes.extend_from_slice(&1_u32.to_be_bytes());
    bytes.push(0);
    bytes
}

pub fn sync_login() -> Vec<u8> {
    sync_login_for_rid(1)
}

pub fn sync_login_for_rid(client_rid: u16) -> Vec<u8> {
    let mut bytes = Vec::new();
    bytes.extend_from_slice(&1_u16.to_be_bytes());
    bytes.extend_from_slice(&10000_u32.to_be_bytes());
    bytes.extend_from_slice(&u64::from(client_rid).to_be_bytes());
    bytes.resize(26, 0);
    bytes
}

pub(super) fn periodic_time_sync(now_ms: u64) -> ClientBattleMessage {
    let mut body = 11_u16.to_be_bytes().to_vec();
    body.extend_from_slice(&now_ms.to_be_bytes());
    body.extend_from_slice(&0_u64.to_be_bytes());
    body.push(1);
    ClientBattleMessage { command: 8, body }
}

// Fixed compatibility responses only; dynamic profile operations are dispatched
// separately and return success only after the platform transaction commits.
pub fn reply(
    message: &Message,
    logic: bool,
    now: u64,
    unify_at: &mut Option<u64>,
) -> io::Result<Option<Vec<u8>>> {
    let body = &message.body;
    if body.len() < 2 {
        return Err(invalid());
    }
    let selector = u16::from_be_bytes([body[0], body[1]]);
    match (message.command, selector) {
        (0x1d, 1) => heartbeat(body)
            .map(|bytes| Some(bytes.to_vec()))
            .map_err(|_| invalid()),
        (8, 9) => {
            if body.len() != 3 {
                return Err(invalid());
            }
            if unify_at.is_some_and(|last| now.saturating_sub(last) < 250) {
                return Ok(None);
            }
            let rtt = unify_at.map_or(0, |last| now.saturating_sub(last).clamp(1, 60000));
            *unify_at = Some(now);
            let mut result = 10_u16.to_be_bytes().to_vec();
            result.extend_from_slice(&now.to_be_bytes());
            result.extend_from_slice(&rtt.to_be_bytes());
            Ok(Some(result))
        }
        (1, 0x68) if logic => {
            if body.len() != 3 {
                return Err(invalid());
            }
            Ok(Some(vec![0, 0x69, 0, 0]))
        }
        (0x42, 0x30) if logic => {
            if body.len() != 6 {
                return Err(invalid());
            }
            let mut result = vec![0, 0x31, 0, 0, 0, 0];
            result.extend_from_slice(&body[2..]);
            result.resize(14, 0);
            Ok(Some(result))
        }
        (0x1e, 10) if logic => {
            if body.len() != 3 {
                return Err(invalid());
            }
            let mut result = vec![0, 11, 0, 0, 0, 1];
            result.resize(18, 0);
            result[17] = 1;
            result.resize(47, 0);
            Ok(Some(result))
        }
        _ => Ok(None),
    }
}

fn invalid() -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, "invalid client message")
}
