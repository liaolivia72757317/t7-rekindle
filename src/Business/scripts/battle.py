"""服务端权威动作状态下发：四向攻击（左键）与四向招架（右键）。

证据边界（重要，请勿把未闭合项当已恢复）
----------------------------------------
已闭合、可依赖：

* C2S 报文布局——TDR 结构 ``CS_PROTO_BATTLE_BATTLE_MSG`` 直接给出，并由实机报文逐条校验，
  body 恰好 11 字节：

      @0  u16  selector = 1（ENM_BATTLE_CMD_BATTLE_MSG）
      @2  u32  key_comb      "当前的按键组合"
      @6  i32  msg_id        "消息id"
      @10 i8   is_auto_parry "是否为自动格挡"

  ``msg_id`` 在 wire 上就是完整 u32（300540 等），不要再叠加任何前缀。
  ``key_comb``：**是 ``KEY_*`` 枚举的位图**（不是“bit7=按下标志、低4位=方向”那种
  旧描述，那只是恰好成立的巧合）。实测含义：bit0/bit1 = 鼠标方向的两比特
  （0b00=下、0b01=右、0b10=上、0b11=左，故低半字节恒为 0xC..0xF），
  bit2/bit3 = KEY_ATTACK|KEY_MODE（所有动作共同置位），bit7 = 左键、bit8 = 右键、
  bit9 = F、bit11 = E、bit14 = X、bit21 = SHIFT、bit22 = 双击W、bit26 = 快跑状态。
  注意 ``is_auto_parry`` 是布尔标志（0/1），**不是常量**，只做取值域校验。
  实机抓到的每个包都是 1，所以放宽到 {0,1} 是防御性处理而非实机证据驱动；
  旧实现断言 ``== 1`` 在本机从未真正触发过（会话 13496 的 26 条 ERROR 全是 tuple 错误）。

* 四向攻击意图：上 300560、右 300550、下 300570、左 300540；释放 300020。
* 四向招架意图：上 300580、右 300590、下 300600、左 300610；释放 300620。
* 特殊键意图（**2026-09-18 新增**）：F 前踢 300700、E 盾击 300110、
  X 挽剑花 300738、SHIFT+快跑 冲刺攻击 700250。它们的 key_comb 专属位与状态号
  见 ``SPECIAL_CHAIN_BY_INTENT`` 上方那段注释——那是 Hero72 总表第七节当时缺的
  “同帧关联物理键 -> CMD identity -> 战斗缓存”证据。
* 四向攻击四阶段与四向招架三阶段的状态号：见下面两个 ``*_CHAIN_BY_INTENT``。
* 按住/松开语义：见 ``ATTACK_HOLD_INDEX`` 附近注释。
* ``key_comb`` 就是 ``KEY_*`` 位图：bit0/bit1=鼠标方向两比特、bit2/bit3 恒置位
  (KEY_ATTACK|KEY_MODE)、bit7=左键、bit8=右键、bit9=F、bit11=E、bit14=X、
  bit21=SHIFT、bit22=双击W、bit26=快跑状态。

未闭合、本模块不声称已恢复：

* 各阶段 duration 的**归属行**。时长数值本身现在有出处了——见 ``PHASE_MS_PREPARE``
  上方那段注释：客户端 ``s_battle_param_cli.bin`` 的 ``[持续时间]预备/过程/结束``
  字段已被离线读出，四向攻击与特殊键都用表值。仍未闭合的是“某个武器/复合键
  对应表里哪一行”，所以用的是按 ``[攻击类型]`` 名字精确匹配后的中位数，不是
  逐武器查表结果。
* ``state_time_ms`` 语义：沿用既有 initializeBattleState 的 0，不伪造动作时长。
* 特殊键的“msg -> state 链”是按**名字一一对应**推得的，仍缺原服 STATE_SYNC 正样本；
  实机若不表现，优先怀疑这一层而不是 key_comb 位定义。
* SHIFT 的**单键**上行：客户端从未把快跑写进 MOVE_KEY 位图，快跑走的是
  ``cmd=2 selector=63 CS_PROTO_MOVE_FAST_RUN_REQ``，该报文由 ``controls.py`` 处理；
  它与 SHIFT 单键的绑定尚未由实机单键测试确认。
* 格挡成功/被格挡（``sub6 BLOCK`` / ``sub7 BE_BLOCKED``）与命中结果：客户端碰撞见证，
  不是权威结果；``ACT_STATE_BLOCK_SUCCESS=94``、盾牌格挡成功 111 等也没有
  “某个格挡声明 -> 具体 state/结果”的 producer 映射，故不驱动。
* 跳跃：需要 root4 空中/落地状态，而该状态号未闭合；本模块不涉及。

实现约束（踩过的坑，务必遵守）
------------------------------
``flow.session`` 会被原生层序列化并做类型校验，**只允许 JSON 可表达的类型**。
把 tuple 写进 session 会让原生层报 ``unsupported state type: tuple`` 并**丢弃整个
事件**——表现为“按下了但既没日志也没下行”，且该事件的 state 不会提交（于是后续
release 会报 ``release-without-press``）。因此：

* 会话内只存标量（int/bool/None）。链按 ``intent`` 现查 ``_specOf``，
  **不要把链本身存进 session**。
* ``t7_battle_verify.py`` 里有 ``assertJsonSafe`` 回归，覆盖这一条。
"""
from __future__ import annotations

import math
import os
import struct

from . import contracts as wire
# ⭐ 2026-10-03：关卡 NPC（训练场陪练）**可击败化** —— 服务端伤害模型。
# 放在模块级 import 是为了让它的启动自证行 `[npc-battle] …` 与 `[npc]` 一起打出来；
# 真正调用点在下面 `message()` 的「过程」拍。它是**附加能力**，任何异常都被
# 调用点 try 掉，绝不影响本文件已验证的攻击/招架链。
from . import npc_battle
from .codec import battle_flow
from .codec import vision_flow

# --- C2S 报文常量（TDR CS_PROTO_BATTLE_BATTLE_MSG + 实机校验） ---
BATTLE_MESSAGE_SELECTOR = 1
BATTLE_MESSAGE_BODY_LENGTH = 11

# --- 攻击意图 id（实机报文 + Hero72 总表第四节） ---
ATTACK_INTENT_UP = 300560
ATTACK_INTENT_RIGHT = 300550
ATTACK_INTENT_DOWN = 300570
ATTACK_INTENT_LEFT = 300540
ATTACK_RELEASE = 300020

# --- 招架/格挡意图 id（实机报文 + Hero72 总表第五节） ---
BLOCK_INTENT_UP = 300580
BLOCK_INTENT_RIGHT = 300590
BLOCK_INTENT_DOWN = 300600
BLOCK_INTENT_LEFT = 300610
BLOCK_RELEASE = 300620

# --- 换武器 / 武器模式 / 投掷（2026-09-25 宏表 dump + 实机 battle-raw，非推断） ----
# RES_MSG_CHANGE_WEAPON=300715 换出武器消息（暂未用：换武器走 cmd5 sel1/3 那条链）
# RES_MSG_WEAPON_MODE_SWITCH=300718 武器模式切换消息（X 键在双模武器上的功能，
#   如飞戟 近战↔投掷；轮盘上「[X] 切换模式」提示的就是它。⚠️ 实机按 X 客户端
#   **没发**这条，仍被本机挡着，先挂着等它出现）
# RES_MSG_THROW=300670 投掷消息 —— 2026-09-25 实机：切到飞戟后左键发的就是它
#   （key_comb=141 按下 / 13 松开 300020），不是宏表里名字更像入口的 300719。
# 状态号取自 out/act_state_macros_full.md：1=ACT_STATE_CHANGE_WEAPON、
# 347=ACT_STATE_WEAPON_MODE_SWITCH、321..324=THROW_PREPARE/CAPACITY/PROCESS/END。
WEAPON_MODE_SWITCH = 300718
THROW = 300670
CHANGE_WEAPON_STATE = 1
MODE_SWITCH_STATE = 347

# --- 弓（步兵神射）左键射击（2026-10-03 实机定案） ------------------------------
# 用户反馈「职业训练 弓箭 点左箭 不射箭」。实机会话 `50180-1120816275`（关卡
# 10015 = `xsc1` = 职业训练 2 神射·弓箭，卡片 14）里，客户端左键按下报的是：
#
#     battle-raw key_comb=141 msg_id=300680     ← 左键(bit7)+攻击(bit2)+模式(bit3)+方向bit0
#     battle-raw key_comb=14  msg_id=300020     ← 松开（与四向近战同一个释放 id）
#
# 服务端当时对 300680 落到「其余战斗意图」分支 ⇒ 只记 `battle-message-recorded`、
# **一个包都不发**；随后 300020 因为 `action["index"] < 0` 变成
# `battle-release-without-press` ⇒ 客户端等不到权威状态 ⇒ 表现为**不射箭**。
#
# 入口状态不是猜的，读客户端 `s_act_state_cli.bin`（人物状态机的
# (source,msg)->target 转移表，工具 `hkx_decode/act_state_edges.py`）：
#
#     2(待机) --300680--> 164  弓发射预备          ← 入口
#     164/165/167/219/220 --300660--> 326 弓发射取消   ← 右键取消拉弓
#     167(弓发射蓄力) --300020--> 168 弓发射过程    ← **只有蓄力那一拍认释放**
#     219/220(蓄力抖动一/二段) --300020--> 168
#
# ⇒ 弓的链是 **5 拍**（比四向近战多一拍「拉弓」），且按住必须停在 **167 蓄力**、
#   松开才进 **168 过程**。所以它**不能共用** ATTACK_HOLD_INDEX/ATTACK_RELEASE_INDEX。
#   164→165→167 与 168→169→2 都不在这张表里（同四向链：链内推进靠服务端定时器）。
BOW_SHOOT = 300680     # 左键按下：进入弓射击链
BOW_CANCEL = 300660    # 右键按下：取消拉弓（164/165/167/219/220 -> 326）
BOW_SHOOT_READY = 164  # ACT_STATE_BOW_SHOOT_READY  弓发射预备
BOW_SHOOT_DRAW = 165   # ACT_STATE_BOW_SHOOT_DRAW   弓发射拉弓
BOW_SHOOT_CAPACITY = 167  # ACT_STATE_BOW_SHOOT_CAPACITY 弓发射蓄力
BOW_SHOOT_PROCESS = 168   # ACT_STATE_BOW_SHOOT_PROCESS  弓发射过程
BOW_SHOOT_END = 169       # ACT_STATE_BOW_SHOOT_END      弓发射收招
BOW_SHOT_CANCEL_STATE = 326  # ACT_STATE_BOW_SHOT_CANCEL 弓发射取消

# --- 盾牌防御（`key_comb` bit4 = KEY_SHIELD；客户端 `msg_id` 恒为 0） --------------
# 用户反馈：`铁骑训练场 防御训练  3切换盾牌 ，不是防御状态`。
#
# 实机会话 `50180-1120816275`（关卡 10073 = 训练场_招架，卡片 6009）里，切到 3 号槽
# （盾，tid=1070231）之后，客户端按右键发的是：
#
#     battle-raw key_comb=284 msg_id=0  hex=00010000011c0000000000   ← 盾+右键按下
#     battle-raw key_comb=29  msg_id=0  hex=00010000001d0000000000
#     battle-raw key_comb=285 msg_id=0
#     battle-raw key_comb=30  msg_id=0
#     battle-message-recorded key_comb=284 msg_id=0                  ← 服务端只记不发
#
# ⇒ **盾牌状态只通过 `key_comb` 的 bit4 上报，`msg_id` 恒为 0**，服务端一个包都不发
#   ⇒ 客户端等不到权威状态 ⇒ 人物不进防御姿势。
#
# 证据（不是推断）：
#   * 客户端宏表 `KEY_SHIELD = 4`（`sh_proto_cs`，与 `KEY_LEFT_MOUSE=7` /
#     `KEY_RIGHT_MOUSE=8` / `KEY_ATTACK=2` / `KEY_MODE=3` 同一张位图表）；
#     284 = 0x11C = bit2|bit3|bit4|bit8 = 攻击+模式+**盾**+右键。
#   * 全局扫 `data/*/wire/frames-1.jsonl`：**4 个会话**都只出现过 `msg_id=0` 的
#     盾位报文（28/29/30/31、157/158/159、284..287、67108892..67109023 等）。
#   * 状态表里确实有 `2(待机) --300130--> 95 盾牌防御预备`，
#     但 **`300130` / `300140` 在全部历史会话里一次都没出现过** ⇒ 不能等它。
#
# 链（状态表 `s_act_state_cli.bin` + 宏表名字）：
#     95  盾牌防御预备  ACT_STATE_SHIELD_DEFENCE_START
#     309 盾牌防御蓄力  ACT_STATE_SHIELD_DEFENCE_CAPACITY
#     34  盾牌防御过程  ACT_STATE_SHIELD_DEFENCE      ← 名字就叫「盾牌防御」
#     96  盾牌防御结束  ACT_STATE_SHIELD_DEFENCE_END
# 表证：`2 --300130--> 95`（入口）、`309 --300140--> 34`、`34 --300130--> 309`；
# 95→309 与 34→309 的推进表里没有边 ⇒ 同其他链，链内推进由服务端定时器负责。
#
# ⚠️ **链的具体形态是本轮唯一没有实机正样本的部分**（没有任何历史会话进过这个状态），
#    所以整条链做成**可配**：`T7_SHIELD_CHAIN=95,309,34`（默认停最后一拍）、
#    `T7_MS_SHIELD_DEF=360,360,360`（逐拍时长）。实机不对就改这两个，别改代码。
KEY_BIT_SHIELD = 4            # KEY_SHIELD（宏表实证）=「**手上有盾**」，不是「举盾」
# ⭐⭐ 2026-10-03 18:5x 实机修正（会话 50180-1122868420）：
#   **bit4 只是「手上有盾」（装备态），bit8（KEY_BIT_RIGHT_MOUSE）才是「右键按住」**。
#   切到 3 号槽（盾 tid=1070231，走 cmd=5 换武器链，见 event=8030 weapon-change-rsp）
#   之后 bit4 就**一直置位**；按/松右键只切 bit8：
#     285 = 0x11D = 盾(16)|右键(256)|模式(8)|攻击(4)|方向bit0(1) ← 举盾（event=8034 → 发 95 ✅）
#      29 = 0x01D = 盾(16)|模式(8)|攻击(4)|方向bit0(1)          ← 右键已松（event=8038 → 当时**没发 96** ❌）
#      28 = 0x01C = 盾(16)|模式(8)|攻击(4)                      ← 仍是持盾（只是方向bit0=0）
#      13 = 0x00D = 模式(8)|攻击(4)|方向bit0(1)                 ← **无盾**（换走盾牌）
#     157 = 0x09D = 左键(128)|盾(16)|模式(8)|攻击(4)|方向bit0(1) ← 持盾时的左键
#   ⚠️ 注意 **28 的 bit4 也是 1**（28=16+8+4），别把 28 当「无盾」；无盾是 13/12。
#   旧实现只看 bit4 ⇒ ① 一切盾牌就自动举盾；② 松右键时 want 仍为 True、与 up 相等
#   ⇒ 放行 ⇒ **盾收不回来**、之后怎么按都没反应。⇒ 判据必须是「**持盾 AND 右键**」。
SHIELD_READY = 95             # ACT_STATE_SHIELD_DEFENCE_START      盾牌防御预备
SHIELD_CAPACITY = 309         # ACT_STATE_SHIELD_DEFENCE_CAPACITY   盾牌防御蓄力
SHIELD_DEFENCE = 34           # ACT_STATE_SHIELD_DEFENCE            盾牌防御过程
SHIELD_END = 96               # ACT_STATE_SHIELD_DEFENCE_END        盾牌防御结束
SHIELD_CHAIN = (SHIELD_READY, SHIELD_CAPACITY, SHIELD_DEFENCE)
SHIELD_CHAIN_ENV = "T7_SHIELD_CHAIN"
SHIELD_TIMER = "battle-shield-phase"
# 逐拍时长默认 = PHASE_MS_PREPARE（占位值，**非表值**；`propsheet/盾类.psheet` 同弓类
# 一样是空表）。⚠️ 不能在这里直接写 `PHASE_MS_PREPARE` —— 它在文件更下面才定义。
# 实际取值走 `shieldBeats()`，默认即 `(PHASE_MS_PREPARE,) * len(chain)`。
SHIELD_MS_ENV = "T7_MS_SHIELD_DEF"
_shieldWarned = set()

