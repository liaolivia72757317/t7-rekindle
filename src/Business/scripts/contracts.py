"""Wire subsets from Capture-M3LoopbackConnection.ps1, not a game simulation.

Sources: reactive heartbeat/match, CAMP, UNIFY_TIME, Add-M3RoundInfo and
Add-M3RoundStateInfo. Reuse canonical Python encoders wherever available.
"""
import json
import os
import random
import struct

from .codec import login_flow, room_flow, vision_flow

BASELINE_ID = "vm-human-20260913"

# --- 宿主注入的「客户端权威移动」开关（对齐 t7-rekindle 上游） -------------------
# 原生启动器会在内存客户端 overlay 装好后把它写进 Python context：
#   Runtime/core/Common.h           bool runtimeMovement = true;
#   Runtime/server/PythonHost.cpp   put(dict, "runtimeMovement", PyBool_FromLong(...))
# 自建的 T7.Server.exe 不注入 ⇒ 取默认 False ⇒ 全链行为与改动前逐位相同。
# ⚠️ 上游还把这个开关绑到固定武将 RUNTIME_HERO_IDS(110001) 与 VISION 的 gravity
#    偏移上（见其 actorVision / battleHeroes）——那是它单人离线固定场景用的，
#    与本项目 87 张名册不兼容。此处**只取开关本身**，不搬武将绑定。
CLIENT_RUNTIME_MOVEMENT = False
RUNTIME_MOVEMENT_MODE = "client-runtime-offline-v1"

# --- 契约 / 实机 双档位（2026-10-07） -----------------------------------------
# 本 fork 在「客户端权威移动」模式下对上游合成骨架做了**有意的实机扩展**：
#   发镜像帧 / 控制解锁帧、用「当前武将」作唯一出战、actorState/roundState 改用
#   epoch 时间戳、activate 的 active 写死 1 —— 这些正是 WASD / 冲刺 / 控制解锁能在
#   实机跑起来的修复（见 controls.py / scene.py 对应注释）。
# 上游 tests/python 那套 vendored 参考套件按「合成骨架契约」写断言，与本 fork 的
# 实机扩展冲突。用这个档位把两套口径分开：
#   CONTRACT_MODE = False（默认，= 实机 / 本 fork 真实行为）：上面那些扩展全开。
#   CONTRACT_MODE = True （= 上游合成骨架契约）：仅供 tests/python 参考套件对齐上游，
#       证明「没有回归上游契约」。实机部署（t7-rekindle 宿主 / 自建 T7.Server.exe）
#       永不置此档。
# 切换：环境变量 T7_CONTRACT_MODE=1，或 contracts.set_contract_mode(True)。
# ⚠️ 默认档 = 实机，所以改这个开关**不会**动到任何实机行为；只有测试套件会翻到契约档。
_CONTRACT_MODE = os.environ.get("T7_CONTRACT_MODE", "").strip() not in (
    "", "0", "false", "False", "no", "NO")


def set_contract_mode(on):
    global _CONTRACT_MODE
    _CONTRACT_MODE = bool(on)


def contract_mode():
    return _CONTRACT_MODE

# --- 关卡（地图）选择（2026-09-19 新增；**默认与改动前逐位相同**） ---------------
#
# ⚠️ pattern_id 与 level_id 是**两个不同的 ID 空间**，别混：
#     pattern_id = 客户端大厅卡片带的（ROOM_CREATE_REQ.resource_id）
#     level_id   = 客户端 game_level 表里的合法关卡号，也是服务端要回显的
#     出处与完整表见工作区《T7-换图计划-樊城vs宛城.md》§1.1。
#
# 默认 10036 = **洛阳死斗** —— 这就是 T7 现在实际进的图
# （app.py 原来硬校验 resource_id == 1028，而 1028 是洛阳死斗的 pattern）。
#
# 切图不改代码：设环境变量 ``T7_LEVEL=<level_id>``，只对新会话生效。
# 场景目录与出生点都跟着 level 走，**不用另外配**。
LEVEL_ENV = "T7_LEVEL"
LEVEL_ID_DEFAULT = 10036

# level_id -> 场景目录（server/data/scene/<场景>/aairwall.xml，空气墙用）
SCENE_BY_LEVEL = {
    10036: "lysd",       # 洛阳死斗（实战训练模式）
    10085: "pve_gc",     # 宛城之战教学
    10002: "tszz",       # 樊城（攻城模式）
    # ⭐ 2026-09-19：洛阳死斗（**团队模式**）与 10036 是**同一张图**的两个模式
    #    （客户端 game_level_table.csv 两行 name 都叫「洛阳死斗」），沿用 lysd。
    #    ⚠️ 未实测：若实机发现该图空气墙不对，先试改成 ""（不碰撞）。
    10005: "lysd",
    # ⭐ 2026-09-20：**玉门关外**（模式 12 会战模式）。场景目录 `hz_map_b` 是实测的：
    #    `server/data/scene/hz_map_b/aairwall.xml` 早就存在（62 张图批量抽取时就有），
    #    且 `game_level_table.csv` 第 71 行 10062 的 `main_logic = map_hz_b_levellogic`
    #    —— 与 `map_tszz_levellogic → tszz`、`map_gc_map_c_levellogic → gc_map_c`
    #    同构（去掉 `map_` 前缀与 `_levellogic` 后缀）。
    10062: "hz_map_b",
    # ⭐⭐ 2026-09-30 夜：**白马要塞教学**（新手基础教学）。场景目录 **`pve_gc`**
    #    —— 与宛城之战（10085）**同一个场景目录**，共用空气墙/碰撞/高度场。
    #    权威出处 = 客户端 `game_level_table.csv` 第 0 行：
    #      `0,2009,白马要塞教学,白马要塞教学,1,教学模式,子模式_普通模式,`
    #      ` match_jianglingdao3.dds,match_jianglingdao2.dds,,,,`
    #      ` map_training_gc_levellogic,,rock,Play_Music_TongGuan,,`
    #    ⇒ level_id **2009** / 模式 1 教学模式 / `main_logic=map_training_gc_levellogic`。
    #    同文件第 36 行是**同一个 level 的另一种模式**：
    #      `36,2009,白马要塞,攻城实战训练,10,实战训练模式,…,map_pve_gc_levellogic,…`
    #    ⚠️ 两条 `main_logic` 都带 **`_gc_`**，与 `map_tszz_levellogic → tszz`、
    #       `map_yj_levellogic → yjc_low` 同构 ⇒ 去掉 `map_` / `_levellogic` 得 **`gc`**。
    #       map.vfs 里 `gc` 系列只有 `pve_gc` 一族是 PVE 场景（其余 `gc_map_*` 是
    #       国战/攻城变体、`war_gc_map_*` 是战区）⇒ **`pve_gc`** 是唯一命中。
    #    ⚠️ 别被 row 0 的 `match_jianglingdao*.dds` 误导 —— 那是**图标复用**
    #       （江陵道），不是地图指向；判场景只认 `main_logic` 与资源目录。
    #    实机确认方式：点大厅「白马要塞教学」卡(11) → 日志应打
    #       `level-auto-switch kind=switch card=11 -> level=2009 scene=pve_gc`
    2009: "pve_gc",
    # ⭐ 2026-09-27：**江陵城**（攻城模式）。权威出处 = 客户端关卡表（两处互证）：
    #    * `pattern_level_map.csv` 第 86 行：`20002,2,江陵城,3,攻城模式,10020,江陵城`
    #    * `game_level_table.csv`  第 86 行：`84,10020,江陵城,江陵城,3,攻城模式,子模式_普通模式,
    #        match_jianglingcheng3.dds,match_jianglingcheng2.dds,...,map_yj_levellogic,...`
    #    ⇒ level_id **10020** / 卡片 **20002** / 攻城模式 / main_logic=`map_yj_levellogic`。
    #    （另一张 `30001,2,江陵城,3,攻城模式,30001,国战江陵城` 是**国战**入口，先不登记。）
    # ⚠️ 场景目录名 **`yjc_low`** 的三条独立证据：
    #    ① vfs 名字池里 `yjc` 一族 43 条全部指向江陵城 —— `../data/scene/ibl/yjc/`、
    #       `mo_yjc_wall_destroyed_lv1.btree`（城墙摧毁）、`yjc_door`（城门）、
    #       `yjc_house`、`../data/scene/texture/terrian/yjc/yjc_terrain_*.dds`；
    #    ② 加载图 `match_jianglingcheng1/2/3.dds` 与关卡表那一行**逐字对上**
    #       （注意：`loading_jianglingdao.dds` 是**江陵道**，不是江陵城，别混）；
    #    ③ dump 的 `015866~015879` 是 map.vfs 里**地址连续的一块**
    #       （0x1fb3712c → 0x1fb3f59d），其中 `015870` 在 `_name_map2.tsv` 里明写
    #       `../data/scene/map/yjc_low/preintrc_259_199_5.xml`
    #       ⇒ 同块的 `015868`(出生区) / `015873`(CC 表) 同属 `yjc_low`。
    #    ⚠️ 仍属**推断**的一环：`main_logic` 是 `map_yj_levellogic`（`yj`），与目录名
    #       `yjc_low`（`yjc`）不完全同名 —— 未拿到游戏内显示名直接证实。实机若发现
    #       载入的不是江陵城，第一件事就是换这个目录名（`yjc` / `yj` / `war_yjc`）。
    10020: "yjc_low",
    # ⭐⭐⭐ 2026-10-01（第十四轮，**纠正上一轮的重大错误**）：
    #    **「职业训练 6 + 铁骑训练场 5」的正确 level / pattern / 场景**。
    #
    #    ── 上一轮错在哪 ──────────────────────────────────────────────
    #    上一轮我拿「关卡显示名」去猜 level（把 `XL_Map_B` 当成 level 10016、
    #    把 `xlc_map_c_cavalry` 当成 10072），**11 条里错了 10 条**。
    #    实机日志（会话 44700-930691595）拍到客户端 room-create 发的是
    #    **`client=23` 和 `client=14`** —— 这两个数直接把我打醒了：
    #    客户端发的是**独立卡片号**，不是 13xx 整卡；而且 `pattern_level_map.csv`
    #    一直就在本地启动器 VFS 解包备份目录（local_lobby_server/_backup_20260905_vfs_extract/）里。
    #
    #    ── 现在这份的出处（三重交叉，全部对齐）────────────────────────
    #    ① `pattern_level_map.csv`（权威卡片表，138 行）→ pattern/level/模式
    #    ② `_t_22304.bin`（关卡表，**114 行**）→ level/场景目录
    #       ⚠️ 关卡表的正确布局（上一轮记的 HDR 108 / 行 292 **是错的**）：
    #          `i32 level_id` 紧接 `../Data/Scene/Map/<场景>/...` ASCII 路径，
    #          路径**成对出现**（同一行有 2 份）。用正则 `\x00{1,8}(.{4})\.\./Data/Scene/Map/`
    #          扫出 **114 条**，与哈希头 `u32@12 = 114` 吻合。
    #       ⚠️ `_t_22304.bin`(33428B/292/114) 与 `s_res_instance_cfg_cli.bin`
    #          (410729B/2997/137) **是两个文件**，上一轮把后者当关卡表用了。
    #    ③ 实测日志 `client=23` → 剑盾教学(10023) / `client=14` → 弓箭手教学(10015)
    #       ⇒ **与 ①② 完全吻合**，这条链闭死。
    #
    #    ── 职业训练 6 个（客户端「职业训练」页，截图左→右 1..6）────────
    #      卡片 23 先锋·剑盾 → level 10023  XL_Map_A          剑盾教学
    #      卡片 14 神射·弓箭 → level 10015  XSC1              弓箭手教学
    #      卡片 24 奇行·医师 → level 10038  XL_Map_D_doctor   医师教学
    #      卡片 15 骠骑·枪骑 → level 10016  XL_Map_B          骑兵教学
    #      卡片 25 奇行·匠师 → level 10041  XL_Map_C_engineer 匠师教学
    #      卡片 26 奇行·刺客 → level 10079  XL_Map_F_assassin 刺客教学
    #      ⚠️ 模式全是 **1 教学模式**（`mode_a=1`，建房直进）。
    #
    #    ── 铁骑训练场 5 个（模式 **9 训练模式**）────────────────────────
    #      卡片 6006 训练场_挥砍   → level 10069  xlc_map_a_camp
    #      卡片 6007 训练场_骑射   → level 10071  xlc_map_b_archer
    #      卡片 6008 训练场_刀骑   → level 10072  xlc_map_c_cavalry
    #      卡片 6009 训练场_招架   → level 10073  xlc_map_a_camp
    #      卡片 6010 训练场_弓箭手 → level 10074  xlc_map_a_camp
    #      ⚠️ 模式 9 训练模式同样是**建房直进**（按 `mode_a` 列 = 1，与教学同类）。
    #
    #    ⚠️⚠️ 与上一轮相比，`level_id` 换了 **10 个**（只有刺客 10079 是对的）。
    #       上一轮那 10 条已**全部删除**，别照旧记录去用。
    #    ⚠️ 关卡表里还有两条容易混的**邻号**，别拿错：
    #        10016 = XL_Map_B（**骑兵教学**，不是弓箭手）
    #        10024 = JD_Map_A（步兵，*不是*本次目标）
    #        10039 = PVE_JS_Map_B（*不是*医师；医师是 10038）
    #        10042 = TestMap_LD_3（*不是*匠师；匠师是 10041）
    #        10070 = gc_map_i_yangpingguan（*不是*训练场挥砍；挥砍是 10069）
    #        10076 = gc_map_j_jiange（*不是*训练场弓箭手；弓箭手是 10074）
    10023: "xl_map_a",           # 职业训练 1 先锋·剑盾（卡片 23）
    10015: "xsc1",               # 职业训练 2 神射·弓箭（卡片 14）
    10038: "xl_map_d_doctor",    # 职业训练 3 奇行·医师（卡片 24）
    10016: "xl_map_b",           # 职业训练 4 骠骑·枪骑（卡片 15）
    10041: "xl_map_c_engineer",  # 职业训练 5 奇行·匠师（卡片 25）
    10079: "xl_map_f_assassin",  # 职业训练 6 奇行·刺客（卡片 26）
    10069: "xlc_map_a_camp",     # 训练场_挥砍（卡片 6006）
    10071: "xlc_map_b_archer",   # 训练场_骑射（卡片 6007）
    10072: "xlc_map_c_cavalry",  # 训练场_刀骑（卡片 6008）
    10073: "xlc_map_a_camp",     # 训练场_招架（卡片 6009）
    10074: "xlc_map_a_camp",     # 训练场_弓箭手（卡片 6010）
    # ⭐⭐ 2026-10-04：**栖霞山**（人机 / 实战训练模式）= level **18114**，
    #    场景目录 **`td_map_d_temple`**。三份客户端自己的表互证：
    #    ① `pattern_level_map.csv`（权威卡片表）：`18114,2,栖霞山,10,实战训练模式,
    #       18114,栖霞山高级训练` 与 `18124,…,18114,栖霞山实战训练` ⇒ 两张卡、同一 level；
    #    ② `game_level_table.csv` 第 77/78 行：level 18114 = 栖霞山，
    #       `main_logic=map_com_medium_levellogic`、`terrain=rock`、
    #       **`music=Play_Music_SD`**、胜负条件「率先击杀 80 人」；
    #    ③ `_t_22304.bin`（关卡表）level 18114 → `../Data/Scene/Map/TD_MAP_D_Temple/`。
    #    ⚠️ 关卡表的**名字列不可信**（同表把 10036 写成「程序测试」、10062 写成
    #       「国战攻城战3」、18114 写成「实战-夷陵古道」），**只认它的场景路径列**——
    #       该列已三处对上跑通的图：10036→LYSD、10062→HZ_Map_B、10020→YJC_low。
    #    ⚠️ 别用 10028：关卡表把 10028 写成「栖霞山」→ TX_Map_A，但 `game_level_table.csv`
    #       里 10028 = **淮南**（突袭模式，`map_tx_levellogic`），且 138 行卡片表
    #       **没有任何卡指向 10028**。
    #    团队模式的栖霞山是另一个 level **10027**（`map_gccy_med_levellogic`，
    #    music `Play_Music_TD_02`），场景同一目录。
    18114: "td_map_d_temple",
    10027: "td_map_d_temple",
}

