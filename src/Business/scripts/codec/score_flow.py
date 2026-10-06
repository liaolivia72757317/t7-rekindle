# -*- coding: utf-8 -*-
"""计分/统计（``SH_CS_CMD_SCORE`` = 12 / 0x0C）S2C 编码器 —— 连杀/屠夫播报的**真正载体**。

为什么又开一条（2026-10-04 第二轮）
-----------------------------------
上一轮补的 ``SH_CS_CMD_CONK``(19) 连杀事件**字节完全正确**（已按 exe 内嵌 TDR 逐字段核对，
wire 抓包 20 条全发），但客户端**毫无反应**（用户实机「还是没有 战场屠夫」）。
本轮直读客户端资源找到决定性证据 —— ``../data/table/s_res_score_cli.bin``
（**局内计分项表**，MSES rs=421 rc=99）**以全局消息 id 为键**，行里就是播报的
图标 + 文案 + 名称::

    id=710020  二连杀          id=710050  五连杀
    id=710060  六连杀          id=710090  九连杀
    id=710110  大杀特杀        id=710120  屠夫
                (icon tu_fu.dds, text 连续击杀10名敌人！“神挡杀神，佛挡杀佛！”)
    id=720010  多重杀  连续击杀3名敌人！   id=720020  二重杀戮  连续击杀2名敌人！
    id=730000  第一滴血          id=730010  爆头        id=730140  屠夫终结者
    id=700000  击杀敌军          id=730090  占领据点    id=730060  进入温泉 …

⇒ 这些 ``7xxxxx`` 就是运行时**播报消息 id**，而唯一带「消息 id」字段的协议是::

    cmd=12 SH_CS_CMD_SCORE
      sel=1 E_CS_PROTO_SCORE_UPDATE_EVENT  变更原因 + 局内计分项（记分板）
      sel=2 E_CS_PROTO_SCORE_STAT_MSG      统计消息 + 自定义数据（★ 播报）

``CS_PROTO_SCORE_STAT_MSG``（TDR，24B）::

    svr_time    : uint64  @0    svr时间
    rid         : uint64  @8    角色rid
    stat_msg_id : int32   @16   统计消息   ← s_res_score_cli 的 id（710050=五连杀）
    custom_data : int32   @20   自定义数据

为什么 ``stat_msg_id`` 一定是**播报 id**（而不是 ``E_MSG_STAT_API_DEF_*`` 判定 op）：
表里有一批行（730060 进入温泉 / 730070 成功埋弹 / 730090 占领据点 …）**没有任何对应的
统计 op**，只可能是被直接当消息 id 播报的。

铁证：本服务端**从未发过 cmd=12**（wire 抓包 S2C 命令字直方图：1/2/4/8/9/10/14/19/29/
30/32/34/35/40/41/44/54/55/60/66，**无 12**）⇒ 连杀播报链路整条缺失。

线格式（26B，大端）::

    sel(u16=2) | svr_time(u64) | rid(u64) | stat_msg_id(i32) | custom_data(i32)

与 battle / conk 同一套「cmd + selector 打头」的 framing（selector 大端，已由
``battle_result``(0x0009) 等**已实机生效**的包反证）。

语音层：``../data/propsheet/战斗结果提示.psheet``（★★★ 2026-10-04 第三轮）
-----------------------------------------------------------------------------
用户实测「出字了，没有声音」——文字链路（cmd=12 sel=2 → ``s_res_score_cli``）已通，
剩下语音。本轮在 data1.vfs 全库字符串扫描中命中 ``run_idx=3851`` = 该 psheet，
它才是「**消息 id → Wwise 事件名**」的映射表::

    <Header> 语音(str) | UI_STR_ID(i32) | 注释(str)
    <Record Name="710020"><语音 Value="Play_erliansha"/>       <注释 Value="二连杀"/>
    <Record Name="710090"><语音 Value="Play_jiuliansha"/>      <注释 Value="九连杀"/>
    <Record Name="71100"> <语音 Value="Play_dashatesha"/>      <注释 Value="十连杀"/>
    <Record Name="71110"> <语音 Value="Play_dashatesha"/>      <注释 Value="超神"/>
    <Record Name="710110"><语音 Value="Play_zhanchangtufu"/>   <注释 Value="绝命屠夫"/>
    <Record Name="730140"><语音 Value="Play_tufuzhongjiezhe"/> <注释 Value="屠夫终结者"/>

**表里没有 710120**（计分表里的「屠夫」行）⇒ 710120 本身没有对应的语音映射；
这只解释「710120 不会按本表选择 Wwise event」，**不能单独解释**正确映射 710110 仍不出声。

事件名 → Wwise Event id 可从 `Data/Audio/Wwise/Windows/Vox_System.txt` 配套清单复核
（本机没有 `Wwise_IDs.h`）；FNV-1 lowercase 也可交叉验证::

    id = FNV-1( lower(事件名) )        # Wwise 标准；初值 2166136261 / 质数 16777619
    Play_erliansha  -> play_erliansha  -> 832718087  = PLAY_ERLIANSHA     ✓
    Play_wuliansha  -> 244828560 = PLAY_WULIANSHA                          ✓
    Play_zhanchangtufu -> 52711210 = PLAY_ZHANCHANGTUFU（★ 战场屠夫）      ✓
    Play_dashatesha -> 2303476104 = PLAY_DASHATESHA                        ✓
    Play_tufuzhongjiezhe -> 1687865627 = PLAY_TUFUZHONGJIEZHE              ✓

这些事件与媒体都在 ``Data/Audio/Wwise/Windows/Vox_System.bnk`` / ``Media_Vox_System.bnk`` 中；
这证明资源可用，但**不证明**当前客户端处理路径会按此表调用 PostEvent。

⇒ **映射结论**：`710110` 在 `战斗结果提示.psheet` 指向 `Play_zhanchangtufu`，所以它是正确的
「屠夫播报候选 id」；但映射存在**不等于客户端实际收到后一定 PostEvent**。

第四轮（2026-10-04）—— 原 sel=1 假设（2026-10-05 实机后撤回）
⚠️ 当时把「缺 sel=1 连杀计分项」当成无声根因，并推断它可触发语音；这**没有证据支撑**。
sel=1 的 TDR 语义确为「变更原因」+ 计分项，代码仍保留它用于记分/连杀数据，但不能把它当作已证实的语音入口。

第六/七轮又因静态字符串与 UI 资源，转而推断 `cmd=11 sel=1` 是语音入口；第八轮据 exe 字符串关联
把 `cmd=12 sel=2` 提升为候选主链。**两种推断都未通过运行时听感确认。**

2026-10-05 最新游戏会话 `43352-1285792500` 给出新的实证：
* `cmd=12 sel=2` 共发 19 条，`stat_msg_id=710020..710090,710110`，`custom_data=连杀数`；**用户仍无连杀语音**；
* `cmd=12 sel=1` 共发 20 条，当前已按 TDR 定长写 830B（含外层 selector），同样不能证明其触发声音；
* 该次会话 `[npc] voice_sweep=off`，`ui_print=off`、`audio=off`，所以**它并没有执行新候选扫描**。

故目前**尚未定位语音实际触发通道**。下一步应隔离发送 `voice_sweep` 矩阵；逐次杀 NPC，wire 中 `reason` 应出现 `npc-voice-sweep`，并按当次声音反馈判断。
"""
import struct

