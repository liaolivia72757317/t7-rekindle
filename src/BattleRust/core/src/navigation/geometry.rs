use super::Point;

pub const EPSILON: f64 = 1e-5;
pub const DEFAULT_RADIUS: f64 = 0.35;

pub(crate) fn cross(a: Point, b: Point, c: Point) -> f64 {
    (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
}

pub(crate) fn distance_xy(a: Point, b: Point) -> f64 {
    norm([a[0] - b[0], a[1] - b[1]])
}

pub(crate) fn distance(a: Point, b: Point) -> f64 {
    norm([a[0] - b[0], a[1] - b[1], a[2] - b[2]])
}

pub(crate) fn compensated_sum(values: impl Iterator<Item = f64>) -> f64 {
    let (mut sum, mut error) = (0.0_f64, 0.0_f64);
    for value in values {
        let next = sum + value;
        error += if sum.abs() >= value.abs() {
            (sum - next) + value
        } else {
            (value - next) + sum
        };
        sum = next;
    }
    if error != 0.0 && error.is_finite() {
        sum + error
    } else {
        sum
    }
}

// Compensated squares plus a corrected root preserve math.dist ordering at ties.
// A chained platform hypot can differ by one ulp and select a different respawn.
fn norm<const N: usize>(values: [f64; N]) -> f64 {
    let values = values.map(f64::abs);
    let maximum = values.iter().copied().fold(0.0, f64::max);
    if maximum == 0.0 || maximum.is_infinite() {
        return maximum;
    }
    if maximum < f64::MIN_POSITIVE {
        return f64::MIN_POSITIVE * norm(values.map(|v| v / f64::MIN_POSITIVE));
    }
    let exponent = ((maximum.to_bits() >> 52) & 0x7ff) as i32 - 1022;
    let scale = if exponent <= 1022 {
        f64::from_bits(((1023 - exponent) as u64) << 52)
    } else {
        f64::from_bits(1 << (1074 - exponent))
    };
    let (mut sum, mut product_error, mut sum_error) = (1.0, 0.0, 0.0);
    for value in values {
        let value = value * scale;
        let square = value * value;
        product_error += value.mul_add(value, -square);
        let next = sum + square;
        sum_error += (sum - next) + square;
        sum = next;
    }
    let root = (sum - 1.0 + (product_error + sum_error)).sqrt();
    let square = -root * root;
    product_error += (-root).mul_add(root, -square);
    let next = sum + square;
    sum_error += (sum - next) + square;
    let residual = next - 1.0 + (product_error + sum_error);
    (root + residual / (2.0 * root)) / scale
}

pub(crate) fn inside(polygon: &[Point], point: Point) -> bool {
    polygon
        .iter()
        .zip(polygon.iter().cycle().skip(1))
        .all(|(&a, &b)| cross(a, b, point) >= -EPSILON)
}

pub(crate) fn height(polygon: &[Point], point: Point) -> Option<f64> {
    let a = *polygon.first()?;
    for pair in polygon[1..].windows(2) {
        let (b, c) = (pair[0], pair[1]);
        let area = cross(a, b, c);
        if area.abs() <= EPSILON {
            continue;
        }
        let (u, v) = (cross(a, point, c), cross(a, b, point));
        if u >= -EPSILON && v >= -EPSILON && u + v <= area + EPSILON {
            return Some(a[2] + (u * (b[2] - a[2]) + v * (c[2] - a[2])) / area);
        }
    }
    None
}

pub(crate) fn interpolate(a: Point, b: Point, fraction: f64) -> Point {
    std::array::from_fn(|i| a[i] + (b[i] - a[i]) * fraction)
}

pub(crate) fn compare_points(a: &Point, b: &Point) -> std::cmp::Ordering {
    // Coordinates are finite; numeric comparison keeps -0.0 equal to 0.0.
    (0..3)
        .map(|i| a[i].partial_cmp(&b[i]).expect("finite coordinate"))
        .find(|order| !order.is_eq())
        .unwrap_or(std::cmp::Ordering::Equal)
}

pub(crate) fn crossing(a: Point, b: Point, portal: (Point, Point)) -> Option<Point> {
    let (c, d) = portal;
    let (dx, dy) = (b[0] - a[0], b[1] - a[1]);
    let (ex, ey) = (d[0] - c[0], d[1] - c[1]);
    let determinant = dx * ey - dy * ex;
    if determinant.abs() <= EPSILON {
        return [a, b].into_iter().find(|&p| {
            cross(c, d, p).abs() <= EPSILON
                && (p[0] - c[0]) * (p[0] - d[0]) + (p[1] - c[1]) * (p[1] - d[1]) <= EPSILON
        });
    }
    let t = ((c[0] - a[0]) * ey - (c[1] - a[1]) * ex) / determinant;
    let u = ((c[0] - a[0]) * dy - (c[1] - a[1]) * dx) / determinant;
    if (-EPSILON..=1.0 + EPSILON).contains(&t) && (-EPSILON..=1.0 + EPSILON).contains(&u) {
        Some([a[0] + t * dx, a[1] + t * dy, 0.0])
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::{compensated_sum, norm};

    #[test]
    fn distance_rounding_matches_python_at_sort_ties_and_numeric_extremes() {
        for (input, expected_bits) in [
            (
                [0.7071067811865474, -0.7071067811865426],
                0x3fefffffffffffdf,
            ),
            (
                [-0.7071067811865475, 0.7071067811865461],
                0x3feffffffffffff7,
            ),
            ([1e308, 1e308], 0x7fe92c80954c51f5),
            ([1e-310, 1e-310], 0x1a088b6bf34f),
            ([3.0, 4.0], 0x4014000000000000),
            ([1e-300, 1e300], 0x7e37e43c8800759c),
        ] {
            assert_eq!(norm(input).to_bits(), expected_bits, "{input:?}");
        }
        assert_eq!(norm([0.0, 0.0]), 0.0);
        assert_eq!(norm([f64::INFINITY, 1.0]), f64::INFINITY);
        assert_eq!(compensated_sum([1e16, 1.0, -1e16].into_iter()), 1.0);
    }
}
