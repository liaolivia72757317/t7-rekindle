# -*- coding: utf-8 -*-
"""关卡 NPC（训练场陪练 / 训练官）的**可击败化** —— 服务端伤害模型。

为什么需要
----------
``npc.py`` 把关卡 NPC 作为 actor 视野对象下发（``current_hp = maximum_hp = 100``），
但**此前没有任何东西会扣它的血**：

* 服务端没有伤害模型（``grep -rn "damage" scripts/`` **零命中**）；
* 客户端**不报近战命中** —— 2026-10-03 实机（会话 ``47680-1109853472``）里
  用户挥砍一次只上行 ``cmd=4 sel=1``（攻击意图 300540），
  ``cmd=4 sel=5``(BATTLE_HIT) **全程 0 次**；那条是**飞行物**撞物专用
  （见 ``battle.hitMessage`` 的注释：客户端报「飞行物撞到东西」）。

于是陪练只是站着不动的摆设。用户实机问「有两个 NPC 怎么打败他们」。

本模块补上这一环
----------------
1. **命中判定**（服务端）：攻击链走到「过程」拍（``battle.ATTACK_RELEASE_INDEX``）时，
   取玩家当前坐标 + 朝向，在**前方扇形 + 近战距离**内挑**最近**的存活 NPC；
2. **扣血**：``session["npcHp"]`` 记账（懒初始化，读 ``npc.actors()`` 的 hp）；
3. **回包**（三条都是 ``codec/battle_flow.py`` 里**早已还原好、但从未启用**的编码器）：
   * ``battle_result``(sel 0x09)    —— 权威伤害（客户端据此跳伤害数字 / 掉血条 / 判死）
   * ``hit_point_notify``(sel 0x1B) —— 命中特效（火花）
   * ``dead_notify``(sel 0x0A)      —— 血尽时通知死亡
4. **连杀提示**（2026-10-04 新增，``codec/conk_flow.py`` + ``codec/score_flow.py``）：
   玩家每击杀一个 NPC ⇒ 连杀数 +1，下发**两条**协议：

   * ``SH_CS_CMD_CONK``(19) 的 ``CS_PROTO_CONTINUOUS_KILL_EVENT``（带 连杀数）；
   * ``SH_CS_CMD_SCORE``(12) 的 ``E_CS_PROTO_SCORE_STAT_MSG``（带**播报消息 id**）。

   ⚠️ 2026-10-04 第二轮：CONK 那条**字节完全正确**（按 exe 内嵌 TDR 逐字段核对过，
   wire 抓包 20 条全发）但客户端**毫无反应**（用户实机「还是没有 战场屠夫」）。
   本轮直读客户端资源找到决定性证据：``../data/table/s_res_score_cli.bin``
   （**局内计分项表**）**以全局消息 id 为键**，行里就是播报的图标+文案+名称
   （710050=五连杀、710110=大杀特杀、710120=屠夫「连续击杀10名敌人！」、730000=第一滴血…）；
   而本服务端**从未发过 cmd=12**（wire S2C 命令字直方图里没有 12）⇒ 播报链路整条缺失。
   故改由 ``score_flow`` 下发播报（CONK 保留，两条互不影响）。
   开关 ``[npc] streak`` / ``T7_NPC_STREAK``（默认 on，管 CONK）+
   ``[npc] score_msg`` / ``T7_NPC_SCORE_MSG``（默认 on，管播报）。

   ⚠️ 2026-10-04 第三轮：`战斗结果提示.psheet` 将消息 id 映射到 Wwise Event 名；710120
   没有对应记录，而 710110 映射到 `Play_zhanchangtufu`。这是**资源映射事实**，并不保证
   客户端收到 710110 就执行 PostEvent。`butcher_mode` 仍用于选播报 id：voice/text/both/spread；
   声音触发仍待实机确认。

   ⚠️ 2026-10-04 第四轮（**历史假设，2026-10-05 已被实机否证**）：当时因「只发 sel=2 有字无声」
   推断是缺 ``sel=1`` 的连杀计分项，于是补发 ``CS_PROTO_SCORE_UPDATE_EVENT``。
   TDR 语义仍然成立：sel=1 的 ``stat_msg_id`` =「变更原因」(op)，连杀数在
   ``E_SCORE_FIELD_MANSLAUGHTER``(6)；但这只能解释计分数据，**不能证明它能触发语音**。
   2026-10-05 会话 ``43352-1285792500`` 确实发出 20 条 830B 定长 sel=1，普通 sel=2
   也发出 19 条，用户仍无语音。另据 TDR，``CS_PROTO_SCORE.score_items[67]`` 是定长，
   旧版变长口径已改正；见 ``score_update_pad``。

   ⚠️ 2026-10-04 第五轮：补充独立音效广播 ``cmd=10 sel=301``（26B），``sound_id`` 查
   ``音效id表.psheet``（有喊话、音乐、命中和普通连杀音效号）。**它能否触发 HUD 连杀语音
   尚未证明**；表里没有 ``tufu``，因此不能靠它播 ``Play_zhanchangtufu``。
   编码器 ``codec/audio_flow.py``；开关 ``[npc] audio`` / ``T7_NPC_AUDIO``。

   ⚠️ 2026-10-04 第六轮（**历史推断，运行时触发仍未定案**）：``音效id表.psheet`` 里没有
   ``tufu``，所以 ``cmd=10 sel=301`` 不能直接调用 ``Play_zhanchangtufu``。exe 静态字符串/RTTI
   表明 ``CGeGameTipService`` 与 ``战斗结果提示.psheet`` 有关，但相邻字符串/指针本身**不能证明**
   具体运行时触发报文。第六轮加的 ``cmd=11 sel=1`` UI 打印保留为候选（``codec/ui_flow.py``，
   开关 ``[npc] ui_print`` / ``T7_NPC_UI_PRINT``）；2026-10-05 的真实 `sel=2` 报文依旧无声，
   需执行隔离版 `voice_sweep` 才能再判。

   ⚠️⚠️ 2026-10-04 第七轮（用户「**左边是字 战场屠夫 右边 是大杀特杀 没有语音**」）：
   本轮把链路**整条挖通**，并**推翻第六轮的 id 口径**：

   1. ``战斗结果提示.psheet`` 的 ``UI_STR_ID`` 列 → ``../data/table/s_ui_str_cli.bin``
      （**真身 = data1.vfs run 22825，MSES rs=536 rc=359**；旧 ``s_ui_str_cli`` 抽到的是
      漂移错的 GFX 文件，已废弃）。行 = `id / 台词(@4) / 图标名(@260) / 语音事件名(@408)`。
      **交叉验证**：psheet 的 UI_STR_ID 14/15/45..50/90000/90001 全部命中该表；
      且 15→图标 ``dashatesha``（对应语音 ``Play_dashatesha``）、
      90000→``tufu``（对应 ``Play_zhanchangtufu``）—— 图标与语音一一对应。
   2. ⇒ ``cmd=11 sel=1`` 的 ``id`` 是 **字符串资源 id（UI_STR_ID）**，不是 7xxxxx 全局消息 id。
      屠夫 = **90000**（台词「**谁敢杀我！**」+ 图标 ``tufu``）、十连杀 = 15、四连杀 = 45 …
      ⇒ 第 ⑤ 条已改；开关 ``[npc] ui_print_mode`` = ``str``（默认，正解）/ ``msg``（旧写法）。
   3. **「战场屠夫」四个字在客户端根本不存在** —— ``data1.vfs`` 全部 **26971 个 run 逐个解压**
      + ``TieJiClient.exe`` / ``TieJiClientBase.dll`` / ``Data/**`` 三编码（GBK/UTF-8/UTF-16LE）
      均 **0 命中**（工具 ``hkx_decode/_scan_vfs_multi.py``）；对照「大杀特杀」2 处、
      「屠夫」9 处、「超神」1 处都在 ⇒ 搜索方法有效。
      psheet 里 710110 的**注释**写作「绝命屠夫」；HUD 上真正的字是「谁敢杀我！」。
    4. **语音入口仍未闭环**。`voice_sweep` 有 14 项（第 2..15 次击杀，之后取模）；2026-10-05
       会话 `3880-1299637081` 已实际发出 20 条候选（完整矩阵 + 重复项），但用户仍反馈无声。
       该隔离局同时关闭了常规计数/记分开关，导致那局 0 统计；当前已恢复日常开关并关闭 sweep。
       具体候选与实发内容见本模块 `_VOICE_SWEEP` 和 `level.ini`。

   ⚠️⚠️ 2026-10-04/05 第八轮复盘（继续追「没有语音」）—— 静态字符串/RTTI 显示 `CGeGameTipService` 与 `cmd=12 sel=2` 有关联，
   但 2026-10-05 实机已确认该包按真实 streak id 发出仍不播声音；**不能再把静态相邻字符串当成实际 PostEvent 已触发的证明**：

   1. ★★ exe 字符串池显示 `CGeGameTipService`、`E_CS_PROTO_SCORE_STAT_MSG`、
      `战斗结果提示.psheet` 三者紧挨关联，但这**不能证明运行时 selector 注册/调用路径**。证据：（0x94C348 `战斗结果提示.psheet` / 0x94C3D8 `GeGameTipService` /
      0x94C3EC `E_CS_PROTO_SCORE_STAT_MSG`），而且 `战斗结果提示.psheet` 这个名字在整个
      exe 里**只有一处指针引用**（file 0x10FB100），正夹在 RTTI 名字
      `.?AV?CGEntityEventListener@VGeGameTargetService@@` 与 `.?AV?CGeGameTipService@@` 之间。
      同簇还列出该服务的成员/方法名：`ShowKillText`(localCamp/deadCamp/killerCamp/killerName/
      deadName/deadRebel/killerRebel/mcName)、`ShowKillNum`、**`ShowContinuousKill`**、
      以及一个成员就叫 **`连杀语音`**（在 `SetRebornTime` 与 `ShowKillNum` 之间）。
      ⇒ 这些是**静态归属证据**，不等于运行时一定会查表/PostEvent；2026-10-05 会话
      `43352-1285792500` 已按正确 710020..710110 发送 `cmd=12 sel=2`，仍无语音，
      所以需要用独立扫射验证真实触发路径。
   2. 音效资源**齐全**（`Data/Audio/Wwise/Windows/` 里每个 `.bnk` 都有配套 `.txt` 清单）：
      `Vox_System.txt` = 16 个 Event（`Play_erliansha` 832718087 … `Play_zhanchangtufu` 52711210、
      `Play_tufuzhongjiezhe` 1687865627、`Play_baotou`、`Play_first_blood`、`Play_double_kill`、
      `Play_multi_kill`、`Play_revenge`）；`Media_Vox_System.txt` = 20 条媒体
      （`zhanchangtufu` / `dashatesha` / `liuliansha` … 每条 10~32KB）。
   3. **bank 不会被卸载**：`instancetypeaudio.psheet` 里只有「初始音效资源」(-1) 那一行卸
      `Vox_System.bnk`；1~14 号玩法模式（含 5 死斗 / 9 单人训练 / 10 实战训练）都**不卸**。
   4. **客户端音频没被静音**：`Data/UserData/UserData.cfg` 里 `MusicMute=false MusicVolume=1
      AudioMute=false AudioVolume=1`。
   5. **`音效id表.psheet` 里确实没有屠夫**（`tufu` 零命中），只有
      `70002 /Voice/dashatesha_01`、`70003 /Voice/erliansha_01`、`70081 /Voice/liuliansha_02` …
      ⇒ 第五轮的 `cmd=10 sel=301` **天生播不出屠夫**，与 11/1 是两件独立的事。
   6. TDR 复核（全部命中，字节格式没问题）：
      `SH_CS_CMD_CONK=19`、`E_CS_PROTO_CONTINUOUS_KILL_EVENT=1`、
      `CS_PROTO_CONTINUOUS_KILL_PKG{msg_id:i16, data}`、
      `E_CS_PROTO_SCORE_UPDATE_EVENT=1` / `E_CS_PROTO_SCORE_STAT_MSG=2`、
      `E_CS_PROTO_UI_PRINT_STRING=1`（字段注释就是「**字符串资源id**」）、
      `E_TRANS_CONTENT_TYPE_DEFINE_3D_SOUND=1`（只有这一个有效值）、
      `E_MSG_STAT_API_DEF_*` 0..17（9=连杀判定 / 10=多重杀 / 14=屠夫终结者）。
   7. **武将音效.psheet 里还有一套「按武将」的连杀语音**（列名 `5连杀语音` / `7连杀语音` /
      `9连杀语音`，6/8/10 列为空）：
      `赵云 → Play_Voice_Commu_kill1_zhaoyun / kill5_zhaoyun / kill10_zhaoyun`、
      `貂蝉 → Play_Voice_Commu_kill1_woman_A …`。这套是**客户端自己按「本地玩家连杀数」
      查表播的**（exe 里有成员名 `连杀语音` 与这套列名同源）⇒ 服务端只要把**连杀数**
      正确送到（cmd=19 的 `continuous_kill_num`），武将语音就会自己响。
   8. ⇒ 本轮落地：`butcher_mode` 新增 **`spread`**（N≥10 时把 psheet 里三条可能带语音的行
      710110 / **71100** / **71110** 一次发全，对冲「客户端按哪个 id 查表」这个唯一未知），
      并把 `voice_sweep` 矩阵按上面的知识重排（12 个候选）。
      ⚠️ 仍**未定案**的只剩「到底哪条报文触发 PostEvent」——只能靠 `voice_sweep` 实机定位。

朝向约定（**已核实，不是猜的**）
--------------------------------
``controls.project()`` → ``move_flow.project_standard_ground_step``：
W 时 ``normalized_forward_back = -1``，位移 ``delta = (cosθ, -sinθ)``
（θ = ``heading`` 弧度，默认基向量）⇒ **世界前向单位向量 = (cosθ, -sinθ)**。
2026-09-22 实测（会话 13976-191219260）纯 W 时「位移方位角 + yaw == 0.0」，
误差 < 0.1°。本模块沿用这条已实测的约定。

开关
----
``[npc] battle=on|off`` / 环境变量 ``T7_NPC_BATTLE``（改 ini 后重启服务端才生效）。

⚠️⚠️⚠️ **键必须 str —— 实机事故（2026-10-03 16:30，已修）**
--------------------------------------------------------
首版把 ``session["npcHp"]`` / ``session["npcDead"]`` 的键写成 **mid(int)**
（``{30001: 80.0}``）。后果：

* 原生宿主序列化会话 state 时**只认 str key 的 dict**，遇到 int key 直接抛
  ``unsupported state type: dict`` 并**丢弃整条消息**；
* 被丢的正是那条挥砍上行 ``cmd=4 sel=1`` ⇒ 服务端**等于没收到** ⇒ 客户端动作链
  等不到确认 ⇒ **挥砍卡住出不来**；
* 而**远离 NPC** 时 ``pickTarget`` 提前 return、**根本不会创建这两个 dict**
  ⇒ 一切正常。

⇒ 用户看到的症状就是「**靠近 NPC 挥砍卡住，离远换地方上下左右都正常**」。

抓包铁证：``data/46708-1114847671/wire/frames-1.jsonl`` 里 13 条
``direction=ERROR / reason="unsupported state type: dict"``，时间 16:30:18 起
（= 第一次命中的时刻）。

**同一个坑 2026-09-24 踩过一次**（``app.py`` 的 ``upKey`` 用 tuple），当时症状是
**黑屏进不去**。``app.py:327`` 有完整记录。本轮又在 ``app.py`` 加了一道
**兜底 sanitizer**（``sanitizeState``，``handleEvent`` 返回前把非 str 键压成 str
并落盘告警），但**写新代码时仍必须直接用 str 键**。

⚠️ **本模块是附加能力**：开关关掉 / 场景没配 NPC / 任何异常 ⇒ 一个包都不发，
``battle`` 那条已验证的链**逐位不变**（与 ``npc`` / ``ccobject`` / ``tutorial`` 同一纪律）。
"""
import math
import os

