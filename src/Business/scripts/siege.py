# -*- coding: utf-8 -*-
"""攻城器械三件套（2026-09-23 夜新增）—— 云梯立起 / 投石车操控 / 攻城车前进。

为什么要单独一个模块
--------------------
``mo.py`` 的状态机只覆盖「云梯 + 门」两族；``_handleInteract`` 里那句

    if not (isLadder(state) or isDoor(state)):
        # 云梯/门以外的（投石车 / 攻城车 / 门机关…）状态机还没做：
        # 只确认收到，不改状态。

就是「按 C 对投石车/攻城车毫无反应」的直接原因。这三件事各有独立的报文与
状态族，塞进 ``mo.py`` 会把那条**已经实机验证过**的云梯链搅浑，所以单独一个模块、
单独一组开关，坏了一个不影响另一个。

三件事各自的铁证（全部来自 TDR / 客户端配置表，不是推断）
--------------------------------------------------------
① **云梯立起来** → ``CS_PROTO_MO_INCLINE_NTF``（sel=16）
   ``python -m codec union --union CS_PROTO_MO_DATA`` 解出该分支存在且
   ``-m codec struct`` 给出 ``@12 incline_angle float「MO倾斜弧度」``。
   * 行为树 ``云梯_出生.btree`` 只做「设 Character + 动画事件」，**没有旋转动作**；
   * ``propsheet/攻城器械.psheet`` 云梯行（``器械类型=1``，记录名为空串）
     ``nav文件 = TSZZ_stair_04.hkt``（与行为树同一字符串）、``触发参数2 = 1.57``；
   * 实机：按 C 后梯子**横躺**不动。⇒ 缺的就是这条倾角通知。

② **投石车按 C 绑定人物可操控** → ``CS_PROTO_MO_CONTROL_ON``（sel=4）
   exe 字符串链：``AVGeRecvSiegeControlOn`` / ``AVGeGetOnSiegeAction`` /
   ``AVGeSetCatapultChargeTimerAction``；操作者状态表
   ``propsheet/battlestate/攻城器械类.psheet``（全是 ``武将_弩机_*``，含
   ``武将_弩机_不可脱离``）；器械自己的 ``投石车.psheet`` 有 ``武将_投石车_射击/蓄力``；
   驾驶树 ``移动_投石车.btree`` 里写着 ``Event="LEAVE_DEMOLISHER"`` +
   ``切换行为树 → 移动_步兵``。
   ⇒ ``CONTROL_ON(actor, mo)`` + 状态切到 ``6001``（``CATAPULT_BE_OP_WAIT``
   「被操控待机」）。这条状态值正好对上行为树 ``投石车_被操控待机.btree``。

③ **攻城车靠近自动前进** → ``CS_PROTO_MOVE_MO_BC``（命令2 / sel=50，41 字节）
   + 状态 ``1021``（``E_MO_STATE_ATTACK_CITY_CAR_MOVE``）。
   ``propsheet/攻城器械.psheet`` 攻城车行有 ``nav文件 = TSZZ_siege_01_part1.hkt``、
   ``最低人数 1 / 最高人数 5``、``人数速度1..5 = 0.5/0.6/0.72/0.84/1``
   ⇒ 由**服务端**驱动它在 nav 上走（客户端只收广播播动画）。
   ⚠️ 本轮只实现「按 C 开始推」；「靠近自动触发」还差一个逐拍玩家位置钩子
   （在 ``controls`` 的 ground tick 里），故 ``car_trigger`` 默认 ``interact``。

开关（全部在 ``level.ini`` 的 ``[cc]`` 段，环境变量优先）
------------------------------------------------------
``ladder_incline``  on/off   云梯立起（默认 on）
``ladder_angle``    1.5708   立起的倾角（弧度，π/2 = 90°）
``ladder_steps``    8        分几步推到位（客户端会插值，给中间值更顺）
``ladder_step_ms``  120      每步间隔
``ladder_state``    up_settle  在哪些状态推倾角：``up_only``=只 1002+1003 /
                     ``up_settle``=1002+1003+1004（默认）
``cat_control``     on/off   投石车 CONTROL_ON/OFF（默认 on）
``cat_state``       6001     被操控时的 MO 状态
``cat_actor_ntf``   on/off   是否附带 ACTOR_CONTROL_NTF(27)（默认 on）
``cat_interact_stop`` on/off 6001 之后是否补发 STOP_INTERACT_NTF(22) 收尾（默认 on）
``cat_actor_state`` on/off   是否给**人物**发 STATE_SYNC(cmd4/sel3)（默认 on）
``cat_actor_state_id`` 350   发哪个人物状态号（``ACT_STATE_*``，全表见
                      ``hkx_decode/out/act_state_macros_full.md``；
                      349 预备 / 350 过程 / 351 结束 / 352 装填）
``car_drive``       off/on   攻城车驾驶（**默认 off**：要服务端自己积分路径）
``car_speed``       1.0      米/秒
``car_step_ms``     200      广播间隔
``car_target_rid``  10002    朝哪个 rid 走（默认 3 号物件=正门）
``car_move_state``  2        ``MOVE_MO_BC.state``（**移动档 0..255**，不是 E_MO_STATE。
                             自检发现 1021 塞不进 uchar ⇒ 二者不是一个命名空间）
``car_dir_flip``    0        1 = 把 dir 取负（符号约定未实测）

回退（10 秒）：把对应开关改 ``off`` 重启服务端；或整体关掉 —— 删掉 ``mo.py`` 里
三处 ``siege.*`` 调用即可（每处都有 ⭐ 标记）。
"""
import json
import math
import os

from . import ccobject
from . import contracts as wire
from . import tracelog
from .codec import mo_flow, move_flow

# --- 器械类型 = ``ccobject.json`` 的 tid = ``propsheet/攻城器械.psheet`` 的「器械类型」
LADDER_TID = 1
CITY_CAR_TID = 2
CATAPULT_TID = 14

# --- MO 状态 -----------------------------------------------------------------
#
# ⭐⭐⭐ 2026-09-24 修正：云梯四个状态值**全部错位**，已按客户端权威表重定。
#
# 客户端真值（data1.vfs blk=121169 状态名池，263 行；存盘 out/cc/mo_state_names.json）：
#     1000 MO_Ladder_OnGround         躺地
#     1001 MO_Ladder_Falling          正在倒
#     1002 MO_Ladder_InAir            在空中（架起中）
#     1003 MO_Ladder_Standing         已立住
#     1004 MO_Ladder_InAir_Occupied   空中·有人
#   ⚠️ 1005-1010 是**哨塔族**（哨塔状态_初始状态 / 建造完毕 / 破坏状态 …），
#      不是云梯。旧代码把 LADDER_IDLE/UP/UP_END/DOWN 写成 1006/1007/1008/1009，
#      于是每次按 C 都在给云梯下「哨塔状态」⇒ 客户端加载 哨塔状态_*.btree
#      ⇒ 可见性=true + 造塔特效 ⇒ **凭空冒出瞭望塔**；下一拍破坏态 ⇒ **碎一地**。
#
# 出处在客户端里也留了痕迹（``mo_state_macros_full.md`` 是 exe 宏表，**这一段是错的**）：
#   宏名 E_MO_STATE_LADDER_UP=1007 之类**名字对了、值错了**，两边在这段区间冲突。
LADDER_GROUND = 1000          # 躺地（未架起）
LADDER_FALLING = 1001         # 正在倒下（回收动画中）
LADDER_INAIR = 1002           # 在空中（架起动画中）
LADDER_STANDING = 1003        # 已立住
LADDER_INAIR_OCCUPIED = 1004  # 空中·有人（爬梯中）

# 旧名别名，值与上面一致（防别处引用断掉）。
LADDER_IDLE = LADDER_GROUND
LADDER_UP = LADDER_INAIR
LADDER_UP_END = LADDER_STANDING
LADDER_DOWN = LADDER_FALLING

CATAPULT_WAIT = 6000
CATAPULT_BE_OP_WAIT = 6001
CAR_IDLE = 1020
CAR_MOVE = 1021
CAR_OPENING = 1022   # MO_Siege_Opening —— 架梯/展开动画开始（vfs 状态池 blk 121169 实证）
CAR_OPENED = 1023    # MO_Siege_Opened  —— 架梯/展开完成

# --- 人物状态号（``ACT_STATE_*``，出处 ``hkx_decode/out/act_state_macros_full.md``，
#     2026-09-24 从 ``TieJiClient.exe / sh_proto_cs`` 逐条取值）----------------
#
# ⚠️ 与上面的 ``E_MO_STATE_*`` 是**两套编号**：那套走 ``MO_UPDATE_STATE`` 改**器械**，
#    这套走 ``cmd=4 sel=3 STATE_SYNC`` 改**人物**。22:3x 实机口供「按 C 人物没动」
#    的根因就在这 —— 投石车分支只发了器械的 6001，从未给人物发过状态。
ACT_BALLISTA_PREPARE = 349    # ACT_STATE_BALLISTA_SHOT_PRAPARE
ACT_BALLISTA_PROCESS = 350    # ACT_STATE_BALLISTA_SHOT_PROCESS
ACT_BALLISTA_END = 351        # ACT_STATE_BALLISTA_SHOT_END
ACT_BALLISTA_LOAD = 352       # ACT_STATE_BALLISTA_SHOT_LOAD
ACT_IDLE = 2                  # 待机（解绑后回这儿，否则人物卡在弩机姿态）
# --- 2026-09-28 新增：投石车自己的四个动作号 + 换武器号 -------------------------
# 出处 ``hkx_decode/out/act_state_macros_full.md``（exe metalib 直读，659 条）：
#   157 ACT_STATE_CATAPULT_CAPACITY_1（蓄力一段）
#   158 ACT_STATE_CATAPULT_CAPACITY_2（蓄力二段）
#   159 ACT_STATE_CATAPULT_ATTACK（发射）
#   160 ACT_STATE_CATAPULT_CAMERA（弹道相机）
#   ⚠️ 这张表里**没有**「投石车待机」——待机就是通用的 ``ACT_IDLE``(2)，
#      客户端靠**手持武器的类型**去挑 ``投石车.psheet`` 才把 2 解释成
#      ``Battle\Catapult\武将_投石车_待机``。所以「上车」缺的不是状态号，是**武器**。
#   1  ACT_STATE_CHANGE_WEAPON（换出武器）—— 换武器链收尾那一拍，与 app.py 同款。
ACT_CATAPULT_CAPACITY_1 = 157
ACT_CATAPULT_CAPACITY_2 = 158
ACT_CATAPULT_ATTACK = 159
ACT_CATAPULT_CAMERA = 160
ACT_CHANGE_WEAPON = 1
# ⭐⭐⭐ 2026-09-28：为什么「换武器」之后**必须再补一拍待机**（本模块最容易踩的一脚）。
#   出处 ``data1.vfs`` 块 123614（``s_act_state_cli.bin``，MSES v7，行宽 10950 × 445 行，
#   表体在块内偏移 140；行首 ``u32 状态号 + 32B GBK 名``，行内 +7000 起是
#   ``msg u32 | 30051 u32 | 目标状态 u32`` 的边表）—— 本轮实测解出：
#     * 全表 445 行里**没有任何一条边**指向状态 1（换出武器）来自 2，也**没有
#       任何一条边从 1 指回 2** ⇒ 状态 1 是**死路**，进去就出不来。
#     * 能回到 2（待机）的边只有 19 条，全是「读条/上马/装填/复活」收尾那一拍；
#       投石车自己的两条是 ``157 --msg 300020--> 2`` 与 ``160 --msg 300490--> 2``。
#     * 能进 157/160 的**只有状态 2**（``2 --msg 300010--> 157``、``2 --msg 300480--> 160``）
#       ⇒ 投石车那套动作全部以「先处于待机」为前提。
#   ⇒ 客户端只在**状态迁移那一刻**去查 ``propsheet/<武器类型名>.psheet``；同值重发
#     （2→2）不会重进状态树，挂接节点不跑。所以「换武器」那条链的正确收尾是
#     **1 然后 2**，不是只发 1、也不是只发 2。
#   先例（**已实机确认**）：会话 ``6800-700367836`` t=230.651 客户端按 2 换武器，
#   服务端 ``RSP → USE_UPDATE → STATE_SYNC(1)``，12ms 后紧跟一条 ``STATE_SYNC(2)``
#   （日志标签 ``battle-attack-return-idle``），用户确认换武器成功。
ACT_WAIT = ACT_IDLE           # 别名：待机=2，写清楚意图（见上）

# 投石车的**武器 tid**。出处：客户端武器模板表 ``data1.vfs`` 块 125289 起
# （行宽 2829、1016 行、行首 u32 = tid）；同行 名字/类型/类别 = 投石车/投石车/攻城。
# 同表校验：1030411=龙牙刀、1080311=飞戟、1080511=竹烟筒，与 hero_roster.json 吻合。
# 同族：29002=云梯、30004/30008=弩机；49001..49004 为同内容变体。
# ⚠️ 29001 是**本表证据最强的那一个**（与 29002 云梯成对），但「客户端实际认哪一把」
#    仍需实机验证；换 49001 只需改 level.ini ``cat_weapon_tid``，不必动代码。
CATAPULT_WEAPON_TID = 29001
# 虚拟武器挂到哪个武器槽。玩家名册只占 1..5，这里用 9 避开（不改动原有槽位）。
CATAPULT_WEAPON_SLOT = 9

# 投石初速 / 仰角 —— 「松手发射」那条链用的（见 ``sendShootRock``）。
# 出处：``CS_PROTO_MO_SHOOT_ROCK`` 的载荷是 ``attacker | flyer | pos | velocity``，
# 初位初速由**服务端**给（客户端只收，不自己算）。这两个值是**经验值**，
# 实机看投石飞得远近/高低再调 ``cat_shoot_speed`` / ``cat_shoot_elev``，不必动代码。
CATAPULT_SHOOT_SPEED = 30.0     # m/s
CATAPULT_SHOOT_ELEV = 45.0      # 度

# 立梯目标角 = π/2。表源：``propsheet/攻城器械.psheet`` 云梯行 ``触发参数2 = 1.57``。
LADDER_ANGLE_FULL = math.pi / 2

# --- 云梯「立起通道」（2026-09-24 新增，逐架可选，见 ``ladder_raise_table``）------
#
# 三条通道都是**真实存在**的协议分支（``-m codec union --union CS_PROTO_MO_DATA``
# 的 27 条里直读出来），区别只在「谁来算这个位姿」：
#
#   incline  老路：``MO_INCLINE_NTF``(sel=16) 只给**一个标量角度**
#            ⇒ 客户端自己决定绕哪转。实测它绕**模型原点**转，而梯脚在局部
#            X = −8.038（``攻城器械.psheet`` 的 ``[触发区域]LocalPosX = −8``
#            独立印证）⇒ 梯脚沉到地下 8 m、顶端只剩半根 ——
#            用户原话「搭得低了，再高一半」。
#   born     ``E_MO_EVENT_CALL_BORN_SCRIPT``(1023) 塞进 ``update_state`` 的
#            ``event_var`` ⇒ 客户端跑实体挂的 ``云梯_出生.btree``
#            （``001506_37bc216.xml`` 的 ``<GeBehaviorable>``）⇒ 该树发 Havok
#            事件 ``Opened`` + 浮点参数 ``CropTime=0.38`` ⇒ 行为图
#            ``005892_behavior_TSZZ_stair_04_*.hkx`` 切到
#            ``MO_Ladder_Setup_animation.hkt``。**位姿由原生动画的 root motion
#            决定** —— 这就是「让它本来就是对的」。
#   rbtrans  ``MO_RB_TRANSFORM``(sel=8) 直给**位置 + 四元数**
#            ⇒ 绕哪转由我们说了算（默认绕梯脚）。位姿我们自己算，最可控，
#            但也就绕开了原生动画。
#   born_late ⭐ 2026-09-24 下午新增，专治用户口供「**先倒到墙上，后播放的
#            动画，顺序反了**」。做法只有一处差别：``_apply`` 在「读条结束」
#            那一帧**下发的 state 仍是 1000（躺地，与客户端当前值相同）**，
#            只把 ``event_var`` 带上 —— 于是客户端**没有状态变化可执行**，
#            唯一的动作就是跑出生树播原生动画；等动画播完（t≈4952）再下发
#            1003 让它落到「已立住」。**顺序就正过来了：先动画、后到位。**
#            ⚠️ 唯一风险：客户端 FSM 若对「状态未变化」的 update_state 做
#            early-return，会**连带丢掉 event_var**（表现＝按 C 毫无反应）。
#            有这个症状就说明此路不通，回 ``born``。
#   none     什么都不发（连倾角/位姿都不发）—— **纯状态对照**：客户端只收到
#            ``update_state(1002)`` 与 ``update_state(1003)``。
#            ✅ 2026-09-24 已**结案**（用户城外那架 14.3 s 录屏逐帧判读，见 §24）：
#            客户端确实是**状态驱动** —— 只喂两条 update_state 就能让梯子
#            「一帧瞬移到墙上（1002）→ 再从原地重演一遍架梯动画（1003）」。
#            且**全程没有任何 event_var** ⇒ **1023 事件不是动画的必要条件**。
#   skip_inair  比 none 再进一步：``1002`` 那条 update_state **整条不发**。
#            依据就是上面那份判读 —— 「先到墙上」是 1002 干的、「动画」是 1003 干的，
#            那把 1002 抽掉就该只剩动画 ⇒ 这正是「先动画、后状态」。
#            与 ``born_late`` 的差别：那条仍发一条 state=1000 的包（赌客户端
#            early-return）；这条彻底不发（赌 1002 是唯一元凶）。两个赌注不同，
#            一次重启同时摆上就能分开。
RAISE_INCLINE = "incline"
RAISE_BORN = "born"
RAISE_BORN_LATE = "born_late"
RAISE_SKIP_INAIR = "skip_inair"
RAISE_RBTRANS = "rbtrans"
RAISE_NONE = "none"
# skip_inair_rb ⭐⭐ 2026-09-24 14:55 新增 —— **「顺序」+「位姿」两条都要**。
#
# 由来（14:52 那一局，用户口供 + 抓包双证）：
#   用户按了两架，两架各对一半：
#     · 云梯3(`rbtrans`)   → 「先立到墙上，后出的动画」❌ 顺序不对
#     · 云梯1(`skip_inair`)→ 「出动画，再立到墙上」   ✅ 顺序对
#   抓包时序（`data/13976-331220703/wire/frames-1.jsonl`，按 monotonicUs 排）：
#     云梯3(10010, rbtrans)   t=2211.089  rb-transform（位姿）
#                             t=2211.251  update_state(1002)   ← 同一帧
#                             t=4873.476  rb-transform
#                             t=4873.545  update_state(1003)   ← 2.66 s 后才到
#     ⇒ **`rb_transform` 在 `1002` 那一刻就把梯子摆到 46°** ⇒ 读条一结束就「立到墙上」，
#       而触发动画的 `1003` 要等 2.66 s ⇒ 「先立墙、后动画」是**必然**。
#       注意：代码里其实是 `1002` 先、位姿后（差 0.019 ms），但落在同一帧，
#       客户端收到就立刻摆 —— 所以「谁先发」不是解法，**「什么时候发」才是**。
#   云梯1(10001, skip_inair) t=85767.020 skip state=1002（整条不发）
#                            t=88432.030 update_state(1003)（只有它）✅
#
# ⇒ 这一条通道 = **skip_inair 的顺序 + rbtrans 的位姿**：
#     ① 不发 `1002`（由 ``mo.ladderSkipState`` 抽掉）⇒ 没有可摆位的东西；
#     ② `1003` 照发 ⇒ 触发原生架梯动画（用户要的「出动画」）；
#     ③ **动画播完之后**（``ladder_rb_settle_ms``，默认 = `ccobject.json` 的
#        ``total_animation_time_ms`` 2666）再补一条 `rb_transform` 收尾定位
#        ⇒ 梯脚钉在地面、梯顶落进墙顶带（用户要的「到位」）。
#
# ⚠️ 两个已知风险（判读时先怀疑这两条，别怀疑思路）：
#   a) 动画自己的 root motion 末态未必 = 我们的 46°，收尾那一下可能**看得见跳变**。
#      真有跳变 ⇒ 说明客户端动画末态与 `verify_ladder_raise.py` 的几何解不一致，
#      那是**下一个独立变量**（要拿录屏逐帧判读动画末态的实际角度）。
#   b) `ladder_rb_settle_ms` 若短于实际动画时长，收尾会**插进动画中间** ⇒
#      动画播到一半被拽走。默认 2666 取自表，实测不够就加大。
RAISE_SKIP_INAIR_RB = "skip_inair_rb"
# incline_org ⭐⭐⭐ 2026-09-24 16:10 新增，16:50 / 17:10 两次收敛
#                            —— **位置 + 朝向都走 CC 重发**。
#
# 由来（15:54~16:04 抓包 + 用户录屏**双证**）：
#   · 客户端**不采纳** `MO_RB_TRANSFORM`：我们给云梯4 发 **90.58°**，实机做出 ~40°；
#     三架角度差 23.5°（45.29 / 68.75 / 90.58）却**表现一致** ⇒ 四元数被彻底忽略。
#   · 客户端**完全采纳** CC 物件 `pos`（§10.1「整架悬在半空」= 它老实照 pos 摆），
#     但把 pos 当**模型原点**、**绕它转** ⇒ 梯脚入地 `8.038·sinθ`、梯顶水平多伸
#     `8.038·(1−cosθ)` ⇒ 这就是用户看到的「**半截入地 + 1/4 穿墙**」。
#   · 倾角被几何**唯一确定**：梯脚贴地 + 梯顶搭墙顶 ⇒ 竖直跨距 = 墙高 11.377 m，
#     梯长 16.008 m ⇒ θ = asin(11.377 / 16.008) = **0.7907 rad (45.30°)**。
#     角度不对则**无解**（要么梯脚悬空、要么梯顶够不着）。
#
# ⇒ 本通道 = **只靠一份 CC 重发**同时给位置和朝向：
#     ① 把该架的**模型原点**钉到 `_ladderPose(pivot="foot")` 算出的点
#        （`ccobject.setDynamicOrigin`）；
#     ② 把**朝向**钉到 `(face[0], 逐架俯仰, face[2])`（`ccobject.setDynamicRotation`，
#        逐架值来自 `ladder_org_tilt_table`；没配则该架不动朝向）；
#     ③ **重发该架 CC 物件** ⇒ 客户端照新 pos + rotation 摆。
#
# ⛔ **不发 `MO_INCLINE_NTF`** —— 16:50 去掉。两条独立依据：
#     ① 14:09 那轮（**没有** incline）梯子呈正常斜靠、梯脚贴地
#        （`outputs/04_远景_梯子已靠墙.png`）⇒ 客户端**原生姿态本来就对**；
#     ② 16:32 那轮发了 `0.7905`（= 45.3°）却得到**水平**。一条「绝对倾角」报文
#        不可能把 45.3° 变成 0° ⇒ 那条倾角**没有被采纳**（被紧随的 CC 重发重置）。
#
# ⏱ **时序**（17:10 定，对齐 `skip_inair_rb`）：`1002` 什么都不发 → `1003` 触发原生
#    动画 → 延时 `ladder_org_delay_ms`（默认回落 `ladder_rb_settle_ms` = 2666 ms）
#    后再重发。**为什么必须在本模块也早退**：`mo._apply` 里 `ladderSkipState` 只抽掉
#    那**一条** `update_state` 报文，`siege.onStateChange` 是**无条件**调用的
#    （mo.py L1119-1132）⇒ 不早退就等于「我们替客户端把梯子先摆到墙上」，
#    而原生动画要到 `1003` 才开始 = 用户说的「先到墙上、再播动画」。
#
# ⚠️ 躺地（θ=0）时 `_ladderPose` 的 origin **恰好等于原 pos** ⇒ 分支里 clear，
#    **躺地零影响** —— 这正是它区别于静态 `ladder_lift_table` 的地方
#    （后者会让躺地的梯子浮空，已被用户 15:45 否决）。
#
# ✅ 已验证（16:32 抓包，会话 13976-337307418）：客户端收到**重复 `CC_ADD_EVENT`**
#    是**更新**（采纳了新 pos）；但它会按记录里的 `rotation` **重置姿态**
#    ⇒ 所以位置和朝向必须**同一份**给。
#    （旧版这里写「未验证：更新还是忽略」，那两个候选已由抓包定案。）
RAISE_INCLINE_ORG = "incline_org"
# crop ⭐⭐⭐ 2026-09-27 新增 —— **直接给 ``CropTime`` 动画参数**（唯一没试过的姿态旋钮）。
#
# 与 ``skip_inair`` 的差别**只有一处**：``1003`` 那条 ``update_state`` 的
# ``event_var`` 里多带一个 ``param(HAVOK_PARAM_TYPE_CROPTIME, 值)``。
# 状态序列、发不发 ``1002``、位姿一律与 ``skip_inair`` 完全相同
# ⇒ 「crop 架 vs skip_inair 架」的差异就是**这一个参数**，单变量干净。
#
# 依据见上方 ``HAVOK_PARAM_TYPE_CROPTIME`` 的注释块（btree + 行为图 + 绑定三重取证）。
# ⚠️ 未实测：客户端是否采信 ``update_state`` 里带来的 param。若采信，值一变
#    立起高度就该变；若**纹丝不动**，则说明这条通道也和 ``born`` 一样送不到，
#    下一步要怀疑 ``encode_animation_event_var`` 的端序（见 ``mo_flow`` 那条 ⚠️）。
RAISE_CROP = "crop"
_RAISE_MODES = (RAISE_INCLINE, RAISE_BORN, RAISE_BORN_LATE,
                RAISE_SKIP_INAIR, RAISE_RBTRANS, RAISE_NONE,
                RAISE_SKIP_INAIR_RB, RAISE_INCLINE_ORG, RAISE_CROP)

# ``E_MO_EVENT_CALL_BORN_SCRIPT`` —— TDR 宏表 ``mo_state_macros_full.md`` :252 直读
MO_EVENT_CALL_BORN_SCRIPT = 1023

# ⭐ 2026-09-27（投石车按 C 上车复盘）：``mo_flow`` 里从 exe 直读出来的两条
# 投石车控制事件，骑在 ``update_state`` 的 ``event_var`` 上：
#   MO_EVENT_BE_OP (1001)       被操作 → 投石车切到 ``投石车_被操控待机``
#   MO_EVENT_START_CONTROL(1012)开始操控 → 本地玩家切到 ``移动_投石车``
# 服务端一直空着 ``event_var`` 没发，CONTROL_ON(4) 客户端又当没看见
# （wire 实测：4/27/牛/6001 全发到位，客户端零反应）→ 这才是「按 C 上不去车」的根因。
MO_EVENT_BE_OP = 1001
MO_EVENT_START_CONTROL = 1012
# ⭐ 2026-10-07 第三十七轮：协议里一直有、但从没用过的一号事件。
#   上车链把客户端切进「移动_投石车」控制树（靠 6001+武器 29001），
#   下车却只发 6000 状态、**不带任何事件** ⇒ 客户端没有「离开控制树」的触发，
#   表现为 HUD 还写着「退出投石车 C」、人物原地不动（会话 -10 截图实证）。
#   ``MO_EVENT_STOP_CONTROL``(1013) 的官方语义就是「停止操控」，
#   与 ``MO_EVENT_START_CONTROL``(1012) 配对（见 mo_flow 常量表）。
MO_EVENT_STOP_CONTROL = 1013

# --- ``CropTime`` 动画参数通道（2026-09-27 新增）-----------------------------
#
# 为什么会有这一条：``born`` / ``born_late`` 实测对云梯立起高度**零影响**
# （用户 00:30 口供「第一个按了，还是在下面低一半」），而原因是**机理层面**的 ——
# ``云梯_出生.btree`` 里那两条 ``动画系统浮点参数 CropTime=0.38``，与客户端
# 状态驱动路径用的是**同一个值**，所以再触发一遍出生树等于把 0.38 又设了一遍。
#
# 证据链（2026-09-27 00:4x 全部新做，可复现）：
#   ① ``云梯_出生.btree``（``out/btree/``）：节点名「架梯动画调试」，
#      ``设置Character=TSZZ_stair_04.hkt`` + 「架梯中」``Opened``+``CropTime 0.38``
#      + ``DELAY 2286`` + 「架梯完成」``Opened``+``CropTime 0.38``（同一组发两遍）。
#   ② ``005892_behavior_TSZZ_stair_04_*.hkx`` 用 ``hkx_decode/hkx_tagfile.py``
#      拆开（脚本 ``ladder_frames/dump_behavior.py``，名字区起点 **0x1f**）：
#        ``hkbBehaviorGraphStringData.variableNames = ['CropTime']``
#        ``eventNames = ['Open','Close','Closed','Opened','EnterPreview','LeavePreview']``
#        ``hkbStateMachine`` 起始态 = ``Closed``(3)，另有
#        ``Closing``(1)=MO_Ladder_Landing_animation.hkt
#        ``Opening``(2)=MO_Ladder_Setup_animation.hkt
#        ``Opened`` (4)=上面那支 + MO_Ladder_shake_additive.hkt（叠加）
#   ③ 三个 ``hkbClipGenerator`` 的 ``hkbVariableBindingSet`` 成员路径直读：
#        ``Opening``/``Opened`` 的片段 → **``cropEndAmountLocalTime``**
#        ``Closing`` 的片段        → ``cropStartAmountLocalTime``
#      ⇒ ``CropTime`` 就是**裁掉动画尾部**的量。梯子最终立到多少度，
#        取决于这个裁切值。**这是目前唯一还没试过的、直接改姿态的旋钮。**
#
# ``param_id`` 出处：``E_SH_HAVOK_HARDCODE_PARAM_DEF``（客户端 metalib
# ``sh_proto_cs``，2026-09-27 ``tdr/dump_group.py`` 按分组名直读）。
# ⚠️ 数字 30 与 ``0.38`` 没有任何关系，别把两者混起来记。
HAVOK_PARAM_TYPE_CROPTIME = 30

# ⚠️ 语义**未实测**（两种读法都还活着，值的选择就是为了把两者分开）：
#   * 若 ``cropEndAmountLocalTime`` 按 Havok 原生语义读 = **秒**，
#     则 0.38 s 只裁掉 2666 ms 里的 380 ms（``total_animation_time_ms``
#     − ``animation_time_ms`` = 2666 − 2286 = 380，**正好对上**）。
#   * 若这个引擎把它当**归一化比例**读，则 0.38 = 只播到 38%。
#   ⇒ 默认探针值取 **0.9**：两种读法下**方向相反**（比例读法会变高、
#     秒读法会变矮），所以一次实验就能定死是哪种，比取 0.0 信息量大。
LADDER_CROP_PROBE = 0.9

# 梯脚在模型局部 X 上的位置。两处独立取证：
#   ① ``nif_verts.py`` 实测 ``tszz_stair_04.nif`` 包围盒 X = −8.038 .. +7.970；
#   ② ``攻城器械.psheet`` 云梯行 ``[触发区域]LocalPosX = −8``（HalfExtentsX=0.5，
#      盒子只有 1 m 厚 ⇒ 精确锁在端点）。
LADDER_FOOT_X = -8.038
# 梯顶在模型局部 X 上的位置（同一份 `nif_verts.py` 实测包围盒的另一端）。
LADDER_TIP_X = 7.970
# 梯长 = 两端点之差。**这个数是「按高度反算角度」的除数**（见 ``_angleForTipZ``）。
LADDER_LENGTH = LADDER_TIP_X - LADDER_FOOT_X      # = 16.008 m

_TILT_PREFIX = "siege-tilt-"
_DRIVE_PREFIX = "siege-drive-"
# ⭐ 2026-09-29 16:1x：车到位「架梯」的收尾定时器（发 1022 之后延时发 1023）。
_CAROPEN_PREFIX = "siege-caropen-"
_RB_PREFIX = "siege-rbsettle-"     # ``skip_inair_rb`` 的收尾定时器（见 RAISE_SKIP_INAIR_RB）
# ⭐⭐⭐ 2026-09-24 17:10：``incline_org`` 的收尾定时器。
#   为什么需要它：``mo._apply`` 里 ``ladderSkipState`` **只**抽掉那一条 ``update_state``
#   报文，``siege.onStateChange`` 是**无条件**调用的（mo.py L1119-1132）。所以
#   「抽掉 1002」不足以让位姿延后 —— 必须在本模块里也早退 + 延时。
#   时序对齐 ``skip_inair_rb``：1002 不发 → 1003 触发原生动画 → 延时后补位姿。
_ORG_PREFIX = "siege-orgsettle-"
# ⭐ 2026-09-28：投石车「换出武器(1) → 待机(2)」两拍之间的延时定时器。
#   为什么不能同一拍发完：客户端要**看得见两次迁移**才会重进状态树，
#   同一 tick 里两条可能被折叠成一次（只认最终值）。先例是 12ms 分两拍。
_CATWAIT_PREFIX = "siege-catwait-"
# ⭐⭐ 2026-09-28 22:4x：投石车「蓄力 → 发射」链的两个定时器。
#   一段 →（cat_charge_ms）→ 二段 →（松手）→ 射击 →（cat_fire_ms）→ 待机。
_CATCHARGE_PREFIX = "siege-catcharge-"
_CATFIRE_PREFIX = "siege-catfire-"

