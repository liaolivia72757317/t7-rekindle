# -*- coding: utf-8 -*-
"""验证客户端权威输入同步（第三十三轮：跳跃闸门 + 下蹲回声）。

跑法（与 ``verify_climb_echo.py`` 同款）：

    cd <Business> && PYTHONIOENCODING=utf-8 <python> scripts/verify_input_sync.py

背景（对比 git e29de12 老版，用户口供「新版可以一直跳」「Ctrl 下蹲没了」）：
  * 老版服务端权威有完整跳跃状态机（``jumpActive`` 闸门防连跳 + 起跳/滞空/
    着陆三段 + IN_AIR 下行）；v0.2.0 客户端权威迁移时**没移植**；
  * ``localReport`` 一直在记 ``keys[4]=下蹲 / keys[5]=跳跃``，但记账后没动作。

修复（本脚本验证）：
  * 跳跃按下沿 → ``notifyInAir(1)`` + 排 ``jump-air`` 定时器（700ms 后解除）；
    空中期间再按被闸（= 老版 ``jumpActive`` 等价物）；定时器到 → ``notifyInAir(0)``；
  * 下蹲沿 → sel=39 ``BC_WITH_SPECIAL_ANIMATION``（squat=1/anim=1 蹲下，
    squat=0/anim=2 起身）。
"""
import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import controls  # noqa: E402
from scripts.codec import move_flow  # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(ok)
    print(("  OK   " if ok else "  FAIL ") + name + ("  " + detail if detail else ""))


class Flow:
    """最小桩：session/result/now/timer 全套。"""

    now = 12345

    def __init__(self):
        self.session = {"scene": "tszz", "battleEntered": True,
                        "groundEnabled": True}
        # 客户端权威（真实会话里宿主注入 runtimeMovement=true）。本脚本测的
        # 就是客户端权威路径，桩必须带上这个开关，否则 moveWireState 会走
        # 服务端分支（runFallback=True），测不到本轮要验的东西。
        self.state = {"runtimeMovement": True}
        self.result = {"logs": []}
        self.frames = []
        self.timers = []

    def send(self, cmd, body, tag=""):
        self.frames.append((cmd, bytes(body), str(tag)))

    def later(self, name, ms):
        self.timers.append((str(name), int(ms)))

    def cancel(self, *a):
        pass


def report(flow, crouched, jump, mask=0):
    """模拟一条 sel=52 客户端上报（24B：sel + category=WASD + keys×6 + 位置）。"""
    keys = bytes([bool(mask & 1), bool(mask & 2), bool(mask & 4), bool(mask & 8),
                  int(crouched), int(jump)])
    body = (struct.pack(">H", 52)
            + struct.pack(">i", controls.MOVE_KEY_CATEG_WASD)
            + keys
            + struct.pack(">fff", 500.0, 550.0, 44.4))
    controls.localReport(flow, 52, body)


def tags_of(flow):
    return [t for _c, _b, t in flow.frames]


# ---- 1. 跳跃闸门 ------------------------------------------------------------
flow = Flow()
report(flow, False, True)          # 按下空格 → 应进空中
tags = tags_of(flow)
check("跳跃沿 → IN_AIR(1)", any("in-air-state-bc-air" in t for t in tags), str(tags))
check("跳跃沿 → 排 jump-air 定时器 700ms",
      ("jump-air", 700) in flow.timers, str(flow.timers))
check("日志 jump-gate air=1", any("jump-gate air=1" in l for l in flow.result["logs"]))

n_air = len(flow.frames)
report(flow, False, True)          # 空中再按 → 必须被闸（无新帧）
check("空中再按被闸（无新帧）", len(flow.frames) == n_air,
      "n=%d" % len(flow.frames))

# ⭐ 第三十八轮：客户端权威下老状态机不跑，sel=55 跳跃广播必须由这里补
check("跳跃沿 → sel=55 跳跃动画广播",
      any("move-bc-actor-with-jump-air" in t for t in tags), str(tags))
