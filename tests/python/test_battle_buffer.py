from copy import deepcopy
import struct

import pytest

from Business.scripts import app, contracts as wire, scene
from Business.scripts.codec import battle_flow
from test_battle_actions import (
    ACTION_TIMER, ATTACKS, ATTACK_TIMES, BLOCKS, ROOT, actionBody,
    advanceAction, animationStates, host_runtime, makeFlow, sendAction,
)


ATTACK_PHASES = [(0, False), (0, True), (1, False), (2, True)]
PARRY_BUFFER_PHASES = [(phase, released) for phase, released in ATTACK_PHASES if phase >= 2]


def attackPhaseFlow(intent, phase, released):
    flow = makeFlow()
    sendAction(flow, intent)
    if phase >= 1:
        advanceAction(flow)
    if released:
        sendAction(flow, 300020)
    if phase == 3:
        advanceAction(flow)
    return flow


def recoveryFlow(intent=300560):
    flow = makeFlow()
    sendAction(flow, intent)
    sendAction(flow, 300020)
    advanceAction(flow)
    advanceAction(flow)
    return flow


def bufferFlow(intent=300560):
    return attackPhaseFlow(intent, 2, True)


@pytest.mark.parametrize("activeIntent,activeChain", BLOCKS)
@pytest.mark.parametrize("nextIntent,nextChain", ATTACKS + BLOCKS)
@pytest.mark.parametrize("phase", [0, 1])
def test_parry_release_accepts_next_action_immediately(activeIntent, activeChain, nextIntent, nextChain, phase):
    flow = makeFlow()
    sendAction(flow, activeIntent)
    if phase == 1:
        advanceAction(flow)
    sendAction(flow, 300620)
    assert animationStates(flow) == [*activeChain[:phase + 1], 2]
    assert ACTION_TIMER not in flow.session["pending"]
    previous = animationStates(flow)
    sendAction(flow, nextIntent)
    assert animationStates(flow) == [*previous, nextChain[0]]
    assert flow.session["action"].get("buffered") is None
    prepare = ATTACK_TIMES[nextIntent][0] if nextIntent in ATTACK_TIMES else 0
    assert flow.session["pending"][ACTION_TIMER] == flow.now + prepare
    advanceAction(flow)
    assert animationStates(flow) == [*previous, *nextChain[:2]]
    assert ACTION_TIMER not in flow.session["pending"]


@pytest.mark.parametrize("activeIntent,_", ATTACKS)
@pytest.mark.parametrize("nextIntent,nextChain", ATTACKS + BLOCKS)
def test_attack_recovery_is_immediately_interrupted_by_attack_or_parry(activeIntent, _, nextIntent, nextChain):
    flow = recoveryFlow(activeIntent)
    oldDeadline = flow.session["pending"][ACTION_TIMER]
    flow.now = oldDeadline - 1
    previous = animationStates(flow)
    sendAction(flow, nextIntent)
    assert animationStates(flow) == [*previous, nextChain[0]]
    assert flow.session["action"]["intent"] == nextIntent
    assert flow.session["action"]["index"] == 0
    assert flow.session["action"].get("buffered") is None
    prepare = ATTACK_TIMES[nextIntent][0] if nextIntent in ATTACK_TIMES else 0
    assert flow.session["pending"][ACTION_TIMER] == flow.now + prepare
    if prepare:
        flow.now = oldDeadline
        app.handleTimer(flow, ACTION_TIMER)
        assert animationStates(flow) == [*previous, nextChain[0]]
    advanceAction(flow)
    assert animationStates(flow) == [*previous, *nextChain[:2]]
    assert ACTION_TIMER not in flow.session["pending"]


@pytest.mark.parametrize("phase,released,nextIntent,nextChain", [
    (phase, released, intent, chain)
    for intent, chain in ATTACKS + BLOCKS
    for phase, released in (ATTACK_PHASES if intent in ATTACK_TIMES else PARRY_BUFFER_PHASES)
])
@pytest.mark.parametrize("activeIntent,activeChain", ATTACKS)
def test_attack_buffer_windows_do_not_interrupt_current_action(
        phase, released, activeIntent, activeChain, nextIntent, nextChain):
    flow = attackPhaseFlow(activeIntent, phase, released)
    before = deepcopy(flow.result)
    action = deepcopy(flow.session["action"])
    pending = deepcopy(flow.session["pending"])
    sendAction(flow, nextIntent)
    buffered = {"intent": nextIntent, "released": False}
    assert flow.session["action"] == {**action, "buffered": buffered}
    assert flow.result["send"] == before["send"]
    assert flow.result["timers"] == before["timers"]
    assert flow.session["pending"] == pending

    if not released:
        sendAction(flow, 300020)
        buffered = {**buffered, "released": nextIntent in ATTACK_TIMES}
    assert flow.session["action"]["released"] is True
    assert flow.session["action"]["buffered"] == buffered
    while flow.session["action"]["index"] < 2:
        advanceAction(flow)
        assert flow.session["action"]["intent"] == activeIntent
        assert flow.session["action"]["buffered"] == buffered
    deadline = flow.session["pending"][ACTION_TIMER]
    assert deadline == 1000 + sum(ATTACK_TIMES[activeIntent][:2])
    previous = animationStates(flow)
    advanceAction(flow)
    if buffered["released"]:
        assert animationStates(flow) == [*previous, activeChain[-1]]
        assert flow.session["pending"][ACTION_TIMER] == deadline + ATTACK_TIMES[activeIntent][-1]
        advanceAction(flow)
        assert animationStates(flow) == [*previous, activeChain[-1], 2]
        assert flow.session["action"].get("buffered") is None
        assert flow.session["action"]["index"] == -1
        assert ACTION_TIMER not in flow.session["pending"]
        return
    assert flow.session["action"].get("buffered") is None
    assert animationStates(flow) == [*previous, nextChain[0]]
    prepare = ATTACK_TIMES[nextIntent][0] if nextIntent in ATTACK_TIMES else 0
    assert flow.session["pending"][ACTION_TIMER] == deadline + prepare
    advanceAction(flow)
    assert animationStates(flow) == [*previous, *nextChain[:2]]
    assert ACTION_TIMER not in flow.session["pending"]


