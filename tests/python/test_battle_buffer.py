from copy import deepcopy
import struct

import pytest

from Business.scripts import app, contracts as wire, scene
from Business.scripts.codec import battle_flow
from test_battle_actions import (
    ACTION_TIMER, ATTACKS, ATTACK_TIMES, BLOCKS, ROOT, actionBody,
    advanceAction, animationStates, host_runtime, makeFlow, sendAction,
)


def recoveryFlow(intent=300560):
    flow = makeFlow()
    sendAction(flow, intent)
    sendAction(flow, 300020 if intent in ATTACK_TIMES else 300620)
    if intent in ATTACK_TIMES:
        advanceAction(flow)
        advanceAction(flow)
    return flow


@pytest.mark.parametrize("activeIntent,_", ATTACKS + BLOCKS)
@pytest.mark.parametrize("nextIntent,nextChain", ATTACKS + BLOCKS)
def test_recovery_buffers_all_directions_without_extending_deadline(activeIntent, _, nextIntent, nextChain):
    flow = recoveryFlow(activeIntent)
    before = deepcopy(flow.result)
    deadline = flow.session["pending"][ACTION_TIMER]
    flow.now = deadline - 1
    sendAction(flow, nextIntent)
    assert flow.result["send"] == before["send"]
    assert flow.result["timers"] == before["timers"]
    assert flow.session["pending"][ACTION_TIMER] == deadline
    previous = animationStates(flow)
    advanceAction(flow)
    assert animationStates(flow) == [*previous, nextChain[0]]
    assert flow.session["action"].get("buffered") is None
    prepare = ATTACK_TIMES[nextIntent][0] if nextIntent in ATTACK_TIMES else 200
    assert flow.session["pending"][ACTION_TIMER] == deadline + prepare
    advanceAction(flow)
    assert animationStates(flow) == [*previous, *nextChain[:2]]
    assert ACTION_TIMER not in flow.session["pending"]


def test_last_press_wins_across_attack_and_parry_without_queueing_older_intents():
    flow = recoveryFlow()
    ground = {"position": [1., 2., 3.], "heading": 90, "mask": 1, "tick": 200}
    flow.session["ground"] = deepcopy(ground)
    previous = animationStates(flow)
    for intent in (300580, 300550, 300600, 300540):
        sendAction(flow, intent)
    sendAction(flow, 300620)
    sendAction(flow, 300020)
    advanceAction(flow)
    assert animationStates(flow) == [*previous, 253]
    advanceAction(flow)
    advanceAction(flow)
    advanceAction(flow)
    assert animationStates(flow) == [*previous, 253, 256, 257, 2]
    assert ACTION_TIMER not in flow.session["pending"]
    assert flow.session["ground"] == ground
    packets = [battle_flow.decode_battle_state_sync_simple(item["body"])
               for item in flow.result["send"]]
    assert [packet.seq_no for packet in packets] == list(range(2, 2 + len(packets)))
    assert all(packet.state_time_ms == 0 for packet in packets)


@pytest.mark.parametrize("intent,chain", ATTACKS)
@pytest.mark.parametrize("releaseBeforeStart", [True, False])
def test_buffered_attack_tap_keeps_full_prepare_and_only_strikes_once(intent, chain, releaseBeforeStart):
    flow = recoveryFlow()
    sendAction(flow, intent)
    if releaseBeforeStart:
        sendAction(flow, 300020)
    advanceAction(flow)
    started = flow.now
    if not releaseBeforeStart:
        flow.now += 10
        sendAction(flow, 300020)
    previous = animationStates(flow)
    assert previous[-1] == chain[0]
    assert flow.session["pending"][ACTION_TIMER] == started + ATTACK_TIMES[intent][0]
    advanceAction(flow)
    assert animationStates(flow) == [*previous, chain[2]]
    advanceAction(flow)
    advanceAction(flow)
    assert animationStates(flow) == [*previous, chain[2], chain[3], 2]
    assert flow.now == started + sum(ATTACK_TIMES[intent])
    assert ACTION_TIMER not in flow.session["pending"]


@pytest.mark.parametrize("intent,_", BLOCKS)
def test_releasing_buffered_parry_cancels_it_without_restoring_an_older_attack(intent, _):
    flow = recoveryFlow()
    sendAction(flow, 300550)
    sendAction(flow, intent)
    sendAction(flow, 300620)
    sendAction(flow, 300620)
    sendAction(flow, 300020)
    previous = animationStates(flow)
    assert flow.session["action"].get("buffered") is None
    advanceAction(flow)
    assert animationStates(flow) == [*previous, 2]
    assert flow.session["action"]["index"] == -1
    assert ACTION_TIMER not in flow.session["pending"]


def test_repress_after_a_buffered_tap_tracks_the_last_press_as_held():
    flow = recoveryFlow()
    sendAction(flow, 300550)
    sendAction(flow, 300020)
    sendAction(flow, 300550)
    sendAction(flow, 300550)
    advanceAction(flow)
    advanceAction(flow)
    assert animationStates(flow)[-2:] == [241, 243]
    assert ACTION_TIMER not in flow.session["pending"]
    sendAction(flow, 300020)
    assert animationStates(flow)[-1] == 244