check("跳跃沿 → 附带 sel=39 特殊动画",
      any("move-bc-special-animation-jump-air" in t for t in tags), str(tags))

controls.timer(flow, "jump-air")   # 定时器到 → 解除
tags = tags_of(flow)
check("定时器到 → IN_AIR(0)", any("in-air-state-bc-ground" in t for t in tags), str(tags[-2:]))
check("日志 jump-gate air=0", any("jump-gate air=0" in l for l in flow.result["logs"]))
check("落地 → sel=55 着陆动画(5) + 排后续两拍",
      any("move-bc-actor-with-jump-ground" in t for t in tags)
      and ("jump-air-end", 200) in flow.timers, str(flow.timers))
controls.timer(flow, "jump-air-end")
check("第二拍 → 结束着陆(6) + 排 NONE 拍",
      ("jump-air-none", 200) in flow.timers, str(flow.timers))
n_before = len(flow.frames)
controls.timer(flow, "jump-air-none")
check("第三拍 → NONE 收尾（不卡落地姿势）",
      len(flow.frames) > n_before, "n=%d" % len(flow.frames))

n = len(flow.frames)
report(flow, False, False)         # 松开空格
report(flow, False, True)          # 再次按下 → 允许再次起跳
check("落地后可再跳（重新 IN_AIR(1) + 定时器）",
      len(flow.frames) > n and ("jump-air", 700) in flow.timers[1:],
      "n=%d" % len(flow.frames))

# ---- 2. 下蹲回声 ------------------------------------------------------------
flow2 = Flow()
report(flow2, False, False)        # 基线（首条只记边沿基准，不广播）
n2 = len(flow2.frames)
report(flow2, True, False)         # Ctrl 按下 → 蹲下广播
tags2 = tags_of(flow2)
check("下蹲沿 → sel=39 蹲下广播",
      any("special-animation-crouch-on" in t for t in tags2), str(tags2))
body = next(b for c, b, t in flow2.frames if "crouch-on" in t)
check("sel=39 帧长 48", len(body) == 48, "len=%d" % len(body))
n2 = len(flow2.frames)
report(flow2, True, False)         # 按住不放 → 无重复
check("蹲住无重复帧", len(flow2.frames) == n2)
report(flow2, False, False)        # 松开 → 起身广播
tags2 = tags_of(flow2)
check("起身沿 → sel=39 起身广播",
      any("special-animation-crouch-off" in t for t in tags2), str(tags2[-1:]))

# ---- 3. 第三十五轮：CTRL(cat=2)/SPACE(cat=3) 独立包也要触发同步 --------------
# 会话 -9 实证：CTRL 走 cat=2（keys[4] 蹲位）、SPACE 走 cat=3 —— 客户端权威下
# message() 把 sel=52 全部路由进 localReport，旧代码把它们原样退回 ⇒ 0 触发。
def report_categ(flow, categ, crouched, jump, mask=0):
    keys = bytes([bool(mask & 1), bool(mask & 2), bool(mask & 4), bool(mask & 8),
                  int(crouched), int(jump)])
    body = (struct.pack(">H", 52)
            + struct.pack(">i", categ)
            + keys
            + struct.pack(">fff", 500.0, 550.0, 44.4))
    controls.localReport(flow, 52, body)


flow4 = Flow()
report_categ(flow4, controls.MOVE_KEY_CATEG_CTRL, True, False)   # CTRL 包：蹲下
tags4 = tags_of(flow4)
check("cat=2 CTRL 包 → sel=39 蹲下广播",
      any("special-animation-crouch-on" in t for t in tags4), str(tags4))
check("cat=2 不写位置账本", flow4.session["ground"]["position"] is None)

flow5 = Flow()
report_categ(flow5, controls.MOVE_KEY_CATEG_SPACE, False, True)  # SPACE 包：起跳
tags5 = tags_of(flow5)
check("cat=3 SPACE 包 → IN_AIR(1) + 定时器",
      any("in-air-state-bc-air" in t for t in tags5)
      and ("jump-air", 700) in flow5.timers, str(tags5))
