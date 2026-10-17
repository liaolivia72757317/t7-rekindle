"""Local melee opponents. Decision rules are local, not recovered server AI."""
import math
import struct

from . import actors, battle, body_collision, bot_decision as decision, bot_footwork, bot_life, bot_profiles, bot_roaming, bot_tactics, bot_unstuck, contracts as wire, controls, lysd_spawn_exit
from .codec import battle_flow, move_flow, room_flow, vision_flow
from .navmesh_resource import loadNavigation

THINK_TIMER = "bot-think"
MOVE_TIMER = "bot-move"
ACTION_TIMER = "bot-action-phase"
RELEASE_TIMER = "bot-release"
THINK_MS, STRATEGY_MS, MOVE_MS, REPATH_MS = 100, 500, 50, 500
DECISION_TIMER = 'actors-decision'
REACTION_MS, SNAPSHOT_MAX_AGE_MS = bot_profiles.PROFILES['balanced'].attackDelayMs, 250
MELEE_RANGE, JOG_SPEED, RUN_SPEED = 2.0, 3.0, 5.0
RUN_DELAY_MS = controls.GROUND_RUN_DELAY_MS
TURN_SPEED, MOVE_ANGLE = 360.0, 45.0
WAYPOINT_RADIUS = .25
PARRY_MS = 600
USER_ID = 10001
NAME = "电脑玩家".encode("gbk")
ATTACK_ORDER = decision.ATTACK_ORDER
PARRY_FOR_ATTACK = decision.PARRY_FOR_ATTACK


def createBot(playerCamp):
    if playerCamp not in (1, 2):
        raise ValueError("bot requires a playing camp")
    return {"camp": 3 - playerCamp, "position": list(wire.ENEMY_POSITION), "heading": 0,
            "status": "waiting", "moving": False, "initialized": False,
            "path": [], "step": 0, "attackCount": 0, "repathAt": 0, "reactAt": None,
            "forwardMs": 0}


def vision(value, actorId=wire.BOT_ID):
    body = wire.actorVision(value["camp"], wire.HERO_ID, value.get("name", NAME.decode("gbk")).encode("gbk"),
                           runtimeMovement=True, position=tuple(value["position"]),
                           actorId=actorId, userId=wire.USER_ID + actorId - 1, currentHp=value.get("health", 100))
    # CS_ACTOR_CURR_MOVE_DATA starts at byte 24; dir is at +21.
    return body[:45] + struct.pack(">h", value["heading"]) + body[47:]


def importMember(flow, actorId=wire.BOT_ID):
    from . import combat
    value = actors.bots(flow.session).get(str(actorId))
    if value is None:
        return
    # The instance roster is independent of VISION's scene entity registry.
    flow.send(0xA, room_flow.encode_instance_update_actor_basic_info(
        server_time_ms=flow.now, instance_id=1, actor_mid=actorId, user_id=wire.USER_ID + actorId - 1,
        user_name=value.get("name", NAME.decode("gbk")).encode("gbk"), user_image_id=7, level=1, actor_state=8,
        hero_resource_id=wire.HERO_ID, camp=value["camp"], start_pattern=wire.START_PATTERN),
        "bot-instance-member")
    flow.send(0x23, wire.campExchange(flow.now, value["camp"], actorId=actorId), "bot-camp-exchange")
    combat.publishScore(flow, actorId)


def enterScene(flow, actorId=wire.BOT_ID):
    value = actors.bots(flow.session).get(str(actorId))
    if value is None:
        return
    flow.send(0x36, room_flow.encode_actor_update_state(
        user_id=wire.USER_ID + actorId - 1, actor_mid=actorId, actor_state=6, update_time_ms=flow.now), "bot-in-scene")
    # Loading-time VISION can precede scene creation; rebuild at the same
    # battle-entry boundary as the local actor, not on every VISION refresh.
    flow.send(0xE, vision_flow.encode_vision_del_event(object_mid=actorId), "bot-vision-del-at-entry")
    flow.send(0xE, vision(value, actorId), "bot-vision-add-at-entry")


