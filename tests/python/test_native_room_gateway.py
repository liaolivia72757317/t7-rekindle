"""Opt-in Windows transport test against a running development Rust service."""
import importlib.util
import os
from pathlib import Path
import queue
import socket
import struct
import subprocess
import sys
import threading
import time
from dataclasses import replace

import pytest

from Business.scripts.codec import custom_room as codec, method3, protocol

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(sys.platform != "win32" or not os.environ.get("T7_GATEWAY_TEST_PORT"),
                                reason="requires Windows native harness and a development Rust service")


class NativeClient:
    def __init__(self, package, writable, account, *, scene=False, gateway_config=None):
        writable.mkdir()
        config = gateway_config or dict(serverHost='127.0.0.1', serverPort=int(os.environ['T7_GATEWAY_TEST_PORT']), secure='false', accountId=account)
        (writable / "custom-room.ini").write_text("".join(f"{key}={value}\n" for key, value in config.items()), encoding="utf-8")
        binary = ROOT / "artifacts/native/bin/x64/Release/T7.RuntimeTests.exe"
        environment = {**os.environ, "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")}
        self.process = subprocess.Popen([str(binary), "--room-scene" if scene else "--room-gateway", str(package), str(writable)],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, encoding='utf-8', errors='replace', env=environment)
        self.socket = None
        self.sequence = 0
        self.inbox = []
        self.buffer = bytearray()
        ready = queue.Queue()
        reader = threading.Thread(target=lambda: ready.put(self.process.stdout.readline()), daemon=True)
        reader.start()
        try:
            line = ready.get(timeout=15).strip()
            assert line.startswith("READY "), line
            ports = [int(port) for port in line[6:].split()]
            self.instance_port = ports[1] if scene else None
            self.socket = socket.create_connection(("127.0.0.1", ports[0]), timeout=10)
            self.socket.sendall(bytes.fromhex("550e030000000000000000000000000000000000000000030000000000"))
            assert self.frame()[2] == 1
            login = self.receive(command=1)
            self.user_id = protocol.decode_minimal_login_success(login).user_id
            sync = self.receive(command=0x2c)
            assert protocol.decode_sync_login_response(sync).user_id == self.user_id
        except BaseException:
            self.close()
            raise

    def frame(self):
        while True:
            if len(self.buffer) >= 12:
                header, size = struct.unpack_from(">II", self.buffer, 4)
                if len(self.buffer) >= header + size:
                    value = bytes(self.buffer[:header + size])
                    del self.buffer[:header + size]
                    return value
            data = self.socket.recv(65536)
            assert data, "native gateway disconnected"
            self.buffer.extend(data)

    def receive(self, selector=None, command=59):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            for index, (actual_command, body) in enumerate(self.inbox):
                if actual_command == command and (selector is None or int.from_bytes(body[:2]) == selector):
                    self.inbox.pop(index)
                    return body
            frame = self.frame()
            assert frame[2] == 5
            actual_command = int.from_bytes(frame[13:15])
            self.inbox.append((actual_command, frame[23:]))
        raise AssertionError("original protocol response timeout")

    def send(self, body, command=59):
        self.sequence += 1
        clear = struct.pack(">IHQ", self.sequence, command, 0) + body
        encrypted = method3.encode_method3_payload(clear, b"\0" * 16)
        self.socket.sendall(bytes.fromhex("550e0004") + struct.pack(">II", 12, len(encrypted)) + encrypted)

    def close(self):
        if self.socket:
            self.socket.close()
            self.socket = None
        try:
            output, error = self.process.communicate("stop\n", timeout=15)
            assert self.process.returncode == 0, error
            assert "STOPPED" in output, output
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.communicate()
            raise AssertionError("native gateway failed to stop")


def string(value):
    return struct.pack(">I", len(value) + 1) + value + b"\0"