# 投石车蓄力/发射的**上行判据**（会话 6800-704076109 实证）。
#   上车后客户端照常把左键按下/松开报成 ``cmd=4 sel=1``，与普通攻击同一套布局：
#     ``00 01 | 00 00 00 8f | 00 04 93 ea | 00`` → key_comb=0x8f（bit7=左键按下）, msg_id=300010
#     ``00 01 | 00 00 00 0f | 00 04 93 f4 | 00`` → key_comb=0x0f（bit7=0 已松开）, msg_id=300020
#   ⇒ **权威判据是 key_comb 的 bit7（KEY_LEFT_MOUSE）**，msg_id 只是捎带。
#   ``300020`` 与 ``battle.ATTACK_RELEASE`` 同值（普通攻击的释放包）。
CATAPULT_CHARGE_MSG = 300010
CATAPULT_RELEASE_MSG = 300020
_KEY_BIT_LEFT_MOUSE = 7


# ============================ 开关 ==========================================
def _knob(key, env, default):
    """读 ``[cc]`` 段旋钮（**环境变量 > ini > 默认**），复用 ccobject 的三通道实现。

    复用的好处：来源会记进 ``ccobject._CC_SOURCE``，于是启动自证行与
    ``cc-object-sent`` 日志能一起证明「这个开关到底从哪来」——
    本项目在「开关没进进程」上已经白跑过三局。
    """
    return ccobject._ccKnobRaw(env, key, default)


def _flag(key, env, default):
    return ccobject._ccTrue(_knob(key, env, "on" if default else "off")[0], default)


def _num(key, env, default, cast=float):
    try:
        return cast(float(str(_knob(key, env, str(default))[0]).strip()))
    except (TypeError, ValueError):
        return default


def _text(key, env, default):
    """文本旋钮（与 ``_num`` 同源，只是不转数字）。空串按「没设」回落 ``default``。"""
    try:
        raw = str(_knob(key, env, default)[0]).strip()
    except (TypeError, ValueError):
        return default
    return raw or default


def ladderInclineEnabled():
    return _flag("ladder_incline", "T7_CC_LADDER_INCLINE", True)


def ladderAngle():
    return _num("ladder_angle", "T7_CC_LADDER_ANGLE", LADDER_ANGLE_FULL)


def ladderSteps():
    return max(1, min(64, int(_num("ladder_steps", "T7_CC_LADDER_STEPS", 8, int))))


def ladderStepMs():
    return max(10, min(5000, int(_num("ladder_step_ms", "T7_CC_LADDER_STEP_MS", 120, int))))


def ladderStateMode():
    return str(_knob("ladder_state", "T7_CC_LADDER_STATE", "up_settle")[0]).strip().lower()


# ---- 立起通道：逐架真值表（2026-09-24）--------------------------------------
def _raiseTable():
    """``{pathId: 通道名}`` —— 解析 ``ladder_raise_table=10015:born,40028:rbtrans``。

    为什么要**逐架**：三架云梯的 ``cc_tid`` 都是 1，只有 ``pathId`` 分得开
    （云梯1=10015 / 云梯3=40028 / 云梯4=40040）。做成真值表就能**一次重启同时
    对比三条通道** —— 本项目在「一局只测一个变量」上已经耗掉好几局。

    整表替换（不是叠加）。``off`` / ``none`` / ``0`` / 空 ⇒ 关表，
    全部回落默认通道（``incline`` = 改动前行为）。

    ⚠️ 歧义提醒：**整值**是 ``none`` ⇒ 关表；**某一项的值**是 ``none``
    （如 ``10015:none``）⇒ 那一架走「什么都不发」的纯状态对照通道。
    两者语义不同，别写混。
    """
    raw = str(_knob("ladder_raise_table", "T7_CC_LADDER_RAISE_TABLE", "")[0]).strip()
    if raw.lower() in ("", "off", "none", "0"):
        return {}
    table = {}
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        key, _, value = part.partition(":")
        try:
            pathId = int(key.strip())
        except (TypeError, ValueError):
            continue
        mode = value.strip().lower()
        if mode in _RAISE_MODES:
            table[pathId] = mode
    return table


def ladderRaiseTableTag():
    """自证短标记：``tbl[10015:incline,40028:born]`` / ``flat`` / ``err``。"""
    try:
        table = _raiseTable()
        if not table:
            return "flat"
        return "tbl[" + ",".join("%d:%s" % kv for kv in sorted(table.items())) + "]"
    except Exception:  # noqa: BLE001 —— 自证行绝不能把服务端带崩
        return "err"


def ladderRaiseMode(item):
    """某架云梯走哪条通道；表里没有 ⇒ ``incline``（**与改动前一致**）。

    ⭐ 2026-09-27：``ladder_crop_auto`` 打开时，**tid==1 的云梯**在逐架表里
    没配的情况下默认走 ``crop`` —— 这是「换图不用改配置」的前一半。新图的云梯
    pathId 必然不在表里，不补这一档就永远回落 ``incline`` ⇒ 整条 ``crop``
    通道不触发 ⇒ ``ladderCropTime`` 算得再对也发不出去。
    **默认 off ⇒ 与改动前逐位一致**（早返回在 ``ladderCropAuto()`` 之前）。
    """
    try:
        pathId = int(item.get("pathId"))
    except (TypeError, ValueError, AttributeError):
        return RAISE_INCLINE
    table = _raiseTable()
    if pathId in table:
        return table[pathId]
    if ladderCropAuto():
        try:
            if int(item.get("tid")) == LADDER_TID:
                return RAISE_CROP
        except (TypeError, ValueError):
            pass
    return RAISE_INCLINE


# ---- ⭐ 2026-09-27：跨图自动适配（``ladder_crop_auto``，**默认 off**）---------
#
# 由来（用户定的方向）：「用原版地图数据 + 公式，其他地图可以复用」。
#
# 现在的取值链是**逐架硬编码**（``ladder_crop_table=10015:0.0,40040:0.19``），
# 换一张图必然失效 —— 因为 **pathId 是按图分配的，跨图不唯一**：
#   * 云梯1 在 樊城(tszz)=10015 / 江陵城(yjc_low)=10013 / 玉门关外(hz_map_b)=40028 …
#   * 实测同一 ``pid=10015`` 在三张图里 CropTime 分别是 0.380 / 0.086 / 0.321
# ⇒ 按 pid 硬编码的表**换图必错**，这不是「补几行」能解决的。
#
# 正确来源是**地图自带的数据**：``ccobject.json`` 每个物件的 ``kv`` 里有两格
# （原版 ``SH_RES_CC_OBJECT_TAB`` 字段，不是我们加的）：
#     animation_time_ms        实际播放长度
#     total_animation_time_ms  动画总时长
# 公式（**逆向所得**，非官方文档）：
#     CropTime = (total_animation_time_ms − animation_time_ms) / 1000    ← 秒
# 三图实测全部对上：樊城 云梯1 (2666−2286)/1000 = 0.380 ✓
#                   江陵城 云梯1 (2666−2286)/1000 = 0.380 ✓
#                   江陵城 云梯2 (2666−2336)/1000 = 0.330 ✓
#                   江陵城 云梯4 (2666−2236)/1000 = 0.430 ✓
#
# ⚠️ 为什么做成**开关**而不是直接替换：
#   ① ``CropTime`` 的**语义**（秒 / 归一化比例）还没实机定死（见 ``LADDER_CROP_PROBE``）；
#   ② 默认 off ⇒ 现有樊城配置**逐位不变**，这是本项目的硬底线。
# 开关打开后**同时**管两件事（少任何一件，新图都发不出正确的 CropTime）：
#   * ``ladderRaiseMode`` —— tid==1 的云梯在逐架表里没配 ⇒ 默认走 ``crop``
#     （否则回落 ``incline``，整条 ``crop`` 通道根本不会触发）；
#   * ``ladderCropTime``  —— 逐架表里没有 ⇒ **从该架 kv 现算**，不回落全局默认。
LADDER_CROP_AUTO_ENV = "T7_CC_LADDER_CROP_AUTO"


def ladderCropAuto():
    """兜底开关 ``ladder_crop_auto``（**默认 off**）：换图不改配置。

    off ⇒ 逐架表 > 全局 ``ladder_crop_time``（**与改动前逐位一致**）；
    on  ⇒ 逐架表 > 该架 kv 现算 > 全局 ``ladder_crop_time``。
    """
    return _flag("ladder_crop_auto", LADDER_CROP_AUTO_ENV, False)


def ladderCropTimeFromKv(item):
    """从物件自带 ``kv`` 现算 ``CropTime``（秒）；缺字段 / 坏值 → ``None``。

    只做减法，两个字段都直接来自地图原版数据。``item`` 是
    ``ccobject.loadScene()`` 返回的**原始 dict**（``_records`` 原样透传），
    所以 ``kv`` 一定在。返回 ``None`` 时调用方回落全局默认 —— 绝不抛。
    """
    kv = item.get("kv") if hasattr(item, "get") else None
    if not isinstance(kv, dict):
        return None
    try:
        total = float(str(kv.get("total_animation_time_ms")).strip())
        anim = float(str(kv.get("animation_time_ms")).strip())
    except (TypeError, ValueError):
        return None
    return (total - anim) / 1000.0


# ---- crop 通道的 ``CropTime`` 值：全局默认 + 逐架真值表（2026-09-27）----------
def _cropTable():
    """``{pathId: CropTime}`` —— 解析 ``ladder_crop_table=10015:0.9,40028:0.0``。

    与 ``_raiseTable`` 同款：**逐架**，一次重启就能摆上多个探针值。
    整表替换（不是叠加）。``off`` / 空 ⇒ 关表，全部回落 ``ladder_crop_time``。
    """
    raw = str(_knob("ladder_crop_table", "T7_CC_LADDER_CROP_TABLE", "")[0]).strip()
    if raw.lower() in ("", "off", "none"):
        return {}
    table = {}
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        key, _, value = part.partition(":")
        try:
            pathId = int(key.strip())
            table[pathId] = float(value.strip())
        except (TypeError, ValueError):
            continue
    return table


def ladderCropTime(item):
    """该架云梯 ``crop`` 通道要发的 ``CropTime``（浮点）。

    优先级（⭐ 2026-09-27 加中间一档）：
      ① ``ladder_crop_table`` 里该架的逐架值 —— **人工覆盖，最高**；
      ② ``ladder_crop_auto`` 打开时：**从该架 kv 现算**（跨图复用，见上方那段）；
      ③ ``ladder_crop_time`` 全局默认 —— 最后兜底，默认值是 ``LADDER_CROP_PROBE``
         （0.9，见上方注释：这个值能把「比例 / 秒」两种语义**分开**，而 0.0 分不开）。

    ``ladder_crop_auto`` 默认 off ⇒ 只剩 ①③ 两级，**与改动前逐位一致**。
    """
    try:
        pathId = int(item.get("pathId"))
    except (TypeError, ValueError, AttributeError):
        pathId = None
    if pathId is not None:
        table = _cropTable()
        if pathId in table:
            return table[pathId]
    if ladderCropAuto():
        value = ladderCropTimeFromKv(item)
        if value is not None:
            return value
    return _num("ladder_crop_time", "T7_CC_LADDER_CROP_TIME", LADDER_CROP_PROBE)


def ladderEventParams(flow, rid, state):
    """``mo._apply`` 用：这条 ``update_state`` 的 ``event_var`` 要不要带动画参数。

    只有「通道 = ``crop``」且「状态 ∈ 架起态」时返回
    ``((HAVOK_PARAM_TYPE_CROPTIME, 值),)``；**其余一律 ``()``**
    ⇒ 报文与改动前逐字节相同，可无脑调。

    纯查询，不发送，不会成环（与 ``ladderBornEvents`` 同款纪律：
    器械是附加能力，任何异常都自己吞掉，绝不拖垮 ``mo`` 那条已验证的链）。
    """
    try:
        record = _object(flow, rid)
        if record is None:
            return ()
        item = record[1]
        if int(item.get("tid")) != LADDER_TID:
            return ()
        if ladderRaiseMode(item) != RAISE_CROP:
            return ()
        if state not in _tiltStates():
            return ()
        return ((HAVOK_PARAM_TYPE_CROPTIME, ladderCropTime(item)),)
    except Exception:  # noqa: BLE001
        return ()


def ladderCropTag():
    """自证短标记：``crop0.9000/flat`` / ``crop[40028:0.9000]/auto`` / ``err``。

    为什么必须加进 ``report()``：本项目在「开关没进进程」上已经白跑过三局
    （见 ``siege._knob`` 的注释）。``crop`` 的值只有**确实被进程读到**才有意义，
    所以启动那一行必须能直接看到它 —— 不用等实机表现。

    ⭐ 2026-09-27：尾巴上的 ``/flat`` ``/auto`` 是 ``ladder_crop_auto`` 的**来源自证**
    （``flat`` = 关，回落旧链路；``auto`` = 开，新图从 kv 现算）。开关进没进进程，
    看这一眼就够，不必再去翻 ini。
    """
    try:
        table = _cropTable()
        default = _num("ladder_crop_time", "T7_CC_LADDER_CROP_TIME", LADDER_CROP_PROBE)
        tail = "/auto" if ladderCropAuto() else "/flat"
        if not table:
            return "crop%.4f" % default + tail
        return "crop[%s]" % ",".join("%d:%.4f" % kv for kv in sorted(table.items())) + tail
    except Exception:  # noqa: BLE001
        return "err"


def ladderRbAngle():
    """``rbtrans`` 通道的目标倾角（弧度）。默认 π/2。

    ⚠️ 默认取 π/2 是**为了对比干净**：与 ``incline`` 同角度，
    于是两架梯子的唯一差别就是「绕原点 vs 绕梯脚」。
    想让梯子**斜靠**城墙（墙高 ≈ 11.5 m、梯长 16.007 m）应取
    ``asin(11.5 / 16.007) ≈ 0.80``（≈46°）；先确认绕对了再调这个。
    """
    return _num("ladder_rb_angle", "T7_CC_LADDER_RB_ANGLE", LADDER_ANGLE_FULL)


# ---- rbtrans 目标角：逐架真值表（2026-09-24 下午新增）------------------------
#
# 为什么要逐架：``ladder_rb_angle`` 是**全局**旋钮，一次重启只能试一个角度。
# 而这一轮要同时回答**两个互相独立**的问题：
#   ① 绕对了吗？   —— 90° 时梯脚该「原地不动、原点抬高 8.038 m」
#   ② 角度对吗？   —— 46°（0.80 rad）时梯顶该落进墙顶带 55.5~56.5 m
# 两个问题用一个角度测不出来（绕错了 + 角度对了，和绕对了 + 角度错了，
# 表现可能一样）。做成逐架表就能**一次重启摆两组**，行为即身份。
#
# 表源：``out/cc/verify_ladder_raise.py`` 的几何自检（57/57 全绿）——
#   绕梯脚 90°  ⇒ 梯脚 (514.896, 553.350, 44.204) 不动、原点抬到 52.242
#   绕原点 90°  ⇒ 梯脚 (522.828, 554.648, 36.166) 沉入地下 8.038
#   绕梯脚 0.80 ⇒ 梯顶 z = 55.687（墙顶实测 55.8~56.3）
def _rbAngleTable():
    """``{pathId: 角度}`` —— 解析 ``ladder_rb_angle_table=40028:0.80,40040:1.5708``。

    整表替换（不是叠加）。``off`` / ``none`` / ``0`` / 空 ⇒ 关表，
    全部回落全局 ``ladder_rb_angle``（**与改动前逐位一致**）。
    坏项跳过（不整表作废），解析不出来的 pathId / 角度都当坏项。
    """
    raw = str(_knob("ladder_rb_angle_table",
                    "T7_CC_LADDER_RB_ANGLE_TABLE", "")[0]).strip()
    if raw.lower() in ("", "off", "none", "0"):
        return {}
    table = {}
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        key, _, value = part.partition(":")
        try:
            pathId = int(key.strip())
            angle = float(value.strip())
        except (TypeError, ValueError):
            continue
        table[pathId] = angle
    return table


def ladderRbAngleTableTag():
    """自证短标记：``rbt[40028:0.8000,40040:1.5708]`` / ``flat`` / ``err``。"""
    try:
        table = _rbAngleTable()
        if not table:
            return "flat"
        return "rbt[" + ",".join("%d:%.4f" % kv for kv in sorted(table.items())) + "]"
    except Exception:  # noqa: BLE001 —— 自证行绝不能把服务端带崩
        return "err"


def ladderRbSettleMs():
    """``skip_inair_rb`` 通道：``1003`` 之后**等多久**再补那条收尾 ``rb_transform``。

    默认 **2666** —— 出处是 `ccobject.json` 三架云梯的 ``total_animation_time_ms``
    （云梯1/3/4 = 2666 / 2666 / 2666，三架一致）。

    ⚠️ 这个值是**表里的动画总时长**，不是「实测动画播完的时刻」。
    抓包实证的旁证：云梯3 的 ``1002``（t=2211.089）与 ``1003``（t=4873.476）
    相差 **2662 ms**，与 2666 只差 4 ms ⇒ 服务端的两个时刻差 = 表值，可信。
    但**动画本身播完的时刻**是否也等于 2666，未实测 —— 若收尾插进动画中间
    （观感：动画播到一半被拽走），把本值**加大**即可（改 ini，重进关卡）。
    """
    return max(0, min(20000, int(_num("ladder_rb_settle_ms",
                                     "T7_CC_LADDER_RB_SETTLE_MS", 2666, int))))


def ladderOrgDelayMs():
    """``incline_org`` 通道：``1003`` 之后**等多久**再把该架 CC 物件重发到位。

    默认**回落** ``ladderRbSettleMs()``（= 2666 ms，表里的动画总时长）——
    与 ``skip_inair_rb`` 同一套时序：**先动画、后到位**。

    ⚠️ 为什么需要这个延时（2026-09-24 17:05 实测）：
      ``mo._apply`` 里 ``ladderSkipState`` 只抽掉那**一条** ``update_state`` 报文，
      而 ``siege.onStateChange`` 是**无条件**调用的（mo.py L1119-1132）。于是
      ``incline_org`` 分支在 ``1002`` 时刻照样重发 CC 物件、把梯子**瞬移**到立起位姿，
      而原生动画要到 ``1003`` 才开始 ⇒ 用户看到的「先到墙上、再播动画」。

    ⚠️ 设 **0** ⇒ 立刻发（= 16:55 那版行为：梯子先瞬移到墙上、动画才开始）。
       设更大 ⇒ 收尾更晚（若观感是「动画播到一半被拽走」，把它调大）。
    """
    raw = str(_knob("ladder_org_delay_ms", "T7_CC_LADDER_ORG_DELAY_MS", "")[0]).strip()
    if raw.lower() in ("", "off", "none"):
        return ladderRbSettleMs()
    try:
        return max(0, min(20000, int(float(raw))))
    except (TypeError, ValueError):
        return ladderRbSettleMs()


# ---- rbtrans 目标**高度**：逐架真值表（2026-09-24 15:15 新增）----------------
#
# 为什么加「高度」这一层：用户思考的单位是**米**，不是**弧度**。
#   14:56 三架统一 `skip_inair_rb` + 全局 0.80 之后，动画（顺序）三架全过，
#   但**三架梯顶高度并不一致** —— 因为三架的 `pos.z` 不同：
#       云梯1 (pos.z=44.2037) ⇒ 梯顶 55.687
#       云梯3 (pos.z=44.7406) ⇒ 梯顶 56.224
#       云梯4 (pos.z=44.7887) ⇒ 梯顶 56.272      ← 三架差 0.585 m
#   同一个角度换不来同一个高度。要让三架**梯顶齐平**（或各自对准墙垛），
#   要么逐架写角度（不直观），要么**直接写高度让服务端反算角度**（本表）。
#
# 硬依据（墙高到底多少）：`propsheet/攻城器械.psheet` 云梯行
#   `[触发区域]LocalPosY = 11.25`（梯脚到墙的**水平**距离）
#   + 梯长 16.007 ⇒ 竖直 = sqrt(16.007² − 11.25²) = **11.377 m**
# ⇒ **psheet 自己给出的墙高 = 11.377 m（相对梯脚）**，这是最权威的口径。
#   交叉印证：CC 物件反推的墙顶 55.8~56.3 与「梯脚 + 11.377」吻合到 1% 以内。
#   ⚠️ 别拿 `heightfield.json` 的 maxZ=55.385 当墙顶 —— 那是城外山坡。
#
# 换算：θ = asin(11.377 / 16.008) = **0.7900 rad**
#   ⇒ 0.80 其实**高了 0.107 m**（0.80 → 梯顶 = 梯脚 + 11.4845）。
#     0.107 m 在实机上是「梯顶比墙垛高出一小截」——看得见。
def _tipZTable():
    """``{pathId: 目标梯顶高度 m}`` —— 解析 ``ladder_rb_tip_z_table=10015:56.0,...``。

    整表替换；``off``/``none``/``0``/空 ⇒ 关表；坏项跳过；``<= 0`` 的值当坏项。
    """
    raw = str(_knob("ladder_rb_tip_z_table",
                    "T7_CC_LADDER_RB_TIP_Z_TABLE", "")[0]).strip()
    if raw.lower() in ("", "off", "none", "0"):
        return {}
    table = {}
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        key, _, value = part.partition(":")
        try:
            pathId = int(key.strip())
            tip_z = float(value.strip())
        except (TypeError, ValueError):
            continue
        if tip_z > 0:
            table[pathId] = tip_z
    return table


def ladderRbTipZTableTag():
    """自证短标记：``rbtz[10015:55.580,*:56.000]`` / ``flat`` / ``err``。

    ``*`` = 全局 ``ladder_rb_tip_z``（逐架表没命中时用它）。
    """
    try:
        parts = ["%d:%.3f" % kv for kv in sorted(_tipZTable().items())]
        global_z = _num("ladder_rb_tip_z", "T7_CC_LADDER_RB_TIP_Z", 0.0)
        if global_z > 0:
            parts.append("*:%.3f" % global_z)
        return "rbtz[" + ",".join(parts) + "]" if parts else "flat"
    except Exception:  # noqa: BLE001 —— 自证行绝不能把服务端带崩
        return "err"


def ladderRbTipZFor(item):
    """该架的**目标梯顶高度**（米）；表里没有 ⇒ 全局；全局也 ≤0 ⇒ ``None``（走角度）。"""
    try:
        pathId = int(item.get("pathId"))
    except (TypeError, ValueError, AttributeError):
        pathId = None
    table = _tipZTable()
    if pathId is not None and pathId in table:
        return table[pathId]
    global_z = _num("ladder_rb_tip_z", "T7_CC_LADDER_RB_TIP_Z", 0.0)
    return global_z if global_z > 0 else None


def _angleForTipZ(pos_z, tip_z):
    """由**目标梯顶高度**反算倾角。

    几何（与 ``_ladderPose`` 同源，只是反着算）::

        梯顶 z = pos.z + (TIP_X − FOOT_X)·sin(θ) = pos.z + 16.008·sin(θ)
        ⇒ sin(θ) = (tip_z − pos.z) / 16.008
        ⇒ θ = asin(...)

    自证（云梯1，pos.z = 44.2037）::

        θ = 0.80   ⇒ 梯顶 = 44.2037 + 16.008·sin(0.80) = 55.688  ✓ 与几何自检一致
        θ = 0.7900 ⇒ 梯顶 = 44.2037 + 16.008·sin(0.79) = 55.580
                   （= psheet 口径的墙顶：梯脚 + 11.377）

    夹取：``sin`` 落在 (0, 1) 之外时夹到 0 / π/2 —— 目标高度低于地面或高过梯长
    都是配置错误，**夹取比抛异常好**（服务端不能因为一行 ini 崩）。
    """
    s = (float(tip_z) - float(pos_z)) / LADDER_LENGTH
    if s <= 0.0:
        return 0.0
    if s >= 1.0:
        return math.pi / 2
    return math.asin(s)


def ladderRbAngleFor(item):
    """某架云梯的 ``rbtrans`` 目标角（弧度）。

    **优先级（2026-09-24 15:15 起「高度优先」）**：
      ① 逐架高度表 ``ladder_rb_tip_z_table`` 命中 ⇒ 按该高度**反算**角度
      ② 逐架角度表 ``ladder_rb_angle_table`` 命中 ⇒ 用该角度
      ③ 全局高度 ``ladder_rb_tip_z`` > 0 ⇒ 按该高度**反算**角度
      ④ 全局角度 ``ladder_rb_angle``（= 改动前行为）

    ⇒ 高度优先于角度：**高度是目的，角度只是手段**。
    ⚠️ 与 ``ladderRaiseMode`` 同一纪律：**查不到一律回落默认**，
    于是关表 = 逐位回到改动前行为，可无脑调。
    """
    tip_z = ladderRbTipZFor(item)
    if tip_z:
        try:
            pos_z = float((item.get("pos") or (0.0, 0.0, 0.0))[2])
            return _angleForTipZ(pos_z, tip_z)
        except (TypeError, ValueError, IndexError):
            pass                      # 拿不到 pos ⇒ 静默回落角度，绝不抛
    try:
        pathId = int(item.get("pathId"))
    except (TypeError, ValueError, AttributeError):
        return ladderRbAngle()
    return _rbAngleTable().get(pathId, ladderRbAngle())


def ladderOrgTiltTable():
    """``{pathId: 俯仰角(弧度)}`` —— 解析 ``ladder_org_tilt_table=40028:-0.7905,40040:0.7905``。

    ⭐ 2026-09-24 16:50 新增，只给 ``incline_org`` 通道用。

    作用：重发 CC 物件时，把该架的 ``rotation`` 从「躺地」改成「立起」。
    见 ``ccobject._DYNAMIC_ROTATION`` 的注释块（重发会把姿态重置）。

    ⚠️ **正负号还没实测**（右手系下「向上抬」应是 ``−θ``，客户端可能是左手系）。
    ⇒ 逐架可配：一次上机让三架各试一个符号，一次定案。
    整表替换；``off`` / 空 ⇒ 关表 ⇒ 三架都**不改朝向**（透传原 ``face``）。
    """
    raw = str(_knob("ladder_org_tilt_table",
                    "T7_CC_LADDER_ORG_TILT_TABLE", "")[0]).strip()
    if raw.lower() in ("", "off", "none", "0"):
        return {}
    table = {}
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        key, _, value = part.partition(":")
        try:
            pathId = int(key.strip())
            tilt = float(value.strip())
        except (TypeError, ValueError):
            continue
        table[pathId] = tilt
    return table


def ladderOrgTiltFor(item):
    """该架在 ``incline_org`` 通道下的**俯仰角**；没配 ⇒ ``None``（不改朝向）。"""
    try:
        return ladderOrgTiltTable().get(int(item.get("pathId")))
    except (TypeError, ValueError, AttributeError):
        return None


def ladderOrgTiltTag():
    """自证短标记：``[40028:-0.7905,40040:0.7905]`` / ``flat``（配 ``orgtilt=%s`` 用）。"""
    try:
        table = ladderOrgTiltTable()
    except Exception:  # noqa: BLE001
        return "err"
    if not table:
        return "flat"
    return "[%s]" % ",".join("%d:%g" % (k, table[k]) for k in sorted(table))


def ladderRbPivot():
    """旋转中心：``foot``（梯脚，默认）/ ``origin``（模型原点 = 现状那种错法）。"""
    value = str(_knob("ladder_rb_pivot", "T7_CC_LADDER_RB_PIVOT", "foot")[0]).strip().lower()
    return value if value in ("foot", "origin") else "foot"


def ladderRbQuatOrder():
    """四元数分量顺序。**这是本通道唯一的假设**（TDR 只有 ``rot_0..rot_3`` 之名）。

    ``wxyz``（默认）/ ``xyzw``。实测不对就换一个重启，一秒的事。
    """
    value = str(_knob("ladder_rb_quat", "T7_CC_LADDER_RB_QUAT", "wxyz")[0]).strip().lower()
    return value if value in ("wxyz", "xyzw") else "wxyz"


def _ladderPose(pos, yaw, angle, pivot, foot=LADDER_FOOT_X):
    """躺平 → 立起：算目标 ``(pos, quat)``。**纯几何，可离线自检**。

    模型（三处实测合起来唯一自洽的读法）：
      * 梯长轴 = 模型局部 **X**（``nif_verts.py``：全长梯 X −8.038..+7.970 (16.007)，
        Y ±1.0、Z ±1.0 ⇒ 16×2×2 的长条）；
      * 躺平时长轴**水平**，方向由 ``face`` 的 yaw 给（三架云梯 ``face`` 的
        x/y 分量都 ≈ 0，只有 z 显著 ⇒ 只取 yaw，误差 < 0.04 rad）；
      * 梯脚在局部 X = ``foot``（−8.038）。

    立起 = 绕**过梯脚、方向为 ``a = d × up``（水平且 ⊥ d，单位向量）**的轴转 ``angle``，
    于是 ``d' = d·cosθ + up·sinθ``。

    ``pivot="foot"`` 时把梯脚钉在原地 ⇒ 模型原点必须抬起来：
    ``origin' = (pos + foot·d) − foot·d'``，θ=90° 时正好抬高 ``|foot| = 8.038 m``。
    """
    d = (math.cos(yaw), math.sin(yaw), 0.0)          # 躺平：梯长方向（水平）
    axis = (math.sin(yaw), -math.cos(yaw), 0.0)      # d × up
    ca, sa = math.cos(angle), math.sin(angle)
    d2 = (d[0] * ca, d[1] * ca, sa)                  # 立起后：d·cosθ + up·sinθ
    if pivot == "foot":
        px = pos[0] + foot * d[0] - foot * d2[0]
        py = pos[1] + foot * d[1] - foot * d2[1]
        pz = pos[2] - foot * d2[2]
    else:                                            # origin：原点不动（现状那种错法）
        px, py, pz = pos[0], pos[1], pos[2]
    half = angle / 2.0
    w, s = math.cos(half), math.sin(half)
    return (px, py, pz), (w, s * axis[0], s * axis[1], s * axis[2])


def _sendRbTransform(flow, rid, item, angle):
    """``rbtrans`` 通道：把算好的位姿用 ``MO_RB_TRANSFORM``(sel=8) 下发。"""
    try:
        x, y, z = (float(v) for v in (item.get("pos") or (0.0, 0.0, 0.0)))
        yaw = float((item.get("face") or (0.0, 0.0, 0.0))[2])
    except (TypeError, ValueError, IndexError):
        _log(flow, "siege-ladder-rbtrans-bad-data rid=%s" % rid)
        return False
    point, quat = _ladderPose((x, y, z), yaw, angle, ladderRbPivot())
    if ladderRbQuatOrder() == "xyzw":
        quat = (quat[1], quat[2], quat[3], quat[0])
    flow.send(mo_flow.MO_COMMAND,
              mo_flow.encode_rb_transform(int(rid), wire.serverNowMs(), [(point, quat)]),
              "mo-rb-transform-%d" % int(rid))
    _log(flow, "siege-ladder-rbtrans rid=%d state=%.4frad pivot=%s quat=%s"
         " pos=(%.3f,%.3f,%.3f)" % (int(rid), angle, ladderRbPivot(),
                                    ladderRbQuatOrder(),
                                    point[0], point[1], point[2]))
    return True


def _resendCcObject(flow, rid):
    """把 ``rid`` 这架 CC 物件**重发**一次（带它当前的 ``pos``）。

    ``incline_org`` 通道的第二步 —— 客户端只认 CC 物件的 ``pos``，
    改了 ``ccobject._DYNAMIC_ORIGIN`` 之后必须重发它才会看到。

    ⚠️ ``scene`` **必须函数内 import**：``scene`` 顶层 import ``mo``，
    而 ``mo`` 顶层 import 本模块 ⇒ 顶层 import 会成环。
    （``controls`` / ``mo`` 在本文件里也是这么处理的，同一套理由。）

    返回 ``scene.sendVisionObject`` 的结果（``True`` = 认这个 mid）。
    异常一律由调用方吞掉 —— 器械是**附加**能力，坏了不该拖垮状态机。
    """
    from . import scene
    return scene.sendVisionObject(flow, int(rid))


