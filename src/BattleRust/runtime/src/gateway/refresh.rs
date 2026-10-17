use super::{
    platform::Identity,
    scene_actor_wire::{LocalSceneActor, SceneRoster},
    scene_entry_wire, scene_vision_wire,
};
use crate::{
    battle_session::{Output, OutputContext, SampledPose},
    client_battle_wire::{self, ClientBattleMessage},
};
use std::{collections::BTreeMap, io, mem::size_of};
use t7r_battle_core::battle_semantics::state::StateActor;

/// Travels in the same bounded queue as its projected messages; never visible before them.
#[derive(Default)]
pub(super) struct RefreshUpdate {
    squads: Option<std::sync::Arc<crate::battle_session::SquadRoster>>,
    actors: Vec<StateActor>,
    poses: Vec<(u32, SampledPose)>,
    local: Option<LocalSceneActor>,
    client_origin_ms: Option<i128>,
}

impl RefreshUpdate {
    pub(super) fn project(
        outputs: &[Output],
        context: &OutputContext,
        roster: Option<&SceneRoster>,
        client_offset: Option<i64>,
        server_offset: Option<i64>,
    ) -> Self {
        let actors: Vec<_> = outputs
            .iter()
            .filter_map(|output| match output {
                Output::State(state) => Some(state.actors.iter().copied()),
                _ => None,
            })
            .flatten()
            .collect();
        let local = roster
            .filter(|roster| {
                actors
                    .iter()
                    .any(|actor| actor.actor_id == roster.local.actor_id)
            })
            .map(|roster| roster.local.clone());
        Self {
            squads: outputs.iter().rev().find_map(|output| match output {
                Output::Squad { roster, .. } => Some(roster.clone()),
                _ => None,
            }),
            actors,
            poses: context
                .actor_poses
                .iter()
                .map(|(&id, &pose)| (id, pose))
                .collect(),
            local,
            client_origin_ms: client_offset
                .zip(server_offset)
                .map(|(client, server)| i128::from(server) - i128::from(client)),
        }
    }

    pub(super) fn is_empty(&self) -> bool {
        self.actors.is_empty() && self.poses.is_empty() && self.squads.is_none()
    }

    pub(super) fn heap_bytes(&self) -> usize {
        self.actors.capacity() * size_of::<StateActor>()
            + self.squads.as_ref().map_or(0, |s| s.heap_bytes())
            + self.poses.capacity() * size_of::<(u32, SampledPose)>()
            + self.local.as_ref().map_or(0, |actor| {
                actor.equipment.capacity() * size_of::<t7r_battle_core::action::EquipmentItem>()
            })
    }
}

pub(super) struct SceneRefresh {
    pub squads: std::sync::Arc<crate::battle_session::SquadRoster>,
    pub roster: SceneRoster,
    actors: BTreeMap<u32, StateActor>,
    animations: BTreeMap<u16, ClientBattleMessage>,
    pose_floors: BTreeMap<u32, u64>,
    client_origin_ms: Option<i128>,
}

impl SceneRefresh {
    pub(super) fn new(roster: SceneRoster, instance_started_at_ms: u64) -> Self {
        Self {
            squads: std::sync::Arc::new(crate::battle_session::SquadRoster::with_local_player(
                roster.bots.iter().map(|bot| (bot.actor_id, bot.camp)),
                (roster.local.actor_id, roster.local.camp),
            )),
            roster,
            actors: BTreeMap::new(),
            animations: BTreeMap::new(),
            pose_floors: BTreeMap::new(),
            // Preparation has no native samples yet; VISION still needs its movement lock.
            client_origin_ms: Some(i128::from(instance_started_at_ms)),
        }
    }