from . import contracts as wire
from . import dungeon
from . import npc
from .codec import audio_flow, battle_flow, conk_flow, score_flow, ui_flow

# --- 可调参数（全部集中在这里，方便实机微调） --------------------------------
DAMAGE_PER_HIT = 20.0     # 每刀伤害（NPC 默认 hp=100 ⇒ 5 刀致死）
# ⚠️ 2026-10-04：npc_ai 把 NPC 的停步距离从 3.0 调到 5.0 米（用户「离远一点」），
# 所以玩家侧的命中距离必须跟着放到 6.0，否则 NPC 站在打不到的地方 ⇒ 打不死。
# 环境变量 ``T7_NPC_HIT_RANGE`` / ``[npc] hit_range`` 可现场微调。
MELEE_RANGE_M = 6.0       # 近战命中距离（米，水平面）
FRONT_ARC_DEG = 120.0     # 前方扇形总张角（±60°）
STREAK_WINDOW_MS = 10000  # 连杀窗口：两杀间隔超过它就从 1 重新计（避免隔很久还累加）

NPC_BATTLE_ENV = "T7_NPC_BATTLE"
NPC_BATTLE_INI_SECTION = "npc"
NPC_BATTLE_INI_KEY = "battle"
NPC_STREAK_ENV = "T7_NPC_STREAK"
NPC_STREAK_INI_KEY = "streak"
NPC_SCORE_MSG_ENV = "T7_NPC_SCORE_MSG"
NPC_SCORE_MSG_INI_KEY = "score_msg"
NPC_SCORE_MSG_MODE_ENV = "T7_NPC_SCORE_MSG_MODE"
NPC_SCORE_MSG_MODE_INI_KEY = "score_msg_mode"
NPC_BUTCHER_MODE_ENV = "T7_NPC_BUTCHER"
NPC_BUTCHER_MODE_INI_KEY = "butcher_mode"
NPC_SCORE_UPDATE_ENV = "T7_NPC_SCORE_UPDATE"
NPC_SCORE_UPDATE_INI_KEY = "score_update"
NPC_SCORE_UPDATE_MODE_ENV = "T7_NPC_SCORE_UPDATE_MODE"
NPC_SCORE_UPDATE_MODE_INI_KEY = "score_update_mode"
NPC_SCORE_UPDATE_PAD_ENV = "T7_NPC_SCORE_UPDATE_PAD"
NPC_SCORE_UPDATE_PAD_INI_KEY = "score_update_pad"
NPC_AUDIO_ENV = "T7_NPC_AUDIO"
NPC_AUDIO_INI_KEY = "audio"
NPC_UI_PRINT_ENV = "T7_NPC_UI_PRINT"
NPC_UI_PRINT_INI_KEY = "ui_print"
NPC_UI_PRINT_MODE_ENV = "T7_NPC_UI_PRINT_MODE"
NPC_UI_PRINT_MODE_INI_KEY = "ui_print_mode"
NPC_UI_PRINT_ID_ENV = "T7_NPC_UI_PRINT_ID"
NPC_UI_PRINT_ID_INI_KEY = "ui_print_id"
NPC_VOICE_SWEEP_ENV = "T7_NPC_VOICE_SWEEP"
NPC_VOICE_SWEEP_INI_KEY = "voice_sweep"
NPC_STREAK_BCAST_ENV = "T7_NPC_STREAK_BCAST"
NPC_STREAK_BCAST_INI_KEY = "streak_broadcast"
NPC_HIT_RANGE_ENV = "T7_NPC_HIT_RANGE"
NPC_HIT_RANGE_INI_KEY = "hit_range"


