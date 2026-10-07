"""关卡自身 NPC（陪练 / 城镇人 / 刷怪组单位）的加载与下发。

为什么需要
----------
用户 2026-10-02 三条诉求第 1 条：「**接关卡自己的 NPC 通道**」。
现状（改动前）``scene.py`` 只有两条视野附加通道：

* ``ccobject``   —— CC 攻城器械，rid 10001 起（``CC_RID_BASE``）；
* ``tutorial``   —— 教学引导物件，rid 20001 起（``GUIDE_RID_BASE``）。

**关卡自己的 NPC**（训练场陪练「赵虎 / 赵豹」、城镇 NPC、刷怪组单位）没有通道 ——
它们既不是器械也不是引导装饰，而是**带模型的 actor**，必须走 actor 视野对象。
（``contracts.enemyVision()`` 那条写死占位敌人被停用后，地图里就再没有任何 NPC 了。）

与既有模块的分工（完全对称，别混）
----------------------------------
* ``codec/vision_flow.py`` —— 编码器（**共用**，本模块不新增编码器）：
  ``encode_fixed_local_actor_vision_add_event`` —— 实机跑通的那条 actor ADD_EVENT
  （本地玩家走的就是它；``enemyVision`` 当年也用它发过 rid=2 的占位敌人）。
* **本模块** —— 读 ``data/scene/<场景>/npc_actors.json`` → 逐条编码报文。
* ``scene.py`` —— 决定**什么时候**发（进图 / ``VISION_LIST_RSP`` 之后补发 / 按 mid 回包）。

数据格式（``npc_actors.json``）
-------------------------------
::

    {
      "scene": "xlc_map_a_camp",
      "note": "铁骑训练场陪练",
      "actors": [
        {"mid": 30001, "res_id": 500, "name": "赵虎", "camp": 2,
         "pos": [475.051, 437.252, 87.4901]}
      ]
    }

* ``pos`` = ``[x, y, z]``（``z`` 才是高度，与 ``ccobject.json`` / ``tutorial_guide.json`` 同序）。
* ``res_id`` = **actor 资源号**（写进 actor 视野对象的 ``hero_resource_id``）。
* ``mid`` 可省 —— 省了按序号自动编（``ridBase() + index``）。
* ``camp`` 可省，默认 **0（中立，不参与阵营判定）**。⚠️ 2026-10-04 改：
  原默认 2（红方）会把训练陪练与玩家（camp 1/2）判成同队 ⇒ 客户端弹
  「杀害友军」。改成 0 后陪练**完全不参与阵营判定**，既不误伤友军、也保留
  可攻击的实体（带 hp/模型的 actor）。``hp`` / ``gravity`` 可省。

⚠️ **本通道是「附加能力」**：场景没有 ``npc_actors.json`` 时行为**逐位不变**
（返回空 ⇒ 一个包都不发）。读盘 / 编码失败只记日志、不抛 —— 宁可少几个 NPC，
也不能让人进不去图（与 ``ccobject`` / ``tutorial`` 同一纪律）。

开关
----
``[npc]`` 段 ini / 环境变量 ``T7_NPC``（改 ini 后重启服务端才生效）：

    0 / off                → 不发（回到改动前）
    on / all / true / yes  → 发全部
    <N>（正整数）          → 只发前 N 个（二分定位用）

rid 命名空间
------------
NPC 用 ``NPC_RID_BASE``（默认 30000）起，**避开** 本地玩家(1) / 坐骑(21) /
CC 器械(10001..10024) / 教学引导(20001..)。同一条 ``VISION_LIST_RSP`` 里必须登记
它们的 mid，否则客户端收到报文**无处挂载 ⇒ 整包丢弃**（2026-09-20 樊城那条铁证的
同一机制；见 ``scene.visionObjectMids``）。
"""
import io
import json
import os

from . import contracts as wire
from .codec import vision_flow

# --- [npc] 段 ini 通道（与 ccobject / tutorial 完全对称） ---------------------
NPC_INI_SECTION = "npc"
NPC_INI_ENV = "T7_NPC_INI"


def _npcIniRaw():
    """读 ``level.ini`` / ``server.ini`` 的 ``[npc]`` 段。"""
    return wire.iniSection(NPC_INI_SECTION, NPC_INI_ENV)


_NPC_INI = _npcIniRaw()
_NPC_SOURCE = {}


