from copy import deepcopy
from pathlib import Path
import struct
import sys

import pytest

from Business.scripts import app, contracts as wire, controls, scene
from Business.scripts.codec import battle_flow


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/Business/runtime"))
import host_runtime  # noqa: E402

ACTION_TIMER = "battle-action-phase"
ATTACKS = [
    (300560, (235, 237, 238, 239)),
    (300550, (241, 243, 244, 245)),
    (300570, (247, 249, 250, 251)),
    (300540, (253, 255, 256, 257)),
]
BLOCKS = [
    (300580, (305, 223, 227)),
    (300590, (308, 224, 228)),
    (300600, (307, 225, 229)),
    (300610, (306, 226, 230)),
]
ATTACK_TIMES = {
    300560: (360, 640, 500),
    300550: (360, 640, 500),
    300570: (400, 700, 400),
    300540: (360, 640, 530),
}


def makeFlow(**sessionValues):
    state = app.createState({"runtimeMovement": True})
    state["sessions"]["1"] = {
        "role": "instance", "hydration": [], "pending": {}, "camp": 1,
        "authenticated": True, "battleEntered": True, "groundEnabled": True,
        "instanceStartedAt": 100, "moveClock": controls.MOVE_CLOCK,
        "controlBaseline": wire.BASELINE_ID, **sessionValues,
    }
    return app.Flow({"connection": 1}, state, {"nowMs": 1000})


def actionBody(intent, autoParry=1):
    return struct.pack(">HIib", 1, 0x8E, intent, autoParry)


def sendAction(flow, intent):
    app.handleMessage(flow, {"command": 4, "body": actionBody(intent)})


def advanceAction(flow):
    flow.now = flow.session["pending"][ACTION_TIMER]
    app.handleTimer(flow, ACTION_TIMER)


def animationStates(flow):
    assert all(item["command"] == 4 for item in flow.result["send"])
    return [battle_flow.decode_battle_state_sync_simple(item["body"]).state
            for item in flow.result["send"]]


@pytest.mark.parametrize("intent,chain", ATTACKS)
def test_attack_prepares_holds_releases_and_returns_to_idle(intent, chain):
    flow = makeFlow()
    prepare, process, finish = ATTACK_TIMES[intent]
    sendAction(flow, intent)
    assert animationStates(flow) == [chain[0]]
    assert flow.session["pending"][ACTION_TIMER] == 1000 + prepare
    advanceAction(flow)
    assert animationStates(flow) == list(chain[:2])
    assert ACTION_TIMER not in flow.session["pending"]
    flow.now += 500
    app.handleTimer(flow, ACTION_TIMER)
    assert animationStates(flow) == list(chain[:2])
    sendAction(flow, 300020)
    assert animationStates(flow) == list(chain[:3])
    assert flow.session["pending"][ACTION_TIMER] == flow.now + process
    advanceAction(flow)
    assert flow.session["pending"][ACTION_TIMER] == flow.now + finish
    advanceAction(flow)
    assert animationStates(flow) == [*chain, 2]
    assert ACTION_TIMER not in flow.session["pending"]
    assert flow.session["action"]["index"] == -1


@pytest.mark.parametrize("intent,chain", BLOCKS)
def test_parry_holds_until_matching_release(intent, chain):
    flow = makeFlow()
    sendAction(flow, intent)
    advanceAction(flow)
    assert animationStates(flow) == list(chain[:2])
    assert ACTION_TIMER not in flow.session["pending"]
    flow.now += 1000
    app.handleTimer(flow, ACTION_TIMER)
    sendAction(flow, 300020)
    assert animationStates(flow) == list(chain[:2])
    sendAction(flow, 300620)
    assert animationStates(flow) == list(chain)
    assert flow.session["pending"][ACTION_TIMER] == flow.now + 200
    before = deepcopy(flow.result)
    sendAction(flow, 300620)
    assert flow.result == before
    advanceAction(flow)
    assert animationStates(flow) == [*chain, 2]
    assert ACTION_TIMER not in flow.session["pending"]


