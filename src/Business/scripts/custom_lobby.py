"""Pure translation between the original lobby and the shared room service."""
from . import online_limits
import json
from dataclasses import replace

from . import online_scene, online_actions
from .codec import custom_room as codec, room_flow, leave_flow


def text(value, capacity):
    if type(value) is not str:
        raise ValueError("room text must be a string")
    encoded = value.encode("gbk", errors="strict")
    if len(encoded) >= capacity or b"\0" in encoded:
        raise ValueError("room text exceeds client storage")
    return encoded


def integer(value, minimum=0, maximum=0x7fffffff):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("invalid room service integer")
    return value


def valid(session):
    if "customLobby" not in session:
        return True
    gateway = session["customLobby"]
    try:
        if (session["role"] != "logic" or type(gateway) is not dict
                or type(gateway["identified"]) is not bool or type(gateway["pending"]) is not dict
                or len(gateway["pending"]) > 8):
            return False
        next_id = integer(gateway["nextId"], 1, 0xffffffffffffffff)
        for sequence, item in gateway["pending"].items():
            if guid(sequence) >= next_id or item["kind"] not in (
                    "hello", "ping", "channels", "list", "get", "create", "join", "quit", "ready", "camp", "start", "view", "loaded", "scene-leave", "action", "pose", "contact", "respawn"):
                return False
        if gateway["identified"]:
            integer(gateway["userId"], 1, 0xffffffff)
            integer(gateway["level"], 1)
        if 'rttMs' in gateway:
            integer(gateway['rttMs'], 0, 60000)
        if "room" in gateway:
            full_room(gateway["room"], active=True)
        if "loadedMatch" in gateway and type(gateway["loadedMatch"]) is not str:
            return False
        if 'startReplyPending' in gateway and type(gateway['startReplyPending']) is not bool:
            return False
        return True
    except (KeyError, TypeError, ValueError, StopIteration):
        return False


def guid(value):
    if type(value) is not str or not value.isascii() or not value.isdecimal():
        raise ValueError("invalid room service guid")
    number = integer(int(value), 1, 0xffffffffffffffff)
    if str(number) != value:
        raise ValueError("noncanonical room service guid")
    return number


def summary(room, owner):
    return codec.RoomInfo(guid(room["roomGuid"]), integer(room["roomNumber"], 1, 0xffffffff),
                          integer(room["mapId"], 1), text(owner, 32), text(room["name"], 64),
                          0, False, integer(room.get("memberCount", len(room.get("members", [])))), 0, 0)


def full_room(room, *, active=False):
    if room["phase"] not in (("waiting", "loading", "countdown", "running") if active else ("waiting",)):
        raise ValueError("game lobby requires a waiting room")
    integer(room["mapId"], 1)
    members = room["members"]
    if type(members) is not list or not 1 <= len(members) <= online_limits.ROOM_CAPACITY:
        raise ValueError("invalid room service members")
    owner = next(member for member in members if member["accountId"] == room["ownerAccountId"])
    entries = []
    for member in members:
        if type(member["ready"]) is not bool:
            raise ValueError("invalid ready flag")
        entries.append(codec.MemberInfo(integer(member["userId"], 1, 0xffffffff),
                                        text(member["displayName"], 32),
                                        integer(member["camp"], 1, 2), member["ready"],
                                        level=integer(member["level"], 1)))
    result = codec.FullRoomInfo(summary(room, owner["displayName"]), owner["userId"],
                               tuple(entries), integer(room["channelId"], 1))
    codec.encode_full_room(result)
    integer(room["revision"], 1, 0xffffffffffffffff)
    if type(room["roomId"]) is not str or not room["roomId"]:
        raise ValueError("invalid room identity")
    return result


def effect(flow, op, request_id, request=""):
    flow.result.setdefault("gateway", []).append({"connection": flow.connection,
                                                  "op": op, "requestId": request_id, "request": request})


