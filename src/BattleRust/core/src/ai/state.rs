use super::types::*;
use crate::action::Direction;
use crate::navigation::Point;
use std::collections::BTreeMap;

#[derive(Clone, Debug, PartialEq)]
pub struct BotState {
    pub config: BotConfig,
    pub target_id: Option<u32>,
    pub target_since_ms: Option<u64>,
    pub react_at_ms: Option<u64>,
    pub decision_seed: u32,
    pub rhythm_seed: u32,
    pub route_seed: u32,
    pub last_attack: Option<Direction>,
    pub attack_count: u64,
    pub(crate) observed: Option<Observed>,
    pub(crate) sense_at_ms: u64,
    pub(crate) last_seen: Option<Point>,
    pub(crate) search_target: Option<u32>,
    pub(crate) search_until_ms: u64,
    pub(crate) roam_goal: Option<Point>,
    pub(crate) roam_anchor: Point,
    pub(crate) roam_count: u64,
    pub(crate) roam_wait_until_ms: u64,
    pub(crate) pursuit: Option<Pursuit>,
    pub(crate) threats: BTreeMap<u32, Threat>,
    pub(crate) step_plan: Option<StepPlan>,
    pub(crate) tactic: Option<TacticPlan>,
    pub(crate) assist: Option<AssistPlan>,
    pub(crate) motion_probe: Option<MotionProbe>,
    pub(crate) stuck_heading: Option<f64>,
    pub(crate) recovery: Option<RecoveryPlan>,
    pub(crate) strategy_at_ms: u64,
    pub(crate) combat_at_ms: u64,
    pub(crate) movement: MovementIntent,
    pub(crate) status: &'static str,
}
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct Observed {
    pub action: ActionObservation,
    pub seen_at: u64,
    pub phase_seen_at: u64,
}
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct Pursuit {
    pub target: u32,
    pub close_at: u64,
    pub progress_at: u64,
    pub distance: f64,
}
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct Threat {
    pub damage: f64,
    pub at: u64,
}
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct StepPlan {
    pub target: u32,
    pub until: u64,
    pub gap: f64,
    pub side: i8,
    pub goal: Point,
    pub target_position: Point,
    pub mode: &'static str,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct TacticPlan {
    pub target: u32,
    pub until: u64,
    pub goal: Point,
    pub target_position: Point,
    pub mode: &'static str,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct AssistPlan {
    pub ally: u32,
    pub target: u32,
    pub until: u64,
    pub goal: Point,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct MotionProbe {
    pub position: Point,
    pub since: u64,
    pub sampled_at: u64,
    pub attempted_at: u64,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct RecoveryPlan {
    pub attempt: u32,
    pub turn: usize,
    pub heading: f64,
    pub goal: Option<Point>,
    pub until: u64,
}

impl BotState {
    pub(crate) fn new(config: BotConfig) -> Self {
        Self {
            decision_seed: config.decision_seed,
            rhythm_seed: config.rhythm_seed,
            route_seed: config.route_seed,
            roam_anchor: config.patrol_anchor,
            config,
            target_id: None,
            target_since_ms: None,
            react_at_ms: None,
            last_attack: None,
            attack_count: 0,
            observed: None,
            sense_at_ms: 0,
            last_seen: None,
            search_target: None,
            search_until_ms: 0,
            roam_goal: None,
            roam_count: 0,
            roam_wait_until_ms: 0,
            pursuit: None,
            threats: BTreeMap::new(),
            step_plan: None,
            tactic: None,
            assist: None,
            motion_probe: None,
            stuck_heading: None,
            recovery: None,
            strategy_at_ms: 0,
            combat_at_ms: 0,
            movement: MovementIntent::Stop,
            status: "waiting",
        }
    }
    pub(crate) fn observe(&mut self, action: ActionObservation, now: u64) {
        if self.observed.is_some_and(|old| {
            old.action.intent == action.intent
                && old.action.index() == action.index()
                && old.action.sequence == action.sequence
                && old.action.phase == action.phase
        }) {
            return;
        }
        let continues = self.observed.filter(|old| {
            old.action.intent == action.intent
                && old.action.index() >= 0
                && old.action.index() < action.index()
                && old.action.sequence != action.sequence
                && !action.reaction()
        });
        self.observed = Some(Observed {
            action,
            seen_at: continues.map_or(now, |old| old.seen_at),
            phase_seen_at: now,
        });
    }
    pub(crate) fn rhythm(&mut self, min: u64, max: u64) -> u64 {
        self.rhythm_seed = self
            .rhythm_seed
            .wrapping_mul(1664525)
            .wrapping_add(1013904223);
        min + u64::from(self.rhythm_seed >> 8) % (max - min + 1)
    }
    pub(crate) fn vary(&mut self, duration: u64, low: f64, high: f64) -> u64 {
        self.rhythm(
            (duration as f64 * low).round_ties_even() as u64,
            (duration as f64 * high).round_ties_even() as u64,
        )
    }
    pub(crate) fn random_step(&mut self) -> u32 {
        self.route_seed = self
            .route_seed
            .wrapping_mul(1664525)
            .wrapping_add(1013904223);
        self.route_seed
    }
    pub(crate) fn remember(&mut self, target: u32, position: Point, now: u64) {
        self.search_target = Some(target);
        self.last_seen = Some(position);
        self.search_until_ms = now + 6000;
        self.roam_anchor = position;
        self.roam_count = self.roam_count.max(1);
        self.roam_goal = None;
        self.roam_wait_until_ms = 0;
    }
    pub(crate) fn forget_target(&mut self) {
        self.target_id = None;
        self.target_since_ms = None;
        self.observed = None;
        self.react_at_ms = None;
        self.pursuit = None;
        self.step_plan = None;
        self.tactic = None;
        self.assist = None;
        self.sense_at_ms = 0;
    }
    pub(crate) fn pursuit_limits(&self) -> (f64, u64, u64) {
        if self.config.profile == Profile::Berserker {
            (10.0, 3000, 6000)
        } else {
            (6.0, 2000, 4000)
        }
    }
    pub(crate) fn pursuit_active(&self, target: u32, now: u64) -> bool {
        let (_, idle, max) = self.pursuit_limits();
        self.pursuit.is_some_and(|p| {
            p.target == target && now < p.close_at + max && now < p.progress_at + idle
        })
    }
    pub(crate) fn track(&mut self, target: u32, distance: f64, now: u64) {
        if distance <= 2.2 {
            self.pursuit = Some(Pursuit {
                target,
                close_at: now,
                progress_at: now,
                distance,
            });
            return;
        }
        if distance > self.pursuit_limits().0 || !self.pursuit_active(target, now) {
            self.pursuit = None;
            return;
        }
        if let Some(p) = &mut self.pursuit {
            let closing = p.distance - distance >= 0.4;
            if closing || distance > p.distance {
                p.distance = distance;
                if closing {
                    p.progress_at = now;
                }
            }
        }
    }
    pub(crate) fn threat(&self, id: u32, now: u64) -> f64 {
        self.threats
            .get(&id)
            .filter(|t| now >= t.at && now - t.at < 5000)
            .map_or(0.0, |t| {
                (t.damage - 5.0 * (now - t.at) as f64 / 1000.0).max(0.0)
            })
    }
}