@pytest.mark.parametrize("intent,chain", ATTACKS)
def test_quick_attack_release_finishes_prepare_before_striking(intent, chain):
    flow = makeFlow()
    sendAction(flow, intent)
    flow.now += 10
    sendAction(flow, 300020)
    assert animationStates(flow) == [chain[0]]
    assert flow.session["pending"][ACTION_TIMER] == 1000 + ATTACK_TIMES[intent][0]
    flow.now = 1200
    app.handleTimer(flow, ACTION_TIMER)
    assert animationStates(flow) == [chain[0]]
    advanceAction(flow)
    assert animationStates(flow) == [chain[0], chain[2]]
    assert flow.session["pending"][ACTION_TIMER] == flow.now + ATTACK_TIMES[intent][1]
    advanceAction(flow)
    advanceAction(flow)
    assert animationStates(flow) == [chain[0], chain[2], chain[3], 2]


@pytest.mark.parametrize("intent,chain", BLOCKS)
def test_quick_parry_release_retracts_before_returning_to_locomotion(intent, chain):
    flow = makeFlow()
    sendAction(flow, intent)
    flow.now += 10
    sendAction(flow, 300620)
    assert animationStates(flow) == [chain[0], chain[2]]
    assert flow.session["pending"][ACTION_TIMER] == 1210
    flow.now = 1200
    app.handleTimer(flow, ACTION_TIMER)
    assert animationStates(flow) == [chain[0], chain[2]]
    advanceAction(flow)
    assert animationStates(flow) == [chain[0], chain[2], 2]
    assert ACTION_TIMER not in flow.session["pending"]


@pytest.mark.parametrize("intent,chain", ATTACKS)
def test_attack_accepts_next_press_at_recovery_end_without_extra_cooldown(intent, chain):
    flow = makeFlow()
    sendAction(flow, intent)
    sendAction(flow, 300020)
    advanceAction(flow)
    advanceAction(flow)
    recoveryEnd = flow.session["pending"][ACTION_TIMER]
    assert recoveryEnd == 1000 + sum(ATTACK_TIMES[intent])
    advanceAction(flow)
    assert flow.now == recoveryEnd
    sendAction(flow, intent)
    assert animationStates(flow) == [chain[0], chain[2], chain[3], 2, chain[0]]
    assert flow.session["pending"][ACTION_TIMER] == recoveryEnd + ATTACK_TIMES[intent][0]


@pytest.mark.parametrize("intent,chain", BLOCKS)
def test_parry_release_keeps_the_current_running_input(intent, chain):
    flow = makeFlow()
    ground = {"position": [1., 2., 3.], "heading": 90, "mask": 1, "tick": 200}
    flow.session["ground"] = deepcopy(ground)
    sendAction(flow, intent)
    advanceAction(flow)
    sendAction(flow, 300620)
    advanceAction(flow)
    assert animationStates(flow) == [*chain, 2]
    assert flow.session["ground"] == ground


def test_unmapped_weapon_does_not_reuse_the_default_attack_timing(monkeypatch):
    flow = makeFlow()
    before = deepcopy(flow.state)
    monkeypatch.setattr(wire, "WEAPON_ID", 999999)
    with pytest.raises(ValueError, match="weapon.*timing"):
        sendAction(flow, 300560)
    assert flow.state == before
    assert flow.result["send"] == flow.result["timers"] == []


def test_repeated_press_release_and_conflicting_intents_do_not_restart_action():
    flow = makeFlow()
    sendAction(flow, 300560)
    before = deepcopy(flow.result)
    for intent in (300560, 300550, 300580, 300620):
        sendAction(flow, intent)
    assert flow.result == before
    sendAction(flow, 300020)
    before = deepcopy(flow.result)
    sendAction(flow, 300020)
    assert flow.result == before


@pytest.mark.parametrize("intent", [300020, 300620])
def test_release_without_press_emits_nothing(intent):
    flow = makeFlow()
    sendAction(flow, intent)
    assert flow.result["send"] == []
    assert flow.result["timers"] == []


@pytest.mark.parametrize("sessionValues", [
    {"groundEnabled": False}, {"battleEntered": False},
    {"leaving": True}, {"controlBaseline": "unknown"},
])
def test_actions_remain_locked_outside_active_game(sessionValues):
    flow = makeFlow(**sessionValues)
    for intent, _ in ATTACKS + BLOCKS:
        sendAction(flow, intent)
    assert flow.result["send"] == []
    assert flow.result["timers"] == []
    assert "action" not in flow.session


