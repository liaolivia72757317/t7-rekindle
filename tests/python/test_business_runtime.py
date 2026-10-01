import json
import math
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/Business" / "runtime"))
import host_runtime  # noqa: E402


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
