use super::{
    platform::Identity,
    scene_actor_wire::{self, LocalSceneActor, SceneRoster},
};
use crate::client_battle_wire::ClientBattleMessage;
use std::{collections::BTreeSet, io};

pub(super) fn local_actor(
    actor: &LocalSceneActor,
    identity: &Identity,
) -> io::Result<ClientBattleMessage> {
    actor_view(actor, 10_000, &identity.name, identity.level, None)
}

fn actor_view(
    actor: &LocalSceneActor,
    user_id: u32,
    name: &[u8],
    level: i32,
    health: Option<u32>,
) -> io::Result<ClientBattleMessage> {
    let health = health.unwrap_or(actor.max_health);
    if actor.client_rid == 0
        || !matches!(actor.camp, 1 | 2)
        || name.is_empty()
        || name.len() >= 32
        || name.contains(&0)
        || level <= 0
        || actor.max_health == 0
        || actor.max_health > i32::MAX as u32
        || health > actor.max_health
        || actor.equipment.len() > 11
        || actor
            .equipment
            .iter()
            .any(|item| item.slot == 0 || item.slot > 12)
    {
        return Err(invalid("invalid local VISION actor"));
    }
    let heading = actor.heading.round();
    if !heading.is_finite()
        || !(-180. ..=180.).contains(&heading)
        || actor
            .position
            .iter()
            .any(|value| !value.is_finite() || value.abs() >= 100000.)
    {
        return Err(invalid("invalid local VISION spawn"));
    }
    let hero_id =
        i32::try_from(actor.hero_id).map_err(|_| invalid("hero id exceeds VISION wire range"))?;
    let equipped = i32::try_from(actor.weapon_id)
        .map_err(|_| invalid("weapon id exceeds VISION wire range"))?;
    let offhand = i32::try_from(actor.offhand_id)
        .map_err(|_| invalid("offhand id exceeds VISION wire range"))?;
    let mut slots = actor
        .equipment
        .iter()
        .map(|item| item.slot)
        .collect::<BTreeSet<_>>();
    if slots.len() != actor.equipment.len() || !slots.insert(13) {
        return Err(invalid("duplicate local VISION equipment slot"));
    }
    let mut body = 1_u16.to_be_bytes().to_vec();
    body.extend_from_slice(&1_i32.to_be_bytes());
    body.extend_from_slice(&1_i32.to_be_bytes());
    body.extend_from_slice(&user_id.to_be_bytes());
    body.extend_from_slice(&u64::from(actor.client_rid).to_be_bytes());
    body.extend_from_slice(&actor.client_rid.to_be_bytes());
    move_data(
        &mut body,
        1,
        actor.position,
        heading as i16,
        i32::from(actor.client_rid),
        -10000,
    );
    body.extend_from_slice(&i32::from(actor.camp).to_be_bytes());
    body.extend_from_slice(&[1, 0]);
    body.extend_from_slice(&level.to_be_bytes());
    body.extend_from_slice(&[0; 8]);
    tdr_string(&mut body, &[]);
    body.extend_from_slice(&hero_id.to_be_bytes());
    body.push(0);
    for _ in 0..9 {
        body.extend_from_slice(&0_i32.to_be_bytes());
    }
    body.extend_from_slice(&((actor.equipment.len() + 1) as i16).to_be_bytes());
    for (slot, weapon_id) in actor
        .equipment
        .iter()
        .map(|item| (item.slot, item.weapon_id))
        .chain([(13, 100000)])
    {
        let weapon_id = i32::try_from(weapon_id)
            .map_err(|_| invalid("equipment id exceeds VISION wire range"))?;
        body.extend_from_slice(&weapon_id.to_be_bytes());
        body.extend_from_slice(&[
            slot,
            0,
            u8::from(weapon_id == equipped || weapon_id == offhand),
        ]);
        body.extend_from_slice(&[0; 22]);
    }
    body.extend_from_slice(&[0; 20]);
    for value in [health as i32, actor.max_health as i32, 100] {
        body.extend_from_slice(&value.to_be_bytes());
    }
    body.extend_from_slice(&[0; 14]);
    body.extend_from_slice(&[0; 12]);
    body.extend_from_slice(&[0; 10]);
    tdr_string(&mut body, name);
    body.extend_from_slice(&[0; 28]);
    move_data(&mut body, 0, [0.; 3], 0, 0, 0);
    body.extend_from_slice(&[0; 9]);
    body.extend_from_slice(&[0; 28]);
    body.extend_from_slice(&[0; 4]);
    body.extend_from_slice(&14_i32.to_be_bytes());
    body.extend_from_slice(&0_u64.to_be_bytes());
    Ok(ClientBattleMessage {
        command: 0x0e,
        body,
    })
}

