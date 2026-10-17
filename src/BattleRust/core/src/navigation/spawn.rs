use super::geometry::{compare_points, compensated_sum, cross, distance_xy, height, inside};
use super::{DEFAULT_RADIUS, EPSILON, NavMesh, NavigationError, PathStatus, Point, valid_point};
use std::collections::BTreeSet;

const DIAMETER: f64 = 2.0 * DEFAULT_RADIUS;
const SKIN: f64 = 0.001;
const MARGIN: f64 = DIAMETER / 2.0 + SKIN;
const SPACING: f64 = DIAMETER + SKIN;
const MAX_DISTANCE: f64 = 32.0;
const MAX_CANDIDATES: usize = 8;
const GROUND_OFFSET: f64 = 0.2;
const HEIGHT: f64 = 1.8;
const RESPAWN_MS: u64 = 5000;

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct SpawnReference {
    pub position: Point,
    pub heading: f64,
}

/// Immutable match preflight output; the battle still checks occupancy when respawning.
#[derive(Clone, Debug, PartialEq)]
pub struct SpawnCandidates {
    groups: Vec<Vec<SpawnReference>>,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum SpawnSelection {
    Ready {
        reference: usize,
        spawn: SpawnReference,
    },
    NoCandidates,
    Occupied,
}

impl SpawnCandidates {
    pub fn groups(&self) -> &[Vec<SpawnReference>] {
        &self.groups
    }

    /// Neighbors are already filtered by the match's actor lifecycle and sample freshness.
    pub fn choose(
        &self,
        actor_id: u64,
        died_at_ms: u64,
        neighbors: &[Point],
    ) -> Result<SpawnSelection, NavigationError> {
        if neighbors.iter().any(|p| !valid_point(*p)) {
            return Err(NavigationError("invalid navigation spawn neighbor"));
        }
        if self.groups.iter().all(Vec::is_empty) {
            return Ok(SpawnSelection::NoCandidates);
        }
        let offset = ((u128::from(actor_id) + u128::from(died_at_ms / RESPAWN_MS))
            % self.groups.len() as u128) as usize;
        for index in (offset..self.groups.len()).chain(0..offset) {
            for &spawn in &self.groups[index] {
                if neighbors.iter().all(|point| {
                    (spawn.position[2] - point[2]).abs() >= HEIGHT
                        || distance_xy(spawn.position, *point) >= SPACING
                }) {
                    return Ok(SpawnSelection::Ready {
                        reference: index,
                        spawn,
                    });
                }
            }
        }
        Ok(SpawnSelection::Occupied)
    }

