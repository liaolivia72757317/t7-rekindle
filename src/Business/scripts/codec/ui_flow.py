# -*- coding: utf-8 -*-
"""``cmd=11 SH_CS_CMD_UI`` 的 S2C 编码器 —— HUD 播报字符串（**含连杀/屠夫台词 + 图标**）。

⚠️⚠️ 2026-10-04 第七轮更正（**最关键的一处**）：``id`` 要填 **UI_STR_ID**，不是全局消息 id
--------------------------------------------------------------------------------------
第六轮本模块断言「``id`` = 7xxxxx 播报 id」，**已被证伪**。第七轮把整条链挖通了：

``../data/propsheet/战斗结果提示.psheet`` 有两列关键字段::

    <Header> 语音(str) | UI_STR_ID(i32) | 注释(str)
    <Record Name="710110"><语音 Value="Play_zhanchangtufu"/><UI_STR_ID Value="90000"/></Record>

其中 **``UI_STR_ID`` 指向 ``../data/table/s_ui_str_cli.bin``**
（MSES **rs=536 rc=359**，2026-10-04 定位，真身 = data1.vfs **run 22825**）::

    @0    i32        id        ← 就是 psheet 的 UI_STR_ID
    @4    char[256]  台词       ← HUD 上显示的那行字
    @260  char[128]  图标名     ← ../data/ui/resource/icon/tips/<名>.png|.dds
    @408  char[...]  语音事件名  ← 连杀/屠夫行为空（走 psheet）

三方吻合（TDR 字段注释「**字符串资源id**」+ psheet 列名 **UI_STR_ID** + 该表主键）：

| psheet Record | UI_STR_ID | `s_ui_str_cli` 台词 | 图标 | 语音 |
|---|---|---|---|---|
| 710030 三连杀 | 14 | 斩杀敌将！ | sanliansha | Play_sanliansha |
| 710050 五连杀 | 46 | 挡我者死！ | wuliansha | Play_wuliansha |
| 710090 九连杀 | 50 | 无人可以终结我的连斩！ | jiuliansha | Play_jiuliansha |
| 71100 十连杀 | 15 | 我乃天下无双，无人能挡！ | dashatesha | Play_dashatesha |
| **710110 绝命屠夫** | **90000** | **谁敢杀我！** | **tufu** | **Play_zhanchangtufu** |
| 730140 屠夫终结者 | 90001 | 屠夫终结者 | tufuzhongjie | Play_tufuzhongjie |

★ 图标与语音**一一对应**（15↔dashatesha、90000↔tufu）⇒ 这条链是真的，不是巧合。

⚠️ 另外：**「战场屠夫」四个字在客户端根本不存在**（全库 26971 个 run 解压 + exe/dll/dat
三编码搜索，0 命中；见 ``score_flow.py`` 第七轮一节）。HUD 上真正的字是「谁敢杀我！」。

前六轮为什么都没找对
--------------------

* ``cmd=12 sel=2``（``CS_PROTO_SCORE_STAT_MSG``）—— 出**计分表**文案
  （``s_res_score_cli.bin``，710110 → 「大杀特杀」）+ 由 ``CGeGameTipService``
  查 ``战斗结果提示.psheet`` **播语音**。这条链本身是对的，**继续保留**。
* ``cmd=10 sel=301``（音效广播）—— 音效号只能来自 ``音效id表.psheet``，
  该表里**没有屠夫** ⇒ 播不出屠夫语音（喊话/音乐/命中才是它的用途）。
* ``cmd=11 sel=1``（本模块）—— 是**显示**那条：id = UI_STR_ID。

``CGeGameTipService`` 的归属证据（exe RTTI）：``战斗结果提示.psheet`` 名的唯一 LE32
指针引用正夹在 ``.?AV?CGEntityEventListener@VGeGameTargetService@@`` 与
``.?AV?CGeGameTipService@@`` 之间，同段字符串还有 ``E_CS_PROTO_SCORE_STAT_MSG`` /
``连杀语音`` / ``ShowKillNum`` / ``ShowContinuousKill``。

TDR 权威（``TieJiClient.exe`` 内嵌 sh_proto_cs）::

    ### struct CS_PROTO_UI_PKG
    msg_id  smallint         @0  2        ← 选择子
    data    CS_PROTO_UI_DATA @2  8216     （union，最大成员 8216B）

    ### struct CS_PROTO_UI_PRINT_STRING  (客户端打印字符串)
    svr_time    uint64  @0   8   svr时间
    id          int32   @8   4   ★字符串资源id（= s_ui_str_cli 的 id = psheet 的 UI_STR_ID）
    belong_mid  uint64  @12  8   ★消息所属（谁获得的成就）
    param_num   int32   @20  4   参数个数
    params      string  @24  32  参数（每个 64B，见下）

``E_CS_PROTO_UI_MSGID``::

    E_CS_PROTO_UI_PRINT_STRING         = 1 (0x1)   ★ 本模块
    E_CS_PROTO_UI_PRINT_COUNTER_STRING = 2 (0x2)
    E_CS_PROTO_UI_SHOW_TRAIN_INFO      = 3 (0x3)   （dungeon.py 已用）
    E_CS_PROTO_UI_SHOW_TRAIN_GUIDE     = 4 (0x4)   （dungeon.py 已用）
    E_CS_PROTO_UI_CAMERA_MOVIE         = 5 (0x5)

``SH_CS_CMD_UI = 11 (0xB)``；**上行日志里从未出现 cmd=11 ⇒ s→c 专用**。

``string`` 字段的步长 = **64 字节**（实证：``CS_ROOM_INFO.room_name string @8``，
下一字段 ``sync_url @72`` ⇒ 8..72 = 64B；TDR 里 ``size`` 列显示的 1 是「每字符 1 字节」）。

线格式（大端，``#pragma pack(1)`` 无对齐填充）::

    sel(u16=1) | svr_time(u64) | id(i32) | belong_mid(u64) | param_num(i32)
               | param_num × 64B(GBK, NUL 补齐)

``param_num = 0`` 时**恰好 26 字节**（2+8+4+8+4）—— 播报台词是定字符串模板，
连杀/屠夫都不带参数，故默认 0。

约定（与 ``score_flow`` / ``audio_flow`` 一致）:
* selector 大端；包体不含帧头；
* **变长数组不补齐**（``param_num`` 只跟实际条数 × 64B，与仓库既有约定同）；
* 纯编码函数，不碰 flow / session，**绝不抛异常**（调用方自行 try）。
"""

