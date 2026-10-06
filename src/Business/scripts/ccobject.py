"""CC 攻城器械（云梯 / 攻城车 / 投石车 / 城门 / 屋顶）的加载与下发。

为什么需要
----------
樊城（``tszz``）的 **24 个**可交互攻城器械是 **CC Object**，不在静态资产表
``amodellist.json`` 里。数据早就抽出来了
（``data/scene/<场景>/ccobject.json``，来源是客户端
``010500_SH_RES_CC_OBJECT_TAB_*.xml`` / ``010501_SH_RES_CC_PATH_TAB_*.xml``），
但服务端**从来没引用过**它 —— 2026-09-20 复核
``grep -rn ccobject scripts/*.py`` **零命中**。

所以实机里「少一个攻城车、三个按 C 扶起来的梯子、两个投石车，还有房顶」
全都看不见。用户原话：「**攻城车、梯子都没加上**」。

分工（三层，别混）
------------------
* ``codec/vision_flow.py`` —— **编码器**。``encode_cc_dynamic_vision_add_event``
  字节布局已离线验证：单物件 202 B、24 物件 4958 B、既有 ACTOR 报文 373 B 逐位不变。
* **本模块** —— 读盘 → ``CcDynamicObject`` → 交给编码器。
* ``scene.py`` —— 决定**什么时候**发（``battleEntry`` / ``camp-choice-ok``）。

⚠️ 未闭合边界（别当成已闭合）
------------------------------
* ``rid`` / ``inst_id`` 的**取值语义**没有参考报文可对，只保证「编码合法」。
* 没有实机 CC 报文可比对 —— 验证只到「字节布局与 TDR 声明一致」这一层。
* ``display_name`` 的 TDR ``maximum_size=1``，装不下中文名，一律留空。
* 客户端**从未收过**这类物件，是否会崩未实测。

开关
----
``CC_OBJECT_ENABLED`` 默认 **True**（用户明确要这些器械）。
与空气墙同一套纪律：**并集而不是替换** —— 关掉时 ``scene.py`` 的发送路径与
日志逐位不变。

``T7_CC_OBJECT`` / ``[cc] objects=``（改 ini 后重启服务端才生效）：

    0 / off                → 不发（回到改动前）
    on / all / true / yes  → 发全部 24 个
    <N>（正整数）          → **只发前 N 个**（二分定位用：崩了就知道是编码还是数据）
    未设置                 → 用 ``CC_OBJECT_ENABLED``

⚠️ **订正（2026-09-23）**：本段早先写着「``T7_CC_OBJECT=1`` → 发全部 24 个」，
但 ``ccObjectLimit()`` 从第一天起就是把任何正整数当**件数**（``1`` ⇒ 只发 1 个）。
两者矛盾，已按**代码的实际行为**改文档（行为不动）：「全部」请写 ``on``，
别写 ``1`` —— 写 ``1`` 只会发 1 个物件，实机表现是「器械少了 23 个」。

回退（30 秒）：把 ``scripts/ccobject.py`` 删掉或把 ``CC_OBJECT_ENABLED`` 改 False，
并把 ``scene.py`` 里两处 ``ccObjectVision`` 调用注释掉。``scene.py`` 有备份
``t7_backup/cc-object-wire-*/scene.py``。
"""
import io
import json
import math
import os

from . import contracts as wire
from .codec import vision_flow

# --- [cc] 段 ini 通道（2026-09-23 夜新增） -----------------------------------
#
# 为什么必须加：本模块 6 个旋钮**原先只认环境变量**，而环境变量要在启动
# ``T7.Server.exe`` **之前** set，且服务端是 ``T7.Launcher.exe`` 拉起来的独立进程
# —— 在终端里 ``set T7_CC_ID_MODE=old`` **进不去**。
#
# 实证（2026-09-23，会话 ``13976-268874546``，即樊城那局）：
#   ① 读服务端 PID 13976 的 PEB 环境块：66 个变量里 ``T7_*`` **零命中**；
#   ② 同局线上 24 个 CC 物件的字节 ``inst_id=pathId / res_id=tid`` 全是 tid 模式，
#      ``2370/2377/2384``（old 模式该出现的 id）**零命中**。
#   ⇒ 那局 ``T7_CC_ID_MODE=old`` 实验**根本没生效**，白跑一局。
# 同一个坑 ``level.ini`` 的注释里已经记过两次（T7_LEVEL 连翻两次车），这是第三次。
#
# ini 是**记事本就能改**的，改完重启服务端即可，不依赖 shell。
# 段名 ``[cc]`` 与 ``[level]`` / ``[move]`` 分开，互不影响。
# 文件候选顺序复用 ``contracts._iniCandidates()``（``level.ini`` 优先于 ``server.ini``）。
# ⚠️ **不往 ``server.ini`` 里塞新段** —— 那是 ``T7.Server.exe`` 自己的解析器在读。
#
# 优先级：**环境变量 > [cc] ini > 默认**（与 ``contracts.levelId()`` 同一套）。
# 环境变量留给「临时 A/B 一局」，ini 留给「日常」。
CC_INI_SECTION = "cc"
CC_INI_ENV = "T7_CC_INI"


def _ccIniRaw():
    """把 ``level.ini`` / ``server.ini`` 的 ``[cc]`` 段读成 dict；读不到就是空 dict。

    实现走 ``contracts.iniSection``（2026-09-23 夜收成共享实现 —— ``level.ini`` 里
    ``[level]/[move]/[cc]/[trace]`` 四段的读法完全一样，各写一份必然漂移）。
    ⚠️ 段里的值**不要写行内注释**（``hp=1000    ; 说明``）：ConfigParser 默认
    不认行内注释，值会变成 ``1000    ; 说明`` ⇒ 解析失败 ⇒ **静默退回默认值**。
    """
    return wire.iniSection(CC_INI_SECTION, CC_INI_ENV)


_CC_INI = _ccIniRaw()
# 每个旋钮**最终值来自哪条通道**（``env`` / ``ini`` / ``default``）。
# 启动自证行与 ``describe()`` 都要用，见 ``ccKnobsReport()``。
_CC_SOURCE = {}


def _ccKnobRaw(envName, key, default):
    """读一个 CC 旋钮的原始字符串：**环境变量 > [cc] ini > 默认**。

    返回 ``(原始字符串, 来源)``，来源是 ``env`` / ``ini`` / ``default``，
    同时记进 ``_CC_SOURCE``。

    ⚠️ 为什么必须把来源带出来：本项目在「开关没生效」上踩了三次
    （``T7_LEVEL`` ×2、``T7_CC_ID_MODE`` ×1），每一次**症状都指向别处**，
    全靠反推、每次都浪费一整局。把来源打进日志 = 让这类事故**不可能再静默发生**。
    """
    raw = os.environ.get(envName)
    if raw is not None and str(raw).strip():
        _CC_SOURCE[key] = "env"
        return str(raw).strip(), "env"
    text = str(_CC_INI.get(key, "")).strip()
    if text:
        _CC_SOURCE[key] = "ini"
        return text, "ini"
    _CC_SOURCE[key] = "default"
    return default, "default"


def _ccTrue(raw, fallback):
    """三态读法：``1/on/true/yes`` → True；``0/off/false/no/空`` → False；其他 → ``fallback``。"""
    text = str(raw).strip().lower()
    if text in ("1", "on", "true", "yes"):
        return True
    if text in ("0", "off", "false", "no", ""):
        return False
    return fallback


CC_OBJECT_ENABLED = True
CC_ENV_SWITCH = "T7_CC_OBJECT"
CC_FILE = "ccobject.json"

# ⭐ 2026-09-20：CC 物件的 ``state`` 字段要不要带真实的 MO 状态。
#
# 背景：``state`` 是「按 C 扶起/拆除云梯」那条链路的一环 —— 服务端得先让物件
# 带上合法的 ``E_MO_STATE_*``，客户端的交互前置检查
# （``E_MO_INTERACT_PRE_CHECK_MO_STATE``）才肯放行。
#
# ⚠️ 但实机出现副作用：**开了之后云梯看不见了**（2026-09-20 13:4x）。
# 这是当轮唯一改动的 CC 报文字段，所以先**默认关**（发 0 = 改动前的值），
# 用环境变量单独开，方便二分。
#
# 环境变量 ``T7_CC_STATE``（运行时读，但服务端进程的环境变量要重启才变）：
#     1 / on   → 发真实 MO 状态（云梯 1000、正门 3010…）
#     0 / off  → 发 0（默认，已知「云梯可见」的状态）
CC_STATE_ENABLED = False
CC_STATE_ENV = "T7_CC_STATE"