@pytest.mark.parametrize("activeIntent,phase", [
    (300560, 0), (300560, 1), (300560, 2), (300580, 0), (300580, 1),
])
@pytest.mark.parametrize("intent", [300550, 300590])
def test_inputs_before_recovery_are_not_buffered(activeIntent, phase, intent):
    flow = makeFlow()
    sendAction(flow, activeIntent)
    if phase >= 1:
        advanceAction(flow)
    if phase == 2:
        sendAction(flow, 300020)
    before = deepcopy(flow.state)
    beforeSends = deepcopy(flow.result["send"])
    beforeTimers = deepcopy(flow.result["timers"])
    sendAction(flow, intent)
    assert flow.state == before
    assert flow.result["send"] == beforeSends
    assert flow.result["timers"] == beforeTimers


@pytest.mark.parametrize("body", [actionBody(300550)[:-1], actionBody(300550, 2)])
def test_invalid_packets_do_not_replace_a_buffered_intent(body):
    flow = recoveryFlow()
    sendAction(flow, 300580)
    before = deepcopy(flow.state)
    with pytest.raises(ValueError):
        app.handleMessage(flow, {"command": 4, "body": body})
    assert flow.state == before


def test_unmapped_weapon_does_not_replace_a_buffered_intent(monkeypatch):
    flow = recoveryFlow()
    sendAction(flow, 300580)
    before = deepcopy(flow.state)
    monkeypatch.setattr(wire, "WEAPON_ID", 999999)
    with pytest.raises(ValueError, match="weapon.*timing"):
        sendAction(flow, 300550)
    assert flow.state == before


def test_unrelated_release_and_unsupported_action_do_not_change_buffered_parry():
    flow = recoveryFlow()
    sendAction(flow, 300580)
    sendAction(flow, 300020)
    sendAction(flow, 300670)
    advanceAction(flow)
    advanceAction(flow)
    assert animationStates(flow)[-2:] == [305, 223]
    assert ACTION_TIMER not in flow.session["pending"]


@pytest.mark.parametrize("sessionValues", [
    {"groundEnabled": False}, {"battleEntered": False},
    {"leaving": True}, {"controlBaseline": "unknown"},
])
@pytest.mark.parametrize("event", ["timer", "input"])
def test_disabled_actions_clear_the_buffer_without_dispatch(sessionValues, event):
    flow = recoveryFlow()
    sendAction(flow, 300550)
    flow.session.update(sessionValues)
    before = deepcopy(flow.result["send"])
    if event == "timer":
        advanceAction(flow)
    else:
        sendAction(flow, 300580)
    assert flow.session["action"].get("buffered") is None
    assert flow.session["action"]["index"] == -1
    assert ACTION_TIMER not in flow.session["pending"]
    assert flow.result["send"] == before


@pytest.mark.parametrize("transition", ["leave", "begin"])
def test_scene_transitions_discard_buffered_intent_and_old_timer(transition):
    flow = recoveryFlow()
    sendAction(flow, 300550)
    if transition == "leave":
        scene.leave(flow, struct.pack(">Hb", 2, 0))
    else:
        scene.begin(flow)
    assert flow.session["action"].get("buffered") is None
    assert flow.session["action"]["index"] == -1
    assert ACTION_TIMER not in flow.session["pending"]
    before = deepcopy(flow.result["send"])
    flow.now += 2000
    app.handleTimer(flow, ACTION_TIMER)
    assert flow.result["send"] == before


def test_closed_session_discards_buffer_and_ignores_the_late_timer():
    flow = recoveryFlow()
    sendAction(flow, 300550)
    result = app.handleEvent({"type": "closed", "connection": 1}, flow.state, {"nowMs": flow.now})
    assert result["state"]["sessions"] == {}
    late = app.handleEvent({"type": "timer", "connection": 1, "name": ACTION_TIMER},
                          result["state"], {"nowMs": flow.now + 2000})
    assert late["send"] == late["timers"] == []


@pytest.mark.parametrize("intent,release", [(300550, None), (300550, 300020), (300580, None)])
def test_host_reload_preserves_the_buffer_and_other_sessions(tmp_path, intent, release):
    flow = recoveryFlow()
    sendAction(flow, intent)
    if release is not None:
        sendAction(flow, release)
    flow.state["sessions"]["2"] = deepcopy(flow.session)
    before = deepcopy(flow.state)
    deadline = flow.session["pending"][ACTION_TIMER]
    runtime = host_runtime.Runtime(ROOT / "src/Business/scripts", tmp_path / "revisions")
    try:
        runtime.prepare()
        encoded = runtime.switch(host_runtime.encode(flow.state))
        assert host_runtime.decode(encoded) == before
        event = {"type": "timer", "eventId": 1, "role": "instance", "name": ACTION_TIMER,
                 "connection": 1, "sequence": 0, "serverTimeMs": deadline, "command": 0, "body": b""}
        context = host_runtime.encode({"nowMs": deadline, "connections": [1, 2]})
        encoded, sends, _, _ = runtime.dispatch(host_runtime.encode(event), encoded, context)
        assert len(sends) == 1 and sends[0]["connection"] == 1
        expected = 241 if intent == 300550 else 305
        assert battle_flow.decode_battle_state_sync_simple(sends[0]["body"]).state == expected
        value = host_runtime.decode(encoded)
        action = value["sessions"]["1"]["action"]
        assert action["intent"] == intent and action["index"] == 0
        assert action["released"] == (release is not None)
        assert action.get("buffered") is None
        assert value["sessions"]["2"] == before["sessions"]["2"]
        assert flow.state == before
    finally:
        runtime.close()