# level_id -> (主出生点, 副点/敌兵位)
# 宛城、樊城两组来自客户端 dactorspawnarea.dat（GB2312 明文 XML，实测提取）。
# ⚠️ Z 值差异很大：洛阳 0.218 / 宛城 20.46 / 樊城 43.25。改图不改 Z 会出生在地下。
#
# ⭐⭐⭐ 2026-09-30（第二轮）：**Z 改为 PAMH 真实地面值**。
#
# 上一轮把主点回退到客户端原值 `(411.485, 476.784, 20.4581)` 后，实机反馈变成
# 「**城外地下卡着，跑几步悬空**」—— 问题从「出生点坐标」升级为「**整片地形高度**」。
#
# 根因（本轮定案）：旧 `heightfield.json` **只从 amodellist（建筑）推高度**，城外
# 没有任何建筑资产 ⇒ 整片可玩区被填成常数 `groundBase=20.4581` 的**水平板**。
# 而客户端真实地形（PAMH `aheightmap.dat`，513×513、每格 2 m）里：
#     城内台地 ≈ 19~22 m，城外低洼地 5~15 m，更远处 -28 m —— 落差 30+ m。
# ⇒ 站在城墙上/走出城门，服务端把 Z 钉在 20.46，客户端地形却低十几米 ⇒ 悬空/卡地下。
#
# 已修：`t7_heightfield_build.py` 接入 PAMH 双线性采样，生成逐格 `ground` 数组
# （`heightfield.groundZ()` 在 height 为 null 时用它）。**校验**：用同一套 PAMH
# 采样算出 tszz spawn(503.179,581.889)=43.248（客户端 43.2482）、lysd=0.219
# （客户端 0.229）——**毫米级吻合**，证明 PAMH 就是客户端角色的真实站立高度。
#
# ⚠️ 同口径核算宛城三个出生区：**客户端 z 本身有异常值**。
#      | 出生区 | 客户端 z | PAMH 地面 | 差 |
#      |--------|---------|-----------|-----|
#      | 主 is_main=1 init_state=2 | 20.4581 | **19.105** | +1.353 |
#      | 副 init_state=1           | 19.3473 | **19.348** | -0.001 ✅ |
#      | 区2                        | 32.1101 | 19.311     | +12.799 |
#    副点精确到毫米 ⇒ PAMH 权威；主区偏高 1.35 m、区2 偏高 12.8 m 都是**客户端
#    数据里的异常值**（主区 init_state=2 可能表示站在台基上）。
#    ⇒ 服务端出生点 Z 一律改用 **PAMH 地面值**，与客户端渲染的地形严格一致。
SPAWN_BY_LEVEL = {
    10036: ((272.451, 157.634, 0.218), (271.046, 156.857, 0.218)),
    # 宛城：XY 用客户端 dactorspawnarea.dat 原值；Z 用 PAMH 真实地面
    #   （主 19.105 / 副 19.348；客户端原 z 20.4581 / 19.3473 见上表）
    10085: ((411.485, 476.784, 19.105), (502.861, 467.661, 19.348)),
    # ⭐⭐ 2026-09-30 夜：**白马要塞教学（2009）** —— 场景与宛城**同目录**（`pve_gc`），
    #    出生区表也用同一份 `data/scene/pve_gc/dactorspawnarea.dat`（三个区见上）。
    #    ⇒ 直接沿用宛城那一对点（主 `is_main=1` + 副），**Z 同样是 PAMH 真实地面值**。
    #    ⚠️ 教学关卡 `mode_a=1`（走「建房直进」），客户端**不走匹配链路**，
    #       所以这两个点主要用于「服务端自己那份 POSITION」与兜底下发。
    #    ⚠️ 未单独解析教学专用出生区：`dactorspawnarea.dat` 里只有 3 个区、
    #       没有「is_tutorial」之类的标记位；若实机发现教学出生点不对，
    #       第一件事是重新 dump 这份表看有没有教学专用区（另见
    #       `dactorspawngroup.dat` / `dspawninfo.dat`）。
    2009: ((411.485, 476.784, 19.105), (502.861, 467.661, 19.348)),
    # 樊城我方：用主区 3935066568 的 pos `503.179,581.889,43.2482`（**实机实证**：
    # 步兵轮 9356-109347746 用它移动了 475 帧、位置正常漂移）。
    # ⚠️ 2026-09-22 曾换成出生组 3494155748 里的 `494.305,584.875,42.8802`（理由：区 pos
    #    实测离复活组 1805636849 只有 2.1 m、离出生组 9.4 m，开局像站在复活点上；新点
    #    净空 63.07 m、Havok 碰撞格 (83,343) 是空的、groundZ 43.62）。**已退回**：
    #    实机轮 46668-140498449 证明骑兵**根本不采纳服务端下发的出生点**（客户端把骑兵
    #    角色放在世界原点，上一轮 9356-110117498 服务端跟到 (-1.8,-0.7)），换点对骑兵
    #    零收益，只是多一个变量。⇒ 骑兵的落点不在出生表里，在**坐骑实体**上：
    #    原版坐骑是独立 VISION 对象（object_type=2），客户端按 actor.mount_rid==mount.rid
    #    挂骑手；我们此前只给 mount_tid、rid/inst_id/位置全 0。见 MOUNT_VISION_RID 与
    #    server/scripts/README.md「骑兵不采纳出生点」。
    # ⚠️ 2026-09-22 晚补：这一对**不是**「我方/敌方」，而是两个 ``is_main=1`` 主营地
    #    （[0] 是 ``init_state=2`` 那侧、[1] 是 ``init_state=1`` 那侧）。恒发 [0] 会把
    #    camp=1 的人放到对面营地、离要塞 119 米 ⇒「一进来在图外」。用哪个由 camp 决定，
    #    见 ``SPAWN_INDEX_BY_CAMP`` / ``applyCampSpawn``。
    10002: ((503.179, 581.889, 43.2482), (622.006, 572.036, 44.4526)),
    # ⭐ 2026-09-19：洛阳死斗（团队模式）暂用 10036 的出生点 —— 同一张图（lysd），
    #    但**两个模式的出生区可能不同**，未实测。
    #    ⚠️ 若实机「出生在地下 / 出生在墙里」，就是这条要单独提
    #    （来源：客户端 ../data/scene/map/lysd/dactorspawnarea.dat）。
    #    ⭐ 2026-09-22 晚查实：这个点**不是任何区的 pos**，是点组 ``3379637679``
    #    （``005773_SH_RES_ACTOR_SPAWN_GROUP_TAB``）的第 1 个散点，该组
    #    ``is_infantry=1 / is_cavalry=0`` ⇒ 纯步兵点。同表**有**骑兵专用 born 组
    #    （``1492739893`` 6 点、主区 ``2592267309`` 名下；``3796015618`` 4 点），
    #    骑兵要换点就从这两组里挑，别再往散点组里找。
    10005: ((272.451, 157.634, 0.218), (271.046, 156.857, 0.218)),
    # 玉门关外：取自客户端 `../data/scene/map/hz_map_b/dactorspawnarea.dat`
    #   = 解包文件 `D:\dfjq_out\map\004557_SH_RES_ACTOR_SPAWN_AREA_TAB_a2de226.xml`，
    #   两个 `is_main=1` 的区：`init_state=2` → 主点，`init_state=1` → 副点。
    # 距离一律按 `airwall.AirWall.footprint` **折线段**最小距离算（拿 `<Entity Position>`
    #   中心点算会偏小/偏大各 1~1.4 m，别混用）。
    # 主点 = 己方区 pos，实测离质心 2.62 m、距墙 11.45 m、groundZ 6.895 vs 配置 6.861 —— 直接可用。
    # ⚠️ 副点**没有**用该区自己的 pos `(554.927, 624.18, 12.5815)`：实测它离本区 48 个散点的
    #    质心 20.72 m、离最近散点 10.78 m（Y=624 已在散布范围 593.8~614.3 之外），
    #    且距墙只有 **1.68 m**（宛城当年「卡地下」是 4.69 m）⇒ 这个区 pos 不可信。
    #    改用同区 `is_born=1` 散点组（004558，48 点）里**净空最大**的点
    #    `(564.818, 609.795, 12.124)`：距墙 12.20 m（该组上限就 12.2 m，整组都贴墙），
    #    groundZ 12.089 vs 配置 12.124 差 0.035 m。
    #    （2026-09-21~22 曾填 `(551.369,593.76,11.3787)`，段距离只有 **3.90 m**；当时注释写的
    #     「距墙 32.73 m」是**拿空气墙 `<Entity Position>` 中心点**算的（实测 32.72），
    #     中心离墙脚可以很远，不能当净空判据 —— 段距离才是。同理区 pos 的「2.68 m」也是中心距，
    #     段距离 1.68 m。判据：< 5 m 会被挤，≥ 10 m 才稳。
    #     ⚠️ README/手册里「主点 10.06 / 洛阳 12.57 / 宛城 14.89 / 樊城 58.87」那一组数
    #     全是中心距口径，和上面的段距口径不能混着比。）
    # ⚠️ 2026-09-21 之前的 `(313,299,27)/(514,479,42)` 是从 `preintrc_313_299_17.dds`
    #    **文件名**反推的假值（平面差 ~135、Z 高 20 米），实机表现为出生点不对。
    #    ⇒ `preintrc_X_Y_Z` 不带坐标含义，别再拿它反推任何东西。
    # ⚠️ 骑/步**不能**指望这张表分开：004558 两组都带 `is_cavalry=1 is_infantry=1`
    #    （樊城红方才只有纯步兵 born 组），所以骑兵没有专属点可填。
    10062: ((429.429, 360.939, 6.86067), (564.818, 609.795, 12.124)),
    # ⭐ 2026-09-27：**江陵城**。取自客户端 `015868_SH_RES_ACTOR_SPAWN_AREA_TAB_1fb3793e.xml`
    #    （6 个出生区，GB2312 明文 XML，实测提取）：
    #      #0 id=2886657221  pos 511.041,354.258,12.7277  is_main=1 init_state=2
    #      #1 id=3601318610  pos 450.555,354.258,12.7277  is_main=1 init_state=2
    #      #2 id=3935066569  pos 476.997,426.012,31.708   is_main=0 init_state=1  display_name=门
    #      #3 id=3935066571  pos 511.406,488.987,19.8381  is_main=0 init_state=1  display_name=一
    #      #4 id=3935066572  pos 449.430,489.958,19.8544  is_main=0 init_state=1  display_name=二
    #      #5 id=3935066573  pos 476.160,588.578,25.1002  is_main=1 init_state=1
    #    ⇒ 攻城战结构一目了然：#2/#3/#4 是 `is_can_occupy=1` 的**据点**（门 → 一 → 二，
    #      由外向内推进），#0/#1 是**攻方主营**、#5 是**守方主营**。
    #    ⭐ **城外 / 城内怎么判**（坐标法，不猜语义）：本图云梯 pos 的 Y ≈ 402~404、
    #      正门 Y=419.6、城墙 Y=425.8、旗点 Y≈489、侧门 Y=546、大道旗 Y=562.9。
    #      云梯是**贴城墙外侧**摆的 ⇒ **Y 小 = 城外**。故攻方(城外) = `init_state=2`
    #      的两区（Y=354.258），守方(城内) = `init_state=1` 的 #5（Y=588.578）。
    #    [0] = `init_state=2` 侧（攻方/城外）—— 两个 is_main=1 区里**净空更大**的那个；
    #    [1] = `init_state=1` 侧（守方/城内）唯一主区。
    #    ⚠️ 净空判据 = `aairwall.xml` 的 `AirWall.footprint` **折线段最小距离**
    #      （不是 `<Entity Position>` 中心距；< 5 m 会被挤、≥ 10 m 才稳）。
    #      `data/scene/yjc_low/aairwall.xml` 34 段墙，实测：
    #        #0 (511.041,354.258,12.7277) 段距 **71.40 m**  ← 选它，余量极足
    #        #1 (450.555,354.258,12.7277) 段距  25.74 m
    #        #5 (476.160,588.578,25.1002) 段距  40.73 m  ← 守方
    #      （判据与樊城/玉门关外同一套，见上面 10062 那段注释。）
    10020: ((511.041, 354.258, 12.7277), (476.160, 588.578, 25.1002)),
    # ⭐⭐⭐ 2026-10-01（第十六轮）：**职业训练 6 + 铁骑训练场 5** 的**真出生点**。
    #
    #    ⚠️ 第十四轮填的是「`aairwall.xml` 包围盒中心 + 空气墙最低 z」的**兜底值**，
    #       实机「人物有一半在地下 / 一进来位置就不对」，**全部作废**（实测 XY 偏差
    #       6.7 ~ 143.2 m，见下表）。根因：兜底点只保证「在图内」，不保证「在出生区」，
    #       有的点落在路网/台阶/半坡上 ⇒ 客户端把角色塞进地形。
    #
    #    ✅ 本轮改为**客户端自己的出生表原值**，来源（map.vfs，按名直取）：
    #       `../data/scene/map/<场景>/dactorspawngroup.dat`
    #         → `<SH_RES_ACTOR_SPAWN_GROUP>` 里 `is_born=1` 的组，取其 `<pos>`。
    #       **交叉验证**：同图 `dspawninfo.dat` 里的 `camp_red.nif` 实体坐标
    #         与 born 组 pos **逐位/厘米级吻合**（8 张图全对，见下）。
    #       ⇒ 这两个文件是**客户端权威数据**，服务端下发同一组数即可，无需自己算。
    #
    #    抽取脚本：`hkx_decode/pick_xl_spawn.py`（可重复跑，会重印全表）。
    #
    #    ── 兜底值 vs 真值（第十六轮实测）────────────────────────────────
    #    level  场景                旧兜底(作废)                  真出生点                    偏差
    #    10023  xl_map_a            (465.419, 528.680, 23.778)   (454.7660, 503.9150, 23.6228)  26.96 m
    #    10015  xsc1                (447.201, 521.569, 23.950)   (452.2720, 517.2590, 24.1256)   6.66 m
    #    10038  xl_map_d_doctor     (493.900, 751.879, 19.860)   (502.5070, 748.1270, 19.9141)   9.39 m
    #    10016  xl_map_b            (601.582, 615.981, 16.036)   (525.5550, 737.3210, 15.9372) 143.19 m ★最离谱
    #    10041  xl_map_c_engineer   (492.993, 751.879, 20.350)   (476.6350, 724.1560, 19.8601)  32.19 m
    #    10079  xl_map_f_assassin   (491.079, 492.178, 23.990)   (425.8440, 459.3940, 24.8405)  73.01 m
    #    10069  xlc_map_a_camp      (480.484, 469.201, 87.287)   (475.0510, 437.2520, 87.4901)  32.41 m
    #    10071  xlc_map_b_archer    (478.673, 300.209, 34.717)   (525.8280, 297.1470, 34.7316)  47.25 m
    #    10072  xlc_map_c_cavalry   (513.760, 750.562, 21.128)   (548.3230, 669.0250, 34.6557)  88.56 m
    #    10073/10074 = 10069 同图（`xlc_map_a_camp`）
    #
    #    ⚠️ 这几张图的出生组**只有红方一个 `is_born=1` 组**（`is_red=1 is_blue=0`），
    #       所以 [0]/[1] 只能同值 —— 与旧兜底的处理相同，**不新增 `SPAWN_INDEX_BY_CAMP`**。
    #       例外：`xlc_map_c_cavalry` 另有一组 `camp_blue.nif`，但它不是 born 组；
    #       `xl_map_h_oilbomb`（未接线）才有真正的双侧 born 组。
    #    ⚠️ Z 一律**用出生表原值**，不再自己算 —— 客户端会自行贴地，服务端这一份跟着
    #       客户端走才不会出现「服务端认为你在 A、客户端在 B」的错位。
    10023: ((454.7660, 503.9150, 23.6228), (454.7660, 503.9150, 23.6228)),  # 先锋·剑盾（卡片 23）xl_map_a
    10015: ((452.2720, 517.2590, 24.1256), (452.2720, 517.2590, 24.1256)),  # 神射·弓箭（卡片 14）xsc1
    10038: ((502.5070, 748.1270, 19.9141), (502.5070, 748.1270, 19.9141)),  # 奇行·医师（卡片 24）xl_map_d_doctor
    10016: ((525.5550, 737.3210, 15.9372), (525.5550, 737.3210, 15.9372)),  # 骠骑·枪骑（卡片 15）xl_map_b
    10041: ((476.6350, 724.1560, 19.8601), (476.6350, 724.1560, 19.8601)),  # 奇行·匠师（卡片 25）xl_map_c_engineer
    10079: ((425.8440, 459.3940, 24.8405), (425.8440, 459.3940, 24.8405)),  # 奇行·刺客（卡片 26）xl_map_f_assassin
    10069: ((475.0510, 437.2520, 87.4901), (475.0510, 437.2520, 87.4901)),  # 训练场_挥砍（6006）xlc_map_a_camp
    10071: ((525.8280, 297.1470, 34.7316), (525.8280, 297.1470, 34.7316)),  # 训练场_骑射（6007）xlc_map_b_archer
    10072: ((548.3230, 669.0250, 34.6557), (548.3230, 669.0250, 34.6557)),  # 训练场_刀骑（6008）xlc_map_c_cavalry
    10073: ((475.0510, 437.2520, 87.4901), (475.0510, 437.2520, 87.4901)),  # 训练场_招架（6009，与挥砍同图）
    10074: ((475.0510, 437.2520, 87.4901), (475.0510, 437.2520, 87.4901)),  # 训练场_弓箭手（6010，同图）
    # ⭐ 2026-10-04 **栖霞山（人机 level 18114）**。出生点不在 `dactorspawnarea`
    #    （`D:\dfjq_out\map\009841_SH_RES_ACTOR_SPAWN_AREA_TAB_14131240.xml` 是**空表**），
    #    而在**出生组表** `009842_SH_RES_ACTOR_SPAWN_GROUP_TAB_14131292.xml`
    #    （6 组 67 点，`is_born=1` 的两组）：
    #      蓝方 born 组 2059393081 = 14 点，y≈592.7~598.5、x≈463~532、z 48.5855~48.6498
    #      红方 born 组 2599954444 = 18 点，y≈485.8~489.5、x≈463~531、z 48.4597~48.6243
    #    各取组内第一点。⚠️ **Z 用 PAMH 地面值**（沿用 2026-09-30 定下的口径）：
    #    本图 PAMH 在 (r=298,c=247)=48.486、(r=245,c=243)=48.499，客户端原 z 48.6498 /
    #    48.6243 各高 0.16 / 0.13 米 ⇒ 差在半个格内，取 PAMH 值与渲染地形一致。
    #    复活点那两组 z≈54.06/54.16 比地面高 5.5 米（在台子/建筑顶，PAMH 采不到），
    #    本轮不用作出生点。
    18114: ((494.7460, 596.4570, 48.486), (486.5750, 489.5440, 48.499)),
}

# level_id -> 大厅卡片的 pattern_id（只用于日志提示，服务端不靠它校验）
# ⚠️ 2026-09-19 定论：**客户端认的是 pattern，不是 level_id**。
#    洛阳能进是因为服务端恒发 1028 = PATTERN_BY_LEVEL[10036]（洛阳 level_id
#    是 10036，不是 1028）。所以切图后也必须发 pattern：宛城 29 / 樊城 20001。
# ⭐ 玉门关外（10062）的**主卡片是 10002**（「玉门关外」/ 模式 12 会战模式），
#    另一张 50002 是「州间资源战玉门关外」（同 level，见 PATTERNS_BY_LEVEL）。
#    出处：客户端 `pattern_level_map.csv` 第 73 / 121 行。
PATTERN_BY_LEVEL = {10036: 1028, 10085: 29, 10002: 20001, 10005: 1003, 10062: 10002,
                    # ⭐ 2026-09-27 江陵城（攻城模式），`pattern_level_map.csv` 第 86 行。
                    10020: 20002,
                    # ⭐⭐ 2026-09-30 夜：白马要塞**教学**卡 = **11**。
                    #    出处：客户端 `pattern_level_map.csv` 第 1 行
                    #      `11,1,白马要塞教学,1,教学模式,2009,白马要塞教学`
                    #    ⇒ pattern 11 / mode_a 1 / level 2009。
                    #    ⚠️ 同一 level 2009 还有**另一张卡 2009**（`2009,1,白马要塞,10,
                    #       实战训练模式,2009,攻城实战训练`）—— 是「攻城实战训练」入口，
                    #       不是教学。两张卡都指向 level 2009，见 PATTERNS_BY_LEVEL。
                    #    默认发**主卡**由场景决定：本次要的是**基础教学** ⇒ 发 11。
                    2009: 11,
                    # ⭐⭐⭐ 2026-10-01（第十四轮）：**职业训练 6 + 铁骑训练场 5**。
                    #    ⚠️⚠️ 这 11 个 level 的**每张卡都是独立 pattern_id**
                    #       （客户端 `room-create` 直接发卡号；实机日志拍到
                    #        `client=23` / `client=14`）—— **根本不需要钉住表**。
                    #       上一轮搞的 `mode_item_N` 那套是**基于错误前提**
                    #       （我以为它们是 13xx 整卡的子项），现在**已废弃**。
                    #    出处：`pattern_level_map.csv`（138 行，三重交叉验证过）。
                    10023: 23,    # 先锋·剑盾教学
                    10015: 14,    # 神射·弓箭手教学
                    10038: 24,    # 奇行·医师教学
                    10016: 15,    # 骠骑·骑兵教学
                    10041: 25,    # 奇行·匠师教学
                    10079: 26,    # 奇行·刺客教学
                    10069: 6006,  # 训练场_挥砍
                    10071: 6007,  # 训练场_骑射
                    10072: 6008,  # 训练场_刀骑
                    10073: 6009,  # 训练场_招架
                    10074: 6010,  # 训练场_弓箭手
                    # ⭐ 2026-10-04 **栖霞山（人机）**：默认发**高级训练卡 18114**
                    #    （`pattern_level_map.csv`：`18114,2,栖霞山,10,实战训练模式,
                    #     18114,栖霞山高级训练`）。要发「栖霞山实战训练」那张就换 **18124**
                    #    （同 level、同音乐 `Play_Music_SD`，只有 instance 显示名不同）。
                    18114: 18114,
                    }

# level_id -> 该关卡**所有**大厅卡片 pattern（同一张图有多个入口卡片）
# 来源：客户端 pattern_level_map.csv（权威），实测 10036/10002 各有 2~3 张卡。
PATTERNS_BY_LEVEL = {
    10036: (1028, 18111),        # 洛阳死斗实战训练 / 高级训练（mode 10 实战训练）
    10085: (29,),                # 宛城之战教学（只有一张卡）
    # ⭐⭐ 2026-09-30 夜：**白马要塞**（level 2009）有**两张**入口卡：
    #      11   =「白马要塞教学」（mode_a **1** 教学模式）→ **新手基础教学**走这张
    #      2009 =「白马要塞」    （mode_a 1，显示名「攻城实战训练」，`mode_id=10`）
    #    出处：客户端 `pattern_level_map.csv` 第 1 行与第 37 行，两行 level_id 都是 2009。
    #    ⚠️ 两张卡**同 level**，登记在一起不会触发 `probeMatchPattern` 的
    #       「≥2 命中就不切图」（那条看命中了几张**卡**，这里解析出的 level 相同）。
    #    ⚠️ `PATTERN_BY_LEVEL` 默认发 **11**（教学卡）；要发实战训练卡就把那里改成 2009。
    2009: (11, 2009),
    10002: (20001, 60005, 61005),  # 樊城 / 排位赛樊城 ×2
    # ⭐ 2026-09-19 实机定位：**「团队竞技」= 洛阳死斗（团队模式）= pattern 1003**
    #    权威来源 pattern_level_map.csv：
    #      `1003,2,洛阳死斗,2,团队模式,10005,洛阳死斗`
    #    与 10036 的区别：10036 是 **mode 10 实战训练模式**（名字「洛阳死斗实战训练」），
    #    10005 是 **mode 2 团队模式**。两者 instance_name 都叫「洛阳死斗」，
    #    之前只收了 10036，导致点「团队竞技」时 probeMatchPattern() 扫不到卡 →
    #    兜底停在 level.ini 的图 → 客户端卡死（详见 T7-团队模式卡死根因报告.md）。
    #    ⚠️ 只登记 1003 一张。团队竞技的 body 里还带另外 9 张候选图
    #    （1013 江陵道 / 1021 栖霞山 / …），但 probeMatchPattern 要求**唯一命中**，
    #    多登记一张就会变成 ≥2 命中 → 不切图。要支持那些图需另改扫描逻辑。
    10005: (1003,),
    # ⭐ 2026-09-20：**玉门关外**有两张入口卡，**都指向同一个 level 10062 / 场景 hz_map_b**：
    #      10002 =「玉门关外」          （模式 12 会战模式）
    #      50002 =「州间资源战玉门关外」（模式 12 会战模式）
    #    出处：客户端 `pattern_level_map.csv` 第 73 行与第 121 行，两行 level_id 都是 10062。
    #    ⚠️ 两张卡登记在一起**不会**触发 `probeMatchPattern` 的「≥2 命中就不切图」——
    #    那条判据看的是命中了几张**卡**，而这里两张卡解析出的 **level 相同**，
    #    `applyCard()` 走 `switch`/`card` 两条分支的结果一致。
    10062: (10002, 50002),
    # ⭐ 2026-09-27：**江陵城**（攻城模式）。只登记主卡 20002 一张 ——
    #    `pattern_level_map.csv` 第 86 行是它唯一的入口卡。第 98 行的 `30001,国战江陵城`
    #    是**另一个 level（30001）**，不属于本条目（先不登记）。
    #    ⚠️ `probeMatchPattern` 要求**唯一命中**：若某条 match-start 的 body 里
    #    同时出现 20002 和另一张已登记卡，就会 ≥2 命中 → 不切图（保守，安全）。
    #    实测进图后看日志 `match-start-card-candidates=` 即可知道客户端塞了哪些数。
    10020: (20002,),
    # ⭐⭐⭐ 2026-10-01（第十四轮）：**职业训练 6 + 铁骑训练场 5**，每张卡一个 pattern。
    #    实机铁证：会话 `44700-930691595` 的 wire 日志
    #      `room-create-resource-id client=23 echo=11 level=2009 ... match=0`
    #      `room-create-resource-id client=14 echo=11 level=2009 ... match=0`
    #    ⇒ ① 客户端走的是 **room-create（cmd=0x1E）**，**不是** match-start；
    #       ② 它发的是**独立卡号** 23 / 14（不是整卡、不是 13xx）。
    #    ⚠️ 上一轮把切图逻辑挂在 `match-start`（`probeMatchPattern`）上 ⇒
    #       **永远不被调用**，所以点职业训练卡没反应、停在 `id=2009`（白马要塞）。
    #       **room-create 路径才是主入口**（`app.py` 的 `wire.applyCard()`）。
    #    ⚠️ `match=0` 是因为当时 `level=2009` 而客户端发 23/14 ⇒ `applyCard`
    #       `resolveLevel(23)` 返回 None（没登记）⇒ 不切图。登记后即 `match=1`。
    10023: (23,),      # 先锋·剑盾教学
    10015: (14,),      # 神射·弓箭手教学
    10038: (24,),      # 奇行·医师教学
    10016: (15,),      # 骠骑·骑兵教学
    10041: (25,),      # 奇行·匠师教学
    10079: (26,),      # 奇行·刺客教学
    10069: (6006,),    # 训练场_挥砍
    10071: (6007,),    # 训练场_骑射
    10072: (6008,),    # 训练场_刀骑
    10073: (6009,),    # 训练场_招架
    10074: (6010,),    # 训练场_弓箭手
    # ⭐ 2026-10-04 **栖霞山（人机 level 18114）两张卡**：
    #      18114 =「栖霞山高级训练」（`game_level_table.csv` 第 77 行）
    #      18124 =「栖霞山实战训练」（同表第 78 行）
    #    两行 `level_id` 都是 18114、`main_logic` 都是 `map_com_medium_levellogic`、
    #    音乐都是 `Play_Music_SD` ⇒ 登记在一起不会触发 `probeMatchPattern`
    #    的「≥2 命中就不切图」（那条看命中几张**卡**，这里解析出的 level 相同）。
    18114: (18114, 18124),
}

