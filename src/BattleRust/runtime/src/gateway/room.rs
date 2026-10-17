use super::instance::InstanceIngress;
use std::{
    io,
    sync::Arc,
    time::{Duration, Instant},
};

const ROOM_ID: u64 = 1;
const ROOM_NAME: &[u8] = b"RoomName1";
const MATCH_RESULT_DELAY: Duration = Duration::from_secs(1);
const ENTER_NOTIFY_DELAY: Duration = Duration::from_secs(2);
type Reply = (u16, Vec<u8>);
type Replies = Vec<Reply>;

pub(super) struct LobbyRoom {
    name: Vec<u8>,
    match_ingress: Option<Arc<InstanceIngress>>,
    result_due: Option<Instant>,
    enter_due: Option<Instant>,
}

impl LobbyRoom {
    pub(super) fn new() -> Self {
        Self {
            name: ROOM_NAME.to_vec(),
            match_ingress: None,
            result_due: None,
            enter_due: None,
        }
    }

    pub(super) fn reply(
        &mut self,
        command: u16,
        body: &[u8],
        ingress: Option<&Arc<InstanceIngress>>,
        instance_port: u16,
        now: Instant,
    ) -> io::Result<Option<Replies>> {
        if body.len() < 2 {
            return Err(invalid("invalid lobby room request"));
        }
        let selector = u16::from_be_bytes([body[0], body[1]]);
        if !matches!((command, selector), (0x1e, 1 | 3) | (0x20, 1)) {
            return Ok(None);
        }
        if instance_port == 0 {
            return Err(invalid("invalid lobby room request"));
        }
        if let (Some(bound), Some(current)) = (&self.match_ingress, ingress)
            && !Arc::ptr_eq(bound, current)
        {
            if self.result_due.is_some() || self.enter_due.is_some() {
                return Err(invalid("stale room match generation"));
            }
            self.match_ingress = None;
        }
        match (command, selector) {
            (0x1e, 1) => {
                let ingress = ingress.ok_or_else(|| invalid("room has no staged battle"))?;
                let (name, map_id) = decode_create(body)?;
                if map_id
                    != i32::try_from(ingress.map_id()).map_err(|_| invalid("invalid scene map"))?
                {
                    return Err(invalid("room resource differs from staged battle"));
                }
                self.name = name;
                if t7r_battle_resources::training_maps::contains(ingress.map_id()) {
                    ingress.mark_match_requested()?;
                    self.match_ingress = Some(ingress.clone());
                }
                Ok(Some(vec![
                    (0x1e, create_response(&self.name, map_id)?),
                    (0x1e, enter_response(&self.name, map_id, 0, instance_port)?),
                ]))
            }
            (0x1e, 3) => {
                let ingress = ingress.ok_or_else(|| invalid("room has no staged battle"))?;
                let reconnect = decode_enter(body)?;
                let map_id =
                    i32::try_from(ingress.map_id()).map_err(|_| invalid("invalid scene map"))?;
                Ok(Some(vec![(
                    0x1e,
                    enter_response(&self.name, map_id, reconnect, instance_port)?,
                )]))
            }
            (0x20, 1) => {
                let ingress = ingress.ok_or_else(|| invalid("match has no staged battle"))?;
                if self.match_ingress.is_some() {
                    return Err(invalid("match start repeats"));
                }
                let result = match_start_response(body, 0)?;
                ingress.mark_match_requested()?;
                self.match_ingress = Some(ingress.clone());
                self.result_due = Some(now + MATCH_RESULT_DELAY);
                self.enter_due = Some(now + ENTER_NOTIFY_DELAY);
                Ok(Some(vec![(0x20, result)]))
            }
            _ => Ok(None),
        }
    }

    pub(super) fn next_due(&self) -> Option<Instant> {
        self.result_due.into_iter().chain(self.enter_due).min()
    }

    pub(super) fn acknowledge_quit(&self, current: Option<&Arc<InstanceIngress>>) {
        if let (Some(bound), Some(current)) = (&self.match_ingress, current)
            && Arc::ptr_eq(bound, current)
        {
            bound.mark_room_quit_sent();
        }
    }

    pub(super) fn due(
        &mut self,
        now: Instant,
        current: Option<&Arc<InstanceIngress>>,
    ) -> io::Result<Option<Reply>> {
        let Some(bound) = &self.match_ingress else {
            return Ok(None);
        };
        if !current.is_some_and(|active| Arc::ptr_eq(active, bound)) {
            return Err(invalid("stale room match generation"));
        }
        if self.result_due.is_some_and(|due| now >= due) {
            self.result_due = None;
            return Ok(Some((0x20, match_result_notify())));
        }
        if self.enter_due.is_some_and(|due| now >= due) {
            self.enter_due = None;
            return Ok(Some((0x1e, enter_instance_notify(bound.camp()))));
        }
        Ok(None)
    }
}

pub(super) fn decode_create(body: &[u8]) -> io::Result<(Vec<u8>, i32)> {
    if body.len() < 11 {
        return Err(invalid("truncated room create request"));
    }
    let length = u32::from_be_bytes(body[2..6].try_into().expect("checked length")) as usize;
    if !(1..=64).contains(&length) || body.len() != 6 + length + 4 {
        return Err(invalid("invalid room name length"));
    }
    let encoded = &body[6..6 + length];
    if encoded[length - 1] != 0 || encoded[..length - 1].contains(&0) {
        return Err(invalid("invalid room name terminator"));
    }
    let map_id = i32::from_be_bytes(body[6 + length..].try_into().expect("checked length"));
    Ok((encoded[..length - 1].to_vec(), map_id))
}