@pytest.mark.parametrize("body", [b"\0\1", actionBody(300560)[:-1],
                                  actionBody(300560) + b"\0", actionBody(300560, 2)])
def test_invalid_attack_packets_leave_state_unchanged(body):
    flow = makeFlow()
    before = deepcopy(flow.state)
    with pytest.raises(ValueError):
        app.handleMessage(flow, {"command": 4, "body": body})
    assert flow.state == before
    assert flow.result["send"] == []


@pytest.mark.parametrize("intent", [300670, 300680, 300718, 0, -1])
def test_other_weapon_and_special_actions_are_not_imported(intent):
    flow = makeFlow()
    sendAction(flow, intent)
    assert flow.result["send"] == []
    assert flow.result["timers"] == []
    assert "action" not in flow.session


def test_model_actions_preserve_movement_and_only_emit_state_sync():
    flow = makeFlow()
    ground = {"position": [1., 2., 3.], "heading": 90, "mask": 1, "tick": 200}
    flow.session["ground"] = deepcopy(ground)
    sendAction(flow, 300540)
    advanceAction(flow)
    sendAction(flow, 300020)
    advanceAction(flow)
    advanceAction(flow)
    assert flow.session["ground"] == ground
    packets = [battle_flow.decode_battle_state_sync_simple(item["body"])
               for item in flow.result["send"]]
    assert all(item["command"] == 4 for item in flow.result["send"])
    assert [packet.seq_no for packet in packets] == [2, 3, 4, 5, 6]
    assert [packet.state_change_ms for packet in packets] == [900, 1260, 1260, 1900, 2430]
    assert all(packet.instance_id == 1 and packet.state_time_ms == 0 for packet in packets)


def test_sequence_wraps_without_reusing_zero():
    flow = makeFlow(action={"intent": 0, "index": -1, "seq": 65535, "released": False})
    sendAction(flow, 300560)
    packet = battle_flow.decode_battle_state_sync_simple(flow.result["send"][0]["body"])
    assert packet.seq_no == 1


def test_leave_cancels_action_timers_and_ignores_late_events():
    flow = makeFlow()
    sendAction(flow, 300560)
    scene.leave(flow, struct.pack(">Hb", 2, 0))
    assert ACTION_TIMER not in flow.session["pending"]
    before = deepcopy(flow.result["send"])
    flow.now += 1000
    app.handleTimer(flow, ACTION_TIMER)
    sendAction(flow, 300020)
    assert flow.result["send"] == before


def test_host_reload_preserves_action_and_other_connections(tmp_path):
    runtime = host_runtime.Runtime(ROOT / "src/Business/scripts", tmp_path / "revisions")
    state = makeFlow().state
    state["sessions"]["2"] = deepcopy(state["sessions"]["1"])
    peer = deepcopy(state["sessions"]["2"])
    event = {"type": "message", "eventId": 1, "role": "instance", "name": "",
             "connection": 1, "sequence": 0, "serverTimeMs": 1000,
             "command": 4, "body": actionBody(300560)}
    def context(now):
        return host_runtime.encode({"nowMs": now, "connections": [1, 2]})
    try:
        encoded, sends, _, _ = runtime.dispatch(host_runtime.encode(event),
                                               host_runtime.encode(state), context(1000))
        assert [item["connection"] for item in sends] == [1]
        runtime.prepare()
        encoded = runtime.switch(encoded)
        timer = {**event, "type": "timer", "name": ACTION_TIMER, "body": b"", "command": 0}
        encoded, sends, _, _ = runtime.dispatch(host_runtime.encode(timer), encoded, context(1360))
        assert battle_flow.decode_battle_state_sync_simple(sends[0]["body"]).state == 237
        value = host_runtime.decode(encoded)
        assert value["sessions"]["2"] == peer
        assert value["sessions"]["1"]["action"]["seq"] == 3
        assert "action" not in state["sessions"]["1"]
    finally:
        runtime.close()