# CC 物件的**视野 mid**（TDR 字段名 ``rid``）基数。
#
# ⚠️ 不能与既有 mid 相撞：本地玩家 = 1（``FIXED_LOCAL_ACTOR_MID``）、敌人 = 2。
# 为什么必须唯一 —— 2026-09-20 实机取证：
#   ``TieJiClient.exe`` 的 ``sh_proto_cs`` 里
#     ``CS_PROTO_VISION_VISION_LIST_RSP.obj_mids``  : uint64[]
#     ``CS_PROTO_VISION_CC_DYNAMIC_INFO.rid``       : uint64   ← 同一个东西
#     ``CS_PROTO_VISION_GET_OBJECTS_REQ.obj_mids``  : uint64[]
#   客户端按 ``obj_mids`` 建索引表，**再用 rid 去挂载对象**。
#   改动前 24 个物件的 rid 全是 0，而列表只回 (1, 2) ⇒ 客户端无处挂载 ⇒ 收到即丢。
#
# 环境变量 ``T7_CC_RID_BASE`` 可覆盖（只影响本进程，重启即恢复）。
#
# ⭐ 2026-09-21：基数从 **1000 改成 10000** —— 原值与 ``E_MO_STATE_*`` 撞号。
#
# 取证：``rid = base + index``（index 从 1 起），原 base=1000 ⇒ rid = 1001..1024。
# 而 ``mo.py`` 的状态值是 ``MO_STATE_LADDER_GROUND=1000`` … ``LADDER_UP=1007`` …
# ``ATTACK_CITY_CAR_IDLE=1020`` … ``CATAPULT_WAIT=6000``。两个命名空间**数值重叠**：
#
#   * 攻城车1 是第 7 个物件 ⇒ **rid = 1007**，而 1007 正是 ``MO_STATE_LADDER_UP``
#     （云梯正在架起）。用户在云梯1（rid 1001）上按 C，服务端发
#     ``update_state(target=1001, state=1007)`` —— 若客户端把 1007 当目标去查物件，
#     命中的就是 **攻城车1** ⇒ 攻城车吃了梯子状态 ⇒ 实机「云梯按C变攻城车、碎一地」。
#   * 匾额第 20 个 ⇒ rid = 1020，同样撞 ``MO_STATE_ATTACK_CITY_CAR_IDLE``。
#
# 10000 起 ⇒ rid = 10001..10024，高于已知状态值上界（6000），不再重叠。
#
# ⚠️ 2026-09-24 更正：上面把「云梯按 C 变攻城车、碎一地」归因到 rid↔state 撞号，
# 例子用的是 ``MO_STATE_LADDER_UP = 1007``。现在 1007 已查明是**哨塔状态_破坏状态**
# （不是云梯），而「碎一地」的真凶是**服务端发了哨塔族状态值** ⇒ 客户端加载
# ``哨塔状态_破坏状态.btree`` ⇒ ``会战_哨塔_破碎`` 特效。见 ``mo.py`` 的
# ``MO_STATE_LADDER_GROUND`` 处铁证表。rid 挪到 10000 这条改动本身**依然正确**，
# 但它是**防御性**的，不是本次元凶。
#
# ⚠️ 类型映射本身**没错** —— 已用 ``010504_ObjectsFolder_154a8002.xml`` 验证：
#   ``ID=1 → 攻城器械.云梯``、``ID=2 → 攻城器械.攻城车``，所以 ``res_id = cc_tid`` 正确。
# 这条改动只是把 rid 挪出状态值区间，是**防御性**的（撞号本身是隐患，不管它是不是本次元凶）。
CC_RID_BASE = 10000
CC_RID_ENV = "T7_CC_RID_BASE"


# ⭐ 2026-09-22：CC 物件的 ``hp``。TDR 注释是「血量」，我们一直发 **1**。
#
# 为什么值得怀疑：客户端很可能拿 ``hp`` 去选「完好 / 受损 / 摧毁」的模型档位，
# 而档位切换只在**状态变化时**才重新求值 —— 正好对上「按 C 之前梯子好好的，
# 按完 C 变一地碎片」这个现象（碎片 = 摧毁档的模型）。
#
# ⚠️ 这是**假设**，没有任何实机或反解证据。所以做成开关，别改默认值。
#
# 环境变量 ``T7_CC_HP``（重启服务端进程才生效）：整数。
# ⭐ 2026-09-23：默认 1 → 1000。证据：``config/propsheet/攻城器械.psheet``
# 云梯记录 ``生命值 Value="1000"``；实机 old 模式下物件图标显示**空血**
# （hp=1 相对显示上限≈空）。此前 hp=1 造成「状态一变就重估摧毁档 → 碎片」
# 的假设随之升级为有表值背书。
CC_HP = 1000
CC_HP_ENV = "T7_CC_HP"


# ⭐⭐ 2026-09-23 深夜：``hp`` 的**真值表** —— 按 ``cc_tid``（= 器械类型）逐个取真值。
#
# 为什么必须按 tid 分：``攻城器械.psheet`` 的「生命值」列**逐行不同**，而我们
# 此前给**所有** 24 个物件无脑发同一个 ``hp``。客户端拿 hp 去比**上限**、定档位
# （完好 / 受损 / 摧毁），于是同一个字段能演出两种完全不同的现象：
#   • ``hp=1``    （2026-09-23 之前）⇒ 1/6000 ≈ 0    ⇒ **摧毁档** ⇒ 实机「按 C 变一地碎片」
#   • ``hp=1000`` （当天早些时候）  ⇒ 1000/6000 ≈ 17% ⇒ **受损档** ⇒ 实机「小门带血条」
# 2026-09-23 夜用户**亲手做了这组对照实验**（"之前是碎一地，后来改了 HP 就变血条了，
# 碎片消失"）⇒ hp 是「摧毁/受损档」的控制字段，这条从假设升级为**实证**。
#
# 真值全部抄自 ``dfjq_out/config/propsheet/攻城器械.psheet`` 的「生命值」列：
#   1 云梯=1000   2 攻城车=1000   3 机关城门=1000     4 城门机关=1000
#   5 大道旗旗面=1000  6 大道旗旗杆=1000  **7 门可破坏=6000**  8 攻城车踏板=1000
#   9 不读条城门机关=1000  10 红方军旗=1000  11 蓝方军旗=1000
#   12 红方旗座=1000  13 蓝方旗座=1000  14 投石车=1000
#   15 红方军旗_野外=1000  16 蓝方军旗_野外=1000  17 补给箱=1000
#   **101 屋顶可破坏=10000**
# 提取脚本 ``out/cc/dump_cc_hp.py``；快照 ``out/cc/cc_hp_table.json``。
#
# 表里没有的 tid（105 屋顶3 / 106 匾额 / 201 vol1 / 206 vol_horse / 2000 哨塔…）
# **故意不猜** ⇒ 回退 ``ccHp()``，与改动前逐位一致。
#
# ⚠️ 对云梯(tid=1)这条链而言，本表取值与「统一 1000」**完全相同** ⇒ 开这张表
#    不会改变云梯行为，它只影响门(7)与屋顶(101)。所以「hp 表」与
#    「havok_idx 二分」可以**同时开**：两者作用在**不同物件**上，不构成多变量。
CC_HP_TABLE = {
    1: 1000, 2: 1000, 3: 1000, 4: 1000, 5: 1000, 6: 1000,
    7: 6000, 8: 1000, 9: 1000, 10: 1000, 11: 1000, 12: 1000, 13: 1000,
    14: 1000, 15: 1000, 16: 1000, 17: 1000, 101: 10000,
}
CC_HP_TABLE_ENV = "T7_CC_HP_TABLE"


# ⭐ 2026-09-23：``havok_res_index``（Havok资源索引, int8）—— 旋钮 + **逐物件模式**。
#
# 为什么怀疑它（三重证据）：
#   ① **每个器械的 havok 资源逐类型不同** —— ``攻城器械.psheet`` 的 ``nav文件`` 列：
#        云梯(tid=1)     → TSZZ_stair_04.hkt
#        攻城车(tid=2)   → TSZZ_siege_01_part1.hkt
#        机关城门(tid=3) → TSZZ_door_01.hkt
#        投石车(tid=14)  → CBP_Catapult_01nav.hkt
#        补给箱(tid=17)  → P_box_01nav.hkt
#      而本模块改动前给**全部 24 个物件发同一个 0** —— 逻辑上说不通。
#   ② 客户端表 ``s_res_havok_character_cc_data_clt.bin``（MSES 容器，16 行 × 42B，
#      已用 ``out/cc/dump_mses.py`` 解开）主键是 ``1,2,3,4`` / ``4000001..`` /
#      ``4102002..`` —— **最小 id 是 1，没有 0**。若 0 = 未定义，客户端只能
#      fallback 到默认资源 ⇒ 对上实机「按 C 立起来的是一座**木塔**」。
#   ③ 行为树 ``云梯_出生.btree`` 里架梯 = havok 角色 ``TSZZ_stair_04.hkt``
#      收动画事件 "Opened"（枚举 ``E_HAVOK_ANIMATION_TYPE`` 已排除：那是动画**类别**
#      不是资源）⇒ 立起后应变成「**楼梯**」，不是塔。
#      （旁证：``tszz`` 正是当前场景名，``TSZZ_stair_04`` 也在场景静态模型表里。）
#
# 两个模式（``[cc] havok_idx_mode``）：
#   ``flat``（**默认**）= 24 个物件全用 ``havok_idx`` 的同一个值 = 改动前行为，可回退
#   ``tid``            = 每个物件用**它自己的 tid**（钳到 int8 的 0..127）
#
# 日志 ``cc-object-sent`` 带 ``hidx=<值>(<来源>[,mode])`` 自证。
CC_HAVOK_IDX_ENV = "T7_CC_HAVOK_IDX"
CC_HAVOK_IDX_MODE_ENV = "T7_CC_HAVOK_IDX_MODE"
CC_HAVOK_IDX_MODE_DEFAULT = "flat"


