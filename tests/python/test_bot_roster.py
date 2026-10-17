from copy import deepcopy
from pathlib import Path
import struct
import xml.etree.ElementTree as ET

import pytest

from Business.runtime import host_runtime
from Business.scripts import actors, app, battle, bot, combat, controls, roster, scene, spawn_resource, weapons
from Business.scripts.navigation import NavMesh
from test_bot_player import flowAt
from test_melee_combat import swing, advance, hp
from blade_fixture import contact, publish
from vfs_fixture import spawnXml


def test_spawn_resource_excludes_revive_and_cavalry_points():
    points = spawn_resource.parseSpawns(ET.tostring(spawnXml()))
    assert {camp: len(values) for camp, values in points.items()} == {1: 10, 2: 10}
    assert points[1][0] == ((0., 20., .2), -90)
    assert points[2][0] == ((0., 40., .2), 90)


@pytest.mark.parametrize("mutation", ["count", "overlap", "direction", "nonfinite", "camp"])
def test_invalid_spawn_resource_is_rejected(mutation):
    root = spawnXml()
    if mutation == "count":
        root[0].find("point_num").set("value", "9")
    elif mutation == "overlap":
        root[0].findall("points")[1].find("pos").set("value", "0,20,0.2")
    elif mutation == "direction":
        root[0].find("points/dir").set("value", "0,0,0")
    elif mutation == "nonfinite":
        root[0].find("points/pos").set("value", "nan,0,0")
    else:
        root[0].find("is_red").set("value", "1")
    with pytest.raises(ValueError):
        spawn_resource.parseSpawns(ET.tostring(root))


@pytest.fixture
def multi(spawnPoints, monkeypatch):
    from damage_fixture import install
    install(monkeypatch)
    flow = flowAt()
    roster.assemble(flow)
    nav = NavMesh.fromPolygons([[(-100, -100, 0), (100, -100, 0), (100, 100, 0), (-100, 100, 0)]])
    monkeypatch.setattr(bot, "navigationFor", lambda flow: nav)
    return flow


def sample(flow, placements=None):
    entries = {}
    for rid in actors.ids(flow.session):
        data = actors.data(flow, rid)
        position, heading = (data["spawnPosition"], data["spawnHeading"]) if rid == 1 else (data["position"], data["heading"])
        position, heading = (placements or {}).get(rid, (position, heading))
        entries[str(rid)] = dict(ready=True, position=list(position), heading=heading, targetId=0,
                                terrain={name: dict(ready=True, hit=False) for name in ("forward", "target")})
    value = dict(ready=True, sampledAt=flow.now, instanceStartedAt=flow.session["instanceStartedAt"], actors=entries)
    flow.context["botWorld"] = value
    for rid in actors.ids(flow.session):
        candidates = actors.enemies(flow, rid, value)
        entries[str(rid)]["targetId"] = candidates[0] if candidates else 0
    return value


@pytest.mark.parametrize("camp", [1, 2])
def test_all_spawns_are_unique_with_one_reserved_for_selected_camp(multi, spawnPoints, camp):
    flow = multi
    flow.session["camp"] = camp
    roster.assemble(flow)
    assert flow.session["spawnPosition"] == list(spawnPoints[camp][0][0])
    assert len(actors.bots(flow.session)) == 19
    assert sum(value["camp"] == camp for value in actors.bots(flow.session).values()) == 9
    assert sum(value["camp"] != camp for value in actors.bots(flow.session).values()) == 10
    positions = {tuple(flow.session["spawnPosition"]), *(tuple(value["position"]) for value in actors.bots(flow.session).values())}
    assert len(positions) == 20
    assert all(set(values) <= positions for values in [[point[0] for point in points] for points in spawnPoints.values()])
    assert struct.unpack_from(">h", roster.playerVision(flow), 45)[0] == spawnPoints[camp][0][1]
    assert app.validateState(flow.state)


def test_friendly_and_enemy_bots_select_opponents_and_ignore_missing_neighbor(multi):
    # Both teams are inside local perception range and face one another.
    value = sample(multi, {rid: ((rid % 10, actors.data(multi, rid)['camp'] * 6, .2),
                                -90 if actors.data(multi, rid)['camp'] == 1 else 90)
                           for rid in actors.ids(multi.session)})
    value["actors"]["2"]["ready"] = False
    for rid in range(3, 21):
        snapshot = bot.world(multi, rid)
        assert snapshot is not None
        assert snapshot["targetId"] != 2
        assert actors.data(multi, snapshot["targetId"])["camp"] != actors.data(multi, rid)["camp"]
    assert bot.world(multi, 2) is None


@pytest.mark.parametrize("attacker,defender,ally", [(1, 2, 12), (2, 12, 3), (12, 2, 1)])
def test_multiactor_hits_only_enemy_without_cancelling_locomotion(multi, attacker, defender, ally):
    placements = {attacker: ((0, 0, .2), 0), defender: ((1.5, 0, .2), 180), ally: ((.5, 0, .2), 180)}
    sample(multi, placements)
    publish(multi, attacker, [contact(ally, along=.1), contact(defender)])
    for rid in (defender, ally):
        if rid != 1:
            actors.data(multi, rid)["moving"] = True
            multi.later(actors.timerName(bot.MOVE_TIMER, rid), 10000)
    swing(multi, attacker)
    advance(multi, combat.hitTimer(attacker))
    assert hp(multi, defender) == 80
    assert hp(multi, ally) == hp(multi, attacker) == 100
    if ally != 1:
        assert actors.data(multi, ally)["moving"]
        assert actors.timerName(bot.MOVE_TIMER, ally) in multi.session["pending"]
    if defender != 1:
        assert actors.data(multi, defender)["moving"]
        assert actors.timerName(bot.MOVE_TIMER, defender) in multi.session['pending']
        assert 'hurtUntil' in actors.data(multi, defender)