def catapultControlEvents(flow, rid, state):
    """``mo._apply`` 用（经 ``ladderBornEvents`` 转发）：投石车进入「被操控(6001)」时，
    在 ``update_state`` 的 ``event_var`` 里带上 ``MO_EVENT_BE_OP`` / ``MO_EVENT_START_CONTROL``，
    触发客户端把投石车切到 ``投石车_被操控待机``、把本地玩家切到 ``移动_投石车``——
    也就是「按 C 上投石车」真正缺的那一刀（CONTROL_ON(4) 客户端实测当没看见）。

    由 ``cat_events`` 旋钮控制具体带哪些事件（默认空 = 不发，保持原行为），
    方便实机逐个验证：``"1001"`` / ``"1012"`` / ``"1001,1012"``。取值为逗号分隔的
    ``E_MO_EVENT_*`` 数值，与 ``mo_flow.MO_EVENT_*`` 同表。
    纯查询，不发送，不会成环（与 ladderBornEvents 同款纪律）。
    """
    try:
        if not controlEnabled():
            return ()
        if int(state) == CATAPULT_WAIT:
            # ⭐ 第三十七轮：下车那条 6000 也带上 1013（与脉冲双保险 ——
            #   客户端若按「状态+事件」一起处理，这条就够）。
            return (MO_EVENT_STOP_CONTROL,) if catStopEventEnabled() else ()
        if int(state) != controlState():
            return ()
        raw = str(_knob("cat_events", "T7_CC_CAT_EVENTS", "")[0]).strip()
        if not raw:
            return ()
        out = []
        for tok in raw.split(","):
            tok = tok.strip()
            if tok:
                out.append(int(tok))
        return tuple(out)
    except Exception:  # noqa: BLE001
        return ()


def catStopEventEnabled():
    """下车时补 ``MO_EVENT_STOP_CONTROL``(1013)（第三十七轮实验）。

    旋钮 ``cat_stop_event``（env ``T7_CC_CAT_STOP_EVENT``），**默认 on**。
    背景（会话 -10 实机截图）：按 C 下车后模型变了但 HUD 仍是投石车面板
    （「退出投石车 C」）、人物**原地不动** ⇒ 客户端没离开「移动_投石车」
    控制树。两处下发：
      * ``sendStopControlPulse`` —— **6000 之前**在 6001 状态下补一发纯事件
        报文（客户端此刻还在控制树里，事件才有人接）；
      * ``catapultControlEvents`` 给 6000 那条也带上 1013（双保险）。
    回退（10 秒）：``cat_stop_event=off`` 或 ``T7_CC_CAT_STOP_EVENT=0``。
    """
    return _flag("cat_stop_event", "T7_CC_CAT_STOP_EVENT", True)


def sendStopControlPulse(flow, rid):
    """下车第一步：在**仍是 6001** 的状态下补一发 ``STOP_CONTROL`` 事件。

    ⚠️ 必须**直发报文**，不能走 ``mo.setMoState`` —— 那条会回调
    ``siege.onStateChange(6001)`` → ``onCatapultState(6001)`` → 重发整套上车链
    （CONTROL_ON + 武器 29001），把人又按回车上。这里只发这一条
    ``update_state``，服务端状态机一个字节都不动（紧跟着的 6000 才是真状态切换）。
    """
    if not catStopEventEnabled():
        return False
    try:
        from .codec import mo_flow
        flow.send(mo_flow.MO_COMMAND,
                  mo_flow.encode_update_state(target=int(rid), state=controlState(),
                                              state_change_ms=wire.serverNowMs(),
                                              state_time_ms=0,
                                              events=(MO_EVENT_STOP_CONTROL,)),
                  "mo-update-state-siege-catapult-stop-control+1013")
        _log(flow, "siege-catapult-stop-control-pulse rid=%d" % int(rid))
        return True
    except Exception as error:  # noqa: BLE001 —— 附加能力，坏了不许拖垮下车链
        _log(flow, "siege-catapult-stop-control-pulse-failed rid=%s %r" % (rid, error))
        return False


def ladderBornEvents(flow, rid, state):
    """``mo._apply`` 用：这条 ``update_state`` 要不要带 ``event_var`` 事件。

    云梯 ``born``/``born_late`` 通道 → ``(1023,)``；投石车进入被操控(6001)且
    ``cat_events`` 非空 → 对应控制事件；其余**一律 ``()``**
    ⇒ 报文与改动前逐字节相同（未开启投石车事件时零影响）。
    纯查询，不发送，不会成环。
    """
    try:
        record = _object(flow, rid)
        if record is None:
            return ()
        item = record[1]
        tid = int(item.get("tid"))
        if tid == LADDER_TID:
            if ladderRaiseMode(item) not in (RAISE_BORN, RAISE_BORN_LATE):
                return ()
            return (MO_EVENT_CALL_BORN_SCRIPT,) if state in _tiltStates() else ()
        if tid == CATAPULT_TID:
            return catapultControlEvents(flow, rid, state)
    except Exception:  # noqa: BLE001 —— 附加能力，坏了不能拖垮 mo 那条已验证的链
        return ()
    return ()


def ladderEventState(flow, rid, state):
    """``mo._apply`` 用：这条 ``update_state`` **实际该下发哪个 state**。

    默认**原值返回**（⇒ 逐字节与改动前一致，可无脑调）。

    只有 ``born_late`` 通道 + 状态 = ``1002``（架起中）时返回 ``1000``（躺地）——
    也就是「**只发事件、不改状态**」：客户端看不到状态变化，唯一能执行的就是
    出生树里那两条 ``动画系统事件=Opened``，于是**动画先跑**；位姿等 t≈4952
    那条 ``1003`` 再确认。这一改动的由来是用户实机口供
    「**先倒到墙上，后播放的动画，顺序反了**」—— 对应的假设是
    「客户端收到 ``1002`` 就先把梯子摆到『在空中』的预设位姿，动画只是补播」。

    纯查询，不发送。
    """
    try:
        record = _object(flow, rid)
        if record is None:
            return state
        item = record[1]
        if int(item.get("tid")) != LADDER_TID:
            return state
        if ladderRaiseMode(item) != RAISE_BORN_LATE:
            return state
        if int(state) == LADDER_INAIR:
            return LADDER_GROUND
        return state
    except Exception:  # noqa: BLE001
        return state


def ladderSkipState(flow, rid, state):
    """``mo._apply`` 用：这条 ``update_state`` **整条不发**吗？

    只有「通道 = ``skip_inair``」且「状态 = ``1002``（架起中）」时返回 ``True``；
    **其余一律 ``False``** ⇒ 报文逐字节与改动前相同，可无脑调。

    由来（2026-09-24 用户城外那架 14.3 s 录屏，逐帧到 30 fps）：
    ``t=6.367 s`` 梯子还横躺、``t=6.400 s`` 已斜靠上墙 —— **33 ms 一帧完成，无中间帧**，
    即「瞬移」。而那架走的是 ``none`` 通道（抓包 57 B，``event_num=0``），
    说明这一下**不是我们发的任何位姿指令**，是客户端收到 ``1002`` 后自己按预设位姿摆的。
    约 ``t=9.07 s``（= 服务端 ``1003`` 到达时刻）才出现真正的动画：
    梯子被拽回原地 → 立起 → 靠到墙上。

    ⇒ 「先到墙上」= ``1002``，「动画」= ``1003``。把 ``1002`` 整条抽掉，
    客户端就没有可摆位的东西，只剩 ``1003`` 触发的那段原生动画。

    ⚠️ 抽掉之后 ``t=2286..4952 ms`` 这 2.7 s 里客户端**收不到任何该梯子的消息**
    —— 玩家观感是「读条完了没动静，过 2.7 s 才忽然开始架梯」。这是**预期**，
    不是卡死。判读时别把它当成失败。
    """
    try:
        record = _object(flow, rid)
        if record is None:
            return False
        item = record[1]
        if int(item.get("tid")) != LADDER_TID:
            return False
        # ``skip_inair_rb`` 同样要抽掉 ``1002`` —— 它比 ``skip_inair`` 只多一件事：
        # 动画播完后补一条 ``rb_transform`` 收尾（见 ``_rbSettleTick``）。
        # ⭐⭐⭐ 2026-09-24 16:50：``incline_org`` **也必须抽掉 `1002`** ——
        #    这是本轮实机（会话 13976-337307418）抓包定案的：
        #    1002 事件那一毫秒里，服务端发了
        #        mo-update-state-mo-interact-1002  →  CC 重发(243B)  →  incline×8
        #    客户端收到 `1002` 后**先按预设姿态把梯子摆上去**（就是 14:5x 那轮
        #    `skip_inair_rb` 专门要抽掉的「瞬移」），然后 CC 重发又把它重置 ——
        #    用户 16:32 的原话就是「**动画顺序也错了**」。
        #    ⇒ 抽掉 `1002`，只留 `1003` 触发原生动画；位置/朝向改由 CC 重发负责。
        # ⭐⭐⭐ 2026-09-27：``crop`` 也抽掉 `1002` —— 它与 ``skip_inair`` 的
        #    报文形状必须**只差一个 param**，否则「高度变了」就有两个解释。
        if ladderRaiseMode(item) not in (RAISE_SKIP_INAIR, RAISE_SKIP_INAIR_RB,
                                        RAISE_INCLINE_ORG, RAISE_CROP):
            return False
        return int(state) == LADDER_INAIR
    except Exception:  # noqa: BLE001
        return False


def controlEnabled():
    return _flag("cat_control", "T7_CC_CAT_CONTROL", True)


def controlState():
    return int(_num("cat_state", "T7_CC_CAT_STATE", CATAPULT_BE_OP_WAIT, int))


def actorNtfEnabled():
    return _flag("cat_actor_ntf", "T7_CC_CAT_ACTOR_NTF", True)


def interactStopEnabled():
    return _flag("cat_interact_stop", "T7_CC_CAT_INTERACT_STOP", True)


def catStopFirst():
    """⭐ 2026-09-27 18:25：``STOP_INTERACT_NTF(22)`` 是**先发**还是**后发**。

    默认 **on** = 先 STOP 再 ``setMoState`` —— 对齐云梯成功链
    （``rsp → START(21) → 读条 → STOP(22) → update_state``）。

    取证（会话 ``38000-595458823``，event=7116）：投石车那条 ``update_state(6001)``
    确实发出去了（帧里 ``00 00 17 71``），但**客户端一个上行都没回**、人物继续
    正常走路 ⇒ 客户端在「交互中」把这条 update_state 吞了。

    回退（10 秒）：``cat_stop_first=off`` ⇒ 回到旧顺序（先状态后 STOP）。
    """
    return _flag("cat_stop_first", "T7_CC_CAT_STOP_FIRST", True)


def sendInteractStop(flow, rid):
    """补发 ``CS_PROTO_MO_STOP_INTERACT_NTF``(22)，结束这次交互。

    取证 2026-09-24 23:58:35（会话 ``13976-364031392``）：同局**云梯成功链**是
    ``rsp → START_NTF(21) → 读条 → STOP_NTF(22) → update_state 1003``；
    投石车在 ``onInteract`` 里 ``return True`` 提前接手，只发过 21、**从没发过 22**
    ⇒ 客户端一直停在「交互中」，后面的 CONTROL_ON / 27 全不生效。
    """
    if not interactStopEnabled():
        return
    try:
        from . import mo
        mo._stopInteractNtf(flow, rid)
        _log(flow, "siege-catapult-interact-stop rid=%d" % int(rid))
    except Exception as error:  # noqa: BLE001 —— 附加能力，坏了不许拖垮交互
        _log(flow, "siege-catapult-interact-stop-failed %r" % (error,))


def actorStateEnabled():
    return _flag("cat_actor_state", "T7_CC_CAT_ACTOR_STATE", True)


def actorStateId():
    """操控投石车时给人物下发的 ``ACT_STATE_*``，默认 **待机 2**。

    ⚠️ 2026-09-28 改：旧默认是 ``ACT_BALLISTA_PROCESS``(349) —— 那是**弩机**的
    「发射过程」拍，``投石车.psheet`` 里根本没有对应行（那张表只有 25 行：
    待机 / 射击蓄力一段·二段 / 射击 / 弹道相机 / 换出武器 / 濒死 / 死亡 …），
    落到基表就会播成弩机姿态。投石车「被操控待机」在客户端就是通用的
    ``ACT_STATE_WAIT``(2)，靠**手持武器类型**去 ``投石车.psheet`` 才解释成
    ``Battle\\Catapult\\武将_投石车_待机``。旋钮 ``cat_actor_state_id`` 仍在，
    要试弩机那套值不必改代码。
    """
    return int(_num("cat_actor_state_id", "T7_CC_CAT_ACTOR_STATE_ID",
                    ACT_WAIT, int))


def sendActorState(flow, state, reason):
    """``cmd=4 sel=3 STATE_SYNC`` —— 改的是**人物**的行为状态，不是器械。

    复用 ``battle.syncState``（它管着 ``seq`` 计数与 ``instance_started_at`` 缺失时
    的兜底日志）。取不到 ``syncState`` 时**宁可不发**也不自己拼一条 seq 错位的包。

    返回 ``True`` = 真的发出去了。调用方（``pushStateChange``）靠它决定要不要
    排第二拍 —— 发都没发就排定时器，会把「没生效」伪装成「生效了」。
    """
    if not actorStateEnabled():
        return False
    try:
        from . import battle
        battle.syncState(flow, int(state), reason)
        _log(flow, "siege-catapult-actor-state %d (%s)" % (int(state), reason))
        return True
    except AttributeError:
        _log(flow, "siege-catapult-actor-state-skipped no battle.syncState")
    except (TypeError, ValueError) as exc:
        _log(flow, "siege-catapult-actor-state-failed %s" % exc)
    return False


def carDriveEnabled():
    return _flag("car_drive", "T7_CC_CAR_DRIVE", False)


def carSpeed():
    return max(0.05, min(20.0, _num("car_speed", "T7_CC_CAR_SPEED", 1.0)))


def carStepMs():
    return max(50, min(2000, int(_num("car_step_ms", "T7_CC_CAR_STEP_MS", 200, int))))


def _sceneOf(flow):
    """当前场景名（只为日志；拿不到就 ``"?"``）。"""
    try:
        from . import controls
        return str(controls.airWallScene(flow) or "?")
    except Exception:  # noqa: BLE001
        return "?"


CAR_GATE_TID = 3            # 樊城的「正门」tid（江陵城正门 tid=152 ⇒ 靠名字兜）


def carTargetRaw():
    """``car_target_rid`` 的**原始字符串**（可能是 ``auto``）。"""
    return str(_knob("car_target_rid", "T7_CC_CAR_TARGET_RID", "10002")[0]).strip()


def carTargetAuto():
    """``car_target_rid=auto`` ⇒ 按**当前场景**自动找正门（换图不用改配置）。"""
    return carTargetRaw().lower() in ("auto", "scene")


def carTargetRid():
    """``car_target_rid`` 的**数值**；填 ``auto`` 时返回 ``0``（见 ``carTargetResolved``）。"""
    try:
        return int(float(carTargetRaw()))
    except (TypeError, ValueError):
        return 0


def carHeadingOffset():
    """车头朝向的**固定偏置**（度）。默认 ``0``。

    若实机看到「车头歪 N 度」（但走的方向是对的），把它填成 ∓N。
    """
    return _num("car_heading_offset", "T7_CC_CAR_HEADING_OFFSET", 0.0)


def carDirMode():
    """``move_mo_bc`` 里 ``dir`` 的**语义**：``abs``（默认）或 ``delta``。

    ⭐ 2026-09-29：用户报「**角度 反方向增加了**」—— 这是**增量累加**的典型
    症状（每 200ms 收到一个绝对角却按增量加，5 s = 25 拍 ⇒ 越转越多）。
    ``abs`` 是原行为（逐字节不变）；确认是增量语义后改 ``delta``，
    届时发 ``0``（= 保持当前朝向直行）。

    ⚠️ 判据要分清（都是「车头不对」但修法完全不同）：
      - 车**走的方向对**、车头只是**歪一个固定角** ⇒ ``car_heading_offset``
      - 车**倒退着走**（朝向差 180°）            ⇒ ``car_dir_flip=1``
      - 车**原地越转越多 / 朝向发散**             ⇒ ``car_dir_mode=delta``
    """
    raw = str(_knob("car_dir_mode", "T7_CC_CAR_DIR_MODE", "abs")[0]).strip().lower()
    return "delta" if raw in ("delta", "rel", "relative") else "abs"


def carPoseResend():
    """每拍把车的**新位置 + 新朝向重发 CC 物件**。默认 **on**。

    ⭐⭐ 2026-09-29 实机闭合（录屏 14:28 + 会话 ``22816-761425105``）：
    ``move_mo_bc`` 只让**车轮空转**、车体纹丝不动 —— 而服务端日志是
    702 条 ``move-mo-bc-10004-2`` + ``siege-car-drive-arrived dist=0.31``
    （服务端逻辑全对，走了 67 m）。
    ⇒ 客户端**不采纳** ``move_mo_bc.position``；它只认「**重发的 CC 物件**」
    的 ``pos`` / ``rotation``（与云梯立起**同一条机制**：``_applyOrgPose`` →
    ``scene.sendVisionObject``，见 ``ccobject._DYNAMIC_ORIGIN`` 的注释块）。

    ``move_mo_bc`` 仍然要发 —— 它负责让客户端**播移动动画（车轮转）**。

    回退：``[cc] car_pose_resend=off``（车会退回「轮子空转、位置不动」）。
    """
    return _flag("car_pose_resend", "T7_CC_CAR_POSE_RESEND", True)


def carPoseYawFlip():
    """重发 CC 时 ``heading`` 是否**取负**。默认 **off**。

    ⚠️ 2026-09-29 更正：这条旋钮的**旧依据是错的**。
    旧注释引用 ``ccobject.py`` 第 756-762 行的「``faceZ ≈ −路径方位角``」
    （攻城车2 ``faceZ=−88.68°`` ↔ 路径方位角 ``85.09°``）—— 但**实测场景数据
    里根本没有 −88.68° 这个值**（江陵城车2 是 ``−41.22°``、车1 是 ``+29.75°``；
    樊城车1 是 ``+9.83°``）⇒ 那条笔记引用的是**另一份/旧版**数据，不能当依据。

    现在的依据见 ``carPoseYawOffset``（几何反推，有 0.17° 的实证吻合）。
    本旋钮只留作「整条 yaw 反向」的应急开关；正常保持 **off**。
    """
    return _flag("car_pose_yaw_flip", "T7_CC_CAR_POSE_YAW_FLIP", False)


def carPoseYawOffset():
    """攻城车模型「本地前向」相对世界 ``+X`` 的**固定偏置**（度）。默认 **+20**。

    ⭐⭐⭐ 2026-09-29 16:1x：**符号更正**（15:2x 写的 −20 是错的，已由实机日志纠正）。

    ⚠️⚠️ 15:2x 的**错误**：把 ``C0 = 路径方位 − face.z`` 直接当成了 offset。
       ``C0`` 其实是「**模型本地前向 φ**」，而 **``offset = −φ``**。

    正确框架：设模型本地前向角 = ``φ``，我们发 ``rotation.z = yaw``，
    则 **车头在世界中的方向 = φ + yaw**。要车头朝前进方向 ``az`` ⇒ ``yaw = az − φ``
    ⇒ **``offset = −φ``**。

    标定（同用户两句判断，结论相反）：

      ②「**樊城的方向都是对的**」（樊城 10007）= **静态**判断
        （该车当时从没被驱动过，``grep siege-car-drive-start`` 全库只有 10004）
        ⇒ 作者摆的 ``face.z`` 就是对的
        ⇒ ``φ = az − face.z = −10.14 − 9.83 = −19.97°``
        ⇒ **``offset = −φ = +19.97 ≈ +20``**

      ①「朝向错了，**肯定是横着走过去**的」（江陵城右路 10004）
        ⇒ 用 ``+20`` 得 ``yaw = 85.09 + 20 = 105.09°``，相对 ``face.z=−41.22°``
          转 **+146°** ⇒ 正好从「横着」转正 ✓

    ⭐ **实机铁证**（会话 53492-767953443，16:13，樊城）::

        siege-car-pose rid=10007 az=-10.14deg yaw=-30.14deg face_z=9.83deg off=-20.0deg

    旧值 ``−20`` 把车从 9.83° 硬转到 −30.14°（转 −40°）⇒ 车头歪、**梯子背对城墙**
    （用户 16:14 截图）。改成 ``+20`` 后 ``yaw = 9.86° ≈ 原 face.z`` ⇒ 恢复「对的」。

    ⚠️ 若实机看到车头**整体掉头 180°** ⇒ 把本值改成 ``200``（= 20+180）。
    ⚠️ 若「还差一个固定角 N」⇒ 本值改 ``20 ∓ N``。
    ⚠️ 完全拿不准时：打开 ``car_pose_yaw_probe=on`` 让车自己把 8 个方向转一遍。
    """
    return _num("car_pose_yaw_offset", "T7_CC_CAR_POSE_YAW_OFFSET", 20.0)


def carPoseYawProbe():
    """**标定模式**：让 ``car_pose_yaw_offset`` 自动扫过 8 个 45° 档位。默认 off。

    3 架车的 ``face`` 与各自路径的偏差是 **0.2° / 36° / 110°**（手工摆的，无规律）
    ⇒ 模型的「本地前向」**推不出来**。这个模式让车自己把 8 个方向转给你看：
    每 ``car_pose_probe_hold_ms`` 换一档，并写 ``siege-car-yaw-probe`` 日志
    （含档位序号与角度）。看到「车头正对着它前进方向」那一档，
    把它填进 ``[cc] car_pose_yaw_offset`` 即可。
    """
    return _flag("car_pose_yaw_probe", "T7_CC_CAR_POSE_YAW_PROBE", False)


def carPoseProbeHoldMs():
    """标定模式每档停留多久（毫秒）。默认 2500。"""
    return int(_num("car_pose_probe_hold_ms", "T7_CC_CAR_POSE_PROBE_HOLD_MS", 2500))


CAR_POSE_PROBE_STEPS = 8          # 45° × 8 = 一圈


