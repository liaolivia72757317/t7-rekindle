import pytest

from Business.scripts import app, contracts as wire, scene
from Business.scripts.codec import protocol


@pytest.mark.parametrize("name", ["Rekindler", "重燃玩家", "界" * 15 + "A", "A" * 31])
def test_player_name_uses_gbk_storage_limit(name):
    assert wire.encodePlayerName("  " + name + "  ") == name.encode("gbk")


@pytest.mark.parametrize("name", ["", "  ", "A" * 32, "界" * 16, "a\0b", "a\nb", "a\tb", "😀", "€", "\uE000", None])
def test_invalid_player_names_are_rejected(name):
    with pytest.raises((ValueError, TypeError, UnicodeError)):
        wire.encodePlayerName(name)


def make_flow(name, role="instance"):
    state = app.createState({"playerName": name})
    state["sessions"]["1"] = {"role": role, "hydration": [], "pending": {}, "camp": 1}
    return app.Flow({"connection": 1}, state, {"nowMs": 1000})


@pytest.mark.parametrize("name", ["新玩家", "重燃测试员"])
def test_name_reaches_login_and_all_local_actor_messages(name):
    encoded = name.encode("gbk")
    flow = make_flow(name, "logic")
    app.handleTimer(flow, "login")
    # A due native timer is tracked in the per-connection ledger.
    flow.session["pending"]["login"] = 1000
    app.handleTimer(flow, "login")
    login = protocol.decode_minimal_login_success(flow.result["send"][0]["body"])
    assert login.user_name == encoded

    flow = make_flow(name)
    scene.message(flow, 0x23, 3, b"\0\x03\0")
    bodies = [item["body"] for item in flow.result["send"]]
    assert sum(encoded in body for body in bodies) == 2
    flow.session.update(heroChosen=True, heroId=wire.HERO_ID)
    scene.battleEntry(flow)
    assert encoded in next(item["body"] for item in flow.result["send"]
                           if item["reason"] == "actor-vision-add-after-battle-confirm")


def test_names_are_session_local_and_survive_state_migration():
    first = make_flow("玩家甲")
    second = make_flow("玩家乙")
    assert first.playerName == "玩家甲".encode("gbk")
    assert second.playerName == "玩家乙".encode("gbk")
    assert first.playerName != second.playerName
    assert app.migrateState(app.STATE_VERSION, first.state)["playerName"] == "玩家甲"
    assert wire.USER_NAME == "新玩家".encode("gbk")


def test_context_without_name_uses_new_player():
    state = app.createState({})
    assert state["playerName"] == "新玩家"
