use super::{
    platform::Identity, scene_actor_wire::SceneRoster, scene_entry_wire, scene_vision_wire,
    scene_wire,
};
use crate::{
    battle_session::{Config, Life, MemberKind, Output, OutputContext, Score},
    client_battle_wire::{self, ClientBattleMessage},
};
use std::{
    collections::{BTreeMap, BTreeSet},
    io,
};
use t7r_battle_core::action::ActionEvent;

mod bot;
mod weapon;
use bot::BotMotion;

#[derive(Clone)]
pub(super) struct ClientOutputState {
    pub(super) training: bool,
    scores: BTreeMap<u32, Score>,
    camps: BTreeMap<u32, u8>,
    kill_target: u32,
    bots: BTreeSet<u32>,
    bot_motion: BTreeMap<u32, BotMotion>,
}

impl ClientOutputState {
    pub(super) fn new(config: &Config) -> Self {
        Self {
            training: false,
            scores: config
                .members
                .iter()
                .map(|member| (member.actor_id, Score::default()))
                .collect(),
            camps: config
                .members
                .iter()
                .map(|member| (member.actor_id, member.camp))
                .collect(),
            kill_target: config.kill_target,
            bots: config
                .members
                .iter()
                .filter(|member| member.kind == MemberKind::Bot)
                .map(|member| member.actor_id)
                .collect(),
            bot_motion: BTreeMap::new(),
        }
    }
}

pub(super) fn project_client_messages(
    current: &ClientOutputState,
    batch: &[Output],
    context: &OutputContext,
    client_time_offset_ms: Option<i64>,
    server_time_offset_ms: Option<i64>,
    local_actor_id: Option<u32>,
    actor_rids: &BTreeMap<u32, u16>,
) -> io::Result<(Vec<ClientBattleMessage>, ClientOutputState)> {
    project_client_messages_with_scene(
        current,
        batch,
        context,
        client_time_offset_ms,
        server_time_offset_ms,
        local_actor_id,
        actor_rids,
        None,
    )
}

pub(super) struct SceneProjection<'a> {
    pub roster: &'a SceneRoster,
    pub identity: &'a Identity,
}