_n5 = len(flow5.frames)
report_categ(flow5, controls.MOVE_KEY_CATEG_SPACE, False, True)  # 空中再按被闸
check("cat=3 空中再按被闸", len(flow5.frames) == _n5, "n=%d" % len(flow5.frames))

flow6 = Flow()
report_categ(flow6, 9, True, True)                               # 未知类别仍退回
check("未知 category 仍 return False（无帧）", len(flow6.frames) == 0,
      "n=%d" % len(flow6.frames))

# ---- 4. 会话状态可打包护栏（会话 -10 血泪：tuple 会丢弃整个事件）-------------
# 原生层 host_runtime._pack 只认 None/bool/str/int/float/bytes/list/dict(str键)；
# 写进 tuple 会抛 TypeError: unsupported state type: tuple 并**丢弃整个事件**
# （表现：按了没反应、连日志一起没了）。这里镜像 _pack 递归自检。
def packable(value, depth=0):
    if depth > 32:
        return False
    if value is None or type(value) in (bool, str, int, float, bytes):
        return True
    if type(value) is list:
        return all(packable(item, depth + 1) for item in value)
    if type(value) is dict:
        return all(type(k) is str and packable(v, depth + 1) for k, v in value.items())
    return False


# 自检本身放在 4.5 之后（要用到那里新建的 flow8/flow10）。

# ---- 4.5 第四十轮：走/跑不打架 + 位置外推 + 松手补 STOP ----------------------
# 会话 -12 实证（用户口供「一直在跑，停不下来了」）：
#   * ``_jumpAxes`` → ``moveWireState`` → ``groundMoveState(runFallback=True)``，
#     而客户端权威下 ``moveStartedAt`` 只由这条路径推进、**只增不减** ⇒ 第二次
#     起跳起 ``elapsed`` 恒 > 2000ms ⇒ 发出 state=11（跑）的 sel=55 帧；
#   * 客户端权威下**平时一条 MOVE_BC 都不发**、松手也没有 STOP ⇒ 那条「跑」的
#     驱动状态永远没人清（sel=55/39 是持久驱动状态报文）。
def report_at(flow, mask, pos, jump=False, crouched=False):
    """带自定义位置的 sel=52 上报（cat=WASD）。"""
    keys = bytes([bool(mask & 1), bool(mask & 2), bool(mask & 4), bool(mask & 8),
                  int(crouched), int(jump)])
    body = (struct.pack(">H", 52)
            + struct.pack(">i", controls.MOVE_KEY_CATEG_WASD)
            + keys + struct.pack(">fff", pos[0], pos[1], pos[2]))
    controls.localReport(flow, 52, body)


def jump55(flow, which="ground"):
    """取最后一条 sel=55 帧并解码。"""
    body = [b for _c, b, t in flow.frames if "actor-with-jump-" + which in t][-1]
    return move_flow.decode_move_bc_actor_with_jump(body)


# 4.5a 走/跑：掩码「陈旧」（moveStartedAt 在 10s 前）也不许进跑。
flow7 = Flow()
flow7.now = 100000
report(flow7, False, False, mask=1)                     # W 按下
controls.groundState(flow7)["moveStartedAt"] = 90000    # 人为做成「已持续前向 10s」
report(flow7, False, True, mask=1)                      # 按空格
_d7 = jump55(flow7, "air")
check("客户端权威：moveStartedAt 陈旧也不进「跑」（不再顶成 state=10/11/12）",
      _d7.state == controls.GROUND_WALK_STATES[(-1, 0)],
      "state=%d expect=%d" % (_d7.state, controls.GROUND_WALK_STATES[(-1, 0)]))