# level_id -> pattern_level_map.csv 的 ``mode_a`` 列（**教学 vs 匹配的分水岭**）
#   1 = 教学模式（宛城）：客户端直接 ROOM_CREATE → ROOM_ENTER，
#       **不走 match-start / match-result / match-enter-instance 那一串**；
#   2 = 匹配模式（洛阳 / 樊城）：客户端先走完整匹配链路再 ROOM_ENTER。
# 2026-09-19 实机日志逐条对上：洛阳 S2C 有 match-start-result-zero →
# match-result-notify → match-enter-instance-notify，宛城**一条都没有**。
# ⚠️ 所以「教学模式不能用匹配的进图方案」—— 用户这句判断是对的。
# ⚠️ 10005 必须填 2：团队竞技走的是 match-start 匹配链路，填 1 会复现卡死。
# ⭐ 玉门关外 10062 同样必须填 **2**：会战模式走 match-start 匹配链路
#    （`pattern_level_map.csv` 两行 mode_a 都是 2）。填 1 会走教学「建房直进」→ 卡死。
MODE_A_BY_LEVEL = {10036: 2, 10085: 1, 10002: 2, 10005: 2, 10062: 2,
                   # ⭐ 2026-09-27 江陵城：`pattern_level_map.csv` 第 86 行的 mode_a 列 = 2
                   #   （匹配模式，走 match-start 链路）。填 1 会走教学「建房直进」→ 卡死。
                   10020: 2,
                   # ⭐⭐ 2026-09-30 夜 白马要塞教学（level 2009）：`pattern_level_map.csv`
                   #   第 1 行 mode_a = **1** ⇒ **教学模式**，走「建房直进」，**不进
                   #   match-start 链路**（与宛城 10085 同类）。这正是本次要的行为。
                   #   ⚠️ 同 level 的实战训练卡 2009 的 mode_a 也是 1（表里第 37 行），
                   #      与「实战训练」名字不符 —— 按表填 1，实机若卡死改 2 试。
                   2009: 1,
                   # ⭐⭐⭐ 2026-10-01（第十四轮）：**职业训练 6 + 铁骑训练场 5**。
                   #    ⚠️ 上一轮那 11 条（10017/10024/10039/10042/10070/10076 …）
                   #       **level 全错**，已删除；下面是校对过的正确 level。
                   #    `pattern_level_map.csv` 的 `mode_a` 列这两组都是 **1**
                   #    ⇒ **建房直进**，不走 match-start / match-result /
                   #      match-enter-instance 那一串。
                   #    ⚠️ 必须显式填 1：`modeA()` 默认是 **2**（匹配），
                   #      靠默认会走匹配链路 → 客户端等 match-result → **卡死**。
                   #    ⚠️ 职业训练那 6 张是「教学模式」，铁骑训练场那 5 张是
                   #      「模式 9 训练模式」，但 `mode_a` 列**都是 1** ⇒ 同样填 1。
                   10023: 1,   # 先锋·剑盾教学
                   10015: 1,   # 神射·弓箭手教学
                   10038: 1,   # 奇行·医师教学
                   10016: 1,   # 骠骑·骑兵教学
                   10041: 1,   # 奇行·匠师教学
                   10079: 1,   # 奇行·刺客教学
                   10069: 1,   # 训练场_挥砍
                   10071: 1,   # 训练场_骑射
                   10072: 1,   # 训练场_刀骑
                   10073: 1,   # 训练场_招架
                   10074: 1,   # 训练场_弓箭手
                   # ⭐ 2026-10-04 **栖霞山（人机 level 18114）**：
                   #   `pattern_level_map.csv` 两行（18114/18124）的 mode_a 列都是 **2**
                   #   ⇒ 走 **match-start 匹配链路**（与洛阳高级训练 10036 同类）。
                   #   ⚠️ 填 1 会走教学「建房直进」→ 客户端等进图 → 卡死。
                   18114: 2,
                   }


def modeA() -> int:
    """当前关卡的 ``mode_a``：1=教学（建房直进），2=匹配。"""
    return MODE_A_BY_LEVEL.get(LEVEL_ID, 2)


def isTutorial() -> bool:
    """当前关卡是不是教学模式（``mode_a == 1``）—— 教学不走匹配链路。"""
    return modeA() == 1

_levelWarned = set()


def _warn(tag, raw):
    if tag in _levelWarned:
        return
    _levelWarned.add(tag)
    print("[contracts] " + tag + " 取值非法或未知，已忽略: " + str(raw)
          + "（可选 " + "/".join(str(k) for k in sorted(SPAWN_BY_LEVEL)) + "）",
          flush=True)


def _parseLevel(raw):
    """把任意输入解释成 level_id；不认识就返回 None。"""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


LEVEL_INI_NAME = "level.ini"
# 想**完全不读 ini**（回到环境变量 + 默认）时设 T7_LEVEL_INI=off；
# 也可以直接把 T7_LEVEL_INI 指向另一个 ini 文件（多开服务端时用得上）。
LEVEL_INI_ENV = "T7_LEVEL_INI"


def _walkUp():
    """从 ``__file__`` 所在目录**逐级向上**，最多 8 级。

    ⚠️ 为什么不能只取 ``__file__`` 的上一级：服务端会把脚本**快照**到
    ``server/data/<会话>/revisions/t7rev_<hash>/`` 再跑，那条路径下
    **没有** ``level.ini`` / ``server.ini``。不向上找的话，重启了也读不到。
    实测快照深度是 5 级（``t7rev_x`` → ``revisions`` → ``<会话>`` → ``data``
    → ``server``），8 级留足余量。
    """
    node = os.path.dirname(os.path.abspath(__file__))
    for _ in range(8):
        yield node
        parent = os.path.dirname(node)
        if parent == node:
            break
        node = parent


def walkUp():
    """公开版 ``_walkUp``，供 heightfield / controls 定位**数据目录**。

    ⚠️ 2026-09-19：``data/scene``（高度场 + 空气墙）**必须**用这个来定位，
    不能只用 ``__file__`` 上两级 —— 脚本是被快照到
    ``server/data/<会话>/revisions/t7rev_<hash>/`` 里跑的，那条路径下没有
    ``data/scene``。实测症状：``heightfield.load()`` 返回 None、
    ``ground-z-none ... loaded=False``，玩家走出出生点就沉地下，空气墙也不生效。
    """
    return _walkUp()


def _iniCandidates():
    """按优先级列出可能写着关卡的 ini。

    ⚠️ 独立文件 ``level.ini`` 排在 ``server.ini`` **前面**是有意的：
    ``server.ini`` 是 ``T7.Server.exe`` 自己读的网络配置，往里塞新段
    有可能把 exe 带崩（它的 ini 解析器不是我们写的）。独立文件零风险。
    """
    nodes = list(_walkUp())
    byName = {}
    for name in (LEVEL_INI_NAME, "server.ini"):
        hits = [os.path.join(n, name) for n in nodes if os.path.isfile(os.path.join(n, name))]
        if hits:
            byName[name] = hits
    for name in (LEVEL_INI_NAME, "server.ini"):
        if name in byName:
            return tuple(byName[name])
    # 一个都没找到也要返回原候选，让调用方给出可读的诊断
    return (os.path.join(nodes[0], LEVEL_INI_NAME),)


def _iniGet(keys):
    """从 ini 的 ``[level]`` 段取**第一个命中**的 key 的原始字符串。

    找不到 / 文件坏了 / 关掉了都返回 None。``keys`` 按优先级排列。

    ⚠️ 为什么要有 ini 这条通道：环境变量必须在**启动服务端之前** set，
    双击 ``T7.Server.exe`` 根本吃不到。2026-09-19 实机连翻两次车——
    用户点了四次宛城，日志全是 ``level=10036``（配置没进进程）。
    ini 是**记事本就能改**的，改完重启服务端即可，不依赖 shell。
    """
    override = os.environ.get(LEVEL_INI_ENV)
    if override is not None:
        text = str(override).strip()
        if text.lower() in ("off", "none", "0", ""):
            return None
        paths = (text,)
    else:
        paths = _iniCandidates()

    import configparser
    for path in paths:
        if not os.path.isfile(path):
            continue
        # ⚠️ 别用 ``parser.read(path, encoding=...)``：编码不对时它**抛**
        # UnicodeDecodeError（只捕获 OSError），而且失败后 parser 状态是脏的。
        # 自己按候选编码把文本读出来，再用 read_string()，编码才真正可控。
        # Windows 记事本默认存 GBK，所以 gbk 必须在这里。
        text = None
        for encoding in ("utf-8-sig", "gbk", "latin-1"):
            try:
                with open(path, encoding=encoding) as handle:
                    text = handle.read()
                break
            except (OSError, UnicodeDecodeError, LookupError):
                continue
        if text is None:
            continue
        try:
            parser = configparser.ConfigParser()
            parser.read_string(text)
            if not parser.has_section("level"):
                continue
            for key in keys:
                if parser.has_option("level", key):
                    return parser.get("level", key)
        except Exception:  # noqa: BLE001 —— 配置文件坏了不能把服务端带崩
            continue
    return None


def levelFromIni():
    """从 ini 的 ``[level]`` 段读关卡；读不到返回 None。"""
    return _parseLevel(_iniGet(("id", "level", "level_id", "levelId")))


# --- 通用 ini 段读取（2026-09-23 夜新增，共享实现） ---------------------------
#
# 为什么要有它：``level.ini`` 里已经有 ``[level]`` / ``[move]`` / ``[cc]`` / ``[trace]``
# 四段，读法（walkUp 找候选文件 + utf-8-sig/gbk/latin-1 依次试 + 坏配置吞掉）
# **一模一样**。``controls._moveIniRaw`` 是第一份拷贝，``ccobject._ccIniRaw`` /
# ``tracelog._traceIniRaw`` 是第二三份 —— 再往下写第四份就会开始互相漂移。
# 所以收成这一份，各模块只传段名。
#
# ⚠️ 段里的值**不要写行内注释**（``hp=1000    ; 说明``）：ConfigParser 默认
#    不认行内注释，值会变成 ``1000    ; 说明`` ⇒ 解析失败 ⇒ **静默退回默认值**。
#    注释请单独占行。
def iniSection(section, envName=None):
    """把 ``level.ini`` / ``server.ini`` 的 ``[section]`` 段读成 dict；读不到返回空 dict。

    ``envName`` 给了就是「覆盖用哪个 ini 文件」的环境变量（值 ``off``/``0``/空 =
    完全不读 ini）；文件候选顺序复用 ``_iniCandidates()``（``level.ini`` 优先于
    ``server.ini``）。**配置坏了绝不抛异常**（读不到就空 dict，调用方走默认值）。
    """
    if envName:
        override = os.environ.get(envName)
        if override is not None:
            text = str(override).strip()
            if text.lower() in ("off", "none", "0", ""):
                return {}
            paths = (text,)
        else:
            try:
                paths = _iniCandidates()
            except Exception:  # noqa: BLE001
                return {}
    else:
        try:
            paths = _iniCandidates()
        except Exception:  # noqa: BLE001
            return {}
    import configparser
    for path in paths:
        if not os.path.isfile(path):
            continue
        # 编码按候选依次试（Windows 记事本默认 GBK），并**自己读文本**再
        # read_string —— parser.read() 编码不对时抛 UnicodeDecodeError 且状态变脏。
        text = None
        for encoding in ("utf-8-sig", "gbk", "latin-1"):
            try:
                with open(path, encoding=encoding) as handle:
                    text = handle.read()
                break
            except (OSError, UnicodeDecodeError, LookupError):
                continue
        if text is None:
            continue
        try:
            parser = configparser.ConfigParser()
            parser.read_string(text)
            if not parser.has_section(section):
                continue
            return {key: parser.get(section, key)
                    for key in parser.options(section)}
        except Exception:  # noqa: BLE001 —— 配置文件坏了不能把服务端带崩
            continue
    return {}


def levelIniSection():
    """``level.ini`` 的 ``[level]`` 段（dict）。**认 ``T7_LEVEL_INI`` 覆盖。**

    ⚠️ 2026-10-01：``mode_item_N`` 这类键就靠它读。与 ``levelFromIni`` /
    ``_iniGet`` 同一文件口径 —— 否则设了 ``T7_LEVEL_INI`` 做 A/B 时会读到
    旧 ini，「明明改了却没生效」很难查。
    """
    return iniSection("level", envName=LEVEL_INI_ENV)


_levelSource = "default"


def levelId() -> int:
    """当前关卡（level_id）。**环境变量 > level.ini/server.ini > 默认**。

    * 环境变量 `T7_LEVEL`：临时 A/B 用，优先级最高；空串当没设（**不警告**）。
    * `level.ini`（或 `server.ini`）的 `[level] id=`：日常用，双击 exe 也能生效。
    * 都不认识：退回 `LEVEL_ID_DEFAULT`，**只警告一次**。
    """
    global _levelSource
    raw = os.environ.get(LEVEL_ENV)
    if raw is not None and str(raw).strip():
        value = _parseLevel(raw)
        if value in SPAWN_BY_LEVEL:
            _levelSource = LEVEL_ENV
            return value
        _warn(LEVEL_ENV, raw)

    value = levelFromIni()
    if value in SPAWN_BY_LEVEL:
        _levelSource = "ini"
        return value
    if value is not None:
        _warn("ini [level]", value)

    _levelSource = "default"
    return LEVEL_ID_DEFAULT


LEVEL_ID = levelId()
# 是否显式切了图（用于「默认逐位不变」的开关判断）
LEVEL_CUSTOM = LEVEL_ID != LEVEL_ID_DEFAULT

# --- 回显 level_id 还是 pattern_id（2026-09-19 新增） ---------------------------------
# ⚠️ 这是**唯一还没实机定论**的点：ROOM_CREATE_RSP 里那个 resource_id，
#    客户端到底认「关卡表里的 level_id」还是「大厅卡片的 pattern_id」？
#    大厅文档写「回显 pattern 会被静默丢弃」，但服务端一直回显 1028（是个
#    pattern）却能进洛阳 —— 两者矛盾，只能实测。
#    所以做成开关：一次重启就能 A/B，不必再改代码。
#    默认 ``level``（按客户端 game_level 表校验的说法），不行就改成 ``pattern``。
LEVEL_ECHO_MODES = ("level", "pattern", "client")
LEVEL_ECHO_ENV = "T7_LEVEL_ECHO"
# ⚠️ **默认 ``client``**（2026-09-19 实机定论）：
#   洛阳能进，是因为服务端回 1028 = 客户端发 1028（match=1）。
#   宛城卡 = 29、回 = 10085（match=0）→ 「连接房间服务器失败」。
#   level 模式我加过、回 = 10085 → 还是 match=0。pattern 模式我加过、
#   回 = 29 → 推算可行但没实机。原样回 = 与 洛阳 等价 → **最稳**。
LEVEL_ECHO_DEFAULT = "client"


def echoMode() -> str:
    """回显策略。**环境变量 > ini ``[level] echo=`` > 默认 ``client``**。

    只认 ``level`` / ``pattern`` / ``client``；别的取值**静默**退回默认
    （不警告——这是日常要改的配置，警告会刷屏；真写错了看启动那行
    ``echoMode=`` 就知道）。
    """
    raw = os.environ.get(LEVEL_ECHO_ENV)
    if raw is None or not str(raw).strip():
        raw = _iniGet(("echo", "echo_mode", "echoMode"))
    if raw is None:
        return LEVEL_ECHO_DEFAULT
    value = str(raw).strip().lower()
    return value if value in LEVEL_ECHO_MODES else LEVEL_ECHO_DEFAULT


def echoResourceId(value: int) -> int:
    """回显给客户端的 resource_id。

    * **默认关卡（LEVEL_CUSTOM=False）**：恒为 ``RESOURCE_ID = 1028``，
      **逐位不变**（不依赖 echo 模式）；
    * **切图后（LEVEL_CUSTOM=True）**：
      - ``client``（默认）：原样回客户端发来的 ``value``；
      - ``level``：回 ``LEVEL_ID``（10085 / 10002）；
      - ``pattern``：回 ``PATTERN_BY_LEVEL[LEVEL_ID]``（29 / 20001）。
    """
    if not LEVEL_CUSTOM:
        return RESOURCE_ID
    if _LEVEL_ECHO == "client":
        # ⚠️ 只认**这张图自己的卡片**（PATTERNS_BY_LEVEL），不认识的卡回退到
        # RESOURCE_ID。为什么必须约束：instanceInfo（instance-minimal-update，
        # 客户端拿它加载地图）用的是 RESOURCE_ID **常量**，不看客户端发了什么。
        # 无约束地原样回，客户端就会在同一次进图里拿到两个不同的地图 ID
        # （room-create 回 X、minimal-update 回 29）→ 照样卡在载入地图。
        known = PATTERNS_BY_LEVEL.get(LEVEL_ID, ())
        return value if value in known else RESOURCE_ID
    if _LEVEL_ECHO == "pattern":
        return PATTERN_BY_LEVEL.get(LEVEL_ID, LEVEL_ID)
    return LEVEL_ID


def acceptsResourceId(value) -> bool:
    """客户端 ROOM_CREATE_REQ 带的 resource_id 能不能接受。

    * **默认（没设 T7_LEVEL）**：只认 1028，与改动前**逐位相同**；
    * **切了图**：不校验——服务端只认 ``T7_LEVEL``，客户端点哪张卡都按
      配的关卡走（客户端实际带的 pattern 会进日志 ``room-create-resource-id``）。
      这样「卡片 ID 到底是 pattern 还是 level」这个未闭合项可以先绕过去做实验。
    """
    if not LEVEL_CUSTOM:
        return value == RESOURCE_ID
    return True

# ⚠️⚠️ 2026-09-19 关键修正：**RESOURCE_ID 必须是 pattern_id，不能是 level_id**。
#
# 证据链：
#   1. 洛阳（能进）服务端恒发 1028；而 1028 = PATTERN_BY_LEVEL[10036]，
#      洛阳的 level_id 是 10036 —— 所以客户端认的是 **pattern**。
#   2. ``instanceInfo()``（instance-minimal-update，告诉客户端加载哪张图的
#      那条）用的是**这个常量**，不是 ``echoResourceId()``。
#   3. 上一版把默认改成 echo=client 后：room-create 回 29（客户端发的）✅，
#      但 instance-minimal-update 仍发 10085 ❌ —— **同一次进图里客户端拿到
#      两个互相矛盾的地图 ID**，所以宛城照样卡在「载入地图」。
#
# 所以：切图后默认（client / pattern）一律用 pattern；只有显式 ``echo=level``
# 才回 level_id（保留 A/B 能力）。不切图时恒 1028，逐位不变。
_LEVEL_ECHO = echoMode()
if not LEVEL_CUSTOM:
    RESOURCE_ID = 1028
elif _LEVEL_ECHO == "level":
    RESOURCE_ID = LEVEL_ID
else:
    RESOURCE_ID = PATTERN_BY_LEVEL.get(LEVEL_ID, LEVEL_ID)
START_PATTERN = 2
USER_ID = 10000
# 玩家等级 —— **大厅登录、实例 actor、视野 actor 三处唯一来源**。
# 曾经三处各写各的（大厅 99 / 实例 1 / 视野 1，后两处是编码器默认值），
# 于是同一个玩家在大厅和在局内显示成两个等级。
USER_LEVEL = 100
ACTOR_ID = 1
# 玩家自己操作的那个武将（进图手里拿他的武器、人物模型也是他）。
# 默认 1101 赵云 = 改动前写死的那个值，不设就是现状逐位不变。
# **换人不用改代码**：在 level.ini 的 ``[battle]`` 段写一行 ``hero_id=3031`` 重启即可
# （ini 而不是环境变量：T7.Server.exe 由 launcher 拉起，终端 set 的变量进不去）。
# ⚠️ 前提：这个 tid 得在出战阵容（名册 ``battleFormation``）里，否则客户端选将面板
#    根本没有这张卡 —— 那种情况 ``_battleFormation`` 会打一行警告，别当没看见。
HERO_ID_DEFAULT = 1101


def _playerHeroId():
    """``[battle] hero_id=`` > 默认 1101。写错（非数字）不抛异常，走默认并留一行日志。"""
    raw = str(iniSection("battle").get("hero_id", "")).strip()
    if not raw:
        return HERO_ID_DEFAULT
    try:
        return int(raw, 0)
    except ValueError:
        print("[contracts] [battle] hero_id=" + raw + " 不是数字，按默认武将 "
              + str(HERO_ID_DEFAULT) + " 走", flush=True)
        return HERO_ID_DEFAULT