pub(super) fn objects(
    request: &[u8],
    roster: &SceneRoster,
    identity: &Identity,
) -> io::Result<Vec<ClientBattleMessage>> {
    requested_actors(request, roster)?
        .into_iter()
        .map(|id| actor(roster, identity, id, None))
        .collect()
}

pub(super) fn requested_actors(request: &[u8], roster: &SceneRoster) -> io::Result<Vec<u32>> {
    if request.len() < 7 || request[..2] != [0, 7] {
        return Err(invalid("invalid VISION objects request"));
    }
    let count = i32::from_be_bytes(request[2..6].try_into().expect("checked header"));
    if !(0..=t7r_battle_core::MAX_BATTLE_ACTORS as i32).contains(&count)
        || request.len() != 7 + usize::try_from(count).expect("nonnegative count") * 8
        || !matches!(request[request.len() - 1], 0 | 1)
    {
        return Err(invalid("invalid VISION object references"));
    }
    let mut actors = Vec::with_capacity(count as usize);
    for id in request[6..request.len() - 1].chunks_exact(8) {
        let client_mid = u64::from_be_bytes(id.try_into().expect("checked reference"));
        if client_mid == u64::from(roster.local.client_rid) {
            actors.push(roster.local.actor_id);
        } else {
            let index = roster
                .bots
                .iter()
                .position(|bot| u64::from(bot.client_rid) == client_mid)
                .ok_or_else(|| invalid("VISION requested an unbound actor"))?;
            actors.push(roster.bots[index].actor_id);
        }
    }
    Ok(actors)
}

pub(super) fn actor(
    roster: &SceneRoster,
    identity: &Identity,
    actor_id: u32,
    health: Option<u32>,
) -> io::Result<ClientBattleMessage> {
    if actor_id == roster.local.actor_id {
        return actor_view(
            &roster.local,
            10_000,
            &identity.name,
            identity.level,
            health,
        );
    }
    let index = roster
        .bots
        .iter()
        .position(|bot| bot.actor_id == actor_id)
        .ok_or_else(|| invalid("VISION actor is not bound"))?;
    let names = scene_actor_wire::bot_names(&roster.bot_names, &identity.name, &roster.bots)?;
    let user_id = 9_999_u32
        .checked_add(actor_id)
        .ok_or_else(|| invalid("bot user id overflow"))?;
    actor_view(&roster.bots[index], user_id, &names[index], 1, health)
}

pub(super) fn respawn_actor(
    actor_id: u32,
    position: [f64; 3],
    heading: f64,
    roster: &SceneRoster,
    identity: &Identity,
) -> io::Result<ClientBattleMessage> {
    let mut updated = roster.clone();
    let actor = if updated.local.actor_id == actor_id {
        &mut updated.local
    } else {
        updated
            .bots
            .iter_mut()
            .find(|actor| actor.actor_id == actor_id)
            .ok_or_else(|| invalid("respawn actor is absent from scene roster"))?
    };
    actor.position = position;
    actor.heading = heading;
    let mut request = 7_u16.to_be_bytes().to_vec();
    request.extend_from_slice(&1_i32.to_be_bytes());
    request.extend_from_slice(&u64::from(actor.client_rid).to_be_bytes());
    request.push(0);
    objects(&request, &updated, identity)?
        .pop()
        .ok_or_else(|| invalid("respawn VISION actor is missing"))
}