def test_lateral_bot_vs_bot_parry_keeps_other_actions(multi):
    sample(multi, {2: ((0, 0, .2), 0), 12: ((1.5, 0, .2), 180)})
    publish(multi, 2, [contact(12, defenceWeaponId=weapons.equipped(actors.data(multi, 12)))])
    battle.startAction(multi, 300610, actorId=12)
    advance(multi, battle.actionTimer(12))
    battle.startAction(multi, 300560, actorId=3)
    swing(multi, 2, 300550)
    advance(multi, combat.hitTimer(2))
    assert hp(multi, 12) == 100
    assert actors.data(multi, 2)["action"]["reaction"] == "parried"
    assert actors.data(multi, 12)["action"]["reaction"] == "parry-success"
    assert battle.actionTimer(3) in multi.session["pending"]


def test_ai_terrain_query_does_not_replace_missing_blade_contacts(multi):
    value = sample(multi, {1: ((0, 0, .2), 0), 2: ((1.5, 0, .2), 180)})
    value["actors"]["1"]["targetId"] = 3
    swing(multi)
    advance(multi, combat.hitTimer(1))
    assert hp(multi, 2) == 100
    assert multi.session["swing"]["miss"] == "blade-unavailable"


def test_independent_timers_and_full_roster_survive_reload_then_leave(multi, tmp_path):
    sample(multi)
    bot.start(multi)
    for rid in range(2, 21):
        battle.startAction(multi, 300560, released=True, actorId=rid)
    timers = deepcopy(multi.session["pending"])
    assert len({battle.actionTimer(rid) for rid in range(1, 21)}) == 20
    runtime = host_runtime.Runtime(Path(__file__).resolve().parents[2] / "tests/reference/Business/scripts", tmp_path / "revisions")
    try:
        runtime.prepare()
        state = runtime.switch(host_runtime.encode(multi.state))
        restored = host_runtime.decode(state)
        assert restored == multi.state
        assert restored["sessions"]["1"]["pending"] == timers
    finally:
        runtime.close()
    multi.result["timers"].clear()
    scene.leave(multi, struct.pack(">Hb", 2, 0))
    assert set(multi.session["pending"]) == {"logout"}
    assert all(value["status"] == "stopped" for value in actors.bots(multi.session).values())
    assert len(multi.result["timers"]) <= 256


def test_all_actors_can_tick_without_shared_actions_or_invalid_state(multi):
    sample(multi)
    for rid in range(2, 21):
        bot.think(multi, rid)
        value = actors.data(multi, rid)
        berserker = value['profile'] == 'berserker'
        assert value['status'] == ('approach' if berserker else 'advance')
        assert (value.get('opponentId') is not None) == berserker
    multi.now += bot.MOVE_MS
    sample(multi)
    for rid in range(2, 21):
        app.handleTimer(multi, actors.timerName(bot.MOVE_TIMER, rid))
    assert app.validateState(multi.state)
    assert all(len(multi.result[key]) <= 256 for key in ("send", "timers", "logs"))


def test_ai_batch_keeps_one_pulse_while_action_timers_use_exact_deadlines(multi):
    sample(multi)
    multi.result["timers"].clear()
    bot.start(multi)
    for rid in range(2, 21):
        battle.startAction(multi, 300560, released=True, actorId=rid)
    phaseNames = [battle.actionTimer(rid) for rid in range(2, 21)]
    assert [item["event"]["name"] for item in multi.result["timers"]] == [actors.PULSE_TIMER, *phaseNames]
    assert all(item["delayMs"] == 360 for item in multi.result["timers"][1:])
    multi.now += 350
    sample(multi)
    app.handleTimer(multi, actors.PULSE_TIMER)
    assert all(actors.data(multi, rid)["action"]["index"] == 0 for rid in range(2, 21))
    for name in phaseNames:
        advance(multi, name)
    assert multi.now == 1360 < multi.session['pending'][actors.PULSE_TIMER]
    assert all(actors.data(multi, rid)["action"]["index"] == 2 for rid in range(2, 21))
    assert all(combat.hitTimer(rid) in multi.session["pending"] for rid in range(2, 21))
    assert all(item["event"]["name"] in (actors.PULSE_TIMER, bot.DECISION_TIMER, *phaseNames)
               for item in multi.result["timers"])
    assert app.validateState(multi.state)


def test_multiactor_round_does_not_reuse_legacy_two_actor_snapshot(multi):
    multi.context["botWorld"] = dict(ready=True, sampledAt=multi.now, instanceStartedAt=100,
                                     player=[0., 0., .2], bot=[1., 0., .2])
    assert bot.world(multi, 3) is None
