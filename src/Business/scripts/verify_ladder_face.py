# -*- coding: utf-8 -*-
"""验证云梯爬升面标定（第三十二轮：probe 点云 + acollision 定案的修复）。

跑法（与 ``verify_climb_echo.py`` 同款）：

    cd <Business> && PYTHONIOENCODING=utf-8 <python> scripts/verify_ladder_face.py

背景（第三十一轮实机 ``ladder-climb-probe`` 全部 ``why=no-face``）：
  * 云梯3（wall-hit 路径）：用户实测点 (579.95,581.50) 对登记坡 n=-2.52，
    其中 **1.5 是 ``ladder_climb_side`` 旋钮贡献的** —— 归零后 n=-1.02 命中；
  * 云梯1（回落路径）：旧坡 foot=pos+7.97u、run=11.26，用户探测点全在坡后
    （t'∈[-9.6,-3.5]）—— 一个都罩不住；
  * acollision 标定：用户沿**梯身** ±3m 走动试爬，且云梯1 的墙与梯轴平行斜贴
    （严格判据永不命中），墙顶 55.63。

修复（本脚本验证）：
  1. 命中墙 ⇒ ``run = |face_t| + |LADDER_FOOT_X|``（坡铺满躺地梯身 → 墙脸），
     ``rise = 墙顶中位数 − foot_z + 0.4``（坡顶高出墙顶走道半格）；
  2. 找不到墙 ⇒ ``ladder_climb_fb_run/fb_rise`` 长坡（foot 钉回 −8.038）；
  3. ``side=0``、``width=3.0``（level.ini 同步改，本脚本用同值 env 注入）。
"""
import math
import os
import sys
from pathlib import Path

# 与 level.ini 第三十二轮值一致（env > ini > 默认）。
os.environ["T7_CC_LADDER_CLIMB_WIDTH"] = "3.0"
os.environ["T7_CC_LADDER_CLIMB_SIDE"] = "0"
os.environ["T7_CC_LADDER_CLIMB_FB_RUN"] = "28"
os.environ["T7_CC_LADDER_CLIMB_FB_RISE"] = "13.0"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import siege  # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(ok)
    print(("  OK   " if ok else "  FAIL ") + name + ("  " + detail if detail else ""))


def close(a, b, tol=0.05):
    return abs(a - b) <= tol


def item_of(pos, yaw):
    return {"pos": [float(pos[0]), float(pos[1]), float(pos[2])],
            "face": [0.0, 0.0, float(yaw)]}


def axis_hits(geom, points):
    """把探测点投到坡轴 (t, n)，返回命中数（t∈[0,run]、|n|≤half）。"""
    fx, fy, fz, ux, uy, run, rise, half, cap = geom
    hits = 0
    for (px, py, pz) in points:
        dx, dy = px - fx, py - fy
        t = dx * ux + dy * uy
        n = dy * ux - dx * uy
        if 0.0 <= t <= run and abs(n) <= half and pz <= fz + rise:
            hits += 1
    return hits


SCENE = "tszz"

# ---- 1. 云梯3（rid 10010，wall-hit 路径）----------------------------------
# ⭐ 第三十四轮起旋钮默认 cross_ext=1.5 / land_clear=1.2（落地段修「一上去卡墙」）
_xe = siege.ladderClimbCrossExt()
_lc = siege.ladderClimbLandClear() - 0.4
L3 = item_of((570.347, 581.840, 44.7406), 0.0714)
g3 = siege.ladderClimbFace(L3, SCENE)
check("L3 有爬升面", g3 is not None)
if g3:
    fx, fy, fz, ux, uy, run, rise, half, cap = g3
    # foot_t = 20 − (20+8.038+cross_ext) ⇒ foot = pos − foot_t·u
    # 预期值按旋钮动态算（基线 = cross_ext=0 / clear=0.4）
    check("L3 run 拉长到墙脸（≈28.04+ext）", close(run, 28.038 + _xe, 0.1),
          "run=%.3f" % run)
    check("L3 rise 按墙顶定（≈11.49+clear）", close(rise, 11.49 + _lc, 0.1),
          "rise=%.3f" % rise)
    check("L3 half=3.0（width 旋钮）", close(half, 3.0, 0.01), "half=%.2f" % half)
    exp_x = 570.347 + (-8.038 - _xe) * math.cos(0.0714)
    exp_y = 581.840 + (-8.038 - _xe) * math.sin(0.0714)
    check("L3 foot 落在躺地梯脚端", close(fx, exp_x, 0.05) and close(fy, exp_y, 0.05),
          "foot=(%.2f,%.2f) exp=(%.2f,%.2f)" % (fx, fy, exp_x, exp_y))
    # 第三十一轮用户实测点（旧坡 n=-2.52 miss）—— 归零 side 后必须命中
    p_user = (579.95, 581.50, 44.53)
    dx, dy = p_user[0] - fx, p_user[1] - fy
    t = dx * ux + dy * uy
    n = dy * ux - dx * uy
    check("L3 用户实测点命中", 0 <= t <= run and abs(n) <= half,
          "t=%.2f n=%.2f half=%.2f" % (t, n, half))

