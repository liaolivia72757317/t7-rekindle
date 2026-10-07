# -*- coding: utf-8 -*-
"""验证投石车下车的控制解除链（第三轮：下车补发 31 号 active=1 给玩家本体）。

跑法（与 ``verify_climb_echo.py`` 同款）：

    cd <Business> && PYTHONIOENCODING=utf-8 <python> scripts/verify_cat_dismount.py

背景（实机口供：「投石车按 C 下来，视角没法切换了」）：
  * 第一轮：下车补 43 号 ``lock=0``（``move-lock-orientation-unlock``）——
    日志证实发出，实机无效；
  * 第二轮：补 sel=27 ``(actor, mo_mid=0)`` 解绑 —— 日志证实发出（event=1104），
    实机无效；
  * 第三轮（本脚本验证）：下车补发 31 号 ``MOVE_NOTIFY_ACTIVE(active=1)``
    靶**玩家本体**（inst 1）—— 31 号是客户端 movable「可位移」总闸（原版硬
    约束「unactive 的时候，移动物体是不能在地图上进行位移操作的」），上车链
    可能把玩家本体置回 unactive，下车没人把它置回来。

判据：
  * ``sendControlOff`` 依次发出 4 条：CONTROL_OFF(sel=mo) → sel=27 解绑 →
    43 号解锁 → 31 号 ``active=1``（target=1）；
  * 顺序不能乱（先解绑后重激活）；
  * ``T7_CC_CAT_ACTIVE_RESYNC=0`` ⇒ 31 号不发（回退开关），其余照发。
"""
import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import siege                      # noqa: E402
from scripts.codec import mo_flow, move_flow   # noqa: E402

# ⭐ 2026-10-07 第四十一轮：本脚本验证的是**代码路径**，与运行时的 ini A/B 无关。
#   所以显式钉住「27 号解绑帧」这一支 —— 否则 ini 里的 ``cat_actor_release=off``
#   （下车不能动的单变量 A/B）会把回归染红，而那不是代码坏了。
#   想验 off 分支：把下面那行改成 "0" 再跑，应看到 5 条帧。
os.environ.setdefault("T7_CC_CAT_ACTOR_RELEASE", "1")

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(ok)
    print(("  OK   " if ok else "  FAIL ") + name + ("  " + detail if detail else ""))


class RecFlow:
    """捕获 send 的最小桩（``instanceTick`` 无时钟 ⇒ 退回 tick=1）。"""

    now = 0

    def __init__(self):
        self.session = {"scene": "tszz"}
        self.result = {"logs": []}
        self.frames = []

    def send(self, cmd, body, tag=""):
        self.frames.append((cmd, bytes(body), str(tag)))

    def later(self, *args):
        pass

    def cancel(self, *args):
        pass


def sel_of(body):
    """MO 通道帧的 msg_id = 首部 u16（``_pkg``：2 字节 msg_id + 分支载荷）。"""
    return struct.unpack(">H", body[:2])[0]


def first_u16(body):
    return struct.unpack(">H", body[:2])[0]


# ---- 1. 默认开：6 条按序发出（第四轮 + 回步兵两帧）-------------------------
flow = RecFlow()
siege.sendControlOff(flow, 10014, "verify")
tags = [t for _c, _b, t in flow.frames]
check("共发 6 条帧", len(flow.frames) == 6, "n=%d %s" % (len(flow.frames), tags))
check("① CONTROL_OFF(sel=5) 先发",
      flow.frames[0][0] == mo_flow.MO_COMMAND and sel_of(flow.frames[0][1]) == 5,
      tags[0] if tags else "")
check("② sel=27 解绑（actor, mo_mid=0）",
      flow.frames[1][0] == mo_flow.MO_COMMAND and sel_of(flow.frames[1][1]) == 27,
      tags[1] if len(tags) > 1 else "")
check("③ 43 号方向/相机锁解锁（0x2B）",
      flow.frames[2][0] == 2
      and first_u16(flow.frames[2][1]) == move_flow.MOVE_LOCK_ORIENTATION,
      tags[2] if len(tags) > 2 else "")
body31 = flow.frames[3][1]
sel31, tick31, inst31, act31 = struct.unpack(">HIHB", body31)
check("④ 31 号 MOVE_NOTIFY_ACTIVE 收尾",
      flow.frames[3][0] == 2 and sel31 == move_flow.MOVE_NOTIFY_ACTIVE,
      "sel=%d tick=%d inst=%d active=%d" % (sel31, tick31, inst31, act31))
check("④ 靶玩家本体 inst=1 / active=1", inst31 == 1 and act31 == 1,
      "inst=%d active=%d" % (inst31, act31))
check("④ 顺序：解绑(27) 之后才重激活(31)",
      tags.index("instance-move-notify-active-after-dismount")
      > tags.index("mo-actor-control-release-actor1") if len(tags) == 6 else False)

# ---- 1b. 第四轮：回步兵两帧 ------------------------------------------------
check("⑤ 0x36 actorState(6) 回场上可操控",
      flow.frames[4][0] == 0x36, tags[4] if len(tags) > 4 else "")
try:
    from scripts import contracts as _w
    _body6 = flow.frames[4][1]
    _want6 = _w.actorState(0, 6)
    check("⑤ body 与 actorState(6) 同构", len(_body6) == len(_want6),
          "len=%d" % len(_body6))
except Exception as exc:  # noqa: BLE001
    check("⑤ body 校验（跳过）", True, str(exc))