SCORE_COMMAND = 0x000C            # SH_CS_CMD_SCORE = 12
SCORE_UPDATE_EVENT = 0x0001       # E_CS_PROTO_SCORE_UPDATE_EVENT = 1（记分板，本模块暂不发）
SCORE_STAT_MSG = 0x0002           # E_CS_PROTO_SCORE_STAT_MSG = 2（★ 播报）
SCORE_STAT_MSG_BODY_SIZE = 26     # 2 + 8 + 8 + 4 + 4

# --- 全局消息 id（= s_res_score_cli.bin 的 id 列，取自 exe 内嵌 sh_proto_cs 宏表）----
GLOBAL_MSG_KILL_ENEMY = 700000
GLOBAL_MSG_CONTINUE_KILL = 710010          # 一连杀（计分表里**没有**这一行 ⇒ 不播报）
GLOBAL_MSG_CONTINUE_KILL_2 = 710020
GLOBAL_MSG_CONTINUE_KILL_3 = 710030
GLOBAL_MSG_CONTINUE_KILL_4 = 710040
GLOBAL_MSG_CONTINUE_KILL_5 = 710050
GLOBAL_MSG_CONTINUE_KILL_6 = 710060
GLOBAL_MSG_CONTINUE_KILL_7 = 710070
GLOBAL_MSG_CONTINUE_KILL_8 = 710080
GLOBAL_MSG_CONTINUE_KILL_9 = 710090
GLOBAL_MSG_CONTINUE_KILL_10 = 710100      # 计分表里**没有**这一行（被 710110 取代）
GLOBAL_MSG_BATTLEFIELD_BUTCHER = 710110   # 计分表名：大杀特杀 / 语音表名：绝命屠夫 ★有语音
GLOBAL_MSG_BUTCHER = 710120               # 计分表名：屠夫（文案「连续击杀10名敌人」）⚠️语音表无此行
# ⚠️ 第八轮新增：psheet 里那两个 **5 位** id（不是 710100/710110 的笔误就是另一套编号）。
#    战斗结果提示.psheet 的 Record Name 是**字符串**，所以 "71100" ≠ "710100"。
#    如果客户端是拿「连杀数算出来的 id」去查表，算出来的 710100 查不到，
#    而直接发 71100 反而能查到「十连杀 → Play_dashatesha」⇒ 值得实测。
GLOBAL_MSG_STREAK_TEN_5D = 71100          # psheet 里的「十连杀」（5 位）→ Play_dashatesha
GLOBAL_MSG_STREAK_OVER_5D = 71110         # psheet 里的「超神」（5 位）→ Play_dashatesha
GLOBAL_MSG_FIRST_BLOOD = 730000
GLOBAL_MSG_HEAD_SHOT = 730010
GLOBAL_MSG_BUTCHER_KILLER = 730140

