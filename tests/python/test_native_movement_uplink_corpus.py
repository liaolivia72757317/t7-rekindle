from pathlib import Path

from Business.scripts import controls
from Business.scripts.codec.method3 import encode_method3_payload, method3_padding_length
from Business.scripts.codec.protocol import encode_sh_package
from test_runtime_movement import make_runtime_flow


ROWS = Path(__file__).resolve().parents[1] / "fixtures/protocol/native-movement-uplink-v1.txt"


def test_native_movement_reports_match_original_client_frame_and_business_behavior():
    flow = make_runtime_flow()
    names = []
    for line in ROWS.read_text(encoding="ascii").splitlines():
        name, sequence, command, body_hex, frame_hex = line.split()
        sequence, command = int(sequence), int(command)
        body = bytes.fromhex(body_hex)
        clear = sequence.to_bytes(4, "big") + encode_sh_package(command, 0, body)
        cipher = encode_method3_payload(clear, bytes(16),
                                        random_padding=bytes(method3_padding_length(len(clear)) - 6))
        expected = bytes.fromhex("550e00040000000c") + len(cipher).to_bytes(4, "big") + cipher
        assert expected.hex() == frame_hex
        if command == 2:
            assert controls.message(flow, command, int.from_bytes(body[:2], "big"), body)
        names.append(name)
    assert names == ["move-key", "move-heading", "crouch-key", "instance-refresh"]
    assert flow.session["ground"]["position"] == [10., 20., 30.]
    assert flow.session["ground"]["heading"] == 90
    assert flow.session["ground"]["crouched"] is True
    assert flow.result["send"] == []
