"""Input-driven 20Hz ground fixture and legacy timer compatibility.

2026-09-23 15:3x：三次 touch —— rsp 档实机结论（会话 13976-246839642）：cmd=7 sel=3
RIDE_RSP 客户端**认到了但之后移动输入全停**（心跳继续、cmd=2 归零）⇒ 帧格式/字段
不完整，回退 mount_unride=state 待查 cmd=7 正确组帧（可能缺 RIDE_BC/视野广播）。
"""
import math
import os
import struct

from . import collide
from . import collide_mesh
from . import contracts as wire
from .codec import mount_flow
from .codec import move_flow

GROUND_RUN_DELAY_MS = 2000
MAX_GROUND_ELAPSED_MS = 100
# movable 时钟口径，对齐上游 ``MOVE_CLOCK``：下发/上报的 ``server_tick``
# **不是墙钟毫秒，而是「进图起递增毫秒」**（上游注释：「客户端给 MOVE tick 加
# 实例起点；同毫秒消息共享时间，不额外积分」）。``activate()`` /
# ``unlockOrientation()`` / ``activateMount()`` 三处原本硬写 ``server_tick=1``，
# 等于告诉客户端「这个 movable 是在进图第 1 毫秒激活的」，与真实实例时钟脱节。
# 边界：只有 ``instanceStartedAt`` 已落库（``scene.begin``）时才走该口径；
# 离线仿真桩 / 进图前**退回墙钟口径**，不改既有回归行为。
MOVE_CLOCK = "instance-relative-ms-v1"
MAX_MOVE_TICK = 0x7FFFFFFF
# 2026-09-19 高度跟随：一步最多改变的 Z（米）。楼梯在高度场里是垂直通道，
# 相邻格差 11~12 m（实测 939 处大跳变全在楼梯口），不限速会瞬移。
# ⚠️ 别调太小：advanceGround() 只在**移动时**调用，玩家在楼梯上停手就会
#    卡在半空。6.0 意味着爬完一座楼梯约 2 步（20Hz ≈ 0.1 秒）。
#    普通地形每格差 <0.85 m，根本触发不到限速。
MAX_GROUND_Z_STEP = 6.0
# ⚠️⚠️ 上行闸门（2026-09-19 晚启用；阈值 8.0 是**扫出来**的，不是拍的）
#
# 作用：挡住「走近墙根被凭空抬上 12 米墙顶」。下行侧早有对称的
# ``LEDGE_MAX_DROP = 8.0``（见本文件 §边缘阻挡），这里补上行侧，两侧同取 8.0。
#
# 阈值怎么定的 —— 拿 2026-09-19 22:02 那次实机会话（``13496-329470073``）
# 的 **193 条 ground-z-follow 逐跳回放**（``t7_gap_sweep.py``）：
#
#     GAP      被挡跳数   其中目标格从此**到不了**的
#     2.00        59            48
#     3.50        57            37
#     5.00        43            28
#     7.00        33            23
#     7.50        33            23
#     8.00        33             0   <== 甜点：挡住瞬移，一格不丢
#     12.00        6             0
#     99.0         0             0   （= 现状，无闸门）
#
# 8.0 是**唯一**「挡住瞬移且一个格子都不丢」的取值：33 次 12 米级瞬移全挡，
# 玩家实际走过的每一格仍然能靠走走到。
#
# ⚠️⚠️ 曾经取过 3.5，**那是错的**，别再照着推一遍。
#   当时按「真楼梯最大单步 2.852 + 余量」推，并以为「墙顶仍有 455 格可达」。
#   那 455 格其实是**城内建筑**。真正的城墙是 x[577..646] y[539..647] 那 1226 格，
#   在 3.5 下只剩 158 格（12.9%）可达 —— 等于把城墙封掉一半。
#   逐层扫描（``t7_wall_gap_min.py``）：
#
#     GAP       城墙可达      占比
#     <=7.50      158        12.9%
#     7.75       1207        98.5%   <== 解锁边是 (602,625) 的 7.642 m 落差
#     12.00      1226       100.0%
#
#   也就是说**没有任何阈值能「只放行真楼梯」** —— 城墙四周 606 条上行边里
#   588 条 > 3.5 m、365 条 > 12 m，本来就是一圈断崖。8.0 已是保住城墙的最小代价。
#
# ⚠️⚠️⚠️ 2026-09-20 更正：**上面这套推导的指标本身是错的，别再拿它推阈值。**
#   它的「目标格到不了」是按**地形 BFS** 算的 —— 而当时能走到目标格的路径里，
#   有一大半是**爬墙**上去的（墙内壁 7.7845 m 那条）。所以「一格不丢」其实是
#   「墙还能爬」。8.0 因此**偏松**：它放行了 7.7845 的墙内壁 → 实机「弹上去」。
#
#   真正的病根是**判定位置错了**：这道闸门跑在 X/Y 写入**之后**，只能挡 Z。
#   正解是 **advanceGround 里的 climbBlocked()（CLIMB_MAX_RISE = 5.5）**，
#   见本文件 §上行爬升阻挡。阈值 5.5 由 [楼梯最大单步 3.946, 墙内壁 7.7845) 定，
#   两侧余量各约 1.4 倍。
#
#   本常量现在**降级为 backstop**（防传送/复活那种「位置凭空变了」的情况），
#   日常移动永远轮不到它 —— 因为 climbBlocked 先一步挡住了。保留是为了不破坏
#   已实机验收的行为（``t7_wasd_verify.py`` 187 项是回归门）。
#
# 想改：``set T7_STEP_GAP=7.5`` 试更严的，或 ``off`` 回到无闸门（只影响本进程）。
MAX_GROUND_Z_GAP = 8.0
STEP_GAP_ENV_SWITCH = "T7_STEP_GAP"


def stepUpGap():
    """上行闸门阈值（米）。环境变量优先于常量，方便不改代码做单次实机验证。

    ``T7_STEP_GAP=off`` / ``0`` → 0.0（不限制，回到 2026-09-19 白天那版行为）；
    ``T7_STEP_GAP=7.5`` → 临时换成 7.5 米（会更严，但城墙可达性掉到 12.9%）。
    """
    override = os.environ.get(STEP_GAP_ENV_SWITCH)
    if override is not None:
        if override.strip().lower() in ("0", "off", "false", "no", ""):
            return 0.0
        try:
            return max(0.0, float(override))
        except ValueError:
            pass
    return MAX_GROUND_Z_GAP
# --- 边缘阻挡（2026-09-19）------------------------------------------------------
# 站在城墙顶往旁边走一步就掉下去（人物呈掉落姿势）。用户原话：
#   「挡住是不是就掉不下来了」—— 是的，所以这里**挡住**而不是让它掉。
# 判定按**地形落差**（旧格地面 Z − 新格地面 Z），不是角色 Z：
#   角色 Z 正在被 groundZFollow 拉（限速 6 m/步）时比地形低十几米，
#   拿它当基准会把「正在爬墙」误判成「要跳崖」。
# ⚠️ 阈值 8.0 是实测卡在中间的，别随手改：
#     楼梯/垂直通道下降 = 6.0 m/步（MAX_GROUND_Z_STEP）→ **必须放行**，
#     否则玩家下不了楼、会卡在屋顶；
#     城墙顶 -> 城内天井 = 55.702 - 43.385 = 12.3 m      → **必须挡住**。
#     8.0 正好落在 6.0 与 12.3 之间。
# 跳跃中放行：否则上了城墙就下不来（想下来按跳跃）。
LEDGE_MAX_DROP = 8.0
# ⚠️ 硬挡会变成「看不见的墙」，玩家以为卡死了（实机：同一个点被挡 492 次）。
#    所以做成**按住方向 0.4 秒才放行**：正常走路到崖边会停住（防误掉），
#    但一直推着同一个方向就判定为「故意走下去」→ 放行。永远不会困住。
LEDGE_HOLD_MS = 400
# 只有变化超过这个值才写日志，免得每个 tick 刷屏。
GROUND_Z_LOG_THRESHOLD = 0.05
GROUND_WALK_STATES = {
    (-1, 0): 2, (-1, 1): 3, (0, 1): 4, (1, 1): 5,
    (1, 0): 6, (1, -1): 7, (0, -1): 8, (-1, -1): 9,
}
GROUND_RUN_STATES = {(-1, 0): 10, (-1, 1): 11, (-1, -1): 12}

# --- 上行爬升阻挡（2026-09-20 深夜；`ledgeBlocked` 的**对称另一半**）---------------
# 上面那套（LEDGE_MAX_DROP）只挡**下行**——掉落。**上行一直没人管**，
# 于是「走近墙根被凭空抬上墙顶」和「走进墙里」都从这儿漏出来。
#
# ⚠️⚠️ 为什么不能继续用 groundZFollow 里那道闸门（MAX_GROUND_Z_GAP）
# ------------------------------------------------------------------
# 那道闸门在 `advanceGround()` 里的**调用顺序上排在 X/Y 写入之后**：
#
#     ground["position"] = position      # ← X/Y 已经写进去了
#     groundZFollow(flow, ground)        # ← 这里才判「能不能抬上去」
#
# 所以它只能挡住 Z，挡不住人**走进去**。实机证据（会话 13496-336139497 系列）：
#
#   * 用户卡住点 (553.81, 576.31)：格 (143,334) 地面 54.603，
#     人在 (142,334) 地面 44.530 —— 落差 10.073。
#     日志 `ground-z-blocked-stepup step=10.073 gap=8.0` 连记 20 次，
#     而角色 X/Y 已经写进格 (143,334) 了 → **人站在塔楼壁里面**。
#     用户原话：「**卡在城墙里**」。
#   * 同一局 (596.83, 593.27)：`ground-z-follow 44.3125 -> 50.3125 -> 52.097 -> 55.705`
#     —— 连爬三级上了墙顶。墙内壁落差是 **7.7845**，闸门取 8.0，
#     **只差 0.216 米没挡住**。用户原话：「**弹上去的**」。
#
# 所以正解是把判定挪到 X/Y 写入**之前**，和 ledgeBlocked 并排。
#
# ⚠️⚠️ 阈值 5.5 是**量出来的**，不是拍的（`t7_climb_pick.py` / `t7_climb_probe.py`）
# --------------------------------------------------------------------------
# 窗口 = [楼梯最大单步, 墙内壁最小落差)：
#
#   真楼梯（(597,600)43.55 → 45.29 → 47.03 → 48.76 → 50.50 → 52.24 → 55.71 墙顶）
#       最大单步 = 3.4730 m          ← 阈值必须 **≥** 它，否则楼梯上不去
#   tszz_stair_03 @ (602.81,590.53) 3x3 内最大上行 = 3.9460 m
#       墙内壁 (595.8→596.8, 593.3) 44.312 → 52.097 = 7.7845 m
#       墙内壁 (595.8→596.8, 592.3) 44.303 → 55.703 = 11.4000 m
#                                     ← 阈值必须 **<** 它，否则能爬墙
#
#   窗口 = [3.946, 7.7845)，几何中点 sqrt(3.946*7.7845) = 5.54 → 取 **5.5**
#   两侧余量：5.5/3.946 = 1.39x，7.7845/5.5 = 1.42x（基本对称）
#
# ⚠️ 曾经在 groundZFollow 里取过 3.5，**那是错的**（把城墙封掉 87%）。
#    错因是「闸门只挡 Z、不挡水平移动」——阈值对不对根本不是那次的病根。
#    这次阈值 5.5 与那次 3.5 无关，别混。
#
# ⚠️ 与 MAX_GROUND_Z_STEP = 6.0 的关系：CLIMB_MAX_RISE(5.5) < 6.0，
#    所以每一步上行都能被 groundZFollow **一步跟到位**，角色 Z 永远等于
#    当前格地面值 —— 这正是本判定能拿「角色实际 Z」当基准的前提（见下）。
#
# 想改：`set T7_CLIMB=6.0` 试更松的，或 `off` 回到无上行阻挡（只影响本进程）。
CLIMB_MAX_RISE = 5.5
CLIMB_ENV_SWITCH = "T7_CLIMB"

# ⚠️ 2026-09-20 新增。跳跃时 ``climbBlocked`` 的**额外**放宽量。
#
# 原来 ``climbBlocked`` 里第一行是 ``if jumpActive(flow): return False`` ——
# 跳跃期间**完全**不做上行判定。后果不是「能跳上矮台阶」，而是「能跳上 11 m 的城墙」：
#
#     跳跃最高点 = JUMP_INITIAL_VELOCITY² / (2·JUMP_GRAVITY) = 5²/(2×18) = 0.694 m
#
# 实测（会话 13496-380748287）：43 次 ``jump-take-off``，27 次跳跃后出现
# ``move-blocked-climb``，其中 **23 次（85%）** 落在 ``height`` 54~55 的墙格里 ——
# 角色跳进墙格后 Z 停在 44.4（``stepUpGap`` 闸门跑在 X/Y 写入**之后**，只挡住 Z），
# 之后每一步 ``rise ≈ 11 > 5.5`` 全被挡，**永久锁死**。用户原话「平路还是卡 走不动」。
#
# 现在改成「在 CLIMB_MAX_RISE 基础上再加这一段」：走路 5.5 m、跳跃 6.2 m ——
# 保留「跳上矮台阶」的能力，但跳不上 11 m 的墙。
JUMP_MAX_RISE = 0.7

# --- 空气墙碰撞（2026-09-19 接线；**默认关闭**） ----------------------------------
# 证据链（不是推断）：
#   * 客户端上行**没有位置包**。uplink-diag.log 全量清点只有
#     cmd=2 sel=3（朝向，>h 在 offset 7）、cmd=2 sel=52（WASD 按键位图）、
#     cmd=2 sel=63（快跑）、cmd=4 sel=1（战斗）、cmd=16 sel=3 这几类。
#     —— 位置**完全由服务端积分**，入口就是本模块的 advanceGround()。
#   * advanceGround() 在接线之前只有 WASD + heading + step_distance，
#     delta[2] 硬编码 0.0，**一行碰撞都没有**。所以「空气墙没物理/穿墙」
#     永远是服务端的锅，不是客户端拦不住。
#   * 碰撞数据在客户端 map.vfs 的 ../data/scene/map/<场景>/aairwall.dat
#     （GB2312 纯 XML）。已抽取 62 个场景放到 server/data/scene/<场景>/aairwall.xml。
#
# ⚠️ 开关**默认关闭**，理由与 fastRun 那套一致：**并集而不是替换**。
#    关掉时 advanceGround() 的位置写入路径与日志逐位不变，
#    已实机验收的 WASD 基线不会被这次改动碰到（t7_wasd_verify.py 187 项是回归门）。
#
# 想临时验证又不想改代码时，用环境变量（只影响本进程，重启即恢复）：
#     T7_AIRWALL=1  T7_AIRWALL_SCENE=city_temp_low
# ⚠️ 2026-09-19 改为默认**开启**：用户明确要物理碰撞/空气墙。
#    之前一直 False，加上数据目录被快照路径坑了（见 walkUp 那条），
#    等于空气墙从来没生效过。想关：T7_AIRWALL=0。
#    tszz 只有 5 段墙，误挡风险可控；真被挡住会在日志里留 move-blocked-airwall。
AIRWALL_COLLISION_ENABLED = True
# 场景名。服务端本来没有场景概念（出生点硬编码），所以历史上这里是空的。
# 2026-09-19：改由**关卡表**按 level_id 查（contracts.SCENE_BY_LEVEL），
# 终于和进的图对上了 —— 以前配 city_temp_low 而实际进的是洛阳死斗(lysd)，是错的。
# ⚠️ **没设 T7_LEVEL 时仍是空串**，与改动前逐位相同（不加载、不碰撞）。
# T7_AIRWALL_SCENE 环境变量仍可单独覆盖。
AIRWALL_SCENE = wire.SCENE_BY_LEVEL.get(wire.LEVEL_ID, "") if wire.LEVEL_CUSTOM else ""
# 高度过滤：墙自带竖直区间，理论上「墙顶低于角色」不该阻挡。但服务端的 Z 是
# **占位值**（ground["position"][2] 一直停在 0.218，而 city_temp_low 的墙整体
# 长在 z≈100.7..121.0），一开过滤就会**全放行**。所以默认关：先只按 XY 判，
# Z 维度留到 aheightmap(PAMH) 闭合后再开（未闭合）。
AIRWALL_HEIGHT_FILTER = False
def _sceneDir():
    """定位 ``data/scene``：**逐级向上找**（脚本会被快照到 revisions 下跑）。

    ⚠️ 2026-09-19：只用 ``__file__`` 上两级时，快照路径下没有 ``data/scene``，
    空气墙**也**加载不到（和高度场同一个根因）。
    """
    try:
        for node in wire.walkUp():
            path = os.path.join(node, "data", "scene")
            if os.path.isdir(path):
                return path
    except (AttributeError, TypeError, ValueError):
        pass
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "scene")


AIRWALL_DIR = _sceneDir()
AIRWALL_ENV_SWITCH = "T7_AIRWALL"
AIRWALL_ENV_SCENE = "T7_AIRWALL_SCENE"

_airwallModule = None
_airwallCache = {}
_airwallNoticed = set()


def airWallModule():
    """惰性导入 ``scripts.airwall``——开关关掉时一次都不 import，零开销。"""
    global _airwallModule
    if _airwallModule is None:
        from . import airwall as module
        _airwallModule = module
    return _airwallModule


def airWallScene(flow) -> str:
    """当前会话的空气墙场景名；空串表示「这一局不碰撞」。"""
    scene = os.environ.get(AIRWALL_ENV_SCENE)
    if not scene:
        scene = flow.session.get("scene")
    if not isinstance(scene, str) or not scene:
        # ⚠️ 2026-09-19 补：**每个连接有独立的 session**（app.py:
        # ``self.session = state["sessions"].get(str(self.connection))``）。
        # 切图(match-start)发生在 **logic 连接**上，实际移动发生在之后建立的
        # **instance 连接**上 —— 后者 session 里根本没有 ``scene``，
        # 于是回退到模块加载时的旧场景，高度跟随全程不生效（实测
        # ``ground-z-follow`` 触发 0 次，玩家走出出生点就沉到地下）。
        # 同一局所有连接的场景必然相同，所以回退到**同局任意其它 session**。
        scene = _peerScene(flow)
    if not isinstance(scene, str) or not scene:
        scene = AIRWALL_SCENE
    return scene if isinstance(scene, str) else ""


def _peerScene(flow):
    """同局其它 connection 的 session 里有没有 scene（跨连接继承）。"""
    state = getattr(flow, "state", None)
    if not isinstance(state, dict):
        return ""
    for session in state.get("sessions", {}).values():
        if not isinstance(session, dict):
            continue
        value = session.get("scene")
        if isinstance(value, str) and value:
            return value
    return ""


# --- 可站立高度场（2026-09-19 接线；**默认开启**） --------------------------------
# ⚠️ 与空气墙不同，这里默认开：delta[2] 恒为 0 是「走出出生点就沉地下」的
#    直接原因，关掉等于没修。想关：T7_HEIGHTFIELD=off。
#    场景没有 heightfield.json 时自动不生效（逐位不变）。
_heightfieldModule = None


def heightFieldModule():
    """惰性导入 ``scripts.heightfield``——开关关掉时一次都不 import，零开销。"""
    global _heightfieldModule
    if _heightfieldModule is None:
        from . import heightfield as module
        _heightfieldModule = module
    return _heightfieldModule


def standZ(scene, x, y, zref, src=None):
    """``(x, y)`` 处角色脚底该在的高度 —— **有几何可站立面就信几何，查不到才退回高度场**。

    为什么不「两边取高」（这是踩过的坑）：
    * 高度场每格只有一个 z（``heightfield.json`` 的 ``height`` 是标量数组，按 1 m 格
      取**格内最大值**），楼梯/平台/墙顶这类第二层它表达不出来 → 角色 z 永远停在最
      下面那层，楼梯上不去。
    * 取高 = 站在楼梯脚下会先被抬到「本格里最高的那级台阶」，脚底凭空抬 0.5~0.85 m，
      身体竖段（脚到头顶 1.70 m）就顶进拱顶/上层几何 → 又冻在原地。离线复演同一条线：
      取高走 3.90 m 停，信几何走 7.20 m、z 平滑抬 2.76 m。
    * 反过来「几何比高度场低 0.15 m，会不会沉下去反被墙判挡」这条风险实测过：
      524 个实机站位，信几何比取高只多 1 个原地判挡（43→44），贴墙全冻 0 个。
      可站立面才是真 surface，那 15 cm 本来就该沉。

    ``zref`` = 当前脚底 z，几何那边靠它选层（站在墙顶取墙顶、掉进地下室取地下室），
    窗口外的面当查不到（``STEP_UP`` / ``GROUND_DROP``）。
    两边都没数据 → ``None``，调用方保持原值不动。

    ``src`` = 可选的 list，**只为取证**：调用方传进来时，本函数会把最终采用的那一层
    的名字（``"collide"`` / ``"nav"`` / ``"heightfield"``）append 进去。
    为什么加它（2026-09-27 江陵城「人在地下」）：当时 ``ground-z-diag`` 只记
    ``target=12.7277 z=24.971``，看**值**根本分不出这三层里是哪一层给的答案，
    只能离线拿三份数据源各算一遍。有了 ``src`` 就能一行说清。
    """
    if zref is not None:
        sz = collide_mesh.supportZ(scene, x, y, zref)
        if sz is not None:
            if src is not None:
                src.append("collide")
            return sz
    # 第二层兜底：原版导航网格（anavmesh.dat）就是「原版可行走面」的权威。
    # 高度场是单层的，表达不出楼梯平台/二层（nav=+4.2m 处高度场只有 0.0 → 陷坑）；
    # 普通地面 nav 与高度场只差几厘米（实测中位 -0.04），所以 nav 优先不会伤平地。
    nz = _navZ(scene, x, y, zref)
    if nz is not None:
        if src is not None:
            src.append("nav")
        return nz
    if src is not None:
        src.append("heightfield")
    return heightFieldModule().groundZ(scene, x, y)


# ⚠️ 2026-09-27：由 `None` 改成**按场景名分键的字典**。原写法是单个全局 + `if _navCache is
#    None:`，两个后果都实测到了：
#      ① **不按场景分键** —— 换图测试时 `_navZ` 会拿上一张图的三角形去插值当前图的
#         (x, y)，z 直接错到天上（江陵城 bbox 只有 X 298..676 / Y 232..706，别的图
#         坐标落进去能命中一大片无关三角面）。
#      ② **一个进程只加载一次** —— 场景当时没有 navmesh.json 就永久置 `False`，
#         之后再落盘文件也不重读。江陵城就是这么中招的：`navmesh.json` 14:53 落盘，
#         而 14:40 起的那个进程早已把该场景判成 `False` ⇒ **不重启服务端永远读不到**。
_navCache = {}


def _navSceneDir():
    try:
        from . import contracts as _c
        for node in _c.walkUp():
            path = os.path.join(node, "data", "scene")
            if os.path.isdir(path):
                return path
    except Exception:
        pass
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "scene")


def _navZ(scene, x, y, zref=None):
    """轻量 navmesh 查询：空间哈希 + 重心坐标。只在几何查不到支撑时用。

    同一 (x, y) 若导航网格有上下两层（地面 + 平台顶），``zref`` 给定时取**离当前
    脚底最近**那层，避免把站在平台下的角色瞬移抬到平台顶；``zref`` 为 None 时返回
    首个命中。只有单层时（陷坑处往往只有平台那一层），最近即该层 → 仍能救坑。
    """
    global _navCache
    if scene not in _navCache:
        import json
        path = os.path.join(_navSceneDir(), scene, "navmesh.json")
        try:
            d = json.load(open(path, "rb"))
        except Exception:
            _navCache[scene] = False
            return None
        if not d:
            _navCache[scene] = False
            return None
        v = d["v"]
        t = d["t"]
        B = 2.0
        bk = {}
        for k, tri in enumerate(t):
            xs = [v[i][0] for i in tri]
            ys = [v[i][1] for i in tri]
            for i in range(int(math.floor(min(xs) / B)), int(math.floor(max(xs) / B)) + 1):
                for j in range(int(math.floor(min(ys) / B)), int(math.floor(max(ys) / B)) + 1):
                    bk.setdefault((i, j), []).append(k)
        _navCache[scene] = {"v": v, "t": t, "bk": bk}
    cache = _navCache[scene]
    if not cache:
        return None
    v = cache["v"]
    t = cache["t"]
    bk = cache["bk"]
    B = 2.0
    ci, cj = int(math.floor(x / B)), int(math.floor(y / B))
    bestD = None
    bestZ = None
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            for k in bk.get((ci + di, cj + dj), ()):
                a = v[t[k][0]]
                b = v[t[k][1]]
                c = v[t[k][2]]
                det = (b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])
                if abs(det) < 1e-9:
                    continue
                px, py = x - a[0], y - a[1]
                u = ((c[1] - a[1]) * px + (a[0] - c[0]) * py) / det
                w = ((a[1] - b[1]) * px + (b[0] - a[0]) * py) / det
                if u < -1e-9 or w < -1e-9 or u + w > 1.0 + 1e-9:
                    continue
                z = a[2] + (b[2] - a[2]) * u + (c[2] - a[2]) * w
                if zref is None:
                    return z
                d = abs(z - zref)
                if bestD is None or d < bestD:
                    bestD, bestZ = d, z
    return bestZ



# ==================== 云梯攀爬面（2026-09-29，与 ``siege`` 共用一个 session 键）======
# 数据由 ``siege._syncClimbFace()`` 在云梯立起/倒下时写入，格式：
#   ``"rid,fx,fy,fz,ux,uy,run,rise,half,cap;rid,..."``
# ⚠️ 本键名**必须**与 ``siege.CLIMB_FACES_KEY`` 相等（测试里有一条断言盯着）。
# ⚠️ 这里**刻意不 import siege**（同 ``rigTurning``）：本函数在 50 ms 地面 tick 里
#    被调多次，而爬升面是**纯几何**、只要几个标量 —— 没必要为此拉依赖。
CLIMB_FACES_KEY = "climbFaces"

