"""验证客户端权威下的云梯斜面校正（``controls.climbCorrect``）。

跑法（与 ``verify_runtime_movement.py`` 同款，手动敲，不进运行时）：

    cd <Business> && PYTHONIOENCODING=utf-8 <python> scripts/verify_climb_echo.py

背景：樊城云梯立起来后**能直接穿过去**。两条根因叠加 ——
  ① ``crop`` 通道只让客户端播原生动画、不发刚体（客户端侧无碰撞体）；
  ② 客户端权威移动下 ``controls.message()`` 对 sel 3/52 无条件走 ``localReport``
     ⇒ 服务端运动链（``advanceGround`` → ``groundZAt`` → ``climbFaceZ``）**不跑**
     ⇒ ``siege`` 登记的爬升面**没有任何人读**。
本脚本验的是 ② 的补丁：收到上报位置后查一次斜面，命中就把 Z 补上并用
selector4（``broadcastHeading``）回发。

判据：
  * 位置落在斜面下方 ⇒ 恰好发 1 条 selector4，body 里的 Z = 斜面高度，
    水平 x/y 与上报值**逐位相同**（不许水平橡皮筋）；
  * Z 已到位 / 不在斜面范围 / 没有登记面 ⇒ **一条都不发**；
  * ``T7_CC_LADDER_ECHO=0`` ⇒ 一条都不发（回退开关）。
"""
import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import controls  # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(ok)
    print(("  OK   " if ok else "  FAIL ") + name + ("  " + detail if detail else ""))


# 会话 ``51992-1437159953-1`` 10:51 实机登记的那架（pathId=40028 / 云梯3）。
FACE = "10010,578.96,583.96,43.49,0.997,0.071,11.26,11.38,1.20,1.20"
FOOT = (578.96, 583.96, 43.49)
UX, UY, RUN, RISE = 0.997, 0.071, 11.26, 11.38


def faceZ(t):
    """斜面在沿梯方向 t 米处的高度（与 ``climbFaceZ`` 同公式）。"""
    return FOOT[2] + RISE * (t / RUN)


def onFace(t, lateral=0.0):
    """斜面上沿梯 t 米、横向 lateral 米处的 (x, y)。"""
    return (FOOT[0] + t * UX - lateral * UY, FOOT[1] + t * UY + lateral * UX)


class Flow:
    """带 ``state`` 的最小 flow（照抄 ``verify_runtime_movement.Flow``）。"""

    def __init__(self, runtimeMovement=True, faces=FACE):
        self.now = 1000
        self.sent = []
        self.state = {"runtimeMovement": runtimeMovement}
        session = {"groundEnabled": True, "battleEntered": True,
                   "role": "instance", "pending": {}}
        if faces is not None:
            session[controls.CLIMB_FACES_KEY] = faces
        self.session = session
        self.result = {"logs": []}

    def send(self, command, body, reason):
        self.sent.append((command, reason, body))

    def later(self, name, ms):
        self.session["pending"][name] = self.now + int(ms)

    def cancel(self, name):
        self.session["pending"].pop(name, None)


def headingBody(heading, position):
    """selector 3：21B，heading(int16)@7、position(3×float32)@9。"""
    return b"\x00" * 7 + struct.pack(">h", heading) + struct.pack(">fff", *position)


def report(flow, position, heading=0):
    return controls.localReport(flow, 3, headingBody(heading, position))


# selector4 = ``>HIBHhfff``（selector/tick/count/target/yaw/x/y/z，23 B）
# ⇒ 位置三只 float32 固定在 offset 11（见 ``move_flow._MOVE_DIRECT_BC_STRUCT_FORMAT``）。
DIRECT_POSITION_OFFSET = 11


def decodePosition(body):
    """selector4 的 body 里位置三只 float32。"""
    if len(body) < DIRECT_POSITION_OFFSET + 12:
        return None, None
    return DIRECT_POSITION_OFFSET, struct.unpack_from(
        ">fff", body, DIRECT_POSITION_OFFSET)


