# -*- coding: utf-8 -*-
"""精确几何碰撞（``acollision_mesh.bin``）—— 替代 1 m 栅格的判定。

它解决什么问题
--------------
``acollision.json`` 是**足迹（footprint）口径**：三角面的 XY 投影碰到哪格，
哪格就记成墙。真实角色碰撞是**求交（intersection）口径**：身体胶囊 vs 三角面。

对**竖直薄墙**两者几乎一致；对**斜坡/陡坎**，足迹是一条宽带 → 整条带被堵死
（实机症状：上层台地走不上去、站在坡脚被挡）。而且格边界上必须同时标两侧，
**固有误差 = 半格对角 ≈ 0.71 m，加密只能减小、不能消除**。

本模块改成**真的算身体竖段到三角面的最短距离**，``< radius`` 才算碰到。

判定规则（**刻意与 ``collide._hits`` 对齐，只改「足迹 → 距离」这一件事**）
--------------------------------------------------------------------
- 身体竖段 = ``[z, z + head]``（与 ``collide`` 同口径）。
- 只有 ``|nz| <= wall_abs_nz`` 的面算「墙」（与 ``world_to_cells.py`` 同阈值
  0.35）—— 不够陡的交给高度场。**唯一变量就是判定口径**，收益可归因。
- 面在身体高度范围内必须高出 ``z + step_tol`` 才算障碍（矮坎可迈过）。

开关
----
``T7_COLLIDE_MESH``：``on``（默认）/ ``off``。关掉时 ``blocked()`` 恒返回 ``None``。
场景没有 ``acollision_mesh.bin`` 时自动不生效（与 ``collide.py`` 同约定）。

⚠️ **本模块默认没有被 ``controls.py`` 调用** —— 接线是单独一步，见
``hkx_decode/精确几何碰撞落地_20260922.md``。
"""
import array
import math
import os
import struct

MESH_ENV_SWITCH = "T7_COLLIDE_MESH"
MESH_ENABLED = os.environ.get(MESH_ENV_SWITCH, "on").lower() not in (
    "0", "off", "false", "no")
# 数据目录覆盖（联调/离线验证用）：指向 hkx_decode/out 就能拿未落盘的数据试跑。
MESH_DIR_ENV = "T7_COLLIDE_MESH_DIR"

MAGIC = b"T7MESH1\n"
HEADER_FMT = "<8sIIII6d6d4I"
HEADER_SIZE = struct.calcsize(HEADER_FMT)
TRI_FIELDS = 16
TRI_BYTES = TRI_FIELDS * 4          # float32
BUCKET_FMT = "<iiII"
BUCKET_SIZE = struct.calcsize(BUCKET_FMT)
STEP = 0.25                         # 沿位移采样步长（m）