DIRECTION_NAME = {
    ATTACK_INTENT_UP: "up", ATTACK_INTENT_RIGHT: "right",
    ATTACK_INTENT_DOWN: "down", ATTACK_INTENT_LEFT: "left",
    BLOCK_INTENT_UP: "up", BLOCK_INTENT_RIGHT: "right",
    BLOCK_INTENT_DOWN: "down", BLOCK_INTENT_LEFT: "left",
    THROW: "throw",
    BOW_SHOOT: "bow",
}

# --- 特殊键战斗意图：F（前踢）/ E（盾击）/ X（挽剑花）/ SHIFT（冲刺攻击） ---------
# 证据链（2026-09-18，来自客户端宏表 + 7 个会话的实机 battle-raw 全量配对，非推断）：
#
# 1) 宏表给出 key_comb 的位定义（`KEY_*` 枚举，声明顺序即值）：
#      7=KEY_LEFT_MOUSE  8=KEY_RIGHT_MOUSE  9=KEY_F   10=KEY_T   11=KEY_E
#      12=KEY_Z  13=KEY_R  14=KEY_X  15=KEY_W  16=KEY_S  17=KEY_A  18=KEY_D
#      19=KEY_V  20=KEY_B  21=KEY_SHIFT  22=KEY_DOUBLE_W …  26=KEY_RUN  28=KEY_LOCK
#    另有 `KEY_COND_*` 表（15/16=左键按下/松开、17/18=右键按下/松开、
#    19/20=F 按下/松开、23/24=E 按下/松开、29/30=X 按下/松开、
#    43/44=SHIFT 按下/松开、53=快跑、54=非快跑）。
#
# 2) 实机 (key_comb, msg_id) 配对证明 key_comb 就是上面这张位图，且每个特殊键
#    恰好只有 1 个专属位（bit2/bit3 = KEY_ATTACK|KEY_MODE 是所有动作的共同置位，
#    bit0/bit1 = 鼠标方向的两比特）：
#
#      bit9  (KEY_F)                  -> 300700 RES_MSG_FORWARD_KICK     前踢
#                                       实测 key_comb=525(0x20D)/526(0x20E)
#      bit11 (KEY_E)                  -> 300110 RES_MSG_SHIELD_ATTACK    盾击
#                                       实测 key_comb=2062(0x80E)
#      bit14 (KEY_X)                  -> 300738 RES_MSG_SWORD_FANCY_WAVE 挽剑花
#                                       实测 key_comb=16396/16397/16398(0x400C/0x400D/0x400E)
#      bit21 (KEY_SHIFT)+bit26(KEY_RUN)-> 700250 RES_MSG_DASH_ATTACK      冲刺攻击
#                                       实测 key_comb=69206030/69206031(0x420000E/0x420000F)
#
#    这正是《Hero72 待机移动攻防跳跃特殊键动作映射只读总表》第七节当时缺的那条
#    “同帧关联物理键 -> CMD identity -> 战斗缓存”证据：该节只看到 F/E/Shift 与
#    视角包、左键意图混在同一时间窗，因此当时裁定“不能把包分别命名为 F、E、Shift”。
#    现在 key_comb 的位与 msg_id 在同一帧内一一对应，命名不再靠时间窗猜测。
#
# 3) 状态号同样取自这张宏表，且与 msg 名一一对应：
#      335/336/337 = ACT_STATE_FORWARD_KICK_PRAPARE/PROCESS/END（前踢 预备/过程/结束）
#      32          = ACT_STATE_SHIELD_ATTACK（盾击，单状态）
#      517         = ACT_STATE_SWORD_FANCY_WAVE（挽剑花，单状态）
#      439/440     = ACT_STATE_DASH_ATTACK_PROCESS/END（冲刺攻击 过程/结束）
#
# 4) 链的**入口**不再靠名字猜，改成读 s_act_state_cli.bin 的 (source,msg)->target
#    转移表（2026-09-19）。该表把“客户端本机按这个键会进哪个状态”写死了，而客户端
#    的本机预测表必须与服务端下发一致，所以它同时就是服务端的入口状态：
#
#      300700 -> 335  x61 来源（含 2/34）        前踢：入口是**预备**拍
#      300110 -> 32   x63 来源                   盾击：入口即唯一状态
#      300738 -> 517  x37 来源                   挽剑花：入口即唯一状态
#      700250 -> 439  x3  来源=[2,34,517]        冲刺攻击：入口是**过程**拍
#
#    冲刺攻击这一条是本次唯一改动：旧值写的 438 是 ACT_STATE_DASH_ATTACK_PRAPARE，
#    但 438 在**全表 445 行、约 1.9 万条非零转移里的入边数是 0**——没有任何报文能进
#    去；而 700250 从待机(2)、盾牌防御(34)、挽剑花(517) 三处都直接落到 439。也就是说
#    冲刺攻击在客户端本机根本没有“预备”这一拍（预备动作就是按 SHIFT 快跑本身），
#    服务端若先下发 438，客户端会播一段真实游戏里不存在的预备动画。
#
#    同一个道理也解释了 336/337（前踢过程/结束）与 440（冲刺攻击结束）入边同为 0：
#    它们由**服务端定时器**推进，不走客户端本机报文表。这与本文件的实现方式一致。
#
# 未闭合（请勿当成已恢复）：
#   * 仍缺原服 STATE_SYNC 正样本；本表证明的是“入口状态”，链内后续拍的推进节奏
#     依旧是服务端定时器行为，无法从本表读出。
#   * 冲刺攻击存在**第二套**入口：700250 从 550/551（ACT_STATE_NONE_BATTLE_WAIT /
#     ACT_STATE_BATTLE_WAIT，见下方“两代 act state 机”注）-> 599
#     ACT_STATE_NEW_DASH_ATTACK_PROCESS。当前按旧代 439 实现，未切换。
#   * 特殊键只有“按下”一个边沿（客户端不发释放报文），所以链按自动走完处理；
#     各阶段 duration 见 SPECIAL_BEATS_BY_INTENT（**只有“过程”那一拍是表值**，
#     预备/结束是占位值，详见该常量上方 2026-09-19 的复核说明）。
FORWARD_KICK = 300700
SHIELD_ATTACK = 300110
SWORD_FANCY_WAVE = 300738
DASH_ATTACK = 700250

SPECIAL_CHAIN_BY_INTENT = {
    FORWARD_KICK: (335, 336, 337),
    SHIELD_ATTACK: (32,),
    SWORD_FANCY_WAVE: (517,),
    # 439 起，不是 438 起——依据见上方第 4 条（438 入边为 0，700250 直接落 439）。
    DASH_ATTACK: (439, 440),
    WEAPON_MODE_SWITCH: (MODE_SWITCH_STATE,),
}

SPECIAL_NAME_BY_INTENT = {
    FORWARD_KICK: "forward-kick",
    SHIELD_ATTACK: "shield-attack",
    SWORD_FANCY_WAVE: "sword-fancy-wave",
    DASH_ATTACK: "dash-attack",
    WEAPON_MODE_SWITCH: "weapon-mode-switch",
}

# 每个特殊意图的专属按键位（KEY_* 索引）。冲刺攻击是 SHIFT+RUN 组合，没有单一
# 专属位，取 None —— 只做日志，不做校验。
SPECIAL_KEY_BIT_BY_INTENT = {
    FORWARD_KICK: 9,        # KEY_F
    SHIELD_ATTACK: 11,      # KEY_E
    SWORD_FANCY_WAVE: 14,   # KEY_X
    WEAPON_MODE_SWITCH: 14, # KEY_X（双模武器上 X 的功能是切模式，不是挽剑花）
    DASH_ATTACK: None,
}

KEY_BIT_LEFT_MOUSE = 7
KEY_BIT_RIGHT_MOUSE = 8
KEY_BIT_SHIFT = 21
KEY_BIT_DOUBLE_W = 22
KEY_BIT_RUN = 26

# --- 两代 act state 机并存（2026-09-19 从 s_act_state_cli 发现，**尚未闭合**）---------
#
# 该表里除了本文件使用的这一代（state 1..~540，报文 3005xx/3006xx/3007xx），还有一整套
# **新一代**状态机，入口是两个“等待”态，报文号也是另一套：
#
#   550 ACT_STATE_NONE_BATTLE_WAIT / 551 ACT_STATE_BATTLE_WAIT
#       300763 NEW_DEFENCE      -> 552 ACT_STATE_NEW_DEFENCE
#       300765 LIGHT_ATTACK     -> 564 ACT_STATE_TRIPLE_LIGHT_ATTACK_FIRST
#       300750 HEAVY_ATTACK     -> 567 ACT_STATE_TWICE_HEAVY_ATTACK_FIRST
#       300769/300770/300771/300772 NEW_DODGE_* -> 553/554/555/556
#       700250 DASH_ATTACK      -> 599 ACT_STATE_NEW_DASH_ATTACK_PROCESS
#       300751 GRAB_THROW       -> 582   300763 NEW_DEFENCE -> 552
#
# 当前服务端绑定的是**旧代**报文，且 2026-09-18 实机验收“基本可以”，说明 T7 客户端
# 走的是旧代。但新一代那批报文号（300750~300772）客户端是否也会发、什么条件下发，
# 未闭合。实机若出现“某个键完全没反应”，优先回这张表查是不是落到了 550/551 那一支。

# 四向攻击四阶段（s_act_state_cli 固定行表；Hero72 总表第四节与
# 2026-08-10《Hero1101四向攻防恢复依据与实机门》第二节第 1 条两处独立记录一致）：
#   上 235 -> 237 -> 238 -> 239 -> 2
#   右 241 -> 243 -> 244 -> 245 -> 2
#   下 247 -> 249 -> 250 -> 251 -> 2
#   左 253 -> 255 -> 256 -> 257 -> 2
# 语义：预备 -> 蓄力 -> 过程 -> 结束，随后回待机 2。
ATTACK_CHAIN_BY_INTENT = {
    ATTACK_INTENT_UP: (235, 237, 238, 239),
    ATTACK_INTENT_RIGHT: (241, 243, 244, 245),
    ATTACK_INTENT_DOWN: (247, 249, 250, 251),
    ATTACK_INTENT_LEFT: (253, 255, 256, 257),
    # 投掷：预备 -> 蓄力(按住瞄准) -> 松开进 **363 THROW_DEFORE_SHAKE(出手前摇)**
    # -> 过程(掷出) -> 结束。363 这一拍之前被我们跳掉了：2026-09-25 重抽客户端
    # s_act_state_cli.bin（MSES v7，row10950×445）逐行核对，322 行的释放边是
    # ``300020 -> 363``（另有 ``700101 -> 363``），而 322/323/324 在**全表没有任何
    # 入边**——363 之后的推进与四向链一样全靠服务端定时器推，链内容必须与表一致。
    # 入口 300670 是实机值（切飞戟后左键就是它）：2/34/111/325 等状态按 300670 进 321。
    THROW: (321, 322, 363, 323, 324),
}

