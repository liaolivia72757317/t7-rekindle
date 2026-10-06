"""队伍快捷喊话（``cmd=10`` / ``sel=300`` 上行 → ``sel=301`` 广播）。

协议依据（客户端内嵌 TDR ``sh_proto_cs``）::

    ### struct CS_PROTO_TRANS_MULTI_REQ  (c->s 转发请求)
    trans_type     int32   @0   4   E_TRANS_TYPE_DEFINE_*
    content_type   int32   @4   4   E_TRANS_CONTENT_TYPE_DEFINE_*
    trans_content  CS_PROTO_SYNC_TRANS_CONTENT @8  12  (union)

    ### struct CS_PROTO_TRANS_MULTI_RSP  (s->c 转发响应)
    svr_time       uint64  @0   8
    content_type   int32   @8   4
    trans_content  CS_PROTO_SYNC_TRANS_CONTENT @12 12

    ### struct CS_PROTO_TRANS_RSP  (sync->client 转发错误响应)
    svr_time uint64 @0   error_code int32 @8

    E_TRANS_TYPE_DEFINE_INVALID=0 / ROOM_ALL=1 / ROOM_FRIENDS=2 / SQUAD=3 / MAX=4
    E_TRANS_CONTENT_TYPE_DEFINE_INVALID=0 / 3D_SOUND=1 / MAX=2

``CS_PROTO_SYNC_TRANS_CONTENT`` 是 12 字节 union；``content_type = 1`` 时::

    sound_id  int32   @0   4   音效号
    svr_id    uint64  @4   8   归属对象（3D 音源实体 rid）

本模块收到的 ``body`` 与仓库其余部分一致，**含 2 字节 selector 前缀**，
因此字段落在 ``trans_type@2 / content_type@6 / trans_content@10``，整包 22 字节。

设计约束
------------------------------------------------------------------
* ``trans_content`` 的实际内容（音效号 / 事件名 / 裸字节）尚未确认，因此本模块
  **只在长度与结构对得上时解码**，对不上就返回 ``False`` 交给上层兜底，
  不推测字段、不回包。
* 每次上行整条落追踪日志（长度不截断），用于后续核对真实布局。
* 广播只发给同房间其他 instance 会话（``Flow.send`` 仅发当前连接，
  这里直接向 ``flow.result["send"]`` 追加其他连接的条目）。
"""

from __future__ import annotations

import struct

from . import tracelog
from .codec import audio_flow

TRANS_COMMAND = audio_flow.TRANS_COMMAND          # 0x0A
TRANS_MULTI_REQ = audio_flow.TRANS_MULTI_REQ      # 0x12C = 300 上行
TRANS_MULTI_RSP = audio_flow.TRANS_MULTI_RSP      # 0x12D = 301 下行广播

REQ_SELECTOR_SIZE = 2
REQ_STRUCT_SIZE = 4 + 4 + 12                      # trans_type + content_type + union
REQ_BODY_SIZE = REQ_SELECTOR_SIZE + REQ_STRUCT_SIZE   # 22
REQ_TRANS_TYPE_OFFSET = REQ_SELECTOR_SIZE                  # 2
REQ_CONTENT_TYPE_OFFSET = REQ_SELECTOR_SIZE + 4            # 6
REQ_CONTENT_OFFSET = REQ_SELECTOR_SIZE + 8                 # 10
REQ_SOUND_ID_OFFSET = REQ_CONTENT_OFFSET                   # 10
REQ_SVR_ID_OFFSET = REQ_CONTENT_OFFSET + 4                 # 14

TRANS_CONTENT_3D_SOUND = audio_flow.TRANS_CONTENT_3D_SOUND   # 1

# 接受的转发范围。客户端实际填哪个尚未确认，这里只做白名单，不认得的退给上层。
BROADCAST_TRANS_TYPES = (
    audio_flow.TRANS_TYPE_ROOM_ALL,
    audio_flow.TRANS_TYPE_ROOM_FRIENDS,
    audio_flow.TRANS_TYPE_SQUAD,
)

# 是否回发给发送方。发送方客户端本地已播放，默认不回，避免双播。
ECHO_TO_SENDER = False

