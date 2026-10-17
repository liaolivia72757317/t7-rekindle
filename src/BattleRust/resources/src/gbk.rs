use std::io;

fn valid_pair(lead: u8, trail: u8) -> bool {
    let ranges: &[(u8, u8)] = match lead {
        0xa1 | 0xa3 => &[(0xa1, 0xfe)],
        0xa2 => &[(0xa1, 0xaa), (0xb1, 0xe2), (0xe5, 0xee), (0xf1, 0xfc)],
        0xa4 => &[(0xa1, 0xf3)],
        0xa5 => &[(0xa1, 0xf6)],
        0xa6 => &[
            (0xa1, 0xb8),
            (0xc1, 0xd8),
            (0xe0, 0xeb),
            (0xee, 0xf2),
            (0xf4, 0xf5),
        ],
        0xa7 => &[(0xa1, 0xc1), (0xd1, 0xf1)],
        0xa8 => &[
            (0x40, 0x7e),
            (0x80, 0x95),
            (0xa1, 0xbb),
            (0xbd, 0xbe),
            (0xc0, 0xc0),
            (0xc5, 0xe9),
        ],
        0xa9 => &[
            (0x40, 0x57),
            (0x59, 0x5a),
            (0x5c, 0x5c),
            (0x60, 0x7e),
            (0x80, 0x88),
            (0x96, 0x96),
            (0xa4, 0xef),
        ],
        0xaa..=0xaf | 0xf8..=0xfd => &[(0x40, 0x7e), (0x80, 0xa0)],
        0xd7 => &[(0x40, 0x7e), (0x80, 0xf9)],
        0xfe => &[(0x40, 0x4f)],
        0x81..=0xfd => &[(0x40, 0x7e), (0x80, 0xfe)],
        _ => &[],
    };
    ranges
        .iter()
        .any(|&(start, end)| (start..=end).contains(&trail))
}

pub(crate) fn decode(bytes: &[u8]) -> io::Result<String> {
    // WHATWG GBK also accepts CP936/GB18030 extensions rejected by the original reader.
    let mut position = 0;
    while position < bytes.len() {
        let lead = bytes[position];
        position += 1;
        if lead < 0x80 {
            continue;
        }
        if position == bytes.len() || !valid_pair(lead, bytes[position]) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "invalid GBK text",
            ));
        }
        position += 1;
    }
    let (text, errors) = encoding_rs::GBK.decode_without_bom_handling(bytes);
    if errors {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "invalid GBK text",
        ));
    }
    Ok(text.into_owned())
}

#[cfg(test)]
mod tests {
    #[test]
    fn matches_original_reader_for_every_double_byte_sequence() {
        let fixture = include_str!("../../../../tests/fixtures/resources/gbk-v1.txt");
        for line in fixture.lines().filter(|line| !line.starts_with('#')) {
            let (lead, expected) = line.split_once('|').unwrap();
            let lead = u8::from_str_radix(lead, 16).unwrap();
            assert_eq!(expected.chars().count(), 191);
            for (trail, expected) in (0x40..=0xfe).zip(expected.chars()) {
                let result = super::decode(&[lead, trail]);
                if expected == '\u{fffd}' {
                    assert!(result.is_err(), "{lead:02x}{trail:02x}");
                } else {
                    assert_eq!(
                        result.unwrap(),
                        expected.to_string(),
                        "{lead:02x}{trail:02x}"
                    );
                }
            }
        }
        for byte in 0..=255 {
            assert_eq!(super::decode(&[byte]).is_ok(), byte < 128);
        }
    }
}
