import struct

from Business.scripts import app, controls, contracts as wire


def make_runtime_flow():
    state = app.createState({"runtimeMovement": True})
    state["sessions"]["1"] = {
        "role": "instance", "hydration": [], "pending": {}, "camp": 1,
        "battleEntered": True, "controlBaseline": wire.BASELINE_ID,
    }
    return app.Flow({"connection": 1}, state, {"nowMs": 1000, "runtimeMovement": True})


def test_runtime_context_selects_local_mode_and_single_hero():
    state = app.createState({"runtimeMovement": True})
    assert state["runtimeMovement"] is True
    flow = make_runtime_flow()
    flow.session["instanceStartedAt"] = 0
    flow.session["moveClock"] = controls.MOVE_CLOCK
    flow.session["initialized"] = True
    controls.enableGround(flow)
    assert controls.movementMode(flow) == wire.RUNTIME_MOVEMENT_MODE
    assert wire.heroIds(True) == (110001,)
    assert len(wire.battleHeroes(1, True)) == 116


def test_runtime_reports_update_local_snapshot_without_server_echo():
    flow = make_runtime_flow()
    body = struct.pack(">Hi6Bfff", 52, 1, 1, 0, 0, 0, 0, 1, 10.0, 20.0, 30.0)
    assert controls.message(flow, 2, 52, body) is True
    ground = controls.groundState(flow)
    assert ground["position"] == [10.0, 20.0, 30.0]
    assert ground["crouched"] is False
    assert ground["jumpPressed"] is True
    assert flow.result["send"] == []


def test_runtime_vision_carries_gravity_without_resource_overlay_files():
    body = wire.actorVision(1, runtimeMovement=True)
    assert struct.unpack_from(">h", body, 79)[0] == -10000