# --- 语音层：../data/propsheet/战斗结果提示.psheet（msg id → Wwise 事件名）------
#     id = FNV-1(lower(事件名))，已与 Wwise_IDs.h 逐条核对（见模块 docstring）。
#     表里**只有**这些 id 有语音；710120 不在其中。
VOICE_EVENT_BY_MSG = {
    710020: "Play_erliansha",
    710030: "Play_sanliansha",
    710040: "Play_siliansha",
    710050: "Play_wuliansha",
    710060: "Play_liuliansha",
    710070: "Play_qiliansha",
    710080: "Play_baliansha",
    710090: "Play_jiuliansha",
    71100: "Play_dashatesha",        # 「十连杀」（表中确为 5 位；疑 710100 笔误）
    71110: "Play_dashatesha",        # 「超神」（表中确为 5 位）
    710110: "Play_zhanchangtufu",    # 「绝命屠夫」= 战场屠夫 ★
    730140: "Play_tufuzhongjiezhe",  # 屠夫终结者
}

# --- ★★★ 2026-10-04 第七轮：psheet 的 UI_STR_ID → 真正的字符串表 --------------
# ``战斗结果提示.psheet`` 的 ``UI_STR_ID`` 列不是装饰，它指向
# ``../data/table/s_ui_str_cli.bin``（**MSES rs=536 rc=359**，2026-10-04 第七轮定位，
# 真身 = data1.vfs run **22825**；旧 `vfs_extract_by_name.py s_ui_str_cli` 抽到的是
# 漂移错文件「GFX 13963B」，**已废弃**）。
#
# 行结构（行内偏移）::
#
#     @0    i32         id           ← 就是 psheet 的 UI_STR_ID
#     @4    char[256]   台词          ← 客户端 HUD 上显示的**那行字**
#     @260  char[128]   图标名        ← 对应 ../data/ui/resource/icon/tips/<名>.png|.dds
#     @408  char[...]   语音事件名    ← 多数为空；教学/武将台词填 Play_newbie_* 等
#
# ★ 交叉验证（三方吻合，不是猜的）：
#   * TDR ``CS_PROTO_UI_PRINT_STRING.id`` 注释 = 「**字符串资源id**」；
#   * psheet 的列名就叫 **UI_STR_ID**；
#   * ``s_ui_str_cli.bin`` 的 id 恰好就是 14 / 15 / 45..50 / 90000 / 90001，
#     且 15 → 图标 ``dashatesha``（= 语音 ``Play_dashatesha``）、
#     90000 → 图标 ``tufu``（= 语音 ``Play_zhanchangtufu``）—— 图标与语音**一一对应**。
#
# ⇒ **``cmd=11 sel=1`` 的 ``id`` 要填 UI_STR_ID（如 90000），不是全局消息 id（710110）。**
#   第六轮填 710110 是错的（见 ``codec/ui_flow.py`` 顶部更正）。
UI_STR_ID_BY_MSG = {
    710020: 0,        # 二连杀：表里 UI_STR_ID=0 ⇒ 没有专属台词
    710030: 14,
    710040: 45,
    710050: 46,
    710060: 47,
    710070: 48,
    710080: 49,
    710090: 50,
    71100: 15,        # 十连杀（psheet 里确为 5 位）
    71110: 15,        # 超神
    710110: 90000,    # 绝命屠夫 → 台词「谁敢杀我！」+ 图标 tufu
    730140: 90001,    # 屠夫终结者
}

