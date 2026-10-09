"""Four-direction attack/parry animations without movement or damage simulation."""
import struct

from . import contracts as wire, controls
from .codec import battle_flow

ACTION_TIMER = "battle-action-phase"
BLOCK_PHASE_MS = 200
ATTACK_RELEASE = 300020
BLOCK_RELEASE = 300620
ATTACK_CHAINS = {
    300560: (235, 237, 238, 239),  # up
    300550: (241, 243, 244, 245),  # right
    300570: (247, 249, 250, 251),  # down
    300540: (253, 255, 256, 257),  # left
}
BLOCK_CHAINS = {
    # Prepare -> ParryHold -> ParryRelease. Release only follows the matching key-up.
    300580: (305, 223, 227),  # up
    300590: (308, 224, 228),  # right
    300600: (307, 225, 229),  # down
    300610: (306, 226, 230),  # left
}
# s_battle_param_cli: group 2, weapon category 32, directions 1/4/2/3, mode 2.
# The held phase has no deadline. These are base timings, not an attack-speed formula.
WEAPON_ATTACK_PHASES_MS = {
    1030411: {
        300560: (360, 0, 640, 500),
        300550: (360, 0, 640, 500),
        300570: (400, 0, 700, 400),
        300540: (360, 0, 640, 530),
    },
}


def phaseMs(intent, index):
    if intent not in ATTACK_CHAINS:
        return BLOCK_PHASE_MS
    timings = WEAPON_ATTACK_PHASES_MS.get(wire.WEAPON_ID)
    if timings is None:
        raise ValueError("weapon has no verified attack timing")
    return timings[intent][index]


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
        flow.session["action"].pop("buffered", None)


def startAction(flow, intent, released=False):
    delay = phaseMs(intent, 0)
    chain = ATTACK_CHAINS.get(intent) or BLOCK_CHAINS[intent]
    action = actionState(flow)
    action.update(intent=intent, index=0, released=released)
    action.pop("buffered", None)
    syncState(flow, chain[0])
    flow.later(ACTION_TIMER, delay)


def bufferRecoveryInput(action, intent):
    chain = ATTACK_CHAINS.get(action["intent"]) or BLOCK_CHAINS.get(action["intent"])
    if chain is None or not action["released"] or action["index"] != len(chain) - 1:
        return False
    if intent in ATTACK_CHAINS or intent in BLOCK_CHAINS:
        action["buffered"] = {"intent": intent, "released": False}
    else:
        buffered = action.get("buffered")
        if buffered is not None:
            if intent == ATTACK_RELEASE and buffered["intent"] in ATTACK_CHAINS:
                action["buffered"] = {**buffered, "released": True}
            elif intent == BLOCK_RELEASE and buffered["intent"] in BLOCK_CHAINS:
                action.pop("buffered")
    return True


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
    if intent in ATTACK_CHAINS and action["released"] and nextIndex == 1:
        nextIndex = 2
    holdIndex = 1
    if not action["released"] and nextIndex > holdIndex:
        return True
    if nextIndex == len(chain):
        buffered = action.get("buffered")
        if buffered is not None:
            startAction(flow, buffered["intent"], buffered["released"])
        else:
            syncState(flow, battle_flow.ACT_STATE_IDLE)
            resetAction(flow)
        return True
    action["index"] = nextIndex
    syncState(flow, chain[nextIndex])
    if action["released"] or nextIndex < holdIndex:
        flow.later(ACTION_TIMER, phaseMs(intent, nextIndex))
    return True


def message(flow, command, selector, body):
    if (command != battle_flow.BATTLE_COMMAND or selector != 1
            or flow.session["role"] != "instance"):
        return False
    if (flow.session.get("leaving")
            or flow.session.get("controlBaseline") != wire.BASELINE_ID):
        resetAction(flow)
        return False
    wire.exact(body, 11, "battle action")
    _, intent, autoParry = struct.unpack_from(">Iib", body, 2)
    if autoParry not in (0, 1):
        raise ValueError("battle auto-parry flag must be 0 or 1")
    chain = ATTACK_CHAINS.get(intent) or BLOCK_CHAINS.get(intent)
    if chain is None and intent not in (ATTACK_RELEASE, BLOCK_RELEASE):
        return False
    if not enabled(flow):
        resetAction(flow)
        return True
    if chain is not None:
        phaseMs(intent, 0)
    action = actionState(flow)
    if bufferRecoveryInput(action, intent):
        return True
    if chain is not None:
        if action["index"] >= 0:
            return True
        startAction(flow, intent)
        return True
    expectedRelease = ATTACK_RELEASE if action["intent"] in ATTACK_CHAINS else BLOCK_RELEASE
    if action["index"] < 0 or action["released"] or intent != expectedRelease:
        return True
    if intent == ATTACK_RELEASE and action["index"] == 0:
        action["released"] = True
        return True
    flow.cancel(ACTION_TIMER)
    action.update(index=2, released=True)
    chain = ATTACK_CHAINS.get(action["intent"]) or BLOCK_CHAINS[action["intent"]]
    syncState(flow, chain[2])
    flow.later(ACTION_TIMER, phaseMs(action["intent"], 2))
    return True