def test_later_attack_phases_replace_early_buffer_without_queueing():
    flow = attackPhaseFlow(300560, 0, False)
    sendAction(flow, 300550)
    sendAction(flow, 300020)
    advanceAction(flow)
    sendAction(flow, 300580)
    sendAction(flow, 300540)
    advanceAction(flow)
    sendAction(flow, 300020)
    advanceAction(flow)
    advanceAction(flow)
    advanceAction(flow)
    assert animationStates(flow) == [235, 238, 253, 256, 257, 2]
    assert flow.now == 1000 + sum(ATTACK_TIMES[300560][:2]) + sum(ATTACK_TIMES[300540])
    assert flow.session["action"].get("buffered") is None
    assert ACTION_TIMER not in flow.session["pending"]


def test_last_press_wins_across_attack_and_parry_without_queueing_older_intents():
    flow = bufferFlow()
    ground = {"position": [1., 2., 3.], "heading": 90, "mask": 1, "tick": 200}
    flow.session["ground"] = deepcopy(ground)
    previous = animationStates(flow)
    for intent in (300580, 300550, 300600, 300540):
        sendAction(flow, intent)
    sendAction(flow, 300620)
    advanceAction(flow)
    assert animationStates(flow) == [*previous, 253]
    sendAction(flow, 300020)
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


@pytest.mark.parametrize("activeIntent,_", ATTACKS)
@pytest.mark.parametrize("intent,chain", ATTACKS + BLOCKS)
@pytest.mark.parametrize("releaseBeforeStart", [True, False])
def test_buffered_action_requires_held_key_at_handoff(activeIntent, _, intent, chain, releaseBeforeStart):
    flow = bufferFlow(activeIntent)
    sendAction(flow, intent)
    previous = animationStates(flow)
    deadline = flow.session["pending"][ACTION_TIMER]
    release = 300020 if intent in ATTACK_TIMES else 300620
    flow.now = max(flow.now, deadline - 1)
    if releaseBeforeStart:
        sendAction(flow, release)
    assert animationStates(flow) == previous
    assert flow.session["pending"][ACTION_TIMER] == deadline
    advanceAction(flow)
    if releaseBeforeStart:
        previous = [*previous, dict(ATTACKS)[activeIntent][-1]]
        assert animationStates(flow) == previous
        advanceAction(flow)
        assert animationStates(flow) == [*previous, 2]
        assert flow.session["action"].get("buffered") is None
        assert flow.session["action"]["index"] == -1
        assert ACTION_TIMER not in flow.session["pending"]
        return
    assert flow.session["action"].get("buffered") is None
    started = flow.now
    assert animationStates(flow) == [*previous, chain[0]]
    flow.now += 10
    sendAction(flow, release)
    if intent not in ATTACK_TIMES:
        assert animationStates(flow) == [*previous, chain[0], 2]
        assert flow.now == started + 10
        assert ACTION_TIMER not in flow.session["pending"]
        return
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


@pytest.mark.parametrize("phase,released", PARRY_BUFFER_PHASES)
@pytest.mark.parametrize("intent,_", ATTACKS + BLOCKS)
def test_releasing_buffered_action_discards_it_without_restoring_an_older_attack(
        phase, released, intent, _):
    flow = attackPhaseFlow(300560, phase, released)
    sendAction(flow, 300550)
    sendAction(flow, intent)
    release = 300020 if intent in ATTACK_TIMES else 300620
    sendAction(flow, release)
    sendAction(flow, release)
    sendAction(flow, 300020)
    assert flow.session["action"]["buffered"] == {"intent": intent, "released": True}
    while flow.session["action"]["index"] < 3:
        advanceAction(flow)
    previous = animationStates(flow)
    advanceAction(flow)
    assert animationStates(flow) == [*previous, 2]
    assert flow.session["action"]["index"] == -1
    assert flow.session["action"].get("buffered") is None
    assert ACTION_TIMER not in flow.session["pending"]


