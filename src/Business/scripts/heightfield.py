# -*- coding: utf-8 -*-
"""可站立高度场（``heightfield.json``）的加载与查询。

为什么需要它
------------
``controls.advanceGround()`` 的 ``delta[2]`` 硬编码 ``0.0``，角色的 Z **永远停在
spawn 值**。走出出生点就会：

* 地形起伏的地方 → 陷进地里（用户原话「往前走在地下」）
* 城墙/楼梯 → 完全上不去（用户原话「我要上梯子」）

数据来源（2026-09-19 定论）
--------------------------
**用的是 ``amodellist.dat``，不是 PAMH。** 理由见 SKILL.md，摘要：

* ``aheightmap.dat``(PAMH) 只有 33×33 ≈ **每格 9 米**，城墙/楼梯 2~5 米宽根本
  表示不出来；而且世界 XY→索引的 AABB 映射解不出来（tszz 用真实出生点拟合
  **0 个候选**）。
* ``amodellist.dat`` 是场景资产全表，樊城 **778 个资产带世界坐标**，分层清晰：

  | 层        | z           | 代表模型                          |
  |-----------|-------------|-----------------------------------|
  | 真地面    | 43.25       | spawn 值；PAMH 层0 = 41.2~43.9    |
  | 城墙走道  | 44.3~44.5   | ``tszz_gallet_01/02`` ×169        |
  | 楼梯下段  | 37~44.6     | ``tszz_stair_02/04/06``           |
  | 楼梯上段  | 55.3~55.7   | ``tszz_stair_05`` ×19             |
  | 城墙上层  | 55.3~56.7   | ``tower_04b/05``, ``arms``, ``flag`` |

构建脚本：工作区 ``t7_heightfield_build.py``（含可达性 flood fill，保证「玩家
真能走到」的高度才被采纳，避免城内被城墙顶高度污染）。

文件格式
--------
``server/data/scene/<场景>/heightfield.json``::

    {"scene": "tszz", "cell": 1.0, "x0": .., "y0": .., "w": W, "h": H,
     "groundBase": 43.2482,          # 该关地面基准（= spawn Z）
     "height": [z 或 null, ...],     # 只存**非地面**格；null = 无资产
     "ground": [z, ...]}             # 可选：地面插值面（w*h），替代常数 groundBase

⚠️ 2026-09-19 深夜加 ``ground`` 的原因
--------------------------------------
``height`` 里 **97.5% 是 null**，以前一律返回常数 ``groundBase``。但实测
樊城贴地格是 43.278~45.150，**2725/2725 全部高于 groundBase**。原因是
``groundBase`` 取的是**出生点**（城门外低处）的地面，而城内铺装
（``tszz_gallet_01/02`` ×169，44.3~44.5）比它高 **1.0~1.5 m**。

→ 角色从出生点走进城，Z 还钉在 43.2482，就**沉进地面 1.3 m**。
   用户原话：「**还是卡地下，只有出生点正常**」。

所以：``height`` 有值 → 用 ``height``（建筑/城墙/楼梯，不动）；
``height`` 是 null → 用 ``ground[i]``（局部地面插值）代替常数 base。
**``ground`` 键不存在时行为逐位不变** —— 没生成过这层数据的场景不受影响。

生成器：工作区 ``t7_ground_fill.py``（干跑看数据，``--write`` 落盘）。

开关
----
默认**开启**（这是修 bug，不是加特性）。想关：``T7_HEIGHTFIELD=off``。
没找到 heightfield.json 的场景自动不生效（逐位不变）。
"""
import json
import os

def _sceneDir():
    """定位 ``data/scene``：**逐级向上找**，不能只取 ``__file__`` 上两级。

    ⚠️ 2026-09-19 实机翻车：服务端会把脚本快照到
    ``server/data/<会话>/revisions/t7rev_<hash>/`` 再跑，那条路径下没有
    ``data/scene``，于是 ``load()`` 恒为 None、高度跟随全程不生效
    （玩家走出出生点就沉地下，``ground-z-none loaded=False``）。
    """
    try:
        from . import contracts as _contracts
        for node in _contracts.walkUp():
            path = os.path.join(node, "data", "scene")
            if os.path.isdir(path):
                return path
    except (ImportError, ValueError, TypeError):
        pass
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "scene")