# 四向招架三阶段（同表逐项验证；《Hero1101四向攻防恢复依据与实机门》第二节第 2 条）：
#   上 305 -> 223 -> 227；右 308 -> 224 -> 228
#   下 307 -> 225 -> 229；左 306 -> 226 -> 230
# 语义：预备 -> 蓄力 -> 过程，松开回待机。
PARRY_CHAIN_BY_INTENT = {
    BLOCK_INTENT_UP: (305, 223, 227),
    BLOCK_INTENT_RIGHT: (308, 224, 228),
    BLOCK_INTENT_DOWN: (307, 225, 229),
    BLOCK_INTENT_LEFT: (306, 226, 230),
}

# 按住 / 松开语义（来源：《Hero1101四向攻防恢复依据与实机门》第三节第 3 条——
# “左键支持按住蓄力和快速点击释放；右键按住依次进入对应方向的预备、蓄力、过程，松开回待机”）。
#
# 这解决的是 2026-09-18 实机反馈“按左右键自动松开了，不松开是蓄力”：
# 旧实现按固定节拍把整条链推完，无论按键是否还按着，于是按住也会在约 4*200ms
# 后自动回待机，后续 release 报 release-without-press。
ATTACK_HOLD_INDEX = 1      # 蓄力：按住时停在这一阶段
ATTACK_RELEASE_INDEX = 2   # 过程：松开后从这一阶段继续走完
BLOCK_HOLD_INDEX = 2       # 过程：招架链最后一阶段，按住时停在这里
ACTION_IDLE_STATE = battle_flow.ACT_STATE_IDLE

# --- 弓射击链（5 拍，自己的 hold/release 索引） ------------------------------------
# 语义：预备(164) -> 拉弓(165) -> 蓄力(167, **按住停这里**) -> 过程(168, 松开发射)
#       -> 收招(169) -> 待机(2)
# 与四向近战的区别：多一拍「拉弓」，所以 hold 落在索引 **2** 而不是 1；
# 释放边只有 `167 --300020--> 168` 这一条（表证），所以 release 是索引 **3**。
# 表里 164→165→167 与 168→169→2 都没有边 —— 与四向链同理，链内推进靠服务端定时器。
BOW_CHAIN = (BOW_SHOOT_READY, BOW_SHOOT_DRAW, BOW_SHOOT_CAPACITY,
             BOW_SHOOT_PROCESS, BOW_SHOOT_END)
BOW_HOLD_INDEX = 2
BOW_RELEASE_INDEX = 3

# 覆盖默认 hold/release 的意图（键 = intent，值 = (chain, holdIndex, releaseIndex)）。
# ⚠️ 这张表**不进 session**（tuple 会被原生层拒绝，见文件头「只存标量」纪律）。
CHAIN_SPEC_BY_INTENT = {
    BOW_SHOOT: (BOW_CHAIN, BOW_HOLD_INDEX, BOW_RELEASE_INDEX),
}

ACTION_TIMER = "battle-action-phase"

# --- 各阶段时长：**表值**，来源是客户端 s_battle_param_cli.bin ----------------------
#
# 之前这里写的是“按实机反馈标定”，那是不老实的措辞——那几个数（200/400/700）只是
# 我自己拍的占位值，唯一的输入是“有点快”这句话。2026-09-19 已经把真正的表读出来了：
#
# 出处：D:\刀锋铁骑\vfs\data1.vfs 主数据区 gap 流里解出的 MSES v7 表
#       gapstream_935 @ +7844144，header 140、record 6412、767 行，
#       文件尺寸 4,918,144 B —— 与《Hero72权威战斗表武器动作连接只读复核》§1
#       记录的 s_battle_param_cli.bin（4,918,144B / row6412×767）逐字节吻合。
#       行布局 = 12B 复合键 + 50 组 (64B 字段名, 64B 文本值)；
#       ``[持续时间]预备`` / ``[持续时间]过程`` / ``[持续时间]结束`` 的值就是毫秒。
#       离线复算脚本：t7_dur_stats.py / t7_query_durations.py（工作区根目录）。
#
# 全表统计（n 为填了该字段的行数）：
#   预备  n=348  中位 360ms   p25 360  p75 400   max 600
#   过程  n=528  中位 700ms   p25 640  p75 1200  max 5333
#   结束  n=353  中位 500ms   p25 400  p75 636   max 2833
#   三段齐全行的合计：n=348 中位 1500ms
#
# ⚠️⚠️ 2026-09-19 实机反馈「F 会卡一下 / SHIFT 滑得有点远」之后复核出来的**取法错误**：
#
#   (1) ``[持续时间]预备`` 与 ``[持续时间]结束`` **在带 ``[攻击类型]`` 的行里一次都没出现**
#       （复核实测：预备非空 348 行、结束非空 353 行，其中带 ``[攻击类型]`` 的 = **0 行**）。
#       也就是说**六种攻击动作只有“过程”一拍有表值**，预备/结束那两拍是**别的行类**
#       （受击/防御/招架类，键形如 ``a=2 b=(1,1)``）才有的字段。
#       本文件原来把这两个中位数（360 / 500）当“表值”填进攻击链，**是错的**——
#       它是从另一类行借来的，套到攻击上就是凭空多出两拍冻结。
#   (2) 冲刺攻击行另有 ``[移动时间]``（760/800/967/1033）与 ``[移动初速度]``
#       （2226/2500/2585/3950）——**位移只持续“移动时间”那么久**，不是整个“过程”。
#       脚踢攻击行**没有**这两个字段，所以前踢本身不产生位移。
#       本文件完全没有实现这个位移模型，所以“过程”那一拍有多长，客户端就滑多久。
#
# 按 ``[攻击类型]`` 名字精确匹配的特殊动作行（这些行**只有**“过程”，且按武器分行）：
#   脚踢攻击（F 前踢）  过程 = 1000 / 1333 / 1500                （3 行，各一个值）
#   冲刺攻击（SHIFT）   过程 = 1400 / 1900 / 1933×8 / 2033×7     （17 行，众数 1933）
#   ※ 同一个攻击类型在不同武器下行数不同、值能差 30%+，所以“众数/中位”只是
#     **不知道武器时的兜底**，不是精确值。
PHASE_MS_PREPARE = 360     # [持续时间]预备 中位（n=348，**非攻击行**）
PHASE_MS_PROCESS = 700     # [持续时间]过程 中位（n=528）
PHASE_MS_END = 500         # [持续时间]结束 中位（n=353，**非攻击行**）

# 每个特殊意图**逐拍**的时长（毫秒）；链有几个状态就有几个数，顺序与
# SPECIAL_CHAIN_BY_INTENT 一致。
#
# ⚠️ 只有**“过程”那一拍**是攻击行的表值；前踢的首拍 360、末拍 500 和冲刺的末拍 500
#    都是上面那两个**非攻击行**的中位数，属于**占位值，不是表值**（见上方复核说明）。
#    实机反馈的「F 卡一下」就是这两拍造成的。
#
# 冲刺攻击只有两拍：439 过程（表值众数 1933）+ 440 结束（占位 500）。
# 原来那个开头的 PHASE_MS_PREPARE 随 438 一起去掉了——没有预备状态，就没有预备拍。
SPECIAL_BEATS_BY_INTENT = {
    FORWARD_KICK:     (PHASE_MS_PREPARE, 1333, PHASE_MS_END),
    SHIELD_ATTACK:    (1333,),
    SWORD_FANCY_WAVE: (1333,),
    DASH_ATTACK:      (1933, PHASE_MS_END),
    # 占位值：表里有 REFER_PARAM_TIME_SWITCH_MODE 但还没读出该行数值。
    WEAPON_MODE_SWITCH: (PHASE_MS_END,),
}

# --- 特殊键逐拍时长的**运行时可覆盖**（默认不生效，不改现有行为） -------------------
# 为什么需要：表值是**按武器分**的，同一个攻击类型在不同武器下能差 30%+，
# 而服务端目前**不知道玩家拿的是什么武器**（没有场景/装备概念）。所以留一个
# 不改代码、不重启就能试的口子，在实机上把值调到对为止：
#
#     T7_MS_KICK=1000,1333,500     前踢 逐拍（**拍数必须与链长相等**，否则整条忽略）
#     T7_MS_DASH=1400,500          冲刺攻击 逐拍
#     T7_MS_SHIELD=1000            盾击
#     T7_MS_FANCY=1000             挽剑花
#
# 调对了再回来改 SPECIAL_BEATS_BY_INTENT，并把来源写清楚。
SPECIAL_MS_ENV = {
    FORWARD_KICK: "T7_MS_KICK",
    SHIELD_ATTACK: "T7_MS_SHIELD",
    SWORD_FANCY_WAVE: "T7_MS_FANCY",
    DASH_ATTACK: "T7_MS_DASH",
}
_specialMsWarned = set()

# --- 弓射击链的逐拍时长（毫秒，顺序与 BOW_CHAIN 一致） ---------------------------
# 诚实标注（本仓库纪律：占位值不许写成「表值/实机标定」）：
#   * 索引 3（168 弓发射过程）= **表值量级** PHASE_MS_PROCESS(700)，
#     与 THROW 用的是同一个「[持续时间]过程 中位」口径；
#   * 索引 0/1/2（164/165/167）与索引 4（169 收招）= **占位值**。
#     168 之前那三拍本应查 `propsheet/弓类.psheet`，但实测该文件**是空表**
#     （只有 Header，150B），拿不到逐状态时长 ⇒ 先用 PHASE_MS_PREPARE/END 兜底。
#   * 索引 2 的时长其实**永远用不到**：按住时定时器在 `nextIndex > holdIndex`
#     处停住，不会再排下一拍。
# 实机若觉得「拉弓太快/收招太慢」，用环境变量调，别改常量：
#     T7_MS_BOW=360,360,360,700,500      （拍数必须与链长相等，否则整条忽略）
BOW_BEATS_MS = (PHASE_MS_PREPARE, PHASE_MS_PREPARE, PHASE_MS_PREPARE,
                PHASE_MS_PROCESS, PHASE_MS_END)
BOW_MS_ENV = "T7_MS_BOW"
_bowMsWarned = set()


def specialBeats(intent):
    """该意图实际生效的逐拍时长：环境变量覆盖优先，否则用 ``SPECIAL_BEATS_BY_INTENT``。

    覆盖串拍数与链长不一致时**整条忽略**（宁可走兜底，也不要下发半条链的时长）。
    """
    beats = SPECIAL_BEATS_BY_INTENT.get(intent)
    name = SPECIAL_MS_ENV.get(intent)
    if not name:
        return beats
    raw = os.environ.get(name)
    if not raw:
        return beats
    try:
        parsed = tuple(int(part) for part in raw.split(","))
    except ValueError:
        parsed = ()
    if not parsed or any(value <= 0 for value in parsed):
        if name not in _specialMsWarned:
            _specialMsWarned.add(name)
            print("[battle] " + name + " 覆盖串无效，已忽略: " + raw, flush=True)
        return beats
    if beats is not None and len(parsed) != len(beats):
        if name not in _specialMsWarned:
            _specialMsWarned.add(name)
            print("[battle] " + name + " 拍数 " + str(len(parsed)) + " != 链长 "
                  + str(len(beats)) + "，已忽略: " + raw, flush=True)
        return beats
    return parsed


def bowBeats():
    """弓射击链实际生效的逐拍时长：``T7_MS_BOW`` 覆盖优先，否则 ``BOW_BEATS_MS``。

    与 ``specialBeats`` 同一套纪律：覆盖串拍数 != 链长时**整条忽略**并打印一次告警，
    宁可走兜底，也不下发半条链的时长。
    """
    raw = os.environ.get(BOW_MS_ENV)
    if not raw:
        return BOW_BEATS_MS
    try:
        parsed = tuple(int(part) for part in raw.split(","))
    except ValueError:
        parsed = ()
    if not parsed or any(value <= 0 for value in parsed):
        if BOW_MS_ENV not in _bowMsWarned:
            _bowMsWarned.add(BOW_MS_ENV)
            print("[battle] " + BOW_MS_ENV + " 覆盖串无效，已忽略: " + raw, flush=True)
        return BOW_BEATS_MS
    if len(parsed) != len(BOW_CHAIN):
        if BOW_MS_ENV not in _bowMsWarned:
            _bowMsWarned.add(BOW_MS_ENV)
            print("[battle] " + BOW_MS_ENV + " 拍数 " + str(len(parsed)) + " != 链长 "
                  + str(len(BOW_CHAIN)) + "，已忽略: " + raw, flush=True)
        return BOW_BEATS_MS
    return parsed


# --- 盾牌防御：可配链 + 逐拍时长 ---------------------------------------------------

def shieldChain():
    """盾牌防御链（默认 ``SHIELD_CHAIN``；``T7_SHIELD_CHAIN`` 可覆盖）。

    至少 2 拍（预备 + 防御），值必须是合法 u16 状态号；不合法则整条忽略并告警。
    """
    raw = os.environ.get(SHIELD_CHAIN_ENV)
    if not raw:
        return SHIELD_CHAIN
    try:
        parsed = tuple(int(part) for part in raw.split(","))
    except ValueError:
        parsed = ()
    if len(parsed) < 2 or any(value <= 0 or value > 0xFFFF for value in parsed):
        if SHIELD_CHAIN_ENV not in _shieldWarned:
            _shieldWarned.add(SHIELD_CHAIN_ENV)
            print("[battle] " + SHIELD_CHAIN_ENV + " 覆盖串无效，已忽略: " + raw,
                  flush=True)
        return SHIELD_CHAIN
    return parsed