def _boolKnob(env, ini_key):
    """**环境变量 > [npc] <ini_key> > 默认 on**。返回 bool。"""
    raw = os.environ.get(env)
    if raw is None or not str(raw).strip():
        try:
            raw = wire.iniSection(NPC_BATTLE_INI_SECTION, None).get(ini_key, "")
        except Exception:  # noqa: BLE001 —— 读 ini 失败绝不拖垮战斗链
            raw = ""
    text = str(raw).strip().lower()
    if text in ("", "on", "all", "true", "yes", "1"):
        return True
    return False


def _knob():
    """**环境变量 > [npc] battle > 默认 on**。返回 bool。"""
    return _boolKnob(NPC_BATTLE_ENV, NPC_BATTLE_INI_KEY)


ENABLED = _knob()


def _streakKnob():
    """**环境变量 T7_NPC_STREAK > [npc] streak > 默认 on**。返回 bool。

    连杀提示靠下发 ``SH_CS_CMD_CONK`` 连杀事件驱动（见 ``codec/conk_flow.py``）；
    关掉则一个 CONK 包都不发。
    """
    return _boolKnob(NPC_STREAK_ENV, NPC_STREAK_INI_KEY)


STREAK_ENABLED = _streakKnob()


def _scoreMsgKnob():
    """**环境变量 T7_NPC_SCORE_MSG > [npc] score_msg > 默认 on**。返回 bool。

    连杀/屠夫**播报**靠下发 ``SH_CS_CMD_SCORE`` 的 ``E_CS_PROTO_SCORE_STAT_MSG``
    驱动（消息 id 取自客户端 ``s_res_score_cli.bin``，见 ``codec/score_flow.py``）；
    关掉则一个播报包都不发（CONK 那条不受影响）。
    """
    return _boolKnob(NPC_SCORE_MSG_ENV, NPC_SCORE_MSG_INI_KEY)


SCORE_MSG_ENABLED = _scoreMsgKnob()


def _scoreMsgMode():
    """``id``（默认，取证最充分）| ``op``（备用）。

    ``id`` = ``stat_msg_id`` 填 ``s_res_score_cli`` 的行 id（710050 五连杀…）；
    ``op`` = 填 ``E_MSG_STAT_API_DEF_CONTINUE_KILL_OP``(9)、``custom_data`` = 连杀数。
    见 ``codec/score_flow.py`` 顶部说明；默认 ``id``。
    """
    raw = os.environ.get(NPC_SCORE_MSG_MODE_ENV)
    if raw is None or not str(raw).strip():
        try:
            raw = wire.iniSection(NPC_BATTLE_INI_SECTION, None).get(
                NPC_SCORE_MSG_MODE_INI_KEY, "")
        except Exception:  # noqa: BLE001
            raw = ""
    return score_flow.normalizeMode(raw)


SCORE_MSG_MODE = _scoreMsgMode()


def _butcherMode():
    """N≥10 的屠夫播报用哪个 id：``voice``（默认）| ``text`` | ``both``。

    * ``voice`` → 710110（``GLOBAL_MSG_BATTLEFIELD_BUTCHER``）——
      **有语音** ``Play_zhanchangtufu``（战场屠夫），文字 = 计分表里的「大杀特杀」；
    * ``text``  → 710120（``GLOBAL_MSG_BUTCHER``）——有「屠夫」二字，**没有语音**；
    * ``both``  → 两条都发（有音 + 有「屠夫」二字，屏幕上会出两行字）。

    依据：``../data/propsheet/战斗结果提示.psheet`` 里**只有 710110**，没有 710120
    （见 ``codec/score_flow.py`` 顶部「语音层」一节）。
    """
    raw = os.environ.get(NPC_BUTCHER_MODE_ENV)
    if raw is None or not str(raw).strip():
        try:
            raw = wire.iniSection(NPC_BATTLE_INI_SECTION, None).get(
                NPC_BUTCHER_MODE_INI_KEY, "")
        except Exception:  # noqa: BLE001
            raw = ""
    return score_flow.normalizeButcherMode(raw)


BUTCHER_MODE = _butcherMode()


def _scoreUpdateKnob():
    """**环境变量 T7_NPC_SCORE_UPDATE > [npc] score_update > 默认 on**。返回 bool。

    连杀**语音**（以及 Tab 记分板）靠下发 ``SH_CS_CMD_SCORE`` 的
    ``E_CS_PROTO_SCORE_UPDATE_EVENT``(sel=1) 驱动 —— 它的 ``stat_msg_id`` 是
    「变更原因」op（9=连杀判定），连杀数在 ``E_SCORE_FIELD_MANSLAUGHTER`` 计分项里。
    关掉则一个 sel=1 包都不发（sel=2 播报 / CONK 不受影响）。
    """
    return _boolKnob(NPC_SCORE_UPDATE_ENV, NPC_SCORE_UPDATE_INI_KEY)