fn decode_enter(body: &[u8]) -> io::Result<i32> {
    if body.len() != 14
        || u64::from_be_bytes(body[2..10].try_into().expect("checked length")) != ROOM_ID
    {
        return Err(invalid("invalid room enter request"));
    }
    Ok(i32::from_be_bytes(
        body[10..14].try_into().expect("checked length"),
    ))
}

fn tdr_string(value: &[u8]) -> io::Result<Vec<u8>> {
    if value.len() >= 64 || value.contains(&0) {
        return Err(invalid("invalid room name"));
    }
    let mut result = u32::try_from(value.len() + 1)
        .map_err(|_| invalid("room string is too long"))?
        .to_be_bytes()
        .to_vec();
    result.extend_from_slice(value);
    result.push(0);
    Ok(result)
}

fn create_response(name: &[u8], map_id: i32) -> io::Result<Vec<u8>> {
    let mut body = 2_u16.to_be_bytes().to_vec();
    body.extend_from_slice(&0_i32.to_be_bytes());
    body.extend_from_slice(&ROOM_ID.to_be_bytes());
    body.extend_from_slice(&tdr_string(name)?);
    for value in [map_id, 1, 1, 0] {
        body.extend_from_slice(&value.to_be_bytes());
    }
    Ok(body)
}

fn enter_response(
    name: &[u8],
    map_id: i32,
    reconnect: i32,
    instance_port: u16,
) -> io::Result<Vec<u8>> {
    let mut body = 4_u16.to_be_bytes().to_vec();
    body.extend_from_slice(&ROOM_ID.to_be_bytes());
    body.extend_from_slice(&0_i32.to_be_bytes());
    body.extend_from_slice(&0_i32.to_be_bytes());
    body.extend_from_slice(&reconnect.to_be_bytes());
    body.extend_from_slice(&ROOM_ID.to_be_bytes());
    body.extend_from_slice(&tdr_string(name)?);
    body.extend_from_slice(&1_i32.to_be_bytes());
    body.extend_from_slice(&0_i32.to_be_bytes());
    let url = format!("127.0.0.1:{instance_port}");
    body.extend_from_slice(&tdr_string_url(url.as_bytes())?);
    for value in [0, map_id, 1, 1, 0, 0] {
        body.extend_from_slice(&value.to_be_bytes());
    }
    Ok(body)
}

fn tdr_string_url(value: &[u8]) -> io::Result<Vec<u8>> {
    if value.len() >= 128 || value.contains(&0) {
        return Err(invalid("invalid instance endpoint"));
    }
    let mut result = u32::try_from(value.len() + 1)
        .map_err(|_| invalid("instance endpoint too long"))?
        .to_be_bytes()
        .to_vec();
    result.extend_from_slice(value);
    result.push(0);
    Ok(result)
}

pub(super) fn match_start_response(body: &[u8], result: i32) -> io::Result<Vec<u8>> {
    if !(37..=2100).contains(&body.len()) {
        return Err(invalid("match start body outside evidenced range"));
    }
    let mut response = 2_u16.to_be_bytes().to_vec();
    response.extend_from_slice(&body[6..body.len() - 1]);
    response.extend_from_slice(&result.to_be_bytes());
    response.extend_from_slice(&[0; 197]);
    Ok(response)
}

fn match_result_notify() -> Vec<u8> {
    let mut body = 3_u16.to_be_bytes().to_vec();
    body.extend_from_slice(&0_i32.to_be_bytes());
    body
}

fn enter_instance_notify(camp: u8) -> Vec<u8> {
    let mut body = 9_u16.to_be_bytes().to_vec();
    body.extend_from_slice(&ROOM_ID.to_be_bytes());
    body.extend_from_slice(&i32::from(camp).to_be_bytes());
    body.push(0);
    body
}

fn invalid(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unrelated_commands_do_not_require_or_mutate_room_state() {
        let mut room = LobbyRoom::new();
        let now = Instant::now();
        room.result_due = Some(now);
        for (command, body) in [(31, [0, 1]), (30, [0, 99])] {
            assert!(room.reply(command, &body, None, 0, now).unwrap().is_none());
            assert_eq!(room.result_due, Some(now));
            assert_eq!(room.name, ROOM_NAME);
        }
    }

    fn row(name: &str) -> Vec<u8> {
        let text = include_str!("../../../../../tests/fixtures/protocol/room-entry-wire-v1.txt");
        let hex = text
            .lines()
            .find_map(|line| {
                let (key, value) = line.split_once(' ')?;
                (key == name).then_some(value)
            })
            .expect("room corpus row");
        hex.as_bytes()
            .chunks_exact(2)
            .map(|pair| {
                u8::from_str_radix(std::str::from_utf8(pair).expect("ASCII corpus"), 16)
                    .expect("room corpus hex")
            })
            .collect()
    }

    #[test]
    fn original_room_and_match_messages_match_frozen_corpus() {
        let name = b"RoomName1";
        assert_eq!(
            decode_create(&row("create-request")).unwrap(),
            (name.to_vec(), 1028)
        );
        assert_eq!(create_response(name, 1028).unwrap(), row("create-result"));
        assert_eq!(
            enter_response(name, 1028, 0, 21003).unwrap(),
            row("enter-result")
        );
        assert_eq!(decode_enter(&row("enter-request")).unwrap(), 0);
        assert_eq!(
            match_start_response(&row("match-request"), 0).unwrap(),
            row("match-result")
        );
        assert_eq!(match_result_notify(), row("match-notify"));
        assert_eq!(enter_instance_notify(1), row("instance-notify"));
        assert!(decode_create(&[0, 1, 0, 0, 0, 0]).is_err());
        assert!(decode_enter(&[0, 3]).is_err());
        assert!(match_start_response(&[0, 1], 0).is_err());
    }
}
