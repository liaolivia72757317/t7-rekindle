"""地图物件（MO）交互状态机 —— 「按 C 扶起 / 拆除云梯」的那个**判断**。

为什么需要
----------
用户原话（2026-09-20）：::

    看到梯子 提示 按C 扶起梯子 ，，，一直按C 产生动画，，
    梯子是立起来的状状态 在按C 是拆除，，应该有个判断

也就是：**扶起** 和 **拆除** 是同一个按键（C），靠**物件当前状态**区分。
状态必须由服务端持有 —— 客户端只播动画，自己不管状态，所以每次按 C 都重播扶起动画。

协议（全部反解自 ``TieJiClient.exe`` / metalib ``sh_proto_cs``，**已验证**）
--------------------------------------------------------------------------
* ``SH_CS_CMD_MO = 18`` —— 「地图物件」命令码。
* ``CS_PROTO_MO_PKG``：``@0 smallint msg_id``（= 服务端日志里的 ``selector``）
  + ``@2 CS_PROTO_MO_DATA`` union。
* 用到的分支：

  ==========  ================================  ====  ==========================
  msg_id      结构                                方向  说明
  ==========  ================================  ====  ==========================
  1           CS_PROTO_MO_INTERACT              C->S  交互请求 target/cli_tick/type
  3           CS_PROTO_MO_UPDATE_STATE          S->C  **状态回流**，客户端据此播动画
  12          CS_PROTO_MO_PULL_STATE_REQ        C->S  查询所有 MO 状态
  23          CS_PROTO_MO_INTERACT_RSP          S->C  交互结果（错误码）
  ==========  ================================  ====  ==========================

* 状态值取自宏表 ``E_MO_STATE_*``（**已验证**，见下面常量区的出处注释）。

⭐⭐ 关键实测事实（写这段代码之前的取证，别推翻重来）
------------------------------------------------------
会话 ``13496-380748287``（梯子**已经可见**）里：

* ``cc-object-sent scene='tszz' bytes=4958 count=24/24 sent=24`` —— 24 个物件
  （含 云梯1 / 云梯3 / 云梯4）**确实下发成功**。
* 但整段会话 **cmd=18 一条都没有**（离线解密全部 4163 条 C2S 后统计）。
  ⇒ 客户端按 C **目前不发任何报文**。

最可能的原因：服务端下发的 CC 物件 ``state`` 一直是 **0**，而 0 正是
``E_MO_STATE_INVALID``（未定义）。客户端的前置检查里有一条
``E_MO_INTERACT_PRE_CHECK_MO_STATE = 4``（MO交互前置检查_MO状态），
状态非法 ⇒ 请求在本地就被吞掉。

因此本模块做两件事：
  1. **让物件带合法初始状态**（``ccobject.py`` 会调 ``initialState``）；
  2. **实现 cmd=18 的收发与状态机**（客户端一旦发来就能正确响应）。

⚠️ 未验证边界（老实标注，别当已闭合）
----------------------------------------
* ~~「初始状态该用 GROUND(1000) 还是 IDLE(1006)」~~ —— **2026-09-24 闭合**：
  1006 是**哨塔状态_建造完毕**，根本不是云梯态。云梯只有 1000-1004，初始态
  正确就是 **1000 MO_Ladder_OnGround**。见 ``MO_STATE_LADDER_GROUND`` 的取证表。
* ~~「架起完成态到底是 UP(1007) 还是 UP_END(1008)」~~ —— **2026-09-24 闭合**：
  两个**都是哨塔状态**（1007=破坏状态 / 1008=初始状态_蓝）。正确完成态是
  **1003 MO_Ladder_Standing**。
* ``state_data`` 语义未知，一律 0。
* 客户端收到 ``update_mo_state`` 会不会崩 —— **未实测**（但已发过上千条，未见崩）。

开关
----
``T7_MO``：``0/off`` → 完全不接 cmd=18（回到改动前，报文落到 unhandled 兜底）；
``1`` → 全开。未设置 → 用 ``MO_ENABLED``（默认 True）。

``T7_MO_NTF``：``0/off`` → 不发交互通知 ``START_NTF``(21) / ``STOP_NTF``(22)；
``1/on`` → 发（默认）。**未实测**：客户端收到这两条会不会崩，所以留了开关。

``T7_MO_SETTLE``：架起动画**多久后落地**。
``total``（默认，kv ``total_animation_time_ms``，云梯1 = 2666）/ ``anim``
（kv ``animation_time_ms`` = 2286，改动前的行为）/ ``off``（不落地）/
任意毫秒数。

``T7_MO_SETTLE_STATE``：落地成哪个状态。默认 ``1003``（``MO_Ladder_Standing`` 已立住）。
⚠️ 2026-09-24 前默认是 1008/1006，**两个都是哨塔状态**，所以怎么调都碎。

``T7_MO_RAISE_AT``：``channel-end``（**默认**）/ ``now``。状态**什么时候**切到
动画态 1002（``MO_Ladder_InAir``）。见下面「两个相位」。

``T7_MO_STOP_NTF``：``anim``（**默认**，读条读完那刻发）/ ``settle``（落地时才发）/
``now``（按下就发）/ ``off``。

### ⭐⭐ 两个相位（用户口供 2026-09-22）

原版按 C **是按住不放**，而且**读完条梯子才开始动**。所以交互分两段::

    t=0       读条开始。梯子**不动**，状态仍是 1000，服务端一个 update_state 都不发
    t=2286    读满 → STOP_NTF(22) + update_state(1002, 持续 2666) → 动画**才开始**
    t=4952    动画播完 → update_state(1003, 持续 0) → 立住

总时长 = 2286 + 2666 = **4952 ms**（不是 2666）。

松开 C 时客户端发 ``CANCEL_INTERACT``(2)，现在**真会被处理**：
* 相位 0（读条未完）→ 撤定时器、状态不变、``STOP_NTF(is_interrupt=1)``
* 相位 1（动画中）  → 交互已成立，放行，动画播完

``T7_MO_RAISE_AT=now`` 保留旧行为（按下就切状态），用来做对照。

⚠️ 后三个（SETTLE / SETTLE_STATE / STOP_NTF）是 2026-09-22 为「云梯按 C 变一地碎片」
加的**二分开关**。**2026-09-24 已定位**：碎的原因不在时序，而在**状态值本身**
（1006/1007/1008/1009 全是哨塔状态）。开关保留，但默认值已改成客户端真值。

与 CC 物件同一套纪律：**并集而不是替换** —— 关掉时既有报文与日志逐位不变。

``state_change_ms`` 一律用 ``contracts.serverNowMs()``（**不要**用 ``Flow.now``，
那是原生层单调时钟 ≈ 进程运行毫秒，客户端会算出 1970 年；出处见
``contracts.py`` 上方 505-548 行的取证）。
"""
import os

from . import ccobject
from . import siege
from . import contracts as wire
from . import tracelog
from .codec import mo_flow

MO_ENABLED = True
MO_ENV_SWITCH = "T7_MO"

# ⭐ 2026-09-21：交互通知（msg 21 / 22）要不要发。
#
# 取证：``codec/mo_flow.py`` 里 ``encode_start_interact_ntf``(21) /
# ``encode_stop_interact_ntf``(22) **编码器早写好了，但全库 grep 零调用** ——
# 也就是说原版流程里
#     INTERACT(1) → RSP(23) → START_NTF(21) → 动画 → STOP_NTF(22) → UPDATE_STATE(3)
# 的中间两步一直是断的。客户端收不到 21/22，就不知道「谁在跟哪个物件交互」，
# 动画/挂点无处附着。
#
# ⚠️ 未实测：客户端收到 msg 21/22 会不会崩。所以留开关，出问题一行回到原样。
#
# 环境变量 ``T7_MO_NTF``（只影响本进程，服务端进程的环境变量要重启才变）：
#     0 / off  → 不发（回到改动前，逐位不变）
#     1 / on   → 发（默认）
MO_NTF_ENABLED = True
MO_NTF_ENV_SWITCH = "T7_MO_NTF"

# ⭐⭐ 2026-09-22：按 C 之后「多久落地 / 落到哪个状态 / 什么时候发 STOP_NTF」
# 三个**未验证**变量，全部做成环境变量，好在实机上二分定位
# 「云梯按 C 变成一地碎片」。
#
# 背景（本次离线取证，全部有据）：
#   * 帧头 **23 字节**，剥掉后 ``CS_PROTO_MO_UPDATE_STATE`` 布局与 mo_flow 一致。
#     会话 9356-105542285 实拍（record 2313 / 2363）：
#         update_state(target=10001, state=1007, state_time_ms=**2286**)
#         update_state(target=10001, state=1008, state_time_ms=0)   ← 相隔 2285 ms
#   * TDR 字段描述（metalib 原文）：
#         state_change_ms  「状态切换时间 单位ms」      ← 绝对时刻
#         state_time_ms    「状态持续时间 单位ms」      ← 时长，不是时刻
#         event_var        「当前状态的 havok 事件与变量」
#   * ``CS_PROTO_MO_INTERACT.type`` 描述：「不指定则用配置表中的」，而
#     ``E_INTERACT_TYPE_INVALID = 0`` ⇒ 客户端发 ``type=0`` 是**合法**的
#     （=「听配置表的」）。这条怀疑**已排除**。
#   * kv 里两个时长：``animation_time_ms``=2286、``total_animation_time_ms``=2666。
#     哪个管「读条」、哪个管「动画」**未验证** —— 下面三个开关就是用来测这个的。
#
# 环境变量（都要重启服务端进程才生效）：
#   ``T7_MO_SETTLE``        total(默认,2666) / anim(2286) / off / <毫秒数>
#   ``T7_MO_SETTLE_STATE``  1008(默认 UP_END) / 1006(IDLE) / 1002(AIR) ...
#   ``T7_MO_STOP_NTF``      settle(默认) / anim(2286) / now / off
MO_SETTLE_MODE = "total"
MO_SETTLE_ENV = "T7_MO_SETTLE"