def refresh(flow, actorId=wire.BOT_ID):
    action = actors.data(flow, actorId).get("action", {})
    if "state" in action:
        fields = dict(
            instance_id=actorId, seq_no=action["seq"], state=action["state"],
            state_change_ms=action["changedAt"] - flow.session["instanceStartedAt"],
            state_time_ms=action.get('durationMs', 0))
        body = (battle_flow.encode_battle_state_sync(**fields, target_rid=action["targetId"])
                if "targetId" in action else battle_flow.encode_battle_state_sync_simple(**fields))
        flow.send(battle_flow.BATTLE_COMMAND, body, "bot-action-snapshot")
    if actors.data(flow, actorId).get('life', {}).get('state') == 'dead':
        bot_life.notifyDeath(flow, actorId)


def validState(value):
    return (type(value) is dict and value.get("camp") in (1, 2)
            and validPosition(value.get("position")) and type(value.get("heading")) is int
            and -180 <= value["heading"] <= 180 and type(value.get("status")) is str
            and all(type(value.get(key)) is bool for key in ("moving", "initialized"))
            and type(value.get("path")) is list and all(validPosition(p) for p in value["path"])
            and type(value.get("step")) is int and 0 <= value["step"] <= len(value["path"])
            and all(type(value.get(key)) is int and value[key] >= 0 for key in ("attackCount", "repathAt"))
            and (value.get("reactAt") is None or type(value["reactAt"]) is int)
            and type(value.get("forwardMs", 0)) is int and 0 <= value.get("forwardMs", 0) <= RUN_DELAY_MS
            and type(value.get("lastTurnAt", 0)) is int and value.get("lastTurnAt", 0) >= 0
            and (not value["initialized"] or type(value.get("heightOffset")) in (int, float)
                 and math.isfinite(value["heightOffset"]))
            and decision.validState(value) and bot_roaming.validState(value) and bot_life.validState(value)
            and lysd_spawn_exit.validState(value) and bot_tactics.validState(value)
            and bot_footwork.validState(value) and bot_unstuck.validState(value))


def validPosition(value):
    return (type(value) in (list, tuple) and len(value) == 3
            and all(type(v) in (float, int) and math.isfinite(v) and abs(v) < 100000 for v in value))


def navigationFor(flow):
    return loadNavigation(flow.context.get('clientDirectory', ''))


def world(flow, actorId=wire.BOT_ID, nav=None):
    snapshot = actors.world(flow)
    if snapshot is None or not actors.active(flow, actorId):
        return None
    if "actors" not in snapshot:
        return snapshot if "bots" not in flow.session and all(
            validPosition(snapshot.get(name)) for name in ("player", "bot")) else None
    return bot_roaming.sense(flow, nav or navigationFor(flow), snapshot, actorId)


def ownPose(flow, actorId):
    snapshot = actors.world(flow)
    if snapshot is None or 'bots' in flow.session and 'actors' not in snapshot:
        return None
    return actors.pose(snapshot, actorId)


def status(flow, text, actorId=wire.BOT_ID):
    value = actors.data(flow, actorId)
    if value["status"] != text:
        value["status"] = text
        flow.log("DEBUG", f"bot actor={actorId} status={text}")


def movementAxes(angle):
    return ((-1, 0), (-1, -1), (0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1))[
        math.floor((angle + 22.5) / 45) % 8]


def broadcast(flow, moving, travelHeading=None, actorId=wire.BOT_ID):
    value = actors.data(flow, actorId)
    tick = flow.now - flow.session["instanceStartedAt"]
    if not 0 <= tick <= controls.MAX_MOVE_TICK:
        raise ValueError("bot movement clock outside instance")
    state, forward, lateral, speed = 1, 0, 0, 0
    if moving:
        angle = headingDelta(value["heading"], travelHeading if travelHeading is not None else value["heading"])
        forward, lateral = -round(1000 * math.cos(math.radians(angle))), -round(1000 * math.sin(math.radians(angle)))
        axes = movementAxes(angle)
        running = (not bot_footwork.lockReason(value) and value.get("forwardMs", 0) >= RUN_DELAY_MS
                   and axes in controls.GROUND_RUN_STATES)
        state = (controls.GROUND_RUN_STATES if running else controls.GROUND_WALK_STATES)[axes]
        speed = round(1000 * (RUN_SPEED if running else JOG_SPEED))
    # Runtime retains this native composite's intent commands, omitting PosCommand for RID 2..20.
    flow.send(2, move_flow.encode_move_bc_with_system_and_active(
        server_tick=tick, target_instance_id=actorId, position=tuple(value["position"]),
        direction_yaw=value["heading"], state=state, forward_back=forward, left_right=lateral,
        current_velocity=speed, max_velocity=25000 if moving else 0,
        system_group=actorId, active=1, acceleration=0), "bot-move" if moving else "bot-stop")
    value["moving"] = moving