# ⭐⭐⭐ 2026-10-01（第十五轮）：**按关卡自动换将**。
#
# 用户要求：点「弓箭手训练」就用弓箭系的将、点「骑兵训练」就用骑兵系的将，
# **不用改 ini、不用重启**。
#
# 机制：``level.ini [battle] hero_id=`` 是**全局单值**，不管进哪张图都是同一个将
# （目前写死 3031 华佗）；而下面 ``actorInfo``/``actorVision``/``currentWeaponTid``
# 等函数的 ``heroId=HERO_ID`` 是 **Python 默认参数**（定义时求值一次）——
# 运行时改 ``HERO_ID`` 对它们**无效**。所以这里改成两级：
#
#   ① ``HERO_BY_LEVEL``：关卡 → 武将 tid（本表**只列想覆盖的图**，未列的走 ini/默认）
#   ② ``_ACTIVE_HERO``：当前生效的武将，由 ``applyLevel()`` 切图时同步
#   ③ 所有函数签名改成 ``heroId=None`` 哨兵，函数体首行
#      ``if heroId is None: heroId = _ACTIVE_HERO``（运行时读）
#
# 约束（务必遵守，否则换将白配）：
#   * **武将必须在名册的武器表里**（``EQUIP_GROUPS_BY_HERO_TID`` 那 5 个）。
#     否则 ``battleLoadout()`` 返回空列表 ⇒ 进图**空手**、选将面板也没这张卡。
#     本表 11 条全部取自那 5 个已验证武将，见下面注释。
#   * 换将只影响**玩家自己**（actorInfo/actorVision/weapon 三条链路），
#     不动别人、不动出生点。
#
# 职业配对（按用户「你按游戏常识帮我配」）：
#   先锋·剑盾 → 赵云 1101（龙牙刀，B级双手）｜神射·弓箭 → 黄忠 3012（长弓）
#   骠骑·枪骑 → 姜维 2121（骑兵，带马）    ｜奇行·医师 → 华佗 3031（四组全默认）
#   奇行·匠师 → 华佗 3031（借，暂缺匠师专属将）｜奇行·刺客 → 赵云 1101（借）
#
# 铁骑训练场五种：**已按客户端原表逆向查实**（2026-10-01），不再是「就近借」。
#   证据 = data1.vfs 的 `../data/ui/resource/icon/rune/r19_1.png`
#          （名为 png，实为「武将选角」总表：MSES ver7、rs=7286、rc=1343）。
#          其中行 409~413 第 0 列直接写关卡名，第 17~20 列是出战模型 .nif：
#            行409 挥砍训练关 → B_xf_xuchu_a.nif     = 许褚 3005
#            行410 格挡训练关 → B_xf_xuhuang_a.nif   = 徐晃 3066
#            行411 神射训练关 → B_Sunshangxiang_ss_a = 孙尚香 2111（性别字段=女，与卡面一致）
#            行412 骑术训练关 → B_bq_machao_a.nif    = 马超 3221
#            行413 骑射训练关 → B_ss_zhangren_a.nif  = 张任 2023
#          交叉验证：用行 0~408 建「模型名→武将名」映射反查，5 个里 3 个唯一解，全部吻合。
#          官方关卡名（`s_res_havok_character_select_clt.bin` 行 65~70）：
#            10069 训练场_挥砍 / 10071 骑射训练 / 10072 刀骑训练 /
#            10073 招架训练   / 10074 弓箭手训练
#   ⚠️ 这 5 位的武器行由 `武将批量解锁/5_补训练场武将武器.py` 补进名册
#      （组号取自同表行内偏移 4211 起的 7×12B 装备组段），缺少则进图空手。
HERO_BY_LEVEL = {
    # --- 职业训练 6 张（level.ini [battle] hero_id 会被这里的值覆盖）---
    10023: 1101,   # 1 先锋·剑盾  → 赵云（龙牙刀，先锋系双手）
    10015: 3012,   # 2 神射·弓箭  → 黄忠（长弓，神射手）
    10038: 3031,   # 3 奇行·医师  → 华佗（本命）
    10016: 2121,   # 4 骠骑·枪骑  → 姜维（骑兵，名册里唯一带马的卡）
    10041: 3031,   # 5 奇行·匠师  → 华佗（暂借；匠师无专属武器数据）
    10079: 1101,   # 6 奇行·刺客  → 赵云（暂借；刺客无专属武器数据）
    # --- 铁骑训练场 5 张（原表实证，见上面注释）---
    10069: 3005,   # 挥砍训练关   → 许褚（双手斧 L3）
    10073: 3066,   # 格挡/防御训练关 → 徐晃（长柄刀 L3 + 长矛 + 重盾）
    10074: 2111,   # 神射训练关   → 孙尚香（长弓 L3）
    10072: 3221,   # 骑术训练关   → 马超（长枪 L4）
    10071: 2023,   # 骑射训练关   → 张任（短弓 L3）
}

HERO_ID = _playerHeroId()
# 当前生效的武将。``applyLevel()`` 切图时会把它同步成 ``HERO_BY_LEVEL[level]``；
# 初始值 = 启动时 level.ini 那张（现在是 3031），**不切图时逐位不变**。
#
# ⚠️ 所有原来写 ``heroId=HERO_ID`` 的函数都改成 ``heroId=None``，
#    函数体内 ``if heroId is None: heroId = _ACTIVE_HERO`` ——
#    因为 Python 默认参数在**定义时**求值一次，运行时改 ``HERO_ID`` 对它们无效。
_ACTIVE_HERO = HERO_ID


def setHeroForLevel(levelId) -> bool:
    """切图时把当前武将同步成 ``HERO_BY_LEVEL[levelId]``。

    返回 ``True`` 表示真的换了人（打了日志）；``False`` 表示该图没配、保持原样。
    **只在换人时打日志**，避免每次点图都刷屏。
    """
    global _ACTIVE_HERO
    want = HERO_BY_LEVEL.get(levelId)
    if want is None or want == _ACTIVE_HERO:
        return False
    old = _ACTIVE_HERO
    _ACTIVE_HERO = want
    print("[contracts] hero-auto-switch level=" + str(levelId)
          + " hero " + str(old) + " -> " + str(want), flush=True)
    return True


def activeHeroId():
    """**当前生效武将**（``setHeroForLevel`` 按关卡切换后的值）。

    ⭐⭐ 2026-10-03（第十六轮）新增。为什么必须有这个访问器
    ------------------------------------------------------
    ``actorInfo()`` / ``actorVision()`` 的 ``heroId=None`` 哨兵只对
    **不传 heroId** 的调用生效。而 ``scene.py`` 有三处把
    ``flow.session["heroId"]`` **显式传进去**，那个值是
    ``scene.py`` 用 ``setdefault("heroId", wire.HERO_ID)`` 冻结的
    **ini 常量**（3031 华佗）—— 于是：

      * ``actorInfo()``   不传 heroId ⇒ 走 ``_ACTIVE_HERO``  ⇒ 发的是许褚(3005) ✅
      * ``actorVision()`` 显式传 heroId ⇒ 覆盖成 3031        ⇒ 发的是华佗     ❌

    两条链路**自相矛盾**，客户端按**视野实体**建人 ⇒ 实机表现就是
    「点哪张训练卡，人物模型都是华佗，换将没生效」（用户 2026-10-03 报的第 2 条）。
    ⇒ ``scene.py`` 一律改用它取默认值，**不再回落 ini 常量**。
    """
    return _ACTIVE_HERO

# 战斗侧（进图匹配面板）的可选武将在下面 HERO_IDS，跟着名册出战阵容走。
# 这里只留大厅出战卡的默认值。
#
# 昵称（``USER_NAME`` / ``encodePlayerName`` / ``defaultPlayerName``）
# ------------------------------------------------------------------
# 昵称**不是常量**：宿主在 context 里传 ``playerName``（启动器里配的那个）。
# ``createState()`` 存进 state，``Flow.playerName`` 取出来，登录应答和所有角色
# 消息都用它。``USER_NAME`` 只是「没配昵称时」的默认值。
#
# 存储口径是 **GBK，最多 31 字节**（客户端 ``user_name`` 字段按 GBK 解、超长会
# 被截断成乱码），所以统一走 ``encodePlayerName()`` 收口：strip、拒控制字符、
# 拒空、限长。别再让某条报文直接用原始字符串。
USER_NAME = "新玩家".encode("gbk")


def encodePlayerName(value):
    """昵称 -> 线上字节（GBK，≤31 字节）。校验不通过直接抛。

    ``strip()`` 之后为空、含控制字符（含 DEL 与 C1）、GBK 编不出来、超长 —— 都算无效。
    """
    if type(value) is not str:
        raise TypeError("playerName must be a string")
    value = value.strip()
    if not value or any(ord(char) < 32 or 127 <= ord(char) < 160 for char in value):
        raise ValueError("playerName must be nonempty and contain no control characters")
    encoded = value.encode("gbk", errors="strict")
    if len(encoded) > 31:
        raise ValueError("playerName exceeds the 31-byte GBK limit")
    return encoded


def defaultPlayerName():
    """没配昵称时的默认值：``level.ini [server] player_name=`` > ``USER_NAME``。

    走 ini 而不是环境变量：宿主进程由启动器拉起，终端 ``set`` 的变量进不去。
    """
    raw = str(iniSection("server").get("player_name", "")).strip()
    if not raw:
        return USER_NAME.decode("gbk")
    try:
        encodePlayerName(raw)
    except (ValueError, TypeError) as error:
        print("[contracts] [server] player_name=" + raw + " 无效（" + str(error)
              + "），按默认昵称走", flush=True)
        return USER_NAME.decode("gbk")
    return raw


POSITION, ENEMY_POSITION = SPAWN_BY_LEVEL[LEVEL_ID]

# camp -> 用 ``SPAWN_BY_LEVEL[level]`` 那**一对点**里的第几个。
# 只列「客户端表里两边各有一个 ``is_main=1`` 主营地」的图；没列的图恒用 [0]。
# 樊城（tszz，``010495/011028_SH_RES_ACTOR_SPAWN_AREA_TAB``）：
#   区 3935066568 ``is_main=1 init_state=2`` = 503.179,581.889,43.2482 —— 表里的 [0]
#   区 3935066570 ``is_main=1 init_state=1`` = 622.006,572.036,44.4526 —— 表里的 [1]
# 实测 2026-09-22 两局（44792-181725929，樊城 21:19 / 洛阳 21:25）客户端上行的
# camp **都是 1**（明文 S2C ``instance-camp-exchange-notify`` 第 5 个 int 解出来的），
# 而旧代码不看 camp、恒发 [0] ⇒ camp=1 的人被放进 ``init_state=2`` 那侧营地。
# 樊城中央要塞的空气墙在 x 547..613，两个营地分列 x=503 / x=622、相隔 119 米，
# 所以放错边看起来就是「一进来在图外」。
# ⚠️ ``init_state`` 与 camp 的对应（1↔1、2↔2）是按数值推的，客户端没实锤；
#    实机若变成「开局在对面」，把这张表两个值对调即可，只改这一处。
SPAWN_INDEX_BY_CAMP = {10002: {1: 1, 2: 0},
                       # ⭐ 2026-09-27 江陵城 —— **实机修正**（会话 33716-589277802，14:30）。
                       #   原按「camp ↔ init_state 数值对应」推成 `{1: 1, 2: 0}`，实测**反了**：
                       #   客户端报 `camp=1`（trace `spawn-side-chosen camp=1 level=10020`），
                       #   却落到 `[1]=(476.160, 588.578, 25.1002)` = **城内**
                       #   （Y=588.578，紧贴「大道旗 Y=562.9」）⇒ 用户要的攻方 / 城外没生效。
                       #   按本表上方那段注释的既定处置「实机若攻守反了，只把这两个值对调」⇒ 对调。
                       #   现在 `camp=1 → index=0 → [0]=(511.041, 354.258, 12.7277)` = **城外**
                       #   （判据见 `SPAWN_BY_LEVEL[10020]` 那段：云梯 Y≈402~404 贴城墙外侧摆，
                       #    Y 小 = 城外；本点净空 71.40 m，该图 34 段墙里余量最大）。
                       #   ⚠️ 樊城（10002）**保持不动** —— 它没有同类反馈，别跟着一起改。
                       #   ⚠️ 仍属**数值推断**：若实机变成「守方被丢到城外」，把这两个值再对调回来。
                       10020: {1: 0, 2: 1}}
_appliedCampSpawn = None
_camp = None                      # 客户端报过的 camp；没报过 = None（不替它猜）

# --- 骑兵专用出生点（2026-09-22 夜接线，**默认关**）------------------------------
# 起因：洛阳（lysd）现在用的出生点 `272.451,157.634,0.21751` 来自点组
# ``3379637679``，该组 ``is_infantry=1 / is_cavalry=0`` —— **是纯步兵点**。
# 同一张表（``005773_SH_RES_ACTOR_SPAWN_GROUP_TAB_c3059c0.xml``）里有**骑兵专用**
# born 组，一侧一个，正好对得上 camp：
#   * ``1492739893``  6 点  ``is_born=1 is_revive=1 is_cavalry=1``  **is_red=1 is_blue=0**
#   * ``3796015618``  4 点  ``is_born=1 is_revive=1 is_cavalry=1``  **is_blue=1 is_red=0**
# 下面两个坐标是各组里**净空最大**的那个点（判据 = ``aairwall.xml`` 的
# ``AirWall.footprint`` **折线段最小距离**，不是 ``<Entity Position>`` 中心点距离；
# < 5 m 会被挤、≥ 10 m 才稳），由 ``hkx_decode/pick_cavalry_spawn.py`` 现读表算出：
#   * blue 组 3796015618 最佳 #0 `258.730,135.621,0.107806`  段距 13.54 m  groundZ 差 0.11
#     （该组另外 3 点段距只有 9.34 / 7.27 / 4.27 m，只有这一个够用）
#   * red  组 1492739893 最佳 #5 `252.620,232.727,0.0950927` 段距 30.30 m  groundZ 差 0.16
#     （整组 6 点段距都 ≥ 26 m）
# 对照：现在用的步兵点段距只有 10.92 m，刚好过线。
#
# ⚠️ **``camp`` ↔ ``is_blue``/``is_red`` 的对应客户端没实锤**。这里按「camp 1 = blue」
#    填（依据：lysd 现在用的步兵组就是 ``is_blue=1``，而 camp 没报时也用 [0]）。
#    实机若变成「骑兵在对面出生」，**只把下面两个值对调**，别改别处。
# ⚠️ 洛阳（10036）与洛阳死斗（10005）是**同一张图 lysd**，所以两张表内容一样。
CAVALRY_SPAWN_ENV_SWITCH = "T7_CAVALRY_SPAWN"
CAVALRY_SPAWN_ENABLED = str(os.environ.get(CAVALRY_SPAWN_ENV_SWITCH, "")).lower() in (
    "1", "on", "true", "yes")

_CAVALRY_LYSD = {1: (258.730, 135.621, 0.107806),      # blue 组 3796015618
                 2: (252.620, 232.727, 0.0950927)}     # red  组 1492739893
CAVALRY_SPAWN_BY_LEVEL = {10036: dict(_CAVALRY_LYSD), 10005: dict(_CAVALRY_LYSD)}


def cavalrySpawnPoint(camp=None):
    """当前关卡 + camp 下的**骑兵专用**出生点；开关关 / 没这张表 → ``None``。"""
    if not CAVALRY_SPAWN_ENABLED:
        return None
    table = CAVALRY_SPAWN_BY_LEVEL.get(LEVEL_ID)
    if not table:
        return None
    key = _camp if camp is None else camp
    if key not in table:
        key = 1
    return table.get(key)


def _recomputeSpawn():
    """按 (关卡, camp, 是否骑兵) 重算 ``POSITION/ENEMY_POSITION``；返回是否真的变了。

    **唯一入口** —— ``applyCampSpawn`` / ``applyCavalrySpawn`` / ``applyLevel`` 都走它，
    免得「营地换边」和「骑兵换点」各写一份、互相覆盖。
    """
    global POSITION, ENEMY_POSITION
    points = SPAWN_BY_LEVEL[LEVEL_ID]
    index = SPAWN_INDEX_BY_CAMP.get(LEVEL_ID, {}).get(_camp, 0)
    base = (points[1], points[0]) if index else points
    cavalry = cavalrySpawnPoint()
    wanted = (cavalry, base[1]) if cavalry is not None else base
    if (POSITION, ENEMY_POSITION) == wanted:
        return False
    POSITION, ENEMY_POSITION = wanted
    return True


def applyCampSpawn(camp) -> bool:
    """按**客户端选的 camp** 把出生点换到本方那一侧营地。

    只在 ``instance-camp-choose`` 真的解析出 camp 之后调用 —— 客户端没说 camp 时
    保持 ``SPAWN_BY_LEVEL`` 的 [0]/[1] 顺序，不替它猜。幂等：同 (关卡, camp) 重复
    调用一个字节都不改。不在 ``SPAWN_INDEX_BY_CAMP`` 里的图恒用 [0]（洛阳那两个点
    只差 1.6 m，是同一组散点而不是两边，换边没有意义）。
    """
    global _appliedCampSpawn, _camp
    if _appliedCampSpawn == (LEVEL_ID, camp):
        return False
    _camp = camp
    _appliedCampSpawn = (LEVEL_ID, camp)
    return _recomputeSpawn()


def applyCavalrySpawn(heroId=None) -> bool:
    """名册带坐骑（骑兵）时，把出生点换成该关卡的**骑兵专用 born 组**点。

    ⚠️ 默认**关**（``T7_CAVALRY_SPAWN=on`` 才生效）。**步兵 / 开关关着 / 这张图没有
    骑兵点 → 一个字节都不改**（三次早返回，连 ``_recomputeSpawn`` 都不进）。

    由 ``actorVision()`` 自己调（它有三个调用点，放外面容易漏一个），
    这样「下发给客户端的出生点」与「服务端自己那份 POSITION」永远是同一个值。

    ``heroId`` 传 None（默认）= 取**当前生效武将**（``heroId()``，会跟着关卡换）。
    """
    if heroId is None:
        heroId = _ACTIVE_HERO
    if not CAVALRY_SPAWN_ENABLED:
        return False
    if cavalrySpawnPoint() is None:
        return False
    if not heroHasMount(heroId):
        return False
    return _recomputeSpawn()

# --- 将星录解锁名册（2026-09-21） ---------------------------------------------
# 武将卡 + 将魂 + 货币，读 ``server/data/hero/hero_roster.json``。
# 为什么放数据文件：名单里是客户端 herocard 表的 TID，加武将/换版本只改 json，
# 不用碰编解码器。
# ⚠️ 定位必须走 ``walkUp()``：脚本会被快照到 ``data/<会话>/revisions/t7rev_x/``
#    再跑，那条路径下没有 ``data/hero``（同 heightfield 踩过的坑）。
def _heroDataDir():
    for node in walkUp():
        path = os.path.join(node, "data", "hero")
        if os.path.isdir(path):
            return path
    return os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "data", "hero")


HERO_ROSTER_PATH = os.path.join(_heroDataDir(), "hero_roster.json")


# pos 1 那张是战斗侧出战绑定认的 guid/位置，名册里也必须保持它，
# 否则 ``battle_hero`` 绑到一张不存在的卡上，进图就没人可控了。
HERO_CARD_GUID = 1


def _coerceRoster(data, source):
    """把「JSON 形状」的名册规范成运行期形状（元组化 / 键名映射 / 类型收敛）。"""
    return {
        "cards": tuple(tuple(c) for c in data["cards"]),
        "souls": tuple(tuple(s) for s in data.get("souls", [])),
        "currency": dict(data.get("currency", {})),
        # JSON 里叫 ``containerCardLimit``，``herodefault.asRoster()`` 里叫 ``limit``
        # —— 两种形状都认，避免内置默认那条路静默丢掉卡位上限。
        "limit": int(data.get("containerCardLimit", data.get("limit", 50))),
        "unlocks": {int(k): tuple(v) for k, v in data.get("unlocks", {}).items()},
        "battleFormation": tuple(data.get("battleFormation") or (1,)),
        "weapons": tuple(tuple(w) for w in data.get("weapons", [])),
        "source": source,
    }


def _builtinRoster():
    """内置默认名册（``herodefault.py``），干净检出时的兜底。

    ``src/Business/data/`` 是运行期数据、不进版本库，所以干净检出没有
    ``hero_roster.json``。只退回「一张卡 + 空装备」会让 ``HERO_BATTLE_LOADOUTS``
    变成 ``{}`` ⇒ ``battleLoadout()`` 返回空列表 ⇒ 进图空手、坐骑链路失败。
    ``herodefault.py`` 与 JSON 同源，由 ``tools/hero_roster.py`` 生成。
    """
    try:
        from . import herodefault
    except Exception as error:                       # pragma: no cover - 自包含校验
        try:
            import herodefault                       # 脚本被当顶层模块加载时
        except Exception:
            print("[contracts] herodefault 不可用: " + repr(error), flush=True)
            return None
    try:
        return _coerceRoster(herodefault.asRoster(), "builtin")
    except Exception as error:                       # pragma: no cover - 自包含校验
        print("[contracts] herodefault 内容异常: " + repr(error), flush=True)
        return None


