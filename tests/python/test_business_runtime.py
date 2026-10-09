import json
import math
import struct
import sys
from pathlib import Path

import pytest

from Business.scripts import app, contracts as wire, scene


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/Business" / "runtime"))
import host_runtime  # noqa: E402


def test_round_timing_configuration():
    assert wire.PREPARE_MS == 30000
    assert wire.START_MS == 5000
    assert wire.GAME_MS == 1200000


def test_initial_prepare_uses_preparation_duration():
    state = app.createState({})
    state["sessions"]["1"] = {"role": "instance"}
    flow = app.Flow({"connection": 1}, state, {"nowMs": 1000})

    assert scene.timer(flow, "instance-round-state") is True

    packet = flow.result["send"][0]
    assert packet["command"] == 0xA
    assert struct.unpack(">HQQiQQi", packet["body"]) == (0x67, 1000, 1, 2, 1000, 30000, 0)


def test_business_self_test_and_context(tmp_path):
    runtime = host_runtime.Runtime(ROOT / "src/Business" / "scripts", tmp_path / "revisions")
    try:
        context = host_runtime.encode({
            "nowMs": 0,
            "advertisedAddress": "127.0.0.1",
            "instancePort": 12345,
            "connections": [],
        })
        state = runtime.create(context)
        assert runtime.phase(state) == "waiting"
        assert json.loads(host_runtime.encode(host_runtime.decode(context)))
    finally:
        runtime.close()


def test_state_encoding_rejects_unsupported_values():
    try:
        host_runtime.encode({"bad": object()})
    except TypeError:
        pass
    else:
        raise AssertionError("unsupported state value was accepted")


def test_state_encoding_rejects_non_finite_numbers():
    try:
        host_runtime.encode({"bad": math.nan})
    except (TypeError, ValueError):
        pass
    else:
        raise AssertionError("non-finite state value was accepted")


def test_connected_event_round_trip(tmp_path):
    runtime = host_runtime.Runtime(ROOT / "src/Business" / "scripts", tmp_path / "revisions")
    try:
        context = host_runtime.encode({
            "nowMs": 1,
            "advertisedAddress": "127.0.0.1",
            "instancePort": 12345,
            "connections": [7],
        })
        state = runtime.create(context)
        event = host_runtime.encode({
            "type": "connected", "eventId": 1, "role": "login", "name": "",
            "connection": 7, "sequence": 0, "serverTimeMs": 1, "command": 0, "body": b"",
        })
        next_state, sends, timers, logs = runtime.dispatch(event, state, context)
        value = host_runtime.decode(next_state)
        assert value["sessions"]["7"]["role"] == "login"
        assert sends == [] and timers == [] and logs == []
    finally:
        runtime.close()


@pytest.mark.parametrize("role, timer_name, delay_ms", [
    ("login", "version", 1000),
    ("logic", "login", 1000),
    ("instance", "instance-init", 20),
])
def test_authentication_schedules_startup_once(role, timer_name, delay_ms):
    context = {"nowMs": 300}
    connected = app.handleEvent({"type": "connected", "connection": 7, "role": role},
                                app.createState(context), context)
    assert connected["send"] == connected["timers"] == []
    event = {"type": "authenticated", "connection": 7}
    authenticated = app.handleEvent(event, connected["state"], context)
    assert authenticated["send"] == []
    assert authenticated["timers"] == [{
        "id": f"7:{timer_name}", "delayMs": delay_ms,
        "event": {"type": "timer", "connection": 7, "name": timer_name},
    }]
    assert authenticated["state"]["sessions"]["7"]["pending"] == {timer_name: 300 + delay_ms}

    repeated = app.handleEvent(event, authenticated["state"], {"nowMs": 301})
    assert repeated["send"] == repeated["timers"] == []
    assert repeated["state"] == authenticated["state"]


@pytest.mark.parametrize("role, steps", [
    ("login", [("version", 1, "version-response")]),
    ("logic", [("login", 1, "fixed-local-login"), ("sync", 0x2C, "sync-login")]),
])
def test_startup_responses_follow_short_deadlines_in_order(role, steps):
    now = 300
    result = app.handleEvent({"type": "connected", "connection": 7, "role": role},
                             app.createState({}), {"nowMs": now})
    result = app.handleEvent({"type": "authenticated", "connection": 7},
                             result["state"], {"nowMs": now})
    for timer_name, command, reason in steps:
        assert len(result["timers"]) == 1
        timer = result["timers"][0]
        assert timer["event"]["name"] == timer_name
        assert timer["delayMs"] == 1000
        now += timer["delayMs"]
        early = app.handleEvent(timer["event"], result["state"], {"nowMs": now - 1})
        assert early["send"] == early["timers"] == []
        assert early["state"] == result["state"]

        result = app.handleEvent(timer["event"], result["state"], {"nowMs": now})
        assert [(packet["connection"], packet["command"], packet["reason"])
                for packet in result["send"]] == [(7, command, reason)]
        assert timer_name not in result["state"]["sessions"]["7"]["pending"]
        repeated = app.handleEvent(timer["event"], result["state"], {"nowMs": now})
        assert repeated["send"] == repeated["timers"] == []
        assert repeated["state"] == result["state"]
    assert result["timers"] == []
    assert result["state"]["sessions"]["7"]["pending"] == {}
    if role == "logic":
        assert result["state"]["phase"] == "hydrating"


@pytest.mark.parametrize("role", ["login", "logic"])
def test_closed_connection_does_not_receive_startup_responses(role):
    result = app.handleEvent({"type": "connected", "connection": 7, "role": role},
                             app.createState({}), {"nowMs": 0})
    result = app.handleEvent({"type": "authenticated", "connection": 7},
                             result["state"], {"nowMs": 0})
    timer = result["timers"][0]
    closed = app.handleEvent({"type": "closed", "connection": 7}, result["state"], {"nowMs": 1})
    late = app.handleEvent(timer["event"], closed["state"], {"nowMs": timer["delayMs"]})
    assert late["send"] == late["timers"] == []
    assert late["state"]["phase"] == "waiting"
    assert late["state"]["sessions"] == {}