def _npcKnobRaw(envName, key, default):
    """**环境变量 > [npc] ini > 默认**；同时记来源（自证用）。"""
    raw = os.environ.get(envName)
    if raw is not None and str(raw).strip():
        _NPC_SOURCE[key] = "env"
        return str(raw).strip(), "env"
    text = str(_NPC_INI.get(key, "")).strip()
    if text:
        _NPC_SOURCE[key] = "ini"
        return text, "ini"
    _NPC_SOURCE[key] = "default"
    return default, "default"


NPC_ENABLED = True
NPC_ENV_SWITCH = "T7_NPC"
NPC_FILE = "npc_actors.json"

# 视野 mid 基数：比教学引导(20000) 再高一档，互不相撞。
NPC_RID_BASE = 30000
NPC_RID_ENV = "T7_NPC_RID_BASE"

# NPC 的 user_id 命名空间（避开玩家 USER_ID / 旧占位敌人的 10001）。
NPC_USER_ID_BASE = 70000


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


def npcLimit():
    """本进程要发几个 NPC。``None`` = 不发。"""
    raw, _ = _npcKnobRaw(NPC_ENV_SWITCH, "actors",
                         "on" if NPC_ENABLED is True else "off")
    text = str(raw).strip().lower()
    if text in ("", "0", "off", "false", "no"):
        return None
    if text in ("on", "all", "true", "yes"):
        return 1 << 30 if NPC_ENABLED is True else None
    try:
        return max(1, int(float(text)))
    except ValueError:
        return 1 << 30 if NPC_ENABLED is True else None


def npcRidBase():
    """NPC 视野 mid 起始值。**环境变量 > [npc] rid_base > 30000**。"""
    raw, _ = _npcKnobRaw(NPC_RID_ENV, "rid_base", str(NPC_RID_BASE))
    try:
        return max(NPC_RID_BASE, int(float(raw)))
    except ValueError:
        return NPC_RID_BASE


def ridFor(index):
    """第 ``index`` 个 NPC（1 起）的视野 mid。"""
    return npcRidBase() + index


def loadScene(scene, baseDir=None):
    """读 ``data/scene/<场景>/npc_actors.json``，返回**原始** dict 列表。

    文件不存在 → ``[]``（不是错误：只有配过 NPC 的场景才有这份数据）。
    解析失败 → 抛 ``ValueError``，由调用方决定是否吞掉。
    """
    if not scene:
        return []
    root = baseDir or _sceneDir()
    path = os.path.join(root, scene, NPC_FILE)
    if not os.path.isfile(path):
        return []
    with io.open(path, encoding="utf-8") as handle:
        raw = json.load(handle)
    items = raw.get("actors")
    if not isinstance(items, list):
        raise ValueError("%s: 'actors' is not a list" % path)
    return items


def actors(scene, baseDir=None):
    """``npc_actors.json`` → ``tuple[dict, ...]``（按 limit 截断，已补默认值）。

    每项含 ``mid`` / ``res_id`` / ``name`` / ``camp`` / ``pos`` / ``hp`` /
    ``gravity`` / ``user_id`` / ``instance_id``。解析不了的条目**跳过**。
    """
    limit = npcLimit()
    if limit is None:
        return ()
    items = loadScene(scene, baseDir)
    if not items:
        return ()
    out = []
    for index, item in enumerate(items[:limit], start=1):
        try:
            res_id = int(item["res_id"])
        except (KeyError, TypeError, ValueError):
            continue
        pos = item.get("pos") or (0.0, 0.0, 0.0)
        if len(pos) != 3:
            continue
        name = item.get("name") or ("npc%d" % index)
        if isinstance(name, bytes):
            name_bytes = name
        else:
            try:
                name_bytes = str(name).encode("gbk")
            except UnicodeEncodeError:
                name_bytes = str(name).encode("utf-8")
        # 每 NPC 专属武器（可选）：json 里 "weapons": [[slot, tid, lock], ...]。
        # 不配则 None ⇒ encodeActor 退回「镜像玩家当前武器」（旧训练场行为不变）。
        raw_weapons = item.get("weapons")
        weapons = None
        if raw_weapons:
            try:
                weapons = tuple(
                    (int(e[0]), int(e[1]), int(e[2]))
                    for e in raw_weapons
                    if isinstance(e, (list, tuple)) and len(e) >= 3)
                if not weapons:
                    weapons = None
            except (TypeError, ValueError):
                weapons = None
        out.append({
            "mid": int(item.get("mid") or ridFor(index)),
            "res_id": res_id,
            "name": name_bytes,
            "camp": int(item.get("camp", 0)),
            "pos": (float(pos[0]), float(pos[1]), float(pos[2])),
            "hp": int(item.get("hp", 100)),
            "gravity": int(item.get("gravity", 0)),
            "user_id": int(item.get("user_id") or (NPC_USER_ID_BASE + index)),
            "instance_id": int(item.get("instance_id", NPC_RID_BASE + index)),
            "weapons": weapons,
        })
    return tuple(out)


