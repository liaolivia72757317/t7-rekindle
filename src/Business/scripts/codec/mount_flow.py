"""坐骑空间 ``cmd=7``（``SH_CS_CMD_MOUNT``）的编解码。

证据边界（**请勿把未闭合项当已恢复**）
--------------------------------------

已闭合、可依赖（全部来自 ``tdr_dump.py`` 直读 exe 的 ``sh_proto_cs``，逐字段可复核）：

``CS_PROTO_MOUNT_PKG`` = ``msg_id`` smallint@0 + ``CS_PROTO_MOUNT_DATA`` union@2，
union 的 selector 与 ``E_CS_PROTO_MOUNT_MSGID`` 一一对应：

    sel=1  CS_PROTO_MOUNT_RIDE_REQ   32B  cli->svr 骑马操作请求（上下马)
    sel=2  CS_PROTO_MOUNT_RIDE_BC    28B  svr->cli 骑马操作广播(上下马)
    sel=3  CS_PROTO_MOUNT_RIDE_RSP   16B  svr->cli 骑马操作回复(上下马)
    sel=4  CS_PROTO_MOUNT_FREE_BC    20B  svr->cli 马释放广播

``E_CS_PROTO_MOUNT_MSGID`` 的原版中文说明原文：

    RIDE_REQ = 1  cli->svr:上下坐骑请求
    RIDE_BC  = 2  svr->cli:上下坐骑广播， 因为要表现给整个房间，所以是bc，不是RSP
    RIDE_RSP = 3  svr->cli, 上下坐骑回复, 如果失败会带原因的
    FREE_BC  = 4  马消失的广播，防止服务器和客户端场景不一致

``E_CS_MOVE_RIDE_TYPE``（``RIDE_REQ/RSP/BC`` 的 ``ride_type`` 都 bind 它）：

    MIN = 0 / **UP = 1「骑乘坐骑」** / **DOWN = 2「取消骑乘坐骑」** / MAX = 3

``E_CS_MOVE_RIDE_FAILED_REASON``（``RIDE_RSP.reason`` 的取值域）：

    1 = 位置不符合骑乘坐骑要求     2 = 已经是骑乘状态

字段布局（``tdr_dump.py struct`` 直读，无一处推断；``>`` 前缀 = 无对齐填充）：

    CS_PROTO_MOUNT_RIDE_RSP   target    biguint@0  「骑马操作玩家」
                              ride_type int@8      「上下马类型，bind->E_CS_MOUNT_RIDE_TYPE」
                              reason    int@12     「请求失败的原因, bind->E_CS_MOUNT_RIDE_FAILED_REASON」
    CS_PROTO_MOUNT_RIDE_REQ   ride_type int@0 / mount_rid biguint@4 /
                              map_pos SH_POSITION(12B)@12 「上马前在地图位置|下马后在地图位置」/
                              cli_tick biguint@24
    CS_PROTO_MOUNT_RIDE_BC    target biguint@0 / ride_type int@8 /
                              svr_tick biguint@12 / map_pos biguint@20
    CS_PROTO_MOUNT_FREE_BC    mount_rid biguint@0 / svr_tick biguint@8 / reason int@16

未闭合、本模块**不声称**已恢复
------------------------------

* **没有一条真实样本。** ``cmd=7`` 在全部 14 个 ``13976-*`` 会话里的出现次数 = **0**
  （普查脚本 ``hkx_decode/c2s_decrypt_census.py``）。所以本模块产出的一切都属于
  「按 TDR 结构重建」，**不是**「照样本回放」—— 实机若无效，第一嫌疑就是这一层。
* ``CS_PROTO_MOUNT_RIDE_BC.map_pos`` 是 ``biguint``(8B)「上下马服务器的位置」，
  既不是 ``SH_POSITION``(12B) 也不是 3×float，**打包编码未知且无样本**。
  ⇒ **故意不实现 ``encode_mount_ride_bc``**：宁可缺，也不要编一个坐标编码出来
  （编错了就是「马瞬移」这类难查的实机故障）。
* ``target`` 到底是 actor 的 ``rid`` 还是 ``user_id``：``RIDE_REQ`` 用 ``mount_rid``
  指马，``RIDE_RSP``/``RIDE_BC`` 用 ``target`` 指人。本模块按 ``wire.ACTOR_ID`` 填
  （与其余单播一致），**未验证**。
* 客户端到底认不认 ``cmd=7``：prior_art 107 篇里**没有任何一篇涉及下马 / RIDE_BC**，
  加壳客户端也拿不到接收器反汇编。所以这条下行只能靠实机 A/B 判定。

和 ``cmd=4`` 的关系（为什么会有这个模块）
----------------------------------------

实机骑马按 C，客户端上行的是 ``cmd=4 sel=13`` = ``CS_PROTO_BATTLE_UNRIDE_MOUNT``
「C->S 下马通知包」（``controls.mountCommand`` 处理），**不是** ``cmd=7 sel=1``。
而 ``CS_PROTO_BATTLE_DATA`` union 那 50 项里**根本没有「下马确认」的 S->C 结构**
（只有 sel=14 ``MOUNT_BE_HIT_NOTIFY`` / sel=15 ``MOUNT_ATTR_NOTIFY``）
⇒ 如果客户端要等服务端确认，确认**只能**从 ``cmd=7`` 回来。
这就是本模块存在的唯一理由。
"""
from __future__ import annotations

