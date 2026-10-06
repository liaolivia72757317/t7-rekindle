# -*- coding: utf-8 -*-
"""音效广播（``cmd=10`` 的 ``sel=301``）S2C 编码器 —— **喊话 / 音乐 / 命中**类音效的载体。

⚠️⚠️ 2026-10-04 第六轮更正（**别再把「连杀/屠夫语音」挂到这条上**）
------------------------------------------------------------------
本模块最初（第五轮）被写成「连杀 / 喊话语音的真正载体」——**前半句是错的**。
用户实测「没有播 战场屠夫」后逐表核对，硬结论：

* ``sound_id`` **只能**查 ``../data/propsheet/音效id表.psheet``（Record Name = 5 位音效号），
  而那张表里**根本没有屠夫**（搜 ``tufu`` 零命中；只有 ``/Voice/baotou_01/02`` 爆头、
  ``/Voice/dashatesha_01/02`` 大杀特杀）⇒ **本模块天生播不出「战场屠夫」**。
* 「战场屠夫」的语音映射 ``Play_zhanchangtufu`` 在
  ``../data/propsheet/战斗结果提示.psheet`` 的 **710110** 行；exe 静态字符串/RTTI
  显示 ``CGeGameTipService`` 与该表有关联，但**当前尚未确认实际 PostEvent 入口**。
  ``cmd=11 sel=1`` 与 ``cmd=12 sel=2`` 都只是待实机验证的候选。

⇒ 本模块**适合**播：喊话（31252 攻击 / 31254 欢呼 / 31256 防守…）、背景音乐（60001~60033）、
命中（30005 起）、连杀音效号（70003~70084 / 70002，这些**确实**在 ``音效id表`` 里）；
**不适合**播「战场屠夫」（该表里没有）。

为什么又开一条（2026-10-04 第五轮，用户提供线索 + TDR 逐字段核对）
------------------------------------------------------------------
``CS_PROTO_TRANS_MULTI_RSP`` 是与 ``cmd=12`` 统计消息分离的音效广播协议：客户端可用
``sound_id`` 查 ``../data/propsheet/音效id表.psheet``（如 ``/Voice/erliansha_01``）。
但这条通道是否触发 HUD 连杀播报、是否独立调用 Wwise，目前**没有实机证据**；本服旧会话
曾发 0 条，2026-10-05 隔离矩阵将用普通音效/音乐作控制样本。

TDR 权威（``TieJiClient.exe`` 内嵌 sh_proto_cs）::

    ### struct CS_PROTO_TRANS_MULTI_RSP  (s->c转发响应)
    svr_time       uint64                        @0    8   svr时间
    content_type   int32                         @8    4  传输内容id
    trans_content  CS_PROTO_SYNC_TRANS_CONTENT   @12  12  传输内容
    → 8 + 4 + 12 = 24B body；+ 2B selector = 26B ✓

    ### struct CS_PROTO_TRANS_MULTI_REQ  (c->s转发请求)   ← 对照，非本模块
    trans_type     int32   @0   4  传输类型
    content_type   int32   @4   4  传输内容id
    trans_content  CS_PROTO_SYNC_TRANS_CONTENT @8  12

    ### struct CS_PROTO_TRANS_RSP  (sync->client转发错误响应)  ← 对照
    svr_time   uint64 @0   error_code int32 @8

``CS_PROTO_SYNC_TRANS_CONTENT`` 是 **union**（12B）；``content_type = 1`` 时::

    sound_id   int32   @0   4   音效号（查 ``音效id表.psheet``）
    owner_rid  uint64  @4   8   归属对象（3D 音源实体 rid）

相关宏::

    E_CS_PROTO_SYNC_TRANS_MULTI_REQ = 300 (0x12C)
    E_CS_PROTO_SYNC_TRANS_MULTI_RSP = 301 (0x12D)   ★ 本模块
    E_CS_PROTO_SYNC_TRANS_RSP       = 302 (0x12E)

    E_TRANS_CONTENT_TYPE_DEFINE_INVALID  = 0
    E_TRANS_CONTENT_TYPE_DEFINE_3D_SOUND = 1   ★
    E_TRANS_CONTENT_TYPE_DEFINE_MAX      = 2

    E_TRANS_TYPE_DEFINE_ROOM_ALL = 1（仅 REQ 用）

线格式（26B，大端）::

    sel(u16=301) | svr_time(u64) | content_type(i32=1) | sound_id(i32) | owner_rid(u64)

音效号（取自 ``../data/propsheet/音效id表.psheet``，1124 行，Record Name 即音效号）::

    ⚠️ 下表**只是「这张表里有哪些音效号」的事实**；它不证明官方 HUD 连杀语音走这条。
       语音触发入口尚未由实机隔离实验定案，本表仅用于 sweep 的普通音效/音乐控制。

    连杀:  2→70003 erliansha_01    3→70004 sanliansha_01   4→70005 siliansha_01
           5→70006 wuliansha_01    6→70081 liuliansha_02   7→70082 qiliansha_02
           8→70083 baliansha_02    9→70084 jiuliansha_02   ≥10→70002 dashatesha_01
    喊话:  攻击 31252 attack_man / 欢呼 31254 cheer_man_1 / 防守 31256 defend_man   ← 本模块主用途
    其它:  第一滴血 70116 vo_first_blood / 双杀 70117 vo_double_kill / 多杀 70119 vo_multi_kill
    音乐:  60001~60033（60009 竞技场 / 60022 lysd_fight 洛阳死斗）                ← 本模块主用途
    命中:  30005 起（``/Hit/*``）                                                  ← 本模块主用途
    ⚠️ **没有** tufu / 战场屠夫（该表无此音效号，硬事实）

约定（与 ``score_flow`` / ``battle_flow`` 一致）:
* selector 大端；包体不含帧头；
* 纯编码函数，不碰 flow / session，**绝不抛异常**（调用方自行 try）。
"""