#[allow(clippy::too_many_arguments)]
pub(super) fn project_client_messages_with_scene(
    current: &ClientOutputState,
    batch: &[Output],
    context: &OutputContext,
    client_time_offset_ms: Option<i64>,
    server_time_offset_ms: Option<i64>,
    local_actor_id: Option<u32>,
    actor_rids: &BTreeMap<u32, u16>,
    scene: Option<SceneProjection<'_>>,
) -> io::Result<(Vec<ClientBattleMessage>, ClientOutputState)> {
    let mut next = current.clone();
    if batch.is_empty() {
        return Err(invalid("invalid contextual battle output"));
    }
    let match_now_ms = batch.iter().find_map(|output| match output {
        Output::State(state) => Some(state.at_ms),
        _ => None,
    });
    if match_now_ms.is_some_and(|at_ms| at_ms != context.at_ms) {
        return Err(invalid(
            "output context differs from authoritative state time",
        ));
    }
    let state = batch.iter().find_map(|output| match output {
        Output::State(state) => Some(state),
        _ => None,
    });
    let mut messages = Vec::new();
    let mut pending_scores = Vec::new();
    let mut dead = None;
    let mut fatal_damage = None;
    for output in batch {
        match output {
            Output::Training(snapshot) => {
                messages.extend(super::training_wire::project(
                    snapshot,
                    bound_clock(server_time_offset_ms, "training server clock is not bound")?,
                    client_battle_wire::client_rid(
                        actor_rids,
                        local_actor_id.ok_or_else(|| invalid("training has no player"))?,
                    )?,
                )?);
            }
            Output::Squad {
                actor_id,
                command,
                result,
                previous,
                roster,
            } => {
                if local_actor_id == Some(*actor_id) {
                    messages.push(super::squad_wire::response(*command, *result));
                }
                messages.extend(super::squad_wire::changes(previous, roster, actor_rids)?);
            }
            Output::SpawnRejected { actor_id, result } if local_actor_id == Some(*actor_id) => {
                messages.push(scene_wire::actor_play_result(*result));
            }
            Output::Action(ActionEvent::WeaponChangeResult {
                actor_id,
                status,
                weapon_id,
                offhand_id,
            }) if local_actor_id == Some(*actor_id) => {
                messages.push(client_battle_wire::weapon::change_result(
                    *status,
                    *weapon_id,
                    *offhand_id,
                )?);
            }
            Output::Action(event @ ActionEvent::EquipmentChanged { .. }) => {
                messages.push(weapon::project(
                    event,
                    state.ok_or_else(|| invalid("equipment output has no authoritative state"))?,
                    scene
                        .as_ref()
                        .ok_or_else(|| invalid("equipment scene is not bound"))?,
                    bound_clock(server_time_offset_ms, "battle server clock is not bound")?,
                    actor_rids,
                )?);
            }
            Output::Action(ActionEvent::Animation(event)) => {
                messages.push(client_battle_wire::encode_animation_for_client_clock(
                    event,
                    bound_clock(client_time_offset_ms, "battle client clock is not bound")?,
                    actor_rids,
                )?);
            }
            Output::Damage {
                attacker,
                target,
                weapon_id,
                health,
                evidence,
                ..
            } => {
                let now = match_now_ms.ok_or_else(|| invalid("damage batch has no match clock"))?;
                let actor = local_actor_id.ok_or_else(|| invalid("damage has no local player"))?;
                messages.extend(client_battle_wire::encode_damage_for_client_clock(
                    output,
                    actor,
                    now,
                    bound_clock(server_time_offset_ms, "battle server clock is not bound")?,
                    actor_rids,
                )?);
                if *health == 0
                    && fatal_damage
                        .replace((
                            *attacker,
                            *target,
                            *weapon_id,
                            evidence.target_position,
                            evidence.target_heading,
                        ))
                        .is_some()
                {
                    return Err(invalid("overlapping fatal damage outputs"));
                }
            }
            Output::Guard { actor_id, value } => {
                let local = local_actor_id.ok_or_else(|| invalid("guard has no local player"))?;
                if *actor_id == local {
                    messages.push(client_battle_wire::encode_guard_value_for_client(*value)?);
                }
            }
            Output::Shield {
                actor_id,
                weapon_id,
                health,
            } => messages.extend(project_shield(
                *actor_id,
                *weapon_id,
                *health,
                state.ok_or_else(|| invalid("shield output has no authoritative state"))?,
                scene
                    .as_ref()
                    .ok_or_else(|| invalid("shield scene is not bound"))?,
                local_actor_id,
                server_time_offset_ms,
                actor_rids,
            )?),
            Output::Parry { .. } => {
                messages.extend(client_battle_wire::parry::encode_parry_for_client_clock(
                    output,
                    bound_clock(server_time_offset_ms, "battle server clock is not bound")?,
                    actor_rids,
                )?)
            }
            Output::SurfaceHit { .. } => messages.push(
                client_battle_wire::surface::encode_surface_hit_for_client_clock(
                    output,
                    bound_clock(server_time_offset_ms, "battle server clock is not bound")?,
                    actor_rids,
                )?,
            ),
            Output::Bot(bot_output) => messages.extend(bot::project_bot(
                &mut next,
                bot_output,
                context,
                client_time_offset_ms,
                actor_rids,
            )?),
            Output::Score { actor_id, score } => pending_scores.push((*actor_id, *score)),
            Output::Life {
                actor_id,
                state: Life::Dead { .. },
            } => {
                if dead.replace(*actor_id).is_some() {
                    return Err(invalid("overlapping death outputs"));
                }
                if next.bots.contains(actor_id) {
                    let (_, _, _, position, heading) = fatal_damage
                        .filter(|(_, target, ..)| target == actor_id)
                        .ok_or_else(|| invalid("bot death has no fatal contact evidence"))?;
                    messages.extend(bot::stop_dead_bot(
                        &mut next,
                        *actor_id,
                        position,
                        heading,
                        context.at_ms,
                        client_time_offset_ms,
                        actor_rids,
                    )?);
                }
            }
            Output::Life {
                actor_id,
                state: Life::Selecting { .. },
            } if local_actor_id == Some(*actor_id) => {
                let now =
                    match_now_ms.ok_or_else(|| invalid("selection batch has no match clock"))?;
                let wire_at = client_battle_wire::wire_time(
                    now,
                    bound_clock(server_time_offset_ms, "battle server clock is not bound")?,
                )?;
                messages.push(scene_wire::actor_state(
                    client_battle_wire::client_rid(actor_rids, *actor_id)?,
                    7,
                    wire_at,
                )?);
            }
            Output::Life {
                actor_id,
                state: Life::Spawning { spawn, .. },
            } => {
                let actor = state
                    .and_then(|value| {
                        value
                            .actors
                            .iter()
                            .find(|actor| actor.actor_id == *actor_id)
                    })
                    .ok_or_else(|| invalid("respawn has no authoritative actor state"))?;
                if actor.life != 5
                    || actor.spawn_position != spawn.position
                    || actor.spawn_heading != spawn.heading
                {
                    return Err(invalid("respawn differs from authoritative actor state"));
                }
                messages.extend(project_respawn(
                    &mut next,
                    *actor_id,
                    actor,
                    context.at_ms,
                    client_time_offset_ms,
                    server_time_offset_ms,
                    local_actor_id,
                    actor_rids,
                    scene
                        .as_ref()
                        .ok_or_else(|| invalid("respawn scene is not bound"))?,
                )?);
            }
            Output::Life {
                actor_id,
                state: Life::Alive,
            } if local_actor_id == Some(*actor_id) => {
                let elapsed = u32::try_from(client_battle_wire::wire_time(
                    context.at_ms,
                    bound_clock(client_time_offset_ms, "battle client clock is not bound")?,
                )?)
                .map_err(|_| invalid("move activation exceeds instance wire range"))?;
                messages.push(scene_entry_wire::move_active(elapsed, true));
            }
            Output::Kill {
                attacker,
                target,
                first_blood,
                butcher_ended,
            } => {
                let now = match_now_ms.ok_or_else(|| invalid("kill batch has no match clock"))?;
                let state =
                    state.ok_or_else(|| invalid("kill batch has no authoritative state"))?;
                let offsets = (
                    bound_clock(client_time_offset_ms, "battle client clock is not bound")?,
                    bound_clock(server_time_offset_ms, "battle server clock is not bound")?,
                );
                let weapon = fatal_damage
                    .take()
                    .ok_or_else(|| invalid("kill has no fatal damage"))?;
                if weapon.0 != *attacker || weapon.1 != *target || dead.take() != Some(*target) {
                    return Err(invalid("kill does not match fatal damage and death"));
                }
                let scores = std::mem::take(&mut pending_scores);
                messages.extend(project_kill(
                    &mut next,
                    KillOutput {
                        attacker: *attacker,
                        target: *target,
                        weapon_id: weapon.2,
                        first_blood: *first_blood,
                        butcher_ended: *butcher_ended,
                        scores: &scores,
                        match_now_ms: now,
                        state,
                        offsets,
                        local_actor_id,
                        actor_rids,
                    },
                )?);
                if local_actor_id == Some(*target)
                    && !batch
                        .iter()
                        .any(|output| matches!(output, Output::Ended(_)))
                {
                    messages.push(scene_wire::actor_state(
                        client_battle_wire::client_rid(actor_rids, *target)?,
                        9,
                        client_battle_wire::wire_time(now, offsets.1)?,
                    )?);
                }
            }
            Output::Ended(settlement) => {
                if settlement.members.len() != next.scores.len()
                    || settlement
                        .members
                        .iter()
                        .any(|member| next.scores.get(&member.actor_id) != Some(&member.score))
                {
                    return Err(invalid("settlement differs from projected live scores"));
                }
                if next.training {
                    continue;
                }
                let server_offset =
                    bound_clock(server_time_offset_ms, "battle server clock is not bound")?;
                let mut client_settlement = settlement.clone();
                client_settlement.started_at_ms =
                    client_battle_wire::wire_time(settlement.started_at_ms, server_offset)?;
                client_settlement.ended_at_ms =
                    client_battle_wire::wire_time(settlement.ended_at_ms, server_offset)?;
                messages.extend(client_battle_wire::encode_settlement_start_for_client(
                    &client_settlement,
                    actor_rids,
                )?);
            }
            Output::ContactOverflow { .. } => {
                return Err(invalid("contact evidence loss prevents client projection"));
            }
            _ => {}
        }
    }
    if !pending_scores.is_empty() || dead.is_some() || fatal_damage.is_some() {
        return Err(invalid("incomplete fatal output batch"));
    }
    Ok((messages, next))
}