def shieldBeats(chain):
    """盾牌防御链的逐拍时长；``T7_MS_SHIELD_DEF`` 覆盖，拍数 != 链长时整条忽略。"""
    fallback = (PHASE_MS_PREPARE,) * len(chain)
    raw = os.environ.get(SHIELD_MS_ENV)
    if not raw:
        return fallback
    try:
        parsed = tuple(int(part) for part in raw.split(","))
    except ValueError:
        parsed = ()
    if len(parsed) != len(chain) or any(value <= 0 for value in parsed):
        if SHIELD_MS_ENV not in _shieldWarned:
            _shieldWarned.add(SHIELD_MS_ENV)
            print("[battle] " + SHIELD_MS_ENV + " 覆盖串无效或拍数 != 链长，已忽略: "
                  + raw, flush=True)
        return fallback
    return parsed


def shieldIndex(flow) -> int:
    """盾牌状态簿记：``-1`` 未举盾 / ``>=0`` 链内第几拍 / ``-2`` 已发「结束」等回待机。

    ⚠️ 只存**标量**（int/bool）—— 见文件头「session 只存标量」纪律，写 dict/tuple
    会被原生层丢整条报文。
    """
    return int(flow.session.get("shieldIndex", -1))


def shieldMessage(flow, keyComb, msgId):
    """盾牌防御：客户端**只用 ``key_comb`` 的 KEY_SHIELD 位**上报，``msg_id`` 恒为 0。

    返回 ``True`` = 这条报文由本函数接管（含「盾位没变、无需发包」之外的已处理情形）。
    返回 ``False`` = 不是盾牌报文 / 开关关着 / 正在出招，交回 ``message`` 的通用分支
    （保持「零行为差异」——旧行为就是记一行 `battle-message-recorded`）。
    """
    if msgId != 0:
        return False
    if not wire.shieldDefenceSwitch():
        return False
    # ⭐⭐ 判据 = **持盾（bit4）AND 右键按住（bit8）**。见常量区 KEY_BIT_SHIELD 处的
    #   实机取证：bit4 是「手上有盾」的装备态，切到盾牌槽后一直置位；右键才是举盾。
    hasShield = bool(int(keyComb) & (1 << KEY_BIT_SHIELD))
    rmb = bool(int(keyComb) & (1 << KEY_BIT_RIGHT_MOUSE))
    want = hasShield and rmb
    up = bool(flow.session.get("shieldOn"))
    if want == up:
        # 防御位没变化 ⇒ 不是「进/出防御」。放行，让它走通用记录分支。
        return False
    if want:
        # 正在出招（攻击/招架/投掷）时不抢状态：出招期间的盾位上报交给动作链。
        if actionState(flow)["index"] >= 0:
            return False
        chain = shieldChain()
        flow.session["shieldOn"] = True
        flow.session["shieldIndex"] = 0
        syncState(flow, chain[0], "battle-shield-begin")
        flow.later(SHIELD_TIMER, shieldBeats(chain)[0])
        return True
    # 放下盾：只有**真的举着**（簿记 >= 0）才发「盾牌防御结束」，否则只清标记。
    flow.session["shieldOn"] = False
    flow.cancel(SHIELD_TIMER)
    if shieldIndex(flow) >= 0:
        flow.session["shieldIndex"] = -2
        syncState(flow, SHIELD_END, "battle-shield-end")
        flow.later(SHIELD_TIMER, PHASE_MS_END)
    else:
        flow.session["shieldIndex"] = -1
    return True


def shieldTimer(flow, name):
    """盾牌防御链的定时器：沿链推进到**最后一拍（举盾持续态）后停住**；
    ``-2``（已发结束）时下一拍回待机 2。"""
    if name != SHIELD_TIMER:
        return False
    index = shieldIndex(flow)
    if index == -2:
        syncState(flow, ACTION_IDLE_STATE, "battle-shield-return-idle")
        flow.session["shieldIndex"] = -1
        return True
    if index < 0:
        return True
    chain = shieldChain()
    if index + 1 >= len(chain):
        return True          # 已到链尾：举着盾停住，等 key_comb 的盾位清零
    nxt = index + 1
    flow.session["shieldIndex"] = nxt
    syncState(flow, chain[nxt], "battle-shield-phase" + str(nxt))
    flow.later(SHIELD_TIMER, shieldBeats(chain)[nxt])
    return True


# 上表里 E 盾击 / X 挽剑花 的 1333 是**代理值**，不是表值：
# 该表 ``[攻击类型]`` 只有 劈/刺/左砍/右砍/冲刺/脚踢 六种，没有“盾击”“挽剑花”
# 这两类动作的行，所以拿同为“无蓄力短近战特殊技”的脚踢攻击中位顶上去。
# 实机若仍偏快/偏慢，直接改这两个元组里的数即可（或临时用 T7_MS_SHIELD / T7_MS_FANCY）。
#
# ⚠️ 这两个名字是**静态快照**，不跟随 T7_MS_* 环境变量；实际下发一律走
# ``beatMs()`` -> ``specialBeats()``。别拿它们做判断依据。
SHIELD_ATTACK_BEATS = SPECIAL_BEATS_BY_INTENT[SHIELD_ATTACK]
SWORD_FANCY_WAVE_BEATS = SPECIAL_BEATS_BY_INTENT[SWORD_FANCY_WAVE]

# --- 四向攻防：表值也算出来了，但**默认不动** -------------------------------------
# 表值：预备 360 / 过程 640~700 / 结束 500（四向行见 row127..row142，
# ``[防御状态的招架类型]预备`` = 向上/下/左/右侧招架，就是四向攻击行）。
# 但 200ms/拍 是 2026-09-18 实机验收过“基本可以”的，属于已交付行为，
# 不跟着特殊键一起改。想切到表值：把 ATTACK_USE_TABLE_DURATIONS 改成 True。
# 四向链是 预备/蓄力/过程/结束 四拍，其中“蓄力”是玩家按住的那一拍
# （ATTACK_HOLD_INDEX=1），表里没有它的时长，用 PHASE_MS_PREPARE 补位。
ATTACK_USE_TABLE_DURATIONS = False
ACTION_PHASE_MS = 200      # 四向攻防当前每拍时长（2026-09-18 实机验收值，保留）
ATTACK_BEATS_MS = (PHASE_MS_PREPARE, PHASE_MS_PREPARE,
                   PHASE_MS_PROCESS, PHASE_MS_END)
# 投掷链逐拍（321 预备 / 322 蓄力 / 363 出手前摇 / 323 过程 / 324 结束）。
# 数值用 s_battle_param_cli 的中位：预备 360、过程 700、结束 500；蓄力与 363
# 是按住/衔接拍，各取 360。363 只停一拍就把 323（真正掷出）推上去。
THROW_BEATS_MS = (PHASE_MS_PREPARE, PHASE_MS_PREPARE, PHASE_MS_PREPARE,
                  PHASE_MS_PROCESS, PHASE_MS_END)

# session["action"] 的默认值：全部为标量，保证可被原生层序列化。
_ACTION_DEFAULT = {"intent": 0, "index": -1, "seq": 1, "released": False}


def actionState(flow):
    action = flow.session.setdefault("action", dict(_ACTION_DEFAULT))
    for name, value in _ACTION_DEFAULT.items():
        action.setdefault(name, value)
    return action


def _specOf(intent):
    """按 intent 现查 ``(chain, holdIndex, releaseIndex)``。

    ``releaseIndex is None`` 表示松开即回待机（招架、特殊键）；否则松开后从该阶段
    继续沿链走完（攻击）。**返回值不写进 session**——tuple 会被原生层拒绝。

    查表顺序：``CHAIN_SPEC_BY_INTENT``（自带 hold/release 的链，如弓）→ 四向攻击 →
    招架 → 特殊键。放最前面是因为这类链的 hold/release 索引与四向**不同**，
    共用默认值会把「按住停蓄力」变成「按住停拉弓」。
    """
    spec = CHAIN_SPEC_BY_INTENT.get(intent)
    if spec is not None:
        return spec
    chain = ATTACK_CHAIN_BY_INTENT.get(intent)
    if chain is not None:
        return chain, ATTACK_HOLD_INDEX, ATTACK_RELEASE_INDEX
    chain = PARRY_CHAIN_BY_INTENT.get(intent)
    if chain is not None:
        return chain, BLOCK_HOLD_INDEX, None
    chain = SPECIAL_CHAIN_BY_INTENT.get(intent)
    if chain is not None:
        # 特殊键只有“按下”一个边沿（客户端不发释放报文），所以按“自动走完”处理：
        # holdIndex 取最后一阶段、releaseIndex=None，起点即置 released=True，
        # 由 ACTION_TIMER 把整条链推完再回待机 2。
        return chain, len(chain) - 1, None
    return None, 0, None


def _labelOf(intent) -> str:
    """reason 里的动作标签。四向攻防沿用原来的 up/right/down/left，保持日志兼容。"""
    name = DIRECTION_NAME.get(intent)
    if name is not None:
        return name
    return SPECIAL_NAME_BY_INTENT.get(intent, "intent" + str(intent))


def _kindOf(intent) -> str:
    if intent in PARRY_CHAIN_BY_INTENT:
        return "block"
    if intent in SPECIAL_CHAIN_BY_INTENT:
        return "special"
    return "attack"


def _releaseOf(intent):
    if intent in PARRY_CHAIN_BY_INTENT:
        return BLOCK_RELEASE
    if intent in SPECIAL_CHAIN_BY_INTENT:
        # 特殊键没有对应的释放报文。返回 0（不是合法 msg_id）使任何释放报文都
        # 不匹配，于是落到 battle-release-without-press 分支，而**不会**被
        # 误判成招架释放而把动作提前踢回待机。
        return 0
    return ATTACK_RELEASE


def resetAction(flow) -> None:
    actionState(flow).update(intent=0, index=-1, released=False)


def syncState(flow, state: int, reason: str) -> None:
    """下发一次 root4/sub3 STATE_SYNC_SIMPLE。"""
    startedAt = flow.session.get("instanceStartedAt")
    if startedAt is None:
        flow.result["logs"].append(
            "new instance required for battle state sync; unknown instance epoch")
        return
    action = actionState(flow)
    action["seq"] = action["seq"] % 0xFFFF + 1
    body = battle_flow.encode_battle_state_sync_simple(
        instance_id=wire.ACTOR_ID, seq_no=action["seq"], state=state,
        state_change_ms=(flow.now - startedAt) & 0xFFFFFFFF, state_time_ms=0)
    flow.send(battle_flow.BATTLE_COMMAND, body, reason)


def _phaseReason(action, index: int) -> str:
    return ("battle-" + _kindOf(action["intent"]) + "-phase" + str(index)
            + "-" + _labelOf(action["intent"]))


def beatMs(intent, index: int = 0) -> int:
    """第 ``index`` 拍的下发间隔（毫秒）。

    * 特殊键 -> ``SPECIAL_BEATS_BY_INTENT`` 里逐拍查表（表值，见该常量上方注释）；
      可用 ``T7_MS_*`` 环境变量临时覆盖（见 ``specialBeats``）
    * 四向攻防 -> ``ACTION_PHASE_MS``；若把 ``ATTACK_USE_TABLE_DURATIONS``
      打开则改用 ``ATTACK_BEATS_MS`` 逐拍查表

    ``index`` 越界时取最后一拍的值，保证定时器永远能拿到一个正数。
    """
    beats = specialBeats(intent)
    if beats is None:
        if intent == THROW:
            # 投掷链单独给表值量级：200ms 连拍会把投掷动画从中间掐断（实机
            # 「手里武器闪一下就收回去」）。700 是 [持续时间]过程 的中位，正好
            # 盖住松手到出手那段动画。
            beats = THROW_BEATS_MS
        elif intent == BOW_SHOOT:
            # 弓链 5 拍，逐拍时长见 BOW_BEATS_MS（168 过程是表值量级，其余占位值）。
            beats = bowBeats()
        elif ATTACK_USE_TABLE_DURATIONS and intent in ATTACK_CHAIN_BY_INTENT:
            beats = ATTACK_BEATS_MS
        else:
            return ACTION_PHASE_MS
    if not beats:
        return ACTION_PHASE_MS
    return beats[index] if 0 <= index < len(beats) else beats[-1]


def totalMs(intent) -> int:
    """该意图从按下到回待机的**总时长**（只用于日志与自检，不参与下发）。"""
    chain, _hold, _release = _specOf(intent)
    if chain is None:
        return 0
    return sum(beatMs(intent, i) for i in range(len(chain)))


def timer(flow, name):
    if name == SHIELD_TIMER:
        # 盾牌防御链有自己的定时器名：与动作链（ACTION_TIMER）分开，
        # 免得「举着盾时出招」把两套推进互相 cancel 掉。
        return shieldTimer(flow, name)
    if name != ACTION_TIMER:
        return False
    action = actionState(flow)
    if flow.session.get("leaving"):
        resetAction(flow)
        return True
    chain, holdIndex, _ = _specOf(action["intent"])
    if chain is None or action["index"] < 0:
        resetAction(flow)
        return True
    nextIndex = action["index"] + 1
    if action["released"]:
        # 已松开：沿链走完 -> 待机
        if nextIndex >= len(chain):
            syncState(flow, ACTION_IDLE_STATE,
                      "battle-" + _kindOf(action["intent"]) + "-return-idle")
            resetAction(flow)
            return True
    elif nextIndex > holdIndex:
        # 按住阶段已到，停住不再推进——这就是“不松开是蓄力”。
        return True
    action["index"] = nextIndex
    syncState(flow, chain[nextIndex], _phaseReason(action, nextIndex))
    # 定时器按**刚进入的这一拍**自己的时长排下一拍（逐拍时长，不再是统一节拍）。
    flow.later(ACTION_TIMER, beatMs(action["intent"], nextIndex))
    return True