def _loadHeroRoster():
    """本地 JSON 优先；缺文件/坏 json 时退到内置默认，最后才是单卡兜底。

    ``source`` 有三个取值，用来在启动自证行里区分「代码没生效」和「文件没找到」：

      * ``file``     —— 读到了 ``data/hero/hero_roster.json``（自研运行期的常态）；
      * ``builtin``  —— 没有文件，用的 ``herodefault.py`` 内置默认（**干净检出**）；
      * ``fallback`` —— 连内置默认都用不了（只有 pos 1 一张卡 + 空装备，
                        退化到改动前的行为，仅作最后保险）。
    """
    try:
        with open(HERO_ROSTER_PATH, encoding="utf-8") as fp:
            return _coerceRoster(json.load(fp), "file")
    except Exception as error:
        print("[contracts] hero_roster 读取失败，退回内置默认: " + repr(error),
              flush=True)
    builtin = _builtinRoster()
    if builtin is not None:
        return builtin
    return {"cards": ((1, HERO_CARD_GUID, HERO_ID),), "souls": (),
            "currency": {}, "limit": 50, "unlocks": {},
            "battleFormation": (1,), "weapons": (), "source": "fallback"}


def _battleFormation(roster):
    """出战阵容：名册里的 herocard 槽位 -> (pos, guid, tid)。

    槽位在 0x22/4 应答里按 herocard 位置引用，但 type-2 容器自己另占 1..N 槽，
    两边靠同一个 guid 对上（prior_art §2）。名册里查不到的位置直接跳过——
    绑一张不存在的卡会让客户端阵容空转。
    """
    byPos = {c[0]: c for c in roster["cards"]}
    entries = []
    for position in roster["battleFormation"]:
        card = byPos.get(position)
        if card is None:
            print("[contracts] battleFormation 位置 " + str(position) + " 不在名册，跳过",
                  flush=True)
            continue
        entries.append((card[0], card[1], card[2]))
    if not entries:
        return ((1, HERO_CARD_GUID, _ACTIVE_HERO),)
    entries = tuple(entries)
    # ⭐ 玩家自己 = **阵容第 1 格**（客户端按这个顺序认人、把第一格当本地英雄）。
    # 所以换了 hero_id 就要把那张卡转到最前，其余保持原序。
    # 默认 hero_id=1101 时名册里它本来就在第 1 ⇒ 这一转等于没转，逐位不变。
    #
    # ⭐⭐ 2026-10-01（第十五轮）：本函数在**模块加载时**被调用一次，那时
    # ``_ACTIVE_HERO`` = ini 里那张 ⇒ 与旧行为逐位相同。运行时换将**不改这份**，
    # 而是靠 ``battleFormationNow()`` 现算（下方）。
    current = _ACTIVE_HERO
    for index, entry in enumerate(entries):
        if entry[2] == current:
            return tuple(entries[index:] + entries[:index])
    print("[contracts] ⚠️ hero_id=" + str(current) + " 不在出战阵容 "
          + str([entry[2] for entry in entries])
          + " 里：进图会按他的武器表下发，但选将面板没这张卡，按 1/2/3 大概率对不上。"
            "要测多武器武将请换成阵容里的卡（3031 带 4 把）", flush=True)
    return tuple(entries)


def battleFormationNow():
    """**运行时**重算出战阵容，保证**当前武将稳居第 1 格**。

    ``HERO_BATTLE_FORMATION`` 是加载时算的常量、大厅发出去之后就不动了；
    切图换将后必须用本函数现算一份，否则客户端第 1 格还是旧武将。

    ⭐⭐ 2026-10-01（第二十一轮）修正 —— **「职业教学 武将没有换成默认的」根因**：

      旧实现只在**名册出战阵容那 4 张卡里**做「轮转到第 1 格」:

          for index, entry in enumerate(entries):
              if entry[2] == current:
                  return entries[index:] + entries[:index]
          return entries          # ← 找不到就原样返回！

      ``_ROSTER["battleFormation"]`` 只有 4 个槽位（``[4, 5, 1, 3]`` →
      华佗/姜维/赵云/黄忠）。而 ``HERO_BY_LEVEL`` 给训练场/职业教学配的
      **许褚 3005 / 张任 2023 / 马超 3221 / 徐晃 3066 / 孙尚香 2111 都不在这 4 格里**
      ⇒ 命中不了 ⇒ 走 ``return entries`` ⇒ **第 1 格永远是名单里那位（赵云）**
      ⇒ 客户端把赵云当本地英雄 ⇒「武将没有换成默认的」。

      实机取证（``data/44700-957621011``，2026-10-01 22:00）：点 10023/10015/10038
      三张职业教学卡，``instance-sync-item-container`` 里装的 4 张卡恒为
      ``赵云/黄忠/华佗/姜维``；而服务端日志明明打了
      ``hero-auto-switch level=10023 hero 3031 -> 1101`` —— **服务端换了、
      但那份名单没换**，所以客户端看不到。

      修法：**当前武将不在名册阵容里时，把它作为「外部卡」插到第 1 格**，
      名册那几张顺次后移（挤掉最后一张 —— 客户端只认第 1 格是本地英雄，
      后面几张是队友位，但本服务端是单机，多出来的人不会真的出现）。
      ``guid`` 用名册第 1 张卡的 guid 会撞号，所以外部卡用
      ``HERO_CARD_GUID + slot`` 另编一个**稳定且唯一**的 guid（同一武将每次进图
      同值，客户端才认得出是哪张卡）。

      当前武将在名册里时，行为与旧实现**逐位完全相同**（原来的轮转逻辑保留）。
    """
    byPos = {c[0]: c for c in HERO_CARDS}
    entries = []
    for position in _ROSTER["battleFormation"]:
        card = byPos.get(position)
        if card is None:
            continue
        entries.append((card[0], card[1], card[2]))
    if not entries:
        return ((1, HERO_CARD_GUID, _ACTIVE_HERO),)
    current = _ACTIVE_HERO
    for index, entry in enumerate(entries):
        if entry[2] == current:
            return tuple(entries[index:] + entries[:index])
    # ⭐ 当前武将不在名册阵容里（训练场/职业教学的按图换将就是这条路径）：
    #    造一张「外部卡」放第 1 格。pos 用 0（名册里没有 0 号 herocard 槽，
    #    不会和任何既有卡撞），guid 用 HERO_CARD_GUID + 槽号派生，保证唯一且稳定。
    external = (_EXTERNAL_HERO_POS, _EXTERNAL_HERO_GUID, current)
    return (external,) + tuple(entries[:len(entries) - 1])


# 按图换将时，若目标武将不在名册出战阵容里，用这张「外部卡」占第 1 格。
#   * ``pos=0`` —— 名册 cards 的 pos 从 1 起，0 不会被任何既有卡占用；
#   * ``guid`` —— 取 ``HERO_CARD_GUID``（名册第 1 张）再 +1，与它错开；
#     ⚠️ 同一武将每次都必须得到**同一个** guid，客户端才认得出是同一张卡，
#     所以这里用常量、不要改成随机/自增。
_EXTERNAL_HERO_POS = 0
_EXTERNAL_HERO_GUID = HERO_CARD_GUID + 1



def _heroWeapons(roster):
    """名册的 weapons 行 -> ``{herocard 槽位: (武器槽位, set下标, 组号, 组内序号, tid, 锁定态)}``。

    一行 = ``[pos, weapon_slot, set_index, group_id, index, weapon_tid, lock_state]``。
    ``set_index/index`` 决定武器写在 ``hero_weapon_set[set_index].weapons[index]`` 的哪一格，
    ``group_id`` 是**装备组编号**（像 104029 这种 6 位数，不是 set 下标）——
    ``weapon_slots`` 条目里带的是它，客户端拿它去武将的 7 个装备组里找组，
    所以填 0（= 该武将没有这一组）会被当无效丢掉，槽位永远空着。
    依据：武将装备链数据梳理 §七「group 即本表组号；index 为组内序号」。
    pos 不在名册里的行直接跳过——写一张不存在的卡会让整帧长度对不上。
    """
    known = {c[0] for c in roster["cards"]}
    byPos = {}
    for entry in roster["weapons"]:
        if len(entry) != 7:
            print("[contracts] weapons 行长度不是 7，跳过: " + str(entry), flush=True)
            continue
        position, weapon_slot, set_index, group_id, index, weapon_tid, lock_state = entry
        if position not in known:
            print("[contracts] weapons 位置 " + str(position) + " 不在名册，跳过",
                  flush=True)
            continue
        byPos.setdefault(position, []).append(
            (weapon_slot, set_index, group_id, index, weapon_tid, lock_state))
    return {position: tuple(items) for position, items in byPos.items()}


_ROSTER = _loadHeroRoster()
HERO_CARDS = _ROSTER["cards"]
HERO_SOULS = _ROSTER["souls"]
HERO_CURRENCY = _ROSTER["currency"]
HERO_CONTAINER_LIMIT = _ROSTER["limit"]
HERO_UNLOCK_BY_ID = _ROSTER["unlocks"]
HERO_BATTLE_FORMATION = _battleFormation(_ROSTER)
HERO_CARD_WEAPONS = _heroWeapons(_ROSTER)
_HERO_ROSTER_SOURCE = _ROSTER["source"]

# 战斗侧选将名单 = 大厅出战阵容那几张卡的 hero_resource_id，两边同一份数据。
# 以前这里写死 (110001, 110003, 110004)（赵云/关羽/孙尚香，照 VM 抓包的 fixture），
# 和大厅名册各走各的，于是「外面出战四个、进图匹配是另外三个」。
HERO_IDS = tuple(card[2] for card in HERO_BATTLE_FORMATION)

# 客户端权威移动模式下，出战阵容收敛成**当前武将一人**：移动由客户端 overlay
# 自己驱动，服务端不再维护其他人的位置，多出来的槽位只会让选将面板多出几张
# 选不动的卡。取「当前武将」而不是写死某个 tid —— 写死会让按关卡自动换将失效。
RUNTIME_HERO_IDS = (_ACTIVE_HERO,)


def heroIds(runtimeMovement=False):
    """当前模式下的出战武将元组。``runtimeMovement`` 见 ``RUNTIME_HERO_IDS``。

    ⚠️ 契约档（``contract_mode()``）：客户端权威模式回落到上游合成骨架的固定单人
    武将 ``(110001,)``，便于 vendored 参考套件对齐上游、证明没有回归。实机档仍是
    动态「当前武将」``RUNTIME_HERO_IDS``。
    """
    if runtimeMovement and contract_mode():
        return (110001,)
    return RUNTIME_HERO_IDS if runtimeMovement else HERO_IDS


# --- 战斗侧（进图）的武器与坐骑 -----------------------------------------------------
# ``CS_BATTLE_HERO_DEF`` 的 weapon_slots、instance actor 的 mount_tid、VISION actor 的
# weapon_use_data 都是战斗侧**自己那一份**装备，以前不是写 0 就是写死一把 1030411
# 龙牙刀 —— 所以外面武器栏点亮了、进图手里还是那一把、骑兵也没马。现在统一从名册
# 的 ``weapons`` 行推出来。
# 坐骑组（装备组号 111xxx，或 5xxxx 那套坐骑表）不进武器列表，单独走 ``mount_tid``。
MOUNT_GROUP_RANGES = ((111000, 112000), (50000, 60000))


def _isMountGroup(group_id):
    return any(low <= group_id < high for low, high in MOUNT_GROUP_RANGES)


def _battleLoadoutByTid(roster):
    """{武将卡 tid: (非坐骑的武器 6 元组…, 坐骑 tid)}。"""
    tidByPos = {card[0]: card[2] for card in roster["cards"]}
    out = {}
    for position, entries in HERO_CARD_WEAPONS.items():
        hero_tid = tidByPos.get(position)
        if hero_tid is None:
            continue
        out[hero_tid] = (
            tuple(entry for entry in entries if not _isMountGroup(entry[2])),
            next((entry[4] for entry in entries if _isMountGroup(entry[2])), 0),
        )
    return out


HERO_BATTLE_LOADOUTS = _battleLoadoutByTid(_ROSTER)


# --- 投掷/消耗武器的 HUD 弹药数（2026-09-24 实机 A/B） ----------------------------
# 面板上「飞戟 5」那个数字 = 武器记录 bullet_info.stack_count。权威数值在客户端
# s_weapon_flyer_cli.bin（打包在 data1.vfs 里，本地这套 VFS 工具只手动抠出过 3 张
# 表、没有通用 name→run 映射，抠不出这一张），数值按实机视频对齐：飞戟 5、
# 竹烟筒 1（用完客户端显沙漏、冷却到自动刷回 1，刷新是客户端自己的行为）。
# 只有列在这里的 tid 会被标成「吃弹药」，近战武器（如乌铁长刀 1030411）不进这张
# 表、bullet_info 仍全 0、HUD 不显数字。单把覆盖用 level.ini ``[battle]`` 的
# ``throw_ammo_<tid>``（如 ``throw_ammo_1080311=5``）。
_THROW_AMMO_DEFAULTS = {
    1080311: 5,  # 飞戟   —— 赵云 1101 槽 2
    1080511: 1,  # 竹烟筒 —— 赵云 1101 槽 3
}


# 生效通道是 **level.ini 的 ``[battle]`` 段**，不是环境变量：T7.Server.exe 由
# T7.Launcher.exe 拉起，终端 ``set`` 的变量**进不去那个进程**（``[trace]`` 段
# 2026-09-23 就为此踩过一次）。
_BATTLE_INI = iniSection("battle")


def _battleIniSwitch(*keys):
    """读 ``[battle]`` 段里的布尔开关：1/on/true/yes 为真，其余为假。"""
    for key in keys:
        value = str(_BATTLE_INI.get(key, "")).strip().lower()
        if value:
            return value in ("1", "on", "true", "yes")
    return False


def _battleIniInt(key, default=0):
    """读 ``[battle]`` 段里的整数；没填或填错都走默认，不抛异常。"""
    try:
        return int(str(_BATTLE_INI.get(key, "")).strip(), 0)
    except ValueError:
        return default


# 投掷落地挂的那颗物件默认=投掷武器自己的 MO 号（烟筒 1080511=「烟雾弹范围」，半径 5 米）。
# ``cc_res_id`` 把它临时换成别的 MO 号做验证（客户端 MO 表里物件 id 就是武器 tid 本身）：
#   1100531 = 香炉3a（类别「物件_带Buff.香炉」、半径 1.5 米；客户端区域动作宏表第 1 条
#             ``E_REGION_ENTITY_ACTION_CENSER_ADD_HP``「香炉加血」就是它的持续作用）
#   1100131 = 捡药范围/草药包（半径 1 米，进视野就播「草药包治疗01」）
# 填 0 或不填 = 不覆盖，按武器自己那颗走。
CC_LANDING_RES_ID = _battleIniInt("cc_res_id", 0)

# 玩家自己的**当前血量**，写进进图那条视野包（``CS_PROTO_VISION_ACTOR_INFO`` 里
# ``current_hp/max_hp/stamina`` 三个连续 int32，``vision_flow.py`` 打包）。
# 默认 100 = 与改动前逐位相同（原来根本没传这三个参数、走 vision_flow 的默认 100）。
# 填 30 的用意：**没有 AI 能打人的情况下把血弄缺一块**，好验两件事——
#   1) 客户端血条到底读不读我们这条包（读 ⇒ 以后做「砍人掉血」有地方落）；
#   2) 站进草药包那个绿圈里，血会不会自己涨回来（客户端本机回血？）。
# ⚠️ 上限恒按 100 发（只动当前值 = 一个变量）。真伤不伤、会不会猝死都不管，
#    这只是取证，不是把血量系统做出来。
HP_NOW = _battleIniInt("hp_now", 100)

# 玩家自己那份视野姿态里的**重力**（int16，单位 0.001 米每二次方秒）。
# 我们一直填 0 ⇒ 客户端拿到的角色重力就是零，离开支撑物不再往下掉（朋友
# ``相机重力.pdf`` 标定 -10000 = -10.0）。默认 0 = 逐位不变；填 -10000 才生效。
# ⚠️ 只写 actor 这一份：坐骑/攻城器械那两份姿态不传这个参数，恒 0。
ACTOR_GRAVITY = _battleIniInt("actor_gravity", 0)

# 客户端权威移动模式下的重力。上游 ``contracts.actorVision(..., runtimeMovement=True)``
# 在 ``gravityOffset = 79`` 处硬写 ``struct.pack(">h", -10000)``（= -10.0 m/s²）；
# 本项目 ``vision_flow.encode_fixed_local_actor_vision_add_event`` 的 ``gravity``
# 形参落点**同为偏移 79..80**（gravity=0 → ``0000``，-10000 → ``d8f0``），
# 因此不需要像上游那样按偏移打补丁，直接传值即可。
# 语义边界：**只在 runtimeMovement 模式生效**；服务端权威模式仍走
# ``ACTOR_GRAVITY``（默认 0），不改变服务端权威模式的行为。
RUNTIME_GRAVITY = _battleIniInt("runtime_gravity", -10000)


def _throwAmmo(weapon_tid):
    default = _THROW_AMMO_DEFAULTS.get(weapon_tid)
    if default is None:
        return 0
    raw = _BATTLE_INI.get("throw_ammo_%d" % weapon_tid)
    if raw is None or not str(raw).strip():
        return default
    try:
        return max(0, int(str(raw).strip()))
    except ValueError:
        return default


# --- 弹药的服务端运行时计数（2026-09-25 实机） --------------------------------------
# 客户端**本机自己扣**弹夹（HUD 变 0 → 提示「弹药不足」→ 投掷输入被挡），服务端
# 必须用 ``CS_PROTO_WEAPON_BULLET_UPDATE``（cmd=5 sel=4，宏表
# E_CS_PROTO_WEAPON_BULLET_UPDATE=4「svr->cli: 武器弹药信息更新」）把权威数量发回去，
# 否则扣完就永远卡在 0。剩余量由服务端记（本 dict），每掷出一次扣一发。
_weaponAmmoLeft = {}


def weaponAmmoLeft(weapon_tid):
    """当前剩余弹药。没消耗过就等于表值（``_throwAmmo``）。"""
    return _weaponAmmoLeft.get(weapon_tid, _throwAmmo(weapon_tid))


def consumeWeaponAmmo(weapon_tid):
    """扣一发并返回扣完后的余量；非消耗武器（表值 0）返回 None。"""
    if _throwAmmo(weapon_tid) <= 0:
        return None
    left = max(0, weaponAmmoLeft(weapon_tid) - 1)
    _weaponAmmoLeft[weapon_tid] = left
    return left


def currentWeaponTid(heroId=None):
    """**当前手持**武器的 tid；查不到返回 0。

    ⚠️ 2026-09-28 补：必须把 ``_VIRTUAL_WEAPONS``（器械虚拟武器）也算进来。
    原来只遍历名册 ``HERO_BATTLE_LOADOUTS``，而虚拟武器是**另一张表**
    （``setVirtualWeapon`` 只写 ``_VIRTUAL_WEAPONS``）⇒ 上车时 ``_CURRENT_SLOT``
    被设成 9，名册里只有槽 1/2/3，**循环找不到槽 9 就返回 0**。

    实证代价（会话 ``6800-706576879``）：投石车上车完全成功
    （``cmd=18 sel=3 target=10014 state=6001`` + 事件 ``1001,1012``、
    ``cmd=5 sel=3`` 里含 ``tid=29001 slot=9``），客户端也把左键按下报上来了
    （``battle-raw key_comb=141 msg_id=300010``），但
    ``siege.onCatapult()`` 读到的却是 **0 ≠ 29001** ⇒ 蓄力链整条不触发。
    离线复现：``setVirtualWeapon(9, 29001); setCurrentSlot(9)`` 之后
    本函数返回 **0**，而 ``battleLoadout()`` 里明明有 ``(9, 29001, 1)``。

    **纯加法**：``_CURRENT_SLOT`` 是 1/2/3（正常换武器）时第一段就返回了，
    新增分支根本不会走到 ⇒ 对既有链路逐位不变。
    """
    if heroId is None:
        heroId = _ACTIVE_HERO
    held, _mount = HERO_BATTLE_LOADOUTS.get(heroId, ((), 0))
    for entry in held:
        if entry[0] == _CURRENT_SLOT:
            return entry[4]
    for slot, tid in _VIRTUAL_WEAPONS.items():
        if slot == _CURRENT_SLOT:
            return tid
    return 0


def _bulletInfo(weapon_tid):
    """WEAPON_BULLET_INFO 12B —— 与 ``vision_flow.encode_weapon_use_record`` 同一套
    已被实机证明的填法（开局 HUD 显 5 走的就是它）：consumed_type 保持 0，
    stack/charger/package 填当前余量，max 两栏填表值。

    之前 USE_UPDATE 里 charger 恒发 0 是这轮「按 2 直接变 0 + 弹药不足」的头号
    嫌疑：投掷门禁看弹夹余量（``bullet_left_in_charger``），换武器重读数据时
    读到 0 就把武器判空了。
    """
    maxn = _throwAmmo(weapon_tid)
    left = weaponAmmoLeft(weapon_tid)
    if not maxn:
        return bytes(12)
    return struct.pack(">bhhbhhh",
                       0,      # consumed_type —— 与 vision 通道一致，别改
                       left,   # stack_count（HUD 显示值）
                       maxn,   # max_stack_count
                       1,      # weapon_need_bullet
                       left,   # bullet_left_in_charger
                       left,   # bullet_left_in_package
                       maxn)   # max_bullet_left_in_package


def weaponBulletUpdate(weapon_tid):
    """``CS_PROTO_WEAPON_BULLET_UPDATE``（cmd=5 sel=4）：i32 weapon_id + 12B 弹药。"""
    return struct.pack(">Hi", 4, weapon_tid) + _bulletInfo(weapon_tid)


def allWeaponsUsing():
    """全部已装备武器是否都标 ``is_using=1``。**env > ini > 关**。"""
    raw = os.environ.get("T7_ALL_WEAPONS_USING")
    if raw is not None and str(raw).strip():
        return str(raw).strip().lower() in ("1", "on", "true", "yes")
    return _battleIniSwitch("all_weapons_using", "all_using")


