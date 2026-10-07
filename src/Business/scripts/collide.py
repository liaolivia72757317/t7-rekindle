# -*- coding: utf-8 -*-
"""建筑碰撞（``acollision.json``）的加载与判定。

和空气墙的区别
--------------
``aairwall.xml`` 是关卡边界的「navigation slice」，樊城只有 5 段，
**建筑/城墙/塔楼一条都没有** —— 所以角色直接穿过房子（用户原话「碰撞还没做好」）。
高度场也管不了这个：每格只有一个地面 Z，穿模的地方地面本来就是平的。

这里的数据是从 ``.nif`` 的**竖直三角面**抽出来的（工作区 ``t7_wall_field.py``）：
每格记录「竖直墙面从哪个高度到哪个高度」。判定就是
「角色从 p0 走到 p1，路上穿过的格子里，有没有一段区间压住脚到头顶」。
门洞底下能过 —— 那一格的墙面只在 50~56，角色 44~45.7 不压。

为什么区间要裁过（数据侧，但影响这里怎么写）
--------------------------------------------
塔楼的 .nif 有一部分顶点在地面以下（实测最低到 28.6）。不裁的话合并区间
会从 28.6 一路盖到 46，把**楼梯**整段堵死（实测 490 个楼梯格里 261 个被压住）。
所以生成器按「站立面 + 0.3 m」裁一遍，矮于 1 m 的边沿也扔掉；
楼梯格（520 个）直接不让立墙。

开关
----
默认**开启**（这是修 bug）。想关：``T7_COLLIDE=off`` ——
关掉时 ``advanceGround()`` 的写入路径与日志逐位不变。
场景没有 ``acollision.json`` 时自动不生效。
"""
import json
import os

COLLIDE_ENV_SWITCH = "T7_COLLIDE"
COLLIDE_ENABLED = os.environ.get(COLLIDE_ENV_SWITCH, "on").lower() not in (
    "0", "off", "false", "no")

# ---- 可跨过的小坎（2026-09-22 新增）------------------------------------------
# 症状：高度场换成**原版地面**（lysd 0.218 -> 0.0）后，一批「墙带顶面只比脚底高
# 几厘米到 0.35 m」的区间开始压住脚，把人挡在平地上。它们是 `world_to_cells.py`
# 按 `MIN_WALL_HEIGHT = 0.35` 本该丢掉、却被 `merge_gap = 0.30` 合并出来的矮带。
# 旧高度场之所以「不挡」，是因为它把角色放在**屋顶高度**（AABB 顶面）上，
# 脚从墙顶跨过去了 —— 那是穿模，不是通行。
#
# 判据：`hi <= z + STEP_OVER` 的区间**不参与判定**（脚能跨过去）。
# 取值依据：与生成器 `MIN_WALL_HEIGHT = 0.35` 一致；比 heightfield 的
# `stepUp = 0.85` 保守（那只管 Z，不管横向）。
# 关掉：`T7_COLLIDE_STEP_OVER=0`（或 off/false/no）→ 行为与接线前逐位相同。
COLLIDE_STEP_OVER_SWITCH = "T7_COLLIDE_STEP_OVER"
try:
    COLLIDE_STEP_OVER = float(os.environ.get(COLLIDE_STEP_OVER_SWITCH, "0.35"))
except ValueError:
    COLLIDE_STEP_OVER = 0.35
if str(os.environ.get(COLLIDE_STEP_OVER_SWITCH, "")).lower() in ("off", "false", "no"):
    COLLIDE_STEP_OVER = 0.0


def _sceneDir():
    """定位 ``data/scene``：**逐级向上找**，不能只取 ``__file__`` 上两级。

    ⚠️ 服务端会把脚本快照到 ``server/data/<会话>/revisions/t7rev_<hash>/`` 再跑，
    那条路径下没有 ``data/scene``。和 heightfield/airwall 同一个坑，同一份解法。
    """
    try:
        from . import contracts as _contracts
        for node in _contracts.walkUp():
            path = os.path.join(node, "data", "scene")
            if os.path.isdir(path):
                return path
    except (ImportError, ValueError, TypeError, AttributeError):
        pass
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "scene")