#[allow(clippy::too_many_arguments)]
fn project_shield(
    actor_id: u32,
    weapon_id: u32,
    health: u32,
    state: &t7r_battle_core::battle_semantics::state::MatchState,
    scene: &SceneProjection<'_>,
    local_actor_id: Option<u32>,
    server_time_offset_ms: Option<i64>,
    actor_rids: &BTreeMap<u32, u16>,
) -> io::Result<Vec<ClientBattleMessage>> {
    let actor = state
        .actors
        .iter()
        .find(|actor| actor.actor_id == actor_id)
        .ok_or_else(|| invalid("shield actor is absent from authoritative state"))?;
    if actor.shields[..usize::from(actor.shield_count)]
        .iter()
        .find(|(id, _)| *id == weapon_id)
        .map(|(_, value)| *value)
        != Some(health)
    {
        return Err(invalid(
            "shield durability differs from authoritative state",
        ));
    }
    let scene_actor = if local_actor_id == Some(actor_id) {
        &scene.roster.local
    } else {
        scene
            .roster
            .bots
            .iter()
            .find(|member| member.actor_id == actor_id)
            .ok_or_else(|| invalid("shield actor is absent from client scene"))?
    };
    let mut messages = Vec::new();
    if local_actor_id == Some(actor_id) {
        let maximum = scene
            .roster
            .local_shields
            .iter()
            .find(|(id, _)| *id == weapon_id)
            .map(|(_, value)| *value)
            .ok_or_else(|| invalid("local shield maximum is absent"))?;
        let slot = scene_actor
            .equipment
            .iter()
            .find(|item| item.weapon_id == weapon_id)
            .ok_or_else(|| invalid("local shield slot is absent"))?
            .slot;
        messages.push(client_battle_wire::encode_shield_health_for_client(
            weapon_id, slot, health, maximum,
        )?);
    }
    if health == 0 {
        if actor.offhand_id != 0 {
            return Err(invalid("broken shield remains equipped"));
        }
        let now = client_battle_wire::wire_time(
            state.at_ms,
            bound_clock(server_time_offset_ms, "battle server clock is not bound")?,
        )?;
        messages.push(client_battle_wire::encode_shield_break_for_client(
            actor_id, weapon_id, actor_rids,
        )?);
        messages.push(client_battle_wire::encode_equipment_update_for_client(
            actor_id,
            &scene_actor.equipment,
            actor.weapon_id,
            actor.offhand_id,
            now,
            actor_rids,
        )?);
    }
    Ok(messages)
}

