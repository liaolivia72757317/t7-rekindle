use crate::client_battle_wire::ClientBattleMessage;
use std::io;

pub(super) fn decode_sync_logout(body: &[u8]) -> io::Result<u8> {
    if body.len() != 3 || body[..2] != 2_u16.to_be_bytes() {
        return Err(invalid("invalid instance sync logout request"));
    }
    Ok(body[2])
}

pub(super) fn instance_leave(now_ms: u64) -> ClientBattleMessage {
    let mut body = 3_u16.to_be_bytes().to_vec();
    for value in [now_ms, 1, 1] {
        body.extend_from_slice(&value.to_be_bytes());
    }
    ClientBattleMessage { command: 10, body }
}

pub(super) fn sync_logout_response(now_ms: u64, back_data: u8) -> ClientBattleMessage {
    let mut body = 3_u16.to_be_bytes().to_vec();
    body.extend_from_slice(&now_ms.to_be_bytes());
    body.extend_from_slice(&0_i32.to_be_bytes());
    body.push(back_data);
    ClientBattleMessage {
        command: 0x2c,
        body,
    }
}

pub(super) fn user_group_quit_response(body: &[u8]) -> io::Result<ClientBattleMessage> {
    if body.len() != 14 || body[..2] != 1_u16.to_be_bytes() {
        return Err(invalid("invalid user group quit request"));
    }
    let mut reply = 2_u16.to_be_bytes().to_vec();
    reply.extend_from_slice(&body[2..]);
    reply.extend_from_slice(&0_i32.to_be_bytes());
    Ok(ClientBattleMessage {
        command: 0x37,
        body: reply,
    })
}

fn invalid(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row(name: &str) -> Vec<u8> {
        let text = include_str!("../../../../../tests/fixtures/protocol/leave-wire-v1.txt");
        let hex = text
            .lines()
            .find_map(|line| {
                let (key, value) = line.split_once(' ')?;
                (key == name).then_some(value)
            })
            .expect("leave corpus row");
        hex.as_bytes()
            .chunks_exact(2)
            .map(|pair| {
                u8::from_str_radix(std::str::from_utf8(pair).expect("ASCII corpus"), 16)
                    .expect("leave corpus hex")
            })
            .collect()
    }

    #[test]
    fn original_leave_messages_match_frozen_corpus() {
        assert_eq!(decode_sync_logout(&row("sync-logout-request")).unwrap(), 1);
        assert_eq!(instance_leave(5000).body, row("instance-leave"));
        assert_eq!(
            sync_logout_response(5000, 1).body,
            row("sync-logout-response")
        );
        assert_eq!(
            user_group_quit_response(&row("user-group-quit-request"))
                .unwrap()
                .body,
            row("user-group-quit-response")
        );
        assert!(decode_sync_logout(&[0, 2]).is_err());
        assert!(user_group_quit_response(&[0, 1]).is_err());
    }
}
