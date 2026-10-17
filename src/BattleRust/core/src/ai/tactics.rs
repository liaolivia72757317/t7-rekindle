use super::{
    clearance::{clear_route, ground_goal},
    footwork, math, perception,
    state::{BotState, TacticPlan},
    types::*,
};
use crate::navigation::{NavMesh, NavPath, PathStatus, Point};
use std::collections::BTreeMap;

const LOCAL_RANGE: f64 = 8.;
const JOIN_RANGE: f64 = 7.;
const SPACING: f64 = 1.25;
const SUPPORT_RADIUS: f64 = 3.1;
const ARC_RADIUS: f64 = 2.4;
const ARRIVAL_RADIUS: f64 = 0.25;

fn flank_route(
    nav: &NavMesh,
    world: &WorldObservation,
    source: &ActorSnapshot,
    target: &ActorSnapshot,
    goal: Point,
) -> Option<NavPath> {
    let origin = source.position;
    let first = (origin[1] - target.position[1]).atan2(origin[0] - target.position[0]);
    let last = (goal[1] - target.position[1]).atan2(goal[0] - target.position[0]);
    let delta = (last - first + std::f64::consts::PI).rem_euclid(2. * std::f64::consts::PI)
        - std::f64::consts::PI;
    let mut stops = Vec::new();
    if delta.abs() > 60_f64.to_radians() {
        let radius = ARC_RADIUS.max(math::horizontal(goal, target.position));
        let count = (delta.abs() / 40_f64.to_radians()).ceil() as usize;
        for step in 0..=count {
            stops.push(math::point_at(
                target.position,
                first + delta * step as f64 / count as f64,
                radius,
            ));
        }
    }
    stops.push(goal);
    let mut points: Vec<Point> = Vec::new();
    for end in stops {
        let route = nav
            .find_path(points.last().copied().unwrap_or(origin), end, 0.35)
            .ok()?;
        if route.status != PathStatus::Ready {
            return None;
        }
        let start = usize::from(!points.is_empty());
        points.extend_from_slice(&route.points[start..]);
    }
    if math::length(&points, false) > math::distance(origin, goal) + 8. {
        return None;
    }
    let offset = origin[2] - nav.project(origin)?[2];
    if !clear_route(&points, offset, world, source.id) {
        return None;
    }
    Some(NavPath {
        status: PathStatus::Ready,
        faces: Vec::new(),
        points,
    })
}

fn available(
    goal: Point,
    source: &ActorSnapshot,
    allies: &[&ActorSnapshot],
    world: &WorldObservation,
    reserved: &[Point],
    target: &ActorSnapshot,
    front: Option<Point>,
) -> bool {
    let actual = goal;
    let bodies = world
        .actors
        .iter()
        .filter(|actor| actor.id != source.id && actor.blocks_movement);
    if bodies.clone().any(|body| {
        (actual[2] - body.position[2]).abs() < 1.8 && math::horizontal(actual, body.position) < 0.8
    }) {
        return false;
    }
    allies
        .iter()
        .map(|a| a.position)
        .chain(reserved.iter().copied())
        .chain(front)
        .all(|other| {
            (actual[2] - other[2]).abs() >= 1.8 || math::horizontal(actual, other) >= SPACING
        })
        && math::horizontal(actual, target.position) >= 0.7
}

