# -*- coding: utf-8 -*-
"""训练关（``SH_CS_CMD_DUNGEON = 67``）协议 + 训练信息 UI（``cmd=11``）下发。

用户诉求（2026-10-03 第三项续做）
----------------------------------
「接着做**打完通关涨分**」—— 前一轮已让训练场陪练**能被打死**
（``scripts/npc_battle.py``）。这一轮补上「打完 → 通关 → 涨分」那一环。

协议取证（全部来自 ``TieJiClient.exe`` 内嵌 ``sh_proto_cs`` TDR，权威）
---------------------------------------------------------------------
1. 命令宏（``tdr_dump.py find SH_CS_CMD``）::

       SH_CS_CMD_UI      = 11   （ui协议包，sync 通道）
       SH_CS_CMD_DUNGEON = 67   （dungeon_pkg「关卡协议」，sync 通道）

   通道归属出处 = exe 里的「pkg → 通道」注册表（@17300xxx 一带，明文）::

       ui协议包    | sync
       dungeon_pkg | 关卡协议 | sync

2. ``E_CS_PROTO_DUNGEON_MSGID``（训练关协议族，cmd=67）::

       E_CS_PROTO_DUNGEON_CHANGE_STATE_REQ  = 1   c->s: 训练关改变状态请求
       E_CS_PROTO_DUNGEON_STATE_NOTIFY      = 2   s->c: 训练关状态通知
       E_CS_PROTO_DUNGEON_HINT_NOTIFY       = 3   s->c: 训练关提示通知
       E_CS_PROTO_DUNGEON_ENTER_BLACK_SCREEN= 4   s->c: 客户端进入黑屏通知

   结构（``tdr_dump.py struct``，字段偏移为 **pack(1)** 布局 ⇒ 线上就是字段顺序拼接）::

       CS_PROTO_DUNGEON_CHANGE_STATE_REQ : state i32@0                       （4B）
       CS_PROTO_DUNGEON_STATE_NOTIFY     : state i32@0, svr_time u64@4,
                                           state_time u32@12                 （16B）
       CS_PROTO_DUNGEON_HINT_NOTIFY      : hint_type i32@0, svr_time u64@4,
                                           is_open i8@12                     （13B）
       CS_PROTO_DUNGEON_ENTER_BLACK_SCREEN: svr_time u64@0, black_time i16@8 （10B）

   ⚠️ 上面是**结构体**长度。线上每包还要**再 +2 字节选择子前缀**
   （``>H`` 大端）⇒ 实际包长 = 结构长 + 2：

   ================================  ==========  ==========
   协议                               结构        线上
   ================================  ==========  ==========
   CHANGE_STATE_REQ  (c→s)            4           6
   STATE_NOTIFY      (s→c)            16          18
   HINT_NOTIFY       (s→c)            13          15
   ENTER_BLACK_SCREEN(s→c)            10          12
   SHOW_TRAIN_INFO   (cmd=11)         24          26
   SHOW_TRAIN_GUIDE  (cmd=11)         16          18
   ================================  ==========  ==========

   pack(1) 的**实证**：``CS_PROTO_UI_PRINT_STRING`` 里 ``belong_mid u64`` 落在
   偏移 **12**（不是 16）—— C++ 只有 ``#pragma pack(1)`` 才会出现非对齐的 u64。
   线上编码 = 字段顺序 + 大端（与仓库既有编码器 ``>HQQ`` 同一约定）。
   包长与字段序的黄金串校验见 ``hkx_decode/test_dungeon.py`` 第 ① 段。

3. 状态枚举 ``E_DUNGEON_STATE_DEF``::

       E_DUNGEON_STATE_INIT          = 0   关卡_初始状态
       E_DUNGEON_STATE_TRANSFER      = 1   关卡_传送状态
       E_DUNGEON_STATE_PREPARE       = 2   关卡_准备状态
       E_DUNGEON_STATE_RUNNING       = 3   关卡_运行状态
       E_DUNGEON_STATE_NOTIFY_RESULT = 4   关卡_通知结果状态   ← 「通关」

4. 训练内容枚举 ``E_DUNGEON_CONTENT_*``（谁是谁，与关卡卡片一一对上）::

       E_DUNGEON_CONTENT_BASIC_CHOP_TRAIN    = 1   初级挥砍训练
       E_DUNGEON_CONTENT_ADVANCED_CHOP_TRAIN = 2   高级挥砍训练
       E_DUNGEON_CONTENT_BASIC_BLOCK_TRAIN   = 3   初级格挡训练
       E_DUNGEON_CONTENT_ADVANCED_BLOCK_TRAIN= 4   高级格挡训练
       E_DUNGEON_CONTENT_BASIC_PARRY_TRAIN   = 5   初级招架训练
       E_DUNGEON_CONTENT_ADVANCED_PARRY_TRAIN= 6   高级招架训练
       E_DUNGEON_CONTENT_SHOOT_TRAIN         = 7   弓箭训练
       E_DUNGEON_CONTENT_MOUNT_SHOOT_TRAIN   = 8   骑射训练
       E_DUNGEON_CONTENT_BLADE_MOUNT_TRAIN   = 9   刀骑训练
       E_DUNGEON_CONTENT_SPEAR_MOUNT_TRAIN   = 10  枪骑训练

   训练提示 ``E_DUNGEON_HINT_*``：``INVALID=0 / AI_ATTACK_DIR=1 / SUCCESS_PARRY=2``。

5. 训练信息 UI（``cmd=11`` UI，s->c 专用；上行日志里**从未**出现 cmd=11）::

       E_CS_PROTO_UI_SHOW_TRAIN_INFO  = 3
       CS_PROTO_UI_SHOW_TRAIN_INFO : svr_time u64@0, id i32@8（**0=隐藏**）,
                                     tip i32@12, count i32@16, total i32@20
       E_CS_PROTO_UI_SHOW_TRAIN_GUIDE = 4
       CS_PROTO_UI_SHOW_TRAIN_GUIDE: svr_time u64@0, id i32@8, tip i32@12

6. 训练场 5 张卡 ↔ 关卡 ↔ 场景（``contracts.py`` 已登记，出处 pattern_level_map.csv）::

       卡片 6006 挥砍   → level 10069 → xlc_map_a_camp
       卡片 6007 骑射   → level 10071 → xlc_map_b_archer
       卡片 6008 刀骑   → level 10072 → xlc_map_c_cavalry
       卡片 6009 招架   → level 10073 → xlc_map_a_camp
       卡片 6010 弓箭手 → level 10074 → xlc_map_a_camp

本模块做什么
------------
* ``enterTraining(flow)`` —— 进图（``scene.battleEntry`` 尾）发一次
  ``STATE_NOTIFY(RUNNING)``（把客户端推进「训练关运行中」）+ ``SHOW_TRAIN_INFO``。
* ``onAllTargetsDead(flow)`` —— ``npc_battle`` 判定「陪练全灭」时调用：
  发 ``STATE_NOTIFY(NOTIFY_RESULT)``（**通关**）+ ``SHOW_TRAIN_INFO(count+1)``。
* ``message(flow, command, selector, body)`` —— 收 ``cmd=67 sel=1``
  （``CHANGE_STATE_REQ``）→ 回 ``STATE_NOTIFY``（同 state 回显）+ 记日志。

开关（``[dungeon]`` 段 / 环境变量 ``T7_DUNGEON_INI``）
-----------------------------------------------------
===============  ==========================  ==========================
键                环境变量                     默认
===============  ==========================  ==========================
``state``        ``T7_DUNGEON_STATE``         ``on``  训练关状态机
``train_info``   ``T7_DUNGEON_TRAIN_INFO``    ``on``  训练信息 UI
``hint``         ``T7_DUNGEON_HINT``          ``on``  提示 UI（招架/AI 方向）
``content``      ``T7_DUNGEON_CONTENT``       ``-1``  覆盖训练内容号（-1=按关卡查表）
``id``           ``T7_DUNGEON_INFO_ID``       ``-1``  覆盖 SHOW_TRAIN_INFO 的 id
===============  ==========================  ==========================

⚠️ **本模块是附加能力**：开关关掉 / 关卡不是训练场 / 任何异常 ⇒ 一个包都不发，
``battle`` 与 ``npc_battle`` 两条已验证的链**逐位不变**
（与 ``npc`` / ``ccobject`` / ``tutorial`` / ``npc_battle`` 同一纪律）。

仍未闭合（实机才能定）
----------------------
* ``SHOW_TRAIN_INFO.id`` 的**真实取值**：TDR 注释是「新手教学关卡信息资源id」，
  本模块默认发**卡片号**（6006..6010，与 room-create 上行同源），可用 ``id=`` 覆盖。
* 「涨分」的**持久化分数**走的是 ``E_CS_PROTO_USER_INFO_MSGID`` 里的
  ``E_CS_PROTO_USER_STAT_INFO_UPDATE``（``CS_PROTO_USER_STAT_INFO_UPDATE`` =
  ``{user_stat_module_type, update_index, update_value}``，
  域号 32 = ``E_USER_STAT_INFO_CHANGE_SINGLE_TRAIN_MODE_COMMON_DATA``，
  字段号 6 = ``..._BASIC_CHOP_TRAIN_MAX_SCORE``）。
  **该协议族在 exe 的注册表里挂在 ``logic`` 通道**（``user_info | 基本信息 | logic``），
  与 sync/instance 通道不是同一套帧格式 —— cmd 号尚未定案，本轮**不发**。
"""
import os
import struct

