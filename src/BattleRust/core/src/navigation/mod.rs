//! Deterministic walking corridors and respawn preflight, not movement simulation.
pub(crate) mod geometry;
mod path;
mod resource;
mod spawn;

pub use geometry::{DEFAULT_RADIUS, EPSILON};
pub use path::{NavPath, PathStatus};
pub use resource::{NavigationEdge, NavigationFace, NavigationResource};
pub use spawn::{SpawnCandidates, SpawnReference, SpawnSelection};

use geometry::{compare_points, compensated_sum, cross, height, inside};
use std::collections::{BTreeMap, HashMap};
use std::fmt;

pub type Point = [f64; 3];
const CELL_SIZE: f64 = 8.0;
const HEIGHT_TOLERANCE: f64 = 1.0;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct NavigationError(pub(crate) &'static str);

impl fmt::Display for NavigationError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.0)
    }
}

impl std::error::Error for NavigationError {}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct NavLink {
    pub neighbor: usize,
    pub a: Point,
    pub b: Point,
}

#[derive(Debug)]
pub struct NavMesh {
    polygons: Vec<Vec<Point>>,
    links: Vec<Vec<NavLink>>,
    centers: Vec<Point>,
    cells: HashMap<[i64; 2], Vec<usize>>,
}

impl NavMesh {
    pub fn new(
        polygons: Vec<Vec<Point>>,
        mut links: Vec<Vec<NavLink>>,
    ) -> Result<Self, NavigationError> {
        if polygons.len() != links.len() {
            return Err(NavigationError("navigation link rows do not match faces"));
        }
        let mut prepared = Vec::with_capacity(polygons.len());
        for mut polygon in polygons {
            if !(3..=128).contains(&polygon.len()) || polygon.iter().any(|p| !valid_point(*p)) {
                return Err(NavigationError("invalid navigation polygon"));
            }
            let area = compensated_sum(
                polygon
                    .iter()
                    .zip(polygon.iter().cycle().skip(1))
                    .map(|(a, b)| a[0] * b[1] - b[0] * a[1]),
            );
            if !area.is_finite() {
                return Err(NavigationError("navigation face extent exceeds limit"));
            }
            if area.abs() <= EPSILON {
                prepared.push(Vec::new());
                continue;
            }
            if area < 0.0 {
                polygon.reverse();
            }
            let size = polygon.len();
            if (0..size).any(|i| {
                cross(
                    polygon[(i + size - 1) % size],
                    polygon[i],
                    polygon[(i + 1) % size],
                ) < -EPSILON
            }) {
                prepared.push(Vec::new());
            } else {
                prepared.push(polygon);
            }
        }
        for (i, row) in links.iter_mut().enumerate() {
            if row.iter().any(|link| {
                link.neighbor >= prepared.len() || !valid_point(link.a) || !valid_point(link.b)
            }) {
                return Err(NavigationError("invalid navigation link"));
            }
            row.retain(|link| !prepared[i].is_empty() && !prepared[link.neighbor].is_empty());
            row.sort_by(|a, b| {
                a.neighbor
                    .cmp(&b.neighbor)
                    .then_with(|| compare_points(&a.a, &b.a))
                    .then_with(|| compare_points(&a.b, &b.b))
            });
        }
        let centers: Vec<Point> = prepared
            .iter()
            .map(|polygon| {
                if polygon.is_empty() {
                    [0.0; 3]
                } else {
                    std::array::from_fn(|i| {
                        compensated_sum(polygon.iter().map(|p| p[i])) / polygon.len() as f64
                    })
                }
            })
            .collect();
        if centers.iter().any(|point| !valid_point(*point)) {
            return Err(NavigationError("navigation center exceeds numeric range"));
        }
        let mut cells: HashMap<[i64; 2], Vec<usize>> = HashMap::new();
        for (index, polygon) in prepared.iter().enumerate() {
            if polygon.is_empty() {
                continue;
            }
            let mut lower = [i64::MAX; 2];
            let mut upper = [i64::MIN; 2];
            for point in polygon {
                let cell =
                    cell(*point).ok_or(NavigationError("navigation face extent exceeds limit"))?;
                for axis in 0..2 {
                    lower[axis] = lower[axis].min(cell[axis]);
                    upper[axis] = upper[axis].max(cell[axis]);
                }
            }
            let width = i128::from(upper[0]) - i128::from(lower[0]) + 1;
            let height = i128::from(upper[1]) - i128::from(lower[1]) + 1;
            if width > 10000 || height > 10000 || width * height > 10000 {
                return Err(NavigationError("navigation face extent exceeds limit"));
            }
            for x in lower[0]..=upper[0] {
                for y in lower[1]..=upper[1] {
                    cells.entry([x, y]).or_default().push(index);
                }
            }
        }
        Ok(Self {
            polygons: prepared,
            links,
            centers,
            cells,
        })
    }

    pub fn from_polygons(polygons: Vec<Vec<Point>>) -> Result<Self, NavigationError> {
        let mut links = vec![Vec::new(); polygons.len()];
        let mut edges = BTreeMap::new();
        for (index, polygon) in polygons.iter().enumerate() {
            for (&a, &b) in polygon.iter().zip(polygon.iter().cycle().skip(1)) {
                let (mut a_key, mut b_key) = (point_key(a), point_key(b));
                if a_key > b_key {
                    std::mem::swap(&mut a_key, &mut b_key);
                }
                if let Some(&other) = edges.get(&(a_key, b_key)) {
                    links[index].push(NavLink {
                        neighbor: other,
                        a,
                        b,
                    });
                    links[other].push(NavLink {
                        neighbor: index,
                        a: b,
                        b: a,
                    });
                } else {
                    edges.insert((a_key, b_key), index);
                }
            }
        }
        Self::new(polygons, links)
    }

    pub fn polygons(&self) -> &[Vec<Point>] {
        &self.polygons
    }
    pub fn links(&self) -> &[Vec<NavLink>] {
        &self.links
    }

    pub fn locate(&self, point: Point) -> Option<usize> {
        if !valid_point(point) {
            return None;
        }
        let candidates = self.cells.get(&cell(point)?)?;
        let mut best: Option<(f64, usize)> = None;
        for &index in candidates {
            let polygon = &self.polygons[index];
            if !inside(polygon, point) {
                continue;
            }
            if let Some(surface) = height(polygon, point) {
                let difference = (surface - point[2]).abs();
                if difference <= HEIGHT_TOLERANCE
                    && best.is_none_or(|previous| (difference, index) < previous)
                {
                    best = Some((difference, index));
                }
            }
        }
        best.map(|(_, index)| index)
    }

    pub fn project(&self, point: Point) -> Option<Point> {
        let face = self.locate(point)?;
        Some([point[0], point[1], height(&self.polygons[face], point)?])
    }
}

fn cell(point: Point) -> Option<[i64; 2]> {
    let x = (point[0] / CELL_SIZE).floor();
    let y = (point[1] / CELL_SIZE).floor();
    [x, y]
        .iter()
        .all(|&v| v >= i64::MIN as f64 && v < i64::MAX as f64)
        .then_some([x as i64, y as i64])
}

fn point_key(point: Point) -> [u64; 3] {
    point.map(|value| if value == 0.0 { 0 } else { value.to_bits() })
}

pub(crate) fn valid_point(point: Point) -> bool {
    point.iter().all(|v| v.is_finite())
}