HEIGHTFIELD_DIR = _sceneDir()

HEIGHTFIELD_ENV_SWITCH = "T7_HEIGHTFIELD"

# ⚠️ 与 AIRWALL_COLLISION_ENABLED 不同，这里默认**开启**：
#    Z 恒定是「沉地下」的直接原因，关掉等于没修。
#    关掉时 advanceGround() 的写入路径与接线前逐位相同（t7_wasd_verify 是回归门）。
HEIGHTFIELD_ENABLED = os.environ.get(HEIGHTFIELD_ENV_SWITCH, "on").lower() not in (
    "0", "off", "false", "no")

_cache = {}
_cache_stamp = {}
_noticed = set()


def _enabled():
    return HEIGHTFIELD_ENABLED is True


def _fileStamp(path):
    """``(mtime_ns, size)``；文件不存在/读不到 → ``None``。

    ⚠️ 2026-09-20 加。``_cache`` 以前**永不过期**：磁盘上重写了
    ``heightfield.json``（补格 855 格城墙），内存里还是旧数据，
    实机表现就是「补了墙还是穿墙」，而日志里 ``climb-blocked`` 恒为 0。
    服务端的**脚本**会热重载，但**数据文件不会** —— 只能自己比对。
    """
    try:
        info = os.stat(path)
    except OSError:
        return None
    return (info.st_mtime_ns, info.st_size)


def load(scene):
    """加载场景高度场；失败/不存在返回 ``None``（只提示一次）。

    文件被重写（mtime/大小变化）时**自动重新加载** —— 见 ``_fileStamp``。
    文件没变时仍走缓存，行为与加这个检查之前逐位相同。
    """
    if not scene:
        return None
    path = os.path.join(HEIGHTFIELD_DIR, scene, "heightfield.json")
    stamp = _fileStamp(path)
    if scene in _cache and _cache_stamp.get(scene) == stamp:
        return _cache[scene]
    data = None
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
        w = int(raw["w"])
        h = int(raw["h"])
        if w > 0 and h > 0 and len(raw["height"]) == w * h:
            ground = raw.get("ground")
            if not (isinstance(ground, list) and len(ground) == w * h):
                # ⚠️ 缺这一层时退回常数 groundBase —— 老文件逐位不变
                ground = None
            data = {
                "cell": float(raw["cell"]),
                "x0": float(raw["x0"]),
                "y0": float(raw["y0"]),
                "w": w,
                "h": h,
                "base": float(raw["groundBase"]),
                "height": raw["height"],
                "ground": ground,
            }
    except (OSError, ValueError, KeyError, TypeError) as exc:
        if scene not in _noticed:
            _noticed.add(scene)
            print("[heightfield] scene=" + str(scene) + " unavailable: " + str(exc))
        data = None
    _cache[scene] = data
    _cache_stamp[scene] = stamp
    return data


def groundZ(scene, x, y):
    """查询 ``(x, y)`` 的可站立高度。

    返回 ``None`` 表示**不该由本模块决定 Z**（开关关 / 场景没数据 / 在网格外）
    —— 调用方必须保持原值不动，否则会破坏「逐位不变」的回归底线。
    """
    if not _enabled():
        return None
    field = load(scene)
    if field is None:
        return None
    cell = field["cell"]
    i = int((x - field["x0"]) / cell)
    j = int((y - field["y0"]) / cell)
    if i < 0 or j < 0 or i >= field["w"] or j >= field["h"]:
        return None
    z = field["height"][j * field["w"] + i]
    if z is None:
        # 有地面插值面就用它（比一刀切的常数准 1.0~1.5 m）；没有就退回常数。
        ground = field.get("ground")
        if ground is not None:
            return float(ground[j * field["w"] + i])
        return field["base"]
    return float(z)
