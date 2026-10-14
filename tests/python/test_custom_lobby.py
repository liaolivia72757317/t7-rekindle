import json
import struct
from copy import deepcopy

import pytest

from Business.runtime.host_runtime import validateTransition
from Business.scripts import app, custom_lobby
from Business.scripts.codec import custom_room as codec, protocol


class Client:
    def __init__(self, runtime_movement=False):
        self.context = {"nowMs": 0, "connections": [1], "playerName": "Player",
                        "advertisedAddress": "127.0.0.1", "instancePort": 1,
                        "roomGateway": {"accountId": "player-a"}, "runtimeMovement": runtime_movement}
        self.state = app.createState(self.context)
        self.event("connected")
        self.event("authenticated")

    def event(self, kind, *, body=b"", command=0, name=""):
        event = {"type": kind, "connection": 1, "role": "logic", "command": command,
                 "body": body, "name": name, "sequence": 1, "serverTimeMs": 0}
        result = app.handleEvent(event, self.state, self.context)
        assert app.validateState(result["state"])
        validateTransition(result, self.context)
        self.state = result["state"]
        return result

    def reply(self, sequence, kind, **payload):
        envelope = {"protocolVersion": 1, "event": {"type": kind, "payload": payload}}
        if sequence is not None:
            envelope["requestId"] = str(sequence)
        return self.event("gateway", body=json.dumps(envelope).encode())

    def identify(self):
        return self.reply(1, "welcome", accountId="player-a", userId=700, displayName="Player", level=100)


def room(revision=1, peer=False):
    members = [{"accountId": "player-a", "userId": 700, "displayName": "Player",
                "actorId": 1, "camp": 1, "ready": False, "loaded": False, "level": 100}]
    if peer:
        members.append({"accountId": "player-b", "userId": 900, "displayName": "Peer",
                        "actorId": 2, "camp": 2, "ready": False, "loaded": False, "level": 100})
    return {"roomId": "room-test", "roomGuid": "42", "roomNumber": 42, "channelId": 1,
            "name": "测试", "mapId": 1028, "ownerAccountId": "player-a", "phase": "waiting",
            "members": members, "revision": revision}


def string(value):
    return struct.pack(">I", len(value) + 1) + value + b"\0"


def test_identification_precedes_login_and_uses_server_player_id():
    client = Client()
    pending = client.state["sessions"]["1"]["customLobby"]
    assert pending["pending"] == {"1": {"kind": "hello"}}
    result = client.identify()
    assert result["gateway"][0]["op"] == "ack"
    assert not result["send"]
    client.context["nowMs"] = 1000
    result = client.event("timer", name="login")
    identity = protocol.decode_minimal_login_success(result["send"][0]["body"])
    assert identity.user_id == 700 and identity.level == 100
    client.context["nowMs"] = 2000
    result = client.event("timer", name="sync")
    assert protocol.decode_sync_login_response(result["send"][0]["body"]).user_id == 700


@pytest.mark.parametrize("map_id", (1028, 7001, 7002))
def test_create_translates_gbk_and_waits_for_the_authoritative_reply(map_id):
    client = Client(); client.identify()
    body = struct.pack(">Hi", 2, map_id) + string("测试".encode("gbk")) + string(b"") + struct.pack(">i", 1)
    result = client.event("message", command=59, body=body)
    assert result["send"] == []
    command = json.loads(result["gateway"][0]["request"])["command"]
    assert command == {"type": "createCustomRoom", "payload": {
        "name": "测试", "password": "", "mapId": map_id, "channelId": 1}}
    def snapshot(revision=1, peer=False):
        return {**room(revision, peer), "mapId": map_id}
    result = client.reply(2, "room", room=snapshot())
    assert result["send"][0]["body"] == codec.encode_room_response(3, custom_lobby.full_room(snapshot()))
    assert client.reply(None, "roomChanged", room=snapshot())["send"] == []
    result = client.reply(None, "roomChanged", room=snapshot(2, True))
    assert [int.from_bytes(item["body"][2:6]) for item in result["send"]] == [4, 3]
    member = custom_lobby.full_room(snapshot(2, True)).members[1]
    assert member.user_id == 900 and member.user_id != 2
    assert client.reply(None, "roomChanged", room=snapshot())["send"] == []
    result = client.reply(None, "roomChanged", room=snapshot(3))
    assert [int.from_bytes(item["body"][2:6]) for item in result["send"]] == [5, 3]


@pytest.mark.parametrize("field,value", (("mapId", 7002), ("channelId", 2)))
def test_create_cannot_silently_replace_the_selected_map_or_channel(field, value):
    client = Client(); client.identify()
    body = struct.pack(">Hi", 2, 7001) + string(b"room") + string(b"") + struct.pack(">i", 1)
    client.event("message", command=59, body=body)
    before = deepcopy(client.state)
    reply = {**room(), "mapId": 7001, field: value}
    with pytest.raises(ValueError, match="requested map or channel"):
        client.reply(2, "room", room=reply)
    assert client.state == before


