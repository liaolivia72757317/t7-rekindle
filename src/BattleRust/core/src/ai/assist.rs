use super::{
    clearance::{clear_route, ground_goal},
    math,
    state::{AssistPlan, BotState},
    types::*,
};
use crate::navigation::{NavMesh, NavPath, PathStatus, Point};
use std::collections::BTreeMap;

const ASSIST_RADIUS: f64 = 1.65;
const PLAN_MS: u64 = 1200;

pub(super) fn plan(
    state: &mut BotState,
    states: &BTreeMap<u32, BotState>,
    source: &ActorSnapshot,
    world: &WorldObservation,
    nav: &NavMesh,
    now: u64,
) -> Option<(NavPath, Point)> {
    state.assist = None;
    let across_map = state.config.profile != Profile::Berserker;
    let allies: Vec<_> = world
        .actors
        .iter()
        .filter(|a| a.id != source.id && a.alive && a.camp == source.camp)
        .filter(|a| {
            across_map
                || ((a.position[2] - source.position[2]).abs() <= 1.
                    && math::horizontal(a.position, source.position) <= 8.
                    && nav
                        .find_path(source.position, a.position, 0.)
                        .is_ok_and(|p| {
                            p.status == PathStatus::Ready
                                && math::length(&p.points, true)
                                    <= math::horizontal(source.position, a.position) + 0.05
                        }))
        })
        .collect();
    let reservations: Vec<_> = allies
        .iter()
        .filter_map(|ally| states.get(&ally.id))
        .flat_map(|bot| {
            bot.tactic
                .filter(|p| p.until > now)
                .map(|p| p.goal)
                .into_iter()
                .chain(bot.assist.filter(|p| p.until > now).map(|p| p.goal))
        })
        .collect();
    let side = if state.config.lane != 0 {
        state.config.lane
    } else if source.id % 2 == 1 {
        1
    } else {
        -1
    };
    let mut candidates = Vec::new();
    for ally in &allies {
        let Some(bot) = states.get(&ally.id) else {
            continue;
        };
        let Some(target_id) = bot.target_id else {
            continue;
        };
        let Some(target) = world
            .actors
            .iter()
            .find(|a| a.id == target_id && a.alive && a.camp != source.camp)
        else {
            continue;
        };
        let Some(last_seen) = bot.last_seen.filter(|_| now < bot.search_until_ms) else {
            continue;
        };
        if math::distance(ally.position, last_seen) > 7. {
            continue;
        }
        let axis = (last_seen[1] - ally.position[1]).atan2(last_seen[0] - ally.position[0]);
        for degrees in [
            60 * i32::from(side),
            -60 * i32::from(side),
            120 * i32::from(side),
            -120 * i32::from(side),
            180,
        ] {
            let Some(goal) = ground_goal(
                nav,
                math::point_at(
                    ally.position,
                    axis + (degrees as f64).to_radians(),
                    ASSIST_RADIUS,
                ),
            ) else {
                continue;
            };
            if allies
                .iter()
                .map(|a| a.position)
                .chain(reservations.iter().copied())
                .any(|p| (goal[2] - p[2]).abs() < 1.8 && math::horizontal(goal, p) < 1.25)
            {
                continue;
            }
            let distance = math::distance(source.position, goal);
            candidates.push((distance, ally.id, target.id, last_seen, ally.position, goal));
        }
    }
    candidates.sort_by(|a, b| a.0.total_cmp(&b.0).then(a.1.cmp(&b.1)));
    for (distance, ally, target, target_position, ally_position, goal) in candidates {
        let Ok(route) = nav.find_path(source.position, goal, 0.35) else {
            continue;
        };
        if route.status != PathStatus::Ready
            || (!across_map && math::length(&route.points, false) > distance + 4.)
            || !clear_route(
                &route.points,
                source.position[2] - nav.project(source.position)?[2],
                world,
                source.id,
            )
        {
            continue;
        }
        state.assist = Some(AssistPlan {
            ally,
            target,
            goal,
            until: now + PLAN_MS,
        });
        state.roam_anchor = ally_position;
        state.roam_count = state.roam_count.max(1);
        state.roam_goal = None;
        state.roam_wait_until_ms = 0;
        return Some((route, target_position));
    }
    None
}