def send(flow, kind, command, **metadata):
    gateway = flow.session["customLobby"]
    if len(gateway["pending"]) >= 8:
        raise ValueError("room gateway request capacity exceeded")
    request_id = str(gateway["nextId"])
    if gateway["nextId"] >= 0xffffffffffffffff:
        raise ValueError("room gateway request sequence exhausted")
    gateway["nextId"] += 1
    if kind in ('action', 'contact', 'ping'):
        metadata['queuedAt'] = flow.now
    gateway["pending"][request_id] = {"kind": kind, **metadata}
    request = json.dumps({"protocolVersion": 1, "requestId": request_id, "command": command},
                         ensure_ascii=False, separators=(",", ":"))
    effect(flow, "send", request_id, request)


def begin(flow):
    if "customLobby" in flow.session:
        raise ValueError("room gateway already initialized")
    flow.session["customLobby"] = {"nextId": 1, "pending": {}, "identified": False}
    send(flow, "hello", {"type": "gatewayHello", "payload": {
        "accountId": flow.context["roomGateway"]["accountId"],
        "displayName": flow.state["playerName"]}})


def heartbeat(flow):
    gateway = flow.session["customLobby"]
    if len(gateway["pending"]) < 8 and not any(item["kind"] == "ping" for item in gateway["pending"].values()):
        send(flow, "ping", {"type": "ping"})
    flow.later("custom-lobby-ping", online_limits.SERVER_PING_INTERVAL_MS)


def measured_rtt(flow):
    for key, session in flow.state['sessions'].items():
        if int(key) in flow.context.get('connections', []) and 'customLobby' in session:
            return session['customLobby'].get('rttMs', 0)
    return 0


def failure(flow, kind, metadata):
    if kind == "channels":
        body = codec.encode_channels((), result=1)
    elif kind == "list":
        body = codec.encode_room_list((), start=metadata["start"], total=0, result=1)
    elif kind == "get":
        body = codec.encode_get_response(result=1)
    elif kind in ("create", "join"):
        body = codec.encode_room_response(3 if kind == "create" else 9, result=1)
    elif kind in ("quit", "ready", "start"):
        body = codec.encode_result({"quit": 11, "ready": 19, "start": 22}[kind], 1)
    elif kind == "camp":
        flow.log("WARN", "custom-room camp request rejected")
        return
    else:
        raise ValueError("room service rejected identification or heartbeat")
    flow.send(codec.COMMAND, body, "custom-room-rejected")


def request(flow, body):
    request = codec.decode_request(body)
    gateway = flow.session["customLobby"]
    if not gateway["identified"]:
        raise ValueError("room gateway not identified")
    match request:
        case codec.ChannelRequest():
            send(flow, "channels", {"type": "listChannels"})
        case codec.ListRequest(channel_id, start, limit):
            send(flow, "list", {"type": "listRoomPage", "payload": {
                "channelId": channel_id, "start": start, "limit": limit}},
                 start=start, channelId=channel_id, limit=limit)
        case codec.GetRequest(channel_id, room_number):
            send(flow, "get", {"type": "getRoomListing", "payload": {
                "channelId": channel_id, "roomNumber": room_number}}, channelId=channel_id, roomNumber=room_number)
        case codec.CreateRequest(map_id, name, password, channel_id):
            flow.log("INFO", f"custom-room create requested: map={map_id} channel={channel_id}")
            send(flow, "create", {"type": "createCustomRoom", "payload": {
                "mapId": map_id, "name": name.decode("gbk"), "password": password.decode("gbk"),
                "channelId": channel_id}}, mapId=map_id, channelId=channel_id)
        case codec.JoinRequest(room_guid, password):
            send(flow, "join", {"type": "joinCustomRoom", "payload": {
                "roomGuid": str(room_guid), "password": password.decode("gbk")}}, roomGuid=str(room_guid))
        case codec.QuitRequest():
            send(flow, "quit", {"type": "leaveRoom"})
        case codec.ReadyRequest(ready):
            room = gateway.get("room")
            owner = room is not None and room["ownerAccountId"] == flow.context["roomGateway"]["accountId"]
            flow.log("INFO", f"custom-room ready requested: ready={ready} owner={owner}")
            send(flow, "ready", {"type": "ready", "payload": {"ready": ready}})
        case codec.CampRequest(camp):
            send(flow, "camp", {"type": "setCamp", "payload": {"camp": camp}})
        case codec.StartRequest():
            room = gateway.get("room")
            if (room is None or room["phase"] != "waiting" or room["mapId"] not in (1003, 1028)
                    or not flow.state["runtimeMovement"]
                    or any(s["role"] == "instance" for s in flow.state["sessions"].values())
                    or any(item["kind"] == "start" for item in gateway["pending"].values())):
                failure(flow, "start", {})
            else:
                flow.log("INFO", f"custom-room start requested: map={room['mapId']} members={len(room['members'])}")
                send(flow, "start", {"type": "start"})


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate room service JSON field")
        result[key] = value
    return result