from . import contracts as wire

# --- 命令宏（TDR 权威） -----------------------------------------------------
UI_COMMAND = 0x000B            # SH_CS_CMD_UI      = 11
DUNGEON_COMMAND = 0x0043       # SH_CS_CMD_DUNGEON = 67

# --- cmd=67 选择子（E_CS_PROTO_DUNGEON_MSGID） ------------------------------
DUNGEON_CHANGE_STATE_REQ = 0x0001
DUNGEON_STATE_NOTIFY = 0x0002
DUNGEON_HINT_NOTIFY = 0x0003
DUNGEON_ENTER_BLACK_SCREEN = 0x0004

# --- cmd=11 选择子（E_CS_PROTO_UI_MSGID） -----------------------------------
UI_SHOW_TRAIN_INFO = 0x0003
UI_SHOW_TRAIN_GUIDE = 0x0004

# --- E_DUNGEON_STATE_DEF ----------------------------------------------------
STATE_INIT = 0
STATE_TRANSFER = 1
STATE_PREPARE = 2
STATE_RUNNING = 3
STATE_NOTIFY_RESULT = 4
STATE_NAMES = {
    STATE_INIT: "init", STATE_TRANSFER: "transfer", STATE_PREPARE: "prepare",
    STATE_RUNNING: "running", STATE_NOTIFY_RESULT: "notify-result",
}