def stopMotion(flow, actorId=wire.BOT_ID, *, publish=True, resetRecovery=True):
    value = actors.data(flow, actorId)
    flow.cancel(actors.timerName(MOVE_TIMER, actorId))
    value["path"] = []
    value["step"] = 0
    value["forwardMs"] = 0
    value.pop('footwork', None)
    value.pop('assist', None)
    value.pop('motionProbe', None)
    if resetRecovery:
        value.pop('unstuck', None)
        value.pop('meshRecovery', None)
    if value["moving"] and publish and battle.botInstanceMatches(flow):
        if 'spawnExit' in value:
            lysd_spawn_exit.steer(flow, actorId, False)
        else:
            broadcast(flow, False, actorId=actorId)
    value["moving"] = False


def stop(flow, actorId=wire.BOT_ID, *, publish=True):
    if str(actorId) not in actors.bots(flow.session):
        return
    stopMotion(flow, actorId, publish=publish)
    flow.cancel(actors.timerName(THINK_TIMER, actorId))
    flow.cancel(actors.timerName(RELEASE_TIMER, actorId))
    flow.cancel(actors.timerName(bot_life.RESPAWN_TIMER, actorId))
    battle.resetAction(flow, actorId)
    actors.data(flow, actorId).pop("observedPlayer", None)
    actors.data(flow, actorId).pop('tactic', None)
    actors.data(flow, actorId).pop('opponentId', None)
    bot_roaming.forget(actors.data(flow, actorId))
    actors.data(flow, actorId).pop('roamAnchor', None)
    for key in ('threats', 'targetSince', 'pressAt', 'stepPlan', 'pursuit'):
        actors.data(flow, actorId).pop(key, None)
    actors.data(flow, actorId)["reactAt"] = None
    status(flow, "stopped", actorId)


def start(flow):
    for actorId in (int(key) for key in actors.bots(flow.session)):
        flow.later(actors.timerName(THINK_TIMER, actorId), THINK_MS + bot_profiles.rhythm(flow, actorId, 0, 350))
    if "bots" in flow.session:
        flow.later(actors.PULSE_TIMER, THINK_MS)


def headingDelta(current, target):
    return (target - current + 180) % 360 - 180


def turnToward(flow, heading, actorId=wire.BOT_ID):
    value = actors.data(flow, actorId)
    elapsed = max(0, min(controls.MAX_GROUND_ELAPSED_MS,
                         flow.now - value.get("lastTurnAt", flow.now - THINK_MS)))
    value["lastTurnAt"] = flow.now
    limit = TURN_SPEED * elapsed / 1000
    change = max(-limit, min(limit, headingDelta(value["heading"], heading)))
    value["heading"] = round((value["heading"] + change + 180) % 360 - 180)
    return abs(headingDelta(value["heading"], heading))


def faceTarget(flow, target, actorId=wire.BOT_ID):
    value = actors.data(flow, actorId)
    dx, dy = target[0] - value["position"][0], target[1] - value["position"][1]
    if dx == 0 and dy == 0:
        return True
    heading = int(round(math.degrees(math.atan2(-dy, dx))))
    previous = value["heading"]
    remaining = turnToward(flow, heading, actorId)
    if value["heading"] != previous and not value['moving']:
        flow.send(2, move_flow.encode_move_in_air_direct_bc(
            server_tick=flow.now - flow.session["instanceStartedAt"], target_instance_id=actorId,
            direction_yaw=value["heading"], left_right=0, forward_back=0), "bot-face-target")
    return remaining == 0