from __future__ import annotations

import struct

# --- 命令 / 选择号 -----------------------------------------------------------
TRANS_COMMAND = 0x000A                       # SH_CS_CMD_INSTANCE（对局通道）
TRANS_MULTI_RSP = 0x012D                     # 301 E_CS_PROTO_SYNC_TRANS_MULTI_RSP
TRANS_MULTI_REQ = 0x012C                     # 300（对照）
TRANS_RSP = 0x012E                           # 302（对照）

# --- 传输内容类型（E_TRANS_CONTENT_TYPE_DEFINE_*）-----------------------------
TRANS_CONTENT_INVALID = 0
TRANS_CONTENT_3D_SOUND = 1                   # ★ 3D 音效
TRANS_CONTENT_MAX = 2

# --- 传输类型（E_TRANS_TYPE_DEFINE_*，仅 REQ 用）------------------------------
TRANS_TYPE_INVALID = 0
TRANS_TYPE_ROOM_ALL = 1
TRANS_TYPE_ROOM_FRIENDS = 2
TRANS_TYPE_SQUAD = 3
TRANS_TYPE_MAX = 4

TRANS_MULTI_RSP_SIZE = 26                    # 2 + 8 + 4 + 4 + 8

# --- 音效号（音效id表.psheet）-------------------------------------------------
SOUND_STREAK = {
    2: 70003,       # /Voice/erliansha_01
    3: 70004,       # /Voice/sanliansha_01
    4: 70005,       # /Voice/siliansha_01
    5: 70006,       # /Voice/wuliansha_01
    6: 70081,       # /Voice/liuliansha_02
    7: 70082,       # /Voice/qiliansha_02
    8: 70083,       # /Voice/baliansha_02
    9: 70084,       # /Voice/jiuliansha_02
}
SOUND_STREAK_TEN = 70002                     # /Voice/dashatesha_01（大杀特杀 / 屠夫档）
SOUND_FIRST_BLOOD = 70116                    # /tieqi_vo/vo_first_blood
SOUND_DOUBLE_KILL = 70117                    # /tieqi_vo/vo_double_kill
SOUND_MULTI_KILL = 70119                     # /tieqi_vo/vo_multi_kill
SOUND_SHOUT_ATTACK = 31252                   # /Vox/1016/attack_man
SOUND_SHOUT_CHEER = 31254                    # /Vox/1016/cheer_man_1
SOUND_SHOUT_DEFEND = 31256                   # /Vox/1016/defend_man

# 音效号 → 路径（仅日志用，不参与编码）
SOUND_PATH = {
    70002: "/Voice/dashatesha_01",
    70003: "/Voice/erliansha_01",
    70004: "/Voice/sanliansha_01",
    70005: "/Voice/siliansha_01",
    70006: "/Voice/wuliansha_01",
    70081: "/Voice/liuliansha_02",
    70082: "/Voice/qiliansha_02",
    70083: "/Voice/baliansha_02",
    70084: "/Voice/jiuliansha_02",
    70116: "/tieqi_vo/vo_first_blood",
    70117: "/tieqi_vo/vo_double_kill",
    70119: "/tieqi_vo/vo_multi_kill",
    31252: "/Vox/1016/attack_man",
    31254: "/Vox/1016/cheer_man_1",
    31256: "/Vox/1016/defend_man",
    60005: "/Music/tongguan1",
    60009: "/Music/jingjichang",           # 竞技场/训练循环
    60022: "/Music/lysd/lysd_fight",
    60033: "/Music/Town_South",
    30005: "/Hit/Arrow_Hit_Body",
}


def streakSoundId(kill_num):
    """连杀数 → 音效号。``<2`` → 0（不播）；``≥10`` → ``SOUND_STREAK_TEN``。"""
    try:
        n = int(kill_num)
    except (TypeError, ValueError):
        return 0
    if n < 2:
        return 0
    if n >= 10:
        return SOUND_STREAK_TEN
    return SOUND_STREAK.get(n, 0)


def soundPath(sound_id):
    """音效号 → 路径（日志用）。未知返回 ``""``。"""
    try:
        return SOUND_PATH.get(int(sound_id), "")
    except (TypeError, ValueError):
        return ""


def encode_trans_multi_rsp(
    *,
    server_time_ms,
    sound_id,
    owner_rid,
    content_type=TRANS_CONTENT_3D_SOUND,
):
    """``E_CS_PROTO_SYNC_TRANS_MULTI_RSP``（sel=301，音效广播，整包 26B）。

    :param server_time_ms: 服务器时间（u64）
    :param sound_id: 音效号（查 ``音效id表.psheet``）
    :param owner_rid: 归属对象 rid（3D 音源实体）
    :param content_type: 传输内容类型，默认 1 = 3D 音效
    """
    return struct.pack(
        ">HQiiQ",
        TRANS_MULTI_RSP,
        int(server_time_ms) & 0xFFFFFFFFFFFFFFFF,
        int(content_type),
        int(sound_id),
        int(owner_rid) & 0xFFFFFFFFFFFFFFFF,
    )