# --- E_DUNGEON_HINT_* -------------------------------------------------------
HINT_AI_ATTACK_DIR = 1
HINT_SUCCESS_PARRY = 2

# --- E_DUNGEON_CONTENT_* ----------------------------------------------------
CONTENT_BASIC_CHOP_TRAIN = 1
CONTENT_ADVANCED_CHOP_TRAIN = 2
CONTENT_BASIC_BLOCK_TRAIN = 3
CONTENT_ADVANCED_BLOCK_TRAIN = 4
CONTENT_BASIC_PARRY_TRAIN = 5
CONTENT_ADVANCED_PARRY_TRAIN = 6
CONTENT_SHOOT_TRAIN = 7
CONTENT_MOUNT_SHOOT_TRAIN = 8
CONTENT_BLADE_MOUNT_TRAIN = 9
CONTENT_SPEAR_MOUNT_TRAIN = 10
CONTENT_NAMES = {
    CONTENT_BASIC_CHOP_TRAIN: "basic-chop", CONTENT_ADVANCED_CHOP_TRAIN: "advanced-chop",
    CONTENT_BASIC_BLOCK_TRAIN: "basic-block", CONTENT_ADVANCED_BLOCK_TRAIN: "advanced-block",
    CONTENT_BASIC_PARRY_TRAIN: "basic-parry", CONTENT_ADVANCED_PARRY_TRAIN: "advanced-parry",
    CONTENT_SHOOT_TRAIN: "shoot", CONTENT_MOUNT_SHOOT_TRAIN: "mount-shoot",
    CONTENT_BLADE_MOUNT_TRAIN: "blade-mount", CONTENT_SPEAR_MOUNT_TRAIN: "spear-mount",
}