#[allow(clippy::too_many_arguments)]
fn project_respawn(
    next: &mut ClientOutputState,
    actor_id: u32,
    actor: &t7r_battle_core::battle_semantics::state::StateActor,
    match_now_ms: u64,
    client_time_offset_ms: Option<i64>,
    server_time_offset_ms: Option<i64>,
    local_actor_id: Option<u32>,
    actor_rids: &BTreeMap<u32, u16>,
    scene: &SceneProjection<'_>,
) -> io::Result<Vec<ClientBattleMessage>> {
    let rid = client_battle_wire::client_rid(actor_rids, actor_id)?;
    let visible = if scene.roster.local.actor_id == actor_id {
        &scene.roster.local
    } else {
        scene
            .roster
            .bots
            .iter()
            .find(|member| member.actor_id == actor_id)
            .ok_or_else(|| invalid("respawn actor is absent from client scene"))?
    };
    if visible.hero_id != actor.hero_id
        || visible.weapon_id != actor.weapon_id
        || visible.offhand_id != actor.offhand_id
    {
        return Err(invalid(
            "respawn appearance differs from authoritative loadout",
        ));
    }
    let elapsed = u32::try_from(client_battle_wire::wire_time(
        match_now_ms,
        bound_clock(client_time_offset_ms, "battle client clock is not bound")?,
    )?)
    .map_err(|_| invalid("respawn animation exceeds instance wire range"))?;
    let sequence = actor.sequence;
    let mut messages = Vec::new();
    if local_actor_id == Some(actor_id) {
        let now = client_battle_wire::wire_time(
            match_now_ms,
            bound_clock(server_time_offset_ms, "battle server clock is not bound")?,
        )?;
        messages.push(scene_wire::actor_play_result(0));
        messages.push(scene_wire::actor_state(rid, 6, now)?);
    } else if !next.bots.contains(&actor_id) {
        return Err(invalid("respawn actor has no client scene member"));
    }
    messages.push(scene_entry_wire::vision_del(rid));
    messages.push(scene_vision_wire::respawn_actor(
        actor_id,
        actor.spawn_position,
        actor.spawn_heading,
        scene.roster,
        scene.identity,
    )?);
    if local_actor_id == Some(actor_id) {
        for &(weapon_id, health) in &actor.shields[..usize::from(actor.shield_count)] {
            let slot = scene
                .roster
                .local
                .equipment
                .iter()
                .find(|item| item.weapon_id == weapon_id)
                .ok_or_else(|| invalid("respawn shield has no local equipment slot"))?
                .slot;
            messages.push(client_battle_wire::encode_shield_health_for_client(
                weapon_id, slot, health, health,
            )?);
        }
        messages.push(client_battle_wire::encode_guard_value_for_client(
            actor.guard,
        )?);
    } else {
        next.bot_motion.remove(&actor_id);
    }
    messages.push(scene_entry_wire::idle_state(rid, sequence, elapsed));
    if local_actor_id == Some(actor_id) {
        messages.push(client_battle_wire::encode_health_value_for_client(
            actor.health,
        )?);
        messages.push(scene_entry_wire::move_active(elapsed, false));
    }
    Ok(messages)
}

