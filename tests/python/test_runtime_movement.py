import struct
from pathlib import Path

import math

import pytest

from Business.scripts import app, controls, contracts as wire, scene
from Business.scripts.codec import move_flow


def test_overlay_follows_independent_image_recovery_before_first_resume():
    root = Path(__file__).resolve().parents[2]
    source = (root / "src/Runtime/launcher/Bootstrap.cpp").read_text(encoding="utf-8")
    launch = source.split("void Bootstrap::launch(", 1)[1].split("void Bootstrap::stop()", 1)[0]
    assert launch.count("movementOverlay_.install(") == 1
    recovered = launch.index("recoverClientImage(")
    adapted = launch.index("applyMemoryPatches(")
    started = launch.index("debugClient_->start(")
    installed_image = launch.index("installClientImage(")
    installed = launch.index("movementOverlay_.install(")
    assert recovered < adapted < started < installed_image < installed
    debug = (root / "src/Runtime/launcher/DebugClient.cpp").read_text(encoding="utf-8")
    assert debug.index("prepare(process_, primaryThread)") < debug.index("ResumeThread(primaryThread)")
    assert "startup signature changed before patch" not in launch


def make_runtime_flow(*, playing=True):
    state = app.createState({"runtimeMovement": True})
    state["sessions"]["1"] = {
        "role": "instance", "hydration": [], "pending": {}, "camp": 1,
        "battleEntered": True, "controlBaseline": wire.BASELINE_ID,
        "groundEnabled": playing, "instanceStartedAt": 0, "moveClock": controls.MOVE_CLOCK,
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


@pytest.mark.parametrize("playing", [False, True])
def test_runtime_entry_initializes_idle_once_without_position_stop(playing):
    flow = make_runtime_flow(playing=playing)
    flow.session.update(battleEntered=False, heroChosen=True, heroId=110001,
                        instanceStartedAt=100, moveClock=controls.MOVE_CLOCK)
    scene.battleEntry(flow)
    sends = list(flow.result["send"])
    commands = [0x36, 0xE, 0xE, 4] + ([] if playing else [0x36, 2]) + [4]
    assert [item["command"] for item in sends] == commands
    assert sends[0]["body"] == wire.actorState(flow.now, 6)
    assert sends[3]['reason'] == 'combat-guard-value'
    assert struct.unpack('>Hf', sends[3]['body']) == (48, 5.)
    if not playing:
        assert sends[-3]["body"] == wire.actorState(flow.now, 8)
        active = move_flow.decode_move_notify_active(sends[-2]["body"])
        assert (active.server_tick, active.target_instance_id, active.active) == (900, 1, 0)
    assert struct.unpack(">HHHHIH", sends[-1]["body"]) == (3, 1, 1, 2, 900, 0)
    scene.battleEntry(flow)
    assert flow.result["send"] == sends


@pytest.mark.parametrize("mask", range(16))
def test_runtime_preparation_to_game_only_toggles_native_active(mask):
    flow = make_runtime_flow(playing=False)
    flow.session.update(battleEntered=False, heroChosen=True, heroId=110001)
    scene.battleEntry(flow)
    active = [move_flow.decode_move_notify_active(item["body"])
              for item in flow.result["send"] if item["command"] == 2]
    assert len(active) == 1 and active[0].active == 0

    flow.result["send"].clear()
    keys = [(mask >> index) & 1 for index in range(4)] + [0, 0]
    body = struct.pack(">Hi6Bfff", 52, 1, *keys, 10., 20., 30.)
    assert controls.message(flow, 2, 52, body)
    assert flow.result["send"] == []

    flow.now += wire.START_MS
    assert scene.timer(flow, "round-game")
    movement = [item["body"] for item in flow.result["send"] if item["command"] == 2]
    assert movement == [move_flow.encode_move_notify_active(
        server_tick=flow.now, target_instance_id=1, active=1)]
    assert controls.groundState(flow)["position"] is None
    sends = list(flow.result["send"])
    assert scene.timer(flow, "round-game")
    assert flow.result["send"] == sends


def test_runtime_battle_confirmation_closes_selection_and_starts_full_countdown_immediately():
    flow = make_runtime_flow(playing=False)
    flow.session.update(battleEntered=False, loaded=True)
    flow.later("round-start", wire.PREPARE_MS)
    pending = dict(flow.session["pending"])

    assert scene.message(flow, 0x36, 0x32, struct.pack(">Hi", 0x32, 1))
    assert flow.session["pending"] == pending
    flow.result["send"].clear()
    flow.result["timers"].clear()
    assert scene.message(flow, 0x36, 0x64, struct.pack(">HHb", 0x64, 1, 0))

    sends = flow.result["send"]
    # IN_SCENE closes selection; READY_PLAY and native Active keep preparation stationary.
    states = [item["body"] for item in sends
              if item["command"] == 0x36 and item["body"][:2] == b"\0\1"]
    assert states == [wire.actorState(flow.now, 6), wire.actorState(flow.now, 8)]
    assert [item["command"] for item in sends] == [0x36, 0x36, 0xE, 0xE, 4, 0x36, 2, 4, 0xA]
    assert sends[4]['reason'] == 'combat-guard-value'
    assert struct.unpack('>Hf', sends[4]['body']) == (48, 5.)
    assert move_flow.decode_move_notify_active(sends[6]["body"]).active == 0
    assert sends[-1]["body"] == wire.roundState(flow.now, 3, wire.START_MS)
    assert not controls.groundEnabled(flow)
    assert flow.session["phase"] == "start-sent"
    assert flow.session["pending"] == {"round-game": flow.now + wire.START_MS}
    assert [(item["id"], item["delayMs"]) for item in flow.result["timers"]] == [
        ("1:round-start", -1), ("1:round-game", wire.START_MS)]
    deadline = flow.now + wire.START_MS

    for elapsedMs in (0, 500, 5500):
        flow.result["send"].clear()
        flow.result["timers"].clear()
        flow.now = deadline - wire.START_MS + elapsedMs
        assert scene.message(flow, 0x36, 0x64, struct.pack(">HHb", 0x64, 1, 0))
        assert len(flow.result["send"]) == 1
        assert flow.result["send"][0]["reason"] == "actor-play-result-zero"
        assert flow.session["pending"] == {"round-game": deadline}
        assert flow.result["timers"] == []
        assert not controls.groundEnabled(flow)

    flow.result["send"].clear()
    flow.result["timers"].clear()
    flow.now = pending["round-start"]
    app.handleTimer(flow, "round-start")
    assert flow.result["send"] == flow.result["timers"] == []
    assert flow.session["pending"] == {"round-game": deadline}


@pytest.mark.parametrize("remainingMs", [30000, 6001, 6000, 5999, 5500, 5000, 1000, 1, 0, -1000])
def test_runtime_battle_confirmation_gets_full_window_regardless_of_prepare_deadline(remainingMs):
    flow = make_runtime_flow(playing=False)
    flow.session.update(battleEntered=False, loaded=True)
    flow.later("round-start", wire.PREPARE_MS)
    scene.message(flow, 0x36, 0x32, struct.pack(">Hi", 0x32, 1))
    originalDeadline = flow.session["pending"]["round-start"]
    flow.now = originalDeadline - remainingMs
    flow.result["send"].clear()
    flow.result["timers"].clear()

    assert scene.message(flow, 0x36, 0x64, struct.pack(">HHb", 0x64, 1, 0))

    deadline = flow.now + wire.START_MS
    assert flow.session["pending"] == {"round-game": deadline}
    updates = [item["body"] for item in flow.result["send"] if item["command"] == 0xA]
    timers = [(item["id"], item["delayMs"]) for item in flow.result["timers"]]
    assert updates == [wire.roundState(flow.now, 3, wire.START_MS)]
    assert timers == [("1:round-start", -1), ("1:round-game", wire.START_MS)]
    assert not controls.groundEnabled(flow)


@pytest.mark.parametrize("remainingMs", [5999, 5998, 5000, 1, 0])
def test_runtime_battle_confirmation_preserves_started_countdown(remainingMs):
    flow = make_runtime_flow(playing=False)
    flow.session.update(battleEntered=False, loaded=True)
    flow.later("round-start", wire.PREPARE_MS)
    scene.message(flow, 0x36, 0x32, struct.pack(">Hi", 0x32, 1))
    scene.message(flow, 0x36, 0x64, struct.pack(">HHb", 0x64, 1, 0))
    deadline = flow.session["pending"]["round-game"]
    flow.now = deadline - remainingMs
    if remainingMs == 0:
        app.handleTimer(flow, "round-game")
    pending = dict(flow.session["pending"])
    flow.result["send"].clear()
    flow.result["timers"].clear()

    assert scene.message(flow, 0x36, 0x64, struct.pack(">HHb", 0x64, 1, 0))

    assert [item["reason"] for item in flow.result["send"]] == ["actor-play-result-zero"]
    assert flow.result["timers"] == []
    assert flow.session["pending"] == pending
    assert controls.groundEnabled(flow) == (remainingMs == 0)


@pytest.mark.parametrize("chooseBeforeExpiry", [False, True])
def test_runtime_preparation_expiry_waits_for_confirmation_even_with_a_selected_hero(chooseBeforeExpiry):
    flow = make_runtime_flow(playing=False)
    flow.session.update(battleEntered=False, loaded=True)
    flow.later("round-start", wire.PREPARE_MS)
    if chooseBeforeExpiry:
        scene.message(flow, 0x36, 0x32, struct.pack(">Hi", 0x32, 1))
    flow.result["send"].clear()
    flow.now = flow.session["pending"]["round-start"]
    app.handleTimer(flow, "round-start")
    assert flow.session["prepareExpired"]
    assert "round-game" not in flow.session["pending"]
    assert not flow.session["battleEntered"]
    assert flow.result["send"] == []

    flow.now += 1000
    scene.message(flow, 0x36, 0x32, struct.pack(">Hi", 0x32, 1))
    assert flow.session["pending"] == {}
    app.handleTimer(flow, "round-start")
    assert not flow.session["battleEntered"]
    assert not any(item["command"] == 0xA for item in flow.result["send"])
    assert scene.message(flow, 0x36, 0x64, struct.pack(">HHb", 0x64, 1, 0))
    app.handleTimer(flow, "round-start")

    starts = [item for item in flow.result["send"] if item["reason"] == "instance-round-state-start"]
    assert len(starts) == 1
    assert starts[0]["body"] == wire.roundState(flow.now, 3, wire.START_MS)
    assert flow.session["pending"] == {"round-game": flow.now + wire.START_MS}
    assert not flow.session.get("prepareExpired")


@pytest.mark.parametrize("receiveDelayMs", [0, 1, 16, 100, 250])
def test_runtime_start_window_covers_native_first_tick_and_five_digits(receiveDelayMs):
    flow = make_runtime_flow(playing=False)
    flow.session.update(battleEntered=False, loaded=True)
    scene.message(flow, 0x36, 0x32, struct.pack(">Hi", 0x32, 1))
    scene.message(flow, 0x36, 0x64, struct.pack(">HHb", 0x64, 1, 0))
    packet = next(item["body"] for item in flow.result["send"]
                  if item["reason"] == "instance-round-state-start")
    _, serverNow, _, state, start, duration, _ = struct.unpack(">HQQiQQi", packet)
    assert state == 3 and start == serverNow == flow.now
    assert duration == 5999
    clientNow = serverNow + receiveDelayMs
    # The native HUD truncates once, then ticks locally at one-second intervals.
    totalTicks = max(int((start + duration - clientNow) / 1000), 0) + 1
    assert [totalTicks - tick for tick in range(1, totalTicks)] == [5, 4, 3, 2, 1]
    lastDigitAt = clientNow + (totalTicks - 1) * 1000
    assert lastDigitAt < flow.session["pending"]["round-game"] == start + duration


def test_runtime_confirmation_cancels_pending_selection_and_prepare_timers():
    flow = make_runtime_flow(playing=False)
    flow.session.update(battleEntered=False, actorImported=True)
    scene.message(flow, 0x36, 0x14, b"\0\x14\0")
    assert flow.session["pending"]["round-start"] == flow.now + wire.PREPARE_MS
    scene.message(flow, 0x36, 0x32, struct.pack(">Hi", 0x32, 1))
    scene.message(flow, 0x36, 0x64, struct.pack(">HHb", 0x64, 1, 0))
    deadline = flow.now + wire.START_MS
    assert flow.session["pending"] == {"round-game": deadline}
    assert {(item["id"], item["delayMs"]) for item in flow.result["timers"]
            if item["delayMs"] == -1} == {("1:actor-select", -1), ("1:round-start", -1)}
    flow.result["send"].clear()
    flow.now += 1000
    app.handleTimer(flow, "actor-select")
    app.handleTimer(flow, "round-start")
    assert flow.result["send"] == []
    assert flow.session["pending"] == {"round-game": deadline}


def test_runtime_object_refresh_preserves_position_and_gravity():
    flow = make_runtime_flow()
    flow.session.update(instanceStartedAt=0, moveClock=controls.MOVE_CLOCK)
    position = (20., 30., -4.)
    controls.message(flow, 2, 52, struct.pack(">Hi6Bfff", 52, 1, 1, 0, 0, 0, 0, 0, *position))
    scene.message(flow, 0xE, 7, struct.pack(">HiQb", 7, 1, 1, 0))
    vision = next(item["body"] for item in flow.result["send"] if item["command"] == 0xE)
    assert struct.unpack_from(">fff", vision, 47) == position
    assert struct.unpack_from(">h", vision, 79)[0] == -10000
    assert [item["body"][:2] for item in flow.result["send"] if item["command"] == 2] == [b"\0\x1f"]


def test_runtime_stale_timers_and_repeated_reports_never_echo():
    flow = make_runtime_flow()
    for position in ((1., 2., 3.), (7., 8., -2.)):
        controls.message(flow, 2, 52, struct.pack(">Hi6Bfff", 52, 1, 1, 0, 0, 0, 0, 0, *position))
        for timer in ("ground-step", "direction-prime-start", "direction-prime-stop"):
            assert controls.timer(flow, timer)
        assert controls.groundState(flow)["position"] == list(position)
    controls.message(flow, 2, 3, struct.pack(">HiBhfff", 3, 1, 0, 90, -1., -2., -3.))
    assert controls.groundState(flow)["position"] == [-1., -2., -3.]
    assert flow.result["send"] == []


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_runtime_nonfinite_reports_do_not_change_snapshot(value):
    flow = make_runtime_flow()
    before = dict(flow.session)
    with pytest.raises(ValueError):
        controls.message(flow, 2, 52, struct.pack(">Hi6Bfff", 52, 1, 0, 0, 0, 0, 0, 0, 1., 2., value))
    assert flow.session == before


@pytest.mark.parametrize("keys", [
    (1, 0, 0, 0, 0, 0), (0, 1, 0, 0, 0, 0),
    (0, 0, 1, 0, 0, 0), (0, 0, 0, 1, 0, 0),
    (0, 0, 0, 0, 1, 0), (0, 0, 0, 0, 0, 1),
    (1, 1, 1, 1, 1, 1), (0, 0, 0, 0, 0, 0),
])
def test_runtime_preparation_ignores_movement_jump_and_crouch_reports(keys):
    flow = make_runtime_flow(playing=False)
    before = dict(flow.session)
    assert controls.message(flow, 2, 52, struct.pack(">Hi6Bfff", 52, 1, *keys, 10., 20., 30.))
    assert flow.session == before
    assert flow.result["send"] == []


def test_runtime_preparation_heading_does_not_accept_position_or_echo_camera_commands():
    flow = make_runtime_flow(playing=False)
    before = dict(flow.session)
    assert controls.message(flow, 2, 3, struct.pack(">HiBhfff", 3, 1, 0, 90, 10., 20., 30.))
    assert flow.session == before
    assert flow.result["send"] == []


@pytest.mark.parametrize("playing", [False, True])
def test_runtime_object_refresh_obeys_movement_phase(playing):
    flow = make_runtime_flow(playing=playing)
    scene.message(flow, 0xE, 7, struct.pack(">HiQb", 7, 1, 1, 0))
    active = move_flow.decode_move_notify_active(flow.result["send"][-1]["body"])
    assert active.active == int(playing)
    vision = flow.result["send"][0]["body"]
    assert struct.unpack_from(">fff", vision, 47) == pytest.approx(wire.POSITION)


def test_runtime_controls_unlock_only_after_start_countdown_and_without_respawn():
    flow = make_runtime_flow(playing=False)
    flow.session.update(battleEntered=False, loaded=True)
    flow.later("round-start", wire.PREPARE_MS)
    scene.message(flow, 0x36, 0x32, struct.pack(">Hi", 0x32, 1))
    scene.message(flow, 0x36, 0x64, struct.pack(">HHb", 0x64, 1, 0))
    startAt = flow.now
    assert not controls.groundEnabled(flow)
    deadline = flow.session["pending"]["round-game"]
    assert deadline == startAt + wire.START_MS
    assert not controls.groundEnabled(flow)
    flow.result["send"].clear()
    flow.now = deadline - 1
    app.handleTimer(flow, "round-game")
    assert not controls.groundEnabled(flow)
    assert flow.result["send"] == []
    flow.now = deadline
    app.handleTimer(flow, "round-game")
    assert controls.groundEnabled(flow)
    assert [item["command"] for item in flow.result["send"]] == [0xA, 0x36, 2]
    assert flow.result["send"][1]["body"] == wire.actorState(deadline, 6)
    assert move_flow.decode_move_notify_active(flow.result["send"][2]["body"]).active == 1
    sends = list(flow.result["send"])
    app.handleTimer(flow, "round-game")
    controls.enableGround(flow)
    assert flow.result["send"] == sends
    controls.message(flow, 2, 52, struct.pack(">Hi6Bfff", 52, 1, 1, 0, 0, 0, 1, 1, 10., 20., 30.))
    ground = controls.groundState(flow)
    assert ground["position"] == [10., 20., 30.]
    assert ground["crouched"] is True and ground["jumpPressed"] is True
    assert flow.result["send"] == sends
