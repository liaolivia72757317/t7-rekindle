//! Bounded client pose replication while the original client owns local physics.
use serde::{Deserialize, Serialize};

use crate::{client_view::ActorId, protocol::ErrorCode};

pub const POSE_INTERVAL_MS: u64 = 50;
const MAX_SPEED: f32 = 40.0;
const BURST_DISTANCE: f32 = 2.0;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Pose {
    pub position: [f32; 3],
    pub heading: i16,
    pub forward_back: i8,
    pub left_right: i8,
    pub running: bool,
}

impl Pose {
    fn valid(&self) -> bool {
        self.position
            .iter()
            .all(|v| v.is_finite() && v.abs() < 100_000.0)
            && (-180..=180).contains(&self.heading)
            && (-1..=1).contains(&self.forward_back)
            && (-1..=1).contains(&self.left_right)
            && (!self.running || self.forward_back == -1)
    }
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PoseEvent {
    pub room_id: String,
    pub match_id: String,
    pub actor_id: ActorId,
    pub event_seq: u64,
    pub input_seq: u32,
    pub server_time_ms: u64,
    pub pose: Pose,
}

#[derive(Debug)]
struct ActorPose {
    id: ActorId,
    input_seq: u32,
    accepted: Option<(Pose, u64)>,
    distance_budget: f32,
    dirty: bool,
}

#[derive(Debug)]
pub(crate) struct Motion {
    room_id: String,
    match_id: String,
    event_seq: u64,
    next_publish: u64,
    actors: Vec<ActorPose>,
    pending: Vec<PoseEvent>,
}

impl Motion {
    pub fn new(room_id: String, match_id: String, actors: impl Iterator<Item = u32>) -> Self {
        Self {
            room_id,
            match_id,
            event_seq: 0,
            next_publish: 0,
            actors: actors
                .map(|id| ActorPose {
                    id: ActorId(id),
                    input_seq: 0,
                    accepted: None,
                    distance_budget: BURST_DISTANCE,
                    dirty: false,
                })
                .collect(),
            pending: Vec::new(),
        }
    }

    pub fn input(
        &mut self,
        actor_id: u32,
        input_seq: u32,
        pose: Pose,
        now: u64,
    ) -> Result<(), ErrorCode> {
        let actor = self
            .actors
            .iter_mut()
            .find(|a| a.id.0 == actor_id)
            .ok_or(ErrorCode::UnknownActor)?;
        if input_seq == 0 || input_seq <= actor.input_seq {
            return Err(ErrorCode::StaleInput);
        }
        if !pose.valid() {
            return Err(ErrorCode::InvalidPose);
        }
        let mut budget = actor.distance_budget;
        if let Some((previous, at)) = &actor.accepted {
            budget += now.saturating_sub(*at).min(5000) as f32 * MAX_SPEED / 1000.0;
            let distance = previous
                .position
                .iter()
                .zip(pose.position)
                .map(|(a, b)| (a - b).powi(2))
                .sum::<f32>()
                .sqrt();
            if distance > budget {
                return Err(ErrorCode::InvalidPose);
            }
            budget = (budget - distance).min(BURST_DISTANCE);
        }
        actor.distance_budget = budget;
        actor.input_seq = input_seq;
        actor.accepted = Some((pose, now));
        actor.dirty = true;
        Ok(())
    }

    pub fn advance(&mut self, now: u64) {
        if now < self.next_publish {
            return;
        }
        self.next_publish = now + POSE_INTERVAL_MS;
        for actor in &mut self.actors {
            if !actor.dirty {
                continue;
            }
            actor.dirty = false;
            let (pose, at) = actor.accepted.as_ref().expect("dirty pose is accepted");
            self.event_seq += 1;
            self.pending.push(PoseEvent {
                room_id: self.room_id.clone(),
                match_id: self.match_id.clone(),
                actor_id: actor.id,
                event_seq: self.event_seq,
                input_seq: actor.input_seq,
                server_time_ms: *at,
                pose: pose.clone(),
            });
        }
    }

    pub fn take_events(&mut self) -> Vec<PoseEvent> {
        std::mem::take(&mut self.pending)
    }

    pub(crate) fn latest(&self, id: u32, now: u64) -> Option<&Pose> {
        let (pose, at) = self
            .actors
            .iter()
            .find(|a| a.id.0 == id)?
            .accepted
            .as_ref()?;
        (now.saturating_sub(*at) <= 1000).then_some(pose)
    }

    pub(crate) fn reset(&mut self, id: u32) {
        let actor = self
            .actors
            .iter_mut()
            .find(|a| a.id.0 == id)
            .expect("known actor");
        actor.accepted = None;
        actor.distance_budget = BURST_DISTANCE;
        actor.dirty = false;
    }
}
