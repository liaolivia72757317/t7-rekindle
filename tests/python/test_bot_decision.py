from copy import deepcopy

import pytest
import py_trees

from Business.scripts import app, battle, bot, bot_decision as decision
from Business.runtime import host_runtime
from Business.scripts.navigation import NavMesh
from test_bot_player import flowAt, snapshot, tick


@pytest.fixture
def duel():
    flow = flowAt()
    flow.session["bot"].update(position=[0., 0., .2], heading=0, reactAt=flow.now)
    flow.session["ground"] = {"heading": 180}
    snapshot(flow, (1.5, 0., .2))
    return flow


def observe(flow, elapsed=0):
    flow.now += elapsed
    snapshot(flow, (1.5, 0., .2))
    decision.observe(flow)


def choose(flow):
    return decision.choose(flow, flow.context["botWorld"])


def test_uses_real_behavior_tree_and_keeps_decision_state_serializable(duel, monkeypatch):
    calls = []
    original = py_trees.composites.Selector.tick_once
    def tickTree(tree):
        calls.append(tree.name)
        return original(tree)
    monkeypatch.setattr(py_trees.composites.Selector, "tick_once", tickTree)
    observe(duel)
    before = deepcopy(duel.state)
    assert choose(duel).kind == "attack"
    assert calls == ["melee-decision"]
    assert duel.state == before
    assert host_runtime.decode(host_runtime.encode(duel.state)) == before


@pytest.mark.parametrize("intent,guard", list(bot.PARRY_FOR_ATTACK.items()))
def test_threat_requires_reaction_time_before_selecting_guard(duel, intent, guard):
    battle.startAction(duel, intent)
    observe(duel)
    assert choose(duel).reason == "observe-threat"
    observe(duel, decision.OBSERVATION_MS - 1)
    assert choose(duel).kind == "wait"
    observe(duel, 1)
    choice = choose(duel)
    assert (choice.kind, choice.intent) == ("parry", guard)


@pytest.mark.parametrize("obstacle", ["range", "height", "facing", "terrain", "unknown-terrain", "unknown-heading"])
def test_attack_without_a_contact_threat_does_not_trigger_guard(duel, obstacle):
    battle.startAction(duel, 300550)
    observe(duel)
    observe(duel, decision.OBSERVATION_MS)
    world = duel.context["botWorld"]
    if obstacle == "range": world["player"][0] = 5
    if obstacle == "height": world["player"][2] += 2
    if obstacle == "facing": world["playerHeading"] = 0
    if obstacle == "terrain":
        world["terrain"][0]["target"].update(hit=True, material=4, position=[.7, 0, 1.2], normal=[-1, 0, 0], direction=[1, 0, 0])
    if obstacle == "unknown-terrain": world["terrain"][0]["target"]["ready"] = False
    if obstacle == "unknown-heading": world.pop("playerHeading")
    assert choose(duel).kind != "parry"


def test_observation_survives_phase_progression_but_not_new_swings(duel):
    battle.startAction(duel, 300550, released=True)
    observe(duel)
    firstSeen = duel.session["bot"]["observedPlayer"]["seenAt"]
    duel.now = duel.session["pending"][battle.ACTION_TIMER]
    app.handleTimer(duel, battle.ACTION_TIMER)
    observe(duel)
    assert duel.session["bot"]["observedPlayer"]["seenAt"] == firstSeen
    assert choose(duel).kind == "parry"
    battle.startAction(duel, 300550, released=True)
    observe(duel)
    assert choose(duel).reason == "observe-threat"


@pytest.mark.parametrize("remaining,reason", [(-1, "late-threat"), (0, "late-threat"),
                                             (1, "parry"), (99, "parry"), (100, "parry"),
                                             (101, "parry"), (150, "parry")])
def test_zero_prepare_guard_remains_available_until_the_strike_ends(duel, remaining, reason):
    battle.startAction(duel, 300560, released=True)
    observe(duel)
    duel.now = duel.session["pending"][battle.ACTION_TIMER]
    app.handleTimer(duel, battle.ACTION_TIMER)
    duel.now = duel.session["pending"][battle.ACTION_TIMER] - remaining
    observe(duel)
    assert choose(duel).reason == reason