    pub(super) fn apply(&mut self, update: RefreshUpdate) {
        if let Some(squads) = update.squads {
            self.squads = squads;
        }
        if let Some(local) = update.local {
            // Loadout changes must not reset the last published position.
            self.roster.local.hero_id = local.hero_id;
            self.roster.local.max_health = local.max_health;
            self.roster.local.equipment = local.equipment;
        }
        for actor in update.actors {
            if actor.life == 5 && self.pose_floors.get(&actor.actor_id) != Some(&actor.life_at_ms) {
                self.pose_floors.insert(actor.actor_id, actor.life_at_ms);
                if let Some(member) = std::iter::once(&mut self.roster.local)
                    .chain(self.roster.bots.iter_mut())
                    .find(|member| member.actor_id == actor.actor_id)
                {
                    member.position = actor.spawn_position;
                    member.heading = actor.spawn_heading;
                }
            }
            self.actors.insert(actor.actor_id, actor);
        }
        for member in std::iter::once(&mut self.roster.local).chain(self.roster.bots.iter_mut()) {
            if let Some(actor) = self.actors.get(&member.actor_id) {
                member.hero_id = actor.hero_id;
                member.weapon_id = actor.weapon_id;
                member.offhand_id = actor.offhand_id;
            }
            if let Some((_, pose)) = update.poses.iter().find(|(id, pose)| {
                *id == member.actor_id
                    && pose.sampled_at_ms >= self.pose_floors.get(id).copied().unwrap_or(0)
            }) {
                member.position = pose.position;
                member.heading = pose.heading;
            }
        }
        if let Some(origin) = update.client_origin_ms {
            self.client_origin_ms = Some(origin);
        }
    }

    pub(super) fn record_message(&mut self, message: &ClientBattleMessage) {
        if message.command == 4
            && message.body.len() >= 14
            && matches!(&message.body[..2], [0, 3] | [0, 4])
        {
            let rid = u16::from_be_bytes([message.body[2], message.body[3]]);
            if self.roster.bots.iter().any(|bot| bot.client_rid == rid) {
                self.animations.insert(rid, message.clone());
            }
        }
    }

    pub(super) fn camp_score(&self, now_ms: u64) -> io::Result<ClientBattleMessage> {
        let mut kills = [0_u32; 2];
        for actor in self.actors.values() {
            let total = &mut kills[usize::from(actor.camp - 1)];
            *total = total.saturating_add(actor.score[0]).min(i32::MAX as u32);
        }
        client_battle_wire::encode_camp_kill_update(now_ms, 1, kills, Some(self.roster.kill_target))
    }

    pub(super) fn objects(
        &self,
        request: &[u8],
        identity: &Identity,
        now_ms: u64,
        local_hidden: bool,
        movement_active: bool,
    ) -> io::Result<Vec<ClientBattleMessage>> {
        let ids = scene_vision_wire::requested_actors(request, &self.roster)?;
        let mut messages = Vec::new();
        for &id in &ids {
            let local = id == self.roster.local.actor_id;
            if local && local_hidden {
                continue;
            }
            let state = self.actors.get(&id);
            messages.push(scene_vision_wire::actor(
                &self.roster,
                identity,
                id,
                state.map(|actor| actor.health),
            )?);
            if !local {
                let bot = self
                    .roster
                    .bots
                    .iter()
                    .find(|bot| bot.actor_id == id)
                    .expect("validated VISION reference");
                if let Some(animation) = self.animations.get(&bot.client_rid) {
                    messages.push(animation.clone());
                }
                if state.is_some_and(|actor| actor.life == 2) {
                    messages.push(client_battle_wire::encode_dead_for_client(
                        id,
                        now_ms,
                        &BTreeMap::from([(id, bot.client_rid)]),
                    )?);
                }
            }
        }
        if !ids.is_empty()
            && let Some(origin) = self.client_origin_ms
        {
            let elapsed = u32::try_from(i128::from(now_ms) - origin).map_err(|_| {
                io::Error::new(
                    io::ErrorKind::InvalidData,
                    "refresh activation clock out of range",
                )
            })?;
            messages.push(scene_entry_wire::move_active(elapsed, movement_active));
        }
        Ok(messages)
    }
}

#[cfg(test)]
mod tests;