def _probeOffset(flow, rid):
    """标定模式：按已过的拍数算出当前档位。返回 ``(offset_deg, step_index)``。"""
    hold = max(1, int(carPoseProbeHoldMs() / max(1, int(carStepMs()))))
    key = "carProbeTick%d" % int(rid)
    try:
        tick = int(flow.session.get(key, 0))
    except (TypeError, ValueError):
        tick = 0
    flow.session[key] = tick + 1
    step = (tick // hold) % CAR_POSE_PROBE_STEPS
    return step * (360.0 / CAR_POSE_PROBE_STEPS), step


def carPoseFace():
    """重发 CC 时**是否改写朝向**。默认 **on**（2026-09-29 由 off 改为 on）。

    ⭐ 用户实机口供（15:10）：「**物体 方向需要改变，移动的方向是对的**」——
    移动方向（走 path 终点）已修好，但车**一直保持场景摆的 ``face``**，
    所以「**方向没有变**」。⇒ 现在每拍把 ``rotation.z`` 钉到 **航向 − 90°**。

    ⚠️ 之前默认 off 的理由是「场景 ``face`` 是配套的模型旋转，改写会把车转歪」。
    那只对**摆得正**的车成立；实测 10004 被摆歪了 36°（见
    ``carPoseYawOffset`` 的推导表）⇒ 必须改写。
    回退：``[cc] car_pose_face=off``（退回「保持场景 face、只平移」）。
    """
    return _flag("car_pose_face", "T7_CC_CAR_POSE_FACE", True)


def carPoseHangle():
    """是否下发 **``horizontal_angle``（左右旋转目标弧度）**。默认 **on**。

    ⭐⭐⭐⭐ 2026-09-29 17:2x 新增。见 ``ccobject._DYNAMIC_HANGLE`` 的取证块：

    客户端 metalib（``TieJiClient.exe`` / ``sh_proto_cs``）里
    ``CS_PROTO_VISION_CC_DYNAMIC_INFO`` 的**字段描述**：

    * ``rotation``         = 「静态物件，初始可能有绕 **x,y** 轴旋转」← **不是偏航**
    * ``horizontal_angle`` = 「**左右旋转目标弧度**」              ← **这才是偏航**
    * ``vertical_angle``   = 「上下旋转目标弧度」

    实机铁证（17:0x，会话 ``48236-771505160``，江陵城）：服务端把
    rid 10004 的 ``rotation.z`` 由 ``−41.22°`` 改成 ``+105.09°``
    （wire 逐字节核对无误），用户仍报「**两个攻城车，角度没有变**」
    ⇒ ``rotation`` 对车不生效；而 ``horizontal_angle`` **从未被下发过**。

    取值与 ``rotation.z`` **完全同源**（都用 ``carPoseYaw``）⇒
    两处不会漂；``car_pose_yaw_offset`` / ``car_pose_yaw_flip`` /
    ``car_pose_yaw_probe`` 三个标定旋钮对**两条通道同时**生效。

    回退：``[cc] car_pose_hangle=off``（⇒ 该字段回 ``0.0`` = 既有行为）。
    """
    return _flag("car_pose_hangle", "T7_CC_CAR_POSE_HANGLE", True)


# ⭐⭐⭐⭐ 2026-09-29 18:4x：**架梯俯仰角**（抬头 / 低头）
#
# 用户实机口供（18:48，带图，樊城）：
#   「樊城这个，**还是在墙里**，，**有点低了**，，能**往上改改角度**」
#   「**和之前云梯一样呀，，角度不够，也是有一部分在墙体里面，后来通过公式，
#     算出来才正常**」
# 截图：梯子**水平**伸出、末端扎在城墙里、高度偏低 ⇒ 需要让梯子**抬头**。
#
# 取证（客户端 metalib，``CS_PROTO_VISION_CC_DYNAMIC_INFO``，见 ``probe_tdr_fields.py``）：
#   * ``[ 5] rotation``         = 「静态物件，初始可能有绕 **x,y** 轴旋转」← 连 z 都没提
#   * ``[23] horizontal_angle`` = 「左右旋转目标弧度」              ← 偏航（§17 已用）
#   * ``[24] vertical_angle``   = 「**上下旋转目标弧度**」           ← **抬头就是这条**
#
# ⭐⭐⭐ 公式（与云梯 ``θ = asin(墙高 / 梯长)`` **同一套「用地图数据 + 公式」**）：
#
#       θ = atan2(墙顶 z − 车 z , 车到墙的**水平**距离)
#
#   即「把梯子**对准车正前方的城墙顶**」⇒ 梯子不再扎进墙里、末端正好落在墙头。
#   ⚠️ 云梯那条是 ``asin(Δz/梯长)``（梯长已知 ⇒ 梯顶**恰好**搭在墙上）；
#      攻城车的梯长**不在任何数据里**（``ccobject.json`` 的 ``kv`` 只有动画时长、
#      ``攻城器械.psheet`` 攻城车行的几何**全是 0**）⇒ 只能「对准墙顶」，
#      即 ``atan2(Δz, Δh)`` —— 梯长够长时梯顶就落在墙顶。
#
# 输入（**全部来自场景数据，不写死**）：
#   * 车 z / 停位 = ``carPathGoal(item)``（``ccobject.json`` 的 ``path.pos`` **终点**）
#   * 航向 = ``path`` 末段方位 ``atan2(dy, dx)``
#   * 城墙 = ``data/scene/<场景>/acollision.json`` 的**竖直墙面格**
#     （``note``：「竖直面 nz<=0.35；世界三角面 45580 面 → 墙面格 18177」）
#     每格是 ``[i, j, [[z_lo, z_hi], ...]]`` ⇒ ``x = x0 + i*cell``、``y = y0 + j*cell``
#     ⚠️ **必须用 ``acollision.json``，不能用 ``heightfield.json``** ——
#        后者是「可行走地面」，城墙是竖直岩壁，**格子里根本没有**（§16 已踩过）。
#   * 取**航向 ±60°、80m 内最近**的「段顶 ≥ 50」的格子（矮坎/台阶不算城墙）
#
# 实测（樊城 tszz，车 rid 10007，停位 (536.82, 589.0, 44.12)，航向 −9.66°）：
#   正前方 10.6m 处墙面格 (547, 592)，墙顶 z = **57.85** ⇒ Δz = +13.73
#   ⇒ **θ = atan2(13.73, 10.6) = 52.3°**（此前手填的 15° 差得远 ⇒ 用户「角度不够」）。
#
# ⚠️ 正值朝上 / 朝下**未实测** —— 若实机发现是**低头**，``car_pose_vangle_flip=on``。
CAR_WALL_MIN_TOP = 50.0        # 墙面段顶 ≥ 此值才算「城墙」（矮坎 / 台阶不算）
CAR_WALL_MAX_RANGE = 80.0      # 只在 80 m 内找
CAR_WALL_CONE_DEG = 60.0       # 只在「航向 ± 此角」的扇形里找
CAR_POSE_VANGLE_FALLBACK_DEG = 15.0    # 算不出来时的兜底（保守小角度）

_ACOLLISION_CACHE = {}         # scene -> (meta, walls)；600KB，别每次读
_CAR_WALL_CACHE = {}           # (scene, pathId) -> 俯仰角(度) / None


# ---- 城墙判定阈值（2026-09-29 晚）--------------------------------------
# ⭐ 根因：CAR_WALL_MIN_TOP 硬编码 50.0 是按**樊城**城墙顶 55.6 调的。
#   江陵城（yjc_low）城墙顶只有 ~33-36m ⇒ 50 这道门槛把它的墙**全滤掉**，
#   ``carWallPitchDeg`` 恒 ``None`` ⇒ 俯仰永远回落 15° 兜底 ⇒ 「攻城车方向不对」。
#   修法 = 把阈值变成旋钮 + 按场景覆盖表（``car_wall_min_top_table=yjc_low:30``），
#   全局默认仍 50（樊城/洛阳/玉门行为逐位不变）。
def carWallMinTopRaw():
    """全局默认城墙阈值（米）。默认 ``50.0``（= 原硬编码 ``CAR_WALL_MIN_TOP``）。"""
    return _num("car_wall_min_top", "T7_CC_CAR_WALL_MIN_TOP", 50.0)


def _carWallMinTopTable():
    """``{scene: 阈值}`` —— 解析 ``car_wall_min_top_table=yjc_low:30,xxx:25``。

    与 ``_cropTable`` 同款：整表替换，``off`` / 空 ⇒ 关表。
    """
    raw = str(_text("car_wall_min_top_table",
                    "T7_CC_CAR_WALL_MIN_TOP_TABLE", "")).strip()
    if raw.lower() in ("", "off", "none"):
        return {}
    table = {}
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        key, _, value = part.partition(":")
        try:
            table[key.strip()] = float(value.strip())
        except (TypeError, ValueError):
            continue
    return table


def carWallMinTop(scene=None):
    """该场景的城墙判定阈值（米）：**场景表 > 全局默认**。"""
    if scene:
        value = _carWallMinTopTable().get(str(scene))
        if value is not None:
            return value
    return carWallMinTopRaw()


def _acollisionWalls(scene):
    """``data/scene/<scene>/acollision.json`` → ``(meta, walls)``。

    ``meta`` = ``{"cell", "x0", "y0"}``；``walls`` = ``{(i, j): (z_top, z_bottom)}``，
    只保留**城墙段**（段顶 ≥ ``CAR_WALL_MIN_TOP``）。读不到 ⇒ ``(None, {})``。

    ⚠️ 结果**按场景缓存**（600 KB 的 JSON + 1.8 万格，每拍读一次会卡）。
    """
    if scene in _ACOLLISION_CACHE:
        return _ACOLLISION_CACHE[scene]
    meta, walls = None, {}
    try:
        path = os.path.join(ccobject._sceneDir(), scene, "acollision.json")
        with open(path, "r", encoding="utf-8-sig") as handle:
            doc = json.load(handle)
        meta = {"cell": float(doc.get("cell") or 1.0),
                "x0": float(doc.get("x0") or 0.0),
                "y0": float(doc.get("y0") or 0.0)}
        for i, j, spans in doc.get("cells") or ():
            tops = [(float(lo), float(hi)) for lo, hi in spans
                    if float(hi) >= carWallMinTop(scene)]
            if tops:
                walls[(int(i), int(j))] = (max(t for _l, t in tops),
                                           min(l for l, _t in tops))
    except Exception:  # noqa: BLE001 —— 附加能力，读不到就回落兜底角度
        meta, walls = None, {}
    _ACOLLISION_CACHE[scene] = (meta, walls)
    return meta, walls


def _norm180(angle):
    """把角度折到 ``(−180, 180]``。"""
    while angle > 180.0:
        angle -= 360.0
    while angle <= -180.0:
        angle += 360.0
    return angle


def carWallPitchDeg(item, scene=None):
    """按几何算架梯俯仰角（度）：``θ = atan2(墙顶 z − 车 z, 到墙水平距离)``。

    返回 ``None`` ⇒ 算不出来（没 ``acollision.json`` / 没 ``path`` / 附近没城墙）
    —— 调用方回落 ``CAR_POSE_VANGLE_FALLBACK_DEG``。

    ⚠️ 纯读数据 + 缓存，**无副作用**；结果按 ``(scene, pathId)`` 缓存。
    """
    if not scene:
        return None
    try:
        path_id = int(item.get("pathId"))
    except (TypeError, ValueError, AttributeError):
        return None
    key = (scene, path_id)
    if key in _CAR_WALL_CACHE:
        return _CAR_WALL_CACHE[key]

    result = None
    try:
        goal = carPathGoal(item)
        walls = _acollisionWalls(scene)[1]
        if goal is not None and walls:
            points = ((item.get("path") or {}).get("pos") or [])
            if len(points) >= 2:
                heading = math.atan2(float(points[-1][1]) - float(points[-2][1]),
                                     float(points[-1][0]) - float(points[-2][0]))
            else:
                heading = float((item.get("face") or (0.0, 0.0, 0.0))[2])
            meta, _ = _acollisionWalls(scene)
            cell = meta["cell"]
            best = None
            for (i, j), (top, _bottom) in walls.items():
                x = meta["x0"] + i * cell
                y = meta["y0"] + j * cell
                dx = x - float(goal[0])
                dy = y - float(goal[1])
                dist = math.hypot(dx, dy)
                if dist < 1e-6 or dist > CAR_WALL_MAX_RANGE:
                    continue
                if abs(_norm180(math.degrees(math.atan2(dy, dx))
                                - math.degrees(heading))) > CAR_WALL_CONE_DEG:
                    continue
                if best is None or dist < best[0]:
                    best = (dist, top)
            if best is not None:
                result = math.degrees(math.atan2(best[1] - float(goal[2]), best[0]))
    except Exception:  # noqa: BLE001 —— 附加能力，算不出来就回落
        result = None
    _CAR_WALL_CACHE[key] = result
    return result


def carPoseVangleRaw():
    """``car_pose_vangle`` 的**原始文本**：``auto``（默认）/ 纯数字（度）。"""
    return str(_text("car_pose_vangle", "T7_CC_CAR_POSE_VANGLE", "auto")).strip().lower()


def carPoseVangleFlip():
    """``auto`` 算出来的角**取负**（应急：实机发现是低头时用）。默认 off。"""
    return _flag("car_pose_vangle_flip", "T7_CC_CAR_POSE_VANGLE_FLIP", False)


def carPoseVangleFallbackDeg():
    """``auto`` 算不出俯仰角（场景**没有** ``acollision.json``）时的**兜底角度**（度）。

    默认 ``15.0``（与原硬编码 ``CAR_POSE_VANGLE_FALLBACK_DEG`` 逐位相同）。

    ⭐ 只对**缺几何的场景**生效：有 ``acollision.json`` 的场景（樊城 ``tszz`` /
    洛阳 ``lysd`` / 玉门 ``hz_map_b``）仍走 ``carWallPitchDeg`` 现算，
    永远不会落到这条。所以把这条调大，**只改「没有城墙几何」的图**
    （如江陵城 ``yjc_low``），不会污染其它图 —— 这正是 2026-09-29 江陵城
    「攻城车角度不对、樊城却是对的」的根因修复点。
    """
    try:
        return float(_num("car_pose_vangle_fallback",
                          "T7_CC_CAR_POSE_VANGLE_FALLBACK", 15.0))
    except (TypeError, ValueError):
        return 15.0


def carPoseVangleDeg(item=None, scene=None):
    """架梯**俯仰角（度）**。

    * ``auto``（默认）⇒ ``carWallPitchDeg(item, scene)``（**按地图数据现算**）；
      算不出来（场景没 ``acollision.json``）/ 没给 ``item`` ⇒
      ``carPoseVangleFallbackDeg()``（**可配**，默认 15°）
    * 纯数字 ⇒ 强制该角度（``0`` = 关，梯子回水平）
    * 坏值（配错的数字）⇒ **当 ``auto`` 处理**（算不出来再兜底）
    * ``car_pose_vangle_flip=on`` ⇒ ``auto`` 的结果取负
    """
    raw = carPoseVangleRaw()
    if raw not in ("auto", "kv", "on", ""):
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass          # 坏值 ⇒ 当 auto 处理（下面那条分支）
    if item is None:
        return carPoseVangleFallbackDeg()
    deg = carWallPitchDeg(item, scene)
    if deg is None:
        return carPoseVangleFallbackDeg()
    return -deg if carPoseVangleFlip() else deg


def carPoseVangle(item=None, scene=None):
    """架梯俯仰角（**弧度**）—— 下发进 ``vertical_angle``。"""
    return math.radians(carPoseVangleDeg(item, scene))


def carPoseVangleOn(item=None, scene=None):
    """是否真的下发（角度为 0 就不发 ⇒ 该字段保持 ``0.0``）。"""
    return abs(carPoseVangleDeg(item, scene)) > 1e-9


def carPoseRotxDeg():
    """**备用**通道：加进 ``rotation.x`` 的俯仰角（度）。默认 **0**（关，逐位不变）。"""
    try:
        return float(_text("car_pose_rotx", "T7_CC_CAR_POSE_ROTX", "0").strip())
    except (TypeError, ValueError):
        return 0.0


def _curScene(flow):
    """当前场景目录名（``controls.airWallScene(flow)`` 的包装）。拿不到 ⇒ ``None``。

    ⚠️ ``airWallScene`` 在 **``controls``** 里，**不是** ``ccobject``（2026-09-29
    18:5x 踩过：写成 ``ccobject.airWallScene`` ⇒ 恒 ``None`` ⇒ 俯仰角永远回落兜底）。
    懒导入是为了避开与 ``controls`` 的循环依赖。
    """
    try:
        from . import controls
        return controls.airWallScene(flow)
    except Exception:  # noqa: BLE001
        return None


def carPoseYaw(heading, offset_deg=None):
    """把**世界航向**（弧度，``atan2(dy, dx)``）换算成 CC ``rotation.z``。

    ``θ = 航向 + offset``（默认 offset = ``car_pose_yaw_offset`` = ``−20°``，
    推导见 ``carPoseYawOffset``）；``car_pose_yaw_flip=on`` 时先把航向取负
    （应急整体反向）。

    ⚠️ 纯函数、无副作用 —— 这样测试可以直接验公式，不必去翻 CC 字典。
    """
    yaw = float(heading)
    if carPoseYawFlip():
        yaw = -yaw
    off = carPoseYawOffset() if offset_deg is None else float(offset_deg)
    return yaw + math.radians(off)


def carPathGoal(item):
    """该车 ``path.pos`` 的**终点** —— 场景设计者预定义的攻城路线终点。没有 ⇒ ``None``。

    ⭐⭐⭐ 2026-09-29：这是**正解**。``ccobject.json`` 里每架攻城车都自带一条
    3 点导航路径 ``path.pos`` / ``path.face``（= 它自己的「路」）：

    | 车 | pathId | 起点 | **终点** |
    |---|---|---|---|
    | **攻城车2（右路）** | 10017 | (523.85, 366.53) | **(527.03, 403.46)** |
    | **攻城车1（左路）** | 10026 | (447.11, 366.58) | **(428.89, 398.26)** |

    用户实机指出：「车应该向**人物正前方这个缺口**移动，**大门在图片的左手边**，
    这个是**右路**攻城车」。而之前 ``car_target_rid=auto`` 找的是**正门**
    （车2 → (477.39, 419.57)，方向 131.2° 西北）⇒ 正好朝**左前方**走，
    与「缺口在正前方（85.1° 北）」差了 46° —— 用户看到的就是这个错。

    ⚠️ 3 个点近似一条直线（车2：x 523.85→527.03、y 366.53→403.46），
       所以**直接朝终点走**即可，不必逐点走。
    """
    if not isinstance(item, dict):
        return None
    path = item.get("path")
    if not isinstance(path, dict):
        return None
    pts = path.get("pos")
    if not isinstance(pts, (list, tuple)) or not pts:
        return None
    last = pts[-1]
    try:
        return [float(last[0]), float(last[1]), float(last[2])]
    except (TypeError, ValueError, IndexError):
        return None


def carTargetResolved(flow):
    """真正要走的 rid；找不到 ⇒ ``None``。

    ⚠️ **为什么必须有 auto**：``car_target_rid`` 原来写死 **10002** —— 那是
    **樊城**的正门；而**江陵城**的 10002 是**投石车1**（正门是 **10010**）。
    两个场景的 rid 空间**完全独立**（``rid = 10000 + 数组下标``，见
    ``ccobject.ridFor``），所以写死 rid **必然**在别的图指错目标。
    实机症状：车朝投石车走（或目标不存在 ⇒ 干脆不动）。

    查找顺序（全部按**场景数据**，不写死 rid）：
      ① ``tid == CAR_GATE_TID``(3) —— 樊城正门；
      ② 名字**恰好**是「正门」—— 江陵城正门（tid=152）；
      ③ 名字**含**「正门」—— 兜底。
    """
    if not carTargetAuto():
        rid = carTargetRid()
        return rid if _object(flow, rid) else None
    records = _records(flow)
    for rid, (_index, item) in records.items():
        try:
            if int(item.get("tid")) == CAR_GATE_TID:
                return rid
        except (TypeError, ValueError):
            continue
    for exact in (True, False):
        for rid, (_index, item) in records.items():
            name = str(item.get("name") or "")
            if (name == "正门") if exact else ("正门" in name):
                return rid
    return None


def carMoveState():
    """``MOVE_MO_BC.state`` —— ⚠️ **不是** ``E_MO_STATE_*``，是**移动状态档**。

    自检逼出来的：该字段 TDR 类型是 ``uchar``（0..255），而
    ``E_MO_STATE_ATTACK_CITY_CAR_MOVE = 1021`` **塞不进去** ⇒ 二者不是一个命名空间。
    与 38/35 号那族（移动档 0..255）同族。
    默认 ``2`` —— prior_art ``动作.txt`` 六 里用户现场确认过的「向前慢走」档
    （``state=2, LR=0, FB=+1000, cv=1000``）。
    ⚠️ MO（载具）这一侧用哪个档**未实测** ⇒ 做成旋钮，实机不对就改这里。
    """
    return max(0, min(255, int(_num("car_move_state", "T7_CC_CAR_MOVE_STATE", 2, int))))


def carOpenLadder():
    """车到位后**自动架梯**（把车上的梯子搭到城墙上）。默认 **on**。

    ⭐⭐⭐ 2026-09-29 16:1x：用户需求「车移动到位之后，梯子会自动搭到城墙上」。

    机制 = **发 ``MO_Siege_Opening``(1022) 让客户端播架梯动画**，动画播完
    再发 ``MO_Siege_Opened``(1023) 收尾 —— 与云梯的 ``1002→1003`` 是**同一套**
    （客户端原生动画，服务端只负责起止两条状态）。

    出处（``data1.vfs`` 的 MO 状态名池，blk 121169）::

        1020  MO_Siege_Idle       ← CAR_IDLE
        1021  MO_Siege_Move       ← CAR_MOVE（移动）
        1022  MO_Siege_Opening    ← 架梯中
        1023  MO_Siege_Opened     ← 架梯完成
        1024  MO_Siege_Dead

    ⚠️ ``onCarState`` 只对 ``1021``(起步) / ``1020``(停车) 反应 ⇒ 1022/1023
    不会再触发驱动链，不会成环。

    回退：``[cc] car_open_ladder=off``（车到位后停在原地，不架梯）。
    """
    return _flag("car_open_ladder", "T7_CC_CAR_OPEN_LADDER", True)


# kv 取不到时的兜底（= 三辆车的 ``total_animation_time_ms`` 实测值，见 ``carOpenMsFor``）
CAR_OPEN_MS_FALLBACK = 2000


def carOpenMsRaw():
    """``car_open_ms`` 的**原始文本**：``auto``（默认）/ 纯数字（毫秒）。"""
    return str(_text("car_open_ms", "T7_CC_CAR_OPEN_MS", "auto")).strip().lower()


def carOpenMsFor(anim=None):
    """解析出**实际**的架梯收尾延时（毫秒）。

    ⭐⭐ 2026-09-29 17:3x：默认改为 **``auto``** —— 用**该车自己的 kv 动画时长**
    （``anim``，来自 ``mo.animationMsFor`` ⇒ ``mo._animMsOfRecord``，
    优先 ``total_animation_time_ms``），与云梯的 ``CropTime``
    （``ladderCropTimeFromKv`` = ``(total − animation)/1000``）是**同一套
    「从 kv 现算」纪律**。

    ⚠️⚠️ 为什么必须改（旧默认写死 **1540** 是 bug）：

    * 1540 抄的是**樊城车的 ``kv.animation_time_ms``** —— 那是「**按住 C 的读条时长**」，
      **不是动画总长**。三辆车的 ``total_animation_time_ms`` 都是 **2000**。
    * 只等 **1540/2000 = 77%** 就发 ``1023`` ⇒ 客户端把架梯动画**截断**
      ⇒ **梯子没伸到位 / 没搭到缺口上**。
    * 用户实机口供（17:2x，带图）：「**樊城梯子，没搭到缺口上**」+
      「**是不是用之前的公式算一下**」⇒ 就是要**按 kv 现算**。

    取值：
      * ``auto``（默认）⇒ ``anim``（该车 kv 现算）；``anim`` 未知 ⇒ ``CAR_OPEN_MS_FALLBACK``
      * 纯数字 ⇒ 强制该毫秒数（想手动加余量就写 ``2200``）
      * ``0`` ⇒ 立刻发 1023（无过渡，梯子瞬移到位）

    ⚠️ 纯函数（除读旋钮外无副作用）—— 测试可以直接验公式，不必搭 flow。
    """
    raw = carOpenMsRaw()
    if raw in ("auto", "kv", "on", ""):
        if anim:
            return max(0, min(20000, int(anim)))
        return CAR_OPEN_MS_FALLBACK
    try:
        return max(0, min(20000, int(float(raw))))
    except (TypeError, ValueError):
        return CAR_OPEN_MS_FALLBACK


def carOpenMs():
    """不带 ``anim`` 的解析结果（``auto`` ⇒ 兜底 ``CAR_OPEN_MS_FALLBACK``）。

    ⚠️ 真正下发延时的地方是 ``carOpenLadderDeploy``，它调的是
    ``carOpenMsFor(anim)`` —— **别在别处用本函数算延时**，否则会丢掉逐车 kv。
    本函数只给 ``report()`` / 测试看「当前模式解析成多少」。
    """
    return carOpenMsFor(None)


def carAutoEnabled():
    """「靠近自动起步」总开关（③ 的**触发条件**）。默认 **off**。

    用户原话是「人物**靠近**攻城车，攻城车会自动往前走」—— 触发源是**距离**，
    不是按 C。代码已就位但默认关着：等 ①（云梯）实机确认后再开，
    守住「一次只动一个变量」。
    """
    return _flag("car_auto", "T7_CC_CAR_AUTO", False)


def carTriggerRadius():
    """触发半径（米，XY 平面）。默认 5.0 —— 攻城车踏板宽约 4m，5m 够「贴上去」。"""
    return max(0.5, min(50.0, _num("car_trigger_radius", "T7_CC_CAR_TRIGGER_RADIUS", 5.0)))


def carScanEvery():
    """每几个 ``ground-step`` 扫一次距离（默认 5 ⇒ 20Hz/5 = 4Hz）。

    ⚠️ **必须有节流**：``ccobject.loadScene()`` 每调一次就真读一遍 JSON（无缓存），
    而 ``_records()`` 每调一次就会读盘一次。人走 4~6 m/s，4Hz 下每拍位移 1~1.5m，
    5m 半径绰绰有余 —— 没必要 20Hz 读盘。
    """
    return max(1, min(200, int(_num("car_scan_every", "T7_CC_CAR_SCAN_EVERY", 5, int))))


# ============================ 数据查找 ======================================
def _records(flow):
    """``ccobject.json`` 原始记录 → ``{rid: (index, item)}``（读盘失败返回 {}）。"""
    try:
        from . import controls
        items = ccobject.loadScene(controls.airWallScene(flow))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}
    return {ccobject.ridFor(index): (index, item)
            for index, item in enumerate(items, start=1)}


def _object(flow, rid):
    """``rid`` → ``(index, item)``；不是 CC 物件时 ``None``。"""
    return _records(flow).get(int(rid))


def _instanceId(item, index):
    """MO 的 inst_id —— MOVE_MO_BC 要的是这个（uint16），**不是 rid**。

    与 ``ccobject.objects()`` 同一套规则：``pathId`` 低 16 位，缺字段退回序号。
    """
    try:
        return int(item.get("pathId", index)) & 0xFFFF
    except (TypeError, ValueError):
        return index & 0xFFFF


def _log(flow, text):
    try:
        flow.result["logs"].append(text)
    except (AttributeError, TypeError):
        pass


# ============================ ① 云梯立起 ====================================
def _tiltName(rid):
    return "%s%d" % (_TILT_PREFIX, int(rid))


def _rbName(rid):
    """``skip_inair_rb`` 的收尾定时器名（动画播完 → 补位姿）。"""
    return "%s%d" % (_RB_PREFIX, int(rid))


def _orgName(rid):
    """``incline_org`` 的收尾定时器名（动画播完 → 重发 CC 补位置 + 朝向）。"""
    return "%s%d" % (_ORG_PREFIX, int(rid))


def _applyOrgPose(flow, rid, item):
    """把该架云梯的**模型原点** + **朝向**钉到立起位姿，并**重发该架 CC 物件**。

    两条都塞在**同一份**重发里，因为客户端收到 CC 物件时会**同时采纳** ``pos``
    和 ``rotation``（§10.1）—— 只给位置会让梯子「水平悬空」（16:32 实机）。

    返回 ``(origin, tilt, sent)``；``tilt is None`` 表示该架没配俯仰（不改朝向）。
    """
    x, y, z = (float(v) for v in (item.get("pos") or (0.0, 0.0, 0.0)))
    face = item.get("face") or (0.0, 0.0, 0.0)
    origin, _quat = _ladderPose((x, y, z), float(face[2]), ladderAngle(), "foot")
    ccobject.setDynamicOrigin(rid, origin)
    # ⭐ 朝向也要一起给：重发 CC 会把姿态**重置**成记录里的 ``rotation``，
    #   而 ``face`` 是**躺地**朝向 ⇒ 只给位置会让梯子「水平悬空」。
    #   见 ``ccobject._DYNAMIC_ROTATION`` 的注释块。
    tilt = ladderOrgTiltFor(item)
    if tilt is None:
        ccobject.clearDynamicRotation(rid)
    else:
        ccobject.setDynamicRotation(rid, (float(face[0]), float(tilt), float(face[2])))
    return origin, tilt, _resendCcObject(flow, rid)


def _tiltStates():
    """哪些 MO 状态该把梯子**推到立起角** —— 见模块头 ``ladder_state`` 开关。

    ⚠️ 2026-09-24：旧实现返回 ``(1000, 1006, 1007)`` —— 里面 1000 是**躺地**、
    1006/1007 是**哨塔**，三个都不对，所以「推倾角」这件事从来没在正确的时刻发生过。

    * ``up_only``   —— 只认 1002(InAir) / 1003(Standing)，最保守
    * ``up_settle`` —— 再加 1004(InAir_Occupied)：有人爬梯时梯子也得立着（默认）
    """
    if ladderStateMode() == "up_only":
        return (LADDER_INAIR, LADDER_STANDING)
    return (LADDER_INAIR, LADDER_STANDING, LADDER_INAIR_OCCUPIED)


# ============================ ④ 云梯攀爬面（2026-09-29）============================
# 用户口供：「梯了可以立起来了，，现在是穿过去，，怎么能上去」。
#
# 为什么这么做能成（三个前提，都核对过源码，不是猜的）：
#   ① 人物位置**由服务端积分**：``controls.advanceGround()`` 写 X/Y，随后
#      ``groundZFollow()`` 把 Z 拉到 ``groundZAt(x, y)`` 的可站立高度。
#      ⇒ 只要让 ``groundZAt`` 在梯子覆盖处返回**斜面高度**，人就会沿梯面升上去。
#   ② 上行闸门放行：``climbBlocked()`` 的判据是 ``groundZAt(new) − 角色实际Z > 5.5``。
#      走路上坡一步水平 ≈0.05 m、坡度 ≈1.0 ⇒ 每步只抬 ≈0.05 m ⇒ 远低于闸门。
#      （**必须连续**：做成台阶式跳变就会被挡在下面。）
#   ③ 客户端**不需要任何新报文** —— 梯子的「立起」已由 crop 通道做完，
#      这里只是**让地面高度认识那个斜面**。⇒ 零协议风险、可 10 秒回退。
#
# 几何（与 ``_ladderPose`` 同一套模型，见它的 docstring）：
#   梯脚  F = pos + foot·d      d = (cos yaw, sin yaw)，foot = LADDER_FOOT_X(−8.038)
#   沿梯单位水平方向 u = d；立起角 θ = ladderAngle()
#   水平投影 run = L·cosθ，升高 rise = L·sinθ（L = LADDER_LENGTH = 16.008）
#   ⇒ 爬升面 = 「从 F 沿 u 走 t ∈ [0, run]，高度 = F.z + rise·(t/run)」，横向半宽 half。
#   ⚠️ θ→90° 时 run→0（坡面退化成一条线，人走不上去）⇒ 用 ``ladder_climb_run_min``
#      兜底（默认 2.5 m）。樊城当前 θ = 0.7905 rad(45.3°) ⇒ run = 11.26 m，不触发。
#
# ⚠️ 梯顶**之后**：默认再给 ``ladder_climb_cap`` 米等高平台（默认 1.2 m），
#    让人在顶上站得住。梯子若立在空地上（樊城就是），梯顶外面是地面 ⇒ 走出平台会掉，
#    而 ``ledgeBlocked``(LEDGE_MAX_DROP=8.0) 挡住 >8 m 的掉落 ⇒ 人留在顶上，
#    想下来按住方向 0.4 秒即可（它自带兜底）。**想「上城墙」得先把梯子挪到墙边**
#    （改场景 ``pos``），那是另一件事。
#
# 存储：``flow.session[CLIMB_FACES_KEY]`` 一个**字符串**
#   ``"rid,fx,fy,fz,ux,uy,run,rise,half,cap;rid,..."``
#   ⚠️ 为什么用字符串而不是 list/dict/tuple：``flow.session`` 会被原生层序列化并做
#      类型校验，**只允许 JSON 可表达的类型**（``battle.py`` 有记载）；``mo.py`` /
#      本项目为此记过两次事故（int 作 key 的 dict、tuple）。字符串是最省事的安全解。
#   ⚠️ ``controls.climbFaceZ()`` 读**同一个键**（键名必须与 ``controls.CLIMB_FACES_KEY``
#      相等 —— 测试里有一条断言盯着两边）。controls **刻意不 import siege**。
CLIMB_FACES_KEY = "climbFaces"

CLIMB_FACE_FIELDS = 10          # rid + 9 个标量


def ladderClimbEnabled():
    """旋钮 ``ladder_climb``（env ``T7_CC_LADDER_CLIMB``）。**默认 off**。"""
    return _flag("ladder_climb", "T7_CC_LADDER_CLIMB", False)


def ladderClimbWidth():
    """爬升面横向**半宽**（米）。默认 1.2 ⇒ 走道宽 2.4 m。"""
    return _num("ladder_climb_width", "T7_CC_LADDER_CLIMB_WIDTH", 1.2)


def ladderClimbCap():
    """梯顶等高平台长度（米）。``0`` = 不封顶。默认 1.2。"""
    return _num("ladder_climb_cap", "T7_CC_LADDER_CLIMB_CAP", 1.2)


def ladderClimbRunMin():
    """``run`` 下限（米）。θ 接近 90° 时 ``L·cosθ`` 趋于 0，用它兜底。默认 2.5。"""
    return _num("ladder_climb_run_min", "T7_CC_LADDER_CLIMB_RUN_MIN", 2.5)


def ladderClimbRun():
    """``run`` 的强制覆盖（米）。``0`` = 用几何算（默认）。"""
    return _num("ladder_climb_run", "T7_CC_LADDER_CLIMB_RUN", 0.0)


def ladderClimbRise():
    """``rise`` 的强制覆盖（米）。``0`` = 用几何算（默认）。"""
    return _num("ladder_climb_rise", "T7_CC_LADDER_CLIMB_RISE", 0.0)


# ⭐⭐⭐ 2026-10-07：回落路径专用长坡旋钮（找墙不命中时生效）。
#
# 实测依据（樊城 tszz 第三十一轮 ``ladder-climb-probe`` 点云 + acollision 标定）：
#   * 云梯1（pid 10015，不在倾斜表）的墙与梯轴**平行斜贴**（t=+18..26、横向
#     n=+3.9~5.9，每桶 1~3 格）—— 严格判据（格数≥4 且横向跨度≥4m）永不命中，
#     只能走回落；而旧回落（``LADDER_FOOT_X + shift=16.008``、45° 几何
#     run=11.26）的坡在 t∈[+7.97,+19.2]、用户探测点全在梯身 t'∈[-9.6,-3.5]
#     —— **一个都罩不住**，且坡顶 n=0 处是空地（真墙在横向 4.7m 外）。
#   * 用户实机行为：按 C 立起后**沿梯身走、在梯身 ±3m 内试爬**，不会走 8m
#     去找坡脚。
# ⇒ 回落坡改为「躺地梯脚 → 墙带」长坡：foot 钉在模型梯脚（-8.038），
#    ``fb_run`` 给总长（28 = 顶到 t≈+20 墙带），``fb_rise`` 给总升（13.0 =
#    墙顶 55.63 − foot_z 42.95 + 0.45 余量）。默认 0/0 = 旧行为逐位不变。
def ladderClimbFbRun():
    """回落坡总长（米）。``0`` = 旧行为（45° 几何）。默认 0。"""
    return _num("ladder_climb_fb_run", "T7_CC_LADDER_CLIMB_FB_RUN", 0.0)


def ladderClimbFbRise():
    """回落坡总升（米）。``0`` = 旧行为（45° 几何）。默认 0。"""
    return _num("ladder_climb_fb_rise", "T7_CC_LADDER_CLIMB_FB_RISE", 0.0)


# ⭐⭐ 2026-10-07 14:5x：落地段两个旋钮（实机「一上去就卡进墙里/掉进去，上不到二层」）。
#   会话 -7 实测：用户沿坡爬到顶（z=54.87，face=54.76，why=reached ✓）但坡在墙脸
#   处结束、墙体 footprint 有 1-2m 厚 —— 跨越时余量只有 0.4m（坡顶 54.98 vs 墙顶
#   走道 54.58），客户端碰撞把人推进墙体 ⇒ 掉进墙里卡住。
#   * ``cross_ext`` —— 坡越过墙脸再延伸多少米（跨越 footprint 期间仍有支撑）；
#   * ``land_clear`` —— 坡顶高出墙顶走道多少米（余量加大，落上去不磕头）。
#   默认 0 / 0.4 = 修复前行为逐位不变。
def ladderClimbCrossExt():
    """坡越过墙脸的延长（米）。默认 0。"""
    return _num("ladder_climb_cross_ext", "T7_CC_LADDER_CROSS_EXT", 0.0)


def ladderClimbLandClear():
    """坡顶高出墙顶走道的余量（米）。默认 0.4。"""
    return _num("ladder_climb_land_clear", "T7_CC_LADDER_LAND_CLEAR", 0.4)


# ⭐⭐⭐ 2026-09-29 19:5x：爬升面的**平移**旋钮（用户实机「斜坡和梯子一个方向、
# 位置不对，移过去就对上了」）。
#
# 为什么需要：爬升面的梯脚按 ``LADDER_FOOT_X = -8.038``（模型局部 X 的**端点**，
# 由 ``nif_verts.py`` 实测包围盒得来）算，但客户端那架云梯是 **Havok 动画**
# （``SiegeEngines.hkt`` 的 Open/Opened）驱动的 —— 动画绕哪个点转、模型原点在不在
# 梯脚，**服务端无从得知**（hkx 里只有 ``hkbCharacterData``/``hkbBehaviorGraph``
# 结构，没有动画轨道）。所以「几何算出来」的坡与「眼睛看到」的梯子差一个平移。
#
# 这个平移**算不出来，只能量**：给三个沿坡局部坐标的平移量，现场一格一格调。
#   * ``shift`` —— 沿梯方向 ``u``（+ 朝梯顶 / − 朝梯脚）
#   * ``side``  —— 沿法向 ``(-uy, ux)``（坡的左右）
#   * ``drop``  —— 竖直 ``z``（+ 抬 / − 降）
# 三者都默认 ``0``（现状不变），改了才动。
#
# ⚠️ 这三个只改 ``foot``（梯脚坐标），**不动** ``run/rise/half/cap`` ⇒
#    ``CLIMB_FACE_FIELDS`` 仍是 10，``controls.climbFaceZ`` 一行都不用改。
def ladderClimbShift():
    """沿梯方向平移（米）。``+`` 朝梯顶，``−`` 朝梯脚。默认 ``0``。"""
    return _num("ladder_climb_shift", "T7_CC_LADDER_CLIMB_SHIFT", 0.0)


def ladderClimbSide():
    """沿坡法向平移（米）。默认 ``0``。"""
    return _num("ladder_climb_side", "T7_CC_LADDER_CLIMB_SIDE", 0.0)


def ladderClimbDrop():
    """竖直平移（米）。``+`` 抬高，``−`` 降低。默认 ``0``。"""
    return _num("ladder_climb_drop", "T7_CC_LADDER_CLIMB_DROP", 0.0)


