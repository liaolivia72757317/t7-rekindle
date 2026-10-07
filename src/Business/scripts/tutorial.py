"""教学关引导物件（地面箭头 / 流光 / 灯笼 / 靶子）的加载与下发。

为什么需要
----------
用户 2026-09-30 要求：「白马要塞，应该是人机攻城教学，**地面还有指示箭头**，
能实现不能？」——并选定**方案 A（引导版）**：箭头 + 流光 + 灯笼 + 静态靶子。

侦查结论（见 ``T7-白马要塞-人机攻城教学-可行性报告-20260930.md``）：
* 客户端有整套教学 BTree（``../data/btree/newbie/``，117 条），
  但**全是纯 XML「特效播放器」**（播放特效/可见性/碰撞/音效），**无 AI 行为**。
* MO 定义表（``data1.vfs`` run 26905，454 行）里教学物件的 ``tid`` 已解出：
  箭头 ``29/33``、绿箭头 ``203/230-233``、灯笼 ``34/79``、骑兵流光 ``40/41``、
  步兵流光 ``54/55``、白色光环 ``207/210/219/220``、大绿圈 ``214``。
* 服务端已有**实机跑通**的下发通道：
  ``vision_flow.encode_cc_dynamic_vision_add_event``（``cmd=14 sel=1 第4号分支``，
  202B/物件），樊城 24 件器械、烟雾弹、火都走它。
  ⇒ 教学箭头与器械**同一个通道、同一个编码器**，只是 ``res_id`` 与坐标不同。

分工（与 ``ccobject.py`` 完全对称，别混）
-----------------------------------------
* ``codec/vision_flow.py`` —— 编码器（共用，教学物件不新增编码器）。
* **本模块** —— 读 ``data/scene/<场景>/tutorial_guide.json`` → ``CcDynamicObject``。
* ``scene.py`` —— 决定**什么时候**发（教学关开局 / 按 mid 回包）。

数据格式（``tutorial_guide.json``）
----------------------------------
::

    {
      "scene": "pve_gc",
      "note": "白马要塞教学引导",
      "objects": [
        {"tid": 29, "pos": [416.5, 476.8, 19.10], "face": [0,0,0], "name": "箭头1"},
        {"tid": 33, "pos": [421.6, 476.8, 19.16], "name": "箭头2"}
      ]
    }

* ``pos`` 是 ``[x, y, z]``（``z`` 才是高度，与 ``ccobject.json`` 同序）。
* ``face`` 是欧拉角（弧度），可省（默认 0）。地面箭头通常只用 yaw。
* ``tid`` 就是上表里的教学物件号，**直接当 ``res_id``**（与 CC 器械同一命名空间，
  证据：MO 表 row0 tid=1 = 云梯 = ``CC_LADDER_TID``）。

开关（与 ``[cc]`` 同一套纪律）
------------------------------
``tutorial_guide`` 默认 **on**（用户明确要箭头）；但**场景没有
``tutorial_guide.json`` 时行为逐位不变**（返回空 tuple ⇒ 不发包）。
``[guide]`` 段 ini / 环境变量 ``T7_GUIDE``（改 ini 后重启服务端才生效）：

    0 / off                → 不发（回到改动前）
    on / all / true / yes  → 发全部
    <N>（正整数）          → 只发前 N 个（二分定位用）

rid 命名空间
------------
教学物件用 ``GUIDE_RID_BASE``（默认 20000）起，**避开** 本地玩家(1)/敌人(2)/
CC 器械(10001..)。同一条 ``VISION_LIST_RSP`` 里必须登记它们的 mid，
否则客户端收到报文**无处挂载 ⇒ 整包丢弃**（2026-09-20 樊城那条铁证的同一机制）。
"""
import io
import json
import os

from . import contracts as wire
from .codec import vision_flow

# --- [guide] 段 ini 通道（与 ccobject 的 [cc] 完全对称） ---------------------
GUIDE_INI_SECTION = "guide"
GUIDE_INI_ENV = "T7_GUIDE_INI"


