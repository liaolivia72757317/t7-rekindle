"""骑兵转舵 / 骑乘 state 的离线仿真门（不进运行时，手动敲）。

    cd /d/流星/T7/server && PYTHONIOENCODING=utf-8 python/python.exe scripts/verify_mount_turn.py

判据（2026-09-22 实机反馈之后的口径，详见 ``README.md`` 同名条目）：

  * 步兵五种按键组合：朝向必须恒 0、按 W 位移必须 5.00 m、``state`` 必须是步战档
    （W=2），并且一条坐骑报文都不许有；
  * 骑兵按 D 朝向 **−120**、按 A **+120**（``mountTurnKey`` 的实机口径，
    转速 = ``MOUNT_TURN_RATE_DPS`` × 1 秒）；
  * 骑兵的 38 号 ``state`` 必须是**骑乘档**，且档号带上速度档偏移
    （``mountSpeedTier()`` = 1 ⇒ 前进 17、前右弧 31、后退 24、原地转 54）；
  * 骑兵的 38 号 ``left_right`` 轴**每一拍都必须是 0**（A/D 已经改走转舵，
    再喂横移轴就是 2026-09-22 实机「方向都是乱的」那条）；
  * 38 号里的 ``cv``/``mv``：骑兵的 ``mv`` 必须等于 ``坐骑.psheet`` 那档的第 4 级速度
    （×1000），步兵的两个数必须还是老字面值（这轮没动它），停止时双双归 0；
  * 35 号发不发由 ``controls.MOUNT_BC_ENABLED`` 决定（本轮 False），
    两种取值下都要自洽：True 时每步一条、False 时一条都没有；
  * 骑兵 W+D 的合位移必须**小于**直线那 5.00 m（走圆弧）；
  * 跳跃：坐骑的 54 号（滞空标志）起跳/落地各一条，必须都在。

任何一条不满足就退出码 1 —— 改 ``controls.py`` 骑乘路径后先跑它。
"""

import math
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import contracts as c  # noqa: E402
from scripts import controls  # noqa: E402
from scripts.codec import mount_flow  # noqa: E402
from scripts.codec import move_flow  # noqa: E402

GROUND_STEP_MS = c.GROUND_STEP_MS
STEPS = 20
# 名册里 110001 赵云无坐骑、2121 姜维有坐骑（``contracts`` 那行 battle loadout 日志）。
INFANTRY_HERO = 110001
MOUNTED_HERO = 2121
assert not c.heroHasMount(INFANTRY_HERO), "步兵卡不该带坐骑"
assert c.heroHasMount(MOUNTED_HERO), "骑兵卡该带坐骑，否则换一张卡"

# ★★ 显示轴符号的**一手证据矩阵**（2026-09-23 新增）。
# 出处：prior_art ``D:/刀锋铁骑/offline-re/prior_art/动作.txt`` 六 —— 做法是直接调
# 客户端原 MOVE_BC 消费器、发一条持续命令再自动发 MOVE_STOP，**由用户盯着画面
# 逐项确认**。原文确认项：
#     state=2, LR=0, FB=+1000, cv=1000  →  「向前慢走」（连看多次，三次 10 秒）
#     state=2, LR=0, FB=+1000, cv=5000  →  「快速奔跑」
#     state=2, LR=0, FB=-1000, cv=1000  →  「倒退走路」
#     state=4, LR=-1000, FB=0, cv=1000  →  「以人物为中心向右后撤步」
#     state=8, LR=+1000, FB=0, cv=1000  →  「低速向前慢走」
# 我们的 ``GROUND_WALK_STATES`` 给出的 state 正好 W→2、A→4、D→8 ⇒ 档位与上表逐项
# 对上，**只有轴符号反**。这张表是**照抄证据**，不是从 ``displayAxes`` 推出来的，
# 所以把 ``displayAxes`` 改坏它一定红（"能失败的检查"）。
# 值 = (LR, FB)，单位 ±1（下行时 ×1000）。
DISPLAY_MATRIX = {
    ("W", "client"): (0, +1),
    ("S", "client"): (0, -1),
    ("A", "client"): (-1, 0),
    ("D", "client"): (+1, 0),
    ("W", "world"): (0, -1),
    ("S", "world"): (0, +1),
    ("A", "world"): (+1, 0),
    ("D", "world"): (-1, 0),
}
KEY_MASKS = (("W", 1), ("S", 4), ("A", 2), ("D", 8))
# 38 号 body（不含 23 字节传输头），偏移按编码器格式 ``>HIHBhhihfffhibh`` 逐项算实：
# sel@0(H) tick@2(I) inst@6(H) state@8(B) **lr@9(h) fb@11(h) cv@13(i)** mv@17(h)
# pos@19(3×f) yaw@31(h) system_group@33(i) active@37(b) acc@38(h)，body 共 40 B。
# ⚠️ 2026-09-22 校正：``cv`` 是 **int32@13**，不是 int16@15 —— 之前读 @15 只因为它
# 低半字恰好等于 5000/25000 这种 <32767 的数才对上，一旦超过就会读出错的数。
RIDER_STATE_OFFSET = 8
RIDER_LR_OFFSET = 9
RIDER_FB_OFFSET = 11
RIDER_CV_OFFSET = 13
RIDER_MV_OFFSET = 17
RIDER_YAW_OFFSET = 31
RIDER_POS_OFFSET = 19