# ---- 2. 云梯1（rid 10001，找墙不命中 → fb 长坡）----------------------------
L1 = item_of((522.828, 554.648, 44.2037), 0.1622)
g1 = siege.ladderClimbFace(L1, SCENE)
check("L1 有爬升面", g1 is not None)
if g1:
    fx, fy, fz, ux, uy, run, rise, half, cap = g1
    check("L1 run=fb_run(28)", close(run, 28.0, 0.05), "run=%.3f" % run)
    check("L1 rise=fb_rise(13.0)", close(rise, 13.0, 0.05), "rise=%.3f" % rise)
    exp_x = 522.828 + (-8.038) * math.cos(0.1622)
    exp_y = 554.648 + (-8.038) * math.sin(0.1622)
    check("L1 foot 钉回模型梯脚（-8.038）",
          close(fx, exp_x, 0.05) and close(fy, exp_y, 0.05),
          "foot=(%.2f,%.2f) exp=(%.2f,%.2f)" % (fx, fy, exp_x, exp_y))
    # 第三十一轮探测点云里贴着云梯1 的那一簇（frames-1.jsonl 原值）
    PROBES_L1 = [
        (567.25, 570.12, 44.43), (565.59, 571.95, 44.47),   # 云梯3 一带（不属 L1）
        (560.44, 570.81, 44.47), (533.67, 566.84, 44.82),
        (527.74, 554.15, 44.37), (526.81, 553.50, 44.43),
        (520.60, 557.03, 43.99), (521.08, 555.39, 44.11),
        (524.97, 554.89, 44.28), (526.18, 553.70, 44.40),
        (524.00, 554.28, 44.32), (525.76, 553.81, 44.38),
        (521.72, 554.71, 44.16), (522.89, 554.61, 44.20),
        (527.18, 554.06, 44.36), (527.21, 554.05, 44.36),
        (531.99, 553.12, 44.70), (530.94, 553.89, 44.63),
        (528.65, 553.49, 44.45),
    ]
    hits = axis_hits(g1, PROBES_L1)
    # 除 (533.67)（横向 n≈10 远离梯轴）与云梯3 一带 3 点外全部命中
    check("L1 梯身探测点 ≥14/19 命中", hits >= 14, "hits=%d/19" % hits)
    # 旧坡（foot=+7.97u、half=1.2）同一点云命中数 —— 应远少于新坡
    old = (530.70, 555.94, 42.95, ux, uy, 11.26, 11.38, 1.2, 1.2)
    old_hits = axis_hits(old, PROBES_L1)
    check("L1 新坡严格优于旧坡", hits > old_hits,
          "new=%d old=%d" % (hits, old_hits))

# ---- 3. 云梯4（rid 10011，wall-hit、dir=-1 路径回归）-----------------------
L4 = item_of((584.408, 625.344, 44.7887), 1.4709)
g4 = siege.ladderClimbFace(L4, SCENE)
check("L4 有爬升面", g4 is not None)
if g4:
    fx, fy, fz, ux, uy, run, rise, half, cap = g4
    # face_t=-19、dr=-1 ⇒ run=27.04+ext、foot_t=+8.04、坡沿 −u 升
    check("L4 run 拉长（≈27.04+ext）", close(run, 27.04 + _xe, 0.1), "run=%.3f" % run)
    check("L4 rise 按墙顶定（≈12.47+clear）", close(rise, 12.47 + _lc, 0.1),
          "rise=%.3f" % rise)
    # 坡顶 = foot + run·u（geom 的 u 已含 dir）应落回墙脸 t=−19
    top_t = (fx + run * ux - 584.408) * math.cos(1.4709) \
        + (fy + run * uy - 625.344) * math.sin(1.4709)
    check("L4 坡顶贴墙脸（t≈-19）", close(top_t, -19.0, 0.15), "top_t=%.2f" % top_t)

# ---- 4. fb_run=0 ⇒ 旧行为逐位回归（默认不变底线）--------------------------
os.environ["T7_CC_LADDER_CLIMB_FB_RUN"] = "0"
os.environ["T7_CC_LADDER_CLIMB_FB_RISE"] = "0"
os.environ["T7_CC_LADDER_CLIMB_SHIFT"] = "16.008"
g1_old = siege.ladderClimbFace(L1, None)      # scene=None ⇒ 永远走回落
check("L1(scene=None) 有爬升面", g1_old is not None)
if g1_old:
    fx, fy, fz, ux, uy, run, rise, half, cap = g1_old
    check("旧公式 run=11.26", close(run, 11.26, 0.05), "run=%.3f" % run)
    check("旧公式 foot=+7.97u", close(fx, 530.70, 0.05) and close(fy, 555.94, 0.05),
          "foot=(%.2f,%.2f)" % (fx, fy))

print()
total, good = len(RESULTS), sum(1 for r in RESULTS if r)
print("verify_ladder_face: %d/%d OK" % (good, total))
sys.exit(0 if good == total else 1)