SCORE_UPDATE_ENABLED = _scoreUpdateKnob()


def _scoreUpdateMode():
    """sel=1 的 ``stat_msg_id`` 填 ``op``（默认）还是 ``id``（A/B 对照用）。"""
    raw = os.environ.get(NPC_SCORE_UPDATE_MODE_ENV)
    if raw is None or not str(raw).strip():
        try:
            raw = wire.iniSection(NPC_BATTLE_INI_SECTION, None).get(
                NPC_SCORE_UPDATE_MODE_INI_KEY, "")
        except Exception:  # noqa: BLE001
            raw = ""
    return score_flow.normalizeUpdateMode(raw)


SCORE_UPDATE_MODE = _scoreUpdateMode()


def _scoreUpdatePadKnob():
    """**环境变量 T7_NPC_SCORE_UPDATE_PAD > [npc] score_update_pad > 默认 on**。

    第八轮新增。``on``（默认）= sel=1 的 ``score_items`` **按 TDR 定长写满 67 槽**
    （补零到 808B）；``off`` = 旧的变长写法（``count + count×12B``）。
    详见 ``codec/score_flow.py`` ``encode_score_update_event`` 的第八轮说明 ——
    「Tab 记分板一直是空的」很可能就是客户端按定长解包时把变长包整条丢了。
    """
    return _boolKnob(NPC_SCORE_UPDATE_PAD_ENV, NPC_SCORE_UPDATE_PAD_INI_KEY)


SCORE_UPDATE_PAD = _scoreUpdatePadKnob()


def _audioKnob():
    """**环境变量 T7_NPC_AUDIO > [npc] audio > 默认 on**。返回 bool。

    ``cmd=10 sel=301`` 是独立音效广播；``sound_id`` 查 ``音效id表.psheet``。
    它适合用于喊话、音乐、命中及已列出的音效号测试；是否触发 HUD 连杀语音目前未证实。
    2026-10-05 隔离矩阵包含普通音效号和音乐控制样本。
    关掉则一个音效广播包都不发（其他候选通道不受影响）。
    编码器见 ``codec/audio_flow.py``。
    """
    return _boolKnob(NPC_AUDIO_ENV, NPC_AUDIO_INI_KEY)


AUDIO_ENABLED = _audioKnob()


def _uiPrintKnob():
    """**环境变量 T7_NPC_UI_PRINT > [npc] ui_print > 默认 on**。返回 bool。

    发送 ``cmd=11 sel=1 CS_PROTO_UI_PRINT_STRING``，用 ``UI_STR_ID`` 查字符串/图标。
    表字段与 psheet 映射已确认，但此包是否顺带播放连杀语音**尚未实机证明**；
    2026-10-05 隔离矩阵会单独测试 UI_STR_ID=90000。`cmd=12 sel=2` 普通正确 id 已发仍无声，
    但该次 `voice_sweep=off`，不能据此排除 UI 路径。

    编码器见 ``codec/ui_flow.py``；关掉则常规 `ui_print` 不发（sweep 的 ui1 候选仍可发）。
    """
    return _boolKnob(NPC_UI_PRINT_ENV, NPC_UI_PRINT_INI_KEY)


UI_PRINT_ENABLED = _uiPrintKnob()


def _textKnob(env, ini_key):
    """**环境变量 > [npc] <ini_key> > 默认 ""**。返回小写去空字符串。"""
    raw = os.environ.get(env)
    if raw is None or not str(raw).strip():
        try:
            raw = wire.iniSection(NPC_BATTLE_INI_SECTION, None).get(ini_key, "")
        except Exception:  # noqa: BLE001
            raw = ""
    return str(raw or "").strip().lower()


def _intKnob(env, ini_key, default=0):
    """**环境变量 > [npc] <ini_key> > default**。返回 int。"""
    try:
        return int(float(_textKnob(env, ini_key) or default))
    except (TypeError, ValueError):
        return int(default)


UI_PRINT_MODE_STR = "str"
UI_PRINT_MODE_MSG = "msg"


def _uiPrintMode():
    """``cmd=11 sel=1`` 的 ``id`` 填什么：``str``（默认，**正解**）| ``msg``（旧写法）。

    * ``str`` → **字符串资源 id**（= ``s_ui_str_cli.bin`` 主键 = psheet 的 ``UI_STR_ID``），
      屠夫 = **90000**（台词「谁敢杀我！」+ 图标 ``tufu``）、十连杀 = 15、四连杀 = 45 …
    * ``msg`` → 全局消息 id（710110），**第六轮的写法**；TDR 里这个字段注释是
      「字符串资源id」，填 710110 属于越界 id ⇒ 保留只为 A/B 对照。

    依据见 ``codec/ui_flow.py`` / ``codec/score_flow.py`` 顶部第七轮说明。
    """
    text = _textKnob(NPC_UI_PRINT_MODE_ENV, NPC_UI_PRINT_MODE_INI_KEY)
    if text in ("msg", "message", "id", "broadcast"):
        return UI_PRINT_MODE_MSG
    return UI_PRINT_MODE_STR


UI_PRINT_MODE = _uiPrintMode()


def _uiPrintIdOverride():
    """>0 时 ``cmd=11 sel=1`` 强制用这个**字符串资源 id**（实机定位用）；0 = 按 psheet 映射。"""
    return _intKnob(NPC_UI_PRINT_ID_ENV, NPC_UI_PRINT_ID_INI_KEY, 0)


UI_PRINT_ID = _uiPrintIdOverride()


def _voiceSweepKnob():
    """**环境变量 T7_NPC_VOICE_SWEEP > [npc] voice_sweep > 默认 off**。

    开启后，第 N 次击杀选择 14 项隔离矩阵中的候选（N=2..15，之后按 14 取模）。
    为避免叠加，诊断配置应把常规 streak/score_msg/score_update/audio/ui_print 全关；
    `_bumpStreak` 仍会累计击杀并只发送当前矩阵项。描述写在 wire reason，不写 trace_log。
    见 :func:`_voiceSweep`。
    """
    return _textKnob(NPC_VOICE_SWEEP_ENV, NPC_VOICE_SWEEP_INI_KEY) in (
        "on", "all", "true", "yes", "1")


VOICE_SWEEP_ENABLED = _voiceSweepKnob()