def axes(flow):
    """最后一条 38 号的 ``(forward_back, left_right)``（÷1000 还原成 ±1 轴）。"""
    bodies = flow.bodies(lambda r: r.startswith("move-ground"))
    if not bodies:
        return (None, None)
    body = bodies[-1]
    return (struct.unpack_from(">h", body, RIDER_FB_OFFSET)[0] // 1000,
            struct.unpack_from(">h", body, RIDER_LR_OFFSET)[0] // 1000)


def speeds(flow):
    """最后一条 38 号的 ``(cv, mv)``（m/s × 1000）。"""
    bodies = flow.bodies(lambda r: r.startswith("move-ground"))
    if not bodies:
        return None
    body = bodies[-1]
    return (struct.unpack_from(">i", body, RIDER_CV_OFFSET)[0],
            struct.unpack_from(">h", body, RIDER_MV_OFFSET)[0])


def keyBody(category, keys, position):
    return struct.pack(">HI6s", 52, category, bytes(keys)) \
        + struct.pack(">fff", *position)


class Flow:
    """最小可用的 flow：只实现 controls.py 用到的 send/later/cancel/session/now/result。"""

    def __init__(self, heroId):
        self.now = 1000
        self.sent = []
        self.session = {"heroId": heroId, "camp": 1, "battleEntered": True,
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

    def reasons(self):
        return [reason for _, reason, _ in self.sent]

    def bodies(self, predicate):
        return [body for _, reason, body in self.sent if predicate(reason)]


def fresh(heroId):
    flow = Flow(heroId)
    controls.groundState(flow)["position"] = list(c.POSITION)
    return flow


def riderFrames(flow):
    """``(state, yaw, x, y)``：所有 38 号（``move-ground*``）下行。"""
    return [(body[RIDER_STATE_OFFSET],
             struct.unpack_from(">h", body, RIDER_YAW_OFFSET)[0],
             struct.unpack_from(">f", body, RIDER_POS_OFFSET)[0],
             struct.unpack_from(">f", body, RIDER_POS_OFFSET + 4)[0])
            for body in flow.bodies(lambda r: r.startswith("move-ground"))]


def leftRightFrames(flow):
    """所有 38 号的 ``left_right`` 轴（已 ÷1000 还原成位移分量）。"""
    return [struct.unpack_from(">h", body, RIDER_LR_OFFSET)[0] // 1000
            for body in flow.bodies(lambda r: r.startswith("move-ground"))]


def mountBcCount(flow):
    return len(flow.bodies(lambda r: "mount-bc" in r))


# ★ 2026-10-06：原地转舵补发的 selector4「位置校正+方向」。
# body 格式 ``>HIBHhfff`` = sel@0 tick@2 count@6(B) target@7(H) **yaw@9(h)** pos@11(3×f)，
# 共 23 字节（``decode_move_direct_bc`` 就按 23 校验）。
DIRECT_REASON = "move-mount-turn-direct-bc"


def directTurns(flow):
    """所有 4 号补发：``(yaw, target, x, y)``。"""
    return [(struct.unpack_from(">h", body, 9)[0],
             struct.unpack_from(">H", body, 7)[0],
             struct.unpack_from(">f", body, 11)[0],
             struct.unpack_from(">f", body, 15)[0])
            for body in flow.bodies(lambda r: r == DIRECT_REASON)]


def framesWithoutDirect(flow):
    """整局下行（剔掉 4 号补发）的 ``(reason, body)`` 序列 —— 用来证明开关不碰别的包。"""
    return [(reason, body) for _, reason, body in flow.sent if reason != DIRECT_REASON]


# 35 号 body（不含传输头）的角速度区。偏移按 TDR ``CS_PROTO_MOVE_MOUNT_BC``
# 声明顺序 = 编码器格式 ``>HIHBhhhhhhhhffff`` 逐项算实：
#   sel@0(H) tick@2(I) inst@6(H) state@8(B)
#   a@9 bw@11 wf@13 wa@15 cw@17 waf@19 cv@21 mv@23  dir@25(f) pos@29/33/37(f)
MOUNT_ANG_ORDER = ("a", "bw", "wf", "wa", "cw", "waf", "cv", "mv")


def mountAngularFields(flow):
    """最后一条 35 号的 ``{a,bw,wf,wa,cw,waf,cv,mv}``。"""
    body = [b for b in flow.bodies(lambda r: "mount-bc" in r)][-1]
    return dict(zip(MOUNT_ANG_ORDER, struct.unpack_from(">hhhhhhhh", body, 9)))


def drive(heroId, mask, steps=STEPS):
    flow = fresh(heroId)
    keys = [(mask >> index) & 1 for index in range(4)] + [0, 0]
    controls.message(flow, 2, 52,
                     keyBody(controls.MOVE_KEY_CATEG_WASD, keys, list(c.POSITION)))
    for _ in range(steps):
        flow.now += GROUND_STEP_MS
        controls.timer(flow, "ground-step")
    return flow


def press(heroId, mask):
    """只发**一次**按键事件、一拍定时器都不跑。

    用途：检查「按键事件刚结束那一瞬间」的 ``session``。跑过定时器再查是查不到的
    —— ``periodic`` 那拍会把 ``lastEcho`` 重写成字符串，把按键路径留下的脏东西盖掉。
    """
    flow = fresh(heroId)
    keys = [(mask >> index) & 1 for index in range(4)] + [0, 0]
    controls.message(flow, 2, 52,
                     keyBody(controls.MOVE_KEY_CATEG_WASD, keys, list(c.POSITION)))
    return flow


def jump(heroId):
    flow = fresh(heroId)
    controls.message(flow, 2, 52,
                     keyBody(controls.MOVE_KEY_CATEG_SPACE, [0] * 5 + [1], list(c.POSITION)))
    for _ in range(14):
        flow.now += GROUND_STEP_MS
        controls.timer(flow, controls.JUMP_TIMER)
    return flow


def moved(flow):
    frames = riderFrames(flow)
    if not frames:
        return 0.0
    return ((frames[-1][2] - c.POSITION[0]) ** 2 + (frames[-1][3] - c.POSITION[1]) ** 2) ** 0.5


def main():
    failures = []

    def check(label, ok, detail):
        print(("  OK   " if ok else "  FAIL ") + label + "  " + detail)
        if not ok:
            failures.append(label)

    turnDegrees = round(controls.MOUNT_TURN_RATE_DPS * STEPS * GROUND_STEP_MS / 1000.0)
    expectMountBc = STEPS + 1 if controls.MOUNT_BC_ENABLED else 0
    print(f"MOUNT_TURN_RATE_DPS={controls.MOUNT_TURN_RATE_DPS} 转舵容差=±{turnDegrees}° "
          f"MOUNT_BC_ENABLED={controls.MOUNT_BC_ENABLED}（骑兵每局 35 号期望 {expectMountBc} 条）")

    print("步兵（朝向恒 0、state 用步战档、不许有坐骑报文）")
    # ⚠️ 期望的显示轴**写成字面值**（W ⇒ fb=−1），不是从 ``DISPLAY_MATRIX`` 查表 ——
    #    查表的话表跟着代码一起改，门就永远绿了（同义反复）。这里是「别在影响步兵了」
    #    那条硬约束的落地：步兵的 38 号 FB/LR 必须与引入 ``displayAxes`` 之前逐位相同。
    # 字面值是 ``(fb, lr)``，取世界轴原值 ``fb = s−w``、``lr = a−d``
    # （即引入 ``displayAxes`` 之前的下行值）。注意 ``DISPLAY_MATRIX`` 存的是 ``(lr, fb)``，
    # 两者字段序不同，别抄错。
    INFANTRY_WIRE = {"W": (-1, 0), "D": (0, -1), "A": (0, +1), "W+S": (0, 0)}
    for mask, key, wantState in ((1, "W", 2), (8, "D", 8), (2, "A", 4), (5, "W+S", 1)):
        flow = drive(INFANTRY_HERO, mask)
        frames = riderFrames(flow)
        heading = controls.groundState(flow)["heading"]
        distance = moved(flow)
        straight = STEPS * c.GROUND_STEP_DISTANCE
        wantDistance = straight if key == "W" else distance
        fb, lr = axes(flow)
        wantFb, wantLr = INFANTRY_WIRE[key]
        check(f"步兵 按{key}",
              heading == 0 and not mountBcCount(flow) and frames[-1][0] == wantState
              and abs(distance - wantDistance) < 0.01
              and (fb, lr) == (wantFb, wantLr),
              f"heading={heading} state={frames[-1][0]}(期望 {wantState}) "
              f"位移={distance:.2f} 35号={mountBcCount(flow)} "
              f"显示轴=(fb={fb},lr={lr}) 期望=(fb={wantFb},lr={wantLr})")
    # 步兵侧的两个「不许影响」硬门：把两个开关都翻到会伤步兵的那一侧，步兵必须一动不动。
    savedAxis, savedCadence = controls.WIRE_AXIS, controls.ECHO_CADENCE
    try:
        baseline = drive(INFANTRY_HERO, 1)
        baselineBodies = [b for _, r, b in baseline.sent if r.startswith("move-ground")]
        baselineAxes = axes(baseline)
        controls.WIRE_AXIS = "world"           # 翻到另一侧
        controls.ECHO_CADENCE = "onchange"     # 翻到会压帧的那一侧
        flipped = drive(INFANTRY_HERO, 1)
        flippedBodies = [b for _, r, b in flipped.sent if r.startswith("move-ground")]
    finally:
        controls.WIRE_AXIS, controls.ECHO_CADENCE = savedAxis, savedCadence
    check("步兵：wire_axis / echo_cadence 翻到另一侧，38 号必须逐字节不变",
          baselineBodies == flippedBodies,
          f"基线 {len(baselineBodies)} 条 / 翻转后 {len(flippedBodies)} 条，"
          f"逐字节{'相同' if baselineBodies == flippedBodies else '不同'}"
          f"（基线显示轴 fb={baselineAxes[0]}）")

    print("骑兵（A/D 转舵 + 38 号 state 换骑乘档 + 横移轴必须归 0）")
    tier = controls.mountSpeedTier()
    lineUp = move_flow.MOVE_GROUND_MOUNT_STATE_LINE_UP_ACC + tier
    arcRight = move_flow.MOVE_GROUND_MOUNT_STATE_ARC_RIGHT_UP_ACC + tier
    validMountStates = {
        move_flow.MOVE_GROUND_MOUNT_STATE_STOP,
        move_flow.MOVE_GROUND_MOUNT_STATE_LINE_DOWN,
        move_flow.MOVE_GROUND_MOUNT_STATE_ARC_LEFT_DOWN_WALK,
        move_flow.MOVE_GROUND_MOUNT_STATE_ARC_RIGHT_DOWN_WALK,
    }
    for base in (move_flow.MOVE_GROUND_MOUNT_STATE_LINE_UP_ACC,
                 move_flow.MOVE_GROUND_MOUNT_STATE_ARC_LEFT_UP_ACC,
                 move_flow.MOVE_GROUND_MOUNT_STATE_ARC_RIGHT_UP_ACC):
        validMountStates.update(base + index for index in range(move_flow.MOVE_GROUND_MOUNT_TIER_SPAN))
    # 只按 A/D = 原地转舵：前进投影为零，所以档位是 54（``mount_turn_state=arc`` 时是弧档），
    # 只有 yaw 在动。
    # ``wantFb`` **直接查上面那张证据矩阵**（不自己推符号），所以它真的能红。
    axisMode = controls.WIRE_AXIS
    fbOf = lambda key: DISPLAY_MATRIX[(key, axisMode)][1]      # noqa: E731
    # 原地转舵的期望档**跟着 ``[move] mount_turn_state`` 走**（2026-10-05：这一版实机
    # 翻成 arc，写死 54 会让这两条恒红，而它俩要守的是「横移轴归 0 + 零位移」）。
    turnA = (move_flow.MOVE_GROUND_MOUNT_STATE_ARC_LEFT_UP_ACC + tier
             if controls.MOUNT_TURN_STATE == "arc"
             else move_flow.MOVE_GROUND_MOUNT_STATE_STOP)
    turnD = (arcRight if controls.MOUNT_TURN_STATE == "arc"
             else move_flow.MOVE_GROUND_MOUNT_STATE_STOP)
    # ★ 2026-10-06：下面这几段（含 selector4 补发那一段）守的是**纯转舵 = 零位移**那套行为，
    #   必须把「小步转」钉在 0 —— 实机现在跑的是 ``[move] mount_turn_walk=30``，
    #   不钉住的话「按A 前后轴=0」那条会因为投影带上了前进输入而恒红。
    #   小步转自己的断言在后面的独立段落里。
    savedWalk = controls.MOUNT_TURN_WALK_PCT
    controls.MOUNT_TURN_WALK_PCT = 0
    cases = ((1, "W", 0, lineUp, fbOf("W")),
             (8, "D", -turnDegrees, turnD, 0),
             (2, "A", turnDegrees, turnA, 0),
             (9, "W+D", -turnDegrees, arcRight, fbOf("W")),
             (4, "S", 0, move_flow.MOVE_GROUND_MOUNT_STATE_LINE_DOWN, fbOf("S")))
    for mask, key, wantHeading, wantState, wantFb in cases:
        flow = drive(MOUNTED_HERO, mask)
        heading = controls.groundState(flow)["heading"]
        frames = riderFrames(flow)
        fb, lr = axes(flow)
        check(f"骑兵 按{key}",
              heading == wantHeading and frames[-1][0] == wantState
              and all(state in validMountStates for state, _, _, _ in frames)
              and leftRightFrames(flow) == [0] * len(frames)
              and fb == wantFb and lr == 0
              and mountBcCount(flow) == expectMountBc,
              f"heading={heading}(期望 {wantHeading}) state={frames[-1][0]}(期望 {wantState}) "
              f"yaw={frames[0][1] if frames else '-'}→{frames[-1][1] if frames else '-'} "
              f"前后轴={fb}(期望 {wantFb}) 横移轴={set(leftRightFrames(flow))} "
              f"位移={moved(flow):.2f} 35号={mountBcCount(flow)}")

    straight = moved(drive(MOUNTED_HERO, 1))
    arc = moved(drive(MOUNTED_HERO, 9))
    check("骑兵 W+D 比直线短", arc < straight, f"直线={straight:.2f} 圆弧={arc:.2f}")

    # ★★ 显示轴符号（2026-09-23）。治的是实机「WS 前进后退 是反的」。
    # ⚠️ 2026-09-23 收口：``displayAxes`` 现在**只对骑兵生效**（用户要求「别在影响步兵了」），
    #    所以这一段必须用**骑兵**测。步兵侧的对应断言是上面那条
    #    「步兵：wire_axis / echo_cadence 翻到另一侧，38 号必须逐字节不变」。
    # 断言用**照抄证据**的 DISPLAY_MATRIX，两种模式都测 ⇒ 改坏 displayAxes 必红。
    # 只测 W/S：骑兵的 A/D 不进位移投影（``MOUNT_DRIVING_KEY_MASK``），fb/lr 恒 0。
    print("★ 显示轴符号（骑兵；证据 = prior_art 动作.txt 六 的用户现场确认表）")
    savedAxis = controls.WIRE_AXIS
    for mode in ("client", "world"):
        controls.WIRE_AXIS = mode
        for key, mask in (("W", 1), ("S", 4)):
            flow = drive(MOUNTED_HERO, mask)
            fb, lr = axes(flow)
            wantLr, wantFb = DISPLAY_MATRIX[(key, mode)]
            check(f"显示轴[{mode}] 骑兵按{key}", (lr, fb) == (wantLr, wantFb),
                  f"LR={lr} FB={fb}（期望 LR={wantLr} FB={wantFb}）")
    controls.WIRE_AXIS = savedAxis
    # 世界积分**必须不受显示轴影响**：W 的合位移仍是 STEPS × 步长，朝向仍 0。
    controls.WIRE_AXIS = "world"
    worldMove = moved(drive(MOUNTED_HERO, 1))
    controls.WIRE_AXIS = "client"
    clientMove = moved(drive(MOUNTED_HERO, 1))
    controls.WIRE_AXIS = savedAxis
    check("显示轴不影响世界积分", abs(worldMove - clientMove) < 1e-6
          and abs(clientMove - STEPS * c.GROUND_STEP_DISTANCE) < 0.01,
          f"world={worldMove:.4f} client={clientMove:.4f} "
          f"期望 {STEPS * c.GROUND_STEP_DISTANCE:.4f}")

    # ★ 原地转舵档（2026-09-23）：``mount_turn_state`` 只换 state，不许产生位移。
    print("★ 原地转舵档（mount_turn_state=stop/arc；两种都必须零位移）")
    savedTurn = controls.MOUNT_TURN_STATE
    for mode, wantState in (("stop", move_flow.MOVE_GROUND_MOUNT_STATE_STOP),
                            ("arc", move_flow.MOVE_GROUND_MOUNT_STATE_ARC_LEFT_UP_ACC + tier)):
        controls.MOUNT_TURN_STATE = mode
        flow = drive(MOUNTED_HERO, 2)                       # 只按 A
        frames = riderFrames(flow)
        # 位移容差 1 mm：位置是 float32 往返编码，逐拍重发会攒出 ~27 µm 的舍入尾巴
        # （实测 2.71e-05），不是真的走了。真位移是 5.00 m 那个量级。
        check(f"原地转舵[{mode}] 按A",
              frames[-1][0] == wantState and moved(flow) < 1e-3
              and controls.groundState(flow)["heading"] == turnDegrees,
              f"state={frames[-1][0]}(期望 {wantState}) 位移={moved(flow):.6f} "
              f"heading={controls.groundState(flow)['heading']}")
    controls.MOUNT_TURN_STATE = savedTurn

    # ★★ 原地转舵补发 4 号（2026-10-06，旋钮 ``[move] mount_turn_direct``）。
    # 要守的四件事：① 关掉 = 逐字节回到加它之前（**含别的包一个不差**）；
    # ② 打开 = 只在「骑兵 + 只按 A/D」时发，每拍一条，yaw 与同拍 38 号一致；
    # ③ 骑兵走直线（有位移）不发；④ **步兵任何按键都不发**。
    print("★ 原地转舵补发 selector4「位置校正+方向」（mount_turn_direct=off/on）")
    savedDirect = controls.MOUNT_TURN_DIRECT
    for mask, key in ((2, "A"), (8, "D")):
        controls.MOUNT_TURN_DIRECT = False
        off = drive(MOUNTED_HERO, mask)
        controls.MOUNT_TURN_DIRECT = True
        on = drive(MOUNTED_HERO, mask)
        check(f"关⇒零位移基线[{key}]", directTurns(off) == [], f"{len(directTurns(off))} 条")
        turns = directTurns(on)
        frames = riderFrames(on)
        # 换键那一拍自己也会发一条 38 号（``move-ground-start-*``），所以 4 号只和
        # **最后 len(turns) 条** 38 号逐拍对齐。
        wantYaw = [frame[1] for frame in frames][-len(turns):]
        wantPos = [(frame[2], frame[3]) for frame in frames][-len(turns):]
        check(f"开⇒每拍一条 4 号[{key}]", len(turns) == STEPS
              and [t[0] for t in turns] == wantYaw          # yaw 与同拍 38 号严格一致
              and [t[2:] for t in turns] == wantPos          # 位置=同一份服务端轨迹
              and all(t[1] == 1 for t in turns),             # 寻址到人物（跳跃那族同目标）
              f"{len(turns)} 条（期望 {STEPS}）yaw {turns[0][0] if turns else '-'}"
              f"→{turns[-1][0] if turns else '-'}（38 号 {wantYaw[0] if wantYaw else '-'}"
              f"→{wantYaw[-1] if wantYaw else '-'}）target={set(t[1] for t in turns)}")
        # 最硬的一条：开关打开后，**除 4 号之外的每一帧逐字节不变**（tick 不回写那条纪律）。
        check(f"开⇒别的包逐字节不变[{key}]",
              framesWithoutDirect(off) == framesWithoutDirect(on),
              f"off {len(framesWithoutDirect(off))} 帧 vs on {len(framesWithoutDirect(on))} 帧")
        check(f"开⇒仍然零位移[{key}]", moved(on) < 1e-3, f"位移={moved(on):.6f}")
    controls.MOUNT_TURN_DIRECT = True
    check("开⇒骑兵走直线不发 4 号", directTurns(drive(MOUNTED_HERO, 1)) == [],
          f"{len(directTurns(drive(MOUNTED_HERO, 1)))} 条")
    check("开⇒骑兵 W+A 弧线不发 4 号", directTurns(drive(MOUNTED_HERO, 3)) == [],
          f"{len(directTurns(drive(MOUNTED_HERO, 3)))} 条")
    check("开⇒步兵按 A 不发 4 号", directTurns(drive(INFANTRY_HERO, 2)) == [],
          f"{len(directTurns(drive(INFANTRY_HERO, 2)))} 条")
    check("开⇒步兵按 W 不发 4 号", directTurns(drive(INFANTRY_HERO, 1)) == [],
          f"{len(directTurns(drive(INFANTRY_HERO, 1)))} 条")
    # 自证：这条检查**会红**。把补发函数改成永远 return ⇒ 上面「每拍一条」必须红。
    controls.MOUNT_TURN_DIRECT = False
    check("自证：关掉后骑兵按A 立刻没有 4 号", directTurns(drive(MOUNTED_HERO, 2)) == [],
          "关掉还有包 ⇒ 开关是假的")
    controls.MOUNT_TURN_DIRECT = True
    longRun = drive(MOUNTED_HERO, 2, steps=STEPS * 3)       # 转满 360°，把 ±180 折回跑一遍
    check("开⇒转满一圈 yaw 全在 -180..180（编码器没抛）",
          all(-180 <= t[0] <= 180 for t in directTurns(longRun))
          and len(directTurns(longRun)) == STEPS * 3,
          f"{len(directTurns(longRun))} 条 yaw "
          f"{[t[0] for t in directTurns(longRun)][::9]}")
    controls.MOUNT_TURN_DIRECT = savedDirect

    # ★★ 2026-10-06：原地转舵的**小步转**（旋钮 ``[move] mount_turn_walk``，单位=步长百分比）。
    # 背景（四条否证）：stop 档 / arc 档（cv 已经 5000）/ mount_wf=10000 / 每拍补 selector4
    # 全都「原地按 A/D 不转」，而**按空格落地那一下会跳一个方向** ⇒ 客户端本地坐骑控制器
    # 按「角速度 ∝ 前进速度」自己积分，**没有前进输入就不转**。这条是唯一没试过的方向：
    # 真给一点前进驱动。要守的五件事：
    #   ① 0 = 逐字节回到纯转舵（前面那两段钉 0 跑的就是它）；
    #   ② 只在「骑兵 + 只按 A/D」生效 —— W/S 按着、步兵、W+S 待机一律一动不动；
    #   ③ 位移沿**当前朝向**（朝向仍在 120°/s 扫 ⇒ 走的是圆弧），合位移与比例**成正比**；
    #   ④ 档位不许变（还是左右前弧 = ``mount_turn_state=arc`` 那个档，省得两个变量混一起）；
    #   ⑤ 38/35 号的 cv 与真实位移对齐（1.5 m/s 报 5000 会让客户端前冲后被位置顶回去）。
    print("★ 小步转（mount_turn_walk：0 = 纯转舵零位移，30 = 每拍只走三成步距）")
    pct = savedWalk if savedWalk else 30              # 用实机那个值跑；ini 关着就按 30 验机理
    stepDistance = c.GROUND_STEP_DISTANCE
    # 键是 (英雄, 按键) —— 步兵和骑兵的按键掩码会撞（都有 W/A），只按掩码索引会串台。
    walkCases = ((MOUNTED_HERO, 1), (MOUNTED_HERO, 2), (MOUNTED_HERO, 3), (MOUNTED_HERO, 4),
                 (MOUNTED_HERO, 5), (MOUNTED_HERO, 8),
                 (INFANTRY_HERO, 1), (INFANTRY_HERO, 2))
    controls.MOUNT_TURN_WALK_PCT = 0
    walkOff = {(hero, mask): drive(hero, mask) for hero, mask in walkCases}
    controls.MOUNT_TURN_WALK_PCT = pct
    walkOn = {(hero, mask): drive(hero, mask) for hero, mask in walkCases}
    controls.MOUNT_TURN_WALK_PCT = 100
    fullOn = drive(MOUNTED_HERO, 2)                   # 同拍数、满速的那次，用来验「成正比」
    controls.MOUNT_TURN_WALK_PCT = pct
    cavalryA, cavalryD = walkOn[(MOUNTED_HERO, 2)], walkOn[(MOUNTED_HERO, 8)]
    check("关(0)⇒A/D 零位移（这条会红：旋钮是假的就得靠它）",
          moved(walkOff[(MOUNTED_HERO, 2)]) < 1e-3
          and moved(walkOff[(MOUNTED_HERO, 8)]) < 1e-3,
          f"A={moved(walkOff[(MOUNTED_HERO, 2)]):.6f} D={moved(walkOff[(MOUNTED_HERO, 8)]):.6f}")
    for flow, key, wantState in ((cavalryA, "A", turnA), (cavalryD, "D", turnD)):
        distance = moved(flow)
        frames = riderFrames(flow)
        wantHeading = turnDegrees if key == "A" else -turnDegrees
        check(f"开({pct})⇒按{key} 走出一小步",
              0.05 < distance < STEPS * stepDistance,
              f"位移={distance:.4f} m（{pct}% 步距，{STEPS} 拍满速 = {STEPS * stepDistance:.2f} m）")
        check(f"开({pct})⇒按{key} 档位不变（还是弧档，不是新变量）",
              frames[-1][0] == wantState, f"state={frames[-1][0]} 期望 {wantState}")
        check(f"开({pct})⇒按{key} 朝向仍在扫（转舵没被位移顶掉）",
              controls.groundState(flow)["heading"] == wantHeading,
              f"heading={controls.groundState(flow)['heading']} 期望 {wantHeading}")
    check(f"开⇒位移与比例成正比（{pct}% 正好是 100% 的 {pct / 100.0:.2f} 倍）",
          abs(moved(cavalryA) - pct / 100.0 * moved(fullOn)) < 0.02,
          f"A@{pct}%={moved(cavalryA):.4f} m 期望 {pct / 100.0 * moved(fullOn):.4f} m"
          f"（A@100%={moved(fullOn):.4f} m）")
    check("开⇒A 与 D 的位移对称（只是转向相反）",
          abs(moved(cavalryA) - moved(cavalryD)) < 0.02,
          f"A={moved(cavalryA):.4f} D={moved(cavalryD):.4f}")
    fbOn, lrOn = axes(cavalryA)
    check(f"开({pct})⇒38 号带上前进输入（客户端要看的就是这个）",
          (fbOn, lrOn) == (fbOf("W"), 0),
          f"显示轴=(fb={fbOn},lr={lrOn}) 期望=({fbOf('W')}, 0)；关着是 (0, 0)")
    wantCv = int(round(controls.STEP_VELOCITY * pct / 100.0))
    top = int(max(controls.MOUNT_SPEED_TIERS[controls.MOUNT_CLASS_DEFAULT])
              * controls.VELOCITY_SCALE)
    pair38 = speeds(cavalryA)
    check(f"开({pct})⇒cv 与真实位移对齐", pair38[0] == wantCv and pair38[1] == top,
          f"(cv,mv)=({pair38[0]},{pair38[1]}) 期望 ({wantCv},{top})；"
          f"关着 cv={speeds(walkOff[(MOUNTED_HERO, 2)])[0]}")
    if controls.MOUNT_BC_ENABLED:
        mount = [b for b in cavalryA.bodies(lambda r: "mount-bc" in r)][-1]
        check("开⇒35 号 cv/mv 与 38 号同口径",
              struct.unpack_from(">hh", mount, 21) == pair38,
              f"35号={struct.unpack_from('>hh', mount, 21)} vs 38号={pair38}")
        check("开⇒35 号条数不变（没多包也没少包）",
              mountBcCount(cavalryA) == mountBcCount(walkOff[(MOUNTED_HERO, 2)]) == expectMountBc,
              f"开 {mountBcCount(cavalryA)} / 关 {mountBcCount(walkOff[(MOUNTED_HERO, 2)])} "
              f"期望 {expectMountBc}")
    # 最硬的一条：旋钮打开后，**不该受影响的那些路径逐字节不变**（只允许骑兵 A、D 两条变）。
    touched = sorted(mask for hero, mask in walkCases
                     if framesWithoutDirect(walkOff[(hero, mask)])
                     != framesWithoutDirect(walkOn[(hero, mask)])
                     and hero == MOUNTED_HERO)
    infantryUntouched = all(framesWithoutDirect(walkOff[(INFANTRY_HERO, mask)])
                            == framesWithoutDirect(walkOn[(INFANTRY_HERO, mask)])
                            for mask in (1, 2))
    check("开⇒骑兵只有「只按 A/D」变样", touched == [2, 8],
          f"变样的按键={touched}（只允许 2=A、8=D）")
    check("开⇒步兵逐字节不变", infantryUntouched, "步兵 W / A 两条路径")
    controls.MOUNT_TURN_WALK_PCT = savedWalk

    print("速度标度（``坐骑.psheet`` 四档，m/s × 1000）")
    top = int(max(controls.MOUNT_SPEED_TIERS[controls.MOUNT_CLASS_DEFAULT])
              * controls.VELOCITY_SCALE)
    infantry_run, cavalry_run = drive(INFANTRY_HERO, 1), drive(MOUNTED_HERO, 1)
    check("步兵 cv/mv 保持原字面值", speeds(infantry_run) == (5000, 25000),
          f"{speeds(infantry_run)}")
    check("骑兵 mv = 轻骑第 4 档", speeds(cavalry_run)[1] == top,
          f"{speeds(cavalry_run)} 期望 (5000, {top})")
    check("停下 cv/mv 归 0", speeds(drive(MOUNTED_HERO, 5)) == (0, 0),
          f"{speeds(drive(MOUNTED_HERO, 5))}")
    if controls.MOUNT_BC_ENABLED:      # 35 号 body：cv@21 mv@23
        mount = [b for b in cavalry_run.bodies(lambda r: "mount-bc" in r)][-1]
        pair = struct.unpack_from(">hh", mount, 21)
        check("骑兵 35 号 cv/mv 同档", pair == speeds(cavalry_run), f"{pair} vs {speeds(cavalry_run)}")

    # ★★ 角速度五兄弟（2026-09-22 夜新增）。这五个字段服务端此前**全发 0**，
    # 而 ``dir`` 每 50ms 硬掰 6°（=120°/s）⇒ 客户端看到「朝向在瞬移、角速度是 0」，
    # 转不起来只能横着滑 —— 实机「WS 前进后退 变左右了」的机制。
    # 这里同时钉两件事：① 五个字段的**偏移**没被改；② 关掉开关时**逐位回到全 0**。
    print("★ 35 号角速度五兄弟（bw/wf/wa/cw/waf；关掉 mount_angular 必须全 0）")
    if controls.MOUNT_BC_ENABLED:
        turnRate = int(round(controls.MOUNT_TURN_RATE_DPS))
        for mask, key, wantCw in ((1, "W", 0), (2, "A", turnRate), (8, "D", -turnRate),
                                  (3, "W+A", turnRate), (9, "W+D", -turnRate)):
            flow = drive(MOUNTED_HERO, mask)
            got = mountAngularFields(flow)
            want = {"a": 0, "bw": turnRate, "wf": controls.MOUNT_TURN_WF,
                    "wa": 0, "cw": wantCw, "waf": controls.MOUNT_TURN_WAF}
            check(f"骑兵 按{key} 的角速度五兄弟",
                  all(got[name] == value for name, value in want.items()),
                  " ".join(f"{n}={got[n]}" for n in
                           ("bw", "wf", "wa", "cw", "waf"))
                  + f"（期望 bw={turnRate} wf={controls.MOUNT_TURN_WF} wa=0 cw={wantCw} waf=0）")
        # 关掉开关 ⇒ 回到全 0（A/B 用的那条不变量，也是「不接线就逐位不变」的保证）
        savedAngular = controls.MOUNT_ANGULAR_ENABLED
        controls.MOUNT_ANGULAR_ENABLED = False
        off = mountAngularFields(drive(MOUNTED_HERO, 2))
        controls.MOUNT_ANGULAR_ENABLED = savedAngular
        check("mount_angular=off ⇒ 五兄弟全 0",
              all(off[n] == 0 for n in ("bw", "wf", "wa", "cw", "waf")),
              " ".join(f"{n}={off[n]}" for n in ("bw", "wf", "wa", "cw", "waf")))
        # 步兵路径**结构上**碰不到这五个字段：步兵一条 35 号都没有（上面已逐例断言）。
        check("步兵无 35 号 ⇒ 角速度改动不可能影响步兵",
              mountBcCount(drive(INFANTRY_HERO, 2)) == 0,
              f"步兵按 A 的 35 号条数={mountBcCount(drive(INFANTRY_HERO, 2))}")

    # ★ 位移基准开关（``[move] basis``）：两个互斥假设，这里钉住两种取值的**几何定义**，
    # 防止哪天有人用「把 delta 的 x 分量取反」去实现 heading（那是 90° 旋转，不是镜像）。
    savedBasis = move_flow.BASIS_FORWARD_ALONG_HEADING
    basisSeen = {}
    for mode, flag in (("legacy", False), ("heading", True)):
        move_flow.BASIS_FORWARD_ALONG_HEADING = flag
        seen = {}
        for theta in (0, 42, 90, 180, -90):
            projection = move_flow.project_standard_ground_step(
                w_pressed=1, a_pressed=0, s_pressed=0, d_pressed=0,
                heading_degrees=theta, step_distance=c.GROUND_STEP_DISTANCE)
            seen[theta] = round(math.degrees(math.atan2(projection.delta[1],
                                                        projection.delta[0])), 6)
        basisSeen[mode] = seen
    move_flow.BASIS_FORWARD_ALONG_HEADING = savedBasis
    print("★ 位移基准（[move] basis；legacy = 前进方位角 −θ，heading = +θ）")
    check("legacy：纯 W 的位移方位角 = −朝向",
          all(abs(basisSeen["legacy"][t] - (-t)) < 1e-4 for t in (0, 42, 90, 180, -90)),
          str(basisSeen["legacy"]))
    check("heading：纯 W 的位移方位角 = +朝向",
          all(abs(basisSeen["heading"][t] - t) < 1e-4 for t in (0, 42, 90, 180, -90)),
          str(basisSeen["heading"]))

    print("★ 下行节奏（[move] echo_cadence；periodic 每拍重发，onchange 只在状态变化时发）")
    savedCadence = controls.ECHO_CADENCE
    try:
        controls.ECHO_CADENCE = "periodic"
        periodicFlow = drive(MOUNTED_HERO, 1)
        periodicEchoes = periodicFlow.reasons().count("move-ground-periodic-position-echo")
        controls.ECHO_CADENCE = "onchange"
        onchangeFlow = drive(MOUNTED_HERO, 1)
        onchangeEchoes = onchangeFlow.reasons().count("move-ground-periodic-position-echo")
        onchangeFrames = len(onchangeFlow.bodies(lambda r: r.startswith("move-ground")))
    finally:
        controls.ECHO_CADENCE = savedCadence
    # 期望：periodic = 每次 timer 拍都发（= 旧行为，逐拍回写绝对坐标）；
    #       onchange = 按键那一帧已发过，之后状态没变 ⇒ **一次都不补发**，整局只有 1 条。
    check("periodic：直线 W 每拍重发（= 旧行为）",
          periodicEchoes == STEPS, f"重发 {periodicEchoes} 次 / 期望 {STEPS}")
    check("onchange：直线 W 状态不变 ⇒ 一次都不重发",
          onchangeEchoes == 0 and onchangeFrames == 1,
          f"重发 {onchangeEchoes} 次（期望 0），move-ground 共 {onchangeFrames} 条（期望 1）")

    # ★★ 2026-09-23 实机翻车后补的硬门：``flow.session`` 会被原生层做类型校验，
    #    写进 tuple 会报 ``unsupported state type: tuple`` 并**丢弃整个事件**
    #    —— 连这一拍已经排队的 ``flow.send`` 一起丢，四个方向键全哑。
    #    ⚠️ 扫描必须**在按键事件刚结束、定时器还没跑**的那一瞬间做：
    #       ``periodic`` 模式下每拍都会把 ``lastEcho`` 重写成字符串，
    #       跑完 20 拍再扫，那个元组早被覆盖掉了 —— 门会假绿。
    #       所以这里单独用 ``press()``（只发按键、不跑定时器），另外再补一遍
    #       跑完整局的广义扫描兜底。
    print("★ session 可序列化（原生层只吃标量 / str-key dict / list；tuple 会丢整条事件）")

    def badValues(node, path=""):
        found = []
        if isinstance(node, (tuple, set, frozenset, complex, bytes, bytearray)):
            found.append(path + " = " + type(node).__name__)
        elif isinstance(node, dict):
            for key, value in node.items():
                if not isinstance(key, str):
                    found.append(path + " 的键 " + repr(key) + " 不是 str")
                found.extend(badValues(value, path + "[" + str(key) + "]"))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                found.extend(badValues(value, path + "[" + str(index) + "]"))
        return found

    pressFlow = press(MOUNTED_HERO, 1)
    offenders = badValues(pressFlow.session)
    check("骑兵按 W 的瞬间：session 里没有原生层不认的类型",
          not offenders, "违规 " + str(offenders) if offenders else "干净")
    # ⚠️⚠️ 步兵也要扫！2026-09-23 用户实机「步兵 WASD 也不动了」——因为那个元组写在
    #    **共享的按键路径**里（当时没有 ``mounted`` 判断），步兵骑兵一起被丢事件。
    #    这条就是那次翻车的守门。
    infantryPress = press(INFANTRY_HERO, 1)
    offenders = badValues(infantryPress.session)
    check("步兵按 W 的瞬间：session 里没有原生层不认的类型",
          not offenders, "违规 " + str(offenders) if offenders else "干净")
    # 反向自证：按键事件**真的**发出了 38 号（事件没被丢）。骑兵步兵都要有。
    for label, flow in (("骑兵", pressFlow), ("步兵", infantryPress)):
        frames = [b for _, r, b in flow.sent if r.startswith("move-ground")]
        check(f"{label}按 W 必须真的发出 38 号（事件没被丢）", len(frames) == 1,
              f"38 号 {len(frames)} 条（期望 1）")
    for label, flow in (("骑兵直线 W 整局", drive(MOUNTED_HERO, 1)),
                        ("步兵直线 W 整局", drive(INFANTRY_HERO, 1)),
                        ("骑兵原地跳", jump(MOUNTED_HERO))):
        offenders = badValues(flow.session)
        check(f"{label}：session 里没有原生层不认的类型",
              not offenders, "违规 " + str(offenders) if offenders else "干净")
    # 反向自证：往 session 里塞一个 tuple，本门必须变红（否则门是死的）。
    probe = press(MOUNTED_HERO, 1)
    probe.session["__probe"] = ("tuple", 1)
    check("自证：塞一个 tuple 进 session ⇒ 本门必须能红",
          bool(badValues(probe.session)), "扫到 " + str(badValues(probe.session)))

    print("跳跃（坐骑的滞空标志必须到位）")
    infantry = jump(INFANTRY_HERO)
    cavalry = jump(MOUNTED_HERO)
    check("步兵 原地跳无坐骑报文",
          not mountBcCount(infantry) and "mount" not in " ".join(infantry.reasons()),
          f"35号={mountBcCount(infantry)}")
    for reason in ("move-in-air-state-bc-mount-air", "move-in-air-state-bc-mount-ground"):
        check(f"骑兵 {reason}", reason in cavalry.reasons(),
              f"出现 {cavalry.reasons().count(reason)} 次")
    if controls.MOUNT_BC_ENABLED:
        z = [struct.unpack_from(">f", b, 37)[0]
             for b in cavalry.bodies(lambda r: "mount-bc" in r)]
        check("骑兵 原地跳 35 号跟抛物线", bool(z) and max(z) > c.POSITION[2],
              f"35号={len(z)} z {c.POSITION[2]:.3f}→{max(z) if z else 0:.3f}")

    # --- C 键上/下马（[move] mount_unride）------------------------------------------
    # 输入用**实机那 15 条报文的原文**（会话 13976-239667435，逐字节相同），
    # 不是自己拼一个「看着像」的 body —— 拼出来的 body 只能证明我自己的编码自洽。
    print("C 键上/下马（[move] mount_unride；证据：会话 13976-239667435 的 cmd=4 sel=13）")
    UNRIDE_BODY = bytes.fromhex("000d001af55400b0480b4f84ebd0")
    RIDE_BODY = struct.pack(">H", controls.RIDE_MOUNT_SELECTOR) + b"\x00" * 8
    # 35 号 body 偏移（编码器格式 ``>HIHBhhhhhhhhffff``）：
    #   sel@0(H) tick@2(I) inst@6(H) state@8(B) a@9 bw@11 wf@13 wa@15 cw@17 waf@19
    #   cv@21 mv@23 dir@25(f) pos@29/33/37(f)
    MOUNT_STATE_OFFSET, MOUNT_POS_OFFSET = 8, 29
    # 步战档合法域 1..15，骑乘档从 16 起（``MOVE_GROUND_MOUNT_STATE_LINE_UP_ACC``=16）。
    # 用「< 16」而不是「等于 2」——后者只测一个档，前者能抓住任何一个骑乘档漏出来。
    GROUND_STATE_CEILING = move_flow.MOVE_GROUND_MOUNT_STATE_LINE_UP_ACC

    def stepAhead(flow, steps):
        for _ in range(steps):
            flow.now += GROUND_STEP_MS
            controls.timer(flow, "ground-step")

    def walkAndUnride():
        """骑马按住 W 走 2 拍 → 按 C → 再走一整段。返回 ``(flow, 停机位, 下马前35号数)``。"""
        flow = drive(MOUNTED_HERO, 1, 2)
        before = mountBcCount(flow)
        park = [float(v) for v in controls.groundState(flow)["position"]]
        controls.mountCommand(flow, 4, controls.UNRIDE_MOUNT_SELECTOR, UNRIDE_BODY)
        stepAhead(flow, STEPS)
        return flow, park, before

    savedSwitch = controls.MOUNT_UNRIDE
    try:
        dismountFlow, park, bcBefore = walkAndUnride()
        # ① 状态真的翻了
        check("按 C 后 mounted() 必须变 False",
              controls.mounted(dismountFlow) is False,
              "mounted()=" + str(controls.mounted(dismountFlow)))
        check("按 C 后 session[\"dismounted\"] 必须是 True",
              dismountFlow.session.get("dismounted") is True,
              "dismounted=" + repr(dismountFlow.session.get("dismounted")))
        check("按 C 后 mountPark 必须是 list[float]（原生层只吃 list，不吃 tuple）",
              isinstance(dismountFlow.session.get("mountPark"), list)
              and all(isinstance(v, float) for v in dismountFlow.session["mountPark"]),
              repr(dismountFlow.session.get("mountPark")))
        # 实机 15 条 sel=13 是客户端未获确认时的重复通知；不能让它重置停机位。
        parkBeforeDuplicate = list(dismountFlow.session["mountPark"])
        sentBeforeDuplicate = len(dismountFlow.sent)
        handledDuplicate = controls.mountCommand(
            dismountFlow, 4, controls.UNRIDE_MOUNT_SELECTOR, UNRIDE_BODY)
        check("重复 sel=13 必须幂等消费（不重置停机位/不追加下行）",
              handledDuplicate is True
              and dismountFlow.session["mountPark"] == parkBeforeDuplicate
              and len(dismountFlow.sent) == sentBeforeDuplicate
              and any("mount-unride-duplicate-ignored" in log
                      for log in dismountFlow.result["logs"]),
              "handled=%s park=%s 新下行=%d"
              % (handledDuplicate, dismountFlow.session["mountPark"],
                 len(dismountFlow.sent) - sentBeforeDuplicate))
        # ② 38 号切回步战档（不能还留骑乘档）
        states = [frame[0] for frame in riderFrames(dismountFlow)]
        afterC = states[-STEPS:]
        check("下马后 38 号必须全是步战档（< %d）" % GROUND_STATE_CEILING,
              bool(afterC) and all(s < GROUND_STATE_CEILING for s in afterC),
              "下马后档位=" + str(sorted(set(afterC))))
        # ③ 35 号不能停（停了会踩 MOUNT_BC_ENABLED 那条实机判据），且必须钉在停机位
        frames = dismountFlow.bodies(lambda r: "mount-bc" in r)
        parked = frames[bcBefore:]
        check("下马后 35 号必须继续发（马留在场景里）",
              len(parked) > 0, "下马后 35 号 %d 条" % len(parked))
        check("下马后 35 号的 state 必须是 MOUNT_STOP(54)",
              all(b[MOUNT_STATE_OFFSET] == move_flow.MOVE_GROUND_MOUNT_STATE_STOP
                  for b in parked),
              "档位=" + str(sorted({b[MOUNT_STATE_OFFSET] for b in parked})))
        drift = [max(abs(struct.unpack_from(">f", b, MOUNT_POS_OFFSET + 4 * i)[0] - park[i])
                     for i in range(3)) for b in parked]
        check("下马后马必须钉在原地（不跟着骑手跑）",
              bool(drift) and max(drift) < 1e-3,
              "最大漂移 %.4f m" % (max(drift) if drift else -1))
        check("下马后 35 号的 cw 必须是 0（停机位不许转）",
              all(struct.unpack_from(">h", b, 17)[0] == 0 for b in parked),
              "cw=" + str(sorted({struct.unpack_from(">h", b, 17)[0] for b in parked})))
        # ④ 反向自证：骑手**确实走开了**，否则 ③ 的「没漂移」可能只是因为没人动
        rider = riderFrames(dismountFlow)[-1]
        walked = ((rider[2] - park[0]) ** 2 + (rider[3] - park[1]) ** 2) ** 0.5
        check("自证：下马后骑手确实走开了（否则 ③ 是假绿）",
              walked > 1.0, "骑手走了 %.2f m" % walked)
        # ⑤ 步兵不受影响：收到也不驱动（客户端结构上不会发这条）
        infantryFlow = fresh(INFANTRY_HERO)
        handled = controls.mountCommand(infantryFlow, 4,
                                       controls.UNRIDE_MOUNT_SELECTOR, UNRIDE_BODY)
        check("步兵收到 sel=13 ⇒ 消费掉但不驱动、不发 cmd=7",
              handled is True and not infantryFlow.session.get("dismounted")
              and not [1 for cmd, _r, _b in infantryFlow.sent if cmd == 7],
              "handled=%s dismounted=%r" % (handled, infantryFlow.session.get("dismounted")))
        # ⑥ 上马通知（sel=12）能恢复
        remount = drive(MOUNTED_HERO, 1, 2)
        controls.mountCommand(remount, 4, controls.UNRIDE_MOUNT_SELECTOR, UNRIDE_BODY)
        controls.mountCommand(remount, 4, controls.RIDE_MOUNT_SELECTOR, RIDE_BODY)
        check("上马通知（sel=12）能清掉 dismounted 并恢复 mounted()",
              remount.session.get("dismounted") is False and controls.mounted(remount) is True,
              "dismounted=%r mounted=%s"
              % (remount.session.get("dismounted"), controls.mounted(remount)))
        sentBeforeRideDuplicate = len(remount.sent)
        controls.mountCommand(remount, 4, controls.RIDE_MOUNT_SELECTOR, RIDE_BODY)
        check("重复 sel=12 必须幂等消费（不重复发 STOP/RSP）",
              len(remount.sent) == sentBeforeRideDuplicate,
              "新增下行=%d" % (len(remount.sent) - sentBeforeRideDuplicate))
        # ⑦ 自证：判据真的是那个标志（去掉它就必须变回 True）
        probe = drive(MOUNTED_HERO, 1, 2)
        probe.session["dismounted"] = True
        off = controls.mounted(probe)
        probe.session.pop("dismounted")
        check("自证：dismounted 去掉 ⇒ mounted() 必须变回 True（证明判据是它）",
              off is False and controls.mounted(probe) is True,
              "置True→%s，去掉→%s" % (off, controls.mounted(probe)))
        # ⑧ mount_unride=off ⇒ 原样：不消费（落回 app.py 的 unhandled 兜底）
        controls.MOUNT_UNRIDE = "off"
        raw = drive(MOUNTED_HERO, 1, 2)
        handled = controls.mountCommand(raw, 4, controls.UNRIDE_MOUNT_SELECTOR, UNRIDE_BODY)
        check("mount_unride=off ⇒ 不消费且状态不变（= 接线前逐字相同）",
              handled is False and not raw.session.get("dismounted"),
              "handled=%s dismounted=%r" % (handled, raw.session.get("dismounted")))
        # ⑨ mount_unride=rsp ⇒ 补一条 cmd=7 sel=3，ride_type=DOWN(2)、reason=0
        controls.MOUNT_UNRIDE = "rsp"
        rspFlow = drive(MOUNTED_HERO, 1, 2)
        controls.mountCommand(rspFlow, 4, controls.UNRIDE_MOUNT_SELECTOR, UNRIDE_BODY)
        packets = [b for cmd, _r, b in rspFlow.sent if cmd == mount_flow.MOUNT_COMMAND]
        check("mount_unride=rsp ⇒ 恰好一条 cmd=7", len(packets) == 1,
              "cmd=7 %d 条" % len(packets))
        if packets:
            got = mount_flow.decode_mount_ride_rsp(packets[0])
            check("cmd=7 的 (sel, ride_type, reason) 必须是 (3, DOWN=2, 0)",
                  got[0] == mount_flow.MOUNT_RIDE_RSP
                  and got[2] == mount_flow.E_CS_MOVE_RIDE_TYPE_DOWN and got[3] == 0,
                  "解出 " + str(got))
            check("cmd=7 的 target 必须是登录应答给客户端的 user_id=10000（2026-09-23 审计："
                  "发 ACTOR_ID=1 那局客户端输入全停，见 13976-246839642）",
                  got[1] == 10000, "target=%d（期望 10000）" % got[1])
        controls.MOUNT_UNRIDE = "state"
        # ⑨' mount_unride=bc ⇒ RSP 之外再补一条 cmd=7 sel=2 RIDE_BC
        controls.MOUNT_UNRIDE = "bc"
        bcFlow = drive(MOUNTED_HERO, 1, 2)
        controls.mountCommand(bcFlow, 4, controls.UNRIDE_MOUNT_SELECTOR, UNRIDE_BODY)
        bcPackets = [b for cmd, _r, b in bcFlow.sent if cmd == mount_flow.MOUNT_COMMAND]
        check("mount_unride=bc ⇒ 恰好两条 cmd=7（RSP+BC）", len(bcPackets) == 2,
              "cmd=7 %d 条" % len(bcPackets))
        if len(bcPackets) == 2:
            b = mount_flow.decode_mount_ride_bc(bcPackets[1])
            check("RIDE_BC 的 (sel, ride_type) 必须是 (2, DOWN=2)",
                  b[0] == mount_flow.MOUNT_RIDE_BC
                  and b[2] == mount_flow.E_CS_MOVE_RIDE_TYPE_DOWN,
                  "解出 " + str(b[:3]))
            check("RIDE_BC 的 target 必须=10000（与 RSP 同层）",
                  b[1] == 10000, "target=%d" % b[1])
            check("RIDE_BC 的 map_pos 必须是 2-float(x,y) 打包且=下马停机位",
                  abs(struct.unpack(">ff", struct.pack(">Q", b[4]))[0]
                      - bcFlow.session["mountPark"][0]) < 0.01,
                  "map_pos 解出 %r，停机位 x=%r" % (
                      struct.unpack(">ff", struct.pack(">Q", b[4])),
                      bcFlow.session["mountPark"][0]))
        controls.MOUNT_UNRIDE = "state"
        # ⑨'' mount_unride=free ⇒ RSP+BC+FREE_BC 三条 cmd=7
        controls.MOUNT_UNRIDE = "free"
        freeFlow = drive(MOUNTED_HERO, 1, 2)
        controls.mountCommand(freeFlow, 4, controls.UNRIDE_MOUNT_SELECTOR, UNRIDE_BODY)
        freePackets = [b for cmd, _r, b in freeFlow.sent if cmd == mount_flow.MOUNT_COMMAND]
        check("mount_unride=free ⇒ 恰好三条 cmd=7（RSP+BC+FREE）",
              len(freePackets) == 3, "cmd=7 %d 条" % len(freePackets))
        if len(freePackets) == 3:
            f = mount_flow.decode_mount_free_bc(freePackets[2])
            check("FREE_BC 的 (sel, mount_rid) 必须是 (4, 21=视野里的马)",
                  f[0] == mount_flow.MOUNT_FREE_BC and f[1] == 21,
                  "解出 (%d, %d)" % (f[0], f[1]))
        controls.MOUNT_UNRIDE = "state"
        # ⑩ 下马后的整个 session 必须仍可被原生层序列化
        offenders = badValues(dismountFlow.session)
        check("下马后整局：session 里没有原生层不认的类型",
              not offenders, "违规 " + str(offenders) if offenders else "干净")
    finally:
        controls.MOUNT_UNRIDE = savedSwitch

    print("FAIL " + str(len(failures)) if failures else "ALL OK")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
