use super::{
    clearance::{clear_route, ground_goal},
    math,
    state::{BotState, StepPlan},
    types::*,
};
use crate::action::{Intent, Phase};
use crate::navigation::{NavMesh, NavPath, PathStatus, Point};

pub(super) fn preferred_gap(profile: Profile, source: &ActorSnapshot) -> f64 {
    let offset = match profile {
        Profile::Aggressive | Profile::Berserker => -0.1,
        Profile::Balanced => 0.0,
        Profile::Defensive => 0.1,
    };
    (source.preferred_distance + offset).clamp(0.8, 1.8)
}

pub(crate) fn plan(
    state: &mut BotState,
    source: &ActorSnapshot,
    target: &ActorSnapshot,
    world: &WorldObservation,
    nav: &NavMesh,
    now: u64,
) -> Option<(NavPath, &'static str)> {
    let origin = source.position;
    let position = target.position;
    let distance = math::horizontal(origin, position);
    if distance > 6.0 || (origin[2] - position[2]).abs() > 1.0 {
        state.step_plan = None;
        return None;
    }
    let can_defend = state.config.profile != Profile::Berserker;
    let mut gap = preferred_gap(state.config.profile, source);
    if can_defend && source.health <= 40 {
        gap = gap.max(1.85);
    }
    if can_defend
        && (source.hurt
            || source.action.reaction()
            || matches!(source.action.intent, Some(Intent::Attack(_)))
                && source.action.phase == Phase::Recovery
            || matches!(source.action.intent, Some(Intent::Guard(_))) && source.action.index() >= 0)
    {
        gap = gap.max(if state.config.profile == Profile::Aggressive {
            1.85
        } else {
            2.05
        });
    }
    let mode = if distance > gap + 0.2 {
        Some("press")
    } else if distance < gap - 0.2 {
        Some("retreat")
    } else {
        None
    };
    let Some(mode) = mode else {
        state.step_plan = None;
        return None;
    };
    let height_offset = origin[2] - nav.project(origin)?[2];
    let route_to = |goal: Point| -> Option<NavPath> {
        let route = nav.find_path(origin, goal, 0.35).ok()?;
        (route.status == PathStatus::Ready
            && math::length(&route.points, true) <= math::horizontal(origin, goal) + 0.05
            && clear_route(&route.points, height_offset, world, source.id))
        .then_some(route)
    };
    let previous = state.step_plan;
    if let Some(previous) = previous.filter(|p| {
        p.target == target.id
            && now < p.until
            && p.gap == gap
            && math::distance(p.target_position, position) <= 0.4
            && p.mode == mode
    }) {
        if math::horizontal(origin, previous.goal) <= 0.25 {
            return None;
        }
        if let Some(route) = route_to(previous.goal) {
            return Some((route, mode));
        }
    }
    state.step_plan = None;
    let angle = if distance > 0.001 {
        (origin[1] - position[1]).atan2(origin[0] - position[0])
    } else {
        std::f64::consts::PI - source.heading.to_radians()
    };
    let side = if let Some(previous) = previous.filter(|p| p.target == target.id) {
        previous.side * if state.rhythm(0, 3) == 0 { -1 } else { 1 }
    } else if state.rhythm(0, 1) == 1 {
        1
    } else {
        -1
    };
    for degrees in [
        0,
        35 * i32::from(side),
        -35 * i32::from(side),
        70 * i32::from(side),
        -70 * i32::from(side),
    ] {
        let Some(goal) = ground_goal(
            nav,
            math::point_at(position, angle + (degrees as f64).to_radians(), gap),
        )
        .filter(|goal| math::horizontal(origin, *goal) > 0.25) else {
            continue;
        };
        if let Some(route) = route_to(goal) {
            let until = now + state.rhythm(350, 900);
            state.step_plan = Some(StepPlan {
                target: target.id,
                until,
                gap,
                side: if degrees == 0 {
                    side
                } else if degrees > 0 {
                    1
                } else {
                    -1
                },
                goal,
                target_position: position,
                mode,
            });
            return Some((route, mode));
        }
    }
    None
}