# ``s_ui_str_cli.bin`` 的连杀/屠夫行（仅用于日志自证，客户端自己读表）。
UI_STRING_TEXT = {
    14: "斩杀敌将！",
    15: "我乃天下无双，无人能挡！",
    45: "斩杀敌将！",
    46: "挡我者死！",
    47: "挡我者死！",
    48: "谁能阻我！",
    49: "谁能阻我！",
    50: "无人可以终结我的连斩！",
    90000: "谁敢杀我！",
    90001: "屠夫终结者",
}
UI_STRING_ICON = {
    14: "sanliansha", 15: "dashatesha",
    45: "siliansha", 46: "wuliansha", 47: "liuliansha",
    48: "qiliansha", 49: "baliansha", 50: "jiuliansha",
    90000: "tufu", 90001: "tufuzhongjie",
}

# --- ⚠️ 2026-10-04 第七轮：「战场屠夫」四个字在客户端**根本不存在** ---------------
# 全库证据（可复跑）：
#   * ``data1.vfs`` **全部 26971 个 run 逐个解压**后搜「战场屠夫」（GBK/UTF-8/UTF-16LE）
#     ⇒ **0 命中**（工具 ``hkx_decode/_scan_vfs_multi.py``）；而「大杀特杀」2 处、
#     「屠夫」9 处、「超神」1 处都在 ⇒ 搜索方法有效。
#   * ``TieJiClient.exe`` / ``TieJiClientBase.dll`` / ``Data/**`` 三编码 ⇒ 0 命中。
# ⇒ 「战场屠夫」只存在于 exe 内嵌宏**名** ``GLOBAL_MSG_BATTLEFIELD_BUTCHER`` 里，
#   以及 psheet 的**注释**列（写作「绝命屠夫」）。客户端 HUD 上真正显示的是
#   ``s_ui_str_cli.bin[90000]`` = **「谁敢杀我！」**（图标 ``tufu``）。
#   psheet 的「注释」列不参与显示。

# 连杀数 → 播报 id。**依据 = s_res_score_cli.bin 实际存在的行**：
#   2..9 → 710020..710090（二连杀..九连杀，一一对应）
#   10   → 710110（大杀特杀；表里没有 710100「十连杀」这一行）
#   ≥11  → 710110（★ 有语音 Play_zhanchangtufu）；710120「屠夫」**没有语音**
#   1    → 0（不播报：表里没有 710010 这一行）
STREAK_MSG_BY_NUM = {n: 710000 + n * 10 for n in range(2, 10)}
STREAK_MSG_TEN = GLOBAL_MSG_BATTLEFIELD_BUTCHER
STREAK_MSG_OVER = GLOBAL_MSG_BATTLEFIELD_BUTCHER