# 浮点保护容差（米）：见 ``climbFaceZ`` 里 ``t`` 的说明。1 mm 足够覆盖 ulp 误差，
# 又远小于一步位移（≈0.05 m）⇒ 不改变几何语义。
CLIMB_EPS = 1e-3


def climbFaceZ(flow, x, y):
    """``(x, y)`` 落在**某架立起的云梯斜面**上时返回该处高度；否则 ``None``。

    ``None`` = 不由本函数决定（调用方按原路径走高度场 / navmesh）⇒ 关掉
    ``ladder_climb``（session 里没这个键）时**逐位不变**。

    斜面模型：从梯脚 ``F`` 沿单位方向 ``u`` 走 ``t`` 米，高度 ``fz + rise·t/run``；
    ``t ∈ (run, run+cap]`` 是梯顶等高平台（``cap=0`` 时不封顶）；``|n| > half`` 落空。
    """
    blob = flow.session.get(CLIMB_FACES_KEY)
    if not blob:
        return None
    for chunk in str(blob).split(";"):
        parts = chunk.split(",")
        if len(parts) != 10:
            continue
        try:
            fx = float(parts[1]); fy = float(parts[2]); fz = float(parts[3])
            ux = float(parts[4]); uy = float(parts[5])
            run = float(parts[6]); rise = float(parts[7])
            half = float(parts[8]); cap = float(parts[9])
        except (TypeError, ValueError):
            continue
        if run <= 0.0 or half <= 0.0:
            continue
        dx = x - fx
        dy = y - fy
        t = dx * ux + dy * uy                 # 沿梯方向的距离（米）
        # ⚠️ 容差：人物坐标与几何点重合时 ``t`` 会差几个 ulp（实测 ≈4e-7），
        #    严格 ``t < 0`` 会把「站在梯脚正下方」判成「在梯子后面」⇒ 永远上不去。
        if t < -CLIMB_EPS or t > run + cap + CLIMB_EPS:
            continue
        n = dy * ux - dx * uy                 # 横向偏移（左法线）
        if n < -half - CLIMB_EPS or n > half + CLIMB_EPS:
            continue
        if t <= run:
            return fz + rise * (t / run)
        return fz + rise                      # 梯顶平台
    return None


# ============ 客户端权威下的云梯斜面校正（2026-10-07）========================
# 背景（实机：樊城云梯立起来后能**直接穿过去**）：
#   ``localReport`` 只**接受**客户端上报、不回写任何运动 ⇒ 服务端整条运动链
#   （``advanceGround`` → ``groundZAt`` → ``climbFaceZ``）在客户端权威下**根本不跑**，
#   于是 ``siege._syncClimbFace`` 登记好的云梯爬升面**没有任何人读**。
#   证据：会话 ``51992-1437159953-1`` 里 ``siege-ladder-climb rid=10010``
#   （foot=(578.96,583.96,43.49) run=11.26 rise=11.38）登记成功，
#   而 ``client-runtime-position-accepted`` 证明走的就是 ``localReport`` 这条路。
# 做法：每次收到上报位置后查一次斜面；命中且客户端 Z 低于斜面 ⇒ **只改 Z**、
#   水平位置原样带回，用 selector4（``broadcastHeading``）回发。
#   ⚠️ 只改 Z 是**故意的**：水平位置用客户端刚上报的值 ⇒ 不会水平橡皮筋
#   （这正是 ``localReport`` 「不回写运动」那条纪律）；只把垂直方向补上。
# 回退（10 秒）：``ladder_climb=off``（session 里没有 ``climbFaces`` 键 ⇒
#   ``climbFaceZ`` 恒返回 ``None`` ⇒ 本函数第一行就 return，逐字节回到改动前）；
#   或 ``set T7_CC_LADDER_ECHO=0`` 只关回发、保留爬升面登记。
CLIMB_ECHO_ENV = "T7_CC_LADDER_ECHO"
CLIMB_ECHO_ENABLED = True
# 回发节流（毫秒）：客户端采纳后 Z 就到位了、会自动停；不采纳时按这个频率发。
CLIMB_ECHO_MIN_MS = 100
# 探测日志节流（毫秒）：只在「没发回包」时记录早退原因，500ms 一条足够复盘。
CLIMB_PROBE_MIN_MS = 500


def climbEchoEnabled() -> bool:
    override = os.environ.get(CLIMB_ECHO_ENV)
    if override == "0":
        return False
    if override == "1":
        return True
    return CLIMB_ECHO_ENABLED is True


def climbCorrect(flow, ground) -> bool:
    """客户端权威下把云梯斜面高度补给客户端（selector4 回发）。返回是否发了。

    ``climbFaceZ`` 返回 ``None``（没登记面 / 不在斜面范围）⇒ 一个字节都不发，
    与加本函数之前逐位相同。

    ⭐ 2026-10-07 实机复盘：第一版上线后 ``ladder-climb-correct`` **一条都没发**
    （用户「云梯还是踩不上去」），而 ``siege-ladder-climb`` 明明登记成功了
    ⇒ 差值出在**这一函数内部的某个早退分支**，但当时没有日志可见。
    所以加一条**探测日志**（``ladder-climb-probe``，节流
    ``CLIMB_PROBE_MIN_MS``）：命中过面的人每次调用都记 ``(x,y,z) → z_斜面``，
    离线复盘就能一眼看出「早退在哪一支」（no-face / 已达标 / 节流）。
    纯日志，不发包，坏了也不影响回发主链。
    """
    if not climbEchoEnabled():
        return False
    position = ground.get("position")
    if not position or len(position) < 3:
        return False
    z = climbFaceZ(flow, position[0], position[1])
    now = flow.now
    last = ground.get("climbProbeAt")
    if z is None or position[2] >= z - CLIMB_EPS or (
            last is not None and now - last < CLIMB_PROBE_MIN_MS):
        if last is None or now - last >= CLIMB_PROBE_MIN_MS:
            ground["climbProbeAt"] = now
            flow.result["logs"].append(
                "ladder-climb-probe x=%.2f y=%.2f z=%.2f face=%s why=%s"
                % (position[0], position[1], position[2],
                   ("%.2f" % z) if z is not None else "none",
                   "no-face" if z is None else
                   ("reached" if position[2] >= z - CLIMB_EPS else "throttled")))
    if z is None:
        return False
    if position[2] >= z - CLIMB_EPS:
        return False
    lastEcho = ground.get("climbEchoAt")
    if lastEcho is not None and now - lastEcho < CLIMB_ECHO_MIN_MS:
        return False
    flow.result["logs"].append(
        "ladder-climb-correct x=%.2f y=%.2f z=%.2f->%.2f"
        % (position[0], position[1], position[2], z))
    ground["position"] = [position[0], position[1], z]
    ground["climbEchoAt"] = now
    broadcastHeading(flow)
    return True


def groundZAt(flow, x, y, src=None):
    """``(x, y)`` 的可站立高度；``None`` = 不由本模块决定（保持原值不动）。

    ``src`` 透传给 ``standZ()``，只用于取证（见 ``standZ`` 的说明）。

    ⭐ 2026-09-29：**先查云梯攀爬面**。命中就返回斜面高度（并且 ``src`` 记
    ``"ladder-climb"``，便于离线复盘区分「高度场抬的」还是「梯子抬的」）。
    爬升面是**连续**的 ⇒ 每步上行 ≈0.05 m，不会被 ``climbBlocked``(5.5) 挡住。
    """
    climb = climbFaceZ(flow, x, y)
    if climb is not None:
        if src is not None:
            src.append("ladder-climb")
        return climb
    scene = airWallScene(flow)
    if not scene:
        return None
    position = groundState(flow)["position"]
    return standZ(scene, x, y, position[2] if position is not None else None, src)


def airWallEnabled(flow) -> bool:
    """环境变量优先于常量，方便不改代码做单次实机验证。"""
    override = os.environ.get(AIRWALL_ENV_SWITCH)
    if override == "1":
        return True
    if override == "0":
        return False
    return AIRWALL_COLLISION_ENABLED is True


def airWallWalls(flow):
    """按场景加载并缓存空气墙。返回 ``[]`` 表示「这一格不碰撞」。

    加载失败只记一次日志，不抛——碰撞是**附加**能力，坏一个场景不该让整局
    移动结算崩掉（服务端宁可「穿墙」也不能「不动」）。
    """
    scene = airWallScene(flow)
    if not scene:
        if "no-scene" not in _airwallNoticed:
            _airwallNoticed.add("no-scene")
            flow.result["logs"].append("airwall-disabled reason=no-scene")
        return []
    if scene not in _airwallCache:
        try:
            walls = airWallModule().loadScene(scene, AIRWALL_DIR)
        except (OSError, ValueError) as error:
            walls = []
            if scene not in _airwallNoticed:
                _airwallNoticed.add(scene)
                flow.result["logs"].append("airwall-load-failed scene=" + scene
                                           + " error=" + str(error))
        _airwallCache[scene] = walls
        flow.result["logs"].append("airwall-loaded scene=" + scene
                                   + " walls=" + str(len(walls)))
    return _airwallCache[scene]


def airWallBlocked(flow, p0, p1):
    """本步位移是否撞墙。返回 ``None`` 表示放行（开关关掉时恒为 None）。"""
    if not airWallEnabled(flow):
        return None
    walls = airWallWalls(flow)
    if not walls:
        return None
    position = groundState(flow)["position"]
    playerZ = position[2] if (AIRWALL_HEIGHT_FILTER and position) else None
    return airWallModule().blocked(p0, p1, walls, playerZ)


def modelBlocked(flow, p0, p1):
    """本步位移会不会**走进建筑**（``acollision.json``，从 .nif 竖直面抽的）。

    为什么空气墙不够用：``aairwall.xml`` 是关卡边界的 navigation slice，
    樊城只有 5 段，**房子/塔楼/城墙一条都没有** —— 所以「穿模」一直是穿建筑，
    不是穿地图边界。而高度场也堵不住它：那玩意儿每格只有一个地面 Z，
    见本文件 ``CLIMB_MAX_RISE`` 注释里「高度场是单值的」那一段。

    判据是「角色脚到头顶被某一格的墙面竖直区间压住」，所以门洞底下照样走得过
    （那一格的墙面只在 50~56，人在 44~45.7 不压）。

    ⭐ 场景有 ``acollision_mesh.bin``（原版 ``acollisionmap`` 的全量三角面）时，
    走 ``collide_mesh``：身体竖段到三角面的**最短距离** < ``radius`` 才算撞。
    栅格是「足迹」口径 —— 面的 XY 投影碰到哪格哪格就是墙，固有误差半格对角
    0.71 m，坡面会被抹成一条宽带。离线拿实机站位对过质（lysd 327 个站位）：
    栅格误判不可通行 87 个（26.6%），几何口径只剩 27 个（8.3%），真墙挡住率
    98.5%。数据在就用它，没有就回落栅格，两者都不判时行为与接线前逐位相同。

    返回 ``(格号, 区间下沿, 区间上沿, 口径)``；开关关掉 / 场景没这数据 →
    ``None``。几何口径既没有格也没有区间：格号取位移起点的 1 m 格（只为下游
    replay 工具的正则还能匹配），区间两位都填接触点 z。
    """
    position = groundState(flow)["position"]
    if position is None:
        return None
    scene = airWallScene(flow)
    if collide_mesh.load(scene) is not None:
        # 目标处地面更高（上台阶/上坡）时，按**抬脚之后**的高度判挡 ——
        # 否则第二级台阶的踢脚高出脚底就超过 stepTol，人永远迈不上去。
        # 用的是 groundZAt 同一个 standZ：走一步之后脚底落在哪，跟地面跟随一个口径。
        # 两边都没数据时退回当前脚底 z，别让 blocked 拿到 None。
        zref = standZ(scene, p1[0], p1[1], position[2])
        if zref is None:
            zref = position[2]
        got = collide_mesh.blocked(scene, p0, p1, zref)
        if got is None:
            return None
        return (collide.cellIndex(scene, p0[0], p0[1]), got[1], got[1], "mesh")
    gridHit = collide.blocked(scene, p0, p1, position[2])
    if gridHit is None:
        return None
    return gridHit + ("grid",)


def groundBlocked(ground: dict) -> bool:
    """上一拍是否被空气墙拦下。

    这是给**不经过 advanceGround 的广播路径**用的（目前只有快跑 sel=63 的
    状态回声）。否则会出现「贴着墙按 SHIFT，服务端又发一次移动中状态，
    客户端继续本地预测前冲」的漏网。开关关闭时恒为 False。
    """
    return ground.get("airwallBlocked") is True


# E_MOVE_KEY_CATEG：CS_PROTO_MOVE_KEY_MSG.key_categ 的类别值，
# 取自客户端 TDR metalib sh_proto_cs（声明顺序即值）。
MOVE_KEY_CATEG_WASD = 1
MOVE_KEY_CATEG_CTRL = 2
MOVE_KEY_CATEG_SPACE = 3

# --- 快跑 / SHIFT 加速（CS_PROTO_MOVE_FAST_RUN_REQ） -----------------------------
# 证据（2026-09-18，全部来自客户端宏表与实机报文，非推断）：
#
# 1. 宏表 `E_CS_PROTO_MOVE_FAST_RUN_REQ = 63`「快跑请求」。
# 2. TDR 结构 CS_PROTO_MOVE_FAST_RUN_REQ 只有一个字段
#    `@0 int8 is_start`「是否开始快跑」，所以 body 恰好 3 字节：
#    u16 selector(=63) + int8 is_start（实机上行出现过 003f01 / 003f00）。
# 3. 宏表 `KEY_RUN = 26`「快跑状态」、`KEY_COND_RUN = 53`「快跑」、
#    `KEY_COND_WALK = 54`「非快跑」，且战斗 key_comb 里 bit26 就是 KEY_RUN
#    （实测 key_comb=67109005 = bit26|0x8D 的右砍，=0x4000000|0x8D）。
# 4. 客户端**不会**把快跑写进 sel=52 的 MOVE_KEY 位图——`E_MOVE_KEY_CATEG`
#    只有 INVALID/WASD/CTRL/SPACE 四类，没有快跑类别。所以快跑只能走 sel=63。
#
# 未闭合（请勿当成已恢复）：
#   * sel=63 与 **SHIFT 单键**的绑定尚未由实机单键测试确认。7 个会话里各只出现
#     1~2 次，且都落在 SPACE 按下与松开之间。因此本实现只按报文自身语义
#     （is_start 是否开始快跑）驱动，**不假设它一定来自 SHIFT**；SHIFT 的按键位
#     （key_comb bit21）由 battle.py 侧记录。
#   * `is_start` 的正文无法从抓包直接读出（C2S 负载加密），只能由服务端解密后的
#     处理路径取得，故此处保留完整 hex 落日志以便离线比对。
FAST_RUN_SELECTOR = 63
FAST_RUN_BODY_LENGTH = 3

# --- 跳跃（未闭合项已逐条标注） -------------------------------------------------
# 证据：Hero72 总表第六节 —— CMD_JMP -> GeGameInput bit5 -> root2/sub52 category3，
# 并发布 GeLevelEventJumpUp；可表达跳跃表现的正式下行是 root2/sub55
# BC_ACTOR_WITH_JUMP 加 sub39 的 jump/jump_animation 附属标志。
# 总表同节明确：**仅靠这两个报文不足以真正离地**，还需要服务端垂直速度、IN_AIR、
# 位置回流和落地状态。本模块把这四项都补上，但下面两个物理常量是占位值。
JUMP_TIMER = "jump-step"
# 待实机标定：原服的跳跃初速度与重力均**未闭合**，下列数值是按“走速
# GROUND_STEP_DISTANCE/GROUND_STEP_MS = 5 单位/秒”量级推的占位值，不是原服数值。
# 实测若跳跃过高/过低/过快，只改这两个常量即可。
JUMP_INITIAL_VELOCITY = 5.0   # 单位/秒，起跳瞬间的垂直速度
JUMP_GRAVITY = 18.0           # 单位/秒²，垂直加速度（向下）

# --- ⭐⭐⭐ 2026-10-07：客户端权威输入同步（跳跃闸门 + 下蹲回声）----------------
# 证据链（对比 git e29de12 老版 = 服务端权威时期的完整跳跃状态机）：
#   * 老版 ``jumpActive()`` 闸门 —— 空中再按空格不起跳 ⇒「正常的跳」；
#   * v0.2.0 客户端权威迁移时**状态机没移植**（本文件只剩注释与常量）⇒ 客户端
#     本地「想跳就跳」（无限跳）、Ctrl 下蹲没有服务端确认；
#   * ``localReport`` 其实**已经在记** ``keys[4]=下蹲 / keys[5]=跳跃``
#     （上游同款记账），只是记账之后没有任何动作 —— 本轮把动作接上：
#     跳跃按下沿 → ``notifyInAir(1)``（客户端 DriveEnable 关 ⇒ 空中跳不了），
#     ``JUMP_AIR_MS`` 后定时器解除；下蹲沿 → sel=39 下蹲动画广播
#     （老版实测取值 SQUAT=1 / END_SQUAT=2）。
#   回退：``[move] input_sync=off`` 或环境变量 ``T7_CC_INPUT_SYNC=0``。
JUMP_AIR_TIMER = "jump-air"
JUMP_AIR_MS = 700            # 滞空期：2·v/g = 2·5/18 ≈ 555ms + 落地缓冲
# ⭐ 2026-10-07 第三十八轮：落地动画尾巴的两个后续拍（老 ``stepJump`` 三拍：
#   着陆(5) → 结束着陆(6) → NONE(0)）。缺了尾巴客户端会停在落地姿势。
JUMP_AIR_END_TIMER = "jump-air-end"
JUMP_AIR_NONE_TIMER = "jump-air-none"

# --- ⭐⭐⭐ 2026-10-07 第四十一轮：客户端权威的「移动状态镜像」 -------------------
# 实机（会话 16:28 那局）证明客户端的**走/跑表现**是跟着服务端 MOVE_BC 的
# ``state`` 走的：老版服务端权威每 50ms 重发一次，所以「按 W 一会就进跑」
# （用户原话「老版本按 W 一会 SHIFT 就显示了」）；迁到客户端权威后服务端
# **平时一条 MOVE_BC 都不发** ⇒ 用户口供「shift 冲刺没有出来 / ~ 空手跑也没出来」。
# 本轮把「按变化镜像」补回来（老版 = 每 50ms 无条件重发，这里 = 只在变化时发）：
#   * 按键/快跑**变化** ⇒ 镜像一条走/跑/停的 MOVE_BC；
#   * 起步沿 ⇒ 排 ``cc-run-delay`` 定时器，``GROUND_RUN_DELAY_MS`` 后补一条「跑」
#     （这就是「按 W 一会就进跑」那条，老版靠 50ms 周期帧实现）。
# 回退（10 秒）：``[move] cc_move_mirror=off`` 或环境变量 ``T7_CC_MOVE_MIRROR=0``。
CC_RUN_TIMER = "cc-run-delay"

# --- ⭐⭐⭐ 2026-10-07 第四十轮：客户端权威广播的位置外推 -------------------------
# 客户端上行只有 ~3 帧/秒（会话 -12 实测：``input-report-diag`` 相邻两条 1.0~2.9s，
# 真实包更密也远慢于服务端 50ms 节拍）。而我们的跳跃/下蹲广播（sel=55/39）发生在
# 两帧**之间** ⇒ 直接拿 ``ground["position"]``（= 最后一帧的坐标）发出去，客户端
# 会被**拽回**最多半秒前的坐标 —— 用户口供「按完空格，W 变后退一段距离」就是它。
# 这里按最后一次上报的**水平速度**线性外推，上限 ``CLIENT_EXTRAP_MAX_MS``
# （≈ 一个上报间隔）；超过就认为客户端已经停了（停住就不再上报位置），不外推。
CLIENT_EXTRAP_MAX_MS = 350
# 外推位移的硬上限（米）。就算 dt 在闸门内，速度异常大时也不许把人瞬移出去。
CLIENT_EXTRAP_MAX_M = 1.5


def inputSyncEnabled() -> bool:
    """客户端权威输入同步（跳跃闸门 + 下蹲回声）总开关。默认 on。"""
    override = os.environ.get("T7_CC_INPUT_SYNC")
    if override == "0":
        return False
    if override == "1":
        return True
    return _moveIniFlag("input_sync", True)


def jumpStateMirrorMode() -> str:
    """跳跃/下蹲广播里的 ``state`` 怎么填（第四十轮应急旋钮）。

    ``mirror``（默认）= 镜像客户端上报的按键掩码（老版服务端权威的口径：
    按着 W 起跳就发「走」档，松手那一拍由 ``_mirrorStop`` 补 STOP）。
    ``stop``         = 永远发静止档 —— 如果实机发现「跳跃广播一写移动驱动状态
    客户端就停不下来」，一行切到这个，服务端就完全不碰客户端的移动状态。

    旋钮：``[move] cc_jump_state`` / 环境变量 ``T7_CC_JUMP_STATE``。
    """
    override = os.environ.get("T7_CC_JUMP_STATE")
    if override in ("mirror", "stop"):
        return override
    value = _moveIniText("cc_jump_state", "mirror")
    return value if value in ("mirror", "stop") else "mirror"


def ccMoveMirrorEnabled() -> bool:
    """客户端权威「移动状态镜像」开关（第四十一轮）。默认 on。

    旋钮 ``[move] cc_move_mirror`` / 环境变量 ``T7_CC_MOVE_MIRROR``。
    关掉 = 回到「服务端平时一条 MOVE_BC 都不发」的旧行为（逐位相同）。
    """
    override = os.environ.get("T7_CC_MOVE_MIRROR")
    if override == "0":
        return False
    if override == "1":
        return True
    return _moveIniFlag("cc_move_mirror", True)


def footAxisMode() -> str:
    """客户端权威下**步兵** MOVE_BC 的 ``forward_back`` / ``left_right`` 符号（第四十二轮）。

    ``client``（默认）= **取负** —— 客户端约定：按 W 必须发 ``fb=+1000``。
      一手证据见 ``displayAxes`` 里 prior_art《2026年8月9日四方向反向与移动过快
      三倍修正》那段：「当前 W 事件的 wire 表现为 fb=-1000，而历史成功抓包 W 为
      fb=+1000；**这解释 W 显示后退步态**」。
    ``world``         = 历史字面值（W ⇒ −1000），A/B 用；等于逐位回到第四十一轮之前。

    只在**客户端权威 + 步兵**生效（服务端权威与骑兵都不受影响，见 ``displayAxes``）。
    旋钮：``[move] cc_foot_axis`` / 环境变量 ``T7_CC_FOOT_AXIS``。
    """
    override = os.environ.get("T7_CC_FOOT_AXIS")
    if override in ("client", "world"):
        return override
    value = _moveIniText("cc_foot_axis", "client")
    return value if value in ("client", "world") else "client"

# --- MOVE_STATE_DATA_ANIMATION_*：动画索引的**实测值**，不是推断 -----------------
# 来源：2026-09-18 用 `scripts/codec/tdr.py` 直接从客户端 TieJiClient.exe 的
# sh_proto_cs 宏表读出（`parse_tdr_macro(block, macro_name=...)`）。
#   MOVE_STATE_DATA_ANIMATION_NONE        = 0  移动数据无动画
#   MOVE_STATE_DATA_ANIMATION_SQUAT       = 1  下蹲          ← 曾误用为“跳跃动画”
#   MOVE_STATE_DATA_ANIMATION_END_SQUAT   = 2  下蹲结束
#   MOVE_STATE_DATA_ANIMATION_SUDDEN_STOP = 3  马撞停
#   MOVE_STATE_DATA_ANIMATION_JUMP        = 4  起跳
#   MOVE_STATE_DATA_ANIMATION_JUMP_LAND   = 5  着陆缓冲
#   MOVE_STATE_DATA_ANIMATION_END_JUMP    = 6  结束着陆
#
# ⚠️ 这是 2026-09-18 实机“按空格变成蹲下了、起不来了”的**根因**：
# sel=55/39 的 `jump_animation` 是**该枚举的索引**，不是布尔标志。旧实现发 1，
# 而 1 恰好是 SQUAT（下蹲），所以客户端播的是下蹲动画并且停在那一帧。
# 正确取值：起跳 4、着陆缓冲 5、结束着陆 6、收尾回 0(NONE)。
MOVE_ANIMATION_NONE = 0
MOVE_ANIMATION_SQUAT = 1
MOVE_ANIMATION_END_SQUAT = 2
MOVE_ANIMATION_SUDDEN_STOP = 3
MOVE_ANIMATION_JUMP = 4
MOVE_ANIMATION_JUMP_LAND = 5
MOVE_ANIMATION_END_JUMP = 6

# 落地动画两拍的占位时长（各阶段 duration 未闭合，待实机标定）。
JUMP_LAND_MS = 200
JUMP_END_MS = 200

# 跳跃阶段机：AIR 做垂直积分，LAND/END 只播落地动画尾巴，END 之后必须回到 NONE，
# 否则会像旧实现那样把角色停在某一帧上（“起不来了”）。
JUMP_PHASE_IDLE = 0
JUMP_PHASE_AIR = 1
JUMP_PHASE_LAND = 2
JUMP_PHASE_END = 3

# 跳跃广播里的 state 字段取值未闭合：待机起跳用 MOVE_GROUND_STATE_STOP，
# 移动中起跳沿用地面积分算出的走/跑 state，不额外发明新 state 编号。
_JUMP_DEFAULT = {"active": 0, "phase": 0, "pressed": 0, "z": 0.0, "vz": 0.0,
                 "groundZ": 0.0}