# ⭐⭐ 2026-09-23 18:3x：架起落点从 1008 二分到 **1006**（LADDER_IDLE）。
#
# 取证（会话 13976-255177267，18:25:24，event=190455）：
#   实机对樊城云梯3（rid 10010）按 C，服务端**完整走通**
#   1000 →(读条2206)→ 1007 →(动画2666)→ 1008，时间轴一秒不差；
#   但客户端画面是「梯子立起后又自己塌成一地碎片」。
#   ⇒ 服务端状态链是对的，碎在客户端对 1008 的表现上。
#
#   剩两个候选（skill 挂的未闭合清单）：
#     ② 1008(LADDER_UP_END) 的姿态/动画客户端播不出来 → 换 1006(IDLE) 二分 ← 本改动
#     ⑤ ccobject 的 havok_res_index 一直填 0（grep 0 命中，从未设置过）
#        —— 若 1006 仍碎，下一个动它。
#
#   ⚠️ 语义代价（写清楚）：nextLadderState() 把 IDLE(1006) 归在「可再次扶起」组，
#   所以落 1006 后**再按 C 会重新扶起**（1007），而不是像 1008 那样走「拆除」(1009)。
#   这是二分的临时代价；若 1006 证实不碎、回头要恢复「拆除」语义，改
#   nextLadderState 让 1006 也走 FALLING 分支。
#
#   回退：把这里改回 None 即回 UP_END（或设环境变量 T7_MO_SETTLE_STATE=1008，
#   但双击启动的 exe 吃不到环境变量，改这里最稳）。
#
# ⭐⭐⭐ 2026-09-24 终结：上一条二分是**在错的坐标系里二分**。
#   1006 与 1008 **都是哨塔状态**（见上方常量块的铁证表），所以「1008 碎、
#   1006 也碎」是必然的 —— 两次都在放 ``哨塔状态_*.btree``。
#   现改为客户端真值 **1003 MO_Ladder_Standing（已立住）**。
#   ⚠️ 该值是「哑」状态（无 ``MO_Ladder_Standing.btree``），客户端不会跑行为树；
#   云梯的架起姿态由 hkx 的 ``Opened`` 事件 + MO_INCLINE_NTF 倾角驱动，
#   所以这次验证要看的是「塔有没有消失」+「梯子倾角有没有动」，而不是看塔变形。
MO_SETTLE_STATE_DEFAULT = 1003     # 客户端真值 MO_Ladder_Standing；None = 用 MO_STATE_LADDER_UP_END
MO_SETTLE_STATE_ENV = "T7_MO_SETTLE_STATE"

MO_STOP_NTF_MODE = "anim"
MO_STOP_NTF_ENV = "T7_MO_STOP_NTF"

# ⭐⭐ 2026-09-22（用户口供）：原版按 C 是**按住不放**读完条。
#
# 这条口供把 kv 里两个数字的分工坐实了：
#   ``animation_time_ms``      = 2286 = **读条**（要按住 C 多久）
#   ``total_animation_time_ms`` = 2666 = **动画总长**（松手之后梯子还在立）
#
# ⇒ 正确时序：
#     t=0     INTERACT → RSP(23) + START_NTF(21) + update_state(1007，持续 2666)
#     t=2286  读条读完 → STOP_NTF(22, is_interrupt=0)      ← 交互结束，可以松手
#     t=2666  动画播完 → update_state(1008，持续 0)          ← 立住
#
# 所以 ``MO_STOP_NTF_MODE`` 默认从 ``settle`` 改成 ``anim``。
#
# 「按住」还带出第二个要求：**中途松手要能取消**。客户端会发
# ``CS_PROTO_MO_CANCEL_INTERACT``(2)，服务端得回退状态、发
# ``STOP_NTF(is_interrupt=1)``。见 ``_handleCancelInteract``。

# 进行中的读条。session 只能安全存 str（见下方 _stateMap 的事故注释）。
# 格式 ``"<rid>|<startMs>|<channelMs>|<fromState>"``。
MO_CHANNEL_KEY = "moChannel"

# STOP_NTF 是否已经发过（防止 settle 定时器再发一条重复的）。``"1"`` / ``""``。
MO_STOPNTF_KEY = "moStopNtfSent"

# ⭐ 状态**什么时候**切到动画态（1007）：读完条才切（默认）/ 按下就切。
MO_RAISE_AT = "channel-end"
MO_RAISE_AT_ENV = "T7_MO_RAISE_AT"

# --- E_MO_STATE_*（出处：TieJiClient.exe / sh_proto_cs 宏表，2026-09-20 反解） ---
MO_STATE_INVALID = 0            # E_MO_STATE_INVALID 未定义

# ⭐⭐⭐ 2026-09-24 修正：云梯状态值**整段错位**，已按客户端权威表重定。
#
# 取证手法（本轮新做，可复现）：
#   ① 解 data1.vfs 的 ``blk=121169``（状态名池：263 行 × 260B = u16 id + u16 0
#      + 名字(GBK, NUL 结尾) + 填充）→ ``out/cc/mo_state_names.json``
#   ② 全量扫 125678 个块收集 ``*.btree`` 资源名（378 个）→ ``out/cc/btree_names.txt``
#
# 铁证（客户端真名 ← 服务端旧名）：
#   1000 MO_Ladder_OnGround          ← GROUND          ✓ 唯一本来就对的
#   1001 MO_Ladder_Falling           ← 旧叫 GROUNDING   （语义反了：是「倒下」不是「落地」）
#   1002 MO_Ladder_InAir             ← 旧叫 AIR
#   1003 MO_Ladder_Standing          ← 旧叫 AIRING      （语义反了：是「已立住」不是「升空中」）
#   1004 MO_Ladder_InAir_Occupied    ← 旧叫 AIR_HAS_ACTOR
#   1005 哨塔状态_初始状态            ← 旧叫 GROUND_HAS_ACTOR   ✗ **是哨塔**
#   1006 哨塔状态_建造完毕            ← 旧叫 IDLE               ✗ **是哨塔**
#   1007 哨塔状态_破坏状态            ← 旧叫 UP                 ✗ **是哨塔**
#   1008 哨塔状态_初始状态_蓝          ← 旧叫 UP_END             ✗ **是哨塔**
#   1009 哨塔状态_建造完毕_蓝          ← 旧叫 DOWN               ✗ **是哨塔**
#
# 「按 C 冒塔 / 碎一地」的完整机制：
#   客户端的「状态名」就是**行为树资源名**。发 1006 → 客户端加载
#   ``哨塔状态_建造完毕.btree`` → 首条动作 ``设置实体可见性 = true``
#   + 播 ``塔楼监造完成阶段`` ⇒ **塔凭空冒出来**；发 1007 →
#   ``哨塔状态_破坏状态.btree`` → ``<Param 特效实体名称 = 会战_哨塔_破碎>``
#   ⇒ **碎一地**。两个现象逐帧对应，无剩余疑点。
#
# 为什么 1000-1004 是安全的：
#   全客户端 378 个 btree 里 **``Ladder`` 零命中** —— 不存在任何
#   ``MO_Ladder_*.btree``。所以发这几个状态**不会触发任何行为树**（哑状态）。
#
# 云梯真正的动画在 Havok 角色侧，不在 btree：
#   ``TSZZ_stair_04.hkt`` + ``005892_behavior_TSZZ_stair_04_*.hkx`` 里挂着
#   ``MO_Ladder_Setup_animation.hkt``（架起）/ ``MO_Ladder_Landing_animation.hkt``（落回），
#   由 hkx 事件 ``Open / Close / Closed / Opened / EnterPreview / LeavePreview / CropTime``
#   驱动 —— ``云梯_出生.btree`` 发的 ``动画系统事件=Opened`` +
#   ``动画系统浮点参数=CropTime`` 与之精确对齐，交叉验证成立。
MO_STATE_LADDER_GROUND = 1000          # 躺地（未架起）
MO_STATE_LADDER_FALLING = 1001         # 正在倒下（回收动画中）
MO_STATE_LADDER_INAIR = 1002           # 在空中（架起动画中）
MO_STATE_LADDER_STANDING = 1003        # 已立住
MO_STATE_LADDER_INAIR_OCCUPIED = 1004  # 空中·有人（爬梯中）

# --- 旧名保留为别名（别处仍在引用），值已全部指向客户端真值 -------------------
MO_STATE_LADDER_GROUNDING = MO_STATE_LADDER_FALLING
MO_STATE_LADDER_AIR = MO_STATE_LADDER_INAIR
MO_STATE_LADDER_AIRING = MO_STATE_LADDER_STANDING
MO_STATE_LADDER_AIR_HAS_ACTOR = MO_STATE_LADDER_INAIR_OCCUPIED
# ⚠️ 下面三个在客户端**不存在**（1005/1006/1009 是哨塔），全部退回语义最近的云梯态：
MO_STATE_LADDER_GROUND_HAS_ACTOR = MO_STATE_LADDER_GROUND    # 客户端无「地面·有人」
MO_STATE_LADDER_IDLE = MO_STATE_LADDER_GROUND                # 「待机」= 躺地
MO_STATE_LADDER_UP = MO_STATE_LADDER_INAIR                   # 「正在架起」
MO_STATE_LADDER_UP_END = MO_STATE_LADDER_STANDING            # 「已立住」
MO_STATE_LADDER_DOWN = MO_STATE_LADDER_FALLING               # 「正在拆除」= 倒下

MO_STATE_ATTACK_CITY_CAR_IDLE = 1020   # 攻城车
MO_STATE_SWITCH_CLOSED = 2000          # 门机关（关）
MO_STATE_SWITCH_CLOSING = 2001         # 门机关（关门中）⚠️ 语义**未实测**（与 2003 对称推断）
MO_STATE_SWITCH_OPEN = 2002            # 门机关（开）
MO_STATE_SWITCH_OPENING = 2003         # 门机关（开门中）—— 依据：`STATE_FAMILIES` 处的注释
                                       #   「门机关 2000 CLOSED → 2003 OPENING → 2002 OPEN」
