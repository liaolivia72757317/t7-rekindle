use std::io;

pub(super) fn accept(command: u16, body: &[u8]) -> io::Result<bool> {
    if command != 2 || body.len() < 2 {
        return Ok(false);
    }
    let selector = u16::from_be_bytes([body[0], body[1]]);
    match selector {
        63 => {
            if body.len() != 3 || body[2] > 1 {
                return Err(invalid("invalid native fast-run request"));
            }
            // FAST_RUN_REQ is a native movement report, not a combat dash intent.
            Ok(true)
        }
        3 => {
            if body.len() != 21 {
                return Err(invalid("invalid native heading report length"));
            }
            let heading = i16::from_be_bytes(body[7..9].try_into().expect("checked length"));
            if !(-180..=180).contains(&heading) || !finite_position(&body[9..21]) {
                return Err(invalid("invalid native heading report"));
            }
            Ok(true)
        }
        52 => {
            if body.len() != 24 {
                return Err(invalid("invalid native key report length"));
            }
            let category = i32::from_be_bytes(body[2..6].try_into().expect("checked length"));
            if !matches!(category, 1..=3) {
                return Ok(false);
            }
            if body[6..12].iter().any(|&key| key > 1)
                || category == 1 && !finite_position(&body[12..24])
            {
                return Err(invalid("invalid native key report"));
            }
            Ok(true)
        }
        _ => Ok(false),
    }
}

fn finite_position(bytes: &[u8]) -> bool {
    bytes
        .chunks_exact(4)
        .all(|axis| f32::from_be_bytes(axis.try_into().expect("four-byte axis")).is_finite())
}

fn invalid(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn key_report(category: i32, position: [f32; 3]) -> Vec<u8> {
        let mut body = 52_u16.to_be_bytes().to_vec();
        body.extend_from_slice(&category.to_be_bytes());
        body.extend_from_slice(&[1, 0, 0, 0, 0, 0]);
        for axis in position {
            body.extend_from_slice(&axis.to_be_bytes());
        }
        body
    }

    #[test]
    fn local_native_reports_are_validated_without_becoming_battle_motion() {
        let mut heading = 3_u16.to_be_bytes().to_vec();
        heading.extend_from_slice(&1_i32.to_be_bytes());
        heading.push(0);
        heading.extend_from_slice(&90_i16.to_be_bytes());
        for axis in [10_f32, 20., 30.] {
            heading.extend_from_slice(&axis.to_be_bytes());
        }
        assert!(accept(2, &heading).unwrap());
        assert!(accept(2, &key_report(1, [10., 20., 30.])).unwrap());
        assert!(accept(2, &key_report(2, [f32::NAN, f32::INFINITY, 0.])).unwrap());
        assert!(accept(2, &key_report(3, [f32::NAN, f32::INFINITY, 0.])).unwrap());
        assert!(!accept(2, &key_report(4, [0.; 3])).unwrap());
        assert!(!accept(3, &heading).unwrap());
        assert!(accept(2, &key_report(1, [f32::NAN, 0., 0.])).is_err());
        let mut bad_heading = heading.clone();
        bad_heading[7..9].copy_from_slice(&181_i16.to_be_bytes());
        assert!(accept(2, &bad_heading).is_err());
        let mut bad_keys = key_report(2, [0.; 3]);
        bad_keys[10] = 2;
        assert!(accept(2, &bad_keys).is_err());
        assert!(accept(2, &heading[..20]).is_err());
        assert!(accept(2, &[0, 63, 1]).unwrap());
        assert!(accept(2, &[0, 63, 0]).unwrap());
        assert!(accept(2, &[0, 63]).is_err());
        assert!(accept(2, &[0, 63, 0, 0]).is_err());
        assert!(accept(2, &[0, 63, 2]).is_err());
    }
}
