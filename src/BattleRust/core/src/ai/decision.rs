use super::{math, types::*};
use crate::action::{Direction, Intent, Phase};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct DecisionContext {
    pub now_ms: u64,
    pub profile: Profile,
    pub tempo: u16,
    pub seed: u32,
    pub last_attack: Option<Direction>,
    pub react_at_ms: Option<u64>,
    pub observed_since_ms: u64,
    pub phase_observed_since_ms: u64,
}

fn wait(reason: &'static str) -> Decision {
    Decision {
        kind: DecisionKind::Wait,
        reason,
        intent: None,
        seed: 0,
    }
}

pub(crate) fn clear_contact(source: &ActorSnapshot, target: &ActorSnapshot) -> bool {
    source.contact.clear_for(target.id)
        && math::horizontal(source.position, target.position) <= 2.2
        && (source.position[2] - target.position[2]).abs() <= 1.0
        && math::facing(source.position, target.position, source.heading)
}

pub fn choose_decision(
    context: &DecisionContext,
    source: &ActorSnapshot,
    target: &ActorSnapshot,
    timings: &AttackTimings,
) -> Result<Decision, AiError> {
    if !(85..=115).contains(&context.tempo)
        || context.observed_since_ms > context.now_ms
        || context.phase_observed_since_ms < context.observed_since_ms
        || context.phase_observed_since_ms > context.now_ms
        || !valid_point(source.position)
        || !valid_point(target.position)
        || !source.heading.is_finite()
        || !target.heading.is_finite()
    {
        return Err(AiError("invalid AI decision observation"));
    }
    let action = target.action;
    let settings = context.profile.settings(context.tempo);
    let observed = context.now_ms - context.observed_since_ms;
    let phase_observed = context.now_ms - context.phase_observed_since_ms;
    if source.hurt || source.action.locked() {
        return Ok(wait("action-locked"));
    }
    if context.profile == Profile::Aggressive
        && context.react_at_ms.is_some_and(|at| context.now_ms >= at)
        && matches!(action.intent, Some(Intent::Attack(_)))
        && action.phase == Phase::Hold
        && !action.reaction()
        && observed >= settings.observation_ms
        && clear_contact(source, target)
    {
        return Ok(attack(context, source, target, timings, "pressure"));
    }
    if context.profile != Profile::Berserker {
        let parry = match action.intent {
            Some(Intent::Attack(Direction::Up)) => Some(Direction::Up),
            Some(Intent::Attack(Direction::Right)) => Some(Direction::Left),
            Some(Intent::Attack(Direction::Down)) | Some(Intent::Dash) => Some(Direction::Down),
            Some(Intent::Attack(Direction::Left)) => Some(Direction::Right),
            _ => None,
        };
        let strike = if action.intent == Some(Intent::Dash) {
            1
        } else {
            2
        };
        if let Some(direction) = parry.filter(|_| {
            action.index() >= 0
                && action.index() <= strike
                && !action.reaction()
                && clear_contact(target, source)
                && math::facing(source.position, target.position, source.heading)
        }) {
            if observed < settings.observation_ms {
                return Ok(wait("observe-threat"));
            }
            if action.index() == strike
                && action.phase_due_ms.is_none_or(|due| due <= context.now_ms)
            {
                return Ok(wait("late-threat"));
            }
            return Ok(Decision {
                kind: DecisionKind::Parry,
                reason: "parry",
                intent: Some(if source.shield_equipped {
                    Intent::Shield
                } else {
                    Intent::Guard(direction)
                }),
                seed: 0,
            });
        }
    }
    let recovering = (matches!(action.intent, Some(Intent::Attack(_)))
        && action.phase == Phase::Recovery)
        || (action.intent == Some(Intent::Dash) && action.phase == Phase::Strike)
        || matches!(action.phase, Phase::Parried | Phase::Terrain);
    if recovering
        && phase_observed >= settings.counter_ms
        && action
            .phase_due_ms
            .is_some_and(|due| due >= context.now_ms && due - context.now_ms >= 100)
        && clear_contact(source, target)
    {
        return Ok(attack(context, source, target, timings, "counterattack"));
    }
    if context.react_at_ms.is_none_or(|at| context.now_ms < at) {
        return Ok(wait("ready"));
    }
    if !clear_contact(source, target) {
        return Ok(wait("contact-unavailable"));
    }
    Ok(attack(context, source, target, timings, "attack"))
}

fn attack(
    context: &DecisionContext,
    source: &ActorSnapshot,
    target: &ActorSnapshot,
    timings: &AttackTimings,
    reason: &'static str,
) -> Decision {
    let action = target.action;
    let blocked = if context.now_ms - context.observed_since_ms
        >= context.profile.settings(context.tempo).observation_ms
        && matches!(action.phase, Phase::Prepare | Phase::Hold)
        && !action.released
        && math::facing(target.position, source.position, target.heading)
    {
        action.intent
    } else {
        None
    };
    let dx = target.position[0] - source.position[0];
    let dy = target.position[1] - source.position[1];
    let angle = ((-dy).atan2(dx).to_degrees() - source.heading + 180.0).rem_euclid(360.0) - 180.0;
    let height = target.position[2] - source.position[2];
    let mut seed = context.seed;
    let mut best = None;
    for (index, direction) in DIRECTIONS.into_iter().enumerate() {
        seed = seed.wrapping_mul(1664525).wrapping_add(1013904223);
        if !timings.allowed[index] {
            continue;
        }
        let mut score = 100 + (seed >> 27) as i64;
        if angle > 15.0 && direction == Direction::Right
            || angle < -15.0 && direction == Direction::Left
        {
            score += 15;
        }
        if height > 0.25 && direction == Direction::Up
            || height < -0.25 && direction == Direction::Down
        {
            score += 8;
        }
        if math::horizontal(source.position, target.position) > 1.7 {
            score += 400u64.saturating_sub(timings.windup_ms[index]) as i64 / 5;
        }
        if context.last_attack == Some(direction) {
            score -= 64;
        }
        let blocked = match blocked {
            Some(Intent::Guard(Direction::Right | Direction::Left)) => {
                matches!(direction, Direction::Left | Direction::Right)
            }
            Some(Intent::Guard(Direction::Up)) => direction == Direction::Up,
            Some(Intent::Guard(Direction::Down)) | Some(Intent::Shield) => {
                direction == Direction::Down
            }
            _ => false,
        };
        if blocked {
            score -= 128;
        }
        if best.is_none_or(|(previous, _)| score > previous) {
            best = Some((score, direction));
        }
    }
    let Some((_, direction)) = best else {
        return wait("attack-unavailable");
    };
    Decision {
        kind: DecisionKind::Attack,
        reason,
        intent: Some(Intent::Attack(direction)),
        seed,
    }
}