MO_STATE_MAIN_DOOR_CLOSED = 3010       # 正门
MO_STATE_MAIN_DOOR_OPENING = 3011      # 正门开门中
MO_STATE_MAIN_DOOR_OPENED = 3012       # 正门已开
MO_STATE_MAIN_DOOR_CLOSING = 3013      # 正门关门中
MO_STATE_SIDE_DOOR_CLOSED = 3014       # 侧门 / 内门
MO_STATE_SIDE_DOOR_OPENING = 3015      # 侧门开门中
MO_STATE_SIDE_DOOR_OPENED = 3016       # 侧门已开
MO_STATE_SIDE_DOOR_CLOSING = 3017      # 侧门关门中
MO_STATE_SIDE_DOOR_REVIVE = 3018       # （宏表有值，语义未实测，暂不用）
MO_STATE_SIDE_DOOR_BAD = 3019          # （宏表有值，语义未实测，暂不用）
MO_STATE_BIG_FLAG_IDLE = 3020          # 大道旗
MO_STATE_CATAPULT_WAIT = 6000          # 投石车

# ⚠️ 下面四个是「语义旋钮」，集中放在这里方便改。
#    2026-09-24 起已指向客户端真值（旧值 1006/1007/1008/1009 全是哨塔状态）。
LADDER_DOWN_STATE = MO_STATE_LADDER_GROUND      # 梯子躺地（未架起）
LADDER_RAISING_STATE = MO_STATE_LADDER_INAIR    # 架起动画中（在空中）
LADDER_UP_STATE = MO_STATE_LADDER_STANDING      # 已立住
LADDER_FALLING_STATE = MO_STATE_LADDER_FALLING  # 拆除动画中（正在倒下）

# cc_tid -> 初始状态。tid 取自 ccobject.json 的 ``tid``（= XML 的 cc_tid）。
# ⚠️ 云梯1/3/4 的 tid 都是 1（同类型共享模板 id），所以按 tid 映射是对的。
INITIAL_STATE_BY_TID = {
    1: MO_STATE_LADDER_GROUND,          # 云梯
    2: MO_STATE_ATTACK_CITY_CAR_IDLE,   # 攻城车
    3: MO_STATE_MAIN_DOOR_CLOSED,       # 正门
    4: MO_STATE_SWITCH_CLOSED,          # 正门机关
    6: MO_STATE_BIG_FLAG_IDLE,          # 大道旗
    7: MO_STATE_SIDE_DOOR_CLOSED,       # 侧门 / 内门
    9: MO_STATE_SWITCH_CLOSED,          # 侧门机关 / 内门机关
    14: MO_STATE_CATAPULT_WAIT,         # 投石车
}

# 状态 -> 动画时长（ms）。先取物件 kv 的 animation_time_ms，取不到用这个兜底。
DEFAULT_ANIMATION_MS = 2000
LADDER_ANIMATION_MS = 2286      # = 云梯1 的 kv animation_time_ms


def moEnabled():
    """``T7_MO`` 开关。"""
    override = os.environ.get(MO_ENV_SWITCH)
    if override is not None:
        text = override.strip().lower()
        if text in ("0", "off", "false", "no", ""):
            return False
        if text in ("1", "on", "true", "yes"):
            return True
    return MO_ENABLED is True


def moNtfEnabled():
    """交互通知（msg 21/22）开关 ``T7_MO_NTF``。默认 **开**，见常量处说明。"""
    override = os.environ.get(MO_NTF_ENV_SWITCH)
    if override is not None:
        text = override.strip().lower()
        if text in ("0", "off", "false", "no", ""):
            return False
        if text in ("1", "on", "true", "yes"):
            return True
    return MO_NTF_ENABLED is True


def _actorMid():
    """交互者（玩家）的 mid。取 ``contracts.ACTOR_ID``，取不到退回 1。"""
    try:
        return int(wire.ACTOR_ID)
    except (AttributeError, TypeError, ValueError):
        return 1


def _startInteractNtf(flow, rid):
    """S->C ``CS_PROTO_MO_START_INTERACT_NTF``(21)：告诉客户端「谁开始跟它交互了」。"""
    if not moNtfEnabled():
        return
    flow.send(mo_flow.MO_COMMAND,
              mo_flow.encode_start_interact_ntf(mo_mid=int(rid),
                                                actor_mid=_actorMid()),
              "mo-start-interact-ntf")


def _stopInteractNtf(flow, rid, interrupt=0, once=False):
    """S->C ``CS_PROTO_MO_STOP_INTERACT_NTF``(22)：交互结束（含被忽略的情况）。

    ``interrupt=1`` → 被打断（玩家读条中途松手 / 取消）。
    ``once=True``   → 整次交互只发一条（避免读条定时器和落地定时器各发一次）。
    """
    if not moNtfEnabled():
        return
    if once and flow.session.get(MO_STOPNTF_KEY) == "1":
        return
    if once:
        flow.session[MO_STOPNTF_KEY] = "1"
    flow.send(mo_flow.MO_COMMAND,
              mo_flow.encode_stop_interact_ntf(mo_mid=int(rid),
                                               actor_mid=_actorMid(),
                                               is_interrupt=int(interrupt)),
              "mo-stop-interact-ntf")


# --- 读条（按住 C）状态 ------------------------------------------------------
#
# 为什么需要：原版是**按住不放**（用户 2026-09-22 口供）。读条期间玩家可以松手，
# 客户端会发 ``CANCEL_INTERACT``(2)，服务端必须能回退 —— 否则「按一下就跑」
# 也能把梯子立起来，状态机等于没上锁。

def _channelSave(flow, rid, startMs, channelMs, fromState, toState, phase):
    """记一条进行中的交互。

    ``phase``：``0`` = **读条中**（梯子还没动，状态仍是 ``from``）；
    ``1`` = **动画中**（状态已是 ``to``，正在播 1002 的动画）。

    ⚠️ 这里**不许**碰 ``MO_STOPNTF_KEY`` —— 相位 0→1 的迁移会再调一次本函数，
    把去重标记清掉 ⇒ 落地时又发一条重复的 STOP_NTF（2026-09-22 踩过两次了）。
    复位只在**新一轮交互开始时**做，见 ``_handleInteract``。
    """
    flow.session[MO_CHANNEL_KEY] = "%d|%d|%d|%d|%d|%d" % (
        int(rid), int(startMs), int(channelMs or 0), int(fromState),
        int(toState), int(phase))


def _channelLoad(flow):
    raw = flow.session.get(MO_CHANNEL_KEY)
    if not isinstance(raw, str) or "|" not in raw:
        return None
    parts = raw.split("|")
    if len(parts) != 6:
        return None
    try:
        return {"rid": int(parts[0]), "start": int(parts[1]),
                "ms": int(parts[2]), "from": int(parts[3]),
                "to": int(parts[4]), "phase": int(parts[5])}
    except (TypeError, ValueError):
        return None


def _channelClear(flow):
    """清掉读条记录**并且**复位 STOP_NTF 标记（落地之后 / 取消回退之后用）。"""
    flow.session[MO_CHANNEL_KEY] = ""
    flow.session[MO_STOPNTF_KEY] = ""


def _channelDrop(flow):
    """只清读条记录，**保留** STOP_NTF 标记。

    ⭐ 读条已读完才松手的场景必须用这个：此时 STOP_NTF 已经在 2286 发过了，
    落地（2666）时靠 ``MO_STOPNTF_KEY == "1"`` 去重。要是这里把标记清了，
    落地会再发一条 —— 客户端收到两条 STOP_NTF。
    """
    flow.session[MO_CHANNEL_KEY] = ""


def raiseAtMode():
    """``T7_MO_RAISE_AT`` → ``"channel-end"``（默认）/ ``"now"``。

    ⭐⭐ 2026-09-22 用户口供：**读完条才开始动**。
    也就是按 C 之后有**两个相位**::

        t=0        读条开始，梯子**不动**，状态仍是 1000
        t=2286     读满 → STOP_NTF(22) + update_state(1002, 持续 2666) → 动画开始
        t=4952     动画播完 → update_state(1003, 持续 0) → 立住

    总时长 = 读条 2286 + 动画 2666 = **4952 ms**，不是 2666。

    * ``channel-end``（默认）：按上面这个来。
    * ``now``：按下就切状态、动画立刻开始（**改动前**的行为，留着做对照）。
    """
    override = os.environ.get(MO_RAISE_AT_ENV)
    if override is None:
        return MO_RAISE_AT
    text = override.strip().lower()
    if text in ("now", "0", "immediate"):
        return "now"
    if text in ("channel-end", "channel", "1"):
        return "channel-end"
    return MO_RAISE_AT


def settleMode():
    """``T7_MO_SETTLE`` → ``"total"`` / ``"anim"`` / ``"off"`` / 整数毫秒。

    ``total``（默认）用 kv 的 ``total_animation_time_ms``（云梯1 = 2666）；
    ``anim`` 用 ``animation_time_ms``（= 2286，是**改动前**的行为）；
    ``off`` 表示**完全不落地**（只发 1002，把后续交给客户端自己的
    ``E_MO_EVENT_TIMEOUT_CHANGE_STATE``=1016 超时迁移）。
    """
    override = os.environ.get(MO_SETTLE_ENV)
    if override is None:
        return MO_SETTLE_MODE
    text = override.strip().lower()
    if text in ("off", "0", "no", "false", "never"):
        return "off"
    if text in ("total", "anim"):
        return text
    try:
        return max(0, int(float(text)))
    except ValueError:
        return MO_SETTLE_MODE


def settleStateValue():
    """``T7_MO_SETTLE_STATE`` → 架起动画播完后落到哪个状态（默认 1003 已立住）。"""
    override = os.environ.get(MO_SETTLE_STATE_ENV)
    if override is not None:
        try:
            return int(float(override.strip()))
        except ValueError:
            pass
    if MO_SETTLE_STATE_DEFAULT is not None:
        return int(MO_SETTLE_STATE_DEFAULT)
    return MO_STATE_LADDER_UP_END