def _ladderWallFace(scene, x, y, ux, uy, run, top_z):
    """在场景 ``acollision`` 里沿梯轴找「这架梯子靠的那面墙」。

    返回 ``(face_t, dir, wall_top)``：``face_t`` = 墙脸在轴线上的距离（从梯子
    原点 pos 起，``+u`` 为正）；``dir`` = 梯子往哪边立（``+1`` 顶朝 ``+u`` /
    ``-1`` 顶朝 ``-u``）；``wall_top`` = 命中桶的墙顶中位数（米，2026-10-07 加，
    供 ``ladderClimbFace`` 把 ``rise`` 定到「坡顶高出墙顶走道半格」）。
    找不到 ⇒ ``None``（调用方回落旧公式）。

    ⭐ 为什么必须找：有的梯子顶朝 +u 立、有的朝 **-u**（对上了
    ``ladder_org_tilt_table=40028:-0.7905, 40040:+0.7905`` 两个相反倾斜号）——
    云梯4 的墙只在 -u 侧、江陵城三架也只在 -u 侧。爬升面原先写死沿 +u 延伸
    ⇒ 这些梯子的坡「伸向旷野」，怎么平移都不对。

    ⭐⭐ 决胜规则（2026-09-29 24:0x 用数据定案）：候选桶按
    **(格数最多, 墙顶离坡顶最近, |t| 最小)** 排序。
    - 格数主判据保证稳定（云梯4 −19 有 12 格最多）；
    - **墙顶高度差**是破平手的关键：云梯3 两侧都有墙（-16 外墙 10 格 vs
      +20 内墙 10 格，格数平手），但 +20 的墙顶 54.58 与坡顶 54.87 只差
      **0.29m**（-16 差 4.98m）—— 梯顶搭墙顶正是云梯角度公式
      ``θ = asin(墙高/梯长)`` 的设计意图；且用户实机验证过反转版坡顶
      t=+19.2 离 +20 墙脸仅 0.8m（几乎贴上）⇒ 云梯3 的目标墙就是 +20，
      -16 是它**身后**的外墙。
    判据：1m 一档沿轴分桶（横向 ±6m），墙脸桶 = 格数 ≥4 且横向跨度 ≥4m
    （垂直于梯轴的墙面才会在同一 t 上横向铺开）。墙脸取桶中心，
    梯脚 = 墙脸 − dir·run（坡顶正好贴墙脸）。
    """
    meta, walls = _acollisionWalls(scene)
    if meta is None or not walls:
        return None
    cell = meta["cell"]
    x0 = meta["x0"]
    y0 = meta["y0"]
    bins = {}                       # round(t) -> [(n, top, bot), ...]
    for (i, j), (top, bot) in walls.items():
        if top - bot < 5.0:         # 只要高墙（≥5m 的立面）
            continue
        dx = (x0 + i * cell) - x
        dy = (y0 + j * cell) - y
        t = dx * ux + dy * uy
        n = dx * uy - dy * ux
        if abs(n) > 6.0 or abs(t) > 26.0:
            continue
        bins.setdefault(int(round(t)), []).append((n, top, bot))
    cands = []
    for t_bin, cells in bins.items():
        ns = [c[0] for c in cells]
        if len(cells) >= 4 and (max(ns) - min(ns)) >= 4.0:
            tops = sorted(c[1] for c in cells)
            mid_top = tops[len(tops) // 2]      # 桶内墙顶中位
            cands.append((len(cells), abs(mid_top - top_z), abs(t_bin), t_bin))
    if not cands:
        return None
    # 升序元组排序 = 格数最少优先 —— 与上面的决胜规则相反（2026-09-29 24:3x 修）。
    # 用 key 显式写出意图：格数↓（多者优先）→ 高度差↑ → |t|↑。
    cands.sort(key=lambda c: (-c[0], c[1], c[2]))    # 格数↓ → 高度差↑ → |t|↑
    face_t = float(cands[0][3])
    win_tops = sorted(c[1] for c in bins[int(face_t)])
    wall_top = float(win_tops[len(win_tops) // 2])
    return face_t, (1 if face_t >= 0.0 else -1), wall_top


def _ladderWallFaceSpan(scene, x, y, ux, uy, face_t, tol=1.0):
    """命中桶的**横向范围** ``(n_lo, n_hi, n_mid)`` —— 只为日志/诊断。

    ⭐ 2026-09-30：实机「坡仍偏左 15 度」排查用。坡顶的横向位置是 ``n=0``
    （坡轴穿梯子记录位），而墙脸桶在横向可能铺 ±5m ⇒ 两者一比自己就知道
    坡顶落在墙面的左半边还是右半边、还差多少米（= 该给 ``side`` 填多少）。
    找不到 ⇒ ``None``。
    """
    meta, walls = _acollisionWalls(scene)
    if meta is None or not walls:
        return None
    cell, x0, y0 = meta["cell"], meta["x0"], meta["y0"]
    ns = []
    for (i, j), (top, bot) in walls.items():
        if top - bot < 5.0:
            continue
        dx = (x0 + i * cell) - x
        dy = (y0 + j * cell) - y
        t = dx * ux + dy * uy
        if abs(t - face_t) > tol:
            continue
        n = dx * uy - dy * ux
        if abs(n) > 6.0:
            continue
        ns.append(n)
    if not ns:
        return None
    ns.sort()
    return ns[0], ns[-1], ns[len(ns) // 2]


def ladderClimbFace(item, scene=None):
    """算一架云梯的爬升面 → 9 个 float，或 ``None``（数据坏 / 退化）。

    顺序：``fx, fy, fz, ux, uy, run, rise, half, cap``
    （``fz`` 取**记录位置**的 z —— 梯脚钉在地面，躺平记录的 z 就是梯脚高度。）

    ⭐⭐ 2026-09-29 23:5x：给 ``scene`` 时**自动找墙**（``_ladderWallFace``）——
    梯子往哪边立由墙在哪边决定，坡从「墙脸 − dir·run」处起、沿 ``dir·u`` 升，
    坡顶正好贴墙脸。``side`` 仍是横向微调；``shift`` **只属于回落公式**
    （找到墙时不读它 —— 那是全局反转值 16.008，叠上来必错）。
    ⭐⭐ 2026-10-07（第三十二轮，probe 点云定案）：
    * 命中墙时 run 拉长到 ``|face_t| + |LADDER_FOOT_X|`` —— 坡脚自动落到躺地
      梯脚端，梯身全程可踩（用户沿梯身走、在梯身 ±3m 内试爬）；rise 按
      墙顶中位数定（坡顶高出墙顶走道 0.4m），不再受 45° 假角限制。
    * 找不到墙 ⇒ ``ladder_climb_fb_run/fb_rise`` 旋钮驱动的「躺地梯脚 → 墙带」
      长坡（foot 钉回 −8.038）；fb_run=0 ⇒ 旧公式（``LADDER_FOOT_X + shift``）。
    """
    if not isinstance(item, dict):
        return None
    try:
        x, y, z = (float(v) for v in (item.get("pos") or (0.0, 0.0, 0.0)))
        yaw = float((item.get("face") or (0.0, 0.0, 0.0))[2])
    except (TypeError, ValueError, IndexError):
        return None
    angle = ladderAngle()
    ca, sa = math.cos(angle), math.sin(angle)
    ux, uy = math.cos(yaw), math.sin(yaw)          # 沿梯子的单位水平方向
    run = ladderClimbRun()
    if run <= 0.0:
        run = max(LADDER_LENGTH * ca, ladderClimbRunMin())
    rise = ladderClimbRise()
    if rise <= 0.0:
        rise = LADDER_LENGTH * sa
    # ⭐⭐ 自动找墙：墙在哪边、梯顶就朝哪边，坡顶贴墙脸。
    #    top_z = 坡顶高度（fz + rise），给决胜判据「墙顶离坡顶最近」用。
    hit = (_ladderWallFace(scene, x, y, ux, uy, run, z + ladderClimbDrop() + rise)
           if scene else None)
    if hit is not None:
        face_t, dr, wall_top = hit
        # ⚠️ **不加 shift**：坡脚由墙脸直接反推（face_t − dir·run，坡顶贴墙脸）。
        # ⭐⭐ 2026-10-07：坡从「躺地梯脚」一直铺到「墙脸」——
        #    run = |face_t| + |LADDER_FOOT_X|。实测（第三十一轮 probe 点云）：
        #    用户按 C 立起后沿**梯身**走、在梯身 ±3m 内试爬，根本不会走 8m 去
        #    找坡脚；旧 run=11.26 的坡 t∈[+8.7,+20] 对梯身上的探测点一个都罩
        #    不住。拉长后坡脚自动落到 −8.038（躺地梯脚端），坡顶仍贴墙脸。
        #    ⭐⭐ 14:5x：+ cross_ext —— 坡越过墙脸再延伸一段（实机：坡在墙脸
        #    结束 ⇒ 跨越 1-2m 厚的墙体 footprint 时失去支撑掉进墙里）。
        run = max(run, abs(face_t) + abs(LADDER_FOOT_X) + ladderClimbCrossExt())
        foot_t = face_t - dr * run
        # ⭐⭐ 2026-10-07：rise 按墙顶定（坡顶高出墙顶走道 land_clear，落上去
        #    不磕头）。显式旋钮（ladder_climb_rise > 0）优先，维持旧行为可覆盖。
        if ladderClimbRise() <= 0.0 and wall_top > 0.0:
            rise = max(rise, wall_top - (z + ladderClimbDrop())
                       + ladderClimbLandClear())
        wux, wuy = dr * ux, dr * uy                # 坡的延伸方向（= 梯顶朝向）
        _climb_dbg = (face_t, dr, wall_top)
    elif ladderClimbFbRun() > 0.0:
        # ⭐⭐ 2026-10-07：找墙不命中（樊城云梯1 的墙与梯轴平行斜贴，每桶 1~3 格）
        #    ⇒ 「躺地梯脚 → 墙带」长坡：foot 钉回模型梯脚（-8.038，旧行为的
        #    ``+shift=16.008`` 把坡脚推到 +7.97，用户探测点 t'∈[-9.6,-3.5] 全在
        #    坡后，一个都罩不住），总长/总升由 fb 旋钮给（level.ini：fb_run=28
        #    顶到 t≈+20 墙带、fb_rise=13.0 顶过墙顶 55.63）。fb_run=0 ⇒ 旧行为。
        run = ladderClimbFbRun()
        foot_t = LADDER_FOOT_X
        if ladderClimbFbRise() > 0.0:
            rise = ladderClimbFbRise()
        wux, wuy = ux, uy
        _climb_dbg = None
    else:
        foot_t = LADDER_FOOT_X + ladderClimbShift()
        wux, wuy = ux, uy
        _climb_dbg = None
    fx = x + foot_t * ux
    fy = y + foot_t * uy
    # ⭐ 横向微调旋钮（默认 0）。法向取 ``(-uy, ux)`` —— 世界坐标的「左」，
    #    与 ``controls.climbFaceZ`` 里的 ``n = dy*ux - dx*uy`` 同一手性。
    fx += ladderClimbSide() * (-uy)
    fy += ladderClimbSide() * ux
    half = abs(ladderClimbWidth())
    cap = max(0.0, ladderClimbCap())
    if run <= 0.0 or rise <= 0.0 or half <= 0.0:
        return None
    return (fx, fy, z + ladderClimbDrop(), wux, wuy, run, rise, half, cap)


def _climbEncode(faces):
    """``{rid: 9 个 float}`` → 字符串（格式见 ``CLIMB_FACES_KEY``）。"""
    chunks = []
    for rid in sorted(faces):
        vals = faces[rid]
        chunks.append(",".join([str(int(rid))] + ["%.6f" % float(v) for v in vals]))
    return ";".join(chunks)


def _climbDecode(blob):
    """字符串 → ``{rid: 9 个 float}``。坏行**整行丢弃**（不抛）。"""
    faces = {}
    for chunk in str(blob or "").split(";"):
        parts = chunk.split(",")
        if len(parts) != CLIMB_FACE_FIELDS:
            continue
        try:
            rid = int(parts[0])
            vals = tuple(float(v) for v in parts[1:])
        except (TypeError, ValueError):
            continue
        if rid:
            faces[rid] = vals
    return faces


def _climbTrace(flow, text):
    """爬升面登记/注销**落盘**（普通 ``_log`` 只进 ``flow.result['logs']``，不落盘）。

    ⚠️ 2026-09-29：用户说「你看一下日志」，结果 ``grep siege-ladder-climb trace_log.txt``
       **永远是 0 条** —— 不是没登记，是 ``_log`` 那一路不落盘（跟 controls 里
       ``move-blocked-model`` 曾经踩的坑一模一样，那里也是靠 ``_trace`` 修的）。
       调 ``ladder_climb_shift/side/drop`` 这三个旋钮时**全靠这条日志**看坡的实际几何，
       所以必须落盘。频率很低（一架梯子状态变一次），不会刷屏。
    """
    _log(flow, text)
    try:
        from . import tracelog
        tracelog.emit("siege", "", text)
    except Exception:
        pass


def setClimbFace(flow, rid, item):
    """把某架云梯的爬升面登记进 ``flow.session``。返回是否登记成功。

    ⭐⭐ 2026-09-29：传 ``_curScene(flow)`` 进 ``ladderClimbFace`` —— 自动找墙
    （``_ladderWallFace``）只在给 scene 时才工作；不传的话运行时永远走旧公式
    （"偏左 25 度 / 在对面"的修复就不会生效）。日志里带 ``dir=/face_t=``：
    实机对照「梯子往哪边立、墙脸在轴上多远」用的就是这两个数。
    """
    faces = _climbDecode(flow.session.get(CLIMB_FACES_KEY))
    scene = _curScene(flow)
    geom = ladderClimbFace(item, scene) if item is not None else None
    if geom is None:
        if int(rid) in faces:
            del faces[int(rid)]
            flow.session[CLIMB_FACES_KEY] = _climbEncode(faces)
        return False
    # ⭐ 只为日志：再问一次墙脸（频率极低，一架梯子状态变一次，成本可忽略）。
    hit = None
    if scene is not None:
        try:
            _x, _y, _z = (float(v) for v in (item.get("pos") or (0.0, 0.0, 0.0)))
            _yaw = float((item.get("face") or (0.0, 0.0, 0.0))[2])
            hit = _ladderWallFace(scene, _x, _y, math.cos(_yaw), math.sin(_yaw),
                                  geom[5], geom[2] + geom[6])
        except (TypeError, ValueError, IndexError):
            hit = None
    faces[int(rid)] = geom
    flow.session[CLIMB_FACES_KEY] = _climbEncode(faces)
    # ⭐ 落盘（见 ``_climbTrace``）；末尾三个是平移旋钮，调坡对位时看这三个。
    # ⭐⭐ 2026-09-30：命中时多打 ``span=`` —— 墙脸桶的横向范围与坡顶横向 n。
    #    ⚠️ 世界坐标横轴用 (uy,−ux)，**不是** (−uy,ux)：分桶时 n = dx·uy − dy·ux，
    #    所以 ``span`` 与 ``side`` 的符号相反（span 偏负侧 ⇒ 要往正 side 挪）。
    if hit is not None:
        _span = None
        if scene is not None:
            try:
                _span = _ladderWallFaceSpan(scene, _x, _y, math.cos(_yaw),
                                            math.sin(_yaw), hit[0])
            except (TypeError, ValueError, IndexError):
                _span = None
        _span_txt = (" span=[%+.2f,%+.2f]mid=%+.2f/toe=%+.2f"
                     % (_span[0], _span[1], _span[2], 0.0 - _span[2])
                     if _span else "")
        _climbTrace(flow, "siege-ladder-climb rid=%d foot=(%.2f,%.2f,%.2f) u=(%.3f,%.3f)"
                          " run=%.2f rise=%.2f half=%.2f cap=%.2f"
                          " dir=%+d face_t=%.2f wall_top=%.2f%s"
                          " shift=%.3f side=%.3f drop=%.3f"
                    % (int(rid), geom[0], geom[1], geom[2], geom[3], geom[4],
                       geom[5], geom[6], geom[7], geom[8],
                       hit[1], hit[0], hit[2], _span_txt,
                       ladderClimbShift(), ladderClimbSide(), ladderClimbDrop()))
    else:
        _climbTrace(flow, "siege-ladder-climb rid=%d foot=(%.2f,%.2f,%.2f) u=(%.3f,%.3f)"
                          " run=%.2f rise=%.2f half=%.2f cap=%.2f"
                          " dir=none(no-wall)"
                          " shift=%.3f side=%.3f drop=%.3f"
                    % (int(rid), geom[0], geom[1], geom[2], geom[3], geom[4],
                       geom[5], geom[6], geom[7], geom[8],
                       ladderClimbShift(), ladderClimbSide(), ladderClimbDrop()))
    return True


def clearClimbFace(flow, rid):
    """注销某架云梯的爬升面（躺地 / 倒下 / 开关关掉时调）。"""
    faces = _climbDecode(flow.session.get(CLIMB_FACES_KEY))
    if int(rid) not in faces:
        return False
    del faces[int(rid)]
    flow.session[CLIMB_FACES_KEY] = _climbEncode(faces)
    _climbTrace(flow, "siege-ladder-climb-clear rid=%d left=%d" % (int(rid), len(faces)))
    return True


def _syncClimbFace(flow, rid, record, raising, lowering):
    """按云梯状态维护爬升面：**立起 ⇒ 登记；躺地/倒下/开关关 ⇒ 注销**。

    ⚠️ 开关关掉时**只清不建** —— 否则上一拍留下的面会变成「幽灵斜面」，
    把人凭空顶在半空（比不开还糟）。
    """
    if not raising or lowering or not ladderClimbEnabled():
        return clearClimbFace(flow, rid)
    if record is None:
        return False
    return setClimbFace(flow, rid, record[1])


def onLadderState(flow, rid, state):
    """云梯状态落地 → 按**该架自己的通道**把它立起来。

    通道见模块头与 ``level.ini`` 的 ``ladder_raise_table``。
    **默认 ``incline`` = 改动前行为**，所以关掉表就完全回到原样。

    ⚠️ ``born`` 与 ``incline`` **互斥**：同时发会和客户端抢刚体（一条给角度、
    一条让动画 root motion 去摆），谁赢未实测 —— 别把两个混成一个变量。
    """
    record = _object(flow, rid)
    mode = ladderRaiseMode(record[1]) if record else RAISE_INCLINE
    raising = state in _tiltStates()
    lowering = state in (LADDER_GROUND, LADDER_FALLING)

    # ⭐ 2026-09-29 ④ 云梯攀爬面：立起 ⇒ 登记斜面；躺地/倒下 ⇒ 注销。
    #    纯服务端高度场注入（不发任何报文）⇒ 任何 raise 通道都不受影响。
    #    包 try：这是**附加**能力，坏了也不该让「立起来」这条主链崩。
    try:
        _syncClimbFace(flow, rid, record, raising, lowering)
    except Exception:  # noqa: BLE001
        pass

    # ⭐ 2026-09-27：每次云梯状态落地都**落盘一行**到 ``trace_log.txt``。
    #    为什么必须落盘：``print("[siege] ...")`` 只在**模块导入时**打一次、且只进
    #    服务端控制台，离线复盘时什么都看不到（本项目已在「开关到底进没进进程」上
    #    白跑过三局）。这一行给出**该架这次用的通道与 CropTime 实值**，
    #    ⇒ 下次不用问用户，直接 ``grep siege trace_log.txt`` 就能确认取值。
    #    纯记录，包 try，任何异常都不许影响主流程。
    try:
        tracelog.emit(
            "siege", "",
            "ladder-state rid=%d state=%d mode=%s crop=%s" % (
                int(rid), int(state), mode,
                ("%.4f" % ladderCropTime(record[1]))
                if (record and mode == RAISE_CROP) else "-"))
    except Exception:  # noqa: BLE001
        pass

    # ---- 通道 born / born_late：不碰姿态，交给 ``mo._apply`` 在 update_state 里带事件 ----
    # 两者在**本函数**里行为完全相同（都不发倾角、不发位姿），差别只在
    # ``_apply`` 下发的那条 update_state 的 state 值 —— 见 ``ladderEventState``。
    if mode in (RAISE_BORN, RAISE_BORN_LATE):
        _safeCancel(flow, _tiltName(rid))
        if raising or lowering:
            _log(flow, "siege-ladder-%s rid=%d state=%d 不发倾角，改由 event_var 带"
                      " CALL_BORN_SCRIPT=%d" % (mode, int(rid), state,
                                                MO_EVENT_CALL_BORN_SCRIPT))
        return

    # ---- 通道 skip_inair / crop：``1002`` 那条 update_state **整条不发**（见模块头 §24）----
    # 本函数只负责「不发倾角/不发位姿」；真正把 ``1002`` 抽掉的是
    # ``mo._apply`` 里的 ``ladderSkipState``。这里两条日志分开写，是为了让抓包里
    # 能一眼分清「表现层没发」和「状态层也没发」。
    # ``crop`` 与 ``skip_inair`` 在**本函数**里完全相同（都不碰姿态）——
    # 两者唯一的差别是 ``1003`` 那条包要不要带 ``CropTime`` 参数，
    # 那件事在 ``ladderEventParams`` 里，不在本函数。
    if mode in (RAISE_SKIP_INAIR, RAISE_CROP):
        _safeCancel(flow, _tiltName(rid))
        if raising or lowering:
            extra = ""
            if mode == RAISE_CROP:
                extra = "（crop 通道：1003 另带 param(%d, %.4f)）" % (
                    HAVOK_PARAM_TYPE_CROPTIME, ladderCropTime(record[1]) if record else 0.0)
            _log(flow, "siege-ladder-%s rid=%d state=%d 该条 update_state 整条不发"
                      "（1002 抽掉，只留 1003 触发原生动画）%s"
                 % (mode, int(rid), state, extra))
        return

    # ---- 通道 none：**什么都不发**（纯状态对照，见模块头）----
    if mode == RAISE_NONE:
        _safeCancel(flow, _tiltName(rid))
        if raising or lowering:
            _log(flow, "siege-ladder-none rid=%d state=%d 纯状态对照，不发任何表现层指令"
                 % (int(rid), state))
        return

    # ---- 通道 skip_inair_rb：**「顺序」+「位姿」两条都要**（2026-09-24 14:55）----
    #   `1002` → **什么都不发**（该条 update_state 另由 ``mo.ladderSkipState`` 抽掉）
    #   `1003` → 照发（触发原生动画），但**位姿不立刻发**，而是延时到动画播完再补
    #   其余   → 回落成「立刻发位姿」，与 ``rbtrans`` 一致
    #   完整由来见 ``RAISE_SKIP_INAIR_RB`` 的注释块（含抓包时序）。
    if mode == RAISE_SKIP_INAIR_RB:
        _safeCancel(flow, _tiltName(rid))
        if int(state) == LADDER_INAIR:
            _log(flow, "siege-ladder-skiprb rid=%d state=1002 不发位姿（该条 update_state"
                      " 也整条不发）—— 等 1003 触发动画" % int(rid))
            return
        try:
            if raising:
                delay = ladderRbSettleMs()
                _safeCancel(flow, _rbName(rid))
                if delay > 0:
                    flow.later(_rbName(rid), delay)
                    _log(flow, "siege-ladder-skiprb rid=%d state=%d 动画先跑，%d ms 后补"
                              " rb_transform（%.4f rad）"
                         % (int(rid), int(state), delay,
                            ladderRbAngleFor(record[1])))
                else:
                    _sendRbTransform(flow, rid, record[1], ladderRbAngleFor(record[1]))
            elif lowering:
                _sendRbTransform(flow, rid, record[1], 0.0)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            _log(flow, "siege-ladder-skiprb-failed rid=%d %r" % (int(rid), error))
        return

    # ---- 通道 incline_org：**位置 + 朝向都走 CC 重发**（16:10 立，16:50 / 17:10 两次改）----
    #   完整由来见 ``RAISE_INCLINE_ORG`` 的注释块。时序（对齐 ``skip_inair_rb``）：
    #     ``1002`` → **什么都不发**（等 ``1003`` 触发原生动画）
    #     ``1003`` → ``update_state`` 照发（动画开始）；位姿/朝向**延时**到动画播完再补
    #     躺地    → 立刻清除 + 重发（躺地没有动画顺序问题）
    #   位姿与朝向都塞在**同一份 CC 重发**里（客户端只认 CC 的 pos/rotation，见 §10.1）。
    if mode == RAISE_INCLINE_ORG:
        _safeCancel(flow, _tiltName(rid))
        # ⭐⭐⭐ 1002：**什么都不发**。见 ``_ORG_PREFIX`` 的注释块 ——
        #   ``ladderSkipState`` 只抽掉那一条 ``update_state``，``onStateChange``
        #   仍会被调用；不在这里早退就等于「我们替客户端把梯子先摆到墙上」。
        if int(state) == LADDER_INAIR:
            _log(flow, "siege-ladder-org rid=%d state=1002 不发位姿/朝向"
                      "（该条 update_state 也整条不发）—— 等 1003 触发动画" % int(rid))
            return
        try:
            if raising:
                delay = ladderOrgDelayMs()
                _safeCancel(flow, _orgName(rid))
                if delay > 0:
                    flow.later(_orgName(rid), delay)
                    _log(flow, "siege-ladder-org rid=%d state=%d 动画先跑，%d ms 后补"
                              " origin+rotation + 重发该架 CC 物件"
                         % (int(rid), int(state), delay))
                else:
                    origin, tilt, sent = _applyOrgPose(flow, rid, record[1])
                    _log(flow, "siege-ladder-org rid=%d state=%d origin=(%.3f,%.3f,%.3f)"
                              " θ=%.4f tilt=%s 重发=%s"
                         % (int(rid), int(state), origin[0], origin[1], origin[2],
                            ladderAngle(),
                            "flat" if tilt is None else "%+.4f" % tilt, sent))
            elif lowering:
                _safeCancel(flow, _orgName(rid))
                ccobject.clearDynamicOrigin(rid)
                ccobject.clearDynamicRotation(rid)
                sent = _resendCcObject(flow, rid)
                _log(flow, "siege-ladder-org rid=%d state=%d 躺地：清除 origin+rot + 重发=%s"
                     % (int(rid), int(state), sent))
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            _log(flow, "siege-ladder-org-failed rid=%d %r" % (int(rid), error))
        # ⚠️⚠️ **本通道不发 `MO_INCLINE_NTF`**（2026-09-24 16:50 去掉）。
        #   依据（两条独立）：
        #     ① 14:09 那轮（**没有** incline）梯子呈正常斜靠、梯脚贴地
        #        （见 ``outputs/04_远景_梯子已靠墙.png``）⇒ 客户端原生姿态本来就对；
        #     ② 本轮发了 `0.7905`（= 45.3°）却得到**水平**。一条「绝对倾角」报文
        #        不可能把 45.3° 变成 0° ⇒ 那条倾角**没有被采纳**
        #        （姿态已被紧随其后的 CC 重发重置）。
        #   ⇒ 位置和朝向都改由 CC 重发负责，倾角通道在这里是多余的。
        return

    # ---- 通道 rbtrans：直给位姿（绕哪转我们说了算）----
    if mode == RAISE_RBTRANS:
        _safeCancel(flow, _tiltName(rid))
        try:
            if raising:
                # 逐架角度（``ladder_rb_angle_table``）；查不到回落全局。
                _sendRbTransform(flow, rid, record[1], ladderRbAngleFor(record[1]))
            elif lowering:
                _sendRbTransform(flow, rid, record[1], 0.0)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            _log(flow, "siege-ladder-rbtrans-failed rid=%d %r" % (int(rid), error))
        return

    # ---- 通道 incline（默认 = 改动前行为）----
    if not ladderInclineEnabled():
        return
    want = ladderAngle() if raising else (0.0 if lowering else None)
    if want is None:
        return
    _safeCancel(flow, _tiltName(rid))
    base = _tiltKey(rid)
    flow.session[base] = want
    flow.session[base + "-step"] = 0
    _log(flow, "siege-ladder-tilt-start rid=%d state=%d -> %.4f rad (%d 步)"
         % (int(rid), state, want, ladderSteps()))
    _tiltTick(flow, rid)          # 立刻推第一步，别等第一个定时器


def _tiltKey(rid):
    return "siegeTilt%d" % int(rid)


def _tiltTick(flow, rid):
    """推一步倾角；推满 ``ladder_steps`` 步就收工。

    步数存在 session 里（``<key>-step``）—— 只存标量，**绝不能存 tuple/list**：
    ``flow.session`` 会被原生层做类型校验，写非标量会以
    ``unsupported state type`` 丢弃**整条事件**（2026-09-23 实机翻车过，
    见 ``controls.echoKey()`` 的 docstring）。
    """
    base = _tiltKey(rid)
    target = flow.session.get(base)
    if target is None:
        return True
    steps = ladderSteps()
    step = int(flow.session.get(base + "-step", 0)) + 1
    flow.session[base + "-step"] = step
    if step > steps:
        return True
    angle = float(target) * (min(step, steps) / float(steps))
    flow.send(mo_flow.MO_COMMAND,
              mo_flow.encode_incline_ntf(wire.serverNowMs(), int(rid), angle),
              "mo-incline-%d-%d-of-%d" % (int(rid), step, steps))
    if step < steps:
        flow.later(_tiltName(rid), ladderStepMs())
    return True


# ============================ ② 投石车操控 ==================================
def _controlNames(rid):
    return ("siege-ctl-%d" % int(rid), "siege-ctl-ntf-%d" % int(rid))


def _actorMid():
    """本地玩家 mid（``mo._actorMid()`` 的薄封装，拿不到就退回 1）。"""
    try:
        from . import mo
        return int(mo._actorMid())
    except (ImportError, AttributeError, TypeError, ValueError):
        return 1


def sendControlOn(flow, rid):
    """``CONTROL_ON``(sel=4) (+ ``ACTOR_CONTROL_NTF``(sel=27)) —— 绑定人物与器械。"""
    actor = _actorMid()
    flow.send(mo_flow.MO_COMMAND, mo_flow.encode_control_on(actor, int(rid)),
              "mo-control-on-actor%d-mo%d" % (actor, int(rid)))
    if actorNtfEnabled():
        flow.send(mo_flow.MO_COMMAND, mo_flow.encode_actor_control_ntf(actor, int(rid)),
                  "mo-actor-control-ntf-actor%d-mo%d" % (actor, int(rid)))
    _log(flow, "siege-catapult-control-on rid=%d actor=%d" % (int(rid), actor))


def catActorReleaseEnabled():
    """下车补发 ``ACTOR_CONTROL_NTF(mo_mid=0)`` 解绑（env ``T7_CC_CAT_ACTOR_RELEASE``）。

    ⚠️ **实验**（2026-10-07）：用户实测下车后视角仍固定 —— 43 号方向锁不够。
    上车时发了 sel=27 ``(actor, mo)`` 把相机绑到器械，但下车**从没发过解除**；
    协议表里没有配对的「解除」号，这里按「再发一条 sel=27、mo_mid=0」当解绑实验
    （语义未实测）。回退（10 秒）：``set T7_CC_CAT_ACTOR_RELEASE=0``。
    """
    return _flag("cat_actor_release", "T7_CC_CAT_ACTOR_RELEASE", True)


def sendControlOff(flow, rid, reason="off"):
    """``CONTROL_OFF``(sel=5) —— 不解除的话玩家会卡在器械上。"""
    actor = _actorMid()
    flow.send(mo_flow.MO_COMMAND, mo_flow.encode_control_off(actor, int(rid)),
              "mo-control-off-actor%d-mo%d" % (actor, int(rid)))
    # ⭐⭐ 2026-10-07：**解绑实验** —— 补一条 ``ACTOR_CONTROL_NTF(mo_mid=0)``。
    #   上车 ``sendControlOn`` 发了 sel=27 ``(actor, mo)``（客户端据此把相机绑到
    #   器械），但下车从未发过对应的解除 ⇒ 实机「下车后视角固定不能转」。
    #   协议表没有配对的解除号，按「sel=27 + mo_mid=0」当解绑（语义未实测，
    #   见 ``catActorReleaseEnabled`` 的说明）。放 CONTROL_OFF 之后、43 号之前。
    if catActorReleaseEnabled():
        flow.send(mo_flow.MO_COMMAND, mo_flow.encode_actor_control_ntf(actor, 0),
                  "mo-actor-control-release-actor%d" % actor)
    _log(flow, "siege-catapult-control-off rid=%d actor=%d why=%s"
         % (int(rid), actor, reason))
    # ⭐⭐ 2026-10-07：**下车补一条 43 号「方向/相机锁」解锁**。
    #   为什么需要（实机口供：「投石车按 C 下来，视角没法切换了」）：
    #   上车时 ``mo-actor-control-ntf`` 把客户端相机绑到投石车，而下车只发
    #   ``CONTROL_OFF`` ⇒ 相机锁留在投石车上。``controls.unlockOrientation()``
    #   发的正是 43 号（``lock=0`` / ``lock_camera=0``），但它此前**只在进场时
    #   发一次**（见该函数自己的说明：「发送时机（进场一次）是兼容推断」）。
    #   ⚠️ 本函数是投石车**唯一**的下车出口（``siege-catapult-exit`` /
    #   ``-control-off`` 两条日志都从这儿出），所以补在这里即覆盖全部路径。
    #   回退（10 秒）：删掉本块，逐字节回到改动前。
    #   包 try：附加能力，坏了不该让「下车」这条主链崩。
    try:
        from . import controls
        controls.unlockOrientation(flow)
    except Exception:  # noqa: BLE001
        pass
    # ⭐⭐⭐ 2026-10-07 第三轮：补发 31 号 ``MOVE_NOTIFY_ACTIVE(active=1)`` 给
    #   **玩家本体**（inst 1）。前两轮（43 号解锁 / sel=27 mo_mid=0 解绑）日志
    #   证实都发出了但实机仍「视角固定」⇒ 换靶子：31 号是客户端 movable 的
    #   「可位移」总闸（原版中文硬约束：「unactive 的时候，移动物体是不能在
    #   地图上进行位移操作的」）。上车链（``sendControlOn`` → mo 状态机）可能
    #   把玩家本体的 movable 置回 unactive（操控权移交器械）；下车只发
    #   CONTROL_OFF 没把它置回 active ⇒ 视角/操控留在器械上。参数与进场
    #   ``activate()``（``instance-move-notify-active-after-in-scene``）同款，
    #   仅时机不同。回退（10 秒）：``T7_CC_CAT_ACTIVE_RESYNC=0``。
    if catActiveResyncEnabled():
        try:
            from . import controls
            _tick = controls.instanceTick(flow)
            flow.send(2, move_flow.encode_move_notify_active(
                server_tick=1 if _tick is None else _tick,
                target_instance_id=1, active=1),
                "instance-move-notify-active-after-dismount")
        except Exception:  # noqa: BLE001
            pass
    # ⭐⭐⭐ 2026-10-07 第四轮：补「回步兵」两帧。
    #   实机（会话 -5）：相机已解锁 ✓（第三轮 31 号生效），但英雄模型停在
    #   「骑兵赵云（无马）」、不能走 —— 器械操控态把人物留在了操作者变体上，
    #   且没有告诉客户端「英雄已回到场上步行档」。
    #   ① ``0x36 actorState(6)`` —— battleEntry 的「actor-in-scene」解锁帧
    #      （scene.py:415/535），语义 = 人物在场上、可操控；
    #   ② 步兵静止档 ground-stop 广播 —— 与骑马下马链 ③ 同款
    #      （``controls.broadcast`` + ``stopState``），把人物的步行静止姿态
    #      补给全场。
    #   回退（10 秒）：``T7_CC_CAT_FOOT_RESYNC=0``。
    if catFootResyncEnabled():
        try:
            from . import controls
            from . import contracts as _w
            flow.send(0x36, _w.actorState(flow.now, 6),
                      "actor-in-scene-after-dismount")
        except Exception:  # noqa: BLE001
            pass
        try:
            from . import controls
            _g = controls.groundState(flow)
            _pos = _g["position"]
            if _pos is None:
                # ⭐⭐⭐ 2026-10-07 第三十五轮（会话 -9 实证）：骑乘期地面账本
                #   可能是**空的**（客户端权威下骑乘/短距移动时 sel=3/52 一条
                #   都没进来 —— 下车后 6ms 才有第一条 no-motion-echo），旧代码
                #   回退 ``POSITION``（出生点）⇒ ground-stop 广播把人当场拽回
                #   出生点（「下车就回城」的根因）。改成用投石车自己的坐标
                #   （人下车就在车旁，位置基本重合，同 catapultShoot 的兜底）。
                _record = _object(flow, rid)
                if _record is not None:
                    _raw = _record[1].get("pos")
                    try:
                        _pos = [float(_raw[0]), float(_raw[1]), float(_raw[2])]
                    except (TypeError, ValueError, IndexError):
                        _pos = None
            if _pos is None:
                _log(flow, "siege-catapult-dismount-no-pos skip-ground-stop")
            else:
                controls.broadcast(flow, _pos, _g["heading"], controls.stopState(flow),
                                   0, 0, "catapult-dismount-ground-stop")
        except Exception:  # noqa: BLE001
            pass


def catapultWeaponEnabled():
    return _flag("cat_weapon", "T7_CC_CAT_WEAPON", False)


def catActiveResyncEnabled():
    """下车重发 31 号 ``MOVE_NOTIFY_ACTIVE(active=1)`` 给玩家本体（第三轮实验）。

    旋钮 ``cat_active_resync``（env ``T7_CC_CAT_ACTIVE_RESYNC``）。**默认 on**。
    实验史：第一轮 43 号（lock=0）实机无效；第二轮 sel=27 ``(actor, mo_mid=0)``
    实机无效 —— 两条日志都证实发出。第三轮换 31 号靶玩家本体（inst 1），
    **实机生效**（会话 -5：相机解锁 ✓）。
    """
    return _flag("cat_active_resync", "T7_CC_CAT_ACTIVE_RESYNC", True)


def catFootResyncEnabled():
    """下车补「回步兵」两帧（第四轮：0x36 actorState(6) + 步兵静止档广播）。

    旋钮 ``cat_foot_resync``（env ``T7_CC_CAT_FOOT_RESYNC``）。**默认 on**。
    实机（会话 -5）：相机解锁后英雄模型停在「骑兵赵云（无马）」且不能走 ——
    缺「人物已回场上步行档」的通知。
    """
    return _flag("cat_foot_resync", "T7_CC_CAT_FOOT_RESYNC", True)


def catapultStateWaitMs():
    """``ACT_CHANGE_WEAPON(1)`` 与 ``ACT_WAIT(2)`` 两拍之间隔多少毫秒。

    旋钮 ``cat_state_wait``（env ``T7_CC_CAT_STATE_WAIT``），默认 **120**。
    先例是 12ms（``battle-attack-return-idle`` 的定时器间隔），但那是巧合量级；
    这里取 120 保证客户端**分两 tick**处理（换武器动画还没起完就回待机也无妨，
    我们只要它「看见两次迁移」）。想更快/更慢直接调这个值，不用改代码。
    """
    return int(_num("cat_state_wait", "T7_CC_CAT_STATE_WAIT", 120, int))


def catapultRangeEnabled():
    """按 C 的**距离闸门**总开关。旋钮 ``cat_range``，默认 **on**。

    ⭐ 2026-09-28 新增。根因：客户端**每次**按 C 都送 ``target=10014``（不管人在哪），
    而本模块原先只看 ``tid`` ⇒ **半张地图外按 C 也能注入投石车武器**。
    实测那一局 18 次按 C，有 7 次人在 8.93~43.90 m 外，全部照样触发
    （脚本 ``cat_dist_audit.py`` 量的）。

    这既是个真 bug，也让「有没有上车」这个验收点**无法判读** ——
    因为姿势本来就会随每次按 C 变。回退 = ``cat_range=off``（10 秒）。
    """
    return _flag("cat_range", "T7_CC_CAT_RANGE", True)


def catapultToggleEnabled():
    """按 C 是否**开关式**（on = 已经在车上时，再按一次是**下车**）。默认 **on**。

    ⭐ 2026-09-28 22:1x 新增。实机症状：「绑上了，但**按 C 无法退出**」。

    根因：客户端在车上按 C（HUD 写「退出投石车 C」）发的**还是**
    ``cmd=18 sel=1 INTERACT target=10014`` —— 与上车**同一个包**。
    而 ``onInteract`` 原先只会再设一次 6001 ⇒ 永远出不来。

    实证（会话 ``6800-702631627``）：一局里 6 次按 C **全是** ``target=10014``，
    每次都重放 ``cmd=4 sel=3`` 的 1→2，人一直挂在车上。

    回退 = ``cat_toggle=off``（回到「按 C 只会上车」的旧行为，10 秒）。
    """
    return _flag("cat_toggle", "T7_CC_CAT_TOGGLE", True)


def catapultFireEnabled():
    """投石车「按住左键蓄力 / 松手发射」链开关。默认 **on**。

    ⭐⭐⭐ 2026-09-28 22:4x 新增。实机口供：「蓄力的时候，投石车，应该同步动，
    松手，发射投石」。

    为什么这条链**必须由服务端驱动**：客户端只在**状态迁移那一刻**去查
    ``propsheet/投石车.psheet``。那条链上的三个状态名已从 ``s_act_state_cli.bin``
    直读确认（442 行；行首 u32 状态号 + 32B GBK 名）：

        157 射击蓄力一段 / 158 射击蓄力二段 / 159 射击 / 160 弹道相机

    而 ``武将_投石车_射击蓄力一段.btree`` 的进入节点会
    ``设置当前交互物件行为树="投石车_蓄力一段"``（二段/射击同理）——
    **「投石车同步动」就是这三棵树在器械侧播的动画/音效**。

    ⚠️⚠️ 2026-09-28 23:4x **更正**：这一段原先写「发射本身是**客户端本地**的……
    所以只要松手时人物正处于 158，投石就出去了」—— **那条结论是错的**，
    照它做完实机表现就是「看不到弹道」（用户口供）。

    真相：``武将_投石车_射击蓄力二段.btree`` 的射出节点是
    ``SEQ Event="GeEventShootRock" → 攻城器械射出飞行道具("武器_飞行.投石")``，
    它**只监听事件、自己不产事件**。对照能正常发射的弩机
    （``武将_弩机_弩机发射预备.btree`` 的进入节点是 ``ACTION 请求射出``，
    客户端**主动请求**服务端算弹道）—— 投石车**没有**这一步。
    所以该事件只能由服务端下发的 ``CS_PROTO_MO_SHOOT_ROCK``(cmd=18 sel=6) 转成：
    **服务端不发，投石永不出膛**。修法见 ``sendShootRock``（开关 ``cat_shoot``）。

    服务端在这条链上的职责：推到 158 → 松手时**先发 sel=6、再推 159** → 最后回 2。

    回退 = ``cat_fire=off``（回到「左键在投石车上什么都不做」的旧行为，10 秒）。
    """
    return _flag("cat_fire", "T7_CC_CAT_FIRE", True)


def catapultChargeMs():
    """蓄力**一段 → 二段**的间隔（毫秒）。旋钮 ``cat_charge_ms``，默认 **1500**。

    纯配置，实机手感不对就调它 —— 不用动代码。
    """
    return max(100, min(10000, _num("cat_charge_ms", "T7_CC_CAT_CHARGE_MS", 1500, cast=int)))


def catapultFireMs():
    """「射击」态的停留时长（毫秒）。旋钮 ``cat_fire_ms``，默认 **900**。

    这段时间客户端跑 ``武将_投石车_射击``（器械侧 ``投石车_射击``：Attack 动画 +
    ``Play_Catapult_Launch`` 音效）。走完再回待机 2。
    """
    return max(100, min(10000, _num("cat_fire_ms", "T7_CC_CAT_FIRE_MS", 900, cast=int)))


def catapultShootEnabled():
    """投石车「松手发射」那条链的开关。默认 **on**。

    ⭐ 2026-09-28 新增。回退 = ``cat_shoot=off``（回到「松手只播发射动画、
    投石不出膛」的旧行为）。
    """
    return _flag("cat_shoot", "T7_CC_CAT_SHOOT", True)


def catapultShootSpeed():
    """投石初速（m/s）。旋钮 ``cat_shoot_speed``，默认 **30.0**。

    纯配置 —— 实机看投石飞得远近再调，不必动代码。
    """
    return max(1.0, min(200.0, _num("cat_shoot_speed", "T7_CC_CAT_SHOOT_SPEED",
                                    CATAPULT_SHOOT_SPEED)))


def catapultShootElevation():
    """投石仰角（度，0=水平）。旋钮 ``cat_shoot_elev``，默认 **45.0**。"""
    return max(-89.0, min(89.0, _num("cat_shoot_elev", "T7_CC_CAT_SHOOT_ELEV",
                                     CATAPULT_SHOOT_ELEV)))


def catapultShootResync():
    """发射前是否**先重推一次 158**。旋钮 ``cat_shoot_resync``，默认 **on**。

    ⭐⭐ 2026-09-29 新增。为什么要有它（本链**最脆**的一环）：

    ``GeEventShootRock`` 的接收器只挂在 **158** 树的执行节点上
    （``武将_投石车_射击蓄力二段.btree``）；**159 树里没有接收器**
    （``武将_投石车_射击.btree`` 的执行节点只有 Alt 键盘分支）。

    而客户端状态机 ``s_act_state_cli.bin`` 里有一条边 ``158 --msg 300020--> 159``，
    而 ``300020`` 正是客户端**本地**左键松开的消息号 ⇒ 松手那一刻客户端可能
    **本地**就切到了 159。服务端再快也要等一个 RTT 才知道松手，包到的时候
    158 那棵树可能已经退了 —— 事件没人接，投石不出膛。

    对策：**发 ``MO_SHOOT_ROCK`` 之前先补一条 ``STATE_SYNC(158)``**。
      * 若客户端还在 158（本地没切）：同值重发 = 不重进状态树（已实证），纯 no-op；
      * 若客户端已本地切到 159：这条把它**拉回 158**，接收器重新挂上，
        随后 ``MO_SHOOT_ROCK`` 就能被接住（代价是蓄力特效闪一下）。

    两种情况都能发出去 ⇒ 关掉它没有收益，只有风险；留开关是为了 A/B 定位。
    """
    return _flag("cat_shoot_resync", "T7_CC_CAT_SHOOT_RESYNC", True)


def catapultShootFlyerMode():
    """``MO_SHOOT_ROCK.flyer`` 填什么。旋钮 ``cat_shoot_flyer``，默认 **rid**。

    取值：
      * ``rid``  —— 填投石车自己的 rid（2026-09-28 起的旧行为，**保持默认不改**）；
      * ``fresh`` —— 每次发射分配一个没用过的 id（``cat_shoot_flyer_base`` 起递增）。

    为什么做成旋钮：TDR 只写「飞行物」，语义**未实测**。已确证的是
    ``攻城器械射出飞行道具`` 那个 ACTION 的参数里**只有实体名、没有 id**
    ⇒ 客户端自己生成飞行物实体、自己分配 id ⇒ ``flyer`` 大概率只是
    「关联/所有权」用的，**不影响生成**。所以默认不动它；
    真遇到「包发了、客户端不理」再切 ``fresh`` 做 A/B。
    """
    mode = _text("cat_shoot_flyer", "T7_CC_CAT_SHOOT_FLYER", "rid").lower()
    return mode if mode in ("rid", "fresh") else "rid"


def catapultShootFlyerBase():
    """``flyer`` 用 ``fresh`` 时的起始 id。旋钮 ``cat_shoot_flyer_base``，默认 90000。

    选 90000 的理由：玩家 mid 是小整数（本会话 ``actor=1``）、器械 rid 从
    ``ccobject`` 的 ``rid_base``(10000) 起 —— 90000 离两边都远，不会撞。
    """
    return int(max(1000, min(2000000000,
                             _num("cat_shoot_flyer_base", "T7_CC_CAT_SHOOT_FLYER_BASE",
                                  90000.0))))


def _catRidKey():
    """``flow.session`` 里存「当前操控的投石车 rid」的键。"""
    return "siegeCatRid"


def _nextShootFlyer(flow):
    """给这次发射分配一个没用过的 ``flyer`` id（``cat_shoot_flyer=fresh`` 时用）。

    起始值 ``cat_shoot_flyer_base``(90000)，**每次 +1**、存在 session 里。
    为什么不复用 rid：rid 是**已存在的器械实体**的 id，拿去当飞行物 id 会撞号。
    """
    key = "siegeCatFlyerSeq"
    try:
        nxt = int(flow.session.get(key, catapultShootFlyerBase()))
    except (TypeError, ValueError):
        nxt = catapultShootFlyerBase()
    flow.session[key] = nxt + 1
    return nxt


def catapultRid(flow):
    """当前操控的投石车 rid；没记录返回 ``None``。

    为什么要有它：``onCatapultAttack(flow, key_comb, msg_id)`` 的签名里**没有 rid**
    （调用点在 ``battle.message``，那里只有报文），而发射要往投石车身上发。
    rid 由 ``onCatapultState`` 在落地 6001（被操控）那一刻记下。
    """
    try:
        rid = int(flow.session.get(_catRidKey(), 0))
    except (TypeError, ValueError):
        return None
    return rid or None


def sendShootRock(flow, reason="siege-catapult-shoot"):
    """``MO_SHOOT_ROCK``(cmd=18 sel=6) —— 让客户端**真的把投石射出去**。

    ⭐⭐ 为什么必须有这一条（本轮「看不到弹道」+「Alt 切不了视角」的同一个根因）：

    行为树 ``武将_投石车_射击蓄力二段.btree`` 的射出节点是

        SEQ Event="GeEventShootRock" → 攻城器械射出飞行道具("武器_飞行.投石")

    —— 它**只监听事件、自己不产事件**。对照能正常发射的弩机：
    ``武将_弩机_弩机发射预备.btree`` 的进入节点是 ``ACTION 请求射出``，
    客户端**主动请求**服务端算弹道。投石车**没有**「请求射出」，
    所以 ``GeEventShootRock`` 只能由服务端下发的 ``CS_PROTO_MO_SHOOT_ROCK`` 转成。
    **服务端不发这条，投石永远不出膛。**

    而 ``武将_投石车_射击.btree`` 里 Alt 的条件链是

        按键被按下(Alt) → 当前投出物是否飞行中 → 使用弹道相机

    没有飞行物，第二个条件恒假 ⇒ Alt 永远切不了弹道相机。
    两个症状一个根因，修这条即同时修好。

    ⚠️ **时机**：必须在客户端**仍处于 158** 时发出。158→159 是松手包的本地预测，
    而 ``GeEventShootRock`` 挂在 158 树的**执行节点**上；客户端一切到 159，
    那棵树退出、事件就没人接了。所以调用点必须排在 ``STATE_SYNC(159)`` **之前**。

    ⚠️ 初位初速是**服务端自己算**的（客户端只收，不自己积分）。这里用
    「投石车位置 + 玩家 heading(度) + 可配仰角/速度」。方向/远近不对就调
    ``cat_shoot_elev`` / ``cat_shoot_speed``。

    返回 ``True`` = 包发出去了。
    """
    if not catapultShootEnabled():
        return False
    rid = catapultRid(flow)
    if rid is None:
        _log(flow, "siege-catapult-shoot-skip no-rid")
        return False
    pos = None
    record = _object(flow, rid)
    if record is not None:
        raw = record[1].get("pos")
        if raw:
            try:
                pos = [float(raw[0]), float(raw[1]), float(raw[2])]
            except (TypeError, ValueError, IndexError):
                pos = None
    if pos is None:
        pos = _playerPos(flow)          # 兜底：人就在车上，位置基本重合
    if pos is None:
        _log(flow, "siege-catapult-shoot-skip no-pos rid=%d" % rid)
        return False
    heading = 0.0
    try:
        from . import controls
        heading = float(controls.groundState(flow).get("heading") or 0.0)
    except (ImportError, AttributeError, TypeError, ValueError):
        heading = 0.0
    v = catapultShootSpeed()
    elev = math.radians(catapultShootElevation())
    rad = math.radians(heading)         # heading 是**度**（controls 里就是这么用的）
    velocity = [math.cos(rad) * v * math.cos(elev),
                math.sin(rad) * v * math.cos(elev),
                v * math.sin(elev)]
    # ⚠️⚠️ **先重推一次 158，再发 sel=6**（顺序不可换，理由见 catapultShootResync）。
    #   客户端可能在收到松手包的那一刻就**本地**切到了 159，而 ``GeEventShootRock``
    #   的接收器只在 158 树上；不补这一拍，事件到了没人接。
    #   包 try：重推失败也得把 sel=6 发出去（至少 A/B 时能看出是哪一步没生效）。
    resync = catapultShootResync()
    if resync:
        try:
            sendActorState(flow, ACT_CATAPULT_CAPACITY_2, "siege-catapult-shoot-resync")
        except Exception:  # noqa: BLE001
            pass
    actor = _actorMid()
    flyer = rid if catapultShootFlyerMode() == "rid" else _nextShootFlyer(flow)
    flow.send(mo_flow.MO_COMMAND,
              mo_flow.encode_shoot_rock(actor, flyer, pos, velocity),
              "mo-shoot-rock-actor%d-mo%d-flyer%d" % (actor, rid, flyer))
    _log(flow, "siege-catapult-shoot actor=%d rid=%d flyer=%d pos=(%.2f,%.2f,%.2f) "
               "v=(%.2f,%.2f,%.2f) heading=%.1f speed=%.1f elev=%.1f resync=%s"
         % (actor, rid, flyer, pos[0], pos[1], pos[2],
            velocity[0], velocity[1], velocity[2],
            heading, v, catapultShootElevation(),
            "on" if resync else "off"))
    return True


def onRockHit(flow, body):
    """``MO_ROCK_HIT``(cmd=18 sel=7, C->S) —— 客户端回报「投石砸到了谁」。

    ⭐⭐ 2026-09-29 新增。**这是「投石真的飞出去了」唯一可信的验收信号**：

    客户端只有在 ``攻城器械射出飞行道具("武器_飞行.投石")`` 真的生成了飞行物、
    并且它撞到东西之后才会回这条包。所以：

      * 服务端日志出现 ``siege-catapult-shoot``（发了 sel=6）**但全库看不到 sel=7**
        ≡ 客户端根本没生成投石（包被丢了、或字段不对）；
      * 出现 ``siege-catapult-rock-hit`` ≡ 投石**确实出膛并命中了**。

    本函数只记日志、不改状态 —— 伤害结算不在本轮范围内（``hit_range`` 的语义、
    以及是否要回 ``MO_UPDATE_STATE`` 才掉血，都**未实测**）。

    返回 ``True`` = 这条包已被本模块接管（不再往上层抛）。
    """
    if not catapultShootEnabled():
        return False              # 投石链关着 ⇒ 不接管，照旧落 app 的 unhandled
    try:
        target_mid, hit_range = mo_flow.decode_rock_hit(body)
    except (ValueError, TypeError) as error:
        _log(flow, "siege-catapult-rock-hit-bad %r" % (error,))
        return True
    _log(flow, "siege-catapult-rock-hit target=%d range=%d rid=%s"
         % (target_mid, hit_range, catapultRid(flow)))
    return True


def onCatapult(flow):
    """人物是否**正在投石车上**。

    两条判据，**任一成立**即「在车上」：

    1. ``wire.currentWeaponTid() == cat_weapon_tid`` —— 手里正是那把虚拟武器；
    2. **虚拟武器还装着**（``wire.virtualWeapons()`` 含 ``(cat_weapon_slot, cat_weapon_tid)``）
       —— 兜底：人还在车上、但当前槽被 1/2/3 改掉时，第 1 条会失效。

    为什么不用 ``mo.getState``：那要先把「人物 rid → 器械 rid」的对应关系存下来，
    而本模块至今没有这张表（``onInteract`` 拿到的 rid 是器械自己的）。
    手里的武器是**同一件事的另一个视角**：``sendCatapultWeapon`` 上车时装上、
    ``releaseCatapultWeapon`` 下车时摘掉，且 ``cat_weapon=off`` 时两边同时不发生
    ⇒ 不会出现「上了车但判据说没上」的错配。

    ⚠️⚠️ 2026-09-28 修正（**本轮「蓄力没反应」的根因**）：第 1 条判据
    **曾经整条失效** —— ``contracts.currentWeaponTid()`` 只遍历名册
    ``HERO_BATTLE_LOADOUTS``，而虚拟武器写在**另一张表** ``_VIRTUAL_WEAPONS`` 里。
    上车时 ``sendCatapultWeapon`` 把 ``_CURRENT_SLOT`` 设成 9，名册里只有槽 1/2/3
    ⇒ 该函数返回 **0** ≠ 29001 ⇒ 蓄力链**一次都没触发**。

    实证（会话 ``6800-706576879``）：上车完全成功
    （``cmd=18 sel=3 target=10014 state=6001`` + 事件 ``1001,1012``；
    ``cmd=5 sel=3`` 含 ``tid=29001 slot=9``），客户端也把左键按下送来了
    （``battle-raw key_comb=141 msg_id=300010``、``battle-raw key_comb=13 msg_id=300020``），
    但服务端 ``cmd=4 sel=3`` 里 **157/158/159 一条都没有** ⇒ 被本函数挡在门外。

    离线复现：``setVirtualWeapon(9, 29001); setCurrentSlot(9)`` 之后
    ``currentWeaponTid()`` 返回 **0**，而 ``battleLoadout()`` 里明明有 ``(9, 29001, 1)``
    —— 「武器装上了、但查询看不见」这个错配，正是「姿势变了却判据说没上车」的同类。

    读失败一律按「不在车上」处理（器械是附加能力，坏了不能拖垮 battle 那条链）。
    """
    try:
        if int(wire.currentWeaponTid()) == int(catapultWeaponTid()):
            return True
        want = (int(catapultWeaponSlot()), int(catapultWeaponTid()))
        return want in tuple(wire.virtualWeapons())
    except Exception:  # noqa: BLE001
        return False


def _catChargeState(flow):
    """蓄力链的会话内状态（只存一个 bool）。"""
    return flow.session.setdefault("siegeCatCharge", {})


def onCatapultAttack(flow, key_comb, msg_id):
    """投石车「按住蓄力 / 松手发射」链。返回 ``True`` = 这条攻击报文已被本模块接管。

    调用点在 ``battle.message`` 里、**普通攻击分发之前**。返回 False 时 battle
    照旧处理（所以「不在投石车上」时零行为差异）。

    ⚠️ 只接管「在车上」这一种情形；``cat_fire=off`` 或 ``cat_actor_state=off`` 时
    一律返回 False —— 后者是**故意**的：状态通道关着却把包吞掉，会表现为
    「按左键什么都不发生」，比不接管更难查。
    """
    if not catapultFireEnabled():
        return False
    if not actorStateEnabled():
        return False
    if not onCatapult(flow):
        return False
    state = _catChargeState(flow)
    key_comb = int(key_comb)
    msg_id = int(msg_id)
    lmb = bool((key_comb >> _KEY_BIT_LEFT_MOUSE) & 1)
    if not lmb:
        # ⚠️ 只认**明确的释放包**（``300020``，与 ``battle.ATTACK_RELEASE`` 同值）。
        #   不按「bit7=0」就判松手 —— 客户端还会发 ``msg_id=0`` 的纯按键状态上报，
        #   那类包里 bit7 也是 0，会把「蓄力中」误判成「已松手」而提前发射。
        if msg_id != CATAPULT_RELEASE_MSG:
            return False
        if not state.get("charging"):
            return False          # 没在蓄力 ⇒ 不是我们的包，交回 battle
        state["charging"] = False
        flow.cancel(_CATCHARGE_PREFIX + "t")
        flow.cancel(_CATFIRE_PREFIX + "t")
        # ⚠️ 顺序**不能换**：``MO_SHOOT_ROCK`` 必须排在 ``STATE_SYNC(159)`` **之前**。
        #    ``GeEventShootRock`` 挂在 158 树的**执行节点**上，客户端一切到 159
        #    那棵树就退出、事件没人接（详见 ``sendShootRock`` 的 docstring）。
        #    包 try：发射是附加能力，它坏了也得把 159 推出去（否则「射击动画都不播」）。
        try:
            sendShootRock(flow, "siege-catapult-shoot")
        except Exception:  # noqa: BLE001
            pass
        sendActorState(flow, ACT_CATAPULT_ATTACK, "siege-catapult-fire")
        flow.later(_CATFIRE_PREFIX + "t", catapultFireMs())
        _log(flow, "siege-catapult-fire release msg=%d key_comb=0x%x state=%d stay=%dms"
             % (msg_id, key_comb, ACT_CATAPULT_ATTACK, catapultFireMs()))
        return True
    if msg_id == 0:
        # 纯按键状态上报（组合变了但没解析出动作）：不当作蓄力起点。
        return False
    if state.get("charging"):
        return True               # 已在蓄力：客户端会连发按下包，忽略
    state["charging"] = True
    flow.cancel(_CATCHARGE_PREFIX + "t")
    flow.later(_CATCHARGE_PREFIX + "t", catapultChargeMs())
    sendActorState(flow, ACT_CATAPULT_CAPACITY_1, "siege-catapult-charge-1")
    _log(flow, "siege-catapult-charge press msg=%d key_comb=0x%x state=%d wait=%dms"
         % (msg_id, key_comb, ACT_CATAPULT_CAPACITY_1, catapultChargeMs()))
    return True


def catapultInteractRadius():
    """交互距离闸门（米，XY 平面）。旋钮 ``cat_interact_radius``，默认 **8.0**。

    为什么是 8：实测那一局 18 次按 C，**贴车**的 11 次落在 1.48~4.45 m，
    远处 7 次是 8.93~43.90 m。取 8.0 给「服务端位置比客户端慢半拍」留一倍余量，
    同时把 8.93 m 以上全部挡住。

    ``攻城器械.psheet`` 投石车那行有 ``触发参数1=5``（配 ``触发形状=方形``），
    那大概是原版的真实触发尺寸 —— 想贴原版就把本项调成 5.0。
    """
    return max(0.5, min(50.0, _num("cat_interact_radius", "T7_CC_CAT_INTERACT_RADIUS", 8.0)))


def _interactDistance(flow, rid):
    """玩家到某个 CC 物件的**水平**距离（米）；任一侧拿不到就 ``None``。

    ⚠️ 拿不到时调用方**必须放行**（``None`` = 不拦）—— 位置读不到是常态
    （进图头几拍 ``ground["position"]`` 是 ``None``），宁可按旧行为放行，
    也不要因为读不到位置把功能锁死。
    """
    me = _playerPos(flow)
    if me is None:
        return None
    record = _object(flow, rid)
    if record is None:
        return None
    raw = record[1].get("pos") or (0.0, 0.0, 0.0)
    try:
        return math.hypot(float(raw[0]) - me[0], float(raw[1]) - me[1])
    except (TypeError, ValueError, IndexError):
        return None


def _catWaitName(rid):
    return "%s%d" % (_CATWAIT_PREFIX, int(rid))


def pushStateChange(flow, rid, reason):
    """推**一对** ``STATE_SYNC``：``ACT_CHANGE_WEAPON(1)`` → 延时 ``ACT_WAIT(2)``。

    这是「换武器之后让客户端重进待机状态树」的唯一正确姿势。为什么必须成对，
    见 ``ACT_WAIT`` 上方那段边表实证（一句话：状态 1 是死路，同值重发不重进）。

    返回 ``True`` 表示第一拍发出去了（第二拍由定时器兜着）。
    """
    if not sendActorState(flow, ACT_CHANGE_WEAPON, "%s-state-1-change-weapon" % reason):
        return False
    flow.later(_catWaitName(rid), catapultStateWaitMs())
    return True


def catapultWeaponTid():
    return int(_num("cat_weapon_tid", "T7_CC_CAT_WEAPON_TID", CATAPULT_WEAPON_TID, int))


def catapultWeaponSlot():
    return int(_num("cat_weapon_slot", "T7_CC_CAT_WEAPON_SLOT", CATAPULT_WEAPON_SLOT, int))


def sendCatapultWeapon(flow, rid, reason="siege-catapult-weapon"):
    """把**人物**手里换成器械类武器（投石车）—— 「绑人」真正的入口。

    ⭐⭐⭐ 2026-09-28：为什么是这条而不是 MO 组。
    人物动作状态表是**按手持武器的类型**挑的（``propsheet/<武器类型名>.psheet``，
    46/80 个类型有同名文件）。投石车那张 ``投石车.psheet`` 的 `待机` 指向
    ``Battle\\Catapult\\武将_投石车_待机``，而那棵树的「首次进入」节点里才有
    ``挂接器械(挂点名称="Man")`` + ``发送消息 ENTER_DEMOLISHER``。
    手持刀剑时这张表根本查不到 ⇒ CONTROL_ON(4)/27/6001 发得再齐也不上车
    （2026-09-28 wire 实证：三条全发到位、客户端零上行、人物照常走路）。

    链路与 ``app.py`` 里按 1/2/3 那条**已实机确认**的换武器链同构：
    ``CHANGE_WEAPON_RSP``(cmd=5 sel=2, 12B) → ``WEAPON_USE_UPDATE``(sel=3)
    → ``STATE_SYNC``(cmd=4 sel=3, ``ACT_STATE_CHANGE_WEAPON``=1)。
    差别只在于：那条是**回**客户端的请求，这条是服务端**主动推**（上车）。

    ⚠️ 已知风险（``app.py`` 2026-09-25 01:1x 实测记录）：换武器回包后客户端可能
    WASD 失效。所以默认 ``cat_weapon=off``，一次只开这一个变量；关掉后
    ``clearVirtualWeapons()`` 让 loadout 逐字节回到原样。

    ⭐⭐⭐ 2026-09-28 补（**上一轮「姿势变了但人没上车」的根因**）：
    收尾那一拍**不能只发 ``STATE_SYNC(1)``**，要发 **1 然后 2**（``pushStateChange``）。
    实证：会话 ``6800-700367836`` 里按 C 之后 ``RSP/USE_UPDATE`` 都发出去了
    （HUD 左下角确实变成「弹道相机 Alt / 蓄力 左键 / 退出投石车 C」——那三个串
    只存在于武器模板表 29001 那一行，证明客户端认下了这把武器），
    但**整个爆发里一条 ``cmd=4`` 都没有** —— 因为 ``cat_actor_state=off``，
    ``sendActorState`` 第一行就 return 了。人物状态没动过（按 C 前最后一条
    是 236.244 的 ``state=2``）⇒ 客户端没重进状态树 ⇒ ``投石车.psheet`` 没被重查
    ⇒ ``武将_投石车_待机`` 的 ``挂接器械("Man")`` 没跑，相机也没切「投石车」。
    """
    if not catapultWeaponEnabled():
        return False
    try:
        from . import contracts as wire
        tid = catapultWeaponTid()
        slot = catapultWeaponSlot()
        wire.setVirtualWeapon(slot, tid)
        wire.setCurrentSlot(slot)
        flow.send(5, wire.changeWeaponRspBody(tid, 0),
                  "%s-rsp rid=%d tid=%d slot=%d" % (reason, int(rid), tid, slot))
        flow.send(5, wire.weaponUseUpdate(), "%s-use-update tid=%d" % (reason, tid))
        pushStateChange(flow, rid, reason)
        _log(flow, "siege-catapult-weapon rid=%d tid=%d slot=%d wait=%dms"
             % (int(rid), tid, slot, catapultStateWaitMs()))
        return True
    except Exception as error:  # noqa: BLE001 —— 附加能力，坏了不许拖垮交互
        _log(flow, "siege-catapult-weapon-failed rid=%s %r" % (rid, error))
        return False


def releaseCatapultWeapon(flow, rid, reason="siege-catapult-weapon-off"):
    """下车：把虚拟武器摘掉（下一次 ``WEAPON_USE_UPDATE`` 就不再含它）。

    ⚠️ 摘武器**必须再推一对状态**，否则人物卡在 ``投石车.psheet`` 的待机里 ——
    状态号还是 2，同值重发不重进树，客户端不会回去查普通武器那张表。
    顺序：先 USE_UPDATE（手里换回真武器）**再** 1→2（这样重进待机时
    ``武器类型`` 已经是龙牙刀/环首刀那套）。
    """
    if not catapultWeaponEnabled():
        return False
    try:
        from . import contracts as wire
        wire.clearVirtualWeapons()
        wire.setCurrentSlot(1)
        # ⭐⭐⭐ 2026-10-07 第三十五轮（会话 -9 实证）：下车后人物停在
        #   「骑兵赵云（无马）」变体，**按 1 才变回赵云** ⇒ 被动的
        #   USE_UPDATE+状态对不够，客户端认的是与「按 1」同款的完整应答链。
        #   这里照 app.py cmd=5 sel=1 的实现主动补 RSP（回**槽位 1 真武器
        #   tid**，选将感知 —— 会话 -9 按 1 时回的就是 1030411 龙牙刀）。
        _hero = None
        try:
            from . import scene as _scene
            _hero = _scene.heroIdOf(flow)
            _held = wire.battleLoadout(_hero)[0]
            _slotTids = {entry[0]: entry[1] for entry in _held}
            _right = int(_slotTids.get(1, 0))
            if _right:
                flow.send(5, wire.changeWeaponRspBody(_right, 0),
                          "%s-rsp slot=1 tid=%d" % (reason, _right))
        except Exception:  # noqa: BLE001
            _hero = None
        if _hero is not None:
            flow.send(5, wire.weaponUseUpdate(_hero), "%s-use-update" % reason)
        else:
            flow.send(5, wire.weaponUseUpdate(), "%s-use-update" % reason)
        pushStateChange(flow, rid, reason)
        _log(flow, "siege-catapult-weapon-release rid=%d" % int(rid))
        return True
    except Exception as error:  # noqa: BLE001
        _log(flow, "siege-catapult-weapon-release-failed rid=%s %r" % (rid, error))
        return False


def onCatapultState(flow, rid, state):
    """投石车状态落地 → 被操控(6001) 绑人，回到待机(6000) 解绑。

    ⭐ 2026-09-24 22:5x：这里同时负责**人物**状态。放这一处而不是 ``onInteract``，
    是因为 ``onInteract`` 里那句 ``setMoState`` 会回调本函数 —— 两处都发的话
    一次按 C 会打出两条 ``STATE_SYNC``（``CONTROL_ON`` 现在就是这么重复的）。

    ⚠️ 2026-09-28 顺序依赖（**别动**）：``onInteract`` 里的调用次序是
    ``sendInteractStop → sendControlOn → setMoState(6001) → sendCatapultWeapon``。
    6001 这里发的 ``STATE_SYNC(actorStateId())`` 是**在换武器之前**打的，
    所以它最多让人物回到「旧武器那张表的待机」（正常走路），**不会上车**；
    真正上车靠 ``sendCatapultWeapon`` 里 ``pushStateChange`` 的 1→2
    （那时手里已经是 29001）。把 ``sendCatapultWeapon`` 挪到 ``setMoState``
    之前会打断这条 —— 那时 1→2 打完了 6001 才发，最终状态虽然还是 2，
    但 6001 的 CONTROL_ON 与 actor_ntf 就落到「已换武器」之后了，难排查。
    """
    if not controlEnabled():
        return
    if state == controlState():
        # 发射链要 rid，而 ``onCatapultAttack`` 的签名里没有它 —— 在「落地被操控」
        # 这一刻记下。只在 6001 分支写，6000 分支不擦（下车后残留也无害：
        # ``catapultShootEnabled`` + ``onCatapult`` 两道闸都在前面）。
        flow.session[_catRidKey()] = int(rid)
        # ⭐ 2026-09-29：把「人在投石车上」这个 bool 立起来 —— ``controls.rigTurning``
        #    读的就是它，A/D 从「横移」切成「转角度」全靠这一个标志位。
        #    放在这里（状态**落地**）而不是 ``onInteract``：那条路上 6001 还没落地，
        #    中间还有 rsp/START/读条/STOP 四拍，中途失败就不该改 A/D 的语义。
        #    ⚠️⚠️ **必须跟着 ``cat_turn`` 一起门控**：`cat_turn=off` 是这条功能的
        #       回退开关，而回退的语义是「逐位回到改动前」。要是关掉开关却仍然把
        #       标志位立起来，``controls`` 那半边**照旧**摘 A/D，可转角那半边停了
        #       ⇒ 表现为「A/D 彻底没反应」—— 比改动前（横移）更坏。
        setRigOn(flow, catapultTurnEnabled())
        sendControlOn(flow, rid)
        sendActorState(flow, actorStateId(), "siege-catapult-op")
    elif state == CATAPULT_WAIT:
        setRigOn(flow, False)
        sendControlOff(flow, rid, "state-6000")
        sendActorState(flow, ACT_IDLE, "siege-catapult-release")
        # ⭐ 2026-09-28：下车时把注入的投石车武器摘掉，loadout 回到原样。
        #    摘完再推 1→2 —— 否则人物停在投石车那张表的待机里出不来。
        releaseCatapultWeapon(flow, rid)


# ==================== ②-2 投石车 A/D 转向（2026-09-29）======================
# 用户口供（本轮）：「左右 还是没反应 ，，按 A键 会变成 操控 投石车 位置方向」
#                  「按AD 肯定是调角度的 角度0」
#
# 离线定案（全部只读证据：客户端 exe 的 ACTION 名注册表校验 + 工作区
# ``cat_turn_audit.py`` 的 wire 直方图）：
#   * ``移动_投石车.btree`` **确实**处理 A/D，但执行节点里**没有任何旋转 ACTION**
#     —— 只有 ``移动处理``（播/停 ``Play_Catapult_Move`` 音效）+
#     ``设置投石车转向提示(true/false)``。后者是一个**提示**（HUD 亮一下），
#     **不是转**。⇒ 客户端自己不会转，转角只能由服务端驱动。
#   * 服务端这边，``controls`` 的「A/D 是转舵」那条链只认 ``mounted()``，
#     而投石车骑手 ``heroHasMount`` 为假（名册里没有坐骑）⇒ A/D 一路进位移投影
#     ⇒ 人物连同相机**横着滑走** ⇒ 就是用户看到的「操控投石车位置方向」。
#   * ``MO_BALLISTA_ROTATE_NTF``(sel=25) 看着像现成答案，但客户端**没实现**
#     （exe 里 ``Ballista`` 出现 **0** 次；70 个 ``GeRecv*`` 接收器里没有任何
#     rotate 接收器）⇒ 发了也不会有反应，**已排除**。
#   * ``MO_INCLINE_NTF``(sel=16) 是**一个标量角度**，历史上只喂云梯立起
#     （见 ``onLadderState``），不是水平转角，也不适合复用。
#
# 所以本段做了**三条**通道，各自一个旋钮、可独立 A/B。**2026-09-29 实机把它们
# 全跑了一遍，三条全部否证，结论如下（默认值就是结论，别再翻）**：
#
#   | 旋钮 | 通道 | 默认 | 实机结论 |
#   |---|---|---|---|
#   | ``cat_turn_cc`` | ``scene.sendVisionObject`` 重推 CC 物件 | off | ⛔ **「按 A/D 人物消失」** —— 那是「add event」语义，8 次/秒重建物件，而人物**挂接在投石车上** |
#   | ``cat_turn_heading`` | ``direction_yaw`` 推人物朝向 | off | ⛔ **「方向还是不变」** —— 朝向的主人是**客户端**（每 50 ms 用 ``cmd=2 sel=3`` 自报，服务端只回显）；wire 实测下行包在我的值和客户端的恒定值之间**交替** |
#   | ``cat_turn_rb`` | ``MO_RB_TRANSFORM``(sel=8) 改器械**刚体**位姿 | off | ⛔ **发了 204 条、客户端零反应**（`target=10014`/`msg_id=8`/`|q|=1.0` 全部正确，会话 `23856-756719862`）。**我原先写「云梯立起用的就是它、已实机有效」是错的** —— 全库历史里云梯 `rbtrans` 只在 2026-09-24 试验期出现 **12 条**，9-27 定案改用 `CropTime`，这条路**从未被验证有效** |
#
# ⭐ **根因（客户端侧，服务端无解）**：原版客户端**不支持投石车 A/D 转向**。
#   证据见 SKILL「投石车 A/D 转向」一节（转向模型表里有 `设置步兵转向` /
#   `设置骑兵转向` / `设置弩机转向`，**唯独没有投石车**；`投石车.psheet` 各状态
#   「转向参数」全空；btree 的 A/D 分支只播音效 + `设置投石车转向提示`）。
#   ⇒ 本段的价值只剩**前半**：让 ``controls`` 把 A/D 从位移投影里摘掉
#   （人物不再横滑）。**转向本身不要指望服务端。**
#
# ⚠️ 「A/D 不进位移投影」那半边在 ``controls`` 里（``drivingMask`` / ``stepMoving``），
#    由 ``controls.rigTurning()`` 读本段维护的 ``session["siegeCatOn"]`` 这个 bool。
_RIG_ON_KEY = "siegeCatOn"
_RIG_MAX_ELAPSED_MS = 500


def _rigYawKey(rid):
    """该架投石车**累积的水平转角**（度）在 session 里的键。"""
    return "siegeCatYaw%d" % int(rid)


def _rigBaseKey(rid):
    """该架投石车「按下 A/D 那一刻的人物朝向」（度）的键。"""
    return "siegeCatBase%d" % int(rid)


def _rigTickKey(rid):
    """上一拍的 ``flow.now``（算 ``elapsed`` 用）。"""
    return "siegeCatTick%d" % int(rid)


def _rigLogKey(rid):
    """上次落日志时的「15 度档」，用来把逐拍日志自节流成「每 15 度一行」。"""
    return "siegeCatLog%d" % int(rid)


def _rigRbKey(rid):
    """上一次**真的发出去**的 ``rb_transform`` 对应的转角（度）—— 步进闸门用。

    ⚠️ 松手时**不擦**：转角是累积量，留着它下次按下的步进判断才连得上。
    """
    return "siegeCatRb%d" % int(rid)


def catapultTurnEnabled():
    """投石车 A/D 转向总开关。旋钮 ``cat_turn``，默认 **on**。

    ⭐ 2026-09-29 新增。关掉它 = 回到「A/D 进位移投影、人物横着滑」的旧行为
    （但**仍然**会摘 A/D 的那半边只由 ``controls.rigTurning`` 控制，
    而那个标志位也是本模块维护的 ⇒ ``cat_turn=off`` 时本模块**不再维护**它，
    标志位自然恒为假 ⇒ 整条路一起停，逐位回到改动前）。
    """
    return _flag("cat_turn", "T7_CC_CAT_TURN", True)


def catapultTurnRate():
    """转向角速度（度/秒）。旋钮 ``cat_turn_rate``，默认 **60.0**。

    60 °/s 的依据：客户端 ``投石车_蓄力一段`` 的 ``动画系统浮点参数
    BattleStateDuration=9.0``（一个完整蓄力循环 9 秒），按「一个循环里能扫过
    大半圈」取 60 ⇒ 9 秒约 540°。**纯手感量，实机不对直接调这个值**，不用改代码。
    """
    return max(1.0, min(720.0, _num("cat_turn_rate", "T7_CC_CAT_TURN_RATE", 60.0)))


def catapultTurnCc():
    """转角是否写进**投石车 CC 物件**的 ``rotation``。旋钮 ``cat_turn_cc``，默认 **off**。

    ⛔⛔ **2026-09-29 实机否证，默认关掉，别当默认值用。**
    用户口供：「按 A 和 D **人物消失**」。

    机理：``_rigPushCc`` 走的 ``scene.sendVisionObject`` 是「**add event**」语义
    （reason 就叫 ``instance-cc-dynamic-vision-add-event-by-mid``），
    wire 实测**每秒 8 次**重推投石车 —— 而人物正**挂接在投石车上**
    （``武将_投石车_待机.btree`` 的 ``挂接器械(挂点=Man)``）⇒ 客户端把物件
    一遍遍重建，挂在上面的东西跟着没了。

    留着这个旋钮只为**留证**（想复现那个 bug 就把它打开），不要默认开。
    """
    return _flag("cat_turn_cc", "T7_CC_CAT_TURN_CC", False)


def catapultTurnHeading():
    """转角是否推给**人物朝向**。旋钮 ``cat_turn_heading``，默认 **off**。

    ⛔⛔ **2026-09-29 实机否证，默认关掉。**

    机理：**朝向的主人是客户端**。客户端每 50 ms 用 ``cmd=2 sel=3``
    （``CS_PROTO_MOVE_GROUND_HEADING``）**自报朝向**，而服务端
    ``controls.handleGroundHeading`` 只是把那个值写回 ``ground["heading"]``
    再**回显**一条 ``move-direct-bc``。

    wire 实测（会话 ``23856-755761772``）：转向窗口里下行 ``move-direct-bc``
    在**两个值之间交替** —— 我算的（50,53,56,59,62…）和客户端报的
    （恒 89，之后恒 70）各约 10 次/秒。客户端的值恒不变 ⇒ 用户看到的
    「**方向还是不变**」。

    ⇒ 服务端**推不动**人物朝向，这条路是死的（而且白刷 10 条包/秒）。
    """
    return _flag("cat_turn_heading", "T7_CC_CAT_TURN_HEADING", False)


def catapultTurnRb():
    """转角是否用 ``MO_RB_TRANSFORM``(sel=8) 下发给器械**刚体**。旋钮 ``cat_turn_rb``，默认 **off**。

    ⛔ **2026-09-29 实机否证，别再打开**（除非有新证据）。
    会话 `23856-756719862` 里发了 **204 条**，报文本身完全正确
    （`msg_id=8` / `target=10014` / `pos=(507.978,552.919,44.054)` / `|q|=1.0`），
    但**客户端零反应**（用户口供「左右还是不行」）。

    ⚠️ 我原先的推理「云梯立起用的就是它 ⇒ 已实机有效」是**错的**：
    全库历史里云梯 ``rbtrans`` 通道只在 2026-09-24 试验期出现 **12 条**，
    而 9-27 云梯立起的定案改用了 ``CropTime``（见 ``RAISE_CROP``）。
    **这条路从未被验证有效。**
    """
    return _flag("cat_turn_rb", "T7_CC_CAT_TURN_RB", False)


def catapultTurnRbStep():
    """RB 通道的**最小角度步进**（度）。旋钮 ``cat_turn_rb_step``，默认 **2.0**。

    步进闸门的作用：转角是逐拍累加的（50 ms 一拍），每拍都发一条
    ``rb_transform`` 就是 20 条/秒 —— 没必要，而且真机带宽/表现都不划算。
    默认 2° ⇒ 60°/s 时约 **6 条/秒**，肉眼看仍是连续的。
    """
    return max(0.1, min(90.0, _num("cat_turn_rb_step", "T7_CC_CAT_TURN_RB_STEP", 2.0)))


def setRigOn(flow, on):
    """维护「人在投石车上」这个 bool —— ``controls.rigTurning`` 的**唯一**数据源。

    由 ``onCatapultState`` 在「6001 被操控 / 6000 待机」两个**状态落地**时刻调用
    （不是 ``onInteract``：那条路上状态可能还没落地）。
    ⚠️ 只写/删一个 **bool 标量**，不碰别的键 —— 与 ``mo.py`` 那条「只写标量」
    的纪律同款。
    """
    try:
        if on:
            flow.session[_RIG_ON_KEY] = True
        else:
            flow.session.pop(_RIG_ON_KEY, None)
    except (AttributeError, TypeError):
        pass


def _rigYaw(flow, rid):
    """该架投石车当前累积的水平转角（度）。没转过 ⇒ ``0.0``。"""
    try:
        return float(flow.session.get(_rigYawKey(rid), 0.0))
    except (TypeError, ValueError):
        return 0.0


def _rigPushCc(flow, rid, yaw):
    """把转角写进投石车 CC 物件的 ``rotation`` 并**单推这一个物件**。

    ``ccobject.setDynamicRotation`` 是既有的「动态朝向覆盖」通道（云梯立起用它），
    与 ``ccobject.objects()`` 里的透传 ``face`` **同构** —— 都是欧拉角**弧度**。
    这里只在原 ``face`` 的 Z（yaw）上叠加，X/Y 原样保留（投石车的 face
    实测就是「只绕 Z 的 yaw」，如 ``[-0.0, -0.0, 0.865739]``）。

    ⚠️ 写的是 ``ccobject`` 的**进程级**字典，所以必须紧接着重推一次物件
    （客户端只认报文里的 ``rotation``，不重推它看不到）—— 见 ``_resendCcObject``。
    """
    try:
        record = _object(flow, rid)
        if record is None:
            return False
        face = record[1].get("face") or (0.0, 0.0, 0.0)
        base = (float(face[0]), float(face[1]), float(face[2]))
    except (TypeError, ValueError, IndexError):
        return False
    try:
        ccobject.setDynamicRotation(rid, (base[0], base[1],
                                          base[2] + math.radians(yaw)))
        _resendCcObject(flow, rid)
    except Exception:  # noqa: BLE001 —— 器械是附加能力，坏了不能拖垮地面 tick
        return False
    return True


def _rigPushRb(flow, rid, yaw):
    """把转角用 ``MO_RB_TRANSFORM``(sel=8) 下发给器械的**刚体**。

    ⭐⭐ 2026-09-29：三条通道里**唯一结构上正确**的一条。另外两条的实机否证
    写在 ``catapultTurnCc`` / ``catapultTurnHeading`` 的 docstring 里，别重走。

    位姿与云梯那条 ``_sendRbTransform`` **同构**：
      * ``pos`` 取器械自己的 ``ccobject.json`` 的 ``pos``（**原点不动**，
        只换朝向 —— 转角度不是位移）；
      * 四元数按 ``(w, x, y, z)`` 组装（纯绕 Z 的 yaw：``axis=(0,0,1)``
        ⇒ ``w=cos(θ/2), z=sin(θ/2)``），``ladderRbQuatOrder()=="xyzw"`` 时重排
        —— **与云梯共用同一个顺序旋钮**，别自己造第二个。

    返回 ``True`` = 这一拍真发了；``False`` = 被步进闸门挡下（或读盘失败）。
    """
    try:
        record = _object(flow, rid)
        if record is None:
            return False
        item = record[1]
        pos = item.get("pos") or (0.0, 0.0, 0.0)
        face = item.get("face") or (0.0, 0.0, 0.0)
        x, y, z = float(pos[0]), float(pos[1]), float(pos[2])
        yawRad = float(face[2]) + math.radians(yaw)
    except (TypeError, ValueError, IndexError):
        return False
    # 步进闸门：转不够就不发（默认 2°）—— 逐拍 20 条/秒没必要。
    try:
        last = float(flow.session.get(_rigRbKey(rid), 1.0e9))
    except (TypeError, ValueError):
        last = 1.0e9
    if abs(yaw - last) < catapultTurnRbStep():
        return False
    half = yawRad / 2.0
    quat = (math.cos(half), 0.0, 0.0, math.sin(half))
    if ladderRbQuatOrder() == "xyzw":
        quat = (quat[1], quat[2], quat[3], quat[0])
    try:
        flow.send(mo_flow.MO_COMMAND,
                  mo_flow.encode_rb_transform(int(rid), wire.serverNowMs(),
                                              [((x, y, z), quat)]),
                  "mo-rb-transform-cat-turn-%d" % int(rid))
    except Exception:  # noqa: BLE001 —— 器械是附加能力，坏了不能拖垮地面 tick
        return False
    # 发成功了才记 —— 否则闸门会把没发出去的拍也当成「已发」。
    flow.session[_rigRbKey(rid)] = yaw
    return True


def _rigPushHeading(flow, rid, heading, yaw):
    """把转角推给**人物朝向**（``direction_yaw``，走 ``controls.broadcastHeading``）。

    基准朝向是**按下 A/D 那一刻**的人物朝向（``heading``）—— 每次重新按下都重取
    一次（松手时 ``onRigTurnTick`` 会擦掉基准键），免得跨两次按键累积漂移；
    转的过程中**不读回** ``ground["heading"]`` —— 客户端可能用 ``cmd=2 sel=3``
    回报它自己的朝向，读回来会和我们打架（基准固定住就没有这个问题）。

    ⚠️ ``encode_move_direct_bc`` 的 ``direction_yaw`` **必须是 int 且在 -180..180**
    （见那个函数的校验），所以这里先归一化再取整。
    """
    key = _rigBaseKey(rid)
    try:
        # ⚠️ 必须一起接 ``KeyError`` —— ``session[key]`` 缺键抛的是它，
        #    不是 ``TypeError``/``ValueError``（离线单测在这里翻过车）。
        base = float(flow.session[key])
    except (TypeError, ValueError, KeyError):
        base = float(heading)
        flow.session[key] = base
    try:
        from . import controls
        ground = controls.groundState(flow)
    except (ImportError, AttributeError, TypeError):
        return False
    ground["heading"] = int(round(((base + yaw + 180.0) % 360.0) - 180.0))
    try:
        controls.broadcastHeading(flow)
    except Exception:  # noqa: BLE001
        return False
    return True


def onRigTurnTick(flow):
    """投石车 A/D 转向的**唯一**实现 —— 由 ``controls`` 在**两个**地方调用：

      * ``controls.timer("ground-step")`` —— 按住 A/D 时**逐拍**积分（连续转）；
      * ``controls.message()`` 的 sel=52 分支、``ground["mask"]`` 刚写完处 ——
        **掩码一变就叫一次**。那一下不是用来转的（``elapsed`` 恒为 0），而是用来
        「松手时把基准/计时键擦干净」：``moving`` 为假时地面定时器会被 cancel，
        松手那一拍**没有任何 tick**，只挂定时器的话下一轮按下的第一拍就会拿一个
        很大的 ``elapsed`` 去积分 ⇒ 瞬跳十几度。
        两处都按 ``flow.now`` 算 ``elapsed`` ⇒ 同一毫秒内连调两次第二次数出 0，
        **不会重复积分**。

    只在 ``controls.rigTurning(flow)`` 为真时被调用；本函数自己再查一次
    ``ground["mask"]`` 里的 A/D —— 掩码为 0 就静默收工（站住不动时零成本，
    也不会往 session 里写东西）。

    为什么挂在**逐拍定时器**上而不是「按键事件」上：按住 A 是**一个**按键事件，
    转角要**连续**累加 ⇒ 必须有时间基准。这与骑兵的 ``advanceMountHeading``
    （挂在 ``advanceGround`` 里、每拍用 ``elapsed`` 积分）是同一个套路。
    ⚠️ 所以 ``controls.stepMoving`` 里那条「转舵也算在动」**必须**把投石车算进来，
    否则定时器不续 ⇒ 按一次 A 只转一帧。

    返回 ``True`` = 这一拍归本模块处理（与 ``mo.timer`` 的约定一致）。
    """
    if not catapultTurnEnabled():
        return False
    # ★ 自我修复：标志位万一残留（比如 6000 那条路没走到），在这里一拍之内擦掉。
    #   判据与蓄力/发射链**同源**（``onCatapult`` = 手里那把虚拟武器还在不在），
    #   而虚拟武器是 ``sendCatapultWeapon`` / ``releaseCatapultWeapon`` 严格配对的。
    if not onCatapult(flow):
        setRigOn(flow, False)
        return False
    rid = catapultRid(flow)
    if rid is None:
        return False
    try:
        from . import controls
        ground = controls.groundState(flow)
        mask = int(ground.get("mask", -1))
        heading = ground.get("heading", 0)
    except (ImportError, AttributeError, TypeError, ValueError):
        return False
    if mask < 0:
        return False
    turn = controls.mountTurnKey(mask)
    if not turn:
        # 松手：清掉基准/计时，下次按下重新取一次（否则跨两次按键会累积漂移）。
        flow.session.pop(_rigBaseKey(rid), None)
        flow.session.pop(_rigTickKey(rid), None)
        return True
    now = int(flow.now)
    try:
        last = int(flow.session.get(_rigTickKey(rid), now))
    except (TypeError, ValueError):
        last = now
    flow.session[_rigTickKey(rid)] = now
    elapsed = max(0, min(_RIG_MAX_ELAPSED_MS, now - last))
    if elapsed <= 0:
        return True
    yaw = _rigYaw(flow, rid) + turn * catapultTurnRate() * elapsed / 1000.0
    yaw = ((yaw + 180.0) % 360.0) - 180.0
    flow.session[_rigYawKey(rid)] = yaw
    # ⚠️ 三条通道的默认值就是「哪条被实机证明能走」的结论（见各自 docstring）：
    #    rb=on（更新语义、已实机有效）／cc=off（add 语义，实测「人物消失」）／
    #    heading=off（朝向的主人客户端，推了白推）。
    rbOk = _rigPushRb(flow, rid, yaw) if catapultTurnRb() else None
    ccOk = _rigPushCc(flow, rid, yaw) if catapultTurnCc() else None
    hdOk = (_rigPushHeading(flow, rid, heading, yaw)
            if catapultTurnHeading() else None)
    # 自节流日志：每转过一个 15 度档落一行（60°/s ⇒ 约 4 行/秒），
    # 既不刷屏、又能在离线 wire/日志里逐度核对「到底转没转」。
    bucket = int(math.floor(yaw / 15.0))
    if bucket != flow.session.get(_rigLogKey(rid)):
        flow.session[_rigLogKey(rid)] = bucket
        _log(flow, "siege-catapult-turn rid=%d turn=%d yaw=%.1f cur_heading=%s"
                  " rb=%s cc=%s heading=%s rate=%.1f"
             % (int(rid), turn, yaw, heading, rbOk, ccOk, hdOk,
                catapultTurnRate()))
    return True


# ============================ ③ 攻城车前进 ==================================
def _driveName(rid):
    return "%s%d" % (_DRIVE_PREFIX, int(rid))


def _driveKey(rid):
    return "siegeDrive%d" % int(rid)


def _driveFields(rid):
    """驾驶状态的 **8 个独立标量键**（与 ``mo.MO_STATE_KEY`` 同一条纪律）。

    ⚠️ 绝不把 list/dict/tuple 塞进 ``flow.session``。``mo.py`` 记过两次事故：
    ``int`` key 的 dict（2026-09-20，樊城卡死进不去）、tuple（``controls`` 里
    按 W 没下行没日志）。那条注释的结论原话是「**不赌哪种结构能过序列化这个黑盒**」
    —— 位置有 6 个 float，全部拆成独立键，键名是 str、值是原生标量。
    """
    base = _driveKey(rid)
    return {
        "on": base, "inst": base + "Inst",
        "px": base + "Px", "py": base + "Py", "pz": base + "Pz",
        "gx": base + "Gx", "gy": base + "Gy", "gz": base + "Gz",
    }


def _driveLoad(flow, rid):
    """读驾驶状态 → ``(inst, [px,py,pz], [gx,gy,gz])``；没在驾驶时 ``None``。

    ⚠️ 返回的 tuple/list 是**内存态，绝不回写 session**（同 ``mo._stateMap`` 的做法）。
    """
    fields = _driveFields(rid)
    if not flow.session.get(fields["on"]):
        return None
    try:
        inst = int(flow.session.get(fields["inst"], 0)) & 0xFFFF
        pos = [float(flow.session.get(fields["px"], 0.0)),
               float(flow.session.get(fields["py"], 0.0)),
               float(flow.session.get(fields["pz"], 0.0))]
        goal = [float(flow.session.get(fields["gx"], 0.0)),
                float(flow.session.get(fields["gy"], 0.0)),
                float(flow.session.get(fields["gz"], 0.0))]
    except (TypeError, ValueError):
        return None
    return inst, pos, goal


def notifyCarMove(flow, rid):
    """让**客户端**进入「攻城车移动」状态 ``1021`` —— 发一条 ``MO_UPDATE_STATE``。

    ⭐⭐ 2026-09-29 实机定位：**靠近自动起步这条路原本漏了这一步**，而按 C 那条
    一直有（``onInteract`` 里那句 ``mo.setMoState(..., CAR_MOVE, ...)``）。
    客户端不在 1021 时会把 ``MOVE_MO_BC`` 整条丢掉 —— 表现正是用户报的
    「走到车边，车**纹丝不动**」，而服务端抓包 523 条 ``sel=50 / inst=10017 /
    dir=+2.29rad / pos 逐拍推进`` 全部正确。

    ``MO_UPDATE_STATE`` 的 ``state`` 字段 TDR 类型是 ``i32``（``@8``）⇒ 装得下
    1021；这与 ``MOVE_MO_BC.state`` 那个 ``uchar`` 是两个命名空间，别混。

    ⚠️ 本函数会**同步回调** ``siege.onStateChange → onCarState → driveStart``
    （``mo._apply`` 里没有同状态跳过）。防递归靠 ``driveStart`` 的 ``_driveLoad``
    重入闸 + ``onCarState`` 传 ``notify=False``，**不要**在这里加锁。
    """
    try:
        from . import mo
    except ImportError:  # pragma: no cover —— 打包异常时别拖垮器械
        return False
    anim = 0
    try:
        anim = mo.animationMsFor(mo.sceneInfoByRid(flow), rid) or 0
    except (AttributeError, TypeError, ValueError):
        anim = 0
    try:
        mo.setMoState(flow, rid, CAR_MOVE, anim, "siege-car-move")
    except Exception as error:  # noqa: BLE001
        _log(flow, "siege-car-drive-state-failed rid=%s %r" % (rid, error))
        return False
    _log(flow, "siege-car-drive-state rid=%d state=%d anim=%d"
         % (int(rid), CAR_MOVE, anim))
    return True


def _carOpenName(rid):
    return "%s%d" % (_CAROPEN_PREFIX, int(rid))


def carOpenLadderDeploy(flow, rid):
    """车**到位后**架梯：发 ``MO_Siege_Opening``(1022)，并挂一个收尾定时器。

    用户需求（2026-09-29 16:1x）：「车移动到位之后，梯子会自动搭到城墙上」。

    客户端收到 ``1022`` 就会播**原生架梯动画**（把车上的梯子翻起 / 搭出去），
    所以服务端**不需要**算任何角度 —— 与云梯靠客户端 btree 播动画同理。
    动画播完由 ``_carOpenSettle`` 在 ``car_open_ms`` 毫秒后发 ``1023`` 收尾。

    ⚠️ 只在 ``car_open_ladder=on`` 时由 ``_driveTick`` 的到达分支调用。
    ⚠️ ``flow.later`` 用的是**独立前缀** ``siege-caropen-`` ⇒ 不会被
    ``driveStop`` 的 ``_safeCancel(_driveName(rid))`` 误杀。
    """
    try:
        from . import mo
    except ImportError:  # pragma: no cover —— 打包异常时别拖垮器械
        return False
    anim = 0
    try:
        anim = mo.animationMsFor(mo.sceneInfoByRid(flow), rid) or 0
    except (AttributeError, TypeError, ValueError):
        anim = 0
    try:
        mo.setMoState(flow, rid, CAR_OPENING, anim, "siege-car-open")
    except Exception as error:  # noqa: BLE001
        _log(flow, "siege-car-open-failed rid=%s %r" % (rid, error))
        return False
    _log(flow, "siege-car-open rid=%d state=%d anim=%d settle=%dms mode=%s"
         % (int(rid), CAR_OPENING, anim, carOpenMsFor(anim), carOpenMsRaw()))
    # ⭐⭐ 17:3x：延时**用该车自己的 kv 动画时长**（``anim``），不是写死的 1540
    #    —— 写死只播 77% 就发 1023，架梯动画被截断（用户「没搭到缺口上」）。
    flow.later(_carOpenName(rid), carOpenMsFor(anim))
    return True


def _carOpenSettle(flow, rid):
    """架梯动画播完 ⇒ 发 ``MO_Siege_Opened``(1023) 收尾（梯子已搭在城墙上）。"""
    try:
        from . import mo
    except ImportError:  # pragma: no cover
        return False
    anim = 0
    try:
        anim = mo.animationMsFor(mo.sceneInfoByRid(flow), rid) or 0
    except (AttributeError, TypeError, ValueError):
        anim = 0
    try:
        mo.setMoState(flow, rid, CAR_OPENED, anim, "siege-car-opened")
    except Exception as error:  # noqa: BLE001
        _log(flow, "siege-car-opened-failed rid=%s %r" % (rid, error))
        return False
    _log(flow, "siege-car-opened rid=%d state=%d anim=%d"
         % (int(rid), CAR_OPENED, anim))
    return True


def _applyDrivePose(flow, rid, pos, heading):
    """把车的**新位置 + 新朝向**钉进 CC 物件并**重发**（客户端只认这条）。

    依据见 ``carPoseResend`` 的 docstring；这是「车不动」的修法。

    ``heading`` 传的是 **原始航向**（``_driveTick`` 里的 ``azimuth``，
    ``atan2(dy, dx)``）—— **不是** ``move_mo_bc`` 报文里那个会被
    ``car_dir_mode``/``car_dir_flip``/``car_heading_offset`` 改写的角。

    朝向：``car_pose_face=on``（默认）时把 ``rotation.z`` 钉到
    ``carPoseYaw(heading)`` = ``航向 + car_pose_yaw_offset``（默认 −20°，
    推导见 ``carPoseYawOffset``）；``car_pose_face=off`` 时**不设**
    ``_DYNAMIC_ROTATION`` ⇒ ``visionEventFor`` **透传** ``ccobject.json``
    的 ``face``（场景作者摆的姿态）—— 那是「车横着走」的旧行为。

    返回 ``scene.sendVisionObject`` 的结果；异常一律由调用方吞掉 ——
    器械是**附加**能力，坏了不该拖垮 ``move_mo_bc`` 那条已验证的链。
    """
    record = _object(flow, rid)
    if record is None:
        return False
    face = record[1].get("face") or (0.0, 0.0, 0.0)
    try:
        ccobject.setDynamicOrigin(
            rid, (float(pos[0]), float(pos[1]), float(pos[2])))
        if carPoseFace():
            if carPoseYawProbe():
                # ⭐ 标定模式：offset 自己扫一圈，并记日志（见 carPoseYawProbe）。
                off, step = _probeOffset(flow, rid)
                skey = "carProbeStep%d" % int(rid)
                if flow.session.get(skey) != step:
                    flow.session[skey] = step
                    _log(flow, "siege-car-yaw-probe rid=%d step=%d/%d"
                              " off=%.0fdeg yaw=%.1fdeg az=%.1fdeg"
                         % (int(rid), step + 1, CAR_POSE_PROBE_STEPS, off,
                            math.degrees(carPoseYaw(float(heading), off)),
                            math.degrees(float(heading))))
                yaw = carPoseYaw(float(heading), off)
            else:
                yaw = carPoseYaw(float(heading))
            ccobject.setDynamicRotation(
                rid, (float(face[0]), float(face[1]), yaw))
            # ⭐⭐⭐⭐ 2026-09-29 17:2x：**同源**下发 `horizontal_angle`
            #    （客户端真正用来「左右转」的字段；`rotation.z` 对车不生效）。
            #    与 `rotation.z` 用**同一个** `yaw` ⇒ 两条通道永远一致。
            if carPoseHangle():
                ccobject.setDynamicHangle(rid, yaw)
            # ⭐⭐⭐⭐ 2026-09-29 18:4x：**同源**下发 `vertical_angle`（架梯俯仰 / 抬头）
            #    —— 用户「樊城这个还是在墙里，有点低了，能往上改改角度」。
            #    角度按**该车自己的路径终点 + 城墙**现算（`car_pose_vangle=auto`），
            #    与出生值同源 ⇒ 出生 / 移动永远一致。
            _item = record[1]
            if carPoseVangleOn(_item, _curScene(flow)):
                ccobject.setDynamicVangle(
                    rid, carPoseVangle(_item, _curScene(flow)))
    except (TypeError, ValueError, IndexError):
        return False
    return _resendCcObject(flow, rid)


def driveStart(flow, rid, notify=True):
    """起驾驶：记起点、目标点，然后按 ``car_step_ms`` 逐拍广播位置。

    ``notify`` = 起步前先让客户端进 1021（见 ``notifyCarMove``）。
    靠近路径（``onGroundTick``）**必须** True；由 ``onCarState`` 回调进来时传
    False —— 那条状态**就是**它自己发的，再发一次会 ``_apply → onStateChange →
    onCarState → driveStart`` 无限递归。
    """
    if not carDriveEnabled():
        return
    # ⭐ 重入闸：已经在开就返回。``mo._apply`` **没有**同状态跳过，所以
    #    ``onCarState`` 的回调**一定**会走回这里 —— 没有这道闸就会**重复起步**
    #    （重写一遍 session、多发一拍 ``move_mo_bc``、多挂一个定时器）。
    #    ⚠️ 它与 ``onCarState`` 传 ``notify=False`` 是**两道互补**的防线：
    #       单拆任一道都只是「重复发包」，**两道都拆**才无限递归
    #       （``falsify_car_drive_state.py`` 注入 ④ 专门证这一条）。
    if _driveLoad(flow, rid) is not None:
        return
    me = _object(flow, rid)
    if me is None:
        return

    def _pos(item):
        raw = item.get("pos") or (0.0, 0.0, 0.0)
        return [float(raw[0]), float(raw[1]), float(raw[2])]

    # ⭐⭐⭐ 2026-09-29：**优先用该车自带的 `path` 终点**（场景设计者预定义的
    #    攻城路线 = 用户说的「正前方的缺口」）。只有没有 `path` 时才退回找正门。
    goal = carPathGoal(me[1])
    via = "path"
    if goal is None:
        via = "target"
        target_rid = carTargetResolved(flow)
        target = _object(flow, target_rid) if target_rid is not None else None
        if target is None:
            _log(flow, "siege-car-drive-no-target rid=%d target=%s scene=%s"
                 % (int(rid), carTargetRaw(), _sceneOf(flow)))
            return
        goal = _pos(target[1])

    start = _pos(me[1])
    # ⭐ 2026-09-29：若这架车**上一轮已经走过一段**（CC 动态位姿还留着），
    #    就从**它现在实际所在的位置**续 —— 否则会「瞬移回 JSON 原点再走一遍」。
    dynamic = ccobject.ccDynamicOriginFor(rid)
    if dynamic:
        start = [float(dynamic[0]), float(dynamic[1]), float(dynamic[2])]
    inst = _instanceId(me[1], me[0])
    fields = _driveFields(rid)
    flow.session[fields["on"]] = 1
    flow.session[fields["inst"]] = inst
    for key, value in zip(("px", "py", "pz"), start):
        flow.session[fields[key]] = float(value)
    for key, value in zip(("gx", "gy", "gz"), goal):
        flow.session[fields[key]] = float(value)
    _log(flow, "siege-car-drive-start rid=%d inst=%d via=%s from=%s to=%s speed=%.2f"
         % (int(rid), inst, via,
            [round(v, 2) for v in start], [round(v, 2) for v in goal], carSpeed()))
    # ⭐ 2026-09-29：先把客户端推进 1021，**再**发第一拍 ``move_mo_bc``。
    #    顺序不能反 —— 客户端在 1021 之前收到的 move 包会被丢掉。
    if notify:
        notifyCarMove(flow, rid)
    _driveTick(flow, rid)


def driveStop(flow, rid, reason="off"):
    """停车。

    ⚠️ **故意不清 CC 动态位姿**（``ccobject.clearDynamicOrigin``）—— 清了车会
    **弹回 ``ccobject.json`` 的原始 pos**（客户端是按 CC 记录摆的）。车应当
    **停在到达点**；下一轮 ``driveStart`` 会从那里续（见它的 ``dynamic`` 分支）。

    真要复位：``ccobject.clearDynamicOrigin(rid)`` +
    ``ccobject.clearDynamicRotation(rid)`` + 重发一次该 CC 物件。
    """
    _safeCancel(flow, _driveName(rid))
    fields = _driveFields(rid)
    was_running = flow.session.pop(fields["on"], None) is not None
    for key in fields.values():
        flow.session.pop(key, None)
    if was_running:
        _log(flow, "siege-car-drive-stop rid=%d why=%s" % (int(rid), reason))


def _driveTick(flow, rid):
    """一拍：朝目标推进 ``speed * dt``，发 ``MOVE_MO_BC``(sel=50)。

    ⚠️ ``dir`` 用 ``atan2(dy, dx)``，**这个符号约定未实测**（与 35 号坐骑广播同族，
    那一族的 ``dir`` 语义我们已闭合，但 MO 这一侧的接收器是否同约定**没验证**）。
    若实机表现「车倒退着走 / 横着漂」，改 ``[cc] car_dir_flip=1``。
    """
    state = _driveLoad(flow, rid)
    if state is None:
        return True
    inst, pos, goal = state
    dx, dy = goal[0] - pos[0], goal[1] - pos[1]
    distance = math.hypot(dx, dy)
    if distance < 0.5:
        flow.send(mo_flow.MO_COMMAND,
                  mo_flow.encode_update_state(target=int(rid), state=CAR_IDLE,
                                              state_change_ms=wire.serverNowMs(),
                                              state_time_ms=0,
                                              events=(mo_flow.MO_EVENT_REACH_PATH_POINT,)),
                  "mo-car-reach-path-point-%d" % int(rid))
        _log(flow, "siege-car-drive-arrived rid=%d dist=%.2f" % (int(rid), distance))
        driveStop(flow, rid, "arrived")
        # ⭐ 2026-09-29 16:1x：到位 ⇒ 自动架梯（把车上的梯子搭到城墙上）。
        if carOpenLadder():
            carOpenLadderDeploy(flow, rid)
        return True

    step = carSpeed() * (carStepMs() / 1000.0)
    if step > distance:
        step = distance
    pos[0] += dx / distance * step
    pos[1] += dy / distance * step

    # 位置推进后立刻回写 session（逐键标量）—— 下一拍从这里续。
    fields = _driveFields(rid)
    flow.session[fields["px"]] = float(pos[0])
    flow.session[fields["py"]] = float(pos[1])
    flow.session[fields["pz"]] = float(pos[2])

    # ⭐⭐ 2026-09-29：**两条通道用两个角，别串味**。
    #   ``azimuth`` = 原始航向（数学角）—— 给 **CC 物件的 rotation** 用；
    #   ``dir``     = ``move_mo_bc`` 报文里的角 —— 会被 delta/flip/hoff 改写。
    #   之前两者共用一个变量，``car_dir_mode=delta`` 时 `heading` 被置 0，
    #   会把 CC 的朝向也一起钉成 −90°（车头全部朝同一方向）—— 是 bug。
    azimuth = math.atan2(dy, dx)
    heading = azimuth
    if carDirMode() == "delta":
        # ⭐ 2026-09-29：客户端把 ``dir`` 当**增量**累加时用这条 —— 发 0
        #    （= 保持当前朝向直行）。发绝对角会每拍累加一次，越转越多。
        heading = 0.0
    else:
        if str(_knob("car_dir_flip", "T7_CC_CAR_DIR_FLIP", "0")[0]).strip() in ("1", "on", "true"):
            heading = -heading
        # ⭐ 2026-09-29：车头朝向的固定偏置（度）。「走的方向对、但车头歪 N 度」时填 ∓N。
        _hoff = carHeadingOffset()
        if _hoff:
            heading += math.radians(_hoff)

    flow.send(move_flow.MOVE_COMMAND,
              move_flow.encode_move_mo_bc(
                  server_tick=wire.serverNowMs() & 0xFFFFFFFF,
                  target_instance_id=inst,
                  state=carMoveState(),
                  position=(pos[0], pos[1], pos[2]),
                  direction_radians=heading,
                  current_velocity=int(carSpeed() * 1000),
                  max_velocity=int(carSpeed() * 1000)),
              "move-mo-bc-%d-%s" % (int(rid), carMoveState()))
    # ⭐⭐ 2026-09-29：**真正让车动起来的是这一条** —— 把新位置 + 新朝向钉进
    #    CC 物件并重发。实机（录屏 14:28）只发上面那条 ``move_mo_bc`` 时，
    #    客户端**车轮空转、车体纹丝不动**；而服务端日志是 702 条 move +
    #    ``arrived dist=0.31``（逻辑全对）⇒ 客户端不采纳 move 的 position。
    if carPoseResend():
        try:
            sent = _applyDrivePose(flow, rid, pos, azimuth)
        except Exception as error:  # noqa: BLE001
            _log(flow, "siege-car-pose-failed rid=%s %r" % (rid, error))
        else:
            # 只记一次 —— 每 200 ms 一条会刷爆 wire 的 reason 字段。
            key = "carPoseLog%d" % int(rid)
            if sent and not flow.session.get(key):
                flow.session[key] = 1
                # ⭐ 2026-09-29：把**实际下发的 rotation** 也记下来 ——
                #    「车头方向不对」时能离线一眼看出「到底发了多少度、
                #    和场景原 face 差多少」，不必再靠截图猜。
                rec = _object(flow, rid)
                face = (rec[1].get("face") or (0.0, 0.0, 0.0)) if rec else (0.0,) * 3
                _log(flow, "siege-car-pose rid=%d pos=%s az=%.2fdeg yaw=%.2fdeg"
                          " face_z=%.2fdeg off=%.1fdeg flip=%s faceknob=%s probe=%s"
                     % (int(rid), [round(v, 2) for v in pos],
                        math.degrees(azimuth), math.degrees(carPoseYaw(azimuth)),
                        math.degrees(float(face[2])),
                        carPoseYawOffset(),
                        "on" if carPoseYawFlip() else "off",
                        "on" if carPoseFace() else "off",
                        "on" if carPoseYawProbe() else "off"))
    flow.later(_driveName(rid), carStepMs())
    return True


def onCarState(flow, rid, state):
    """``mo._apply`` 的回调：状态落地后决定起不起步。

    ⚠️ ``notify=False`` **不能改** —— 这条 1021 就是 ``_apply`` 自己刚发的，
    再 ``notifyCarMove`` 一次就是「自己触发自己」：``_apply → onStateChange →
    onCarState → driveStart → notifyCarMove → _apply …``。
    它与 ``driveStart`` 的重入闸是**两道互补**的防线：单拆任一道都只是重复发包，
    **两道都拆**才无限递归（``falsify_car_drive_state.py`` 注入 ④ 自证）。
    按 C 那条路靠 ``onInteract`` 里的 ``setMoState`` 把状态发出去，
    这里只负责**起步**。
    """
    if state == CAR_MOVE:
        driveStart(flow, rid, notify=False)
    elif state == CAR_IDLE:
        driveStop(flow, rid, "state-1020")


# ======================= ③b 靠近自动起步（逐拍） =============================
def _scanKey():
    return "siegeScan"


def _playerPos(flow):
    """玩家当前位置 → ``[x, y, z]``；拿不到返回 ``None``。

    来源是 ``controls.groundState(flow)["position"]`` —— 也就是那个**一直正常**
    的 str-key dict ``session["ground"]``（见 ``mo.py`` 里那段事故注释的反证）。
    返回的 list 是**内存态，绝不落 session**。
    """
    try:
        from . import controls
        raw = controls.groundState(flow).get("position")
    except (ImportError, AttributeError, TypeError, ValueError):
        return None
    if not raw:
        return None
    try:
        return [float(raw[0]), float(raw[1]), float(raw[2])]
    except (TypeError, ValueError, IndexError):
        return None


def onGroundTick(flow):
    """``controls.timer("ground-step")`` 挂点 —— ③ 的「靠近自动起步」。

    ⚠️ 必须挂在 ``controls.timer`` 里 ``stepMoving`` 闸门**之前**：玩家走到车边
    站住不动时 ``stepMoving`` 为假、那个函数会提前 return，挂在后面就永远不触发。

    ⚠️ 已知边界（写在明处，别当成 bug）：``ground-step`` 是**自续**定时器，玩家站住
    不动时它不续（``controls.timer`` 提前 return、不重新 ``later``），所以
    「出生点恰好就贴着车、又全程不动」那种情形本条不会触发 —— 但正常**走**过去的
    过程一定在移动，那几拍足够进 5m 半径。兜底还有 ``onInteract``（按 C）与
    ``onCarState``（状态驱动）。

    返回 ``True`` = 这拍归本模块处理（与 ``mo.timer`` 的约定一致）；``False`` =
    开关没开，完全不动。
    """
    if not carAutoEnabled():
        return False
    every = carScanEvery()
    try:
        count = int(flow.session.get(_scanKey(), 0)) + 1
    except (TypeError, ValueError):
        count = 1
    if count < every:
        flow.session[_scanKey()] = count      # ⚠️ 只写 int 标量
        return True
    flow.session[_scanKey()] = 0
    me = _playerPos(flow)
    if me is None:
        return True
    radius = carTriggerRadius()
    cars = 0
    paths = 0
    for rid, (_index, item) in _records(flow).items():
        try:
            if int(item.get("tid")) != CITY_CAR_TID:
                continue
        except (TypeError, ValueError):
            continue
        cars += 1
        if carPathGoal(item) is not None:
            paths += 1
        if _driveLoad(flow, rid) is not None:
            continue                          # 已经在开，别重复起步
        raw = item.get("pos") or (0.0, 0.0, 0.0)
        try:
            distance = math.hypot(float(raw[0]) - me[0], float(raw[1]) - me[1])
        except (TypeError, ValueError, IndexError):
            continue
        if distance <= radius:
            _log(flow, "siege-car-proximity rid=%d dist=%.2f radius=%.2f -> drive"
                 % (int(rid), distance, radius))
            driveStart(flow, rid)
        else:
            # ⭐ 2026-09-29：**走不近时也要留痕**。否则「靠近了没反应」在离线日志里
            #    **一条都没有**，无法区分「根本没触发」和「触发了但没目标」
            #    （实机踩过：江陵城车不动、`grep siege-car` 零条，只能靠猜）。
            #    每架只记一次（session 标量），不刷屏。
            key = "carFar%d" % int(rid)
            if not flow.session.get(key):
                flow.session[key] = 1
                _log(flow, "siege-car-far rid=%d dist=%.2f radius=%.2f scene=%s"
                     % (int(rid), distance, radius, _sceneOf(flow)))
    # ⭐ 首次扫描记一条汇总：一眼看出「开关进没进进程 / 找到几架车 / 目标是哪个」。
    if not flow.session.get("carScanSeen"):
        flow.session["carScanSeen"] = 1
        _log(flow, "siege-car-scan scene=%s cars=%d radius=%.2f drive=%s auto=%s"
                  " target=%s resolved=%s paths=%d"
             % (_sceneOf(flow), cars, radius,
                "on" if carDriveEnabled() else "off",
                "on" if carAutoEnabled() else "off",
                carTargetRaw(), carTargetResolved(flow), paths))
    return True


# ============================ 挂点 ==========================================
def onInteract(flow, rid, tid, state):
    """``mo._handleInteract`` 的挂点。**返回 True = 这条交互由 siege 接管**。

    旧逻辑把「云梯/门以外」一律丢进「只确认收到、不改状态」——
    也就是投石车按 C 什么都没发生。这里补上 ② 和 ③。

    ⚠️ 云梯（``tid=1``）**一律返回 False**，继续走 ``mo`` 里那条已经实机验证过的
    云梯链，本模块绝不插手 —— 这是三件事里唯一有回归风险的一条，必须隔离。
    """
    try:
        tid = int(tid) if tid is not None else None
    except (TypeError, ValueError):
        return False
    if tid == LADDER_TID:
        return False
    if tid == CATAPULT_TID and controlEnabled():
        # ⭐⭐⭐ 2026-09-28：**距离闸门**。客户端每次按 C 都送 ``target=10014``，
        #   不管人在哪（实测 43.9 m 外照样送），而本模块原先只看 ``tid`` ⇒
        #   在任意位置注入投石车武器 ⇒ 用户看到的「**跑远一点按 C 也变换姿势**」。
        #   加这道闸门有两个作用：① 修掉「远程上车」这个真 bug；
        #   ② 让「有没有上车」变得可判读 —— 否则姿势本来就每次按 C 都变。
        #   ⚠️ 拿不到距离（位置缺失 / 读盘失败）⇒ **放行**，按旧行为走。
        distance = _interactDistance(flow, rid)
        if (catapultRangeEnabled() and distance is not None
                and distance > catapultInteractRadius()):
            _log(flow, "siege-catapult-too-far rid=%d dist=%.2f radius=%.2f -> 只确认收到"
                 % (int(rid), distance, catapultInteractRadius()))
            return False
        from . import mo
        # ⭐⭐⭐ 2026-09-27 21:1x 修正：本函数**不再**在开头发 CONTROL_ON。
        #   旧写法在开头就 `sendControlOn`，那时投石车还是 6000（待机），
        #   `onCatapultState` 又会在 `setMoState(6001)` 之后**再发一次** ⇒
        #   wire 实测一按 C 出两条 `mo-control-on`（38000-612159125，事件 39889，
        #   帧 1092 + 1096）。客户端对「先到、且此时车还是 6000」的那条很可能
        #   拒收，对第二条又当重复忽略 ⇒ CONTROL_ON 等于白发。
        #   正确顺序：**先 STOP（结束交互态）→ 再 update_state(6001) →
        #   onCatapultState 在状态落地后才发 CONTROL_ON**（车已是 6001）。
        #   所以 CONTROL_ON 只在此处由 onCatapultState 发一次、且车已在被操控态。
        #   若要回退到「开头也发一份」的旧行为，把下面这行加回来即可：
        #       sendControlOn(flow, rid)
        anim = 0
        try:
            anim = mo.animationMsFor(mo.sceneInfoByRid(flow), rid) or 0
        except (AttributeError, TypeError, ValueError):
            anim = 0
        # ⭐⭐⭐ 2026-09-28 22:1x：**按 C 是开关，不是单向上车**。
        #   客户端在车上按 C（HUD 写「退出投石车 C」）发的**还是**
        #   `cmd=18 sel=1 INTERACT target=10014` —— 与上车**同一个包**。
        #   原实现只会再设一次 6001 ⇒ **永远出不来**。
        #   实证（会话 6800-702631627）：一局 6 次按 C 全是 target=10014，
        #   每次都重放 `cmd=4 sel=3` 的 1→2，人一直挂在车上。
        #   ⇒ 已处于被操控态时，这一下按 C 走**下车**：setMoState(6000)，
        #     由 `onCatapultState` 收尾（CONTROL_OFF + STATE_SYNC(2) + 摘武器）。
        #   回退 = `cat_toggle=off`。
        #   ⚠️ 取当前状态包一层 try：器械是**附加**能力，读状态失败也**不能**
        #      拖垮 mo 那条已验证的交互链 ⇒ 失败时按「没在车上」处理（走上车路径，
        #      与改动前的行为一致）。
        try:
            on_catapult = mo.getState(flow, rid) == controlState()
        except Exception:  # noqa: BLE001
            on_catapult = False
        if catapultToggleEnabled() and on_catapult:
            if catStopFirst():
                sendInteractStop(flow, rid)
            # ⭐ 第三十七轮：6000 之前，趁客户端还在 6001 控制树里补一发
            #   「停止操控(1013)」—— 这是「下车后 HUD 还挂在投石车、人不能走」
            #   唯一的候选缺口（协议里有事件、代码从没用过）。
            #   回退：cat_stop_event=off。
            sendStopControlPulse(flow, rid)
            mo.setMoState(flow, rid, CATAPULT_WAIT, anim, "siege-catapult-off")
            if not catStopFirst():
                sendInteractStop(flow, rid)
            _log(flow, "siege-catapult-exit rid=%d -> state=%d" % (int(rid), CATAPULT_WAIT))
            return True
        # ⭐⭐⭐ 2026-09-27 18:25 **顺序修正**（`cat_stop_first`，默认 on）。
        #   链应该是 rsp(23) → START(21) → 读条 → **STOP(22)** → update_state。
        #   实测（会话 38000-595458823，event=7116）投石车那条 update_state(6001)
        #   确实发出去了，但客户端**一个上行都没回**、人物继续正常走路 ⇒
        #   客户端在「交互中」把这条 update_state 吞了 ⇒ 先收掉交互态再切状态。
        #   回退 = `cat_stop_first=off`。
        if catStopFirst():
            sendInteractStop(flow, rid)
            mo.setMoState(flow, rid, controlState(), anim, "siege-catapult-be-op")
        else:
            mo.setMoState(flow, rid, controlState(), anim, "siege-catapult-be-op")
            sendInteractStop(flow, rid)
        # ⭐⭐⭐ 2026-09-28：最后一刀 —— 把人物手里换成投石车武器。
        #   放在 MO 状态落地之后：那时车已是 6001（被操控待机），人物树一进来
        #   就把「当前交互物件行为树」设成同一棵树，前后一致。
        #   受 `cat_weapon` 门控，默认 off（逐字节不变，见函数注释里的 WASD 风险）。
        sendCatapultWeapon(flow, rid)
        return True
    if tid == CITY_CAR_TID and carDriveEnabled():
        from . import mo
        anim = 0
        try:
            anim = mo.animationMsFor(mo.sceneInfoByRid(flow), rid) or 0
        except (AttributeError, TypeError, ValueError):
            anim = 0
        mo.setMoState(flow, rid, CAR_MOVE, anim, "siege-car-move")
        return True
    return False


def onStateChange(flow, rid, state):
    """``mo._apply`` 的挂点：状态落地后推表现层。**任何异常都不许影响主流程。**"""
    try:
        record = _object(flow, rid)
        if record is None:
            return
        tid = record[1].get("tid")
        try:
            tid = int(tid)
        except (TypeError, ValueError):
            return
        if tid == LADDER_TID:
            onLadderState(flow, rid, state)
        elif tid == CATAPULT_TID:
            onCatapultState(flow, rid, state)
        elif tid == CITY_CAR_TID:
            onCarState(flow, rid, state)
    except Exception as error:  # noqa: BLE001 —— 器械是附加能力，坏了不能拖垮进图
        _log(flow, "siege-state-hook-failed rid=%s %r" % (rid, error))


def timer(flow, name):
    """``mo.timer`` 的挂点：返回 True 表示这个定时器是本模块的。"""
    if name.startswith(_TILT_PREFIX):
        return _tiltTick(flow, int(name[len(_TILT_PREFIX):]))
    if name.startswith(_DRIVE_PREFIX):
        return _driveTick(flow, int(name[len(_DRIVE_PREFIX):]))
    if name.startswith(_CAROPEN_PREFIX):
        return _carOpenSettle(flow, int(name[len(_CAROPEN_PREFIX):]))
    if name.startswith(_RB_PREFIX):
        return _rbSettleTick(flow, int(name[len(_RB_PREFIX):]))
    if name.startswith(_ORG_PREFIX):
        return _orgSettleTick(flow, int(name[len(_ORG_PREFIX):]))
    if name.startswith(_CATWAIT_PREFIX):
        return _catWaitTick(flow, int(name[len(_CATWAIT_PREFIX):]))
    if name.startswith(_CATCHARGE_PREFIX):
        return _catChargeTick(flow)
    if name.startswith(_CATFIRE_PREFIX):
        return _catFireTick(flow)
    return False


def _catChargeTick(flow):
    """蓄力**一段 → 二段**：``STATE_SYNC(158)``。

    这一步是「投石车同步动」的关键 —— 158 对应
    ``Battle\\Catapult\\武将_投石车_射击蓄力二段``，它的进入节点会把**器械**的
    行为树切到 ``投石车_蓄力二段``，那才是真正在器械上播的蓄力表现。
    """
    sendActorState(flow, ACT_CATAPULT_CAPACITY_2, "siege-catapult-charge-2")
    _log(flow, "siege-catapult-charge-2 state=%d after=%dms"
         % (ACT_CATAPULT_CAPACITY_2, catapultChargeMs()))
    return True


def _catFireTick(flow):
    """射击动画走完 → 回**待机 2**。

    回 2 是必须的：客户端重进 ``武将_投石车_待机`` 才会把器械行为树切回
    ``投石车_被操控待机``（蓄力计时 start=false），否则器械卡在 ``投石车_射击``
    的姿态里，下次蓄力就再也播不出 Charge 音效。
    """
    sendActorState(flow, ACT_WAIT, "siege-catapult-fire-done")
    _log(flow, "siege-catapult-fire-done state=%d after=%dms"
         % (ACT_WAIT, catapultFireMs()))
    return True


def _catWaitTick(flow, rid):
    """``pushStateChange`` 的第二拍：延时后补 ``STATE_SYNC(ACT_WAIT=2)``。

    这一拍才是「进 ``武将_投石车_待机``」的那一脚 —— 第一拍（1=换出武器）
    只是把人物从当前状态踹出去（状态 1 是死路，所以必须紧跟这一拍）。
    定时器名字已被 ``handleTimer`` 消费掉，不会重入。
    """
    if not sendActorState(flow, ACT_WAIT, "siege-catapult-state-2-wait"):
        return True
    _log(flow, "siege-catapult-state-wait rid=%d after=%dms"
         % (int(rid), catapultStateWaitMs()))
    return True


def _rbSettleTick(flow, rid):
    """``skip_inair_rb`` 的收尾：**动画播完之后**补一条 ``rb_transform`` 把梯子摆到位。

    这是本通道与 ``skip_inair`` 的**唯一差别** —— 顺序走 ``skip_inair``（先动画），
    位姿走 ``rbtrans``（我们算的 46°）。两条都要，就得把位姿**推到动画之后**。

    纯查询 + 一次发送。拿不到物件 / 通道已改 / 不是云梯 ⇒ 静默收工
    （定时器名字已被消费，不会重入；也不会误伤别的通道）。
    """
    try:
        record = _object(flow, rid)
        if record is None:
            _log(flow, "siege-ladder-skiprb-tick rid=%d 查不到物件，跳过" % int(rid))
            return True
        item = record[1]
        if int(item.get("tid")) != LADDER_TID:
            return True
        if ladderRaiseMode(item) != RAISE_SKIP_INAIR_RB:
            _log(flow, "siege-ladder-skiprb-tick rid=%d 通道已不是 skip_inair_rb，跳过"
                 % int(rid))
            return True
        _sendRbTransform(flow, rid, item, ladderRbAngleFor(item))
        _log(flow, "siege-ladder-skiprb-settle rid=%d 动画播完，补位姿收尾（%.4f rad）"
             % (int(rid), ladderRbAngleFor(item)))
    except Exception as error:  # noqa: BLE001 —— 附加能力，坏了不能拖垮 mo 的链
        _log(flow, "siege-ladder-skiprb-tick-failed rid=%d %r" % (rid, error))
    return True


def _orgSettleTick(flow, rid):
    """``incline_org`` 的收尾：**动画播完之后**把该架 CC 物件重发到位。

    与 ``_rbSettleTick`` 同一时序，唯一差别是补的东西：

    ====================  ==========================  ==================
    通道                  补什么                      客户端采纳吗
    ====================  ==========================  ==================
    ``skip_inair_rb``     ``MO_RB_TRANSFORM``         **不采纳**（§10.1）
    ``incline_org``       **重发该架 CC 物件**          **采纳**
    ====================  ==========================  ==================

    纯查询 + 一次重发。拿不到物件 / 通道已改 / 不是云梯 ⇒ 静默收工
    （定时器名字已被消费，不会重入；也不会误伤别的通道）。
    """
    try:
        record = _object(flow, rid)
        if record is None:
            _log(flow, "siege-ladder-org-tick rid=%d 查不到物件，跳过" % int(rid))
            return True
        item = record[1]
        if int(item.get("tid")) != LADDER_TID:
            return True
        if ladderRaiseMode(item) != RAISE_INCLINE_ORG:
            _log(flow, "siege-ladder-org-tick rid=%d 通道已不是 incline_org，跳过"
                 % int(rid))
            return True
        origin, tilt, sent = _applyOrgPose(flow, rid, item)
        _log(flow, "siege-ladder-org-settle rid=%d 动画播完，补 origin=(%.3f,%.3f,%.3f)"
                  " θ=%.4f tilt=%s 重发=%s"
             % (int(rid), origin[0], origin[1], origin[2], ladderAngle(),
                "flat" if tilt is None else "%+.4f" % tilt, sent))
    except Exception as error:  # noqa: BLE001 —— 附加能力，坏了不能拖垮 mo 的链
        _log(flow, "siege-ladder-org-tick-failed rid=%d %r" % (rid, error))
    return True


def _safeCancel(flow, name):
    try:
        flow.cancel(name)
    except (AttributeError, TypeError, ValueError):
        pass


def report():
    """一行启动自证（与 ``[cc]`` / ``[move]`` 那几行同一风格）。"""
    return ("ladder_incline=%s angle=%.4f steps=%d/%dms state=%s raise=%s"
            " orgtilt=%s orgdelay=%dms rb=%.4f/%s/%s rbt=%s rbtz=%s rbs=%dms"
            " crop=%s"
            " cat_control=%s state=%d actor_ntf=%s inter_stop=%s actor_state=%s/%d"
            " cat_weapon=%s/%d@%d wait=%dms cat_events=%s"
            " cat_range=%s/%.1fm cat_toggle=%s"
            " cat_fire=%s/%dms/%dms"
            " cat_shoot=%s/%.1f/%.1f resync=%s flyer=%s@%d"
            " cat_turn=%s/%.1f rb=%s@%.1f cc=%s heading=%s"
            " car_drive=%s auto=%s target=%s hoff=%.1f dmode=%s pose=%s poseface=%s"
            " yawoff=%.1f yawflip=%s yawprobe=%s hang=%s vang=%s vfb=%.1f rotx=%.1f"
            " r=%.1fm scan=%d"
            " mv_state=%d speed=%.2f/%dms open=%s/%dms"
            " ladder_climb=%s w=%.1f cap=%.1f runmin=%.1f run=%.1f rise=%.1f"
            % ("on" if ladderInclineEnabled() else "off", ladderAngle(),
               ladderSteps(), ladderStepMs(), ladderStateMode(),
               ladderRaiseTableTag(),
               ladderOrgTiltTag(),
               ladderOrgDelayMs(),
               ladderRbAngle(), ladderRbPivot(), ladderRbQuatOrder(),
               ladderRbAngleTableTag(), ladderRbTipZTableTag(), ladderRbSettleMs(),
               ladderCropTag(),
               "on" if controlEnabled() else "off", controlState(),
               "on" if actorNtfEnabled() else "off",
               "on" if interactStopEnabled() else "off",
               "on" if actorStateEnabled() else "off", actorStateId(),
               "on" if catapultWeaponEnabled() else "off", catapultWeaponTid(),
               catapultWeaponSlot(), catapultStateWaitMs(),
               str(_knob("cat_events", "T7_CC_CAT_EVENTS", "")[0]).strip() or "-",
               "on" if catapultRangeEnabled() else "off", catapultInteractRadius(),
               "on" if catapultToggleEnabled() else "off",
               "on" if catapultFireEnabled() else "off",
               catapultChargeMs(), catapultFireMs(),
               "on" if catapultShootEnabled() else "off",
               catapultShootSpeed(), catapultShootElevation(),
               "on" if catapultShootResync() else "off",
               catapultShootFlyerMode(), catapultShootFlyerBase(),
               "on" if catapultTurnEnabled() else "off", catapultTurnRate(),
               "on" if catapultTurnRb() else "off", catapultTurnRbStep(),
               "on" if catapultTurnCc() else "off",
               "on" if catapultTurnHeading() else "off",
               "on" if carDriveEnabled() else "off",
               "on" if carAutoEnabled() else "off", carTargetRaw(),
               carHeadingOffset(), carDirMode(),
               "on" if carPoseResend() else "off",
               "on" if carPoseFace() else "off",
               carPoseYawOffset(),
               "on" if carPoseYawFlip() else "off",
               "on" if carPoseYawProbe() else "off",
               "on" if carPoseHangle() else "off",
               carPoseVangleRaw(), carPoseVangleFallbackDeg(), carPoseRotxDeg(),
               carTriggerRadius(),
               carScanEvery(), carMoveState(), carSpeed(), carStepMs(),
               "on" if carOpenLadder() else "off", carOpenMs(),
               "on" if ladderClimbEnabled() else "off", ladderClimbWidth(),
               ladderClimbCap(), ladderClimbRunMin(), ladderClimbRun(),
               ladderClimbRise()))


print("[siege] " + report(), flush=True)
# ⭐ 2026-09-27：同一行**也落盘**到 ``trace_log.txt``。``print`` 只进服务端控制台，
#    离线复盘看不到 —— 而「三架的通道 / CropTime 到底进没进进程」必须可离线核对。
#    落盘后直接 ``grep "siege-boot" server/data/trace_log.txt`` 即可。
try:
    tracelog.emit("siege", "", "[siege-boot] " + report())
except Exception:  # noqa: BLE001
    pass