@pytest.mark.parametrize("body,kind,payload", (
    (struct.pack(">Hb", 23, 0), "listChannels", None),
    (struct.pack(">Hiii", 16, 1, 20, 10), "listRoomPage", {"channelId": 1, "start": 20, "limit": 10}),
    (struct.pack(">HiI", 6, 1, 42), "getRoomListing", {"channelId": 1, "roomNumber": 42}),
    (struct.pack(">HQ", 8, 0xffffffffffffffff) + string(b""), "joinCustomRoom",
     {"roomGuid": "18446744073709551615", "password": ""}),
    (struct.pack(">Hb", 10, 0), "leaveRoom", None),
    (struct.pack(">Hi", 18, 2), "ready", {"ready": False}),
    (struct.pack(">Hi", 20, 2), "setCamp", {"camp": 2}),
))
def test_original_requests_use_one_identified_connection(body, kind, payload):
    client = Client(); client.identify()
    result = client.event("message", command=59, body=body)
    command = json.loads(result["gateway"][0]["request"])["command"]
    assert command["type"] == kind
    assert command.get("payload") == payload
    assert result["send"] == []


def test_rejected_create_has_a_nonzero_result_and_no_local_room():
    client = Client(); client.identify()
    body = struct.pack(">Hi", 2, 1028) + string(b"room") + string(b"p") + struct.pack(">i", 1)
    client.event("message", command=59, body=body)
    result = client.reply(2, "error", code="passwordUnsupported")
    assert result["send"][0]["body"][:6] == struct.pack(">HI", 3, 1)
    assert "room" not in client.state["sessions"]["1"]["customLobby"]


def test_channels_and_page_are_encoded_from_rust_without_padding():
    client = Client(); client.identify()
    client.event("message", command=59, body=struct.pack(">Hb", 23, 0))
    result = client.reply(2, "channelList", channels=[{"channelId": 1, "name": "自定义房间"}])
    assert result["send"][0]["body"] == codec.encode_channels((codec.ChannelInfo(1, "自定义房间".encode("gbk"), 0),))
    client.event("message", command=59, body=struct.pack(">Hiii", 16, 1, 0, 20))
    entry = {**room(), "ownerDisplayName": "Player", "memberCount": 1}
    result = client.reply(3, "roomPage", page={"channelId": 1, "start": 0, "total": 1, "rooms": [entry]})
    assert result["send"][0]["body"] == codec.encode_room_list(
        (custom_lobby.summary(entry, "Player"),), start=0, total=1)


def test_invalid_or_unrelated_reply_cannot_advance_the_original_state():
    client = Client(); client.identify()
    before = deepcopy(client.state)
    with pytest.raises(KeyError):
        client.reply(99, "room", room=room())
    assert client.state == before
    with pytest.raises(ValueError, match="duplicate"):
        client.event("gateway", body=b'{"protocolVersion":1,"protocolVersion":1}')
    with pytest.raises(ValueError, match="unsolicited"):
        client.reply(None, "roomChanged", room=room())


def test_unimplemented_scene_handoff_cannot_start_a_fake_match():
    client = Client(); client.identify()
    result = client.event("message", command=59, body=struct.pack(">Hb", 21, 0))
    assert result.get("gateway", []) == []
    assert result["send"][0]["body"] == struct.pack(">Hi", 22, 1)


@pytest.mark.parametrize("ready,status", ((True, 1), (False, 2)))
def test_owner_ready_request_preserves_its_intent_and_does_not_become_start(ready, status):
    client = Client(); client.identify()
    body = struct.pack(">Hi", 2, 1028) + string(b"room") + string(b"") + struct.pack(">i", 1)
    client.event("message", command=59, body=body)
    client.reply(2, "room", room=room())
    result = client.event("message", command=59, body=struct.pack(">Hi", 18, status))
    command = json.loads(result["gateway"][0]["request"])["command"]
    assert command == {"type": "ready", "payload": {"ready": ready}}
    assert result["send"] == []
    assert any(f"ready={ready} owner=True" in line["message"] for line in result["logs"])


def test_pending_requests_are_bounded_and_heartbeat_uses_the_same_sequence():
    client = Client(); client.identify()
    client.context["nowMs"] = 5000
    result = client.event("timer", name="custom-lobby-ping")
    assert json.loads(result["gateway"][0]["request"])["command"] == {"type": "ping"}
    client.reply(2, "pong", serverTimeMs=5000)
    for _ in range(8):
        client.event("message", command=59, body=struct.pack(">Hb", 23, 0))
    before = deepcopy(client.state)
    with pytest.raises(ValueError, match="capacity"):
        client.event("message", command=59, body=struct.pack(">Hb", 23, 0))
    assert client.state == before
    client.context["nowMs"] = 10000
    heartbeat = client.event("timer", name="custom-lobby-ping")
    assert heartbeat.get("gateway", []) == []
    assert heartbeat["timers"][0]["delayMs"] == 1000


def test_periodic_instance_sync_keeps_measured_server_latency():
    from Business.scripts import scene
    client = Client(); client.identify()
    client.context['nowMs'] = 1
    client.event('timer', name='custom-lobby-ping')
    client.context['nowMs'] = 22
    client.reply(2, 'pong', serverTimeMs=999999)
    client.context['connections'].append(2)
    client.state['sessions']['2'] = {'role': 'instance'}
    flow = app.Flow({'connection': 2}, client.state, client.context)
    assert scene.timer(flow, 'time-sync')
    assert struct.unpack('>HQQB', flow.result['send'][0]['body']) == (11, 22, 21, 1)


def test_state_migration_preserves_pending_requests_without_issuing_effects():
    client = Client()
    assert app.STATE_VERSION == 22
    restored = app.migrateState(17, client.state)
    assert restored == client.state and restored is not client.state
    restored["sessions"]["1"]["customLobby"]["nextId"] = 1
    assert not app.validateState(restored)