# 4.5b 位置外推：广播发生在两次上报**之间**时，位置要按上报速度外推。
flow8 = Flow()
flow8.now = 1000
report_at(flow8, 1, (500.0, 550.0, 44.4))
flow8.now = 1100
report_at(flow8, 1, (500.5, 550.0, 44.4))               # 0.5m / 0.1s = 5 m/s
flow8.now = 1200
report_at(flow8, 1, (501.0, 550.0, 44.4), jump=True)    # 起跳（dt=0 ⇒ 不外推）
flow8.now = 1400
report_at(flow8, 1, (502.0, 550.0, 44.4))               # 空中又报一帧
flow8.now = 1600
controls.timer(flow8, "jump-air")                       # 落地拍：距上报 200ms
_d8 = jump55(flow8, "ground")
check("位置外推：落地拍用外推位置（502.0 + 5m/s×0.2s = 503.0）",
      abs(_d8.position[0] - 503.0) < 0.01,
      "x=%.3f" % _d8.position[0])
check("外推不改账本（账本仍是客户端报的 502.0）",
      abs(flow8.session["ground"]["position"][0] - 502.0) < 1e-6,
      "ledger=%.3f" % flow8.session["ground"]["position"][0])

# 4.5c 上报过期：掩码按静止处理 + 不外推（客户端停发 = 已经站住）。
flow9 = Flow()
flow9.now = 1000
report(flow9, False, False, mask=1)
flow9.now = 1100
report(flow9, False, False, mask=1)
flow9.now = 1200
report(flow9, False, True, mask=1)                      # 起跳后客户端不再发包
flow9.now = 3000
controls.timer(flow9, "jump-air")
_d9 = jump55(flow9, "ground")
check("上报过期（1.8s 无包）⇒ 跳跃广播按静止发（不凭陈旧掩码说「在走」）",
      _d9.state == move_flow.MOVE_GROUND_STATE_STOP, "state=%d" % _d9.state)
check("上报过期 ⇒ 位置不外推（原样账本）",
      abs(_d9.position[0] - 500.0) < 1e-6, "x=%.3f" % _d9.position[0])

# 4.5d 松手沿 → 补 STOP（清掉「持久驱动状态」）。
flow10 = Flow()
report(flow10, False, False, mask=1)                    # W 按下
report(flow10, False, False, mask=0)                    # 松手
_tags10 = tags_of(flow10)
check("松手沿 → 补一条 STOP 回声",
      any("client-authority-stop-mirror" in t for t in _tags10), str(_tags10))
_body10 = next(b for _c, b, t in flow10.frames if "stop-mirror" in t)
_d10 = move_flow.decode_move_bc_with_system_and_active(_body10)
check("STOP 回声的 state 是静止档",
      _d10.state == move_flow.MOVE_GROUND_STATE_STOP, "state=%d" % _d10.state)
_n10 = len(flow10.frames)
report(flow10, False, False, mask=0)                    # 站着不动持续 keys=0
report(flow10, False, False, mask=0)
check("持续 keys=0 不重复补 STOP", len(flow10.frames) == _n10,
      "n=%d" % len(flow10.frames))

# 4.5e 应急旋钮 ``cc_jump_state=stop`` ⇒ 跳跃广播永远发静止档。
flow10b = Flow()
flow10b.now = 100000
report(flow10b, False, False, mask=1)
controls.groundState(flow10b)["moveStartedAt"] = 90000
os.environ["T7_CC_JUMP_STATE"] = "stop"
try:
    report(flow10b, False, True, mask=1)
    _d10b = jump55(flow10b, "air")
    check("T7_CC_JUMP_STATE=stop ⇒ 跳跃广播发静止档",
          _d10b.state == move_flow.MOVE_GROUND_STATE_STOP, "state=%d" % _d10b.state)
finally:
    del os.environ["T7_CC_JUMP_STATE"]
check("旋钮默认 mirror", controls.jumpStateMirrorMode() == "mirror")

# 4.5f 服务端权威回归：账本里没有上报痕迹 ⇒ 外推一个字节都不生效。
flow11 = Flow()
flow11.session["ground"] = {"mask": 1, "heading": 0,
                            "position": [500.0, 550.0, 44.4], "tick": 0}
check("无上报账本 ⇒ clientAuthorityPosition 原样返回（服务端权威逐位不变）",
      controls.clientAuthorityPosition(flow11, controls.groundState(flow11))
      == [500.0, 550.0, 44.4])