print("== 1. 命中斜面：恰好 1 条 selector4，Z 补上、水平不动 ==")
flow = Flow()
x, y = onFace(1.0)
want = faceZ(1.0)
ok = report(flow, (x, y, FOOT[2]))
check("localReport 返回 True", ok is True)
check("恰好发 1 条", len(flow.sent) == 1, "sent=%d" % len(flow.sent))
if flow.sent:
    command, reason, body = flow.sent[0]
    check("是 selector4 那条", reason == "move-ground-heading-direct-bc", reason)
    offset, values = decodePosition(body)
    check("body 里找得到位置三元组", offset is not None)
    if values is not None:
        check("x 逐位不变", abs(values[0] - x) < 1e-4, "%.4f vs %.4f" % (values[0], x))
        check("y 逐位不变", abs(values[1] - y) < 1e-4, "%.4f vs %.4f" % (values[1], y))
        check("z = 斜面高度", abs(values[2] - want) < 1e-3,
              "%.4f vs %.4f" % (values[2], want))
check("账本 Z 也被补上", abs(flow.session["ground"]["position"][2] - want) < 1e-3,
      "%.4f vs %.4f" % (flow.session["ground"]["position"][2], want))
check("记了 ladder-climb-correct 日志",
      any("ladder-climb-correct" in line for line in flow.result["logs"]),
      str(flow.result["logs"][:1]))

print("== 2. Z 已到位：一条都不发 ==")
flow = Flow()
x, y = onFace(1.0)
report(flow, (x, y, faceZ(1.0)))
check("已到位不发", len(flow.sent) == 0, "sent=%d" % len(flow.sent))

print("== 3. 不在斜面范围：一条都不发 ==")
flow = Flow()
report(flow, (0.0, 0.0, 0.0))
check("图外不发", len(flow.sent) == 0, "sent=%d" % len(flow.sent))
flow = Flow()
x, y = onFace(1.0, lateral=5.0)          # 横向超出 half=1.20
report(flow, (x, y, FOOT[2]))
check("横向出界不发", len(flow.sent) == 0, "sent=%d" % len(flow.sent))
flow = Flow()
x, y = onFace(RUN + 5.0)                 # 越过梯顶平台（run+cap=12.46）
report(flow, (x, y, FOOT[2]))
check("越过梯顶不发", len(flow.sent) == 0, "sent=%d" % len(flow.sent))

print("== 4. 没登记面（ladder_climb=off）：一条都不发 ==")
flow = Flow(faces=None)
x, y = onFace(1.0)
report(flow, (x, y, FOOT[2]))
check("无面不发", len(flow.sent) == 0, "sent=%d" % len(flow.sent))

print("== 5. 回退开关 T7_CC_LADDER_ECHO=0：一条都不发 ==")
os.environ[controls.CLIMB_ECHO_ENV] = "0"
try:
    check("开关读到 off", controls.climbEchoEnabled() is False)
    flow = Flow()
    x, y = onFace(1.0)
    report(flow, (x, y, FOOT[2]))
    check("关掉后不发", len(flow.sent) == 0, "sent=%d" % len(flow.sent))
finally:
    del os.environ[controls.CLIMB_ECHO_ENV]

print("== 6. 节流：100 ms 内第二次不发，过了就发 ==")
flow = Flow()
x, y = onFace(1.0)
report(flow, (x, y, FOOT[2]))
first = len(flow.sent)
report(flow, (x, y, FOOT[2]))            # 同一时刻、Z 又被客户端「打回」地面
check("同拍不重发", len(flow.sent) == first, "sent=%d" % len(flow.sent))
flow.now += controls.CLIMB_ECHO_MIN_MS + 1
report(flow, (x, y, FOOT[2]))
check("过了节流窗就发", len(flow.sent) == first + 1, "sent=%d" % len(flow.sent))

print("== 7. 模式判定（闸门在 message() 里，见 verify_runtime_movement.py）==")
check("客户端权威模式名", controls.movementMode(Flow(True)) == controls.wire.RUNTIME_MOVEMENT_MODE,
      controls.movementMode(Flow(True)))
check("服务端权威模式名", controls.movementMode(Flow(False)) == "server-ground-v1",
      controls.movementMode(Flow(False)))

passed = sum(1 for ok in RESULTS if ok)
print("\n%d/%d passed" % (passed, len(RESULTS)))
sys.exit(0 if passed == len(RESULTS) else 1)