@pytest.mark.parametrize("intent,chain", ATTACKS + BLOCKS)
def test_repress_after_a_buffered_tap_tracks_the_last_press_as_held(intent, chain):
    flow = bufferFlow()
    release = 300020 if intent in ATTACK_TIMES else 300620
    sendAction(flow, intent)
    sendAction(flow, release)
    sendAction(flow, intent)
    sendAction(flow, intent)
    advanceAction(flow)
    advanceAction(flow)
    assert animationStates(flow)[-2:] == list(chain[:2])
    assert ACTION_TIMER not in flow.session["pending"]
    sendAction(flow, release)
    assert animationStates(flow)[-1] == (chain[2] if intent in ATTACK_TIMES else 2)


@pytest.mark.parametrize("activeIntent,_", BLOCKS)
@pytest.mark.parametrize("phase", [0, 1])
@pytest.mark.parametrize("intent", [intent for intent, _ in BLOCKS])
def test_parry_presses_during_prepare_and_hold_do_not_restart_or_buffer(activeIntent, _, phase, intent):
    flow = makeFlow()
    sendAction(flow, activeIntent)
    if phase >= 1:
        advanceAction(flow)
    before = deepcopy(flow.state)
    beforeSends = deepcopy(flow.result["send"])
    beforeTimers = deepcopy(flow.result["timers"])
    sendAction(flow, intent)
    assert flow.state == before
    assert flow.result["send"] == beforeSends
    assert flow.result["timers"] == beforeTimers


@pytest.mark.parametrize("body", [actionBody(300550)[:-1], actionBody(300550, 2)])
def test_invalid_packets_do_not_replace_a_buffered_intent(body):
    flow = bufferFlow()
    sendAction(flow, 300580)
    before = deepcopy(flow.state)
    with pytest.raises(ValueError):
        app.handleMessage(flow, {"command": 4, "body": body})
    assert flow.state == before


def test_unmapped_weapon_does_not_replace_a_buffered_intent(monkeypatch):
    flow = bufferFlow()
    sendAction(flow, 300580)
    before = deepcopy(flow.state)
    monkeypatch.setattr(wire, "WEAPON_ID", 999999)
    with pytest.raises(ValueError, match="weapon.*timing"):
        sendAction(flow, 300550)
    assert flow.state == before


def test_unrelated_release_and_unsupported_action_do_not_change_buffered_parry():
    flow = bufferFlow()
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
    flow = bufferFlow()
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
def test_scene_transitions_discard_buffered_intent_and_old_timer(spawnPoints, transition):
    flow = bufferFlow()
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
    flow = bufferFlow()
    sendAction(flow, 300550)
    result = app.handleEvent({"type": "closed", "connection": 1}, flow.state, {"nowMs": flow.now})
    assert result["state"]["sessions"] == {}
    late = app.handleEvent({"type": "timer", "connection": 1, "name": ACTION_TIMER},
                          result["state"], {"nowMs": flow.now + 2000})
    assert late["send"] == late["timers"] == []


@pytest.mark.parametrize("intent,release", [
    (300550, None), (300550, 300020), (300580, None), (300580, 300620),
])
def test_host_reload_preserves_the_buffer_and_other_sessions(tmp_path, intent, release):
    flow = bufferFlow()
    sendAction(flow, intent)
    if release is not None:
        sendAction(flow, release)
    flow.state["sessions"]["2"] = deepcopy(flow.session)
    before = deepcopy(flow.state)
    deadline = flow.session["pending"][ACTION_TIMER]
    runtime = host_runtime.Runtime(ROOT / "tests/reference/Business/scripts", tmp_path / "revisions")
    try:
        runtime.prepare()
        encoded = runtime.switch(host_runtime.encode(flow.state))
        assert host_runtime.decode(encoded) == before
        event = {"type": "timer", "eventId": 1, "role": "instance", "name": ACTION_TIMER,
                 "connection": 1, "sequence": 0, "serverTimeMs": deadline, "command": 0, "body": b""}
        context = host_runtime.encode({"nowMs": deadline, "connections": [1, 2]})
        encoded, sends, _, _ = runtime.dispatch(host_runtime.encode(event), encoded, context)
        assert len(sends) == 1 and sends[0]["connection"] == 1
        expected = 239 if release is not None else (241 if intent == 300550 else 305)
        assert battle_flow.decode_battle_state_sync_simple(sends[0]["body"]).state == expected
        value = host_runtime.decode(encoded)
        action = value["sessions"]["1"]["action"]
        assert action["intent"] == (intent if release is None else 300560)
        assert action["index"] == (0 if release is None else 3)
        assert action["released"] is (release is not None)
        assert action.get("buffered") == (None if release is None else {"intent": intent, "released": True})
        assert value["sessions"]["2"] == before["sessions"]["2"]
        assert flow.state == before
    finally:
        runtime.close()