flow12 = Flow()
check("账本无位置 ⇒ 回出生点（与改动前同口径）",
      list(controls.clientAuthorityPosition(flow12, controls.groundState(flow12)))
      == list(controls.wire.POSITION))

# ---- 4.7 第四十一轮：客户端权威「移动状态镜像」（走/跑表现） ------------------
# 实机定案：客户端的**走/跑表现**跟着服务端 MOVE_BC 的 ``state`` 走。老版服务端
# 权威每 50ms 重发一次 ⇒「按 W 一会就进跑 / SHIFT 就显示了」；迁到客户端权威后
# 服务端**平时一条都不发** ⇒ 用户口供「shift 冲刺没有出来 / ~ 空手跑也没出来」。
# 本轮按「按键变化镜像 + 2s 进跑定时器」把这条补回来（回退：cc_move_mirror=off）。
flow13 = Flow()
flow13.now = 50000
report(flow13, False, False, mask=1)                    # W 按下（起步沿）
_tags13 = tags_of(flow13)
check("W 按下 → 镜像一条走档 MOVE_BC",
      any("client-authority-move-mirror" in t for t in _tags13), str(_tags13))
_b13 = next(b for _c, b, t in flow13.frames if "move-mirror" in t)
_d13 = move_flow.decode_move_bc_with_system_and_active(_b13)
check("起步镜像 = 走档", _d13.state == controls.GROUND_WALK_STATES[(-1, 0)],
      "state=%d expect=%d" % (_d13.state, controls.GROUND_WALK_STATES[(-1, 0)]))
check("起步沿 → 排 cc-run-delay 定时器（2s 后进跑）",
      ("cc-run-delay", controls.GROUND_RUN_DELAY_MS) in flow13.timers,
      str(flow13.timers))
check("起步镜像不改账本（仍是客户端报的 500.0）",
      abs(flow13.session["ground"]["position"][0] - 500.0) < 1e-6,
      "ledger=%.3f" % flow13.session["ground"]["position"][0])

# 按住 W 不放、掩码不变 ⇒ 不发重复帧（老版是 50ms 周期帧，这里是「变化才发」）。
_n13 = len(flow13.frames)
report(flow13, False, False, mask=1)
check("按住不放不重复镜像", len(flow13.frames) == _n13, "n=%d" % len(flow13.frames))

# cc-run-delay 到点（2s 后）→ 补一条「跑」档（= 老版 50ms 周期帧那条）。
flow13.now = 50000 + controls.GROUND_RUN_DELAY_MS
controls.timer(flow13, "cc-run-delay")
_tags13b = tags_of(flow13)
check("2s 到点 → 补「跑」档镜像",
      any("client-authority-run-delay-mirror" in t for t in _tags13b), str(_tags13b))
_b13b = next(b for _c, b, t in flow13.frames if "run-delay-mirror" in t)
_d13b = move_flow.decode_move_bc_with_system_and_active(_b13b)
check("进跑镜像 = 跑档(state=10)",
      _d13b.state == controls.GROUND_RUN_STATES[(-1, 0)],
      "state=%d expect=%d" % (_d13b.state, controls.GROUND_RUN_STATES[(-1, 0)]))

# 松手 → STOP（清掉「持久驱动状态」）。
report(flow13, False, False, mask=0)
check("松手 → 补 STOP 回声（清掉跑档）",
      any("client-authority-stop-mirror" in t for t in tags_of(flow13)),
      str(tags_of(flow13)[-3:]))

# 4.7b 起步后掩码不变、但 ``moveStartedAt`` 被「中间那次跳跃广播」重锚 ⇒ 定时器
#      到点也不许漏掉进跑：按剩余时间补排一拍。
flow13b = Flow()
flow13b.now = 70000
report(flow13b, False, False, mask=1)                   # W 按下 → 排 2s 定时器
controls.groundState(flow13b)["moveStartedAt"] = 71000   # 模拟被跳跃广播重锚（晚 1s）
flow13b.now = 72000                                     # 定时器到点，但只过了 1s
_n13c = len(flow13b.frames)
controls.timer(flow13b, "cc-run-delay")
check("未满 2s 不误发跑档，改按剩余时间补排",
      len(flow13b.frames) == _n13c
      and ("cc-run-delay", controls.GROUND_RUN_DELAY_MS - 1000) in flow13b.timers,
      str(flow13b.timers[-2:]))