from __future__ import annotations

import struct

# --- 命令 / 选择号 -----------------------------------------------------------
UI_COMMAND = 0x000B                 # SH_CS_CMD_UI = 11（ui协议包，sync 通道）

UI_PRINT_STRING = 0x0001            # ★ E_CS_PROTO_UI_PRINT_STRING
UI_PRINT_COUNTER_STRING = 0x0002
UI_SHOW_TRAIN_INFO = 0x0003
UI_SHOW_TRAIN_GUIDE = 0x0004
UI_CAMERA_MOVIE = 0x0005

# --- 字符串字段步长（TDR 实证：CS_ROOM_INFO.room_name @8 → sync_url @72）------
STRING_STRIDE = 64

# 字符串资源 id → 台词/图标（见 ``score_flow.UI_STR_ID_BY_MSG`` / ``UI_STRING_TEXT``）:
#   ``cmd=11 sel=1`` 的 ``id`` = **UI_STR_ID**（= ``s_ui_str_cli.bin`` 主键）：
#     2..9 连杀 → 0 / 14 / 45..50（710030→14、710040→45、710050→46 … 710090→50）
#     十连杀   → 15（71100/71110）
#     ≥10 屠夫 → **90000**（710110）→ 台词「谁敢杀我！」+ 图标 tufu
#     屠夫终结者 → 90001（730140）
#   ⚠️ 全局消息 id（710110 等）是 ``cmd=12 sel=2`` 用的，**不要填到这里**。


def encodeString(text):
    """一个 ``string`` 字段 = **64 字节** GBK + NUL 补齐（超长截断）。"""
    if text is None:
        text = ""
    if isinstance(text, bytes):
        raw = text
    else:
        raw = str(text).encode("gbk", "replace")
    raw = raw[: STRING_STRIDE - 1]
    return raw + b"\x00" * (STRING_STRIDE - len(raw))


def encode_ui_print_string(
    *,
    svr_time_ms,
    msg_id,
    belong_mid,
    param_num=None,
    params=(),
):
    """``E_CS_PROTO_UI_PRINT_STRING``（sel=1，客户端打印字符串）。

    :param svr_time_ms: 服务器时间（u64）
    :param msg_id: **字符串资源 id**（= ``s_ui_str_cli.bin`` 主键 = psheet 的 ``UI_STR_ID``；
                   屠夫 = **90000**，十连杀 = 15）。**不是**全局消息 id 710110。
    :param belong_mid: 消息所属（玩家视野 mid；本地玩家 = 1）
    :param param_num: 参数个数；``None`` ⇒ 取 ``len(params)``
    :param params: 参数字符串序列（每个编码成 64B GBK）
    """
    items = tuple(params or ())
    count = len(items) if param_num is None else int(param_num)
    body = struct.pack(
        ">HQiQi",
        UI_PRINT_STRING,
        int(svr_time_ms) & 0xFFFFFFFFFFFFFFFF,
        int(msg_id),
        int(belong_mid) & 0xFFFFFFFFFFFFFFFF,
        count,
    )
    # 变长：只跟实际条数（不补齐到 TDR 声明的 32）
    for i in range(count):
        body += encodeString(items[i] if i < len(items) else "")
    return body