# ⭐⭐⭐ 2026-09-24：``havok_res_index`` 的**真值表**（按 ``cc_tid``）—— 云梯回来了。
#
# 为什么这次敢改默认行为（此前这个旋钮被封存过，见 ``level.ini`` 的
# 「hidx=1 实测结案」注释）：**那次实验是在错的坐标系里做的。**
# 会话 ``13976-275799381``（23:23）跑 hidx=1 时，云梯的状态值还是错的
# （服务端发 ``1006/1007`` = 客户端哨塔族）⇒ 梯子**根本没进「架起」分支**，
# 任何资源都看不出差别。用户当时反馈的「云梯没有变化，和原来一样」
# 只能证明「资源索引不改哨塔行为」—— 而哨塔本来就不是它的活儿。
# 现在状态值已修正（``LADDER_INAIR=1002 / LADDER_STANDING=1003``），
# ``havok_res_index`` 才第一次有了可观察的因变量。
#
# 本轮新证据（三条独立）：
#   ① **云梯模型长度成 1/2 : 3/4 : 1 的精确比例**（本轮新写的 NIF 顶点解析
#      ``out/cc/nif_verts.py``，按 ``NiTriShapeData`` 布局定位顶点数组）：
#        tszz_stair_04a.nif     2073 顶点   长 8.232 m   ≈ 1/2
#        tszz_stair_04b.nif     2727 顶点   长 11.953 m  ≈ 3/4
#        tszz_stair_04.nif      3625 顶点   长 16.007 m  = 1（全长）
#        tszz_stair_04_new.nif  3625 顶点   长 16.007 m  （与 04 同 size，同一资源）
#      8.232 : 11.953 : 16.007 = 0.514 : 0.747 : 1.000 —— 对上
#      ``propsheet/mo特效表.psheet`` 的三条记录
#      **「云梯_二分之一长」/「云梯_四分之三长」/「云梯」**。
#   ② 本表 ``s_res_havok_character_cc_data_clt.bin`` 主键 = ``1,2,3,4`` /
#      ``4000001..4000005`` / ``4102002..4102008`` —— **没有 0**。
#      而我们 24 个物件一直发 ``hidx=0`` = 未定义 ⇒ 客户端只能 fallback。
#   ③ 云梯是 **Havok 角色**（``云梯_出生.btree``: ``设置Character = TSZZ_stair_04.hkt``），
#      它的"身体"由 havok 角色决定 ⇒ ``havok_res_index`` 正是选它的字段。
#      ``_name_map2.tsv`` 里 04/04a/04b 各有独立 hkt：
#        tszz_stair_04.hkt  → 006995 / tszz_stair_04a.hkt → 006996 /
#        tszz_stair_04b.hkt → 006998
#
# 与实机现象的对应：用户报「梯子搭得低了，再高一半，到城墙上」——
# 城墙实测高 **11.2 m**（``heightfield.json`` 的 ``maxZ=55.385`` 减去城内地平面
# 44.2；``amodellist.json`` 里 tszz_tower_* 大片停在 z=55.3 交叉印证）。
# 若客户端 fallback 到 1/2 长（8.23 m）⇒ 顶端够不到 11.2 m 的墙顶 ⇒ **完全吻合**；
# 换全长 16.0 m 则绰绰有余。
#
# ⚠️ 这张表是**逐 tid** 的，所以它只动云梯（tid=1）一个物件 ——
# 其余 23 个照旧走 ``flat``/``havok_idx``，不构成多变量。
# ini ``havok_idx_table`` 写了就整表替换；写 ``off``/``none`` = 关掉本表
# （回到「24 个物件全发 havok_idx」的改动前行为）。
CC_HAVOK_IDX_TABLE = {1: 1}
CC_HAVOK_IDX_TABLE_ENV = "T7_CC_HAVOK_IDX_TABLE"


# ⭐⭐⭐ 2026-09-24：云梯的 **Z 抬升**（实测出来的"差一半"补偿旋钮）。
#
# 用户口供（本轮）：梯子立起来了，但「**低子搭的低了，再高一半，到城墙上**」，
# 「也可能是城墙高度，低了，上面属于 Z 轴了吧」——这句把轴序也纠正了：
# ``ccobject.json`` 的 ``pos`` 是 ``[x, y, z]``，**z 才是高度**
# （城内地平面 44.2 / 城墙顶 55.385）。
#
# 为什么会"差一半"——两条独立证据指向同一个解释：**转轴在模型中心，不在底端**。
#
#   ① 本轮新量的包围盒（``out/cc/nif_verts.py``，按 NiTriShapeData 布局定位）：
#        tszz_stair_04.nif   X  −8.038 .. +7.970   （Δ16.007，全长）
#        tszz_stair_04b.nif  X  −5.998 .. +5.955   （Δ11.953，3/4）
#        tszz_stair_04a.nif  X  −4.064 .. +4.167   （Δ 8.232，1/2）
#      三段几何**都关于原点对称**。⚠️ 注意：这是**未叠加 NiNode 平移**的原始顶点，
#      所以它只证明"几何建在原点两侧"，不等于"节点平移也是 0"——这条是**弱证据**。
#   ② 客户端原生管线**根本不靠旋转立梯**：``云梯_出生.btree`` 只有
#        ``设置Character = TSZZ_stair_04.hkt``
#        ``动画系统事件     = Opened``
#        ``动画系统浮点参数 = CropTime 0.38``
#        延时 2286 ms 后**再来一遍** ``Opened`` + ``CropTime``
#      没有任何旋转/平移节点 ⇒ 把梯子摆到墙上的位移在 **hkx 动画（root motion）** 里。
#      而服务端从没发过动画事件 —— ``mo_flow.encode_animation_event_var()``
#      **写好了却从没被调用**（``update_mo_state`` 的 ``event_var`` 一直发空）。
#      ⇒ 我们是用 ``MO_INCLINE_NTF``（纯姿态旋转）**顶替**那条动画，
#        于是梯子绕**模型原点**转起来：底端沉到地下约半根、顶端只到半根高。
#        「再高一半」正是这个半根。
#
# ⚠️ 本条是**假设 + 可二分的补偿**，不是已证结论。而且抬的是**报给客户端的 pos**，
#    会同时抬走客户端的「按 C 触发区域」，所以它是**诊断用**的，不是终点。
#    终点应是把动画事件接上（``MO_HAVOK_RES_INDEX_NTF`` / ``ANIMATION_EVENT_AND_VAR``）。
#
# 单位：米，作用在 **z** 上；只对 ``cc_tid == 1``（云梯）生效，其余 23 个物件逐位不变。
# 默认 **0.0** ⇒ 与改动前**逐位一致**，不影响任何既有实验。
CC_LADDER_LIFT = 0.0
CC_LADDER_LIFT_ENV = "T7_CC_LADDER_LIFT"
CC_LADDER_LIFT_TABLE = {}
CC_LADDER_LIFT_TABLE_ENV = "T7_CC_LADDER_LIFT_TABLE"
CC_LADDER_TID = 1


def ccLadderLift():
    """云梯的**全局** Z 抬升量（米）。**环境变量 > ``[cc] ladder_lift`` > 0**。

    ⛔ 2026-09-24 01:30 更正：上一版这里写「城墙高 ≈ 11.2 m，取自 ``heightfield.json``
    ``maxZ=55.385`` − 城内地平面 44.2」——**那个 55.385 不是城墙，是城外山坡**。
    实测（``ladder_profile.py``，1 m 精度）：云梯1/3/4 与玩家四周 **60 m 内地面是
    43.9~45.0 的平地，最近的地形台阶在 37~80 m 外、而且是往下掉（34~39 m）**。
    高度场里根本没有那堵墙。

    城墙高要从**模型**里读 —— CC 物件本身就是墙上的东西：
    ``正门机关 z=55.849`` / ``屋顶1 = 屋顶2 z=56.250`` / ``大道旗 z=56.717`` /
    ``vol1 z=55.672``，而地面 z≈44.29 ⇒ **墙顶一层 55.8~56.3，墙高 ≈ 11.5 m**。

    二分建议：1/2 长 = 8.0 m。若"转轴在模型中心"成立，可见顶端最高只有 8.0 m，
    抬 ``8·sinθ`` 才能让**梯脚落回地面**、顶端升到 ``16·sinθ``。
    θ≈45° ⇒ **5.7 m**。先试 4 / 6.5 两档（一次性对比，见 ``ladder_lift_table``）。
    """
    raw, _ = _ccKnobRaw(CC_LADDER_LIFT_ENV, "ladder_lift", str(CC_LADDER_LIFT))
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return CC_LADDER_LIFT


def ccLadderLiftTable():
    """``pathId -> Z 抬升量（米）`` 真值表。**一次性对比三架云梯用**。

    为什么要这张表：三架云梯的 ``cc_tid`` **都是 1**（同一个器械类型），
    靠 ``tid`` 分不开；能分开的是 ``pathId``（云梯1=10015 / 云梯3=40028 /
    云梯4=40040）。一张表 = **一次重启测三个值**，省掉两轮起服。

    与 ``ccHpTable()`` 同一套纪律：ini 写了就**整表替换**（不是合并）；
    写 ``off`` / ``none`` / ``0`` = 关掉本表（回落到全局 ``ladder_lift``）；
    坏项跳过，不让一行笔误毁掉整张表。

    ini 写法：``ladder_lift_table = 10015:0, 40028:4, 40040:6.5``
    """
    raw, _ = _ccKnobRaw(CC_LADDER_LIFT_TABLE_ENV, "ladder_lift_table", "")
    if not raw:
        return dict(CC_LADDER_LIFT_TABLE)
    if str(raw).strip().lower() in ("off", "none", "0"):
        return {}
    table = {}
    for part in str(raw).replace(";", ",").split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        key, _, value = part.partition(":")
        try:
            table[int(float(key.strip()))] = float(value.strip())
        except (TypeError, ValueError):
            continue
    return table


def ccLadderLiftFor(tid, path_id=None, table=None):
    """该物件的 Z 抬升量。优先级：**pathId 真值表 > 全局 ``ladder_lift`` > 0**。

    非云梯（``tid != 1``）恒返回 0.0 —— 本旋钮**只动云梯**，不构成多变量。
    """
    if int(tid) != CC_LADDER_TID:
        return 0.0
    tbl = ccLadderLiftTable() if table is None else table
    if path_id is not None:
        try:
            key = int(path_id)
        except (TypeError, ValueError):
            key = None
        if key is not None and key in tbl:
            return float(tbl[key])
    return ccLadderLift()


def ccLadderLiftTableTag():
    """自证短标记：``tbl[10015:0,40028:4.0,40040:6.5]`` / ``flat`` / ``err``。

    为什么：这一栏能证明 ini 那行**真的被读到了**。此前「读到了没有」只能靠推断，
    而推断错过三次。逻辑与 ``ccHavokIdxTableTag`` / ``ccHpTableTag`` 一致。
    """
    try:
        tbl = ccLadderLiftTable()
        if not tbl:
            return "flat"
        parts = ["%d:%g" % (k, tbl[k]) for k in sorted(tbl)]
        text = "tbl[" + ",".join(parts) + "]"
        return text if len(text) <= 70 else text[:67] + "..."
    except Exception:  # noqa: BLE001 —— 自证行绝不能把服务端带崩
        return "err"


def ccHavokIdx():
    """``havok_res_index`` 的**全局值**。**环境变量 > ``[cc] havok_idx`` > 0**。"""
    raw, _ = _ccKnobRaw(CC_HAVOK_IDX_ENV, "havok_idx", "0")
    try:
        return max(-128, min(127, int(float(raw))))
    except ValueError:
        return 0


