# -*- coding: utf-8 -*-
"""空气墙（``aairwall.dat``）的加载与 2D 碰撞判定。

为什么需要它
------------
客户端上行**没有位置包**：``uplink-diag.log`` 全量清点下来只有
``cmd=2 sel=3``（朝向，``>h`` 在 offset 7）、``cmd=2 sel=52``（WASD 按键位图）、
``cmd=2 sel=63``（快跑请求）、``cmd=4 sel=1``（战斗）、``cmd=16 sel=3`` 这几类。
也就是说**位置完全由服务端积分**，入口就是 ``controls.advanceGround()``。
而那个函数在接线之前只有

    delta = (distance * (-normFB*cos + normLR*sin),
             distance * ( normFB*sin + normLR*cos),
             0.0)

—— 输入只有 WASD + heading + step_distance，``delta[2]`` 硬编码 0.0，
**一行碰撞都没有**。所以「空气墙没有物理」永远是服务端的锅，不是客户端拦不住。

数据来源
--------
原版客户端 ``vfs`` 包里的 ``map.vfs``，每个场景一份
``../data/scene/map/<场景>/aairwall.dat``，是 **GB2312 的纯 XML**。
已抽取 62 个场景，落在 ``server/data/scene/<场景>/aairwall.xml``。

格式（实测）
------------
    <Entity Position="748.807,656.661,106.692" Rotation="0,0,0" Scale="1">
      <GeAirWall>
        <AirWallPoints Value="p 12.470215,11.819092,-3.384827,p 12.47...,..."/>
        <AirWallHeight Value="0"/>
      </GeAirWall>
    </Entity>

* ``Position`` 是世界平移；``Rotation`` 实测 83%（1428/1721）为 ``0,0,0``，
  其余基本是 ``0,0,<角度>``（**只绕 Z 轴**，正是竖直幕布该有的样子）；
  ``Scale`` 恒为 1。
* ``AirWallPoints`` 是**局部空间**折线，点用 ``p x,y,z`` 逗号分隔。
* 本引擎 **Z 是高度轴**（出生点 ``272.451,157.634,0.218``，第三分量贴地）。

所以空气墙在几何上就是「XY 平面折线 + 竖直高度区间」，判定只需 2D 线段相交。

⚠️ 两个已经踩过的坑（改动前先读）
----------------------------------
1. **点列是带状幕布，必须按偶数下标两两成对**。下标 ``0,1`` 是同一断面的
   「下沿/上沿」（同 x,y、z 不同），``2,3`` 是下一个断面。实测 11,387 组里
   10,630 组（93.4%）满足这个结构。XY 上真正构成墙面的是**隔点取**
   （``pts[0::2]``）；而 ``0→1`` 那段在 XY 上退化成一点，拿它做线段相交
   **永远判不中**。
2. **别用「所有相邻点对」去判成对**：那样只有 ``1→2``、``3→4`` 这些不配对的
   会被算进去，比例掉到 50% 以下，反而判不出来。必须 ``range(0, len-1, 2)``。

未闭合
------
* **场景绑定**：服务端目前没有场景概念（出生点硬编码），``AIRWALL_SCENE``
  由 ``controls.py`` 侧配置，映射表 ``s_res_instance_cfg_cli.bin`` 尚未从
  ``data1.vfs`` 的间隙区取出。
* **高度过滤**：墙自带竖直区间，但服务端 Z 是占位值，默认不过滤（见
  ``controls.AIRWALL_HEIGHT_FILTER``）。
* **没有碰撞半径**：命中即「不推进位置」，不做墙面吸附/滑行，也不做
  capsule 半径补偿。
* ``acollisionmap.dat``（Havok 二进制 4.5MB）里的地形碰撞**没做**，
  本模块只管空气墙。

用法
----
    python -m scripts.airwall city_temp_low      # 打印该场景墙统计
    python -m scripts.airwall                    # 列出全部已抽取场景
"""
import math
import os
import re

