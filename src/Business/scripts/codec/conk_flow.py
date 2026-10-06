# -*- coding: utf-8 -*-
"""连杀（``SH_CS_CMD_CONK`` = 19 / 0x13）S2C 编码器。

客户端收到「连杀事件」后显示「X连杀 / 战场屠夫」并播放「连杀语音」。
本服务端此前**从未发过这条协议** ⇒ 打多少都不出连杀/屠夫提示（用户实机反馈）。

取证（2026-10-04，``hkx_decode/tdr_dump.py`` 直读 ``TieJiClient.exe`` 内嵌
``sh_proto_cs`` TDR 元数据 —— 最硬的一手证据）
------------------------------------------------
* 命令宏：``SH_CS_CMD_CONK = 19 (0x13)``
* 消息结构（TDR ``struct CS_PROTO_CONTINUOUS_KILL_PKG``）::

      msg_id : smallint                        @0   size 2
      data   : CS_PROTO_CONTINUOUS_KILL_DATA   @2   size 12

* 选择子：``E_CS_PROTO_CONTINUOUS_KILL_EVENT = 1``（即 ``msg_id`` 填 1）
* 数据（12B）：``角色rid``(u64) + ``continuous_kill_num``(i32, 连杀数)
* 客户端全局消息（字符串池）：``GLOBAL_MSG_CONTINUE_KILL_5`` … ``_10``、
  ``GLOBAL_MSG_BATTLEFIELD_BUTCHER``（10 杀=战场屠夫）、``GLOBAL_MSG_BUTCHER``、
  打怪另有 ``GLOBAL_MSG_CONTINUE_KILL_*_MONSTER`` / ``_OVER_10_MONSTER``。

线格式（14B，大端）::

    msg_id(i16=1) | rid(u64) | continuous_kill_num(i32)

与 vision / battle 同一套「cmd + selector 打头」的 framing。
"""
import struct

CONK_COMMAND = 0x0013          # SH_CS_CMD_CONK = 19
CONK_EVENT_KILL = 0x0001       # E_CS_PROTO_CONTINUOUS_KILL_EVENT = 1
CONK_BODY_SIZE = 14            # 2 + 8 + 4

# --- 杀人事件（INSTANCE 命令 cmd=10/0xA 下的 E_CS_PROTO_INSTANCE_KILL_EVENT=6）----
# 客户端很可能**靠这条"杀人事件"计数连杀**（并显示击杀播报），CONK 只是播报连杀数。
# TDR：``struct CS_PROTO_INSTANCE_KILL_EVENT { svr_time:u64; source_rid:u64;
#   target_rid:u64; weapon_tid:i32; hit_part:i32 }``（32B，杀人角色/被杀角色）。
INSTANCE_COMMAND = 0x000A       # SH_CS_CMD_INSTANCE = 10
INSTANCE_KILL_EVENT = 0x0006    # E_CS_PROTO_INSTANCE_KILL_EVENT = 6
INSTANCE_KILL_BODY_SIZE = 34    # 2 + 8 + 8 + 8 + 4 + 4


def encode_instance_kill_event(server_time_ms, source_rid, target_rid,
                               weapon_tid=0, hit_part=0):
    """编一条「杀人事件」（cmd=0xA）：``source_rid`` 杀了 ``target_rid``。

    客户端据此更新击杀播报 / 连杀计数。
    """
    try:
        return struct.pack(
            ">HQQQii",
            INSTANCE_KILL_EVENT,
            int(server_time_ms) & 0xFFFFFFFFFFFFFFFF,
            int(source_rid) & 0xFFFFFFFFFFFFFFFF,
            int(target_rid) & 0xFFFFFFFFFFFFFFFF,
            int(weapon_tid),
            int(hit_part))
    except struct.error as error:
        raise ValueError("instance kill-event field is outside its TDR wire range") from error


def encode_continuous_kill_event(rid, kill_num):
    """编一条「连杀事件」：``rid`` 达成 ``kill_num`` 连杀。

    ``rid`` = 达成连杀的玩家 rid（本地玩家 = ``wire.ACTOR_ID`` = 1）；
    ``kill_num`` = 连杀数（客户端按它选 5..10 连杀 / 战场屠夫 文案与语音）。
    """
    try:
        return struct.pack(
            ">hQi",
            CONK_EVENT_KILL,
            int(rid) & 0xFFFFFFFFFFFFFFFF,
            int(kill_num))
    except struct.error as error:
        raise ValueError("conk field is outside its TDR wire range") from error