def changed(flow, room):
    gateway = flow.session["customLobby"]
    previous = gateway.get("room")
    if previous is None:
        raise ValueError("unsolicited room before a successful join")
    if room["roomId"] != previous["roomId"]:
        raise ValueError("room notification belongs to another room")
    if room["mapId"] != previous["mapId"]:
        raise ValueError("room map changed without an update request")
    if any(room[key] != previous[key] for key in ('roomGuid', 'roomNumber', 'channelId')):
        raise ValueError('room notification changed the directory identity')
    revision = integer(room["revision"], 1, 0xffffffffffffffff)
    if revision <= previous["revision"]:
        return
    current = full_room(room, active=True)
    old = full_room(previous, active=True)
    old_members = {member.user_id: member for member in old.members}
    new_members = {member.user_id: member for member in current.members}
    own = gateway["userId"]
    if own not in new_members:
        raise ValueError("room service removed the local member")
    if not any(m['userId'] == own and m['accountId'] == flow.context['roomGateway']['accountId'] for m in room['members']):
        raise ValueError('room notification changed the local identity')
    if room["phase"] != "waiting":
        current_match = room["currentMatch"]
        if type(current_match) is not dict or type(current_match["matchId"]) is not str:
            raise ValueError("active room lacks a match")
        if previous["phase"] != "waiting":
            if current_match["matchId"] != previous["currentMatch"]["matchId"]:
                raise ValueError("room notification changed the frozen match")
            fields = ('accountId', 'actorId', 'userId', 'camp', 'displayName', 'level')
            if [tuple(m[key] for key in fields) for m in room['members']] != [
                    tuple(m[key] for key in fields) for m in previous['members']]:
                raise ValueError("active room changed the frozen roster")
        gateway["room"] = room
        match = flow.state.get("onlineScene")
        if match is None:
            if room["phase"] != "loading" or room["mapId"] not in (1003, 1028) or not flow.state["runtimeMovement"]:
                raise ValueError("unsupported running-client scene")
            if not any(item['kind'] == 'view' for item in gateway['pending'].values()):
                send(flow, "view", {"type": "getClientView", "payload": {"matchId": current_match["matchId"]}},
                     matchId=current_match["matchId"])
        else:
            if current_match['matchId'] != match['matchId']:
                raise ValueError("scene notification belongs to another match")
            phase = {"loading": 1, "countdown": 2, "running": 3}[room['phase']]
            if phase < match['phase']:
                raise ValueError("scene phase cannot move backwards")
            match['phase'] = phase
            online_scene.applyAuthority(flow)
        return
    if previous['phase'] != 'waiting':
        cancel_scene(flow)
        gateway.pop('loadedMatch', None)
        gateway.pop('startReplyPending', None)
    for user_id, member in old_members.items():
        if user_id not in new_members:
            flow.send(codec.COMMAND, codec.encode_member_notify(5, member), "custom-member-left")
    for user_id, member in new_members.items():
        entry = 4 if user_id not in old_members else 6 if old_members[user_id] != member else None
        if entry is not None:
            flow.send(codec.COMMAND, codec.encode_member_notify(entry, member), "custom-member-changed")
    flow.send(codec.COMMAND, codec.encode_room_notify(current), "custom-room-changed")
    gateway["room"] = room


def cancel_scene(flow):
    match = flow.state.get('onlineScene')
    if match is None:
        return
    match['phase'] = 4
    online_scene.applyAuthority(flow)
    connection, session = flow.connection, flow.session
    for key, value in flow.state['sessions'].items():
        if value['role'] == 'instance' and int(key) in flow.context.get('connections', []):
            flow.connection, flow.session = int(key), value
            if not value.get('leaving'):
                flow.send(0xA, leave_flow.encode_instance_leave_instance(server_time_ms=flow.now), 'custom-scene-cancelled-leave')
                value['leaving'] = True
    flow.connection, flow.session = connection, session
    if not any(s['role'] == 'instance' for s in flow.state['sessions'].values()):
        flow.state.pop('onlineScene', None)