def ccHavokIdxMode():
    """``flat``（默认，全物件同一值）| ``tid``（逐物件用自身 tid）。"""
    raw, _ = _ccKnobRaw(CC_HAVOK_IDX_MODE_ENV, "havok_idx_mode",
                        CC_HAVOK_IDX_MODE_DEFAULT)
    mode = raw.strip().lower()
    return "tid" if mode in ("tid", "per-tid", "pertid", "1", "on") else "flat"


def ccHavokIdxTable():
    """``tid -> havok_res_index`` 真值表。**``[cc] havok_idx_table`` > ``CC_HAVOK_IDX_TABLE``**。

    与 ``ccHpTable()`` 同一套纪律：ini 写了就**整表替换**（不是合并），
    写 ``off`` / ``none`` = **关掉本表**（那时所有物件回到 ``havok_idx`` 那一个值，
    即改动前行为）。坏项跳过，不让一行笔误毁掉整张表。

    ini 写法：``havok_idx_table = 1:1``
    """
    raw, _ = _ccKnobRaw(CC_HAVOK_IDX_TABLE_ENV, "havok_idx_table", "")
    if not raw:
        return dict(CC_HAVOK_IDX_TABLE)
    if str(raw).strip().lower() in ("off", "none", "0"):
        return {}
    table = {}
    for part in str(raw).replace(";", ",").split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        key, _, value = part.partition(":")
        try:
            table[int(float(key.strip()))] = max(-128, min(127, int(float(value.strip()))))
        except ValueError:
            continue
    return table or dict(CC_HAVOK_IDX_TABLE)


def havokIdxForTid(tid, mode=None):
    """按物件取 ``havok_res_index``。

    优先级：**逐 tid 真值表** > ``flat``（全局值）> ``tid``（该物件自身 tid）。

    ⚠️ 表放最前是刻意的：它承载「云梯该用哪套 havok 角色」这条已取证的结论
    （见 ``CC_HAVOK_IDX_TABLE``），不该被任何全局旋钮覆盖掉。
    ``tid`` 有 101/105/106/201/206 这类，>127 会被钳到 127 —— 那说明该模式
    对它们不适用（属预期内的边界，不是 bug）。
    """
    try:
        key = int(tid)
    except (TypeError, ValueError):
        return ccHavokIdx()
    table = ccHavokIdxTable()
    if key in table:
        return int(table[key])
    if (mode or ccHavokIdxMode()) == "tid":
        return max(0, min(127, key))
    return ccHavokIdx()


def ccHp():
    """CC 物件血量兜底值。**环境变量 > ``[cc] hp`` > ``CC_HP``**。"""
    raw, _ = _ccKnobRaw(CC_HP_ENV, "hp", str(CC_HP))
    try:
        return int(float(raw))
    except ValueError:
        return CC_HP


def ccHpTable():
    """``tid -> hp`` 真值表。**``[cc] hp_table`` > ``CC_HP_TABLE``**。

    ini 写法的键值对用 ``:`` 分隔、``,`` 或 ``;`` 分隔条目，例如::

        hp_table = 1:1000,7:6000,101:10000

    写了 ini 就**整表替换**（不是合并）—— 免得「我明明改了 7，怎么还是 6000」
    这类要靠猜的事故。解析不出任何一项时回退代码里的 ``CC_HP_TABLE``。
    """
    raw, _ = _ccKnobRaw(CC_HP_TABLE_ENV, "hp_table", "")
    if not raw:
        return dict(CC_HP_TABLE)
    table = {}
    for part in str(raw).replace(";", ",").split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        key, _, value = part.partition(":")
        try:
            table[int(float(key.strip()))] = int(float(value.strip()))
        except ValueError:
            continue          # 坏项直接跳过，别让一行笔误毁掉整张表
    return table or dict(CC_HP_TABLE)


def hpForTid(tid, table=None):
    """按 ``tid`` 取该物件的 hp **真值**；表里没有 → 回退 ``ccHp()``。

    回退而不是报错，是因为「猜一个值」比「保持改动前行为」危险得多：
    tid 认不出时，宁可与历史逐位一致。
    """
    try:
        key = int(tid)
    except (TypeError, ValueError):
        return ccHp()
    lookup = ccHpTable() if table is None else table
    if key in lookup:
        return int(lookup[key])
    return ccHp()


def ccHpTableTag():
    """自证短标记：``flat`` = 全表与兜底同值；否则列出**与兜底不同**的项。

    用途同上那三条自证纪律 —— 「表改了但没生效」必须一眼能看出来。
    """
    try:
        table = ccHpTable()
        base = ccHp()
        diff = ["%d:%d" % (k, v) for k, v in sorted(table.items())
                if int(v) != base]
        return "flat" if not diff else "table[" + ",".join(diff) + "]"
    except Exception:  # noqa: BLE001 —— 自证行绝不能把服务端带崩
        return "err"


def ccHavokIdxTableTag():
    """自证短标记：``flat`` = 表空（等于改动前）；否则列出 ``tid:hidx``。

    用途同 ``ccHpTableTag()`` —— 「表改了但没生效」必须一眼看出来。
    """
    try:
        table = ccHavokIdxTable()
        if not table:
            return "flat"
        return "tbl[" + ",".join("%d:%d" % (k, v) for k, v in sorted(table.items())) + "]"
    except Exception:  # noqa: BLE001 —— 自证行绝不能把服务端带崩
        return "err"


def ccLadderLiftTag():
    """自证短标记：``<全局值>+<逐架表>``，例：``0+tbl[10015:0,40028:4,40040:6.5]``。

    用途同上 —— 「旋钮改了但没生效」必须一眼看出来。这里**把两个旋钮都摊开**：
    全局值在前、逐架表在后，所以 ``lift=0+tbl[...]`` 表示「全局是 0，但表在生效」
    —— 这两个数**不一致**才是正常状态，别拿它当 bug。
    """
    try:
        lift = ccLadderLift()
        head = "0" if lift == 0.0 else ("%g" % lift)
        return head + "+" + ccLadderLiftTableTag()
    except Exception:  # noqa: BLE001 —— 自证行绝不能把服务端带崩
        return "err"


def ccStateEnabled():
    """CC 物件要不要带真实 MO 状态。**环境变量 > ``[cc] state`` > ``CC_STATE_ENABLED``**。

    默认 **关**，见常量处说明。三态：``on`` / ``off`` / 其他 = 默认。
    """
    raw, _ = _ccKnobRaw(CC_STATE_ENV, "state",
                        "on" if CC_STATE_ENABLED is True else "off")
    return _ccTrue(raw, CC_STATE_ENABLED is True)


def ccRidBase():
    """CC 物件 rid 的起始值。**环境变量 > ``[cc] rid_base`` > ``CC_RID_BASE``**。

    下限 3 —— 1 和 2 被本地玩家 / 敌人占了，撞上去会把它们的视野顶掉。
    """
    raw, _ = _ccKnobRaw(CC_RID_ENV, "rid_base", str(CC_RID_BASE))
    try:
        return max(3, int(float(raw)))
    except ValueError:
        return CC_RID_BASE


def ridFor(index):
    """第 ``index`` 个 CC 物件（1 起）的视野 mid。"""
    return ccRidBase() + index


# ⭐⭐⭐ 2026-09-24 16:10 新增：**动态 origin 覆盖**（云梯立起时用）
#
# 为什么需要它
# ------------
# 实机 + 抓包（2026-09-24 15:54~16:04）证明：客户端**完全采纳** CC 物件的 ``pos``，
# 但把 ``pos`` 当作**模型原点**，然后**绕它转**（``MO_INCLINE_NTF`` 只给标量角度）。
# 而梯脚在模型局部 ``X = -8.038``（不在原点上）⇒ 一转就整体错位：
#
#     梯脚 z    = pos.z - 8.038·sinθ   ⇒ θ≈45° 时**入地 5.71 m**（用户说的「半截」）
#     梯顶 水平 = pos + 7.970·cosθ     ⇒ 多伸 2.35 m ⇒ **插进墙体**（用户说的「穿墙」）
#
# 修正 = 把 ``pos`` 挪到**正确的模型原点**：
#
#     origin = pos + foot·d - foot·d2        （= ``siege._ladderPose(pivot="foot")``）
#
# 而躺地（θ=0）时 ``d2 == d`` ⇒ origin 恰好等于原 ``pos`` ⇒ **修正量随 θ 变**
# ⇒ 必须**动态**，不能像 ``ladder_lift_table`` 那样在关卡加载时一次性写死。
#
# ⚠️ 静态版会让**躺地的梯子浮空** —— 用户 2026-09-24 15:45 明确否决
#    （「梯子在半空，不能用」）。所以这里做成**可运行时覆盖**：
#    ``siege.onLadderState`` 在立起时 ``set``、躺地时 ``clear``，
#    躺地状态**零影响**（回归底线）。
#
# ⚠️ 与 ``ladder_lift_table`` 的关系：本表**优先**（它是完整的三轴修正，
#    后者只是 z 的静态近似，且已被否决）。两者同时非空时本表赢。
_DYNAMIC_ORIGIN = {}


def setDynamicOrigin(rid, xyz):
    """把 ``rid`` 这架云梯的**模型原点**钉到世界坐标 ``xyz``；``None`` = 清除。"""
    if xyz is None:
        _DYNAMIC_ORIGIN.pop(int(rid), None)
        return
    _DYNAMIC_ORIGIN[int(rid)] = (float(xyz[0]), float(xyz[1]), float(xyz[2]))


def clearDynamicOrigin(rid=None):
    """清除动态 origin（``rid=None`` ⇒ 全部清）。"""
    if rid is None:
        _DYNAMIC_ORIGIN.clear()
    else:
        _DYNAMIC_ORIGIN.pop(int(rid), None)


def ccDynamicOriginFor(rid):
    """该 ``rid`` 的动态 origin；没设过 ⇒ ``None``（走原 ``pos``）。"""
    return _DYNAMIC_ORIGIN.get(int(rid))