def searchTurn(flow, actorId):
    value = actors.data(flow, actorId)
    elapsed = max(0, min(controls.MAX_GROUND_ELAPSED_MS, flow.now - value.get('lastTurnAt', flow.now - THINK_MS)))
    heading = value['heading'] + 180 * elapsed / 1000
    faceTarget(flow, bot_tactics.pointAt(value['position'], -math.radians(heading), 1.), actorId)


def parrySucceeded(flow, actorId):
    if actors.data(flow, actorId).get('profile') == 'defensive':
        # Mark release before the zero-duration success phase can finish.
        battle.releaseAction(flow, battle.BLOCK_RELEASE, actorId)
        flow.cancel(actors.timerName(RELEASE_TIMER, actorId))


def combatThink(flow, actorId=wire.BOT_ID):
    """Only the existing target: observe, turn and choose an action; never plan movement."""
    value = actors.data(flow, actorId)
    if not battle.enabled(flow) or not actors.active(flow, actorId) or not value['initialized']:
        return
    snapshot = actors.world(flow)
    source = actors.pose(snapshot, actorId)
    if source is None:
        stopMotion(flow, actorId)
        for key in ('observedPlayer', 'tactic', 'stepPlan', 'pressAt', 'pursuit'):
            value.pop(key, None)
        value['reactAt'] = None
        if not battle.botInstanceMatches(flow):
            battle.resetAction(flow, actorId)
            flow.cancel(actors.timerName(RELEASE_TIMER, actorId))
        status(flow, 'world-unavailable', actorId)
        return
    targetId = value.get('opponentId', wire.ACTOR_ID if 'bots' not in flow.session else None)
    if targetId is None:
        return
    if (targetId not in actors.ids(flow.session) or not actors.active(flow, targetId)
            or actors.data(flow, targetId).get('camp', 1) == value['camp']
            or actors.pose(snapshot, targetId) is None):
        stopMotion(flow, actorId)
        for key in ('opponentId', 'observedPlayer', 'tactic', 'stepPlan', 'pressAt', 'pursuit'):
            value.pop(key, None)
        value.update(reactAt=None, senseAt=0)
        flow.later(actors.timerName(THINK_TIMER, actorId), 0)
        status(flow, 'search', actorId)
        return
    pair = actors.pair(snapshot, actorId, targetId)
    value['position'] = list(source['position'])
    decision.observe(flow, actorId)
    if value['reactAt'] is None:
        value['reactAt'] = flow.now + bot_profiles.vary(flow, actorId, bot_profiles.get(value).attackDelayMs, .65, 1.2)
    locked = bot_footwork.lockReason(value)
    if locked:
        status(flow, locked, actorId)
        return
    if math.dist(pair['bot'], pair['player']) > MELEE_RANGE:
        return
    if not faceTarget(flow, pair['player'], actorId) and not decision.clearContact(flow, pair, actorId):
        status(flow, 'turning', actorId)
        return
    choice = decision.choose(flow, pair, actorId)
    if choice.kind == 'wait':
        moving = value.get('footwork', value.get('tactic', {})).get('mode') if value['path'] else None
        status(flow, moving if moving and choice.reason in ('ready', 'contact-unavailable') else choice.reason, actorId)
        return
    # Only a proposed new action needs the short melee reach check, not every combat tick.
    try:
        route = navigationFor(flow).findPath(pair['bot'], pair['player'])
    except (ValueError, OSError) as error:
        stopMotion(flow, actorId)
        status(flow, 'navigation-invalid' if isinstance(error, ValueError) else 'navigation-unavailable', actorId)
        return
    if route.status != 'ready' or sum(math.dist(a, b) for a, b in zip(route.points, route.points[1:])) > MELEE_RANGE:
        return
    parry = choice.kind == 'parry'
    battle.startAction(flow, choice.intent, released=not parry, actorId=actorId)
    if parry:
        flow.later(actors.timerName(RELEASE_TIMER, actorId),
                   bot_profiles.vary(flow, actorId, bot_profiles.get(value).guardMs, .8, 1.2))
    else:
        value['attackCount'] += 1
        value.update(lastAttack=choice.intent, decisionSeed=choice.seed)
        value.pop('pressAt', None)
    value['reactAt'] = None
    flow.log('DEBUG', f'bot-decision actor={actorId} target={targetId} reason={choice.reason} intent={choice.intent}')
    status(flow, choice.reason, actorId)


