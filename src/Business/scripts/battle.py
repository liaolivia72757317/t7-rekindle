"""Four-direction attack/parry animations without movement or damage simulation."""
import struct

from . import contracts as wire, controls
from .codec import battle_flow

ACTION_TIMER = "battle-action-phase"
ACTION_PHASE_MS = 200
ATTACK_RELEASE = 300020
BLOCK_RELEASE = 300620
ATTACK_CHAINS = {
    300560: (235, 237, 238, 239),  # up
    300550: (241, 243, 244, 245),  # right
    300570: (247, 249, 250, 251),  # down
    300540: (253, 255, 256, 257),  # left
}
BLOCK_CHAINS = {
    300580: (305, 223, 227),  # up
    300590: (308, 224, 228),  # right
    300600: (307, 225, 229),  # down
    300610: (306, 226, 230),  # left
}


def actionState(flow):
    return flow.session.setdefault("action", {
        "intent": 0, "index": -1, "seq": 1, "released": False,
    })


def enabled(flow):
    return (flow.session["role"] == "instance" and not flow.session.get("leaving")
            and flow.session.get("controlBaseline") == wire.BASELINE_ID
            and controls.groundEnabled(flow))


def syncState(flow, state):
    startedAt = flow.session.get("instanceStartedAt")
    if type(startedAt) is not int:
        raise ValueError("battle animation requires an instance start time")
    action = actionState(flow)
    sequence = action["seq"] % 0xFFFF + 1
    body = battle_flow.encode_battle_state_sync_simple(
        instance_id=wire.ACTOR_ID, seq_no=sequence, state=state,
        state_change_ms=flow.now - startedAt, state_time_ms=0)
    action["seq"] = sequence
    flow.send(battle_flow.BATTLE_COMMAND, body, "battle-model-state-" + str(state))


def resetAction(flow):
    flow.cancel(ACTION_TIMER)
    if "action" in flow.session:
        flow.session["action"].update(intent=0, index=-1, released=False)


def timer(flow, name):
    if name != ACTION_TIMER:
        return False
    action = flow.session.get("action")
    if not enabled(flow) or action is None or action["index"] < 0:
        resetAction(flow)
        return True
    intent = action["intent"]
    chain = ATTACK_CHAINS.get(intent) or BLOCK_CHAINS.get(intent)
    if chain is None:
        raise ValueError("unknown active battle animation")
    nextIndex = action["index"] + 1
    holdIndex = 1 if intent in ATTACK_CHAINS else 2
    if not action["released"] and nextIndex > holdIndex:
        return True
    if nextIndex == len(chain):
        syncState(flow, battle_flow.ACT_STATE_IDLE)
        resetAction(flow)
        return True
    action["index"] = nextIndex
    syncState(flow, chain[nextIndex])
    if action["released"] or nextIndex < holdIndex:
        flow.later(ACTION_TIMER, ACTION_PHASE_MS)
    return True


def message(flow, command, selector, body):
    if (command != battle_flow.BATTLE_COMMAND or selector != 1
            or flow.session["role"] != "instance" or flow.session.get("leaving")
            or flow.session.get("controlBaseline") != wire.BASELINE_ID):
        return False
    wire.exact(body, 11, "battle action")
    _, intent, autoParry = struct.unpack_from(">Iib", body, 2)
    if autoParry not in (0, 1):
        raise ValueError("battle auto-parry flag must be 0 or 1")
    chain = ATTACK_CHAINS.get(intent) or BLOCK_CHAINS.get(intent)
    if chain is None and intent not in (ATTACK_RELEASE, BLOCK_RELEASE):
        return False
    if not enabled(flow):
        return True
    action = actionState(flow)
    if chain is not None:
        if action["index"] >= 0:
            return True
        action.update(intent=intent, index=0, released=False)
        syncState(flow, chain[0])
        flow.later(ACTION_TIMER, ACTION_PHASE_MS)
        return True
    expectedRelease = ATTACK_RELEASE if action["intent"] in ATTACK_CHAINS else BLOCK_RELEASE
    if action["index"] < 0 or action["released"] or intent != expectedRelease:
        return True
    flow.cancel(ACTION_TIMER)
    if intent == BLOCK_RELEASE:
        syncState(flow, battle_flow.ACT_STATE_IDLE)
        resetAction(flow)
    else:
        action.update(index=2, released=True)
        syncState(flow, ATTACK_CHAINS[action["intent"]][2])
        flow.later(ACTION_TIMER, ACTION_PHASE_MS)
    return True