def stopNtfMode():
    """``T7_MO_STOP_NTF`` → ``"settle"`` / ``"anim"`` / ``"now"`` / ``"off"``。

    * ``settle``（默认）：和落地同一时刻发（改动后的行为）。
    * ``anim``：在 ``animation_time_ms``（云梯1 = 2286，即「读条」结束）时发，
      落地仍在 ``total``(2666) —— 用来验证「读条时长」和「动画时长」是两个数。
    * ``now``：RSP 之后立刻发（等于说「交互瞬间就结束了」）。
    * ``off``：完全不发 STOP_NTF。
    """
    override = os.environ.get(MO_STOP_NTF_ENV)
    if override is None:
        return MO_STOP_NTF_MODE
    text = override.strip().lower()
    if text in ("off", "0", "no", "false", "never"):
        return "off"
    if text in ("settle", "anim", "now"):
        return text
    return MO_STOP_NTF_MODE


def initialState(tid):
    """按 cc_tid 取初始 MO 状态；不认识的类型返回 ``None``（调用方保持原值）。"""
    try:
        return INITIAL_STATE_BY_TID.get(int(tid))
    except (TypeError, ValueError):
        return None


# ⭐⭐ 2026-09-23：「按 C 变瞭望塔」的闸门 —— 状态必须跟物件类型自洽
#
# 取证（会话 ``13976-249702540`` / ``13976-255177267``，2026-09-23）：
#
#   * 用户以为在樊城，实际在**玉门关外**。第一层根因是 ID 撞号：
#     ``pattern_level_map.csv`` 第 73 行 **卡片 10002 = 玉门关外**(level 10062 /
#     hz_map_b)，第 85 行 **卡片 20001 = 樊城**(level 10002 / tszz)。
#     日志 ``match-start-card-detected card=10002 -> level=10062 scene=hz_map_b``。
#
#   * 「瞭望塔起来就塌」是**第二层** bug，跟第一层独立：
#     ``ccobject.ridFor()`` 的 rid 对所有场景都是 ``10000 + 序号``，
#     两张图的 rid 区间**完全重叠**；而状态存在 session 里，跨图会被沿用。
#     于是玉门关外的哨塔（tid=2000，**不在** ``INITIAL_STATE_BY_TID`` 里）
#     拿到了樊城云梯留下的 1000，被套上「1000 → 1007 架起 → 1008 立住」这条链
#     ⇒ 哨塔模型播云梯动画，表现就是「瞭望塔起来就塌」。
#
# ⇒ 判据不能只看 ``state``（它可能是上一张图留下的），必须**拿当次现读的
#   tid 去校验 state 的族**。两侧对不上就只确认收到、绝不动状态。
#
# 环境变量 ``T7_MO_TID_GUARD``（默认 on；``off`` = 回到改动前的行为）。
MO_TID_GUARD_ENV = "T7_MO_TID_GUARD"
MO_TID_GUARD_DEFAULT = True

# 状态族区间。出处 **客户端 data1.vfs 的 blk=121169 状态名池**（263 行），
# 宏表 ``hkx_decode/out/mo_state_macros_full.md``（214 条）**在云梯段是错的**，
# 已由 ``out/cc/mo_state_names.json`` 证伪。
#
# ⚠️ **数值顺序 ≠ 语义顺序**：门机关 2000 CLOSED → 2003 OPENING → 2002 OPEN；
#    云梯 1000 躺地 → 1002 架起中 → 1003 立住 → 1001 倒下。
#
# ⚠️ 云梯上界从 1009 收到 **1004**：1005-1010 是**哨塔族**（哨塔状态_*）。
#    旧区间把哨塔吃进 "ladder"，于是 ``tidGuardVerdict`` 会认为
#    「哨塔(1006) 与 云梯(1000) 同族」而放行 —— 这正是「瞭望塔起来就塌」的
#    放行通道。收窄后哨塔一律判 family-mismatch。
STATE_FAMILIES = (
    ("ladder",   1000, 1004),   # 云梯（客户端只有 5 个态）
    ("car",      1020, 1023),   # 攻城车
    ("switch",   2000, 2003),   # 门机关
    ("door",     3000, 3019),   # 正门 / 侧门 / 内门
    ("flag",     3020, 3029),   # 大道旗
    ("catapult", 6000, 6003),   # 投石车
)


def stateFamily(state):
    """状态值属于哪一族；认不出来返回 ``None``（含 ``MO_STATE_INVALID`` = 0）。"""
    try:
        value = int(state)
    except (TypeError, ValueError):
        return None
    for name, low, high in STATE_FAMILIES:
        if low <= value <= high:
            return name
    return None


def tidGuardEnabled():
    """``T7_MO_TID_GUARD`` 开关，默认 **开**。"""
    override = os.environ.get(MO_TID_GUARD_ENV)
    if override is not None:
        text = override.strip().lower()
        if text in ("0", "off", "false", "no", ""):
            return False
        if text in ("1", "on", "true", "yes"):
            return True
    return MO_TID_GUARD_DEFAULT is True


def tidGuardVerdict(tid, state):
    """``None`` = 放行；否则返回拦截原因（``unknown-tid`` / ``family-mismatch``）。

    ``unknown-tid``       —— 这张图里有我们不认得类型（哨塔 tid=2000 之类），
                             绝不能给它套任何状态链。
    ``family-mismatch``   —— session 里的状态跟当前 tid 不是一个族
                             （典型：跨图 rid 重叠留下的旧状态）。
    """
    if not tidGuardEnabled():
        return None
    expected = initialState(tid)
    if expected is None:
        return "unknown-tid"
    if stateFamily(state) != stateFamily(expected):
        return "family-mismatch"
    return None


def isLadder(state):
    """状态值是否落在云梯那一段（1000..1004）。

    ⚠️ 上界是 1004 不是 1009 —— 1005-1010 是哨塔族（见 ``STATE_FAMILIES``）。
    """
    return MO_STATE_LADDER_GROUND <= state <= MO_STATE_LADDER_INAIR_OCCUPIED


def nextLadderState(state):
    """⭐ **用户要的那个判断**：按一次 C，云梯该进哪个状态。

    返回 ``(新状态, 动画毫秒)``；``None`` 表示当前状态下按 C **不响应**
    （动画进行中 / 有人站在梯子上）。

    * 躺地（OnGround 1000）      → **扶起**：InAir(1002 动画中) → 定时落到 Standing(1003)
    * 立住（Standing 1003）      → **拆除**：Falling(1001 动画中) → 定时落到 OnGround(1000)
    * 动画中（1001 / 1002）      → 不响应，避免重复触发（就是用户说的「一直按C产生动画」）

    ⚠️ 2026-09-24：值改为客户端真值。旧值 (1000→1007→1008→1009) 里
    1007/1008/1009 是**哨塔状态**，这就是「按 C 长出瞭望塔」的直接原因。
    """
    if state in (LADDER_DOWN_STATE, MO_STATE_LADDER_IDLE):
        return LADDER_RAISING_STATE, LADDER_ANIMATION_MS
    if state == LADDER_UP_STATE:
        return LADDER_FALLING_STATE, LADDER_ANIMATION_MS
    return None


def isDoor(state):
    """状态值是否落在门段（正门 3010..3013 / 侧门·内门 3014..3017）。

    ⚠️ 门**机关**（SWITCH 2000..2003）不在这里 —— 机关和门是 ``relate_cc_name``
    联动的一对（正门↔正门机关），原版开门是否两个一起动**没有证据**，
    第一版只做单物件，联动留待实测（见 ``nextDoorState`` 注释）。
    """
    return MO_STATE_MAIN_DOOR_CLOSED <= state <= MO_STATE_SIDE_DOOR_CLOSING


def nextDoorState(state):
    """按一次 C，门该进哪个状态。返回 ``(新状态, 动画毫秒)`` / ``None`` 不响应。

    宏值出处 ``mo_state_macros_full.md``（与云梯一样，**数值顺序 ≠ 语义顺序**）：

    * 正门：3010 CLOSED →(OPENING)→ 3011 → settled → 3012 OPENED
            3012 OPENED →(CLOSING)→ 3013 → settled → 3010
    * 侧门/内门：3014 CLOSED →(OPENING)→ 3015 → settled → 3016 OPENED
              3016 OPENED →(CLOSING)→ 3017 → settled → 3014
    * 过渡态（OPENING/CLOSING）按 C **不响应**，与云梯的 UP/DOWN 同规矩。

    ⚠️ 动画时长用 ``DEFAULT_ANIMATION_MS``(2000) 兜底 —— 但调用方
    （``_handleInteract``）会先拿 kv 的 ``animation_time_ms`` 覆盖
    （正门 2200 / 侧门 2800），所以实际下发的是 kv 值。
    ⚠️ 机关联动未做：原版开门时正门机关（rid 相邻）是否同步 2000→2003 **无证据**。
    """
    if state in (MO_STATE_MAIN_DOOR_CLOSED,):
        return MO_STATE_MAIN_DOOR_OPENING, DEFAULT_ANIMATION_MS
    if state in (MO_STATE_MAIN_DOOR_OPENED,):
        return MO_STATE_MAIN_DOOR_CLOSING, DEFAULT_ANIMATION_MS
    if state in (MO_STATE_SIDE_DOOR_CLOSED,):
        return MO_STATE_SIDE_DOOR_OPENING, DEFAULT_ANIMATION_MS
    if state in (MO_STATE_SIDE_DOOR_OPENED,):
        return MO_STATE_SIDE_DOOR_CLOSING, DEFAULT_ANIMATION_MS
    return None


def isSwitch(state):
    """状态值是否落在**门机关**那一段（2000..2003）。

    ⚠️ 机关与门是**两个物件**：门本体 3010..3017，机关 2000..2003。
    客户端 kv 的 ``relate_cc_name`` 把它们**双向配对**（正门机关↔正门、
    侧门机关↔侧门、内门机关↔内门），联动由客户端自己的行为树负责。
    """
    return MO_STATE_SWITCH_CLOSED <= state <= MO_STATE_SWITCH_OPENING


MO_SWITCH_ENV = "T7_MO_SWITCH"


def switchEnabled() -> bool:
    """门机关状态机开关（**默认开** —— 这是补协议缺口，不是实验能力）。

    为什么默认开：用户原始诉求就是「按 C 开城门」，而实机上按 C 交互的
    **就是机关**（``mo-interact target=10005 state=2000``，10005 = 侧门机关
    tid=9）。关掉它等于回到「只确认收到、不改状态」。
    回退（10 秒）：``set T7_MO_SWITCH=0``。
    """
    override = os.environ.get(MO_SWITCH_ENV)
    if override is not None:
        text = override.strip().lower()
        if text in ("0", "off", "false", "no", ""):
            return False
        if text in ("1", "on", "true", "yes"):
            return True
    return True