def think(flow, actorId=wire.BOT_ID, *, combat=True):
    if not bot_life.ready(flow, actorId):
        return
    value = actors.data(flow, actorId)
    observed = ownPose(flow, actorId)
    if observed is None:
        stopMotion(flow, actorId)
        value.pop('pressAt', None)
        value.pop('stepPlan', None)
        value.pop('pursuit', None)
        if not battle.botInstanceMatches(flow):
            battle.resetAction(flow, actorId)
            flow.cancel(actors.timerName(RELEASE_TIMER, actorId))
        value["reactAt"] = None
        value.pop("observedPlayer", None)
        value.pop('tactic', None)
        status(flow, "world-unavailable", actorId)
        return
    if value.get('action', {}).get('reaction') == 'parry-success':
        parrySucceeded(flow, actorId)
    try:
        nav = navigationFor(flow)
    except (ValueError, OSError) as error:
        value.pop('tactic', None)
        value.pop('stepPlan', None)
        value.pop('pursuit', None)
        stopMotion(flow, actorId)
        status(flow, "navigation-unavailable" if isinstance(error, OSError) else "navigation-invalid", actorId)
        return
    if lysd_spawn_exit.handle(flow, nav, observed, actorId):
        return
    actual = nav.project(observed['position'])
    if (actual is None or 'meshRecovery' in value) and bot_unstuck.returnToMesh(flow, nav, observed, actorId):
        return
    snapshot = world(flow, actorId, nav)
    if snapshot is not None:
        decision.observe(flow, actorId)
    if not value["initialized"]:
        value["position"] = list(observed['position'])
        value["heightOffset"] = observed['position'][2] - actual[2]
        if not bot_footwork.lockReason(value):
            battle.syncState(flow, battle_flow.ACT_STATE_IDLE, actorId)
        value["initialized"] = True
    if math.dist(observed['position'], value["position"]) > 1.0:
        stopMotion(flow, actorId)
        value.pop('stepPlan', None)
        value["repathAt"] = 0
    value["position"] = list(observed['position'])
    value["heightOffset"] = observed['position'][2] - actual[2]
    battle.actionState(flow, actorId)
    locked = bot_footwork.lockReason(value)
    if snapshot is None:
        value.pop('pressAt', None)
        value.pop('stepPlan', None)
        value.pop('pursuit', None)
        value.pop('tactic', None)
        value.pop('footwork', None)
        value['reactAt'] = None
        value.pop('observedPlayer', None)
        if 'bots' not in flow.session:
            stopMotion(flow, actorId)
            status(flow, 'world-unavailable', actorId)
            return
        recovery = recoverMotion(flow, actorId, nav=nav)
        if recovery is not None:
            status(flow, locked or recovery, actorId)
            return
        if not locked and lysd_spawn_exit.returnToBattle(flow, nav, observed, actorId):
            return
        lysd_spawn_exit.pause(flow, actorId)
        assistance = bot_tactics.assist(flow, nav, actorId)
        if assistance is not None:
            if math.dist(value['position'][:2], assistance.goal[:2]) > bot_tactics.ARRIVAL_RADIUS:
                if follow(flow, nav, assistance.goal, '' if locked else 'assist', actorId, assistance.route) and locked:
                    status(flow, locked, actorId)
            else:
                stopMotion(flow, actorId)
                searchTurn(flow, actorId)
                status(flow, locked or 'assist', actorId)
            return
        goal, mode = bot_roaming.goal(flow, nav, actorId)
        if goal is None:
            stopMotion(flow, actorId)
            searchTurn(flow, actorId)
            status(flow, locked or mode, actorId)
        else:
            following = follow(flow, nav, goal, '' if locked else mode, actorId)
            if not following and mode == 'search':
                searchTurn(flow, actorId)
                status(flow, locked or mode, actorId)
            elif following and locked:
                status(flow, locked, actorId)
        return
    lysd_spawn_exit.pause(flow, actorId)
    target = nav.project(snapshot['player'])
    if target is None:
        value.pop('tactic', None)
        value.pop('stepPlan', None)
        value.pop('pursuit', None)
        stopMotion(flow, actorId)
        status(flow, 'outside-mesh', actorId)
        return
    distance = math.dist(value["position"], snapshot["player"])
    if distance <= MELEE_RANGE:
        value.setdefault('lastTurnAt', flow.now - THINK_MS)
    value.pop('pressAt', None)
    if value['reactAt'] is None:
        # Prepare while closing or finishing the current action, not after arriving.
        value['reactAt'] = flow.now + bot_profiles.vary(flow, actorId, bot_profiles.get(value).attackDelayMs, .65, 1.2)
    recovery = recoverMotion(flow, actorId, nav=nav)
    threat = distance <= MELEE_RANGE and decision.defend(flow, snapshot, actorId) is not None
    footwork = bot_footwork.plan(flow, nav, snapshot, actorId) if recovery is None else None
    engaging = footwork is not None or distance <= MELEE_RANGE and decision.clearContact(flow, snapshot, actorId)
    if engaging:
        value.pop('tactic', None)
    tactic = bot_tactics.plan(flow, nav, snapshot, actorId) if not engaging and recovery is None and locked not in (
        'hurt', 'parried', 'parry-success', 'terrain-rebound') else None
    positioning, motionMode = False, recovery
    if tactic is not None and not threat:
        if math.dist(value['position'][:2], tactic.goal[:2]) > bot_tactics.ARRIVAL_RADIUS:
            value.pop('footwork', None)
            value.pop('stepPlan', None)
            if not follow(flow, nav, tactic.goal, '', actorId, tactic.route):
                return
            positioning, motionMode = True, tactic.mode
        elif tactic.mode == 'support' and distance > MELEE_RANGE:
            stopMotion(flow, actorId)
            faceTarget(flow, target, actorId)
            status(flow, locked or 'support', actorId)
            return
    if not positioning and recovery is None:
        if footwork is not None:
            value['repathAt'] = 0
            if not follow(flow, nav, footwork.goal, '', actorId, footwork.route):
                return
            value['footwork'] = dict(targetId=snapshot.get('targetId', wire.ACTOR_ID), mode=footwork.mode)
            motionMode = footwork.mode
        elif distance <= MELEE_RANGE:
            route = nav.findPath(value['position'], snapshot['player'])
            if route.status == 'ready' and sum(math.dist(a, b) for a, b in zip(route.points, route.points[1:])) <= MELEE_RANGE:
                stopMotion(flow, actorId)
            elif follow(flow, nav, snapshot['player'], '', actorId, route):
                motionMode = 'approach'
            else:
                return
        else:
            value.pop('footwork', None)
            if not follow(flow, nav, snapshot['player'], '', actorId):
                return
            motionMode = 'approach'
    if locked or motionMode:
        status(flow, locked or motionMode, actorId)
    if combat:
        combatThink(flow, actorId)


