from copy import deepcopy
import math
from pathlib import Path
import struct

import pytest

from Business.scripts import app, battle, bot, contracts as wire, controls, scene
from Business.scripts.codec import battle_flow, move_flow
from Business.scripts.navigation import NavMesh
from dash_fixture import profile


def flowAt(now=1000, *, playing=True):
    state = app.createState({"runtimeMovement": True})
    state["sessions"]["1"] = {
        "role": "instance", "hydration": [], "pending": {}, "camp": 1,
        "battleEntered": playing, "groundEnabled": playing, "heroChosen": True,
        "instanceStartedAt": 100, "moveClock": controls.MOVE_CLOCK,
        "controlBaseline": wire.BASELINE_ID, "bot": bot.createBot(1),
    }
    return app.Flow({"connection": 1}, state, {"nowMs": now, "dashAttack": profile()})


@pytest.fixture
def nav(monkeypatch):
    result = NavMesh.fromPolygons([[(250, 140, 0), (290, 140, 0), (290, 190, 0), (250, 190, 0)]])
    monkeypatch.setattr(bot, "navigationFor", lambda flow: result)
    return result


def snapshot(flow, player=(280., 157., 0.2), *, age=0, epoch=100):
    flow.context["botWorld"] = {"ready": True, "sampledAt": flow.now - age,
                               "instanceStartedAt": epoch, "player": list(player),
                               "bot": list(flow.session["bot"]["position"]),
                               "playerHeading": flow.session.get("ground", {}).get("heading", 0),
                               "botHeading": flow.session["bot"]["heading"],
                               "terrain": [{name: {"ready": True, "hit": False}
                                            for name in ("forward", "target")} for _ in range(2)]}


def tick(flow, name=bot.THINK_TIMER):
    flow.later(name, 0)
    app.handleTimer(flow, name)


def test_bot_identity_and_opposite_camp():
    for camp in (1, 2):
        value = bot.createBot(camp)
        assert value["camp"] == 3 - camp
        body = bot.vision(value)
        assert struct.unpack_from(">IQH", body, 10) == (10001, 2, 2)
        assert body.count(struct.pack(">iBBB", wire.WEAPON_ID, 1, 0, 1)) == 1


def test_ready_only_after_game_and_fresh_world(nav):
    flow = flowAt(playing=False)
    snapshot(flow)
    tick(flow)
    assert flow.result["send"] == []
    flow.session.update(battleEntered=True, groundEnabled=True)
    flow.context.pop("botWorld")
    tick(flow)
    assert not flow.result["send"]
    assert flow.session["bot"]["status"] == "world-unavailable"
    snapshot(flow)
    tick(flow)
    assert flow.session["bot"]["status"] == "approach"
    assert bot.MOVE_TIMER in flow.session["pending"]


def test_moves_only_bot_and_stops_for_stale_or_wrong_instance(nav):
    flow = flowAt()
    snapshot(flow)
    tick(flow)
    origin = tuple(flow.session["bot"]["position"])
    flow.now += 50
    tick(flow, bot.MOVE_TIMER)
    moves = [move_flow.decode_move_bc_with_system_and_active(x["body"])
             for x in flow.result["send"] if x["command"] == 2 and x["body"][:2] == b"\0\x26"]
    assert moves and all(x.target_instance_id == 2 for x in moves)
    assert tuple(flow.session["bot"]["position"]) == origin
    assert moves[-1].current_velocity == 3000
    for epoch, age in [(99, 0), (100, 1000)]:
        snapshot(flow, epoch=epoch, age=age)
        tick(flow)
        assert flow.session["bot"]["status"] == "world-unavailable"
        assert bot.MOVE_TIMER not in flow.session["pending"]


def test_attack_does_not_change_player_action(nav):
    flow = flowAt()
    battle.startAction(flow, 300550)
    playerAction = deepcopy(flow.session["action"])
    snapshot(flow, (272., 157., 0.2))
    tick(flow)
    flow.now += bot.REACTION_MS
    snapshot(flow, (272., 157., 0.2))
    tick(flow)
    assert flow.session["action"] == playerAction
    events = [battle_flow.decode_battle_state_sync_simple(x["body"])
              for x in flow.result["send"] if x["command"] == 4]
    assert any(x.instance_id == 2 for x in events)
    assert flow.session["bot"]["action"]["index"] >= 0


