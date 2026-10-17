//! Sample-driven bot decisions. Outputs are controller intents, never simulated positions.
mod assist;
mod clearance;
mod decision;
mod footwork;
mod math;
mod perception;
mod roaming;
mod state;
mod tactics;
mod types;
mod unstuck;

pub use decision::{DecisionContext, choose_decision};
pub use perception::visible;
pub use state::BotState;
pub use types::*;

use crate::action::Intent;
use crate::navigation::{NavMesh, PathStatus};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Debug, PartialEq)]
pub struct Controller {
    instance_started_at_ms: u64,
    last_tick_ms: u64,
    bots: BTreeMap<u32, BotState>,
}

impl Controller {
    pub fn new(instance_started_at_ms: u64, bots: Vec<BotConfig>) -> Result<Self, AiError> {
        if bots.len() >= crate::MAX_BATTLE_ACTORS {
            return Err(AiError("AI roster exceeds actor capacity"));
        }
        let mut states = BTreeMap::new();
        for config in bots {
            if !(2..=crate::MAX_BATTLE_ACTORS as u32).contains(&config.actor_id)
                || !(1..=2).contains(&config.camp)
                || !(85..=115).contains(&config.tempo)
                || !(-1..=1).contains(&config.lane)
                || !valid_point(config.patrol_anchor)
            {
                return Err(AiError("invalid AI roster configuration"));
            }
            if states
                .insert(config.actor_id, BotState::new(config))
                .is_some()
            {
                return Err(AiError("duplicate AI actor"));
            }
        }
        Ok(Self {
            instance_started_at_ms,
            last_tick_ms: instance_started_at_ms,
            bots: states,
        })
    }

    pub fn state(&self, actor_id: u32) -> Option<&BotState> {
        self.bots.get(&actor_id)
    }

    /// Record only a native-confirmed attempt to translate, not a requested route or turn.
    pub fn observe_motion(&mut self, now_ms: u64, attempt: MotionAttempt) -> Result<(), AiError> {
        if now_ms < self.last_tick_ms
            || attempt.entity == 0
            || attempt.sampled_at_ms < self.instance_started_at_ms
            || attempt.sampled_at_ms > now_ms
            || now_ms - attempt.sampled_at_ms > 250
            || !valid_point(attempt.position)
            || !attempt.heading.is_finite()
            || !(-180. ..=180.).contains(&attempt.heading)
        {
            return Err(AiError("invalid AI motion evidence"));
        }
        let state = self
            .bots
            .get_mut(&attempt.actor_id)
            .ok_or(AiError("unknown AI motion actor"))?;
        unstuck::observe(state, attempt, now_ms);
        Ok(())
    }

    pub fn reset_actor(&mut self, actor_id: u32) -> Result<(), AiError> {
        let old = self
            .bots
            .get(&actor_id)
            .ok_or(AiError("unknown AI actor"))?;
        let mut fresh = BotState::new(old.config.clone());
        fresh.decision_seed = old.decision_seed;
        fresh.rhythm_seed = old.rhythm_seed;
        fresh.route_seed = old.route_seed;
        fresh.last_attack = old.last_attack;
        fresh.attack_count = old.attack_count;
        self.bots.insert(actor_id, fresh);
        Ok(())
    }

    pub fn notice_damage(
        &mut self,
        actor_id: u32,
        attacker_id: u32,
        damage: f64,
        now_ms: u64,
        world: &WorldObservation,
    ) -> Result<(), AiError> {
        if !damage.is_finite() || damage < 0.0 || now_ms < self.last_tick_ms {
            return Err(AiError("invalid AI damage observation"));
        }
        let source =
            perception::actor(world, actor_id).ok_or(AiError("AI damage target absent"))?;
        let attacker =
            perception::actor(world, attacker_id).ok_or(AiError("AI damage source absent"))?;
        let state = self
            .bots
            .get_mut(&actor_id)
            .ok_or(AiError("unknown AI actor"))?;
        if damage == 0.0 || source.camp == attacker.camp {
            return Ok(());
        }
        let amount = (state.threat(attacker_id, now_ms) + damage).min(80.0);
        state.threats.insert(
            attacker_id,
            state::Threat {
                damage: amount,
                at: now_ms,
            },
        );
        state.sense_at_ms = 0;
        Ok(())
    }

