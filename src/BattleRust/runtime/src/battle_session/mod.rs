//! In-process sampled-world battle owner. Network adapters publish semantic output.
mod actions;
mod contact_ingress;
pub mod contacts;
#[cfg(test)]
mod health_tests;
mod life;
#[cfg(test)]
mod motion_tests;
mod profiles;
#[cfg(test)]
mod publication_tests;
mod selection;
mod squad_spawn;
mod squads;
mod state_publication;
pub use squads::{
    MAX_SQUAD_MEMBERS, MAX_SQUADS_PER_CAMP, Squad, SquadCommand, SquadMember, SquadRoster,
};
#[cfg(test)]
mod squad_tests;
#[cfg(test)]
pub(crate) use squad_tests::selecting_squad_fixture;
pub mod training;
#[cfg(test)]
mod training_tests;
mod types;

use crate::battle_resources::BattleResources;
pub(crate) use selection::PreparedHero;
#[cfg(test)]
pub(crate) use selection::tests::selecting_fixture;
use std::{
    collections::{BTreeMap, BTreeSet},
    io,
    sync::Arc,
};
pub use t7r_battle_core::action::EquipmentItem;
use t7r_battle_core::{action, ai, guard_meter::GuardMeter};
pub use t7r_battle_core::{
    action::{Direction, Intent, Phase},
    damage::Region,
};
use t7r_battle_resources::ResourceBundle;
use types::*;
pub use types::{
    ActorSnapshot, Config, EndReason, Input, Life, Member, MemberKind, Output, Pose, RankedScore,
    Score, Settlement, SourceWorld, World,
};

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct SampledPose {
    pub position: [f64; 3],
    pub heading: f64,
    pub sampled_at_ms: u64,
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct OutputContext {
    pub at_ms: u64,
    pub actor_poses: BTreeMap<u32, SampledPose>,
}

#[derive(Clone)]
struct State {
    training: Option<training::Training>,
    now_ms: u64,
    squads: Arc<SquadRoster>,
    actors: BTreeMap<u32, Arc<Actor>>,
    guard_meters: BTreeMap<u32, GuardMeter>,
    world: Option<Arc<World>>,
    actor_sampled_at_ms: BTreeMap<u32, u64>,
    source_cursors: BTreeMap<u64, (u64, u64)>,
    player_cursors: BTreeMap<u32, (u64, u64)>,
    legacy_world_used: bool,
    ai: ai::Controller,
    contact_cursors: BTreeMap<u64, u64>,
    source_contact_through: BTreeMap<u64, f64>,
    pending_contacts: Arc<Vec<contacts::ContactEvent>>,
    contact_through: f64,
    first_blood: bool,
    settlement: Option<Settlement>,
    state_revision: u64,
    last_checkpoint_at_ms: u64,
}

pub struct BattleSession {
    config: Config,
    resources: Arc<BattleResources>,
    state: State,
    single_source: bool,
}

impl BattleSession {
    pub fn new(
        bundle: &ResourceBundle,
        resources: Arc<BattleResources>,
        config: Config,
    ) -> io::Result<Self> {
        if config.epoch == 0
            || config.members.is_empty()
            || config.members.len() > t7r_battle_core::MAX_BATTLE_ACTORS
            || config.duration_ms == 0
            || config.duration_ms > 86400000
            || config.kill_target == 0
            || config.kill_target > i32::MAX as u32
            || resources.rules_version() != bundle.rules_version()
            || resources.resource_version() != bundle.resource_version()
            || resources.map_id() != bundle.map_id()
        {
            return Err(invalid("invalid battle configuration"));
        }
        deadline(config.started_at_ms, config.duration_ms)?;
        let mut actors = BTreeMap::new();
        let mut bots = Vec::new();
        let mut human_sources = BTreeSet::new();
        for member in &config.members {
            if !(1..=t7r_battle_core::MAX_BATTLE_ACTORS as u32).contains(&member.actor_id)
                || !(1..=2).contains(&member.camp)
                || member.sample_source_id == 0
                || actors.contains_key(&member.actor_id)
                || member.kind == MemberKind::Human
                    && !human_sources.insert(member.sample_source_id)
            {
                return Err(invalid("invalid battle member"));
            }
            let actor_resources = Arc::new(profiles::parse(bundle, member)?);
            let action = action::ActionState::new(
                member.actor_id,
                &actor_resources.loadout,
                member.weapon_id,
                1,
            )
            .map_err(invalid)?;
            let shields = actor_resources
                .damage
                .weapons
                .iter()
                .filter(|(_, w)| w.shield_max > 0)
                .map(|(&id, w)| (id, w.shield_max))
                .collect();
            actors.insert(
                member.actor_id,
                Arc::new(Actor {
                    member: member.clone(),
                    health: actor_resources.damage.max_health,
                    resources: actor_resources,
                    action,
                    life: Life::Alive,
                    contact_life_at_ms: config.started_at_ms,
                    last_attacked_at_ms: None,
                    score: Score::default(),
                    shields,
                    buffs: BTreeMap::new(),
                    swings: Vec::new(),
                    release_at_ms: None,
                }),
            );
            if member.kind == MemberKind::Bot {
                let camp = usize::from(member.camp - 1);
                let own: Vec<_> = resources.camps()[camp]
                    .spawns()
                    .iter()
                    .map(|s| s.position)
                    .collect();
                let other: Vec<_> = resources.camps()[1 - camp]
                    .spawns()
                    .iter()
                    .map(|s| s.position)
                    .collect();
                bots.push(
                    ai::BotConfig::from_spawns(
                        member.actor_id,
                        bots.len(),
                        member.camp,
                        config.seed,
                        &own,
                        &other,
                    )
                    .map_err(invalid)?,
                );
            }
        }
        if human_sources.is_empty()
            || actors.values().any(|actor| {
                actor.member.kind == MemberKind::Bot
                    && !human_sources.contains(&actor.member.sample_source_id)
            })
        {
            return Err(invalid("battle requires an owner for each sampled actor"));
        }
        let ai = ai::Controller::new(config.started_at_ms, bots).map_err(invalid)?;
        let now_ms = config.started_at_ms;
        let guard_meters = actors
            .keys()
            .map(|&id| {
                Ok((
                    id,
                    GuardMeter::new(resources.guard_rules()).map_err(invalid)?,
                ))
            })
            .collect::<io::Result<_>>()?;
        let bots = config
            .members
            .iter()
            .filter(|m| m.kind == MemberKind::Bot)
            .map(|m| (m.actor_id, m.camp));
        // Local scenes have one human; multi-source matches keep explicit player membership.
        let squads = if human_sources.len() == 1 {
            let player = config
                .members
                .iter()
                .find(|m| m.kind == MemberKind::Human)
                .expect("validated battle has a human");
            SquadRoster::with_local_player(bots, (player.actor_id, player.camp))
        } else {
            SquadRoster::with_bots(bots)
        };
        let training = t7r_battle_resources::training_maps::contains(bundle.map_id()).then(|| {
            training::Training::new(
                bundle.map_id(),
                config
                    .members
                    .iter()
                    .find(|member| member.kind == MemberKind::Human)
                    .expect("validated human")
                    .actor_id,
                now_ms,
            )
        });
        Ok(Self {
            config,
            resources,
            single_source: human_sources.len() == 1,
            state: State {
                training,
                now_ms,
                squads: Arc::new(squads),
                actors,
                guard_meters,
                ai,
                world: None,
                actor_sampled_at_ms: BTreeMap::new(),
                source_cursors: BTreeMap::new(),
                player_cursors: BTreeMap::new(),
                legacy_world_used: false,
                contact_cursors: BTreeMap::new(),
                source_contact_through: BTreeMap::new(),
                pending_contacts: Arc::new(Vec::new()),
                contact_through: 0.,
                first_blood: false,
                settlement: None,
                state_revision: 0,
                last_checkpoint_at_ms: now_ms,
            },
        })
    }

    pub fn actor(&self, id: u32) -> Option<ActorSnapshot> {
        self.state
            .actors
            .get(&id)
            .map(|a| a.snapshot(self.state.guard_meters[&id].value()))
    }
    pub fn squads(&self) -> &SquadRoster {
        &self.state.squads
    }
    pub(crate) fn actor_shields(&self, id: u32) -> Option<Vec<(u32, u32)>> {
        self.state.actors.get(&id).map(|actor| {
            actor
                .shields
                .iter()
                .map(|(&weapon, &health)| (weapon, health))
                .collect()
        })
    }
    pub fn settlement(&self) -> Option<&Settlement> {
        self.state.settlement.as_ref()
    }
    pub fn epoch(&self) -> u64 {
        self.config.epoch
    }
    pub fn started_at_ms(&self) -> u64 {
        self.config.started_at_ms
    }
    pub fn next_deadline_ms(&self, after_ms: Option<u64>) -> Option<u64> {
        if self.state.settlement.is_some() {
            return None;
        }
        let mut due = self
            .config
            .started_at_ms
            .checked_add(self.config.duration_ms)
            .filter(|value| after_ms.is_none_or(|after| *value > after));
        if let Some(training) = &self.state.training {
            due = training
                .due()
                .filter(|value| after_ms.is_none_or(|after| *value > after));
        }
        for actor in self.state.actors.values() {
            let action = actor.action.snapshot();
            for deadline in [
                action.phase_due_ms,
                action.hurt_due_ms,
                action.movement_due_ms,
                actor.release_at_ms,
                match actor.life {
                    Life::Dead { respawn_at_ms, .. } => Some(respawn_at_ms),
                    _ => None,
                },
            ]
            .into_iter()
            .flatten()
            {
                if after_ms.is_none_or(|after| deadline > after) {
                    due = Some(due.map_or(deadline, |old| old.min(deadline)));
                }
            }
        }
        due
    }

    /// Publication must accept the whole batch or reject it without side effects.
    /// Actor/profile/world storage is shared; only changed actors are copied.
    pub fn dispatch(
        &mut self,
        epoch: u64,
        now_ms: u64,
        input: Input,
        publish: impl FnOnce(&[Output]) -> io::Result<()>,
    ) -> io::Result<()> {
        self.dispatch_with_context(epoch, now_ms, input, |events, _| publish(events))
    }

    pub(crate) fn dispatch_with_context(
        &mut self,
        epoch: u64,
        now_ms: u64,
        input: Input,
        publish: impl FnOnce(&[Output], &OutputContext) -> io::Result<()>,
    ) -> io::Result<()> {
        if epoch != self.config.epoch || now_ms < self.state.now_ms {
            return Err(invalid("stale battle epoch or time"));
        }
        let mut next = self.state.clone();
        let mut events = Vec::new();
        next.now_ms = now_ms;
        if next.settlement.is_none() {
            next.training_tick(&mut events)?;
            match input {
                Input::Training {
                    source_id,
                    actor_id,
                    command,
                } => {
                    next.validate_source(source_id, actor_id, MemberKind::Human)?;
                    next.training_command(command, &self.resources, &mut events)?;
                }
                Input::World(world) => {
                    next.observe(world, &self.config, &mut events)?;
                }
                Input::SourceWorld(world) => {
                    next.observe_source(world, &self.config, &mut events)?;
                }
                Input::MotionAttempt(attempt) => {
                    self.require_single_source()?;
                    next.observe_motion(attempt)?;
                }
                Input::SourceMotionAttempt { source_id, attempt } => {
                    next.validate_source(source_id, attempt.actor_id, MemberKind::Bot)?;
                    next.observe_motion(attempt)?;
                }
                Input::Action { actor_id, command } => {
                    self.require_single_source()?;
                    next.action(actor_id, command, None, &mut events)?;
                }
                Input::SourceAction {
                    source_id,
                    actor_id,
                    command,
                } => {
                    next.validate_source(source_id, actor_id, MemberKind::Human)?;
                    if !matches!(
                        command,
                        action::Command::Press { .. }
                            | action::Command::ReleaseAttack
                            | action::Command::ReleaseGuard
                            | action::Command::ChangeSlot(_)
                    ) {
                        return Err(invalid("source action is not a player command"));
                    }
                    next.action(actor_id, command, None, &mut events)?;
                }
                Input::PlayerAction {
                    stamp,
                    actor_id,
                    command,
                } => {
                    next.validate_source(stamp.source_id, actor_id, MemberKind::Human)?;
                    if stamp.epoch != self.config.epoch
                        || stamp.batch_id == 0
                        || stamp.at_ms < self.config.started_at_ms
                        || stamp.at_ms < next.actors[&actor_id].contact_life_at_ms
                        || stamp.at_ms > now_ms
                        || next
                            .player_cursors
                            .get(&actor_id)
                            .is_some_and(|&(batch, at)| stamp.batch_id <= batch || stamp.at_ms < at)
                        || !matches!(
                            command,
                            action::Command::Press { .. }
                                | action::Command::ReleaseAttack
                                | action::Command::ReleaseGuard
                                | action::Command::ChangeSlot(_)
                        )
                    {
                        return Err(invalid("invalid player command stamp or command"));
                    }
                    next.action(actor_id, command, None, &mut events)?;
                    next.player_cursors
                        .insert(actor_id, (stamp.batch_id, stamp.at_ms));
                }
                Input::Contacts(batch) => {
                    self.require_single_source()?;
                    next.contacts(batch, &self.config, &self.resources, &mut events)?;
                }
                Input::SourceContacts { source_id, batch } => {
                    if source_id == 0
                        || !next
                            .actors
                            .values()
                            .any(|actor| actor.member.sample_source_id == source_id)
                        || batch.events.iter().any(|event| {
                            next.actors
                                .get(&event.actor_id)
                                .is_none_or(|actor| actor.member.sample_source_id != source_id)
                        })
                    {
                        return Err(invalid("contact source does not own attacker"));
                    }
                    next.source_contacts(
                        source_id,
                        batch,
                        &self.config,
                        &self.resources,
                        &mut events,
                    )?;
                }
                timed @ (Input::Timers | Input::Tick) => {
                    if next.training.is_none()
                        && now_ms >= deadline(self.config.started_at_ms, self.config.duration_ms)?
                    {
                        next.end(&self.config, EndReason::Timeout, &mut events);
                    } else {
                        next.timers(&mut events)?;
                        next.life_tick(&self.resources, &mut events)?;
                        if matches!(timed, Input::Tick)
                            && next
                                .training
                                .as_ref()
                                .is_none_or(|training| training.snapshot.state == 3)
                        {
                            next.decide(&self.resources, &mut events)?;
                            next.timers(&mut events)?;
                        }
                    }
                }
                Input::RequestPlayerSpawn => {
                    self.require_single_source()?;
                    next.request_human_spawn(1, &self.resources, &mut events)?;
                }
                Input::RequestHumanSpawn { actor_id } => {
                    self.require_single_source()?;
                    next.request_human_spawn(actor_id, &self.resources, &mut events)?;
                }
                Input::SourceSpawn {
                    source_id,
                    actor_id,
                } => {
                    next.validate_source(source_id, actor_id, MemberKind::Human)?;
                    next.request_human_spawn(actor_id, &self.resources, &mut events)?;
                }
                Input::SourceSquad {
                    source_id,
                    actor_id,
                    command,
                } => {
                    next.validate_source(source_id, actor_id, MemberKind::Human)?;
                    next.squad_command(actor_id, command, &mut events);
                }
                Input::SourceSquadSpawn {
                    source_id,
                    actor_id,
                    target_id,
                    position,
                } => {
                    next.validate_source(source_id, actor_id, MemberKind::Human)?;
                    next.request_squad_spawn(
                        actor_id,
                        target_id,
                        position,
                        &self.resources,
                        &mut events,
                    )?;
                }
                Input::Quit => next.end(&self.config, EndReason::Quit, &mut events),
            }
            next.training_score(&mut events);
        }
        next.append_state_output(&self.state, self.config.epoch, &mut events)?;
        let context = next.output_context();
        publish(&events, &context)?;
        self.state = next;
        Ok(())
    }

    fn require_single_source(&self) -> io::Result<()> {
        if self.single_source {
            Ok(())
        } else {
            Err(invalid("multi-source match requires sourced input"))
        }
    }
}

impl State {
    fn output_context(&self) -> OutputContext {
        let actor_poses = self
            .actors
            .keys()
            .filter_map(|&id| {
                let pose = self.fresh_pose(id)?;
                Some((
                    id,
                    SampledPose {
                        position: pose.position,
                        heading: pose.heading,
                        sampled_at_ms: self.actor_sampled_at_ms[&id],
                    },
                ))
            })
            .collect();
        OutputContext {
            at_ms: self.now_ms,
            actor_poses,
        }
    }

    fn actor_mut(&mut self, id: u32) -> io::Result<&mut Actor> {
        self.actors
            .get_mut(&id)
            .map(Arc::make_mut)
            .ok_or_else(|| invalid("unknown battle actor"))
    }
    fn fresh_world(&self) -> Option<&World> {
        self.world.as_deref().filter(|world| {
            self.now_ms.saturating_sub(world.sampled_at_ms) <= 250
                && world.actors.keys().any(|&id| self.fresh_pose(id).is_some())
        })
    }
    fn fresh_pose(&self, id: u32) -> Option<&Pose> {
        let sampled_at = *self.actor_sampled_at_ms.get(&id)?;
        (self.now_ms >= sampled_at && self.now_ms - sampled_at <= 250)
            .then(|| self.world.as_ref()?.actors.get(&id))
            .flatten()
    }
    fn validate_source(&self, source_id: u64, actor_id: u32, kind: MemberKind) -> io::Result<()> {
        if source_id == 0
            || self.actors.get(&actor_id).is_none_or(|actor| {
                actor.member.sample_source_id != source_id || actor.member.kind != kind
            })
        {
            return Err(invalid("source does not own actor input"));
        }
        Ok(())
    }
    fn observe_motion(&mut self, attempt: ai::MotionAttempt) -> io::Result<()> {
        let pose = self
            .world
            .as_ref()
            .and_then(|world| world.actors.get(&attempt.actor_id))
            .ok_or_else(|| invalid("AI motion actor absent from sampled world"))?;
        if self.actor_sampled_at_ms.get(&attempt.actor_id) != Some(&attempt.sampled_at_ms)
            || attempt.sampled_at_ms > self.now_ms
            || pose.entity != attempt.entity
            || pose.position != attempt.position
            || pose.heading != attempt.heading
            || self.actors[&attempt.actor_id].member.kind != MemberKind::Bot
        {
            return Err(invalid("AI motion differs from sampled world"));
        }
        // World and per-bot motion are submitted separately and may age in transit.
        // Matching but expired evidence must not drive AI or fail the battle.
        if self.now_ms - attempt.sampled_at_ms > 250 {
            return Ok(());
        }
        // Native sampling continues while death/respawn output is in flight.
        // Validate the evidence, but do not accumulate an inactive bot's motion.
        if self.actors[&attempt.actor_id].life != Life::Alive {
            return Ok(());
        }
        self.ai
            .observe_motion(self.now_ms, attempt)
            .map_err(invalid)
    }
    fn observe(
        &mut self,
        world: Arc<World>,
        config: &Config,
        events: &mut Vec<Output>,
    ) -> io::Result<()> {
        if config
            .members
            .iter()
            .map(|member| member.sample_source_id)
            .collect::<BTreeSet<_>>()
            .len()
            != 1
            || !self.source_cursors.is_empty()
            || world.epoch != config.epoch
            || world.sampled_at_ms > self.now_ms
            || world.actors.len() > self.actors.len()
            || self
                .world
                .as_ref()
                .is_some_and(|old| old.sampled_at_ms > world.sampled_at_ms)
            || world
                .actors
                .iter()
                .any(|(id, p)| !self.actors.contains_key(id) || !valid_pose(p))
        {
            return Err(invalid("invalid sampled world"));
        }
        self.actor_sampled_at_ms = world
            .actors
            .keys()
            .map(|&id| (id, world.sampled_at_ms))
            .collect();
        self.world = Some(world);
        self.legacy_world_used = true;
        self.confirm_spawns(events)?;
        Ok(())
    }

    fn observe_source(
        &mut self,
        batch: Arc<SourceWorld>,
        config: &Config,
        events: &mut Vec<Output>,
    ) -> io::Result<()> {
        if batch.epoch != config.epoch
            || self.legacy_world_used
            || batch.source_id == 0
            || batch.batch_id == 0
            || batch.sampled_at_ms < config.started_at_ms
            || batch.sampled_at_ms > self.now_ms
            || batch.actors.len() > self.actors.len()
            || batch.actors.iter().any(|(&id, pose)| {
                self.actors
                    .get(&id)
                    .is_none_or(|actor| actor.member.sample_source_id != batch.source_id)
                    || !valid_pose(pose)
            })
            || !self
                .actors
                .values()
                .any(|actor| actor.member.sample_source_id == batch.source_id)
        {
            return Err(invalid("invalid source world"));
        }
        if let Some(&(previous_id, previous_at)) = self.source_cursors.get(&batch.source_id) {
            if batch.batch_id == previous_id {
                return Ok(());
            }
            if batch.batch_id < previous_id || batch.sampled_at_ms < previous_at {
                return Err(invalid("regressing source world"));
            }
        }
        let mut merged = self
            .world
            .as_ref()
            .map_or_else(BTreeMap::new, |world| world.actors.clone());
        for (&id, pose) in &batch.actors {
            merged.insert(id, pose.clone());
            self.actor_sampled_at_ms.insert(id, batch.sampled_at_ms);
        }
        let sampled_at_ms = self.world.as_ref().map_or(batch.sampled_at_ms, |old| {
            old.sampled_at_ms.max(batch.sampled_at_ms)
        });
        self.world = Some(Arc::new(World {
            epoch: config.epoch,
            sampled_at_ms,
            actors: merged,
        }));
        self.source_cursors
            .insert(batch.source_id, (batch.batch_id, batch.sampled_at_ms));
        self.confirm_spawns(events)
    }
}

fn valid_pose(pose: &Pose) -> bool {
    pose.entity != 0
        && valid_point(pose.position)
        && pose.heading.is_finite()
        && pose.heading.abs() <= 180.
}