# --- sel=1 记分板/连杀数（2026-10-04 第四轮；⚠️ **不是**语音触发点，见上）-------
# TDR ``struct CS_PROTO_SCORE_UPDATE_EVENT``（828B）::
#
#     svr_time    : uint64           @0    svr时间
#     rid         : uint64           @8    角色rid
#     stat_msg_id : int32            @16   ★「变更原因」← 不是 7xxxxx 消息 id！
#     scores      : CS_PROTO_SCORE   @20   局内计分项
#
# ``CS_PROTO_SCORE``（808B 定长存储）::
#
#     count       : int32            @0
#     score_items : CS_PROTO_SCORE_ITEM[67]  @4   （每条 12B）
#
# ``CS_PROTO_SCORE_ITEM``::
#
#     type      : int32   @0   计分类型（E_SCORE_FIELD_*）
#     src_value : int32   @4   源值
#     dst_value : int32   @8   终值
#
# ⚠️ 变长数组**不补齐**到声明长度（与 ``vision_flow.encode_weapon_use_data``
# 同一约定，已实机生效）⇒ wire 上就是 ``count + count×12B``。
#
# ⚠️⚠️ 2026-10-04 第八轮新增：**这个「变长」假设很可疑，本轮改为默认补齐**。
#   理由：TDR 里 ``CS_PROTO_SCORE`` 是 ``count i32 + score_items[67]`` —— **数组维度是
#   编译期常量 67**，TDR 生成的 marshal 代码通常按**定长**写满 67 槽（多出的槽填零），
#   只有 ``count`` 表示有效条目数。我们此前发 42B（count=3 + 3×12B）⇒ 客户端按定长
#   解 808B 时会**越过包尾**（读到后面别的字节）或直接判长度不符**整条丢弃** ——
#   这与「Tab 记分板一直是空的」现象完全吻合。
#   ⇒ ``encode_score_update_event(..., pad=True)`` 默认补零到 ``4 + 67×12 = 808B``；
#     开关 ``[npc] score_update_pad``（默认 on）可切回变长做 A/B。
SCORE_ITEM_MAX = 67                     # TDR: CS_PROTO_SCORE.score_items[67]
SCORE_MAX_SIZE = 4 + SCORE_ITEM_MAX * 12   # 808B（count + 67×12B）
#
# ★ TDR 字段语义：sel=1 是计分变更，sel=2 是统计消息；运行时声音效果尚未定案：
#   TDR 注释里两条协议的 ``stat_msg_id`` 语义**不同** ——
#     sel=1 ``CS_PROTO_SCORE_UPDATE_EVENT.stat_msg_id`` = 「**变更原因**」；
#     sel=2 ``CS_PROTO_SCORE_STAT_MSG.stat_msg_id``      = 「统计消息」。
#   「变更原因」的取值域是 ``E_MSG_STAT_API_DEF_*``（0..17，9=连杀判定），
#   再配合 ``E_SCORE_FIELD_MANSLAUGHTER``(6) 的 ``dst_value`` = 连杀数。
#   ⇒ 本轮补上 sel=1（修 Tab 记分板 + 给客户端连杀数）。
#
# ⚠️ 2026-10-05：**语音触发入口仍未实机定案**。
#   第六轮曾据「710110 在 psheet 但无声」推断 cmd=12 不查语音表，并把 cmd=11 sel=1
#   当作入口；第八轮又根据静态字符串/RTTI 推断 cmd=12 sel=2 是入口。
#   最新会话 43352-1285792500：cmd=12 sel=2 确实按 710020..710110 发出 19 条仍无声，
#   且那次 voice_sweep=off，所以两种入口结论都未被证明。下一步必须隔离扫射，见
#   ``[npc] voice_sweep`` / ``npc_battle._VOICE_SWEEP``；sel=1 的语义仍是计分变更原因。
SCORE_UPDATE_EVENT = 0x0001       # E_CS_PROTO_SCORE_UPDATE_EVENT = 1（记分板 + 连杀数）
SCORE_UPDATE_HEADER_SIZE = 26     # 2 + 8 + 8 + 4 + 4
SCORE_ITEM_SIZE = 12              # 4 + 4 + 4

# --- E_SCORE_FIELD_*（计分项类型，取自 exe 内嵌宏表）--------------------------
E_SCORE_FIELD_POINT = 1           # 积分
E_SCORE_FIELD_KILL_NUM = 2        # 击杀数
E_SCORE_FIELD_DEAD_NUM = 3        # 死亡数
E_SCORE_FIELD_ASSISTS_NUM = 4     # 助攻数
E_SCORE_FIELD_HEADSHOT_NUM = 5    # 爆头数
E_SCORE_FIELD_MANSLAUGHTER = 6    # ★ 屠杀（连杀数 / 屠夫）
E_SCORE_FIELD_RESCUER_NUM = 7     # 救援数
E_SCORE_FIELD_FIRST_BLOOD = 10    # 第一滴血