def _guideIniRaw():
    """读 ``level.ini`` / ``server.ini`` 的 ``[guide]`` 段。"""
    return wire.iniSection(GUIDE_INI_SECTION, GUIDE_INI_ENV)


_GUIDE_INI = _guideIniRaw()
_GUIDE_SOURCE = {}


def _guideKnobRaw(envName, key, default):
    """**环境变量 > [guide] ini > 默认**；同时记来源（自证用）。"""
    raw = os.environ.get(envName)
    if raw is not None and str(raw).strip():
        _GUIDE_SOURCE[key] = "env"
        return str(raw).strip(), "env"
    text = str(_GUIDE_INI.get(key, "")).strip()
    if text:
        _GUIDE_SOURCE[key] = "ini"
        return text, "ini"
    _GUIDE_SOURCE[key] = "default"
    return default, "default"


GUIDE_ENABLED = True
GUIDE_ENV_SWITCH = "T7_GUIDE"
GUIDE_FILE = "tutorial_guide.json"

# 视野 mid 基数：比 CC 器械(10000)高一档，互不相撞，也远离 MO 状态值上界(6000)。
GUIDE_RID_BASE = 20000
GUIDE_RID_ENV = "T7_GUIDE_RID_BASE"

_cache = {}


def _sceneDir():
    """定位 ``data/scene``：逐级向上找（与 ``ccobject._sceneDir`` 同一套）。"""
    try:
        for node in wire.walkUp():
            path = os.path.join(node, "data", "scene")
            if os.path.isdir(path):
                return path
    except (AttributeError, TypeError, ValueError):
        pass
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "scene")


def guideLimit():
    """本进程要发几个引导物件。``None`` = 不发。

    * ``off`` / ``0`` → ``None``
    * ``on`` / ``all`` / ``true`` / ``yes`` → 全部（内部 1<<30）
    * ``<N>`` → ``N``
    * 未设置 → 看 ``GUIDE_ENABLED``
    """
    raw, _ = _guideKnobRaw(GUIDE_ENV_SWITCH, "objects",
                           "on" if GUIDE_ENABLED is True else "off")
    text = str(raw).strip().lower()
    if text in ("", "0", "off", "false", "no"):
        return None
    if text in ("on", "all", "true", "yes"):
        return 1 << 30 if GUIDE_ENABLED is True else None
    try:
        return max(1, int(float(text)))
    except ValueError:
        return 1 << 30 if GUIDE_ENABLED is True else None


def guideRidBase():
    """引导物件 rid 起始值。**环境变量 > [guide] rid_base > 20000**。

    下限 ``20001`` —— 比 CC 器械上界（10024）高，绝不与既有 mid 撞。
    """
    raw, _ = _guideKnobRaw(GUIDE_RID_ENV, "rid_base", str(GUIDE_RID_BASE))
    try:
        return max(GUIDE_RID_BASE, int(float(raw)))
    except ValueError:
        return GUIDE_RID_BASE


def ridFor(index):
    """第 ``index`` 个引导物件（1 起）的视野 mid。"""
    return guideRidBase() + index


def loadScene(scene, baseDir=None):
    """读 ``data/scene/<场景>/tutorial_guide.json``，返回**原始** dict 列表。

    文件不存在 → ``[]``（不是错误：只有配过引导的场景才有这份数据）。
    解析失败 → 抛 ``ValueError``，由调用方决定是否吞掉。
    """
    if not scene:
        return []
    root = baseDir or _sceneDir()
    path = os.path.join(root, scene, GUIDE_FILE)
    if not os.path.isfile(path):
        return []
    with io.open(path, encoding="utf-8") as handle:
        raw = json.load(handle)
    items = raw.get("objects")
    if not isinstance(items, list):
        raise ValueError("%s: 'objects' is not a list" % path)
    return items


