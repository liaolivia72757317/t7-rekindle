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
  * ``scene.battleEntry`` / ``scene.sendVisionObject`` 的同款闸门
      - 客户端权威：**不发** ``instance-ground-initial-stop`` 静止帧；
        视野 ADD 回**客户端上报的实时坐标**（回出生点 = 每次刷新都把本地
        角色拉回去，撤销掉客户端刚走的位移 ⇒ 实机 WASD 无效）；
      - 服务端权威：照旧发静止帧、照旧回出生点。
  * 取不到 ``flow.state``（离线仿真桩，如 ``verify_mount_turn.Flow``）
      - 退回服务端权威，**不抛 AttributeError**。

任何一条不满足就退出码 1 —— 改移动路径后先跑它。
"""
import struct
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import contracts as c  # noqa: E402
from scripts import controls  # noqa: E402
from scripts import scene  # noqa: E402

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
                        "heroChosen": True, "groundEnabled": True, "leaving": False,
                        "role": "instance", "pending": {},
                        "controlBaseline": c.BASELINE_ID}
        self.result = {"logs": []}
        self.timerDue = None

    def phase(self, name):
        """``scene.battleEntry`` 末尾会调；桩里无副作用。"""

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


print("★ scene.battleEntry：客户端权威时不得下发 initial-stop 静止帧")

# battleEntry 里那些「依赖场景资源 / 实例时钟」的协作者与本组判据无关，
# 换成空操作，只留下要观测的静止帧闸门那条路径。
for _name in ("sendCcObjects", "sendTutorialObjects", "sendNpcObjects",
              "initializeBattleState", "scheduleBattleMusic", "sendInstanceEnter"):
    setattr(scene, _name, lambda flow: None)
scene.dungeon = types.SimpleNamespace(enterTraining=lambda flow: None)


def entryReasons(runtimeMovement):
    flow = Flow(runtimeMovement)
    flow.session["battleEntered"] = False  # 否则 battleEntry 第一行就 return
    scene.battleEntry(flow)
    return [reason for _, reason, _ in flow.sent]


authoritative = entryReasons(True)
check("客户端权威：不发 initial-stop 静止帧（= 人物不被钉在出生点）",
      not any("initial-stop" in r for r in authoritative), repr(authoritative))
check("服务端权威：照旧发 initial-stop 静止帧（对照）",
      any("initial-stop" in r for r in entryReasons(False)), "")

print("★ scene.sendVisionObject：客户端权威时回实时坐标，不回出生点")

POS_REPORTED = (111.0, 222.0, 33.0)
BYTES_REPORTED = struct.pack(">fff", *POS_REPORTED)
BYTES_SPAWN = struct.pack(">fff", *c.POSITION)
# 人物对象在 ADD_EVENT 里的坐标偏移：头部 >Hii 10 B + 对象头 >IQH 14 B
# + ACTOR_CURR_MOVE_DATA 内偏移 23 B（>bbB 3 B + h*10 20 B）= 47。
# ⚠️ 别退化成「body 里不得出现出生点字节」：骑兵图里坐骑是**第二个**视野
#    对象，它本来就停在出生点（见 verify_mount_turn 的骑兵用例）。
ACTOR_POSITION_OFFSET = 10 + 14 + 23


def visionBody(runtimeMovement):
    flow = Flow(runtimeMovement)
    controls.groundState(flow)["position"] = list(POS_REPORTED)
    handled = scene.sendVisionObject(flow, scene.wire.ACTOR_ID)
    return handled, flow.sent[-1][2]


def actorPositionBytes(body):
    return body[ACTOR_POSITION_OFFSET:ACTOR_POSITION_OFFSET + 12]


handledA, bodyA = visionBody(True)
check("客户端权威：人物对象坐标 = 客户端上报的实时坐标",
      handledA and actorPositionBytes(bodyA) == BYTES_REPORTED,
      repr(actorPositionBytes(bodyA)))
handledS, bodyS = visionBody(False)
check("服务端权威：人物对象坐标仍是出生点（对照）",
      handledS and actorPositionBytes(bodyS) == BYTES_SPAWN,
      repr(actorPositionBytes(bodyS)))


print("★ 无 state 的离线桩（verify_mount_turn.Flow 那种）")


class NoStateFlow(Flow):
    def __init__(self, runtimeMovement):
        super().__init__(runtimeMovement)
        del self.state


# 无 state 的离线桩退回 ``CLIENT_RUNTIME_MOVEMENT`` 默认值（独立跑 fixture 时
# 即服务端权威）；不抛 AttributeError 的口径不变。真实开关是宿主注入的
# ``runtimeMovement``，见 ``controls.runtimeMovement``。
check("取不到 state ⇒ 退回 CLIENT_RUNTIME_MOVEMENT 默认(False)（不抛 AttributeError）",
      controls.runtimeMovement(NoStateFlow(True)) is c.CLIENT_RUNTIME_MOVEMENT)

print("ALL OK" if all(RESULTS) else "FAIL " + str(RESULTS.count(False)))
sys.exit(0 if all(RESULTS) else 1)