def test_two_original_instances_receive_peer_native_positions_without_local_echo(tmp_path):
    spec = importlib.util.spec_from_file_location('build_fixture', ROOT / 'scripts/build.py')
    build = importlib.util.module_from_spec(spec); spec.loader.exec_module(build)
    package = build.prepare_runtime_fixture(Path(sys.executable).parent)
    clients, instances = [], []
    try:
        a = NativeClient(package, tmp_path / 'motion-a', 'motion-a-' + str(os.getpid()), scene=True); clients.append(a)
        b = NativeClient(package, tmp_path / 'motion-b', 'motion-b-' + str(os.getpid()), scene=True); clients.append(b)
        a.send(struct.pack('>Hi', 2, 1003) + string(b'motion') + string(b'') + struct.pack('>i', 1))
        guid = struct.unpack_from('>Q', a.receive(3), 6)[0]
        b.send(struct.pack('>HQ', 8, guid) + string(b'')); b.receive(9)
        b.send(struct.pack('>Hi', 18, 1)); b.receive(19)
        a.send(struct.pack('>Hb', 21, 0)); assert a.receive(22) == codec.encode_result(22, 0)
        epochs = []
        for client in clients:
            client.receive(9, command=0x1e)
            client.send(struct.pack('>HQi', 3, 1, 0), command=0x1e); client.receive(4, command=0x1e)
            actor = InstanceClient(client); instances.append(actor)
            assert protocol.decode_sync_login_response(actor.receive(command=0x2c)).user_id == client.user_id
            info = actor.receive(0x64, command=0xA)
            epochs.append(struct.unpack_from('>Q', info, 18)[0])
            actor.send(b'\0\3\0', command=0x23); actor.receive(4, command=0x23); actor.receive(command=0xE)
            actor.send(b'\0\x14\0', command=0x36)
            actor.send(struct.pack('>Hi', 0x32, 1), command=0x36); actor.receive(0x33, command=0x36)
            actor.send(struct.pack('>HHb', 0x64, 1, 0), command=0x36); actor.receive(0x65, command=0x36)
        for actor in instances:
            while struct.unpack_from('>i', actor.receive(0x67, command=0xA), 18)[0] != 4: pass
        from Business.scripts.codec import move_flow
        for index, client in enumerate(clients):
            client.process.stdin.write(f'world {epochs[index]} {10 + index} 20 0.5 {45 + index}\n')
            client.process.stdin.flush()
        for index, actor in enumerate(instances):
            peer = move_flow.decode_move_direct_bc(actor.receive(4, command=2))
            assert (peer.target_instance_id, peer.position, peer.direction_yaw) == (2, (float(11 - index), 20.0, .5), 46 - index)
        time.sleep(.1)
        a.process.stdin.write(f'world {epochs[0]} 10.5 20 1.5 90\n'); a.process.stdin.flush()
        corrections = []
        while True:
            update = move_flow.decode_move_direct_bc(instances[1].receive(4, command=2))
            corrections.append(update)
            if update.position == (10.5, 20.0, 1.5):
                break
            assert 10 <= update.position[0] < 10.5 and .5 <= update.position[2] < 1.5
        assert len(corrections) >= 2 and update.direction_yaw == 90
        assert [p.position[0] for p in corrections] == sorted(p.position[0] for p in corrections)
        assert not any(command == 2 and int.from_bytes(body[:2]) == 4 for command, body in instances[0].inbox)
        # Real native contact output crosses the existing gateway; Rust alone
        # awards HP and broadcasts recipient-specific feedback to both instances.
        from Business.scripts.codec import battle_flow
        for index, client in enumerate(clients):
            client.process.stdin.write(f'world {epochs[index]} {10 + index} 20 0.5 {180 * index}\n')
            client.process.stdin.flush()
        time.sleep(.2)
        instances[0].send(struct.pack('>HIib', 1, 0, 300560, 0), command=4)
        instances[0].send(struct.pack('>HIib', 1, 0, 300020, 0), command=4)
        while True:
            action = battle_flow.decode_battle_state_sync_simple(instances[0].receive(3, command=4))
            if action.state == 238:
                break
        b.process.stdin.write(f'world {epochs[1]} 11 20 0.5 180\n')
        b.process.stdin.flush()
        time.sleep(.1)
        a.process.stdin.write(f'contact {epochs[0]} 10 20 0.5 0 {action.seq_no} 238 2\n')
        a.process.stdin.flush()
        for index, actor in enumerate(instances):
            effect = battle_flow.decode_battle_hit_point_notify(actor.receive(27, command=4))
            assert effect.attacker_mid == (1 if index == 0 else 2)
            assert effect.target_mid == (2 if index == 0 else 1)
        hp = instances[1].receive(11, command=4)
        assert struct.unpack_from('>i', hp, 4)[0] == 80
        time.sleep(1.6)
        instances[1].send(struct.pack('>HIib', 1, 0, 300580, 0), command=4)
        while battle_flow.decode_battle_state_sync_simple(instances[1].receive(3, command=4)).state != 223:
            pass
        b.process.stdin.write(f'world {epochs[1]} 11 20 0.5 180\n')
        b.process.stdin.flush()
        time.sleep(.1)
        instances[0].send(struct.pack('>HIib', 1, 0, 300560, 0), command=4)
        instances[0].send(struct.pack('>HIib', 1, 0, 300020, 0), command=4)
        while True:
            action = battle_flow.decode_battle_state_sync_simple(instances[0].receive(3, command=4))
            if action.state == 238:
                break
        a.process.stdin.write(f'weapon-contact {epochs[0]} 10 20 0.5 0 {action.seq_no} 238 2\n')
        a.process.stdin.flush()
        for actor in instances:
            effect = battle_flow.decode_battle_hit_point_notify(actor.receive(27, command=4))
            assert effect.target_weapon_id == 1030411
            actor.receive(40, command=4)
        assert struct.unpack_from('>i', instances[1].receive(11, command=4), 4)[0] == 80
    finally:
        for actor in reversed(instances): actor.close()
        for client in reversed(clients): client.close()
        build.remove_tree(package)