def shieldDefenceSwitch():
    """盾牌防御（服务端按 ``key_comb`` 的 KEY_SHIELD 位驱动状态）。**env > ini > **开**。

    ⚠️ 默认 **开** —— 与其他开关（默认关）不同：用户明确报「按 3 切盾牌后不是防御状态」，
    这是修 bug 而不是加实验能力。要 A/B 回退用 ``T7_SHIELD_DEFENCE=off``。
    """
    raw = os.environ.get("T7_SHIELD_DEFENCE")
    if raw is not None and str(raw).strip():
        return str(raw).strip().lower() in ("1", "on", "true", "yes")
    return _battleIniSwitchDefaultOn("shield_defence", "shield")


def _battleIniSwitchDefaultOn(*keys):
    """同 ``_battleIniSwitch``，但**没填时算开**（只给「默认开」的开关用）。"""
    for key in keys:
        value = str(_BATTLE_INI.get(key, "")).strip().lower()
        if value:
            return value in ("1", "on", "true", "yes")
    return True


# 当前手持武器槽（1/2/3）。is_using 的官方语义是「**只有手里这把** =1」：
# 2026-09-25 实机 A/B —— 三把全 1 时按 2/3 客户端报「无法切换武器」，
# 全 0/只 1 时静默不换（缺 USE_UPDATE，见 ``weaponUseUpdate``）。
_CURRENT_SLOT = 1


def currentSlot():
    return _CURRENT_SLOT


def setCurrentSlot(slot):
    global _CURRENT_SLOT
    _CURRENT_SLOT = slot


# --- 器械「虚拟武器」临时注入（2026-09-28，投石车按 C 上车） --------------------
# 为什么需要它：客户端是**按人物手持武器的类型**去挑人物动作状态表
# （``propsheet/<武器类型名>.psheet``，46/80 个类型有同名文件）。投石车那张表
# ``投石车.psheet`` 的 `待机` 指向 ``Battle\Catapult\武将_投石车_待机``，而那棵树
# 的「首次进入」节点里才是 ``挂接器械(挂点名称="Man")`` —— 也就是「绑人」那一刀。
# 手持刀剑时这张表根本不会被查到 ⇒ MO 组的 CONTROL_ON(4) / ACTOR_CONTROL_NTF(27)
# / update_state(6001) 发得再齐也不上车（2026-09-28 wire 实证：三条全发到位、
# 客户端零上行、人物照常走路）。
#
# 投石车的武器 tid 出处：客户端武器模板表（``data1.vfs`` 块 **125289** 起，行宽
# **2829**、**1016** 行，**行首 u32 = 武器 tid**，其后是 名字|武器类型名|类别|模型名|…）：
#   tid=**29001**  投石车 / 投石车 / 攻城 / toushiche.png / 武器_工程.工程
#   同族：29002=云梯、30004 与 30008=弩机；另有 49001..49004 四个同内容变体
#        （差异只在按键提示与两个小整数，疑似不同势力/关卡版本）。
# 校验：同表内 1030411=龙牙刀、1080311=飞戟、1080511=竹烟筒 与
# ``data/hero/hero_roster.json`` 逐条吻合 ⇒ 「行首 u32 = tid」成立。
# 读表脚本（本次新建，工作区）：``vfs_path.py``（命名池→run→块）、
# ``vfs_find_weapon_tbl.py``（按已知 tid 定位表）。
#
# ⚠️ 注入的武器必须**一起进** ``WEAPON_USE_UPDATE`` 的条目表 —— 客户端按那张表决定
#    哪把武器被 enable。所以走「临时槽位」而不是改写玩家原有槽位，退出时
#    ``clearVirtualWeapons()`` 还回去（``cat_weapon`` 关掉即完全无副作用）。
_VIRTUAL_WEAPONS = {}


def setVirtualWeapon(slot, tid):
    """把一把器械类虚拟武器临时挂到 ``slot``（进 loadout / USE_UPDATE / 视野包）。"""
    _VIRTUAL_WEAPONS[int(slot)] = int(tid)


def clearVirtualWeapons():
    _VIRTUAL_WEAPONS.clear()


def virtualWeapons():
    """``((slot, tid)…)``，按槽位升序。"""
    return tuple(sorted(_VIRTUAL_WEAPONS.items()))


def changeWeaponRspBody(right_tid, left_tid=0, err_no=0):
    """``CS_PROTO_CHANGE_WEAPON_RSP``（cmd=5 / sel=2）body：``selector u16 大端=2``
    + ``err_no i32 + right_hand_weapon_tid i32 + left_hand_weapon_tid i32``（共 12B）。

    与 ``app.py`` 里按 1/2/3 那条链逐字节同构（那条已 2026-09-25 实机确认可用），
    这里只是把「要回哪把 tid」的参数化出来，给器械注入用。
    """
    return struct.pack(">Hiii", 2, int(err_no), int(right_tid), int(left_tid))


def battleLoadout(heroId):
    """战斗侧武器列表 ``((武器槽位, 武器tid, 是否使用中, 弹药数)…, 坐骑tid)``。

    只喂 **VISION actor** 和 **instance ACTOR_BASIC_INFO** 那两条通道（字段有
    ``sh_proto_cs_metas`` 依据）。出战本体容器 ``CS_BATTLE_HERO_DEF`` 那一格仍留 0 ——
    它的武器条目是 106B/条、要装整份武器信息，见 ``battleHeroes()`` 的说明。

    「使用中」只给**当前手持槽**（``currentSlot``，默认 1）那一把，换武器就是换谁
    using。名册里查不到这张卡就给空列表 —— 宁可空手，也不要再写死一把不属于它的
    龙牙刀。第 4 位「弹药数」只对投掷/消耗武器非 0（见 ``_throwAmmo``），近战恒 0。

    ``is_using``：客户端消费者 ``0x0089AC90`` 用它决定**这把武器有没有被 enable**
    （``!=0`` 走 holder vtable ``+0x1C`` 取用/启用，``==0`` 走 ``+0x24`` 非使用、
    永不启用）。``T7_ALL_WEAPONS_USING=1`` 让全部已装备武器都 ``is_using=1`` ——
    实机已证这是**错**语义（按 2/3 报「无法切换武器」），只留作反例复现用。
    """
    held, mount_tid = HERO_BATTLE_LOADOUTS.get(heroId, ((), 0))
    # ⭐ 2026-09-28：把临时注入的器械类虚拟武器一起排进去（默认空 ⇒ 逐字节不变）。
    #    6 元组与名册同构：(武器槽位, set下标, 装备组号, 组内序号, tid, 锁定态)；
    #    装备组号填 0 ⇒ ``_isMountGroup`` 为假、不会被当坐骑。
    held = tuple(held) + tuple((slot, 0, 0, 0, tid, 1)
                               for slot, tid in _VIRTUAL_WEAPONS.items())
    ordered = sorted(held, key=lambda entry: entry[0])
    all_using = allWeaponsUsing()
    using_slot = _CURRENT_SLOT
    return (tuple((entry[0], entry[4],
                   1 if (all_using or entry[0] == using_slot) else 0,
                   _throwAmmo(entry[4]))
                  for index, entry in enumerate(ordered)), mount_tid)


def weaponUseUpdate(heroId=None):
    """``CS_PROTO_WEAPON_USE_UPDATE``（command=5 / selector=3，S→C）。

    换武器链里 RSP 只是「准许」，客户端**真正执行切换**靠这一包（2026-09-25 实机：
    只发 RSP 时按 2/3 静默不换）。

    布局（TDR 权威 + 2026-09-28 拿真帧逐字节复核，**已闭合**）：
    ``selector u16 | rid u64 | weapon_num i16 | 条目 29B × N | is_change_state i16
    | cur_svr_time u64`` ⇒ 长度 = ``22 + 29N``。
    复核方法：取本会话 S2C 真帧 ``offset=598395 wireLength=161``，body 从第 23 字节
    起 = **138** B（N=4），与我方 ``weaponUseUpdate()`` 输出逐字节比：
    除尾部 8B 时间戳外只有 **6 个字节**不同，全落在条目 1/2 的 ``bullet`` 段
    （偏移 50/55/57/79/84/86）—— 那是开局弹药余量差异，不是结构差异。
    尾部实测 ``is_change_state = 0x0000``。

    ⚠️ TDR 里 ``CS_PROTO_WEAPON_USE_UPDATE`` 的内存布局是
    ``rid u64 | ACTOR_WEAPON_USE_DATA(727B) | is_change_state i16 | cur_svr_time u64``
    （= 745B），其中 ``ACTOR_WEAPON_USE_DATA`` = ``weapon_num i16 + WEAPON_USE_DATA[25]×29B``。
    **745 是内存上限，不是线上长度** —— 线上按 ``weapon_num`` 变长，别照 745 补零。
    单条 29B = ``tid i32 | slot i8 | upgrade i8 | is_using i8 | bullet 12B | bm 10B``；
    bullet 12B = ``consumed i8 | stack i16 | max i16 | need i8 | charger i16
    | package i16 | max_package i16``。

    ``is_change_state`` 恒填 0。**语义未验证**（名字像「这次换武器要不要连带改人物
    状态」）—— 已实测的另一条路是「不发它、另发一对 ``STATE_SYNC(1)→(2)``」，
    那条在按 2/3 换武器与投石车上车都走通了（见 ``siege.pushStateChange``）。
    所以这一位先不动；真要试就单开一轮把它填 1。
    rid 填玩家 actor 的 1（prior art 样本里玩家 rid=1）。

    ⚠️ **只发这一包不够**：客户端换完武器不会自己去查新武器那张
    ``propsheet/<武器类型名>.psheet`` —— 得靠一次**状态迁移**逼它重进状态树。
    """
    if heroId is None:
        heroId = _ACTIVE_HERO
    weapons = battleLoadout(heroId)[0]
    out = bytearray(struct.pack(">HQh", 3, ACTOR_ID, len(weapons)))
    for slot, tid, is_using, ammo in weapons:
        out += struct.pack(">ibbb", tid, slot, 0, is_using)
        out += _bulletInfo(tid)
        out += bytes(10)
    out += struct.pack(">hQ", 0, serverNowMs())
    return bytes(out)


# ⚠️ 启动打一行，把**生效的关卡和它的来源**说清楚。
# 2026-09-19 两次实机翻车都是「配置没生效但看不出来」：
# 第一次 level=10036（环境变量没进进程），第二次还是 10036（服务端没重启）。
# 这一行让「到底读没读到 level.ini」不再靠猜。
# ⚠️ 必须放在 POSITION 之后——放在前面会 NameError（踩过）。
print("[contracts] level=" + str(LEVEL_ID)
      + " source=" + _levelSource
      + " scene=" + str(SCENE_BY_LEVEL.get(LEVEL_ID, ""))
      + " spawn=" + str(POSITION)
      + " echoMode=" + _LEVEL_ECHO
      + " echo=" + str(RESOURCE_ID)
      + " pattern=" + str(PATTERN_BY_LEVEL.get(LEVEL_ID))
      + " mode_a=" + str(modeA())
      + (" tutorial" if isTutorial() else " match"), flush=True)
# 同理，解锁名册到底读没读到也得看得见（读不到就退回单卡，界面上一片灰，
# 没有这一行根本分不清是「代码没生效」还是「文件没找到」）。
print("[contracts] hero_roster=" + _HERO_ROSTER_SOURCE
      + " cards=" + str(len(HERO_CARDS))
      + " souls=" + str(len(HERO_SOULS))
      + " contLimit=" + str(HERO_CONTAINER_LIMIT)
      + " formation=" + str([c[0] for c in HERO_BATTLE_FORMATION])
      + " battleHeroes=" + str(HERO_IDS)
      + " weapons=" + str({k: len(v) for k, v in HERO_CARD_WEAPONS.items()})
      + " path=" + HERO_ROSTER_PATH
      + " exists=" + ("yes" if os.path.isfile(HERO_ROSTER_PATH) else "no"), flush=True)
# 战斗侧那一份也必须看得见：进图手里拿什么、有没有马，全看这一行。
# ⚠️ all_using / throw_ammo 是「1/2/3 切不过去」这条链的启用位与弹药，
#    曾因只读 env（进不了 launcher 进程）而静默失效；source= 指出它到底从哪生效。
print("[contracts] battle loadout="
      + str({tid: (len(battleLoadout(tid)[0]), battleLoadout(tid)[1])
             for tid in HERO_IDS})
      + " (武器条数, 坐骑tid)"
      + " player_hero=" + str(HERO_ID)
      + "/槽" + str(len(battleLoadout(HERO_ID)[0]))
      + " slots=" + str([entry[0] for entry in battleLoadout(HERO_ID)[0]])
      + " all_using=" + ("on" if allWeaponsUsing() else "off")
      + " throw_ammo=" + str(_throwAmmo(1080311)) + "/" + str(_throwAmmo(1080511))
      + " cc_res=" + str(CC_LANDING_RES_ID)
      + " hp_now=" + str(HP_NOW)
      + " actor_gravity=" + str(ACTOR_GRAVITY)
      + " ini_section=" + ("present" if _BATTLE_INI else "absent"), flush=True)

# --- 按「点到的卡片」自动定图（2026-09-19，用户要求） -----------------------------
# 之前服务端只看 level.ini 里手填的 T7_LEVEL，**不管客户端点的是哪张卡**。
# 于是「配了宛城却点了攻城」→ 发的还是宛城 → 客户端卡在载入地图。
# 用户要求：点宛城之战卡片就只能载入宛城地图；点攻城就只有樊城；洛阳同理。
#
# 做法：pattern_level_map.csv 是权威的 card ↔ level 映射，反查即可。
# 房间创建时拿客户端带的 resource_id 反查 level，真的变了才切。
LEVEL_BY_PATTERN = {}
for _lv, _cards in PATTERNS_BY_LEVEL.items():
    for _card in _cards:
        LEVEL_BY_PATTERN[_card] = _lv


def resolveLevel(card) -> int | None:
    """客户端点的卡片 → 关卡号；不认识的卡返回 ``None``。"""
    try:
        return LEVEL_BY_PATTERN.get(int(card))
    except (TypeError, ValueError):
        return None


def applyLevel(levelId, card=None) -> bool:
    """把**当前生效的关卡**切到 ``levelId``（由点到的卡片驱动）。

    ⚠️ **只在关卡真的变了时才动**：洛阳默认路径（客户端发 1028 → 10036，
    和当前关卡相同）**不会触发任何赋值**，所以「不切图时逐位不变」这条
    硬底线仍然成立。

    切的时候三样东西必须一起走，否则会自相矛盾：
      * ``RESOURCE_ID`` —— 客户端拿它加载地图，**就用点到的那张卡**；
      * ``POSITION / ENEMY_POSITION`` —— 出生点，不然会生在图外/地下；
      * 空气墙场景 —— 由调用方写 ``flow.session["scene"]``。

    返回 ``True`` 表示真的切了（调用方要打日志）。
    """
    global LEVEL_ID, LEVEL_CUSTOM, RESOURCE_ID, POSITION, ENEMY_POSITION
    if levelId is None or levelId == LEVEL_ID or levelId not in SPAWN_BY_LEVEL:
        return False
    LEVEL_ID = levelId
    LEVEL_CUSTOM = True
    POSITION, ENEMY_POSITION = SPAWN_BY_LEVEL[levelId]
    # ⭐⭐ 2026-10-01（第十五轮）：**跟着图换将**。
    # 用户要「点弓箭手训练就用弓箭系的将」。这里把当前武将同步成
    # ``HERO_BY_LEVEL[levelId]``；没配这张图 / 本来就是同一个人 → ``False``、不吭声。
    # ⚠️ 必须在 ``applyCavalrySpawn`` 之前 —— 它读 ``heroHasMount(heroId)``
    #    判断这张图要不要换骑兵点（姜维带马、赵云不带）。
    setHeroForLevel(levelId)
    if _appliedCampSpawn is not None:
        # 切图会先把两边重置成 [0]/[1]，这里按**已知的 camp** 重新选边
        # （``instance-camp-choose`` 早于 ``match-start`` 的情况）。
        applyCampSpawn(_appliedCampSpawn[1])
    # 卡片是本图的合法入口就用卡片本身（排位赛樊城 60005 也是樊城，
    # 但和 20001 不是同一张卡，客户端认的是它点那张）。
    # ⭐⭐ 2026-10-01：`card` 也可能是**客户端实际发的卡号**（钉住通道传来的），
    #    即便它不在 `PATTERNS_BY_LEVEL` 里也要**回显它** —— 客户端只认自己发出去的
    #    那个 resource_id，回一个 level_id 它加载不了（模式 1 教学模式尤其明显）。
    #    ⇒ 优先级：本图登记的入口卡(同 card) > 客户端发来的卡号 > 本图主卡 > level_id。
    if card is not None and (card in PATTERNS_BY_LEVEL.get(levelId, ())
                             or card not in PATTERN_BY_LEVEL):
        RESOURCE_ID = card
    else:
        RESOURCE_ID = PATTERN_BY_LEVEL.get(levelId, levelId)
    return True


# --- 运行期快照（热重载）-----------------------------------------------------------
# 「当前关卡 / 出生点 / 生效武将 / 手持武器槽」都是**模块全局**，而宿主热重载
# （``Runtime.prepare()`` + ``switch()``）会重建模块 ⇒ 全局回到启动默认值，
# 但宿主管的 ``state``（会话）还留着原场景 ⇒ 两边自相矛盾：
# 关卡从 10085 退回 10036、武器槽从 3 退回 1，而会话仍在原场景里。
#
# 修法：这些值**以会话为准**。``Flow`` 建立时用会话里存的快照灌回模块全局，
# 事件处理结束再把全局写回会话 —— 改这些全局的地方一个都不用动。
def runtimeSnapshot():
    """当前运行期快照。字段都是宿主 ``state`` 能编码的标量。"""
    return {"levelId": LEVEL_ID, "levelCustom": bool(LEVEL_CUSTOM),
            "resourceId": RESOURCE_ID, "heroId": _ACTIVE_HERO,
            "weaponSlot": _CURRENT_SLOT,
            "campSpawnCamp": None if _appliedCampSpawn is None else _appliedCampSpawn[1]}


def restoreRuntime(snapshot):
    """把会话里的快照灌回模块全局。返回 ``False`` 表示快照不可用、保持现状。

    逐字段校验：任何一项类型/范围不对就整体不采用（宁可退回启动默认，
    也不要用半份脏数据把出生点或武器槽改坏）。
    """
    global LEVEL_ID, LEVEL_CUSTOM, RESOURCE_ID, POSITION, ENEMY_POSITION
    global _ACTIVE_HERO, _CURRENT_SLOT, _appliedCampSpawn
    if type(snapshot) is not dict:
        return False
    levelId = snapshot.get("levelId")
    resourceId = snapshot.get("resourceId")
    if type(levelId) is not int or levelId not in SPAWN_BY_LEVEL:
        return False
    if type(resourceId) is not int or not 0 < resourceId < (1 << 31):
        return False
    LEVEL_ID = levelId
    LEVEL_CUSTOM = bool(snapshot.get("levelCustom", True))
    RESOURCE_ID = resourceId
    POSITION, ENEMY_POSITION = SPAWN_BY_LEVEL[levelId]
    heroId = snapshot.get("heroId")
    if type(heroId) is int and heroId > 0:
        _ACTIVE_HERO = heroId
    slot = snapshot.get("weaponSlot")
    if type(slot) is int and 1 <= slot <= 9:
        _CURRENT_SLOT = slot
    camp = snapshot.get("campSpawnCamp")
    # 先清空再交给 ``applyCampSpawn`` —— 它是幂等的（同 (关卡, camp) 直接返回
    # False），自己赋值会让它以为「已经应用过」而跳过 ``_recomputeSpawn()``。
    _appliedCampSpawn = None
    if type(camp) is int and camp in (1, 2):
        applyCampSpawn(camp)
    return True


# ❌ 2026-09-27 **已作废并删除**：此处曾短暂加过一个模块级标记 `_matchDecided`
#    （「随机档下本局图已由 match-start 定下 ⇒ 后续 room-create 不许覆盖」）。
#    **删掉的原因：它防的是一个不存在的威胁，而且有害。** 记录在此以免以后再踩：
#
#    误判过程：会话 `13496-335497367` 里看到
#      recordId=1588  15:49:29.855  event=221007  match-start hits=[(28,1003)] → 10005
#      recordId=3256  15:50:48.280  event=221778  room-create client=29       → 10085
#    就当成「match-start 定图后被 room-create 覆盖」。**错在两处**：
#      ① 两条记录相隔 **78.4 秒**，且 event id 不同（221007 → 221778）⇒ 是**两次独立操作**；
#      ② 那次 room-create 是玩家退回大厅点了「宛城之战教学」（`mode_a=1`，
#         本来就该走建房直进），不是同一局的后续。
#
#    全量复核（`data/*/wire/frames-1.jsonl` **全部**会话）：
#      `match-start` 之后出现 `room-create` 的总共 **只有 1 次** —— 就是上面那次 78.4 秒的；
#      **5 秒内 0 次**。真实链路是 `match-start` → `cmd=1 sel=5` → `room-enter-sent`，
#      **中间没有 room-create**。
#
#    ⇒ 守卫若留着：随机档下玩家退回大厅换图时 `applyCard(29)` 会被拦掉 ⇒
#      服务端停在旧图、客户端期望新图 ⇒ **卡死**。
#    ⇒ 现在 `applyCard` 对**任何来源**的 card 一视同仁，与 `matchRandomEnabled()` 无关。


