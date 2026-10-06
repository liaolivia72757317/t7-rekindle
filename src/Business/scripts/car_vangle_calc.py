# -*- coding: utf-8 -*-
"""从场景数据反推**攻城车架梯俯仰角**（``car_pose_vangle``）—— 不用再一档一档试。

⭐ 公式（与云梯 ``θ = asin(墙高 / 梯长)`` **同一套「用地图数据 + 公式」**）：

    θ = atan2(墙顶 z − 车 z , 车到墙的**水平**距离)

即「把梯子**对准车正前方的城墙顶**」⇒ 梯子不再扎进墙里、末端正好落在墙头。

为什么攻城车不能照抄云梯那条 ``asin(Δz/梯长)``：
  * 云梯的**梯长已知**（``LADDER_LENGTH = 16.008``，来自模型/常量），
    所以 ``asin(Δz/L)`` 能让梯顶**恰好**搭在墙上。
  * 攻城车的**梯长不在任何数据里** —— ``ccobject.json`` 的 ``kv`` 只有动画时长
    （``animation_time_ms`` / ``total_animation_time_ms``），
    ``propsheet/攻城器械.psheet`` 的「攻城器械.攻城车」行**几何全是 0**。
  ⇒ 只能「对准墙顶」：``atan2(Δz, Δh)``（梯长够长时梯顶就落在墙顶）。

数据源（**全部来自场景数据，不写死**）：
  * ``data/scene/<场景>/ccobject.json``
      车停位 = 该车 ``path.pos`` 的**终点**；航向 = ``path`` 末段 ``atan2(dy, dx)``
  * ``data/scene/<场景>/acollision.json``
      竖直墙面格（``note``：「竖直面 nz<=0.35；世界三角面 45580 面 → 墙面格 18177」）
      每格 ``[i, j, [[z_lo, z_hi], ...]]``，世界坐标 ``x = x0 + i*cell``、``y = y0 + j*cell``
      ⚠️ **必须用 acollision.json，不能用 heightfield.json** ——
         后者是「可行走地面」，城墙是**竖直岩壁**，格子里根本没有（已踩过）。
  * 取「**航向 ±60°、80m 内最近**」且「**段顶 ≥ 50**」的格子（矮坎 / 台阶不算城墙）。

实测（樊城 tszz，车 rid 10007）：
    停位 (536.82, 589.0, 44.12)、航向 −9.66° ⇒ 正前方 10.6m 处墙顶 z = 57.85
    ⇒ Δz = +13.73 ⇒ **θ = atan2(13.73, 10.6) = 52.3°**
    （此前手填 15° 差得远 ⇒ 正是用户说的「角度不够」。）

用法：
    python car_vangle_calc.py            # 当前关卡（读 level.ini 的 level id）
    python car_vangle_calc.py tszz       # 指定场景
    python car_vangle_calc.py --all      # 全量扫描
"""
import io
import json
import math
import os
import re
import sys

SERVER = r"D:\流星\T7\server"
SCENE_DIR = os.path.join(SERVER, "data", "scene")
INI = os.path.join(SERVER, "level.ini")

CAR_TID = 2                    # 攻城车（propsheet 器械类型 = 2）
WALL_MIN_TOP = 50.0            # 墙面段顶 ≥ 此值才算「城墙」
MAX_RANGE = 80.0               # 只在 80 m 内找
CONE_DEG = 60.0                # 只在「航向 ± 此角」的扇形里找
FALLBACK_DEG = 15.0            # 服务端算不出来时的兜底（要与 siege.py 一致）

ID_TO_SCENE_FALLBACK = {
    "10002": "tszz",       # 樊城（攻城）
    "10020": "yjc_low",    # 江陵城（攻城）
    "10062": "hz_map_b",   # 玉门关外（会战）
    "10036": "lysd",       # 洛阳死斗
    "10005": "lysd",       # 洛阳死斗（团队模式，同图）
    "10085": "pve_gc",     # 宛城之战教学
}