struct KillOutput<'a> {
    attacker: u32,
    target: u32,
    weapon_id: u32,
    first_blood: bool,
    butcher_ended: bool,
    scores: &'a [(u32, Score)],
    match_now_ms: u64,
    state: &'a t7r_battle_core::battle_semantics::state::MatchState,
    offsets: (i64, i64),
    local_actor_id: Option<u32>,
    actor_rids: &'a BTreeMap<u32, u16>,
}

fn project_kill(
    next: &mut ClientOutputState,
    kill: KillOutput<'_>,
) -> io::Result<Vec<ClientBattleMessage>> {
    let KillOutput {
        attacker,
        target,
        weapon_id,
        first_blood,
        butcher_ended,
        scores,
        match_now_ms,
        state,
        offsets,
        local_actor_id,
        actor_rids,
    } = kill;
    if scores.len() != 2 || scores[0].0 != attacker || scores[1].0 != target {
        return Err(invalid("kill score outputs are incomplete or unordered"));
    }
    let server_at = client_battle_wire::wire_time(match_now_ms, offsets.1)?;
    let client_at = u32::try_from(client_battle_wire::wire_time(match_now_ms, offsets.0)?)
        .map_err(|_| invalid("death animation exceeds instance wire range"))?;
    let core_sequence = state
        .actors
        .iter()
        .find(|actor| actor.actor_id == target)
        .ok_or_else(|| invalid("death actor is absent from authoritative state"))?
        .sequence;
    let death_sequence = sequence_with_offset(core_sequence, 1)?;
    let previous_winner = *next
        .scores
        .get(&attacker)
        .ok_or_else(|| invalid("unbound killer score"))?;
    let previous_victim = *next
        .scores
        .get(&target)
        .ok_or_else(|| invalid("unbound victim score"))?;
    let winner = scores[0].1;
    let victim = scores[1].1;
    if winner.kills != previous_winner.kills.saturating_add(1).min(i32::MAX as u32)
        || winner.points
            != previous_winner
                .points
                .saturating_add(100)
                .min(i32::MAX as u32)
        || winner.streak
            != previous_winner
                .streak
                .saturating_add(1)
                .min(i32::MAX as u32)
        || victim.deaths
            != previous_victim
                .deaths
                .saturating_add(1)
                .min(i32::MAX as u32)
        || victim.streak != 0
        || butcher_ended != (previous_victim.streak >= 10)
    {
        return Err(invalid(
            "kill score transition differs from authoritative state",
        ));
    }
    let mut messages = vec![client_battle_wire::encode_kill_for_client(
        attacker, target, weapon_id, server_at, actor_rids,
    )?];
    if winner.streak >= 2 {
        messages.push(client_battle_wire::encode_streak_for_client(
            attacker,
            winner.streak,
            server_at,
            actor_rids,
        )?);
    }
    if butcher_ended {
        messages.push(client_battle_wire::encode_butcher_end_for_client(
            attacker, server_at, actor_rids,
        )?);
    }
    messages.push(client_battle_wire::encode_score_update_for_client(
        attacker,
        previous_winner,
        winner,
        if first_blood { 730000 } else { 0 },
        server_at,
        actor_rids,
    )?);
    messages.push(client_battle_wire::encode_score_update_for_client(
        target,
        previous_victim,
        victim,
        0,
        server_at,
        actor_rids,
    )?);
    next.scores.insert(attacker, winner);
    next.scores.insert(target, victim);
    let mut camp_kills = [0_u32; 2];
    for (&actor, score) in &next.scores {
        let camp = *next
            .camps
            .get(&actor)
            .ok_or_else(|| invalid("unbound score camp"))?;
        if !(1..=2).contains(&camp) {
            return Err(invalid("invalid score camp"));
        }
        let total = &mut camp_kills[usize::from(camp - 1)];
        *total = total.saturating_add(score.kills).min(i32::MAX as u32);
    }
    messages.push(client_battle_wire::encode_camp_kill_update(
        server_at,
        1,
        camp_kills,
        Some(next.kill_target),
    )?);
    messages.push(client_battle_wire::encode_death_state_for_client(
        target,
        attacker,
        death_sequence,
        client_at,
        actor_rids,
    )?);
    if local_actor_id == Some(target) {
        messages.push(scene_entry_wire::move_active(client_at, false));
    }
    messages.push(client_battle_wire::encode_dead_for_client(
        target, server_at, actor_rids,
    )?);
    Ok(messages)
}

