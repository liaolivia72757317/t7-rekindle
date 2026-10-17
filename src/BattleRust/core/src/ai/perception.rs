use super::{math, state::BotState, types::*};
use crate::navigation::{NavMesh, PathStatus};
use std::collections::BTreeMap;

pub(crate) fn actor(world: &WorldObservation, id: u32) -> Option<&ActorSnapshot> {
    world.actors.iter().find(|a| a.id == id)
}

fn straight(nav: &NavMesh, a: crate::navigation::Point, b: crate::navigation::Point) -> bool {
    nav.find_path(a, b, 0.0).is_ok_and(|path| {
        path.status == PathStatus::Ready
            && math::length(&path.points, true) <= math::horizontal(a, b) + 0.05
    })
}

pub fn visible(
    nav: &NavMesh,
    source: &ActorSnapshot,
    target: &ActorSnapshot,
    distance_limit: f64,
    near_range: f64,
) -> bool {
    let a = source.position;
    let b = target.position;
    let distance = math::horizontal(a, b);
    if distance > distance_limit || (a[2] - b[2]).abs() > 2.5 || !source.heading.is_finite() {
        return false;
    }
    let yaw = source.heading.to_radians();
    if distance > near_range
        && ((b[0] - a[0]) * yaw.cos() - (b[1] - a[1]) * yaw.sin()) / distance
            < 110.0_f64.to_radians().cos()
    {
        return false;
    }
    straight(nav, a, b)
}

pub(crate) fn allies<'a>(
    nav: &NavMesh,
    source: &ActorSnapshot,
    world: &'a WorldObservation,
) -> Vec<&'a ActorSnapshot> {
    world
        .actors
        .iter()
        .filter(|a| {
            a.id != source.id
                && a.alive
                && a.camp == source.camp
                && (a.position[2] - source.position[2]).abs() <= 1.0
                && math::horizontal(a.position, source.position) <= 8.0
                && straight(nav, source.position, a.position)
        })
        .collect()
}

pub(crate) fn sense(
    state: &mut BotState,
    states: &BTreeMap<u32, BotState>,
    source: &ActorSnapshot,
    world: &WorldObservation,
    nav: &NavMesh,
    now: u64,
) -> Option<u32> {
    let current = state.target_id;
    let mut tracked = current.or(state.search_target);
    if tracked.is_some_and(|id| actor(world, id).is_none_or(|a| !a.alive)) {
        state.search_target = None;
        state.last_seen = None;
        state.search_until_ms = 0;
        tracked = None;
        state.sense_at_ms = 0;
    } else if now >= state.search_until_ms && state.last_seen.is_some() {
        tracked = None;
    }
    let berserker = state.config.profile == Profile::Berserker;
    let limit = match state.config.profile {
        Profile::Aggressive => 18.0,
        Profile::Balanced => 16.0,
        Profile::Defensive => 14.0,
        Profile::Berserker => f64::INFINITY,
    };
    let perceived = |target: &ActorSnapshot, engaged: bool| {
        if berserker {
            nav.find_path(source.position, target.position, 0.35)
                .is_ok_and(|p| p.status == PathStatus::Ready)
        } else {
            visible(
                nav,
                source,
                target,
                limit + if engaged { 2.0 } else { 0.0 },
                if engaged { 6.0 } else { 3.0 },
            )
        }
    };
    let retained = tracked
        .and_then(|id| actor(world, id))
        .filter(|target| perceived(target, true));
    if let Some(target) = retained {
        state.track(
            target.id,
            math::distance(source.position, target.position),
            now,
        );
    } else {
        state.pursuit = None;
    }
    if now < state.sense_at_ms {
        if current.is_none() && retained.is_none() {
            return None;
        }
        if current == tracked
            && let Some(target) = retained
        {
            state.remember(target.id, target.position, now);
            return current;
        }
    }
    state.sense_at_ms = now + 500;
    let mut candidates: Vec<_> = world
        .actors
        .iter()
        .filter(|a| a.id != source.id && a.alive && a.camp != source.camp)
        .filter(|a| {
            if Some(a.id) == tracked {
                retained.is_some()
            } else {
                perceived(a, false)
            }
        })
        .collect();
    candidates.sort_by(|a, b| {
        math::distance(source.position, a.position)
            .total_cmp(&math::distance(source.position, b.position))
            .then(a.id.cmp(&b.id))
    });
    let target = select(
        state,
        states,
        world,
        nav,
        &candidates,
        if berserker { current } else { tracked },
        now,
    );
    if target != current {
        state.observed = None;
        state.react_at_ms = None;
        state.step_plan = None;
        state.pursuit = None;
        state.target_since_ms = target.map(|_| now);
    }
    state.target_id = target;
    if let Some(target) = target.and_then(|id| actor(world, id)) {
        state.track(
            target.id,
            math::distance(source.position, target.position),
            now,
        );
        state.remember(target.id, target.position, now);
    }
    target
}