def _streakBcastKnob():
    """**环境变量 T7_NPC_STREAK_BCAST > [npc] streak_broadcast > 默认 on**。

    第十一轮（2026-10-06）**改通道**：连杀语音走 **sel=2**，不是 sel=1。

    ★ 决定性证据（客户端启动期注册表，``voice_forensics/step20~23``）
    ------------------------------------------------------------------
    客户端在 ``0xA89100`` 里用 ``0x7DAB60`` 把「**协议事件名字符串** → handler」
    写进 ``[this+0x1C]``。同一次注册里：

    * ``E_CS_PROTO_SCORE_STAT_MSG``    （cmd=12 **sel=2**）→ handler **0xA89EA0**
    * ``E_CS_PROTO_SCORE_UPDATE_EVENT``（cmd=12 **sel=1**）→ handler **0xB0D0D0**

    两个 handler 都先取消息的 ``data`` 字段（``0x425010``），然后：

    * **0xA89EA0**（sel=2）→ ``0xA87D80``（**唯一调用者 0xA89FB7**）→ ``0xA87920``
      → 查 ``service+0x74`` 的 ``map<int id, {CString 语音; i32 UI_STR_ID}>``
      → ``0xA8D650``（按 rid 找实体 + 武将音效.psheet 的「N连杀语音」列）
      → **PostEvent**（``[0x3224998]`` vtable+0x54 传事件名 / +0x58 传事件 id，
      实现体 ``0x7E76D0``，失败会记 ``Wwise: event error: <名>``）。
    * **0xB0D0D0**（sel=1）→ ``0xA57210`` → ``0xA5D510`` → ``0xB0DD80``，
      **完全不引用 0xA87D80** ⇒ 只更新记分板/连杀数，**永远不播语音**。

    ⇒ 第十轮 ⑥★ 把连杀 id（710020..710110）塞进 **sel=1** 是**走错通道**：
    它只会刷新记分板，这正是「有 HUD 字但没声」的成因。本轮已改为 **sel=2**。

    活内存交叉验证（2026-10-06，客户端 PID 49120，``probe_chain2.py``）
    ------------------------------------------------------------------
    * ``[0x3237804]+0x88`` = ``10``（``0x798DF0`` 的返回值）≠7 ⇒ 走**映射路径**（非 killNum 弹窗）；
    * 本地 rid = ``*(u64*)([0x323C7C0]+0x30)`` = **1**，与服务端 ``ACTOR_ID=1`` 一致
      ⇒ ``0xA87D80`` 走「rid 命中」分支，``0xA87920`` 的 arg4=1，走
      ``PostEvent(表内事件名)``（而不是 arg4=0 的那条固定 id ``0x715B3EE9``）；
    * ``service+0x74`` 映射表 12 条**全健康**：710020→Play_erliansha … 710110→
      Play_zhanchangtufu、730140→Play_tufuzhongjiezhe（含 UI_STR_ID）；
    * 服务端 wire 实测确实发过 sel=2（``npc-streak-msg id=710110 voice=Play_zhanchangtufu``，
      49B = 23B 头 + 2B sel + 24B data），报文逐字段正确（sel=2 / rid=1 / id=710020）。

    ⇒ 至此「服务端 → 客户端解析 → 映射表 → PostEvent」全链只剩 **Wwise 播放层**
    未证实；本开关用于在**只开它**的隔离局里复测（其余 streak/score_msg/
    score_update/audio/ui_print 全关）。
    """
    return _boolKnob(NPC_STREAK_BCAST_ENV, NPC_STREAK_BCAST_INI_KEY)


STREAK_BCAST_ENABLED = _streakBcastKnob()

# 连杀数 → (通道, id, 说明) 的**隔离诊断矩阵**（2026-10-05 修正）。
# 开扫射时应把其它通道（streak/score_msg/score_update/audio/ui_print）关掉，
# 避免同一次击杀有多个候选同时出声；_bumpStreak 的入口守卫仍允许 sweep 单独运行。
# 通道：
#   "score2" = cmd=12 sel=2（stat_msg_id 直接查战斗结果提示.psheet）
#   "conk"   = cmd=19 sel=1（continuous_kill_num，本地武将 5/7/9 档语音候选）
#   "ui1"    = cmd=11 sel=1（id = s_ui_str_cli.bin 主键）
#   "audio"  = cmd=10 sel=301（音效id表.psheet 的 5 位音效号）
#   "music"  = cmd=10 sel=301（控制项：验证 301 广播基本通路）
#   "op"     = cmd=12 sel=2 但 stat_msg_id 填 E_MSG_STAT_API_DEF_CONTINUE_KILL_OP(9)
#   "both"   = cmd=12 sel=2 + cmd=11 sel=1（两条候选同时发）
_VOICE_SWEEP = {
    2: ("score2", 710020, "控制：psheet 二连杀 → Play_erliansha"),
    3: ("score2", 710090, "控制：psheet 九连杀 → Play_jiuliansha"),
    4: ("op", 9, "对照：stat_msg_id 填 op=9，custom_data=连杀数"),
    5: ("conk", 0, "cmd=19 连杀数=5：按武将音效.psheet 查 5连杀语音"),
    6: ("ui1", 90000, "cmd=11 UI_STR_ID 90000：谁敢杀我！+ tufu"),
    7: ("conk", 0, "cmd=19 连杀数=7：按武将音效.psheet 查 7连杀语音"),
    8: ("audio", 70002, "cmd=10 音效id 70002=/Voice/dashatesha_01"),
    9: ("conk", 0, "cmd=19 连杀数=9：按武将音效.psheet 查 9连杀语音"),
    10: ("score2", 710110, "★ 正常十连档：psheet Play_zhanchangtufu"),
    11: ("score2", 71100, "psheet 的 5 位 id「十连杀」→ Play_dashatesha"),
    12: ("score2", 71110, "psheet 的 5 位 id「超神」→ Play_dashatesha"),
    13: ("music", 60009, "控制：音乐号 60009，验证 cmd=10 sel=301 通路"),
    14: ("both", 710110, "组合：cmd=12 sel=2 710110 + cmd=11 sel=1 90000"),
    15: ("score2", 710120, "负对照：计分表有「屠夫」，但语音 psheet 无此 id"),
}
# 14 个样本对应 N=2..15；N>15 按 14 取模循环。
_VOICE_SWEEP_SPAN = 14


def _voiceSweep(flow, num, now):
    """诊断扫射：按 ``_VOICE_SWEEP`` 发一条候选报文。绝不外抛。

    仅当此项被选中时发送一种诊断通道；第 2..15 次连杀覆盖 14 项，之后取模。
    本诊断矩阵故意隔离常规通道；声效触发与否仍须用户实机听辨。
    """
    try:
        n = int(num)
        if n < 2:
            return
        item = _VOICE_SWEEP.get(((n - 2) % _VOICE_SWEEP_SPAN) + 2)
        if not item:
            return
        channel, value, note = item
        if channel in ("score2", "op"):
            flow.send(
                score_flow.SCORE_COMMAND,
                score_flow.encode_score_stat_msg(now, wire.ACTOR_ID, value, n),
                "npc-voice-sweep #%d(%s) channel=cmd12/sel2 id=%d note=%s"
                % (n, channel, value, note))
        elif channel == "conk":
            flow.send(
                conk_flow.CONK_COMMAND,
                conk_flow.encode_continuous_kill_event(wire.ACTOR_ID, n),
                "npc-voice-sweep #%d channel=cmd19/sel1 rid=%d num=%d note=%s"
                % (n, wire.ACTOR_ID, n, note))
        elif channel == "ui1":
            flow.send(
                ui_flow.UI_COMMAND,
                ui_flow.encode_ui_print_string(
                    svr_time_ms=now, msg_id=value, belong_mid=wire.ACTOR_ID),
                "npc-voice-sweep #%d channel=cmd11/sel1 id=%d note=%s"
                % (num, value, note))
        elif channel == "audio":
            flow.send(
                audio_flow.TRANS_COMMAND,
                audio_flow.encode_trans_multi_rsp(
                    server_time_ms=now, sound_id=value, owner_rid=wire.ACTOR_ID),
                "npc-voice-sweep #%d channel=cmd10/sel301 sound=%d note=%s"
                % (num, value, note))
        elif channel == "music":
            flow.send(
                audio_flow.TRANS_COMMAND,
                audio_flow.encode_trans_multi_rsp(
                    server_time_ms=now, sound_id=value, owner_rid=wire.ACTOR_ID),
                "npc-voice-sweep #%d channel=cmd10/sel301 sound=%d note=%s"
                % (num, value, note))
        elif channel == "both":
            flow.send(
                score_flow.SCORE_COMMAND,
                score_flow.encode_score_stat_msg(now, wire.ACTOR_ID, value, num),
                "npc-voice-sweep #%d channel=cmd12/sel2 id=%d note=%s"
                % (num, value, note))
            sid = score_flow.uiStringId(value)
            if sid > 0:
                flow.send(
                    ui_flow.UI_COMMAND,
                    ui_flow.encode_ui_print_string(
                        svr_time_ms=now, msg_id=sid, belong_mid=wire.ACTOR_ID),
                    "npc-voice-sweep #%d channel=cmd11/sel1 id=%d note=%s"
                    % (num, sid, note))
    except Exception as error:  # noqa: BLE001
        try:
            flow.result["logs"].append("npc-voice-sweep-failed " + repr(error))
        except Exception:  # noqa: BLE001
            pass