def objects(scene, baseDir=None):
    """``tutorial_guide.json`` → ``tuple[CcDynamicObject, ...]``（按 limit 截断）。

    ``res_id = tid``（教学物件号直接当资源号，与 CC 器械同一命名空间）；
    ``inst_id`` = 序号低 16 位；``camp`` 固定 0（中立，不参与阵营判定）。
    ``hp`` / ``state`` / ``havok_res_index`` 一律给**中性值**——
    教学物件是「看得见就行」的装饰，不接交互链路，所以不猜任何状态值。
    """
    limit = guideLimit()
    if limit is None:
        return ()
    items = loadScene(scene, baseDir)
    if not items:
        return ()
    out = []
    for index, item in enumerate(items[:limit], start=1):
        try:
            tid = int(item["tid"])
        except (KeyError, TypeError, ValueError):
            continue
        pos = item.get("pos") or (0.0, 0.0, 0.0)
        if len(pos) != 3:
            continue
        face = item.get("face") or (0.0, 0.0, 0.0)
        if len(face) != 3:
            face = (0.0, 0.0, 0.0)
        out.append(vision_flow.CcDynamicObject(
            inst_id=index & 0xFFFF,
            res_id=tid,
            rid=ridFor(index),
            position=(float(pos[0]), float(pos[1]), float(pos[2])),
            rotation=(float(face[0]), float(face[1]), float(face[2])),
            camp=0,
            hp=1,
            state=0,
            havok_res_index=0,
        ))
    return tuple(out)


def visionEvent(scene, now, baseDir=None):
    """返回一条可直接 ``flow.send(0xE, ...)`` 的报文；不发时返回 ``None``。

    ``None`` 的三种情况（都**不报错**）：开关关掉 / 场景没数据 / 一个都没截到。
    加载或编码失败抛异常，由调用方吞掉并记日志 —— 引导物件是**附加**能力，
    坏一个场景不该让整局进图流程崩掉（与 ``ccobject`` 同一纪律）。
    """
    items = objects(scene, baseDir)
    if not items:
        return None
    return vision_flow.encode_cc_dynamic_vision_add_event(
        objects=items, server_time_ms=now & 0xFFFFFFFFFFFFFFFF)


def visionEventFor(items, now):
    """把**指定的**几个引导物件编成一条 ADD_EVENT；空集返回 ``None``。"""
    items = tuple(items)
    if not items:
        return None
    return vision_flow.encode_cc_dynamic_vision_add_event(
        objects=items, server_time_ms=now & 0xFFFFFFFFFFFFFFFF)


def byRid(scene, baseDir=None):
    """``{rid: CcDynamicObject}`` —— 按视野 mid 索引本场景引导物件。"""
    return {obj.rid: obj for obj in objects(scene, baseDir)}


def mids(scene, baseDir=None):
    """本场景引导物件的 mid 列表（要登记进 ``VISION_LIST_RSP``）。"""
    try:
        return tuple(obj.rid for obj in objects(scene, baseDir))
    except (OSError, ValueError, KeyError, TypeError):
        return ()


def describe(scene, baseDir=None):
    """一行摘要，给日志用。不抛异常。"""
    limit = guideLimit()
    if limit is None:
        return "disabled"
    try:
        items = loadScene(scene, baseDir)
    except (OSError, ValueError) as error:
        return "load-failed " + repr(error)
    if not items:
        return "no-data"
    names = [str(item.get("name") or item.get("tid")) for item in items[:limit]]
    return ("count=%d/%d sent=%d [%s]"
            % (len(items[:limit]), len(items), len(names), "、".join(names)))


def guideReport():
    """一行旋钮自证，给启动行用；不抛异常。"""
    try:
        limit = guideLimit()
        if limit is None:
            objects = "off"
        elif limit >= (1 << 30):
            objects = "all"
        else:
            objects = str(limit)
        return ("objects=%s rid_base=%d ini=%s"
                % (objects, guideRidBase(),
                   GUIDE_INI_SECTION if _GUIDE_INI else "none"))
    except Exception as error:  # noqa: BLE001 —— 自证行绝不能把服务端带崩
        return "report-failed " + repr(error)


# 启动自证行（与 ``[cc]`` 同一风格）。
print("[guide] " + guideReport(), flush=True)
