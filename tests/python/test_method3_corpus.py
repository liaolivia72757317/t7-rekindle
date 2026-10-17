from pathlib import Path

import pytest

from Business.scripts.codec.method3 import (
    decode_method3_uplink_frame, encode_method3_payload, method3_padding_length,
)


CORPUS = Path(__file__).resolve().parents[1] / 'fixtures/protocol/method3-v1.txt'
ROWS = [tuple(bytes.fromhex(item) for item in line.split())
        for line in CORPUS.read_text(encoding='ascii').splitlines() if not line.startswith('#')]


@pytest.mark.parametrize('plaintext,frame', ROWS)
def test_shared_method3_vectors_match_python_reference(plaintext, frame):
    padding = bytes(range(method3_padding_length(len(plaintext)) - 6))
    assert encode_method3_payload(plaintext, bytes(16), random_padding=padding) == frame[12:]
    actual = decode_method3_uplink_frame(frame, bytes(16))
    assert actual.sequence == int.from_bytes(plaintext[:4], 'big')
    assert actual.command_id == int.from_bytes(plaintext[4:6], 'big')
    assert actual.server_time_ms == int.from_bytes(plaintext[6:14], 'big')
    assert actual.body == plaintext[14:]
