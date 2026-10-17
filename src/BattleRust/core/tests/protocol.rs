use t7r_battle_core::protocol::{FrameDecoder, MAX_FRAME, ProtocolError, heartbeat, plain_frame};

#[test]
fn downlink_preserves_header_and_integer_byte_order() {
    let frame = plain_frame(0x1234, 0x0102_0304_0506_0708, &[0xaa, 0xbb]).unwrap();
    assert_eq!(
        frame,
        [
            0x55, 0x0e, 5, 0, 0, 0, 0, 12, 0, 0, 0, 13, 0, 0x12, 0x34, 1, 2, 3, 4, 5, 6, 7, 8,
            0xaa, 0xbb
        ]
    );
}

#[test]
fn split_and_coalesced_frames_are_emitted_once_in_order() {
    let first = plain_frame(4, 0, &[1, 2, 3]).unwrap();
    let second = plain_frame(29, 0, &[0, 2]).unwrap();
    for split in 0..first.len() {
        let mut decoder = FrameDecoder::default();
        decoder.push(&first[..split]).unwrap();
        assert_eq!(decoder.next_frame().unwrap(), None);
        decoder.push(&[&first[split..], &second].concat()).unwrap();
        assert_eq!(decoder.next_frame().unwrap(), Some(first.clone()));
        assert_eq!(decoder.next_frame().unwrap(), Some(second.clone()));
        assert_eq!(decoder.next_frame().unwrap(), None);
        assert_eq!(decoder.buffered_len(), 0);
    }
}

#[test]
fn zero_header_auth_waits_for_the_complete_variable_header() {
    let mut auth = vec![0; 31];
    auth[..3].copy_from_slice(&[0x55, 0x0e, 3]);
    auth[23] = 3;
    auth[28] = 2;
    let mut decoder = FrameDecoder::default();
    decoder.push(&auth[..29]).unwrap();
    assert_eq!(decoder.next_frame().unwrap(), None);
    decoder.push(&auth[29..]).unwrap();
    assert_eq!(decoder.next_frame().unwrap(), Some(auth));
}

#[test]
fn invalid_headers_and_overflow_are_errors_not_partial_frames() {
    let valid = plain_frame(1, 0, &[]).unwrap();
    for index in [0, 1, 7] {
        let mut data = valid.clone();
        data[index] = 0;
        let mut decoder = FrameDecoder::default();
        decoder.push(&data).unwrap();
        assert!(decoder.next_frame().is_err());
    }
    let mut data = valid;
    data[8..12].copy_from_slice(&u32::MAX.to_be_bytes());
    let mut decoder = FrameDecoder::default();
    decoder.push(&data).unwrap();
    assert_eq!(decoder.next_frame(), Err(ProtocolError::InvalidLength));
    assert!(plain_frame(1, 0, &vec![0; MAX_FRAME - 22]).is_err());
    assert_eq!(
        FrameDecoder::default().push(&vec![0; MAX_FRAME * 2 + 1]),
        Err(ProtocolError::BufferLimit)
    );
}

#[test]
fn heartbeat_echoes_the_original_twelve_timestamp_bytes() {
    let body = [0, 1, 0, 0, 0, 0, 7, 0x5b, 0xcd, 0x15, 0, 0, 0, 7, 0, 8];
    assert_eq!(
        heartbeat(&body).unwrap(),
        [0, 2, 0, 0, 0, 0, 7, 0x5b, 0xcd, 0x15, 0, 0, 0, 7]
    );
    assert!(heartbeat(&body[..15]).is_err());
    let mut wrong = body;
    wrong[1] = 2;
    assert!(heartbeat(&wrong).is_err());
}

#[test]
fn method3_padding_checks_every_remainder_and_rejects_bad_markers() {
    use t7r_battle_core::protocol::strip_method3_padding;
    for length in 1..=64 {
        let remainder = length % 16;
        let padding = (if remainder <= 10 { 16 } else { 32 }) - remainder;
        let mut clear = vec![7; length + padding];
        clear[length + padding - 6..length + padding - 1].copy_from_slice(b"tsf4g");
        clear[length + padding - 1] = padding as u8;
        let mut corrupt = clear.clone();
        corrupt[length + padding - 2] ^= 1;
        assert_eq!(
            strip_method3_padding(&mut corrupt),
            Err(ProtocolError::InvalidPadding)
        );
        let mut corrupt = clear.clone();
        corrupt[length + padding - 1] = 5;
        assert_eq!(
            strip_method3_padding(&mut corrupt),
            Err(ProtocolError::InvalidPadding)
        );
        strip_method3_padding(&mut clear).unwrap();
        assert_eq!(clear, vec![7; length]);
    }
    for length in 0..16 {
        assert!(strip_method3_padding(&mut vec![0; length]).is_err());
    }
}

#[test]
fn ciphertext_and_plaintext_parsing_require_complete_frames() {
    use t7r_battle_core::protocol::{Message, frame_command, method3_ciphertext};
    for length in 0..14 {
        assert!(Message::from_plaintext(&vec![0; length]).is_err());
    }
    let mut frame = plain_frame(4, 100, &[0; 16]).unwrap();
    assert!(method3_ciphertext(&frame).is_err());
    frame.pop();
    assert!(frame_command(&frame).is_err());
    assert!(frame_command(&[]).is_err());
}