def message(flow, command, selector, body):
    if (command != battle_flow.BATTLE_COMMAND or selector != BATTLE_MESSAGE_SELECTOR
            or flow.session["role"] != "instance" or flow.session.get("leaving")):
        return False
    if flow.session.get("controlBaseline") != wire.BASELINE_ID:
        return False
    if len(body) != BATTLE_MESSAGE_BODY_LENGTH:
        raise ValueError(
            f"battle message must contain exactly {BATTLE_MESSAGE_BODY_LENGTH} bytes,"
            f" got {len(body)}")
    # TDR: key_comb=uint, msg_id=int, is_auto_parry=int8 —— 符号严格对应。
    keyComb, msgId, isAutoParry = struct.unpack(">Iib", body[2:])
    if isAutoParry not in (0, 1):
        raise ValueError(f"is_auto_parry must be 0 or 1, got {isAutoParry}")
    # 原始字节落日志：客户端在 GAME 之后会发 msg_id=0 的报文，其字段语义尚未闭合，
    # 保留 hex 供离线比对，不做任何推断。
    flow.result["logs"].append(
        "battle-raw key_comb=" + str(keyComb) + " msg_id=" + str(msgId)
        + " is_auto_parry=" + str(isAutoParry) + " hex=" + body.hex())
    if msgId in SPECIAL_CHAIN_BY_INTENT:
        # 特殊键的位图诊断：这是“key_comb 就是 KEY_* 位图”这条结论的现场复验。
        # present=0 不阻断驱动（msg_id 本身才是权威），只作为下一轮的比对证据。
        bit = SPECIAL_KEY_BIT_BY_INTENT.get(msgId)
        present = bit is not None and bool(keyComb & (1 << bit))
        flow.result["logs"].append(
            "battle-special-key key=" + SPECIAL_NAME_BY_INTENT[msgId]
            + " bit=" + str(bit) + " present=" + ("1" if present else "0")
            + " shift=" + str((keyComb >> KEY_BIT_SHIFT) & 1)
            + " run=" + str((keyComb >> KEY_BIT_RUN) & 1)
            + " doubleW=" + str((keyComb >> KEY_BIT_DOUBLE_W) & 1)
            + " lmb=" + str((keyComb >> KEY_BIT_LEFT_MOUSE) & 1)
            + " rmb=" + str((keyComb >> KEY_BIT_RIGHT_MOUSE) & 1))
    if not flow.session.get("battleEntered"):
        # 门控用 battleEntered（角色就绪）而不是 groundEnabled（GAME 开启）：
        # 实机日志显示客户端的攻击按下意图出现在 PREPARE 阶段（GAME 之前），
        # 用 groundEnabled 会把它们全部挡掉，表现为“按了没反应”。
        flow.result["logs"].append(
            "battle-message-before-entry key_comb=" + str(keyComb) + " msg_id=" + str(msgId))
        return True

    # ⭐⭐ 2026-09-28 22:4x：投石车「按住左键蓄力 / 松手发射」链**优先接管**。
    #   上车后客户端把左键按下/松开**照常**报成 ``cmd=4 sel=1``（判据是 ``key_comb``
    #   的 bit7 = KEY_LEFT_MOUSE），与普通攻击**同一套布局** ⇒ 必须在攻击分发
    #   之前拦，否则会被当成一次普通攻击。
    #   不在投石车上 / 开关关着 ⇒ ``siege`` 返回 False，下面照旧走（零行为差异）。
    #   包 try：器械是附加能力，坏了**不许**拖垮 battle 这条已验证的链。
    try:
        from . import siege as _siege
        if _siege.onCatapultAttack(flow, keyComb, msgId):
            return True
    except Exception:  # noqa: BLE001
        pass

    action = actionState(flow)
    chain = _specOf(msgId)[0]

    # ⭐⭐ 2026-10-03：**盾牌防御优先接管 `msg_id=0` 的盾位上报**。
    #   判据是 `key_comb` 的 bit4（KEY_SHIELD，宏表实证 = 4），**不是** msg_id ——
    #   客户端举盾/放盾时 msg_id 恒为 0（全局 4 个会话一致，见常量区注释）。
    #   不是盾牌报文 / 开关关着 / 正在出招 ⇒ 返回 False，下面照旧走（零行为差异）。
    #   包 try：盾牌是附加能力，坏了不许拖垮 battle 这条已验证的链。
    try:
        if shieldMessage(flow, keyComb, msgId):
            return True
    except Exception:  # noqa: BLE001
        pass

    if msgId == BOW_CANCEL:
        # 右键取消拉弓。客户端状态表：164/165/167/219/220 --300660--> 326(弓发射取消)。
        # ⚠️ 只在「当前动作确实是弓」时接管 —— 其他情形（步兵右键、待机右键）本表
        #    没有边，客户端自己处理，我们**必须放行**落到下面的通用分支，
        #    否则会凭空吃掉一条原本无副作用的报文（零行为差异原则）。
        if action["index"] >= 0 and action["intent"] == BOW_SHOOT:
            flow.cancel(ACTION_TIMER)
            syncState(flow, BOW_SHOT_CANCEL_STATE, "battle-attack-cancel-bow")
            resetAction(flow)
            return True

    if chain is not None:
        if action["index"] >= 0:
            # 已有动作未结束时拒绝覆盖，避免状态竞争导致抖动或动作跳变。
            flow.result["logs"].append(
                "battle-" + _kindOf(msgId) + "-rejected msg_id=" + str(msgId)
                + " while running intent=" + str(action["intent"]))
            return True
        # 出招接管：清掉盾牌状态簿记。**不发 96** —— 新动作的 STATE_SYNC 会直接覆盖，
        # 多发一包「盾牌防御结束」反而会插一帧收盾动作。`shieldOn` 保持不动，这样
        # 玩家仍按着右键时，后面那条盾位报文不会因为「标记已清」而被漏判。
        if shieldIndex(flow) >= 0:
            flow.cancel(SHIELD_TIMER)
            flow.session["shieldIndex"] = -1
        # 特殊键没有客户端释放报文，起点就标 released，由定时器把整条链推完。
        action.update(intent=msgId, index=0, released=msgId in SPECIAL_CHAIN_BY_INTENT)
        # ⭐ 2026-10-03 18:5x：弓的「**隐形蓄力**」从这一刻开始计时。
        #   用户说明：弓的射程由「按住左键的时长」决定（客户端**没有可见蓄力条**，
        #   所以叫隐形蓄力），**不是**服务端固定 45° 抛射。实测客户端在 REQ 的
        #   `fly_pose.dir`（三个浮点 = 初速度向量）里已经算好了蓄力结果，
        #   `precision` 也是蓄力量 ⇒ 服务端**原样回显**即等于「按蓄力算距离」。
        #   这里记下按下时刻，只为在 `fly-init-pose-req` 日志里打出「按了多久」，
        #   供实机对照 `precision` 是否随蓄力增长（**不参与任何计算**）。
        if msgId == BOW_SHOOT:
            flow.session["bowPressMs"] = wire.serverNowMs()
        syncState(flow, chain[0],
                  "battle-" + _kindOf(msgId) + "-begin-" + _labelOf(msgId))
        if msgId in SPECIAL_CHAIN_BY_INTENT:
            flow.result["logs"].append(
                "battle-special-timing key=" + SPECIAL_NAME_BY_INTENT[msgId]
                + " phases=" + str(len(chain))
                + " beatsMs=" + ",".join(str(beatMs(msgId, i))
                                         for i in range(len(chain)))
                + " totalMs=" + str(totalMs(msgId))
                + " src=client:s_battle_param_cli")
        flow.later(ACTION_TIMER, beatMs(msgId, 0))
        return True

    if msgId in (ATTACK_RELEASE, BLOCK_RELEASE):
        if action["index"] < 0 or msgId != _releaseOf(action["intent"]):
            # 没有按下、或释放意图与当前动作不是同一类（左键/右键不匹配），
            # 或当前是特殊键（_releaseOf 返回 0，永远不匹配）。
            flow.result["logs"].append(
                "battle-release-without-press msg_id=" + str(msgId))
            return True
        # 关键：必须按**已按下的意图**查链，不能按释放报文 id 查
        # （300020/300620 本身不在任何链里，按它查会得到 releaseIndex=None，
        #  于是所有释放都被误判成招架、直接回待机，攻击永远走不到过程态）。
        chain, holdIndex, releaseIndex = _specOf(action["intent"])
        flow.cancel(ACTION_TIMER)
        if releaseIndex is None:
            # 招架：松开直接回待机。
            syncState(flow, ACTION_IDLE_STATE, "battle-block-release-return-idle")
            resetAction(flow)
            return True
        # 攻击：松开进入过程态，随后由定时器走到结束并回待机。
        action.update(index=releaseIndex, released=True)
        syncState(flow, chain[releaseIndex],
                  "battle-attack-process-" + DIRECTION_NAME[action["intent"]])
        # ⭐⭐ 2026-10-03：**「过程」拍 = 唯一的近战命中时刻**，在这里做 NPC 命中判定。
        # 为什么必须服务端自己算：客户端**不报近战命中** —— 实机（会话
        # 47680-1109853472）一次挥砍只上行 `cmd=4 sel=1`，`cmd=4 sel=5`
        # (BATTLE_HIT) 全程 0 次；那条是**飞行物**撞物专用（见 hitMessage）。
        # 为什么只有这一处会触发：`ATTACK_HOLD_INDEX=1` ⇒ 按住时定时器停在 1 不推进
        # （见上面 timer 的 `nextIndex > holdIndex` 分支），索引 2 只能由**松开**写进
        # 来 ⇒ 不会与定时器重复触发（不会一刀双伤）。
        # 投掷（THROW）走飞行物那条链，不在此处判。
        if (action["intent"] in ATTACK_CHAIN_BY_INTENT
                and action["intent"] != THROW):
            npc_battle.onAttackHit(flow, action["intent"])
        if action["intent"] == THROW:
            # 掷出这一拍：服务端扣一发并回推 CS_PROTO_WEAPON_BULLET_UPDATE
            # （cmd=5 sel=4）。⚠️ 2026-09-26 实机否证了「挪到 flyMessage 再扣」：
            # 松手拍不推 sel=4，客户端就**一条飞行物请求都不发**（21 次投掷 0 条
            # cmd9 sel7，且 HUD 数字不动）⇒ 它拿这包当「服务端认可这次投掷」的门禁，
            # HUD 递减也是这包驱动的。扣弹必须留在这里，弹体生成（flyMessage）不再二扣。
            tid = wire.currentWeaponTid()
            left = wire.consumeWeaponAmmo(tid)
            if left is not None:
                flow.send(5, wire.weaponBulletUpdate(tid),
                          "weapon-bullet-update throw tid=%d left=%d" % (tid, left))
            # 诊断探针（2026-09-26）：记录每次松手时玩家坐标，与飞行物申请配对。
            # 当天用它否证了站位/距离说（申请一直都在发，是 handler 崩了没回包）。
            from . import controls as _controls
            _pos = _controls.groundState(flow).get("position") or wire.POSITION
            flow.result["logs"].append(
                "throw-release-pos tid=%d at=%.1f,%.1f,%.1f" % (tid, *_pos))
        flow.later(ACTION_TIMER, beatMs(action["intent"], releaseIndex))
        return True

    # 其余战斗意图（msg_id=0 的纯按键状态上报、格挡成功、被格挡、命中结果等）：
    # msg_id=0 已实测为“按键组合变化但未解析出动作”（SHIFT/双击W 的按下松开就是
    # 这一类），语义已闭合为“无动作”，但没有对应 state，故只记录不驱动。
    flow.result["logs"].append(
        "battle-message-recorded key_comb=" + str(keyComb) + " msg_id=" + str(msgId))
    return True


# --- HAVOK 飞行物组（cmd=9）：投掷出去的武器本体的权威通道 --------------------
# 2026-09-25 实机（会话 16052-448001441）：飞戟松手动画走完后客户端**各发一条**
# cmd=9 sel=7，body 44B 与 TDR ``CS_PROTO_BATTLE_FLY_INIT_POSE_REQ`` 逐字节吻合：
#   weapon_type i32(=54=ARM_TYPE_FLYING_HALBERD，宏表枚举实证) | weapon_tid i32
#   (=1080311) | fly_pose f32[6](位置 618,565,46 + 朝向) | precision f32(≈2.0)
#   | mo_mid u64(=0)。客户端把出手参数都报上来了，服务端此前零响应
#   （落在 unhandled）⇒ 人做了投掷动作、弹药在扣，但**飞行物本体不出现**。
# 同组宏 ``ENM_HAVOK_CMD_*``：3/4=TURN_WAIST REQ/RES（客户端也发过 sel=3，8B，
# 未处理不影响本线）、7/8=BC_FLY_INIT_POSE REQ/RES、10/11=BC_FLY_STOP_POSE
# REQ/RES（飞行物落地/插地姿态广播）。RES 布局取自 TDR：
#   shooter_rid u64 | weapon_type i32 | weapon_tid i32 | fly_pose 24B |
#   svr_time u64 | flyer_seq_no u32 | clt_report_collision i8 | mo_mid u64。
HAVOK_COMMAND = 9
FLY_INIT_POSE_REQ = 7
FLY_INIT_POSE_RES = 8
FLY_STOP_POSE_REQ = 10
FLY_STOP_POSE_RES = 11
FLY_INIT_POSE_BODY = 46  # 2B 选择子 + 44B 结构
FLY_STOP_POSE_BODY = 42  # 2B 选择子 + seq u32 + pos/rot/normal 各 12B

