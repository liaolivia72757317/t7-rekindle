import struct
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "reference" / "Business" / "scripts"))

from codec import leave_flow  # noqa: E402
from codec.method3 import encode_method3_payload, method3_padding_length  # noqa: E402


def test_original_leave_wire_matches_frozen_corpus():
    rows = dict(
        line.split(" ", 1)
        for line in (ROOT / "tests" / "fixtures" / "protocol" / "leave-wire-v1.txt")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    assert bytes.fromhex(rows["sync-logout-request"]) == struct.pack(">Hb", 2, 1)
    assert bytes.fromhex(rows["instance-leave"]) == leave_flow.encode_instance_leave_instance(
        server_time_ms=5000
    )
    assert bytes.fromhex(rows["sync-logout-response"]) == leave_flow.encode_sync_logout_response(
        server_time_ms=5000, back_data=1
    )
    assert bytes.fromhex(rows["user-group-quit-request"]) == struct.pack(">HQi", 1, 1, 3)
    assert bytes.fromhex(rows["user-group-quit-response"]) == leave_flow.encode_user_group_quit_response(
        user_group_guid=1, user_group_type=3
    )


def test_original_leave_uplink_uses_method3_frame():
    bodies = dict(
        line.split(" ", 1)
        for line in (ROOT / "tests" / "fixtures" / "protocol" / "leave-wire-v1.txt")
        .read_text(encoding="ascii")
        .splitlines()
    )
    rows = [
        line.split()
        for line in (ROOT / "tests" / "fixtures" / "protocol" / "leave-uplink-v1.txt")
        .read_text(encoding="ascii")
        .splitlines()
    ]
    expected = [
        ("sync-logout", 6, 44, bytes.fromhex(bodies["sync-logout-request"])),
        ("user-group-quit", 7, 55, bytes.fromhex(bodies["user-group-quit-request"])),
    ]
    for row, (name, sequence, command, body) in zip(rows, expected, strict=True):
        clear = struct.pack(">IHQ", sequence, command, 0) + body
        padding = bytes(method3_padding_length(len(clear)) - 6)
        cipher = encode_method3_payload(clear, bytes(16), random_padding=padding)
        frame = bytes.fromhex("550e00040000000c") + struct.pack(">I", len(cipher)) + cipher
        assert row == [name, str(sequence), str(command), frame.hex()]