def ccDynamicOriginReport():
    """一行摘要，给日志/自检用。没设过时**必须**是 ``dyn-origin=0``（零影响自证）。"""
    if not _DYNAMIC_ORIGIN:
        return "dyn-origin=0"
    parts = ["%d:(%.3f,%.3f,%.3f)" % (k, v[0], v[1], v[2])
             for k, v in sorted(_DYNAMIC_ORIGIN.items())]
    return "dyn-origin=%d[%s]" % (len(parts), ",".join(parts))


# ⭐⭐⭐ 2026-09-24 16:50 新增：**动态朝向覆盖**（与 ``_DYNAMIC_ORIGIN`` 对称）
#
# 为什么还要改朝向
# ----------------
# 实机（16:29 会话 13976-337307418）证明：**重发 CC 物件会把该物件「重置」**。
# 抓包时序（1002 事件，同一毫秒内）：
#
#     [S] mo-update-state-mo-interact-1002          ← 先切状态
#     [S] instance-cc-dynamic-vision-add-event-by-mid   ← 再重发物件（243 B）
#     [S] mo-incline-10010-1-of-8 … 8-of-8          ← 最后推倾角
#
# 而 CC 记录里带两个「姿态字段」：
#   · ``state``    —— 我们一直发 **0**（``E_MO_STATE_INVALID``，因为 ``ccStateEnabled()`` 默认关）
#   · ``rotation`` —— 我们一直**透传** ``ccobject.json`` 的 ``face``，
#                     而 ``face`` 是**躺地**朝向（云梯1 ≈ ``(0.002, 0.038, 0.162)``，三个角都近 0）
#
# ⇒ 客户端按这两个字段把梯子摆成**躺地**，且把之前 ``update_state-1002`` 的姿态覆盖掉。
#   实测结果就是「**整架水平悬在半空**」（用户 16:32 截图）。
#
# 修法：重发时**同时**给「正确的位置」和「正确的朝向」。
# 朝向按**欧拉角**给（与 ``face`` 同构，见 §11）：梯子长轴是模型局部 X，
# 绕局部 Y 转就是俯仰 ⇒ 立起 = ``rotation = (face[0], ±θ, face[2])``。
#
# ⚠️ **俯仰的正负号还没实测**（右手系 ``Rz·Ry·Rx`` 下 ``+X`` 会被转到 ``−Z``，
#    所以「向上抬」应是 ``−θ``；但客户端可能是左手系）。
#    ⇒ 做成**逐架可配**（``siege`` 的 ``ladder_org_tilt_table``），
#    一次上机让三架各试一个符号，一次就能定下来。
_DYNAMIC_ROTATION = {}


def setDynamicRotation(rid, xyz):
    """把 ``rid`` 这架云梯的**朝向（欧拉角，弧度）**钉到 ``xyz``；``None`` = 清除。"""
    if xyz is None:
        _DYNAMIC_ROTATION.pop(int(rid), None)
        return
    _DYNAMIC_ROTATION[int(rid)] = (float(xyz[0]), float(xyz[1]), float(xyz[2]))


def clearDynamicRotation(rid=None):
    """清除动态朝向（``rid=None`` ⇒ 全部清）。"""
    if rid is None:
        _DYNAMIC_ROTATION.clear()
    else:
        _DYNAMIC_ROTATION.pop(int(rid), None)


def ccDynamicRotationFor(rid):
    """该 ``rid`` 的动态朝向；没设过 ⇒ ``None``（透传原 ``face``）。"""
    return _DYNAMIC_ROTATION.get(int(rid))


def ccDynamicRotationReport():
    """一行摘要。没设过时**必须**是 ``dyn-rot=0``（零影响自证）。"""
    if not _DYNAMIC_ROTATION:
        return "dyn-rot=0"
    parts = ["%d:(%.3f,%.3f,%.3f)" % (k, v[0], v[1], v[2])
             for k, v in sorted(_DYNAMIC_ROTATION.items())]
    return "dyn-rot=%d[%s]" % (len(parts), ",".join(parts))


# ⭐⭐⭐⭐ 2026-09-29 17:2x 新增：**动态「左右旋转目标」覆盖**（``horizontal_angle``）
#
# 为什么又开一条通道 —— 因为 ``rotation`` 对攻城车**根本不生效**
# ---------------------------------------------------------------
# 客户端 metalib（``TieJiClient.exe`` / ``sh_proto_cs``，
# ``CS_PROTO_VISION_CC_DYNAMIC_INFO``）的**字段描述**是决定性的：
#
#     rotation         = "静态物件，初始可能有绕 **x,y** 轴旋转"   ← 静态倾斜，**不是偏航**
#     horizontal_angle = "**左右旋转目标弧度**"                    ← **这才是偏航（左右转向）**
#     vertical_angle   = "上下旋转目标弧度"
#
# 实机铁证（2026-09-29 17:0x，会话 ``48236-771505160``，江陵城 ``yjc_low``）：
# 服务端把 rid 10004 的 ``rotation.z`` 从 ``−41.22°`` 改成 ``+105.09°``、
# rid 10006 从 ``+29.75°`` 改成 ``+139.91°``（wire 逐字节核对**无误**，
# 见 ``_probe_car_rot.py``），用户仍报「**江陵城，两个攻城车，角度没有变**」
# ⇒ ``rotation`` 对攻城车**不生效**。
#
# 而 ``horizontal_angle`` **从未被下发过**（全库恒为 ``0.0``）——
# 此前否证的三条通道是 ``rotation`` / 下行 ``direction_yaw`` / ``MO_RB_TRANSFORM``，
# **没有一条是它**。字段描述与需求（左右转向）逐字吻合 ⇒ 首选。
#
# 语义：``0.0`` 保持既有行为（= 用场景 ``face`` / 客户端自己的数据），
#      非 0 ⇒ 客户端把物件**左右转到该目标弧度**。
# ⚠️ 因此本通道**默认只对攻城车**开（``car_pose_hangle=on``），
#    其余 20+ 个物件仍是 ``0.0`` ⇒ **与改动前逐位一致**（回归底线）。
_DYNAMIC_HANGLE = {}


def setDynamicHangle(rid, angle):
    """把 ``rid`` 的**左右旋转目标弧度**钉到 ``angle``；``None`` = 清除（回 0.0）。"""
    if angle is None:
        _DYNAMIC_HANGLE.pop(int(rid), None)
        return
    _DYNAMIC_HANGLE[int(rid)] = float(angle)


def clearDynamicHangle(rid=None):
    """清除动态左右旋转目标（``rid=None`` ⇒ 全部清）。"""
    if rid is None:
        _DYNAMIC_HANGLE.clear()
    else:
        _DYNAMIC_HANGLE.pop(int(rid), None)


def ccDynamicHangleFor(rid):
    """该 ``rid`` 的动态左右旋转目标；没设过 ⇒ ``None``（发 ``0.0``）。"""
    return _DYNAMIC_HANGLE.get(int(rid))


def ccDynamicHangleReport():
    """一行摘要。没设过时**必须**是 ``dyn-hang=0``（零影响自证）。"""
    if not _DYNAMIC_HANGLE:
        return "dyn-hang=0"
    parts = ["%d:%.4f" % (k, v) for k, v in sorted(_DYNAMIC_HANGLE.items())]
    return "dyn-hang=%d[%s]" % (len(parts), ",".join(parts))


# ⭐⭐⭐⭐ 2026-09-29 18:4x 新增：**动态「上下旋转目标」覆盖**（``vertical_angle``）
#
# 用户实机口供（18:48，带图，樊城）：
#   「樊城这个，**还是在墙里**，，**有点低了**，，能**往上改改角度**」
# ⇒ 梯子要**抬头**。
#
# 取证（客户端 metalib，``CS_PROTO_VISION_CC_DYNAMIC_INFO``，见 ``probe_tdr_fields.py``）：
#   * ``[ 5] rotation``         = 「静态物件，初始可能有绕 **x,y** 轴旋转」← 连 z 都没提
#   * ``[23] horizontal_angle`` = 「左右旋转目标弧度」              ← 偏航（§17 已用）
#   * ``[24] vertical_angle``   = 「**上下旋转目标弧度**」           ← **抬头就是这条**
#
# 三条车的 ``face`` 都是 ``[-0.0, -0.0, yaw]`` ⇒ ``rotation.x = rotation.y = 0``，
# 而 ``vertical_angle`` **全库恒 ``0.0``** ⇒ 梯子**天生水平**（与截图一致）。
#
# ⚠️ 与 ``_DYNAMIC_HANGLE`` **完全对称**：``objects()`` 里取值顺序同样是
#    「动态覆盖 > 出生现算 > 0.0」。默认只对攻城车非 0 ⇒ 其余物件逐位不变。
_DYNAMIC_VANGLE = {}


def setDynamicVangle(rid, angle):
    """把 ``rid`` 的**上下旋转目标弧度**钉到 ``angle``；``None`` = 清除（回 0.0）。"""
    if angle is None:
        _DYNAMIC_VANGLE.pop(int(rid), None)
        return
    _DYNAMIC_VANGLE[int(rid)] = float(angle)


def clearDynamicVangle(rid=None):
    """清除动态上下旋转目标（``rid=None`` ⇒ 全部清）。"""
    if rid is None:
        _DYNAMIC_VANGLE.clear()
    else:
        _DYNAMIC_VANGLE.pop(int(rid), None)


def ccDynamicVangleFor(rid):
    """该 ``rid`` 的动态上下旋转目标；没设过 ⇒ ``None``（走出生现算 / 0.0）。"""
    return _DYNAMIC_VANGLE.get(int(rid))


def ccDynamicVangleReport():
    """一行摘要。没设过时**必须**是 ``dyn-vang=0``（零影响自证）。"""
    if not _DYNAMIC_VANGLE:
        return "dyn-vang=0"
    parts = ["%d:%.4f" % (k, v) for k, v in sorted(_DYNAMIC_VANGLE.items())]
    return "dyn-vang=%d[%s]" % (len(parts), ",".join(parts))