class InstanceClient(NativeClient):
    def __init__(self, owner):
        self.sequence = 0
        self.inbox = []
        self.buffer = bytearray()
        self.socket = socket.create_connection(('127.0.0.1', owner.instance_port), timeout=10)
        self.socket.sendall(bytes.fromhex('550e030000000000000000000000000000000000000000030000000000'))
        assert self.frame()[2] == 1

    def close(self):
        self.socket.close()


def test_original_protocol_solo_start_melee_and_logout(tmp_path):
    spec = importlib.util.spec_from_file_location('build_fixture', ROOT / 'scripts/build.py')
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    package = build.prepare_runtime_fixture(Path(sys.executable).parent)
    owner = instance = None
    try:
        owner = NativeClient(package, tmp_path / 'solo', 'gateway-solo-' + str(os.getpid()), scene=True)
        owner.send(struct.pack('>Hi', 2, 1003) + string(b'solo') + string(b'') + struct.pack('>i', 1))
        assert struct.unpack_from('>I', owner.receive(3), 2)[0] == 0
        owner.send(struct.pack('>Hb', 21, 0))
        assert owner.receive(22) == codec.encode_result(22, 0)
        assigned = owner.receive(1)
        assert struct.unpack_from('>i', assigned, 2)[0] == 3
        assert owner.receive(9, command=0x1e) == struct.pack('>HQib', 9, 1, 1, 0)
        owner.send(struct.pack('>HQi', 3, 1, 0), command=0x1e)
        entered = owner.receive(4, command=0x1e)
        assert struct.pack('>i', 1003) in entered
        assert f'127.0.0.1:{owner.instance_port}'.encode() in entered
        instance = InstanceClient(owner)
        sync = instance.receive(command=0x2c)
        assert protocol.decode_sync_login_response(sync).user_id == owner.user_id
        instance.receive(0x64, command=0xA)
        instance.send(b'\0\3\0', command=0x23)
        instance.receive(4, command=0x23)
        instance.receive(command=0xE)
        instance.send(b'\0\x14\0', command=0x36)
        instance.send(struct.pack('>Hi', 0x32, 1), command=0x36)
        instance.receive(0x33, command=0x36)
        instance.send(struct.pack('>HHb', 0x64, 1, 0), command=0x36)
        instance.receive(0x65, command=0x36)
        phases = []
        while not phases or phases[-1] != 4:
            update = instance.receive(0x67, command=0xA)
            phases.append(struct.unpack_from('>i', update, 18)[0])
        assert 3 in phases and phases.index(3) < phases.index(4)
        from Business.scripts.codec import battle_flow
        assert battle_flow.decode_battle_state_sync_simple(instance.receive(3, command=4)).seq_no == 1
        instance.send(struct.pack('>HIib', 1, 0, 300560, 0), command=4)
        assert battle_flow.decode_battle_state_sync_simple(instance.receive(3, command=4)).state == 235
        instance.send(struct.pack('>HIib', 1, 0, 300020, 0), command=4)
        assert [battle_flow.decode_battle_state_sync_simple(instance.receive(3, command=4)).state
                for _ in range(3)] == [238, 239, 2]
        instance.send(struct.pack('>HIib', 1, 0, 300580, 0), command=4)
        assert [battle_flow.decode_battle_state_sync_simple(instance.receive(3, command=4)).state
                for _ in range(2)] == [305, 223]
        instance.send(struct.pack('>HIib', 1, 0, 300620, 0), command=4)
        assert battle_flow.decode_battle_state_sync_simple(instance.receive(3, command=4)).state == 2
        instance.send(struct.pack('>Hb', 2, 1), command=0x2c)
        instance.receive(3, command=0xA)
        assert instance.receive(3, command=0x2c)[-1] == 1
        dismissed = owner.receive(1)
        assert struct.unpack_from('>i', dismissed, 2)[0] == 2
        instance.close(); instance = None
        owner.send(struct.pack('>Hiii', 16, 1, 0, 20))
        assert owner.receive(17) == codec.encode_room_list((), start=0, total=0)
    finally:
        try:
            if instance is not None: instance.close()
            if owner is not None: owner.close()
        finally:
            build.remove_tree(package)