fn select(
    state: &mut BotState,
    states: &BTreeMap<u32, BotState>,
    world: &WorldObservation,
    nav: &NavMesh,
    candidates: &[&ActorSnapshot],
    current: Option<u32>,
    now: u64,
) -> Option<u32> {
    let source = actor(world, state.config.actor_id).expect("validated perception source");
    state.threats.retain(|&id, t| {
        now >= t.at
            && now - t.at < 5000
            && t.damage - 5.0 * (now - t.at) as f64 / 1000.0 > 0.0
            && actor(world, id).is_some_and(|a| a.alive && a.camp != source.camp)
    });
    if candidates.is_empty() {
        return None;
    }
    let berserker = state.config.profile == Profile::Berserker;
    let current_actor = candidates.iter().copied().find(|a| Some(a.id) == current);
    if current_actor.is_some()
        && (source.action.index() >= 0
            || state
                .target_since_ms
                .is_some_and(|at| now < at + if berserker { 3000 } else { 1200 }))
    {
        return current;
    }
    if berserker && current_actor.is_none() {
        return Some(candidates[0].id);
    }
    let allies = if berserker {
        Vec::new()
    } else {
        allies(nav, source, world)
    };
    let mut scores = BTreeMap::new();
    for target in candidates {
        let distance = math::distance(source.position, target.position);
        let load = allies
            .iter()
            .filter(|a| {
                math::distance(a.position, target.position) <= 7.0
                    && (states
                        .get(&a.id)
                        .is_some_and(|s| s.target_id == Some(target.id))
                        || math::distance(a.position, target.position) <= 2.2)
            })
            .count();
        let mut score = distance + 3.0 * load.saturating_sub(1) as f64
            - (0.15 * state.threat(target.id, now)).min(6.0);
        if Some(target.id) == current {
            let bias = 2.0 * ((4.0 - distance) / (4.0 - 2.2)).clamp(0.0, 1.0);
            score -= bias * if target.health <= 20 { 2.0 } else { 1.0 };
        }
        scores.insert(target.id, (score, distance));
    }
    let protected = current_actor.is_some_and(|a| {
        let d = scores[&a.id].1;
        d > 2.2 && d <= state.pursuit_limits().0 && state.pursuit_active(a.id, now)
    });
    let best = candidates
        .iter()
        .filter(|a| !protected || Some(a.id) == current || state.threat(a.id, now) >= 30.0)
        .min_by(|a, b| {
            scores[&a.id]
                .0
                .total_cmp(&scores[&b.id].0)
                .then(a.id.cmp(&b.id))
        })
        .expect("current or candidates")
        .id;
    if let Some(current) = current_actor {
        let margin = if berserker {
            4.0_f64.max(scores[&current.id].1 * 0.3)
        } else {
            1.5
        };
        if scores[&current.id].0 <= scores[&best].0 + margin {
            return Some(current.id);
        }
    }
    Some(best)
}