# 服务端侧限频（客户端自带「快捷语音一分钟内次数限制」，这层只防刷屏）。
VOICE_LIMIT_WINDOW_MS = 60000
VOICE_LIMIT_COUNT = 30


def decodeRequest(body):
    """解 ``CS_PROTO_TRANS_MULTI_REQ``（含 2B selector）。

    返回 ``(trans_type, content_type, trans_content)``；长度不符返回 ``None``。
    """
    if type(body) is not bytes or len(body) != REQ_BODY_SIZE:
        return None
    transType = struct.unpack_from(">i", body, REQ_TRANS_TYPE_OFFSET)[0]
    contentType = struct.unpack_from(">i", body, REQ_CONTENT_TYPE_OFFSET)[0]
    return transType, contentType, body[REQ_CONTENT_OFFSET:REQ_CONTENT_OFFSET + 12]


def decodeSound(content):
    """``content_type == 3D_SOUND`` 时解 union：``sound_id(u32) + svr_id(u64)``。"""
    if type(content) is not bytes or len(content) < 12:
        return None
    return struct.unpack_from(">IQ", content, 0)


def broadcast(flow, body):
    """把 301 广播给同房间其他 instance 会话，返回实际发送的会话数。"""
    sent = 0
    for connection, session in list(flow.state.get("sessions", {}).items()):
        if session.get("role") != "instance" or session.get("leaving"):
            continue
        if not ECHO_TO_SENDER and connection == str(flow.connection):
            continue
        flow.result["send"].append({"connection": connection, "command": TRANS_COMMAND,
                                    "body": body, "reason": "voice-quick-talk-broadcast"})
        sent += 1
    return sent


def rateLimited(flow) -> bool:
    """滑动窗口限频。"""
    window = flow.session.setdefault("voiceWindow", {})
    marks = window.get("marks")
    if type(marks) is not list:
        marks = []
    cutoff = flow.now - VOICE_LIMIT_WINDOW_MS
    marks = [value for value in marks if type(value) is int and value > cutoff]
    limited = len(marks) >= VOICE_LIMIT_COUNT
    if not limited:
        marks.append(flow.now)
    window["marks"] = marks
    return limited


def message(flow, command, selector, body) -> bool:
    """上行分派钩子。返回 ``True`` 表示已消费。"""
    if command != TRANS_COMMAND or selector != TRANS_MULTI_REQ:
        return False
    scene = flow.session.get("scene", "")
    tracelog.emit("voice", scene,
                  "quick-talk-uplink len=%d hex=%s" % (len(body), body.hex()))
    decoded = decodeRequest(body)
    if decoded is None:
        tracelog.emit("voice", scene,
                      "quick-talk-ignored len=%d expected=%d"
                      % (len(body), REQ_BODY_SIZE))
        return False
    transType, contentType, content = decoded
    if transType not in BROADCAST_TRANS_TYPES or contentType != TRANS_CONTENT_3D_SOUND:
        tracelog.emit("voice", scene,
                      "quick-talk-ignored trans_type=%d content_type=%d"
                      % (transType, contentType))
        return False
    sound = decodeSound(content)
    if sound is None:
        return False
    soundId, ownerRid = sound
    if not ownerRid:
        # 客户端未填归属时用发送方 actor，保证 3D 音源有落点。
        ownerRid = flow.session.get("actorMid") or flow.session.get("actorId") or 1
    if rateLimited(flow):
        flow.result["logs"].append("voice-quick-talk-throttled sound_id=%d" % soundId)
        return True
    bodyOut = audio_flow.encode_trans_multi_rsp(
        server_time_ms=flow.now, sound_id=soundId, owner_rid=ownerRid,
        content_type=TRANS_CONTENT_3D_SOUND)
    sent = broadcast(flow, bodyOut)
    flow.result["logs"].append(
        "voice-quick-talk-broadcast sound_id=%d owner_rid=%d trans_type=%d peers=%d"
        % (soundId, ownerRid, transType, sent))
    tracelog.emit("voice", scene,
                  "quick-talk-broadcast sound_id=%d owner_rid=%d peers=%d path=%s"
                  % (soundId, ownerRid, sent, audio_flow.soundPath(soundId)))
    return True