_ENTITY = re.compile(
    r'<Entity\s+Position="([^"]*)"\s+Rotation="([^"]*)"\s+Scale="([^"]*)"(.*?)</Entity>',
    re.S)
_POINTS = re.compile(r'<AirWallPoints\s+Value="([^"]*)"')
_PT = re.compile(r'p\s+(-?[\d.]+),(-?[\d.]+),(-?[\d.]+)')


class AirWall(object):
    """一段空气墙。

    ``footprint`` 是**用于 XY 碰撞的折线**（见 :func:`footprint`），
    ``raw`` 是原始世界点列，``zmin``/``zmax`` 是整段墙的高度区间。
    """

    __slots__ = ('footprint', 'raw', 'zmin', 'zmax', 'paired',
                 'xmin', 'xmax', 'ymin', 'ymax')

    def __init__(self, raw, fp, paired):
        self.raw = raw
        self.footprint = fp
        self.paired = paired
        zs = [p[2] for p in raw]
        self.zmin = min(zs)
        self.zmax = max(zs)
        xs = [p[0] for p in fp]
        ys = [p[1] for p in fp]
        self.xmin, self.xmax = min(xs), max(xs)
        self.ymin, self.ymax = min(ys), max(ys)

    def coversZ(self, z):
        """该墙的竖直区间是否覆盖高度 z（带 0.5 容差）。"""
        return self.zmin - 0.5 <= z <= self.zmax + 0.5

    def __repr__(self):
        return ("AirWall(edges=%d, z=%.2f..%.2f, paired=%s)"
                % (len(self.footprint) - 1, self.zmin, self.zmax, self.paired))


def footprint(pts):
    """从点列里取出 XY 碰撞用的折线，并报告是否为「成对」结构。

    返回 ``(折线, 是否成对)``。坑 1、坑 2 见模块 docstring。
    """
    paired = 0
    total = 0
    for k in range(0, len(pts) - 1, 2):
        total += 1
        a, b = pts[k], pts[k + 1]
        if abs(a[0] - b[0]) < 1e-3 and abs(a[1] - b[1]) < 1e-3:
            paired += 1
    if total and paired >= total * 0.8:
        return pts[0::2], True
    return pts, False


def parseText(xmlText):
    """把 ``aairwall.dat`` 的 XML 文本解析成 ``[AirWall, ...]``（世界空间）。"""
    walls = []
    for m in _ENTITY.finditer(xmlText):
        px, py, pz = (float(v) for v in m.group(1).split(','))
        rot = [float(v) for v in m.group(2).split(',')]
        scale = float(m.group(3))
        # 实测只有绕 Z 的旋转有意义；出现非 Z 分量时按「只取 Z」处理，
        # 因为 1721 段里 83% 是 0,0,0、其余基本是 0,0,<角度>，没有倾斜墙样本。
        rz = rot[2] if len(rot) >= 3 else 0.0
        cz, sz = math.cos(rz), math.sin(rz)
        pm = _POINTS.search(m.group(4))
        if not pm:
            continue
        pts = []
        for gx, gy, gz in _PT.findall(pm.group(1)):
            x, y, z = float(gx) * scale, float(gy) * scale, float(gz) * scale
            pts.append((px + x * cz - y * sz, py + x * sz + y * cz, pz + z))
        if len(pts) < 2:
            continue
        fp, paired = footprint(pts)
        if len(fp) >= 2:
            walls.append(AirWall(pts, fp, paired))
    return walls


def parseFile(path):
    with open(path, 'rb') as handle:
        return parseText(handle.read().decode('gb2312', 'replace'))


def scenePath(scene, baseDir):
    return os.path.join(baseDir, scene, 'aairwall.xml')


def loadScene(scene, baseDir):
    """按场景名加载 ``<baseDir>/<场景>/aairwall.xml``。

    不做缓存——缓存由调用方（``controls.airWallWalls``）负责，
    这样离线脚本可以随意重复加载。
    """
    path = scenePath(scene, baseDir)
    if not os.path.isfile(path):
        raise OSError("airwall not found: " + path)
    return parseFile(path)