# --- E_MSG_STAT_API_DEF_*（sel=1 的「变更原因」取值域，0..17）------------------
E_MSG_STAT_API_DEF_INVALID = 0
E_MSG_STAT_API_DEF_ASSISTS_OP = 1
E_MSG_STAT_API_DEF_KILL_OP = 2
E_MSG_STAT_API_DEF_KILL_TWO_OP = 3
E_MSG_STAT_API_DEF_ONE_HIT_KILL_OP = 4
E_MSG_STAT_API_DEF_BLOOD_FOR_BLOOD_OP = 5
E_MSG_STAT_API_DEF_REVENGE_OP = 6
E_MSG_STAT_API_DEF_TERMINATE_OP = 7
E_MSG_STAT_API_DEF_RESCUE_OP = 8
E_MSG_STAT_API_DEF_CONTINUE_KILL_OP = 9      # ★ 连杀判定
E_MSG_STAT_API_DEF_MULTIPLE_KILL_OP = 10     # 多重杀
E_MSG_STAT_API_DEF_HEAD_SHOT_OP = 11
E_MSG_STAT_API_DEF_FIRST_BLOOD_OP = 12
E_MSG_STAT_API_DEF_FRIEND_DAMAGE_PUNISH = 13
E_MSG_STAT_API_DEF_BUTCHER_KILLER_OP = 14    # 屠夫终结者
E_MSG_STAT_API_DEF_MO_DAMAGE_OP = 15
E_MSG_STAT_API_DEF_KILL_FLAGMAN_OP = 16
E_MSG_STAT_API_DEF_MONSTER_DAMAGE_OP = 17
E_MSG_STAT_API_DEF_MAX = 18

# sel=1 的 stat_msg_id 用哪种取值：``op``（默认，TDR 注释「变更原因」= op 枚举）
# 还是 ``id``（直接填 710020 这类消息 id，做 A/B 对照用）。
SCORE_UPDATE_MODE_OP = "op"
SCORE_UPDATE_MODE_ID = "id"

# 每击杀的积分（记分板 POINT 列；纯展示，不影响任何判定）。
POINT_PER_KILL = 100


def normalizeUpdateMode(raw):
    """``op`` / ``id`` → 规范值；空/非法 → ``op``（默认）。"""
    text = str(raw or "").strip().lower()
    return SCORE_UPDATE_MODE_ID if text in ("id", "msg", "msg_id") else SCORE_UPDATE_MODE_OP


def streakOp(kill_num):
    """连杀数 → sel=1 的「变更原因」op。

    ``≥2`` → ``E_MSG_STAT_API_DEF_CONTINUE_KILL_OP``(9)；
    ``1``  → ``E_MSG_STAT_API_DEF_KILL_OP``(2)。
    """
    try:
        n = int(kill_num)
    except (TypeError, ValueError):
        return E_MSG_STAT_API_DEF_INVALID
    if n >= 2:
        return E_MSG_STAT_API_DEF_CONTINUE_KILL_OP
    if n == 1:
        return E_MSG_STAT_API_DEF_KILL_OP
    return E_MSG_STAT_API_DEF_INVALID


def encode_score_update_event(server_time_ms, rid, stat_msg_id, items, pad=True):
    """编一条「局内计分项变更」（cmd=0x0C sel=1）。

    ``items`` = 可迭代 ``(type, src_value, dst_value)``，类型见 ``E_SCORE_FIELD_*``。

    ``pad=True``（默认，第八轮）: 按 TDR 的**定长数组**写满 ``score_items[67]``
    —— ``count`` 仍是有效条目数，其后补零到 808B（``4 + 67×12``）。
    ``pad=False``（旧行为）: 只写实际条目（``count + count×12B``，变长）。

    线格式（大端）::

        sel(u16=1) | svr_time(u64) | rid(u64) | stat_msg_id(i32)
                   | count(i32) | 67 × (type(i32) src(i32) dst(i32))   # pad=True
                   | count(i32) | count × (type(i32) src(i32) dst(i32)) # pad=False
    """
    rows = [(int(t), int(s), int(d)) for t, s, d in items]
    if len(rows) > SCORE_ITEM_MAX:
        rows = rows[:SCORE_ITEM_MAX]
    try:
        body = struct.pack(
            ">HQQii",
            SCORE_UPDATE_EVENT,
            int(server_time_ms) & 0xFFFFFFFFFFFFFFFF,
            int(rid) & 0xFFFFFFFFFFFFFFFF,
            int(stat_msg_id),
            len(rows))
        for t, s, d in rows:
            body += struct.pack(">iii", t, s, d)
        if pad:
            body += b"\x00" * (SCORE_ITEM_SIZE * (SCORE_ITEM_MAX - len(rows)))
        return body
    except struct.error as error:
        raise ValueError("score update-event field is outside its TDR wire range") from error