# 关卡 → 训练内容号。出处 = 卡片名（contracts 的注释）+ 内容枚举的中文名。
TRAIN_CONTENT_BY_LEVEL = {
    10069: CONTENT_BASIC_CHOP_TRAIN,     # 训练场_挥砍（卡片 6006）
    10071: CONTENT_MOUNT_SHOOT_TRAIN,    # 训练场_骑射（卡片 6007）
    10072: CONTENT_BLADE_MOUNT_TRAIN,    # 训练场_刀骑（卡片 6008）
    10073: CONTENT_BASIC_PARRY_TRAIN,    # 训练场_招架（卡片 6009）
    10074: CONTENT_SHOOT_TRAIN,          # 训练场_弓箭手（卡片 6010）
}

# 关卡 → 卡片号（SHOW_TRAIN_INFO 的 id 默认值）。
TRAIN_CARD_BY_LEVEL = {
    10069: 6006, 10071: 6007, 10072: 6008, 10073: 6009, 10074: 6010,
}

# --- 开关 -------------------------------------------------------------------
DUNGEON_INI_SECTION = "dungeon"
DUNGEON_INI_ENV = "T7_DUNGEON_INI"
_ENV_STATE = "T7_DUNGEON_STATE"
_ENV_TRAIN_INFO = "T7_DUNGEON_TRAIN_INFO"
_ENV_HINT = "T7_DUNGEON_HINT"
_ENV_CONTENT = "T7_DUNGEON_CONTENT"
_ENV_INFO_ID = "T7_DUNGEON_INFO_ID"

_INI = {}
try:
    _INI = wire.iniSection(DUNGEON_INI_SECTION, DUNGEON_INI_ENV) or {}
except Exception:  # noqa: BLE001 —— 读 ini 失败绝不拖垮主链
    _INI = {}

_SOURCE = {}


def _raw(envName, key):
    """**环境变量 > [dungeon] ini > 空**；同时记来源（自证用）。"""
    value = os.environ.get(envName)
    if value is not None and str(value).strip():
        _SOURCE[key] = "env"
        return str(value).strip()
    text = str(_INI.get(key, "")).strip()
    if text:
        _SOURCE[key] = "ini"
        return text
    _SOURCE[key] = "default"
    return ""


def _flag(envName, key, default):
    text = _raw(envName, key).lower()
    if text in ("", "default"):
        return default
    return text in ("on", "all", "true", "yes", "1")


def _number(envName, key, default):
    text = _raw(envName, key)
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return default


STATE_ENABLED = _flag(_ENV_STATE, "state", True)
TRAIN_INFO_ENABLED = _flag(_ENV_TRAIN_INFO, "train_info", True)
HINT_ENABLED = _flag(_ENV_HINT, "hint", True)
CONTENT_OVERRIDE = _number(_ENV_CONTENT, "content", -1)
INFO_ID_OVERRIDE = _number(_ENV_INFO_ID, "id", -1)


# ---------------------------------------------------------------------------
# 编码 / 解码（线上 = 大端 + 字段顺序 + 2 字节选择子前缀）
# ---------------------------------------------------------------------------
def decode_change_state_req(body):
    """``cmd=67 sel=1`` 上行 → ``state``。body = sel(2) + state(i32)。"""
    if not isinstance(body, (bytes, bytearray)):
        raise TypeError("body must be bytes")
    if len(body) != 6:
        raise ValueError("dungeon change-state-req must be exactly 6 bytes, got %d"
                         % len(body))
    selector, state = struct.unpack(">Hi", body)
    if selector != DUNGEON_CHANGE_STATE_REQ:
        raise ValueError("dungeon change-state-req expected selector 1")
    return state


