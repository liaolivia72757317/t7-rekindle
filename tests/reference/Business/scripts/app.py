"""Versioned fixed business adapter; native code owns sockets and timer lifetime."""
from copy import deepcopy
import struct

from . import actors, contracts as wire, scene, roster, controls, battle, bot, bot_life, combat, player_life
from . import battle_clock, settlement, weapons, loadouts, player_profile, hero_resources
from . import titles
from .codec import kill_flow, login_flow, protocol, room_flow
from .codec.method3 import Method3UplinkMessage

API_VERSION = 1
STATE_VERSION = 19
STARTUP_STEP_DELAY_MS = 1000


def createState(context):
    playerName = wire.encodePlayerName(context.get("playerName", wire.USER_NAME.decode("gbk"))).decode("gbk")
    runtimeMovement = context.get("runtimeMovement", wire.CLIENT_RUNTIME_MOVEMENT)
    if type(runtimeMovement) is not bool:
        raise ValueError("runtimeMovement must be a bool")
    state = {"phase": "waiting", "sessions": {}, "roomId": 1, "actorId": 1,
             "playerName": playerName, "runtimeMovement": runtimeMovement}
    if context.get('playerProfilePath'):
        catalogue = hero_resources.loadCatalogue(context['clientDirectory'])
        profile = context.get('playerProfile') or player_profile.create(catalogue)
        if not player_profile.validate(profile, catalogue):
            raise ValueError('saved formation does not match the current client catalogue')
        state['playerProfile'] = deepcopy(profile)
    return state


def validateState(state):
    if (type(state) is not dict or type(state.get("sessions")) is not dict
            or type(state.get("phase")) is not str or state.get("roomId") != 1
            or state.get("actorId") != 1):
        return False
    try:
        wire.encodePlayerName(state.get("playerName", wire.USER_NAME.decode("gbk")))
    except (ValueError, TypeError):
        return False
    if type(state.get("runtimeMovement", wire.CLIENT_RUNTIME_MOVEMENT)) is not bool or not settlement.validState(state) or not titles.valid(state):
        return False
    if 'playerProfile' in state and not player_profile.validate(state['playerProfile']):
        return False
    if 'matchedProfile' in state and not player_profile.validate(state['matchedProfile']):
        return False
    return all(type(key) is str and type(session) is dict
               and session.get("role") in ("login", "logic", "instance")
               and type(session.get("hydration")) is list
               and type(session.get("pending", {})) is dict
               and ("bot" not in session or bot.validState(session["bot"]))
               and roster.validState(session)
               and combat.validState(session)
               and weapons.validState(session)
               and ('loadoutProfile' not in session or player_profile.validate(session['loadoutProfile']))
               and all(battle.validAction(data) for data in (session, *actors.bots(session).values()))
               and player_life.validState(session)
               and battle_clock.validState(session)
               and ('killTarget' not in session or kill_flow.valid_kill_target(session['killTarget']))
               and ("bot" not in session or combat.validState(session["bot"]))
               and (not session.get("initialized") or controls.validMoveClock(session))
               for key, session in state["sessions"].items())