def streakScoreItems(kill_num, prev_kills=None, prev_num=None, kills=None):
    """连杀数 → sel=1 的计分项列表。

    默认给出三条：``KILL_NUM`` / ``MANSLAUGHTER``（★ 连杀数）/ ``POINT``；
    首杀额外加 ``FIRST_BLOOD``。
    """
    try:
        n = int(kill_num)
    except (TypeError, ValueError):
        return []
    if n < 1:
        return []
    total = int(kills) if kills is not None else n
    before = int(prev_kills) if prev_kills is not None else max(0, total - 1)
    prev = int(prev_num) if prev_num is not None else max(0, n - 1)
    items = [
        (E_SCORE_FIELD_KILL_NUM, before, total),
        (E_SCORE_FIELD_MANSLAUGHTER, prev, n),
        (E_SCORE_FIELD_POINT, max(0, before) * POINT_PER_KILL, total * POINT_PER_KILL),
    ]
    if n == 1:
        items.append((E_SCORE_FIELD_FIRST_BLOOD, 0, 1))
    return items


# 屠夫播报模式（N≥10 时用哪几个 id）
BUTCHER_MODE_VOICE = "voice"   # 只发 710110（psheet：绝命屠夫 → Play_zhanchangtufu）
BUTCHER_MODE_TEXT = "text"     # 只发 710120（有「屠夫」二字，**语音表无此行**）
BUTCHER_MODE_BOTH = "both"     # 710110 + 710120（有音 + 有「屠夫」二字，会出两条字）
# ★ 第八轮新增：把「十连杀/屠夫」在 psheet 里**所有可能有语音的行**一次发全，
#   用来对冲「客户端到底按哪个 id 查表」这个唯一还没证实的环节：
#     710110 绝命屠夫 → Play_zhanchangtufu ★
#     71100  十连杀   → Play_dashatesha   （5 位，见上面的说明）
#     71110  超神     → Play_dashatesha
#   三条都会出字（710110=大杀特杀 / 71100、71110=…），但**只要能听到任一条语音**，
#   就知道「cmd=12 sel=2 → psheet → PostEvent」这条链是通的，再把多余的两条去掉即可。
BUTCHER_MODE_SPREAD = "spread"
_BUTCHER_IDS = {
    BUTCHER_MODE_VOICE: (GLOBAL_MSG_BATTLEFIELD_BUTCHER,),
    BUTCHER_MODE_TEXT: (GLOBAL_MSG_BUTCHER,),
    BUTCHER_MODE_BOTH: (GLOBAL_MSG_BATTLEFIELD_BUTCHER, GLOBAL_MSG_BUTCHER),
    BUTCHER_MODE_SPREAD: (GLOBAL_MSG_BATTLEFIELD_BUTCHER,
                          GLOBAL_MSG_STREAK_TEN_5D,
                          GLOBAL_MSG_STREAK_OVER_5D),
}


def normalizeButcherMode(raw):
    """``voice`` / ``text`` / ``both`` / ``spread`` → 规范值；空/非法 → ``voice``。"""
    text = str(raw or "").strip().lower()
    return text if text in _BUTCHER_IDS else BUTCHER_MODE_VOICE


def butcherIds(kill_num, butcher=BUTCHER_MODE_VOICE):
    """连杀数 ≥10 时应发的播报 id 元组；<10 返回空元组。"""
    try:
        n = int(kill_num)
    except (TypeError, ValueError):
        return ()
    if n < 10:
        return ()
    return _BUTCHER_IDS[normalizeButcherMode(butcher)]


def voiceEventName(msg_id):
    """播报 id → Wwise 事件名；没有语音返回 ``""``。"""
    return VOICE_EVENT_BY_MSG.get(int(msg_id), "")