def test_two_native_original_protocol_connections_share_rust_rooms(tmp_path):
    spec = importlib.util.spec_from_file_location("build_fixture", ROOT / "scripts/build.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    package = build.prepare_runtime_fixture(Path(sys.executable).parent)
    clients = []
    try:
        account_suffix = str(os.getpid())
        owner = NativeClient(package, tmp_path / "owner", "gateway-a-" + account_suffix); clients.append(owner)
        peer = NativeClient(package, tmp_path / "peer", "gateway-b-" + account_suffix); clients.append(peer)
        assert owner.user_id != peer.user_id
        owner.send(struct.pack(">Hb", 23, 0))
        assert owner.receive(24) == codec.encode_channels((codec.ChannelInfo(1, "自定义房间".encode("gbk"), 0),))
        name = "共享测试".encode("gbk")
        map_id = int(os.environ.get("T7_GATEWAY_TEST_MAP", "1028"))
        owner.send(struct.pack(">Hi", 2, map_id) + string(name) + string(b"") + struct.pack(">i", 1))
        created = owner.receive(3)
        assert int.from_bytes(created[2:6]) == 0
        guid, number = struct.unpack_from(">QI", created, 6)
        info = codec.RoomInfo(guid, number, map_id, b"Player", name, 0, False, 1, 0, 0)
        member_a = codec.MemberInfo(owner.user_id, b"Player", 1, False, level=100)
        room = codec.FullRoomInfo(info, owner.user_id, (member_a,), 1)
        assert created == codec.encode_room_response(3, room)
        peer.send(struct.pack(">Hiii", 16, 1, 0, 20))
        listed = peer.receive(17)
        assert struct.unpack_from(">iiii", listed, 2)[1:] == (0, 1, 1)
        assert listed == codec.encode_room_list((info,), start=0, total=1)
        peer.send(struct.pack(">HiI", 6, 1, number))
        assert peer.receive(7) == codec.encode_get_response(info)
        peer.send(struct.pack(">HQ", 8, guid) + string(b"p"))
        assert peer.receive(9) == codec.encode_room_response(9, result=1)
        peer.send(struct.pack(">HQ", 8, guid) + string(b""))
        member_b = codec.MemberInfo(peer.user_id, b"Player", 2, False, level=100)
        joined_info = codec.RoomInfo(guid, number, map_id, b"Player", name, 0, False, 2, 0, 0)
        joined_room = codec.FullRoomInfo(joined_info, owner.user_id, (member_a, member_b), 1)
        assert peer.receive(9) == codec.encode_room_response(9, joined_room)
        assert owner.receive(1) == codec.encode_member_notify(4, member_b)
        assert owner.receive(1) == codec.encode_room_notify(joined_room)
        peer.send(struct.pack(">Hi", 18, 1))
        ready_b = replace(member_b, ready=True)
        ready_room = replace(joined_room, members=(member_a, ready_b))
        assert peer.receive(19) == codec.encode_result(19, 0)
        for client in (owner, peer):
            assert client.receive(1) == codec.encode_member_notify(6, ready_b)
            assert client.receive(1) == codec.encode_room_notify(ready_room)
        owner.send(struct.pack(">Hi", 20, 2))
        changed_a = replace(member_a, camp=2)
        changed_room = replace(joined_room, members=(changed_a, member_b))
        for client in (owner, peer):
            assert client.receive(1) == codec.encode_member_notify(6, changed_a)
            assert client.receive(1) == codec.encode_member_notify(6, member_b)
            assert client.receive(1) == codec.encode_room_notify(changed_room)
        owner.send(struct.pack(">Hi", 20, 1))
        for client in (owner, peer):
            assert client.receive(1) == codec.encode_member_notify(6, member_a)
            assert client.receive(1) == codec.encode_room_notify(joined_room)
        owner.send(struct.pack(">Hb", 21, 0))
        assert owner.receive(22) == codec.encode_result(22, 1)
        # No lobby input for longer than the service's 30-second idle deadline.
        time.sleep(31)
        peer.send(struct.pack(">Hiii", 16, 1, 0, 20))
        assert peer.receive(17) == codec.encode_room_list((joined_info,), start=0, total=1)
        peer.close(); clients.remove(peer)
        assert owner.receive(1) == codec.encode_member_notify(5, member_b)
        assert owner.receive(1) == codec.encode_room_notify(room)
        owner.send(struct.pack(">Hb", 10, 0))
        assert owner.receive(11) == codec.encode_result(11, 0)
        owner.send(struct.pack(">Hiii", 16, 1, 0, 20))
        assert owner.receive(17) == codec.encode_room_list((), start=0, total=0)
        owner.send(struct.pack(">HQ", 8, guid) + string(b""))
        assert owner.receive(9) == codec.encode_room_response(9, result=1)
    finally:
        try:
            for client in reversed(clients):
                client.close()
        finally:
            build.remove_tree(package)


def test_gateway_sends_next_request_before_first_reply(tmp_path, monkeypatch):
    """A withheld pose-like reply must not block a following lobby/action request."""
    import base64
    import hashlib
    import json
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0)); listener.listen(1); listener.settimeout(15)
    monkeypatch.setenv('T7_GATEWAY_TEST_PORT', str(listener.getsockname()[1]))
    errors, observed = [], []
    def exact(connection, size):
        data = bytearray()
        while len(data) < size:
            value = connection.recv(size - len(data))
            if not value: raise EOFError('websocket closed')
            data.extend(value)
        return bytes(data)
    def read(connection):
        first, second = exact(connection, 2)
        if first & 15 == 8: raise EOFError('websocket close')
        size = second & 127
        if size == 126: size = int.from_bytes(exact(connection, 2))
        elif size == 127: size = int.from_bytes(exact(connection, 8))
        assert size <= 4096 and second & 128
        mask = exact(connection, 4); payload = exact(connection, size)
        return json.loads(bytes(value ^ mask[i % 4] for i, value in enumerate(payload)))
    def reply(connection, request, kind, payload):
        data = json.dumps(dict(protocolVersion=1, requestId=request['requestId'], event=dict(type=kind, payload=payload))).encode()
        header = bytes([0x81, len(data)]) if len(data) < 126 else b'\x81\x7e' + struct.pack('>H', len(data))
        connection.sendall(header + data)
    def serve():
        try:
            with listener.accept()[0] as connection:
                connection.settimeout(5)
                headers = bytearray()
                while not headers.endswith(b'\r\n\r\n'): headers.extend(exact(connection, 1))
                key = next(line.split(':', 1)[1].strip() for line in headers.decode().splitlines() if line.lower().startswith('sec-websocket-key:'))
                accept = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
                connection.sendall(('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ' + accept + '\r\n\r\n').encode())
                hello = read(connection)['command']['payload']
                # The first gateway request always has sequence 1.
                reply(connection, {'requestId':'1'}, 'welcome', dict(accountId=hello['accountId'], connectionId='fixture', userId=1, displayName=hello['displayName'], level=100))
                def next_request():
                    while True:
                        request = read(connection)
                        if request['command']['type'] != 'ping':
                            return request
                        reply(connection, request, 'pong', dict(serverTimeMs=0))
                first = next_request()
                second = next_request()  # Intentionally withhold first response.
                observed.extend([first['command']['type'], second['command']['type']])
                reply(connection, first, 'channelList', dict(channels=[dict(channelId=1, name='自定义房间')]))
                reply(connection, second, 'roomPage', dict(page=dict(channelId=1, start=0, total=0, rooms=[])))
                try: next_request()
                except (EOFError, OSError): pass
        except BaseException as error: errors.append(error)
    worker = threading.Thread(target=serve, daemon=True); worker.start()
    spec = importlib.util.spec_from_file_location('build_pipeline_fixture', ROOT / 'scripts/build.py')
    build = importlib.util.module_from_spec(spec); spec.loader.exec_module(build)
    package = build.prepare_runtime_fixture(Path(sys.executable).parent)
    client = None
    try:
        client = NativeClient(package, tmp_path / 'pipeline', 'pipeline-player')
        client.send(struct.pack('>Hb', 23, 0))
        client.send(struct.pack('>Hiii', 16, 1, 0, 20))
        client.receive(24)
        assert client.receive(17) == codec.encode_room_list((), start=0, total=0)
        assert observed == ['listChannels', 'listRoomPage']
    finally:
        if client is not None: client.close()
        listener.close(); worker.join(timeout=10)
        build.remove_tree(package)
    assert not worker.is_alive()
    assert not errors, errors


