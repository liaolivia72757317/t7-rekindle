use super::geometry::{cross, crossing, distance, distance_xy, height, inside, interpolate};
use super::{EPSILON, NavMesh, NavigationError, Point};
use std::cmp::Ordering;
use std::collections::BinaryHeap;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PathStatus {
    Ready,
    OutsideMesh,
    NoPath,
}

impl PathStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Ready => "ready",
            Self::OutsideMesh => "outside-mesh",
            Self::NoPath => "no-path",
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct NavPath {
    pub status: PathStatus,
    pub faces: Vec<usize>,
    pub points: Vec<Point>,
}

impl NavPath {
    fn empty(status: PathStatus) -> Self {
        Self {
            status,
            faces: Vec::new(),
            points: Vec::new(),
        }
    }
}

#[derive(Clone, Copy, PartialEq)]
struct QueueEntry {
    priority: f64,
    face: usize,
}
impl Eq for QueueEntry {}
impl PartialOrd for QueueEntry {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}
impl Ord for QueueEntry {
    fn cmp(&self, other: &Self) -> Ordering {
        other
            .priority
            .total_cmp(&self.priority)
            .then_with(|| other.face.cmp(&self.face))
    }
}

impl NavMesh {
    pub fn find_path(
        &self,
        start: Point,
        target: Point,
        radius: f64,
    ) -> Result<NavPath, NavigationError> {
        let (Some(first), Some(last)) = (self.locate(start), self.locate(target)) else {
            return Ok(NavPath::empty(PathStatus::OutsideMesh));
        };
        if !radius.is_finite() || radius < 0.0 {
            return Err(NavigationError("invalid navigation agent radius"));
        }
        let mut costs = vec![f64::INFINITY; self.polygons.len()];
        let mut previous = vec![None; self.polygons.len()];
        let mut portals = vec![None; self.polygons.len()];
        let mut closed = vec![false; self.polygons.len()];
        costs[first] = 0.0;
        let mut queue = BinaryHeap::from([QueueEntry {
            priority: 0.0,
            face: first,
        }]);
        while let Some(QueueEntry { face, .. }) = queue.pop() {
            if closed[face] {
                continue;
            }
            if face == last {
                break;
            }
            closed[face] = true;
            for link in &self.links[face] {
                if distance_xy(link.a, link.b) <= 2.0 * radius + EPSILON {
                    continue;
                }
                let cost = costs[face] + distance(self.centers[face], self.centers[link.neighbor]);
                if cost < costs[link.neighbor] {
                    costs[link.neighbor] = cost;
                    previous[link.neighbor] = Some(face);
                    portals[link.neighbor] = Some((link.a, link.b));
                    queue.push(QueueEntry {
                        priority: cost + distance(self.centers[link.neighbor], self.centers[last]),
                        face: link.neighbor,
                    });
                }
            }
        }
        if !costs[last].is_finite() {
            return Ok(NavPath::empty(PathStatus::NoPath));
        }
        let mut chain = vec![last];
        while *chain.last().expect("nonempty chain") != first {
            chain.push(
                previous[*chain.last().expect("nonempty chain")].expect("reachable predecessor"),
            );
        }
        chain.reverse();
        let start = self.project(start).expect("located start");
        let target = self.project(target).expect("located target");
        let mut corridor = vec![(start, start)];
        for pair in chain.windows(2) {
            let (before, after) = (pair[0], pair[1]);
            let (a, b) = portals[after].expect("reachable portal");
            let margin = radius / distance_xy(a, b);
            let (mut left, mut right) = (interpolate(a, b, margin), interpolate(b, a, margin));
            if cross(self.centers[before], self.centers[after], left) < 0.0 {
                std::mem::swap(&mut left, &mut right);
            }
            corridor.push((left, right));
        }
        corridor.push((target, target));
        let anchors = pull_string(&corridor);
        let points = self.route_points(&chain, &corridor, &anchors).or_else(|| {
            // Ambiguous overlapping seam portals retain the corridor instead of cutting across it.
            let anchors: Vec<_> = corridor
                .iter()
                .enumerate()
                .map(|(i, &(a, b))| (std::array::from_fn(|axis| (a[axis] + b[axis]) / 2.0), i))
                .collect();
            self.route_points(&chain, &corridor, &anchors)
        });
        Ok(match points {
            Some(points) if !points.is_empty() => NavPath {
                status: PathStatus::Ready,
                faces: chain,
                points,
            },
            _ => NavPath::empty(PathStatus::NoPath),
        })
    }

    fn route_points(
        &self,
        chain: &[usize],
        corridor: &[(Point, Point)],
        anchors: &[(Point, usize)],
    ) -> Option<Vec<Point>> {
        let mut points = vec![corridor[0].0];
        for pair in anchors.windows(2) {
            let ((a, i), (b, j)) = (pair[0], pair[1]);
            for k in i + 1..(j + 1).min(corridor.len() - 1) {
                let p = crossing(a, b, corridor[k])?;
                for face in [chain[k - 1], chain[k]] {
                    if !inside(&self.polygons[face], p) {
                        return None;
                    }
                    let point = [p[0], p[1], height(&self.polygons[face], p)?];
                    if distance(*points.last().expect("start exists"), point) > EPSILON {
                        points.push(point);
                    }
                }
            }
        }
        points.push(corridor.last().expect("target exists").0);
        Some(points)
    }
}

fn pull_string(portals: &[(Point, Point)]) -> Vec<(Point, usize)> {
    let mut apex = portals[0].0;
    let (mut left, mut right) = (apex, apex);
    let (mut left_index, mut right_index) = (0, 0);
    let mut result = vec![(apex, 0)];
    let mut index = 1;
    while index < portals.len() {
        let (new_left, new_right) = portals[index];
        if cross(apex, right, new_right) >= 0.0 {
            if apex == right || cross(apex, left, new_right) < 0.0 {
                right = new_right;
                right_index = index;
            } else {
                result.push((left, left_index));
                apex = left;
                right = apex;
                right_index = left_index;
                index = left_index + 1;
                continue;
            }
        }
        if cross(apex, left, new_left) <= 0.0 {
            if apex == left || cross(apex, right, new_left) > 0.0 {
                left = new_left;
                left_index = index;
            } else {
                result.push((right, right_index));
                apex = right;
                left = apex;
                left_index = right_index;
                index = right_index + 1;
                continue;
            }
        }
        index += 1;
    }
    if result.last().expect("start exists").1 != portals.len() - 1 {
        result.push((portals.last().expect("target exists").0, portals.len() - 1));
    }
    result
}