@pytest.mark.parametrize("intent,kind,reason", [
    (300550, "attack", "counterattack"), (battle.DASH_ATTACK, "wait", "ready"),
])
def test_only_an_active_recovery_phase_precedes_normal_attack_cooldown(duel, intent, kind, reason):
    battle.startAction(duel, intent, released=True)
    for _ in range(1 if intent == battle.DASH_ATTACK else 2):
        duel.now = duel.session["pending"][battle.ACTION_TIMER]
        app.handleTimer(duel, battle.ACTION_TIMER)
    observe(duel)
    duel.session["bot"]["reactAt"] = duel.now + 300
    assert choose(duel).kind == "wait"
    observe(duel, decision.OBSERVATION_MS)
    choice = choose(duel)
    # Dash returns to idle without an artificial recovery phase to counter.
    assert (choice.kind, choice.reason) == (kind, reason)
    assert duel.now < duel.session["bot"]["reactAt"]


def test_recovery_counter_needs_remaining_time_and_clear_contact(duel):
    duel.session["action"] = {"intent": 300560, "index": 3, "seq": 4, "released": True}
    duel.later(battle.ACTION_TIMER, 400)
    observe(duel)
    observe(duel, 200)
    duel.session["bot"]["reactAt"] = duel.now + 1000
    world = duel.context["botWorld"]
    world["terrain"][1]["target"]["ready"] = False
    assert choose(duel).kind == "wait"
    world["terrain"][1]["target"]["ready"] = True
    duel.session["pending"][battle.ACTION_TIMER] = duel.now + 50
    assert choose(duel).kind == "wait"


@pytest.mark.parametrize("reason", ["attack", "parry", "hurt", "reaction"])
def test_decision_does_not_interrupt_locked_actions(duel, reason):
    battle.startAction(duel, 300560)
    observe(duel)
    observe(duel, 200)
    if reason == "attack": battle.startAction(duel, 300550, actorId=2)
    if reason == "parry": battle.startAction(duel, 300610, actorId=2)
    if reason == "hurt": duel.session["bot"]["hurtUntil"] = duel.now + 400
    if reason == "reaction": duel.session["bot"]["action"] = {"index": -1, "reaction": "parried"}
    before = deepcopy(duel.state)
    assert choose(duel).reason == "action-locked"
    assert duel.state == before


@pytest.mark.parametrize("guard,blocked", [(300580, {300560}), (300600, {300570}),
                                         (300590, {300540, 300550}), (300610, {300540, 300550})])
def test_direction_selection_avoids_observed_guard_including_both_lateral_attacks(duel, guard, blocked):
    battle.startAction(duel, guard)
    observe(duel)
    duel.now = duel.session["pending"][battle.ACTION_TIMER]
    app.handleTimer(duel, battle.ACTION_TIMER)
    observe(duel, decision.OBSERVATION_MS)
    for seed in range(20):
        duel.session["bot"]["decisionSeed"] = seed
        assert choose(duel).intent not in blocked


def test_attack_variety_is_replayable_and_does_not_repeat_immediately(duel):
    observe(duel)
    original = deepcopy(duel.state)
    choices = []
    for _ in range(12):
        choice = choose(duel)
        choices.append(choice.intent)
        duel.session["bot"].update(lastAttack=choice.intent, decisionSeed=choice.seed)
    assert len(set(choices)) >= 3
    assert all(a != b for a, b in zip(choices, choices[1:]))
    duel.state = original
    duel.session = original["sessions"]["1"]
    assert choose(duel).intent == choices[0]


def test_decision_memory_survives_reload_without_mutating_old_state(duel):
    observe(duel)
    choice = choose(duel)
    duel.session["bot"].update(lastAttack=choice.intent, decisionSeed=choice.seed)
    original = deepcopy(duel.state)
    assert app.migrateState(app.STATE_VERSION, duel.state) == original
    assert duel.state == original
    for field, invalid in (("decisionSeed", -1), ("decisionSeed", True), ("lastAttack", 999),
                           ("observedPlayer", {"seenAt": "bad"})):
        broken = deepcopy(original)
        broken["sessions"]["1"]["bot"][field] = invalid
        assert not app.validateState(broken)


