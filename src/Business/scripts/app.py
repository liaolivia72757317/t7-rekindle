"""Versioned fixed business adapter; native code owns sockets and timer lifetime."""
from copy import deepcopy
import struct

from . import contracts as wire, scene, controls
from .codec import login_flow, protocol, room_flow
from .codec.method3 import Method3UplinkMessage

API_VERSION = 1
STATE_VERSION = 2


def createState(context):
    playerName = wire.encodePlayerName(context.get("playerName", wire.USER_NAME.decode("gbk"))).decode("gbk")
    runtimeMovement = context.get("runtimeMovement", wire.CLIENT_RUNTIME_MOVEMENT)
    if type(runtimeMovement) is not bool:
        raise ValueError("runtimeMovement must be a bool")
    return {"phase": "waiting", "sessions": {}, "roomId": 1, "actorId": 1,
            "playerName": playerName, "runtimeMovement": runtimeMovement}


def validateState(state):
    if (type(state) is not dict or type(state.get("sessions")) is not dict
            or type(state.get("phase")) is not str or state.get("roomId") != 1
            or state.get("actorId") != 1):
        return False
    try:
        wire.encodePlayerName(state.get("playerName", wire.USER_NAME.decode("gbk")))
    except (ValueError, TypeError):
        return False
    if type(state.get("runtimeMovement", wire.CLIENT_RUNTIME_MOVEMENT)) is not bool:
        return False
    return all(type(key) is str and type(session) is dict
               and session.get("role") in ("login", "logic", "instance")
               and type(session.get("hydration")) is list
               and type(session.get("pending", {})) is dict
               and (not session.get("initialized") or controls.validMoveClock(session))
               for key, session in state["sessions"].items())


def migrateState(fromVersion, state):
    if fromVersion not in (1, STATE_VERSION) or not validateState(state):
        raise ValueError("no migration for this state version")
    return deepcopy(state)


def selfTest():
    return (protocol.encode_version_check_response(0) == bytes.fromhex("006500000000")
            and len(wire.roundState(0, 2, 30000)) == 42
            and len(wire.actorVision(1)) == 397 + len(wire.USER_NAME)
            and len(wire.battleHeroes(13)) == 292)


class Flow:
    def __init__(self, event, state, context):
        self.state, self.context = state, context
        self.playerName = wire.encodePlayerName(state.get("playerName", wire.USER_NAME.decode("gbk")))
        self.connection = event["connection"]
        self.now = context["nowMs"]
        self.session = state["sessions"].get(str(self.connection))
        self.result = {"state": state, "send": [], "timers": [], "logs": []}

    def phase(self, name):
        self.session["phase"] = name
        activeInstance = any(session["role"] == "instance" and not session.get("leaving")
                             for session in self.state["sessions"].values())
        if self.session["role"] == "instance" or not activeInstance:
            self.state["phase"] = name

    def send(self, command, body, reason):
        self.result["send"].append({"connection": self.connection, "command": command,
                                    "body": body, "reason": reason})

    def later(self, name, delay):
        self.session.setdefault("pending", {})[name] = self.now + delay
        self.result["timers"].append({"id": f"{self.connection}:{name}", "delayMs": delay,
            "event": {"type": "timer", "connection": self.connection, "name": name}})

    def cancelTimers(self):
        for name in self.session.get("pending", {}):
            self.result["timers"].append({"id": f"{self.connection}:{name}", "delayMs": -1,
                "event": {"type": "timer", "connection": self.connection, "name": name}})
        self.session["pending"] = {}

    def cancel(self, name):
        if name in self.session.get("pending", {}):
            del self.session["pending"][name]
            self.result["timers"].append({"id": f"{self.connection}:{name}", "delayMs": -1,
                "event": {"type": "timer", "connection": self.connection, "name": name}})

    def syncBody(self):
        return protocol.encode_sync_login_response(protocol.SyncLoginResponse(10000, 1, 0, 0))


def handleTimer(flow, name):
    pending = flow.session.get("pending")
    if pending is None:
        # Old revision 1 stored native login timers but no script timer ledger.
        if name not in ("version", "login", "sync"):
            return
        flow.session["pending"] = {}
    elif name not in pending or flow.now < pending[name]:
        return
    else:
        flow.timerDue = pending[name]
        del pending[name]
    if name == "version":
        flow.send(1, protocol.encode_version_check_response(0), "version-response")
    elif name == "login":
        identity = protocol.MinimalLoginIdentity(wire.USER_ID, flow.playerName, user_image_id=7, level=7)
        flow.send(1, protocol.encode_minimal_login_success(identity), "fixed-local-login")
        flow.later("sync", 20000)
    elif name == "sync":
        flow.send(0x2C, flow.syncBody(), "sync-login")
        flow.phase("hydrating")
    elif name == "match-result":
        flow.send(0x20, struct.pack(">Hi", 3, 0), "match-result-notify")
    elif name == "match-enter":
        flow.send(0x1E, room_flow.encode_room_enter_instance_notify(
            room_id=1, user_camp=1, notify_type=0), "match-enter-instance-notify")
        flow.phase("instance-notified")
    elif not controls.timer(flow, name) and not scene.timer(flow, name):
        raise ValueError(f"unknown business timer: {name}")


