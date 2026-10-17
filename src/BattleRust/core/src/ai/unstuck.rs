use super::{
    clearance::{clear_route, ground_goal},
    math,
    state::{BotState, MotionProbe, RecoveryPlan},
    types::{ActorSnapshot, MotionAttempt, MovementIntent, WorldObservation},
};
use crate::navigation::{NavMesh, PathStatus, Point};

const SAMPLE_GAP_MS: u64 = 250;
const STUCK_MS: u64 = 1200;
const RECOVERY_MS: u64 = 1500;
const RETRY_MS: u64 = 1000;
const TURNS: [f64; 16] = [
    90., -90., 180., 135., -135., 45., -45., 0., 112.5, -112.5, 157.5, -157.5, 67.5, -67.5, 22.5,
    -22.5,
];

pub(super) fn observe(state: &mut BotState, attempt: MotionAttempt, now: u64) {
    if !attempt.attempted_forward {
        state.motion_probe = None;
        state.stuck_heading = None;
        return;
    }
    let next = match state.motion_probe {
        Some(previous)
            if now.saturating_sub(previous.attempted_at) <= SAMPLE_GAP_MS
                && attempt.sampled_at_ms >= previous.sampled_at
                && attempt.sampled_at_ms - previous.sampled_at <= SAMPLE_GAP_MS
                && math::distance(attempt.position, previous.position) < 0.2 =>
        {
            MotionProbe {
                sampled_at: attempt.sampled_at_ms,
                attempted_at: now,
                ..previous
            }
        }
        _ => {
            state.stuck_heading = None;
            MotionProbe {
                position: attempt.position,
                since: attempt.sampled_at_ms,
                sampled_at: attempt.sampled_at_ms,
                attempted_at: now,
            }
        }
    };
    if next.sampled_at - next.since >= STUCK_MS && state.recovery.is_none() {
        state.stuck_heading = Some(attempt.heading);
    }
    state.motion_probe = Some(next);
}

fn route_to(
    state: &BotState,
    source: &ActorSnapshot,
    world: &WorldObservation,
    nav: &NavMesh,
    goal: Point,
) -> Option<MovementIntent> {
    let route = nav.find_path(source.position, goal, 0.35).ok()?;
    if route.status != PathStatus::Ready
        || !clear_route(
            &route.points,
            source.position[2] - nav.project(source.position)?[2],
            world,
            state.config.actor_id,
        )
    {
        return None;
    }
    Some(MovementIntent::Follow {
        points: route.points,
        look_at: None,
    })
}

pub(super) fn plan(
    state: &mut BotState,
    source: &ActorSnapshot,
    world: &WorldObservation,
    nav: &NavMesh,
    now: u64,
) -> Option<(MovementIntent, &'static str)> {
    let previous = state.recovery;
    if previous.is_none() && state.stuck_heading.is_none() {
        return None;
    }
    if let Some(previous) = previous {
        if previous
            .goal
            .is_some_and(|goal| math::horizontal(source.position, goal) <= 0.25)
        {
            state.recovery = None;
            state.stuck_heading = None;
            state.motion_probe = None;
            return None;
        }
        if now < previous.until {
            return Some(match previous.goal {
                Some(goal) => {
                    if let Some(route) = route_to(state, source, world, nav, goal) {
                        (route, "unstuck")
                    } else {
                        (MovementIntent::Stop, "unstuck-wait")
                    }
                }
                None => (MovementIntent::Stop, "unstuck-wait"),
            });
        }
    }
    let heading = previous.map_or_else(
        || state.stuck_heading.expect("stuck trigger"),
        |p| p.heading,
    );
    let start = previous.map_or(0, |p| (p.turn + 1) % TURNS.len());
    let side = if state.config.lane != 0 {
        state.config.lane
    } else if source.id % 2 == 1 {
        1
    } else {
        -1
    };
    let mut chosen = None;
    for distance in [1.2, 0.8, 0.5] {
        for offset in 0..TURNS.len() {
            let turn = (start + offset) % TURNS.len();
            let angle = (-heading + TURNS[turn] * f64::from(side)).to_radians();
            let Some(goal) = ground_goal(nav, math::point_at(source.position, angle, distance))
            else {
                continue;
            };
            let Some(MovementIntent::Follow { points, .. }) =
                route_to(state, source, world, nav, goal)
            else {
                continue;
            };
            if math::length(&points, true) > math::horizontal(source.position, goal) + 0.05 {
                continue;
            }
            chosen = Some((turn, goal, points));
            break;
        }
        if chosen.is_some() {
            break;
        }
    }
    let (turn, goal, movement, status) = if let Some((turn, goal, points)) = chosen {
        (
            turn,
            Some(goal),
            MovementIntent::Follow {
                points,
                look_at: None,
            },
            "unstuck",
        )
    } else {
        (start, None, MovementIntent::Stop, "unstuck-wait")
    };
    state.recovery = Some(RecoveryPlan {
        attempt: previous.map_or(1, |p| p.attempt.saturating_add(1)),
        turn,
        heading,
        goal,
        until: now
            + if goal.is_some() {
                RECOVERY_MS
            } else {
                RETRY_MS
            },
    });
    state.motion_probe = None;
    state.stuck_heading = None;
    state.tactic = None;
    state.assist = None;
    state.step_plan = None;
    state.roam_goal = None;
    Some((movement, status))
}