# ⭐⭐ 2026-10-03 20:2x：`cmd=9 sel=3` = `ENM_HAVOK_CMD_TURN_WAIST_REQ`（转腰请求）。
#   全量会话扫描：`cmd=9 sel=3` 出现 **507 次**，是**唯一**被客户端反复发送却
#   服务端零响应的战斗报文（`cmd=9 sel=7` 只有 12 次，就是飞行物那条）。
#   客户端在**拉弓瞄准**（按住 300680）和**投掷**时都会发，角度随瞄准方向变化。
#
#   选择子定标（TieJiClient.exe 内嵌枚举描述符区 0x010599ab 起的成员顺序
#   + 实机选择子反推，偏移恒为 +2）：
#       1 = ANIMATION_START_REQ      2 = ANIMATION_START_RES
#       3 = TURN_WAIST_REQ           4 = TURN_WAIST_RES     (只带 Y)
#       5 = TURN_WAIST_XY_RES        6 = TURN_WAIST_X_RES   (只带 X)
#       7 = BC_FLY_INIT_POSE_REQ     8 = BC_FLY_INIT_POSE_RES
#       9 = TURN_WAIST_RES_TIMER    10 = BC_FLY_STOP_POSE_REQ
#      11 = BC_FLY_STOP_POSE_RES
#   （官方注释「转腰是高频操作 为节省流量 此条只有 X 方向转腰」⇒ 三条 RES 是按
#   轴向拆开的省流量变体，按 REQ 里实际带哪个轴挑一条回。）
#
#   TDR 结构（`CS_PROTO_HAVOK_TURN_WAIST_*`，metalib sh_proto_cs）：
#     REQ  net 6  : turn_type_x char@0 | turn_type_y char@1
#                  | turn_angle_x short@2 | turn_angle_y short@4
#                  （类型取值 **1=Y轴 2=X轴**，0=该槽无转腰）
#     RES  net 18 : turner biguint@0 | turn_time biguint@8 | turn_waist_y_value short@16
#     X_RES net18 : turner | turn_time | turn_waist_x_value short@16
#     XY_RES net20: turner | turn_time | turn_waist_x_value short@16 | y short@18
#   实机样本 body[2:8] = `00 01 00 00 ff fd` ⇒ type_x=0(无) type_y=1(Y轴)
#   angle_x=0 angle_y=-3 ⇒ **只回 Y 一条**（sel=4）。
TURN_WAIST_REQ = 3
TURN_WAIST_RES = 4
TURN_WAIST_XY_RES = 5
TURN_WAIST_X_RES = 6
TURN_WAIST_REQ_BODY = 8  # 2B 选择子 + 6B 结构
TURN_WAIST_ENV = "T7_TURN_WAIST"
TURN_TYPE_Y = 1  # 转腰类型枚举：1 = Y 轴
TURN_TYPE_X = 2  # 转腰类型枚举：2 = X 轴


def turnWaistOn():
    """是否响应 `cmd=9 sel=3` 转腰请求。**默认开**（修协议缺口，不是实验能力）。"""
    raw = os.environ.get(TURN_WAIST_ENV)
    if raw is None or not str(raw).strip():
        return True
    return str(raw).strip().lower() in ("1", "on", "true", "yes")


def turnWaistReply(body):
    """把转腰 REQ 的 6B 结构解成 ``(selector, xValue, yValue, typeX, typeY)``。

    每个槽是 ``(type, angle)`` 一对；``type`` 指明这个角度属于哪个物理轴
    （1=Y轴 2=X轴，0=该槽无转腰）。返回要回的 RES 选择子与两个轴的值。
    """
    typeX, typeY, angleX, angleY = struct.unpack_from(">BBhh", body, 2)
    xValue, yValue = 0, 0
    if typeX == TURN_TYPE_X:
        xValue = angleX
    elif typeX == TURN_TYPE_Y:
        yValue = angleX
    if typeY == TURN_TYPE_X:
        xValue = angleY
    elif typeY == TURN_TYPE_Y:
        yValue = angleY
    if xValue and yValue:
        return TURN_WAIST_XY_RES, xValue, yValue
    if xValue:
        return TURN_WAIST_X_RES, xValue, yValue
    return TURN_WAIST_RES, xValue, yValue
SMOKE_MORTAR_TID = 1080511  # 竹烟筒（3 号槽）。它落地要在落点挂一颗「烟雾弹范围」物件
HERB_PACKET_TID = 1100131  # 草药包（华佗 4 号槽）。落地同样挂 MO（进视野自带绿圈治疗光）
SMOKE_CLOUD_RID_BASE = 900000  # 烟团物件的 rid 起点（避开玩家 rid=1 和器械的 10000+）

# ⭐ 2026-10-03 18:5x：**远程直射类（弓，type=49）纯回显客户端 REQ 的 fly_pose**。
#   会话 50180-1122868420 实机：弓 5 发全部走到 `battle-attack-process-bow` 并发出
#   `fly-init-pose-res`（type=49 tid=1050231 prec=0.20），但用户**看不到箭**。
#   本链原先只有投掷物两个样本 —— 竹烟筒（type=73，能飞）与飞戟（type=54，不飞），
#   服务端给它们的 RES 是「出生点前移 2.5m + 抬高 1m + 45° 仰角 8 m/s」的**抛射**写法。
#   箭不该套这套：① 箭从弓上射出，出生点前移 2.5m 会让箭凭空出现在身前；
#   ② 箭的初速/仰角本该由客户端按准星与箭速算好并写在 REQ 的 fly_pose 里，
#      服务端覆盖成 8 m/s + 45° 是错的（8 m/s 对箭太慢，45° 让它斜向上飘）。
#   ⇒ 对 `FLY_ECHO_DEFAULT` 里的类型**原样回显** REQ 的 pose（一个字段都不改）。
#   `T7_FLY_ECHO` 可覆盖：逗号分隔的 type 列表 / `all`（全部）/ `off`（关，回旧抛射写法）。
FLY_ECHO_ENV = "T7_FLY_ECHO"
# ⚠️ 2026-10-03 19:1x **默认改成空**：纯回显对弓**已被实机否证** ——
#   会话 `52680-1124348253` 9 发全部 `dirLen=1.00`，弓的 REQ `dir` 是**单位朝向**
#   而不是初速度 ⇒ 纯回显等于把初速设成 1 m/s、箭原地不动。
#   弓现在走 `bowChargePose`（服务端按按住时长算初速，默认开，见下）。
#   本开关保留给「REQ 里 dir 确实是初速度」的武器用（`T7_FLY_ECHO=49` / `all`）。
FLY_ECHO_DEFAULT = ()
_flyEchoWarned = set()


def flyEchoTypes():
    """返回「纯回显」的 weapon_type 集合；``("*",)`` 表示全部，``()`` 表示关。"""
    raw = os.environ.get(FLY_ECHO_ENV)
    if raw is None or not str(raw).strip():
        return FLY_ECHO_DEFAULT
    text = str(raw).strip().lower()
    if text in ("off", "none", "0"):
        return ()
    if text == "all":
        return ("*",)
    out = []
    for piece in text.replace(" ", "").split(","):
        if not piece:
            continue
        try:
            out.append(int(piece, 0))
        except ValueError:
            if FLY_ECHO_ENV not in _flyEchoWarned:
                _flyEchoWarned.add(FLY_ECHO_ENV)
                print("[battle] " + FLY_ECHO_ENV + " 覆盖串无效，已忽略: " + raw,
                      flush=True)
            return FLY_ECHO_DEFAULT
    return tuple(out)


def flyEchoes(weaponType):
    types = flyEchoTypes()
    return "*" in types or weaponType in types


# ⭐⭐⭐ 2026-10-03 19:1x：弓「**隐形蓄力 → 射程**」—— 实机 9 发定案，**默认开**。
#   用户说明：弓的射程由「**按住左键的时长**」决定，客户端界面上**没有可见的蓄力条**
#   （所以叫隐形蓄力），**不是**服务端固定 45° 抛射。
#
#   ⚠️ 先试过「纯回显 REQ 的 pose」，**实机证明不行** —— 会话 `52680-1124348253`
#   9 发全部 `dirLen=1.00`：**弓的 REQ 里 `fly_pose.dir` 是「单位朝向」，不是初速度**！
#   （投掷物那边 dir 是初速度，长度决定射程；弓不一样。）
#   ⇒ 纯回显等于把初速设成 1 m/s，箭原地不动，所以用户看不到箭飞出去。
#
#   同时 9 发对照证明 **`precision` 不是蓄力量**（与按住时长不相关）：
#       held= 621ms → prec=5.70      held= 700ms → prec=4.77
#       held=1185ms → prec=0.27      held=1200ms → prec=0.51
#       held=1315ms → prec=0.20      held=1965ms → prec=0.20
#       held=2465ms → prec=0.20      held=4816ms → prec=0.21
#   （看起来 prec 在 ~700ms 内最大、按久了反而掉下来，疑似「精度/拉满度」而不是蓄力量。）
#   ⇒ **蓄力量只能由服务端按 `held`（按下 300680 → 松开 300020 的间隔）自己算**。
#
#   最终写法：**方向用 REQ 的单位朝向**（玩家瞄哪射哪，含俯仰，不再固定仰角），
#   **速度 = 按住时长线性映射**。`T7_BOW_CHARGE=off` 可关（关了就回到旧抛射写法）。
BOW_CHARGE_ENV = "T7_BOW_CHARGE"
BOW_CHARGE_MS_ENV = "T7_BOW_CHARGE_MS"
BOW_CHARGE_SPEED_ENV = "T7_BOW_SPEED"
BOW_CHARGE_TYPES = (49,)            # 弓（实机 REQ 的 weapon_type）
BOW_CHARGE_MAX_MS = 1000.0          # 满蓄时长（按住 1 秒 = 满蓄；实机 held 621~4816ms）
BOW_CHARGE_SPEED_MIN = 7.0          # 未蓄（点一下）初速 m/s
BOW_CHARGE_SPEED_MAX = 15.0         # 满蓄初速 m/s（12/25 仍显快，2026-10-04 再降到 7/15）


def bowChargeOn():
    """弓是否由服务端按「按住时长」算初速。**默认开**（纯回显已证明对弓无效）。"""
    raw = os.environ.get(BOW_CHARGE_ENV)
    if raw is None or not str(raw).strip():
        return True
    return str(raw).strip().lower() in ("1", "on", "true", "yes")


def bowChargeMaxMs():
    raw = os.environ.get(BOW_CHARGE_MS_ENV)
    if raw is None or not str(raw).strip():
        return BOW_CHARGE_MAX_MS
    try:
        value = float(raw)
    except ValueError:
        return BOW_CHARGE_MAX_MS
    return value if value > 1.0 else BOW_CHARGE_MAX_MS


def bowChargeSpeedRange():
    """``T7_BOW_SPEED=18,50`` 覆盖 (未蓄, 满蓄) 初速；非法值忽略。"""
    raw = os.environ.get(BOW_CHARGE_SPEED_ENV)
    if raw is None or not str(raw).strip():
        return BOW_CHARGE_SPEED_MIN, BOW_CHARGE_SPEED_MAX
    pieces = str(raw).replace(" ", "").split(",")
    if len(pieces) != 2:
        return BOW_CHARGE_SPEED_MIN, BOW_CHARGE_SPEED_MAX
    try:
        lo, hi = float(pieces[0]), float(pieces[1])
    except ValueError:
        return BOW_CHARGE_SPEED_MIN, BOW_CHARGE_SPEED_MAX
    if not (0.0 < lo < hi):
        return BOW_CHARGE_SPEED_MIN, BOW_CHARGE_SPEED_MAX
    return lo, hi


def bowChargePose(pose, heldMs):
    """按「按住左键的时长」算弓的初速姿态；返回 ``(新pose, speed, charge)``。

    **方向用 REQ 的 dir 归一化**（实机 `dirLen=1.00`，本来就是单位朝向；这里仍做一次
    归一化以兼容非单位的情况），**位置原样不动**（箭从客户端给的出箭点射出，
    不做投掷物那种前移 2.5m）。``charge`` ∈ [0,1] 供日志对照。
    """
    px, py, pz, dx, dy, dz = pose
    norm = math.sqrt(dx * dx + dy * dy + dz * dz)
    if norm < 1e-6:
        nx, ny, nz = 1.0, 0.0, 0.0
    else:
        nx, ny, nz = dx / norm, dy / norm, dz / norm
    lo, hi = bowChargeSpeedRange()
    charge = min(1.0, max(0.0, heldMs / bowChargeMaxMs()))
    speed = lo + (hi - lo) * charge
    return (px, py, pz, nx * speed, ny * speed, nz * speed), speed, charge