def handleRoom(flow, body, selector):
    syncUrl = f"{flow.context['advertisedAddress']}:{flow.context['instancePort']}".encode("ascii")
    if selector == 10:
        room_flow.decode_room_reconnect_request(body)
        flow.send(0x1E, room_flow.encode_room_reconnect_response(result=1), "room-reconnect-result-nonzero")
    elif selector in (1, 3):
        roomName, reconnect = b"RoomName1", 0
        if selector == 1:
            request = room_flow.decode_room_create_request(body)
            if request.resource_id != wire.RESOURCE_ID:
                raise ValueError("fixed native scene requires resource_id 1028")
            roomName = request.room_name
            flow.send(0x1E, room_flow.encode_room_create_response(
                result=0, room_id=1, room_name=roomName, resource_id=wire.RESOURCE_ID), "room-create-result-zero")
        else:
            request = room_flow.decode_room_enter_request(body)
            if request.room_id != 1:
                raise ValueError("fixed native scene requires room_id 1")
            reconnect = request.is_reconnect
        flow.send(0x1E, room_flow.encode_room_enter_response(
            room_id=1, result=0, is_reconnect=reconnect, room_name=roomName,
            resource_id=wire.RESOURCE_ID, sync_url=syncUrl, prefer=0), "room-enter-result-zero")
        flow.phase("room-data-sent")
    else:
        return False
    return True


def handleMessage(flow, event):
    body, command = event["body"], event["command"]
    if type(body) is not bytes or len(body) < 2:
        raise ValueError("business message requires a two-byte selector")
    selector = int.from_bytes(body[:2], "big")
    if command == 0x1D and selector == 1:
        flow.send(command, wire.heartbeat(body), "heart-beat-response")
        return
    if command == 8 and selector == 9:
        wire.exact(body, 3, "unify-time")
        last = flow.session.get("unifyAt")
        if last is None or flow.now - last >= 250:
            rtt = 0 if last is None else min(60000, max(1, flow.now - last))
            flow.send(8, struct.pack(">HQQ", 10, flow.now, rtt), "unify-time-rsp")
            flow.session["unifyAt"] = flow.now
        return
    if scene.message(flow, command, selector, body):
        return
    if controls.message(flow, command, selector, body):
        return
    if flow.session["role"] == "logic":
        message = Method3UplinkMessage(event["sequence"], command, event["serverTimeMs"], body)
        responses = login_flow.build_login_flow_responses(
            message, include_fixed_hero_card=True, include_fixed_battle_hero=True)
        for response in responses:
            flow.send(response.command_id, response.body, response.reason)
            if response.reason not in flow.session["hydration"]:
                flow.session["hydration"].append(response.reason)
            flow.phase("lobby-data-sent")
        if responses or (command == 0x1E and handleRoom(flow, body, selector)):
            return
        if command == 0x20 and selector == 1:
            flow.send(0x20, wire.matchStart(body), "match-start-result-zero")
            flow.later("match-result", 1000)
            flow.later("match-enter", 2000)
            flow.phase("matching")
            return
    observed = flow.session.setdefault("unhandled", {})
    key = f"{command}:{selector}"
    if key not in observed and len(observed) < 64:
        observed[key] = True
        flow.result["logs"].append(f"unhandled command={command} selector={selector}; no speculative response")


def handleEvent(event, state, context):
    state = deepcopy(state)
    flow = Flow(event, state, context)
    eventType, key = event["type"], str(event["connection"])
    if eventType == "operator":
        if event.get("name") != "diagnostic":
            raise ValueError("only the diagnostic command is implemented; no speculative correction bundle")
        flow.result["logs"].append(f"diagnostic phase={state['phase']} sessions={len(state['sessions'])} "
                                   f"baseline={wire.BASELINE_ID} scene=1028 actor=1 heroes={wire.HERO_IDS} "
                                   f"ground={wire.GROUND_STEP_DISTANCE}/{wire.GROUND_STEP_MS}ms "
                                   "prime=state10-state1 outcome=none clientAcceptance=unverified")
        for connection, session in list(state["sessions"].items())[:32]:
            flow.result["logs"].append(
                f"connection={connection} role={session['role']} phase={session.get('phase', 'waiting')} "
                f"actorImported={session.get('actorImported', False)} heroChosen={session.get('heroChosen', False)} "
                f"battleEntered={session.get('battleEntered', False)} pending={list(session.get('pending', {}))}")
    elif eventType == "connected":
        if event["role"] not in ("login", "logic", "instance") or key in state["sessions"]:
            raise ValueError("invalid or duplicate business connection")
        state["sessions"][key] = {"role": event["role"], "hydration": [], "pending": {}, "camp": 1,
                                  "authenticated": False}
    elif eventType == "closed":
        state["sessions"].pop(key, None)
        if not state["sessions"]:
            state["phase"] = "waiting"
        elif not any(session["role"] == "instance" for session in state["sessions"].values()):
            state["phase"] = "lobby-data-sent"
    elif flow.session is not None:
        if eventType == "authenticated":
            if not flow.session.get("authenticated"):
                flow.session["authenticated"] = True
                name, delay = {"login": ("version", 25000), "logic": ("login", 25000),
                               "instance": ("instance-init", 20)}[flow.session["role"]]
                flow.later(name, delay)
        elif eventType == "timer":
            handleTimer(flow, event["name"])
        elif eventType == "message":
            if flow.session.get("authenticated", True) is not True:
                raise ValueError("message before authentication")
            handleMessage(flow, event)
        else:
            raise ValueError(f"unknown business event: {eventType}")
    return flow.result