def nextSwitchState(state):
    """按一次 C，门机关该进哪个状态。返回 ``(新状态, 动画毫秒)`` / ``None`` 不响应。

    状态走向（依据 ``STATE_FAMILIES`` 处那条注释
    「门机关 2000 CLOSED → **2003 OPENING** → 2002 OPEN」）：

    * 2000 CLOSED  →(2003 OPENING)→ 落 2002 OPEN
    * 2002 OPEN    →(2001 CLOSING)→ 落 2000 CLOSED
    * 过渡态（2003/2001）按 C **不响应**，与云梯/门的规矩一致。

    ⚠️ ``2001`` 是**与 2003 对称推断**出来的，**没有实测证据** ——
    它只在「关门」这条路上用得到；开门那条路（2000→2003→2002）的两个值都有出处。
    实机若发现关门方向不对，先怀疑这个数（回退：``T7_MO_SWITCH=0``）。
    """
    if state == MO_STATE_SWITCH_CLOSED:
        return MO_STATE_SWITCH_OPENING, DEFAULT_ANIMATION_MS
    if state == MO_STATE_SWITCH_OPEN:
        return MO_STATE_SWITCH_CLOSING, DEFAULT_ANIMATION_MS
    return None


MO_DOOR_LINK_ENV = "T7_MO_DOOR_LINK"


def switchDoorLinkEnabled() -> bool:
    """机关 ⇒ 门**联动**开关（**默认开**）。

    为什么默认开（2026-10-07 实机定案）：机关状态机接进去之后，用户实测
    「**铁链有动画，门没有开**」—— 即客户端**不会**因为机关 2000→2002 就自动开门，
    ``relate_cc_name`` 的联动必须由服务端把门的 update_state 也发一遍。
    回退（10 秒）：``set T7_MO_DOOR_LINK=0``（回到「只切机关」）。
    """
    override = os.environ.get(MO_DOOR_LINK_ENV)
    if override is not None:
        text = override.strip().lower()
        if text in ("0", "off", "false", "no", ""):
            return False
        if text in ("1", "on", "true", "yes"):
            return True
    return True


def _doorPeerRid(flow, rid):
    """机关 rid → 配对门 rid（按客户端 kv 的 ``relate_cc_name`` **双向配对**）。

    配对表（``data/scene/tszz/ccobject.json``，2026-10-07 实测）：
    正门机关↔正门 / 侧门机关↔侧门 / 内门机关↔内门。
    找不到配对（名字空 / 自指 / 场景里没有同名物件）⇒ ``None``。
    """
    try:
        from . import controls
        items = ccobject.loadScene(controls.airWallScene(flow))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    if not items:
        return None
    myName = None
    peerName = None
    for index, item in enumerate(items, start=1):
        if ccobject.ridFor(index) == int(rid):
            myName = str(item.get("name") or "")
            peerName = str((item.get("kv") or {}).get("relate_cc_name") or "")
            break
    if not myName or not peerName or peerName == myName:
        return None
    for index, item in enumerate(items, start=1):
        if str(item.get("name") or "") == peerName:
            return ccobject.ridFor(index)
    return None


def _linkDoor(flow, switchRid, switchNewState, info):
    """机关切状态 ⇒ **配对的门跟着切**（用户实测客户端不自动联动，见开关说明）。

    * 机关 → OPENING(2003) ⇒ 门 → OPENING（正门族 3011 / 侧门·内门族 3015）
    * 机关 → CLOSING(2001) ⇒ 门 → CLOSING（正门族 3013 / 侧门·内门族 3017）
    * 门的族由门**当前状态**判断（3010..3013 = 正门族，3014..3017 = 侧门·内门族）；
    * 门的动画时长取门自己 kv 的 ``animation_time_ms``（正门 2200 / 侧门 2800），
      落点交给既有的 ``mo-settle-<doorRid>`` 定时器（``settledState`` 已支持门段）。
    * 任何异常只记日志，**绝不**影响机关自己的状态链。
    """
    doorRid = _doorPeerRid(flow, switchRid)
    if doorRid is None:
        flow.result["logs"].append(
            "mo-door-link-skip switch=" + str(switchRid) + " no-peer")
        return
    doorState = getState(flow, doorRid)
    if doorState is None:
        doorState = initialFor(flow, doorRid)
    if doorState is None or not isDoor(doorState):
        flow.result["logs"].append(
            "mo-door-link-skip door=" + str(doorRid) + " state=" + str(doorState))
        return
    mainFamily = doorState <= MO_STATE_MAIN_DOOR_CLOSING
    if switchNewState == MO_STATE_SWITCH_OPENING:
        doorNew = MO_STATE_MAIN_DOOR_OPENING if mainFamily else MO_STATE_SIDE_DOOR_OPENING
    elif switchNewState == MO_STATE_SWITCH_CLOSING:
        doorNew = MO_STATE_MAIN_DOOR_CLOSING if mainFamily else MO_STATE_SIDE_DOOR_CLOSING
    else:
        return
    if doorState in (MO_STATE_MAIN_DOOR_OPENING, MO_STATE_SIDE_DOOR_OPENING,
                     MO_STATE_MAIN_DOOR_CLOSING, MO_STATE_SIDE_DOOR_CLOSING):
        flow.result["logs"].append(
            "mo-door-link-skip door=" + str(doorRid) + " busy state=" + str(doorState))
        return
    if doorNew == doorState:
        flow.result["logs"].append(
            "mo-door-link-skip door=" + str(doorRid) + " already state=" + str(doorState))
        return
    doorAnim = animationMsFor(info, doorRid) or DEFAULT_ANIMATION_MS
    _channelSave(flow, doorRid, wire.serverNowMs(), 0, doorState, doorNew, 1)
    _apply(flow, doorRid, doorNew, doorAnim, "mo-door-link")
    settleMs = _settleDelayFor(doorAnim, 0)
    if settleMs:
        _safeCancel(flow, _timerName(doorRid))
        flow.later(_timerName(doorRid), settleMs)
    flow.result["logs"].append(
        "mo-door-link switch=%d(%d) door=%d %s->%s anim=%dms"
        % (int(switchRid), int(switchNewState), int(doorRid),
           doorState, doorNew, doorAnim))


def settledState(state):
    """动画播完之后该落到哪个**稳定**状态；非过渡态原样返回。

    ⭐ 2026-09-22：架起的落点改成**可配置**（``T7_MO_SETTLE_STATE``）。
    ⭐ 2026-09-24：落点定稿 **1003 MO_Ladder_Standing**。
    此前在 1008 / 1006 之间二分过两轮，实机都是「立起后塌成一地碎片」——
    因为 1008 与 1006 **都是哨塔状态**（1008=哨塔状态_初始状态_蓝，
    1006=哨塔状态_建造完毕），两次都在放哨塔的行为树，怎么可能不碎。
    见 ``MO_STATE_LADDER_GROUND`` 处的完整取证表。

    ⭐ 2026-09-23：新增门段 —— OPENING 落 OPENED、CLOSING 落 CLOSED。
    """
    if state == LADDER_RAISING_STATE:
        return settleStateValue()
    if state == LADDER_FALLING_STATE:
        return LADDER_DOWN_STATE
    if state == MO_STATE_MAIN_DOOR_OPENING:
        return MO_STATE_MAIN_DOOR_OPENED
    if state == MO_STATE_MAIN_DOOR_CLOSING:
        return MO_STATE_MAIN_DOOR_CLOSED
    if state == MO_STATE_SIDE_DOOR_OPENING:
        return MO_STATE_SIDE_DOOR_OPENED
    if state == MO_STATE_SIDE_DOOR_CLOSING:
        return MO_STATE_SIDE_DOOR_CLOSED
    # ⭐ 2026-10-07：门机关也要落点 —— 少了这两行，机关会**卡在过渡态**，
    #    下次按 C 落到 ``nextSwitchState`` 的 ``return None``（不响应）。
    if state == MO_STATE_SWITCH_OPENING:
        return MO_STATE_SWITCH_OPEN
    if state == MO_STATE_SWITCH_CLOSING:
        return MO_STATE_SWITCH_CLOSED
    return state


# --- 会话状态 ----------------------------------------------------------------
#
# ⚠️⚠️ 2026-09-20 事故（别再犯）：**别往 session 里塞新结构**。
#
#   第一版这里写成 ``session["moState"] = {rid: state}``（rid 是 int 1001…1024）。
#   结果原生层序列化会话 state 时直接抛
#       ``unsupported state type: dict``
#   —— 发生在 camp-choice-ok（``command=0x23 selector=3``）里第一次调
#   pushStates 的那一刻（会话 13496-384267552，recordId 393，13:18:12）。
#   那条消息被**整条丢弃** ⇒ 后续进图报文全不发 ⇒ **樊城攻城模式卡死进不去**。
#
#   现存的 ``session["ground"]`` 也是 dict 且一直正常，所以问题**不是** dict 本身，
#   而是我这个 dict 的 **key 是 int**（原生序列化器按类型分派，只认 str key）。
#
# ⭐ 修法：**整个状态表序列化成一个 str** 存 ``session["moStates"]``。
#   不赌「哪种结构能过序列化」这个黑盒 —— str 一定安全（代码里到处在存 str）。
#   格式 ``"1001=1000;1002=1000"``，读写时才 parse，24 个物件开销可忽略。

MO_STATE_KEY = "moStates"


def _stateMap(flow):
    """读：``session["moStates"]`` → ``{rid:int -> state:int}``（内存态，不落 session）。"""
    raw = flow.session.get(MO_STATE_KEY)
    out = {}
    if isinstance(raw, str) and raw:
        for part in raw.split(";"):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            try:
                out[int(key)] = int(value)
            except (TypeError, ValueError):
                continue
    return out


def _saveStates(flow, table):
    """写：内存态 → 单个 str。"""
    flow.session[MO_STATE_KEY] = ";".join(
        "%d=%d" % (rid, state) for rid, state in sorted(table.items()))