def view(flow, value, metadata):
    gateway = flow.session['customLobby']
    room = gateway['room']
    if (value['roomId'] != room['roomId'] or value['matchId'] != metadata['matchId']
            or value['matchId'] != room['currentMatch']['matchId'] or value['mapId'] != room['mapId']
            or value['phase'] != 'loading' or room['phase'] != 'loading'
            or value['revision'] != room['revision'] or 'onlineScene' in flow.state):
        raise ValueError('client view does not match the allocated scene')
    members = room['members']
    local = next(m for m in members if m['userId'] == gateway['userId'])
    peers = [m for m in members if m['userId'] != gateway['userId']]
    expected = [dict(actorId=local['actorId'], localRid=1, camp=local['camp'], isLocal=True)]
    enemy, friendly = online_limits.ENEMY_FIRST_RID, online_limits.FRIENDLY_FIRST_RID
    for peer in peers:
        slot = friendly if peer['camp'] == local['camp'] else enemy
        expected.append(dict(actorId=peer['actorId'], localRid=slot, camp=peer['camp'], isLocal=False))
        if peer['camp'] == local['camp']: friendly += 1
        else: enemy += 1
    if value['actors'] != expected or value['localActorId'] != local['actorId']:
        raise ValueError('client view changed the actor mapping')
    match = dict(matchId=value['matchId'], mapId=value['mapId'], phase=1,
                 localActorId=local['actorId'], localCamp=local['camp'], localUserId=local['userId'],
                 peers=[dict(actorId=p['actorId'], localRid=b['localRid'], camp=p['camp'],
                     userId=p['userId'], name=p['displayName'],
                     spawnIndex=sorted(m['actorId'] for m in members if m['camp'] == p['camp']).index(p['actorId']))
                     for p, b in zip(peers, expected[1:])])
    if not online_scene.valid(match):
        raise ValueError('invalid allocated scene')
    flow.state['onlineScene'] = match
    if gateway.pop('startReplyPending', False):
        flow.send(codec.COMMAND, codec.encode_result(22, 0), 'custom-room-started')
    data = full_room(room, active=True)
    data = replace(data, summary=replace(data.summary, scene_room_id=1))
    flow.send(codec.COMMAND, codec.encode_room_notify(data), 'custom-room-scene-assigned')
    flow.send(0x1E, room_flow.encode_room_enter_instance_notify(
        room_id=1, user_camp=match['localCamp'], notify_type=0), 'custom-enter-instance-notify')
    flow.log('INFO', f"custom-scene allocated: map={match['mapId']} actors={len(expected)}")


def enter(flow, body):
    request = room_flow.decode_room_enter_request(body)
    match = flow.state.get('onlineScene')
    if match is None or match['phase'] != 1 or request.room_id != 1 or request.is_reconnect:
        raise ValueError('custom instance entry requires the allocated loading scene')
    room = flow.session['customLobby']['room']
    url = f"{flow.context['advertisedAddress']}:{flow.context['instancePort']}".encode('ascii')
    camps = [m['camp'] for m in room['members']]
    flow.send(0x1E, room_flow.encode_room_enter_response(
        room_id=1, result=0, is_reconnect=0, room_name=text(room['name'], 64), resource_id=match['mapId'],
        sync_url=url, prefer=0, room_user_count=len(camps), blue_user_count=camps.count(1),
        red_user_count=camps.count(2)), 'custom-room-enter-result')
    flow.phase('instance-notified')


def loaded(flow):
    match = flow.state.get('onlineScene')
    if match is None or match['phase'] != 1:
        return
    instances = [s for s in flow.state['sessions'].values() if s['role'] == 'instance' and not s.get('leaving')]
    if len(instances) != 1 or not all(instances[0].get(k) is True for k in ('loaded', 'heroChosen', 'battleEntered')):
        return
    connection, session = flow.connection, flow.session
    for key, value in flow.state['sessions'].items():
        gateway = value.get('customLobby')
        if gateway is not None and int(key) in flow.context.get('connections', []) and gateway.get('loadedMatch') != match['matchId']:
            flow.connection, flow.session = int(key), value
            send(flow, 'loaded', {'type': 'loaded', 'payload': {'matchId': match['matchId']}})
            gateway['loadedMatch'] = match['matchId']
            flow.log('INFO', 'custom-scene real client readiness committed')
    flow.connection, flow.session = connection, session


