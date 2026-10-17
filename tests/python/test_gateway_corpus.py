from pathlib import Path
import struct

from Business.scripts import contracts
from Business.scripts.codec import protocol, login_flow, room_flow
from Business.scripts.codec.method3 import encode_method3_payload, method3_padding_length


CORPUS = Path(__file__).resolve().parents[1] / 'fixtures/protocol/gateway-v1.txt'


def vectors():
    def uplink(command, body):
        plain = struct.pack('>IHQ', 1, command, 0) + body
        padding = bytes(range(method3_padding_length(len(plain)) - 6))
        encoded = encode_method3_payload(plain, bytes(16), random_padding=padding)
        return bytes.fromhex('550e00040000000c') + struct.pack('>I', len(encoded)) + encoded

    identity = protocol.MinimalLoginIdentity(10000, '测试玩家'.encode('gbk'), user_image_id=7, level=100)
    heartbeat = struct.pack('>HIQH', 1, 123, 456, 0)
    return [
        ('login', 1, b'', protocol.encode_minimal_login_success(identity)),
        ('sync', 0x2c, b'', protocol.encode_sync_login_response(protocol.SyncLoginResponse(10000, 1, 0, 0))),
        ('version', 1, b'', protocol.encode_version_check_response(0)),
        ('heartbeat', 0x1d, uplink(0x1d, heartbeat), contracts.heartbeat(heartbeat)),
        ('custom', 1, uplink(1, bytes.fromhex('006800')), login_flow.encode_empty_custom_data_response()),
        ('governor', 0x42, uplink(0x42, struct.pack('>Hi', 0x30, 3)), login_flow.encode_empty_country_governor_response(3)),
        ('reconnect', 0x1e, uplink(0x1e, bytes.fromhex('000a00')), room_flow.encode_room_reconnect_response(result=1)),
    ]


def test_gateway_vectors_match_existing_business_codecs():
    actual = [tuple(line.split()) for line in CORPUS.read_text(encoding='ascii').splitlines()
              if line and not line.startswith('#')]
    expected = [(name, str(command), incoming.hex() or '-', outgoing.hex())
                for name, command, incoming, outgoing in vectors()]
    assert actual == expected