def follow(flow, nav, goal, mode, actorId, route=None):
    value = actors.data(flow, actorId)
    if 'routeGoal' in value and math.dist(value['routeGoal'], goal) > .5:
        value['repathAt'] = 0
    if not value["path"] or flow.now >= value["repathAt"]:
        route = route or nav.findPath(value["position"], goal)
        value["repathAt"] = flow.now + bot_profiles.vary(flow, actorId, REPATH_MS, .7, 1.3)
        if route.status != "ready":
            stopMotion(flow, actorId)
            value.pop('roamGoal', None)
            status(flow, route.status, actorId)
            return False
        value["path"], value["step"] = [list(p) for p in route.points[1:]], 0
        value['routeGoal'] = list(goal)
    if mode:
        status(flow, mode, actorId)
    if actors.timerName(MOVE_TIMER, actorId) not in flow.session["pending"]:
        value['lastMoveAt'] = flow.now
        value.setdefault('lastTurnAt', flow.now)
        flow.later(actors.timerName(MOVE_TIMER, actorId), MOVE_MS)
    return True


def recoverMotion(flow, actorId, heading=None, nav=None):
    value = actors.data(flow, actorId)
    if 'unstuck' not in value and heading is None:
        return None
    try:
        nav = nav or navigationFor(flow)
        recovery = bot_unstuck.plan(flow, nav, actorId, heading)
    except (ValueError, OSError):
        stopMotion(flow, actorId)
        return 'navigation-unavailable'
    if recovery is None:
        return None
    if recovery.goal is None:
        stopMotion(flow, actorId, resetRecovery=False)
        return 'unstuck-wait'
    return 'unstuck' if follow(flow, nav, recovery.goal, '', actorId, recovery.route) else value['status']