import struct

# --- 顶层命令号与选择子（TDR ``SH_CS_CMD`` / ``E_CS_PROTO_MOUNT_MSGID``） ---
MOUNT_COMMAND = 7
MOUNT_RIDE_REQ = 1
MOUNT_RIDE_BC = 2
MOUNT_RIDE_RSP = 3
MOUNT_FREE_BC = 4

# --- ``E_CS_MOVE_RIDE_TYPE`` ---
E_CS_MOVE_RIDE_TYPE_MIN = 0
E_CS_MOVE_RIDE_TYPE_UP = 1        # 骑乘坐骑
E_CS_MOVE_RIDE_TYPE_DOWN = 2      # 取消骑乘坐骑
E_CS_MOVE_RIDE_TYPE_MAX = 3

# --- ``E_CS_MOVE_RIDE_FAILED_REASON`` ---
E_CS_MOVE_RIDE_FAILED_NONE = 0    # 成功（字段只在失败时有意义）
E_CS_MOVE_RIDE_FAILED_POS_INVALID = 1
E_CS_MOVE_RIDE_FAILED_ALREAY_RIDED = 2

# ``msg_id``(2) + ``target``(8) + ``ride_type``(4) + ``reason``(4)
MOUNT_RIDE_RSP_BODY_LENGTH = 18
# ``msg_id``(2) + ``target``(8) + ``ride_type``(4) + ``svr_tick``(8) + ``map_pos``(8)
MOUNT_RIDE_BC_BODY_LENGTH = 30
# ``msg_id``(2) + ``mount_rid``(8) + ``svr_tick``(8) + ``reason``(4)
MOUNT_FREE_BC_BODY_LENGTH = 22

_UINT64_MAX = (1 << 64) - 1


def encode_mount_ride_rsp(*, target: int, ride_type: int, reason: int = 0) -> bytes:
    """编码 selector3 ``CS_PROTO_MOUNT_RIDE_RSP``（原版中文说明「骑马操作回复(上下马)」）。

    ``ride_type`` 只能取 ``UP``(1) / ``DOWN``(2)；``reason`` 只在**失败**时有意义，
    成功填 0（``E_CS_MOVE_RIDE_FAILED_NONE``）。字段域严格校验 —— 编错了客户端
    收到的是一条「格式合法但语义垃圾」的回复，比收不到更难查。
    """
    if ride_type not in (E_CS_MOVE_RIDE_TYPE_UP, E_CS_MOVE_RIDE_TYPE_DOWN):
        raise ValueError("mount ride rsp ride_type must be UP(1) or DOWN(2)")
    if reason not in (E_CS_MOVE_RIDE_FAILED_NONE, E_CS_MOVE_RIDE_FAILED_POS_INVALID,
                      E_CS_MOVE_RIDE_FAILED_ALREAY_RIDED):
        raise ValueError("mount ride rsp reason is outside E_CS_MOVE_RIDE_FAILED_REASON")
    if not 0 <= target <= _UINT64_MAX:
        raise ValueError("mount ride rsp target is outside biguint")
    try:
        return struct.pack(">H Q i i", MOUNT_RIDE_RSP, target, ride_type, reason)
    except struct.error as error:  # pragma: no cover - 上面已逐项校验，兜底而已
        raise ValueError("mount ride rsp field is outside its TDR wire range") from error


