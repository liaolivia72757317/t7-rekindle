use super::ClientOutputState;
use crate::{
    battle_session::{OutputContext, SampledPose},
    client_battle_wire::{self, BotMoveWire, ClientBattleMessage},
};
use std::{collections::BTreeMap, io};
use t7r_battle_core::ai::{BotOutput, MovementIntent};

const TURN_DEGREES_PER_SECOND: f64 = 360.;
const RUN_DELAY_MS: u64 = 1000;
const WAYPOINT_RADIUS: f64 = 0.25;
const WALK_STATES: [u8; 8] = [2, 9, 8, 7, 6, 5, 4, 3];

#[derive(Clone, Copy, Default)]
pub(super) struct BotMotion {
    moving: bool,
    heading: i16,
    last_pose: Option<SampledPose>,
    last_turn_at_ms: Option<u64>,
    forward_since_ms: Option<u64>,
}

pub(super) fn project_bot(
    next: &mut ClientOutputState,
    output: &BotOutput,
    context: &OutputContext,
    client_time_offset_ms: Option<i64>,
    actor_rids: &BTreeMap<u32, u16>,
) -> io::Result<Vec<ClientBattleMessage>> {
    if !next.bots.contains(&output.actor_id) {
        return Err(invalid("movement actor is not a bound bot"));
    }
    let rid = client_battle_wire::client_rid(actor_rids, output.actor_id)?;
    let fresh = context.actor_poses.get(&output.actor_id).copied();
    if fresh.is_some_and(|pose| {
        pose.sampled_at_ms > context.at_ms
            || context.at_ms - pose.sampled_at_ms > 250
            || !valid_point(pose.position)
            || !pose.heading.is_finite()
            || !(-180. ..=180.).contains(&pose.heading)
    }) {
        return Err(invalid("bot output pose is stale or invalid"));
    }
    let mut motion = next
        .bot_motion
        .get(&output.actor_id)
        .copied()
        .unwrap_or_default();
    if motion
        .last_turn_at_ms
        .is_some_and(|last| context.at_ms < last)
    {
        return Err(invalid("bot movement output time regressed"));
    }
    if let Some(pose) = fresh {
        motion.last_pose = Some(pose);
        motion.heading = yaw_i16(pose.heading)?;
    }
    let mut messages = Vec::new();
    match &output.movement {
        MovementIntent::Stop => stop(
            &mut motion,
            rid,
            context.at_ms,
            client_time_offset_ms,
            &mut messages,
        )?,
        MovementIntent::Turn { target } => {
            let pose = fresh.ok_or_else(|| invalid("bot turn has no fresh pose"))?;
            if !valid_point(*target) {
                return Err(invalid("invalid bot turn target"));
            }
            stop(
                &mut motion,
                rid,
                context.at_ms,
                client_time_offset_ms,
                &mut messages,
            )?;
            let yaw = turn_toward(&motion, pose, *target, context.at_ms)?;
            if yaw != yaw_i16(pose.heading)? {
                messages.push(client_battle_wire::encode_bot_turn_for_client(
                    wire_tick(context.at_ms, client_time_offset_ms)?,
                    rid,
                    yaw,
                )?);
            }
            motion.heading = yaw;
            motion.last_turn_at_ms = Some(context.at_ms);
        }
        MovementIntent::Follow { points, look_at } => {
            let pose = fresh.ok_or_else(|| invalid("bot route has no fresh pose"))?;
            if points.iter().any(|point| !valid_point(*point))
                || look_at.is_some_and(|point| !valid_point(point))
            {
                return Err(invalid("invalid bot movement route"));
            }
            let waypoint = route_waypoint(points, pose.position);
            if let Some(waypoint) = waypoint {
                let facing = look_at.unwrap_or(waypoint);
                let yaw = turn_toward(&motion, pose, facing, context.at_ms)?;
                let desired = heading_to(pose.position, facing, pose.heading)?;
                if normalize(desired - f64::from(yaw)).abs() > 45. {
                    stop(
                        &mut motion,
                        rid,
                        context.at_ms,
                        client_time_offset_ms,
                        &mut messages,
                    )?;
                    if yaw != yaw_i16(pose.heading)? {
                        messages.push(client_battle_wire::encode_bot_turn_for_client(
                            wire_tick(context.at_ms, client_time_offset_ms)?,
                            rid,
                            yaw,
                        )?);
                    }
                    motion.heading = yaw;
                    motion.last_turn_at_ms = Some(context.at_ms);
                } else {
                    let travel = heading_to(pose.position, waypoint, f64::from(yaw))?;
                    let angle = normalize(travel - f64::from(yaw));
                    let sector = ((angle + 22.5) / 45.).floor().rem_euclid(8.) as usize;
                    let forward = matches!(sector, 0 | 1 | 7);
                    motion.forward_since_ms = if forward {
                        Some(motion.forward_since_ms.unwrap_or(context.at_ms))
                    } else {
                        None
                    };
                    let running = motion
                        .forward_since_ms
                        .is_some_and(|since| context.at_ms - since >= RUN_DELAY_MS);
                    let state = match (running, sector) {
                        (true, 0) => 10,
                        (true, 1) => 12,
                        (true, 7) => 11,
                        _ => WALK_STATES[sector],
                    };
                    let lateral = (-1000. * angle.to_radians().sin()).round() as i16;
                    let forward_back = (-1000. * angle.to_radians().cos()).round() as i16;
                    messages.push(client_battle_wire::encode_bot_move_for_client(
                        BotMoveWire {
                            tick_ms: wire_tick(context.at_ms, client_time_offset_ms)?,
                            client_rid: rid,
                            state,
                            left_right: lateral,
                            forward_back,
                            current_velocity: if running { 5000 } else { 3000 },
                            max_velocity: 25000,
                            position: pose.position.map(|value| value as f32),
                            direction_yaw: yaw,
                        },
                    )?);
                    motion.moving = true;
                    motion.heading = yaw;
                    motion.last_turn_at_ms = Some(context.at_ms);
                }
            } else {
                stop(
                    &mut motion,
                    rid,
                    context.at_ms,
                    client_time_offset_ms,
                    &mut messages,
                )?;
            }
        }
    }
    next.bot_motion.insert(output.actor_id, motion);
    Ok(messages)
}

