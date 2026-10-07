"""宿主级契约测试：经 ``host_runtime.Runtime`` 走一遍，而不是自己搭 Flow 桩。

为什么要单独一层
----------------
``src/Business/scripts/verify_*.py`` 用的是手写的 ``Flow`` 桩，绕过了宿主的
``validateTransition``。于是有一类问题它们**结构上不可能发现**：

* ``result["send"][i]["connection"]`` 必须是**整数**且落在 ``live`` 连接表里 ——
  桩不校验，宿主用 ``PyLong_AsUnsignedLongLong`` 解，传字符串直接抛
  ``send targets a non-live connection``，整次事件被拒。
* 热重载（``prepare()`` + ``switch()``）会重建模块，模块全局回到启动默认值，
  而 ``state`` 还留着原场景 —— 桩根本不走重载路径。

本文件用真实的 ``Runtime`` 覆盖这两类。
"""
import importlib
import struct
import sys
from pathlib import Path

from Business.scripts import contracts as wire
from Business.scripts import voice
from Business.scripts.codec import audio_flow, protocol

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/Business" / "runtime"))
import host_runtime  # noqa: E402

SCRIPTS = ROOT / "src/Business" / "scripts"


def _runtime(tmp_path):
    return host_runtime.Runtime(SCRIPTS, tmp_path / "revisions")


def _contracts(runtime):
    """当前生效 revision 里的 ``contracts`` 子模块（模块全局在这里）。"""
    return importlib.import_module(runtime.active.name + ".contracts")


def _context(nowMs, connections, **extra):
    value = {"nowMs": nowMs, "advertisedAddress": "127.0.0.1", "instancePort": 12345,
             "connections": list(connections)}
    value.update(extra)
    return host_runtime.encode(value)


def _event(kind, connection, **extra):
    value = {"type": kind, "eventId": 1, "role": "instance", "name": "",
             "connection": connection, "sequence": 0, "serverTimeMs": 1,
             "command": 0, "body": b""}
    value.update(extra)
    return host_runtime.encode(value)


def _instanceSession(extra=None):
    session = {"role": "instance", "hydration": [], "pending": {}, "camp": 1,
               "authenticated": True}
    session.update(extra or {})
    return session


def _voiceBody(sound_id=60001, owner_rid=1):
    """``CS_PROTO_TRANS_MULTI_REQ``：selector(2) + trans_type(4) + content_type(4)
    + 3D 音效 union(sound_id u32 + svr_id u64)。"""
    return (struct.pack(">HiiI", voice.TRANS_MULTI_REQ,
                        audio_flow.TRANS_TYPE_ROOM_ALL,
                        voice.TRANS_CONTENT_3D_SOUND, sound_id)
            + struct.pack(">Q", owner_rid))


def test_voice_broadcast_targets_live_connections(tmp_path):
    """喊话广播的 connection 必须是整数连接号，否则宿主整次事件都拒。"""
    runtime = _runtime(tmp_path)
    try:
        context = _context(1000, [7, 8])
        state = host_runtime.decode(runtime.create(context))
        state["sessions"]["7"] = _instanceSession()
        state["sessions"]["8"] = _instanceSession()
        state = host_runtime.encode(state)

        _, sends, _, logs = runtime.dispatch(
            _event("message", 7, command=voice.TRANS_COMMAND,
                   body=_voiceBody()), state, context)
    finally:
        runtime.close()

    assert [item["connection"] for item in sends] == [8], sends
    assert all(type(item["connection"]) is int for item in sends)
    assert any("voice-quick-talk-broadcast" in line for line in logs), logs


def test_voice_broadcast_skips_sender_without_echo(tmp_path):
    """发送方默认不回发（客户端本地已播），广播只给其他 instance 会话。"""
    runtime = _runtime(tmp_path)
    try:
        context = _context(1000, [7, 8])
        state = host_runtime.decode(runtime.create(context))
        state["sessions"]["7"] = _instanceSession()
        state["sessions"]["8"] = _instanceSession({"role": "logic"})
        state = host_runtime.encode(state)

        _, sends, _, _ = runtime.dispatch(
            _event("message", 7, command=voice.TRANS_COMMAND,
                   body=_voiceBody()), state, context)
    finally:
        runtime.close()

    assert sends == []


def test_hot_reload_keeps_level_and_weapon_slot(tmp_path):
    """``prepare()`` + ``switch()`` 重建模块后，关卡与武器槽必须从 state 还原。"""
    runtime = _runtime(tmp_path)
    try:
        context = _context(1000, [7])
        module = _contracts(runtime)
        level = 10085
        resource = module.PATTERN_BY_LEVEL[level]
        state = host_runtime.decode(runtime.create(context))
        state["sessions"]["7"] = _instanceSession({
            "runtime": {"levelId": level, "levelCustom": True, "resourceId": resource,
                        "heroId": module.HERO_ID, "weaponSlot": 3,
                        "campSpawnCamp": None},
        })
        state = host_runtime.encode(state)

        runtime.prepare()
        state = runtime.switch(state)
        reloaded = _contracts(runtime)
    finally:
        pass

    # 新模块刚建好时是启动默认值 —— 这一条正是被修掉的现象
    assert reloaded.LEVEL_ID != level or reloaded.RESOURCE_ID != resource

    try:
        _, sends, _, _ = runtime.dispatch(
            _event("message", 7, command=0xA, body=b"\x00\x6a"), state, context)
    finally:
        runtime.close()

    body = next(item["body"] for item in sends if item["command"] == 0xA)
    _, _, _, startedAt, resourceId, _, _ = struct.unpack_from(">HQQQiiB", body, 0)
    assert resourceId == resource, "热重载后关卡退回了默认值"
    assert reloaded.currentSlot() == 3, "热重载后武器槽退回了默认值"


def test_host_login_carries_configured_name_and_level(tmp_path):
    """宿主注入的昵称与统一等级必须原样到达登录应答。"""
    runtime = _runtime(tmp_path)
    try:
        context = _context(0, [7], playerName="Rekindler")
        state = runtime.create(context)
        state = runtime.dispatch(
            _event("connected", 7, role="logic"), state, context)[0]
        state = runtime.dispatch(_event("authenticated", 7, role="logic"),
                                 state, context)[0]
        # 第一次到点前不发；把时钟推到 ``later("login", 25000)`` 之后
        late = _context(25000, [7], playerName="Rekindler")
        _, sends, _, _ = runtime.dispatch(
            _event("timer", 7, role="logic", name="login"), state, late)
    finally:
        runtime.close()

    login = next(item for item in sends if item["command"] == 1)
    identity = protocol.decode_minimal_login_success(login["body"])
    assert identity.user_name == "Rekindler".encode("gbk")
    assert identity.level == wire.USER_LEVEL == 100
