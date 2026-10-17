use super::{clearance::ground_goal, math, state::BotState};
use crate::navigation::{NavMesh, PathStatus, Point};
use std::collections::BTreeSet;

fn reachable_goal(nav: &NavMesh, origin: Point, desired: Point) -> Option<Point> {
    let mut faces = BTreeSet::new();
    let mut pending = Vec::new();
    if let Some(face) = nav.locate(origin) {
        faces.insert(face);
        pending.push(face);
    }
    while let Some(face) = pending.pop() {
        for link in &nav.links()[face] {
            if math::horizontal(link.a, link.b) > 0.7 + 1e-5 && faces.insert(link.neighbor) {
                pending.push(link.neighbor);
            }
        }
    }
    let mut centers: Vec<_> = faces
        .into_iter()
        .map(|index| {
            let polygon = &nav.polygons()[index];
            std::array::from_fn(|i| math::sum(polygon.iter().map(|p| p[i])) / polygon.len() as f64)
        })
        .collect();
    centers.sort_by(|a, b| math::distance(*a, desired).total_cmp(&math::distance(*b, desired)));
    centers.truncate(6);
    nav.project(desired)
        .into_iter()
        .chain(centers)
        .find(|candidate| {
            math::distance(*candidate, desired) <= 18.0
                && math::horizontal(*candidate, origin) > 0.65
                && nav
                    .find_path(origin, *candidate, 0.35)
                    .is_ok_and(|p| p.status == PathStatus::Ready)
        })
}

fn local_goal(nav: &NavMesh, origin: Point, seed: u32) -> Option<Point> {
    for radius in [5.0, 2.0, 1.0] {
        for offset in (0..360).step_by(45) {
            let desired =
                math::point_at(origin, ((seed % 360 + offset) as f64).to_radians(), radius);
            if let Some(point) = ground_goal(nav, desired)
                && nav
                    .find_path(origin, point, 0.35)
                    .is_ok_and(|p| p.status == PathStatus::Ready)
            {
                return Some(point);
            }
        }
    }
    None
}

pub(crate) fn goal(
    state: &mut BotState,
    nav: &NavMesh,
    origin: Point,
    now: u64,
) -> (Option<Point>, &'static str) {
    if now < state.search_until_ms
        && let Some(position) = state.last_seen
    {
        let approaching = now < state.search_until_ms.saturating_sub(2000)
            && math::distance(origin, position) > 0.65;
        return (approaching.then_some(position), "search");
    }
    state.search_target = None;
    state.last_seen = None;
    state.search_until_ms = 0;
    if now < state.roam_wait_until_ms {
        return (None, "patrol-wait");
    }
    if state
        .roam_goal
        .is_some_and(|p| math::horizontal(origin, p) <= 0.65)
    {
        state.roam_goal = None;
    }
    if let Some(goal) = state.roam_goal {
        return (
            Some(goal),
            if state.roam_count == 1 {
                "advance"
            } else {
                "patrol"
            },
        );
    }
    let mut desired = state.roam_anchor;
    if state.roam_count > 0 {
        let seed = state.random_step();
        let angle = (seed % 360) as f64;
        let radius = 5.0 + ((seed >> 12) % 9) as f64;
        desired = math::point_at(desired, angle.to_radians(), radius);
    }
    let target = reachable_goal(nav, origin, desired)
        .or_else(|| local_goal(nav, origin, state.random_step()));
    if target.is_none() {
        state.roam_wait_until_ms = now + 1000;
        return (None, "no-path");
    }
    state.roam_goal = target;
    state.roam_count += 1;
    (
        target,
        if state.roam_count == 1 {
            "advance"
        } else {
            "patrol"
        },
    )
}