# 4.7c 旋钮 cc_move_mirror=off ⇒ 不发带方向的走/跑镜像帧，但**松手 STOP 必须还在**
#      （第四十三轮解绑：STOP 是第四十轮「一直在跑停不下来」的修复，FB/LR=0、
#       无方向冲突，不该被这个 A/B 旋钮牵连 —— 否则 A/B 一次动了两个变量）。
os.environ["T7_CC_MOVE_MIRROR"] = "0"
try:
    flow14 = Flow()
    flow14.now = 80000
    report(flow14, False, False, mask=1)                # W 按下：不许发任何帧
    check("cc_move_mirror=off ⇒ 起步不发带方向的镜像帧",
          not any("client-authority-move-mirror" in t for t in tags_of(flow14)),
          str(tags_of(flow14)))
    flow14.now = 80000 + controls.GROUND_RUN_DELAY_MS
    controls.timer(flow14, "cc-run-delay")              # 到点也不许发跑档
    check("cc_move_mirror=off ⇒ 到点也不发跑档镜像",
          not any("client-authority-run-delay-mirror" in t for t in tags_of(flow14)),
          str(tags_of(flow14)))
    report(flow14, False, False, mask=0)                # 松手：STOP 必须还在
    _t14 = tags_of(flow14)
    check("cc_move_mirror=off ⇒ 松手 STOP **仍然**补发（解绑生效）",
          any("client-authority-stop-mirror" in t for t in _t14), str(_t14))
    _b14 = next(b for _c, b, t in flow14.frames if "stop-mirror" in t)
    _d14 = move_flow.decode_move_bc_with_system_and_active(_b14)
    check("关镜像时的 STOP = 静止档且 FB/LR 全 0（无方向冲突）",
          _d14.state == controls.stopState(flow14)
          and _d14.forward_back == 0 and _d14.left_right == 0,
          "state=%d fb=%d lr=%d" % (_d14.state, _d14.forward_back, _d14.left_right))
finally:
    del os.environ["T7_CC_MOVE_MIRROR"]
check("镜像开关默认开", controls.ccMoveMirrorEnabled() is True)

# ---- 4.8 第四十二轮：**只有跳跃广播**用客户端显示约定（55 号 W ⇒ +1000）-------
# 一手证据（prior_art《2026年8月9日四方向反向与移动过快三倍修正》原文）：
#   「当前 W 事件的 wire 表现为 fb=-1000，而历史成功抓包 W 为 fb=+1000；
#     **这解释 W 显示后退步态**」「wire 表现恢复客户端约定：fb=W-S，W 为 +1000」。
# 用户口供「按完空格，还是会后退」= 按着 W 起跳时 55 号带 fb=-1000（世界轴原值）。
# ⚠️⚠️ **返工教训（2026-10-07 同一天）**：一开始把这条取负加在了 ``displayAxes``
#   上，结果**38 号也一起取负** ⇒ 用户实测「WASD 都是反方向」。原因是两者语义不同：
#     * 38 号 ``MOVE_BC_WITH_SYSTEM_AND_ACTIVE`` = prior_art 明说的「**持续驱动命令**」
#       ⇒ 必须与键盘同轴 = **世界轴原值**；
#     * 55 号 ``BC_ACTOR_WITH_JUMP`` = **动画广播** ⇒ 才用客户端**显示**约定。
#   所以本段的判据是：**镜像帧(38) 必须是 −1000，起跳帧(55) 必须是 +1000**。
flow15 = Flow()
flow15.now = 90000
report(flow15, False, False, mask=1)                     # W 按下
report(flow15, False, True, mask=1)                      # 按空格起跳
_d15 = jump55(flow15, "air")
check("起跳帧(55) FB = +1000（显示约定，W 前进）",
      _d15.forward_back == 1000, "fb=%d" % _d15.forward_back)
