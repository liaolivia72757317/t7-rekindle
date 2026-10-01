"""Input-driven 20Hz ground fixture and legacy timer compatibility."""
import math
import struct

from . import contracts as wire
from .codec import move_flow

GROUND_RUN_DELAY_MS = 2000
MAX_GROUND_ELAPSED_MS = 100
MOVE_CLOCK = "instance-relative-ms-v1"
MAX_MOVE_TICK = 0x7FFFFFFF
GROUND_WALK_STATES = {
    (-1, 0): 2, (-1, 1): 3, (0, 1): 4, (1, 1): 5,
    (1, 0): 6, (1, -1): 7, (0, -1): 8, (-1, -1): 9,
}
GROUND_RUN_STATES = {(-1, 0): 10, (-1, 1): 11, (-1, -1): 12}


def activate(flow):
    tick = nextGroundTick(flow)
    flow.send(2, move_flow.encode_move_notify_active(
        server_tick=tick, target_instance_id=1, active=1), "instance-move-notify-active-after-in-scene")
    ground = groundState(flow)
    ground["tick"] = ground["timingTick"] = tick


def project(mask, heading):
    return move_flow.project_standard_ground_step(
        w_pressed=mask & 1, a_pressed=(mask >> 1) & 1,
        s_pressed=(mask >> 2) & 1, d_pressed=(mask >> 3) & 1,
        heading_degrees=heading, step_distance=wire.GROUND_STEP_DISTANCE)


def runtimeMovement(flow) -> bool:
    return flow.state.get("runtimeMovement", wire.CLIENT_RUNTIME_MOVEMENT) is True


def movementMode(flow) -> str:
    return wire.RUNTIME_MOVEMENT_MODE if runtimeMovement(flow) else "server-ground-v1"


def cancelMotionTimers(flow) -> None:
    for name in ("ground-step", "direction-prime-start", "direction-prime-stop"):
        flow.cancel(name)


def localReport(flow, selector, body):
    if selector == 3:
        wire.exact(body, 21, "runtime-local-heading")
        heading = struct.unpack_from(">h", body, 7)[0]
        if not -180 <= heading <= 180:
            raise ValueError("runtime local heading must be in -180..180")
        position = list(struct.unpack_from(">fff", body, 9))
    else:
        wire.exact(body, 24, "runtime-local-key-state")
        category = struct.unpack_from(">i", body, 2)[0]
        keys = body[6:12]
        if category != 1:
            return False
        if any(value not in (0, 1) for value in keys):
            raise ValueError("runtime local key state must be 0 or 1")
        position = list(struct.unpack_from(">fff", body, 12))
    if not all(math.isfinite(value) for value in position):
        raise ValueError("non-finite runtime local position")
    if not flow.session.get("battleEntered"):
        return True
    cancelMotionTimers(flow)
    ground = groundState(flow)
    if ground["position"] is None:
        flow.result["logs"].append("client-runtime-position-accepted; no-motion-echo")
    ground["position"] = position
    if selector == 3:
        ground["heading"] = heading
    else:
        ground["mask"] = sum(keys[index] << index for index in range(4))
        ground["crouched"] = bool(keys[4])
        ground["jumpPressed"] = bool(keys[5])
    return True


def groundEnabled(flow) -> bool:
    if not flow.session.get("battleEntered"):
        return False
    if "groundEnabled" in flow.session:
        return flow.session["groundEnabled"] is True
    return flow.session.get("phase") == "game-sent"


def enableGround(flow) -> None:
    if runtimeMovement(flow):
        cancelMotionTimers(flow)
        flow.session["groundEnabled"] = True
        return
    if not groundEnabled(flow):
        ground = groundState(flow)
        if ground["mask"] != -1:
            ground.update(mask=-1, moveStartedAt=None, lastAdvanceAt=None, nextDeadline=None)
        for name in ("ground-step", "direction-prime-start", "direction-prime-stop"):
            flow.cancel(name)
    flow.session["groundEnabled"] = True


def groundState(flow):
    ground = flow.session.setdefault("ground", {"mask": -1, "heading": 0,
                                               "position": None, "tick": 0})
    ground.setdefault("moveStartedAt", None)
    return ground