def activate(flow):
    """进场后激活玩家 movable（``MOVE_NOTIFY_ACTIVE``，cmd=2 / sel=0x1F）。

    ``server_tick`` 用 ``nextGroundTick(flow)``（进图起递增毫秒），并回写
    ``ground["tick"]`` / ``["timingTick"]`` —— 与上游 ``controls.activate`` 同口径。
    语义依据：该字段是客户端 movable 的**时钟基准**；硬写 1 等于宣称
    「本 movable 在进图第 1ms 激活」，与真实实例时钟脱节。

    ⚠️ 与上游的**唯一**分歧：``active`` 仍写死 1，不用上游的
    ``int(groundEnabled(flow))``。理由：本项目 ``scene.py`` 会在
    ``VISION_GET_OBJECTS``（0xE/7）里**直接**调 ``activate``，那时
    ``battleEntered`` 还没置位 ⇒ ``groundEnabled`` 为 False ⇒ 上游写法会发出
    ``active=0``（**取消**激活）。这里保持既有行为，不动这个字节。
    """
    tick = nextGroundTick(flow)
    # 实机档：``active`` 写死 1（见下方注释里的分歧说明）。契约档（``wire.contract_mode()``）
    # 回落到上游合成骨架的 ``int(groundEnabled)``，供 vendored 参考套件对齐。
    active = (int(groundEnabled(flow)) if wire.contract_mode() else 1)
    flow.send(2, move_flow.encode_move_notify_active(
        server_tick=tick, target_instance_id=1, active=active),
        "instance-move-notify-active-after-in-scene")
    ground = groundState(flow)
    ground["tick"] = ground["timingTick"] = tick


def project(mask, heading, stepDistance=None):
    return move_flow.project_standard_ground_step(
        w_pressed=mask & 1, a_pressed=(mask >> 1) & 1,
        s_pressed=(mask >> 2) & 1, d_pressed=(mask >> 3) & 1,
        heading_degrees=heading,
        step_distance=wire.GROUND_STEP_DISTANCE if stepDistance is None else stepDistance)


def groundEnabled(flow) -> bool:
    if not flow.session.get("battleEntered"):
        return False
    if "groundEnabled" in flow.session:
        return flow.session["groundEnabled"] is True
    return flow.session.get("phase") == "game-sent"


def runtimeMovement(flow) -> bool:
    """宿主注入的「客户端权威移动」开关（对齐 t7-rekindle 上游）。

    原生启动器在内存客户端 overlay 装好后把 ``runtimeMovement`` 写进 context
    （``Runtime/core/Common.h`` 的 ``bool runtimeMovement = true`` 与
    ``Runtime/server/PythonHost.cpp`` 的 ``put(dict, "runtimeMovement", ...)``），
    ``app.createState`` 再把它落到 state。自建的 ``T7.Server.exe`` 不注入
    ⇒ 默认 False ⇒ 本模块行为与加这个闸门之前**逐位相同**。

    ⚠️ 这里走 ``getattr`` 而不是上游的 ``flow.state.get(...)``：离线仿真桩
    （``verify_mount_turn.Flow``）没有 ``state`` 属性，直接取会 AttributeError。
    取不到就按「宿主未注入」处理 ⇒ 与改动前同口径。
    """
    state = getattr(flow, "state", None)
    if not isinstance(state, dict):
        return wire.CLIENT_RUNTIME_MOVEMENT is True
    return state.get("runtimeMovement", wire.CLIENT_RUNTIME_MOVEMENT) is True


def movementMode(flow) -> str:
    return wire.RUNTIME_MOVEMENT_MODE if runtimeMovement(flow) else "server-ground-v1"


def cancelMotionTimers(flow) -> None:
    """停掉全部**服务端驱动**的运动定时器。

    上游只取消前三个（它没有跳跃状态机）。本项目多了 ``JUMP_TIMER``，
    客户端权威模式下跳跃由客户端自己算，服务端不该再逐拍推进。
    """
    for name in ("ground-step", "direction-prime-start", "direction-prime-stop",
                 JUMP_TIMER):
        flow.cancel(name)


def localReport(flow, selector, body, *, mirror=True) -> bool:
    """客户端权威模式：只**接受**客户端上报的位置/朝向/按键，不回写任何运动。

    ``mirror``（默认 True）= 是否下发镜像/同步帧（cmd=2 的走/跑/停、
    跳跃闸门、下蹲回声、云梯斜面回发）。契约档（``wire.contract_mode()``）传
    ``mirror=False``：仍更新本地快照、仍校验数值，但**不下发任何帧**，回落到
    上游合成骨架的 ``flow.result["send"] == []``，供 vendored 参考套件对齐。

    与上游 ``controls.localReport`` 同口径。字段布局照抄上游：
      selector 3  = 朝向 + 位置（21B；heading@7 int16，position@9 三只 float32）
      selector 52 = 按键 + 位置（24B；category@2 int32，keys@6..11，position@12）

    ⚠️ 上游的 selector 集合只有 (3, 52)；本项目还多一个快跑 selector(63)，
    那不在本函数职责内（见 ``handleFastRun`` 里的 runtimeMovement 分支）。
    """
    if selector == 3:
        wire.exact(body, 21, "runtime-local-heading")
        heading = struct.unpack_from(">h", body, 7)[0]
        if not -180 <= heading <= 180:
            raise ValueError("runtime local heading must be in -180..180")
        position = list(struct.unpack_from(">fff", body, 9))
    elif selector == 52:
        wire.exact(body, 24, "runtime-local-key-state")
        category = struct.unpack_from(">i", body, 2)[0]
        keys = body[6:12]
        # ⭐⭐ 输入诊断（2026-10-07 第三十四轮）：会话 -8 实机复盘 —— 新代码
        #   已加载（快照 grep 证实）但 7 分钟对局 0 次 jump-gate / 0 次下蹲帧，
        #   唯一的 move-in-air-state-bc 是进场景瞬间的杂散触发 ⇒ `_syncJumpAndCrouch`
        #   根本没看到真实按键。嫌疑：客户端的跳跃/CTRL 不在 category=1 的
        #   keys[4]/keys[5] 位，或走了别的 selector。在 category 过滤**之前**
        #   节流记一条，让下次实测能直接看到上报分布。纯日志，节流 2s。
        _inputDiag(flow, selector, category, keys, body)
        if category not in (MOVE_KEY_CATEG_WASD, MOVE_KEY_CATEG_CTRL,
                            MOVE_KEY_CATEG_SPACE):
            return False
        if any(value not in (0, 1) for value in keys):
            raise ValueError("runtime local key state must be 0 or 1")
        if category != MOVE_KEY_CATEG_WASD:
            # ⭐⭐⭐ 2026-10-07 第三十五轮（会话 -9 实证）：CTRL 走 **cat=2**
            #   （input-report-diag sel=52 cat=2 keys=00 00 00 00 01 00 —— keys[4]
            #   蹲位），SPACE 走 cat=3（TDR ``E_MOVE_KEY_CATEG``，见 :700 注释）。
            #   客户端权威下 ``message()`` 把 sel=52 **全部**路由到本函数，旧代码
            #   在这里把 cat=2/3 原样退回 ⇒ 跳跃/下蹲永远 0 触发（会话 -8/-9
            #   两轮实机 0 次 jump-gate 的根因）。现在：这两类包只做跳跃/下蹲
            #   同步，**不碰位置账本**（cat=2/3 的 position 语义未实证，先不采）。
            if not groundEnabled(flow):
                return True
            ground = groundState(flow)
            ground["crouched"] = bool(keys[4])
            ground["jumpPressed"] = bool(keys[5])
            if mirror and inputSyncEnabled():
                try:
                    _syncJumpAndCrouch(flow, ground, keys)
                except Exception:  # noqa: BLE001
                    pass
            return True
        position = list(struct.unpack_from(">fff", body, 12))
    else:
        return False
    if not all(math.isfinite(value) for value in position):
        raise ValueError("non-finite runtime local position")
    if not groundEnabled(flow):
        return True
    cancelMotionTimers(flow)
    ground = groundState(flow)
    if ground["position"] is None:
        flow.result["logs"].append(
            "client-runtime-position-accepted; no-motion-echo")
    # ⭐ 第四十轮：先记「上报速度」，再覆盖位置（顺序不能反 —— 外推要用上一帧）。
    _recordReportVelocity(flow, ground, position)
    ground["position"] = position
    if selector == 3:
        ground["heading"] = heading
    else:
        prevMask = ground.get("mask", -1)
        ground["mask"] = sum(keys[index] << index for index in range(4))
        # 上游同款记账：keys[4] = 下蹲、keys[5] = 跳跃。客户端权威模式下服务端
        # 不据此发包，只留给日志/诊断用（缺了会让上游 pytest 的
        # test_runtime_reports_update_local_snapshot_without_server_echo 报 KeyError）。
        ground["crouched"] = bool(keys[4])
        ground["jumpPressed"] = bool(keys[5])
        # ⭐⭐⭐ 2026-10-07：输入同步（跳跃闸门 + 下蹲回声）—— 记账之外真正干活。
        #   包 try：附加能力，坏了不许拖垮「接受上报位置」这条主链。
        #   契约档（mirror=False）跳过：不下发跳跃/下蹲同步帧。
        if mirror and inputSyncEnabled():
            try:
                _syncJumpAndCrouch(flow, ground, keys)
            except Exception:  # noqa: BLE001
                pass
        # ⭐⭐⭐ 第四十一轮：按键**变化** ⇒ 镜像一条走/跑/停的 MOVE_BC
        #   （见 ``_mirrorMoveState``）。放在跳跃/下蹲同步**之后**：那一支刚刚可能
        #   发过一帧「在移动」的驱动状态，本条正好把它收尾 / 校正。
        #   只在**真正的变化沿**发 —— 客户端站着不动时会持续上报 keys=0。
        #   （快跑标志的变化走 ``handleFastRun``，不在这里。）
        #   ⭐⭐⭐ 2026-10-07 第四十三轮：**把「松手补 STOP」从 ``cc_move_mirror`` 解绑**。
        #   为什么：原先整段都挂在 ``ccMoveMirrorEnabled()`` 上 ⇒ 把旋钮改 off 做
        #   A/B 时，「走/跑镜像」和「松手补 STOP」**一起**没了，A/B 不干净（一次动
        #   了两个变量）。而 STOP 帧 FB/LR=0、无方向冲突，正是最不该被 A/B 牵连的
        #   那条 —— 它是第四十轮「一直在跑停不下来」的修复。现在：
        #     · 镜像 on  ⇒ 走原路（``_mirrorMoveState`` 内部自己会在松手沿补 STOP）；
        #     · 镜像 off ⇒ 仍然只补那条松手 STOP，一个带方向的帧都不发。
        if mirror and inputSyncEnabled() and ground["mask"] != prevMask:
            try:
                if ccMoveMirrorEnabled():
                    _mirrorMoveState(flow, ground, prevMask, ground.get("fastRun", 0))
                elif ground["mask"] <= 0 and prevMask not in (0, -1):
                    _mirrorStop(flow, ground)
            except Exception:  # noqa: BLE001
                pass
    # ⭐ 2026-10-07：客户端权威下把云梯斜面高度补回来（见 ``climbCorrect``）。
    #   放最后：位置账本已经记完，这里只在命中斜面时**改写 Z 并回发**。
    #   包 try：这是附加能力，坏了也不该让「接受上报位置」这条主链崩。
    #   契约档（mirror=False）跳过：不下发云梯斜面回发帧。
    if mirror:
        try:
            climbCorrect(flow, ground)
        except Exception:  # noqa: BLE001
            pass
    return True


def enableGround(flow) -> None:
    if runtimeMovement(flow):
        # 客户端权威：只翻开关 + 广播一次 active，**不启动**任何服务端运动定时器。
        cancelMotionTimers(flow)
        wasEnabled = groundEnabled(flow)
        flow.session["groundEnabled"] = True
        if not wasEnabled:
            activate(flow)
        return
    if not groundEnabled(flow):
        ground = groundState(flow)
        if ground["mask"] != -1:
            ground.update(mask=-1, moveStartedAt=None, lastAdvanceAt=None, nextDeadline=None,
                          airwallBlocked=False)
        # 快跑标志与位移一起复位：换局后客户端会重新发快跑请求。
        ground["fastRun"] = 0
        for name in ("ground-step", "direction-prime-start", "direction-prime-stop",
                     JUMP_TIMER):
            flow.cancel(name)
        resetJump(flow)
    flow.session["groundEnabled"] = True


def groundState(flow):
    ground = flow.session.setdefault("ground", {"mask": -1, "heading": 0,
                                               "position": None, "tick": 0})
    ground.setdefault("moveStartedAt", None)
    return ground


def _recordReportVelocity(flow, ground, position) -> None:
    """记下客户端**上报速度**（供 ``clientAuthorityPosition`` 外推用）。

    只存**标量/浮点列表**：会话快照会被原生层 ``host_runtime._pack`` 逐值校验，
    tuple 会抛 ``TypeError: unsupported state type: tuple`` 并**丢弃整个事件**
    （会话 -10 血泪，见 ``_syncJumpAndCrouch`` 同款注释）。所以速度拆成两个
    float（``reportVx``/``reportVy``），上一帧位置存成 list。

    任何一步不成立都静默跳过 —— 这是给广播用的附加信息，坏了不许拖垮
    「接受上报位置」这条主链。
    """
    try:
        prevAt = ground.get("reportAt")
        prevPos = ground.get("reportPrev")
        now = flow.now
        if (type(prevAt) is int and type(prevPos) is list and len(prevPos) >= 2
                and now > prevAt):
            dt = (now - prevAt) / 1000.0
            if dt > 0:
                ground["reportVx"] = (position[0] - prevPos[0]) / dt
                ground["reportVy"] = (position[1] - prevPos[1]) / dt
        ground["reportPrev"] = [float(position[0]), float(position[1]),
                                float(position[2])]
        ground["reportAt"] = int(now)
    except Exception:  # noqa: BLE001
        pass


def clientAuthorityPosition(flow, ground, extrapolate=True):
    """客户端权威广播用的位置：把最后一次上报按上报速度外推到当前时刻。

    背景与上限来历见 ``CLIENT_EXTRAP_MAX_MS`` 的注释。任何一步不成立
    （没上报过 / 还没算出速度 / 时间倒流 / 超过外推窗口 / 位移超硬上限）
    ⇒ **原样返回账本位置**，与加这个函数之前逐位相同。所以服务端权威模式
    （``localReport`` 从不被调用、这些键根本不存在）行为一个字节都不变。
    """
    position = ground.get("position")
    if position is None:
        return wire.POSITION
    if not extrapolate:
        return position
    reportAt = ground.get("reportAt")
    vx = ground.get("reportVx")
    vy = ground.get("reportVy")
    if type(reportAt) is not int or vx is None or vy is None:
        return position
    dt = flow.now - reportAt
    if dt <= 0 or dt > CLIENT_EXTRAP_MAX_MS:
        return position
    dx = vx * dt / 1000.0
    dy = vy * dt / 1000.0
    if math.hypot(dx, dy) > CLIENT_EXTRAP_MAX_M:
        return position
    return [position[0] + dx, position[1] + dy, position[2]]


# 服务端**第一次**建立角色位置时的正常范围（米）—— 见 ``anchorToSpawn``。
SPAWN_ACCEPT_RADIUS = 150.0


def anchorToSpawn(flow, position):
    """把客户端首包报的离谱 ``curr_pos`` 退回出生点，别拿它当角色的位置。

    为什么要有这道闸门（会话 9356-110117498，2026-09-21 实机，两条 instance 连接
    同在一个抓包里，可以直接对比）：

      * 连接 69（骑兵）17:27:50 首个带位置的包报的是 ``(0,0,0)``。服务端照单全收，
        之后每 50ms 把 ``(0,-0.25,0)`` 之类的值回显给客户端（``move-ground-periodic-
        position-echo``），角色被钉死在世界原点 —— 图外、天上、没有地形，客户端判
        「逃离战场，你已被处决」。同刻的服务端下行明文可见：
        ``instance-ground-initial-stop`` 里服务端自己发的仍是出生点
        ``(503.179, 581.889, 43.248)``，也就是客户端**没**把自己摆进场景。
      * 连接 70（步兵）同一处报的就是出生点，全程正常（1294 次位置回显、跳跃、
        攻城器械交互都对）。

    客户端为什么报 0 还没闭合（C2S 载荷是 method-3 密文，这个抓包里读不到 body）。
    但服务端不该跟着把角色搬去原点：这里只拦「服务端还没建立跟踪」那一次，
    超出半径就用 ``wire.POSITION``（服务端下发给客户端的那个出生点）顶上；
    之后每步都由 ``advanceGround`` 积分，不再经过这里，复活/传送不受影响。
    """
    spawn = list(wire.POSITION)
    distance = math.hypot(position[0] - spawn[0], position[1] - spawn[1])
    if distance <= SPAWN_ACCEPT_RADIUS:
        return position
    flow.result["logs"].append(
        "ground-position-reanchored scene=" + str(flow.session.get("scene"))
        + " from=" + ",".join("%.2f" % value for value in position)
        + " dist=" + "%.1f" % distance
        + " max=" + str(SPAWN_ACCEPT_RADIUS)
        + " to=" + ",".join("%.2f" % value for value in spawn))
    return spawn


def groundMoveState(ground: dict, projection: move_flow.GroundMoveProjection,
                    now: int, runFallback: bool = True) -> int:
    if not projection.moving:
        ground["moveStartedAt"] = None
        return move_flow.MOVE_GROUND_STATE_STOP
    if ground["moveStartedAt"] is None:
        ground["moveStartedAt"] = now
    axes = (projection.forward_back, projection.left_right)
    elapsed = now - ground["moveStartedAt"]
    # 走/跑的选择有**两条**来源，取并集（都不成立才是走）：
    #   1. 客户端显式发来的 CS_PROTO_MOVE_FAST_RUN_REQ（ground["fastRun"]）——SHIFT 加速；
    #   2. 原有兜底：持续前向移动超过 GROUND_RUN_DELAY_MS 自动进跑。
    # 做成并集而不是替换，是为了**不改动**已实机验收的 WASD 行为：客户端不发
    # sel=63 时 fastRun 恒为 0，判断结果与改动前逐位相同（t7_wasd_verify.py 有回归门）。
    # ⭐⭐⭐ 2026-10-07 第三十九轮（客户端权威专用）：``runFallback=False`` 时
    #   **只认客户端显式发来的 ``fastRun``（SHIFT）**，不启用「持续前向 2s 自动进跑」
    #   那条服务端兜底。理由：客户端权威下位移与走/跑**全由客户端决定**，而
    #   ``moveStartedAt`` 只由服务端路径推进（localReport 不推进它），一旦被某次
    #   广播写进就只增不减 ⇒ ``elapsed`` 恒 > 2s ⇒ 服务端广播「跑」而客户端本地是
    #   「走」，两边状态打架（用户口供：按 W 一会「两个都启动了」）。
    run = bool(ground.get("fastRun")) or (runFallback and elapsed >= GROUND_RUN_DELAY_MS)
    if axes in GROUND_RUN_STATES and run:
        return GROUND_RUN_STATES[axes]
    return GROUND_WALK_STATES[axes]