@pytest.fixture
def nearBot(duel, monkeypatch):
    nav = NavMesh.fromPolygons([[(-5, -5, 0), (5, -5, 0), (5, 5, 0), (-5, 5, 0)]])
    monkeypatch.setattr(bot, "navigationFor", lambda flow: nav)
    duel.session["bot"]["reactAt"] = None
    return duel


def test_bot_executes_guard_once_and_leaves_player_action_untouched(nearBot):
    flow = nearBot
    battle.startAction(flow, 300550, released=True)
    player = deepcopy(flow.session["action"])
    tick(flow)
    assert flow.session["bot"]["action"]["index"] == -1
    observe(flow, 200)
    tick(flow)
    assert flow.session["bot"]["action"]["intent"] == 300610
    due = flow.session["pending"][bot.RELEASE_TIMER]
    sequence = flow.session["bot"]["action"]["seq"]
    observe(flow, 100)
    tick(flow)
    assert flow.session["bot"]["action"]["seq"] == sequence
    assert flow.session["pending"][bot.RELEASE_TIMER] == due
    assert flow.session["action"] == player


def test_bot_starts_counterattack_without_changing_weapon_timing(nearBot):
    flow = nearBot
    battle.startAction(flow, 300560, released=True)
    for _ in range(2):
        flow.now = flow.session["pending"][battle.ACTION_TIMER]
        app.handleTimer(flow, battle.ACTION_TIMER)
    snapshot(flow, (1.5, 0., .2))
    tick(flow)
    observe(flow, 200)
    tick(flow)
    value = flow.session["bot"]
    assert value["status"] == "counterattack"
    assert value["attackCount"] == 1 and value["lastAttack"] == value["action"]["intent"]
    assert flow.session["pending"][bot.ACTION_TIMER] - flow.now == battle.phaseMs(value["lastAttack"], 0)
    assert any("reason=counterattack" in line["message"] for line in flow.result["logs"])


@pytest.mark.parametrize("timerName", [bot.THINK_TIMER, bot.MOVE_TIMER])
def test_stale_world_discards_observation_and_does_not_choose_action(nearBot, timerName):
    flow = nearBot
    battle.startAction(flow, 300560)
    tick(flow)
    assert "observedPlayer" in flow.session["bot"]
    snapshot(flow, (1.5, 0., .2), age=1000)
    tick(flow, timerName)
    assert "observedPlayer" not in flow.session["bot"]
    assert flow.session["bot"]["reactAt"] is None
    assert flow.session["bot"]["action"]["index"] == -1


def test_hot_reload_preserves_observation_and_continues_the_same_decision(nearBot, tmp_path, monkeypatch):
    from pathlib import Path
    flow = nearBot
    battle.startAction(flow, 300550, released=True)
    tick(flow)
    runtime = host_runtime.Runtime(Path(__file__).resolve().parents[2] / "tests/reference/Business/scripts", tmp_path)
    try:
        original = deepcopy(flow.state)
        runtime.prepare()
        state = runtime.switch(host_runtime.encode(original))
        monkeypatch.setattr(runtime.active.module.bot, "navigationFor", bot.navigationFor)
        assert host_runtime.decode(state) == original
        observe(flow, 200)
        event = {"type": "timer", "connection": 1, "name": bot.THINK_TIMER}
        context = {**flow.context, "nowMs": flow.now, "connections": [1]}
        state, sends, timers, logs = runtime.dispatch(host_runtime.encode(event), state, host_runtime.encode(context))
        current = host_runtime.decode(state)["sessions"]["1"]["bot"]
        assert current["action"]["intent"] == 300610
        assert current["observedPlayer"]["seenAt"] == original["sessions"]["1"]["bot"]["observedPlayer"]["seenAt"]
    finally:
        runtime.close()
