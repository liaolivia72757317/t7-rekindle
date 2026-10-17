#![cfg(windows)]

use t7r_battle_core::protocol::plain_frame;
use t7r_battle_runtime::client_codec::{ClientCodec, ClientEvent};

fn hex(text: &str) -> Vec<u8> {
    text.as_bytes()
        .chunks_exact(2)
        .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
        .collect()
}

fn auth() -> Vec<u8> {
    let mut frame = vec![0; 29];
    frame[..3].copy_from_slice(&[0x55, 0x0e, 3]);
    frame[23] = 3;
    frame
}

fn authenticated() -> ClientCodec {
    let mut codec = ClientCodec::default();
    let ClientEvent::Authenticated { reply } = codec.receive(&auth()).unwrap() else {
        panic!("expected authentication");
    };
    assert_eq!(
        reply.as_slice(),
        hex(
            "550e01000000003000000000000300207aca0fd9bcd6ec7c9f97466616e6a2826f4220bc6fb86f56960c05cd30dbe3f8"
        )
    );
    codec
}

#[test]
fn shared_method3_vectors_match_python_and_native_reference() {
    let mut codec = authenticated();
    let corpus = include_str!("../../../../tests/fixtures/protocol/method3-v1.txt");
    let mut count = 0;
    for line in corpus.lines().filter(|line| !line.starts_with('#')) {
        let (expected, wire) = line.split_once(' ').unwrap();
        let expected = hex(expected);
        let ClientEvent::Message(message) = codec.receive(&hex(wire)).unwrap() else {
            panic!("expected application message");
        };
        assert_eq!(
            message.sequence,
            u32::from_be_bytes(expected[..4].try_into().unwrap())
        );
        assert_eq!(
            message.command,
            u16::from_be_bytes(expected[4..6].try_into().unwrap())
        );
        assert_eq!(
            message.server_time_ms,
            u64::from_be_bytes(expected[6..14].try_into().unwrap())
        );
        assert_eq!(message.body, expected[14..]);
        count += 1;
    }
    assert_eq!(count, 32);
}

#[test]
fn invalid_order_and_malformed_ciphertext_terminate_the_decoder() {
    let mut codec = ClientCodec::default();
    assert!(codec.receive(&plain_frame(1, 0, &[]).unwrap()).is_err());
    assert!(codec.receive(&auth()).is_err());
    for frame in [
        auth(),
        vec![0; 28],
        hex("550e00040000000c0000001000000000000000000000000000000000"),
    ] {
        let mut codec = authenticated();
        assert!(codec.receive(&frame).is_err());
        assert!(codec.receive(&auth()).is_err());
    }
}

#[test]
fn close_is_terminal_and_never_becomes_a_business_message() {
    let mut codec = authenticated();
    let close = hex("550e0d040000000c0000000400000001");
    assert!(matches!(
        codec.receive(&close).unwrap(),
        ClientEvent::Closed
    ));
    assert!(codec.receive(&auth()).is_err());
}