    /// The runtime validates session epoch/generation before supplying a world observation.
    pub fn tick(
        &mut self,
        now_ms: u64,
        world: &WorldObservation,
        nav: &NavMesh,
        timings: &BTreeMap<u32, AttackTimings>,
    ) -> Result<Vec<BotOutput>, AiError> {
        if now_ms < self.last_tick_ms
            || now_ms > u64::MAX - 10000
            || world.actors.len() > crate::MAX_BATTLE_ACTORS
            || self.bots.keys().any(|id| {
                timings
                    .get(id)
                    .is_none_or(|t| t.windup_ms.iter().any(|&t| t > 65535))
            })
        {
            return Err(AiError("invalid AI tick clock or resources"));
        }
        let mut ids = BTreeSet::new();
        for actor in &world.actors {
            if !(1..=crate::MAX_BATTLE_ACTORS as u32).contains(&actor.id)
                || !(1..=2).contains(&actor.camp)
                || !ids.insert(actor.id)
                || !valid_point(actor.position)
                || !actor.preferred_distance.is_finite()
                || !(0.8..=1.8).contains(&actor.preferred_distance)
                || !actor.heading.is_finite()
                || !(-180.0..=180.0).contains(&actor.heading)
            {
                return Err(AiError("invalid AI actor observation"));
            }
            if self
                .bots
                .get(&actor.id)
                .is_some_and(|state| state.config.camp != actor.camp)
            {
                return Err(AiError("AI actor camp changed within match"));
            }
        }
        let mut pending = self.clone();
        let ids: Vec<_> = pending.bots.keys().copied().collect();
        let mut outputs = Vec::with_capacity(ids.len());
        let fresh = world.sampled_at_ms >= self.instance_started_at_ms
            && world.sampled_at_ms <= now_ms
            && now_ms - world.sampled_at_ms <= 250;
        for id in ids {
            let mut state = pending.bots.remove(&id).expect("configured AI actor");
            let source = perception::actor(world, id).filter(|a| a.alive && fresh);
            let action = if let Some(source) = source {
                tick_bot(
                    &mut state,
                    &pending.bots,
                    source,
                    world,
                    nav,
                    &timings[&id],
                    now_ms,
                )?
            } else {
                state.forget_target();
                state.motion_probe = None;
                state.stuck_heading = None;
                state.recovery = None;
                state.movement = MovementIntent::Stop;
                state.status = if fresh && perception::actor(world, id).is_some_and(|a| !a.alive) {
                    "inactive"
                } else {
                    "world-unavailable"
                };
                None
            };
            outputs.push(BotOutput {
                actor_id: id,
                target_id: state.target_id,
                status: state.status,
                action,
                movement: state.movement.clone(),
            });
            pending.bots.insert(id, state);
        }
        pending.last_tick_ms = now_ms;
        *self = pending;
        Ok(outputs)
    }
}