def scene_map():
    """level_id(字符串) -> 场景目录名。优先读服务端 contracts.py。"""
    import ast
    p = os.path.join(SERVER, "scripts", "contracts.py")
    try:
        tree = ast.parse(io.open(p, encoding="utf-8", errors="replace").read())
    except Exception:
        return dict(ID_TO_SCENE_FALLBACK)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "SCENE_BY_LEVEL":
                    try:
                        return {str(k.value): v.value
                                for k, v in zip(node.value.keys, node.value.values)}
                    except Exception:
                        pass
    return dict(ID_TO_SCENE_FALLBACK)


ID_TO_SCENE = scene_map()


def current_scene():
    """从 level.ini 的 ``[level]`` 段读 ``id=``，再映射成场景目录名。"""
    scene = None
    in_level = False
    for line in io.open(INI, "r", encoding="utf-8-sig", errors="replace"):
        s = line.strip()
        if s.startswith("["):
            in_level = (s == "[level]")
            continue
        if not in_level or s.startswith(";") or s.startswith("#"):
            continue
        m = re.match(r"id\s*=\s*(\S+)", s)
        if m:
            scene = m.group(1)
            break
    if scene is None:
        return None
    return ID_TO_SCENE.get(scene, scene)


def norm180(a):
    while a > 180.0:
        a -= 360.0
    while a <= -180.0:
        a += 360.0
    return a


def loadWalls(scene):
    """→ (meta, walls)；没有 acollision.json ⇒ (None, {})。"""
    p = os.path.join(SCENE_DIR, scene, "acollision.json")
    if not os.path.exists(p):
        return None, {}
    doc = json.load(io.open(p, encoding="utf-8-sig"))
    meta = {"cell": float(doc.get("cell") or 1.0),
            "x0": float(doc.get("x0") or 0.0),
            "y0": float(doc.get("y0") or 0.0)}
    walls = {}
    for i, j, spans in doc.get("cells") or ():
        tops = [(float(lo), float(hi)) for lo, hi in spans
                if float(hi) >= WALL_MIN_TOP]
        if tops:
            walls[(int(i), int(j))] = (max(t for _l, t in tops),
                                       min(l for l, _t in tops))
    return meta, walls


def cars(scene):
    p = os.path.join(SCENE_DIR, scene, "ccobject.json")
    if not os.path.exists(p):
        return []
    doc = json.load(io.open(p, encoding="utf-8-sig"))
    return [o for o in doc.get("objects") or ()
            if isinstance(o, dict) and int(o.get("tid", -1)) == CAR_TID]


def calcCar(item, meta, walls):
    """→ (θ度, 详情 dict) 或 (None, 原因字符串)。"""
    path = item.get("path") or {}
    pts = path.get("pos") or []
    if not pts:
        return None, "没有 path"
    stop = pts[-1]
    if len(pts) >= 2:
        heading = math.atan2(pts[-1][1] - pts[-2][1], pts[-1][0] - pts[-2][0])
    else:
        heading = float((item.get("face") or (0.0, 0.0, 0.0))[2])
    if meta is None or not walls:
        return None, "没有 acollision.json（或里面没有城墙格）"
    cell = meta["cell"]
    best = None
    for (i, j), (top, bottom) in walls.items():
        x = meta["x0"] + i * cell
        y = meta["y0"] + j * cell
        dx, dy = x - stop[0], y - stop[1]
        dist = math.hypot(dx, dy)
        if dist < 1e-6 or dist > MAX_RANGE:
            continue
        az = math.degrees(math.atan2(dy, dx))
        if abs(norm180(az - math.degrees(heading))) > CONE_DEG:
            continue
        if best is None or dist < best[0]:
            best = (dist, top, bottom, x, y, az)
    if best is None:
        return None, "航向 ±%.0f°、%.0fm 内没有城墙" % (CONE_DEG, MAX_RANGE)
    dist, top, bottom, x, y, az = best
    dz = top - stop[2]
    return math.degrees(math.atan2(dz, dist)), {
        "stop": stop, "heading": math.degrees(heading), "dist": dist,
        "top": top, "bottom": bottom, "dz": dz, "x": x, "y": y, "az": az}


