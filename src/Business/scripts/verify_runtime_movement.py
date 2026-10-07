"""验证 ``controls.runtimeMovement`` 闸门：宿主注入 True 时进入「客户端权威」模式。

跑法（与 ``verify_mount_turn.py`` 同款，手动敲，不进运行时）：

    cd <Business> && PYTHONIOENCODING=utf-8 <python> scripts/verify_runtime_movement.py

判据：

  * ``runtimeMovement=True``（t7-rekindle 宿主注入）
      - selector 52 / 3 **只接受**客户端上报的位置与朝向，不驱动任何服务端运动；
      - ⭐ 第四十一轮：按键**变化**时补一条「移动状态镜像」（走/跑/停的 MOVE_BC）——
        这是客户端权威下「按 W 一会进跑 / SHIFT 立即加速」的唯一来源（老版靠
        服务端权威的 50ms 周期帧）。它**不驱动位移**，只镜像驱动状态；
      - 周期拍（ground-step）**不再追加**服务端运动帧，且运动定时器被取消；
      - 快跑（sel=63）记标志 + 补一条「跑」档镜像，**不发**旧版的
        ``move-ground-fast-run-state-echo``。
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
from scripts.codec import move_flow  # noqa: E402

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
# ⭐ 第四十一轮：不再是「一条都不发」—— 按 W（掩码变化沿）要补一条**走档**镜像，
#   但那是「镜像驱动状态」而不是「服务端驱动位移」：位置仍是客户端报的，且**没有**
#   服务端运动定时器在跑。回退：``cc_move_mirror=off`` ⇒ 逐位回到旧行为。
_mirror = [x for x in f.sent if x[1] == "client-authority-move-mirror"]
check("selector 52：只补一条走档镜像（不驱动位移）",
      len(f.sent) == 1 and len(_mirror) == 1, "sent=%d %r" % (len(f.sent), [x[1] for x in f.sent]))
if _mirror:
    _dm = move_flow.decode_move_bc_with_system_and_active(_mirror[0][2])
    check("镜像帧 = 走档（state=%d）" % controls.GROUND_WALK_STATES[(-1, 0)],
          _dm.state == controls.GROUND_WALK_STATES[(-1, 0)], "state=%d" % _dm.state)
check("镜像不改账本（位置仍是客户端报的）", g.get("position") == POS,
      repr(g.get("position")))
check("留下 no-motion-echo 日志",
      any("no-motion-echo" in x for x in f.result["logs"]),
      repr(f.result["logs"]))

f.now += 50
_nSent = len(f.sent)
controls.timer(f, "ground-step")
check("ground-step 周期拍：不再追加服务端运动帧", len(f.sent) == _nSent,
      "%d 条" % (len(f.sent) - _nSent))
check("运动定时器被取消", "ground-step" not in f.session["pending"],
      repr(list(f.session["pending"])))

_nSent = len(f.sent)
controls.handleFastRun(f, b"\x00\x3f" + struct.pack(">b", 1))
check("快跑：记标志 + 补一条跑档镜像（不发旧版 echo）",
      g.get("fastRun") == 1 and len(f.sent) == _nSent + 1
      and f.sent[-1][1] == "client-authority-fast-run-mirror",
      "fastRun=%r sent=%d %r" % (g.get("fastRun"), len(f.sent), f.sent[-1][1]))

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


print("★ round-game：必须补发「控制解锁」帧 actorState(6)")
# 上游 tests/python/test_runtime_movement.py::test_runtime_controls_unlock_only_
# after_start_countdown_and_without_respawn 断言 [0xA, 0x36, 2]。
# ⚠️ 缺这条帧 ⇒ actor 永远停在 battleEntry 结尾的 state 8（不可操控）
#    ⇒ 实机「WASD / F1F2 / 空格 / ~ 切空手 / Ctrl 下蹲」全部无响应。
f5 = Flow(True)
f5.session.update(groundEnabled=False, instanceStartedAt=0,
                  moveClock=controls.MOVE_CLOCK)
scene.timer(f5, "round-game")
check("客户端权威：round-game 发 [0xA, 0x36, 2]（控制解锁）",
      [command for command, _, _ in f5.sent] == [0xA, 0x36, 2],
      repr([(command, reason) for command, reason, _ in f5.sent]))
check("解锁帧的 body 是 actorState(now, 6)",
      f5.sent[1][2] == scene.wire.actorState(f5.now, 6)
      or f5.sent[1][2][2:10] == scene.wire.actorState(f5.now, 6)[2:10],
      repr(f5.sent[1][2][:12]))
f6 = Flow(False)
f6.session.update(groundEnabled=False, instanceStartedAt=0,
                  moveClock=controls.MOVE_CLOCK)
scene.timer(f6, "round-game")
check("服务端权威：同样发 actorState(6)（对照）",
      [command for command, _, _ in f6.sent] == [0xA, 0x36],
      repr([command for command, _, _ in f6.sent]))

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

# ---- 第三十九轮：客户端权威下走/跑只镜像客户端显式 fastRun -------------------
# 用户口供「老版按 W 一会 SHIFT 就显示（服务端 2s 自动进跑）；现在两个都启动了」。
# ``moveStartedAt`` 在客户端权威下由 localReport 之外的路径写入后**只增不减**，
# 于是服务端广播「跑」而客户端本地是「走」⇒ 两边状态打架。
import types as _types  # noqa: E402


def _forward_proj():
    return _types.SimpleNamespace(moving=True, forward_back=-1, left_right=0)


_walk = controls.GROUND_WALK_STATES[(-1, 0)]
_run = controls.GROUND_RUN_STATES[(-1, 0)]

check("服务端权威：持续前向 >2s ⇒ 跑（旧兜底保留，逐位不变）",
      controls.groundMoveState({"moveStartedAt": 0, "fastRun": 0},
                               _forward_proj(), 5000) == _run)
check("客户端权威：未发 fastRun ⇒ 走（不被 2s 兜底顶成跑）",
      controls.groundMoveState({"moveStartedAt": 0, "fastRun": 0},
                               _forward_proj(), 5000, False) == _walk)
check("客户端权威：fastRun=1 ⇒ 跑（镜像 SHIFT）",
      controls.groundMoveState({"moveStartedAt": 0, "fastRun": 1},
                               _forward_proj(), 5000, False) == _run)
check("客户端权威：刚起步也不误判（elapsed=0 ⇒ 走）",
      controls.groundMoveState({"moveStartedAt": 5000, "fastRun": 0},
                               _forward_proj(), 5000, False) == _walk)

print("ALL OK" if all(RESULTS) else "FAIL " + str(RESULTS.count(False)))
sys.exit(0 if all(RESULTS) else 1)