pub(super) fn stop_dead_bot(
    next: &mut ClientOutputState,
    actor_id: u32,
    position: [f64; 3],
    heading: f64,
    at_ms: u64,
    client_time_offset_ms: Option<i64>,
    actor_rids: &BTreeMap<u32, u16>,
) -> io::Result<Vec<ClientBattleMessage>> {
    if !next.bots.contains(&actor_id) || !valid_point(position) {
        return Err(invalid("invalid dead bot movement state"));
    }
    let mut motion = next.bot_motion.get(&actor_id).copied().unwrap_or_default();
    if let Some(mut pose) = motion.last_pose {
        pose.position = position;
        motion.last_pose = Some(pose);
    }
    motion.heading = yaw_i16(heading)?;
    let mut messages = Vec::new();
    stop(
        &mut motion,
        client_battle_wire::client_rid(actor_rids, actor_id)?,
        at_ms,
        client_time_offset_ms,
        &mut messages,
    )?;
    next.bot_motion.insert(actor_id, motion);
    Ok(messages)
}

fn route_waypoint(points: &[[f64; 3]], position: [f64; 3]) -> Option<[f64; 3]> {
    let mut index = if points.len() < 2 {
        0
    } else {
        let mut best = (f64::INFINITY, 0);
        for (segment, pair) in points.windows(2).enumerate() {
            let dx = pair[1][0] - pair[0][0];
            let dy = pair[1][1] - pair[0][1];
            let dz = pair[1][2] - pair[0][2];
            let length_squared = dx * dx + dy * dy + dz * dz;
            let progress = if length_squared == 0. {
                0.
            } else {
                (((position[0] - pair[0][0]) * dx
                    + (position[1] - pair[0][1]) * dy
                    + (position[2] - pair[0][2]) * dz)
                    / length_squared)
                    .clamp(0., 1.)
            };
            let distance_squared = (position[0] - pair[0][0] - progress * dx).powi(2)
                + (position[1] - pair[0][1] - progress * dy).powi(2)
                + (position[2] - pair[0][2] - progress * dz).powi(2);
            if distance_squared <= best.0 {
                best = (distance_squared, segment + 1);
            }
        }
        best.1
    };
    while index < points.len()
        && points[index]
            .iter()
            .zip(position)
            .map(|(waypoint, actual)| (waypoint - actual).powi(2))
            .sum::<f64>()
            .sqrt()
            <= WAYPOINT_RADIUS
    {
        index += 1;
    }
    points.get(index).copied()
}

