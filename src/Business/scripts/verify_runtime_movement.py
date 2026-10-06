"""验证 ``controls.runtimeMovement`` 闸门：宿主注入 True 时进入「客户端权威」模式。

跑法（与 ``verify_mount_turn.py`` 同款，手动敲，不进运行时）：

    cd <Business> && PYTHONIOENCODING=utf-8 <python> scripts/verify_runtime_movement.py

判据：

  * ``runtimeMovement=True``（t7-rekindle 宿主注入）
      - selector 52 / 3 **只接受**客户端上报的位置与朝向，一条运动帧都不发；
      - 周期拍（ground-step）不发帧，且运动定时器被取消；
      - 快跑（sel=63）只记标志，不发 ``move-ground-fast-run-state-echo``。
  * ``runtimeMovement=False``（自建 T7.Server.exe，宿主不注入）
      - 与改动前同口径：照旧下发运动帧。
  * 取不到 ``flow.state``（离线仿真桩，如 ``verify_mount_turn.Flow``）
      - 退回服务端权威，**不抛 AttributeError**。

任何一条不满足就退出码 1 —— 改移动路径后先跑它。
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import contracts as c  # noqa: E402
from scripts import controls  # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(ok)
    print(("  OK   " if ok else "  FAIL ") + name + ("  " + detail if detail else ""))


class Flow:
    """带 ``state`` 的最小 flow（对照 ``verify_mount_turn.Flow``，多了 state）。"""

    def __init__(self, runtimeMovement):
        self.now = 1000
        self.sent = []
        self.state = {"runtimeMovement": runtimeMovement}
        self.session = {"heroId": 110001, "camp": 1, "battleEntered": True,
                        "groundEnabled": True, "leaving": False, "role": "instance",
                        "pending": {}, "controlBaseline": c.BASELINE_ID}
        self.result = {"logs": []}
        self.timerDue = None

    def send(self, command, body, reason):
        self.sent.append((command, reason, body))

    def later(self, name, ms):
        self.session["pending"][name] = self.now + int(ms)

    def cancel(self, name):
        self.session["pending"].pop(name, None)


def keyBody(category, keys, position):
    """selector 52：``>HI6s`` + 三只 float32 = 24B（position@12）。"""
    return struct.pack(">HI6s", 52, category, bytes(keys)) \
        + struct.pack(">fff", *position)


def headingBody(heading, position):
    """selector 3：21B，heading(int16)@7、position(3×float32)@9。"""
    return b"\x00" * 7 + struct.pack(">h", heading) + struct.pack(">fff", *position)


POS = [111.0, 222.0, 33.0]

print("★ runtimeMovement=True（客户端权威，= t7-rekindle 宿主注入）")
f = Flow(True)
controls.message(f, 2, 52, keyBody(controls.MOVE_KEY_CATEG_WASD, [1, 0, 0, 0], POS))
g = f.session.get("ground", {})
check("selector 52：接受客户端上报的位置", g.get("position") == POS,
      repr(g.get("position")))
check("selector 52：不发任何运动帧", len(f.sent) == 0, "%d 条" % len(f.sent))
check("留下 no-motion-echo 日志",
      any("no-motion-echo" in x for x in f.result["logs"]),
      repr(f.result["logs"]))

f.now += 50
controls.timer(f, "ground-step")
check("ground-step 周期拍：一条不发", len(f.sent) == 0, "%d 条" % len(f.sent))
check("运动定时器被取消", "ground-step" not in f.session["pending"],
      repr(list(f.session["pending"])))

controls.handleFastRun(f, b"\x00\x3f" + struct.pack(">b", 1))
check("快跑：只记标志、不发帧",
      g.get("fastRun") == 1 and len(f.sent) == 0,
      "fastRun=%r sent=%d" % (g.get("fastRun"), len(f.sent)))

f3 = Flow(True)
controls.message(f3, 2, 3, headingBody(-45, POS))
g3 = f3.session.get("ground", {})
check("selector 3：接受朝向 + 位置且不发帧",
      g3.get("heading") == -45 and g3.get("position") == POS and len(f3.sent) == 0,
      "heading=%r pos=%r sent=%d"
      % (g3.get("heading"), g3.get("position"), len(f3.sent)))

print("★ runtimeMovement=False（服务端权威，= 自建 T7.Server.exe）")
f2 = Flow(False)
controls.message(f2, 2, 52, keyBody(controls.MOVE_KEY_CATEG_WASD, [1, 0, 0, 0], POS))
check("照旧下发运动帧（对照）", len(f2.sent) == 1, "%d 条" % len(f2.sent))


print("★ 无 state 的离线桩（verify_mount_turn.Flow 那种）")


class NoStateFlow(Flow):
    def __init__(self, runtimeMovement):
        super().__init__(runtimeMovement)
        del self.state


check("取不到 state ⇒ 退回服务端权威（不抛 AttributeError）",
      controls.runtimeMovement(NoStateFlow(True)) is False)

print("ALL OK" if all(RESULTS) else "FAIL " + str(RESULTS.count(False)))
sys.exit(0 if all(RESULTS) else 1)
