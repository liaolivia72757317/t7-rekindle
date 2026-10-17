use crate::navigation::Point;
pub(crate) use crate::navigation::geometry::{
    compensated_sum as sum, distance, distance_xy as horizontal,
};
pub(crate) fn facing(a: Point, b: Point, heading: f64) -> bool {
    let d = horizontal(a, b);
    d < 0.01
        || ((b[0] - a[0]) * heading.to_radians().cos() - (b[1] - a[1]) * heading.to_radians().sin())
            / d
            >= 0.5
}
pub(crate) fn point_at(target: Point, angle: f64, radius: f64) -> Point {
    [
        target[0] + angle.cos() * radius,
        target[1] + angle.sin() * radius,
        target[2],
    ]
}
pub(crate) fn length(points: &[Point], xy: bool) -> f64 {
    sum(points.windows(2).map(|p| {
        if xy {
            horizontal(p[0], p[1])
        } else {
            distance(p[0], p[1])
        }
    }))
}
