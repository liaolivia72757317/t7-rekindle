from pathlib import Path
import struct

from Business.scripts import app, scene
from Business.scripts.codec.method3 import encode_method3_payload, method3_padding_length


CORPUS = Path(__file__).resolve().parents[1] / 'fixtures/protocol/gateway-clock-v1.txt'


def vectors():
    def uplink(sequence, command, body):
        clear = struct.pack('>IHQ', sequence, command, 0) + body
        cipher = encode_method3_payload(clear, bytes(16),
                                       random_padding=bytes(method3_padding_length(len(clear)) - 6))
        return bytes.fromhex('550e00040000000c') + struct.pack('>I', len(cipher)) + cipher

    request = b'\0\x09\0'
    state = app.createState({})
    state['sessions']['1'] = {'role': 'instance'}
    flow = app.Flow({'connection': 1}, state, {'nowMs': 5000})
    app.handleMessage(flow, {'command': 8, 'body': request})
    assert scene.timer(flow, 'time-sync') is True
    assert flow.result['timers'][0]['delayMs'] == 2000
    reply, periodic = flow.result['send']
    flow.result['send'].clear()
    app.handleMessage(flow, {'command': 8, 'body': request})
    assert not flow.result['send']
    heartbeat = struct.pack('>HIQH', 1, 123, 456, 0)
    app.handleMessage(flow, {'command': 29, 'body': heartbeat})
    return [('unify', 8, uplink(1, 8, request), reply['body']),
            ('periodic', 8, b'', periodic['body']),
            ('unify-throttled', 8, uplink(2, 8, request), b''),
            ('heartbeat', 29, uplink(3, 29, heartbeat), flow.result['send'][0]['body'])]


def test_clock_vectors_match_original_time_handlers():
    actual = [tuple(line.split()) for line in CORPUS.read_text(encoding='ascii').splitlines()]
    assert actual == [(name, str(command), uplink.hex() or '-', body.hex() or '-')
                      for name, command, uplink, body in vectors()]