fn sequence_with_offset(sequence: u16, offset: u32) -> io::Result<u16> {
    if sequence == 0 {
        return Err(invalid("invalid action sequence"));
    }
    Ok(((u64::from(sequence) - 1 + u64::from(offset)) % u64::from(u16::MAX) + 1) as u16)
}

fn bound_clock(offset: Option<i64>, message: &'static str) -> io::Result<i64> {
    offset.ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, message))
}

fn invalid(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

#[cfg(test)]
mod surface_tests {
    use super::*;
    use crate::battle_session::contacts::ContactEvent;
    use t7r_battle_core::{action::Phase, damage::Region};

    #[test]
    fn surface_evidence_projects_once_with_bound_clock_and_rid() {
        let config = Config {
            epoch: 7,
            started_at_ms: 0,
            duration_ms: 10_000,
            kill_target: 10,
            seed: 1,
            members: Vec::new(),
        };
        let current = ClientOutputState::new(&config);
        let evidence = ContactEvent {
            guard_volume: false,
            actor_id: 1,
            target_id: 0,
            source_entity: 101,
            target_entity: 0,
            sequence: 1,
            phase_at_ms: 100,
            target_intent: None,
            target_phase: Phase::Idle,
            target_phase_at_ms: 0.,
            target_observed_at_ms: None,
            source_observed_at_ms: None,
            occurred_at_ms: 250.5,
            along: 0.5,
            defence_weapon_id: 0,
            region: Region::Torso,
            body_index: -1,
            source_position: [0.; 3],
            target_position: [0.; 3],
            source_heading: 0.,
            target_heading: 0.,
            source_velocity: [0.; 3],
            target_velocity: [0.; 3],
            weapon_velocity: [0.; 3],
            velocity_ready: false,
            airborne: false,
            target_airborne: false,
            position: [1., 2., 3.],
            normal: [0., 0., 1.],
            direction: [1., 0., 0.],
            material: 0,
        };
        let hit = Output::SurfaceHit {
            attacker: 1,
            weapon_id: 701,
            ground: true,
            evidence: Box::new(evidence),
        };
        let batch = [
            Output::Contact {
                attacker: 1,
                target: 0,
                ground: true,
                occurred_at_ms: 250.5,
            },
            hit.clone(),
        ];
        let context = OutputContext {
            at_ms: 250,
            actor_poses: BTreeMap::new(),
        };
        let rids = BTreeMap::from([(1, 101)]);
        let (messages, _) =
            project_client_messages(&current, &batch, &context, None, Some(5000), None, &rids)
                .unwrap();
        assert_eq!(messages.len(), 1);
        assert_eq!(
            messages[0],
            client_battle_wire::surface::encode_surface_hit_for_client_clock(&hit, 5000, &rids)
                .unwrap()
        );
        assert!(
            project_client_messages(&current, &batch, &context, None, None, None, &rids).is_err()
        );
        assert!(
            project_client_messages(
                &current,
                &batch,
                &context,
                None,
                Some(5000),
                None,
                &BTreeMap::new(),
            )
            .is_err()
        );
    }
}

#[cfg(test)]
mod tests;