def getState(flow, rid, default=None):
    """某物件的当前状态；没记录返回 ``default``。"""
    return _stateMap(flow).get(int(rid), default)


def sceneInfoByRid(flow):
    """``{rid -> {"tid": tid, "anim_ms": ms}}``。**一次读盘**，循环里别重复调。

    ``ccobject.ridFor(index)`` 用的 index 与 ``ccobject.json`` 的顺序一致
    （都从 1 起），所以能按序对上。

    ⭐ 2026-09-21：**动画时长必须从这里的原始记录取**。
    ``ccobject.byRid()`` 返回的是 ``vision_flow.CcDynamicObject``
    （``frozen slots`` dataclass），**根本没有** ``kv`` 字段 ——
    旧写法 ``obj.get("kv", {})`` 恒抛 ``AttributeError`` ⇒ 永远掉进兜底
    ``DEFAULT_ANIMATION_MS``(2000)，而云梯的真实值是 ``2286``
    （``data/scene/tszz/ccobject.json`` 里「云梯1」的 kv）。
    注释写的是「优先 kv animation_time_ms」，可对错了对象 ——
    ``kv`` 只在 ``loadScene()`` 返回的原始 dict 里。
    """
    try:
        from . import controls
        items = ccobject.loadScene(controls.airWallScene(flow))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}
    return {ccobject.ridFor(index): {"tid": item.get("tid"),
                                     "anim_ms": _animMsOfRecord(item),
                                     "channel_ms": _channelMsOfRecord(item)}
            for index, item in enumerate(items, start=1)}


def _animMsOfRecord(item):
    """``ccobject.json`` 的一条**原始记录** → 动画时长（ms）；取不到返回 ``None``。

    ⭐ 2026-09-22：改为**优先** ``total_animation_time_ms``，兜底 ``animation_time_ms``。

    取证（``data/scene/tszz/ccobject.json`` 的 kv）：
        云梯1   animation 2286  total 2666  差 380
        云梯3   animation 2206  total 2666  差 460
        云梯4   animation 2280  total 2666  差 386
        攻城车1 animation 1540  total 2000  差 460
        正门    animation 2200  total 3000  差 800
        侧门    animation 2800  total 3000  差 200

    假设：``animation_time_ms`` 是「按住 C 的读条时长」，``total_animation_time_ms``
    是「完整动画时长」。

    ⚠️ 2026-09-24 更正：上面这段曾经把「碎了一地」归因于**抢发 UP_END 打断动画**
    （旧文写得很像定论，其实只是假设）。真正的成因是**状态值本身指到了哨塔族**
    —— 1007/1008 都是哨塔状态，客户端一收到就去加载 ``哨塔状态_*.btree``，
    跟时序一点关系都没有。见 ``MO_STATE_LADDER_GROUND`` 处的铁证表。
    本函数（读 total 而不是 animation）仍然保留，因为它符合用户口供的
    「读完条梯子还在立」，与「碎」无关。
    """
    kv = item.get("kv") or {}
    try:
        v = int(float(kv.get("total_animation_time_ms") or 0))
        if v > 0:
            return v
        v = int(float(kv.get("animation_time_ms") or 0))
        if v > 0:
            return v
        return None
    except (TypeError, ValueError):
        return None


def _channelMsOfRecord(item):
    """``ccobject.json`` 一条记录 → **读条时长**（kv ``animation_time_ms``）。

    与 ``_animMsOfRecord``（= ``total_animation_time_ms``）是一对。云梯1：
    读条 2286 / 总动画 2666。哪个管交互、哪个管动画**未验证** ——
    ``T7_MO_STOP_NTF=anim`` 就是拿这个数去发 STOP_NTF 做二分。
    """
    kv = item.get("kv") or {}
    try:
        v = int(float(kv.get("animation_time_ms") or 0))
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def channelMsFor(info, rid):
    """物件读条时长（ms）；取不到返回 ``None``。"""
    try:
        return info[int(rid)]["channel_ms"]
    except (KeyError, TypeError, ValueError):
        return None


def tidByRid(flow):
    """``{rid -> cc_tid}``（``sceneInfoByRid`` 的薄封装，旧调用点用）。"""
    return {rid: info["tid"] for rid, info in sceneInfoByRid(flow).items()}


def initialFor(flow, rid, tidMap=None):
    """某物件的**初始** MO 状态（按 cc_tid 查表）。

    ⚠️ 刻意**不读** ``CcDynamicObject.state`` —— 那个字段受 ``T7_CC_STATE``
    控制（实机发现它会让云梯不可见），两条链路必须能独立开关。

    ``tidMap`` 两种形状都收：``{rid: tid}``（``tidByRid``）或
    ``{rid: {"tid":…, "anim_ms":…}}``（``sceneInfoByRid``）。
    """
    if tidMap is None:
        tidMap = tidByRid(flow)
    value = tidMap.get(int(rid))
    tid = value.get("tid") if isinstance(value, dict) else value
    state = initialState(tid)
    return state if state is not None else MO_STATE_INVALID


def setState(flow, rid, state):
    """写某个物件的状态（**会落 session**）。"""
    table = _stateMap(flow)
    table[int(rid)] = int(state)
    _saveStates(flow, table)


def currentState(flow, rid):
    """某物件的当前状态；没记录则返回 ``None``。"""
    return getState(flow, rid)


def animationMsFor(info, rid):
    """物件动画时长（ms）：kv ``animation_time_ms`` → 兜底 ``DEFAULT_ANIMATION_MS``。

    ``info`` 是 ``sceneInfoByRid(flow)`` 的结果（别再传 dataclass，见那里注释）。
    """
    try:
        return int(info[int(rid)]["anim_ms"]) or DEFAULT_ANIMATION_MS
    except (KeyError, TypeError, ValueError):
        return DEFAULT_ANIMATION_MS


# --- 收发 --------------------------------------------------------------------

def _ridSet(flow):
    """本场景 CC 物件的 rid 集合；读盘失败返回空集（不抛，同 ccobject 纪律）。"""
    try:
        from . import controls
        return set(ccobject.byRid(controls.airWallScene(flow)))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return set()


def pushStates(flow, reason):
    """把本场景所有 MO 的当前状态推一份给客户端（``update_mo_state``）。

    什么时候需要：CC 物件刚下发完之后。在此之前客户端拿到的 ``state`` 是 0
    （``E_MO_STATE_INVALID``），按 C 会被本地前置检查吞掉。
    """
    if not moEnabled():
        return 0
    try:
        from . import controls
        table = ccobject.byRid(controls.airWallScene(flow))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        flow.result["logs"].append("mo-push-failed error=" + repr(error))
        return 0
    sent = 0
    tidMap = tidByRid(flow)          # 一次读盘，别在循环里重复算
    for rid, obj in sorted(table.items()):
        state = getState(flow, rid)
        # ⭐ 2026-09-23：状态与当前 tid 不自洽就**重算**，别沿用。
        # 场景 A：跨图 rid 重叠（樊城 10001..10024 ↔ 玉门关外 10001..10021），
        #         上一张图的状态被带进新图。
        if state is None or tidGuardVerdict(tidMap.get(rid), state) is not None:
            state = initialFor(flow, rid, tidMap)
            setState(flow, rid, state)
        if state == MO_STATE_INVALID:
            continue
        flow.send(mo_flow.MO_COMMAND,
                  mo_flow.encode_update_state(target=rid, state=state,
                                              state_change_ms=wire.serverNowMs(),
                                              state_time_ms=obj.state_time_ms),
                  reason)
        sent += 1
    if sent:
        msg = "mo-push reason=" + reason + " count=" + str(sent)
        flow.result["logs"].append(msg)
        try:
            tracelog.emit("mo", controls.airWallScene(flow), msg)
        except Exception:
            pass
    return sent


def pushState(flow, rid, reason):
    """只推**一个**物件的状态（``VISION_GET_OBJECTS_REQ`` 按 mid 逐个要对象时用）。"""
    if not moEnabled():
        return False
    try:
        from . import controls
        obj = ccobject.byRid(controls.airWallScene(flow)).get(rid)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False
    if obj is None:
        return False
    tidMap = tidByRid(flow)          # 一次读盘，与 pushStates 同款纪律
    state = getState(flow, rid)
    # 同 pushStates：不自洽（跨图残留）就按当前 tid 重算，别沿用。
    if state is None or tidGuardVerdict(tidMap.get(rid), state) is not None:
        state = initialFor(flow, rid, tidMap)
        setState(flow, rid, state)
    if state == MO_STATE_INVALID:
        return False
    flow.send(mo_flow.MO_COMMAND,
              mo_flow.encode_update_state(target=rid, state=state,
                                          state_change_ms=wire.serverNowMs(),
                                          state_time_ms=obj.state_time_ms),
              reason)
    return True


