"""实例起点必须整局固定，刷新（``0xA/0x6A``）不许把它推走。

需求
----
``instance-minimal-update`` 里 ``server_time_ms``（「现在」）与
``instance_start_time_ms``（「本局起点」）语义不同。若两者都取当前时间，客户端
每次请求刷新都会被告诉「本局刚刚开始」，而移动 / 战斗状态仍按原起点算 ⇒
时间轴不一致（倒计时重置、状态机错位）。

判据：
  * 刷新只允许 ``server_time_ms`` 前进，``instance_start_time_ms`` 必须与
    ``scene.begin()`` 时**逐值相同**；
  * 没走过 ``begin()``（``instanceEpochMs`` 未落库）时退回「起点 = 现在」。
"""
import struct

from Business.scripts import app, contracts as wire, scene
from Business.scripts.codec import room_flow

# 外层报文号（``flow.send`` 的第一个参数）；``selector`` 0x6A = 请求刷新实例信息。
INSTANCE_COMMAND = 0xA
INSTANCE_REFRESH_SELECTOR = 0x6A
# 内层 TDR 的 ``CS_PROTO_SI_UPDATE_INST`` 号（``room_flow.INSTANCE_UPDATE``）。
WIRE_INSTANCE_UPDATE = room_flow.INSTANCE_UPDATE
# 定长前缀：>HQQQiiB
#   cmd(u16) server_time_ms(u64) instance_id(u64) instance_start_time_ms(u64)
#   resource_id(i32) start_pattern(i32) u8
_PREFIX = struct.Struct(">HQQQiiB")

T0 = 1_700_000_000_000


def _decode(body):
    cmd, serverTime, instanceId, startedAt, resourceId, pattern, tail = \
        _PREFIX.unpack_from(body, 0)
    assert cmd == WIRE_INSTANCE_UPDATE, (cmd, WIRE_INSTANCE_UPDATE)
    assert instanceId == 1
    return {"server_time_ms": serverTime, "instance_start_time_ms": startedAt,
            "resource_id": resourceId, "start_pattern": pattern}


def _flow(nowMs):
    state = app.createState({})
    state["sessions"]["1"] = {"role": "instance", "hydration": [], "pending": {}, "camp": 1}
    return app.Flow({"connection": 1}, state, {"nowMs": nowMs})


def _instanceUpdate(flow):
    for packet in flow.result["send"]:
        if packet["command"] == INSTANCE_COMMAND:
            return _decode(packet["body"])
    raise AssertionError("没有 instance-minimal-update 报文: "
                         + str([p["command"] for p in flow.result["send"]]))


def test_begin_records_fixed_instance_origin(monkeypatch):
    clock = {"ms": T0}
    monkeypatch.setattr(wire, "serverNowMs", lambda: clock["ms"])

    flow = _flow(1000)
    scene.begin(flow)

    info = _instanceUpdate(flow)
    assert info["server_time_ms"] == T0
    assert info["instance_start_time_ms"] == T0
    # epoch 起点落在 session 里，供后续刷新复用
    assert flow.session["instanceEpochMs"] == T0
    # 单调钟那一份仍然只用于本地差值
    assert flow.session["instanceStartedAt"] == 1000


def test_refresh_keeps_origin_and_advances_server_time(monkeypatch):
    clock = {"ms": T0}
    monkeypatch.setattr(wire, "serverNowMs", lambda: clock["ms"])

    flow = _flow(1000)
    scene.begin(flow)
    first = _instanceUpdate(flow)

    # 十分钟后客户端请求刷新
    clock["ms"] += 600_000
    flow.result["send"].clear()
    flow.now = 601_000
    assert scene.message(flow, INSTANCE_COMMAND, INSTANCE_REFRESH_SELECTOR, b"") is True
    again = _instanceUpdate(flow)

    assert again["instance_start_time_ms"] == first["instance_start_time_ms"], \
        "刷新把实例起点推走了（P1-2 回归）"
    assert again["server_time_ms"] == first["server_time_ms"] + 600_000, \
        "server_time_ms 应随刷新前进"


def test_repeated_refresh_never_moves_origin(monkeypatch):
    clock = {"ms": T0}
    monkeypatch.setattr(wire, "serverNowMs", lambda: clock["ms"])

    flow = _flow(1000)
    scene.begin(flow)
    origin = _instanceUpdate(flow)["instance_start_time_ms"]

    for step in range(1, 6):
        clock["ms"] += 90_000
        flow.result["send"].clear()
        scene.message(flow, INSTANCE_COMMAND, INSTANCE_REFRESH_SELECTOR, b"")
        assert _instanceUpdate(flow)["instance_start_time_ms"] == origin


def test_refresh_before_begin_falls_back_to_now(monkeypatch):
    """没走过 ``begin()``（无 ``instanceEpochMs``）时退回「起点 = 现在」，不炸。"""
    clock = {"ms": T0}
    monkeypatch.setattr(wire, "serverNowMs", lambda: clock["ms"])

    flow = _flow(1000)
    assert scene.message(flow, INSTANCE_COMMAND, INSTANCE_REFRESH_SELECTOR, b"") is True
    info = _instanceUpdate(flow)
    assert info["instance_start_time_ms"] == T0
    assert info["server_time_ms"] == T0


def test_instanceInfo_honours_explicit_startedAt(monkeypatch):
    clock = {"ms": T0 + 5_000}
    monkeypatch.setattr(wire, "serverNowMs", lambda: clock["ms"])
    info = _decode(wire.instanceInfo(0, T0))
    assert info["server_time_ms"] == T0 + 5_000
    assert info["instance_start_time_ms"] == T0