def encode_state_notify(*, state, svr_time_ms, state_time):
    """``cmd=67 sel=2``：state(i32) + svr_time(u64) + state_time(u32)。"""
    return struct.pack(">HiQI", DUNGEON_STATE_NOTIFY, int(state),
                       int(svr_time_ms) & 0xFFFFFFFFFFFFFFFF,
                       int(state_time) & 0xFFFFFFFF)


def encode_hint_notify(*, hint_type, svr_time_ms, is_open):
    """``cmd=67 sel=3``：hint_type(i32) + svr_time(u64) + is_open(i8)。"""
    return struct.pack(">HiQb", DUNGEON_HINT_NOTIFY, int(hint_type),
                       int(svr_time_ms) & 0xFFFFFFFFFFFFFFFF,
                       1 if is_open else 0)


def encode_enter_black_screen(*, svr_time_ms, black_time):
    """``cmd=67 sel=4``：svr_time(u64) + black_time(i16)。"""
    return struct.pack(">HQh", DUNGEON_ENTER_BLACK_SCREEN,
                       int(svr_time_ms) & 0xFFFFFFFFFFFFFFFF,
                       int(black_time))


def encode_show_train_info(*, info_id, tip, count, total, svr_time_ms):
    """``cmd=11 sel=3``：svr_time(u64) + id(i32) + tip(i32) + count(i32) + total(i32)。"""
    return struct.pack(">HQiiii", UI_SHOW_TRAIN_INFO,
                       int(svr_time_ms) & 0xFFFFFFFFFFFFFFFF,
                       int(info_id), int(tip), int(count), int(total))


def encode_show_train_guide(*, info_id, tip, svr_time_ms):
    """``cmd=11 sel=4``：svr_time(u64) + id(i32) + tip(i32)。"""
    return struct.pack(">HQii", UI_SHOW_TRAIN_GUIDE,
                       int(svr_time_ms) & 0xFFFFFFFFFFFFFFFF,
                       int(info_id), int(tip))


# ---------------------------------------------------------------------------
# 场景/关卡判定
# ---------------------------------------------------------------------------
def contentFor(levelId=None):
    """本关的训练内容号；不是训练场返回 ``0``。"""
    if CONTENT_OVERRIDE > 0:
        return CONTENT_OVERRIDE
    try:
        level = wire.LEVEL_ID if levelId is None else int(levelId)
    except Exception:  # noqa: BLE001
        return 0
    return TRAIN_CONTENT_BY_LEVEL.get(level, 0)


def infoIdFor(levelId=None):
    """``SHOW_TRAIN_INFO.id``。``id=`` 覆盖优先；否则用卡片号；再不行退回 LEVEL_ID。"""
    if INFO_ID_OVERRIDE >= 0:
        return INFO_ID_OVERRIDE
    try:
        level = wire.LEVEL_ID if levelId is None else int(levelId)
    except Exception:  # noqa: BLE001
        return 0
    return TRAIN_CARD_BY_LEVEL.get(level, level)


def isTraining(levelId=None):
    """本关是不是训练场（决定要不要驱动训练关状态机）。"""
    if CONTENT_OVERRIDE > 0:
        return True
    try:
        level = wire.LEVEL_ID if levelId is None else int(levelId)
    except Exception:  # noqa: BLE001
        return False
    return level in TRAIN_CONTENT_BY_LEVEL


def _targetCount(flow, scene):
    """本场景陪练（NPC）总数。取 ``npc.actors()`` 的长度。"""
    try:
        from . import npc
        return len(npc.actors(scene))
    except Exception:  # noqa: BLE001
        return 0


def _deadCount(flow):
    try:
        return len(flow.session.get("npcDead") or {})
    except Exception:  # noqa: BLE001
        return 0