def _handleInteract(flow, target, cli_tick, interact_type):
    """C->S 交互请求：跑状态机 → 回 RSP → 广播新状态。"""
    info = sceneInfoByRid(flow)      # 一次读盘：tid 和动画时长一起拿齐
    try:
        from . import controls
        obj = ccobject.byRid(controls.airWallScene(flow)).get(target)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        obj = None
    if obj is None:
        # 不认识的物件：照回一个「成功」以外的错误码比静默好，但错误码语义未实测，
        # 所以只记日志、不回包（保持与改动前一致：落到 unhandled 兜底）。
        flow.result["logs"].append("mo-interact-unknown target=" + str(target)
                                   + " cli_tick=" + str(cli_tick)
                                   + " type=" + str(interact_type))
        return False

    state = getState(flow, target)
    if state is None:
        state = initialFor(flow, target, info)
    flow.result["logs"].append("mo-interact target=" + str(target)
                               + " type=" + str(interact_type)
                               + " state=" + str(state))

    reply = mo_flow.encode_interact_rsp(target=target, cli_tick=cli_tick,
                                        result=mo_flow.SH_ERR_SUCCESS)
    flow.send(mo_flow.MO_COMMAND, reply, "mo-interact-rsp-success")

    # ⭐ 2026-09-21：补上原版流程的 START_NTF(21)。少了它，客户端不知道
    #   「谁在跟哪个物件交互」，扶梯动画无处附着。
    _startInteractNtf(flow, target)

    # ⭐⭐ 2026-09-23：类型闸门。取证见 ``STATE_FAMILIES`` 处的注释。
    # 这一步**必须**放在 ``isLadder(state)`` 之前：``state`` 可能是上一张图
    # 经重叠的 rid 留下的（哨塔拿到云梯的 1000），只看 state 会把云梯链
    # 套到哨塔头上 —— 实机就是「按 C 变瞭望塔、起来就塌」。
    entry = info.get(int(target)) if isinstance(info, dict) else None
    tid = entry.get("tid") if isinstance(entry, dict) else None
    verdict = tidGuardVerdict(tid, state)
    if verdict is not None:
        flow.result["logs"].append(
            "mo-interact-guard-blocked target=" + str(target)
            + " tid=" + str(tid) + " state=" + str(state)
            + " family=" + str(stateFamily(state)) + " why=" + verdict)
        _stopInteractNtf(flow, target)
        return True

    # ⭐⭐ 2026-09-23 夜：把**投石车 / 攻城车**交给 ``siege``。
    #    它们在这之前落到下面那句「云梯/门以外的状态机还没做：只确认收到、不改状态」
    #    —— 也就是「按 C 对投石车毫无反应」的直接原因。
    #    ⚠️ 云梯（tid=1）在 siege.onInteract 里**硬编码返回 False**，
    #       继续走下面这条已经实机验证过的云梯链，本改动不碰它。
    if siege.onInteract(flow, target, tid, state):
        return True

    if not (isLadder(state) or isDoor(state) or isSwitch(state)):
        # 云梯/门/机关以外的（投石车 / 攻城车 / 哨塔…）状态机还没做：
        # 只确认收到，不改状态。
        _stopInteractNtf(flow, target)
        return True

    # ⭐ 2026-09-23：门段（3010/3014）接进状态机 —— 用户原始诉求「开城门按 C 开门」。
    # ⭐⭐ 2026-10-07：**门机关**（2000..2003）也接进来。为什么必须：
    #    实机上用户按 C 交互的**不是门本体，而是机关** —— 会话
    #    ``51992-1437159953-1``：``mo-interact target=10005 state=2000``，
    #    而 10005 = **侧门机关**（tid=9，见 ``data/scene/tszz/ccobject.json``）。
    #    机关此前落到上面那句「只确认收到、不改状态」⇒ 门一直开不了。
    #    机关与门由客户端 kv 的 ``relate_cc_name`` **双向配对**（正门机关↔正门、
    #    侧门机关↔侧门、内门机关↔内门），**联动由客户端自己的行为树负责**
    #    ⇒ 服务端只切机关状态、不碰门（一次只动一个变量）。
    if isSwitch(state) and not switchEnabled():
        _stopInteractNtf(flow, target)
        return True

    # 分派规则：云梯走 nextLadderState，门走 nextDoorState，机关走 nextSwitchState，
    # 其余（投石车/攻城车/哨塔等）维持旧行为「只确认收到、不改状态」。
    if isSwitch(state):
        transition = nextSwitchState(state)
    elif isDoor(state):
        transition = nextDoorState(state)
    else:
        transition = nextLadderState(state)
    if transition is None:
        # ⭐ 这就是「一直按C产生动画」的根因：动画中或已占用时**不再触发**。
        flow.result["logs"].append("mo-interact-ignored target=" + str(target)
                                   + " state=" + str(state))
        # 已经发过 START_NTF，被忽略也要补一条 STOP，别让客户端卡在交互态。
        _stopInteractNtf(flow, target)
        return True

    newState, animMs = transition
    animMs = animationMsFor(info, target) or animMs
    channelMs = channelMsFor(info, target)

    # 已经有交互在跑（同一物件）→ 不再叠加，别让「狂按 C」刷出多条动画
    busy = _channelLoad(flow)
    if busy is not None and busy["rid"] == int(target):
        flow.result["logs"].append("mo-interact-busy target=" + str(target)
                                   + " phase=" + str(busy["phase"]))
        _stopInteractNtf(flow, target, once=True)
        return True

    # 新一轮交互：复位 STOP_NTF 去重标记（只在这里复位，别在 _channelSave 里）
    flow.session[MO_STOPNTF_KEY] = ""

    # ---------- 相位 0：读条（梯子不动）--------------------------------
    if raiseAtMode() == "channel-end" and channelMs:
        _channelSave(flow, target, wire.serverNowMs(), channelMs, state,
                     newState, 0)
        flow.later(_channelTimerName(target), channelMs)
        flow.result["logs"].append("mo-interact-channel target=" + str(target)
                                   + " ms=" + str(channelMs)
                                   + " from=" + str(state) + " to=" + str(newState))
        # 只有 ``now`` 会在这一刻就把 STOP_NTF 发出去（其余等读满 / 落地）
        if stopNtfMode() == "now":
            _stopInteractNtf(flow, target, once=True)
        return True

    # ---------- 旧行为（T7_MO_RAISE_AT=now）：按下就切状态 --------------
    settleMs = _settleDelayFor(animMs, channelMs)

    _channelSave(flow, target, wire.serverNowMs(), channelMs or 0, state,
                 newState, 1)
    _apply(flow, target, newState, animMs, "mo-interact")
    # ⭐⭐ 2026-10-07：机关切状态 ⇒ **配对的门跟着切**（用户实测：铁链动了门不动，
    #    客户端不自动联动）。开关 ``T7_MO_DOOR_LINK``（默认 on），包 try：
    #    联动是附加能力，坏了不许拖垮机关自己这条链。
    if isSwitch(state):
        try:
            _linkDoor(flow, target, newState, info)
        except Exception:  # noqa: BLE001
            pass
    if settleMs:
        flow.later(_timerName(target), settleMs)

    ntf = stopNtfMode()
    if ntf == "now":
        _stopInteractNtf(flow, target, once=True)
    elif ntf == "anim" and channelMs and channelMs != settleMs:
        flow.later(_stopNtfTimerName(target), channelMs)
    return True


def _apply(flow, rid, state, state_time_ms, reason, events=None):
    setState(flow, rid, state)
    # ⭐⭐ 2026-09-24：``event_var`` 从本模块诞生起**一直发空**（注释里那句
    #    「真实客户端是否期待非零 event/param 未实测」挂了三天）。现在接上：
    #    ``siege.ladderBornEvents`` 只在「该架走 ``born`` 通道 **且** 状态 =
    #    架起中/已立住」时返回 ``(E_MO_EVENT_CALL_BORN_SCRIPT,)``；**其余一律 ()**
    #    ⇒ 报文与改动前逐字节相同（仍是 ``event_num=0, param_num=0`` 两字节）。
    #    这一条事件就是「让客户端跑它实体自己挂的 ``云梯_出生.btree``」的开关，
    #    梯子怎么摆交给原生动画的 root motion —— 即用户选的「让它本来就是对的」。
    #    ⚠️ 包一层 try：器械是**附加**能力，坏了不能拖垮 mo 这条已验证的链。
    #    ⭐ 2026-10-07 第三十七轮：``events`` 参数可**显式覆盖**（投石车下车要
    #       在 6001 控制树里补一发 ``MO_EVENT_STOP_CONTROL``，见 siege 同名函数）。
    #       传 None = 走原来的推导链，逐字节不变。
    if events is None:
        try:
            events = siege.ladderBornEvents(flow, rid, state)
        except Exception:  # noqa: BLE001
            events = ()
    # ⭐⭐⭐ 2026-09-27：``crop`` 通道 —— 同一条 ``update_state`` 的 ``event_var``
    #    里带 ``param(HAVOK_PARAM_TYPE_CROPTIME, 值)``，直接改客户端的
    #    ``CropTime`` 动画参数（= 行为图里 ``cropEndAmountLocalTime``）。
    #    这是 ``born`` 实测无效之后唯一还没试过的姿态旋钮：
    #    ``born`` 走的是出生树，而出生树写的 ``CropTime`` 也是 0.38，
    #    与状态路径同一个值 ⇒ 再触发一遍等于没改。这里是我们**自己给值**。
    #    依据见 ``siege.HAVOK_PARAM_TYPE_CROPTIME`` 的注释块。
    #    同样包 try：器械是附加能力，坏了自己吞，别拖垮 mo 的已验证链。
    try:
        params = siege.ladderEventParams(flow, rid, state)
    except Exception:  # noqa: BLE001
        params = ()
    # ⭐⭐ 2026-09-24 下午：``born_late`` 通道「**事件先、状态后**」。
    #    用户实机口供：「先倒到墙上，后播放的动画，顺序反了」——
    #    假设是「客户端收到 1002 就把梯子摆到『在空中』的预设位姿，动画只是补播」。
    #    于是这条通道下把**下发的 state 改回 1000**（与客户端当前值相同）：
    #    客户端无状态变化可执行 ⇒ 只剩出生树的 ``Opened`` ⇒ 动画先跑，
    #    位姿等 ``settled`` 那条再确认。**其余通道原值返回 ⇒ 逐字节不变。**
    #    同样是包在 try 里：器械是附加能力，坏了自己吞，别拖垮 mo 的已验证链。
    try:
        send_state = siege.ladderEventState(flow, rid, state)
    except Exception:  # noqa: BLE001
        send_state = state
    # ⭐⭐ 2026-09-24 傍晚：``skip_inair`` 通道 —— 这条 update_state **整条不发**。
    #    依据是用户那架 14.3 s 录屏的逐帧判读（30 fps，见 ``siege`` 模块头 §24）：
    #      t=6.367 s 梯子横躺 → t=6.400 s 已斜靠上墙 —— 33 ms 一帧完成，无中间帧。
    #    而那架走的是 ``none`` 通道（抓包 57 B / ``event_num=0``）⇒ 「先到墙上」
    #    不是我们发的位姿，是客户端收到 ``1002`` 后**自己**按预设位姿摆的；
    #    真正的动画在 ``t≈9.07 s``（= 服务端 ``1003`` 到达）才开始。
    #    ⇒ 抽掉 ``1002`` 就等于抽掉「先到墙上」，只留 ``1003`` 触发原生动画。
    #    ⚠️ 副作用是 ``t=2286..4952 ms`` 这 2.7 s 客户端收不到该梯子任何消息
    #       （观感：读条完没动静，2.7 s 后才开始架梯）—— 预期，不是卡死。
    try:
        skip_send = siege.ladderSkipState(flow, rid, state)
    except Exception:  # noqa: BLE001
        skip_send = False
    # 不发包，但**状态记账照常**（``setState`` 已在函数开头做过），
    # 也照常通知 ``siege`` 推表现层（``onStateChange`` 在下方）——
    # 保证「跳过」只影响**这一条 wire 报文**，不碰任何内部状态机。
    if not skip_send:
        flow.send(mo_flow.MO_COMMAND,
                  mo_flow.encode_update_state(target=rid, state=send_state,
                                              state_change_ms=wire.serverNowMs(),
                                              state_time_ms=state_time_ms,
                                              events=events, params=params),
                  "mo-update-state-" + reason + "-" + str(send_state)
                  + ("+born" if events else "")
                  + ("+crop" if params else "")
                  + (" (late<-%d)" % state if send_state != state else ""))
    # ⭐ 2026-09-23 夜：状态落地后通知 ``siege`` 推表现层（云梯立起 = MO_INCLINE_NTF
    #   推倾角；投石车被操控 = CONTROL_ON；攻城车前进 = MOVE_MO_BC）。
    #   这是本模块与攻城三件事**唯一**的耦合点：siege 内部自带 try/except，
    #   任何异常都吞掉，绝不拖垮 mo 这条已实机验证过的链路。
    siege.onStateChange(flow, rid, state)