def migrateState(fromVersion, state):
    if fromVersion < 19 and any(session.get('role') == 'instance' and (session.get('initialized') or session.get('battleEntered'))
                               for session in state.get('sessions', {}).values()):
        raise ValueError('damage contract changed; leave the battle and restart Runtime before upgrading')
    if fromVersion not in range(1, STATE_VERSION + 1) or not validateState(state):
        raise ValueError("no migration for this state version")
    result = deepcopy(state)
    for session in result['sessions'].values():
        for data in (session, *actors.bots(session).values()):
            snapshots = (data.get('action', {}), data.get('swing') or {}, *data.get('contactSwings', []))
            if any(value.get('intent') == battle.DASH_ATTACK and 'dash' not in value for value in snapshots):
                battle.missingDash()
    if fromVersion < 12:
        for session in result['sessions'].values():
            if any(name.startswith(('battle-action-phase', 'bot-action-phase', 'bot-release', 'combat-hurt-'))
                   for name in session.get('pending', {})):
                session['rearmActionTimers'] = True
            for data in (session, *actors.bots(session).values()):
                action, swing = data.get('action', {}), data.get('swing')
                intent = action.get('intent')
                chain = battle.attackChain(intent)
                if swing is not None and chain is not None:
                    swing.update(intent=intent, state=chain[battle.strikeIndex(intent)],
                                 beganAt=swing['until'] - combat.contactEndMs(intent, dash=action.get('dash')))
    if fromVersion < 13:
        for session in result['sessions'].values():
            battle_clock.migrate(session)
    return result


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
        self.rearmActionTimers()

    def log(self, level, message):
        self.result["logs"].append({"level": level, "message": message})

    def rearmActionTimers(self):
        if self.session is None or not self.session.pop('rearmActionTimers', False):
            return
        for name, due in self.session.get('pending', {}).items():
            if name.startswith(('battle-action-phase', 'bot-action-phase', 'bot-release', 'combat-hurt-')):
                self.result['timers'].append({'id': f'{self.connection}:{name}', 'delayMs': max(0, due - self.now),
                    'event': {'type': 'timer', 'connection': self.connection, 'name': name}})

    def phase(self, name):
        self.session["phase"] = name
        activeInstance = any(session["role"] == "instance" and not session.get("leaving")
                             for session in self.state["sessions"].values())
        if 'settlement' in self.state and name not in ('settlement', 'leaving'):
            self.state['phase'] = 'settlement'
            return
        if self.session["role"] == "instance" or not activeInstance:
            self.state["phase"] = name

    def send(self, command, body, reason):
        self.result["send"].append({"connection": self.connection, "command": command,
                                    "body": body, "reason": reason})

    def later(self, name, delay):
        self.session.setdefault("pending", {})[name] = self.now + delay
        if actors.localTimer(self.session, name):
            return
        self.result["timers"].append({"id": f"{self.connection}:{name}", "delayMs": delay,
            "event": {"type": "timer", "connection": self.connection, "name": name}})

    def cancelTimers(self):
        for name in self.session.get("pending", {}):
            if actors.localTimer(self.session, name):
                continue
            self.result["timers"].append({"id": f"{self.connection}:{name}", "delayMs": -1,
                "event": {"type": "timer", "connection": self.connection, "name": name}})
        self.session["pending"] = {}

    def cancel(self, name):
        if name in self.session.get("pending", {}):
            del self.session["pending"][name]
            if actors.localTimer(self.session, name):
                return
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
    if name != 'round-end' and scene.expire(flow):
        return
    if name == 'title-request-timeout':
        titles.timeout(flow)
    elif name == "version":
        flow.send(1, protocol.encode_version_check_response(0), "version-response")
    elif name == "login":
        identity = protocol.MinimalLoginIdentity(
            wire.USER_ID, flow.playerName, user_image_id=7, level=wire.USER_LEVEL)
        flow.send(1, protocol.encode_minimal_login_success(identity), "fixed-local-login")
        flow.later("sync", STARTUP_STEP_DELAY_MS)
    elif name == "sync":
        flow.send(0x2C, flow.syncBody(), "sync-login")
        flow.phase("hydrating")
        titles.publish(flow)
    elif name == "match-result":
        flow.send(0x20, struct.pack(">Hi", 3, 0), "match-result-notify")
    elif name == "match-enter":
        flow.send(0x1E, room_flow.encode_room_enter_instance_notify(
            room_id=1, user_camp=1, notify_type=0), "match-enter-instance-notify")
        flow.phase("instance-notified")
    elif not any(handler(flow, name) for handler in (battle.timer, bot.timer, bot_life.timer, player_life.timer,
                                                    combat.timer, controls.timer, scene.timer)):
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
    scene.expire(flow)
    body, command = event["body"], event["command"]
    if type(body) is not bytes or len(body) < 2:
        raise ValueError("business message requires a two-byte selector")
    selector = int.from_bytes(body[:2], "big")
    if titles.message(flow, command, selector, body, event):
        return
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
    if loadouts.message(flow, command, selector, body):
        return
    if controls.message(flow, command, selector, body):
        return
    if weapons.message(flow, command, selector, body):
        return
    if battle.message(flow, command, selector, body):
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
        flow.log("WARN", f"unhandled command={command} selector={selector}; no speculative response")


def handleEvent(event, state, context):
    state = deepcopy(state)
    flow = Flow(event, state, context)
    eventType, key = event["type"], str(event["connection"])
    if eventType == "titles":
        titles.sync(flow, event)
    elif eventType == "operator":
        if event.get("name") != "diagnostic":
            raise ValueError("only the diagnostic command is implemented; no speculative correction bundle")
        flow.log("DEBUG", f"diagnostic phase={state['phase']} sessions={len(state['sessions'])} "
                                   f"baseline={wire.BASELINE_ID} scene=1028 actor=1 heroes={wire.HERO_IDS} "
                                   f"ground={wire.GROUND_STEP_DISTANCE}/{wire.GROUND_STEP_MS}ms "
                                   "prime=state10-state1 outcome=none clientAcceptance=unverified")
        for connection, session in list(state["sessions"].items())[:32]:
            flow.log("DEBUG",
                f"connection={connection} role={session['role']} phase={session.get('phase', 'waiting')} "
                f"actorImported={session.get('actorImported', False)} heroChosen={session.get('heroChosen', False)} "
                f"battleEntered={session.get('battleEntered', False)} pending={list(session.get('pending', {}))}")
    elif eventType == "world":
        for connection in context.get("connections", []):
            session = state["sessions"].get(str(connection))
            if session is not None and session["role"] == "instance":
                flow.connection, flow.session = connection, session
                flow.rearmActionTimers()
                scene.expire(flow)
                combat.worldUpdated(flow)
                bot.worldUpdated(flow)
    elif eventType == "connected":
        if event["role"] not in ("login", "logic", "instance") or key in state["sessions"]:
            raise ValueError("invalid or duplicate business connection")
        state["sessions"][key] = {"role": event["role"], "hydration": [], "pending": {}, "camp": 1,
                                  "authenticated": False}
    elif eventType == "closed":
        if state.get('titlePending', {}).get('connection') == event['connection']:
            state.pop('titlePending')
        state["sessions"].pop(key, None)
        if 'settlement' in state:
            state['phase'] = 'settlement'
        elif not state["sessions"]:
            state["phase"] = "waiting"
        elif not any(session["role"] == "instance" for session in state["sessions"].values()):
            state["phase"] = "lobby-data-sent"
    elif flow.session is not None:
        if eventType == "authenticated":
            if not flow.session.get("authenticated"):
                flow.session["authenticated"] = True
                name, delay = {"login": ("version", STARTUP_STEP_DELAY_MS),
                               "logic": ("login", STARTUP_STEP_DELAY_MS),
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