def nextDeadline(deadline: int, now: int) -> int:
    if deadline > now:
        return deadline
    return deadline + ((now - deadline) // wire.GROUND_STEP_MS + 1) * wire.GROUND_STEP_MS


def groundTiming(flow, ground: dict) -> None:
    pending = flow.session.get("pending", {}).get("ground-step")
    timerDue = getattr(flow, "timerDue", None)
    valid = (ground.get("timingTick") == ground["tick"]
             and type(ground.get("lastAdvanceAt")) is int
             and type(ground.get("nextDeadline")) is int)
    if not valid:
        ground["lastAdvanceAt"] = flow.now
        ground["nextDeadline"] = (pending if pending is not None else
                                  timerDue if timerDue is not None else
                                  flow.now + wire.GROUND_STEP_MS)
        ground["timingTick"] = ground["tick"]
        flow.result["logs"].append("ground-timing-rebase tick=" + str(ground["tick"]))
    if flow.now < ground["lastAdvanceAt"]:
        raise ValueError("ground monotonic time moved backwards")
    if pending is None and timerDue is None:
        ground["nextDeadline"] = nextDeadline(ground["nextDeadline"], flow.now)
        flow.later("ground-step", ground["nextDeadline"] - flow.now)
        flow.result["logs"].append("ground-timing-missing-timer repaired")


def ledgeBlocked(flow, old_pos, new_pos) -> bool:
    """横向这一步是否会跨过一个「悬崖」（落差 > LEDGE_MAX_DROP）。

    见 LEDGE_MAX_DROP 注释：挡住**城墙边缘 / 屋顶边缘**，但放行楼梯下降。
    高度场没开 / 没数据 / 在网格外时恒为 False（不改变原有行为）。
    """
    if jumpActive(flow):
        return False          # 跳跃中放行：主动跳下城墙/屋顶
    z_old = groundZAt(flow, old_pos[0], old_pos[1])
    z_new = groundZAt(flow, new_pos[0], new_pos[1])
    if z_old is None or z_new is None:
        return False
    return (z_old - z_new) > LEDGE_MAX_DROP


def ledgeHold(flow, ground) -> bool:
    """边缘阻挡的「按住放行」计时。返回 ``True`` = 这一步**仍然挡住**。

    硬挡会把玩家困在屋顶（实机：同一格被挡 492 次，反馈「撞空气墙不动了」）。
    所以做成：走到崖边先停住，但**一直推着同一个方向**超过 ``LEDGE_HOLD_MS``
    就判定为「故意走下去」→ 放行，日志 ``move-ledge-step-off``。
    这样既防误掉，又永远不会困住。
    """
    since = ground.get("ledgeSince")
    if since is not None and flow.now - since >= LEDGE_HOLD_MS:
        ground["ledgeSince"] = None
        return False                      # 按够了 → 放行
    if since is None:
        ground["ledgeSince"] = flow.now   # 第一次被挡，开始计时
    return True                           # 还没按够 → 继续挡


def climbMaxRise():
    """上行爬升阈值（米）。环境变量优先于常量，方便不改代码做单次实机验证。

    ``T7_CLIMB=off`` / ``0`` → 0.0（不限制，回到 2026-09-20 之前的行为）；
    ``T7_CLIMB=6.0`` → 临时换成 6.0 米（更松，但离墙内壁 7.7845 只剩 1.3 倍余量）。
    """
    override = os.environ.get(CLIMB_ENV_SWITCH)
    if override is not None:
        if override.strip().lower() in ("0", "off", "false", "no", ""):
            return 0.0
        try:
            return max(0.0, float(override))
        except ValueError:
            pass
    return CLIMB_MAX_RISE


def climbBlocked(flow, old_pos, new_pos) -> bool:
    """横向这一步是否会**爬上一个爬不上去的坎**（上行落差 > ``CLIMB_MAX_RISE``）。

    与 ``ledgeBlocked()`` 是一对：那边挡**下行**（掉落），这边挡**上行**（爬墙）。
    见 ``CLIMB_MAX_RISE`` 注释里的阈值来历与实机证据。

    ⚠️ 必须由 ``advanceGround()`` 在**写 X/Y 之前**调用。写完之后再判，就只能
    挡住 Z 而挡不住人走进去 —— 那正是「卡在城墙里」的成因。

    ⚠️⚠️ 基准取的是**角色实际高度**（``old_pos[2]``），不是当前格的地形值。
       理由：高度场是**单值**的（每格一个 Z）。城门洞 / 过街楼下面还能走人时，
       格子存的是**上层**的 Z，拿它当基准会把「平地」误判成「要爬 11 米」。
       又因为 ``CLIMB_MAX_RISE(5.5) < MAX_GROUND_Z_STEP(6.0)``，每步上行都能被
       ``groundZFollow()`` 一步跟到位，所以正常情况下角色 Z **恒等于**当前格
       地面值 —— 两种基准等价；只有在「格值在角色上方」时才分道扬镳，
       而那时角色实际高度才是对的。

    高度场没开 / 没数据 / 在网格外时恒为 ``False``（不改变原有行为）。
    """
    limit = climbMaxRise()
    if limit <= 0:
        return False
    if jumpActive(flow):
        # ⚠️ 2026-09-20 改：原来这里直接 `return False`（跳跃期间完全不判上行），
        #    于是「跳跃」成了绕过本判定的后门 —— 角色能跳进 11 m 高的墙格，
        #    落地后 Z 跟不上（``stepUpGap`` 那道闸门跑在 X/Y 写入**之后**，
        #    只挡住 Z、挡不住人），此后每一步 climbBlocked 都是 rise≈11 > 5.5
        #    → **永久锁死**。用户原话「平路还是卡 走不动」。
        #    实测会话 13496-380748287：43 次跳跃 / 27 次跳跃后有阻挡 / 其中 23 次
        #    落在墙格里（85%）。详见 JUMP_MAX_RISE 注释。
        #    现在只放宽「跳跃能多越过的抛物线高度」，不再是「全放行」。
        limit += JUMP_MAX_RISE
    z_new = groundZAt(flow, new_pos[0], new_pos[1])
    if z_new is None:
        return False
    actual = old_pos[2] if len(old_pos) > 2 else None
    if not isinstance(actual, (int, float)):
        # 拿不到角色 Z 就退回地形基准（与 ledgeBlocked 同口径）
        actual = groundZAt(flow, old_pos[0], old_pos[1])
        if actual is None:
            return False
    return (z_new - actual) > limit


def _trace(flow, message):
    """既写 ``flow.result['logs']``（保持原行为），又落盘到追踪日志（若 ``T7_TRACE_LOG`` 开启）。"""
    flow.result["logs"].append(message)
    try:
        from . import tracelog
        tracelog.emit("block", airWallScene(flow), message)
    except Exception:
        pass


def advanceGround(flow) -> bool:
    """按当前按键与朝向把位置积分一步。

    返回 ``True`` 表示**这一步被挡住、位置没有推进**。三种挡法，日志可区分：

    * ``move-blocked-airwall``   —— 客户端 ``aairwall.xml`` 里的线段（index >= 0）
    * ``move-blocked-ledge``     —— 下行悬崖（index = -1，有「按住 0.4 秒放行」）
    * ``move-blocked-climb``     —— 上行爬不上去的坎（index = -2，**无**按住放行）
    * ``move-ledge-step-off``    —— 按住 ledge 方向够久 → 判定为故意走下去，放行

    开关关闭 / 场景没高度场时恒为 ``False``，写入路径与日志与接线前逐位相同。

    骑兵（``mounted``）走的是**转舵**版本：A/D 不进位移投影，只改朝向，位移永远沿
    朝向前进/后退 —— 见 ``advanceMountHeading``。步兵这条 mask 一字不变。

    ⭐ 2026-09-29：投石车（``rigTurning``）**同一条路由** —— A/D 也不进投影，
    但朝向**不在这里改**：它的转角由 ``siege.onRigTurnTick`` 自己累加（要同时
    转 CC 物件的姿态，那是 siege 的活）。所以本函数对投石车只做一件事：
    把 A/D 从 ``mask`` 里摘掉 ⇒ ``projection`` 为 0 ⇒ 位置一格都不动。

    ⚠️ 2026-09-20：**上行爬升（climbBlocked）也在这里判，不是 groundZFollow 里。**
    见 ``CLIMB_MAX_RISE`` 注释——判定必须在写 X/Y **之前**，否则只能挡住 Z，
    人还是走进墙里（实机「卡在城墙里」）。

    撞墙处理只做「不推进 + 记日志」，**不做墙面吸附/滑行**——那需要碰撞半径
    和墙的朝向信息，目前都没有（未闭合）。调用方负责把下行 state 压成 STOP，
    免得客户端继续本地预测前冲。
    """
    ground = groundState(flow)
    if ground["mask"] < 0 or ground["position"] is None:
        return False
    # 骑兵：A/D 不进位移投影（``drivingProjection`` 里摘掉），只走转舵，
    # 位移永远沿当前朝向。``mounted`` 为假时 mask 与原来逐位相同，步兵路径不受影响。
    if not stepMoving(flow, ground["mask"], ground["heading"]):
        return False
    groundTiming(flow, ground)
    elapsed = flow.now - ground["lastAdvanceAt"]
    applied = min(elapsed, MAX_GROUND_ELAPSED_MS)
    if mounted(flow) and mountTurnKey(ground["mask"]) != 0 and applied:
        # 先转舵再算位移：这一步的 delta 要用**转之后**的朝向，否则圆弧会滞后一拍。
        advanceMountHeading(flow, ground, applied)
    projection = drivingProjection(flow, ground["mask"], ground["heading"])
    blockedHit = None
    if applied:
        # 坐标每次写回Single，不保留额外的高精度累加器。
        position = [struct.unpack(">f", struct.pack(">f", value + delta * applied / wire.GROUND_STEP_MS))[0]
                    for value, delta in zip(ground["position"], projection.delta)]
        if not all(math.isfinite(value) for value in position):
            raise ValueError("non-finite ground position")
        blockedHit = airWallBlocked(flow, ground["position"], position)
        # index = -3：建筑碰撞。和空气墙(>=0)、边缘(-1)、爬升(-2)分开记，
        # 日志里是 ``move-blocked-model`` —— 排查「穿模」时只看这一条。
        if blockedHit is None:
            modelHit = modelBlocked(flow, ground["position"], position)
            if modelHit is not None:
                blockedHit = {"index": -3}
                # ⚠️ 2026-09-22：这里原先是 flow.result["logs"].append(...)，
                #    于是最需要的 move-blocked-model **不落盘** —— tracelog.py 的
                #    模块文档却写着它捕获 move-blocked-*，实现与文档不一致。
                #    改走 _trace()：开关关闭时行为逐位不变（_trace 第一行就是 append）。
                _trace(flow,
                    "move-blocked-model scene=" + repr(airWallScene(flow))
                    + " cell=" + str(modelHit[0])
                    + " band=%.2f..%.2f" % (modelHit[1], modelHit[2])
                    + " z=" + "%.3f" % ground["position"][2]
                    + " from=" + ",".join("%.3f" % value for value in ground["position"])
                    + " to=" + ",".join("%.3f" % value for value in position)
                    + " src=" + modelHit[3])
        # ⚠️⚠️ 上行爬升判定必须在这里 —— 在 ``ground["position"] = position`` **之前**。
        #    放到 groundZFollow() 里（那道 MAX_GROUND_Z_GAP 闸门的位置）只能挡住 Z，
        #    挡不住 X/Y，人还是走进墙里了。见 CLIMB_MAX_RISE 注释。
        if blockedHit is None and climbBlocked(flow, ground["position"], position):
            # index = -2：刻意与空气墙(>=0)、边缘阻挡(-1)区分开，
            # 免得复盘时又把 climb 记成 airwall（这个坑踩过一次）。
            blockedHit = {"index": -2}
            _trace(flow,
                "move-blocked-climb scene=" + airWallScene(flow)
                + " max=" + str(climbMaxRise())
                + " rise=" + "%.4f" % (groundZAt(flow, position[0], position[1])
                                       - ground["position"][2])
                + " from=" + ",".join("%.3f" % value for value in ground["position"])
                + " to=" + ",".join("%.3f" % value for value in position))
        if blockedHit is None and ledgeBlocked(flow, ground["position"], position):
            if ledgeHold(flow, ground):
                blockedHit = {"index": -1}
                # 同上：改走 _trace 让它落盘（原先 append 不落盘）。
                _trace(flow,
                    "move-blocked-ledge scene=" + airWallScene(flow)
                    + " from=" + ",".join("%.3f" % value for value in ground["position"])
                    + " to=" + ",".join("%.3f" % value for value in position))
            else:
                # 一直推着这个方向 → 判定为「故意走下去」，放行（防被困住）
                _trace(flow,
                    "move-ledge-step-off scene=" + airWallScene(flow)
                    + " heldMs=" + str(LEDGE_HOLD_MS)
                    + " from=" + ",".join("%.3f" % value for value in ground["position"])
                    + " to=" + ",".join("%.3f" % value for value in position))
        if blockedHit is None:
            ground["position"] = position
            groundZFollow(flow, ground)
            if ground.get("ledgeSince") is not None:
                ground["ledgeSince"] = None
        elif blockedHit.get("index", -1) >= 0:
            # ⚠️ 只在这里记空气墙日志。边缘阻挡 index=-1、上行爬升 index=-2，
            #    都走各自的分支，不能再记成 airwall（实机复盘时被这个日志误导过一次）。
            _trace(flow,
                "move-blocked-airwall scene=" + airWallScene(flow)
                + " seg=" + str(blockedHit["index"])
                + " from=" + ",".join("%.3f" % value for value in ground["position"])
                + " to=" + ",".join("%.3f" % value for value in position))
    ground["lastAdvanceAt"] = flow.now
    if elapsed > applied:
        flow.result["logs"].append("ground-timing-clamp droppedMs=" + str(elapsed - applied))
    # 只在**真正用过碰撞判定**之后才往 session 里加这个键：
    # 开关关闭时 session 形状也逐位不变（原生层会校验 state 类型）。
    if blockedHit is not None:
        ground["airwallBlocked"] = True
    elif "airwallBlocked" in ground:
        ground["airwallBlocked"] = False
    return blockedHit is not None


def groundZFollow(flow, ground) -> bool:
    """把 ``position[2]`` 拉到 ``(x, y)`` 处的可站立高度。

    2026-09-19：``delta[2]`` 恒为 0，角色 Z 永远停在 spawn 值，所以走出出生点
    就会陷进地里、也上不了城墙。这里按高度场补上 Z。

    * ``groundZAt()`` 返回 ``None`` 时**一个字节都不动**（场景没数据 / 开关关 /
      在网格外），保证 t7_wasd_verify 那 187 项基线逐位不变。
    * 用 ``MAX_GROUND_Z_STEP`` 限速：楼梯在高度场里是**垂直通道**（相邻格可能
      差十几米），不限速会瞬移。普通地形每格差 <0.85 m，根本触发不到限速。
    """
    position = ground["position"]
    src = []
    target = groundZAt(flow, position[0], position[1], src)
    if target is None:
        # ⭐ 2026-09-19 诊断：为什么高度跟随全程不生效（玩家沉地下）。
        #    每个 session 只记一次，避免刷爆日志。
        if not flow.session.get("_groundZNoneLogged"):
            flow.session["_groundZNoneLogged"] = True
            try:
                module = heightFieldModule()
                scene = airWallScene(flow)
                flow.result["logs"].append(
                    "ground-z-none scene=" + repr(scene)
                    + " enabled=" + str(getattr(module, "HEIGHTFIELD_ENABLED", "?"))
                    + " loaded=" + str(module.load(scene) is not None)
                    + " at=" + ",".join("%.2f" % v for v in position[:2])
                    + " z=" + str(round(position[2], 4)))
            except Exception as exc:
                flow.result["logs"].append("ground-z-none diag-failed " + repr(exc))
        return False
    # 无条件记录前 6 次调用详情：区分「拿到值但认为无需调整」和「根本没跑到」
    pull = target - position[2]
    _n = flow.session.get("_gzDiagCount", 0)
    if _n < 6:
        flow.session["_gzDiagCount"] = _n + 1
        flow.result["logs"].append(
            "ground-z-diag n=" + str(_n + 1)
            + " scene=" + repr(airWallScene(flow))
            + " src=" + (src[0] if src else "none")
            + " target=" + str(round(target, 4))
            + " z=" + str(round(position[2], 4))
            + " pull=" + ("%+.3f" % pull)
            + " at=" + ",".join("%.2f" % v for v in position[:2]))
    # ⭐ 2026-09-27：另记「大幅拉 z」。上面那 6 行只在**开局**打，而走位过程中真正
    #    危险的是 navmesh 把角色往上一层猛拽（离线复演：城墙/正门附近有 6.29 m 跳变）。
    #    `src` 一起记 —— 只有 `src=nav` 的大跳才可疑，`src=heightfield` 的大跳是
    #    高度场常数在硬拉（江陵城就是这种：全域返回 groundBase 12.7277）。
    #    限 12 条，避免每 50 ms 刷屏。
    if abs(pull) > 2.0:
        _p = flow.session.get("_gzPullCount", 0)
        if _p < 12:
            flow.session["_gzPullCount"] = _p + 1
            flow.result["logs"].append(
                "ground-z-pull n=" + str(_p + 1)
                + " scene=" + repr(airWallScene(flow))
                + " src=" + (src[0] if src else "none")
                + " pull=" + ("%+.3f" % pull)
                + " from=" + str(round(position[2], 4))
                + " to=" + str(round(target, 4))
                + " at=" + ",".join("%.2f" % v for v in position[:2]))
    single = struct.unpack(">f", struct.pack(">f", float(target)))[0]
    # ⚠️ 两端都要过一遍 Single：position[2] 是 Python 双精度，直接
    #    ``single == position[2]`` 在城内平地也会被判成"需要调"
    #    （Single(43.2482) != 43.2482），既每 tick 空转又把精度改掉。
    if single == struct.unpack(">f", struct.pack(">f", position[2]))[0]:
        return False
    step = single - position[2]
    gap = stepUpGap()
    if gap > 0 and step > gap:
        # ⚠️⚠️ 2026-09-20：**这道闸门已降级为 backstop**，日常移动轮不到它。
        #    上行真正由 advanceGround() 里的 climbBlocked(CLIMB_MAX_RISE=5.5) 把关 ——
        #    那道跑在**写 X/Y 之前**，所以人根本走不进爬不上去的格子。
        #    留在这里是为了兜「位置凭空变了」的情况（传送 / 复活 / 旧存档），
        #    以及不破坏已实机验收的行为。
        #    历史教训：只靠这一道挡不住「走进墙里」，因为它跑在 X/Y 写入**之后** ——
        #    实测日志 `ground-z-blocked-stepup step=10.073` 连记 20 次，
        #    而角色早已站在塔楼壁里面（用户原话「卡在城墙里」）。
        #
        # ⚠️ 只看**上行**（step > 0），不要用 abs(step)。
        #    下行归 ledgeBlocked() 管，它有「按住方向 0.4 秒就放行」的兜底；
        #    这里若连下行一起挡，位置**已经写过了**，Z 会永远悬在半空 ——
        #    那是个比原来更糟的新「卡住」。
        # 只记前 20 次，免得贴着墙走时每帧刷屏。
        _n = flow.session.get("_stepUpBlockedCount", 0)
        if _n < 20:
            flow.session["_stepUpBlockedCount"] = _n + 1
            flow.result["logs"].append(
                "ground-z-blocked-stepup scene=" + repr(airWallScene(flow))
                + " step=" + str(round(step, 4))
                + " gap=" + str(gap)
                + " at=" + ",".join("%.2f" % value for value in position[:2]))
        return False
    if abs(step) > MAX_GROUND_Z_STEP:
        single = struct.unpack(
            ">f", struct.pack(">f", position[2] + math.copysign(MAX_GROUND_Z_STEP, step)))[0]
    old = position[2]
    position[2] = single
    if abs(step) >= GROUND_Z_LOG_THRESHOLD:
        flow.result["logs"].append(
            "ground-z-follow from=" + str(round(old, 4)) + " to=" + str(round(single, 4))
            + " at=" + ",".join("%.2f" % value for value in position[:2]))
    return True


def validMoveClock(session: dict) -> bool:
    """会话是否已带上「进图起递增毫秒」时钟（上游 ``app.validateState`` 同口径）。"""
    return (session.get("moveClock") == MOVE_CLOCK
            and type(session.get("instanceStartedAt")) is int
            and session["instanceStartedAt"] >= 0)


def instanceTick(flow):
    """进图起递增毫秒；时钟未建立时返回 ``None``（调用方决定退路）。"""
    startedAt = flow.session.get("instanceStartedAt")
    if type(startedAt) is not int or startedAt < 0:
        return None
    return flow.now - startedAt


def nextGroundTick(flow):
    tick = instanceTick(flow)
    if tick is None:
        # 退路：离线仿真桩 / ``scene.begin`` 之前，退回墙钟口径。
        tick = max(flow.now & 0xFFFFFFFF, groundState(flow)["tick"] + 1)
        if tick > 0xFFFFFFFF:
            raise ValueError("ground server tick exhausted UInt32 range")
        return tick
    if not 0 <= tick <= MAX_MOVE_TICK:
        raise ValueError("MOVE elapsed time outside nonnegative Int32 range")
    if tick < groundState(flow)["tick"]:
        raise ValueError("MOVE time moved backwards; new instance required after clock change")
    return tick


def broadcastHeading(flow):
    ground = groundState(flow)
    position = ground["position"] if ground["position"] is not None else wire.POSITION
    tick = nextGroundTick(flow)
    body = move_flow.encode_move_direct_bc(
        server_tick=tick, target_instance_id=1, direction_yaw=ground["heading"],
        position=tuple(position))
    ground["tick"] = tick
    ground["timingTick"] = tick
    flow.send(2, body, "move-ground-heading-direct-bc")


def broadcastTurnDirect(flow, heading, position):
    """骑兵**原地转舵**时补一条 selector4「位置校正 + 方向」（旋钮 ``mount_turn_direct``）。

    为什么要另发一条：38/35 号那族是**持续驱动状态**，客户端本地坐骑控制器会按
    「角速度 ∝ 前进速度」自己积分朝向，速度 0 ⇒ 转角 0 ⇒ 我们扫出来的 heading 上不了屏
    （2026-10-06 的实机链与静态依据写在 ``MOUNT_TURN_DIRECT`` 上面那段）。
    selector4 在客户端走的是 ``PosCommand + DirCommand``（README:44），和跳跃那两条
    同族 —— 实机上「按空格突然跳了个方向」就是那条把攒下的朝向兑现了。

    ⚠️ **只读不写** ``ground["tick"]``：这条报文拿一个单调递增的 tick，但**不回写**账本，
    所以开关打开也绝不会改动 38/35 号任何一个字节（回归门「关掉开关逐字节不变」靠的就是
    这条纪律，而不只是那个 if）。
    ⚠️ ``direction_yaw`` 必须是 -180..180 的 **int**，越界编码器直接抛 —— 抛了整条事件被丢
    （``jumpState()`` 那条 tuple 事故的教训同款），所以这里自己先折回区间。
    """
    if not MOUNT_TURN_DIRECT:
        return
    yaw = int(round(heading))
    if yaw > 180:
        yaw -= 360
    elif yaw < -180:
        yaw += 360
    body = move_flow.encode_move_direct_bc(
        server_tick=nextGroundTick(flow), target_instance_id=1,
        direction_yaw=yaw, position=tuple(position))
    flow.send(2, body, "move-mount-turn-direct-bc")


# ---- 显示轴 vs 世界轴（2026-09-23 新增）------------------------------------------
def displayAxes(flow, forwardBack, leftRight):
    """把**世界**投影轴换算成**显示**轴（38/55/39 号报文的 ``forward_back``/``left_right``）。

    ⚠️⚠️ **只对骑兵生效**（2026-09-23 收口）。理由：用户明确要求「**别在影响步兵了**」，
    而本函数挂在共享的 ``broadcast()`` 上，先前是无条件取负 ⇒ 一直在改步兵的下行字节。
    步兵现在走 ``if not mounted(flow): return forwardBack, leftRight`` 直通，
    **与加这个函数之前逐位相同**；骑兵侧保留 ``[move] wire_axis`` 的 A/B 能力。
    （步兵的回归判据见 ``verify_mount_turn.py`` 的「步兵：显示轴必须与历史逐位相同」一条。）
    ★ 为什么必须分成两套符号
    ------------------------------------------------------------------
    prior_art（原游戏客户端同一份的运行时解包映像，离线分析副本；
    映像反汇编 + 实机记录）里 ``动作.txt`` 二.4 原文：

        世界位置积分与动画显示字段**不是同一个符号语义**：
        世界移动仍按 W-S 投影到人物当前 forward；MOVE_BC 的显示 FB 必须按 S-W 编码。

    我们此前把**同一个数**既拿去积分世界位置、又当显示字段发出去 ⇒ 位置是对的、
    动作是反的。这就是实机「**WS 前进后退 是反的**」的机制（见下）。

    显示侧的**一手证据**（``动作.txt`` 六，2026-08-10，做法是直接调客户端原
    MOVE_BC 消费器、发一条持续命令再自动发 MOVE_STOP，**由用户盯着画面逐项确认**）：

      =======================================  ==============================
      发出的 (state, LR, FB, cv)                 用户现场看到的
      =======================================  ==============================
      state=2, LR=0, FB=**+1000**, cv=1000       **向前慢走**（连看多次，三次 10 秒）
      state=2, LR=0, FB=**+1000**, cv=5000       **快速奔跑**
      state=2, LR=0, FB=**−1000**, cv=1000       **倒退走路**
      state=4, LR=**−1000**, FB=0, cv=1000       「以人物为中心向右后撤步」
      state=8, LR=**+1000**, FB=0, cv=1000       「低速向前慢走」
      =======================================  ==============================

    我们线上的 ``GROUND_WALK_STATES`` 给出的 state 正好是 W→2、A→4、D→8 ——
    和上表那三行**逐项对上**，说明**档位选对了，只有轴符号反**：

      * 世界轴 ``fb = s − w`` ⇒ W 发 **−1000**，而现场确认「前进」是 **+1000**；
      * 世界轴 ``lr = a − d`` ⇒ A 发 **+1000**，而现场确认 state=4 那行是 **−1000**。

    ⇒ **显示轴 = 世界轴取负**。一行改完，上表五行全部命中。

    ⚠️ **世界积分一个字都不动**：它已被独立验证（客户端上报的预测位置与服务端 echo
    轨迹跨 17 局 94.6~100% 吻合）。这里只改下行报文的显示字段。

    ``[move] wire_axis``：``client``（默认）= 取负；``world`` = 历史行为逐位不变（A/B 用）。

    ⚠️ **步兵直通**：不改步兵一个字（用户要求「别在影响步兵了」）。
    """
    # ⚠️⚠️ 2026-10-07 第四十二轮**返工记录（别再犯）**：
    #   曾在这里加过「客户端权威 + 步兵 ⇒ 取负」，理由是 prior_art 说客户端约定
    #   W ⇒ fb=+1000。**实测立刻翻车**：用户「WASD 都是反方向」。
    #   原因：38 号 ``MOVE_BC_WITH_SYSTEM_AND_ACTIVE`` 与 55/39 号**不是同一种语义** ——
    #     * 38 号是 prior_art 明说的「按键/方向变化时只发布一次，**作为持续驱动命令**」
    #       ⇒ 客户端拿它**驱动移动**，必须与键盘意图同轴 = **世界轴原值**（W ⇒ −1000）；
    #     * 55 号 ``BC_ACTOR_WITH_JUMP`` 是**动画广播** ⇒ 才需要客户端**显示**约定
    #       （取负，W ⇒ +1000，见 prior_art《四方向反向…》）。
    #   所以取负只加在**跳跃广播**那一侧（见 ``_jumpAxes``），这里一个字都不改。
    if not mounted(flow) or WIRE_AXIS == "world":
        return forwardBack, leftRight
    return -forwardBack, -leftRight


def broadcast(flow, position, heading, state, forwardBack, leftRight, reason):
    """发一条 38 号人物移动广播。

    ``state`` 是**已经定好的上线档位**：步兵 = 步战档、骑兵 = 骑乘档，都由
    ``moveWireState`` 在调用点算好，这里不再查表二次映射（曾经映射过一次，
    于是 ``broadcastMount`` 拿到的已经是骑乘编号、查不到表，两边互相把对方的
    档位当键用 —— 2026-09-22 修掉）。
    """
    ground = groundState(flow)
    moving = state not in (move_flow.MOVE_GROUND_STATE_STOP,
                           move_flow.MOVE_GROUND_MOUNT_STATE_STOP)
    tick = nextGroundTick(flow)
    currentVelocity, maxVelocity = velocityPair(flow, moving)
    # ★ 入参是**世界**轴；下行要用**显示**轴（见 displayAxes 的证据表）。
    displayFb, displayLr = displayAxes(flow, forwardBack, leftRight)
    body = move_flow.encode_move_bc_with_system_and_active(
        server_tick=tick, target_instance_id=1, position=tuple(position),
        direction_yaw=heading, state=state,
        forward_back=displayFb * 1000, left_right=displayLr * 1000,
        current_velocity=currentVelocity, max_velocity=maxVelocity,
        system_group=3, active=1, acceleration=0)
    ground["tick"] = tick
    ground["timingTick"] = tick
    flow.send(2, body, reason)
    broadcastMount(flow, position, heading, state, moving, tick)


# --- 骑乘档与「位移投影」的唯一入口 --------------------------------------------------
# 2026-09-22 之前这里是一张「步战 1..15 → 骑乘档」的字典，在 ``broadcast`` 里查表换号。
# 那张表治不了病根：A/D 还留在位移投影里，客户端于是**一边**按我们改的
# ``direction_yaw`` 转、**一边**按步战横移档演平移（实机「骑兵方向都是乱的」）。
# 现在的口径是：骑兵的 A/D 根本不进投影（``drivingMask``），档位由按键组合直接选
# （``mountMoveState``），所以 ``broadcast`` 拿到的 ``state`` 已经是上线值，不再二次映射。


def mountSpeedTier() -> int:
    """实际积分出来的速度落在 ``坐骑.psheet`` 四档里的第几档（0 基）。

    我们每拍走 ``GROUND_STEP_DISTANCE`` / ``GROUND_STEP_MS`` = 5.0 m/s，
    轻骑四档是 2/4/7/10 ⇒ 命中前两档，返回 1（第 2 档）。
    """
    metresPerSecond = STEP_VELOCITY / VELOCITY_SCALE
    tiers = MOUNT_SPEED_TIERS[MOUNT_CLASS_DEFAULT]
    reached = [index for index, limit in enumerate(tiers) if metresPerSecond >= limit]
    return reached[-1] if reached else 0


def mountMoveState(mask, forwardBack) -> int:
    """骑乘档 = 「前/后 × 转舵方向 × 速度档」。

    ``E_RES_MOVE_GROUND_MOUNT_LINE_UP_{1..4}_ACC_WALK``（16..19）与
    ``MOUNT_ARC_{1..4}_{LEFT,RIGHT}_UP_ACC``（26..29 / 30..33）这三族**每档差 1**，
    档号就是 ``坐骑.psheet`` 的速度档 —— 所以这里加 ``mountSpeedTier()`` 而不是挑个顺眼的。
    后退那一族（``MOUNT_LINE_DOWN_WALK`` 24、``MOUNT_ARC_{LEFT,RIGHT}_DOWN_WALK`` 50/51）
    原版**没有**速度分档，只有一个值。

    ⚠️ 只按 A/D（原地转、没按 W/S）**没有对应的骑乘档**：马的原版状态里没有「原地踏步转」
    （宏表 16..19/20..23/24/26..29/30..33/34..49/50/51/54 全是「线/弧 × 前/后 × 速度档」，
    **没有一条是「原地」**，见 ``codec/move_flow.py`` 那份直读宏表）。
    ⇒ 只能给 ``MOUNT_STOP``(54) 加一个变化的 ``direction_yaw``。

    ★ 2026-09-23 实机回答了这个悬了很久的问题：**客户端不认「54 + dir 斜坡」这个组合**，
    表现就是用户那句「**A/D 左右也不能用**」（服务端 ``dir`` 确实在 120°/s 地转，
    掩码 × ``dir`` 斜坡 **15/15 一致**，出处 = 会话 ``13976-223728875``
    （``mount_steering_verdict.py`` 判据 1：一致 15 / 不一致 0 / 信号不足 0），
    但画面上不转）。
    ⚠️ 注意这条是**推断**不是直测：脚本量的是「服务端 ``dir`` 有没有在斜坡」，
    「客户端认不认」是从「服务端转了 + 用户看不见转」推出来的。
    其余会话的同项数据：``13976-188562898`` 97/0、``13976-191219260`` 39/0、
    ``13976-239667435`` 24/1 ⇒ 服务端侧的斜坡一直是对的，问题在客户端那一侧。
    ⚠️ 另：会话 ``13976-237448338`` 的同项数据是 **0/0/83（判不了，dir 恒 0）**
    —— 那是 tuple 崩溃局（按键事件全被丢），**不能**拿来当这条结论的证据。

    所以留了 ``[move] mount_turn_state``：
      * ``stop``（默认）= 54 + dir 斜坡，**与改动前逐位相同**（不做没证据的默认改动）；
      * ``arc`` = 原地转舵时改用**真实的骑乘转向档**（左/右前弧 + 速度档）。
        它只改 ``state``，``cv`` 仍是 0 ⇒ **不产生位移**，是纯表现实验。
    改一个词 + 重启就能 A/B，不用改代码。
    """
    tier = mountSpeedTier()
    turn = mountTurnKey(mask)
    if forwardBack < 0:                       # 前进（投影里 forward_back = s - w）
        if turn > 0:
            return move_flow.MOVE_GROUND_MOUNT_STATE_ARC_LEFT_UP_ACC + tier
        if turn < 0:
            return move_flow.MOVE_GROUND_MOUNT_STATE_ARC_RIGHT_UP_ACC + tier
        return move_flow.MOVE_GROUND_MOUNT_STATE_LINE_UP_ACC + tier
    if forwardBack > 0:                       # 后退
        if turn > 0:
            return move_flow.MOVE_GROUND_MOUNT_STATE_ARC_LEFT_DOWN_WALK
        if turn < 0:
            return move_flow.MOVE_GROUND_MOUNT_STATE_ARC_RIGHT_DOWN_WALK
        return move_flow.MOVE_GROUND_MOUNT_STATE_LINE_DOWN
    if MOUNT_TURN_STATE == "arc" and turn:
        return (move_flow.MOVE_GROUND_MOUNT_STATE_ARC_LEFT_UP_ACC + tier if turn > 0
                else move_flow.MOVE_GROUND_MOUNT_STATE_ARC_RIGHT_UP_ACC + tier)
    return move_flow.MOVE_GROUND_MOUNT_STATE_STOP


def mountWireState(mask, projection) -> int:
    if mask < 0:
        return move_flow.MOVE_GROUND_MOUNT_STATE_STOP
    return mountMoveState(mask, projection.forward_back)


def drivingMask(flow, mask):
    """进位移投影的按键：骑兵/投石车把 A/D 摘掉（这两位改走转舵），步兵原样返回。

    ⭐ 2026-09-29：判据从 ``mounted(flow)`` 扩成 ``mounted(flow) or rigTurning(flow)``
    —— 投石车骑手不算骑兵，但 A/D 同样是**转角度**不是横移（用户：「按AD 肯定是
    调角度的」）。步兵路径**一字不变**（两个判据都为假时返回原 ``mask``）。
    """
    return mask & MOUNT_DRIVING_KEY_MASK if (mounted(flow) or rigTurning(flow)) else mask


def turnWalkScale(flow, mask):
    """「小步转」的每拍位移比例（0..1），不适用时返回 ``None``。旋钮 ``mount_turn_walk``（百分比）。

    ⭐ 2026-10-06 的实机依据（为什么不再指望纯转舵）：服务端这边朝向每拍都在扫
    （``mount-key`` 里 44→60→70→78→90→…，120 度/秒），38/35 号一条没少发，
    ``cv`` 也已经是 5000，**画面上照样不转**；把 ``mount_wf`` 从 1000 加到 10000 也不转
    （已回 1000），每拍补一条 selector4「位置校正+方向」还是**不转**（``mount_turn_direct``
    那条实验否证）。三条合起来只剩一个解释：
    **客户端本地坐骑控制器按「角速度 ∝ 前进速度」自己积分，没前进输入就不转**
    （自行车模型）。而「按空格那一下突然跳了个方向」正是空中不接管、落地才兑现的旁证。
    ⇒ 唯一没试过的方向：**真的给一点前进驱动**，让客户端自己转起来（真马手感是边走边转）。
    代价是「原地」不成立 —— 每拍只走 ``GROUND_STEP_DISTANCE × 这个比例``，
    30% 时约 **1.5 m/s**，一秒挪 1.5 米。

    返回 ``None`` 的所有情形 ⇒ 调用点走原来的 ``project(drivingMask(...), heading)``，
    逐字节与加它之前相同：旋钮为 0、步兵、投石车骑手（``mounted`` 为假）、
    没按 A/D、以及 **W 或 S 正按着**（那是真前进，本来就在动，不叠加）。
    """
    if not MOUNT_TURN_WALK_PCT or mask < 0:
        return None
    if not mounted(flow) or mountTurnKey(mask) == 0:
        return None
    if mask & MOUNT_DRIVING_KEY_MASK:          # W(1) 或 S(4) 按着
        return None
    return MOUNT_TURN_WALK_PCT / 100.0


def drivingProjection(flow, mask, heading):
    """位移投影。**所有**算位移/发 state 的地方都走它，别再直接 ``project(ground["mask"], ...)``
    —— 漏一处就是「一边转舵一边发横移」那次实机翻车。

    ★ 唯一的例外分支是 ``turnWalkScale()`` 非 None（骑兵原地转舵 + 旋钮 >0）：
    这时给投影**补一个 W 位**（``driven | 1``）。因为 A/D 已经被 ``drivingMask`` 摘掉了，
    ``driven | 1`` 就是「纯前进」⇒ 位移沿**当前朝向**，而朝向仍在由 ``advanceMountHeading``
    按 120°/s 扫 ⇒ 走出来的是一条**圆弧**（不是 45° 斜穿，横移分量仍然是 0）。
    每拍位移按旋钮比例缩小。``forward_back`` 于是是 -1 ⇒ 38 号的 fb 字段带上前进输入，
    这正是「客户端要看到前进输入才转」所要的那个字段；``mountMoveState`` 拿的仍是
    **原始 mask**，所以档位还是左右前弧（与 ``mount_turn_state=arc`` 同一个档）。
    """
    driven = drivingMask(flow, mask)
    scale = turnWalkScale(flow, mask)
    if scale is None:
        return project(driven, heading)
    return project(driven | 1, heading,
                   stepDistance=wire.GROUND_STEP_DISTANCE * scale)


def stepMoving(flow, mask, heading) -> bool:
    """这一拍算不算「在动」：有位移，或者（骑兵/投石车）正在转舵。

    决定地面定时器要不要继续跑。⚠️ 投石车那条**必须**算进来：投石车的转角是由
    ``siege.onRigTurnTick`` 挂在 ``ground-step`` 这个自续定时器上逐拍累加的
    （与骑兵的 ``advanceMountHeading`` 同一个套路）；要是这里返回假，
    ``controls.timer`` 会提前 return、定时器不再续 ⇒ **按一次 A 只转一帧**。
    """
    if mask < 0:
        return False
    if drivingProjection(flow, mask, heading).moving:
        return True
    return (mounted(flow) or rigTurning(flow)) and mountTurnKey(mask) != 0


def stopState(flow) -> int:
    """静止档：步兵 1（``MOUNT`` 之外那套的 STOP），骑兵 54（``MOUNT_STOP``）。"""
    return move_flow.MOVE_GROUND_MOUNT_STATE_STOP if mounted(flow) \
        else move_flow.MOVE_GROUND_STATE_STOP


def moveWireState(flow, ground, mask, projection) -> int:
    """上线的 ``state``：步兵 = 步战档（``groundMoveState``），骑兵 = 骑乘档。"""
    if not mounted(flow):
        if runtimeMovement(flow):
            # ⭐ 第三十九轮：客户端权威 —— 只镜像客户端显式快跑标志（见
            #   ``groundMoveState`` 的 ``runFallback``），并留一条节流诊断，
            #   下一轮实机就能一眼看出「服务端广播的 state」与客户端标志是否一致。
            state = groundMoveState(ground, projection, flow.now, False)
            try:
                last = ground.get("stateMirrorAt")
                if last is None or flow.now - last >= 1000:
                    ground["stateMirrorAt"] = flow.now
                    flow.result["logs"].append(
                        "move-state-mirror state=%d fastRun=%d elapsed=%s"
                        % (state, int(ground.get("fastRun") or 0),
                           "n/a" if ground.get("moveStartedAt") is None
                           else int(flow.now - ground["moveStartedAt"])))
            except Exception:  # noqa: BLE001
                pass
            return state
        return groundMoveState(ground, projection, flow.now)
    return mountWireState(mask, projection)


# 坐骑四档速度，单位 m/s。出处：客户端配置表 ``../data/propsheet/坐骑.psheet``
# （data1.vfs 的 run 3847，2026-09-22 解出；坐骑.psheet 与 VFS 解表脚本均为
# 本地启动器导出 / 解表产物）。
# 这四档不是装饰：``../data/propsheet/移动特效.psheet`` 里
# ``骑兵特效_速度2/3/4`` 分别挂 ``坐骑烟尘2/3/4``，客户端自己就是按这四档分级演特效的。
MOUNT_SPEED_TIERS = {
    "轻骑": (2.0, 4.0, 7.0, 10.0),
    "弓骑": (2.0, 4.0, 7.5, 10.5),
    "重骑": (2.0, 4.0, 6.5, 9.5),
}
# ⚠️ 坐骑 tid → 轻/弓/重骑 的对照**在包里没找到**：``data/table`` 里跟 mount 有关的只有两张，
# ``s_mount_cli.bin`` 解出来是行为树 XML（618B）、``s_mount_attr.bin`` 是 Scaleform GFX，
# 都不是坐骑属性表。所以先统一按轻骑，等实机/别的表再分。
MOUNT_CLASS_BY_TID = {}
MOUNT_CLASS_DEFAULT = "轻骑"
# 报文里 ``cv``/``mv`` 是 m/s × 1000（int16；``forward_back`` 也是 ×1000 同一套标度）。
VELOCITY_SCALE = 1000
# 我们实际积分出来的速度（``GROUND_STEP_DISTANCE`` / ``GROUND_STEP_MS``），不是猜的数。
STEP_VELOCITY = int(wire.GROUND_STEP_DISTANCE / wire.GROUND_STEP_MS * 1000 * VELOCITY_SCALE)


def velocityPair(flow, moving):
    """``(current_velocity, max_velocity)``：跑动时的**实际速度**和**这匹马的上限**。

    步兵那两个数保持本轮之前的字面值（``5000 / 25000``）—— 那族数没在这批表里找到，
    别顺手改，改了就破坏了「步兵逐字节不变」这条回归判据。

    ★ ``current_velocity`` 必须是**服务端真积分出来的那个速度**，不是档位常量：
    骑兵「小步转」（``turnWalkScale``）每拍只走 ``GROUND_STEP_DISTANCE × 比例``，
    实际 1.5 m/s 却报 5000，客户端就会按 5 m/s 本地预测前冲、被下一拍的位置回显顶回去
    （表现是「一跳一跳」，会把实验结果搞脏）。``max_velocity`` 不动 —— 那是这匹马的上限。
    """
    if not moving:
        return 0, 0
    if mounted(flow):
        mask = flow.session.get("ground", {}).get("mask", -1)
        scale = turnWalkScale(flow, mask)
        tid = wire.battleLoadout(flow.session.get("heroId", 0))[1]
        tiers = MOUNT_SPEED_TIERS[MOUNT_CLASS_BY_TID.get(tid, MOUNT_CLASS_DEFAULT)]
        return (int(round(STEP_VELOCITY * (1.0 if scale is None else scale))),
                int(tiers[-1] * VELOCITY_SCALE))
    return 5000, 25000


# ⭐ 2026-09-22 晚：改回 **True**。当天下午为了「按键有反应、人不走」加上 35 号之后，
# 晚上按静态反推停发过一次（理由是「客户端 15 个移动接收函数里没有带 Mount 的」），
# **实机数据正好相反**，7 条骑兵连接逐条对上：
#   * 发 35 号：`44792-158912374` 连接 9/10、`44792-163497723` 连接 13/14（都在樊城）
#     —— 35 号 71~2872 条，位置回显 60~1802 次，**离屏 0 次**，正常跑。
#   * 停发 35 号：`44792-181725929` 连接 17/19/21 —— 回显只剩 26~89 次，客户端首包自报
#     `(-1601.75,118715.27,29606.47)` / `(429.43,540.12,27.70)` / `(-1626.45,120101.26,29953.32)`，
#     被 `anchorToSpawn` 判垃圾顶回出生点，随即「逃离战场，你已被处决」。
#   * 同一会话的步兵连接（21:25 洛阳、21:53 玉门关）一条 35 号都不发，全程正常
#     ⇒ 症状只跟「骑兵且没收到 35 号」绑定。
#   * 把 21:54 那局和 17:45 那局的下行逐帧对齐比过，**唯一差别**就是失败那局少了
#     一条 ``move-mount-bc-stop``（开局那条初始 STOP）。
# 坐骑按 ``actor.mount_rid == mount.rid`` 挂到骑手身上这条仍然成立，但客户端要
# 先收到一条以坐骑 ``inst_id`` 为目标的移动报文，才会把坐骑实体**落进场景**；
# 只发视野 ADD 不发 35 号 = 坐骑停在未初始化位置，骑手跟着被扔出 `dbordermap` 边界。
MOUNT_BC_ENABLED = True


def broadcastMount(flow, position, heading, state, moving, tick):
    """骑兵追加一条 35 号「坐骑移动广播」，寻址到坐骑实体那个 inst_id。
    开关是 ``MOUNT_BC_ENABLED``，2026-09-22 晚的实机判据写在它旁边 —— **别顺手关掉**。

    为什么要它（本轮抓包 ``44792-154674315`` 连接 6，2026-09-22 实机）：客户端自己
    上行过 ``command=2 selector=42``（TDR ``CS_PROTO_MOVE_MOUNT_SUDDEN_STOP``，
    原版中文说明「cli->svr: 请求马瞬停」）—— 也就是说它**已经**把本地角色放进骑乘
    模式了。而服务端这边只发 38 号（人物移动广播），坐骑实体从来没收到过任何一条
    以它为目标的移动报文 ⇒ 屏幕上「按键有反应、人不走」。

    ``state`` 直接沿用调用点算好的**骑乘档**（``moveWireState`` 出来的就是
    ``E_RES_MOVE_GROUND_MOUNT_*`` 那套编号，见 ``mountMoveState``）：马没有
    「左平移」这一档，所以纯 A/D 转舵按前左/前右的弧线档发。
    """
    if not MOUNT_BC_ENABLED:
        return
    # ⚠️ 判据是「名册有没有坐骑」而**不是** ``mounted(flow)``：下马后骑手切回步兵，
    #    但坐骑实体还在场景里，必须继续收到 35 号（理由见 ``mountParked()``）。
    if not wire.heroHasMount(flow.session.get("heroId", 0)):
        return
    ground = groundState(flow)
    if mountParked(flow):
        # ★ 下马后：马**留在原地**，不能跟着走路的骑手跑。
        #   为什么不是干脆停发 35 号 —— ``MOUNT_BC_ENABLED`` 上方那条实机判据写着
        #   「只发视野 ADD 不发 35 号 = 坐骑停在未初始化位置，骑手跟着被扔出
        #   dbordermap 边界」⇒ 宁可用一个合法停机位把它按住。
        park = flow.session.get("mountPark")
        if not park:
            return
        position = tuple(park)
        heading = flow.session.get("mountParkYaw", heading)
        state = move_flow.MOVE_GROUND_MOUNT_STATE_STOP
        moving = False
    currentVelocity, maxVelocity = velocityPair(flow, moving)
    # ★ 角速度五兄弟。表源与理由见 mountAngular 的注释；关掉走 [move] mount_angular=off。
    bw, wf, wa, cw, waf = mountAngular(flow, ground)
    if mountParked(flow):
        # 停机位不许转：骑手已经在走路了，A/D 不该再驱动这匹马（否则马原地打转）。
        cw = 0
    body = move_flow.encode_move_mount_bc(
        server_tick=tick, target_instance_id=wire.MOUNT_VISION_INSTANCE_ID, state=state,
        position=tuple(position), direction_radians=math.radians(heading),
        current_velocity=currentVelocity, max_velocity=maxVelocity,
        base_angular_velocity=bw, angular_factor=wf,
        angular_acceleration=wa, angular_velocity=cw, angular_acc_factor=waf)
    flow.send(2, body, "move-mount-bc-" + ("run" if moving else "stop"))


# ---------------------------------------------------------------------------
# [move] 段 ini 开关（2026-09-22 夜新增）
#
# 为什么走 ini 而不是环境变量：环境变量必须在**启动服务端之前** set，双击
# ``T7.Server.exe`` 根本吃不到 —— 这个坑 ``level.ini`` 的注释里已经踩过两次。
# ini 是记事本就能改的，改完重启服务端即可。
#
# 段名用 ``[move]`` 与 ``contracts._iniGet`` 读的 ``[level]`` 分开，
# 免得动到关卡那条已经验过的回归链。文件候选顺序直接复用
# ``contracts._iniCandidates()``（``level.ini`` 优先于 ``server.ini``）。
# ---------------------------------------------------------------------------
MOVE_INI_SECTION = "move"
MOVE_INI_ENV = "T7_MOVE_INI"


def _moveIniRaw():
    """把 ``level.ini`` / ``server.ini`` 的 ``[move]`` 段读成 dict；读不到就是空 dict。

    编码按 utf-8-sig → gbk → latin-1 依次试（Windows 记事本默认 GBK）；
    配置文件坏了**不能**把服务端带崩，所以全部吞掉返回空 dict。
    """
    override = os.environ.get(MOVE_INI_ENV)
    if override is not None:
        text = str(override).strip()
        if text.lower() in ("off", "none", "0", ""):
            return {}
        paths = (text,)
    else:
        try:
            paths = wire._iniCandidates()
        except Exception:  # noqa: BLE001
            return {}
    import configparser
    for path in paths:
        if not os.path.isfile(path):
            continue
        raw = None
        for encoding in ("utf-8-sig", "gbk", "latin-1"):
            try:
                with open(path, encoding=encoding) as handle:
                    raw = handle.read()
                break
            except (OSError, UnicodeDecodeError, LookupError):
                continue
        if raw is None:
            continue
        try:
            parser = configparser.ConfigParser()
            parser.read_string(raw)
            if not parser.has_section(MOVE_INI_SECTION):
                continue
            return {key: parser.get(MOVE_INI_SECTION, key)
                    for key in parser.options(MOVE_INI_SECTION)}
        except Exception:  # noqa: BLE001
            continue
    return {}


_MOVE_INI = _moveIniRaw()


def _moveIniFlag(key, default):
    """三态读法：``off/false/no/0`` → False；``on/true/yes/1`` → True；其他 → ``default``。"""
    raw = str(_MOVE_INI.get(key, "")).strip().lower()
    if raw in ("off", "false", "no", "0"):
        return False
    if raw in ("on", "true", "yes", "1"):
        return True
    return default


def _moveIniText(key, default):
    raw = str(_MOVE_INI.get(key, "")).strip().lower()
    return raw or default


def _moveIniFloat(key, default, lo=None, hi=None):
    """读一个 float 旋钮；读不出/越界就退回 ``default``（配置坏不能带崩服务端）。

    为什么要有：``wf``（角速度因子）的**量纲在客户端里没有标注**
    （6 个含 ``bw/wf`` 的 TDR 结构，注释只有「角速度计算参数」/「角速度因子」，
    没有任何单位；配置表里也没有这个字段）。客户端是加壳的（``.tp3``，代码段
    vsize 0x016e7c6b ≫ rsize 0x0089be00），静态反汇编拿不到真码，所以只能实机 A/B。
    做成旋钮 = 换一个数只需重启服务端，不用改代码。
    """
    raw = str(_MOVE_INI.get(key, "")).strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if value != value:                                   # NaN
        return default
    if lo is not None and value < lo:
        return default
    if hi is not None and value > hi:
        return default
    return value


# 是否把「角速度五兄弟」填进 35 号坐骑广播。默认 **on**。
# 关掉 = 回到全 0 的旧行为（``[move] mount_angular=off``）。
MOUNT_ANGULAR_ENABLED = _moveIniFlag("mount_angular", True)
# ★★ 下行报文的**显示轴**符号（2026-09-23 新增）。详见 ``displayAxes()`` 的证据表。
#   ⚠️ **只对骑兵生效**：步兵在 ``displayAxes()`` 开头就直通返回，与引入本开关之前逐字节相同。
#   world（默认）= 历史行为（骑兵 W 发 FB=−1000），一字不动
#   client       = 显示轴 = 世界轴取负 ⇒ 骑兵 W 发 FB=+1000、S 发 −1000
#                  （= prior_art ``动作.txt`` 六 里用户现场确认的「向前慢走」配方，
#                    以及二.3 那张旧矩阵的整张符号）
#   ⚠️ 2026-09-23 把默认从 client 改回 world：设成 client 的那一轮实机「四个键全哑」，
#      事后查实是**我自己引入的 tuple 崩溃**（见 ``echoKey()``），也就是说 client
#      这一侧**根本没被真正测到**。所以先退回历史值，让实机只测一个变量。
WIRE_AXIS = _moveIniText("wire_axis", "world")
# ★ 原地转舵（只按 A/D、没按 W/S）用哪个骑乘档。详见 ``mountMoveState()``。
#   stop（默认）= MOUNT_STOP(54)，与改动前逐位相同
#   arc         = 改用左/右前弧档（真实的骑乘转向档），cv 仍为 0 ⇒ 不产生位移
MOUNT_TURN_STATE = _moveIniText("mount_turn_state", "stop")
# ★★ 原地转舵时**补发** selector4「位置校正 + 方向」（2026-10-06 新增，旋钮）。
#   实机链（会话 ``2340-1314020722`` 连接 3，262 条 ``mount-key``）：
#     * 只按 A（``w=0 a=1``）时服务端 heading 每拍都在扫（44→60→70→78→90→100→121…，
#       120 度/秒），38/35 号一条没少发（2202 + 4679 条），`cv` 也已经是非零（弧档 ⇒ moving）；
#     * 但屏幕上不转。同局「按空格那一下突然跳了个方向」⇒ **一次性**的起跳/落地报文
#       （55 号 ``BC_ACTOR_WITH_JUMP``，带同一个 ``direction_yaw``）被客户端采纳了，
#       而 38 号那族**持续驱动状态**里的朝向被客户端本地坐骑控制器覆盖掉
#       （自行车模型：没前进输入 ⇒ 角速度积分为 0）。
#     * README:44 的静态结论正好写着 selector4 在客户端构造 ``PosCommand + DirCommand``
#       —— 和跳跃那两条是同一类「提交」，不是「驱动状态」。
#   ⇒ 这一条就是把「服务端算好的朝向」用提交类报文发过去。
#
#   ⚠️ 已知的在先否证（不能当成保证）：投石车 2026-09-29 ``cat_turn_heading``
#   （``siege.py:3377``）发过 4 号**没效果**，但那一局客户端每 50 ms 自报 ``sel=3``
#   把自己的值顶回去（恒 89）；骑兵局整局只 3 条 ``sel=3`` ⇒ 情形不同，所以值得试。
#   ⚠️ 报文里带**位置**（这是「位置校正」包），所以服务端轨迹和客户端轨迹会被它对齐；
#      原地转本来就没位移，理论上不会顶偏 —— 实机若出现「人跳位」就是这条。
#   off（默认）= 一个字都不发，与加这个开关之前逐字节相同
#   on         = 骑兵原地转舵（有 A/D、没有 W/S）时，每拍补一条
MOUNT_TURN_DIRECT = _moveIniFlag("mount_turn_direct", False)
# ★★ 原地转舵改成「小步转」（2026-10-06 新增，旋钮，单位=每拍位移的百分比）。
#   ``mount_turn_direct`` 那条（每拍补 selector4）实机**否证**了 —— 还是不转。
#   四条证据合起来指向客户端本地坐骑控制器「没前进输入就不转」（自行车模型）：
#     ① stop 档不转 ② arc 档（有动画、cv=5000）不转 ③ mount_wf 1000→10000 不转
#     ④ 每拍补 selector4 仍不转，但**按空格落地那一下会跳一个方向**（空中不接管）。
#   ⇒ 这一条是唯一没试过的方向：给一点**真前进驱动**，位移由服务端逐拍积分，
#     客户端自己就能把马转过去。机理与代价详见 ``turnWalkScale()``。
#   0（默认）= 一个字都不改，与加这个旋钮之前逐字节相同（原地转舵 = 纯转、零位移）
#   30       = 每拍走 ``GROUND_STEP_DISTANCE`` 的 30% ⇒ 约 1.5 m/s，一秒挪 1.5 米
#   100      = 与正常前进同速（5 m/s），转得最快但位移最大
# ⚠️ 这条包会让 38 号的 ``fb`` 从 0 变成 -1000（前进输入），``cv`` 变成 1500（与真实
#    位移对齐）；档位仍是左/右前弧（与 ``mount_turn_state=arc`` 同一个档，不是新变量）。
MOUNT_TURN_WALK_PCT = int(round(_moveIniFloat("mount_turn_walk", 0.0, lo=0.0, hi=100.0)))
# ★★ 下行节奏（2026-09-23 新增，针对「WS 反向 / A/D 无反应」的**双重移动权威**假设）。
#   periodic（默认）= 每 ``GROUND_STEP_MS`` 重发一次驱动状态 **并附带服务端积分后的绝对坐标**，
#                     与改动前逐位相同（不做没证据的默认改动）。
#   onchange       = 只在**驱动状态变化**（按键/档位/朝向）时发一次；状态不变时**不再周期性
#                     重发绝对坐标**，让客户端按已写入控制器的驱动状态自己积分。
#
#   依据（prior_art `2026年8月9日152114移动抖动与队伍规模回退只读复核.txt`）：
#     「MOVE_BC 的运动字段是**持续驱动状态**，不是普通位置采样帧。当前兼容循环每约46.8ms
#       重复发送同一驱动状态，同时每次附带**服务端独立积分后的绝对坐标**；客户端在两个包
#       之间**也由已写入控制器的驱动状态积分**。下一包到达时 PosCommand 又将它提交到不同
#       的服务端轨迹。」实测客户端/服务端分歧**最大出现在换键/转向窗口**（0.42~0.53 m）
#       —— 与「一按键方向就反」同源。
#   同族 `2026年8月9日四方向反向与移动过快三倍修正.txt` 的实机见证：按住 W 到松开，线上
#     只有 **一条** MOVE_BC + 一条 MOVE_STOP，**没有重复驱动帧**。
#   ⚠️ ``onchange`` 会**放弃服务端的周期性位置回写** ⇒ 服务端碰撞不再逐拍约束玩家实际位置。
#      这是有意的（客户端才是位移权威），但只作 A/B，默认不开。
ECHO_CADENCE = _moveIniText("echo_cadence", "periodic")
# ★★ C 键上/下马（2026-09-23 实机接线）。协议侧证据边界见 ``codec/mount_flow.py``，
#   触发侧证据见下面 ``BATTLE_COMMAND_ID`` 那段。
#   off      = 原样：落到 app.py 的 unhandled 兜底，与接线前逐字相同（A/B 基线）
#   state    = 只翻服务端状态（``session["dismounted"]``）并把坐骑钉在原地，
#              **不下发任何新报文** ⇒ 零新增字节，这是最安全的第一步
#   rsp      = 在 state 的基础上再补一条 ``cmd=7 sel=3 MOUNT_RIDE_RSP``
#              （TDR 三个字段全部闭合，但**全历史零样本**，见 mount_flow.py）
MOUNT_UNRIDE = _moveIniText("mount_unride", "state")
if MOUNT_UNRIDE not in ("off", "state", "rsp", "bc", "free"):
    MOUNT_UNRIDE = "state"
# ★ ``MOUNT_RIDE_RSP.target`` 的取值（2026-09-23 审计）：登录应答
#   ``SyncLoginResponse(10000, 1, 0, 0)`` 告诉客户端它自己 user_id=10000；
#   RSP 的 target 是 biguint「骑马操作玩家」 ⇒ 填 user_id 层，不是 inst id。
_MOUNT_RSP_TARGET = 10000

# --- ``cmd=4``（``SH_CS_CMD_BATTLE``）里 C 键那两条通知 -------------------------
#
# 实机证据（会话 ``13976-239667435``，2026-09-23 13:33）：**骑马按 C** ⇒ 客户端上行
# ``cmd=4 sel=13``，body 恒 14 字节 ``000d001af55400b0480b4f84ebd0``，15 条**逐字节
# 相同**，分两簇（05:33:52.459~54.289 八条 / 05:34:35.399~36.434 七条，间隔
# 135~200 ms —— 像按住 C 的重复发送，不是人手动连点）。
#
# TDR ``CS_PROTO_BATTLE_DATA`` union 直读：
#   sel=13 ``CS_PROTO_BATTLE_UNRIDE_MOUNT`` 12B「C->S 下马通知包」
#          唯一字段 ``actor_unride_pos`` ``PROTO_VECTOR``(12B)「下马后的位置」
#   sel=12 ``CS_PROTO_BATTLE_RIDE_MOUNT``   8B「C->S 上马通知包」（``mount_rid`` biguint）
#   sel=16 ``CS_PROTO_BATTLE_DROP_MOUNT_POS`` 12B「落马后的位置上行给服务器」
#
# ⚠️ 那 12 字节**解不出 3 个 float**（``001af554`` 是 denormal、``4f84ebd0`` 是 4.5e9）
#    ⇒ 打包编码未闭合，本模块**不解释它**，只把 hex 记进日志；停机位改用服务端
#    自己算出来的 ``ground["position"]``（那个已被独立验证过）。
#
# 安全边界（普查过，不是猜的）：14 个 ``13976-*`` 会话里 ``sel=13`` **只出现在骑马
#    按 C 的那一局**；``sel=12``（上马）**一次都没有**。⇒ 这条包不会误伤别处。
#
# 当时服务端的反应 = 完全没反应：``unhandled command=4 selector=13``（app.py 兜底），
# 并且**之后仍按原节奏发 35 号骑乘广播**（同窗口 ``move-mount-bc-run`` 460 +
# ``move-mount-bc-stop`` 344）⇒ 这就是「按 C 没反应」的全部原因。
BATTLE_COMMAND_ID = 4
UNRIDE_MOUNT_SELECTOR = 13
RIDE_MOUNT_SELECTOR = 12
# ⚠️ 两条的 body 长度**不一样**：下马带 ``actor_unride_pos`` PROTO_VECTOR(12B)，
#    上马只带 ``mount_rid`` biguint(8B)。写成一个常量就会在 sel=12 上炸
#    （2026-09-23 被 verify_mount_turn.py 的新检查当场抓到）。
UNRIDE_MOUNT_BODY_LENGTH = 14      # 2(sel) + 12(actor_unride_pos)
RIDE_MOUNT_BODY_LENGTH = 10        # 2(sel) + 8(mount_rid)


def echoKey(state, forwardBack, leftRight, heading) -> str:
    """``echo_cadence`` 闸门比对用的**驱动状态指纹**。

    ⚠️⚠️ 必须是**标量/字符串**，绝不能是 tuple/list/dict。``ground`` 就是
    ``flow.session["ground"]``，而 ``flow.session`` 会被原生层做类型校验：
    写进 tuple 会让它报 ``unsupported state type: tuple`` 并**丢弃整个事件**
    —— 连这一拍已经排队的 ``flow.send`` 一起丢，表现就是「按了没反应、一条下行都没有」。
    2026-09-23 实机翻车实录（会话 ``13976-237448338``）：本条闸门最初写的是
    ``ground["lastEcho"] = (state, fb, lr, heading)``，结果 C2S 收到 194 条按键事件，
    S2C 里 ``move-ground-*`` **一条都没有**，同一会话 144 条
    ``unsupported state type: tuple`` 的 ERROR，首条时间与第一次按 W **只差 2 毫秒**。
    同族坑的既有记录见 ``jumpState()`` 的 docstring。
    """
    return "%d|%d|%d|%.3f" % (state, forwardBack, leftRight, heading)


# 位移基准约定。``legacy`` = 历史行为（前进方位角 = −朝向，未验证）；``heading`` = 前进沿朝向(+θ)。
# ⚠️ 这两个是**互斥假设**，不是叠加功能：见 README「移动基准」一节。
MOVE_BASIS = _moveIniText("basis", "legacy")
# ★ 骑乘按键诊断（默认 **on**）。C2S 负载是加密的，抓包里**读不到** WASD 掩码，
# 于是「A 到底是左转还是右转」「basis 该选哪个」在离线永远无法判定。
# 打开后：骑乘态**每次掩码变化**落一行 ``mount-key mask=… turn=… heading=…``，
# 与同一时刻 35 号报文的 ``dir`` 斜坡对质即可定案。一行/次按键事件，不刷屏。
# 关掉：``[move] key_diag=off``。
MOVE_KEY_DIAG = _moveIniFlag("key_diag", True)
# 由 controls 按 ini 决定后写进 codec（``move_flow`` 是纯 codec，不自己读配置）。
move_flow.BASIS_FORWARD_ALONG_HEADING = (MOVE_BASIS == "heading")

# 这一行让「到底读没读到 [move] 段」不再靠猜（与 [contracts] 那几行同一风格）。
print("[move] mount_angular=" + ("on" if MOUNT_ANGULAR_ENABLED else "off")
      + " basis=" + MOVE_BASIS
      + " key_diag=" + ("on" if MOVE_KEY_DIAG else "off")
      + " source=" + (MOVE_INI_SECTION if _MOVE_INI else "default"),
      flush=True)
# 第二行：显示轴符号 + 原地转舵档。这两条是 2026-09-23 实机反馈（「WS 反了 / A/D 不能用」）
# 对应的旋钮，打出来才能一眼看出「到底读到了没有」。
print("[move] wire_axis=" + WIRE_AXIS + " mount_turn_state=" + MOUNT_TURN_STATE
      + " mount_turn_direct=" + ("on" if MOUNT_TURN_DIRECT else "off")
      + " mount_turn_walk=" + str(MOUNT_TURN_WALK_PCT),
      flush=True)
# 第三行：下行节奏 + C 键下马档。``periodic`` = 旧行为；``onchange`` = 只在驱动状态变化时发（A/B 用）。
print("[move] echo_cadence=" + ECHO_CADENCE + " mount_unride=" + MOUNT_UNRIDE, flush=True)


# 骑兵转舵。本轮实机（``44792-158912374``，2026-09-22 15:06 之后）客户端在骑乘态
# **一条朝向上行都没发**（``cmd=2 sel=3`` 计 0 次），而同一张卡没发 35 号那局
# （``44792-154674315``）发了 341 次、yaw 从 179 平滑扫到 −155。⇒ 上了马，朝向就归
# 服务端算。原来那套「A/D = 垂直于朝向的横移」是步战的，用在马上就是用户看到的
# 「左右是平移过去的、脸还朝正前方」。
# 表源 = ``转向参数.psheet`` 骑兵待机 ``Z转向速度=120``。
# 可用 ``[move] mount_rate=`` 覆盖（度/秒，限 1..720）。
MOUNT_TURN_RATE_DPS = _moveIniFloat("mount_rate", 120.0, lo=1.0, hi=720.0)
# 骑乘时仍然直接产生位移的键：W(bit0) 与 S(bit2)。A/D 改成转舵，不再进位移投影。
MOUNT_DRIVING_KEY_MASK = 1 | 4

# ★★ 坐骑角速度五兄弟（35 号报文字段 ``bw/wf/wa/cw/waf``）的表源与取值
#
# 表源 = 客户端配置表 ``转向参数.psheet``（副本
# ``D:\dfjq_out\config\propsheet\转向参数.psheet``，1003 行，GB2312 XML）：
#     骑兵待机：Y转向速度=180   Z转向速度=120   Z转向加速度=0
#     步兵待机：Z转向速度=1440  Z转向加速度=2880
#     （另有「转向参数附加」Z转向速度=450，其余 0 —— **尚未启用**，见 README）
# 骑兵那 20 行的 ``Z转向加速度`` **全是 0**，所以 ``wa`` 给 0 是有表依据的，不是省事。
#
# 单位：服务端自己的积分器 ``advanceMountHeading`` 就是按 ``MOUNT_TURN_RATE_DPS`` 度/秒
# 累加朝向的（50ms × 120°/s = 6°，与实机 35 号 ``dir`` 的步进完全对上），
# 所以 ``bw``（基准角速度）与 ``cw``（当前角速度）都取这个数，口径是**度/秒**。
MOUNT_TURN_ACCEL_DPS2 = 0.0   # 角加速度 wa ← 骑兵待机 Z转向加速度=0
# ---------------------------------------------------------------------------
# ★★★ ``wf``（角速度因子）—— 量纲定案的**全部证据**，以及为什么还剩一个自由参数
# ---------------------------------------------------------------------------
# ① **TDR 里没有量纲**。用 ``hkx_decode/tdr_move_structs.py`` 把客户端内嵌
#    ``sh_proto_cs`` 里含 ``bw``/``wf`` 的结构全列了一遍，共 6 个：
#      CS_PROTO_MOVE_BC(33)        bw「base_w, 基准角速度」  wf「w_factor, 角速度计算参数」
#      CS_PROTO_MOVE_MOUNT_BC(35)  bw 无注释                wf 无注释
#      CS_PROTO_MOVE_MO(50)        同上
#      CS_PROTO_MOVE_MOUNT_DATA    bw「基础角速度」          wf「**角速度因子**」← 最清楚的一份
#      CS_PROTO_MOVE_BC_WITH_SPECIAL_ANIMATION(39) 同 33
#      ACTOR_CURR_MOVE_DATA        bw「base_w, 基准角速度」  wf「w_factor, 角速度计算参数」
#    ⇒ ``wf`` 的语义是「**因子**」（乘数），**不是**单位；exe 里唯一的单位标注
#    ``short*0.01`` 是开发者的 TODO 备注（「也可以改成 short*0.01 的形式」），
#    只针对 cv/mv/a，跟角速度无关。配置表里也**没有**这个字段。
#
# ② **客户端确实消费它**。prior_art（原游戏客户端同一份的运行时解包映像反汇编）
#    已闭合：``0x00AB5110`` 是 33 号接收器，
#    「客户端实际量化消费 LR/FB/a/bw/wf/cv/mv，直接复制 position，**dir/waf 在该入口未读**」；
#    ``GeMovableMoveCommand::execute 0x007CFE30`` 把 ``FB/LR/a/bw/wf/cv/mv/dir``
#    **持久写入** ``GeHkpClientCharacterController`` —— 是**持续驱动状态**，不是一次采样。
#    ⇒ 全 0 = 告诉客户端「这匹马的角速度恒为 0」，与实机症状吻合。
#
# ③ **量化标度 1/1000**。同族 sub60 的「五个运动量」是 cv/mv/a/LR/FB，笔记记明
#    「有符号 word 读取并按原生量化常量转 float」，且兼容策略 ``cv=mv=1000`` ≡
#    「1.0 单位/秒」（``LR/FB`` 同样是 ±1000 → ±1.0）。把这条标度套到 ``bw``：
#    ``120`` → 0.120；而 0.120°/ms ≡ 120°/s，正好等于表里的 ``Z转向速度=120``。
#    ⇒ ``bw``/``cw`` 填 120 在两种读法下**都**是 120°/s，这一步是稳的。
#
# ④ **唯一没闭合的**：``wf`` 是千分比（1000=1.0）、百分比（100=1.0）还是万分比（10000=1.0）。
#    客户端加壳（``.tp3``，代码段 vsize 0x016e7c6b ≫ rsize 0x0089be00），静态反汇编
#    拿不到真码；运行时解包映像已不在盘上（``D:\TieJi_ServerSpike``、``D:\codex`` 均不存在），
#    prior_art 也只写了「量化常量」没写数值；原服非零 MOVE_BC 样本为零（笔记明记）。
#    ⇒ **只能实机 A/B**。所以做成旋钮：``[move] mount_wf=``。
#    判据：默认 1000。若「马转得飞快」（约 10 倍）→ 改 100；若「几乎不转」→ 改 10000。
MOUNT_TURN_WF = int(round(_moveIniFloat("mount_wf", 1000.0, lo=0.0, hi=100000.0)))
MOUNT_TURN_WAF = 0            # 角加速度影响因子 ← wa 已经是 0

# 旋钮的实际取值必须**在启动时打印出来** —— 否则「我改了 ini 到底生效没」又要靠猜。
# （``mount_angular``/``basis``/``key_diag`` 那行在上面，那时这几个常量还没定义。）
print("[move] mount_rate=%.1f mount_wf=%d mount_waf=%d wa=%.1f"
      % (MOUNT_TURN_RATE_DPS, MOUNT_TURN_WF, MOUNT_TURN_WAF, MOUNT_TURN_ACCEL_DPS2),
      flush=True)


def mountAngular(flow, ground):
    """35 号报文的角速度五兄弟 ``(bw, wf, wa, cw, waf)``。

    为什么必须填：``CS_PROTO_MOVE_BC``(33) 的字段注释写着马的角速度 =
    ``bw``(基准角速度) × ``wf``(角速度计算参数)，受 ``waf``(角加速度影响因子) 影响。
    服务端此前把这五个连同 ``cw`` **全发 0**，等于告诉客户端「这匹马的角速度恒为 0」，
    可服务端又**每 50 ms 把 ``dir`` 硬掰 6°** —— 客户端看到的是「朝向在瞬移、
    角速度却是 0」，转不起来，只能横着滑。这正是实机「WS 前进后退 变左右了」的机制。

    ``cw`` 取**带符号**的当前角速度（``mountTurnKey`` 的 A=+1 / D=−1），
    与服务端这一拍实际累加进 ``heading`` 的方向严格同号 —— 两边不能各说各话。

    只在 ``broadcastMount`` 里被调用，而那条只在 ``mounted(flow)`` 时发 ⇒ 步兵路径不受影响。
    """
    if not MOUNT_ANGULAR_ENABLED:
        return 0, 0, 0, 0, 0
    mask = ground.get("mask", -1)
    turn = mountTurnKey(mask) if mask >= 0 else 0
    rate = int(round(MOUNT_TURN_RATE_DPS))
    return (rate,
            MOUNT_TURN_WF,
            int(round(MOUNT_TURN_ACCEL_DPS2)),
            turn * rate,
            MOUNT_TURN_WAF)



def mounted(flow) -> bool:
    """这一局是不是「骑兵驱动」（名册给这张武将卡配了坐骑，且**当前还没下马**）。

    ★ 2026-09-23：叠加**运行时下马**。骑马按 C 时客户端上行
    ``cmd=4 sel=13 CS_PROTO_BATTLE_UNRIDE_MOUNT``（「C->S 下马通知包」，
    见 ``mountCommand``），原版服务端据此把角色切回步兵。本函数是
    「骑兵 vs 步兵」的**唯一闸门**（全文件 12 处调用点全是这个分支），
    所以只要这里认 ``session["dismounted"]``，下面这些会**一起**停，不用逐处改：

      * 骑乘档 ``mountWireState`` / ``mountMoveState``（回落到步战档）
      * 骑乘按键掩码 ``drivingMask``（A/D 从「转舵」变回「横移」）
      * 骑乘朝向回显 ``displayAxes`` / ``advanceMountHeading``
      * 35 号 ``broadcastMount``（不再发骑乘广播）
      * ``mount-key`` 诊断行

    ⚠️ 与 ``mountParked()`` **不是同一个问题**，别合并：下马后控制语义切回步兵，
    但坐骑实体**还得继续收到 35 号**（否则会踩 ``MOUNT_BC_ENABLED`` 上方那条
    实机判据：坐骑停在未初始化位置 → 骑手被扔出边界 → 逃离战场被处决）。
    """
    if flow.session.get("dismounted"):
        return False
    return wire.heroHasMount(flow.session.get("heroId", 0))


def rigTurning(flow) -> bool:
    """这一局是不是「**正在操控投石车**」（siege 的操控台）。

    ⭐⭐ 2026-09-29 新增。用户口供：「左右还是没反应，，按 A 键会变成操控投石车
    位置方向」。根因两条，这是第一条：

      * 投石车骑手**不算骑兵** —— ``wire.heroHasMount`` 为假（名册里没有坐骑），
        所以 ``mounted()`` 那条「A/D 是转舵、不是横移」的链**从来没启动过**；
      * A/D 于是照旧进位移投影（``drivingMask`` 不摘）⇒ 服务端每 50 ms 发一条
        「向左横移」⇒ 人物连同相机**横着滑走** ⇒ 用户看到的「操控投石车位置方向」。

    ★ 只读 ``flow.session`` 里的一个 **bool 标量**（键 ``siegeCatOn``），由 ``siege``
      在「上车(6001) / 下车(6000)」两个状态**落地时刻**维护（``siege.setRigOn``）。
      **刻意不 import siege**：本函数在 50 ms 的地面 tick 里被调 3 次
      （``drivingMask`` / ``stepMoving`` / ``advanceGround``），而且 ``controls`` 是
      核心模块，不该为「器械」这个附加能力引入依赖。
      （投石车侧的自我修复：``siege.onRigTurnTick`` 每拍核对 ``onCatapult``，
      标志位万一残留，一拍之内就会被擦掉。）

    ⚠️ 与 ``mounted()`` 是**并列**关系，不是同一个东西，别合并 —— 骑兵那套
    （骑乘档 54+ / ``mountAngular`` 五兄弟 / 35 号广播）对投石车**一条都不适用**。
    """
    return bool(flow.session.get("siegeCatOn"))


def mountParked(flow) -> bool:
    """坐骑实体是否处于「已下马、钉在原地」状态（要持续发 35 号把它按住）。

    只看**名册**（不看 ``dismounted``）：骑兵这一局永远有坐骑实体，下马只是
    骑手与它分离，马还在场景里 —— 所以 35 号不能停，只能改用停机位 + STOP 档。
    """
    return (bool(flow.session.get("dismounted"))
            and wire.heroHasMount(flow.session.get("heroId", 0)))


def mountCommand(flow, command, selector, body):
    """``cmd=4`` 里的 C 键上/下马通知（``sel=13`` 下马 / ``sel=12`` 上马）。

    做的事（按 ``[move] mount_unride`` 分档，见该常量上方注释）：

      ① 翻 ``session["dismounted"]`` —— ``mounted()`` 是唯一闸门，翻掉它整条
         骑乘链一起停；
      ② 记下**停机位**（``mountPark`` / ``mountParkYaw``，取服务端自己算的
         ``ground["position"]`` / ``ground["heading"]``，不解释报文里那 12 字节）；
      ③ 立刻补一条**步兵静止档** 38 号 —— 不补的话地面定时器要等下一次移动
         才会发，客户端会一直停在最后一条骑乘档上；
      ④ 档位为 ``rsp`` 时再补一条 ``cmd=7 sel=3 MOUNT_RIDE_RSP``。

    返回 ``True`` = 这条报文已被本模块消费（不再走 app.py 的 unhandled 兜底）。
    """
    if (command != BATTLE_COMMAND_ID
            or selector not in (UNRIDE_MOUNT_SELECTOR, RIDE_MOUNT_SELECTOR)
            or flow.session["role"] != "instance" or flow.session.get("leaving")):
        return False
    if flow.session.get("controlBaseline") != wire.BASELINE_ID:
        return False
    if MOUNT_UNRIDE == "off":
        # 原样：返回 False 让它落到 app.py 的 unhandled 兜底，与接线前逐字相同。
        return False
    if not wire.heroHasMount(flow.session.get("heroId", 0)):
        # 步兵结构上发不出这两条（普查：只在骑马按 C 时出现）。真收到就记一笔，不驱动。
        flow.result["logs"].append(
            "mount-ride-ignored-not-mounted sel=" + str(selector))
        return True
    wire.exact(body, UNRIDE_MOUNT_BODY_LENGTH if selector == UNRIDE_MOUNT_SELECTOR
               else RIDE_MOUNT_BODY_LENGTH, "battle-ride-mount")

    dismount = selector == UNRIDE_MOUNT_SELECTOR
    # ★ 客户端在未收到确认时会重复发 sel=13（最新实机局 15 条，分两簇）。
    #   下马必须是**幂等**的：第一次记录停机位，后续只消费、不重新取骑手位置，
    #   否则马的 35 号会跟着已经下马走开的骑手移动，正好把「钉在原地」破坏掉。
    alreadyDismounted = bool(flow.session.get("dismounted"))
    if (dismount and alreadyDismounted) or (not dismount and not alreadyDismounted):
        flow.result["logs"].append(
            "mount-" + ("unride" if dismount else "ride")
            + "-duplicate-ignored sel=" + str(selector))
        return True
    ground = groundState(flow)
    position = ground["position"] if ground["position"] is not None else list(wire.POSITION)
    if dismount:
        # 停机位 = 按下 C 那一刻服务端算出来的位置与朝向（不解析报文的 12 字节）。
        flow.session["mountPark"] = [float(value) for value in position]
        flow.session["mountParkYaw"] = int(ground["heading"])
        flow.session["dismounted"] = True
    else:
        flow.session.pop("mountPark", None)
        flow.session.pop("mountParkYaw", None)
        flow.session["dismounted"] = False
    flow.result["logs"].append(
        "mount-" + ("unride" if dismount else "ride") + "-notify sel=" + str(selector)
        + " bodyLen=" + str(len(body)) + " payload=" + body[2:].hex()
        + " serverPos=" + "%.3f,%.3f,%.3f" % tuple(position)
        + " heading=" + str(ground["heading"])
        + " dismounted=" + ("1" if dismount else "0")
        + " switch=" + MOUNT_UNRIDE)
    # ③ 立刻把「步兵静止档」（或上马后的骑乘静止档）补出去。
    state = stopState(flow)
    broadcast(flow, position, ground["heading"], state, 0, 0,
              "mount-unride-ground-stop" if dismount else "mount-ride-mount-stop")
    # ④ 可选的下行确认（默认档 ``state`` 不发）。
    if MOUNT_UNRIDE in ("rsp", "bc", "free"):
        rideType = (mount_flow.E_CS_MOVE_RIDE_TYPE_DOWN if dismount
                    else mount_flow.E_CS_MOVE_RIDE_TYPE_UP)
        # ★ target 语义（2026-09-23 15:3x 审计改判）：TDR 里是 **biguint**(8B)
        #   「骑马操作玩家」—— 8 字节宽度 + 玩家语义 ⇒ 是 ``user_id`` 层的 id
        #   （登录应答 ``SyncLoginResponse(10000, 1, 0, 0)`` 告诉客户端自己
        #   user_id=10000；enemy 的 user_id=10001 同层），**不是** 4 字节的
        #   inst id（ACTOR_ID=1）。上一局发 1 ⇒ 客户端认到包后输入全停
        #   （会话 13976-246839642），与本帧字节逐项核对过：结构/包装层都
        #   与正常工作的 38 号一致，target 是唯一对不上的字段。
        #   2026-09-23 16:1x 实机复核（会话 13976-249702540）：target=10000
        #   的 RSP 客户端**正常消化、不冻结**，但画面仍不下马 ⇒ RSP 只是回执，
        #   缺的是 BC（TDR 原文「因为要表现给整个房间，所以是bc，不是RSP」）。
        flow.send(mount_flow.MOUNT_COMMAND,
                  mount_flow.encode_mount_ride_rsp(
                      target=_MOUNT_RSP_TARGET, ride_type=rideType),
                  "mount-ride-rsp-" + ("down" if dismount else "up"))
    if MOUNT_UNRIDE in ("bc", "free"):
        # ★ ``bc`` 档：补发「上下坐骑广播」。``map_pos`` 按 sel=52 同款
        #   2-float(x,y) 打包（依据见 ``mount_flow.encode_mount_ride_bc``），
        #   填服务端算出的下马位置；``svr_tick`` 与 38 号同源。
        rideType = (mount_flow.E_CS_MOVE_RIDE_TYPE_DOWN if dismount
                    else mount_flow.E_CS_MOVE_RIDE_TYPE_UP)
        flow.send(mount_flow.MOUNT_COMMAND,
                  mount_flow.encode_mount_ride_bc(
                      target=_MOUNT_RSP_TARGET, ride_type=rideType,
                      svr_tick=nextGroundTick(flow),
                      map_pos_xy=(position[0], position[1])),
                  "mount-ride-bc-" + ("down" if dismount else "up"))
    if MOUNT_UNRIDE == "free" and dismount:
        # ★ ``free`` 档：再补「马消失的广播」（TDR：「防止服务器和客户端场景
        #   不一致」）。三字段全闭合零猜测；``mount_rid``=视野 ADD 里的
        #   ``MOUNT_VISION_RID``(21)，客户端按它认马。下马（DOWN）才发，
        #   上马（UP）时马是新出现的，走视野 ADD，不走 FREE。
        flow.send(mount_flow.MOUNT_COMMAND,
                  mount_flow.encode_mount_free_bc(
                      mount_rid=wire.MOUNT_VISION_RID,
                      svr_tick=nextGroundTick(flow)),
                  "mount-free-bc-down")
    return True


def mountTurnKey(mask) -> int:
    """A/D 的转舵方向：-1 / +1 / 0（都不按或同时按互相抵消）。

    符号历史（**已定案，别再翻来翻去**）：
      ① 先按 ``project_standard_ground_step`` 的基向量推的是 D=+（heading 增大
         时前进方向从 +x 转向 −y，也就是顺时针）。
      ② 2026-09-22 实机反馈「A/D 不对、W/S 前进后退反了」⇒ 改成 **D=− / A=+**。
      ③ 2026-09-22 夜**实测验证 ② 是对的**：C2S 离线解密（会话密钥 = 16 个零字节，
         见 ``hkx_decode/mount_steering_verdict.py``）拿到 WASD 掩码，与同一时刻
         35 号报文的 ``dir`` 斜坡对质 —— 会话 ``13976-191219260`` **42 条一致、
         0 条不一致**（A ⇒ ``dir`` 增，D ⇒ ``dir`` 减）。
    所以本函数**不是经验值了**，改动前请先跑 ``mount_steering_verdict.py`` 复验。
    """
    left, right = (mask >> 1) & 1, (mask >> 3) & 1
    if left == right:
        return 0
    return 1 if left else -1


def advanceMountHeading(flow, ground, elapsed) -> None:
    """按住 A/D 时把朝向累加进 ``ground["heading"]``（度，int16 合法域）。

    小数留在 ``turnCarry`` 里：朝上下行都是 int16 度数，每步取整会把转角吃掉
    （50ms × 120°/s 才 6°，但慢速档会一直凑不满 1° 就永远不转）。
    """
    turn = mountTurnKey(ground["mask"])
    if not turn or elapsed <= 0:
        return
    raw = ground.get("turnCarry", 0.0) + turn * MOUNT_TURN_RATE_DPS * elapsed / 1000.0
    step = int(raw)
    ground["turnCarry"] = raw - step
    if step:
        ground["heading"] = ((ground["heading"] + step + 180) % 360) - 180


def unlockOrientation(flow):
    """进场后补一条 43 号方向锁解锁（``lock=0``/``lock_camera=0``）。

    格式三重闭环（metalib net_unit 6 + 接收器 + 执行器反汇编，见
    ``move_flow.encode_move_lock_orientation`` 的说明），语义出处是旧备份启动脚本的
    注释「正常服务器会在进场后解除角色方向锁」。出处项目据此对照过「能走 vs 不能走」。
    ⚠️ 发送时机（进场一次）是兼容推断，不是原版证据。
    """
    # server_tick 按实例时钟下发；取不到时钟（离线仿真桩 / 进图前）退回 1。
    tick = instanceTick(flow)
    flow.send(2, move_flow.encode_move_lock_orientation(
        server_tick=1 if tick is None else tick),
        "move-lock-orientation-unlock")


def activateMount(flow):
    """给坐骑实体补一条 31 号 ``MOVE_NOTIFY_ACTIVE(active=1)``。

    原版中文说明是硬约束：「unactive 的时候，移动物体是不能在地图上进行位移操作的」。
    坐骑是视野 ADD 时才新建的 movable，我们此前只对玩家(inst 1)发过这条。
    """
    if not wire.heroHasMount(flow.session.get("heroId", 0)):
        return
    # server_tick 按实例时钟下发；取不到时钟退回 1。
    tick = instanceTick(flow)
    flow.send(2, move_flow.encode_move_notify_active(
        server_tick=1 if tick is None else tick,
        target_instance_id=wire.MOUNT_VISION_INSTANCE_ID, active=1),
        "instance-move-notify-active-mount")


def notifyInAir(flow, is_in_air: int) -> None:
    """下发 E_CS_PROTO_IN_AIR_STATE_BC（cmd=2 selector 54）。

    is_in_air == 0 时客户端 GeRecvMoveDriveEnableBC 会入队
    GeMovableDriveEnableCommand(true)，这是客户端本地跳跃/位移预测的前置条件。
    不宣称该报文已恢复原服的全部滞空语义。
    """
    if is_in_air not in (0, 1):
        raise ValueError("in-air state must be 0 or 1")
    ground = groundState(flow)
    tick = nextGroundTick(flow)
    body = move_flow.encode_move_in_air_state_bc(
        server_tick=tick, target_instance_id=1, is_in_air=is_in_air)
    ground["tick"] = tick
    ground["timingTick"] = tick
    flow.send(2, body, "move-in-air-state-bc-" + ("air" if is_in_air else "ground"))
    if mounted(flow):
        # 滞空标志是按实体寻址的（``target_instance_id``），所以马也要单独来一条，
        # 否则客户端只把骑手放进空中。
        flow.send(2, move_flow.encode_move_in_air_state_bc(
            server_tick=tick, target_instance_id=wire.MOUNT_VISION_INSTANCE_ID,
            is_in_air=is_in_air),
            "move-in-air-state-bc-mount-" + ("air" if is_in_air else "ground"))


def _notifyCrouch(flow, ground, crouched: bool) -> None:
    """sel=39 ``BC_WITH_SPECIAL_ANIMATION`` —— 下蹲沿广播（老版实测取值）。

    老版服务端权威时期随 MOVE_STATE_DATA 下发的 ``squat/squat_animation``，
    客户端权威下改用 sel=39 独立广播；取值沿用老版实测：
    ``squat``=1/0，``squat_animation``=SQUAT(1)/END_SQUAT(2)。
    姿态取静止档（下蹲起手瞬间位移未定，不与移动广播打架）。
    """
    position = clientAuthorityPosition(flow, ground)
    tick = nextGroundTick(flow)
    body = move_flow.encode_move_bc_with_special_animation(
        server_tick=tick, target_instance_id=1,
        state=stopState(flow), left_right=0, forward_back=0, acceleration=0,
        current_velocity=0, max_velocity=0, direction_yaw=ground["heading"],
        position=tuple(position),
        squat=1 if crouched else 0,
        squat_animation=(MOVE_ANIMATION_SQUAT if crouched
                         else MOVE_ANIMATION_END_SQUAT))
    flow.send(2, body, "move-bc-special-animation-crouch-"
              + ("on" if crouched else "off"))
    ground["tick"] = tick
    ground["timingTick"] = tick


_INPUT_DIAG_MIN_MS = 2000.0
# 诊断节流时间戳**不能**写进 ``flow.session``。
#
# 为什么（2026-10-07，对齐上游契约时发现）：会话快照会被原生层逐字段打包下发，
# 它是**外部契约**的一部分。上游 ``tests/python/test_runtime_movement.py`` 有多条
# 用例直接断言「上报不改变会话」——
#   ``before = dict(flow.session); controls.message(...); assert flow.session == before``
# 而本函数原先写 ``sess["inputDiagAt"] = now`` ⇒ 会话多出一个键 ⇒ 断言失败
# （实测 11 条用例）。诊断是**纯观察**，不该有可观测副作用。
# 改成模块级 dict（同 ``_airwallCache`` 的口径），按连接号索引；不进会话、不进报文。
_inputDiagAt = {}


def _mirrorStop(flow, ground) -> None:
    """客户端权威：客户端报「方向键全松开」时补一条 STOP（第四十轮）。

    为什么必须有：客户端权威下服务端**平时一条 MOVE_BC 都不发**，只有跳跃/下蹲
    两个边沿会发。而 sel=55/39 属于「移动驱动状态」报文（prior_art 08-09 152114
    对 35 号的结论同源）—— 一旦写了「在移动」，客户端会**持久**保持那个驱动状态
    ⇒ 松手也不停（用户口供「一直在跑，停不下来了」）。老版服务端权威每 50ms
    重发一次驱动状态，松手那一拍自然带 STOP；迁移到客户端权威后这条**丢了**，
    本轮把「松手 → STOP」补回来。

    位置取**账本值**、不外推：客户端报「全松开」时它已经站住了，账本值就是真值
    （外推反而会把它往前送一截）。账本还没有位置时直接不发 —— 绝不能拿
    ``wire.POSITION``（出生点）去发 STOP，那正是「把人拽回出生点」的成因。
    """
    if ground.get("position") is None:
        return
    position = clientAuthorityPosition(flow, ground, extrapolate=False)
    broadcast(flow, position, ground["heading"], stopState(flow), 0, 0,
              "client-authority-stop-mirror")


def _mirrorMoveState(flow, ground, prevMask, prevFastRun) -> None:
    """客户端权威：按键/快跑**变化**时镜像一条 MOVE_BC（走/跑/停）。

    为什么必须有（第四十一轮，会话 16:28 实机）：客户端的走/跑**表现**跟着服务端
    MOVE_BC 的 ``state`` 走 —— 老版服务端权威每 50ms 重发一次，所以「按 W 一会
    就进跑」；迁到客户端权威后服务端平时一条都不发 ⇒ 用户口供「shift 冲刺没有
    出来 / ~ 空手跑也没出来」。这里把「按变化发」补回来。

    ★ 顺带修掉「一直在跑」的**另一半**：``groundMoveState`` 只在
      ``projection.moving`` 为真时推进 ``moveStartedAt``，而客户端权威下它此前
      只被跳跃广播调用（两跳之间没人调）⇒ 那个时间戳**只增不减**。本函数在
      **每一次按键变化**都调它 ⇒ 松手那一拍 ``projection.moving`` 为假，
      ``moveStartedAt`` 被清成 None，下一次起步重新计时（2 秒兜底才正确）。

    ★ 位置用 ``clientAuthorityPosition``（**外推**）：镜像帧落在两次上报之间，
      拿账本原值会把客户端往回拽一截（用户口供「像回档一样」）。
    """
    mask = ground.get("mask", -1)
    if mask <= 0:
        flow.cancel(CC_RUN_TIMER)
        ground["moveStartedAt"] = None
        if prevMask not in (0, -1):
            _mirrorStop(flow, ground)
        return
    if prevMask in (0, -1):
        # 起步沿：立基准 + 排「持续前向 N 秒进跑」那一拍（老版靠 50ms 周期帧达成）。
        ground["moveStartedAt"] = flow.now
        flow.later(CC_RUN_TIMER, GROUND_RUN_DELAY_MS)
    projection = drivingProjection(flow, mask, ground["heading"])
    if not projection.moving:
        return                     # 原地转舵（骑兵 / 投石车）：不动位移就不镜像
    state = groundMoveState(ground, projection, flow.now)
    position = clientAuthorityPosition(flow, ground)
    broadcast(flow, position, ground["heading"], state,
              projection.forward_back, projection.left_right,
              "client-authority-move-mirror")


def mirrorRunState(flow) -> None:
    """``cc-run-delay`` 到点：如果还在移动，补一条「跑」档广播。

    与 ``_mirrorMoveState`` 同一套判定（``groundMoveState`` + ``displayAxes``），
    只是触发源是**时间**而不是按键变化 —— 老版服务端权威的 50ms 周期帧就是
    靠这个把「持续前向 2 秒 → 进跑」送到客户端的。
    """
    ground = groundState(flow)
    mask = ground.get("mask", -1)
    if mask <= 0:
        return
    projection = drivingProjection(flow, mask, ground["heading"])
    if not projection.moving:
        return
    # ★ 到点判定：``moveStartedAt`` 可能被**中间那几次跳跃广播**重锚（那条路径也调
    #   ``groundMoveState``，moving 时会把 None 填成 now）⇒ 这里不能假定「定时器
    #   到点就一定够 2 秒」。不够就按剩余时间补排一拍，避免漏掉「进跑」。
    started = ground.get("moveStartedAt")
    if type(started) is not int:
        ground["moveStartedAt"] = flow.now
        started = flow.now
    remaining = GROUND_RUN_DELAY_MS - (flow.now - started)
    if remaining > 0:
        flow.later(CC_RUN_TIMER, remaining)
        return
    state = groundMoveState(ground, projection, flow.now)
    position = clientAuthorityPosition(flow, ground)
    broadcast(flow, position, ground["heading"], state,
              projection.forward_back, projection.left_right,
              "client-authority-run-delay-mirror")

def _inputDiag(flow, selector, category, keys, body) -> None:
    """sel=52 上报分布诊断（节流 2s，纯日志，任何异常静默吞掉）。

    * ``cat=1 keys=00..``      —— 常规 WASD 包，看 keys[4]/keys[5] 是否出现 1
    * ``cat=N keys=..``        —— 非 WASD 类别（CTRL=2 / SPACE=3，第三十五轮起
      已在本地做跳跃/下蹲同步）；``pos=`` 供离线判断这两类包的位置语义。

    ⚠️ 节流时间戳存在**模块级** ``_inputDiagAt``（按连接号索引），**不写会话** ——
    会话是对外契约，上游有用例断言「上报不改会话」，写进去会破坏它（见该常量注释）。
    """
    try:
        key = getattr(flow, "connection", None)
        if key is None:
            key = id(flow)
        now = flow.now
        last = _inputDiagAt.get(key)
        if last is not None and now - last < _INPUT_DIAG_MIN_MS:
            return
        _inputDiagAt[key] = now
        try:
            pos = "%.1f,%.1f,%.1f" % struct.unpack_from(">fff", body, 12)
        except Exception:  # noqa: BLE001
            pos = "?"
        flow.result["logs"].append(
            "input-report-diag sel=%d cat=%d keys=%s pos=%s"
            % (selector, category, " ".join("%02x" % value for value in keys), pos))
    except Exception:  # noqa: BLE001
        pass


def _syncJumpAndCrouch(flow, ground, keys) -> None:
    """客户端权威下的跳跃闸门与下蹲回声（对比老版状态机的最小等价实现）。

    * 跳跃按下沿且不在空中 ⇒ ``notifyInAir(1)`` + 排 ``JUMP_AIR_TIMER``；
      空中期间（``jumpAir``=1）忽略新的按下沿 —— 这就是老版
      ``jumpActive()`` 闸门的客户端权威等价物（防无限跳）。
    * 下蹲沿（keys[4] 与上次不同）⇒ sel=39 下蹲/起身广播。
    * 每一步都只写标量进 ``ground``（session 原生层类型校验，同老版教训）。
    """
    jump_now = bool(keys[5])
    jump_prev = ground.get("jumpPressedPrev")
    ground["jumpPressedPrev"] = jump_now
    # ⭐ 键位观察日志：只要任何一份上报里出现跳/蹲位，立即记一条（按状态去重）。
    #   若实机日志里这条从不出现而 input-report-diag 正常滚动 ⇒ 客户端把
    #   跳跃/CTRL 放在了别的 category/selector，需要换位置对接。
    #   ⚠️⚠️ 只能用**标量**存状态：会话快照会被原生层 ``host_runtime._pack``
    #   逐值类型校验，tuple/list-of-mixed 之外 tuple 会抛
    #   ``TypeError: unsupported state type: tuple`` 并**丢弃整个事件**
    #   （会话 -10 实证：56 次 TypeError、按空格/CTRL 完全无反应、连诊断日志
    #   都被一起丢掉）。所以这里拆成两个 bool。
    try:
        _jumpObs = bool(jump_now)
        _crouchObs = bool(keys[4])
        if _jumpObs or _crouchObs:
            if (_jumpObs != ground.get("inputKeyObsJump")
                    or _crouchObs != ground.get("inputKeyObsCrouch")):
                ground["inputKeyObsJump"] = _jumpObs
                ground["inputKeyObsCrouch"] = _crouchObs
                flow.result["logs"].append(
                    "input-key-observed jump=%d crouch=%d cat_keys=%s"
                    % (jump_now, _crouchObs, " ".join("%02x" % v for v in keys)))
        else:
            ground["inputKeyObsJump"] = False
            ground["inputKeyObsCrouch"] = False
    except Exception:  # noqa: BLE001
        pass
    if jump_now and not jump_prev and not ground.get("jumpAir"):
        ground["jumpAir"] = 1
        try:
            notifyInAir(flow, is_in_air=1)
        except Exception as error:  # noqa: BLE001
            flow.result["logs"].append("jump-in-air-failed %r" % (error,))
        # ⭐⭐⭐ 2026-10-07 第三十八轮（会话 -11 实证）：老跳跃状态机
        #   （``startJump``/``stepJump``）在客户端权威下**从不被调用** ⇒
        #   wire 里 ``sel=55 BC_ACTOR_WITH_JUMP`` 一条不发、只有 in-air 状态
        #   ⇒ 客户端收不到跳跃动画广播，按空格没反应（服务端日志却「全都发了」）。
        #   这里补上老 ``startJump`` 的**广播那一半**（jump=1/动画=起跳(4)/
        #   附带 sel=39）；**不做**服务端垂直积分 —— 客户端权威下位移归客户端。
        try:
            notifyJump(flow, jump=1, jump_animation=MOVE_ANIMATION_JUMP,
                       special_animation=True)
        except Exception as error:  # noqa: BLE001
            flow.result["logs"].append("jump-broadcast-failed %r" % (error,))
        flow.later(JUMP_AIR_TIMER, JUMP_AIR_MS)
        flow.result["logs"].append("jump-gate air=1 ms=%d" % JUMP_AIR_MS)
    crouch_now = bool(keys[4])
    # ⭐ 第三十五轮：``crouchPrev`` 首次为 None 时按「未蹲」处理 —— 客户端权威下
    #   玩家可能全程站着不动（无 cat=1 包），第一份包就是 CTRL 按下
    #   （会话 -9 实证），按旧写法这条会被当基准吞掉、永远不广播。
    crouch_prev = bool(ground.get("crouchPrev"))
    ground["crouchPrev"] = crouch_now
    if crouch_now != crouch_prev:
        _notifyCrouch(flow, ground, crouch_now)


# --- 跳跃：状态、广播与垂直积分 -------------------------------------------------

def jumpState(flow):
    """``session["jump"]`` 只存标量，保证可被原生层序列化。

    踩过的坑：``flow.session`` 会被原生层做类型校验，写进 tuple 会让它报
    ``unsupported state type: tuple`` 并**丢弃整个事件**（表现是“按了没日志没下行”）。
    """
    jump = flow.session.setdefault("jump", dict(_JUMP_DEFAULT))
    for name, value in _JUMP_DEFAULT.items():
        jump.setdefault(name, value)
    return jump


def resetJump(flow) -> None:
    jumpState(flow).update(_JUMP_DEFAULT)


def jumpActive(flow) -> bool:
    return flow.session.get("jump", {}).get("active") == 1


def _jumpAxes(flow, ground):
    """跳跃广播用的 ``(state, wire_lr, wire_fb)``。

    与地面路径**同一套符号约定**（世界轴进 ``displayAxes`` 换算成显示轴 ×1000），
    这样 sel=55 与 sel=38 对“当前是否在移动”的说法不会互相矛盾。
    未按过任何 WASD 时 ``ground["mask"]`` 是 -1，必须按静止处理——
    ``-1 & 1 == 1`` 会被误当成“按着 W”。
    """
    mask = ground["mask"]
    if mask < 0:
        return stopState(flow), 0, 0
    # ⭐ 第四十轮：客户端权威下掩码会**过期**（客户端 ~3 帧/秒，只在按键变化/上报
    #   节拍发包）。最后一次上报超出外推窗口 ⇒ 根本不知道玩家还在不在动，按静止
    #   处理 —— 绝不能凭一个陈旧掩码把「跑」的驱动状态写进客户端（那正是
    #   「一直在跑，停不下来」）。服务端权威没有 ``reportAt`` 键 ⇒ 本闸门不生效，
    #   行为逐位不变。
    reportAt = ground.get("reportAt")
    if type(reportAt) is int and flow.now - reportAt > CLIENT_EXTRAP_MAX_MS:
        return stopState(flow), 0, 0
    # ⭐ 第四十轮应急旋钮：``cc_jump_state=stop`` ⇒ 服务端一个移动驱动状态都不写
    #   （见 ``jumpStateMirrorMode``）。
    if jumpStateMirrorMode() == "stop":
        return stopState(flow), 0, 0
    projection = drivingProjection(flow, mask, ground["heading"])
    if not projection.moving:
        return stopState(flow), 0, 0
    displayFb, displayLr = displayAxes(flow, projection.forward_back, projection.left_right)
    # ⭐⭐⭐ 第四十二轮（2026-10-07）：**只有跳跃广播这一侧**取负。
    #   为什么 38 号不取负、这里却要取负 —— 见 ``displayAxes`` 顶部的返工记录：
    #   38 号 ``MOVE_BC_WITH_SYSTEM_AND_ACTIVE`` 是**持续驱动命令**（必须跟键盘同轴，
    #   否则用户实测「WASD 都是反方向」）；55 号 ``BC_ACTOR_WITH_JUMP`` 是**动画广播**，
    #   按客户端**显示**约定。prior_art《2026年8月9日四方向反向与移动过快三倍修正》
    #   实测原文：「当前 W 事件的 wire 表现为 fb=-1000，而历史成功抓包 W 为 fb=+1000；
    #   **这解释 W 显示后退步态**」⇒ 显示侧 W 必须是 **+1000**。
    #   症状对应（用户口供「按完空格，还是会后退」）：按着 W 起跳时 55 号带 fb=-1000。
    #   ⚠️ 只在「客户端权威 + 步兵」生效；服务端权威与骑兵一个字不动。
    #   回退（10 秒）：`[move] cc_foot_axis=world` / env `T7_CC_FOOT_AXIS=world`。
    if runtimeMovement(flow) and not mounted(flow) and footAxisMode() == "client":
        displayFb, displayLr = -displayFb, -displayLr
    return (moveWireState(flow, ground, mask, projection),
            displayLr * 1000, displayFb * 1000)


def notifyJump(flow, *, jump: int, jump_animation: int, special_animation: bool) -> None:
    """下发跳跃/落地广播。

    * 主报文 sel=55 ``BC_ACTOR_WITH_JUMP``（TDR 描述：玩家带有跳跃动画信息的广播消息）
    * ``special_animation`` 为真时附带 sel=39 ``BC_WITH_SPECIAL_ANIMATION``
      （TDR 描述：移动广播，带特殊动画，例如马撞停、跳等）

    只在起跳/落地两个边沿附带 sel=39，滞空期间只发 sel=55，避免每 50ms 两条
    重复的位置广播。若实机证明客户端只消费 sel=39，把它改成每步都发即可。
    """
    ground = groundState(flow)
    position = clientAuthorityPosition(flow, ground)
    state, wire_lr, wire_fb = _jumpAxes(flow, ground)
    moving = state not in (move_flow.MOVE_GROUND_STATE_STOP,
                           move_flow.MOVE_GROUND_MOUNT_STATE_STOP)
    current_velocity, max_velocity = velocityPair(flow, moving)
    tick = nextGroundTick(flow)
    body = move_flow.encode_move_bc_actor_with_jump(
        server_tick=tick, target_instance_id=1, state=state,
        left_right=wire_lr, forward_back=wire_fb, acceleration=0,
        current_velocity=current_velocity, max_velocity=max_velocity,
        direction_yaw=ground["heading"], position=tuple(position),
        jump=jump, jump_animation=jump_animation)
    flow.send(2, body, "move-bc-actor-with-jump-" + ("air" if jump else "ground"))
    # 人和马一起跳：原地起跳时地面那条 35 号根本不会发（没按键 → 定时器直接 return），
    # 马会留在地上。这里补一条同位置、同 tick 的坐骑广播，z 已经是抛物线上的值。
    broadcastMount(flow, position, ground["heading"], state, moving, tick)
    if special_animation:
        special = move_flow.encode_move_bc_with_special_animation(
            server_tick=tick, target_instance_id=1, state=state,
            left_right=wire_lr, forward_back=wire_fb,
            current_velocity=current_velocity, max_velocity=max_velocity,
            direction_yaw=ground["heading"], position=tuple(position),
            jump=jump, jump_animation=jump_animation)
        flow.send(2, special,
                  "move-bc-special-animation-jump-" + ("air" if jump else "ground"))
    ground["tick"] = tick
    ground["timingTick"] = tick


def startJump(flow) -> None:
    """起跳：进空中态 + 下发 jump=1/动画=起跳(4)，并按 GROUND_STEP_MS 开始垂直积分。"""
    ground = groundState(flow)
    if ground["position"] is None:
        ground["position"] = list(wire.POSITION)
    jump = jumpState(flow)
    takeOffZ = float(ground["position"][2])
    jump.update(active=1, phase=JUMP_PHASE_AIR, z=takeOffZ,
                vz=JUMP_INITIAL_VELOCITY, groundZ=takeOffZ)
    notifyInAir(flow, is_in_air=1)
    notifyJump(flow, jump=1, jump_animation=MOVE_ANIMATION_JUMP, special_animation=True)
    flow.later(JUMP_TIMER, wire.GROUND_STEP_MS)
    flow.result["logs"].append(
        "jump-take-off z=" + str(round(takeOffZ, 4))
        + " vz=" + str(JUMP_INITIAL_VELOCITY) + " g=" + str(JUMP_GRAVITY)
        + " anim=" + str(MOVE_ANIMATION_JUMP))


def stepJump(flow) -> None:
    """跳跃阶段机的一步：AIR 做垂直积分，LAND/END 播落地动画尾巴。

    AIR   —— 半隐式欧拉积分 z，把高度经位置字段回流给客户端；``z <= groundZ`` 判定落地。
    LAND  —— 播“着陆缓冲”(5)，再排一拍进入 END。
    END   —— 播“结束着陆”(6)，然后**必须**回 NONE(0) 并清 ``active``。

    旧实现在这里直接 ``active=0`` 且动画发的是 1（=下蹲），于是角色停在蹲姿上
    （2026-09-18 实机“起不来了”）。现在收尾一定回到 ``MOVE_ANIMATION_NONE``。

    落地判据仍是最小回流：回到起跳高度即算落地，原服落地语义未闭合。
    """
    jump = jumpState(flow)
    if not jump["active"]:
        return
    ground = groundState(flow)
    phase = jump["phase"]

    if phase == JUMP_PHASE_LAND:
        jump["phase"] = JUMP_PHASE_END
        notifyJump(flow, jump=0, jump_animation=MOVE_ANIMATION_END_JUMP,
                   special_animation=False)
        flow.later(JUMP_TIMER, JUMP_END_MS)
        return

    if phase == JUMP_PHASE_END:
        jump.update(active=0, phase=JUMP_PHASE_IDLE, z=jump["groundZ"], vz=0.0)
        notifyJump(flow, jump=0, jump_animation=MOVE_ANIMATION_NONE,
                   special_animation=False)
        flow.result["logs"].append("jump-animation-end anim=" + str(MOVE_ANIMATION_NONE))
        return

    dt = wire.GROUND_STEP_MS / 1000.0
    jump["vz"] = jump["vz"] - JUMP_GRAVITY * dt
    z = jump["z"] + jump["vz"] * dt
    position = ground["position"]
    if z <= jump["groundZ"]:
        if position is not None:
            position[2] = jump["groundZ"]
        # 进 LAND 相位：active 保持 1，等落地动画播完再清。
        jump.update(phase=JUMP_PHASE_LAND, z=jump["groundZ"], vz=0.0)
        notifyJump(flow, jump=0, jump_animation=MOVE_ANIMATION_JUMP_LAND,
                   special_animation=True)
        notifyInAir(flow, is_in_air=0)
        flow.later(JUMP_TIMER, JUMP_LAND_MS)
        flow.result["logs"].append(
            "jump-landing z=" + str(round(jump["groundZ"], 4))
            + " anim=" + str(MOVE_ANIMATION_JUMP_LAND))
        return
    jump["z"] = z
    if position is not None:
        position[2] = z
    notifyJump(flow, jump=1, jump_animation=MOVE_ANIMATION_JUMP, special_animation=False)
    flow.later(JUMP_TIMER, wire.GROUND_STEP_MS)


def handleSpace(flow, keys, body) -> bool:
    """处理 SPACE 类别按键包（``key_categ == 3``，keys[5] = space_state）。

    只认“按下”边沿：按住不放不重复起跳。起跳门控用 ``groundEnabled``
    （与 WASD 位移同一道门），避免在 GAME 之前就下发跳跃广播。
    """
    jump = jumpState(flow)
    if not keys[5]:
        jump["pressed"] = 0
        return True
    if jump["pressed"]:
        return True
    jump["pressed"] = 1
    if not groundEnabled(flow) or jump["active"]:
        return True
    ground = groundState(flow)
    if ground["position"] is None:
        # SPACE 包与 WASD 包同布局，同样带 curr_pos；从未按过 WASD 的纯原地跳跃
        # 必须靠这里取初始位置，否则没有坐标可用。
        position = list(struct.unpack_from(">fff", body, 12))
        if not all(math.isfinite(value) for value in position):
            raise ValueError("non-finite jump take-off position")
        ground["position"] = anchorToSpawn(flow, position)
    startJump(flow)
    return True


def handleFastRun(flow, body) -> bool:
    """处理 CS_PROTO_MOVE_FAST_RUN_REQ（``cmd=2 selector=63``，SHIFT 加速 / 快跑）。

    报文只有一个字段 ``int8 is_start``「是否开始快跑」，所以：

    * 只更新 ``ground["fastRun"]`` 这个标量，**不发明任何新 state 编号**；
    * 若角色此刻正贴着地面前向移动，立刻按新的走/跑判定补发一次位移广播，
      让“按下 SHIFT 立即加速”生效，而不是等下一个 ground-step 节拍或 2 秒兜底；
    * 空中（``jumpActive``）不动地面速度，跳跃路径自己负责那一段广播；
    * **贴着空气墙时只发 STOP**，不发「移动中」——否则客户端会继续本地预测前冲
      （见 ``groundBlocked``）；
    * 在 GAME 之前到达也照样记下来（``groundEnabled`` 之后自然生效），
      避免把 PREPARE 阶段发来的请求丢掉。

    日志一律带 ``is_start`` / ``previous`` / 原始 hex：sel=63 的单键绑定尚未实机确认，
    这些字段就是下一轮比对要用的证据。
    """
    if len(body) < FAST_RUN_BODY_LENGTH:
        raise ValueError(
            "move-fast-run requires at least " + str(FAST_RUN_BODY_LENGTH)
            + " bytes, got " + str(len(body)))
    isStart = struct.unpack_from(">b", body, 2)[0]
    if isStart not in (0, 1):
        raise ValueError("fast run is_start must be 0 or 1, got " + str(isStart))
    ground = groundState(flow)
    previous = ground.get("fastRun", 0)
    ground["fastRun"] = isStart
    flow.result["logs"].append(
        "move-fast-run-" + ("start" if isStart else "stop")
        + " is_start=" + str(isStart) + " previous=" + str(previous)
        + " hex=" + body.hex())
    if previous == isStart:
        return True
    if runtimeMovement(flow):
        # 客户端权威：老版在这里一条都不发（只记标志）。但实机定案客户端的走/跑
        # **表现**是跟着服务端 MOVE_BC 的 ``state`` 走的 ⇒ 按 SHIFT 必须补一条
        # 「跑」档镜像，否则「shift 冲刺」永远显示不出来（第四十一轮口供）。
        # 位置仍走 ``clientAuthorityPosition``（外推），与移动镜像同口径。
        if (ccMoveMirrorEnabled() and inputSyncEnabled()
                and ground["mask"] > 0 and ground["position"] is not None):
            try:
                projection = drivingProjection(flow, ground["mask"], ground["heading"])
                if projection.moving:
                    state = groundMoveState(ground, projection, flow.now)
                    position = clientAuthorityPosition(flow, ground)
                    broadcast(flow, position, ground["heading"], state,
                              projection.forward_back, projection.left_right,
                              "client-authority-fast-run-mirror")
            except Exception:  # noqa: BLE001
                pass
        return True
    if not groundEnabled(flow) or ground["mask"] < 0 or ground["position"] is None:
        return True
    if jumpActive(flow):
        return True
    projection = drivingProjection(flow, ground["mask"], ground["heading"])
    if not stepMoving(flow, ground["mask"], ground["heading"]):
        return True
    if groundBlocked(ground):
        # 贴着空气墙按 SHIFT：只发 STOP，别把「移动中」再喂给客户端一次
        # （否则客户端会继续本地预测前冲）。见 groundBlocked 的说明。
        broadcast(flow, ground["position"], ground["heading"], stopState(flow), 0, 0,
                  "move-blocked-airwall-stop")
        return True
    state = moveWireState(flow, ground, ground["mask"], projection)
    broadcast(flow, ground["position"], ground["heading"], state,
              projection.forward_back, projection.left_right,
              "move-ground-fast-run-state-echo")
    return True


def timer(flow, name):
    if name == JUMP_AIR_TIMER:
        # ⭐⭐⭐ 2026-10-07：客户端权威跳跃闸门的滞空期结束 → 解除 DriveEnable
        #   （客户端恢复地面移动/允许下一次跳）。放在所有闸门**之前**：
        #   这个定时器恰恰是客户端权威模式专用的（老状态机的那套在
        #   runtimeMovement 下全被 cancel，不能让它被拦掉）。
        if inputSyncEnabled() and groundState(flow).get("jumpAir"):
            groundState(flow)["jumpAir"] = 0
            try:
                notifyInAir(flow, is_in_air=0)
                # ⭐ 第三十八轮：落地动画三拍的第一拍（着陆缓冲 5）+ 排后续两拍。
                notifyJump(flow, jump=0, jump_animation=MOVE_ANIMATION_JUMP_LAND,
                           special_animation=True)
                flow.later(JUMP_AIR_END_TIMER, JUMP_END_MS)
                flow.result["logs"].append("jump-gate air=0")
            except Exception as error:  # noqa: BLE001
                flow.result["logs"].append("jump-land-failed %r" % (error,))
        return True
    if name == JUMP_AIR_END_TIMER:
        # 第二拍：结束着陆(6)。
        try:
            notifyJump(flow, jump=0, jump_animation=MOVE_ANIMATION_END_JUMP,
                       special_animation=False)
            flow.later(JUMP_AIR_NONE_TIMER, JUMP_END_MS)
        except Exception:  # noqa: BLE001
            pass
        return True
    if name == JUMP_AIR_NONE_TIMER:
        # 第三拍：必须回 NONE(0) —— 老版就是漏了这一拍，角色停在落地姿势上。
        try:
            notifyJump(flow, jump=0, jump_animation=MOVE_ANIMATION_NONE,
                       special_animation=False)
        except Exception:  # noqa: BLE001
            pass
        return True
    if name == CC_RUN_TIMER:
        # ⭐⭐⭐ 第四十一轮：客户端权威「持续前向 N 秒 → 进跑」的那一拍。
        #   老版服务端权威靠 50ms 周期帧达成（每帧重算 groundMoveState ⇒ 到点自动
        #   变跑档）；客户端权威下服务端平时一条都不发，所以必须显式排一个定时器。
        #   ⚠️ 必须放在下面 ``runtimeMovement(flow)`` 早退**之前**：那段会把所有
        #      运动帧 cancel 掉，而本定时器恰恰是客户端权威模式专用的。
        try:
            mirrorRunState(flow)
        except Exception:  # noqa: BLE001
            pass
        return True
    if name not in ("direction-prime-start", "direction-prime-stop", "ground-step", JUMP_TIMER):
        return False
    if runtimeMovement(flow):
        # 客户端权威：一条服务端驱动的运动帧都不发（含跳跃推进）。
        # 对应上游 ``timer()`` 里的 ``if runtimeMovement(flow): cancelMotionTimers(); return True``。
        cancelMotionTimers(flow)
        return True
    if (flow.session.get("leaving") or not flow.session.get("battleEntered")
            or not groundEnabled(flow)):
        if name == JUMP_TIMER:
            resetJump(flow)
        return True
    # ⭐ 2026-09-23 siege 挂点（③「人物靠近攻城车 → 车自动往前走」）。
    #    ⚠️ 必须在下面 ``stepMoving`` 闸门**之前**：玩家走到车边站住不动时
    #    ``stepMoving`` 为假、本函数会提前 return，挂在后面就永远不触发。
    #    内部自带节流（``[cc] car_scan_every``，默认 5 ⇒ 4Hz）—— 因为
    #    ``ccobject.loadScene()`` 每调一次真读一遍 JSON，没有缓存。
    #    延迟 import 避开循环依赖（siege 顶部不 import controls）。
    #    回退：删掉本块即可，逐位回到加它之前。
    if name == "ground-step":
        try:
            from . import siege
            siege.onGroundTick(flow)
        except ImportError:
            pass
        # ⭐⭐ 2026-09-29 投石车 A/D 转向挂点（用户：「左右没反应 / 按 A 变成横移」）。
        #    ⚠️ 同样必须在下面 ``stepMoving`` 闸门**之前** —— 见那个函数的说明：
        #       这一拍的 ``mask`` 有 A/D ⇒ ``stepMoving`` 为真 ⇒ 定时器会续；
        #       而转角本身要**逐拍累加**，挂在闸门之后时序上就晚了。
        #    ⚠️ 与上面那条**分开 try**：``onGroundTick`` 的开关（``car_drive``）
        #       与本条（``cat_turn``）互不相干，一条出问题不该拖哑另一条。
        #    ⚠️ ``rigTurning`` 只是「人在投石车上」；真正的 A/D 掩码由
        #       ``siege.onRigTurnTick`` 自己查 ``ground["mask"]``（掩码为 0 ⇒ 静默收工）。
        #    回退：``cat_turn=off``（10 秒），或删掉本块（逐位回到加它之前）。
        if rigTurning(flow):
            try:
                from . import siege
                siege.onRigTurnTick(flow)
            except ImportError:
                pass
    if name == "direction-prime-start":
        return True
    elif name == "direction-prime-stop":
        ground = groundState(flow)
        if stepMoving(flow, ground["mask"], ground["heading"]):
            return True
        position = ground["position"] if ground["position"] is not None else wire.POSITION
        broadcast(flow, position, ground["heading"], stopState(flow), 0, 0,
                  "instance-legacy-prime-stop")
    elif name == JUMP_TIMER:
        stepJump(flow)
    else:
        ground = groundState(flow)
        if ground["mask"] < 0:
            return True
        if not stepMoving(flow, ground["mask"], ground["heading"]):
            return True
        blocked = advanceGround(flow)
        if blocked:
            # 撞墙：把这一拍压成 STOP、速度归零，客户端别继续本地预测前冲。
            # 定时器**不取消**——玩家一转身，下一拍 delta 不再命中就自动恢复。
            broadcast(flow, ground["position"], ground["heading"], stopState(flow), 0, 0,
                      "move-blocked-airwall-stop")
        else:
            # ⚠️ 投影必须在 advanceGround **之后**重算：那一拍里骑兵的朝向可能刚转过，
            # 而且 A/D 已经不走横移了。用转之前、没摘 A/D 的投影发 state，客户端就
            # 一边按新的 ``direction_yaw`` 转、一边按步战横移档演平移 —— 2026-09-22
            # 实机「骑兵方向都是乱的」就是这个。
            projection = drivingProjection(flow, ground["mask"], ground["heading"])
            state = moveWireState(flow, ground, ground["mask"], projection)
            # ★ 下行节奏闸门（``[move] echo_cadence``，默认 periodic = 旧行为）。
            #   ⚠️ **只对骑兵生效**（用户要求「别在影响步兵了」）：步兵恒走 periodic，
            #   与加这个闸门之前逐位相同。骑兵才有「双重移动权威」那个问题
            #   （35 号把驱动状态**持久写入**控制器，见 prior_art 08-09 152114）。
            #   驱动状态 = (state, fb, lr, heading) —— 与 prior_art 说的「持久驱动状态」同口径
            #   （那里含 dir，这里含 heading，是同一个量）。状态没变就别再重发绝对坐标，
            #   否则每 50ms 的服务端积分坐标会把客户端自己积出来的轨迹顶回去。
            #   ⚠️ 指纹走 ``echoKey()``（字符串），**不能**直接存 tuple —— 见那个函数的说明。
            echo = echoKey(state, projection.forward_back, projection.left_right,
                           ground["heading"])
            if (mounted(flow) and ECHO_CADENCE == "onchange"
                    and echo == ground.get("lastEcho")):
                pass
            else:
                if mounted(flow):
                    ground["lastEcho"] = echo
                broadcast(flow, ground["position"], ground["heading"], state,
                          projection.forward_back, projection.left_right,
                          "move-ground-periodic-position-echo")
            # ★ 原地转舵（有 A/D、没 W/S ⇒ 投影不动）时补一条 4 号提交朝向。
            #   开关 ``mount_turn_direct`` 关掉就是直接 return ⇒ 与加它之前逐字节相同。
            if mounted(flow) and not projection.moving and mountTurnKey(ground["mask"]):
                broadcastTurnDirect(
                    flow, ground["heading"],
                    ground["position"] if ground["position"] is not None else wire.POSITION)
        skipped = max(0, (flow.now - ground["nextDeadline"]) // wire.GROUND_STEP_MS)
        if skipped:
            flow.result["logs"].append("ground-timing-skip missed=" + str(skipped))
        ground["nextDeadline"] = nextDeadline(ground["nextDeadline"], flow.now)
        flow.later("ground-step", ground["nextDeadline"] - flow.now)
    return True


def message(flow, command, selector, body):
    if (command != 2 or selector not in (3, 52, FAST_RUN_SELECTOR)
            or flow.session["role"] != "instance" or flow.session.get("leaving")):
        return False
    if flow.session.get("controlBaseline") != wire.BASELINE_ID:
        return False
    if runtimeMovement(flow) and selector in (3, 52):
        # 客户端权威：这两个 selector 只**接受**客户端上报，不回落进服务端的
        # 周期回声 / 跳跃 / 转向路径（与上游同口径）。
        # ⚠️ 快跑 selector(63) 上游没有，仍走下面的原路径（见 handleFastRun）。
        # ⚠️ 契约档（``wire.contract_mode()``）：仍接受上报、更新本地快照、校验
        #    数值，但**不**下发任何镜像帧（上游合成骨架口径：
        #    ``flow.result["send"] == []``），供 vendored 参考套件对齐。
        return localReport(flow, selector, body, mirror=not wire.contract_mode())
    if selector == FAST_RUN_SELECTOR:
        # sel=63 在改动前落到 app.py 的 unhandled 兜底里（日志可见
        # `unhandled command=2 selector=63`），也就是“按了 SHIFT 服务端不认”。
        return handleFastRun(flow, body)
    if selector == 3:
        wire.exact(body, 21, "ground-heading")
        heading = struct.unpack_from(">h", body, 7)[0]
        if not -180 <= heading <= 180:
            raise ValueError("ground heading must be in -180..180")
        if not groundEnabled(flow):
            return True
        advanceGround(flow)
        groundState(flow)["heading"] = heading
        broadcastHeading(flow)
        return True
    wire.exact(body, 24, "ground-key-state")
    category = struct.unpack_from(">i", body, 2)[0]
    keys = body[6:12]
    if any(value not in (0, 1) for value in keys):
        raise ValueError("ground key state must be 0 or 1")
    if category not in (MOVE_KEY_CATEG_WASD, MOVE_KEY_CATEG_CTRL, MOVE_KEY_CATEG_SPACE):
        return False
    if category != MOVE_KEY_CATEG_WASD:
        # keys[4]=ctrl_state、keys[5]=space_state（TDR CS_PROTO_MOVE_KEY_MSG 字段表）。
        # 旧实现把这两类包当垃圾丢弃，导致 SPACE 类别（跳跃键）永远到不了服务端。
        flow.result["logs"].append(
            "move-key-category=" + str(category)
            + " ctrl=" + str(keys[4]) + " space=" + str(keys[5]))
        if category == MOVE_KEY_CATEG_SPACE:
            return handleSpace(flow, keys, body)
        return True
    initialPosition = None
    if flow.session.get("ground", {}).get("position") is None:
        initialPosition = list(struct.unpack_from(">fff", body, 12))
        if not all(math.isfinite(value) for value in initialPosition):
            raise ValueError("non-finite initial ground position")
        initialPosition = anchorToSpawn(flow, initialPosition)
    if not groundEnabled(flow):
        return True
    ground = groundState(flow)
    mask = sum(keys[index] << index for index in range(4))
    if mask == ground["mask"]:
        return True
    if ground["position"] is None:
        ground["position"] = initialPosition
    if ground["mask"] == -1:
        flow.cancel("direction-prime-start")
        flow.cancel("direction-prime-stop")
    wasMoving = stepMoving(flow, ground["mask"], ground["heading"])
    blocked = advanceGround(flow)
    # 新按键的投影：骑兵先摘 A/D（那两位是转舵，不是横移），再按朝向积分。
    projection = drivingProjection(flow, mask, ground["heading"])
    moving = stepMoving(flow, mask, ground["heading"])
    if blocked:
        # 上一次结算撞墙：即使按键仍在移动组合里，也只发 STOP（见 advanceGround）。
        state, wireForwardBack, wireLeftRight = stopState(flow), 0, 0
        reason = "move-blocked-airwall-stop"
    else:
        state = moveWireState(flow, ground, mask, projection)
        wireForwardBack, wireLeftRight = projection.forward_back, projection.left_right
        reason = ("move-ground-start-state10-echo" if moving
                  else "move-ground-stop-state1-echo")
    broadcast(flow, ground["position"], ground["heading"], state,
              wireForwardBack, wireLeftRight, reason)
    # 记下这一拍下发的驱动状态，供 ``echo_cadence=onchange`` 的闸门比对
    # （按键路径已经发过一帧，周期性那拍就不该再补发同一状态）。
    # ⚠️ 走 ``echoKey()`` 存**字符串**。这里原来写的是 tuple，直接把整条按键事件
    #    打成了 ``unsupported state type: tuple`` 被原生层丢弃 —— 四个键全哑。
    # ⚠️ 只给骑兵记：步兵不进 ``onchange`` 分支，写它没有意义，还白白改步兵的 session。
    if mounted(flow):
        ground["lastEcho"] = echoKey(state, wireForwardBack, wireLeftRight,
                                     ground["heading"])
    ground["mask"] = mask
    # ⭐⭐ 2026-09-29 投石车 A/D 转向的**按键路径**挂点（`ground["mask"]` 刚写完，
    #    hook 读到的就是这一拍的新掩码）。
    #    ⚠️ 为什么除了逐拍定时器**还要**这一个：
    #      ``moving`` 为假时下面会 ``cancel("ground-step")`` ⇒ **松手那一拍不会有
    #      任何 tick**。若只挂定时器，「松手 → 再按下」之间基准/计时两个键会一直
    #      留着上一轮的值，下次按下的第一拍就会拿一个很大的 ``elapsed`` 去积分
    #      ⇒ 一瞬间跳十几度。按键路径这一下正好把键擦干净。
    #      顺带还消掉「按下后头 50 ms 不转」的死时间（先立基准，tick 一来就能积）。
    #    ⚠️ 幂等：与定时器那条都按 ``flow.now`` 算 ``elapsed``，同一毫秒内连调两次
    #      第二次数出 0 ⇒ 不会重复积分。
    #    回退：``cat_turn=off``（10 秒），或删掉本块。
    if rigTurning(flow):
        try:
            from . import siege
            siege.onRigTurnTick(flow)
        except ImportError:
            pass
    if MOVE_KEY_DIAG and mounted(flow):
        # ★ 骑乘转向诊断。只在**掩码变化**时到这里（上面 mask==old 已提前 return），
        # 所以是「一行/次按键事件」，不会刷屏。
        # 用途：与同一时刻 35 号报文的 ``dir`` 斜坡对质 —— 掩码在加密的 C2S 里读不到，
        # 这是唯一能离线判定「A 是左转还是右转」和「basis 该选哪个」的证据。
        # 只落**原始量**，不落任何本模块自己推的方位角（那正是待验证的东西，
        # 混进证据里就成了自证）。方位角交给离线工具用 wire_fb/wire_lr + heading 算。
        # 字段：w/a/s/d = 四位原始按键；fb=s−w、lr=a−d（与 project 同口径）；
        #       turn = mountTurnKey(mask)（A=+1 / D=−1）；
        #       heading = **本拍累加后**的朝向；wire_fb/wire_lr = 本拍下行的投影值。
        # ⚠️ 2026-09-23：wire_fb/wire_lr 是**世界**轴；真正下行的**显示**轴是它的取负
        #    （``displayAxes``），这里两个都落，免得离线对质时把口径搞混。
        displayFb, displayLr = displayAxes(flow, wireForwardBack, wireLeftRight)
        flow.result["logs"].append(
            "mount-key w=%d a=%d s=%d d=%d fb=%d lr=%d turn=%d heading=%.1f"
            " wire_fb=%d wire_lr=%d display_fb=%d display_lr=%d state=%d"
            % (mask & 1, (mask >> 1) & 1, (mask >> 2) & 1, (mask >> 3) & 1,
               ((mask >> 2) & 1) - (mask & 1), ((mask >> 1) & 1) - ((mask >> 3) & 1),
               mountTurnKey(mask), ground["heading"],
               wireForwardBack, wireLeftRight,
               displayFb * 1000, displayLr * 1000, state))
    if moving:
        if not wasMoving:
            ground["lastAdvanceAt"] = flow.now
            ground["nextDeadline"] = flow.now + wire.GROUND_STEP_MS
            flow.later("ground-step", wire.GROUND_STEP_MS)
    else:
        ground["lastAdvanceAt"] = flow.now
        ground["nextDeadline"] = None
        flow.cancel("ground-step")
    return True