def _hitRange():
    """**环境变量 T7_NPC_HIT_RANGE > [npc] hit_range > 默认 6.0**。返回 float。

    与 ``npc_ai.NPC_MELEE_RANGE_M``（NPC 停步距离，默认 5.0）联动：
    本值必须比它大，玩家才砍得到停在原地的 NPC。
    """
    raw = os.environ.get(NPC_HIT_RANGE_ENV)
    if raw is None or not str(raw).strip():
        try:
            raw = wire.iniSection(NPC_BATTLE_INI_SECTION, None).get(
                NPC_HIT_RANGE_INI_KEY, "")
        except Exception:  # noqa: BLE001 —— 读 ini 失败绝不拖垮战斗链
            raw = ""
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return MELEE_RANGE_M
    return value if value > 0 else MELEE_RANGE_M


MELEE_RANGE_M = _hitRange()


def _forward(heading_degrees):
    """世界前向单位向量 ``(fx, fy)``。约定见模块 docstring。"""
    theta = math.radians(heading_degrees)
    return math.cos(theta), -math.sin(theta)


def _livePos(flow, mid, fallback):
    """NPC 的**实时**坐标。

    ``ai=on`` 时 NPC 会走位，实时位置存在 ``session["npcAi"][str(mid)]["pos"]``
    （``npc_ai`` 每拍更新）；AI 关着（静态靶）或查不到就退回出生点 ``fallback``。

    ⚠️ 为什么必须读实时坐标：玩家打的是「视觉上已走位的 NPC」，服务端若还按
    ``npc_actors.json`` 的出生点判定命中，NPC 一走动就永远够不着 ⇒ 不掉血。
    """
    try:
        table = flow.session.get("npcAi") or {}
        st = table.get(str(mid))
        pos = st.get("pos") if st else None
        if pos and len(pos) == 3:
            return pos
    except Exception:  # noqa: BLE001
        pass
    return fallback