def test_four_original_instances_have_separate_enemy_and_teammate_positions(tmp_path):
    spec = importlib.util.spec_from_file_location('build_multi_fixture', ROOT / 'scripts/build.py')
    build = importlib.util.module_from_spec(spec); spec.loader.exec_module(build)
    package = build.prepare_runtime_fixture(Path(sys.executable).parent)
    clients, instances, epochs = [], [], []
    try:
        for index in range(4):
            clients.append(NativeClient(package, tmp_path / f'multi-{index}', f'multi-{os.getpid()}-{index}', scene=True))
        owner = clients[0]
        owner.send(struct.pack('>Hi', 2, 1003) + string(b'multi') + string(b'') + struct.pack('>i', 1))
        guid = struct.unpack_from('>Q', owner.receive(3), 6)[0]
        for client in clients[1:]:
            client.send(struct.pack('>HQ', 8, guid) + string(b'')); client.receive(9)
        for client in clients[1:]:
            client.send(struct.pack('>Hi', 18, 1)); client.receive(19)
        owner.send(struct.pack('>Hb', 21, 0)); assert owner.receive(22) == codec.encode_result(22, 0)
        for client in clients:
            client.receive(9, command=0x1e)
            client.send(struct.pack('>HQi', 3, 1, 0), command=0x1e); client.receive(4, command=0x1e)
            actor = InstanceClient(client); instances.append(actor)
            actor.receive(command=0x2c)
            info = actor.receive(0x64, command=0xA); epochs.append(struct.unpack_from('>Q', info, 18)[0])
            actor.send(b'\0\3\0', command=0x23); actor.receive(4, command=0x23)
            actor.send(b'\0\x14\0', command=0x36)
            actor.send(struct.pack('>Hi', 0x32, 1), command=0x36); actor.receive(0x33, command=0x36)
            actor.send(struct.pack('>HHb', 0x64, 1, 0), command=0x36); actor.receive(0x65, command=0x36)
        for actor in instances:
            while struct.unpack_from('>i', actor.receive(0x67, command=0xA), 18)[0] != 4: pass
        for index, client in enumerate(clients):
            client.process.stdin.write(f'world {epochs[index]} {index + 10} 20 0.5 0\n'); client.process.stdin.flush()
        from Business.scripts.codec import move_flow
        for index, actor in enumerate(instances):
            expected, enemy, friendly = {}, 2, 12
            for peer in range(4):
                if peer == index: continue
                if peer % 2 == index % 2: slot = friendly; friendly += 1
                else: slot = enemy; enemy += 1
                expected[slot] = (float(peer + 10), 20., .5)
            received = {}
            while len(received) < 3:
                update = move_flow.decode_move_direct_bc(actor.receive(4, command=2))
                received[update.target_instance_id] = update.position
            assert received == expected
    finally:
        for instance in reversed(instances): instance.close()
        for client in reversed(clients): client.close()
        build.remove_tree(package)