def applyCard(card) -> str:
    """让「当前生效的关卡/地图 ID」跟**客户端点到的那张卡**一致。

    返回 ``"switch"``（换了图）/ ``"card"``（同图换了入口卡）/ ``""``（没变）。

    为什么要分两种情况：单张图可能有**多个入口卡**（樊城：普通 20001、
    排位赛 60005/61005；洛阳：实战 1028、高级 18111）。只判「关卡变没变」
    不够 —— 先点 60005 再点 20001，关卡都是 10002 不会触发切换，
    ``RESOURCE_ID`` 会停在 60005，而 room-create 回 20001 → 又两个地图 ID。
    """
    global RESOURCE_ID
    levelId = resolveLevel(card)
    if levelId is None:
        return ""
    if levelId != LEVEL_ID:
        return "switch" if applyLevel(levelId, card) else ""
    if card in PATTERNS_BY_LEVEL.get(levelId, ()) and card != RESOURCE_ID:
        RESOURCE_ID = card
        return "card"
    return ""


def sceneFor(levelId=None) -> str:
    """关卡 → 空气墙场景目录；不认识的关卡返回空串（不碰撞）。"""
    return SCENE_BY_LEVEL.get(LEVEL_ID if levelId is None else levelId, "")


# The deployed human baseline extended selection to 20 minutes on September 13.
# --- 回合时长（2026-09-21 还原为 90 分钟） -----------------------------------
#
# ⭐⭐ **GAME_MS 必须是 5400000（90 分钟），不要改成 24 小时。**
#
#    2026-09-20 曾按「解除时间限制」的诉求把默认值改成 86400000（24h）。
#    2026-09-21 实机取证（抓包 ``data/12980-18697525/wire/frames-1.jsonl``）
#    证明这是一个**回归**，而且同时造成两个现象：
#
#      ``instance-round-state``（cmd=10 sel=0x67）实际发出的载荷是
#        ``sel(2) + now(u64) + 1(u64) + cur(i32) + now(u64) + dur(u32) + 0(i32)``
#      —— 共 42 字节（``contracts.roundState``），其中 ``dur``
#      **原样透传到客户端**，没有任何换算：
#
#        after-auth : cur=2  dur=86400000   ← 进图第一帧
#        prepare    : cur=2  dur=30000      ← 客户端 load-ok 之后的 30 秒
#        start      : cur=3  dur=5000
#        game       : cur=4  dur=86400000   ← 开局
#        end        : cur=5  dur=0
#
#      · 右上角「游戏时间」显示 ``00:00``
#        = ``dur=86400000`` 被客户端按 **int32 毫秒**吃：客户端拿它当
#        "已过时长" 或拿 ``remaining = dur - elapsed`` 去格式化，24h 超出
#        它 UI 的 HH:MM 表达范围 → 直接渲染成 ``00:00``。
#      · 准备倒计时显示汉字「**九**」且数字不动
#        = ``after-auth`` 那一帧的 ``cur=2``（准备态）配 ``dur=86400000``
#        **先于** ``prepare`` 帧到达。客户端按 24h 算准备倒数：
#        86400000 ms = 2880 分钟 = **9 位数**，它的倒计时 UI 只画得出首位
#        「九」；而且基准大到本局永远走不完 → 看起来就是「卡那不动」。
#
#      ⇒ 两处症状是**同一个原因**，改回 90 分钟一起消失。
#        ``prepare`` 帧的 30000 本来就是对的，不用动。
#
# ⭐⭐⭐ 2026-09-21（第十四轮，**纠正上一轮的错误结论**）：
#    ``now`` 字段**必须**是 epoch 毫秒，脚本传什么就发什么，**没有被原生层改写**。
#
#    上一轮写「原生层改写了 now」是**误判**。当时的证据是
#    roundState 里发出 ``20167243`` 而同事件 ``Flow.now = 20167686``，差 443 ms，
#    于是推断"被改写"。**但 443 ms 其实就是脚本事件到 send 的正常延迟** ——
#    本轮实测差值只有 67 ms（``monotonicUs/1000 = 21306484`` vs 发出 ``21306417``），
#    即 ``Flow.now`` 原样透传。**结论：``now = context["nowMs"]`` 是单调时钟，
#    必须换成 epoch。**
#
#    症状链（两次抓包对齐后唯一能解释全部现象的一条）：
#      ``now`` 是 tconnd 进程运行毫秒（实测 ~21306417 ms ≈ 5.92 h）。
#      客户端把 ``now`` 当成"本局时间基准"去和 epoch 做差 ⇒ 算出 1970 年附近
#      ⇒ 右上角「游戏时间」恒显 ``00:00``（与 GAME_MS 取 86400000 还是 5400000 无关）。
#      同一个错基准喂给倒计时控件 ``Controls.Base.TimeCountDown`` 时，
#      ``21306417 / 21600 (6h) = 986.4`` ⇒ 显示 **987**；
#      而该控件在窄版面上只画得出首位数字 ⇒ 显示汉字「**九**」。
#      （这就解释了「24h 时显示九、90min 时显示 987」这个看似矛盾的现象：
#        ＊两者与 GAME_MS 无关＊，987 是同一个值，只是排版宽度不同。）
#
#    修法见文件下方 ``serverNowMs()``：所有发给客户端的时间字段统一走它。
#
# ✅✅ **已实机闭合（2026-09-21 11:0x，用户确认「现在时间正常了」）**
#    这一条不再是推断：把 ``now`` 换成 epoch 毫秒之后，游戏时间显示正常、
#    倒计时不再显示「九」/「987」。
#    改动范围（改完必须全量扫一遍，**别再有漏网的**）：
#      · ``contracts.py`` 里所有发报文的封装函数 —— ``now`` 形参一律忽略，
#        内部强制 ``serverNowMs()``；
#      · ``scene.py`` 直接 struct.pack 的地方（cmd=8 / 0x36 / 0xA）；
#      · ``ccobject.py`` 的 ``visionEvent*`` —— 由调用点传值，两个调用点已改；
#      · ``mo.py`` 的三处 ``state_change_ms``（2026-09-21 最后一处，见 ``mo.py`` 顶部说明）。
#    ⚠️ **没有**问题的（别误改）：``controls.py`` 的 ``flow.now`` 全是内部
#    定时器/步进，不下发；``battle.py`` / ``scene.py`` 里 ``flow.now - startedAt``
#    是**相对时长**，本来就该用单调时钟。
#
# 想临时覆盖：``set T7_GAME_MS=<毫秒>``。
def _envMs(name, default):
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


PREPARE_MS = _envMs("T7_PREPARE_MS", 30000)
START_MS = _envMs("T7_START_MS", 5000)
GAME_MS = _envMs("T7_GAME_MS", 5400000)
GROUND_STEP_DISTANCE = 0.25
GROUND_STEP_MS = 50


# --- ⭐ 发给客户端的「服务器时间」统一入口（2026-09-21） ----------------------
#
# 背景见文件上方 505-547 行：``Flow.now`` 是原生层单调时钟，**不能**直接发给
# 客户端当服务器时间。所有需要"当前时间"的报文字段一律走本函数。
#
# 口径与 ``app.epochMs()`` **保持一致**（同一个 ``_hall_time.txt``）：
#     空 / ``now`` / ``real`` → 真实时间
#     ``HH:MM``              → 今天的 HH:MM（本地时区，和客户端判窗口同口径）
#     ``<整数>``              → epoch 秒(10位) 或毫秒(13位)
#
# ⚠️ 但不 import app（会循环依赖：app → scene → contracts）。
#    这里复刻一份读取逻辑，**任何改动都要和 app.epochMs() 同步**。
#
# ⚠️ 与 ``app._readHallTimeCfg`` 一样**只**按 ``__file__`` 上溯，不写本机绝对路径
#    （``AGENTS.md`` §3「信息自包含」）。
def _hallTimePath():
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    cands = []
    p = here
    for _ in range(4):
        p = os.path.dirname(p)
        cands.append(os.path.join(p, "_hall_time.txt"))
    return cands


def serverNowMs():
    """客户端认的「服务器 epoch 毫秒」。**不要传 Flow.now。**"""
    # ⭐ 2026-09-21（第十五轮）：优先复用 ``app.epochMs()``，保证与
    # ``unify-time-rsp``（cmd=8 主时钟）**完全同源**。
    # 两处实现若各写一份，将来改 ``_hall_time.txt`` 只改一边就会出现
    # 「periodic-time-sync 与 unify-time-rsp 时间不一致」⇒ 客户端倒计时反复重置
    # ⇒ 滴答音效一直响。这里用 ``sys.modules`` 取，不 import，避免
    # app→scene→contracts 的循环依赖。
    try:
        import sys as _sys
        _app = _sys.modules.get("scripts.app") or _sys.modules.get("app")
        if _app is not None and callable(getattr(_app, "epochMs", None)):
            return _app.epochMs()
    except Exception:
        pass
    import os
    import time
    for c in _hallTimePath():
        try:
            with open(c, "r", encoding="utf-8") as fp:
                raw = fp.readline().strip()
        except OSError:
            continue
        if raw:
            break
    else:
        raw = ""
    if not raw or raw.lower() in ("now", "real"):
        return int(time.time() * 1000)
    if ":" in raw:
        try:
            hh, mm = (int(x) for x in raw.split(":", 1))
        except ValueError:
            return int(time.time() * 1000)
        lt = time.localtime()
        return int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday,
                                hh, mm, 0, 0, 0, -1))) * 1000
    try:
        value = int(raw)
    except ValueError:
        return int(time.time() * 1000)
    return value * 1000 if value < 10000000000 else value


def exact(body, length, label):
    if type(body) is not bytes or len(body) != length:
        raise ValueError(f"{label} requires exactly {length} bytes")


def roundState(now, current, duration):
    """回合状态（cmd=10 sel=0x67）。

    ⚠️ 实机档忽略 ``now``、强制用 ``serverNowMs()``（epoch 毫秒）。契约档
    （``contract_mode()``）按上游合成骨架用传入的 ``now``，以便 vendored 参考套件
    逐字节对齐。

    为什么实机档忽略 ``now``：调用点遍布 ``scene.py``（约 8 处），传的全是
    ``flow.now``（原生单调时钟）。逐个改容易漏，且以后新增调用点还会踩同一个坑。
    在这里**一处收口**最稳：实机只要 epoch，不接受单调时钟。

    载荷 42 字节：``sel(u16) + now(u64) + 1(u64) + cur(i32) + now(u64) + dur(u32) + 0(i32)``
    """
    stamp = now if contract_mode() else serverNowMs()
    return struct.pack(">HQQiQQi", 0x67, stamp, 1, current, stamp, duration, 0)


def roundInfo(now):
    """回合信息（cmd=10 sel=0x66）。``now`` 同样被忽略，内部走 epoch。"""
    stamp = serverNowMs()
    return (struct.pack(">HQQiiiiiQQiii", 0x66, stamp, 1,
                        1, 0, 0, 0, 0, stamp, 0, 0, 0, 2)
            + struct.pack(">iQiQi", 3, 0, 4, GAME_MS, 0))


def instanceInfo(now, startedAt=None):
    """实例信息（``instance-minimal-update``）。``now`` 忽略，走 ``serverNowMs()``。

    这条报文里有两个**语义不同**的时间字段，不能混用：

      * ``server_time_ms``         —— 「现在」，每次刷新都该往前走；
      * ``instance_start_time_ms`` —— 「本局起点」，**整局固定不变**。

    两个字段都写 ``serverNowMs()`` 会让每次刷新都告诉客户端「本局刚刚开始」，
    而移动、战斗状态仍按原起点算 ⇒ 时间轴不一致（倒计时重置、状态机错位）。
    所以 ``startedAt`` 必须采用：由 ``scene.begin()`` 落一次进
    ``session["instanceEpochMs"]``（**epoch** 毫秒），之后刷新复用同一个值。

    ⚠️ 别拿 ``session["instanceStartedAt"]`` 顶替：那是**单调时钟**、只用于本地
    差值，发到线上客户端会算出 1970。
    """
    stamp = serverNowMs()
    start = stamp if startedAt is None else int(startedAt)
    return room_flow.encode_minimal_instance_update(
        server_time_ms=stamp, instance_id=1,
        instance_start_time_ms=start,
        resource_id=RESOURCE_ID, start_pattern=START_PATTERN)


def actorInfo(now, camp, playerName=USER_NAME, withMount=True, heroId=None):
    """实例 actor 基本信息（``0xA``）。

    ``now`` 忽略，统一走 ``serverNowMs()``（``flow.now`` 是原生层单调时钟，
    发到线上客户端会算出 1970）。

    ``playerName`` 是**第 3 个位置参数**（与上游同序）：昵称由宿主注入、经
    ``state["playerName"]`` 传下来，不是常量。``level`` 恒取 ``USER_LEVEL``，
    与大厅登录、视野 actor 同源。

    ``withMount=False`` 把 ``mount_tid`` 抹成 0（选将还没定下来时不发骑兵信号，
    见 ``actorVision()`` 同名参数）。
    """
    if heroId is None:
        heroId = _ACTIVE_HERO
    weapons, mount_tid = battleLoadout(heroId)
    return room_flow.encode_instance_update_actor_basic_info(
        server_time_ms=serverNowMs(), instance_id=1, actor_mid=ACTOR_ID, user_id=USER_ID,
        user_name=playerName, user_image_id=7, level=USER_LEVEL, actor_state=4,
        hero_resource_id=heroId, camp=camp, start_pattern=START_PATTERN,
        weapons=weapons, mount_tid=mount_tid if withMount else 0)


def actorState(now, current):
    """``update_time_ms`` 是发到客户端的时间戳。

    ⚠️ 实机档忽略 ``now``、强制走 ``serverNowMs()``（epoch 毫秒）；用单调时钟会让
    客户端算出 1970。契约档（``contract_mode()``）按上游合成骨架用传入的 ``now``，
    以便 vendored 参考套件逐字节对齐。
    """
    stamp = now if contract_mode() else serverNowMs()
    return room_flow.encode_actor_update_state(
        user_id=USER_ID, actor_mid=ACTOR_ID, actor_state=current,
        update_time_ms=stamp)


# --- 坐骑视野实体 -------------------------------------------------------------------
# 原版里坐骑是**独立**的 VISION 对象（``object_type=2``），客户端按
# ``actor.mount_rid == mount.rid`` 把骑手挂上去（客户端二进制有
# ``No Mount When Set Rider`` / ``No Mount Entity When LocalHero Get on Mount``
# 这些失败分支）。prior art 的**实网样本**（一批 ADD 里 20 Actor + 1 Mount）给的坐骑是
# rid=21 / inst_id=0 / res_id=50001，且**不在** LIST 的 1..20 里。
# 这里照同一套号：21 避开玩家(1)/敌人(2)，也避开 CC 物件的 10001+（``ccobject.CC_RID_BASE``）。
MOUNT_VISION_RID = 21
# ⚠️ ``res_id`` 该用 ``s_mount_cli.bin`` 那套**坐骑表 id**（50001~50033：照夜玉狮子、
#    黄骠马、翻羽…），**不是**名册里的 6 位装备组号 —— 111xxx 是 loadout 记录
#    （prior art 明确把 1110231 过滤掉了）。这台机器上没有 ``s_mount_cli.bin``，
#    ``hero_roster.json`` 里也扫不到 5xxxx 段，所以先全图共用这个原版合法常量把链路打通；
#    武将↔坐骑的正确对应还要等那张表。
MOUNT_SCENE_RES_ID = 50001
# 坐骑实体的局内 inst_id。prior art 的实网样本给的是 0，移动族报文按这个号寻址，
# 所以视野对象和 ``MOVE_MOUNT_BC`` 的 ``target_inst_id`` 必须是同一个值 —— 放一处，
# 不让两边各写一个字面量。
MOUNT_VISION_INSTANCE_ID = 0


def heroHasMount(heroId):
    """这张武将卡是否配了坐骑（骑兵）。控制「要不要发骑乘族报文」的唯一判据。"""
    return bool(battleLoadout(heroId)[1])


def actorVision(camp, heroId=None, actorName=USER_NAME, withMount=True, runtimeMovement=False,
                position=None):
    """本地玩家视野对象（``0xE`` / ``VISION_ADD``）。

    新增 ``runtimeMovement`` 形参。客户端权威移动模式下，角色重力必须下发
    （否则客户端拿到的重力是 0，离开支撑物不往下掉 ⇒ 台阶/落差/跳跃都不成立）。
    口径与上游 ``contracts.actorVision`` 一致：``runtimeMovement=True`` ⇒
    ``gravity = RUNTIME_GRAVITY``（默认 -10000 = -10.0 m/s²）。

    ``position``：客户端权威移动模式下传**客户端上报的实时坐标**；``None`` 表示
    用 ``POSITION``。服务端权威模式必须保持 ``POSITION``，否则会把服务端算出的
    落点覆盖掉。

    ⚠️ 与上游的**唯一**分歧：上游在这里断言 ``heroId == RUNTIME_HERO_IDS[0]``
    （固定将 110001）并对 body 做偏移打补丁；本项目直接走 ``vision_flow`` 的
    ``gravity`` 形参（落点同为偏移 79），因此**任何武将**都能开客户端权威移动，
    不必把阵容锁死成单将。
    """
    # 骑兵专用出生点（默认关，见 applyCavalrySpawn）。放这里是为了三个调用点
    # 都覆盖到，且保证下发的 pos 与服务端 POSITION 始终一致；步兵路径无副作用。
    if heroId is None:
        heroId = _ACTIVE_HERO
    applyCavalrySpawn(heroId)
    if position is None:
        position = POSITION
    weapons, mount_tid = battleLoadout(heroId)
    # ⚠️ 2026-10-05：**选将还没定下来之前不要发马**（`withMount=False`）。
    #   实机取证：进图时这份包按 ini 默认将（姜维 2121）发过一次，之后玩家选了
    #   步兵（赵云 1101 / 黄忠 3012），换将那条虽然不带马了，人身边仍留着一匹
    #   空白马 ⇒ 客户端把「有马」这件事记住了，不是我们以为的「删人就删马」。
    #   坐骑视野对象（rid=21 / res_id=50001 白马）+ actor 里的 mount_tid + mount_rid
    #   三样一起抹平，步兵那三份报文就回到「根本没配坐骑」的样子。
    if not withMount:
        mount_tid = 0
    # 只有名册里带坐骑的武将（骑兵）才追加第 2 个对象；步兵这条一个字节都不变。
    mount = (vision_flow.encode_mount_vision_object(
        rid=MOUNT_VISION_RID, res_id=MOUNT_SCENE_RES_ID, position=POSITION,
        instance_id=MOUNT_VISION_INSTANCE_ID)
        if mount_tid else b"")
    return vision_flow.encode_fixed_local_actor_vision_add_event(
        camp=camp, position=position, hero_resource_id=heroId, actor_name=actorName,
        level=USER_LEVEL,
        weapons=weapons, mount_tid=mount_tid,
        mount_rid=MOUNT_VISION_RID if mount else 0, mount_object=mount,
        current_hp=HP_NOW,
        gravity=RUNTIME_GRAVITY if runtimeMovement else ACTOR_GRAVITY)


def enemyVision():
    """⛔ **已停用（2026-10-01 第二十轮）—— 没有调用点了，保留只为可复现。**

    这是从最初版本（2026-09-20 ``t7rev_f13a4bcc...``）就带进来的**写死占位敌人**：
    ``actor_mid=2 / user_id=10001 / instance_id=2 / hero_resource_id=110003（关羽）
    / camp=2 / actor_name=b"bot"``。它当初只是为了让视野里有个「第二人」把链路跑通。

    **后果**：每张图进图后，除了玩家自己还会多站一个绿甲关羽、头顶标 ``bot``。
    训练场里尤其扎眼（用户 2026-10-01 实机反馈「为啥是一个玩家、一个 NPC 两个角色」）。

    已在 ``scene.visionObjectMids()`` 里把 mid=2 从登记表移除、并删掉
    ``scene.sendVisionObject()`` 里 ``mid == 2`` 那条分支，客户端不再请求它。
    本服务端是**单机练习服**、没有服务端敌人的 AI；真的 NPC 该走关卡自己的
    CC 器械 / tutorial 通道（那两批 rid 一直在正常登记）。

    保留本函数是为了：① 老报文的可复现性（对拍用）；② 将来真要接敌人时有个起点。
    它**没有任何调用点**，也不会再自动下发。
    """
    return vision_flow.encode_fixed_local_actor_vision_add_event(
        actor_mid=2, user_id=10001, instance_id=2, hero_resource_id=110003,
        camp=2, actor_name=b"bot", position=ENEMY_POSITION)