def hpMap(flow, scene):
    """``session["npcHp"]``：``{str(mid): hp}``，懒初始化自 ``npc.actors()``。

    ⚠️⚠️ 键**必须是 str**（2026-10-03 实机事故，见模块 docstring「键必须 str」一节）。
    """
    table = flow.session.setdefault("npcHp", {})
    if not table:
        try:
            for actor in npc.actors(scene):
                table[str(actor["mid"])] = float(actor["hp"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return table


def aliveTargets(flow, scene):
    """本场景**还活着**的 NPC（``actor`` 列表），排除已死 mid。"""
    dead = flow.session.get("npcDead") or {}
    try:
        actors = npc.actors(scene)
    except (OSError, ValueError, KeyError, TypeError):
        return []
    return [a for a in actors if not dead.get(str(a["mid"]))]


def pickTarget(flow, scene, position, heading):
    """在**前方扇形 + 近战距离**内挑最近的存活 NPC。

    返回 ``(actor, distance)``；没有命中返回 ``(None, None)``。
    """
    targets = aliveTargets(flow, scene)
    if not targets:
        return None, None
    fx, fy = _forward(heading)
    cosLimit = math.cos(math.radians(FRONT_ARC_DEG / 2.0))
    best, bestDist = None, None
    for actor in targets:
        lp = _livePos(flow, actor["mid"], actor["pos"])
        ax, ay = lp[0], lp[1]
        dx, dy = ax - position[0], ay - position[1]
        dist = math.hypot(dx, dy)
        if dist > MELEE_RANGE_M or dist <= 1e-6:
            continue
        if (dx * fx + dy * fy) / dist < cosLimit:
            continue                      # 在背后 / 超出扇形
        if bestDist is None or dist < bestDist:
            best, bestDist = actor, dist
    return best, bestDist


def _scene(flow):
    try:
        from . import controls
        return controls.airWallScene(flow)
    except Exception:  # noqa: BLE001
        return flow.session.get("scene") or ""


def _bumpStreak(flow):
    """玩家每击杀一个 NPC：连杀数 +1，并下发连杀提示包。

    下发**五条**（各自独立开关，互不影响）：

    * ``CONK``(cmd=19) ``CS_PROTO_CONTINUOUS_KILL_EVENT`` —— 连杀事件（rid + 连杀数）；
    * ``SCORE``(cmd=12) ``E_CS_PROTO_SCORE_UPDATE_EVENT``（sel=1）—— 「变更原因」op
      （9=连杀判定）+ ``E_SCORE_FIELD_MANSLAUGHTER`` 连杀数（记分板）；
    * ``SCORE``(cmd=12) ``E_CS_PROTO_SCORE_STAT_MSG``（sel=2）—— **播报文案**
      （stat_msg_id = ``s_res_score_cli.bin`` 的行 id，如 710050 五连杀 / 710110 战场屠夫）；
    * ``INSTANCE``(cmd=10) ``E_CS_PROTO_SYNC_TRANS_MULTI_RSP``（sel=301）—— 音效广播
      （``sound_id`` 查 ``音效id表.psheet``，如 70003=二连杀 … 70002=大杀特杀）；
    * ``UI``(cmd=11) ``E_CS_PROTO_UI_PRINT_STRING``（**sel=1**）—— HUD 上**那行台词 + 图标**
      （``id`` = **字符串资源 id**，见第七轮更正）；
    * ``BCAST``(cmd=12 sel=1) ``E_CS_PROTO_SCORE_UPDATE_EVENT``（**stat_msg_id=7100xx**）
      —— ★ 第十轮主实验：sel=1 通道已被 KILL_OP(2) 修复证实可达，档位 id 见
      ``_streakBcastKnob`` 文档（``[npc] streak_broadcast``，默认 on）。

    ⚠️ 历史推断更正（2026-10-05）：TDR 明确 sel=1 是「变更原因」+ 计分项，sel=2 是
    「统计消息」；但这不证明哪条会播放语音。真实会话已按正确 id 发出常规 sel=2 仍无声，
    且当时 voice_sweep=off。sel=1 保留为计分数据，sel=2/sel=11/sel=19/sel=301 的声音作用
    需隔离实测，详见 ``codec/score_flow.py`` 的修正注释与下方 ``_VOICE_SWEEP``。

    ⚠️ ``cmd=10 sel=301`` 是独立音效广播；``音效id表.psheet`` 里 ``tufu`` 零命中，不能
    直接按这张表播放 ``Play_zhanchangtufu``。

    ⚠️⚠️ 2026-10-04 第七轮（用户「左边是字 战场屠夫 右边是大杀特杀 **没有语音**」）：
    本轮只闭合 UI_STR_ID 的映射与“战场屠夫”字串不存在；**语音触发链没有挖通**。

    1. ``战斗结果提示.psheet`` 的 ``UI_STR_ID`` 列指向 ``../data/table/s_ui_str_cli.bin``
       （**真身 = data1.vfs run 22825，MSES rs=536 rc=359**；旧的
       ``vfs_extract_by_name.py s_ui_str_cli`` 抽到的是漂移错的「GFX 13963B」，已废弃）。
       行 = `id / 台词 / 图标名 / 语音事件名`。
    2. ⇒ ``cmd=11 sel=1`` 的 ``id`` = **字符串资源 id（UI_STR_ID）**，不是 7xxxxx：
       屠夫 = **90000**（台词「谁敢杀我！」+ 图标 ``tufu``）、十连杀 = 15、四连杀 = 45 …
       本模块第 ⑤ 条已改（``[npc] ui_print_mode=str`` 默认；``msg`` 保留 A/B）。
    3. **「战场屠夫」四个字在客户端不存在**：全库 26971 个 run 解压 + exe/dll/dat 三编码
       均 0 命中（工具 ``hkx_decode/_scan_vfs_multi.py``）。HUD 上真正的字是「谁敢杀我！」。
    4. **「没有语音」仍未闭环** ⇒ 新增**诊断扫射** ``[npc] voice_sweep``（默认 off）：
       第 N 次连杀走不同通道 + 不同 id，日志打 ``npc-voice-sweep #N channel=... id=...``，
       14 样本矩阵（N=2..15，N>15 按 14 取模）。2026-10-05 会话 3880-1299637081 已完整
       跑完全矩阵仍无声 ⇒ 剩余嫌疑在客户端接收/触发侧。见 :func:`_voiceSweep`。

    连杀窗口 ``STREAK_WINDOW_MS``：两杀间隔超过它就从 1 重新计。
    **绝不抛异常**；session 里只写标量（``{"num":int,"lastMs":int,"kills":int}``，
    键 str，安全）。返回本次连杀数（0=未启用/失败）。
    """
    if (not STREAK_ENABLED and not SCORE_MSG_ENABLED
            and not SCORE_UPDATE_ENABLED and not AUDIO_ENABLED
            and not UI_PRINT_ENABLED and not VOICE_SWEEP_ENABLED
            and not STREAK_BCAST_ENABLED):
        return 0
    try:
        now = wire.serverNowMs()
        st = flow.session.get("npcKillStreak")
        if not isinstance(st, dict):
            st = {"num": 0, "lastMs": 0, "kills": 0}
        last = int(st.get("lastMs", 0) or 0)
        num = int(st.get("num", 0) or 0)
        if num < 0 or now - last > STREAK_WINDOW_MS:
            num = 0
        num += 1
        kills = int(st.get("kills", 0) or 0) + 1
        st["num"] = num
        st["lastMs"] = now
        st["kills"] = kills
        flow.session["npcKillStreak"] = st

        # --- ① sel=1 记分板 + 连杀数（stat_msg_id=变更原因 + score_items） ---
        if SCORE_UPDATE_ENABLED:
            op = score_flow.streakOp(num)
            if SCORE_UPDATE_MODE == score_flow.SCORE_UPDATE_MODE_ID:
                reason = score_flow.streakMessageId(num)
                if num >= 10:
                    ids = score_flow.butcherIds(num, BUTCHER_MODE)
                    reason = ids[0] if ids else 0
                reason = reason or op
            else:
                reason = op
            items = score_flow.streakScoreItems(
                num, prev_kills=kills - 1, prev_num=num - 1, kills=kills)
            flow.send(
                score_flow.SCORE_COMMAND,
                score_flow.encode_score_update_event(
                    now, wire.ACTOR_ID, reason, items, pad=SCORE_UPDATE_PAD),
                "npc-score-update op=%d num=%d kills=%d mode=%s pad=%s items=%s"
                % (op, num, kills, SCORE_UPDATE_MODE,
                   "on" if SCORE_UPDATE_PAD else "off",
                   ",".join("%d:%d>%d" % it for it in items)))
            # ★ 2026-10-05 实测定案：记分板「击杀」列按 KILL_OP(2) 路由——
            #   只发 op=9（连杀判定）时积分会显示但击杀数恒 0（用户实测）。
            #   故每刀追加一条 stat_msg_id=KILL_OP(2)、只带 KILL_NUM 项的计分消息。
            flow.send(
                score_flow.SCORE_COMMAND,
                score_flow.encode_score_update_event(
                    now, wire.ACTOR_ID, score_flow.E_MSG_STAT_API_DEF_KILL_OP,
                    [(score_flow.E_SCORE_FIELD_KILL_NUM, max(0, kills - 1), kills)],
                    pad=SCORE_UPDATE_PAD),
                "npc-score-kill KILL_OP=2 kills=%d" % kills)

        # --- ② CONK 连杀事件 ---
        if STREAK_ENABLED:
            flow.send(
                conk_flow.CONK_COMMAND,
                conk_flow.encode_continuous_kill_event(wire.ACTOR_ID, num),
                "npc-streak num=%d" % num)

        # --- ③ sel=2 播报文案 ---
        if SCORE_MSG_ENABLED:
            ids = []
            if SCORE_MSG_MODE == score_flow.SCORE_MSG_MODE_OP:
                ids = [score_flow.E_MSG_STAT_API_DEF_CONTINUE_KILL_OP]
            elif num >= 10:
                ids = list(score_flow.butcherIds(num, BUTCHER_MODE))
            else:
                one = score_flow.streakMessageId(num)
                ids = [one] if one else []
            for msgId in ids:
                flow.send(
                    score_flow.SCORE_COMMAND,
                    score_flow.encode_score_stat_msg(
                        now, wire.ACTOR_ID, msgId, num),
                    "npc-streak-msg id=%d voice=%s num=%d mode=%s/%s"
                    % (msgId, score_flow.voiceEventName(msgId) or "-",
                       num, SCORE_MSG_MODE, BUTCHER_MODE))

        # --- ④ cmd=10 sel=301 独立音效广播（与 HUD 连杀语音入口是否相同尚未证实） ---
        if AUDIO_ENABLED:
            soundId = audio_flow.streakSoundId(num)
            if soundId:
                flow.send(
                    audio_flow.TRANS_COMMAND,
                    audio_flow.encode_trans_multi_rsp(
                        server_time_ms=now, sound_id=soundId,
                        owner_rid=wire.ACTOR_ID),
                    "npc-audio sound=%d path=%s num=%d"
                    % (soundId, audio_flow.soundPath(soundId) or "-", num))

        # --- ⑤ cmd=11 sel=1 UI 打印字符串（★ HUD 上那行台词 + 图标的入口） ---
        #   ⚠️⚠️ 2026-10-04 第七轮更正：这个字段是「**字符串资源id**」
        #   （= `s_ui_str_cli.bin` 主键 = psheet 的 UI_STR_ID），**不是** 7xxxxx 全局消息 id。
        #     710110 → UI_STR_ID **90000** → 台词「谁敢杀我！」+ 图标 `tufu`；
        #     710030 → 14、710040 → 45、710050 → 46 … 710090 → 50、71100 → 15。
        #   第六轮填 710110（msg id）是错的，见 `codec/ui_flow.py` 顶部更正。
        #   `[npc] ui_print_mode` = `str`（默认，正解）/ `msg`（旧写法，A/B 用）；
        #   `[npc] ui_print_id` > 0 时强制用该 id（实机定位用）。
        if UI_PRINT_ENABLED:
            if num >= 10:
                broadcastIds = list(score_flow.butcherIds(num, BUTCHER_MODE))
            else:
                one = score_flow.streakMessageId(num)
                broadcastIds = [one] if one else []
            # ⚠️ 第八轮：``spread`` 模式下 71100/71110 都映射到 UI_STR_ID **15**，
            #   不去重会把同一行 HUD 台词连发两次（画面叠字）；另外 UI_STR_ID **0**
            #   在 ``s_ui_str_cli.bin`` 里**没有对应行**（710020 二连杀就是 0）
            #   ⇒ 发出去也渲染不出东西，直接跳过。
            seenStrings = set()
            for msgId in broadcastIds:
                if UI_PRINT_ID > 0:
                    stringId = UI_PRINT_ID
                elif UI_PRINT_MODE == UI_PRINT_MODE_MSG:
                    stringId = msgId
                else:
                    stringId = score_flow.uiStringId(msgId)
                if stringId <= 0 or stringId in seenStrings:
                    continue
                seenStrings.add(stringId)
                flow.send(
                    ui_flow.UI_COMMAND,
                    ui_flow.encode_ui_print_string(
                        svr_time_ms=now, msg_id=stringId,
                        belong_mid=wire.ACTOR_ID),
                    "npc-ui-print id=%d text=%s icon=%s voice=%s num=%d mode=%s"
                    % (stringId,
                       score_flow.uiStringText(stringId) or "-",
                       score_flow.uiStringIcon(stringId) or "-",
                       score_flow.voiceEventName(msgId) or "-",
                       num, UI_PRINT_MODE))

        # --- ⑥★ 连杀语音强制播报（★ 2026-10-06 第十一轮**改通道**：sel=2） -----
        #   ★★ 决定性发现（第十一轮，客户端注册表实证，见 _streakBcastKnob）：
        #     连杀语音**只走 sel=2**（E_CS_PROTO_SCORE_STAT_MSG）。
        #     客户端按「协议事件名 → handler」注册（注册器 0xA89100，写 [this+0x1C]）：
        #       E_CS_PROTO_SCORE_STAT_MSG    (sel=2) → 0xA89EA0 → 0xA87D80 → 0xA87920
        #                                              → 名字缓存 → PostEvent
        #       E_CS_PROTO_SCORE_UPDATE_EVENT(sel=1) → 0xB0D0D0 → 0xA57210 → 0xA5D510
        #                                              → 0xB0DD80（**记分板，不碰 0xA87D80**）
        #     ⇒ 第十轮把连杀 id 塞进 sel=1 是**走错通道**：只会更新记分板/连杀数，
        #       永远不会触发语音（这正是「有 HUD 字但没声」的来源）。
        #   ⇒ 本开关改用 sel=2（encode_score_stat_msg），与 ③ score_msg 同通道但
        #     **独立于 score_msg 开关**，用于隔离测试（其余通道全关时只发这一条）。
        if STREAK_BCAST_ENABLED:
            bcastId = score_flow.streakMessageId(num)
            if bcastId:
                flow.send(
                    score_flow.SCORE_COMMAND,
                    score_flow.encode_score_stat_msg(
                        now, wire.ACTOR_ID, bcastId, max(0, num)),
                    "npc-streak-bcast id=%d num=%d kills=%d sel=2" % (bcastId, num, kills))

        # --- ⑦ 诊断扫射（默认 off；见 _voiceSweep / [npc] voice_sweep） ---
        if VOICE_SWEEP_ENABLED:
            _voiceSweep(flow, num, now)

        return num
    except Exception as error:  # noqa: BLE001 —— 附加能力，绝不外抛
        try:
            flow.result["logs"].append("npc-streak-failed " + repr(error))
        except Exception:  # noqa: BLE001
            pass
        return 0


def onAttackHit(flow, intent):
    """攻击「过程」拍调用一次。命中则扣血 + 回包。

    **绝不抛异常** —— 附加能力坏了不许拖垮 battle 链。
    返回被命中的 actor（没命中返回 None），仅供日志/自检。
    """
    if not ENABLED:
        return None
    try:
        if flow.session.get("role") != "instance" or flow.session.get("leaving"):
            return None
        if not flow.session.get("battleEntered"):
            return None
        scene = _scene(flow)
        if not scene:
            return None
        if not aliveTargets(flow, scene):
            return None

        # --- 玩家坐标 / 朝向 ---
        try:
            from . import controls
            ground = controls.groundState(flow)
            position = ground.get("position") or wire.POSITION
            heading = ground.get("heading", 0) or 0
        except Exception:  # noqa: BLE001
            position, heading = wire.POSITION, 0
        if not position:
            return None

        actor, dist = pickTarget(flow, scene, position, heading)
        if actor is None:
            return None

        mid = actor["mid"]
        key = str(mid)          # ⚠️ session 里的键一律 str（见模块 docstring）
        table = hpMap(flow, scene)
        oldHp = table.get(key, float(actor["hp"]))
        newHp = oldHp - DAMAGE_PER_HIT
        table[key] = newHp
        dead = newHp <= 0.0

        weaponTid = 0
        try:
            weaponTid = int(wire.currentWeaponTid())
        except Exception:  # noqa: BLE001
            pass
        now = wire.serverNowMs()
        seq = int(flow.session.get("action", {}).get("seq", 1))

        # --- ① 权威伤害（客户端据此跳伤害数字 / 掉血条） ---
        flow.send(
            battle_flow.BATTLE_COMMAND,
            battle_flow.encode_battle_result(
                attacker_rid=wire.ACTOR_ID,
                target_rid=mid,
                attacker_seq_no=seq,
                target_life_state=(battle_flow.LIFE_STATE_KILL if dead
                                   else battle_flow.LIFE_STATE_NONE),
                attack_weapon_id=weaponTid,
                target_result_type=battle_flow.RESULT_DATA_NORMAL_DAMAGE,
                target_result_data=int(DAMAGE_PER_HIT),
            ),
            "npc-battle-result target=%d dmg=%d hp=%.0f->%.0f"
            % (mid, int(DAMAGE_PER_HIT), oldHp, newHp))

        # --- ② 命中特效（火花） ---
        lp = _livePos(flow, mid, actor["pos"])
        hitPos = (lp[0], lp[1], lp[2] + 1.0)
        flow.send(
            battle_flow.BATTLE_COMMAND,
            battle_flow.encode_battle_hit_point_notify(
                hit_pos=hitPos, attacker_mid=wire.ACTOR_ID, target_mid=mid,
                attacker_weapon_id=weaponTid, target_weapon_id=0,
                server_time_ms=now),
            "npc-battle-hit-fx target=%d pos=%.2f,%.2f,%.2f" % (mid, *hitPos))

        # --- ③ 血尽：死亡通知 ---
        if dead:
            flow.session.setdefault("npcDead", {})[key] = True
            flow.send(
                battle_flow.BATTLE_COMMAND,
                battle_flow.encode_battle_dead_notify(entity_rid=mid,
                                                      server_time_ms=now),
                "npc-battle-dead target=%d" % mid)
            # ⭐ 2026-10-04：击杀 NPC ⇒ ① 杀人事件（cmd=0xA，客户端据此计数/播报）
            #   ② CONK 连杀事件（驱动「X连杀 / 战场屠夫」提示 + 连杀语音）。
            flow.send(
                conk_flow.INSTANCE_COMMAND,
                conk_flow.encode_instance_kill_event(
                    now, wire.ACTOR_ID, mid, weaponTid, 0),
                "npc-kill-event source=%d target=%d" % (wire.ACTOR_ID, mid))
            _bumpStreak(flow)
            left = len(aliveTargets(flow, scene))
            flow.result["logs"].append(
                "npc-battle-all-clear scene=%s left=%d" % (scene, left))
            # ⭐ 2026-10-03（第十七轮）：陪练**全灭** ⇒ 通知训练关状态机
            #    「通关」：发 STATE_NOTIFY(NOTIFY_RESULT) + SHOW_TRAIN_INFO。
            #    非训练场 / 开关关 / 异常 ⇒ 静默（一个包都不发）。
            if left == 0:
                dungeon.onAllTargetsDead(flow)
        return actor
    except Exception as error:  # noqa: BLE001 —— 附加能力，绝不外抛
        try:
            flow.result["logs"].append("npc-battle-failed " + repr(error))
        except Exception:  # noqa: BLE001
            pass
        return None


def describe(flow, scene):
    """一行摘要，给日志用。不抛异常。"""
    if not ENABLED:
        return "disabled"
    try:
        table = flow.session.get("npcHp") or {}
        dead = flow.session.get("npcDead") or {}
        total = len(npc.actors(scene))
        return ("targets=%d alive=%d dmg=%.0f range=%.1fm arc=%.0fdeg"
                % (total, total - len(dead), DAMAGE_PER_HIT,
                   MELEE_RANGE_M, FRONT_ARC_DEG))
    except Exception as error:  # noqa: BLE001
        return "describe-failed " + repr(error)


def report():
    """启动自证行用；不抛异常。"""
    try:
        return ("battle=%s streak=%s score_msg=%s/%s score_update=%s/%s/pad=%s "
                "audio=%s ui_print=%s/%s%s butcher=%s voice_sweep=%s "
                "dmg=%.0f range=%.1fm arc=%.0fdeg"
                % ("on" if ENABLED else "off",
                   "on" if STREAK_ENABLED else "off",
                   "on" if SCORE_MSG_ENABLED else "off", SCORE_MSG_MODE,
                   "on" if SCORE_UPDATE_ENABLED else "off", SCORE_UPDATE_MODE,
                   "on" if SCORE_UPDATE_PAD else "off",
                   "on" if AUDIO_ENABLED else "off",
                   "on" if UI_PRINT_ENABLED else "off", UI_PRINT_MODE,
                   ("/id=%d" % UI_PRINT_ID) if UI_PRINT_ID > 0 else "",
                   BUTCHER_MODE,
                   "on" if VOICE_SWEEP_ENABLED else "off",
                   DAMAGE_PER_HIT, MELEE_RANGE_M, FRONT_ARC_DEG))
    except Exception as error:  # noqa: BLE001
        return "report-failed " + repr(error)


# 启动自证行（与 ``[npc]`` / ``[cc]`` / ``[guide]`` 同一风格）。
print("[npc-battle] " + report(), flush=True)