pub(super) fn move_data(
    out: &mut Vec<u8>,
    active: u8,
    position: [f64; 3],
    heading: i16,
    group: i32,
    gravity: i16,
) {
    out.extend_from_slice(&[0, active, 0]);
    out.extend_from_slice(&[0; 18]);
    out.extend_from_slice(&heading.to_be_bytes());
    for value in position {
        out.extend_from_slice(&(value as f32).to_be_bytes());
    }
    out.extend_from_slice(&[0; 16]);
    out.extend_from_slice(&group.to_be_bytes());
    out.extend_from_slice(&gravity.to_be_bytes());
    out.extend_from_slice(&[0; 5]);
}

fn tdr_string(out: &mut Vec<u8>, value: &[u8]) {
    out.extend_from_slice(&((value.len() + 1) as u32).to_be_bytes());
    out.extend_from_slice(value);
    out.push(0);
}

fn invalid(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

#[cfg(test)]
mod tests {
    use super::*;
    use t7r_battle_core::action::EquipmentItem;

    #[test]
    fn vision_object_request_projects_bound_player_and_bot() {
        let identity = Identity {
            name: b"Player".to_vec(),
            portrait: 7,
            level: 100,
        };
        let local = LocalSceneActor {
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
        let roster = SceneRoster {
            local,
            bots: vec![bot],
            bot_names: vec!["Fixture0".into()],
            kill_target: 10,
            guard_max: 5.,
            local_shields: vec![],
        };
        let rows = include_str!(
            "../../../../../tests/fixtures/protocol/instance-vision-objects-wire-v1.txt"
        )
        .lines()
        .map(|line| line.split_whitespace().collect::<Vec<_>>())
        .collect::<Vec<_>>();
        let request = rows[0][2]
            .as_bytes()
            .chunks_exact(2)
            .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
            .collect::<Vec<_>>();
        let actual = objects(&request, &roster, &identity).unwrap();
        assert_eq!(actual.len(), 2);
        let local_row = include_str!(
            "../../../../../tests/fixtures/protocol/instance-actor-import-wire-v1.txt"
        )
        .lines()
        .find(|line| line.starts_with("actor-vision "))
        .unwrap()
        .split_whitespace()
        .collect::<Vec<_>>();
        for (message, expected) in actual.iter().zip([local_row[2], rows[1][2]]) {
            assert_eq!(message.command, 14);
            assert_eq!(
                message
                    .body
                    .iter()
                    .map(|byte| format!("{byte:02x}"))
                    .collect::<String>(),
                expected
            );
        }
        assert!(objects(&request[..request.len() - 1], &roster, &identity).is_err());
        let mut unknown = request.clone();
        unknown[14..22].copy_from_slice(&999_u64.to_be_bytes());
        assert!(objects(&unknown, &roster, &identity).is_err());
        let mut duplicate = request;
        duplicate[14..22].copy_from_slice(&101_u64.to_be_bytes());
        assert_eq!(objects(&duplicate, &roster, &identity).unwrap().len(), 2);

        let mut varied = roster.clone();
        varied.local.max_health = 150;
        varied.bots[0].max_health = 240;
        let mut varied_request = duplicate;
        varied_request[14..22].copy_from_slice(&202_u64.to_be_bytes());
        let messages = objects(&varied_request, &varied, &identity).unwrap();
        for (index, maximum) in [150_u32, 240].into_iter().enumerate() {
            let mut expected = actual[index].clone();
            // Fixed actor header plus two 29-byte weapon entries precede HP/max HP.
            expected.body[230..234].copy_from_slice(&maximum.to_be_bytes());
            expected.body[234..238].copy_from_slice(&maximum.to_be_bytes());
            assert_eq!(messages[index], expected);
        }
        assert_eq!(local_actor(&varied.local, &identity).unwrap(), messages[0]);
        assert_eq!(
            respawn_actor(
                2,
                varied.bots[0].position,
                varied.bots[0].heading,
                &varied,
                &identity
            )
            .unwrap(),
            messages[1]
        );
        let wounded = actor(&varied, &identity, 1, Some(111)).unwrap();
        assert_eq!(&wounded.body[230..234], &111_u32.to_be_bytes());
        assert_eq!(&wounded.body[234..238], &150_u32.to_be_bytes());
        assert!(actor(&varied, &identity, 1, Some(151)).is_err());
        for invalid_max in [0, i32::MAX as u32 + 1] {
            varied.local.max_health = invalid_max;
            assert!(local_actor(&varied.local, &identity).is_err());
        }
    }
}