    pub fn prewarm(
        nav: &NavMesh,
        points: &[SpawnReference],
        anchor: Point,
    ) -> Result<Self, NavigationError> {
        if !valid_point(anchor)
            || points
                .iter()
                .any(|p| !valid_point(p.position) || !p.heading.is_finite())
        {
            return Err(NavigationError("invalid navigation spawn reference"));
        }
        let interiors: Vec<_> = connected_faces(nav, anchor)
            .into_iter()
            .map(|face| (face, inset(&nav.polygons[face])))
            .collect();
        let mut groups = Vec::with_capacity(points.len());
        for reference in points {
            let position = reference.position;
            let mut nearby = Vec::new();
            for (face, polygon) in &interiors {
                if polygon.is_empty() {
                    continue;
                }
                let closest = nearest(polygon, position);
                let center = [
                    compensated_sum(polygon.iter().map(|p| p[0])) / polygon.len() as f64,
                    compensated_sum(polygon.iter().map(|p| p[1])) / polygon.len() as f64,
                    0.0,
                ];
                let mut proposals = vec![closest, Some(center)];
                if closest.is_some_and(|p| distance_xy(p, position) < SPACING) {
                    for angle in (0..360).step_by(45) {
                        let radians = (angle as f64).to_radians();
                        proposals.push(nearest(
                            polygon,
                            [
                                position[0] + radians.cos(),
                                position[1] + radians.sin(),
                                0.0,
                            ],
                        ));
                    }
                }
                for xy in proposals.into_iter().flatten() {
                    if distance_xy(xy, position) > MAX_DISTANCE + EPSILON
                        || points
                            .iter()
                            .any(|original| distance_xy(xy, original.position) < SPACING)
                    {
                        continue;
                    }
                    if let Some(ground) = height(&nav.polygons[*face], xy) {
                        nearby.push([xy[0], xy[1], ground + GROUND_OFFSET]);
                    }
                }
            }
            nearby.sort_by(compare_points);
            nearby.dedup();
            nearby.sort_by(|a, b| {
                distance_xy(*a, position)
                    .total_cmp(&distance_xy(*b, position))
                    .then_with(|| {
                        (a[2] - position[2])
                            .abs()
                            .total_cmp(&(b[2] - position[2]).abs())
                    })
                    .then_with(|| compare_points(a, b))
            });
            let mut selected: Vec<SpawnReference> = Vec::new();
            for point in nearby {
                if selected
                    .iter()
                    .all(|other| distance_xy(point, other.position) >= SPACING)
                    && nav.find_path(point, anchor, DEFAULT_RADIUS)?.status == PathStatus::Ready
                {
                    selected.push(SpawnReference {
                        position: point,
                        heading: reference.heading,
                    });
                    if selected.len() == MAX_CANDIDATES {
                        break;
                    }
                }
            }
            groups.push(selected);
        }
        Ok(Self { groups })
    }
}

fn connected_faces(nav: &NavMesh, anchor: Point) -> BTreeSet<usize> {
    let Some(start) = nav.locate(anchor) else {
        return BTreeSet::new();
    };
    let mut found = BTreeSet::from([start]);
    let mut pending = vec![start];
    while let Some(face) = pending.pop() {
        for link in &nav.links[face] {
            if distance_xy(link.a, link.b) > DIAMETER + EPSILON && found.insert(link.neighbor) {
                pending.push(link.neighbor);
            }
        }
    }
    found
}

fn inset(polygon: &[Point]) -> Vec<Point> {
    let mut result: Vec<_> = polygon.iter().map(|p| [p[0], p[1], 0.0]).collect();
    for (&a, &b) in polygon.iter().zip(polygon.iter().cycle().skip(1)) {
        let length = distance_xy(a, b);
        if length <= EPSILON {
            continue;
        }
        let mut clipped = Vec::new();
        for (&p, &q) in result.iter().zip(result.iter().cycle().skip(1)) {
            let dp = cross(a, b, p) / length - MARGIN;
            let dq = cross(a, b, q) / length - MARGIN;
            if dp >= 0.0 {
                clipped.push(p);
            }
            if (dp >= 0.0) != (dq >= 0.0) {
                let fraction = dp / (dp - dq);
                clipped.push([
                    p[0] + (q[0] - p[0]) * fraction,
                    p[1] + (q[1] - p[1]) * fraction,
                    0.0,
                ]);
            }
        }
        result = clipped;
        if result.len() < 3 {
            return Vec::new();
        }
    }
    result
}

fn nearest(polygon: &[Point], point: Point) -> Option<Point> {
    if inside(polygon, point) {
        return Some([point[0], point[1], 0.0]);
    }
    let mut best = None;
    for (&a, &b) in polygon.iter().zip(polygon.iter().cycle().skip(1)) {
        let length2 = (b[0] - a[0]).powi(2) + (b[1] - a[1]).powi(2);
        if length2 <= EPSILON * EPSILON {
            continue;
        }
        let fraction = (((point[0] - a[0]) * (b[0] - a[0]) + (point[1] - a[1]) * (b[1] - a[1]))
            / length2)
            .clamp(0.0, 1.0);
        let candidate = [
            a[0] + (b[0] - a[0]) * fraction,
            a[1] + (b[1] - a[1]) * fraction,
            0.0,
        ];
        if best.is_none_or(|previous| distance_xy(candidate, point) < distance_xy(previous, point))
        {
            best = Some(candidate);
        }
    }
    best
}