# ⭐ 2026-10-03 20:3x：**弓的「枪口前移」**——把出箭点沿瞄准方向推离射手碰撞体。
#
#   为什么怀疑这个：实机 `cmd=4 sel=5 BATTLE_HIT` 显示弓的每一发都在**出生瞬间**
#   报碰撞，且 `target=1`（射手本人）+ `rb=1`（`ENM_SH_HAVOK_RB_TYPE_ACTOR`，见
#   TieJiClient.exe 0x00ff0b0c 起的枚举）= **撞在自己身上**；碰撞点 = REQ 姿态的
#   出箭点（452.39,517.40,25.17 vs REQ 452.61,517.69,25.48，只差 0.5m）。对照：
#   竹烟筒/飞戟的自撞点也恒在「客户端 REQ 的出箭点」，与我们 RES 里给的位置无关
#   ⇒ 那条自撞是**客户端本机预测**，改 RES 坐标改不掉（已否证，见 flyMessage 注释）。
#   但 `箭_攻击.btree` 的碰撞分支条件是「**是否为TOI碰撞**」（投掷物/箭/弩矢/飞刀
#   全都有这条），而烟筒那条要求「击中角色」（**排除拥有者**）⇒ 自撞对烟筒无害、
#   对箭却会立刻走「飞行物攻击处理」把飞行结束掉 —— 这才是「箭射出去看不到」的
#   头号嫌疑。本轮单一变量：**只把出箭点沿瞄准方向前移**，看碰撞点是否跟着离开射手。
#
#   `T7_BOW_MUZZLE` 可调：`前移米,抬高米`（默认 `1.5,0.3`）；`off`/`0` 关掉。
BOW_MUZZLE_ENV = "T7_BOW_MUZZLE"
BOW_MUZZLE_FORWARD = 1.5
BOW_MUZZLE_UP = 0.3
_muzzleWarned = set()


def bowMuzzleOffset():
    """返回 ``(前移, 抬高)`` 米；``off``/``0`` ⇒ ``(0.0, 0.0)``（= 出箭点原样）。"""
    raw = os.environ.get(BOW_MUZZLE_ENV)
    if raw is None or not str(raw).strip():
        return BOW_MUZZLE_FORWARD, BOW_MUZZLE_UP
    text = str(raw).strip().lower()
    if text in ("off", "none", "0"):
        return 0.0, 0.0
    pieces = text.replace(" ", "").split(",")
    try:
        if len(pieces) == 1:
            return max(0.0, float(pieces[0])), BOW_MUZZLE_UP
        forward, up = float(pieces[0]), float(pieces[1])
    except ValueError:
        if BOW_MUZZLE_ENV not in _muzzleWarned:
            _muzzleWarned.add(BOW_MUZZLE_ENV)
            print("[battle] " + BOW_MUZZLE_ENV + " 覆盖串无效，已忽略: " + raw,
                  flush=True)
        return BOW_MUZZLE_FORWARD, BOW_MUZZLE_UP
    if forward < 0.0 or up < 0.0:
        return BOW_MUZZLE_FORWARD, BOW_MUZZLE_UP
    return forward, up


def bowMuzzlePose(pose):
    """把出箭点沿**归一化后的瞄准方向**前移 ``forward``、抬高 ``up``；方向不动。"""
    forward, up = bowMuzzleOffset()
    if forward <= 0.0 and up <= 0.0:
        return pose, 0.0, 0.0
    px, py, pz, dx, dy, dz = pose
    norm = math.sqrt(dx * dx + dy * dy + dz * dz)
    if norm < 1e-6:
        nx, ny, nz = 1.0, 0.0, 0.0
    else:
        nx, ny, nz = dx / norm, dy / norm, dz / norm
    return ((px + nx * forward, py + ny * forward, pz + nz * forward + up,
             dx, dy, dz), forward, up)


def flyMessage(flow, command, selector, body):
    if command != HAVOK_COMMAND or selector not in (
            TURN_WAIST_REQ, FLY_INIT_POSE_REQ, FLY_STOP_POSE_REQ):
        return False
    if (flow.session["role"] != "instance" or flow.session.get("leaving")
            or flow.session.get("controlBaseline") != wire.BASELINE_ID):
        return False
    if selector == TURN_WAIST_REQ:
        # ⭐⭐ 2026-10-03 20:2x：转腰请求（见常量区取证）。客户端拉弓瞄准/投掷时
        #   每转一下腰就发一条，服务端此前**零响应**（全量 507 条落 unhandled）。
        #   官方三条 RES 是按轴拆开的省流量变体，按 REQ 实际带的轴挑一条回。
        if len(body) != TURN_WAIST_REQ_BODY:
            raise ValueError(
                "turn-waist request must contain exactly"
                f" {TURN_WAIST_REQ_BODY} bytes, got {len(body)}")
        if not turnWaistOn():
            return True
        reply, xValue, yValue = turnWaistReply(body)
        svrTime = wire.serverNowMs()
        if reply == TURN_WAIST_XY_RES:
            payload = struct.pack(">HQQhh", reply, wire.ACTOR_ID, svrTime,
                                  xValue, yValue)
        elif reply == TURN_WAIST_X_RES:
            payload = struct.pack(">HQQh", reply, wire.ACTOR_ID, svrTime, xValue)
        else:
            payload = struct.pack(">HQQh", reply, wire.ACTOR_ID, svrTime, yValue)
        flow.send(HAVOK_COMMAND, payload,
                  "turn-waist-res sel=%d x=%d y=%d svr=%d"
                  % (reply, xValue, yValue, svrTime))
        return True
    if selector == FLY_INIT_POSE_REQ:
        if len(body) != FLY_INIT_POSE_BODY:
            raise ValueError(
                "fly init-pose request must contain exactly"
                f" {FLY_INIT_POSE_BODY} bytes, got {len(body)}")
        weaponType, weaponTid = struct.unpack_from(">ii", body, 2)
        pose = struct.unpack_from(">6f", body, 10)
        precision = struct.unpack_from(">f", body, 34)[0]
        reqMoMid = struct.unpack_from(">Q", body, 38)[0]
        seq = flow.session.get("flyerSeq", 0) + 1
        flow.session["flyerSeq"] = seq
        # ⚠️ 会话字典由原生层序列化，**只认字符串键**：int 键会让整条事件被
        # 「unsupported state type: dict」丢弃、回包发不出去（2026-09-26 16:42 实机）。
        flow.session.setdefault("flyers", {})[str(seq)] = weaponTid
        # ⭐ 2026-10-03 18:5x：把 REQ 的**原始** fly_pose / precision 落一行日志。
        #   弓（type=49）是本链第一个「直线射击」武器，而之前只记了 RES 的结果，
        #   看不到客户端到底报了什么姿态。要判断箭该「原样回显」还是「服务端算初速」，
        #   必须有 REQ 原值做对照 —— 这一行就是为此加的（`fly-init-pose-req`）。
        #   `held` = 从按下左键（进 164）到这条 REQ 的间隔 = **隐形蓄力的时长**；
        #   `dirLen` = REQ 里 dir 三个浮点的模长 = 客户端算的**初速度**（m/s）。
        #   对照方法：按住左键久一点再松，`held` 应变大；若 `prec` / `dirLen` 也跟着
        #   变大 ⇒ 蓄力是客户端算的（纯回显即正确）；若两者**恒定** ⇒ 才需要在服务端
        #   按 `held` 重算初速（届时开 `T7_BOW_CHARGE`，见下方常量区）。
        pressMs = flow.session.get("bowPressMs")
        heldMs = max(0, wire.serverNowMs() - pressMs) if isinstance(pressMs, int) else 0
        dirLen = math.sqrt(pose[3] ** 2 + pose[4] ** 2 + pose[5] ** 2)
        flow.result["logs"].append(
            "fly-init-pose-req seq=%d tid=%d type=%d prec=%.2f mo=%d held=%dms "
            "dirLen=%.2f pose=%.2f,%.2f,%.2f,%.2f,%.2f,%.2f"
            % (seq, weaponTid, weaponType, precision, reqMoMid, heldMs, dirLen, *pose))
        # 这里**不扣弹**：扣弹与 HUD 递减在松手拍的 sel=4 推送里已完成，
        # 在这里再扣就是双扣（申请与本包先后无关，一直都在发）。
        # 走到无假人的空地后 19/19 仍自撞，且碰撞点=「我们发的出生点往回
        # 1.7m」≈投掷者身体 ⇒ **客户端本机在手上生成弹体、根本不用我们
        # RES 里的 fly_pose 坐标**（出生点前移说从原理上即无效，已撤）。
        # ⭐ 2026-09-26 19:2x 实机（会话 16052-520459635）：**不清坑之后烟筒真的
        # 活了**——自撞报告照旧（出生即在玩家身上），但我方不回 sel=11，客户端
        # 1~3 秒后自己补 sel=10「落地」（seq 全部对得上）⇒ 自撞≠弹体死亡，
        # 之前「弹体一生成就报废」是我们自己一发 sel=11 掐死的。飞戟相反：
        # 自撞之后再无任何报告（既不落地也不消失）。
        # ⇒ 本轮单一变量：**去掉给飞戟(type=54)出生点前移 2 米的动作**，让两种
        # 武器的 RES 变成逐字节的同一套写法（纯回显客户端 REQ 姿态）。飞戟还
        # 是不飞，就证明差异 100% 在客户端内部（表/类型），不在我们发的内容里。
        # 已否证清单：mo_mid（填 1 无差）、svr_time（纪元/进图相对都无差）、
        # precision、流水号、clt_report_collision=0（19:0x 轮：填 0 后自撞报告
        # 一条不少 ⇒ 它不管碰撞检测）。保留 0 只为不再动别的字段。
        reportCollision = 0
        # ⭐⭐ 2026-10-03 18:5x：**远程直射类（默认 type=49 弓）原样回显 REQ 姿态**。
        #   见常量区 FLY_ECHO_DEFAULT 处的取证：抛射写法（前移 2.5m + 抬高 1m +
        #   45° 仰角 8 m/s）是给投掷物调的，套到箭上会让它「凭空出现在身前、
        #   斜向上 8 m/s 飘出去」，而箭的初速本该由客户端写在 REQ 的 fly_pose 里。
        #   一个字段都不改地回显 ⇒ 客户端拿到什么就用什么。
        #   `T7_FLY_ECHO` 可覆盖（逗号分隔 type / `all` / `off`）。
        # ⭐ 兜底：**服务端按「按住时长」重算弓的初速**（默认关，见常量区
        #   BOW_CHARGE_ENV 的说明）。只有在实机证明 REQ 的 prec/dirLen 不随
        #   按住时长变化时才开；正常情况下走下面的「纯回显」。
        if bowChargeOn() and weaponType in BOW_CHARGE_TYPES:
            charged, speed, ratio = bowChargePose(pose, heldMs)
            charged, muzzleFwd, muzzleUp = bowMuzzlePose(charged)
            flow.send(HAVOK_COMMAND, struct.pack(
                ">HQii6fQIbQ", FLY_INIT_POSE_RES, wire.ACTOR_ID,
                weaponType, weaponTid, *charged, wire.serverNowMs(), seq,
                reportCollision, wire.ACTOR_ID),
                "fly-init-pose-res seq=%d tid=%d type=%d charge=1 held=%dms "
                "ratio=%.2f spd=%.2f muzzle=%.1f/%.1f "
                "pos=%.1f,%.1f,%.1f dir=%.2f,%.2f,%.2f"
                % (seq, weaponTid, weaponType, heldMs, ratio, speed,
                   muzzleFwd, muzzleUp, *charged))
            return True
        if flyEchoes(weaponType):
            flow.send(HAVOK_COMMAND, struct.pack(
                ">HQii6fQIbQ", FLY_INIT_POSE_RES, wire.ACTOR_ID,
                weaponType, weaponTid, *pose, wire.serverNowMs(), seq,
                reportCollision, wire.ACTOR_ID),
                "fly-init-pose-res seq=%d tid=%d type=%d prec=%.2f echo=1 "
                "mo=%d pos=%.1f,%.1f,%.1f dir=%.2f,%.2f,%.2f"
                % (seq, weaponTid, weaponType, precision, wire.ACTOR_ID, *pose))
            return True
        # ⭐ 出生点前移/抬高：**已实机证实生效**（落地点跟着平移 ⇒ 客户端确实
        # 拿我们 RES 的 fly_pose 生弹体，2026-09-26 20:2x）。注意自撞报告与坐标
        # 无关（前挪 2.5m 后 38/38 仍报打在投掷者身上）⇒ 那条报告是本机预测碰
        # 撞、不清坑就行（见 hitMessage），别拿它当「弹体报废」判据。
        # ⚠️ 2026-09-26 21:5x 实机否证「出生点推出玩家碰撞体」：**只给飞戟**把出
        # 生点推到 6.0m/1.5m（会话 16052-529256747，10 发全部带 `adj=6.0/1.5` 的
        # 新码标记）⇒ **10 发仍 0 落地**、每发照旧 12~20ms 报自撞。同场烟筒 39 发
        # 39 落地（对照有效）。⇒ 「弹体一出生撞在自己身上」不是我们 RES 坐标能
        # 左右的，飞戟的差异在客户端按 weapon_type 的表/资源，报文这条路到头。
        adjForward, adjUp = 2.5, 1.0
        px, py, pz, dx, dy, dz = pose
        horiz = math.hypot(dx, dy)  # 水平面在 x,y（pos 第三分量=高度，实测 47.1→44.5）
        ux, uy = (dx / horiz, dy / horiz) if horiz > 1e-6 else (1.0, 0.0)
        # ⭐⭐⭐ 2026-09-26 20:2x 实机定案（会话 16052-524644470）：**fly_pose 的
        # dir 三个浮点＝初速度（米/秒），不是单位朝向**。上一轮把 dir 写成单位
        # 向量(长度 1) ⇒ 初速 1 m/s ⇒ 从 2.6m 高落地 0.76s ⇒ 水平只走 0.7m，
        # 与实测「沿发出方向 0.5~1.1m、侧向偏 0.0x、下落越深走得越远」逐条吻合
        # （落点还跟着我们把出生点前挪的 2.5m 一起平移 ⇒ 客户端真用我们的坐标）。
        # ⇒ 「距离固定」是我们把向量长度写死为 1 造成的，不是客户端的表。
        # 本轮：长度按**蓄力**放大 + 改成真正的抛射角。
        # 上一版（会话 16052-526984169，烟筒 32 发全落地）实测：发 6 m/s ⇒ 沿
        # 方向飞 7.3~7.9m（侧偏 ≤0.15、离手到落地 1.6s），把 g 反解出来≈5.9
        # （不是 9.8），且第三分量的向上速度确实生效（不算它就只该飞 5.4m）。
        # ⇒ 20° 仰角 + 6 m/s 在 g=5.9 下最高点只离手 0.35m ⇒ 看着是「平着溜
        # 出去」，不是抛物线。本轮改成 **45° 抛射 + 8 m/s**：顶点离手 2.7m、
        # 落点约 13 米、滞空约 2.3 秒。
        # precision 实测=蓄力量（飞戟轻掷 2.0、满蓄 4.5；烟筒无蓄力恒 0.00 ⇒
        # 取下限 2.0 兜底），倍率 4.0 ⇒ 烟筒/轻掷 8 m/s；上限截 11（45° 下已
        # 经是 22 米射程，满蓄 18 m/s 会甩到 57 米外，出图了）。
        speed = min(max(precision, 2.0) * 4.0, 11.0)
        tilt = 0.707  # sin(45°)，正号=朝上（pos 第三分量越大越高，同轴实测）
        cosT = 0.707
        reqDz = dz
        pose = (px + ux * adjForward, py + uy * adjForward, pz + adjUp,
                ux * cosT * speed, uy * cosT * speed, tilt * speed)
        # 已否证清单：mo_mid=1（会话 502825187，30/30 仍自撞）、svr_time 纪元
        # 毫秒（会话 503210580，49/49 仍自撞）、进图相对 tick。
        # 官方注释：RES 的 mo_mid=「攻击者控制的MO」。客户端 REQ 里恒填 0。
        moMid = wire.ACTOR_ID
        svrTime = wire.serverNowMs()
        flow.send(HAVOK_COMMAND, struct.pack(
            ">HQii6fQIbQ", FLY_INIT_POSE_RES, wire.ACTOR_ID,
            weaponType, weaponTid, *pose, svrTime, seq, reportCollision, moMid),
            "fly-init-pose-res seq=%d tid=%d type=%d prec=%.2f svr=%d "
            "mo=%d reqmo=%d rpt=%d adj=%.1f/%.1f reqdz=%.2f spd=%.2f "
            "pos=%.1f,%.1f,%.1f dir=%.2f,%.2f,%.2f"
            % (seq, weaponTid, weaponType, precision, svrTime, moMid, reqMoMid,
               reportCollision, adjForward, adjUp, reqDz, speed, *pose))
        return True
    if len(body) != FLY_STOP_POSE_BODY:
        raise ValueError(
            "fly stop-pose request must contain exactly"
            f" {FLY_STOP_POSE_BODY} bytes, got {len(body)}")
    seq = struct.unpack_from(">I", body, 2)[0]
    flyerTid = flow.session.get("flyers", {}).get(str(seq), 0)
    flow.send(HAVOK_COMMAND,
              struct.pack(">HI", FLY_STOP_POSE_RES, seq) + body[6:],
              "fly-stop-pose-res seq=%d tid=%d" % (seq, flyerTid))
    # ⭐ 烟是谁放的（2026-09-27 只读取证定案，块号↔文件名靠 vfs 命名池对上）：
    # 客户端有**两棵树**跟烟有关——
    #   块 956 = ``../data/btree/烟雾弹_攻击.btree``（那颗飞行物本身）：落地
    #     (HIT_GROUND)→等 0.2 秒(HitGroundDelay)→条件「拥有者是本地单位」+「确实
    #     贴着地面」→动作「请求飞行物触发」→``Event FIRE`` 才「播放特效 烟雾弹」
    #     + 销毁自己；另有一条保险=出手 3 秒没落地也强制触发(ForceHit)。
    #   块 957 = ``../data/btree/烟雾弹范围.btree``（**持续的那团烟**）：整个树只有
    #     进入节点=「播放特效 特效实体名称=烟雾弹」+ 循环音效
    #     Play_Fire_Grenade_Hit_Bomb_Fire_Loop，退出节点=「删除特效 Effect.Smoke」。
    #     ⇒ 这团烟**不需要任何触发消息**，只要这颗物件出现在客户端视野里就放。
    # **已否证 #1**：给落地补发 cmd=4 sel=41「一次闪光弹爆炸通知」（会话 16052-536259449，
    # 6/6 发出去、字节数与 TDR 定义逐字节吻合、客户端无反应）⇒ 那条不是开关。
    # **已否证 #2**（2026-09-27，会话 16052-538360151，scriptVersion 56fe3ca67047）：
    # 上一轮自造的「cmd=14 sel=1 第 5 号分支简版 MO」78/78 全发出去了（wireLength=279，
    # 布局逐字节自核过），用户回话「没有」烟；落地后上行只有心跳/移动，无报错无新请求。
    # ⇒ 那条通道是**我凭空编的**，没有任何实机证据支撑，不算反解结论。
    # 物件号不用猜：MO 定义表（vfs 块 122436 起 171 块）row358 id=**1080511**
    # （=烟筒武器 tid 本身）、名「灭火物件/烟雾弹范围」、行内 offset 3372 f32=**5.0**
    # 半径；特效表（块 121159）`烟雾弹`=资源 YWD_hurt_001.nif、时长 13 秒。
    # **本轮单一变量**：换成**已实机跑通的那条下发路**——樊城 24 件器械（云梯/攻城车/
    # 投石车）能显示出来走的就是 `encode_cc_dynamic_vision_add_event`（cmd=14 sel=1
    # 第 4 号分支，202B 结构，见 ccobject.py），只把 res_id 填 1080511、位置填落点。
    # 其余字段全用该编码器的默认值：state=0（改动前的 24 件也是 0 却**看得见**，所以
    # 状态号不是可见性的门槛，留 0 才不构成第二个变量）、hp=1、completeness=100。
    # 判新旧码=日志里出现 `smoke-cc-add`（`smoke-mo-add` 是已否证那一轮的标记）。
    # ⭐⭐ 2026-09-27 01:0x **实机通过**（会话 16052-539361884，scriptVersion
    # 02c59913925d）：16/16 条 `smoke-cc-add`、每条 wireLength=243（=23B 壳+净 220B），
    # 用户回话「终于冒烟了」。落地后客户端**一条上行都没多发**（还是只有心跳/移动）
    # ⇒ 印证了取证：这团烟不需要任何触发消息，物件一进视野就自己播。
    #
    # **2026-09-27 01:2x 实机定案：这条通道是通用的**（用户回话「有烟 也有火」）。
    # 探针=在同一落点再挂一颗 res_id=1080411（MO 定义表 row351「燃烧物件/燃烧范围1a」，
    # 树=`../Data/BTree/燃烧范围放大.btree`=vfs 块 970：进入节点 RAND(50/50)「播放特效
    # 燃烧弹地面受击01/02放大」、退出节点删特效 FXRoot/Effect.Aoe、执行节点空 ⇒ 和烟树
    # 完全同构，同样不需要触发消息；特效资源 RSP_hurt_001.nif、时长 8 秒、分类
    # Effect.Aoe，配额单体 2/全局 20；row351 offset 3372 半径=1.5，烟那行是 5.0）。
    # ⇒ cmd=14 sel=1 第 4 号分支能凭空放出物件。**但别按 454 行吹**：454 是 MO 定义表
    # 的总行数；逐行解压自己的 btree 数过（脚本 mo_effect_census.py，输出
    # mo_effect_census_u8.txt）——**无 btree 143 行 / 树在命名池里找不到 13 行 /
    # 进入节点就播特效 52 行 / 进入节点不播 246 行**（另有 50 行的特效写在执行节点，
    # 要事件触发才播）。能「一出现就自己播」的只有那 **52 行**，去重后一小把：
    # 燃烧地面(14 行共用)、烟雾弹、沙袋爆炸、麻药(11 行)、铁蒺藜、捡药治疗(6 行)、
    # 鞭炮、弩机烟尘、马践踏、弓战技箭雨、补给光圈、新手训练用的流光箭头/灯笼等。
    # **火是验证探针，验完已按用户要求删掉，只留烟。**
    if flyerTid in (SMOKE_MORTAR_TID, HERB_PACKET_TID):
        camp = int(flow.session.get("camp", 1))
        landing = struct.unpack_from(">3f", body, 6)
        cloudRid = SMOKE_CLOUD_RID_BASE + seq
        # 默认挂武器自己那颗 MO；``[battle] cc_res_id=`` 可换成别的 MO 号做验证
        # （香炉 1100531 带 1.5 米区域、草药包 1100131 带治疗光）。
        resId = wire.CC_LANDING_RES_ID or flyerTid
        marker = "herb-cc-add" if flyerTid == HERB_PACKET_TID else "smoke-cc-add"
        flow.send(vision_flow.VISION_COMMAND,
                  vision_flow.encode_cc_dynamic_vision_add_event(
                      objects=(vision_flow.CcDynamicObject(
                          inst_id=cloudRid & 0xFFFF, res_id=resId,
                          position=landing, rid=cloudRid, camp=camp),),
                      server_time_ms=wire.serverNowMs()),
                  "%s rid=%d res=%d pos=%.1f,%.1f,%.1f camp=%d"
                  % (marker, cloudRid, resId, *landing, camp))
    return True