def _side(px, py, qx, qy, rx, ry):
    v = (qx - px) * (ry - py) - (qy - py) * (rx - px)
    return (v > 0) - (v < 0)


def _onSeg(ax, ay, bx, by, px, py):
    return (min(ax, bx) - 1e-9 <= px <= max(ax, bx) + 1e-9
            and min(ay, by) - 1e-9 <= py <= max(ay, by) + 1e-9)


def _cross(ax, ay, bx, by, cx, cy, dx, dy):
    """2D 线段 AB 与 CD 是否相交（含端点接触）。"""
    d1 = _side(ax, ay, bx, by, cx, cy)
    d2 = _side(ax, ay, bx, by, dx, dy)
    d3 = _side(cx, cy, dx, dy, ax, ay)
    d4 = _side(cx, cy, dx, dy, bx, by)
    if d1 != d2 and d3 != d4:
        return True
    # 共线/端点情形退化为距离判定
    if d1 == 0 and _onSeg(ax, ay, bx, by, cx, cy):
        return True
    if d2 == 0 and _onSeg(ax, ay, bx, by, dx, dy):
        return True
    if d3 == 0 and _onSeg(cx, cy, dx, dy, ax, ay):
        return True
    if d4 == 0 and _onSeg(cx, cy, dx, dy, bx, by):
        return True
    return False


def blocked(p0, p1, walls, playerZ=None):
    """从 ``p0`` 走到 ``p1`` 是否被空气墙挡住。

    只做 XY 平面相交（墙是竖直幕布）；``playerZ`` 非 None 时再用墙的竖直区间
    过滤掉高度不覆盖的墙。

    返回 ``None`` 表示放行；否则返回 ``{'wall': AirWall, 'index': int,
    'seg': ((ax,ay),(bx,by))}``。
    """
    x0, y0 = p0[0], p0[1]
    x1, y1 = p1[0], p1[1]
    if x0 == x1 and y0 == y1:
        return None
    loX, hiX = (x0, x1) if x0 <= x1 else (x1, x0)
    loY, hiY = (y0, y1) if y0 <= y1 else (y1, y0)
    for wall in walls:
        if playerZ is not None and not wall.coversZ(playerZ):
            continue
        # AABB 预筛：一次矩形比较就剔掉绝大多数墙，20Hz 下开销可以忽略。
        if wall.xmax < loX or wall.xmin > hiX or wall.ymax < loY or wall.ymin > hiY:
            continue
        pts = wall.footprint
        for k in range(len(pts) - 1):
            ax, ay = pts[k][0], pts[k][1]
            bx, by = pts[k + 1][0], pts[k + 1][1]
            if _cross(x0, y0, x1, y1, ax, ay, bx, by):
                return {'wall': wall, 'index': k, 'seg': ((ax, ay), (bx, by))}
    return None


def _main():
    import io
    import sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'data', 'scene')
    scenes = sorted(name for name in os.listdir(base)
                    if os.path.isfile(scenePath(name, base))) if os.path.isdir(base) else []
    if not scenes:
        print('没有 <server>/data/scene/<场景>/aairwall.xml')
        return 1
    args = [a for a in sys.argv[1:] if not a.startswith('-')]
    if args:
        walls = loadScene(args[0], base)
        segs = sum(len(w.footprint) - 1 for w in walls)
        print('%s：墙 %d 段 / 折线边 %d 条 / 成对 %d'
              % (args[0], len(walls), segs, sum(1 for w in walls if w.paired)))
        for w in walls[:3]:
            print('   ', w)
        return 0
    print('%-24s %6s %8s' % ('场景', '墙段', '折线边'))
    for name in scenes:
        walls = loadScene(name, base)
        print('%-24s %6d %8d'
              % (name, len(walls), sum(len(w.footprint) - 1 for w in walls)))
    return 0


if __name__ == '__main__':
    raise SystemExit(_main())