pub(super) fn plan(
    state: &mut BotState,
    states: &BTreeMap<u32, BotState>,
    source: &ActorSnapshot,
    target: &ActorSnapshot,
    world: &WorldObservation,
    nav: &NavMesh,
    now: u64,
) -> Option<(NavPath, &'static str)> {
    if math::distance(source.position, target.position) > LOCAL_RANGE {
        state.tactic = None;
        return None;
    }
    let allies = perception::allies(nav, source, world);
    let supporters: Vec<_> = allies
        .iter()
        .copied()
        .filter(|ally| {
            math::distance(ally.position, target.position) <= JOIN_RANGE
                && if !states.contains_key(&ally.id) {
                    math::distance(ally.position, target.position) <= 2.2
                        && (ally.action.phase != crate::action::Phase::Idle || ally.hurt)
                } else {
                    states
                        .get(&ally.id)
                        .is_some_and(|bot| bot.target_id.is_none_or(|id| id == target.id))
                }
        })
        .collect();
    let crowded = allies
        .iter()
        .any(|ally| math::horizontal(source.position, ally.position) < SPACING);
    if supporters.is_empty() && !crowded {
        state.tactic = None;
        return None;
    }
    let (anchor_id, anchor) = std::iter::once((source.id, source.position))
        .chain(supporters.iter().map(|ally| (ally.id, ally.position)))
        .min_by(|a, b| {
            math::distance(a.1, target.position)
                .total_cmp(&math::distance(b.1, target.position))
                .then(a.0.cmp(&b.0))
        })?;
    let axis = (anchor[1] - target.position[1]).atan2(anchor[0] - target.position[0]);
    let reservations: Vec<_> = allies
        .iter()
        .filter_map(|ally| {
            states
                .get(&ally.id)
                .and_then(|bot| bot.tactic)
                .filter(|plan| plan.until > now)
                .map(|plan| plan.goal)
        })
        .collect();
    let attack_radius = footwork::preferred_gap(state.config.profile, source);
    let front = (anchor_id != source.id).then(|| {
        let anchor = perception::actor(world, anchor_id).expect("observed anchor");
        let radius = states
            .get(&anchor_id)
            .map_or(anchor.preferred_distance, |bot| {
                footwork::preferred_gap(bot.config.profile, anchor)
            });
        math::point_at(target.position, axis, radius)
    });
    if let Some(previous) = state.tactic.filter(|old| {
        old.target == target.id
            && old.until > now
            && math::distance(old.target_position, target.position) <= 0.6
            && (old.mode == "support"
                || (math::horizontal(old.goal, target.position) - attack_radius).abs()
                    <= ARRIVAL_RADIUS)
            && available(
                old.goal,
                source,
                &allies,
                world,
                &reservations,
                target,
                None,
            )
    }) {
        if math::horizontal(source.position, previous.goal) > ARRIVAL_RADIUS {
            if let Some(route) = flank_route(nav, world, source, target, previous.goal) {
                return Some((route, previous.mode));
            }
        } else {
            return Some((
                NavPath {
                    status: PathStatus::Ready,
                    faces: Vec::new(),
                    points: vec![source.position],
                },
                previous.mode,
            ));
        }
    }
    let side = if state.config.lane != 0 {
        state.config.lane
    } else if source.id % 2 == 1 {
        1
    } else {
        -1
    };
    let rear_weight = match state.config.profile {
        Profile::Aggressive => 3.,
        Profile::Balanced => 1.5,
        Profile::Defensive => 0.5,
        Profile::Berserker => 0.,
    };
    let mut candidates = Vec::new();
    for radius in [attack_radius, SUPPORT_RADIUS] {
        for degrees in [
            0,
            60 * i32::from(side),
            -60 * i32::from(side),
            120 * i32::from(side),
            -120 * i32::from(side),
            180,
        ] {
            let angle = axis + (degrees as f64).to_radians();
            let Some(goal) = ground_goal(nav, math::point_at(target.position, angle, radius))
            else {
                continue;
            };
            if !available(goal, source, &allies, world, &reservations, target, front) {
                continue;
            }
            let facing = (angle + target.heading.to_radians()).cos();
            let mode = if radius == SUPPORT_RADIUS {
                "support"
            } else if anchor_id != source.id && facing < -0.35 {
                "rear"
            } else if anchor_id != source.id {
                "flank"
            } else if crowded {
                "spread"
            } else {
                "approach"
            };
            let mut score = math::distance(source.position, goal)
                + if anchor_id != source.id {
                    rear_weight * facing
                } else {
                    0.
                };
            if state
                .tactic
                .is_some_and(|old| old.target == target.id && math::distance(old.goal, goal) < 0.45)
            {
                score -= 2.;
            }
            if mode == "support" {
                score += 100.;
            }
            candidates.push((score, goal, mode));
        }
    }
    candidates.sort_by(|a, b| {
        a.0.total_cmp(&b.0)
            .then_with(|| a.1[0].total_cmp(&b.1[0]))
            .then_with(|| a.1[1].total_cmp(&b.1[1]))
    });
    for (_, goal, mode) in candidates {
        if let Some(route) = flank_route(nav, world, source, target, goal) {
            state.tactic = Some(TacticPlan {
                target: target.id,
                until: now + state.rhythm(800, 1600),
                goal,
                target_position: target.position,
                mode,
            });
            return Some((route, mode));
        }
    }
    state.tactic = None;
    None
}