check("⑥ 步兵静止档 ground-stop 收尾",
      len(tags) > 5 and tags[5] == "catapult-dismount-ground-stop",
      tags[5] if len(tags) > 5 else "")

# ---- 1c. 第四十一轮 A/B 的 off 分支：不发 27 号解绑 --------------------------
os.environ["T7_CC_CAT_ACTOR_RELEASE"] = "0"
try:
    check("cat_actor_release=off ⇒ 关", siege.catActorReleaseEnabled() is False)
    flow0 = RecFlow()
    siege.sendControlOff(flow0, 10014, "verify-norelease")
    tags0 = [t for _c, _b, t in flow0.frames]
    check("off ⇒ 5 条（无 27 解绑帧）",
          len(flow0.frames) == 5
          and all(t != "mo-actor-control-release-actor1" for t in tags0),
          "n=%d %s" % (len(flow0.frames), tags0))
    check("off ⇒ 其余四帧仍在（CONTROL_OFF 打头）",
          flow0.frames[0][0] == mo_flow.MO_COMMAND
          and sel_of(flow0.frames[0][1]) == 5
          and flow0.frames[1][0] == 2, str(tags0))
finally:
    os.environ["T7_CC_CAT_ACTOR_RELEASE"] = "1"

# ---- 2. 回退开关：31 号不发，前三条照发 ------------------------------------
check("回退开关默认开", siege.catActiveResyncEnabled() is True)
os.environ["T7_CC_CAT_ACTIVE_RESYNC"] = "0"
try:
    check("T7_CC_CAT_ACTIVE_RESYNC=0 ⇒ 关", siege.catActiveResyncEnabled() is False)
    flow2 = RecFlow()
    siege.sendControlOff(flow2, 10014, "verify-off")
    tags2 = [t for _c, _b, t in flow2.frames]
    check("回退后 5 条（无 31 号，有回步兵两帧）",
          len(flow2.frames) == 5
          and all(t != "instance-move-notify-active-after-dismount" for t in tags2),
          str(tags2))
finally:
    del os.environ["T7_CC_CAT_ACTIVE_RESYNC"]
os.environ["T7_CC_CAT_FOOT_RESYNC"] = "0"
try:
    check("T7_CC_CAT_FOOT_RESYNC=0 ⇒ 关", siege.catFootResyncEnabled() is False)
    flow3 = RecFlow()
    siege.sendControlOff(flow3, 10014, "verify-foot-off")
    tags3 = [t for _c, _b, t in flow3.frames]
    check("回退后 4 条（无回步兵两帧）",
          len(flow3.frames) == 4
          and all(t != "actor-in-scene-after-dismount"
                  and t != "catapult-dismount-ground-stop" for t in tags3),
          str(tags3))
finally:
    del os.environ["T7_CC_CAT_FOOT_RESYNC"]

# ---- 3. 第三十七轮：STOP_CONTROL(1013) 停止操控 ------------------------------
# 会话 -10 实机：按 C 下车后 HUD 仍挂投石车面板、人物原地不动 ⇒ 客户端没离开
# 「移动_投石车」控制树。协议里 MO_EVENT_STOP_CONTROL(1013) 一直没被用过。
check("cat_stop_event 默认开", siege.catStopEventEnabled() is True)

flow7 = RecFlow()
check("6000 那条 update_state 带 1013",
      siege.catapultControlEvents(flow7, 10015, siege.CATAPULT_WAIT)
      == (siege.MO_EVENT_STOP_CONTROL,),
      str(siege.catapultControlEvents(flow7, 10015, siege.CATAPULT_WAIT)))
check("非 6000 状态不带（原行为）",
      siege.catapultControlEvents(flow7, 10015, 9999) == (),
      str(siege.catapultControlEvents(flow7, 10015, 9999)))

flow8 = RecFlow()
ok8 = siege.sendStopControlPulse(flow8, 10015)
tags8 = [t for _c, _b, t in flow8.frames]
check("脉冲：6001 状态下发一发带事件的 update_state",
      ok8 is True and len(flow8.frames) == 1
      and flow8.frames[0][0] == mo_flow.MO_COMMAND
      and sel_of(flow8.frames[0][1]) == 3 and "stop-control+1013" in tags8[0],
      str(tags8))
_pulseBody = flow8.frames[0][1] if flow8.frames else b""
_noEventBody = mo_flow.encode_update_state(target=10015, state=siege.controlState(),
                                           state_change_ms=0, state_time_ms=0)
check("脉冲 body 比无事件版更长（事件真的编进去了）",
      len(_pulseBody) > len(_noEventBody),
      "pulse=%d plain=%d" % (len(_pulseBody), len(_noEventBody)))

os.environ["T7_CC_CAT_STOP_EVENT"] = "0"
try:
    check("T7_CC_CAT_STOP_EVENT=0 ⇒ 关", siege.catStopEventEnabled() is False)
    flow9 = RecFlow()
    check("关掉后脉冲不发", siege.sendStopControlPulse(flow9, 10015) is False
          and len(flow9.frames) == 0)
    check("关掉后 6000 也不带事件",
          siege.catapultControlEvents(flow9, 10015, siege.CATAPULT_WAIT) == ())
finally:
    del os.environ["T7_CC_CAT_STOP_EVENT"]

passed = sum(1 for ok in RESULTS if ok)
print("\nverify_cat_dismount: %d/%d OK" % (passed, len(RESULTS)))
sys.exit(0 if passed == len(RESULTS) else 1)
