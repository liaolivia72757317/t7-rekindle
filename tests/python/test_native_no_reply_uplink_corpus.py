from pathlib import Path

from Business.scripts import battle, controls, scene
from Business.scripts.codec.method3 import encode_method3_payload, method3_padding_length
from Business.scripts.codec.protocol import encode_sh_package
from test_runtime_movement import make_runtime_flow


ROWS = Path(__file__).resolve().parents[1] / "fixtures/protocol/native-no-reply-uplink-v1.txt"


def test_original_business_ignores_observed_extra_instance_reports_without_response():
    flow = make_runtime_flow()
    for line in ROWS.read_text(encoding="ascii").splitlines():
        name, sequence, command, body_hex, frame_hex = line.split()
        sequence, command = int(sequence), int(command)
        body = bytes.fromhex(body_hex)
        plain = sequence.to_bytes(4, "big") + encode_sh_package(command, 0, body)
        cipher = encode_method3_payload(plain, bytes(16),
                                        random_padding=bytes(method3_padding_length(len(plain)) - 6))
        assert (bytes.fromhex("550e00040000000c") + len(cipher).to_bytes(4, "big") + cipher).hex() == frame_hex
        if name == "move-extra":
            assert controls.message(flow, command, 62, body) is False
        elif name in ("fast-run-start", "fast-run-stop"):
            assert body in (b"\0\x3f\1", b"\0\x3f\0")
            assert controls.message(flow, command, 63, body) is False
        elif name in ("battle-extra", "unknown-action"):
            assert battle.message(flow, command, int.from_bytes(body[:2], "big"), body) is False
        elif name == "scene-extra":
            assert scene.message(flow, command, 3, body) is False
    assert flow.result["send"] == []