# --- cmd=4 sel=5 BATTLE_HIT（客户端报「飞行物撞到东西」）-----------------------
# TDR 布局（82B 净）：target_rid u64 | source_weapon_tid i32 | target_weapon_tid
# i32 | rb_type u8 | rb_index u8 | collision_pos 12B | collision_dir 16B |
# collision_normal 16B | material_id i32 | act_seq_no i32 | flyer_seq_no u32@70
# | attacker_rid u64。
# ⭐ 2026-09-26 定案（会话 16052-496152550 + 494412597 交错投掷铁证）：客户端
# **确实在读我们的 sel=8**（落地报告的 seq 连交错跳号都与我们的全局流水号吻合）。
# 每场**前 3 个**弹体（不分武器）创建后 15-20ms 即自撞报 HIT；此后飞戟既不撞也
# 不落地 ⇒ 判因：自撞弹体无人清理、客户端飞行物坑位（疑似 3 个）被占满，后续
# 飞戟 Add Flyer Failed。烟筒不受影响=烟的特效不走弹体坑位。
# 本轮单一变量：收到 HIT 且流水号是我们的弹体时，补发 sel=11 STOP_RES
# （客户端已证实会处理这条——烟筒落地闭环走的就是它）把它清掉、腾出坑位。
BATTLE_COMMAND = 4
BATTLE_HIT_SELECTOR = 5
BATTLE_HIT_BODY = 84  # 2B 选择子 + 82B 结构


def hitMessage(flow, command, selector, body):
    if command != BATTLE_COMMAND or selector != BATTLE_HIT_SELECTOR:
        return False
    if (flow.session["role"] != "instance" or flow.session.get("leaving")
            or flow.session.get("controlBaseline") != wire.BASELINE_ID):
        return False
    if len(body) != BATTLE_HIT_BODY:
        raise ValueError(
            "battle hit must contain exactly"
            f" {BATTLE_HIT_BODY} bytes, got {len(body)}")
    targetRid, srcTid, dstTid = struct.unpack_from(">Qii", body, 2)
    rbType, rbIndex = struct.unpack_from(">BB", body, 18)
    collisionPos = body[20:32]
    flyerSeq = struct.unpack_from(">I", body, 72)[0]
    attackerRid = struct.unpack_from(">Q", body, 76)[0]
    flow.result["logs"].append(
        "battle-hit target=%d src_tid=%d dst_tid=%d rb=%d/%d pos=%s"
        " flyer_seq=%d attacker=%d"
        % (targetRid, srcTid, dstTid, rbType, rbIndex,
           collisionPos.hex(), flyerSeq, attackerRid))
    if flyerSeq and str(flyerSeq) in flow.session.get("flyers", {}):
        # ⭐ 2026-09-26 19:0x 实机（会话 16052-519109402，rpt=0 已生效）：
        # clt_report_collision 填 0 后**自撞报告照旧 19/19 条**、时序与填 1 时
        # 一致（我方 sel8 后 13~20ms）⇒ 这个字段管不着碰撞检测，本轮否证。
        # 现在唯一没被否证的凶手就是**我们自己发的 sel=11**：收到「打中我自己
        # （target_rid=ACTOR_ID）」就把弹体清掉。烟筒恰恰是在加入这段清坑逻辑
        # 的那一轮从「有动画」变成「没动画」的。
        # 本轮单一变量：**target 是投掷者本人时不清坑、只记一行**，弹体留着看
        # 它能不能继续飞。打中别人/地面（target≠自己）照旧发 sel=11。
        # 判新旧码：reason 里出现 `self-hit-kept`。
        if targetRid == wire.ACTOR_ID and attackerRid in (0, wire.ACTOR_ID):
            flow.result["logs"].append(
                "self-hit-kept seq=%d tid=%d"
                % (flyerSeq, flow.session["flyers"][str(flyerSeq)]))
            return True
        flow.send(HAVOK_COMMAND,
                  struct.pack(">HI", FLY_STOP_POSE_RES, flyerSeq)
                  + collisionPos + b"\x00" * 24,
                  "fly-stop-on-hit seq=%d tid=%d"
                  % (flyerSeq, flow.session["flyers"][str(flyerSeq)]))
    return True