def move(flow, actorId=wire.BOT_ID):
    if not actors.active(flow, actorId):
        return
    value = actors.data(flow, actorId)
    if flow.now < value.get('movementUntil', 0):
        stopMotion(flow, actorId)
        return
    observed = ownPose(flow, actorId)
    if observed is None:
        stopMotion(flow, actorId)
        value.pop('pressAt', None)
        value.pop('stepPlan', None)
        value.pop("observedPlayer", None)
        value.pop('pursuit', None)
        value.pop('tactic', None)
        value["reactAt"] = None
        status(flow, "world-unavailable", actorId)
        return
    value['position'] = list(observed['position'])
    try:
        nav = navigationFor(flow)
    except (ValueError, OSError):
        stopMotion(flow, actorId)
        status(flow, 'navigation-unavailable', actorId)
        return
    if nav.project(observed['position']) is None or 'meshRecovery' in value:
        bot_unstuck.returnToMesh(flow, nav, observed, actorId)
        return
    recovery = recoverMotion(flow, actorId)
    if recovery is not None and not value['path']:
        status(flow, bot_footwork.lockReason(value) or recovery, actorId)
        return
    look = bot_footwork.facingTarget(flow, actorId)
    if 'footwork' in value and look is None:
        stopMotion(flow, actorId)
        return
    elapsed = max(0, min(controls.MAX_GROUND_ELAPSED_MS, flow.now - value.get("lastMoveAt", flow.now)))
    value["lastMoveAt"] = flow.now
    position = list(value["position"])
    position[2] -= value["heightOffset"]
    while value["step"] < len(value["path"]) and math.dist(position, value["path"][value["step"]]) <= WAYPOINT_RADIUS:
        value["step"] += 1
    if value["step"] >= len(value["path"]):
        broadcast(flow, False, actorId=actorId)
        stopMotion(flow, actorId)
        return
    target = value["path"][value["step"]]
    heading = math.degrees(math.atan2(-(target[1] - position[1]), target[0] - position[0]))
    look = look if look is not None else bot_tactics.facingTarget(flow, actorId)
    facing = (math.degrees(math.atan2(-(look[1] - position[1]), look[0] - position[0])) if look is not None
              else value['heading'] if recovery is not None else heading)
    if turnToward(flow, facing, actorId) > MOVE_ANGLE:
        value["forwardMs"] = 0
        value.pop('motionProbe', None)
        broadcast(flow, False, actorId=actorId)
        flow.later(actors.timerName(MOVE_TIMER, actorId), MOVE_MS)
        return
    if recovery is None and bot_unstuck.observe(flow, actorId):
        recovery = recoverMotion(flow, actorId, heading)
        status(flow, bot_footwork.lockReason(value) or recovery, actorId)
        if not value['path']:
            return
        target = value['path'][value['step']]
        heading = math.degrees(math.atan2(-(target[1] - position[1]), target[0] - position[0]))
    value["forwardMs"] = (0 if recovery is not None or bot_footwork.lockReason(value) else
                          min(RUN_DELAY_MS, value.get("forwardMs", 0) + elapsed))
    distance = math.dist(position, target)
    # This endpoint chooses steering only; the native controller integrates motion.
    travel = min(distance, RUN_SPEED * MOVE_MS / 1000)
    position = [a + (b - a) * travel / distance for a, b in zip(position, target)]
    position[2] += value["heightOffset"]
    if 'bots' in flow.session:
        try:
            position = body_collision.moveBot(flow, nav, actorId, value['position'], position)
        except (ValueError, OSError):
            stopMotion(flow, actorId)
            status(flow, 'navigation-unavailable', actorId)
            return
        travelled = math.dist(value['position'], position)
        if travelled < .001:
            value['forwardMs'] = 0
            broadcast(flow, False, actorId=actorId)
            flow.later(actors.timerName(MOVE_TIMER, actorId), MOVE_MS)
            return
        heading = math.degrees(math.atan2(-(position[1] - value['position'][1]), position[0] - value['position'][0]))
    if movementAxes(headingDelta(value['heading'], heading)) not in controls.GROUND_RUN_STATES:
        value['forwardMs'] = 0
    broadcast(flow, True, heading, actorId)
    flow.later(actors.timerName(MOVE_TIMER, actorId), MOVE_MS)