fn stop(
    motion: &mut BotMotion,
    rid: u16,
    at_ms: u64,
    client_time_offset_ms: Option<i64>,
    messages: &mut Vec<ClientBattleMessage>,
) -> io::Result<()> {
    if motion.moving {
        let pose = motion
            .last_pose
            .ok_or_else(|| invalid("moving bot has no sampled pose"))?;
        messages.push(client_battle_wire::encode_bot_move_for_client(
            BotMoveWire {
                tick_ms: wire_tick(at_ms, client_time_offset_ms)?,
                client_rid: rid,
                state: 1,
                left_right: 0,
                forward_back: 0,
                current_velocity: 0,
                max_velocity: 0,
                position: pose.position.map(|value| value as f32),
                direction_yaw: motion.heading,
            },
        )?);
    }
    motion.moving = false;
    motion.forward_since_ms = None;
    Ok(())
}

fn wire_tick(at_ms: u64, offset_ms: Option<i64>) -> io::Result<u32> {
    let offset_ms = offset_ms.ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            "battle client clock is not bound",
        )
    })?;
    u32::try_from(client_battle_wire::wire_time(at_ms, offset_ms)?)
        .map_err(|_| invalid("bot movement time exceeds wire range"))
}

fn turn_toward(
    motion: &BotMotion,
    pose: SampledPose,
    target: [f64; 3],
    at_ms: u64,
) -> io::Result<i16> {
    let desired = heading_to(pose.position, target, pose.heading)?;
    let last = motion.last_turn_at_ms.unwrap_or(at_ms.saturating_sub(100));
    let elapsed = at_ms.saturating_sub(last).min(100) as f64;
    let limit = TURN_DEGREES_PER_SECOND * elapsed / 1000.;
    yaw_i16(normalize(
        pose.heading + normalize(desired - pose.heading).clamp(-limit, limit),
    ))
}

fn heading_to(source: [f64; 3], target: [f64; 3], fallback: f64) -> io::Result<f64> {
    if !valid_point(source) || !valid_point(target) || !fallback.is_finite() {
        return Err(invalid("invalid bot heading geometry"));
    }
    let dx = target[0] - source[0];
    let dy = target[1] - source[1];
    Ok(if dx == 0. && dy == 0. {
        fallback
    } else {
        (-dy).atan2(dx).to_degrees()
    })
}

fn yaw_i16(value: f64) -> io::Result<i16> {
    if !value.is_finite() || !(-180. ..=180.).contains(&value) {
        return Err(invalid("bot heading exceeds wire range"));
    }
    Ok(value.round() as i16)
}

fn normalize(value: f64) -> f64 {
    (value + 180.).rem_euclid(360.) - 180.
}

fn valid_point(point: [f64; 3]) -> bool {
    point
        .iter()
        .all(|value| value.is_finite() && value.abs() < 100000.)
}

fn invalid(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}