def uiStringId(msg_id):
    """全局消息 id → ``s_ui_str_cli.bin`` 的字符串资源 id（= psheet 的 UI_STR_ID）。

    没有对应字符串返回 ``-1``（调用方应跳过 ``cmd=11``）。表见 ``UI_STR_ID_BY_MSG``。
    """
    try:
        return int(UI_STR_ID_BY_MSG.get(int(msg_id), -1))
    except (TypeError, ValueError):
        return -1


def uiStringText(str_id):
    """字符串资源 id → HUD 台词（仅日志自证用，客户端自己读表）。"""
    try:
        return UI_STRING_TEXT.get(int(str_id), "")
    except (TypeError, ValueError):
        return ""


def uiStringIcon(str_id):
    """字符串资源 id → 播报图标名（``tips/<名>.png``）。"""
    try:
        return UI_STRING_ICON.get(int(str_id), "")
    except (TypeError, ValueError):
        return ""


def streakMessageId(kill_num):
    """连杀数 → ``s_res_score_cli`` 播报 id；1 或非法值返回 0（=不播报）。"""
    try:
        n = int(kill_num)
    except (TypeError, ValueError):
        return 0
    if n in STREAK_MSG_BY_NUM:
        return STREAK_MSG_BY_NUM[n]
    if n == 10:
        return STREAK_MSG_TEN
    if n > 10:
        return STREAK_MSG_OVER
    return 0


# --- stat_msg_id 的两种解释（A/B 备用；默认 id 模式）--------------------------
#   id 模式（默认，取证最充分）：stat_msg_id = s_res_score_cli 的行 id（710050 五连杀…）
#   op 模式（备用）：stat_msg_id = E_MSG_STAT_API_DEF_* 判定 op（9=连杀判定），
#                    custom_data = 连杀数 ⇒ 客户端自己算「N连杀」文案。
#   ⚠️ 表里有一批行（730060 进入温泉 / 730090 占领据点…）**没有任何统计 op**，
#      所以 id 模式是正解的概率远高；op 模式只是「万一」时不用再改代码的兜底。
SCORE_MSG_MODE_ID = "id"
SCORE_MSG_MODE_OP = "op"
E_MSG_STAT_API_DEF_CONTINUE_KILL_OP = 9     # 统计api_连杀判定


def normalizeMode(raw):
    """``id`` / ``op`` → 规范值；空/非法 → ``id``（默认）。"""
    text = str(raw or "").strip().lower()
    return SCORE_MSG_MODE_OP if text in ("op", "api", "stat", "op_mode") \
        else SCORE_MSG_MODE_ID


def streakStatMsg(kill_num, mode=SCORE_MSG_MODE_ID, butcher=BUTCHER_MODE_VOICE):
    """连杀数 → ``(stat_msg_id, custom_data)``；不播报返回 ``(0, 0)``。

    ``id`` 模式：``(播报 id, 连杀数)``；``op`` 模式：``(9, 连杀数)``。
    N≥10 时按 ``butcher`` 选 id（``voice`` → 710110 有语音；``text`` → 710120 只有字；
    ``both`` 取第一条，额外那条由 :func:`butcherIds` 给出）。
    """
    try:
        n = int(kill_num)
    except (TypeError, ValueError):
        return 0, 0
    if n < 2:
        return 0, 0
    if normalizeMode(mode) == SCORE_MSG_MODE_OP:
        return E_MSG_STAT_API_DEF_CONTINUE_KILL_OP, n
    if n >= 10:
        ids = butcherIds(n, butcher)
        return (ids[0] if ids else 0), n
    return streakMessageId(n), n


def encode_score_stat_msg(server_time_ms, rid, stat_msg_id, custom_data=0):
    """编一条「统计消息」播报（cmd=0x0C sel=2）。

    ``stat_msg_id`` = ``s_res_score_cli.bin`` 的 id（如 710050 五连杀 / 710110 战场屠夫）；
    ``rid`` = 播报归属玩家 rid（本地玩家 = ``wire.ACTOR_ID`` = 1）；
    ``custom_data`` = 附加数据（连杀数；未知用途，带上无害）。
    """
    try:
        return struct.pack(
            ">HQQii",
            SCORE_STAT_MSG,
            int(server_time_ms) & 0xFFFFFFFFFFFFFFFF,
            int(rid) & 0xFFFFFFFFFFFFFFFF,
            int(stat_msg_id),
            int(custom_data))
    except struct.error as error:
        raise ValueError("score stat-msg field is outside its TDR wire range") from error
