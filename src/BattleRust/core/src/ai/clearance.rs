use super::{math, types::*};
use crate::navigation::{NavMesh, Point};

pub(crate) fn ground_goal(nav: &NavMesh, desired: Point) -> Option<Point> {
    let p = nav.project(desired)?;
    let margin = 0.351;
    if [(margin, 0.0), (-margin, 0.0), (0.0, margin), (0.0, -margin)]
        .iter()
        .any(|&(x, y)| nav.project([p[0] + x, p[1] + y, p[2]]).is_none())
    {
        return None;
    }
    Some(p)
}

// Original non-sliding body clearance is a route test, never a position integrator.
fn contact_fraction(start: Point, delta: Point, body: Point) -> Option<f64> {
    let [x, y, z] = std::array::from_fn(|i| start[i] - body[i]);
    let [dx, dy, dz] = delta;
    let squared = x * x + y * y;
    let radial = x * dx + y * dy;
    let horizontal = dx * dx + dy * dy;
    if squared < 0.7_f64.powi(2) - 1e-9 && z.abs() < 1.8 - 1e-9 {
        if radial > 1e-9
            || squared < 1e-9 && horizontal > 1e-9
            || horizontal < 1e-12 && (z + dz).abs() > z.abs() + 1e-9
        {
            return None;
        }
        let length = if squared > 1e-9 {
            math::horizontal([x, y, 0.0], [0.0; 3])
        } else {
            math::horizontal([dx, dy, 0.0], [0.0; 3])
        };
        return (length > 1e-9).then_some(0.0);
    }
    let (radial_in, radial_out) = if horizontal < 1e-12 {
        if squared >= 0.7_f64.powi(2) {
            return None;
        }
        (f64::NEG_INFINITY, f64::INFINITY)
    } else {
        let discriminant = radial * radial - horizontal * (squared - 0.7_f64.powi(2));
        if discriminant <= 0.0 {
            return None;
        }
        let root = discriminant.sqrt();
        ((-radial - root) / horizontal, (-radial + root) / horizontal)
    };
    let (vertical_in, vertical_out) = if dz.abs() < 1e-12 {
        if z.abs() >= 1.8 {
            return None;
        }
        (f64::NEG_INFINITY, f64::INFINITY)
    } else {
        let (a, b) = ((-1.8 - z) / dz, (1.8 - z) / dz);
        (a.min(b), a.max(b))
    };
    let enter = 0.0_f64.max(radial_in).max(vertical_in);
    let leave = 1.0_f64.min(radial_out).min(vertical_out);
    (enter < leave - 1e-9).then_some(enter)
}

pub(crate) fn clear_route(
    points: &[Point],
    height_offset: f64,
    world: &WorldObservation,
    actor_id: u32,
) -> bool {
    points.windows(2).all(|pair| {
        let mut a = pair[0];
        let mut b = pair[1];
        a[2] += height_offset;
        b[2] += height_offset;
        let distance = math::distance(a, b);
        if distance < 1e-9 {
            return true;
        }
        let delta = std::array::from_fn(|i| b[i] - a[i]);
        let fraction = world
            .actors
            .iter()
            .filter(|actor| actor.id != actor_id && actor.blocks_movement)
            .filter_map(|actor| contact_fraction(a, delta, actor.position))
            .min_by(f64::total_cmp);
        fraction.is_none_or(|fraction| {
            let fraction = (fraction - 0.001 / distance).max(0.0);
            let clipped = std::array::from_fn(|i| a[i] + delta[i] * fraction);
            math::distance(clipped, b) <= 0.01
        })
    })
}