check("起跳帧(55) LR = 0（纯前进）",
      _d15.left_right == 0, "lr=%d" % _d15.left_right)
_b15 = next(b for _c, b, t in flow15.frames if "move-mirror" in t)
_d15b = move_flow.decode_move_bc_with_system_and_active(_b15)
check("镜像帧(38) FB = −1000（驱动约定，必须与键盘同轴，**不许取负**）",
      _d15b.forward_back == -1000, "fb=%d" % _d15b.forward_back)

# 回退旋钮：跳跃广播回到世界轴原值（W ⇒ −1000）。
os.environ["T7_CC_FOOT_AXIS"] = "world"
try:
    check("T7_CC_FOOT_AXIS=world ⇒ 关", controls.footAxisMode() == "world")
    flow16 = Flow()
    flow16.now = 95000
    report(flow16, False, False, mask=1)
    report(flow16, False, True, mask=1)
    _d16 = jump55(flow16, "air")
    check("T7_CC_FOOT_AXIS=world ⇒ 起跳帧回到世界轴 fb=-1000",
          _d16.forward_back == -1000, "fb=%d" % _d16.forward_back)
    _b16 = next(b for _c, b, t in flow16.frames if "move-mirror" in t)
    check("旋钮不影响 38 号（镜像帧恒为 −1000）",
          move_flow.decode_move_bc_with_system_and_active(_b16).forward_back == -1000)
finally:
    del os.environ["T7_CC_FOOT_AXIS"]
check("跳跃显示轴旋钮默认 client", controls.footAxisMode() == "client")

# ``displayAxes`` 本身：两侧都必须**直通**（38 号永远走世界轴）。
flow17 = Flow()
flow17.state = {"runtimeMovement": False}
flow18 = Flow()                                          # 客户端权威 + 步兵
check("displayAxes 服务端权威：直通（世界轴原值）",
      controls.displayAxes(flow17, -1, 0) == (-1, 0)
      and controls.displayAxes(flow17, 1, -1) == (1, -1))
check("displayAxes 客户端权威步兵：也必须直通（38 号是驱动，不许取负）",
      controls.displayAxes(flow18, -1, 0) == (-1, 0)
      and controls.displayAxes(flow18, 1, -1) == (1, -1),
      "%r %r" % (controls.displayAxes(flow18, -1, 0),
                 controls.displayAxes(flow18, 1, -1)))

# ---- 4.6 会话状态可打包护栏（会话 -10 血泪：tuple 会丢弃整个事件）-------------
for _name, _f in (("cat=1 流", flow), ("cat=2/3 流", flow4), ("SPACE 流", flow5),
                  ("外推流", flow8), ("松手流", flow10)):
    bad = [k for k, v in _f.session.items() if not packable(v)]
    check("%s session 全部可打包（无 tuple）" % _name, not bad, "bad=%s" % bad)
_badGround = [k for k, v in flow4.session.get("ground", {}).items() if not packable(v)]
check("ground 账本无不可打包类型", not _badGround, "bad=%s" % _badGround)

# ---- 5. 回退开关 ------------------------------------------------------------
check("开关默认开", controls.inputSyncEnabled() is True)
os.environ["T7_CC_INPUT_SYNC"] = "0"
try:
    check("T7_CC_INPUT_SYNC=0 ⇒ 关", controls.inputSyncEnabled() is False)
    flow3 = Flow()
    report(flow3, True, True)
    check("关闭后跳跃/下蹲都不发（无新帧）", len(flow3.frames) == 0,
          "n=%d" % len(flow3.frames))
finally:
    del os.environ["T7_CC_INPUT_SYNC"]

passed = sum(1 for ok in RESULTS if ok)
print("\nverify_input_sync: %d/%d OK" % (passed, len(RESULTS)))
sys.exit(0 if passed == len(RESULTS) else 1)