# ---------------------------------------------------------------------------
# 下发
# ---------------------------------------------------------------------------
def _stateTime(flow):
    """状态已持续毫秒。``session["dungeonStateAt"]`` 记账，缺省 0。"""
    try:
        started = flow.session.get("dungeonStateAt")
        if started is None:
            return 0
        return max(0, int(flow.now) - int(started))
    except Exception:  # noqa: BLE001
        return 0


def pushState(flow, state, tag=""):
    """发 ``STATE_NOTIFY``；同时记账 ``session["dungeonState"]``。返回 True/False。"""
    if not STATE_ENABLED:
        return False
    try:
        # 先算「上一状态持续了多久」，再覆盖记账点 —— 顺序不能反（否则恒为 0）。
        state_time = _stateTime(flow)
        flow.session["dungeonState"] = int(state)
        flow.session["dungeonStateAt"] = int(flow.now)
        body = encode_state_notify(state=state, svr_time_ms=wire.serverNowMs(),
                                   state_time=state_time)
        flow.send(DUNGEON_COMMAND, body,
                  "dungeon-state-notify state=%d(%s) state_time=%d%s"
                  % (int(state), STATE_NAMES.get(int(state), "?"),
                     state_time, (" " + tag) if tag else ""))
        return True
    except Exception as error:  # noqa: BLE001
        _log(flow, "dungeon-state-failed " + repr(error))
        return False


def pushTrainInfo(flow, *, tip=0, count=None, total=None, tag=""):
    """发 ``SHOW_TRAIN_INFO``。``count``/``total`` 缺省时按陪练死亡数现算。"""
    if not TRAIN_INFO_ENABLED:
        return False
    try:
        scene = flow.session.get("scene") or ""
        if total is None:
            total = _targetCount(flow, scene)
        if count is None:
            count = _deadCount(flow)
        body = encode_show_train_info(
            info_id=infoIdFor(), tip=tip, count=count, total=total,
            svr_time_ms=wire.serverNowMs())
        flow.send(UI_COMMAND, body,
                  "dungeon-train-info id=%d tip=%d count=%d/%d%s"
                  % (infoIdFor(), tip, count, total,
                     (" " + tag) if tag else ""))
        return True
    except Exception as error:  # noqa: BLE001
        _log(flow, "dungeon-train-info-failed " + repr(error))
        return False


def pushHint(flow, hint_type, is_open=True):
    """发 ``HINT_NOTIFY``（招架 / AI 攻击方向提示）。"""
    if not HINT_ENABLED:
        return False
    try:
        body = encode_hint_notify(hint_type=hint_type,
                                  svr_time_ms=wire.serverNowMs(),
                                  is_open=is_open)
        flow.send(DUNGEON_COMMAND, body,
                  "dungeon-hint-notify type=%d open=%d" % (hint_type, 1 if is_open else 0))
        return True
    except Exception as error:  # noqa: BLE001
        _log(flow, "dungeon-hint-failed " + repr(error))
        return False


def _log(flow, text):
    try:
        flow.result["logs"].append(text)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# 生命周期钩子
# ---------------------------------------------------------------------------
def enterTraining(flow):
    """进图（``scene.battleEntry`` 尾）调用一次。

    训练场 ⇒ 发 ``STATE_NOTIFY(RUNNING)`` + ``SHOW_TRAIN_INFO``。
    非训练场 / 开关关 / 异常 ⇒ 静默返回 ``False``（逐位不变）。
    """
    try:
        if not isTraining():
            return False
        if flow.session.get("role") != "instance" or flow.session.get("leaving"):
            return False
        if flow.session.get("dungeonEntered"):
            return False
        flow.session["dungeonEntered"] = True
        level = wire.LEVEL_ID
        content = contentFor()
        _log(flow, "dungeon-enter level=%d content=%d(%s) scene=%s"
             % (level, content, CONTENT_NAMES.get(content, "?"),
                flow.session.get("scene")))
        sent = pushState(flow, STATE_RUNNING, "level=%d" % level)
        sent = pushTrainInfo(flow, tag="enter level=%d" % level) or sent
        return sent
    except Exception as error:  # noqa: BLE001 —— 附加能力，绝不外抛
        _log(flow, "dungeon-enter-failed " + repr(error))
        return False