def decode_mount_ride_rsp(body: bytes):
    """解一条 ``CS_PROTO_MOUNT_RIDE_RSP`` body，返回 ``(msg_id, target, ride_type, reason)``。"""
    if len(body) != MOUNT_RIDE_RSP_BODY_LENGTH:
        raise ValueError("mount ride rsp body must be exactly %d bytes, got %d"
                         % (MOUNT_RIDE_RSP_BODY_LENGTH, len(body)))
    return struct.unpack(">H Q i i", body)


def encode_mount_ride_bc(*, target: int, ride_type: int, svr_tick: int,
                         map_pos_xy=(0.0, 0.0)) -> bytes:
    """编码 selector2 ``CS_PROTO_MOUNT_RIDE_BC``「上下坐骑广播」。

    原版注释原文：「因为要表现给整个房间，所以是bc，不是RSP」⇒ **RSP 只是回执，
    驱动客户端表现的是这条 BC**（2026-09-23 实机：RSP target 修正后不冻结但也不
    下马 ⇒ 缺的就是 BC）。

    ``map_pos``（biguint 8B「上下马服务器的位置」）的打包依据（2026-09-23 17:3x
    升级，不再是瞎赌）：**客户端自己在 ``cmd=2 sel=52`` 按键包的末尾就是把
    (x, y) 两个大端 float 塞进 8 字节、z 留给高度场**（实机样本
    ``0034…43d7d684 43b28ceb`` = (431.84, 356.79)，与当帧服务端位置吻合）。
    ⇒ ``map_pos`` 按**同一种 2-float 打包**填服务端算出的下马位置。
    实机若「马瞬移」仍说明此赌错。
    """
    if ride_type not in (E_CS_MOVE_RIDE_TYPE_UP, E_CS_MOVE_RIDE_TYPE_DOWN):
        raise ValueError("mount ride bc ride_type must be UP(1) or DOWN(2)")
    if not 0 <= target <= _UINT64_MAX or not 0 <= svr_tick <= _UINT64_MAX:
        raise ValueError("mount ride bc field is outside biguint")
    map_pos = struct.unpack(">Q", struct.pack(">ff", float(map_pos_xy[0]),
                                              float(map_pos_xy[1])))[0]
    try:
        return struct.pack(">H Q i Q Q", MOUNT_RIDE_BC, target, ride_type,
                           svr_tick, map_pos)
    except struct.error as error:  # pragma: no cover
        raise ValueError("mount ride bc encode failed") from error


def decode_mount_ride_bc(body: bytes):
    """解一条 ``CS_PROTO_MOUNT_RIDE_BC`` body，返回 ``(msg_id, target, ride_type, svr_tick, map_pos)``。"""
    if len(body) != MOUNT_RIDE_BC_BODY_LENGTH:
        raise ValueError("mount ride bc body must be exactly %d bytes, got %d"
                         % (MOUNT_RIDE_BC_BODY_LENGTH, len(body)))
    return struct.unpack(">H Q i Q Q", body)


def encode_mount_free_bc(*, mount_rid: int, svr_tick: int, reason: int = 0) -> bytes:
    """编码 selector4 ``CS_PROTO_MOUNT_FREE_BC``「马消失的广播」。

    原版注释原文：「马消失的广播，防止服务器和客户端场景不一致」⇒ 客户端删掉
    坐骑实体的通知。**三个字段全部闭合、无位置编码**（TDR 直读：
    ``mount_rid`` biguint@0 / ``svr_tick`` biguint@8 / ``reason`` int@16）。

    ``mount_rid`` = 坐骑实体的 rid —— 与视野 ADD 里的 ``MOUNT_VISION_RID``(21)
    同一个号（prior_art 实网样本也是 21）。``reason`` 取值域没有专门枚举，
    按 0（无原因/正常消失）填。
    """
    if not 0 <= mount_rid <= _UINT64_MAX or not 0 <= svr_tick <= _UINT64_MAX:
        raise ValueError("mount free bc field is outside biguint")
    try:
        return struct.pack(">H Q Q i", MOUNT_FREE_BC, mount_rid, svr_tick, reason)
    except struct.error as error:  # pragma: no cover
        raise ValueError("mount free bc encode failed") from error


def decode_mount_free_bc(body: bytes):
    """解一条 ``CS_PROTO_MOUNT_FREE_BC`` body，返回 ``(msg_id, mount_rid, svr_tick, reason)``。"""
    if len(body) != MOUNT_FREE_BC_BODY_LENGTH:
        raise ValueError("mount free bc body must be exactly %d bytes, got %d"
                         % (MOUNT_FREE_BC_BODY_LENGTH, len(body)))
    return struct.unpack(">H Q Q i", body)
