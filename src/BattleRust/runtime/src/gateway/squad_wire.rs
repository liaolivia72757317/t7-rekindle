use crate::{
    battle_session::{MAX_SQUADS_PER_CAMP, Squad, SquadCommand, SquadRoster},
    client_battle_wire::ClientBattleMessage,
};
use std::{collections::BTreeMap, io};

pub(super) const COMMAND: u16 = 0x40;

pub(super) fn decode(command: u16, body: &[u8]) -> io::Result<Option<SquadCommand>> {
    if command != COMMAND {
        return Ok(None);
    }
    if body.len() < 2 {
        return Err(invalid("truncated squad request"));
    }
    let selector = u16::from_be_bytes(body[..2].try_into().expect("checked size"));
    let request = match (selector, body.len()) {
        (1, 3) => SquadCommand::Create,
        (4, 3) => SquadCommand::Dismiss,
        (10, 3) => SquadCommand::Leave,
        (7, 6) => SquadCommand::Join(i32::from_be_bytes(
            body[2..6].try_into().expect("checked size"),
        )),
        (16, 6) => match i32::from_be_bytes(body[2..6].try_into().expect("checked size")) {
            1 => SquadCommand::SetJoinable(true),
            2 => SquadCommand::SetJoinable(false),
            _ => return Err(invalid("invalid squad state")),
        },
        _ => return Err(invalid("invalid squad request")),
    };
    Ok(Some(request))
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(super) enum SpawnRequest {
    System,
    Squad { target_id: u32, position: [f64; 3] },
}

pub(super) fn decode_spawn(body: &[u8], rids: &BTreeMap<u32, u16>) -> io::Result<SpawnRequest> {
    if body.len() == 5 && body[..4] == [0, 100, 0, 1] {
        return Ok(SpawnRequest::System);
    }
    if body.len() != 24 || body[..4] != [0, 100, 0, 3] {
        return Err(invalid("invalid actor PLAY request"));
    }
    let rid = u64::from_be_bytes(body[4..12].try_into().expect("checked size"));
    let target_id = rids
        .iter()
        .find_map(|(&id, &value)| (u64::from(value) == rid).then_some(id))
        .unwrap_or(0);
    let position = std::array::from_fn(|i| {
        f64::from(f32::from_be_bytes(
            body[12 + i * 4..16 + i * 4]
                .try_into()
                .expect("checked size"),
        ))
    });
    Ok(SpawnRequest::Squad {
        target_id,
        position,
    })
}

pub(super) fn response(command: SquadCommand, result: i32) -> ClientBattleMessage {
    let selector = match command {
        SquadCommand::Create => 2,
        SquadCommand::Dismiss => 5,
        SquadCommand::Join(_) => 8,
        SquadCommand::Leave => 11,
        SquadCommand::SetJoinable(_) => 17,
    };
    let mut message = message(selector);
    put(&mut message.body, result);
    message
}

pub(super) fn changes(
    before: &SquadRoster,
    after: &SquadRoster,
    rids: &BTreeMap<u32, u16>,
) -> io::Result<Vec<ClientBattleMessage>> {
    let mut messages = Vec::new();
    for old in &before.entries {
        let next = after
            .entries
            .iter()
            .find(|s| s.id == old.id && s.camp == old.camp);
        if let Some(next) = next {
            for member in &old.members {
                if !next.members.iter().any(|m| m.actor_id == member.actor_id) {
                    let mut msg = identified(12, old);
                    put_rid(&mut msg.body, member.actor_id, rids)?;
                    messages.push(msg);
                }
            }
            if next.leader != old.leader {
                let mut msg = identified(15, next);
                put_rid(&mut msg.body, next.leader, rids)?;
                messages.push(msg);
            }
            if next.joinable != old.joinable {
                let mut msg = identified(18, next);
                put(&mut msg.body, state(next));
                messages.push(msg);
            }
        } else {
            messages.push(identified(6, old));
        }
    }
    for squad in &after.entries {
        let old = before
            .entries
            .iter()
            .find(|s| s.id == squad.id && s.camp == squad.camp);
        if old.is_none() {
            let mut msg = message(3);
            for value in [squad.id, state(squad), i32::from(squad.camp)] {
                put(&mut msg.body, value);
            }
            put_rid(&mut msg.body, squad.leader, rids)?;
            messages.push(msg);
        }
        for member in &squad.members {
            if old.is_none() && member.actor_id == squad.leader {
                continue;
            }
            if old.is_some_and(|s| s.members.iter().any(|m| m.actor_id == member.actor_id)) {
                continue;
            }
            let mut msg = identified(9, squad);
            put_rid(&mut msg.body, member.actor_id, rids)?;
            msg.body
                .extend_from_slice(&member.joined_at_s.to_be_bytes());
            messages.push(msg);
        }
    }
    Ok(messages)
}

/// UPDATE_INST's optional red/blue squad arrays follow if_support_squad.
pub(super) fn enable(
    mut update: ClientBattleMessage,
    squads: &SquadRoster,
    rids: &BTreeMap<u32, u16>,
) -> io::Result<ClientBattleMessage> {
    if update.command != 10
        || update.body.len() != 62
        || update.body[..2] != [0, 100]
        || update.body[61] != 0
    {
        return Err(invalid("unexpected instance squad envelope"));
    }
    update.body[61] = 1;
    for camp in [2, 1] {
        let entries: Vec<_> = squads.entries.iter().filter(|s| s.camp == camp).collect();
        update
            .body
            .extend_from_slice(&(MAX_SQUADS_PER_CAMP as i16).to_be_bytes());
        update
            .body
            .extend_from_slice(&(entries.len() as i16).to_be_bytes());
        for squad in entries {
            put(&mut update.body, squad.id);
            put(&mut update.body, state(squad));
            put_rid(&mut update.body, squad.leader, rids)?;
            update
                .body
                .extend_from_slice(&(squad.members.len() as i16).to_be_bytes());
            for member in &squad.members {
                put_rid(&mut update.body, member.actor_id, rids)?;
                update
                    .body
                    .extend_from_slice(&member.joined_at_s.to_be_bytes());
            }
        }
    }
    Ok(update)
}

fn state(squad: &Squad) -> i32 {
    if squad.joinable { 1 } else { 2 }
}
fn message(selector: u16) -> ClientBattleMessage {
    ClientBattleMessage {
        command: COMMAND,
        body: selector.to_be_bytes().to_vec(),
    }
}
fn identified(selector: u16, squad: &Squad) -> ClientBattleMessage {
    let mut msg = message(selector);
    put(&mut msg.body, squad.id);
    put(&mut msg.body, i32::from(squad.camp));
    msg
}
fn put(body: &mut Vec<u8>, value: i32) {
    body.extend_from_slice(&value.to_be_bytes());
}
fn put_rid(body: &mut Vec<u8>, id: u32, rids: &BTreeMap<u32, u16>) -> io::Result<()> {
    let rid = rids
        .get(&id)
        .filter(|&&rid| rid != 0)
        .ok_or_else(|| invalid("squad actor has no client RID"))?;
    body.extend_from_slice(&u64::from(*rid).to_be_bytes());
    Ok(())
}
fn invalid(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

#[cfg(test)]
#[path = "squad_wire_tests.rs"]
mod tests;