def onAllTargetsDead(flow):
    """陪练**全灭**时调用（``npc_battle`` 判完 ``left == 0`` 之后）。

    ⇒ 发 ``STATE_NOTIFY(NOTIFY_RESULT)``（通关）+ ``SHOW_TRAIN_INFO(count=total)``。
    只发一次（``session["dungeonCleared"]`` 去重）。
    """
    try:
        if not isTraining():
            return False
        if flow.session.get("dungeonCleared"):
            return False
        flow.session["dungeonCleared"] = True
        scene = flow.session.get("scene") or ""
        total = _targetCount(flow, scene)
        _log(flow, "dungeon-clear level=%d content=%d(%s) targets=%d"
             % (wire.LEVEL_ID, contentFor(), CONTENT_NAMES.get(contentFor(), "?"),
                total))
        sent = pushTrainInfo(flow, count=total, total=total, tag="clear")
        sent = pushState(flow, STATE_NOTIFY_RESULT, "clear targets=%d" % total) or sent
        return sent
    except Exception as error:  # noqa: BLE001
        _log(flow, "dungeon-clear-failed " + repr(error))
        return False


def message(flow, command, selector, body):
    """``app.handleMessage`` 的接线点。处理 ``cmd=67`` 上行；不处理返回 ``False``。"""
    if command != DUNGEON_COMMAND:
        return False
    if flow.session.get("role") != "instance" or flow.session.get("leaving"):
        return False
    if selector == DUNGEON_CHANGE_STATE_REQ:
        try:
            state = decode_change_state_req(body)
        except (TypeError, ValueError) as error:
            _log(flow, "dungeon-change-state-bad " + repr(error)
                 + " hex=" + bytes(body).hex())
            return True
        _log(flow, "dungeon-change-state-req state=%d(%s) training=%d"
             % (state, STATE_NAMES.get(state, "?"), 1 if isTraining() else 0))
        # 原样回显一帧 STATE_NOTIFY —— 让客户端拿到权威的「当前状态 + 服务器时间」。
        pushState(flow, state, "echo")
        return True
    # sel=2/3/4 是 s->c 专用；客户端不该发。记下来不猜。
    _log(flow, "dungeon-unhandled sel=%d len=%d hex=%s"
         % (selector, len(body), bytes(body)[:32].hex()))
    return True


def describe(flow):
    """一行摘要，给日志用。不抛异常。"""
    try:
        return ("training=%d content=%d(%s) id=%d state=%s cleared=%d dead=%d"
                % (1 if isTraining() else 0, contentFor(),
                   CONTENT_NAMES.get(contentFor(), "?"), infoIdFor(),
                   STATE_NAMES.get(flow.session.get("dungeonState", -1), "none"),
                   1 if flow.session.get("dungeonCleared") else 0,
                   _deadCount(flow)))
    except Exception as error:  # noqa: BLE001
        return "describe-failed " + repr(error)


def report():
    """启动自证行用；不抛异常。"""
    try:
        levels = ",".join(str(k) for k in sorted(TRAIN_CONTENT_BY_LEVEL))
        return ("state=%s train_info=%s hint=%s content=%s id=%s levels=[%s] ini=%s"
                % ("on" if STATE_ENABLED else "off",
                   "on" if TRAIN_INFO_ENABLED else "off",
                   "on" if HINT_ENABLED else "off",
                   CONTENT_OVERRIDE if CONTENT_OVERRIDE > 0 else "auto",
                   INFO_ID_OVERRIDE if INFO_ID_OVERRIDE >= 0 else "auto",
                   levels, DUNGEON_INI_SECTION if _INI else "none"))
    except Exception as error:  # noqa: BLE001
        return "report-failed " + repr(error)


# 启动自证行（与 ``[npc]`` / ``[npc-battle]`` / ``[cc]`` / ``[guide]`` 同一风格）。
print("[dungeon] " + report(), flush=True)