# ⭐⭐⭐ 2026-09-27：**CC 朝向的 yaw 符号翻转**（默认关 ⇒ 逐字节不变）
#
# 起因：江陵城实机「外面两台攻城车方向不对」（用户 14:4x 反馈）。
#
# 离线取证（全部只读，见工作区 t7_probe_cc.py / 015876 场景表）：
#   · ``ccobject.json`` 的 ``face`` 与**客户端自己的场景文件**
#     ``015876_ObjectsFolder_1fb3b6b7.xml`` 的 ``Rotation`` **逐位相同**（29/29）
#     ⇒ 我们发出去的就是原图的值，**不是提取错**。
#   · 两台攻城车的 ``face`` 与其**移动路径**严格对应：
#       攻城车2  faceZ = -88.68°  ↔  路径方位角  85.09°（向北推）
#       攻城车1  faceZ = -118.12° ↔  路径方位角 119.91°（向西北推）
#     即 ``faceZ ≈ −路径方位角`` ⇒ **数据自洽**。
#   · 同图 24 个物件里只有攻城车1 离群 29°（投石车 -89/-95°、云梯 -90°、攻城车2 -88.7°），
#     而那 29° 正是它自己的路径方位差 ⇒ 也是对的。
#   ⇒ 若实机看着是「背对城墙 / 倒着走」，就只剩一种解释：
#     **CC 协议里 ``rotation`` 字段的 yaw 约定与场景文件 ``Rotation`` 反号**。
#     这一环 ``ccobject.py`` 自己早就标注过「协议字段本身的字节语义仍未实测」
#     （见 §10.1 那段注释），所以这是**尚未证伪的唯一嫌疑**。
#
# 用法（``level.ini`` 的 ``[cc]`` 段，或环境变量 ``T7_CC_ROT_FLIP_YAW``）：
#     rot_flip_yaw=off   ; 默认：透传 face，一个字节都不动
#     rot_flip_yaw=on    ; 全部物件的 yaw 取反（rz -> -rz）
#     rot_flip_yaw=2     ; 只翻 tid=2（攻城车）；逗号分隔可写多个
#
# ⚠️ 这是**待实测假设**，不是已证结论。默认关 ⇒ 与改动前逐位一致。
CC_ROT_FLIP_YAW_ENV = "T7_CC_ROT_FLIP_YAW"


def ccRotFlipYaw():
    """``(是否全部, frozenset(tid))``；默认 ``(False, frozenset())`` = 不翻。"""
    raw, _ = _ccKnobRaw(CC_ROT_FLIP_YAW_ENV, "rot_flip_yaw", "")
    text = str(raw).strip().lower()
    if text in ("", "0", "off", "false", "no"):
        return False, frozenset()
    if text in ("1", "on", "true", "yes", "all"):
        return True, frozenset()
    tids = set()
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if part:
            try:
                tids.add(int(part))
            except ValueError:
                pass
    return False, frozenset(tids)


def ccRotFlipYawTag():
    """一行摘要给启动自证行用；默认**必须**是 ``off``。"""
    every, tids = ccRotFlipYaw()
    if every:
        return "all"
    if not tids:
        return "off"
    return ",".join(str(t) for t in sorted(tids))


_cache = {}
_noticed = set()