def setMoState(flow, rid, state, state_time_ms, reason, events=None):
    """``_apply`` 的**公开别名** —— 给 ``siege`` 用。

    同包内跨模块调用别去摸 ``_apply`` 这个私有名：改名时调用方会静默失效。

    ``events``（2026-10-07 第三十七轮）：显式指定 ``event_var`` 事件号；
    ``None`` = 走 ``siege.ladderBornEvents`` 推导（原行为，逐字节不变）。
    """
    return _apply(flow, rid, state, state_time_ms, reason, events)


def _timerName(rid):
    return "mo-settle-%d" % rid


def _stopNtfTimerName(rid):
    """``T7_MO_STOP_NTF=anim`` 时单独发 STOP_NTF 用的定时器。"""
    return "mo-stop-ntf-%d" % rid


def _channelTimerName(rid):
    """读条定时器（``T7_MO_RAISE_AT=channel-end`` 时，读满才切状态）。"""
    return "mo-channel-%d" % rid


def _settleDelayFor(animMs, channelMs):
    """落地延时（ms）；``None`` = 不落地。

    ⚠️ 基准时刻随 ``T7_MO_RAISE_AT`` 变：``now`` 时相对**交互开始**，
    ``channel-end`` 时相对**读条结束**。所以别拿这个数直接当绝对时刻。
    """
    mode = settleMode()
    if mode == "off":
        return None
    if isinstance(mode, int):
        return mode
    if mode == "anim":
        return channelMs or animMs
    return animMs


def _safeCancel(flow, name):
    """取消定时器；没这个定时器就当没事（原生层行为未实测，别让它炸整条消息）。"""
    try:
        flow.cancel(name)
    except (AttributeError, TypeError, ValueError):
        pass


def _handleChannelDone(flow, rid):
    """读条读完（2286）→ **交互成立** → 此刻才切状态、才开始播动画。

    ⭐ 用户口供（2026-09-22）：「读完条才开始动」。所以在这之前
    服务端**一个 ``update_state`` 都不发**，梯子保持躺地（1000）。
    """
    cur = _channelLoad(flow)
    if cur is None or cur["rid"] != int(rid) or cur["phase"] != 0:
        return True
    info = sceneInfoByRid(flow)
    animMs = animationMsFor(info, rid)

    # 交互结束（玩家可以松手了）。``settle`` / ``off`` 时不在这里发。
    if stopNtfMode() in ("anim", "now"):
        _stopInteractNtf(flow, rid, once=True)

    _apply(flow, rid, cur["to"], animMs, "mo-interact")
    flow.result["logs"].append("mo-channel-done rid=" + str(rid)
                               + " state=" + str(cur["to"])
                               + " anim_ms=" + str(animMs))

    # 进相位 1（动画中），起落地定时器
    _channelSave(flow, rid, wire.serverNowMs(), cur["ms"], cur["from"],
                 cur["to"], 1)
    settleMs = _settleDelayFor(animMs, cur["ms"])
    if settleMs:
        flow.later(_timerName(rid), settleMs)
    return True


def _handleCancelInteract(flow, target):
    """C->S 取消交互（msg 2）—— **玩家读条中途松手**。

    ⭐ 这是「按住不放」的另一半。没有它，「点一下就跑开」也能把梯子立起来，
    状态机等于没上锁。

    判定：
    * 读条**还没读完**（``now - start < channelMs``）→ 真取消：
      撤掉落地定时器、状态回退到交互前、发 ``STOP_NTF(is_interrupt=1)``。
    * 读条**已读完** → 交互已经成立，动画让它播完，这里什么都不做
      （``STOP_NTF`` 由读条定时器在 2286 那刻发）。
    """
    cur = _channelLoad(flow)
    if cur is None:
        flow.result["logs"].append("mo-cancel-interact no-channel target=" + str(target))
        return True
    rid = cur["rid"]
    elapsed = wire.serverNowMs() - cur["start"]

    if cur["phase"] == 0 and cur["ms"] > 0 and elapsed < cur["ms"]:
        # --- 相位 0：读条没读完就松手 → 真取消 -------------------------
        # ⭐ 读完条才开始动，所以读条期间状态**压根没变过**，通常不用回退。
        #    这里仍然检查一次，防止将来有人在相位 0 就改了状态。
        _safeCancel(flow, _channelTimerName(rid))
        _safeCancel(flow, _timerName(rid))
        _safeCancel(flow, _stopNtfTimerName(rid))
        oldState = getState(flow, rid)
        if oldState != cur["from"]:
            _apply(flow, rid, cur["from"], 0, "cancel")
        flow.result["logs"].append("mo-cancel-interact abort rid=" + str(rid)
                                   + " " + str(oldState) + "->" + str(cur["from"])
                                   + " elapsed=" + str(elapsed) + "/" + str(cur["ms"]))
        _stopInteractNtf(flow, rid, interrupt=1, once=True)
        _channelClear(flow)
        return True

    # --- 相位 1 / 读条已读完：交互已成立，动画让它播完 -----------------
    flow.result["logs"].append("mo-cancel-interact after-channel rid=" + str(rid)
                               + " phase=" + str(cur["phase"])
                               + " elapsed=" + str(elapsed) + "/" + str(cur["ms"]))
    _channelDrop(flow)              # 保留 STOP_NTF 标记，别让落地再发一条
    return True


def _handlePullState(flow):
    """C->S 查询所有 MO 状态 —— 全量回一遍。"""
    flow.result["logs"].append("mo-pull-state-req")
    pushStates(flow, "pull-state")
    return True


def message(flow, command, selector, body):
    """``app.handleMessage`` 的挂点。返回 ``True`` 表示已处理。"""
    if not moEnabled():
        return False
    if command != mo_flow.MO_COMMAND:
        return False
    if flow.session.get("role") != "instance":
        return False
    if selector == mo_flow.MO_INTERACT:
        target, cli_tick, interact_type = mo_flow.decode_interact(body)
        return _handleInteract(flow, target, cli_tick, interact_type)
    if selector == mo_flow.MO_PULL_STATE_REQ:
        mo_flow.decode_pull_state_req(body)
        return _handlePullState(flow)
    if selector == mo_flow.MO_CANCEL_INTERACT:
        # ⭐ 2026-09-22：原版是**按住不放**，所以这条真会被客户端发出来
        # （松手 / 读条被打断）。改动前只记一行日志、什么都不做。
        try:
            target = mo_flow.decode_cancel_interact(body)
        except ValueError as error:
            flow.result["logs"].append("mo-cancel-interact-bad " + repr(error))
            return True
        return _handleCancelInteract(flow, target)
    if selector == mo_flow.MO_ROCK_HIT:
        # ⭐ 2026-09-29：投石命中回报（C->S）。只在 siege 的投石链开着时才接管；
        #   否则照旧落回 app 的 unhandled 兜底（零行为差异）。
        try:
            if siege.onRockHit(flow, body):
                return True
        except Exception:  # noqa: BLE001
            pass
        return False
    return False


def timer(flow, name):
    """``app.handleTimer`` 的挂点：动画播完 → 落到稳定状态。

    返回 ``True`` 表示这个定时器是本模块的。
    """
    # ⭐ 2026-09-23 夜：``siege`` 的定时器（云梯分步推倾角 / 攻城车逐拍广播）
    #   先过一道 —— app.py 的 handleTimer 是链式 ``and not``，多一层就够，不必动 app。
    if siege.timer(flow, name):
        return True
    if name.startswith("mo-channel-"):
        return _handleChannelDone(flow, int(name[len("mo-channel-"):]))
    if name.startswith("mo-stop-ntf-"):
        # 读条读完（2286）→ STOP_NTF。默认路径（T7_MO_STOP_NTF=anim）。
        _stopInteractNtf(flow, int(name[len("mo-stop-ntf-"):]), once=True)
        return True
    if not name.startswith("mo-settle-"):
        return False
    rid = int(name[len("mo-settle-"):])
    state = getState(flow, rid)
    if state is None:
        return True
    settled = settledState(state)
    if settled != state:
        _apply(flow, rid, settled, 0, "settled")
        flow.result["logs"].append("mo-settled target=" + str(rid)
                                   + " " + str(state) + "->" + str(settled))
        # 动画播完 → STOP_NTF(22)，与 START_NTF(21) 成对。
        # ``once=True`` ⇒ 若读条定时器已经发过就不再重复；``=off`` 开关仍可全关。
        if stopNtfMode() in ("settle", "anim"):
            _stopInteractNtf(flow, rid, once=True)
    _channelClear(flow)
    return True