@pytest.mark.parametrize("attack,defence", [
    (300560, 300580), (300550, 300610), (300570, 300600), (300540, 300590),
], ids=["up-up", "right-left", "down-down", "left-right"])
def test_parry_release_finishes_and_player_timer_remains(nav, attack, defence):
    flow = flowAt()
    # An incoming threat faces the bot; an away-facing swing should not trigger a guard.
    flow.session["ground"] = {"heading": 180}
    player = flow.session.setdefault("action", {"intent": attack, "index": 0, "seq": 5, "released": False})
    flow.later(battle.ACTION_TIMER, 900)
    snapshot(flow, (272., 157., 0.2))
    tick(flow)
    flow.now += bot.REACTION_MS
    snapshot(flow, (272., 157., 0.2))
    tick(flow)
    assert flow.session["bot"]["action"]["intent"] == defence
    assert battle.ACTION_TIMER in flow.session["pending"]
    for _ in range(8):
        deadlines = [(due, name) for name, due in flow.session["pending"].items()
                     if name in (bot.RELEASE_TIMER, bot.ACTION_TIMER)]
        if not deadlines:
            break
        flow.now, name = min(deadlines)
        app.handleTimer(flow, name)
    assert flow.session["bot"]["action"]["index"] == -1
    assert player["seq"] == 5


def test_leave_cancels_all_bot_timers(nav):
    flow = flowAt()
    snapshot(flow)
    tick(flow)
    scene.leave(flow, struct.pack(">Hb", 2, 0))
    assert not any(name.startswith("bot-") for name in flow.session["pending"])
    assert flow.session["bot"]["status"] == "stopped"


def test_old_state_migration_rejects_running_instance_without_changes():
    state = flowAt().state
    state["sessions"]["1"].pop("bot")
    original = deepcopy(state)
    with pytest.raises(ValueError, match='restart Runtime'):
        app.migrateState(2, state)
    assert state == original


def test_vision_refresh_preserves_bot_position_and_action(nav):
    flow = flowAt()
    flow.session["bot"]["position"] = [279., 159., 0.2]
    flow.session["bot"]["heading"] = -87
    battle.startAction(flow, 300550, released=True, actorId=wire.BOT_ID)
    duration = battle_flow.decode_battle_state_sync_simple(flow.result['send'][-1]['body']).state_time_ms
    flow.result["send"].clear()
    flow.now += 150
    before = deepcopy(flow.session["bot"])
    scene.message(flow, 0xE, 7, struct.pack(">HiQb", 7, 1, 2, 0))
    assert flow.session["bot"] == before
    assert flow.result["send"][0]["body"] == bot.vision(before)
    assert struct.unpack_from(">h", flow.result["send"][0]["body"], 45)[0] == -87
    current = battle_flow.decode_battle_state_sync_simple(flow.result["send"][1]["body"])
    assert current.instance_id == wire.BOT_ID and current.state == 241 and current.state_time_ms == duration


@pytest.mark.parametrize("intent", tuple(battle.ATTACK_CHAINS) + tuple(battle.BLOCK_CHAINS))
def test_bot_completes_each_direction_independently(intent):
    flow = flowAt()
    snapshot(flow)
    battle.startAction(flow, intent, released=True, actorId=wire.BOT_ID)
    for _ in range(5):
        if bot.ACTION_TIMER not in flow.session["pending"]:
            break
        flow.now = flow.session["pending"][bot.ACTION_TIMER]
        app.handleTimer(flow, bot.ACTION_TIMER)
    assert flow.session["bot"]["action"]["index"] == -1
    states = [battle_flow.decode_battle_state_sync_simple(item["body"]) for item in flow.result["send"]]
    assert all(item.instance_id == wire.BOT_ID for item in states)
    assert all(item.state not in (227, 228, 229, 230) for item in states)
    assert states[-1].state == battle_flow.ACT_STATE_IDLE
    assert "action" not in flow.session


def test_wrong_epoch_does_not_send_stop_or_action_to_reused_id(nav):
    flow = flowAt()
    snapshot(flow)
    tick(flow)
    flow.now += 50
    tick(flow, bot.MOVE_TIMER)
    battle.startAction(flow, 300560, actorId=wire.BOT_ID)
    flow.result["send"].clear()
    snapshot(flow, epoch=200)
    tick(flow)
    assert bot.ACTION_TIMER not in flow.session["pending"]
    assert not flow.result["send"]


def test_parry_release_cannot_reach_another_instance():
    flow = flowAt()
    snapshot(flow)
    battle.startAction(flow, 300580, actorId=wire.BOT_ID)
    flow.now += battle.BLOCK_PHASE_MS
    app.handleTimer(flow, bot.ACTION_TIMER)
    assert flow.session["bot"]["action"]["index"] == 1
    flow.result["send"].clear()
    snapshot(flow, epoch=200)
    tick(flow, bot.RELEASE_TIMER)
    assert not flow.result["send"]
    assert flow.session["bot"]["action"]["index"] == -1