COLLIDE_DIR = _sceneDir()

_cache = {}
_stamp = {}
_noticed = set()


def _enabled():
    return COLLIDE_ENABLED is True


def _fileStamp(path):
    try:
        info = os.stat(path)
    except OSError:
        return None
    return (info.st_mtime_ns, info.st_size)


def load(scene):
    """加载 ``acollision.json``；不存在/读不到返回 ``None``（每场景只提示一次）。"""
    if not scene:
        return None
    path = os.path.join(COLLIDE_DIR, scene, "acollision.json")
    stamp = _fileStamp(path)
    if scene in _cache and _stamp.get(scene) == stamp:
        return _cache[scene]
    data = None
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
        w = int(raw["w"])
        h = int(raw["h"])
        cells = {}
        for i, j, bands in raw["cells"]:
            cells[int(j) * w + int(i)] = [(float(a), float(b)) for a, b in bands]
        data = {"cell": float(raw["cell"]), "x0": float(raw["x0"]),
                "y0": float(raw["y0"]), "w": w, "h": h,
                "head": float(raw.get("head", 1.7)), "cells": cells}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        if scene not in _noticed:
            _noticed.add(scene)
            print("[collide] scene=" + str(scene) + " unavailable: " + str(exc))
        data = None
    _cache[scene] = data
    _stamp[scene] = stamp
    return data


def _hits(grid, i, j, z):
    k = j * grid["w"] + i
    bands = grid["cells"].get(k)
    if not bands:
        return None
    top = z + grid["head"]
    step_over = COLLIDE_STEP_OVER
    for lo, hi in bands:
        if step_over > 0.0 and hi <= z + step_over:
            continue                       # 脚能跨过去的小坎，不算障碍
        if lo <= top and hi >= z:
            return (k, lo, hi)
    return None


def blocked(scene, p0, p1, z):
    """这一步会不会撞进建筑。返回 ``(格号, 区间下沿, 区间上沿)``，不撞返回 ``None``。

    沿走这一小步最多 1 米来采样，步长取半格 —— 服务端每 tick 的位移比格小，
    所以采样不会漏格（不做连续 AABB/线段求交，那需要墙的朝向，数据里没有）。
    """
    if not _enabled():
        return None
    grid = load(scene)
    if grid is None or z is None:
        return None
    cell = grid["cell"]
    dx = p1[0] - p0[0]
    dy = p1[1] - p0[1]
    dist = (dx * dx + dy * dy) ** 0.5
    steps = max(1, int(dist / (cell * 0.5)) + 1)
    for s in range(1, steps + 1):
        t = s / float(steps)
        i = int((p0[0] + dx * t - grid["x0"]) / cell)
        j = int((p0[1] + dy * t - grid["y0"]) / cell)
        if i < 0 or j < 0 or i >= grid["w"] or j >= grid["h"]:
            continue
        hit = _hits(grid, i, j, z)
        if hit is not None:
            return hit
    return None


def cellIndex(scene, x, y):
    """世界坐标 → 1 m 格号。**只为日志里那个 ``cell=`` 有值可读**，判定不依赖它。

    精确几何判定（``collide_mesh``）没有「格」的概念，但它要复用
    ``move-blocked-model`` 这条日志，而下游 ``replay_stuck_points.py`` /
    ``hf_stepover_ab.py`` 的正则是 ``cell=(\\d+)`` —— 所以几何模式也填一个格号。
    """
    grid = load(scene)
    if grid is None:
        return -1
    cell = grid["cell"]
    i = int((x - grid["x0"]) / cell)
    j = int((y - grid["y0"]) / cell)
    return j * grid["w"] + i


def stats(scene):
    grid = load(scene)
    if grid is None:
        return "无数据"
    return "%d 格墙  cell=%.2f  网格 %dx%d" % (len(grid["cells"]), grid["cell"],
                                              grid["w"], grid["h"])