def battleHeroes(sequence, runtimeMovement=False):
    """出战本体容器（instance ``0x29``/selector ``0x65``，每将一份再拼头）。

    ``guid`` 用大厅那张卡的**真 guid**：阵容应答 ``0x22``/4 就是靠 guid 把「阵容里
    这个人」和「本体容器里这个人」对上的，填槽位号对不上，人就直接读不出来。

    每槽写死基线 88B，再按 ``battleLoadout`` 追加 ``weapon_slot_num`` 条 106B
    ``CS_BATTLE_HERO_WEAPON_SLOT_DEF``（``{槽位 1B, 武器信息 105B}``），让 1/2/3
    换武器在客户端点亮。``CS_BATTLE_HERO_DEF`` 的单条是 106B、装整份武器信息，
    不是大厅那套 9B 引用；按 9B 填会让容器变长、客户端卡在选将（09-21 实机）。

    ``runtimeMovement=True`` ⇒ 只发 ``heroIds(True)`` 那一张卡（客户端权威移动下
    服务端不维护其他人）；默认 ``False`` ⇒ 全阵容，与改动前逐字节相同。
    """
    formation = battleFormationNow()
    if runtimeMovement:
        if contract_mode():
            # 契约档：上游合成骨架 = 固定单人 110001、无武器挂载 ⇒ 单条 116B，
            # 与 vendored 参考套件 ``len(battleHeroes(1, True)) == 116`` 对齐。
            hero = 110001
            guid = next((e[1] for e in formation if e[2] == hero), 1)
            return login_flow.encode_fixed_battle_hero_sync_item_container_response(
                sequence=sequence, position=1, guid=guid,
                hero_resource_id=hero, weapons=(), mount_tid=0)
        wanted = set(heroIds(True))
        formation = tuple(entry for entry in formation if entry[2] in wanted) \
            or formation[:1]
    bodies = []
    for slot, (_position, guid, hero) in enumerate(formation, 1):
        weapons, mount_tid = battleLoadout(hero)
        body = login_flow.encode_fixed_battle_hero_sync_item_container_response(
            sequence=sequence, position=slot, guid=guid,
            hero_resource_id=hero, weapons=weapons, mount_tid=mount_tid)
        expected = 116 + login_flow._HERO_WEAPON_ENTRY_SIZE * len(weapons)
        if len(body) != expected:
            raise ValueError("canonical BATTLE_HERO slot layout changed: "
                             + str(len(body)) + " != " + str(expected))
        bodies.append(body)
    return bodies[0][:12] + struct.pack(">4i", *([len(bodies)] * 4)) + b"".join(
        body[28:] for body in bodies)


def startPattern():
    return struct.pack(">HH8i", 0x194, 7, *([0] * 8))


def spawnArea(now):
    """⚠️ ``now`` 被忽略，统一走 ``serverNowMs()``。"""
    return struct.pack(">HQiii", 0xC8, serverNowMs(), 0, 1, 0)


def campExchange(now, camp):
    """⚠️ ``now`` 被忽略，统一走 ``serverNowMs()``。"""
    return struct.pack(">HQiQi", 6, serverNowMs(), 1, ACTOR_ID, camp)


def heartbeat(body):
    exact(body, 16, "heartbeat")
    return b"\0\2" + body[2:14]


def matchStart(body):
    if not 37 <= len(body) <= 2100:
        raise ValueError("match-start body outside evidenced 37..2100-byte bound")
    # The match-pattern union remains opaque; the source listener echoes it.
    return b"\0\2" + body[6:-1] + bytes(201)


# ⭐⭐⭐ 2026-09-27：``match-start`` body 的**固定布局**（从 4 组真实样本解出）
# -----------------------------------------------------------------------------
# 旧实现「扫全 body、唯一命中才切」有个**结构性缺陷**：body 里除了选中的图，
# 还带一整串**候选图**，一旦候选池里有任何一张被登记进 ``LEVEL_BY_PATTERN``，
# 命中数就 ≥2 ⇒ 反而不切图。登记江陵城（20002）正好会踩到这一点。
#
# 实测 4 组 body（`data/*/wire/frames-1.jsonl` 的 `match-start-body-hex`）解出的布局：
#
#   off=0   65536          固定头
#   off=4   65537 / 65543  固定头
#   off=8   4 / 1
#   off=12  1
#   off=16  **mode_id**    12 会战 / 10 实战训练 / 2 团队 / 3 攻城
#                          —— 与 `pattern_level_map.csv` 的 `mode_id` 列**逐个吻合**
#   off=20  1
#   off=24  **卡总数 N**   1 / 3 / 10 / 11  = 1 张选中 + (N−1) 张候选
#   off=28  **★ 客户端选中的图** 10002 / 1028 / 1003 / 20001 —— 与四次实进图**全部吻合**
#   off=32+ **候选池**，步长 4
#           团队(77B): 1013 1021 1020 6001 1001 6003 1030 1018 1034   ← 9 张（与旧注释一致）
#           攻城(81B): 20011 20013 20015 **20002** 20003 20004 20006 20007 20009 20012 ← 10 张
#           ⚠️ 候选池**不含**选中那张（樊城那局 off=28=20001，候选池里没有 20001）
#
# ⇒ **切图决策只该看 off=28**，不该扫候选池。这样：
#     * 已登记图（洛阳/宛城/樊城/玉门关外）off=28 都是已知卡 ⇒ **行为逐位不变**；
#     * 客户端在大厅**选**江陵城 ⇒ off=28 变成 20002 ⇒ 正确切到 10020。
#   实测「选图」就是改 off=28（用户 2026-09-27 确认大厅可以选图）。
MATCH_CARD_OFFSET = 28
# 回退开关：``T7_MATCH_SCAN=on`` 回到旧的「扫全 body、唯一命中才切」。
MATCH_SCAN_ENV = "T7_MATCH_SCAN"


def matchScanMode() -> bool:
    """``True`` = 走旧的「扫全 body」逻辑（**仅排障用**，正常不要开）。"""
    return str(os.environ.get(MATCH_SCAN_ENV, "")).strip().lower() in (
        "1", "on", "true", "yes")


# ⭐ 2026-09-27：**随机进图**（``T7_MATCH_RANDOM=on``，**默认 off**）
# -----------------------------------------------------------------------------
# 大厅「选择地图」是**多选**（截图实测：攻城模式可勾 10 张，弹窗底部写
# 「最少选择四张地图」）。客户端把**选定那张**放 `@28`、其余放 `@32` 起，
# 共 N 张（`@24`）—— 也就是整个 body 尾部就是**用户勾选的图池**。
#
# 真实匹配服务器是在这个池子里**分配**一张，而不是照抄客户端首选 ——
# 这个开关就是模拟它（用户 2026-09-27 要求「加个随机加入」）。
#
# ⚠️ 只在**已登记**（`LEVEL_BY_PATTERN`）的图里随机：池子里我们还没做数据的图
#    （20011 广陵 / 20013 南海 / 20015 交趾 / …）进不去，直接跳过。
#    例：樊城那局池子 11 张里只有 20001(樊城) 与 20002(江陵城) 已登记
#    ⇒ 开随机后是 50/50，正好能反复测江陵城。
# ⚠️ **不缓存**随机结果：同一次勾选连点两次匹配应当可能进不同的图（那才是随机）。
MATCH_RANDOM_ENV = "T7_MATCH_RANDOM"


def matchRandomEnabled() -> bool:
    """``match-start`` 是否**随机**挑图。

    优先级：环境变量 ``T7_MATCH_RANDOM`` > ``level.ini`` 的 ``[level] match_random``。
    ⚠️ **双击 ``T7.Server.exe`` 吃不到环境变量**（skill 里那条老坑）⇒ 日常请写 ini。
    """
    raw = os.environ.get(MATCH_RANDOM_ENV)
    if raw is None or not str(raw).strip():
        raw = iniSection("level").get("match_random")
    return str(raw or "").strip().lower() in ("1", "on", "true", "yes")


# ⭐⭐ 2026-10-01：``off=16`` = **客户端在大厅卡片列表里光标停在第几项**（0-based）。
# -----------------------------------------------------------------------------
# 为什么需要它：「职业教学 6 / 铁骑训练场 5」在客户端是**一张整卡**（模式 1 教学模式），
# 卡片号（``off=28``）对这些**子模式**是同一个 ⇒ 光看 ``off=28`` **分不出**
# 「弓箭手教学」和「骑兵教学」。而 `pattern_level_map.csv`（权威卡片表）
# 在客户端目录和 data1.vfs 里**都找不到**，没法离线反查。
#
# ⇒ 用 ``off=16``：它在大厅就是**选中项下标** —— 实测
#     29 → 0 之后的第 1 项（宛城之战教学）；
#     11 → 第 0 项（白马要塞教学）。
#   在**职业教学页**点第 n 个 → ``off=16`` 就是 n（0-based）。
#   于是把「第 n 项 = 哪个 level」用 ini **钉住**即可，不必拿到那张表：
#     ``[level] mode_item_0 = 10016``   # 职业教学页第 1 项 = 弓箭手教学
#     ``[level] mode_item_1 = 10017``   # 第 2 项 = 骑兵教学
#     ``[level] mode_item_2 = 10024``   # 第 3 项 = 双手剑训练（步兵）
#     ...
#   ⚠️ 没钉住就**不切图**（保守，宁可不切也不切错）。钉错了可以 F5 回大厅重选。
#   ⚠️ 这套只在旧「扫描模式」（`T7_MATCH_SCAN=on`）下才走到 —— 默认档仍走
#      「直读 off=28、只认已登记卡」的原逻辑。
MODE_ITEM_OFFSET = 16

# 缓存 ini 里的钉住表（每局只读一次，避免每帧解析 ini）
_MODE_CARD_ITEM_LEVEL = None


def itemIndex(body):
    """``match-start`` body 的 ``@16`` = 大厅卡片列表**选中项下标**（0-based）。

    读不出（body 太短 / 值不合理）返回 ``-1``。布局见 `MATCH_CARD_OFFSET` 上方。
    """
    if body is None or len(body) < MODE_ITEM_OFFSET + 4:
        return -1
    try:
        idx = struct.unpack_from(">i", body, MODE_ITEM_OFFSET)[0]
    except struct.error:
        return -1
    if idx < 0 or idx > 64:        # 防御：不合理就当读不出来，不猜
        return -1
    return idx


def modeItemPins():
    """``{itemIndex: level_id}``，来自 ``level.ini`` 的 ``[level] mode_item_N``。

    ``N`` 就是 `itemIndex()`（大厅卡片列表选中项，0-based）。带缓存。

    ⚠️⚠️ 2026-10-01 **不能过滤「没出生点」的 level**（踩过）：
       最初这里写了 `if lv not in SPAWN_BY_LEVEL: continue`，想着「反正切不了，
       不登记省事」。**错** —— ``N`` 是**客户端卡片的物理序号**，
       `mode_item_1 = 10017`（骑兵教学，无场景数据）这一条如果被过滤掉，
       玩家点**第 2 项**时 `pins.get(1)` 就是 ``None`` ⇒ **整页序号全错位**。
       ⇒ 这里**原样保留映射**；能不能切由 `applyLevel` 判（它会拒绝并返回 False，
         调用方打 `match-start-pin-rejected`）。
    """
    global _MODE_CARD_ITEM_LEVEL
    if _MODE_CARD_ITEM_LEVEL is not None:
        return _MODE_CARD_ITEM_LEVEL
    pins = {}
    section = levelIniSection()
    for key, value in section.items():
        k = str(key).strip().lower()
        if not k.startswith("mode_item_"):
            continue
        try:
            idx = int(k[len("mode_item_"):])
            lv = int(str(value).strip())
        except (TypeError, ValueError):
            continue
        if idx < 0 or idx > 64:
            continue
        # ⚠️ 这里**故意不判** `lv in SPAWN_BY_LEVEL` —— 见上方 docstring（序号会错位）。
        pins[idx] = lv
    _MODE_CARD_ITEM_LEVEL = pins
    if pins:
        # 启动行一眼看出：钉了哪几项、哪些「无场景数据」（点了会 rejected）。
        noData = sorted(i for i, lv in pins.items() if lv not in SPAWN_BY_LEVEL)
        print("[contracts] mode_item pins=" + str(pins)
              + (" no-scene-data-items=" + str(noData) if noData else "")
              + " (level.ini [level] mode_item_N)", flush=True)
    return pins


def matchCardPool(body):
    """``match-start`` body 尾部的**全部图池**（``@28`` 起连续 N 个 int32）。

    布局见 `MATCH_CARD_OFFSET` 上方那段。`N` 从 `@24` 读；读不出 / 不合理 ⇒ ``[]``。
    实测：樊城那局 N=11 ⇒ `@28,32,...,68` 正好 11 个值，全部是 pattern 量级。
    """
    if body is None or len(body) < MATCH_CARD_OFFSET + 4:
        return []
    try:
        n = struct.unpack_from(">i", body, 24)[0]
    except struct.error:
        return []
    if n <= 0 or n > 64:            # 防御：N 不合理就当读不出来，不猜
        return []
    pool = []
    for i in range(n):
        off = MATCH_CARD_OFFSET + 4 * i
        if off + 4 > len(body):
            break
        try:
            pool.append(struct.unpack_from(">i", body, off)[0])
        except struct.error:
            break
    return pool


def probeMatchPattern(body, logs) -> int | None:
    """⭐ 2026-09-19：从 ``match-start`` 的 opaque body 里找客户端点的卡。

    之前只在 ``room-create``（0x1E/1）路径做了「按卡片自动定图」，
    ``match-start``（0x20/1）路径完全没接 —— 用户在新手向导点「团队模式」
    走的是 match-start，body 不被解析，服务端停在 level.ini 的关卡上
    → 客户端期望洛阳、服务端发宛城 → 卡死。

    修法：扫一遍 body 里所有可能的 4 字节 big-endian int，命中**已知**卡片
    （``LEVEL_BY_PATTERN``）就认。命中**唯一**一张就 ``applyCard`` 自动切；
    命中 0 / ≥2 张则不动（不强行猜），只把扫描结果记账，让下次出问题时
    一眼能看出「是不是身体里就没塞卡片 ID」。

    同步记日志：``match-start-pattern-scan hits=[(offset, value), ...]``，
    命中切图再加 ``match-start-card-detected card=... -> level=...``。
    """
    import struct
    hits = []
    if body is None:
        return None
    # ⭐ 2026-09-20：顺手把「像卡片 id」的候选值也列出来。
    #    卡片 id 实测落在 29 / 1003 / 1028 / 20001 / 60005 这个量级，
    #    所以取 100 <= v <= 200000 且**不是**已知卡片的 int32。
    #    加新图（玉门关外）时一眼能看到客户端到底塞了哪个数，不用手解 hex。
    candidates = []
    for off in range(0, max(0, len(body) - 3)):
        try:
            val = struct.unpack_from(">i", body, off)[0]
        except struct.error:
            break
        if val in LEVEL_BY_PATTERN:
            hits.append((off, val))
        elif 100 <= val <= 200000:
            candidates.append((off, val))
    if logs is not None:
        # ⭐ 2026-09-20：**总是**把 body 打出来。
        #    遇到没登记的图（用户要加「玉门关外」）时，靠这段 hex 就能反推卡片 id，
        #    不用再去客户端翻表。match-start 一局最多几次，日志里留一份很值。
        logs.append("match-start-body-hex=" + body.hex())
        logs.append("match-start-card-candidates=" + str(candidates))
        # ⭐ 2026-09-27：把 body 头三个关键字段单独打出来（布局见 MATCH_CARD_OFFSET 上方）
        if len(body) >= MATCH_CARD_OFFSET + 4:
            try:
                logs.append("match-start-header mode_id="
                            + str(struct.unpack_from(">i", body, 16)[0])
                            + " cards=" + str(struct.unpack_from(">i", body, 24)[0])
                            + " selected="
                            + str(struct.unpack_from(">i", body, MATCH_CARD_OFFSET)[0]))
            except struct.error:
                pass
        if hits:
            logs.append("match-start-pattern-scan hits=" + str(hits)
                        + " bodyLen=" + str(len(body)))
        else:
            # body 里没有已知 pattern —— 大概率是 body 里没有塞卡片 ID，
            # 或者我们漏了某些卡片（人机 / 教学 2）。**不强行猜**，保持当前关卡。
            logs.append("match-start-pattern-scan hits=[] bodyLen=" + str(len(body))
                        + " (no known pattern; keep current level=" + str(LEVEL_ID) + ")")
    # ⭐ 2026-09-27：切图决策 = **直读 off=28**（客户端在大厅选中的那张），
    #    **不扫候选池**（扫了会把候选池里别的已登记图也算成命中 ⇒ ≥2 ⇒ 不切图）。
    card = None
    if matchScanMode():
        # 回退：旧行为（扫全 body、唯一命中才切）。仅排障用。
        if len(hits) == 1:
            card = hits[0][1]
        # ⭐⭐ 2026-10-01：扫到 0 张时**不再直接落空**，继续往下走两条分支：
        #    ① 直读 off=28 → 若命中的是**未登记的 13xx 教学模式卡**，交给
        #       `mode_item_N` 钉住表兜底（显式用户意图，**优先于随机**）；
        #    ② body 太短读不到 off=28 → 用「唯一命中」。
        #    ⇒「职业教学 6 + 铁骑训练场 5」这 11 张卡本就不在 LEVEL_BY_PATTERN 里
        #      （客户端表拿不到，只能实机抓），老逻辑会让玩家点了也什么都不发生。
        #    ⚠️ 仍然**不猜**：`mode_item_N` 只在**显式写了 ini** 且子模式命中时才认。
    pinned = None
    pinnedCard = None
    if card is None and len(body) >= MATCH_CARD_OFFSET + 4:
        # ⭐⭐⭐ 2026-10-01：**钉住表优先于随机档**。
        #    随机档是「模拟匹配服务器在池子里分配」，只认得 `LEVEL_BY_PATTERN`
        #    里已登记的卡；而用户在大厅**明确点了**职业教学/铁骑训练场的某一项时，
        #    意图是确定的 ⇒ 不能被随机覆盖。所以先试钉住，再落到随机。
        #    ⚠️ 顺序很要紧：若把随机放前面，池子里只要有已登记图（如白马要塞
        #      11 在池里）就会被抢走，钉住永远不生效。
        pin = modeItemPins().get(itemIndex(body))
        if pin is not None:
            pinned = pin
            # ⭐ 客户端**实际发的卡号**（off=28）留着 —— 切图时要**回显**它，
            #   客户端只认自己发出去的那个 resource_id（见 `applyLevel` 里那段）。
            pinnedCard = struct.unpack_from(">i", body, MATCH_CARD_OFFSET)[0]
            if logs is not None:
                logs.append("match-start-modecard-pinned item=" + str(itemIndex(body))
                            + " card=" + str(pinnedCard)
                            + " -> level=" + str(pin) + " (level.ini [level] mode_item_"
                            + str(itemIndex(body)) + ")")
    if (card is None and pinned is None
            and len(body) >= MATCH_CARD_OFFSET + 4 and matchRandomEnabled()):
        # ⭐ 随机档：从图池里**已登记**的项中挑一张（模拟真实匹配服务器的分配）。
        pool = matchCardPool(body)
        known = [c for c in pool if c in LEVEL_BY_PATTERN]
        if not known:
            if logs is not None:
                logs.append("match-start-random pool=" + str(pool)
                            + " (no known card; keep current level="
                            + str(LEVEL_ID) + ")")
            return None
        card = random.choice(known)
        if logs is not None:
            logs.append("match-start-random pool=" + str(pool)
                        + " known=" + str(known) + " pick=" + str(card))
    # ⭐⭐ 2026-10-01：**钉住表优先落地**（走 level 直切，不经过 `applyCard`）。
    #    为什么不能塞进 `applyCard`：钉住给的是 **level_id**，而 `applyCard`
    #    收的是**卡片号**（内部 `resolveLevel` 查 `LEVEL_BY_PATTERN`）——
    #    这些新卡本来就不在 `LEVEL_BY_PATTERN` 里，塞进去必然查不到。
    #    ⚠️ **必须排在 `unknown` / `random` 的 `return None` 之前** ——
    #      这两个分支会提前 return，放后面就永远轮不到钉住生效（踩过一次）。
    #    ⚠️ `applyLevel` 对**不在 `SPAWN_BY_LEVEL`** 的 level 返回 False（不切），
    #      所以钉了 `testmap_ld_4` / `testmap_ld_3` / `gc_map_j_jiange` 三张
    #      没数据的图**也不会切** —— 这是有意的（宁可不切，不切错）。
    if pinned is not None:
        if applyLevel(pinned, pinnedCard):
            if logs is not None:
                logs.append("match-start-card-detected card=" + str(pinned)
                            + " (pinned item=" + str(itemIndex(body)) + ")"
                            + " -> level=" + str(LEVEL_ID)
                            + " scene=" + str(sceneFor())
                            + " resource=" + str(RESOURCE_ID)
                            + " mode_a=" + str(modeA()))
            return pinned
        if logs is not None:
            logs.append("match-start-pin-rejected item=" + str(itemIndex(body))
                        + " level=" + str(pinned)
                        + " (not in SPAWN_BY_LEVEL / same level; keep current level="
                        + str(LEVEL_ID) + ")")
        return None
    if card is None and len(body) >= MATCH_CARD_OFFSET + 4:
        want = struct.unpack_from(">i", body, MATCH_CARD_OFFSET)[0]
        if want in LEVEL_BY_PATTERN:
            card = want
        else:
            # ⚠️ 这里不再重复查钉住表 —— 上面 `pinned` 分支已经查过且优先。
            #    off=28 没登记、item 也没钉住 ⇒ **不切**，也**不退回扫候选池**
            #    （扫了只会命中候选池里别的已登记图 ⇒ 切错图，比不切更糟）。
            if logs is not None:
                logs.append("match-start-card-unknown card=" + str(want)
                            + " off=" + str(MATCH_CARD_OFFSET)
                            + " item=" + str(itemIndex(body))
                            + " (not in LEVEL_BY_PATTERN and no mode_item_"
                            + str(itemIndex(body)) + " pin; keep current level="
                            + str(LEVEL_ID) + ")")
            return None
    if card is None and len(hits) == 1:
        # body 太短、读不到 off=28 ⇒ 退回旧的「唯一命中」（保守，不猜）
        card = hits[0][1]
    if card is None:
        return None
    if applyCard(card):
        if logs is not None:
            logs.append("match-start-card-detected card=" + str(card)
                        + " -> level=" + str(LEVEL_ID)
                        + " scene=" + str(sceneFor())
                        + " resource=" + str(RESOURCE_ID)
                        + " mode_a=" + str(modeA()))
        return card
    return None  # 同图，已在另一条日志里记过 room-create 路径，不需要重复