def report(scene):
    print("=" * 88)
    print("攻城车架梯俯仰角反推   scene=%s   （公式：θ = atan2(墙顶 z − 车 z, 到墙水平距离)）"
          % scene)
    print("=" * 88)
    meta, walls = loadWalls(scene)
    print("城墙格 = %d   cell=%s x0=%s y0=%s"
          % (len(walls), meta["cell"] if meta else "-",
             meta["x0"] if meta else "-", meta["y0"] if meta else "-"))
    rows = cars(scene)
    if not rows:
        print("（该场景没有攻城车 tid=%d）" % CAR_TID)
        return
    for it in rows:
        pid = it.get("pathId")
        deg, info = calcCar(it, meta, walls)
        if deg is None:
            print("\n  攻城车 pathId=%-7s  ⚠️ 算不出来：%s ⇒ 服务端兜底 %.1f°"
                  % (pid, info, FALLBACK_DEG))
            continue
        print("\n  攻城车 pathId=%-7s  →  **θ = %+.2f°**" % (pid, deg))
        print("      停位(path 终点) = (%.2f, %.2f, %.2f)   航向 = %+.2f°"
              % (info["stop"][0], info["stop"][1], info["stop"][2], info["heading"]))
        print("      最近城墙格      = (%.1f, %.1f)  az=%+.2f°  r=%.2fm"
              % (info["x"], info["y"], info["az"], info["dist"]))
        print("      墙顶 z=%.2f 墙底 z=%.2f  Δz=%+.2f  ⇒ θ=atan2(%.2f, %.2f)=%+.2f°"
              % (info["top"], info["bottom"], info["dz"],
                 info["dz"], info["dist"], deg))

    # 交叉验证：与服务端同一个公式的实现对账（有 import 得到才算）
    cross(scene, meta, walls, rows)


def cross(scene, meta, walls, rows):
    """把本脚本的独立实现与服务端 ``siege.carWallPitchDeg`` 对账。"""
    try:
        sys.path.insert(0, SERVER)
        from scripts import siege as S      # noqa: E402
    except Exception as error:              # noqa: BLE001
        print("\n⚠️ 跳过交叉验证（import 不到 scripts.siege：%r）" % (error,))
        return
    S._ACOLLISION_CACHE.pop(scene, None)
    S._CAR_WALL_CACHE.clear()
    print("\n--- 交叉验证（本脚本 vs 服务端 siege.carWallPitchDeg）---")
    for it in rows:
        mine, _ = calcCar(it, meta, walls)
        theirs = S.carWallPitchDeg(it, scene)
        ok = (mine is None and theirs is None) or (
            mine is not None and theirs is not None and abs(mine - theirs) < 1e-9)
        print("  pathId=%-7s 本脚本=%s  服务端=%s  %s"
              % (it.get("pathId"),
                 "None" if mine is None else "%.4f" % mine,
                 "None" if theirs is None else "%.4f" % theirs,
                 "✅" if ok else "❌ 不一致！"))


def all_scenes():
    return sorted(d for d in os.listdir(SCENE_DIR)
                  if os.path.isfile(os.path.join(SCENE_DIR, d, "ccobject.json")))


def main():
    args = list(sys.argv[1:])
    if args and args[0] == "--all":
        scenes = all_scenes()
        print("=" * 88)
        print("全量扫描：%d 个场景带 ccobject.json" % len(scenes))
        print("=" * 88)
        for s in scenes:
            meta, walls = loadWalls(s)
            rows = cars(s)
            if not rows:
                continue
            print("\n### %s   城墙格=%d" % (s, len(walls)))
            for it in rows:
                deg, info = calcCar(it, meta, walls)
                if deg is None:
                    print("  pathId=%-7s ⚠️ %s" % (it.get("pathId"), info))
                else:
                    print("  pathId=%-7s θ = %+.2f°   （Δz=%+.2f  r=%.2fm 墙顶=%.2f）"
                          % (it.get("pathId"), deg, info["dz"], info["dist"], info["top"]))
        print("\n⚠️ 没有 acollision.json 的场景算不出来 ⇒ 服务端回落兜底 %.1f°。"
              % FALLBACK_DEG)
        return 0

    scene = args[0] if args else current_scene()
    if not scene:
        print("读不到场景（level.ini 的 [level] id= 没配）")
        return 1
    report(scene)
    print()
    print("✅ 把下面这行填进 level.ini 的 [cc] 段（默认就是它）：")
    print("    car_pose_vangle=auto")
    print("   ⚠️ 正值朝上 / 朝下**未实测** ⇒ 实机若发现是**低头**，改 car_pose_vangle_flip=on。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