@pytest.mark.skipif(not os.environ.get('T7_GATEWAY_TEST_HOST'), reason='requires a trusted public TLS guest service')
def test_native_guest_uses_https_ticket_and_direct_wss(tmp_path):
    import base64
    import ctypes
    import json
    import urllib.request
    endpoint = 'https://' + os.environ['T7_GATEWAY_TEST_HOST'] + ':' + os.environ['T7_GATEWAY_TEST_PORT']
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(endpoint + '/auth/guest', data=json.dumps(dict(displayName='Player')).encode(), headers={'Content-Type':'application/json'})
    with opener.open(request, timeout=10) as response: ticket = json.load(response)
    class Blob(ctypes.Structure):
        _fields_ = [('size', ctypes.c_ulong), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    raw = ticket['ticket'].encode(); data = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    original, protected = Blob(len(raw), data), Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    crypt.CryptProtectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(Blob)]
    crypt.CryptProtectData.restype = ctypes.c_int
    assert crypt.CryptProtectData(ctypes.byref(original), None, None, None, None, 1, ctypes.byref(protected))
    try: encrypted = base64.b64encode(ctypes.string_at(protected.data, protected.size)).decode()
    finally:
        kernel = ctypes.WinDLL('kernel32'); kernel.LocalFree.argtypes = [ctypes.c_void_p]; kernel.LocalFree(protected.data)
    spec = importlib.util.spec_from_file_location('build_tls_fixture', ROOT / 'scripts/build.py')
    build = importlib.util.module_from_spec(spec); spec.loader.exec_module(build)
    package = build.prepare_runtime_fixture(Path(sys.executable).parent)
    client = None
    try:
        client = NativeClient(package, tmp_path / 'guest', ticket['accountId'], gateway_config=dict(
            serverHost=os.environ['T7_GATEWAY_TEST_HOST'], serverPort=os.environ['T7_GATEWAY_TEST_PORT'],
            secure='true', accountId=ticket['accountId'], protectedTicket=encrypted))
        client.send(struct.pack('>Hb', 23, 0))
        assert client.receive(24) == codec.encode_channels((codec.ChannelInfo(1, '自定义房间'.encode('gbk'), 0),))
        client.send(struct.pack('>Hi', 2, 1003) + string(b'tls') + string(b'') + struct.pack('>i', 1))
        assert int.from_bytes(client.receive(3)[2:6]) == 0
        client.send(struct.pack('>Hb', 10, 0)); assert client.receive(11) == codec.encode_result(11, 0)
    finally:
        if client is not None: client.close()
        build.remove_tree(package)