def trackStationaryAttack(flow, actorId):
    value = actors.data(flow, actorId)
    action = value.get('action', {})
    targetId = value.get('opponentId')
    if (not actors.active(flow, actorId) or bot_footwork.lockReason(value) != 'attack'
            or action.get('intent') not in battle.ATTACK_CHAINS or action['index'] > 2
            or targetId not in actors.ids(flow.session) or not actors.active(flow, targetId)):
        return
    snapshot = actors.world(flow)
    source, target = actors.pose(snapshot, actorId), actors.pose(snapshot, targetId)
    if (source is None or target is None
            or math.dist(source['position'][:2], target['position'][:2]) > bot_footwork.ENGAGE_RANGE
            or abs(source['position'][2] - target['position'][2]) > 1):
        return
    value['position'] = list(source['position'])
    faceTarget(flow, target['position'], actorId)


def worldUpdated(flow):
    if 'bots' not in flow.session or not battle.enabled(flow):
        return
    for actorId in (int(key) for key in actors.bots(flow.session)):
        name = actors.timerName(MOVE_TIMER, actorId)
        if name in flow.session.get('pending', {}):
            # Follow the current route from fresh poses, not the next AI deadline.
            flow.cancel(name)
            timer(flow, name)
        else:
            trackStationaryAttack(flow, actorId)


def stopAll(flow, *, publish=True):
    flow.cancel(actors.PULSE_TIMER)
    flow.cancel(DECISION_TIMER)
    for key in actors.bots(flow.session):
        stop(flow, int(key), publish=publish)


def timer(flow, name):
    if name in (actors.PULSE_TIMER, DECISION_TIMER):
        from .app import handleTimer
        if not battle.enabled(flow):
            stopAll(flow)
            return True
        if name == actors.PULSE_TIMER:
            for key in actors.bots(flow.session):
                combatThink(flow, int(key))
        # Strategy/lifecycle only. Weapon phases and movement have separate clocks.
        due = sorted((deadline, key) for key, deadline in flow.session.get("pending", {}).items()
                     if deadline <= flow.now and key.startswith((THINK_TIMER, bot_life.RESPAWN_TIMER)))
        if name == DECISION_TIMER and due:
            # One expensive decision per callback lets native animation deadlines run between actors.
            handleTimer(flow, due[0][1])
            due = due[1:]
        if due and DECISION_TIMER not in flow.session.get('pending', {}):
            flow.later(DECISION_TIMER, 0)
        if name == actors.PULSE_TIMER:
            flow.later(name, THINK_MS)
        return True
    for key in actors.bots(flow.session):
        actorId = int(key)
        names = [actors.timerName(base, actorId) for base in (THINK_TIMER, MOVE_TIMER, RELEASE_TIMER)]
        if name not in names:
            continue
        if not battle.enabled(flow):
            stop(flow, actorId)
        elif name == names[0]:
            legacy = 'bots' not in flow.session
            think(flow, actorId, combat=legacy)
            value = actors.data(flow, actorId)
            if value.get('life', {}).get('state') != 'dead':
                flow.later(name, THINK_MS if legacy or not value['initialized'] else STRATEGY_MS)
        elif name == names[1]:
            move(flow, actorId)
        elif battle.botInstanceMatches(flow) and actors.active(flow, actorId):
            battle.releaseAction(flow, battle.BLOCK_RELEASE, actorId)
        else:
            battle.resetAction(flow, actorId)
        return True
    return False