def encodeActor(actor, now, weapons=None, player_camp=None):
    """把一条 NPC 编成 actor ADD_EVENT 报文。

    ``weapons`` 为 ``None`` 时镜像玩家当前手持武器（``wire.currentWeaponTid()``），
    让陪练也「带装备」。⚠️ 仅视觉：武器 tid 不影响本模块的命中/伤害模型。

    ``player_camp`` 透传玩家阵营（1/2）：若给出，则把 NPC 设成玩家的**敌对阵营**
    （1↔2 互换），让陪练成为可正常互殴的敌人、且不触发「杀害友军」。
    ⚠️ 不传时退回 ``actor["camp"]``（json 默认 0=中立）；但中立在客户端=非战斗单位，
    双向都不结算伤害，所以陪练必须是对立阵营而不是中立（见 npc_ai / scene 调用侧）。
    """
    if weapons is None:
        weapons = actor.get("weapons")   # 优先用本 NPC 的专属武器（json 配）
    if not weapons:
        tid = 0
        try:
            tid = int(wire.currentWeaponTid())
        except Exception:  # noqa: BLE001
            tid = 0
        weapons = ((1, tid, 1),) if tid else ()
    if player_camp in (1, 2):
        camp = 1 if player_camp == 2 else 2      # 玩家敌对阵营
    else:
        camp = int(actor["camp"])
    return vision_flow.encode_fixed_local_actor_vision_add_event(
        server_time_ms=now & 0xFFFFFFFFFFFFFFFF,
        actor_mid=actor["mid"],
        user_id=actor["user_id"],
        instance_id=actor["instance_id"],
        hero_resource_id=actor["res_id"],
        camp=camp,
        actor_name=actor["name"],
        position=actor["pos"],
        current_hp=actor["hp"],
        maximum_hp=actor["hp"],
        gravity=actor["gravity"],
        weapons=weapons,
    )


def visionEvents(scene, now, baseDir=None, player_camp=None):
    """返回**一组**可直接 ``flow.send(0xE, body, ...)`` 的报文（每个 NPC 一条）。

    不发时返回空 tuple（开关关掉 / 场景没数据）。加载或编码失败抛异常，
    由调用方吞掉并记日志 —— 与 ``ccobject`` / ``tutorial`` 同一纪律。
    ``player_camp`` 透传给 ``encodeActor``（让 NPC 成为玩家敌对阵营）。
    """
    return tuple(encodeActor(actor, now, player_camp=player_camp)
                 for actor in actors(scene, baseDir))


def byMid(scene, baseDir=None):
    """``{mid: actor}`` —— 按视野 mid 索引本场景 NPC。"""
    return {actor["mid"]: actor for actor in actors(scene, baseDir)}


def mids(scene, baseDir=None):
    """本场景 NPC 的 mid 列表（要登记进 ``VISION_LIST_RSP``）。"""
    try:
        return tuple(actor["mid"] for actor in actors(scene, baseDir))
    except (OSError, ValueError, KeyError, TypeError):
        return ()


def describe(scene, baseDir=None):
    """一行摘要，给日志用。不抛异常。"""
    limit = npcLimit()
    if limit is None:
        return "disabled"
    try:
        items = loadScene(scene, baseDir)
    except (OSError, ValueError) as error:
        return "load-failed " + repr(error)
    if not items:
        return "no-data"
    names = []
    for item in items[:limit]:
        raw = item.get("name")
        if isinstance(raw, bytes):
            names.append(raw.decode("gbk", "replace"))
        else:
            names.append(str(raw))
    return ("count=%d/%d [%s]"
            % (len(items[:limit]), len(items), "、".join(names)))


def npcReport():
    """一行旋钮自证，给启动行用；不抛异常。"""
    try:
        limit = npcLimit()
        if limit is None:
            value = "off"
        elif limit >= (1 << 30):
            value = "all"
        else:
            value = str(limit)
        return ("actors=%s rid_base=%d ini=%s"
                % (value, npcRidBase(),
                   NPC_INI_SECTION if _NPC_INI else "none"))
    except Exception as error:  # noqa: BLE001 —— 自证行绝不能把服务端带崩
        return "report-failed " + repr(error)


# 启动自证行（与 ``[cc]`` / ``[guide]`` 同一风格）。
print("[npc] " + npcReport(), flush=True)
