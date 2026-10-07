"""验证门机关状态机（``mo.isSwitch`` / ``nextSwitchState`` / ``settledState``）。

跑法：

    cd <Business> && PYTHONIOENCODING=utf-8 <python> scripts/verify_mo_switch.py

背景（用户原始诉求「开城门按 C 开门」）：实机上按 C 交互的**不是门本体，而是机关** ——
会话 ``51992-1437159953-1`` 里 ``mo-interact target=10005 state=2000``，
而 10005 = **侧门机关**（tid=9）。机关此前落进 ``_handleInteract`` 的
「云梯/门以外 ⇒ 只确认收到、不改状态」分支 ⇒ 门一直开不了。

判据：
  * ``isSwitch`` 只认 2000..2003，且与门段（3010..3017）、云梯段（1000..1004）**不重叠**；
  * ``nextSwitchState``：2000 →(2003)→ 落 2002；2002 →(2001)→ 落 2000；
    过渡态（2003/2001）**不响应**（与云梯/门同规矩）；
  * ``settledState`` 必须给机关落点，否则会卡在过渡态；
  * ``T7_MO_SWITCH=0`` ⇒ ``switchEnabled()`` 为 False（回退开关）。
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import mo  # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(ok)
    print(("  OK   " if ok else "  FAIL ") + name + ("  " + detail if detail else ""))


print("== 1. 常量与分段 ==")
check("SWITCH_CLOSED = 2000", mo.MO_STATE_SWITCH_CLOSED == 2000)
check("SWITCH_CLOSING = 2001", mo.MO_STATE_SWITCH_CLOSING == 2001)
check("SWITCH_OPEN = 2002", mo.MO_STATE_SWITCH_OPEN == 2002)
check("SWITCH_OPENING = 2003", mo.MO_STATE_SWITCH_OPENING == 2003)
check("机关四态都在 isSwitch 里", all(mo.isSwitch(s) for s in (2000, 2001, 2002, 2003)))
check("2004 不算机关", not mo.isSwitch(2004))
check("门段不算机关", not any(mo.isSwitch(s) for s in (3010, 3012, 3014, 3016)))
check("云梯段不算机关", not any(mo.isSwitch(s) for s in (1000, 1002, 1003)))
check("机关不算门", not any(mo.isDoor(s) for s in (2000, 2002, 2003)))
check("机关不算云梯", not any(mo.isLadder(s) for s in (2000, 2002, 2003)))

print("== 2. 开门方向：2000 → 2003 → 2002 ==")
t = mo.nextSwitchState(mo.MO_STATE_SWITCH_CLOSED)
check("2000 按 C 进 2003(OPENING)", t is not None and t[0] == mo.MO_STATE_SWITCH_OPENING,
      str(t))
check("2003 落点到 2002(OPEN)", mo.settledState(mo.MO_STATE_SWITCH_OPENING) == mo.MO_STATE_SWITCH_OPEN)

print("== 3. 关门方向：2002 → 2001 → 2000 ==")
t = mo.nextSwitchState(mo.MO_STATE_SWITCH_OPEN)
check("2002 按 C 进 2001(CLOSING)", t is not None and t[0] == mo.MO_STATE_SWITCH_CLOSING,
      str(t))
check("2001 落点到 2000(CLOSED)", mo.settledState(mo.MO_STATE_SWITCH_CLOSING) == mo.MO_STATE_SWITCH_CLOSED)

print("== 4. 过渡态不响应（连按 C 不会连发）==")
check("2003 不响应", mo.nextSwitchState(mo.MO_STATE_SWITCH_OPENING) is None)
check("2001 不响应", mo.nextSwitchState(mo.MO_STATE_SWITCH_CLOSING) is None)

print("== 5. 完整一圈：2000 → 2003 → 2002 → 2001 → 2000 ==")
state = mo.MO_STATE_SWITCH_CLOSED
trail = [state]
for _ in range(4):
    step = mo.nextSwitchState(state)
    if step is None:
        break
    state = mo.settledState(step[0])
    trail.append(state)
check("走回原点且经过 4 步", trail == [2000, 2002, 2000, 2002, 2000] or
      trail == [2000, 2002, 2000, 2002, 2000], str(trail))
check("第 1 步落到 2002(开)", trail[1] == mo.MO_STATE_SWITCH_OPEN, str(trail))
check("第 2 步落到 2000(关)", trail[2] == mo.MO_STATE_SWITCH_CLOSED, str(trail))

print("== 6. 开关（回退用）==")
check("默认开", mo.switchEnabled() is True)
os.environ[mo.MO_SWITCH_ENV] = "0"
try:
    check("T7_MO_SWITCH=0 ⇒ 关", mo.switchEnabled() is False)
finally:
    del os.environ[mo.MO_SWITCH_ENV]
os.environ[mo.MO_SWITCH_ENV] = "1"
try:
    check("T7_MO_SWITCH=1 ⇒ 开", mo.switchEnabled() is True)
finally:
    del os.environ[mo.MO_SWITCH_ENV]

print("== 7. 门/云梯状态机未被带坏（回归门）==")
check("门 3010 按 C 仍进 3011", mo.nextDoorState(3010) is not None
      and mo.nextDoorState(3010)[0] == mo.MO_STATE_MAIN_DOOR_OPENING)
check("云梯 1000 按 C 仍进 1002", mo.nextLadderState(1000) is not None
      and mo.nextLadderState(1000)[0] == mo.MO_STATE_LADDER_INAIR)
check("机关不影响门落点", mo.settledState(3011) == mo.MO_STATE_MAIN_DOOR_OPENED)


print("== 8. 机关⇒门联动（真实 tszz 数据）==")
class PeerFlow:
    """最小桩：``airWallScene`` 只读 ``session['scene']``。"""
    def __init__(self):
        self.session = {"scene": "tszz"}
        self.result = {"logs": []}
    def send(self, *args):
        pass
    def later(self, *args):
        pass
    def cancel(self, *args):
        pass


flow = PeerFlow()
# tszz 实测配对（ccobject.json）：10003 正门机关↔10002 正门、
# 10005 侧门机关↔10004 侧门、10008 内门机关↔10009 内门。
check("正门机关(10003) 配对 → 正门(10002)",
      mo._doorPeerRid(flow, 10003) == 10002, str(mo._doorPeerRid(flow, 10003)))
check("侧门机关(10005) 配对 → 侧门(10004)",
      mo._doorPeerRid(flow, 10005) == 10004, str(mo._doorPeerRid(flow, 10005)))
check("内门机关(10008) 配对 → 内门(10009)",
      mo._doorPeerRid(flow, 10008) == 10009, str(mo._doorPeerRid(flow, 10008)))
check("反向也通：正门(10002) → 正门机关(10003)",
      mo._doorPeerRid(flow, 10002) == 10003, str(mo._doorPeerRid(flow, 10002)))
check("云梯(10010) 无配对 ⇒ None",
      mo._doorPeerRid(flow, 10010) is None, str(mo._doorPeerRid(flow, 10010)))
check("联动开关默认开", mo.switchDoorLinkEnabled() is True)
os.environ[mo.MO_DOOR_LINK_ENV] = "0"
try:
    check("T7_MO_DOOR_LINK=0 ⇒ 关", mo.switchDoorLinkEnabled() is False)
finally:
    del os.environ[mo.MO_DOOR_LINK_ENV]

passed = sum(1 for ok in RESULTS if ok)
print("\n%d/%d passed" % (passed, len(RESULTS)))
sys.exit(0 if passed == len(RESULTS) else 1)