def groundMoveState(ground: dict, projection: move_flow.GroundMoveProjection, now: int) -> int:
    if not projection.moving:
        ground["moveStartedAt"] = None
        return move_flow.MOVE_GROUND_STATE_STOP
    if ground["moveStartedAt"] is None:
        ground["moveStartedAt"] = now
    axes = (projection.forward_back, projection.left_right)
    elapsed = now - ground["moveStartedAt"]
    if elapsed >= GROUND_RUN_DELAY_MS and axes in GROUND_RUN_STATES:
        return GROUND_RUN_STATES[axes]
    return GROUND_WALK_STATES[axes]


def nextDeadline(deadline: int, now: int) -> int:
    if deadline > now:
        return deadline
    return deadline + ((now - deadline) // wire.GROUND_STEP_MS + 1) * wire.GROUND_STEP_MS


def groundTiming(flow, ground: dict) -> None:
    pending = flow.session.get("pending", {}).get("ground-step")
    timerDue = getattr(flow, "timerDue", None)
    valid = (ground.get("timingTick") == ground["tick"]
             and type(ground.get("lastAdvanceAt")) is int
             and type(ground.get("nextDeadline")) is int)
    if not valid:
        ground["lastAdvanceAt"] = flow.now
        ground["nextDeadline"] = (pending if pending is not None else
                                  timerDue if timerDue is not None else
                                  flow.now + wire.GROUND_STEP_MS)
        ground["timingTick"] = ground["tick"]
        flow.result["logs"].append("ground-timing-rebase tick=" + str(ground["tick"]))
    if flow.now < ground["lastAdvanceAt"]:
        raise ValueError("ground monotonic time moved backwards")
    if pending is None and timerDue is None:
        ground["nextDeadline"] = nextDeadline(ground["nextDeadline"], flow.now)
        flow.later("ground-step", ground["nextDeadline"] - flow.now)
        flow.result["logs"].append("ground-timing-missing-timer repaired")


def advanceGround(flow) -> None:
    ground = groundState(flow)
    if ground["mask"] < 0 or ground["position"] is None:
        return
    projection = project(ground["mask"], ground["heading"])
    if not projection.moving:
        return
    groundTiming(flow, ground)
    elapsed = flow.now - ground["lastAdvanceAt"]
    applied = min(elapsed, MAX_GROUND_ELAPSED_MS)
    if applied:
        # 坐标每次写回Single，不保留额外的高精度累加器。
        position = [struct.unpack(">f", struct.pack(">f", value + delta * applied / wire.GROUND_STEP_MS))[0]
                    for value, delta in zip(ground["position"], projection.delta)]
        if not all(math.isfinite(value) for value in position):
            raise ValueError("non-finite ground position")
        ground["position"] = position
    ground["lastAdvanceAt"] = flow.now
    if elapsed > applied:
        flow.result["logs"].append("ground-timing-clamp droppedMs=" + str(elapsed - applied))


def validMoveClock(session: dict) -> bool:
    return (session.get("moveClock") == MOVE_CLOCK
            and type(session.get("instanceStartedAt")) is int
            and session["instanceStartedAt"] >= 0)


def nextGroundTick(flow) -> int:
    if not validMoveClock(flow.session):
        raise ValueError("new instance required for instance-relative MOVE clock")
    # 客户端给 MOVE tick 加实例起点；同毫秒消息共享时间，不额外积分。
    tick = flow.now - flow.session["instanceStartedAt"]
    if not 0 <= tick <= MAX_MOVE_TICK:
        raise ValueError("MOVE elapsed time outside nonnegative Int32 range")
    if tick < groundState(flow)["tick"]:
        raise ValueError("MOVE time moved backwards; new instance required after clock change")
    return tick


def broadcastHeading(flow):
    ground = groundState(flow)
    position = ground["position"] if ground["position"] is not None else wire.POSITION
    tick = nextGroundTick(flow)
    body = move_flow.encode_move_direct_bc(
        server_tick=tick, target_instance_id=1, direction_yaw=ground["heading"],
        position=tuple(position))
    ground["tick"] = tick
    ground["timingTick"] = tick
    flow.send(2, body, "move-ground-heading-direct-bc")


def broadcast(flow, position, heading, state, forwardBack, leftRight, reason):
    ground = groundState(flow)
    moving = state != move_flow.MOVE_GROUND_STATE_STOP
    tick = nextGroundTick(flow)
    body = move_flow.encode_move_bc_with_system_and_active(
        server_tick=tick, target_instance_id=1, position=tuple(position),
        direction_yaw=heading, state=state,
        forward_back=forwardBack * 1000, left_right=leftRight * 1000,
        current_velocity=5000 if moving else 0, max_velocity=25000 if moving else 0,
        system_group=3, active=1, acceleration=0)
    ground["tick"] = tick
    ground["timingTick"] = tick
    flow.send(2, body, reason)


def timer(flow, name):
    if name not in ("direction-prime-start", "direction-prime-stop", "ground-step"):
        return False
    if runtimeMovement(flow):
        cancelMotionTimers(flow)
        return True
    if (flow.session.get("leaving") or not flow.session.get("battleEntered")
            or not groundEnabled(flow)):
        return True
    if name == "direction-prime-start":
        return True
    elif name == "direction-prime-stop":
        ground = groundState(flow)
        if ground["mask"] >= 0 and project(ground["mask"], ground["heading"]).moving:
            return True
        position = ground["position"] if ground["position"] is not None else wire.POSITION
        broadcast(flow, position, ground["heading"], 1, 0, 0, "instance-legacy-prime-stop")
    else:
        ground = groundState(flow)
        if ground["mask"] < 0:
            return True
        projection = project(ground["mask"], ground["heading"])
        if not projection.moving:
            return True
        advanceGround(flow)
        state = groundMoveState(ground, projection, flow.now)
        broadcast(flow, ground["position"], ground["heading"], state, projection.forward_back,
                  projection.left_right, "move-ground-periodic-position-echo")
        skipped = max(0, (flow.now - ground["nextDeadline"]) // wire.GROUND_STEP_MS)
        if skipped:
            flow.result["logs"].append("ground-timing-skip missed=" + str(skipped))
        ground["nextDeadline"] = nextDeadline(ground["nextDeadline"], flow.now)
        flow.later("ground-step", ground["nextDeadline"] - flow.now)
    return True


def message(flow, command, selector, body):
    if (command != 2 or selector not in (3, 52) or flow.session["role"] != "instance"
            or flow.session.get("leaving")):
        return False
    if flow.session.get("controlBaseline") != wire.BASELINE_ID:
        return False
    if runtimeMovement(flow):
        return localReport(flow, selector, body)
    if selector == 3:
        wire.exact(body, 21, "ground-heading")
        heading = struct.unpack_from(">h", body, 7)[0]
        if not -180 <= heading <= 180:
            raise ValueError("ground heading must be in -180..180")
        if not groundEnabled(flow):
            return True
        advanceGround(flow)
        groundState(flow)["heading"] = heading
        broadcastHeading(flow)
        return True
    wire.exact(body, 24, "ground-key-state")
    category = struct.unpack_from(">i", body, 2)[0]
    keys = body[6:12]
    if any(value not in (0, 1) for value in keys):
        raise ValueError("ground key state must be 0 or 1")
    if category != 1 or keys[4] or keys[5]:
        return False
    initialPosition = None
    if flow.session.get("ground", {}).get("position") is None:
        initialPosition = list(struct.unpack_from(">fff", body, 12))
        if not all(math.isfinite(value) for value in initialPosition):
            raise ValueError("non-finite initial ground position")
    if not groundEnabled(flow):
        return True
    ground = groundState(flow)
    mask = sum(keys[index] << index for index in range(4))
    if mask == ground["mask"]:
        return True
    if ground["position"] is None:
        ground["position"] = initialPosition
    if ground["mask"] == -1:
        flow.cancel("direction-prime-start")
        flow.cancel("direction-prime-stop")
    wasMoving = ground["mask"] >= 0 and project(ground["mask"], ground["heading"]).moving
    advanceGround(flow)
    projection = project(mask, ground["heading"])
    state = groundMoveState(ground, projection, flow.now)
    broadcast(flow, ground["position"], ground["heading"], state,
              projection.forward_back, projection.left_right,
              "move-ground-start-state10-echo" if projection.moving else "move-ground-stop-state1-echo")
    ground["mask"] = mask
    if projection.moving:
        if not wasMoving:
            ground["lastAdvanceAt"] = flow.now
            ground["nextDeadline"] = flow.now + wire.GROUND_STEP_MS
            flow.later("ground-step", wire.GROUND_STEP_MS)
    else:
        ground["lastAdvanceAt"] = flow.now
        ground["nextDeadline"] = None
        flow.cancel("ground-step")
    return True
