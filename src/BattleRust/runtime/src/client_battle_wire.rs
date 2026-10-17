//! Original-client battle compatibility at the Windows gateway boundary.
use crate::battle_session::{EndReason, Output, Score, Settlement};
use std::{collections::BTreeMap, io};
use t7r_battle_core::action::{
    Animation, AnimationEvent, Command, Direction, EquipmentItem, HurtKind, Intent, Phase,
};
use t7r_battle_core::damage::Region;

pub const BATTLE_COMMAND: u16 = 4;
pub(crate) mod parry;
pub(crate) mod surface;
pub(crate) mod weapon;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ClientBattleMessage {
    pub command: u16,
    pub body: Vec<u8>,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct BotMoveWire {
    pub tick_ms: u32,
    pub client_rid: u16,
    pub state: u8,
    pub left_right: i16,
    pub forward_back: i16,
    pub current_velocity: i32,
    pub max_velocity: i16,
    pub position: [f32; 3],
    pub direction_yaw: i16,
}

pub fn encode_bot_move_for_client(value: BotMoveWire) -> io::Result<ClientBattleMessage> {
    let moving = value.state != 1;
    if value.tick_ms > i32::MAX as u32
        || value.client_rid == 0
        || !(1..=12).contains(&value.state)
        || !(-1000..=1000).contains(&value.left_right)
        || !(-1000..=1000).contains(&value.forward_back)
        || !(0..=25000).contains(&value.current_velocity)
        || !matches!(value.max_velocity, 0 | 25000)
        || !(-180..=180).contains(&value.direction_yaw)
        || value
            .position
            .iter()
            .any(|coordinate| !coordinate.is_finite() || coordinate.abs() >= 100000.)
        || moving
            && (value.max_velocity == 0
                || value.current_velocity == 0
                || value.left_right == 0 && value.forward_back == 0)
        || !moving
            && (value.max_velocity != 0
                || value.current_velocity != 0
                || value.left_right != 0
                || value.forward_back != 0)
    {
        return Err(invalid("invalid original-client bot movement"));
    }
    let mut body = Vec::with_capacity(40);
    body.extend_from_slice(&0x26_u16.to_be_bytes());
    body.extend_from_slice(&value.tick_ms.to_be_bytes());
    body.extend_from_slice(&value.client_rid.to_be_bytes());
    body.push(value.state);
    body.extend_from_slice(&value.left_right.to_be_bytes());
    body.extend_from_slice(&value.forward_back.to_be_bytes());
    body.extend_from_slice(&value.current_velocity.to_be_bytes());
    body.extend_from_slice(&value.max_velocity.to_be_bytes());
    for coordinate in value.position {
        body.extend_from_slice(&coordinate.to_be_bytes());
    }
    body.extend_from_slice(&value.direction_yaw.to_be_bytes());
    body.extend_from_slice(&u32::from(value.client_rid).to_be_bytes());
    body.push(1);
    body.extend_from_slice(&0_i16.to_be_bytes());
    debug_assert_eq!(body.len(), 40);
    Ok(ClientBattleMessage { command: 2, body })
}

pub fn encode_bot_turn_for_client(
    tick_ms: u32,
    client_rid: u16,
    direction_yaw: i16,
) -> io::Result<ClientBattleMessage> {
    if tick_ms > i32::MAX as u32 || client_rid == 0 || !(-180..=180).contains(&direction_yaw) {
        return Err(invalid("invalid original-client bot turn"));
    }
    let mut body = 0x38_u16.to_be_bytes().to_vec();
    body.extend_from_slice(&tick_ms.to_be_bytes());
    body.push(1);
    body.extend_from_slice(&client_rid.to_be_bytes());
    body.extend_from_slice(&direction_yaw.to_be_bytes());
    body.extend_from_slice(&0_i16.to_be_bytes());
    body.extend_from_slice(&0_i16.to_be_bytes());
    Ok(ClientBattleMessage { command: 2, body })
}

pub fn decode_action(command: u16, body: &[u8]) -> io::Result<Option<Command>> {
    if command != BATTLE_COMMAND {
        return Ok(None);
    }
    if body.len() < 2 {
        return Err(invalid("battle action selector is truncated"));
    }
    if u16::from_be_bytes([body[0], body[1]]) != 1 {
        return Ok(None);
    }
    if body.len() != 11 || !matches!(body[10], 0 | 1) {
        return Err(invalid("invalid original-client battle action"));
    }
    let keys = u32::from_be_bytes(body[2..6].try_into().expect("checked body length"));
    let intent = i32::from_be_bytes(body[6..10].try_into().expect("checked body length"));
    let press = |intent| {
        Some(Command::Press {
            intent,
            defence_down: keys & (1 << 8) != 0,
            dash_run: keys & (1 << 26) != 0,
            dash_forward: keys & ((1 << 21) | (1 << 7)) != 0,
        })
    };
    Ok(match intent {
        300560 => press(Intent::Attack(Direction::Up)),
        300550 => press(Intent::Attack(Direction::Right)),
        300570 => press(Intent::Attack(Direction::Down)),
        300540 => press(Intent::Attack(Direction::Left)),
        300580 => press(Intent::Guard(Direction::Up)),
        300590 => press(Intent::Guard(Direction::Right)),
        300600 => press(Intent::Guard(Direction::Down)),
        300610 => press(Intent::Guard(Direction::Left)),
        300130 => press(Intent::Shield),
        700250 => press(Intent::Dash),
        300020 => Some(Command::ReleaseAttack),
        300620 | 300140 => Some(Command::ReleaseGuard),
        _ => None,
    })
}

pub fn encode_animation(
    event: &AnimationEvent,
    instance_started_at_ms: u64,
) -> io::Result<ClientBattleMessage> {
    let actor =
        u16::try_from(event.actor_id).map_err(|_| invalid("actor RID exceeds wire range"))?;
    if actor == 0 || event.sequence == 0 {
        return Err(invalid("invalid animation actor or sequence"));
    }
    let at = event
        .at_ms
        .checked_sub(instance_started_at_ms)
        .and_then(|value| u32::try_from(value).ok())
        .ok_or_else(|| invalid("animation time exceeds instance wire range"))?;
    let state = animation_state(event.animation)?;
    let mut body = Vec::with_capacity(if event.target_id.is_some() { 24 } else { 14 });
    body.extend_from_slice(
        &if event.target_id.is_some() {
            4_u16
        } else {
            3_u16
        }
        .to_be_bytes(),
    );
    body.extend_from_slice(&actor.to_be_bytes());
    body.extend_from_slice(&event.sequence.to_be_bytes());
    body.extend_from_slice(&state.to_be_bytes());
    body.extend_from_slice(&at.to_be_bytes());
    body.extend_from_slice(&event.duration_ms.to_be_bytes());
    if let Some(target) = event.target_id {
        body.push(0);
        body.extend_from_slice(&u64::from(target).to_be_bytes());
        body.push(0);
    }
    Ok(ClientBattleMessage {
        command: BATTLE_COMMAND,
        body,
    })
}

pub fn encode_animation_for_client(
    event: &AnimationEvent,
    instance_started_at_ms: u64,
    actor_rids: &BTreeMap<u32, u16>,
) -> io::Result<ClientBattleMessage> {
    let mut projected = *event;
    projected.actor_id = u32::from(client_rid(actor_rids, event.actor_id)?);
    projected.target_id = event
        .target_id
        .map(|actor_id| {
            if actor_id == 0 {
                Ok(0)
            } else {
                client_rid(actor_rids, actor_id).map(u32::from)
            }
        })
        .transpose()?;
    encode_animation(&projected, instance_started_at_ms)
}

pub fn encode_animation_for_client_clock(
    event: &AnimationEvent,
    client_time_offset_ms: i64,
    actor_rids: &BTreeMap<u32, u16>,
) -> io::Result<ClientBattleMessage> {
    let elapsed = wire_time(event.at_ms, client_time_offset_ms)?;
    if elapsed > u64::from(u32::MAX) {
        return Err(invalid("animation time exceeds instance wire range"));
    }
    let mut projected = *event;
    projected.at_ms = elapsed;
    encode_animation_for_client(&projected, 0, actor_rids)
}

pub(crate) fn wire_time(match_at_ms: u64, offset_ms: i64) -> io::Result<u64> {
    u64::try_from(i128::from(match_at_ms) + i128::from(offset_ms))
        .map_err(|_| invalid("battle time exceeds wire range"))
}

/// Preserve the original health → contact → local-attacker result order.
pub fn encode_damage(
    output: &Output,
    local_actor_id: u32,
    now_ms: u64,
) -> io::Result<Vec<ClientBattleMessage>> {
    let Output::Damage {
        attacker,
        target,
        sequence,
        health,
        previous_health,
        weapon_id,
        evidence,
        guard_break,
        result,
    } = output
    else {
        return Err(invalid("expected authoritative damage output"));
    };
    if *attacker == 0
        || *target == 0
        || *sequence == 0
        || local_actor_id == 0
        || evidence.actor_id != *attacker
        || evidence.target_id != *target
        || evidence.sequence != *sequence
        || *previous_health > i32::MAX as u32
        || *health > *previous_health
        || result.hp_damage == 0
        || result.hp_damage < *previous_health - *health
        || evidence.body_index < -1
        || !(-1..=7).contains(&evidence.material)
        || [evidence.position, evidence.direction, evidence.normal]
            .into_iter()
            .flatten()
            .any(|value| !value.is_finite() || value.abs() >= 100000.)
    {
        return Err(invalid("inconsistent damage output"));
    }
    let health_i32 = i32::try_from(*health).map_err(|_| invalid("health exceeds wire range"))?;
    let weapon = i32::try_from(*weapon_id).map_err(|_| invalid("weapon exceeds wire range"))?;
    let contact_at = evidence.occurred_at_ms;
    if !contact_at.is_finite() || contact_at < 0. || contact_at >= u64::MAX as f64 {
        return Err(invalid("contact time exceeds wire range"));
    }
    let contact_at = contact_at.trunc() as u64;
    let mut messages = Vec::with_capacity(3);
    if *target == local_actor_id {
        let mut body = 11_u16.to_be_bytes().to_vec();
        body.extend_from_slice(&1_i16.to_be_bytes());
        body.extend_from_slice(&health_i32.to_be_bytes());
        messages.push(ClientBattleMessage {
            command: BATTLE_COMMAND,
            body,
        });
    } else {
        let mut body = 1_u16.to_be_bytes().to_vec();
        body.extend_from_slice(&u64::from(*target).to_be_bytes());
        body.extend_from_slice(&1_i32.to_be_bytes());
        body.extend_from_slice(&health_i32.to_be_bytes());
        body.resize(body.len() + 12, 0);
        body.extend_from_slice(&now_ms.to_be_bytes());
        messages.push(ClientBattleMessage { command: 15, body });
    }

    let mut body = Vec::with_capacity(93);
    body.extend_from_slice(&27_u16.to_be_bytes());
    for coordinate in evidence.position {
        body.extend_from_slice(&(coordinate as f32).to_be_bytes());
    }
    body.push(0);
    for coordinate in evidence.direction {
        body.extend_from_slice(&(coordinate as f32).to_be_bytes());
    }
    body.extend_from_slice(&0_f32.to_be_bytes());
    for coordinate in evidence.normal {
        body.extend_from_slice(&(coordinate as f32).to_be_bytes());
    }
    body.extend_from_slice(&0_f32.to_be_bytes());
    body.extend_from_slice(&i32::from(evidence.material).to_be_bytes());
    body.extend_from_slice(&u64::from(*attacker).to_be_bytes());
    body.extend_from_slice(&weapon.to_be_bytes());
    body.extend_from_slice(&u64::from(*target).to_be_bytes());
    body.extend_from_slice(&0_i32.to_be_bytes());
    body.extend_from_slice(&0_u32.to_be_bytes());
    body.extend_from_slice(&contact_at.to_be_bytes());
    body.push(u8::from(!result.interrupt && !guard_break));
    body.push(1);
    body.extend_from_slice(&0_i32.to_be_bytes());
    debug_assert_eq!(body.len(), 93);
    messages.push(ClientBattleMessage {
        command: BATTLE_COMMAND,
        body,
    });

    if *attacker == local_actor_id {
        let mut body = 9_u16.to_be_bytes().to_vec();
        body.extend_from_slice(&u64::from(*attacker).to_be_bytes());
        body.extend_from_slice(&u64::from(*target).to_be_bytes());
        body.extend_from_slice(&sequence.to_be_bytes());
        // Despite its target_rb_index wire name, the client consumes COLLISION_PART here.
        body.push(match evidence.region {
            Region::None => 0,
            Region::Head => 1,
            Region::Torso => 2,
            Region::Legs => 3,
        });
        body.push(u8::from(*health == 0));
        body.extend_from_slice(&0_i16.to_be_bytes());
        body.extend_from_slice(&(if result.head { 2_i16 } else { 1_i16 }).to_be_bytes());
        body.extend_from_slice(&(if result.decay { 6_i16 } else { 2_i16 }).to_be_bytes());
        let delta = i32::try_from(*health)
            .ok()
            .and_then(|value| {
                i32::try_from(*previous_health)
                    .ok()
                    .map(|before| value - before)
            })
            .ok_or_else(|| invalid("health delta exceeds wire range"))?;
        body.extend_from_slice(&delta.to_be_bytes());
        if result.head {
            body.extend_from_slice(&5_i16.to_be_bytes());
            body.extend_from_slice(&1_i32.to_be_bytes());
        }
        body.extend_from_slice(&weapon.to_be_bytes());
        body.extend_from_slice(&[0, 0]);
        messages.push(ClientBattleMessage {
            command: BATTLE_COMMAND,
            body,
        });
    }
    Ok(messages)
}

pub fn encode_damage_for_client(
    output: &Output,
    local_actor_id: u32,
    now_ms: u64,
    actor_rids: &BTreeMap<u32, u16>,
) -> io::Result<Vec<ClientBattleMessage>> {
    let Output::Damage {
        attacker,
        target,
        evidence,
        ..
    } = output
    else {
        return Err(invalid("expected authoritative damage output"));
    };
    if evidence.actor_id != *attacker || evidence.target_id != *target {
        return Err(invalid("inconsistent damage output"));
    }
    let attacker_rid = u32::from(client_rid(actor_rids, *attacker)?);
    let target_rid = u32::from(client_rid(actor_rids, *target)?);
    let local_rid = u32::from(client_rid(actor_rids, local_actor_id)?);
    let mut projected = output.clone();
    if let Output::Damage {
        attacker,
        target,
        evidence,
        ..
    } = &mut projected
    {
        *attacker = attacker_rid;
        *target = target_rid;
        evidence.actor_id = attacker_rid;
        evidence.target_id = target_rid;
    }
    encode_damage(&projected, local_rid, now_ms)
}

pub fn encode_damage_for_client_clock(
    output: &Output,
    local_actor_id: u32,
    match_now_ms: u64,
    server_time_offset_ms: i64,
    actor_rids: &BTreeMap<u32, u16>,
) -> io::Result<Vec<ClientBattleMessage>> {
    let Output::Damage { evidence, .. } = output else {
        return Err(invalid("expected authoritative damage output"));
    };
    if !evidence.occurred_at_ms.is_finite()
        || evidence.occurred_at_ms < 0.
        || evidence.occurred_at_ms >= u64::MAX as f64
    {
        return Err(invalid("contact time exceeds wire range"));
    }
    let contact_at = wire_time(
        evidence.occurred_at_ms.trunc() as u64,
        server_time_offset_ms,
    )?;
    // The damage encoder carries the contact timestamp as f64 before writing u64.
    if contact_at > (1_u64 << 53) {
        return Err(invalid("contact time loses wire precision"));
    }
    let mut projected = output.clone();
    if let Output::Damage { evidence, .. } = &mut projected {
        evidence.occurred_at_ms = contact_at as f64;
    }
    encode_damage_for_client(
        &projected,
        local_actor_id,
        wire_time(match_now_ms, server_time_offset_ms)?,
        actor_rids,
    )
}

pub(crate) fn client_rid(actor_rids: &BTreeMap<u32, u16>, actor_id: u32) -> io::Result<u16> {
    let rid = *actor_rids
        .get(&actor_id)
        .ok_or_else(|| invalid("unbound output actor"))?;
    if rid == 0 || actor_rids.values().filter(|&&value| value == rid).count() != 1 {
        return Err(invalid("ambiguous output RID"));
    }
    Ok(rid)
}

pub fn encode_dead_for_client(
    actor_id: u32,
    now_ms: u64,
    actor_rids: &BTreeMap<u32, u16>,
) -> io::Result<ClientBattleMessage> {
    let mut body = 10_u16.to_be_bytes().to_vec();
    body.extend_from_slice(&u64::from(client_rid(actor_rids, actor_id)?).to_be_bytes());
    body.extend_from_slice(&now_ms.to_be_bytes());
    Ok(ClientBattleMessage {
        command: BATTLE_COMMAND,
        body,
    })
}

pub fn encode_guard_value_for_client(value: f64) -> io::Result<ClientBattleMessage> {
    if !value.is_finite() || value < 0. || value > f32::MAX as f64 {
        return Err(invalid("invalid original-client guard value"));
    }
    let mut body = 0x30_u16.to_be_bytes().to_vec();
    body.extend_from_slice(&(value as f32).to_be_bytes());
    Ok(ClientBattleMessage {
        command: BATTLE_COMMAND,
        body,
    })
}

pub fn encode_health_value_for_client(health: u32) -> io::Result<ClientBattleMessage> {
    if health > i32::MAX as u32 {
        return Err(invalid("invalid original-client health value"));
    }
    let mut body = 0x0b_u16.to_be_bytes().to_vec();
    body.extend_from_slice(&1_i16.to_be_bytes());
    body.extend_from_slice(&(health as i32).to_be_bytes());
    Ok(ClientBattleMessage {
        command: BATTLE_COMMAND,
        body,
    })
}

pub fn encode_shield_health_for_client(
    weapon_id: u32,
    slot: u8,
    health: u32,
    maximum: u32,
) -> io::Result<ClientBattleMessage> {
    if weapon_id == 0 || slot == 0 || maximum == 0 || maximum > i32::MAX as u32 || health > maximum
    {
        return Err(invalid("invalid original-client shield value"));
    }
    let weapon_id =
        i32::try_from(weapon_id).map_err(|_| invalid("shield id exceeds wire range"))?;
    let mut body = 6_u16.to_be_bytes().to_vec();
    body.extend_from_slice(&weapon_id.to_be_bytes());
    body.extend_from_slice(&i16::from(slot).to_be_bytes());
    body.extend_from_slice(&(health as i32).to_be_bytes());
    body.extend_from_slice(&(maximum as i32).to_be_bytes());
    Ok(ClientBattleMessage { command: 5, body })
}

pub fn encode_shield_break_for_client(
    actor_id: u32,
    weapon_id: u32,
    actor_rids: &BTreeMap<u32, u16>,
) -> io::Result<ClientBattleMessage> {
    let weapon = i32::try_from(weapon_id).map_err(|_| invalid("shield id exceeds wire range"))?;
    if weapon == 0 {
        return Err(invalid("invalid broken shield id"));
    }
    let mut body = 5_u16.to_be_bytes().to_vec();
    body.extend_from_slice(&u64::from(client_rid(actor_rids, actor_id)?).to_be_bytes());
    body.extend_from_slice(&weapon.to_be_bytes());
    Ok(ClientBattleMessage { command: 5, body })
}

pub fn encode_equipment_update_for_client(
    actor_id: u32,
    items: &[EquipmentItem],
    weapon_id: u32,
    offhand_id: u32,
    now_ms: u64,
    actor_rids: &BTreeMap<u32, u16>,
) -> io::Result<ClientBattleMessage> {
    const FIST_ID: u32 = 100000;
    if items.len() > 4
        || weapon_id == 0
        || (weapon_id != FIST_ID && !items.iter().any(|item| item.weapon_id == weapon_id))
        || (offhand_id != 0 && !items.iter().any(|item| item.weapon_id == offhand_id))
        || items
            .iter()
            .any(|item| !(1..=4).contains(&item.slot) || item.weapon_id == FIST_ID)
    {
        return Err(invalid("invalid client equipment update"));
    }
    let mut slots = std::collections::BTreeSet::new();
    let mut body = 3_u16.to_be_bytes().to_vec();
    body.extend_from_slice(&u64::from(client_rid(actor_rids, actor_id)?).to_be_bytes());
    body.extend_from_slice(&((items.len() + 1) as i16).to_be_bytes());
    for item in items.iter().copied().chain([EquipmentItem {
        slot: 13,
        weapon_id: FIST_ID,
    }]) {
        let id = i32::try_from(item.weapon_id)
            .map_err(|_| invalid("equipment id exceeds wire range"))?;
        if id == 0 || item.slot == 0 || !slots.insert(item.slot) {
            return Err(invalid("invalid client equipment slot"));
        }
        body.extend_from_slice(&id.to_be_bytes());
        body.extend_from_slice(&[
            item.slot,
            0,
            u8::from(item.weapon_id == weapon_id || item.weapon_id == offhand_id),
        ]);
        body.extend_from_slice(&[0; 22]);
    }
    body.extend_from_slice(&1_i16.to_be_bytes());
    body.extend_from_slice(&now_ms.to_be_bytes());
    Ok(ClientBattleMessage { command: 5, body })
}

pub fn encode_death_state_for_client(
    actor_id: u32,
    attacker_id: u32,
    sequence: u16,
    instance_elapsed_ms: u32,
    actor_rids: &BTreeMap<u32, u16>,
) -> io::Result<ClientBattleMessage> {
    if sequence == 0 || actor_id == attacker_id {
        return Err(invalid("invalid death state sequence or target"));
    }
    let actor_rid = client_rid(actor_rids, actor_id)?;
    let attacker_rid = client_rid(actor_rids, attacker_id)?;
    let mut body = 4_u16.to_be_bytes().to_vec();
    body.extend_from_slice(&actor_rid.to_be_bytes());
    body.extend_from_slice(&sequence.to_be_bytes());
    body.extend_from_slice(&42_u16.to_be_bytes());
    body.extend_from_slice(&instance_elapsed_ms.to_be_bytes());
    body.extend_from_slice(&0_u16.to_be_bytes());
    body.push(0);
    body.extend_from_slice(&u64::from(attacker_rid).to_be_bytes());
    body.push(0);
    Ok(ClientBattleMessage {
        command: BATTLE_COMMAND,
        body,
    })
}

pub fn encode_kill_for_client(
    attacker: u32,
    target: u32,
    weapon_id: u32,
    now_ms: u64,
    actor_rids: &BTreeMap<u32, u16>,
) -> io::Result<ClientBattleMessage> {
    let weapon = i32::try_from(weapon_id).map_err(|_| invalid("weapon exceeds wire range"))?;
    let mut body = 6_u16.to_be_bytes().to_vec();
    body.extend_from_slice(&now_ms.to_be_bytes());
    body.extend_from_slice(&u64::from(client_rid(actor_rids, attacker)?).to_be_bytes());
    body.extend_from_slice(&u64::from(client_rid(actor_rids, target)?).to_be_bytes());
    body.extend_from_slice(&weapon.to_be_bytes());
    body.extend_from_slice(&0_i32.to_be_bytes());
    Ok(ClientBattleMessage { command: 10, body })
}

pub fn encode_streak_for_client(
    actor_id: u32,
    streak: u32,
    now_ms: u64,
    actor_rids: &BTreeMap<u32, u16>,
) -> io::Result<ClientBattleMessage> {
    if !(2..=i32::MAX as u32).contains(&streak) {
        return Err(invalid("invalid kill streak"));
    }
    let message_id = if streak >= 10 {
        710110
    } else {
        710000 + streak * 10
    };
    encode_stat_message(actor_id, message_id, streak, now_ms, actor_rids)
}

pub fn encode_butcher_end_for_client(
    actor_id: u32,
    now_ms: u64,
    actor_rids: &BTreeMap<u32, u16>,
) -> io::Result<ClientBattleMessage> {
    encode_stat_message(actor_id, 730140, 1, now_ms, actor_rids)
}

fn encode_stat_message(
    actor_id: u32,
    message_id: u32,
    count: u32,
    now_ms: u64,
    actor_rids: &BTreeMap<u32, u16>,
) -> io::Result<ClientBattleMessage> {
    let mut body = 2_u16.to_be_bytes().to_vec();
    body.extend_from_slice(&now_ms.to_be_bytes());
    body.extend_from_slice(&u64::from(client_rid(actor_rids, actor_id)?).to_be_bytes());
    body.extend_from_slice(
        &i32::try_from(message_id)
            .map_err(|_| invalid("stat message exceeds wire range"))?
            .to_be_bytes(),
    );
    body.extend_from_slice(
        &i32::try_from(count)
            .map_err(|_| invalid("stat count exceeds wire range"))?
            .to_be_bytes(),
    );
    Ok(ClientBattleMessage { command: 12, body })
}

pub fn encode_score_update_for_client(
    actor_id: u32,
    previous: Score,
    current: Score,
    stat_message_id: u32,
    now_ms: u64,
    actor_rids: &BTreeMap<u32, u16>,
) -> io::Result<ClientBattleMessage> {
    if current.kills < previous.kills
        || current.deaths < previous.deaths
        || current.points < previous.points
    {
        return Err(invalid("live score moved backwards"));
    }
    let stat_message = i32::try_from(stat_message_id)
        .map_err(|_| invalid("score announcement exceeds wire range"))?;
    let mut body = 1_u16.to_be_bytes().to_vec();
    body.extend_from_slice(&now_ms.to_be_bytes());
    body.extend_from_slice(&u64::from(client_rid(actor_rids, actor_id)?).to_be_bytes());
    body.extend_from_slice(&stat_message.to_be_bytes());
    body.extend_from_slice(&4_i32.to_be_bytes());
    for (key, before, after) in [
        (2_i32, previous.kills, current.kills),
        (44, previous.kills, current.kills),
        (3, previous.deaths, current.deaths),
        (1, previous.points, current.points),
    ] {
        let before = i32::try_from(before).map_err(|_| invalid("score exceeds wire range"))?;
        let after = i32::try_from(after).map_err(|_| invalid("score exceeds wire range"))?;
        for value in [key, before, after] {
            body.extend_from_slice(&value.to_be_bytes());
        }
    }
    Ok(ClientBattleMessage { command: 12, body })
}

pub fn encode_camp_kill_update(
    now_ms: u64,
    instance_id: u64,
    camp_kills: [u32; 2],
    kill_target: Option<u32>,
) -> io::Result<ClientBattleMessage> {
    if instance_id == 0
        || camp_kills.into_iter().any(|kills| kills > i32::MAX as u32)
        || kill_target.is_some_and(|target| target == 0 || target > i32::MAX as u32)
    {
        return Err(invalid("invalid camp kill update"));
    }
    let mut body = 1_u16.to_be_bytes().to_vec();
    body.extend_from_slice(&now_ms.to_be_bytes());
    body.extend_from_slice(&instance_id.to_be_bytes());
    body.extend_from_slice(&(if kill_target.is_some() { 3_i32 } else { 2_i32 }).to_be_bytes());
    for (key, value) in [(7_346_418_i32, camp_kills[0]), (206_159, camp_kills[1])] {
        body.extend_from_slice(&key.to_be_bytes());
        body.extend_from_slice(&u64::from(value).to_be_bytes());
    }
    if let Some(target) = kill_target {
        body.extend_from_slice(&(-1_610_395_699_i32).to_be_bytes());
        body.extend_from_slice(&u64::from(target).to_be_bytes());
    }
    Ok(ClientBattleMessage { command: 23, body })
}

pub fn encode_settlement_start_for_client(
    settlement: &Settlement,
    actor_rids: &BTreeMap<u32, u16>,
) -> io::Result<Vec<ClientBattleMessage>> {
    if settlement.members.is_empty()
        || settlement.members.len() > t7r_battle_core::MAX_BATTLE_ACTORS
        || settlement.result > 3
        || settlement.ended_at_ms < settlement.started_at_ms
    {
        return Err(invalid("invalid settlement output"));
    }
    let now_ms = settlement.ended_at_ms;
    let mut messages = Vec::with_capacity(settlement.members.len() + 2);
    for member in &settlement.members {
        let score = member.score;
        if member.rank == 0
            || member.rank > t7r_battle_core::MAX_BATTLE_ACTORS as u32
            || member.result > 3
            || [score.kills, score.deaths, score.points]
                .into_iter()
                .any(|value| value > i32::MAX as u32)
        {
            return Err(invalid("settlement score exceeds wire range"));
        }
        let mut body = 1_u16.to_be_bytes().to_vec();
        body.extend_from_slice(&now_ms.to_be_bytes());
        body.extend_from_slice(&u64::from(client_rid(actor_rids, member.actor_id)?).to_be_bytes());
        body.extend_from_slice(&0_i32.to_be_bytes());
        body.extend_from_slice(&6_i32.to_be_bytes());
        for (key, value) in [
            (2_i32, score.kills),
            (44, score.kills),
            (3, score.deaths),
            (1, score.points),
            (9, u32::from(member.result)),
            (51, member.rank),
        ] {
            body.extend_from_slice(&key.to_be_bytes());
            body.extend_from_slice(&(value as i32).to_be_bytes());
            body.extend_from_slice(&(value as i32).to_be_bytes());
        }
        messages.push(ClientBattleMessage { command: 12, body });
    }
    let hold_ms = if settlement.reason == EndReason::Quit {
        0_u64
    } else {
        5000
    };
    if hold_ms != 0 {
        let mut body = 0x68_u16.to_be_bytes().to_vec();
        body.extend_from_slice(&now_ms.to_be_bytes());
        body.extend_from_slice(&1_u64.to_be_bytes());
        body.extend_from_slice(&i32::from(settlement.result).to_be_bytes());
        body.extend_from_slice(&1_i32.to_be_bytes());
        messages.push(ClientBattleMessage { command: 10, body });
    }
    let mut body = 0x67_u16.to_be_bytes().to_vec();
    body.extend_from_slice(&now_ms.to_be_bytes());
    body.extend_from_slice(&1_u64.to_be_bytes());
    body.extend_from_slice(&5_i32.to_be_bytes());
    body.extend_from_slice(&now_ms.to_be_bytes());
    body.extend_from_slice(&hold_ms.to_be_bytes());
    body.extend_from_slice(&0_i32.to_be_bytes());
    messages.push(ClientBattleMessage { command: 10, body });
    Ok(messages)
}

pub fn encode_settlement_finish_for_client(
    settlement: &Settlement,
    local_actor_id: u32,
    level: u32,
    local_user_id: u32,
    now_ms: u64,
    actor_rids: &BTreeMap<u32, u16>,
) -> io::Result<[ClientBattleMessage; 2]> {
    let finish_at = if settlement.reason == EndReason::Quit {
        settlement.ended_at_ms
    } else {
        settlement
            .ended_at_ms
            .checked_add(5000)
            .ok_or_else(|| invalid("settlement finish time overflow"))?
    };
    if now_ms < finish_at {
        return Err(invalid("settlement finish is not ready"));
    }
    encode_settlement_finish_messages(
        settlement,
        local_actor_id,
        level,
        local_user_id,
        now_ms,
        actor_rids,
    )
}

// Live connections own the hold timer and may finish it early on an explicit exit.
pub(crate) fn encode_settlement_finish_messages(
    settlement: &Settlement,
    local_actor_id: u32,
    level: u32,
    local_user_id: u32,
    now_ms: u64,
    actor_rids: &BTreeMap<u32, u16>,
) -> io::Result<[ClientBattleMessage; 2]> {
    if level == 0
        || level > i32::MAX as u32
        || settlement.result > 3
        || !settlement
            .members
            .iter()
            .any(|member| member.actor_id == local_actor_id)
        || now_ms < settlement.ended_at_ms
    {
        return Err(invalid("settlement finish is not ready"));
    }
    client_rid(actor_rids, local_actor_id)?;
    let local_result = settlement
        .members
        .iter()
        .find(|member| member.actor_id == local_actor_id)
        .expect("checked local member")
        .result;
    if local_result > 3 {
        return Err(invalid("invalid local settlement result"));
    }
    let mut settle = 1_u16.to_be_bytes().to_vec();
    for value in [level as i32, 0, 2] {
        settle.extend_from_slice(&value.to_be_bytes());
    }
    settle.push(0);
    for value in [0_i32, 0, 0, i32::from(local_result)] {
        settle.extend_from_slice(&value.to_be_bytes());
    }
    let mut finish = 0x69_u16.to_be_bytes().to_vec();
    finish.extend_from_slice(&now_ms.to_be_bytes());
    finish.extend_from_slice(&1_u64.to_be_bytes());
    finish.extend_from_slice(&i32::from(settlement.result).to_be_bytes());
    finish.extend_from_slice(&1_i32.to_be_bytes());
    finish.extend_from_slice(&local_user_id.to_be_bytes());
    finish.resize(118, 0);
    Ok([
        ClientBattleMessage {
            command: 52,
            body: settle,
        },
        ClientBattleMessage {
            command: 10,
            body: finish,
        },
    ])
}

fn animation_state(animation: Animation) -> io::Result<u16> {
    let state = match animation {
        Animation::Idle => 2,
        Animation::WeaponChange => 1,
        Animation::Hurt(HurtKind::Hit) => 35,
        Animation::Hurt(HurtKind::Knockdown) => 40,
        Animation::Hurt(HurtKind::GuardBreak) => 360,
        Animation::Hurt(HurtKind::GuardBreakKnockdown) => 362,
        Animation::Hurt(HurtKind::Exhaustion) => 598,
        Animation::Hurt(HurtKind::ShieldBreak) => 41,
        Animation::Action {
            intent: Intent::Attack(direction),
            phase,
        } => {
            let chain = match direction {
                Direction::Up => [235, 237, 238, 239, 240, 466],
                Direction::Right => [241, 243, 244, 245, 246, 468],
                Direction::Down => [247, 249, 250, 251, 252, 467],
                Direction::Left => [253, 255, 256, 257, 258, 469],
            };
            match phase {
                Phase::Prepare => chain[0],
                Phase::Hold => chain[1],
                Phase::Strike => chain[2],
                Phase::Recovery => chain[3],
                Phase::Parried => chain[4],
                Phase::Terrain => chain[5],
                _ => return Err(invalid("unsupported original-client attack phase")),
            }
        }
        Animation::Action {
            intent: Intent::Guard(direction),
            phase,
        } => {
            let chain = match direction {
                Direction::Up => [305, 223, 231],
                Direction::Right => [308, 224, 232],
                Direction::Down => [307, 225, 233],
                Direction::Left => [306, 226, 234],
            };
            match phase {
                Phase::Prepare => chain[0],
                Phase::Hold => chain[1],
                Phase::ParrySuccess => chain[2],
                _ => return Err(invalid("unsupported original-client guard phase")),
            }
        }
        Animation::Action {
            intent: Intent::Shield,
            phase,
        } => match phase {
            Phase::Prepare => 95,
            Phase::Hold => 309,
            Phase::ParrySuccess => 111,
            _ => return Err(invalid("unsupported original-client shield phase")),
        },
        Animation::Action {
            intent: Intent::Dash,
            phase,
        } => match phase {
            Phase::Strike => 439,
            Phase::Parried | Phase::Terrain => 443,
            _ => return Err(invalid("unsupported original-client dash phase")),
        },
    };
    Ok(state)
}

fn invalid(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}
