from pathlib import Path
import struct

from Business.scripts import contracts
from Business.scripts.codec import protocol


CORPUS = Path(__file__).resolve().parents[1] / 'fixtures/protocol/gateway-downlink-v1.txt'


def vectors():
    messages = [
        ('sync-login', 44, protocol.encode_sync_login_response(
            protocol.SyncLoginResponse(10000, 1, 0, 0))),
        ('instance-update', 10, contracts.instanceInfo(5000)),
        ('camp-choice', 35, struct.pack('>Hiiii', 2, 0, 1, 0, 0)),
    ]
    # Server::commit keeps the outer server_time zero. Body clocks are independent.
    return [(name, command, protocol.encode_plain_tpdu_downlink(
        protocol.encode_raw_application_payload(protocol.encode_sh_package(command, 0, body))))
        for name, command, body in messages]


def test_gateway_downlink_frames_match_original_runtime_envelope():
    actual = [tuple(line.split()) for line in CORPUS.read_text(encoding='ascii').splitlines()
              if line and not line.startswith('#')]
    assert actual == [(name, str(command), frame.hex()) for name, command, frame in vectors()]