def closed(flow, role):
    if role not in ('logic', 'instance') or 'onlineScene' not in flow.state:
        return
    cancel_scene(flow)
    if role != 'instance':
        return
    connection, session = flow.connection, flow.session
    for key, value in flow.state['sessions'].items():
        gateway = value.get('customLobby')
        if (gateway is not None and gateway.get('room') is not None
                and int(key) in flow.context.get('connections', [])
                and not any(item['kind'] in ('quit', 'scene-leave') for item in gateway['pending'].values())):
            flow.connection, flow.session = int(key), value
            send(flow, 'scene-leave', {'type': 'leaveRoom'})
    flow.connection, flow.session = connection, session


def receive(flow, body):
    if type(body) is not bytes or len(body) > 65536:
        raise ValueError("invalid room service body")
    envelope = json.loads(body.decode("utf-8"), object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid JSON number")))
    if integer(envelope["protocolVersion"], 1, 1) != 1:
        raise ValueError("unsupported room protocol")
    message = envelope["event"]
    request_id = envelope.get("requestId")
    gateway = flow.session["customLobby"]
    if request_id is None:
        if message['type'] == 'actionChanged':
            online_actions.receive(flow, message['payload']['action'])
        elif message['type'] == 'combatChanged':
            from . import online_combat
            online_combat.receive(flow, message['payload']['update'])
        elif message['type'] == 'poseChanged':
            from . import online_motion
            online_motion.receive(flow, message['payload']['update'])
        elif message["type"] == "roomChanged":
            changed(flow, message["payload"]["room"])
        else:
            raise ValueError("unsupported room notification")
        return
    metadata = gateway["pending"].pop(request_id)
    kind = metadata["kind"]
    if message["type"] == "error":
        code = message["payload"]["code"]
        if type(code) is not str or not code.isascii() or not code.isalnum() or len(code) > 64:
            raise ValueError("invalid room service error code")
        if kind == 'pose' and code == 'invalidPose':
            flow.log('WARN', 'online pose rejected by movement bounds')
        elif (kind in ('action', 'pose', 'contact', 'respawn') and flow.state.get('onlineScene', {}).get('phase') == 4
                and code in ('wrongPhase', 'staleMatch', 'notInRoom')):
            flow.log('INFO', 'online action cancelled with its scene')
        elif kind in ("hello", "ping", "view", "loaded", "scene-leave", "action", "pose", "contact", "respawn"):
            raise ValueError(f"room service rejected {kind}: {code}")
        else:
            failure(flow, kind, metadata)
            flow.log("WARN", f"custom-room {kind} rejected: {code}")
    else:
        expected = {"hello": "welcome", "ping": "pong", "channels": "channelList", "list": "roomPage",
                    "get": "roomListing", "create": "room", "join": "room", "quit": "left",
                    "ready": "room", "camp": "room", "start": "room", "view": "clientView", "loaded": "room", "scene-leave": "left", "action": "actionAccepted", "pose": "poseAccepted", "contact": "contactAccepted", "respawn": "respawnAccepted"}[kind]
        if message["type"] != expected:
            raise ValueError("room service reply does not match the request")
        payload = message["payload"]
        if kind == "hello":
            if payload["accountId"] != flow.context["roomGateway"]["accountId"]:
                raise ValueError("room service returned a different account")
            if text(payload["displayName"], 32) != flow.playerName:
                raise ValueError("room service returned a different display name")
            gateway.update(identified=True, userId=integer(payload["userId"], 1, 0xffffffff),
                           level=integer(payload["level"], 1))
            flow.later("login", 1000)
            flow.later("custom-lobby-ping", 1)
        elif kind in ('ping'):
            integer(payload["serverTimeMs"], 0, 0xffffffffffffffff)
            # Both endpoints of the measurement use the local monotonic clock.
            gateway['rttMs'] = min(60000, max(0, flow.now - metadata['queuedAt']))
        elif kind == 'respawn':
            if payload['matchId'] != metadata['matchId']:
                raise ValueError('respawn acknowledgement mismatch')
        elif kind in ('action', 'pose', 'contact'):
            if (payload['matchId'] != metadata['matchId']
                    or integer(payload['inputSeq'], 1, 0xffffffff) != metadata['inputSeq']):
                raise ValueError('action acknowledgement does not match the input')
            if kind == 'contact':
                if type(payload.get('accepted')) is not bool:
                    raise ValueError('invalid contact acknowledgement')
                if not payload['accepted']:
                    flow.log('DEBUG', f'online-contact-rejected input={metadata["inputSeq"]}')
        elif kind == "channels":
            channels = tuple(codec.ChannelInfo(integer(item["channelId"], 1), text(item["name"], 32), 0)
                             for item in payload["channels"])
            flow.send(codec.COMMAND, codec.encode_channels(channels), "custom-room-channels")
        elif kind == "list":
            page = payload["page"]
            if (page["start"] != metadata["start"] or page["channelId"] != metadata["channelId"]
                    or type(page["rooms"]) is not list or len(page["rooms"]) > metadata["limit"]):
                raise ValueError("room service returned a different page")
            rooms = tuple(summary(item, item["ownerDisplayName"]) for item in page["rooms"])
            flow.send(codec.COMMAND, codec.encode_room_list(rooms, start=page["start"], total=page["total"]),
                      "custom-room-list")
        elif kind == "get":
            room = payload["room"]
            if room["channelId"] != metadata["channelId"] or room["roomNumber"] != metadata["roomNumber"]:
                raise ValueError("room service returned a different room listing")
            flow.send(codec.COMMAND, codec.encode_get_response(summary(room, room["ownerDisplayName"])),
                      "custom-room-get")
        elif kind in ("create", "join"):
            room = payload["room"]
            data = full_room(room)
            if (not any(member["userId"] == gateway["userId"]
                        and member["accountId"] == flow.context["roomGateway"]["accountId"]
                        for member in room["members"])
                    or kind == "join" and room["roomGuid"] != metadata["roomGuid"]):
                raise ValueError("room reply omits the local member")
            if kind == "create" and room["ownerAccountId"] != flow.context["roomGateway"]["accountId"]:
                raise ValueError("room service created a room with a different owner")
            if kind == "create" and (room["mapId"] != metadata["mapId"]
                                     or room["channelId"] != metadata["channelId"]):
                raise ValueError("room service changed the requested map or channel")
            gateway["room"] = room
            flow.send(codec.COMMAND, codec.encode_room_response(3 if kind == "create" else 9, data),
                      "custom-room-created" if kind == "create" else "custom-room-joined")
        elif kind in ("quit", "scene-leave"):
            if payload["roomId"] != gateway.get("room", {}).get("roomId"):
                raise ValueError("room service left a different room")
            room_guid = guid(gateway.pop("room")["roomGuid"])
            cancel_scene(flow)
            gateway.pop('loadedMatch', None)
            gateway.pop('startReplyPending', None)
            if kind == 'quit':
                flow.send(codec.COMMAND, codec.encode_result(11, 0), "custom-room-left")
            else:
                flow.send(codec.COMMAND, codec.encode_dismiss_notify(room_guid), 'custom-room-scene-left')
        elif kind == 'view':
            view(flow, payload['view'], metadata)
        elif kind in ("ready", "camp", "start", "loaded"):
            if kind == "ready":
                flow.send(codec.COMMAND, codec.encode_result(19, 0), "custom-room-ready")
            elif kind == 'start':
                if payload['room']['phase'] != 'loading':
                    raise ValueError('start did not allocate a loading match')
                # The client may enter immediately after this reply; freeze its view first.
                gateway['startReplyPending'] = True
            changed(flow, payload["room"])
    if kind in ('action', 'contact', 'ping') and 'queuedAt' in metadata:
        elapsed = flow.now - integer(metadata['queuedAt'], 0, 0xffffffffffffffff)
        flow.log('INFO', f'online-gateway kind={kind} elapsedMs={elapsed}')
    effect(flow, "ack", request_id)
