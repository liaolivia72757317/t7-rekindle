"""Minimal M4 MOVE wire codecs.

These codecs only preserve the TDR wire layouts. Selectors 31 and 38 both
append ``GeMovableActiveCommand`` through distinct receivers. EXP-097 closes
selector 38's synchronous target/component dispatch and tick semantics, while
runtime target presence, visible movement, and camera binding remain separate.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass


MOVE_COMMAND = 0x0002
MOVE_DIRECT_BC = 0x0004
_MOVE_DIRECT_BC_STRUCT_FORMAT = ">HIBHhfff"
MOVE_NOTIFY_ACTIVE = 0x001F
MOVE_MOUNT_BC = 0x0023
MOVE_LOCK_ORIENTATION = 0x002B
MOVE_BC_WITH_SPECIAL_ANIMATION = 0x0027
MOVE_BC_WITH_SPECIAL_ANIMATION_BODY_LENGTH = 48
MOVE_BC_WITH_SYSTEM_AND_ACTIVE = 0x0026
MOVE_BC_WITH_SYSTEM_AND_ACTIVE_BODY_LENGTH = 40
MOVE_IN_AIR_STATE_BC = 0x0036
MOVE_BC_ACTOR_WITH_JUMP = 0x0037
MOVE_BC_ACTOR_WITH_JUMP_BODY_LENGTH = 35
# 两个跳跃下行报文的 TDR 结构（sh_proto_cs，声明顺序即 wire 顺序）：
#   sel=55 CS_PROTO_MOVE_BC_ACTOR_WITH_JUMP —— 玩家带有跳跃动画信息的广播消息
#   sel=39 CS_PROTO_MOVE_BC_WITH_SPECIAL_ANIMATION —— 移动广播，带特殊动画（马撞停、跳等）
# 注意两者字段集**不同**：55 有 a/cv/mv 但无 system_group/active/acceleration；
# 39 有 5 个角速度字段和 5 个动画标志。不能互相复用同一个格式串。
_MOVE_BC_ACTOR_WITH_JUMP_STRUCT_FORMAT = ">HIHBhhhhhhfffbb"
_MOVE_BC_SPECIAL_ANIMATION_STRUCT_FORMAT = ">HIHBhhhhhhhhhhhfffbbbbb"
# 坐骑移动广播（35）与方向锁（43）的 TDR 结构，字段顺序/宽度照 ``sh_proto_cs_metas``：
#   CS_PROTO_MOVE_MOUNT_BC      net_unit 39：tick I/inst H/state B/
#                               a bw wf wa cw waf cv mv 各 h /dir f(弧度)/pos fff
#   CS_PROTO_MOVE_LOCK_ORIENTATION net_unit 6：tick I/lock B/lock_camera B
# 两条都在 body 前加 2 字节 selector（与 31/38 同一约定），故长度 = net_unit + 2。
_MOVE_MOUNT_BC_STRUCT_FORMAT = ">HIHBhhhhhhhhffff"
MOVE_MOUNT_BC_BODY_LENGTH = 41
MOVE_LOCK_ORIENTATION_BODY_LENGTH = 8
_MOVE_LOCK_ORIENTATION_STRUCT_FORMAT = ">HIBB"
# Semantic candidates from sh_proto_cs; selector 38's plain fields do not carry
# explicit TDR enum bindings, so the generic codec does not enforce them.
MOVE_GROUND_STATE_STOP = 1
# 骑乘档（``E_RES_MOVE_GROUND_MOUNT_*``）。取值是**客户端主程序里的 sh_proto_cs 宏表直读**
# （``codec/tdr.py`` 的宏表，索引 4856..4893，2026-09-22 复核），不是推断：
#   16..19 MOUNT_LINE_UP_{1..4}_ACC_WALK  向前，加速分四档
#   20..23 MOUNT_LINE_UP_{1..4}_DEC_WALK  向前，减速分四档
#   24     MOUNT_LINE_DOWN_WALK           后退
#   26..29 MOUNT_ARC_{1..4}_LEFT_UP_ACC   左前转，加速分档
#   30..33 MOUNT_ARC_{1..4}_RIGHT_UP_ACC  右前转，加速分档
#   34..49 各种 DEC / MORE_DEC 档；50/51 左后/右后弧；54 MOUNT_STOP；70 下马走
# 这里每族取**按速度算出来的那一档**（见 ``controls.mountMoveState``）：
# ``坐骑.psheet`` 的四档速度（轻骑 2/4/7/10 m/s）与这四连号是一一对应的关系，
# 所以档位不是我们挑的，是实际速度落在哪一档。
MOVE_GROUND_MOUNT_STATE_LINE_UP_ACC = 16
MOVE_GROUND_MOUNT_STATE_LINE_DOWN = 24
MOVE_GROUND_MOUNT_STATE_ARC_LEFT_UP_ACC = 26
MOVE_GROUND_MOUNT_STATE_ARC_RIGHT_UP_ACC = 30
# 后退时的左后/右后弧（宏表 verbatim：``E_RES_MOVE_GROUND_MOUNT_ARC_{LEFT,RIGHT}_DOWN_WALK``，
# 2026-09-22 从 ``TieJiClient.exe`` 的 sh_proto_cs 宏表读出 50 / 51，同一族**没有**四档）。
MOVE_GROUND_MOUNT_STATE_ARC_LEFT_DOWN_WALK = 50
MOVE_GROUND_MOUNT_STATE_ARC_RIGHT_DOWN_WALK = 51
MOVE_GROUND_MOUNT_STATE_STOP = 54
# 一个族里相邻两档相差 1（``…_UP_{1..4}_ACC_WALK``），族宽 4。
MOVE_GROUND_MOUNT_TIER_SPAN = 4
MOVE_MESSAGE_GROUP_SERVER_INTERNAL = 3
_MOVE_BC_STRUCT_FORMAT = ">HIHBhhihfffhibh"

# ★ 位移基准约定开关（2026-09-22 夜）。**默认 False = 历史行为逐位不变。**
# 由 ``controls`` 按 ``level.ini`` 的 ``[move] basis`` 在导入后写进来 ——
# 本模块是纯 codec，不自己读配置文件。详细说明见 ``project_standard_ground_step``。
BASIS_FORWARD_ALONG_HEADING = False


_MOVE_DIRECTION_BY_AXES = {
    (-1, 0): 1,
    (-1, -1): 2,
    (0, -1): 3,
    (1, -1): 4,
    (1, 0): 5,
    (1, 1): 6,
    (0, 1): 7,
    (-1, 1): 8,
    (0, 0): 0,
}


@dataclass(frozen=True, slots=True)
class GroundMoveProjection:
    forward_back: int
    left_right: int
    normalized_forward_back: float
    normalized_left_right: float
    move_direction: int
    heading_degrees: int
    step_distance: float
    delta: tuple[float, float, float]
    moving: bool


def project_standard_ground_step(
    *,
    w_pressed: int,
    a_pressed: int,
    s_pressed: int,
    d_pressed: int,
    heading_degrees: int,
    step_distance: float,
) -> GroundMoveProjection:
    """Project one explicit compatibility step through the standard/state-0 basis.

    The logical axes and diagonal normalization are client contracts. The
    heading is an explicit selector-3-to-selector-38 compatibility candidate,
    and ``step_distance`` is caller-owned so this helper does not invent an
    original-service scalar or cadence.
    """

    key_states = (w_pressed, a_pressed, s_pressed, d_pressed)
    if any(type(value) is not int or value not in (0, 1) for value in key_states):
        raise ValueError("pressed state must be 0 or 1")
    if type(heading_degrees) is not int or not -180 <= heading_degrees <= 180:
        raise ValueError("heading degrees must be an integer in -180..180")
    if (
        not isinstance(step_distance, (int, float))
        or isinstance(step_distance, bool)
        or not math.isfinite(step_distance)
        or step_distance < 0.0
    ):
        raise ValueError("step distance must be a finite nonnegative number")

    forward_back = s_pressed - w_pressed
    left_right = a_pressed - d_pressed
    magnitude = math.hypot(forward_back, left_right)
    if magnitude == 0.0:
        normalized_forward_back = 0.0
        normalized_left_right = 0.0
    else:
        normalized_forward_back = forward_back / magnitude
        normalized_left_right = left_right / magnitude

    # 位移基准约定（2026-09-22 夜新增的开关，默认 False = 历史行为逐位不变）。
    #
    # 历史行为：W（``forward_back = s − w = −1``，即 ``normalized_forward_back = −1``）
    # 得到 delta = (cosθ, −sinθ)，**方位角 = −θ**；A（``nlr = +1``）得到
    # (sinθ, cosθ)，方位角 = 90° − θ，也就是「左」其实在「前进」的**顺时针** 90°
    # ⇒ 整套基向量是右手系的**镜像**（左手系：θ 顺时针为正）。
    #
    # 实测（会话 13976-191219260）严格成立：纯 W 时 ``方位角 + yaw == 0.0``，
    # 误差 < 0.1°，步行与骑乘一致。
    #
    # 打开本开关 ⇒ 用 **−θ** 算基向量：W 得 (cosθ, +sinθ)，方位角 = **+θ**；
    # A 得 (−sinθ, cosθ)，方位角 = θ + 90° ⇒ 变成右手系（θ 逆时针为正），
    # 「左」与「前进」的关系恢复正常。
    #
    # ⚠️ 注意**不能**用「把 delta 的 x 分量取反」代替：那等于绕 90° 轴镜像，
    # 结果差一个 90° 旋转（x 取反给的是方位角 180°+θ，不是 θ）。已实测对过。
    #
    # ⚠️ 到底哪个对**尚未定论**（见 README「移动基准」）：实机只证明了
    # 「位移方位角 = −θ」，没证明客户端渲染的朝向方位角是 +θ 还是 −θ。
    # 打开会**同时影响步兵与骑兵**（同一个函数），所以默认关着。
    theta = math.radians(-heading_degrees if BASIS_FORWARD_ALONG_HEADING
                         else heading_degrees)
    cosine = math.cos(theta)
    sine = math.sin(theta)
    distance = float(step_distance)
    delta = (
        distance
        * (-normalized_forward_back * cosine + normalized_left_right * sine),
        distance
        * (normalized_forward_back * sine + normalized_left_right * cosine),
        0.0,
    )
    return GroundMoveProjection(
        forward_back=forward_back,
        left_right=left_right,
        normalized_forward_back=normalized_forward_back,
        normalized_left_right=normalized_left_right,
        move_direction=_MOVE_DIRECTION_BY_AXES[(forward_back, left_right)],
        heading_degrees=heading_degrees,
        step_distance=distance,
        delta=delta,
        moving=magnitude > 0.0,
    )


@dataclass(frozen=True, slots=True)
class MoveNotifyActive:
    server_tick: int
    target_instance_id: int
    active: int


def encode_move_notify_active(
    *,
    server_tick: int,
    target_instance_id: int,
    active: int,
) -> bytes:
    """Encode the wire body for ``E_CS_PROTO_MOVE_NOTIFY_ACTIVE``.

    The active byte is consumed by ``GeRecvMoveNotifyActive`` and copied into
    a queued ``GeMovableActiveCommand``.
    """

    if active not in (0, 1):
        raise ValueError("move notify active must be 0 or 1")
    try:
        return struct.pack(
            ">HIHB",
            MOVE_NOTIFY_ACTIVE,
            server_tick,
            target_instance_id,
            active,
        )
    except struct.error as error:
        raise ValueError("move notify active field is outside its TDR wire range") from error


def decode_move_notify_active(body: bytes) -> MoveNotifyActive:
    """Decode an exact ``E_CS_PROTO_MOVE_NOTIFY_ACTIVE`` body."""

    if not isinstance(body, bytes):
        raise TypeError("body must be bytes")
    if len(body) != 9:
        raise ValueError(f"move notify active must contain exactly 9 bytes, got {len(body)}")
    selector, server_tick, target_instance_id, active = struct.unpack(">HIHB", body)
    if selector != MOVE_NOTIFY_ACTIVE:
        raise ValueError(
            f"move notify active expected selector 0x{MOVE_NOTIFY_ACTIVE:04X}"
        )
    if active not in (0, 1):
        raise ValueError("move notify active must be 0 or 1")
    return MoveNotifyActive(server_tick, target_instance_id, active)


@dataclass(frozen=True, slots=True)
class MoveInAirStateBc:
    server_tick: int
    target_instance_id: int
    is_in_air: int


def encode_move_in_air_state_bc(
    *,
    server_tick: int,
    target_instance_id: int,
    is_in_air: int,
) -> bytes:
    """Encode the wire body for ``E_CS_PROTO_IN_AIR_STATE_BC`` (selector 54).

    ``GeRecvMoveDriveEnableBC`` queues ``GeMovableDriveEnableCommand(true)``
    when ``is_in_air == 0``; see EXP-094 for the corrected static mapping.
    The raw TDR ``char`` domain (-128..127) is preserved byte-faithfully;
    only zero has the statically verified drive-enable meaning.
    """

    if not -128 <= is_in_air <= 127:
        raise ValueError("move in-air state field is outside its TDR wire range")
    try:
        return struct.pack(
            ">HIHb",
            MOVE_IN_AIR_STATE_BC,
            server_tick,
            target_instance_id,
            is_in_air,
        )
    except struct.error as error:
        raise ValueError("move in-air state field is outside its TDR wire range") from error


def decode_move_in_air_state_bc(body: bytes) -> MoveInAirStateBc:
    """Decode an exact ``E_CS_PROTO_IN_AIR_STATE_BC`` body."""

    if not isinstance(body, bytes):
        raise TypeError("body must be bytes")
    if len(body) != 9:
        raise ValueError(
            f"move in-air state must contain exactly 9 bytes, got {len(body)}"
        )
    selector, server_tick, target_instance_id, is_in_air = struct.unpack(">HIHb", body)
    if selector != MOVE_IN_AIR_STATE_BC:
        raise ValueError(
            f"move in-air state expected selector 0x{MOVE_IN_AIR_STATE_BC:04X}"
        )
    return MoveInAirStateBc(server_tick, target_instance_id, is_in_air)


@dataclass(frozen=True, slots=True)
class MoveBcWithSystemAndActive:
    server_tick: int
    target_instance_id: int
    state: int
    left_right: int
    forward_back: int
    current_velocity: int
    max_velocity: int
    position: tuple[float, float, float]
    direction_yaw: int
    system_group: int
    active: int
    acceleration: int


def _require_finite_position(position: tuple[float, float, float]) -> tuple[float, float, float]:
    if not isinstance(position, tuple) or len(position) != 3:
        raise TypeError("position must be a tuple of three floats")
    if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in position):
        raise ValueError("position values must be finite numbers")
    return (float(position[0]), float(position[1]), float(position[2]))


def encode_move_mount_bc(
    *,
    server_tick: int,
    target_instance_id: int,
    state: int,
    position: tuple[float, float, float],
    direction_radians: float,
    current_velocity: int = 0,
    max_velocity: int = 0,
    acceleration: int = 0,
    angular_velocity: int = 0,
    base_angular_velocity: int = 0,
    angular_factor: int = 0,
    angular_acceleration: int = 0,
    angular_acc_factor: int = 0,
) -> bytes:
    """编码 selector35 ``E_CS_PROTO_MOVE_MOUNT_BC``（原版中文说明「坐骑移动广播」）。

    字段按 TDR 声明顺序：tick/inst/state + a bw wf wa cw waf cv mv（8×int16）+
    dir(float，**弧度**) + pos(3×float)。⚠️ 和 38 号那族不一样：38 的 ``dir`` 是
    int16 的「-180..180 度数」，这里的是 float 弧度，别把度当弧度发。

    ⚠️ **``state`` 要传骑乘档**（16..19/20..23/24/26..29/30..33/50/51/54…，见上面
    ``MOVE_GROUND_MOUNT_STATE_*``），别把 38 号那族步战值（1..15）直接填进来 ——
    合法域是 0..255，编码器不会拦，但客户端会按步战状态机去解这条马的移动。
    调用方选档在 ``controls.mountMoveState``（按「前/后 × 转舵方向 × 速度档」算），
    和 38 号那条用的是**同一个** ``state``。

    ★ 角速度五兄弟（2026-09-22 夜新增，字段注释全部来自 ``tdr_dump.py struct
    CS_PROTO_MOVE_MOUNT_BC`` 直读 ``TieJiClient.exe``）：

    ==========  ======  ==========================================
    参数          wire    客户端字段注释（原版原文）
    ==========  ======  ==========================================
    ``base_angular_velocity`` ``bw``   （无注释，与 33 号同族；33 号写作 base_w 基准角速度）
    ``angular_factor``        ``wf``   （无注释；33 号写作 w_factor 角速度计算参数）
    ``angular_acceleration``  ``wa``   角加速度
    ``angular_velocity``      ``cw``   当前角速度
    ``angular_acc_factor``    ``waf``  angular_acc_factor 角加速度影响因子
    ==========  ======  ==========================================

    **这五个参数默认全 0，与接线前逐位相同** —— 谁不传谁不变，回归门（
    ``scripts/verify_mount_turn.py``）就是靠这条成立的。
    为什么必须填：33 号那条里写着马的角速度 = ``bw``(基准角速度) × ``wf``(角速度计算参数)，
    受 ``waf`` 影响。服务端此前把它们连同 ``cw`` 一起发 0，等于告诉客户端
    「这匹马角速度恒为 0」——而服务端**每 50 ms 把 ``dir`` 硬掰 6°**（=120°/s）。
    客户端拿到的就是「朝向在瞬移、角速度却是 0」，转不起来只能横着滑。
    """
    finite_position = _require_finite_position(position)
    if not 0 <= state <= 255:
        raise ValueError("move mount state must fit the TDR octet domain")
    for name, value in (("current_velocity", current_velocity), ("max_velocity", max_velocity),
                        ("acceleration", acceleration), ("angular_velocity", angular_velocity),
                        ("base_angular_velocity", base_angular_velocity),
                        ("angular_factor", angular_factor),
                        ("angular_acceleration", angular_acceleration),
                        ("angular_acc_factor", angular_acc_factor)):
        if not -32768 <= value <= 32767:
            raise ValueError(f"move mount {name} is outside its TDR int16 range")
    if not math.isfinite(direction_radians):
        raise ValueError("move mount direction must be finite")
    try:
        encoded = struct.pack(
            _MOVE_MOUNT_BC_STRUCT_FORMAT,
            MOVE_MOUNT_BC,
            server_tick,
            target_instance_id,
            state,
            acceleration,
            base_angular_velocity,
            angular_factor,
            angular_acceleration,
            angular_velocity,
            angular_acc_factor,
            current_velocity,
            max_velocity,
            direction_radians,
            *finite_position,
        )
    except struct.error as error:
        raise ValueError("move mount bc field is outside its TDR wire range") from error
    if len(encoded) != MOVE_MOUNT_BC_BODY_LENGTH:
        raise ValueError(f"move mount bc must be {MOVE_MOUNT_BC_BODY_LENGTH} bytes")
    return encoded


# --- selector50 ``CS_PROTO_MOVE_MO_BC``（地图物件移动广播，2026-09-23 新增） ----
#
# 出处：``TieJiClient.exe`` / metalib ``sh_proto_cs``，
#   ``python -m codec union --union CS_PROTO_MOVE_DATA`` → sel=50 / storage=39，
#   ``-m codec struct CS_PROTO_MOVE_MO_BC`` 字段表：
#
#     @0  svr_tick        uint32       svr时间
#     @4  target_inst_id  smalluint    MO移动目标
#     @6  state           uchar        状态
#     @7  a               short        加速度
#     @9  bw              short        （无注释，与 33/35 号同族：基准角速度）
#     @11 wf              short        （角速度因子）
#     @13 wa              short        角加速度
#     @15 cw              short        当前角速度
#     @17 waf             short        angular_acc_factor
#     @19 cv              short        curr velocity 当前速度值
#     @21 mv              short        max_v 最大速度
#     @23 dir             float        用弧度描述的角度
#     @27 pos             PROTO_VECTOR 当前位置
#
# 39 字节 = 4+2+1+2×8+4+12，**与 selector35（坐骑广播）逐字段同构**，
# 所以直接沿用 35 号的 struct 格式，只把 selector 换成 50。
# ⚠️ ``storageSize`` **不含** 2 字节 selector 前缀 ⇒ 线上 body 是 41 字节。
MOVE_MO_BC = 0x0032
# ⚠️ 末尾必须是 **4 个 f**（dir 1 个 + pos 3 个），共 16 个格式符 = 41 字节。
# 2026-09-23 首版误写成 3 个 f（15 个格式符），自检立刻抓到
# 「pack expected 15 items, got 16」→ 见 out/cc/check_codec_bytes.py。
# 参照物：同文件 selector35 `_MOVE_MOUNT_BC_STRUCT_FORMAT` 也是 ">HIHBhhhhhhhhffff"。
_MOVE_MO_BC_STRUCT_FORMAT = ">HIHBhhhhhhhhffff"
MOVE_MO_BC_BODY_LENGTH = 41


def encode_move_mo_bc(
    *,
    server_tick: int,
    target_instance_id: int,
    state: int,
    position: tuple[float, float, float],
    direction_radians: float,
    current_velocity: int = 0,
    max_velocity: int = 0,
    acceleration: int = 0,
    angular_velocity: int = 0,
    base_angular_velocity: int = 0,
    angular_factor: int = 0,
    angular_acceleration: int = 0,
    angular_acc_factor: int = 0,
) -> bytes:
    """编码 selector50 ``CS_PROTO_MOVE_MO_BC``（「地图物件移动广播」，S->C）。

    用途：**服务端驱动地图物件移动** —— 攻城车自动前进（``state=1021``
    ``E_MO_STATE_ATTACK_CITY_CAR_MOVE``）、投石车被推走都走这条。

    ⚠️ ``target_instance_id`` 是 MO 的 **inst_id**（uint16，即 CC 报文里的
    ``inst_id`` = pathId 低 16 位），**不是** rid。``dir`` 是 **float 弧度**。
    ⚠️ 与 38 号那族（``dir`` 是 int16 度）**不同**，别混。
    """
    finite_position = _require_finite_position(position)
    if not 0 <= state <= 255:
        raise ValueError("move mo state must fit the TDR octet domain")
    for name, value in (("current_velocity", current_velocity), ("max_velocity", max_velocity),
                        ("acceleration", acceleration), ("angular_velocity", angular_velocity),
                        ("base_angular_velocity", base_angular_velocity),
                        ("angular_factor", angular_factor),
                        ("angular_acceleration", angular_acceleration),
                        ("angular_acc_factor", angular_acc_factor)):
        if not -32768 <= value <= 32767:
            raise ValueError(f"move mo {name} is outside its TDR int16 range")
    if not math.isfinite(direction_radians):
        raise ValueError("move mo direction must be finite")
    try:
        encoded = struct.pack(
            _MOVE_MO_BC_STRUCT_FORMAT,
            MOVE_MO_BC,
            server_tick,
            target_instance_id,
            state,
            acceleration,
            base_angular_velocity,
            angular_factor,
            angular_acceleration,
            angular_velocity,
            angular_acc_factor,
            current_velocity,
            max_velocity,
            direction_radians,
            *finite_position,
        )
    except struct.error as error:
        raise ValueError("move mo bc field is outside its TDR wire range") from error
    if len(encoded) != MOVE_MO_BC_BODY_LENGTH:
        raise ValueError(f"move mo bc must be {MOVE_MO_BC_BODY_LENGTH} bytes")
    return encoded


def encode_move_lock_orientation(*, server_tick: int, lock: int = 0, lock_camera: int = 0) -> bytes:
    """编码 selector43 ``E_CS_PROTO_MOVE_LOCK_ORIENTATION``（「服务器锁定朝向请求」）。

    格式是三重闭环（metalib net_unit 6 + 客户端接收器 0x00AB5660 + 执行器 0x00B6AD40
    反汇编）：``svr_tick u32@0 / lock u8@4 / lock_camera u8@5``，执行器只读 ``lock``，
    ``lock_camera`` 恒发 0。旧备份启动脚本的原话是「正常服务器会在进场后解除角色方向锁；
    离线流程必须补发该通知」，出处项目据此实机对照过「能走 vs 不能走」。
    """
    if lock not in (0, 1) or lock_camera not in (0, 1):
        raise ValueError("move lock orientation flags must be 0 or 1")
    return struct.pack(
        _MOVE_LOCK_ORIENTATION_STRUCT_FORMAT,
        MOVE_LOCK_ORIENTATION,
        server_tick,
        lock,
        lock_camera,
    )


@dataclass(frozen=True, slots=True)
class MoveDirectBc:
    server_tick: int
    target_instance_id: int
    direction_yaw: int
    position: tuple[float, float, float]


def encode_move_direct_bc(
    *,
    server_tick: int,
    target_instance_id: int,
    direction_yaw: int,
    position: tuple[float, float, float],
) -> bytes:
    """编码 selector4 的单对象子集，包含位置校正与方向，不包含动作状态。"""
    finitePosition = _require_finite_position(position)
    if type(direction_yaw) is not int or not -180 <= direction_yaw <= 180:
        raise ValueError("direction yaw must be an integer in -180..180")
    try:
        return struct.pack(_MOVE_DIRECT_BC_STRUCT_FORMAT, MOVE_DIRECT_BC,
                           server_tick, 1, target_instance_id, direction_yaw,
                           *finitePosition)
    except (struct.error, OverflowError) as error:
        raise ValueError("move direct bc field is outside its TDR wire range") from error


def decode_move_direct_bc(body: bytes) -> MoveDirectBc:
    """仅解码本地单对象子集；不接受多对象数组或尾随字节。"""
    if not isinstance(body, bytes):
        raise TypeError("body must be bytes")
    if len(body) != struct.calcsize(_MOVE_DIRECT_BC_STRUCT_FORMAT):
        raise ValueError("single-target move direct bc must contain exactly 23 bytes")
    selector, tick, count, target, yaw, x, y, z = struct.unpack(
        _MOVE_DIRECT_BC_STRUCT_FORMAT, body)
    if selector != MOVE_DIRECT_BC or count != 1:
        raise ValueError("expected selector4 with exactly one direction entry")
    if not -180 <= yaw <= 180:
        raise ValueError("direction yaw must be in -180..180")
    return MoveDirectBc(tick, target, yaw, _require_finite_position((x, y, z)))


def encode_move_bc_with_system_and_active(
    *,
    server_tick: int,
    target_instance_id: int,
    position: tuple[float, float, float],
    direction_yaw: int,
    active: int,
    state: int = 0,
    left_right: int = 0,
    forward_back: int = 0,
    current_velocity: int = 0,
    max_velocity: int = 0,
    system_group: int = 0,
    acceleration: int = 0,
) -> bytes:
    """Encode ``E_CS_PROTO_MOVE_BC_WITH_SYSTEM_AND_ACTIVE``.

    Metalib struct storage is 38 bytes. The MOVE selector is prepended the
    same way as selector 31, producing a 40-byte body. The body carries a
    position, appends the movement commands to a composite, then synchronously
    dispatches that composite. Camera binding is not a direct side effect.
    """

    finite_position = _require_finite_position(position)
    if active not in (0, 1):
        raise ValueError("move bc active must be 0 or 1")
    if direction_yaw < -180 or direction_yaw > 180:
        raise ValueError("direction yaw must be in -180..180")
    try:
        return struct.pack(
            _MOVE_BC_STRUCT_FORMAT,
            MOVE_BC_WITH_SYSTEM_AND_ACTIVE,
            server_tick,
            target_instance_id,
            state,
            left_right,
            forward_back,
            current_velocity,
            max_velocity,
            finite_position[0],
            finite_position[1],
            finite_position[2],
            direction_yaw,
            system_group,
            active,
            acceleration,
        )
    except struct.error as error:
        raise ValueError("move bc field is outside its TDR wire range") from error


def decode_move_bc_with_system_and_active(body: bytes) -> MoveBcWithSystemAndActive:
    """Decode an exact ``E_CS_PROTO_MOVE_BC_WITH_SYSTEM_AND_ACTIVE`` body."""

    if not isinstance(body, bytes):
        raise TypeError("body must be bytes")
    if len(body) != MOVE_BC_WITH_SYSTEM_AND_ACTIVE_BODY_LENGTH:
        raise ValueError(
            "move bc with system and active must contain exactly "
            f"{MOVE_BC_WITH_SYSTEM_AND_ACTIVE_BODY_LENGTH} bytes, got {len(body)}"
        )
    unpacked = struct.unpack(_MOVE_BC_STRUCT_FORMAT, body)
    selector = unpacked[0]
    if selector != MOVE_BC_WITH_SYSTEM_AND_ACTIVE:
        raise ValueError(
            "move bc with system and active expected selector "
            f"0x{MOVE_BC_WITH_SYSTEM_AND_ACTIVE:04X}"
        )
    active = unpacked[13]
    if active not in (0, 1):
        raise ValueError("move bc active must be 0 or 1")
    direction_yaw = unpacked[11]
    if direction_yaw < -180 or direction_yaw > 180:
        raise ValueError("direction yaw must be in -180..180")
    return MoveBcWithSystemAndActive(
        server_tick=unpacked[1],
        target_instance_id=unpacked[2],
        state=unpacked[3],
        left_right=unpacked[4],
        forward_back=unpacked[5],
        current_velocity=unpacked[6],
        max_velocity=unpacked[7],
        position=(unpacked[8], unpacked[9], unpacked[10]),
        direction_yaw=direction_yaw,
        system_group=unpacked[12],
        active=active,
        acceleration=unpacked[14],
    )


@dataclass(frozen=True, slots=True)
class MoveBcActorWithJump:
    server_tick: int
    target_instance_id: int
    state: int
    left_right: int
    forward_back: int
    acceleration: int
    current_velocity: int
    max_velocity: int
    direction_yaw: int
    position: tuple[float, float, float]
    jump: int
    jump_animation: int


def encode_move_bc_actor_with_jump(
    *,
    server_tick: int,
    target_instance_id: int,
    state: int,
    left_right: int,
    forward_back: int,
    acceleration: int,
    current_velocity: int,
    max_velocity: int,
    direction_yaw: int,
    position: tuple[float, float, float],
    jump: int,
    jump_animation: int,
) -> bytes:
    """编码 ``E_CS_PROTO_MOVE_BC_ACTOR_WITH_JUMP``（cmd=2 selector 55）。

    TDR 结构 ``CS_PROTO_MOVE_BC_ACTOR_WITH_JUMP``，字段 33 字节 + selector 2 字节 = 35 字节。
    与 selector 38 的关键差异：本结构**没有** ``system_group``/``active``/``acceleration``
    这三个字段，取而代之的是 ``a``（加速度）、``cv``/``mv``（均为 short）与末尾两个
    动画标志 ``jump``/``jump_animation``。

    ``jump``/``jump_animation`` 的取值域只做了 TDR 的 char 域校验（-128..127）；
    两者与客户端表现的具体对应关系**未闭合**，见模块头部说明。
    """

    finite_position = _require_finite_position(position)
    for name, value in (("jump", jump), ("jump_animation", jump_animation)):
        if type(value) is not int or not -128 <= value <= 127:
            raise ValueError(f"{name} must be an integer in -128..127")
    try:
        return struct.pack(
            _MOVE_BC_ACTOR_WITH_JUMP_STRUCT_FORMAT,
            MOVE_BC_ACTOR_WITH_JUMP,
            server_tick,
            target_instance_id,
            state,
            left_right,
            forward_back,
            acceleration,
            current_velocity,
            max_velocity,
            direction_yaw,
            finite_position[0],
            finite_position[1],
            finite_position[2],
            jump,
            jump_animation,
        )
    except struct.error as error:
        raise ValueError("move bc actor with jump field is outside its TDR wire range") from error


def decode_move_bc_actor_with_jump(body: bytes) -> MoveBcActorWithJump:
    """解码一个精确的 ``E_CS_PROTO_MOVE_BC_ACTOR_WITH_JUMP`` body。"""

    if not isinstance(body, bytes):
        raise TypeError("body must be bytes")
    if len(body) != MOVE_BC_ACTOR_WITH_JUMP_BODY_LENGTH:
        raise ValueError(
            "move bc actor with jump must contain exactly "
            f"{MOVE_BC_ACTOR_WITH_JUMP_BODY_LENGTH} bytes, got {len(body)}"
        )
    unpacked = struct.unpack(_MOVE_BC_ACTOR_WITH_JUMP_STRUCT_FORMAT, body)
    if unpacked[0] != MOVE_BC_ACTOR_WITH_JUMP:
        raise ValueError(
            "move bc actor with jump expected selector "
            f"0x{MOVE_BC_ACTOR_WITH_JUMP:04X}"
        )
    return MoveBcActorWithJump(
        server_tick=unpacked[1],
        target_instance_id=unpacked[2],
        state=unpacked[3],
        left_right=unpacked[4],
        forward_back=unpacked[5],
        acceleration=unpacked[6],
        current_velocity=unpacked[7],
        max_velocity=unpacked[8],
        direction_yaw=unpacked[9],
        position=_require_finite_position((unpacked[10], unpacked[11], unpacked[12])),
        jump=unpacked[13],
        jump_animation=unpacked[14],
    )


@dataclass(frozen=True, slots=True)
class MoveBcWithSpecialAnimation:
    server_tick: int
    target_instance_id: int
    state: int
    left_right: int
    forward_back: int
    acceleration: int
    base_angular_velocity: int
    angular_velocity_factor: int
    angular_acceleration: int
    current_angular_velocity: int
    angular_acceleration_factor: int
    current_velocity: int
    max_velocity: int
    direction_yaw: int
    position: tuple[float, float, float]
    jump: int
    squat: int
    jump_animation: int
    squat_animation: int
    sudden_stop_animation: int


def encode_move_bc_with_special_animation(
    *,
    server_tick: int,
    target_instance_id: int,
    state: int,
    left_right: int = 0,
    forward_back: int = 0,
    acceleration: int = 0,
    base_angular_velocity: int = 0,
    angular_velocity_factor: int = 0,
    angular_acceleration: int = 0,
    current_angular_velocity: int = 0,
    angular_acceleration_factor: int = 0,
    current_velocity: int = 0,
    max_velocity: int = 0,
    direction_yaw: int = 0,
    position: tuple[float, float, float],
    jump: int = 0,
    squat: int = 0,
    jump_animation: int = 0,
    squat_animation: int = 0,
    sudden_stop_animation: int = 0,
) -> bytes:
    """编码 ``E_CS_PROTO_MOVE_BC_WITH_SPECIAL_ANIMATION``（cmd=2 selector 39）。

    TDR 结构 46 字节 + selector 2 字节 = 48 字节。五个动画标志的语义来自 TDR 描述
    （跳跃/下蹲/跳跃动画/下蹲动画/突然撞停）；其具体取值**未闭合**，此处只做
    char 域校验，不推断客户端表现。
    """

    finite_position = _require_finite_position(position)
    for name, value in (("jump", jump), ("squat", squat), ("jump_animation", jump_animation),
                        ("squat_animation", squat_animation),
                        ("sudden_stop_animation", sudden_stop_animation)):
        if type(value) is not int or not -128 <= value <= 127:
            raise ValueError(f"{name} must be an integer in -128..127")
    try:
        return struct.pack(
            _MOVE_BC_SPECIAL_ANIMATION_STRUCT_FORMAT,
            MOVE_BC_WITH_SPECIAL_ANIMATION,
            server_tick,
            target_instance_id,
            state,
            left_right,
            forward_back,
            acceleration,
            base_angular_velocity,
            angular_velocity_factor,
            angular_acceleration,
            current_angular_velocity,
            angular_acceleration_factor,
            current_velocity,
            max_velocity,
            direction_yaw,
            finite_position[0],
            finite_position[1],
            finite_position[2],
            jump,
            squat,
            jump_animation,
            squat_animation,
            sudden_stop_animation,
        )
    except struct.error as error:
        raise ValueError(
            "move bc with special animation field is outside its TDR wire range") from error


def decode_move_bc_with_special_animation(body: bytes) -> MoveBcWithSpecialAnimation:
    """解码一个精确的 ``E_CS_PROTO_MOVE_BC_WITH_SPECIAL_ANIMATION`` body。"""

    if not isinstance(body, bytes):
        raise TypeError("body must be bytes")
    if len(body) != MOVE_BC_WITH_SPECIAL_ANIMATION_BODY_LENGTH:
        raise ValueError(
            "move bc with special animation must contain exactly "
            f"{MOVE_BC_WITH_SPECIAL_ANIMATION_BODY_LENGTH} bytes, got {len(body)}"
        )
    unpacked = struct.unpack(_MOVE_BC_SPECIAL_ANIMATION_STRUCT_FORMAT, body)
    if unpacked[0] != MOVE_BC_WITH_SPECIAL_ANIMATION:
        raise ValueError(
            "move bc with special animation expected selector "
            f"0x{MOVE_BC_WITH_SPECIAL_ANIMATION:04X}"
        )
    return MoveBcWithSpecialAnimation(
        server_tick=unpacked[1],
        target_instance_id=unpacked[2],
        state=unpacked[3],
        left_right=unpacked[4],
        forward_back=unpacked[5],
        acceleration=unpacked[6],
        base_angular_velocity=unpacked[7],
        angular_velocity_factor=unpacked[8],
        angular_acceleration=unpacked[9],
        current_angular_velocity=unpacked[10],
        angular_acceleration_factor=unpacked[11],
        current_velocity=unpacked[12],
        max_velocity=unpacked[13],
        direction_yaw=unpacked[14],
        position=_require_finite_position((unpacked[15], unpacked[16], unpacked[17])),
        jump=unpacked[18],
        squat=unpacked[19],
        jump_animation=unpacked[20],
        squat_animation=unpacked[21],
        sudden_stop_animation=unpacked[22],
    )