def _sceneDir():
    """定位 ``data/scene``：**逐级向上找**。

    ⚠️ 与 ``controls._sceneDir()`` 同一套逻辑，理由也一样：脚本会被快照到
    ``server/data/<会话>/revisions/t7rev_<hash>/`` 里跑，那条路径下没有
    ``data/scene``，只用 ``__file__`` 上两级会**加载不到**。
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


def ccObjectLimit():
    """本进程要发几个 CC 物件。``None`` = 不发。**环境变量 > ``[cc] objects`` > 默认**。

    * ``off`` / ``0`` → ``None``
    * ``on`` / ``all`` / ``true`` / ``yes`` → 全部（内部用 1<<30 表示）
    * ``<N>``（正整数）→ ``N``（二分定位用）
    * 未设置 → ``None`` 或 1<<30（看 ``CC_OBJECT_ENABLED``）
    """
    raw, _ = _ccKnobRaw(CC_ENV_SWITCH, "objects",
                        "on" if CC_OBJECT_ENABLED is True else "off")
    text = str(raw).strip().lower()
    if text in ("", "0", "off", "false", "no"):
        return None
    if text in ("on", "all", "true", "yes"):
        return 1 << 30 if CC_OBJECT_ENABLED is True else None
    try:
        return max(1, int(float(text)))
    except ValueError:
        return 1 << 30 if CC_OBJECT_ENABLED is True else None


def loadScene(scene, baseDir=None):
    """读 ``data/scene/<场景>/ccobject.json``，返回**原始** dict 列表。

    文件不存在 → 返回 ``[]``（不是错误：只有樊城有这份数据）。
    解析失败 → 抛 ``ValueError``，由调用方决定是否吞掉。
    """
    if not scene:
        return []
    root = baseDir or _sceneDir()
    path = os.path.join(root, scene, CC_FILE)
    if not os.path.isfile(path):
        return []
    with io.open(path, encoding="utf-8") as handle:
        raw = json.load(handle)
    items = raw.get("objects")
    if not isinstance(items, list):
        raise ValueError("%s: 'objects' is not a list" % path)
    return items


# ⭐ 2026-09-21：res_id / inst_id 的正确取值 —— 从客户端场景实体表反推出来。
#
# 取证（d:/dfjq_out/map，樊城 010493~010512 段）：
#   * ``010504_ObjectsFolder_154a8002.xml``（Sheet）里每个 GameObject 有
#     ``<ID>`` 和 ``<PathID>``，24 条与 ccobject.json 一一对应。
#   * 关键对照（投石车）：
#         客户端 GameObject : ID=14  PathID=40030  pos=507.978,552.919,44.0541
#         ccobject.json     : tid=14  pathId=40030  id=2384  pos 同上
#     ⇒ **客户端 GameObject 的 ``ID`` == ``cc_tid``**（类型号），``PathID`` == pathId。
#   * 我们此前把 ``res_id`` 填成 ccobject 的 ``id``（2384）。客户端拿 2384 去查
#     CC 类型表查不到 ⇒ 投石车等器械**渲染不出来**（实机：看不到车）。
#
# 修正：``res_id`` = ``cc_tid``（客户端认的类型号），``inst_id`` = ``pathId``
# （场景内唯一实例标识，取低 16 位适配 TDR smalluint）。
# 环境变量 ``T7_CC_ID_MODE=old`` 可回退到"res_id=id / inst_id=序号"的旧行为。
CC_ID_MODE_ENV = "T7_CC_ID_MODE"
CC_ID_MODE_DEFAULT = "tid"


def ccIdMode():
    """``res_id`` / ``inst_id`` 的取值约定。**环境变量 > ``[cc] id_mode`` > ``tid``**。

    ``old``（或 ``legacy``）→ 回退到「res_id=id / inst_id=序号」的旧行为（A/B 用）；
    其他任何值 → ``tid``。
    """
    raw, _ = _ccKnobRaw(CC_ID_MODE_ENV, "id_mode", CC_ID_MODE_DEFAULT)
    if raw.strip().lower() in ("old", "legacy", "0", "off"):
        return "old"
    return CC_ID_MODE_DEFAULT


def _birthRotation(item, face):
    """**出生朝向**：攻城车按「路径方位 + 偏置」摆正；其余物件原样透传 ``face``。

    ⭐ 2026-09-29 16:5x：用户实机口供「**江陵城，梯子方向一直没变 —— 出生点应该就变的**」。

    由来：车**不移动**时 ``_DYNAMIC_ROTATION`` 没设 ⇒ 这里透传场景 ``face``，
    而江陵城车的 ``face`` 是**歪的**（rid 10004 = ``−41.22°``，而正确应为
    ``路径方位 + car_pose_yaw_offset`` = ``85.09 + 20`` = **``105.09°``**）
    ⇒ 车一出生梯子方向就错、且一直不变（只有移动时 ``siege._applyDrivePose``
    才会改写）。

    这里在**出生时就**按路径方向摆正，用的是**与 ``_applyDrivePose`` 同一个公式**
    （``siege.carPoseYaw``）⇒ 「出生朝向」与「移动朝向」完全一致。

    ⚠️ 兜底：非攻城车 / 没有 ``path`` / 路径退化为一点 / ``car_pose_face=off`` /
    任何异常 ⇒ **原样返回场景 ``face``**（宁可不改，也不编一个角度出来 ——
    与 ``initialMoState`` 同一条纪律）。懒导入 ``siege`` 是为了避开模块级循环依赖。
    """
    base = (float(face[0]), float(face[1]), float(face[2]))
    try:
        from . import siege
        if int(item.get("tid")) != siege.CITY_CAR_TID:
            return base
        if not siege.carPoseFace():
            return base
        goal = siege.carPathGoal(item)
        if goal is None:
            return base
        pos = item.get("pos") or (0.0, 0.0, 0.0)
        dx = float(goal[0]) - float(pos[0])
        dy = float(goal[1]) - float(pos[1])
        if abs(dx) < 1e-9 and abs(dy) < 1e-9:
            return base
        return (base[0], base[1], siege.carPoseYaw(math.atan2(dy, dx)))
    except Exception:  # noqa: BLE001 —— 附加能力，坏了不能拖垮 CC 下发
        return base


def _birthHangle(item, face):
    """**出生「左右旋转目标」**（``horizontal_angle``，弧度）—— 真正管偏航的那条通道。

    ⭐ 2026-09-29 17:2x：见 ``_DYNAMIC_HANGLE`` 的取证块。``rotation`` 对攻城车
    不生效（服务端发对了、用户仍报「角度没有变」）⇒ 改用 metalib 里描述为
    「**左右旋转目标弧度**」的 ``horizontal_angle``。

    用的是**与 ``_birthRotation`` / ``siege._applyDrivePose`` 同一个公式**
    （``siege.carPoseYaw`` = ``航向 + car_pose_yaw_offset``）⇒
    「出生朝向」「移动朝向」「rotation.z」三者**永远一致**，不会各说各话。

    ⚠️ 兜底（一律返回 ``0.0`` = 保持既有行为）：非攻城车 / ``car_pose_face=off`` /
    ``car_pose_hangle=off`` / 没有 ``path`` / 路径退化为一点 / 任何异常。
    """
    try:
        from . import siege
        if int(item.get("tid")) != siege.CITY_CAR_TID:
            return 0.0
        if not siege.carPoseFace() or not siege.carPoseHangle():
            return 0.0
        goal = siege.carPathGoal(item)
        if goal is None:
            return 0.0
        pos = item.get("pos") or (0.0, 0.0, 0.0)
        dx = float(goal[0]) - float(pos[0])
        dy = float(goal[1]) - float(pos[1])
        if abs(dx) < 1e-9 and abs(dy) < 1e-9:
            return 0.0
        return float(siege.carPoseYaw(math.atan2(dy, dx)))
    except Exception:  # noqa: BLE001 —— 附加能力，坏了不能拖垮 CC 下发
        return 0.0


def _birthVangle(item, face, scene=None):
    """**出生「上下旋转目标」**（``vertical_angle``，弧度）—— 架梯**抬头 / 低头**。

    ⭐ 2026-09-29 18:4x：见 ``_DYNAMIC_VANGLE`` 的取证块。三条车的 ``face`` 的
    ``x`` / ``y`` 都是 ``0``、``vertical_angle`` **全库恒 0** ⇒ 梯子**天生水平**
    （用户 18:48：「樊城这个还是在墙里，有点低了，能往上改改角度」）。

    取值 = ``siege.carPoseVangle(item, scene)`` —— ``car_pose_vangle=auto``（默认）
    时**按地图数据现算**：``θ = atan2(墙顶 z − 车 z, 到墙水平距离)``
    （城墙取自 ``acollision.json`` 的竖直墙面格；与云梯同一套「用地图数据 + 公式」）。

    ⚠️ 兜底（一律返回 ``0.0`` = 保持既有行为）：非攻城车 /
    ``car_pose_vangle=0`` / 任何异常。
    """
    try:
        from . import siege
        if int(item.get("tid")) != siege.CITY_CAR_TID:
            return 0.0
        if not siege.carPoseVangleOn(item, scene):
            return 0.0
        return float(siege.carPoseVangle(item, scene))
    except Exception:  # noqa: BLE001 —— 附加能力，坏了不能拖垮 CC 下发
        return 0.0


def _rotxOffset(item):
    """**备用**俯仰通道：给 ``rotation.x`` 加的偏置（弧度）。默认 **0.0**（逐位不变）。

    metalib 说 ``rotation`` 是「静态物件，初始可能有绕 **x,y** 轴旋转」——
    ``rotation.z`` 对车不生效已被 §14~§17 证死，但 **x / y 没试过**。
    本函数给 ``rotation.x`` 加一个可调偏置（``car_pose_rotx``，度，默认 0）
    ⇒ 若 ``vertical_angle`` 那条不通，改这个旋钮就能试第二条，**不用改代码**。

    非攻城车 / 旋钮为 0 / 任何异常 ⇒ ``0.0``（不做任何加法）。
    """
    try:
        from . import siege
        if int(item.get("tid")) != siege.CITY_CAR_TID:
            return 0.0
        return math.radians(siege.carPoseRotxDeg())
    except Exception:  # noqa: BLE001
        return 0.0


def objects(scene, baseDir=None):
    """``ccobject.json`` → ``tuple[CcDynamicObject, ...]``（按 limit 截断）。"""
    limit = ccObjectLimit()
    if limit is None:
        return ()
    items = loadScene(scene, baseDir)
    if not items:
        return ()
    mode = ccIdMode()
    # 真值表只读一次（_ccKnobRaw 背后是进程级缓存，但没必要循环里问 24 遍）。
    hpTable = ccHpTable()
    liftTable = ccLadderLiftTable()
    # ⭐ 2026-09-27：yaw 翻转开关（默认关）。**循环外读一次** —— 每物件读一遍
    #    等于每物件读一次 ini，纯浪费。见 ``CC_ROT_FLIP_YAW_ENV``。
    _rotFlipAll, _rotFlipTids = ccRotFlipYaw()
    out = []
    for index, item in enumerate(items[:limit], start=1):
        # ⭐ 2026-09-24 16:10：``rid`` **提前算** —— 下面要用它查**动态 origin**，
        #    末尾 ``CcDynamicObject(rid=...)`` 也用它。``ridFor`` 每次都会读旋钮，
        #    循环里调两遍没必要。
        rid = ridFor(index)
        pos = item.get("pos") or (0.0, 0.0, 0.0)
        face = item.get("face") or (0.0, 0.0, 0.0)
        tid = int(item.get("tid", item["id"]))
        if mode == "old":
            inst_id = index
            res_id = int(item["id"])
        else:
            # 修正：res_id = cc_tid（客户端场景实体 ID），inst_id = pathId 低 16 位
            inst_id = int(item.get("pathId", index)) & 0xFFFF
            res_id = tid
        # ⭐⭐ 2026-09-24：云梯（``cc_tid == 1``）的 **Z 抬升**，见 ``CC_LADDER_LIFT``。
        #    作用在 **z**（高度）上 —— 用户已纠正过轴序：``pos`` 是 ``[x, y, z]``。
        #    ⭐ 01:30 升级为**逐架**：三架云梯的 ``tid`` 都是 1，只有 ``pathId`` 分得开，
        #    所以用 ``ladder_lift_table`` 一行写三个值 ⇒ **一次重启对比三档**。
        #    默认空表 + ``ladder_lift=0`` ⇒ 这一行对报文字节**零影响**。
        #    只抬云梯：其余 21 个物件走原值，不构成多变量。
        # ⭐⭐⭐ 2026-09-24 16:10：**动态 origin 覆盖**（见 ``_DYNAMIC_ORIGIN`` 注释块）。
        #    立起时 ``siege`` 会把该架的模型原点钉到 ``_ladderPose(pivot="foot")``
        #    算出的点（**三轴都动**）；躺地时清除 ⇒ 走原 ``pos``。
        #    没设过 ⇒ 走 else 分支 ⇒ **与改动前逐位一致**（回归底线）。
        _dyn = ccDynamicOriginFor(rid)
        if _dyn is not None:
            pos = _dyn
            z = float(_dyn[2])
        else:
            z = float(pos[2])
            z += ccLadderLiftFor(tid, item.get("pathId"), liftTable)
        # ⭐⭐⭐ 2026-09-24 16:50：**动态朝向覆盖**（见 ``_DYNAMIC_ROTATION`` 注释块）。
        #    重发 CC 物件会把姿态「重置」成这里给的值 —— 所以立起时必须给**立起**的朝向，
        #    否则客户端把梯子摆回躺地（= 整架水平悬空）。
        #    没设过 ⇒ 透传原 ``face`` ⇒ **与改动前逐位一致**（回归底线）。
        _drot = ccDynamicRotationFor(rid)
        if _drot is not None:
            rotation = _drot
        else:
            rotation = _birthRotation(item, face)
        # ⭐ 2026-09-27：yaw 符号翻转（默认关，见 ``CC_ROT_FLIP_YAW_ENV``）。
        #    刻意放在**两条分支之后** ⇒ 动态朝向与透传 face 一起翻，
        #    不会出现「立起朝一个方向、躺平朝另一个」。开关关时这一行是恒等变换。
        if _rotFlipAll or tid in _rotFlipTids:
            rotation = (float(rotation[0]), float(rotation[1]),
                        -float(rotation[2]))
        # ⭐⭐ 2026-09-29 18:4x：**备用**俯仰通道 —— 给 `rotation.x` 加偏置。
        #    metalib 说 `rotation` 是「静态物件绕 x,y 轴旋转」，而 `rotation.z` 对车
        #    不生效已被证死；x 没试过 ⇒ 留 `car_pose_rotx`（度，默认 0）。
        #    默认 0 时 `_rotx` 为 0.0 ⇒ 这一行**不做任何加法**（逐位不变）。
        _rotx = _rotxOffset(item)
        if _rotx:
            rotation = (float(rotation[0]) + _rotx, float(rotation[1]),
                        float(rotation[2]))
        # ⭐⭐⭐⭐ 2026-09-29 17:2x：**左右旋转目标**（`horizontal_angle`）——
        #    真正管偏航的那条通道（见 `_DYNAMIC_HANGLE` 取证块）。
        #    取值顺序与 rotation 完全对称：动态覆盖 > 出生现算 > 0.0（保持既有行为）。
        #    ⚠️ 默认只有**攻城车**非 0 ⇒ 其余物件仍是 0.0，逐位不变。
        _dhang = ccDynamicHangleFor(rid)
        if _dhang is not None:
            hangle = float(_dhang)
        else:
            hangle = _birthHangle(item, face)
        # ⭐⭐⭐⭐ 2026-09-29 18:4x：**上下旋转目标**（`vertical_angle`）—— 架梯抬头。
        #    与 hangle 完全对称：动态覆盖 > 出生现算 > 0.0（保持既有行为）。
        #    见 `_DYNAMIC_VANGLE` 取证块（用户「有点低了，能往上改改角度」）。
        _dvang = ccDynamicVangleFor(rid)
        if _dvang is not None:
            vangle = float(_dvang)
        else:
            vangle = _birthVangle(item, face, scene)
        out.append(vision_flow.CcDynamicObject(
            inst_id=inst_id,
            res_id=res_id,
            # rid = 客户端挂载对象用的视野 mid。**必须唯一**，见模块顶部 CC_RID_BASE。
            # 改动前这里是默认 0 —— 24 个物件全撞在一起，客户端一个都挂不上。
            rid=rid,
            position=(float(pos[0]), float(pos[1]), z),
            # ⚠️ 2026-09-24 更正：这里原先写着「face 是个方向向量，语义未实测」——
            #    **不是方向向量，是欧拉角（弧度）**。取证：``010500_..._TAB`` 里
            #    正门 face=[-0,-0,0.2055] / 侧门 [0,0,1.6102] / 正门机关 [0,0,-2.9763]，
            #    全是「只绕 Z 转的 yaw」；且客户端自己的场景实体
            #    （``001506_37bc216.xml``）同一槽位写的是 ``Rotation="0,0,-1.5708"``。
            #    ⇒ 透传 ccobject 的 face 与客户端写法**同构**。
            #    （协议字段本身的字节语义仍未实测，只是不再拿错误的假设当依据。）
            rotation=(float(rotation[0]), float(rotation[1]), float(rotation[2])),
            camp=int(item.get("camp", 0)),
            # ⭐⭐ 2026-09-23 深夜：hp 由「统一一个值」改为**按 tid 查真值表**。
            #    见 CC_HP_TABLE 处的取证（门 6000 / 屋顶 10000，其余 1000）。
            hp=hpForTid(res_id, hpTable),
            # ⭐ 2026-09-20：带上**合法的初始 MO 状态**。
            #    改动前这里一律是默认 0，而 0 正是 ``E_MO_STATE_INVALID``（未定义）。
            #    客户端的交互前置检查里有一条
            #    ``E_MO_INTERACT_PRE_CHECK_MO_STATE = 4``（MO交互前置检查_MO状态），
            #    状态非法 ⇒ 按 C 的请求在本地就被吞掉 —— 实测会话
            #    13496-380748287 里梯子**已经可见**，但 cmd=18 一条都没有。
            #    状态值取自 ``E_MO_STATE_*`` 宏表，映射见 ``mo.INITIAL_STATE_BY_TID``。
            state=initialMoState(item),
            # ⭐ 2026-09-23 深夜：havok_res_index 从「全局一个值」升级为**按物件**。
            #    flat（默认）= 全用 havok_idx；tid = 用该物件自己的 tid。
            #    取证见 CC_HAVOK_IDX_ENV 处（nav文件逐类型不同 + 表主键无 0）。
            havok_res_index=havokIdxForTid(res_id),
            # ⭐⭐⭐⭐ 2026-09-29 17:2x：**左右旋转目标弧度** —— 客户端真正用来
            #    把物件「左右转」的字段（metalib 描述逐字：「左右旋转目标弧度」）。
            #    ``0.0`` = 保持既有行为（非攻城车一律走这里）。
            horizontal_angle=float(hangle),
            # ⭐⭐⭐⭐ 2026-09-29 18:4x：**上下旋转目标弧度** —— 客户端真正用来
            #    把物件「上下转」的字段（metalib 描述逐字：「上下旋转目标弧度」）。
            #    架梯抬头 / 低头就靠它（``car_pose_vangle``）。
            #    ``0.0`` = 保持既有行为（非攻城车一律走这里）。
            vertical_angle=float(vangle),
        ))
    return tuple(out)


def initialMoState(item):
    """``ccobject.json`` 的一条记录 → 初始 MO 状态（``E_MO_STATE_*``）。

    ⚠️ 2026-09-20：**默认是关的**（返回 0），因为实机出现「加了 state 之后
    云梯看不见了」。``state`` 是这轮唯一改动的 CC 报文字段，先回到
    **已知可见**的状态，用 ``T7_CC_STATE=1`` 单独验证，出问题好二分。

    认不出类型时也返回 ``0``（= ``E_MO_STATE_INVALID``），保持与改动前一致 ——
    **宁可不改，也不要编一个状态出来**。
    """
    if not ccStateEnabled():
        return 0
    # 懒导入：mo 模块反过来要用本模块的 byRid()，模块级互相 import 会成环。
    try:
        from . import mo
    except ImportError:
        return 0
    state = mo.initialState(item.get("tid"))
    return state if state is not None else 0


def visionEvent(scene, now, baseDir=None):
    """返回一条可直接 ``flow.send(0xE, ...)`` 的报文；不发时返回 ``None``。

    ``None`` 的三种情况（都**不报错**）：开关关掉 / 场景没数据 / 一个都没截到。
    加载或编码失败抛异常，由调用方吞掉并记日志 —— 器械是**附加**能力，
    坏一个场景不该让整局进图流程崩掉（宁可少几个器械，也不能进不去图）。
    """
    items = objects(scene, baseDir)
    if not items:
        return None
    return vision_flow.encode_cc_dynamic_vision_add_event(
        objects=items, server_time_ms=now & 0xFFFFFFFFFFFFFFFF)


def visionEventFor(items, now):
    """把**指定的**几个 CC 物件编成一条 ADD_EVENT；空集返回 ``None``。

    用途：客户端走 ``VISION_GET_OBJECTS_REQ`` 按 mid 逐个要对象时，
    服务端得能只回它要的那一个（``CS_PROTO_VISION_GET_OBJECTS_REQ.obj_mids:uint64``）。
    """
    items = tuple(items)
    if not items:
        return None
    return vision_flow.encode_cc_dynamic_vision_add_event(
        objects=items, server_time_ms=now & 0xFFFFFFFFFFFFFFFF)


def byRid(scene, baseDir=None):
    """``{rid: CcDynamicObject}`` —— 按视野 mid 索引本场景 CC 物件。

    读盘/解析失败抛异常，由调用方吞掉（和 ``visionEvent`` 同一套纪律：
    器械是**附加**能力，坏了不该让整局进图流程崩掉）。
    """
    return {obj.rid: obj for obj in objects(scene, baseDir)}


def describe(scene, baseDir=None):
    """一行摘要，给日志用。不抛异常。"""
    limit = ccObjectLimit()
    if limit is None:
        return "disabled"
    try:
        items = loadScene(scene, baseDir)
    except (OSError, ValueError) as error:
        return "load-failed " + repr(error)
    if not items:
        return "no-data"
    names = [str(item.get("name") or item.get("id")) for item in items[:limit]]
    # ⭐ 2026-09-23：日志自证模式与血量 —— 实验变量（T7_CC_ID_MODE / T7_CC_HP）
    #    曾在不知情下残留，靠症状反推浪费一局。此后每条 cc-object-sent 都带
    #    mode/hp，现场即可核对。
    # ⭐ 2026-09-23 夜：三个值后面**再带上来源**（env/ini/default）。
    #    上一轮的教训是「值对了但来源错了也没人发现」——环境变量根本没进进程，
    #    日志里却看不出。现在 ``hidx=0(default)`` 与 ``hidx=1(ini)`` 一眼可分。
    mode = ccIdMode()
    hp = ccHp()
    hidx = ccHavokIdx()
    hidx_mode = ccHavokIdxMode()
    # ``hmode=tid`` 时 hidx 那一栏只是**兜底值**（真正的值是逐物件各自的 tid）。
    # ⭐ 2026-09-24：再加 ``lift<值>m``（云梯 Z 抬升）—— 这条链现在三个旋钮，
    #    任何一个没生效都会让实机表现"看着像没改"，必须逐条自证。
    return ("mode=%s(%s) hp=%d+%s(%s) hidx=%d+%s(%s,hmode=%s) lift=%s(%s)"
            " count=%d/%d sent=%d [%s]" % (
                mode, _CC_SOURCE.get("id_mode", "?"),
                hp, ccHpTableTag(), _CC_SOURCE.get("hp_table", "?"),
                hidx, ccHavokIdxTableTag(), _CC_SOURCE.get("havok_idx", "?"), hidx_mode,
                ccLadderLiftTag(),
                _CC_SOURCE.get("ladder_lift", "?"),
                len(items[:limit]), len(items), len(names), "、".join(names)))


_KNOB_KEYS = (("objects", "objects"), ("id_mode", "id_mode"), ("hp", "hp"),
              ("hp_table", "hp_table"),
              ("havok_idx", "havok_idx"), ("havok_idx_mode", "havok_idx_mode"),
              ("havok_idx_table", "havok_idx_table"),
              ("ladder_lift", "ladder_lift"),
              ("ladder_lift_table", "ladder_lift_table"),
              ("rid_base", "rid_base"),
              ("rot_flip_yaw", "rot_flip_yaw"),
              ("state", "state"))


def ccKnobsReport():
    """**全部 CC 旋钮的生效值 + 各自来源**，一行；不抛异常。

    格式：``objects=on id_mode=tid hp=1000+table[...] hidx=0+tbl[1:1] hmode=flat``
          `` lift=0 rid_base=10000 state=off``
          ``| objects=ini id_mode=ini ... ladder_lift=ini ...``

    为什么要这个：环境变量/ini 到底有没有被读到，此前**只能靠推断**
    （而推断错过三次）。这一行在服务端启动时打进 stdout，读一次就定案。
    """
    try:
        limit = ccObjectLimit()
        if limit is None:
            objects = "off"
        elif limit >= (1 << 30):
            objects = "all"
        else:
            objects = str(limit)
        values = ("objects=%s id_mode=%s hp=%d+%s hidx=%d+%s hmode=%s"
                  " lift=%s rid_base=%d rot_flip_yaw=%s state=%s" % (
                      objects, ccIdMode(), ccHp(), ccHpTableTag(),
                      ccHavokIdx(), ccHavokIdxTableTag(), ccHavokIdxMode(),
                      ccLadderLiftTag(), ccRidBase(), ccRotFlipYawTag(),
                      "on" if ccStateEnabled() else "off"))
        sources = " ".join("%s=%s" % (key, _CC_SOURCE.get(key, "?"))
                           for key, _ in _KNOB_KEYS)
        return values + " | " + sources
    except Exception as error:  # noqa: BLE001 —— 自证行绝不能把服务端带崩
        return "report-failed " + repr(error)


# 启动自证行（与 ``[contracts]`` / ``[move]`` 那几行同一风格）。
# ``ini=cc`` = 读到了 ``[cc]`` 段；``ini=none`` = 没读到（那 6 个值全走默认）。
print("[cc] " + ccKnobsReport()
      + " ini=" + (CC_INI_SECTION if _CC_INI else "none"), flush=True)