def _sceneDir():
    """定位 ``data/scene``：**逐级向上找**（与 collide/heightfield/airwall 同坑同解）。

    服务端会把脚本快照到 ``data/<会话>/revisions/t7rev_<hash>/`` 再跑，
    那条路径下没有 ``data/scene``。
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


MESH_DIR = os.environ.get(MESH_DIR_ENV) or _sceneDir()

_cache = {}
_stamp = {}
_noticed = set()


def _enabled():
    return MESH_ENABLED is True


def _fileStamp(path):
    try:
        info = os.stat(path)
    except OSError:
        return None
    return (info.st_mtime_ns, info.st_size)


def meshPath(scene):
    """数据文件路径。两种布局都认：

    1. 生产布局 ``<MESH_DIR>/<场景>/acollision_mesh.bin``
    2. 逆向工作目录的平铺布局 ``<MESH_DIR>/<场景>_mesh.bin``
       （``hkx_decode/out/`` 就是这种，方便拿未落盘的数据联调）
    """
    nested = os.path.join(MESH_DIR, scene, "acollision_mesh.bin")
    if os.path.isfile(nested):
        return nested
    flat = os.path.join(MESH_DIR, scene + "_mesh.bin")
    if os.path.isfile(flat):
        return flat
    return nested                    # 都不在 → 返回生产路径，报错信息才有意义


def load(scene):
    """加载 ``acollision_mesh.bin``；不存在/读不到返回 ``None``（每场景只提示一次）。"""
    if not scene:
        return None
    path = meshPath(scene)
    stamp = _fileStamp(path)
    if scene in _cache and _stamp.get(scene) == stamp:
        return _cache[scene]
    data = None
    try:
        data = _read(path)
    except (OSError, ValueError, struct.error) as exc:
        if scene not in _noticed:
            _noticed.add(scene)
            print("[collide_mesh] scene=" + str(scene) + " unavailable: " + str(exc))
        data = None
    _cache[scene] = data
    _stamp[scene] = stamp
    return data


def _read(path):
    with open(path, "rb") as handle:
        blob = handle.read()
    if len(blob) < HEADER_SIZE:
        raise ValueError("mesh file too short")
    (magic, ntri, nbuckets, nidx, _flags,
     binSize, radius, head, stepTol, wallAbsNz, _resv,
     x0, y0, z0, x1, y1, z1, _a, _b, _c, _d) = struct.unpack_from(HEADER_FMT, blob, 0)
    if magic != MAGIC:
        raise ValueError("bad mesh magic: %r" % (magic,))
    off = HEADER_SIZE
    need = off + ntri * TRI_BYTES
    if len(blob) < need:
        raise ValueError("mesh truncated at triangle table")
    tris = array.array("f")
    tris.frombytes(blob[off:need])          # 本机小端（x86）—— 与写出端一致
    off = need
    buckets = {}
    for _ in range(nbuckets):
        i, j, start, count = struct.unpack_from(BUCKET_FMT, blob, off)
        off += BUCKET_SIZE
        buckets[(i, j)] = (start, count)
    need = off + nidx * 4
    if len(blob) < need:
        raise ValueError("mesh truncated at index table")
    idx = array.array("I")
    idx.frombytes(blob[off:need])
    return {"ntri": ntri, "tris": tris, "buckets": buckets, "idx": idx,
            "bin": binSize, "pad": max(1, int(math.ceil(radius / binSize))),
            "radius": radius, "head": head, "stepTol": stepTol,
            "wallAbsNz": wallAbsNz,
            "bbox": (x0, y0, z0, x1, y1, z1)}


def _near(g, x, y):
    """取 XY 包围盒可能落在 ``radius`` 内的候选面（返回三角面在 ``tris`` 里的基址）。"""
    tris, idx, bk = g["tris"], g["idx"], g["buckets"]
    b, pad, r2 = g["bin"], g["pad"], g["radius"] * g["radius"]
    ci, cj = int(math.floor(x / b)), int(math.floor(y / b))
    out = []
    for i in range(ci - pad, ci + pad + 1):
        for j in range(cj - pad, cj + pad + 1):
            got = bk.get((i, j))
            if got is None:
                continue
            start, count = got
            for k in range(start, start + count):
                base = idx[k] * TRI_FIELDS
                xlo = tris[base + 10]
                xhi = tris[base + 11]
                ylo = tris[base + 12]
                yhi = tris[base + 13]
                dx = xlo - x if x < xlo else (x - xhi if x > xhi else 0.0)
                dy = ylo - y if y < ylo else (y - yhi if y > yhi else 0.0)
                if dx * dx + dy * dy <= r2:
                    out.append(base)
    return out


def _closestOnTri(tris, base, px, py, pz):
    """点到三角形最近点（Ericson §5.1.5）。``base`` 是三角面在 ``tris`` 里的下标。"""
    ax, ay, az = tris[base], tris[base + 1], tris[base + 2]
    bx, by, bz = tris[base + 3], tris[base + 4], tris[base + 5]
    cx, cy, cz = tris[base + 6], tris[base + 7], tris[base + 8]
    abx, aby, abz = bx - ax, by - ay, bz - az
    acx, acy, acz = cx - ax, cy - ay, cz - az
    apx, apy, apz = px - ax, py - ay, pz - az
    d1 = abx * apx + aby * apy + abz * apz
    d2 = acx * apx + acy * apy + acz * apz
    if d1 <= 0.0 and d2 <= 0.0:
        return ax, ay, az
    bpx, bpy, bpz = px - bx, py - by, pz - bz
    d3 = abx * bpx + aby * bpy + abz * bpz
    d4 = acx * bpx + acy * bpy + acz * bpz
    if d3 >= 0.0 and d4 <= d3:
        return bx, by, bz
    vc = d1 * d4 - d3 * d2
    if vc <= 0.0 and d1 >= 0.0 and d3 <= 0.0:
        v = d1 / (d1 - d3)
        return ax + abx * v, ay + aby * v, az + abz * v
    cpx, cpy, cpz = px - cx, py - cy, pz - cz
    d5 = abx * cpx + aby * cpy + abz * cpz
    d6 = acx * cpx + acy * cpy + acz * cpz
    if d6 >= 0.0 and d5 <= d6:
        return cx, cy, cz
    vb = d5 * d2 - d1 * d6
    if vb <= 0.0 and d2 >= 0.0 and d6 <= 0.0:
        w = d2 / (d2 - d6)
        return ax + acx * w, ay + acy * w, az + acz * w
    va = d3 * d6 - d5 * d4
    if va <= 0.0 and (d4 - d3) >= 0.0 and (d5 - d6) >= 0.0:
        w = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        return bx + (cx - bx) * w, by + (cy - by) * w, bz + (cz - bz) * w
    denom = 1.0 / (va + vb + vc)
    v, w = vb * denom, vc * denom
    return ax + abx * v + acx * w, ay + aby * v + acy * w, az + abz * v + acz * w


def _bodyDist(g, base, x, y, z):
    """身体竖段 ``[z, z+head]`` 到该三角面的最短距离 + 最近点 z。

    ⚠️ 「算不算障碍」**不在这里判**，用 ``tri[15]``（面顶）在 ``testPoint`` 里判。
    用「接触点 z 高不高」太脆：竖墙的最低采样点恰好等于脚底高，
    ``接触点z > 脚底`` 会判成**不挡**（踩过，漏墙率 8%）。
    """
    tris = g["tris"]
    zlo, zhi = tris[base + 14], tris[base + 15]
    top = z + g["head"]
    if zhi < z or zlo > top:
        return None
    best = float("inf")
    bestZ = 0.0
    n = 17                       # 0.1 m 一步
    for s in range(n + 1):
        tz = z + g["head"] * s / float(n)
        if tz < zlo - best or tz > zhi + best:
            continue
        cpx, cpy, cpz = _closestOnTri(tris, base, x, y, tz)
        d = math.sqrt((cpx - x) ** 2 + (cpy - y) ** 2 + (cpz - tz) ** 2)
        if d < best:
            best = d
            bestZ = cpz
            if best <= 1e-9:
                break
    return (best, bestZ)


def testPoint(scene, x, y, z):
    """站在 ``(x, y, z)`` 会不会撞墙。返回 ``(距离, 接触点z)`` 或 ``None``。"""
    if not _enabled():
        return None
    g = load(scene)
    if g is None or z is None:
        return None
    tris = g["tris"]
    for base in _near(g, x, y):
        if tris[base + 15] <= z + g["stepTol"]:
            continue                      # 面顶不超过「可迈过」的高度 → 矮坎，放行
        got = _bodyDist(g, base, x, y, z)
        if got is not None and got[0] < g["radius"]:
            return got
    return None


def blocked(scene, p0, p1, z):
    """从 ``p0`` 走到 ``p1``（z 不变）会不会撞墙。

    与 ``collide.blocked`` **同签名同返回**：撞了返回 ``(距离, 接触点z)``，
    没撞返回 ``None``。沿位移按 ``STEP`` 采样（服务端每 tick 位移比这小得多）。
    """
    if not _enabled():
        return None
    g = load(scene)
    if g is None or z is None:
        return None
    dx = p1[0] - p0[0]
    dy = p1[1] - p0[1]
    dist = math.sqrt(dx * dx + dy * dy)
    steps = max(1, int(dist / STEP) + 1)
    for s in range(1, steps + 1):
        t = s / float(steps)
        got = testPoint(scene, p0[0] + dx * t, p0[1] + dy * t, z)
        if got is not None:
            return got
    return None


def stats(scene):
    g = load(scene)
    if g is None:
        return "无数据"
    return ("%d 面  %d 桶  bin=%.1f  radius=%.2f  head=%.2f  stepTol=%.2f"
            % (g["ntri"], len(g["buckets"]), g["bin"], g["radius"],
               g["head"], g["stepTol"]))


# ---------------------------------------------------------------------------
# 可站立面（多层地面）—— 治「楼梯上不去」和「站进地下室里」的那一半
# ---------------------------------------------------------------------------
FLOOR_ENV_SWITCH = "T7_COLLIDE_FLOOR"
FLOOR_ENABLED = os.environ.get(FLOOR_ENV_SWITCH, "on").lower() not in (
    "0", "off", "false", "no")
FLOOR_NAME = "acollision_floor.bin"
STEP_UP_SWITCH = "T7_COLLIDE_STEP_UP"
STEP_UP = float(os.environ.get(STEP_UP_SWITCH, "0.35"))
# ⚠️ 下行窗口要**小**：几何地面整体比高度场低约 0.2 m（PAMH 是格心采样），
# 给到 3 m 的话，一旦角色脚下那片可站立面没匹配上（三角形缝隙/量化），
# 就会在 3 m 内捞一个更低的面当支撑 —— 实测 151 个真实站位凭空下沉 >0.3 m，
# 最深 2.57 m（从平台上掉到地面）。收窄到这个值之后，捞不到就返回 None，
# 调用方退回高度场，行为与改动前逐位相同；真下台阶每级 0.19 m，够用。
GROUND_DROP = float(os.environ.get("T7_GROUND_DROP", "0.60"))

_floorCache = {}
_floorStamp = {}
_floorNoticed = set()


def floorPath(scene):
    """地面数据路径。与 ``meshPath`` 同样认两种布局。"""
    nested = os.path.join(MESH_DIR, scene, FLOOR_NAME)
    if os.path.isfile(nested):
        return nested
    flat = os.path.join(MESH_DIR, scene + "_floor.bin")
    if os.path.isfile(flat):
        return flat
    return nested


def loadFloor(scene):
    """加载 ``acollision_floor.bin``（可站立面）；开关关/文件没有 → ``None``。"""
    if not scene or not FLOOR_ENABLED:
        return None
    path = floorPath(scene)
    stamp = _fileStamp(path)
    if scene in _floorCache and _floorStamp.get(scene) == stamp:
        return _floorCache[scene]
    data = None
    if stamp is None:
        # 没这份数据是常态（只有编过 floor 的场景才有）—— 不刷屏、不记为异常
        _floorCache[scene] = None
        _floorStamp[scene] = None
        return None
    try:
        data = _read(path)
    except (OSError, ValueError, struct.error) as exc:
        if scene not in _floorNoticed:
            _floorNoticed.add(scene)
            print("[collide_mesh] floor scene=" + str(scene)
                  + " unavailable: " + str(exc))
        data = None
    if data is not None and scene not in _floorNoticed:
        # 首次加载/热重载成功时报一行：实机「楼梯没变化」时先确认这行有没有。
        _floorNoticed.add(scene)
        print("[collide_mesh] floor scene=" + str(scene) + " loaded: " + floorStats(scene))
    _floorCache[scene] = data
    _floorStamp[scene] = stamp
    return data


def _binAt(g, x, y):
    """点所在桶的面 ``base`` 列表。桶是按面的 bbox 登记的，查所在那一格就够。"""
    i = int(math.floor(x / g["bin"]))
    j = int(math.floor(y / g["bin"]))
    span = g["buckets"].get((i, j))
    if not span:
        return ()
    start, count = span
    idx = g["idx"]
    return [idx[k] * TRI_FIELDS for k in range(start, start + count)]


def _surfZ(tris, base, x, y):
    """``(x, y)`` 落在三角形内 → 该处的面 z（重心插值）；落在外面返回 ``None``。"""
    ax, ay = tris[base], tris[base + 1]
    bx, by = tris[base + 3], tris[base + 4]
    cx, cy = tris[base + 6], tris[base + 7]
    det = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay)
    if abs(det) < 1e-9:
        return None
    px, py = x - ax, y - ay
    v = ((cy - ay) * px + (ax - cx) * py) / det
    w = ((ay - by) * px + (bx - ax) * py) / det
    if v < -1e-6 or w < -1e-6 or v + w > 1.0 + 1e-6:
        return None
    return tris[base + 2] + (tris[base + 5] - tris[base + 2]) * v \
        + (tris[base + 8] - tris[base + 2]) * w


def supportZ(scene, x, y, zref, up=None, drop=None):
    """``(x, y)`` 处**能站的那一层**有多高。

    高度场每个格子只有一个 z（``heightfield.json`` 的 ``height[j*w+i]`` 是标量），
    楼梯、平台、地下室这类第二层它根本表达不出来 —— 角色 z 永远停在最下面那层，
    于是「迈过 0.35 的豁免」用完第二级台阶就再也上不去了。
    这里从几何里取真实可站立面：只考虑 ``[zref-drop, zref+up]`` 这一段，取最高的。

    返回 ``None`` = 这里没有可站立面（数据没编 / 真的悬空），调用方**保持原 z 不动**。
    """
    g = loadFloor(scene)
    if g is None or zref is None:
        return None
    up = STEP_UP if up is None else up
    drop = GROUND_DROP if drop is None else drop
    tris = g["tris"]
    best = None
    for base in _binAt(g, x, y):
        z = _surfZ(tris, base, x, y)
        if z is None or z > zref + up or z < zref - drop:
            continue
        if best is None or z > best:
            best = z
    return best


def floorStats(scene):
    g = loadFloor(scene)
    if g is None:
        return "无地面数据"
    return ("%d 可站立面  %d 桶  |nz|>=%.2f  stepUp=%.2f drop=%.2f"
            % (g["ntri"], len(g["buckets"]), g["wallAbsNz"], STEP_UP, GROUND_DROP))


if __name__ == "__main__":
    import sys
    import time
    scene = sys.argv[1] if len(sys.argv) > 1 else "hz_map_b"
    print("mesh dir: " + MESH_DIR)
    t0 = time.time()
    g = load(scene)
    print("加载 %.2f s | %s" % (time.time() - t0, stats(scene)))
    if g is None:
        sys.exit(1)
    bx0, by0, bz0, bx1, by1, bz1 = g["bbox"]
    print("bbox x[%.1f..%.1f] y[%.1f..%.1f] z[%.1f..%.1f]"
          % (bx0, bx1, by0, by1, bz0, bz1))
    # 拿场景出生点做冒烟
    try:
        from . import contracts as _c
        spawn = _c.SPAWN_BY_LEVEL.get(10005)
    except Exception:                                       # noqa: BLE001
        spawn = None
    if spawn:
        sx, sy = spawn[0], spawn[1]
        t0 = time.time()
        for _ in range(200):
            testPoint(scene, sx, sy, spawn[2])
        print("出生点 200 次 testPoint: %.3f ms/次"
              % ((time.time() - t0) / 200.0 * 1000.0))
    print("自检 OK")
