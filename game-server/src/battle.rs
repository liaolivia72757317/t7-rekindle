//! Server-owned melee phases. Hit validation and damage are separate operations.
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;

use crate::{client_view::ActorId, protocol::ErrorCode};

const CHAIN_RECOVERY_MS: u64 = 100;

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub enum ActionIntent {
    AttackUp,
    AttackRight,
    AttackDown,
    AttackLeft,
    BlockUp,
    BlockRight,
    BlockDown,
    BlockLeft,
    AttackRelease,
    BlockRelease,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ActionEvent {
    pub room_id: String,
    pub match_id: String,
    pub actor_id: ActorId,
    pub event_seq: u64,
    pub action_seq: u16,
    pub state: u16,
    pub server_time_ms: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub target_actor_id: Option<ActorId>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Gesture {
    Attack(usize),
    Block(usize),
}

impl Gesture {
    fn from_intent(intent: ActionIntent) -> Option<Self> {
        use ActionIntent::*;
        Some(match intent {
            AttackUp => Self::Attack(0),
            AttackRight => Self::Attack(1),
            AttackDown => Self::Attack(2),
            AttackLeft => Self::Attack(3),
            BlockUp => Self::Block(0),
            BlockRight => Self::Block(1),
            BlockDown => Self::Block(2),
            BlockLeft => Self::Block(3),
            AttackRelease | BlockRelease => return None,
        })
    }

    fn states(self) -> [u16; 4] {
        match self {
            Self::Attack(direction) => [
                [235, 237, 238, 239],
                [241, 243, 244, 245],
                [247, 249, 250, 251],
                [253, 255, 256, 257],
            ][direction],
            Self::Block(direction) => [
                [305, 223, 227, 2],
                [308, 224, 228, 2],
                [307, 225, 229, 2],
                [306, 226, 230, 2],
            ][direction],
        }
    }

    fn duration(self, phase: usize) -> u64 {
        match self {
            Self::Attack(direction) => [
                [360, 0, 640, 500],
                [360, 0, 640, 500],
                [400, 0, 700, 400],
                [360, 0, 640, 530],
            ][direction][phase],
            Self::Block(_) => 0,
        }
    }

    fn released_by(self, intent: ActionIntent) -> bool {
        matches!(
            (self, intent),
            (Self::Attack(_), ActionIntent::AttackRelease)
                | (Self::Block(_), ActionIntent::BlockRelease)
        )
    }
}

#[derive(Debug)]
enum ActionPhase {
    Preparing { until: u64 },
    Held,
    Releasing { until: u64 },
    Recovering { until: u64, chain_at: u64 },
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct ActionSample {
    pub state: u16,
    pub seq: u16,
    pub at: u64,
    pub ended_at: Option<u64>,
}

#[derive(Debug)]
struct ActiveAction {
    gesture: Gesture,
    phase: ActionPhase,
    released: bool,
    buffered: Option<(Gesture, bool)>,
}

#[derive(Debug)]
struct Actor {
    id: ActorId,
    input_seq: u32,
    action_seq: u16,
    active: Option<ActiveAction>,
    history: VecDeque<ActionSample>,
    reaction_until: Option<u64>,
    disabled: bool,
}

#[derive(Debug)]
pub(crate) struct Battle {
    room_id: String,
    match_id: String,
    event_seq: u64,
    actors: Vec<Actor>,
    pending: Vec<ActionEvent>,
}

impl Battle {
    pub fn new(room_id: String, match_id: String, actors: impl Iterator<Item = u32>) -> Self {
        Self {
            room_id,
            match_id,
            event_seq: 0,
            actors: actors
                .map(|id| Actor {
                    id: ActorId(id),
                    input_seq: 0,
                    action_seq: 1,
                    active: None,
                    history: VecDeque::new(),
                    reaction_until: None,
                    disabled: false,
                })
                .collect(),
            pending: Vec::new(),
        }
    }

    pub fn input(
        &mut self,
        actor_id: u32,
        input_seq: u32,
        intent: ActionIntent,
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
        actor.input_seq = input_seq;
        if actor.disabled || actor.reaction_until.is_some() {
            if intent == ActionIntent::BlockRelease {
                actor.active = None;
            }
            return Ok(());
        }
        let mut states = Vec::new();
        actor.input(intent, now, &mut states);
        self.publish(actor_id, states);
        Ok(())
    }

    pub fn advance(&mut self, now: u64) {
        for index in 0..self.actors.len() {
            let actor = &mut self.actors[index];
            let actor_id = actor.id.0;
            let mut states = Vec::new();
            if actor.reaction_until.is_some_and(|until| now >= until) {
                actor.reaction_until = None;
                let state = actor.active.as_ref().map_or(2, |a| a.gesture.states()[1]);
                actor.emit(state, now, &mut states);
            } else if actor.reaction_until.is_none() && !actor.disabled {
                actor.advance(now, &mut states);
            }
            self.publish(actor_id, states);
        }
    }

    fn publish(&mut self, actor_id: u32, states: Vec<(u16, u16, u64)>) {
        for (state, action_seq, server_time_ms) in states {
            let actor = self
                .actors
                .iter_mut()
                .find(|a| a.id.0 == actor_id)
                .expect("known actor");
            if let Some(previous) = actor.history.back_mut() {
                previous.ended_at = Some(server_time_ms);
            }
            actor.history.push_back(ActionSample {
                state,
                seq: action_seq,
                at: server_time_ms,
                ended_at: None,
            });
            while actor.history.len() > 32 {
                actor.history.pop_front();
            }
            self.event_seq += 1;
            self.pending.push(ActionEvent {
                room_id: self.room_id.clone(),
                match_id: self.match_id.clone(),
                actor_id: ActorId(actor_id),
                event_seq: self.event_seq,
                action_seq,
                state,
                server_time_ms,
                target_actor_id: None,
            });
        }
    }

    pub fn take_events(&mut self) -> Vec<ActionEvent> {
        std::mem::take(&mut self.pending)
    }

    pub(crate) fn sample(&self, actor_id: u32, seq: u16) -> Option<ActionSample> {
        self.actors
            .iter()
            .find(|a| a.id.0 == actor_id)?
            .history
            .iter()
            .rev()
            .find(|s| s.seq == seq)
            .copied()
    }

    pub(crate) fn current(&self, actor_id: u32) -> Option<ActionSample> {
        self.actors
            .iter()
            .find(|a| a.id.0 == actor_id)?
            .history
            .back()
            .copied()
    }

    pub(crate) fn react(
        &mut self,
        actor_id: u32,
        state: u16,
        now: u64,
        duration: u64,
        disabled: bool,
        target: Option<u32>,
    ) {
        let actor = self
            .actors
            .iter_mut()
            .find(|a| a.id.0 == actor_id)
            .expect("known actor");
        actor.active = [231, 232, 233, 234]
            .iter()
            .position(|n| *n == state)
            .map(|direction| ActiveAction {
                gesture: Gesture::Block(direction),
                phase: ActionPhase::Held,
                released: false,
                buffered: None,
            });
        actor.disabled = disabled;
        actor.reaction_until = (!disabled && duration > 0).then_some(now + duration);
        let mut output = Vec::new();
        actor.emit(state, now, &mut output);
        self.publish(actor_id, output);
        self.pending
            .last_mut()
            .expect("reaction event")
            .target_actor_id = target.map(ActorId);
    }
}

impl Actor {
    fn emit(&mut self, state: u16, now: u64, output: &mut Vec<(u16, u16, u64)>) {
        self.action_seq = self.action_seq % u16::MAX + 1;
        output.push((state, self.action_seq, now));
    }

    fn start(&mut self, gesture: Gesture, now: u64, output: &mut Vec<(u16, u16, u64)>) {
        self.active = Some(ActiveAction {
            gesture,
            phase: ActionPhase::Preparing {
                until: now + gesture.duration(0),
            },
            released: false,
            buffered: None,
        });
        self.emit(gesture.states()[0], now, output);
    }

    fn input(&mut self, intent: ActionIntent, now: u64, output: &mut Vec<(u16, u16, u64)>) {
        let gesture = Gesture::from_intent(intent);
        let Some(active) = self.active.as_mut() else {
            if let Some(gesture) = gesture {
                self.start(gesture, now, output);
            }
            return;
        };
        if let Some(gesture) = gesture {
            let chain_window = matches!(active.phase, ActionPhase::Recovering { chain_at, .. }
                if now >= chain_at || matches!(gesture, Gesture::Block(_)));
            if chain_window {
                self.start(gesture, now, output);
                return;
            }
            let preparing = matches!(
                active.phase,
                ActionPhase::Preparing { .. } | ActionPhase::Held
            );
            if preparing
                && std::mem::discriminant(&gesture) != std::mem::discriminant(&active.gesture)
            {
                self.start(gesture, now, output);
            } else if matches!(active.gesture, Gesture::Attack(_))
                || matches!(
                    active.phase,
                    ActionPhase::Releasing { .. } | ActionPhase::Recovering { .. }
                )
            {
                active.buffered = Some((gesture, false));
            }
            return;
        }
        if let Some((gesture, released)) = active.buffered.as_mut()
            && gesture.released_by(intent)
        {
            *released = true;
        }
        if active.released || !active.gesture.released_by(intent) {
            return;
        }
        if matches!(active.gesture, Gesture::Block(_)) {
            let buffered = active.buffered;
            self.active = None;
            self.finish(buffered, now, output);
            return;
        }
        active.released = true;
        if matches!(active.phase, ActionPhase::Held) {
            let gesture = active.gesture;
            active.phase = ActionPhase::Releasing {
                until: now + gesture.duration(2),
            };
            self.emit(gesture.states()[2], now, output);
        }
    }

    fn advance(&mut self, now: u64, output: &mut Vec<(u16, u16, u64)>) {
        // At most one phase per tick: a delayed tick never bursts an entire animation.
        let Some(mut active) = self.active.take() else {
            return;
        };
        let gesture = active.gesture;
        let state = match active.phase {
            ActionPhase::Preparing { until } if now >= until => {
                if active.released {
                    if matches!(gesture, Gesture::Block(_)) {
                        self.finish(active.buffered, now, output);
                        return;
                    }
                    active.phase = ActionPhase::Releasing {
                        until: now + gesture.duration(2),
                    };
                    gesture.states()[2]
                } else {
                    active.phase = ActionPhase::Held;
                    gesture.states()[1]
                }
            }
            ActionPhase::Releasing { until } if now >= until => {
                if matches!(gesture, Gesture::Block(_))
                    || active
                        .buffered
                        .is_some_and(|(g, _)| matches!(g, Gesture::Block(_)))
                {
                    self.finish(active.buffered, now, output);
                    return;
                }
                active.phase = ActionPhase::Recovering {
                    until: now + gesture.duration(3),
                    chain_at: now + CHAIN_RECOVERY_MS,
                };
                gesture.states()[3]
            }
            ActionPhase::Recovering { chain_at, .. }
                if active
                    .buffered
                    .is_some_and(|(g, _)| matches!(g, Gesture::Block(_)) || now >= chain_at) =>
            {
                self.finish(active.buffered, now, output);
                return;
            }
            ActionPhase::Recovering { until, .. } if now >= until => {
                self.finish(active.buffered, now, output);
                return;
            }
            _ => {
                self.active = Some(active);
                return;
            }
        };
        self.active = Some(active);
        self.emit(state, now, output);
    }

    fn finish(
        &mut self,
        buffered: Option<(Gesture, bool)>,
        now: u64,
        output: &mut Vec<(u16, u16, u64)>,
    ) {
        if let Some((gesture, released)) = buffered {
            self.start(gesture, now, output);
            self.active.as_mut().expect("started action").released = released;
        } else {
            self.emit(2, now, output);
        }
    }
}