def test_invalid_static_navigation_and_disconnected_target_stop_without_fallback(monkeypatch, request):
    from Business.scripts import map_navigation_data, navmesh_resource
    monkeypatch.setattr(map_navigation_data, 'FACES', ())
    navmesh_resource.loadNavigation.cache_clear()
    request.addfinalizer(navmesh_resource.loadNavigation.cache_clear)
    flow = flowAt()
    snapshot(flow)
    tick(flow)
    assert flow.session["bot"]["status"] == "navigation-invalid"
    assert bot.MOVE_TIMER not in flow.session["pending"]
    nav = NavMesh.fromPolygons([
        [(270, 155, 0), (275, 155, 0), (275, 160, 0), (270, 160, 0)],
        [(280, 155, 0), (285, 155, 0), (285, 160, 0), (280, 160, 0)],
    ])
    monkeypatch.setattr(bot, "navigationFor", lambda flow: nav)
    tick(flow)
    assert flow.session["bot"]["status"] == "no-path"
    assert bot.MOVE_TIMER not in flow.session["pending"]


def test_snapshot_validation_and_state_migration(nav):
    flow = flowAt()
    snapshot(flow)
    tick(flow)
    state = deepcopy(flow.state)
    assert app.migrateState(app.STATE_VERSION, state) == state
    assert flow.state == state
    state["sessions"]["1"]["bot"]["position"][0] = math.nan
    assert not app.validateState(state)
    for key, value in [("player", [math.inf, 0, 0]), ("bot", [1, 2]), ("sampledAt", flow.now + 1), ("ready", False)]:
        snapshot(flow)
        flow.context["botWorld"][key] = value
        assert bot.world(flow) is None


def test_outside_mesh_turns_toward_nearest_entry_without_pulling_back(nav):
    flow = flowAt()
    snapshot(flow)
    tick(flow)
    flow.now += 50
    tick(flow, bot.MOVE_TIMER)
    flow.context["botWorld"]["bot"] = [295., 170., 0.2]
    tick(flow)
    packet = flow.result["send"][-1]
    assert packet["reason"] == "bot-stop"
    stopped = move_flow.decode_move_bc_with_system_and_active(packet["body"])
    assert stopped.position == pytest.approx((295., 170., 0.2))
    assert flow.session["bot"]["status"] == "mesh-return-wait"
    assert flow.session["bot"]['meshRecovery']['goal'] == pytest.approx((289.649, 170., 0.))
    assert bot.MOVE_TIMER in flow.session['pending']


def test_new_instance_creates_bot_but_disconnect_discards_it(spawnPoints):
    flow = flowAt(playing=False)
    scene.begin(flow)
    assert len(flow.session["bots"]) == 19 and flow.session["bots"]["2"]["camp"] == 2 and bot.THINK_TIMER not in flow.session["pending"]
    state = app.handleEvent({"type": "closed", "connection": 1}, flow.state, flow.context)["state"]
    assert not state["sessions"]


def test_arrival_leaves_serializable_valid_state(nav):
    flow = flowAt()
    snapshot(flow)
    tick(flow)
    origin = list(flow.session['bot']['position'])
    for index in range(1, 101):
        if bot.MOVE_TIMER not in flow.session["pending"]:
            break
        flow.now += 50
        snapshot(flow)
        fraction = min(1., index / 40)
        flow.context['botWorld']['bot'] = [a + (b - a) * fraction for a, b in zip(origin, (280., 157., .2))]
        tick(flow, bot.MOVE_TIMER)
        assert app.validateState(flow.state)
    assert not flow.session["bot"]["path"]
    assert not flow.session["bot"]["moving"]


def test_hot_reload_preserves_bot_and_resumes_its_existing_timer(tmp_path, nav):
    from Business.runtime import host_runtime
    flow = flowAt()
    snapshot(flow)
    tick(flow)
    battle.startAction(flow, 300560, released=True, actorId=wire.BOT_ID)
    original = deepcopy(flow.state)
    runtime = host_runtime.Runtime(Path(__file__).resolve().parents[2] / "tests/reference/Business/scripts", tmp_path / "revisions")
    try:
        state = host_runtime.encode(flow.state)
        runtime.prepare()
        state = runtime.switch(state)
        assert host_runtime.decode(state) == original
        due = flow.session["pending"][bot.ACTION_TIMER]
        context = {**flow.context, "nowMs": due, "connections": [1]}
        event = {"type": "timer", "connection": 1, "name": bot.ACTION_TIMER}
        state, sends, timers, logs = runtime.dispatch(host_runtime.encode(event), state, host_runtime.encode(context))
        value = host_runtime.decode(state)
        assert value["sessions"]["1"]["bot"]["action"]["index"] == 2
        assert battle_flow.decode_battle_state_sync_simple(sends[0]["body"]).instance_id == 2
        assert timers[0][0] == "1:" + bot.ACTION_TIMER
        assert flow.state == original
    finally:
        runtime.close()
