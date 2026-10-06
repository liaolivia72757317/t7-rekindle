# -*- coding: utf-8 -*-
"""验证 F1/F2 快捷喊话（``cmd=10 sel=300`` → ``sel=301`` 广播）。

跑法（与 ``verify_mount_turn.py`` / ``verify_runtime_movement.py`` 同款，手动敲）：

    cd <Business> && PYTHONIOENCODING=utf-8 <python> scripts/verify_voice.py

判据：

  * 结构对得上 ⇒ 解出 ``trans_type/content_type/sound_id/owner_rid``，向**其他**
    instance 会话各发一条 301（26B），发送方自己不收（``ECHO_TO_SENDER=False``）。
  * ``owner_rid == 0`` ⇒ 用发送方 actor 补齐（3D 音源要有落点）。
  * 长度不符 / ``trans_type`` 不在白名单 / ``content_type != 1`` ⇒ 返回 ``False``
    （交给 unhandled 兜底，**不猜字段、不回包**）。
  * 限频窗口内超过 ``VOICE_LIMIT_COUNT`` ⇒ 消费掉但不再转发。
  * 301 的线格式必须与 ``codec/audio_flow.encode_trans_multi_rsp`` 逐字节一致
    （26B：``sel(u16) | svr_time(u64) | content_type(i32) | sound_id(i32) | owner_rid(u64)``）。

任何一条不满足就退出码 1 —— 改喊话链路后先跑它。
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import voice  # noqa: E402
from scripts.codec import audio_flow  # noqa: E402

# ⚠️ 部署里 ``tracelog.enabled()`` 是 True，测试会往**生产** ``data/trace_log.txt``
#    灌噪声（第一次跑就灌了 222 行）。这里先把 emit 换成 no-op；下面「取证」一节
#    再临时换成采集器，跑完恢复成这个 no-op。
voice.tracelog.emit = lambda tag, scene, msg: None

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(ok)
    print(("  OK   " if ok else "  FAIL ") + name + ("  " + detail if detail else ""))


class Flow:
    """最小 flow：两个 instance 会话 + 一个 login 会话，用来验广播范围。"""

    def __init__(self, now=50000):
        self.now = now
        self.connection = 7
        self.session = {"role": "instance", "scene": "lysd", "camp": 1, "pending": {}}
        self.state = {"sessions": {
            "7": self.session,                                        # 发送方
            "9": {"role": "instance", "scene": "lysd", "camp": 1},     # 同房间同伴
            "11": {"role": "instance", "scene": "lysd", "camp": 2},    # 同房间敌营
            "13": {"role": "instance", "scene": "lysd", "leaving": True},  # 正在离场
            "15": {"role": "login", "scene": ""},                      # 大厅连接
        }}
        self.result = {"logs": [], "send": [], "timers": []}


def requestBody(transType, contentType, soundId=31252, svrId=1):
    """``CS_PROTO_TRANS_MULTI_REQ``（含 2B selector）= 22B。

    2B selector + trans_type(i32) + content_type(i32) + union 12B
    （sound_id u32 + svr_id u64 = 12B）。
    """
    return struct.pack(">Hii", voice.TRANS_MULTI_REQ, transType, contentType) \
        + struct.pack(">IQ", soundId, svrId)


def decodeRsp(body):
    return struct.unpack(">HQiiQ", body)


print("★ 结构解码（TDR CS_PROTO_TRANS_MULTI_REQ）")
body = requestBody(audio_flow.TRANS_TYPE_ROOM_ALL, voice.TRANS_CONTENT_3D_SOUND)
check("body 长度 = 22（2B selector + 4 + 4 + 12 union）", len(body) == 22, "%d B" % len(body))
decoded = voice.decodeRequest(body)
check("解出 (trans_type, content_type, 12B union)",
      decoded is not None and decoded[0] == 1 and decoded[1] == 1 and len(decoded[2]) == 12,
      repr(decoded[:2]) + " union=%dB" % len(decoded[2]))
check("union 解出 sound_id / svr_id", voice.decodeSound(decoded[2]) == (31252, 1),
      repr(voice.decodeSound(decoded[2])))
check("长度不符 ⇒ decodeRequest 返回 None（不猜）",
      voice.decodeRequest(body[:-1]) is None and voice.decodeRequest(body + b"\x00") is None)

print("★ 分派闸门")
f = Flow()
check("非 cmd=10/sel=300 ⇒ 不消费",
      voice.message(f, 2, 52, b"") is False and voice.message(f, 10, 301, body) is False)

print("★ 转发（cmd=10 sel=300 ⇒ sel=301）")
f = Flow()
handled = voice.message(f, voice.TRANS_COMMAND, voice.TRANS_MULTI_REQ, body)
check("消费掉该上行", handled is True)
peers = [item["connection"] for item in f.result["send"]]
check("广播给同房间其他 instance（9 / 11），排除离场(13)与大厅(15)",
      sorted(peers) == ["11", "9"], repr(peers))
check("默认不回给发送方(7)（ECHO_TO_SENDER=False）", "7" not in peers, repr(peers))
check("每条都是 cmd=10 / sel=301 / 26B",
      all(item["command"] == 10 and len(item["body"]) == 26
          and int.from_bytes(item["body"][:2], "big") == 301 for item in f.result["send"]),
      repr([(item["command"], len(item["body"])) for item in f.result["send"]]))
sel, svrTime, contentType, soundId, ownerRid = decodeRsp(f.result["send"][0]["body"])
check("301 字段 = (301, now, 1, sound_id, owner_rid)",
      (sel, svrTime, contentType, soundId) == (301, 50000, 1, 31252) and ownerRid == 1,
      repr((sel, svrTime, contentType, soundId, ownerRid)))
check("301 与 audio_flow.encode_trans_multi_rsp 逐字节一致",
      f.result["send"][0]["body"] == audio_flow.encode_trans_multi_rsp(
          server_time_ms=50000, sound_id=31252, owner_rid=1))

print("★ owner_rid 为 0 ⇒ 用发送方 actor 补齐")
f = Flow()
voice.message(f, voice.TRANS_COMMAND, voice.TRANS_MULTI_REQ,
              requestBody(audio_flow.TRANS_TYPE_SQUAD, voice.TRANS_CONTENT_3D_SOUND, svrId=0))
check("owner_rid 落到 1（actor 兜底）",
      decodeRsp(f.result["send"][0]["body"])[4] == 1,
      "owner_rid=%d" % decodeRsp(f.result["send"][0]["body"])[4])

print("★ 白名单 / 内容类型不符 ⇒ 不消费、不回包")
f = Flow()
check("trans_type=0（INVALID）⇒ False",
      voice.message(f, voice.TRANS_COMMAND, voice.TRANS_MULTI_REQ,
                    requestBody(0, voice.TRANS_CONTENT_3D_SOUND)) is False
      and not f.result["send"], repr(f.result["send"]))
f = Flow()
check("trans_type=9（越界）⇒ False",
      voice.message(f, voice.TRANS_COMMAND, voice.TRANS_MULTI_REQ,
                    requestBody(9, voice.TRANS_CONTENT_3D_SOUND)) is False
      and not f.result["send"])
f = Flow()
check("content_type=0（INVALID）⇒ False",
      voice.message(f, voice.TRANS_COMMAND, voice.TRANS_MULTI_REQ,
                    requestBody(audio_flow.TRANS_TYPE_ROOM_ALL, 0)) is False
      and not f.result["send"])
f = Flow()
check("长度不符 ⇒ False（落 unhandled，不猜字段）",
      voice.message(f, voice.TRANS_COMMAND, voice.TRANS_MULTI_REQ, b"\x01\x2c\x00") is False
      and not f.result["send"])

print("★ 服务端侧限频")
f = Flow()
for _ in range(voice.VOICE_LIMIT_COUNT):
    f.result["send"] = []
    voice.message(f, voice.TRANS_COMMAND, voice.TRANS_MULTI_REQ, body)
check("窗口内第 %d 条仍转发" % voice.VOICE_LIMIT_COUNT, len(f.result["send"]) == 2,
      "%d 条" % len(f.result["send"]))
f.result["send"] = []
handled = voice.message(f, voice.TRANS_COMMAND, voice.TRANS_MULTI_REQ, body)
check("超出上限 ⇒ 消费掉但不转发",
      handled is True and not f.result["send"]
      and any("throttled" in x for x in f.result["logs"]),
      repr(f.result["logs"][-1:]))

print("★ 取证：整条 hex 落盘（不截断）")
captured = []
originalEmit = voice.tracelog.emit
voice.tracelog.emit = lambda tag, scene, msg: captured.append((tag, scene, msg))
try:
    f = Flow()
    voice.message(f, voice.TRANS_COMMAND, voice.TRANS_MULTI_REQ, body)
finally:
    voice.tracelog.emit = originalEmit
check("tracelog tag=voice 且带完整 22B hex（app 的 uplink 采样只留 32B，这里全量）",
      bool(captured) and all(item[0] == "voice" for item in captured)
      and any(("hex=" + body.hex()) in item[2] for item in captured),
      "%d 条: %s" % (len(captured), repr(captured[0])[:120]))
check("转发成功也落盘（sound_id / owner_rid / 同房人数）",
      any("quick-talk-broadcast" in item[2] and "peers=2" in item[2] for item in captured),
      repr(captured[-1][2]) if captured else "")
f = Flow()
captured.clear()
voice.tracelog.emit = lambda tag, scene, msg: captured.append((tag, scene, msg))
try:
    voice.message(f, voice.TRANS_COMMAND, voice.TRANS_MULTI_REQ, b"\x01\x2c\xff")
finally:
    voice.tracelog.emit = originalEmit
check("长度不符 ⇒ 仍然整条落盘 + 记 ignored",
      len(captured) == 2 and "quick-talk-ignored" in captured[1][2],
      repr(captured[1][2]) if len(captured) > 1 else "只落了 %d 条" % len(captured))

print("ALL OK" if all(RESULTS) else "FAIL " + str(RESULTS.count(False)))
sys.exit(0 if all(RESULTS) else 1)
