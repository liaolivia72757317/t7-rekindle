"""Test-only Python oracle for the Rust USER_INFO adapter."""
from pathlib import Path
import struct
from Business.scripts.codec import title_flow as wire
from Business.scripts.codec.method3 import encode_method3_payload, method3_padding_length

CORPUS = Path(__file__).resolve().parents[1] / "fixtures/protocol/titles-v1.txt"


def vectors():
    rows = []
    for name, selector, value in [("query-user", 1, 10000), ("query-stats", 3, 10000), ("equip", 5, 1007)]:
        payload = struct.pack(">IHQHI", 1, 31, 0, selector, value)
        encoded = encode_method3_payload(payload, bytes(16),
            random_padding=bytes(range(method3_padding_length(len(payload)) - 6)))
        incoming = bytes.fromhex("550e00040000000c") + struct.pack(">I", len(encoded)) + encoded
        rows.append((name, incoming.hex()))
    rows += [("user", wire.userInfo("测试玩家".encode("gbk"), 0).hex()),
             ("stats", wire.statInfo([1007], 0).hex()),
             ("equipped-user", wire.userInfo("测试玩家".encode("gbk"), 1007).hex()),
             ("equipped-stats", wire.statInfo([1007], 1007).hex()),
             ("accepted", wire.setResult(True).hex()), ("rejected", wire.setResult(False).hex())]
    return rows


def test_title_vectors_match_original_codec():
    assert [tuple(line.split()) for line in CORPUS.read_text(encoding="ascii").splitlines()] == vectors()