fn tick_bot(
    state: &mut BotState,
    states: &BTreeMap<u32, BotState>,
    source: &ActorSnapshot,
    world: &WorldObservation,
    nav: &NavMesh,
    timings: &AttackTimings,
    now: u64,
) -> Result<Option<ActionIntent>, AiError> {
    if nav.project(source.position).is_none() {
        state.motion_probe = None;
        state.stuck_heading = None;
        state.recovery = None;
        state.movement = MovementIntent::Stop;
        state.status = "outside-mesh";
        state.react_at_ms = None;
        return Ok(None);
    }
    if let Some((movement, status)) = unstuck::plan(state, source, world, nav, now) {
        state.movement = movement;
        state.status = status;
        return Ok(None);
    }
    if now >= state.strategy_at_ms {
        state.strategy_at_ms = now + 500;
        let target_id = perception::sense(state, states, source, world, nav, now);
        if let Some(target) = target_id.and_then(|id| perception::actor(world, id)) {
            state.assist = None;
            state.observe(target.action, now);
            if state.react_at_ms.is_none() {
                state.react_at_ms = Some(
                    now + state.vary(
                        state.config.profile.settings(state.config.tempo).attack_ms,
                        0.65,
                        1.2,
                    ),
                );
            }
            let footwork = footwork::plan(state, source, target, world, nav, now);
            let engaging = footwork.is_some()
                || (math::distance(source.position, target.position) <= 2.2
                    && decision::clear_contact(source, target));
            if engaging {
                state.tactic = None;
            }
            let tactic = if engaging || source.action.reaction() {
                None
            } else {
                tactics::plan(state, states, source, target, world, nav, now)
            };
            if let Some((route, mode)) = footwork.or(tactic) {
                state.movement = MovementIntent::Follow {
                    points: route.points,
                    look_at: Some(target.position),
                };
                state.status = mode;
            } else {
                let route = nav
                    .find_path(source.position, target.position, 0.35)
                    .map_err(|_| AiError("invalid AI navigation query"))?;
                if route.status != PathStatus::Ready {
                    state.movement = MovementIntent::Stop;
                    state.status = route.status.as_str();
                } else if math::distance(source.position, target.position) <= 2.0
                    && math::length(&route.points, false) <= 2.0
                {
                    state.movement = MovementIntent::Turn {
                        target: target.position,
                    };
                    state.status = "ready";
                } else {
                    state.movement = MovementIntent::Follow {
                        points: route.points,
                        look_at: Some(target.position),
                    };
                    state.status = "approach";
                }
            }
        } else {
            state.react_at_ms = None;
            state.observed = None;
            state.step_plan = None;
            state.tactic = None;
            if let Some((route, look_at)) = assist::plan(state, states, source, world, nav, now) {
                state.movement = if math::horizontal(
                    source.position,
                    *route.points.last().expect("assist route"),
                ) <= 0.25
                {
                    MovementIntent::Turn { target: look_at }
                } else {
                    MovementIntent::Follow {
                        points: route.points,
                        look_at: Some(look_at),
                    }
                };
                state.status = "assist";
                return Ok(None);
            }
            let (goal, mode) = roaming::goal(state, nav, source.position, now);
            state.status = mode;
            state.movement = if let Some(goal) = goal {
                let route = nav
                    .find_path(source.position, goal, 0.35)
                    .map_err(|_| AiError("invalid AI patrol query"))?;
                if route.status == PathStatus::Ready {
                    MovementIntent::Follow {
                        points: route.points,
                        look_at: None,
                    }
                } else {
                    state.status = route.status.as_str();
                    MovementIntent::Stop
                }
            } else {
                MovementIntent::Stop
            };
        }
    }
    if now < state.combat_at_ms {
        return Ok(None);
    }
    state.combat_at_ms = now + 100;
    let Some(target) = state
        .target_id
        .and_then(|id| perception::actor(world, id))
        .filter(|a| a.alive && a.camp != source.camp)
    else {
        state.forget_target();
        return Ok(None);
    };
    state.observe(target.action, now);
    if math::distance(source.position, target.position) > 2.0 {
        return Ok(None);
    }
    if state.react_at_ms.is_none() {
        state.react_at_ms = Some(
            now + state.vary(
                state.config.profile.settings(state.config.tempo).attack_ms,
                0.65,
                1.2,
            ),
        );
    }
    let observed = state.observed.expect("observed target");
    let choice = choose_decision(
        &DecisionContext {
            now_ms: now,
            profile: state.config.profile,
            tempo: state.config.tempo,
            seed: state.decision_seed,
            last_attack: state.last_attack,
            react_at_ms: state.react_at_ms,
            observed_since_ms: observed.seen_at,
            phase_observed_since_ms: observed.phase_seen_at,
        },
        source,
        target,
        timings,
    )?;
    if choice.kind == DecisionKind::Wait {
        if !matches!(choice.reason, "ready" | "contact-unavailable")
            || !matches!(state.movement, MovementIntent::Follow { .. })
        {
            state.status = choice.reason;
        }
        return Ok(None);
    }
    let route = nav
        .find_path(source.position, target.position, 0.35)
        .map_err(|_| AiError("invalid AI melee query"))?;
    if route.status != PathStatus::Ready || math::length(&route.points, false) > 2.0 {
        return Ok(None);
    }
    let intent = choice.intent.expect("action decision");
    let guard = choice.kind == DecisionKind::Parry;
    let release_after_ms = guard.then(|| {
        state.vary(
            state.config.profile.settings(state.config.tempo).guard_ms,
            0.8,
            1.2,
        )
    });
    if let Intent::Attack(direction) = intent {
        state.attack_count = state
            .attack_count
            .checked_add(1)
            .ok_or(AiError("AI attack count exhausted"))?;
        state.last_attack = Some(direction);
        state.decision_seed = choice.seed;
    }
    state.react_at_ms = None;
    state.status = choice.reason;
    Ok(Some(ActionIntent {
        intent,
        released: !guard,
        release_after_ms,
    }))
}
