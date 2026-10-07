"""Minimal M4 VISION codec for importing one fixed local actor."""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass


VISION_COMMAND = 0x000E
VISION_ADD_EVENT = 0x0001
VISION_DEL_EVENT = 0x0002
VISION_GET_VISION_LIST_REQUEST = 0x0005
VISION_LIST_RESPONSE = 0x0006
VISION_GET_OBJECTS_REQUEST = 0x0007
VISION_OBJECT_ACTOR = 0x00000001
VISION_OBJECT_MOUNT = 0x00000002

# ``CS_PROTO_VISION_MOUNT_INFO`` 定长 106 B，加 ``object_type`` 前缀 4 B = 110 B。
# 偏移逐字段来自 ``sh_proto_cs`` 元数据，prior art 已用哨兵值逐个验证：
#   rid@0(u64) inst_id@8(u16) res_id@10(u32) attr@14(12) move_data@26(62)
#   filter_group@88 sub_system_group@92 mount_bm_data@96(10)
MOUNT_OBJECT_SIZE = 110

INSTANCE_START_PATTERN_PRACTICE = 0x0000000E
FIXED_LOCAL_USER_ID = 10000
FIXED_LOCAL_ACTOR_MID = 1
FIXED_LOCAL_INSTANCE_ID = 1
FIXED_LOCAL_HERO_RESOURCE_ID = 1101


@dataclass(frozen=True, slots=True)
class VisionObjectsRequest:
    object_mids: tuple[int, ...]
    force_get: int


# ``ACTOR_WEAPON_USE_DATA`` = ``{weapon_num u16, weapon_arr[25]}``，单条 29B：
#   { weapon_tid i32, weapon_slot u8, upgrade_level i8, is_using u8,
#     bullet_info 12B, bm_data 10B }
# 字段名与注释出自 ``TieJiClient.exe`` 的 TDR 注释池（0x0103b3d2 起）：
#   weapon_tid「武器的tid」/ weapon_slot「武器的槽位，见 E_WEAPON_SLOT_TYPE」/
#   upgrade_level「强化等级」/ is_using「武器当前是否使用, 0表示未使用」/
#   bullet_info「武器弹药情况」/ bm_data「外观数据」。
# 7 + 12 + 10 = 29 与 ``ACTOR_WEAPON_USE_DATA.nReal=727 = 2 + 25×29`` 对得上。
WEAPON_USE_RECORD_SIZE = 29


def encode_weapon_use_record(
    weapon_slot: int,
    weapon_tid: int,
    is_using: int,
    ammo: int = 0,
    *,
    upgrade_level: int = 0,
) -> bytes:
    """Encode one 29-byte ``WEAPON_USE_DATA`` entry.

    ``weapon_slot`` is the 大厅 面板 slot (1..4) the weapon was equipped in, so
    the battle side can offer the same 1/2/3/4 switching set.  ``ammo`` fills the
    12-byte ``bullet_info``（``WEAPON_BULLET_INFO``）: 近战武器传 0 ⇒ 整段留 0、
    HUD 不显数字；投掷/消耗武器传 N ⇒ 把 ``stack_count`` 等计数写成 N，HUD 才显示
    弹药数。appearance (``bm_data``) 恒为 0。

    ``bullet_info`` 12B 布局（``sh_proto_cs_metas`` WEAPON_BULLET_INFO 逐字段核过）::

        consumed_type u8 | stack_count u16 | max_stack_count u16
        | weapon_need_bullet u8 | bullet_left_in_charger u16
        | bullet_left_in_package u16 | max_bullet_left_in_package u16
    """

    try:
        head = struct.pack(
            ">iBBB", weapon_tid, weapon_slot, upgrade_level, 1 if is_using else 0
        )
        if ammo:
            bullet = struct.pack(
                ">BHHBHHH",
                0,      # consumed_type
                ammo,   # stack_count —— HUD 显示的就是这个
                ammo,   # max_stack_count
                1,      # weapon_need_bullet —— 标记「这把吃弹药」
                ammo,   # bullet_left_in_charger
                ammo,   # bullet_left_in_package
                ammo,   # max_bullet_left_in_package
            )
        else:
            bullet = bytes(12)
    except struct.error as error:
        raise ValueError("weapon-use record is outside its TDR wire range") from error
    return head + bullet + bytes(WEAPON_USE_RECORD_SIZE - 7 - 12)


def encode_weapon_use_data(weapons: tuple[tuple[int, ...], ...]) -> bytes:
    """Encode ``ACTOR_WEAPON_USE_DATA``: count + only the records actually held.

    Each entry is ``(weapon_slot, weapon_tid, is_using[, ammo])`` — the optional
    4th ``ammo`` element carries the throwing/consumable weapon's 弹药数 into
    ``bullet_info``; melee entries omit it (or pass 0) and stay all-zero.

    Variable-length arrays on this wire are not padded to their declared size
    (see the note above ``encode_cc_dynamic_info``).
    """

    return struct.pack(">h", len(weapons)) + b"".join(
        encode_weapon_use_record(*entry) for entry in weapons
    )


def _encode_tdr_string(value: bytes, *, maximum_size: int, field_name: str) -> bytes:
    if not isinstance(value, bytes):
        raise TypeError(f"{field_name} must be bytes")
    if b"\0" in value:
        raise ValueError(f"{field_name} must not contain an embedded NUL")
    encoded = value + b"\0"
    if len(encoded) > maximum_size:
        raise ValueError(f"{field_name} exceeds its {maximum_size}-byte TDR storage")
    return len(encoded).to_bytes(4, "big") + encoded


def decode_vision_list_request(body: bytes) -> int:
    """Decode C→S GET_VISION_LIST_REQ and return its signed sequence number."""

    if not isinstance(body, bytes):
        raise TypeError("body must be bytes")
    if len(body) != 6:
        raise ValueError(
            f"vision-list request must contain exactly 6 bytes, got {len(body)}"
        )
    selector, sequence = struct.unpack(">Hi", body)
    if selector != VISION_GET_VISION_LIST_REQUEST:
        raise ValueError(
            "vision-list request expected selector "
            f"0x{VISION_GET_VISION_LIST_REQUEST:04X}"
        )
    return sequence


def encode_vision_list_response(
    *,
    sequence: int,
    object_mids: tuple[int, ...],
) -> bytes:
    """Encode one complete VISION_LIST_RSP part."""

    if not isinstance(object_mids, tuple):
        raise TypeError("object_mids must be a tuple")
    if len(object_mids) > 1000:
        raise ValueError("vision-list response cannot contain more than 1000 objects")
    try:
        return b"".join(
            (
                struct.pack(
                    ">Hibbii",
                    VISION_LIST_RESPONSE,
                    sequence,
                    0,
                    1,
                    len(object_mids),
                    len(object_mids),
                ),
                b"".join(struct.pack(">Q", object_mid) for object_mid in object_mids),
            )
        )
    except struct.error as error:
        raise ValueError("vision-list response field is outside its TDR wire range") from error


def decode_vision_get_objects_request(body: bytes) -> VisionObjectsRequest:
    """Decode C→S GET_OBJECTS_REQ with its exact refer-count boundary."""

    if not isinstance(body, bytes):
        raise TypeError("body must be bytes")
    if len(body) < 7:
        raise ValueError("vision get-objects request is truncated")
    selector, object_count = struct.unpack(">Hi", body[:6])
    if selector != VISION_GET_OBJECTS_REQUEST:
        raise ValueError(
            "vision get-objects request expected selector "
            f"0x{VISION_GET_OBJECTS_REQUEST:04X}"
        )
    if object_count < 0 or object_count > 50:
        raise ValueError("vision get-objects count must be in 0..50")
    expected_size = 7 + object_count * 8
    if len(body) != expected_size:
        raise ValueError(
            "vision get-objects request must contain exactly "
            f"{expected_size} bytes for {object_count} objects, got {len(body)}"
        )
    offset = 6
    object_mids = tuple(
        struct.unpack_from(">Q", body, offset + index * 8)[0]
        for index in range(object_count)
    )
    force_get = struct.unpack_from(">b", body, offset + object_count * 8)[0]
    if force_get not in (0, 1):
        raise ValueError("vision get-objects force_get must be 0 or 1")
    return VisionObjectsRequest(object_mids, force_get)


def _encode_move_data(
    *,
    active: int,
    position: tuple[float, float, float],
    gravity: int = 0,
) -> bytes:
    if not isinstance(position, tuple) or len(position) != 3:
        raise TypeError("position must be a tuple of three floats")
    if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in position):
        raise ValueError("position values must be finite numbers")
    return struct.pack(
        ">bbB" + "h" * 10 + "fffIfffih" + "b" * 5,
        0,
        active,
        0,
        *([0] * 10),
        *position,
        0,
        0.0,
        0.0,
        0.0,
        0,
        gravity,
        *([0] * 5),
    )


def encode_mount_vision_object(
    *,
    rid: int,
    res_id: int,
    position: tuple[float, float, float],
    instance_id: int = 0,
    moving: bool = True,
    filter_group: int = 0,
    sub_system_group: int = 0,
) -> bytes:
    """Encode one ``CS_PROTO_VISION_OBJECT_INFO`` holding a MOUNT (110 bytes).

    坐骑在原版里是**独立视野实体**：客户端按 ``actor.mount_rid == mount.rid``
    把骑手挂到坐骑上（客户端二进制里有 ``No Mount When Set Rider`` /
    ``No Mount Entity When LocalHero Get on Mount`` 这些失败分支）。

    ``attr``（12 B，``CS_PROTO_VISION_ATTR`` —— ⚠️ 与 actor 那个
    ``CS_ATTR_NOTIFY`` 同名不同元数据）和 ``mount_bm_data``（10 B）留 0：
    坐骑 max hp 的出处 ``s_mount_attr.bin`` 还没解出来，不猜数值。
    """

    try:
        encoded = b"".join(
            (
                struct.pack(">i", VISION_OBJECT_MOUNT),
                struct.pack(">QHI", rid, instance_id, res_id),
                bytes(12),
                _encode_move_data(active=1 if moving else 0, position=position),
                struct.pack(">ii", filter_group, sub_system_group),
                bytes(10),
            )
        )
    except struct.error as error:
        raise ValueError("vision mount field is outside its TDR wire range") from error
    if len(encoded) != MOUNT_OBJECT_SIZE:
        raise ValueError(f"mount object must be {MOUNT_OBJECT_SIZE} bytes")
    return encoded


def encode_fixed_local_actor_vision_add_event(
    *,
    server_time_ms: int = 0,
    actor_mid: int = FIXED_LOCAL_ACTOR_MID,
    user_id: int = FIXED_LOCAL_USER_ID,
    instance_id: int = FIXED_LOCAL_INSTANCE_ID,
    hero_resource_id: int = FIXED_LOCAL_HERO_RESOURCE_ID,
    camp: int = 1,
    level: int = 1,
    actor_name: bytes = b"local",
    position: tuple[float, float, float] = (0.0, 0.0, 0.0),
    current_hp: int = 100,
    maximum_hp: int = 100,
    current_stamina: int = 100,
    gravity: int = 0,
    weapons: tuple[tuple[int, int, int], ...] = (),
    mount_tid: int = 0,
    mount_rid: int = 0,
    mount_object: bytes = b"",
) -> bytes:
    """Encode ADD_EVENT with one schema-valid local ACTOR object.

    ``weapons`` are ``(weapon_slot, weapon_tid, is_using)`` and ``mount_tid`` is
    the 坐骑 tid, both synced from the hero's lobby equipment card so the model
    in battle carries what the 武器 面板 shows.  Action-history, buff, feature
    and skill-book arrays are intentionally empty. PRACTICE selects no
    start-pattern union arm. The identity and hero values are local fixture
    values, not recovered originals.

    ``gravity`` 写进 actor 自己那份 move_data 末尾的 int16（单位 0.001 米每二次方秒，
    -10000 = -10.0）。默认 0 = 与改动前逐位相同。

    ``mount_object`` (see ``encode_mount_vision_object``) additionally puts the
    坐骑 in the same ADD_EVENT as a second object, and ``mount_rid`` is written
    into ``actor.mount_rid`` so the client can pair them.  Both default to
    empty/0, which keeps 步兵 报文 byte-for-byte identical to before.
    """

    if mount_object and len(mount_object) != MOUNT_OBJECT_SIZE:
        raise ValueError(f"mount_object must be {MOUNT_OBJECT_SIZE} bytes")
    legion_name = _encode_tdr_string(
        b"",
        maximum_size=32,
        field_name="legion_name",
    )
    encoded_actor_name = _encode_tdr_string(
        actor_name,
        maximum_size=32,
        field_name="actor_name",
    )
    try:
        actor = b"".join(
            (
                struct.pack(">IQH", user_id, actor_mid, instance_id),
                # ``gravity`` 只写 actor 自己这份姿态；下面那份历史姿态恒 0（一个变量）。
                _encode_move_data(active=1, position=position, gravity=gravity),
                struct.pack(">ibbiii", camp, 1, 0, level, 0, 0),
                legion_name,
                struct.pack(">ibi" + "i" * 8, hero_resource_id, 0, 0, *([0] * 8)),
                encode_weapon_use_data(weapons),
                struct.pack(">HQhQ", 0, 0, 0, 0),
                struct.pack(">iii", current_hp, maximum_hp, current_stamina),
                struct.pack(">IQH", mount_tid, mount_rid, 0),
                struct.pack(">iii", 0, 0, 0),
                struct.pack(">" + "h" * 5, *([0] * 5)),
                encoded_actor_name,
                struct.pack(">i", 0),
                struct.pack(">Q", 0),
                struct.pack(">iiii", 0, 0, 0, 0),
                _encode_move_data(active=0, position=(0.0, 0.0, 0.0)),
                struct.pack(">ibi", 0, 0, 0),
                struct.pack(">" + "i" * 7, *([0] * 7)),
                struct.pack(">h", 0),
                struct.pack(">h", 0),
                struct.pack(">i", INSTANCE_START_PATTERN_PRACTICE),
            )
        )
        return b"".join(
            (
                struct.pack(">Hii", VISION_ADD_EVENT, 1 + (1 if mount_object else 0),
                            VISION_OBJECT_ACTOR),
                actor,
                mount_object,
                struct.pack(">Q", server_time_ms),
            )
        )
    except struct.error as error:
        raise ValueError("vision actor field is outside its TDR wire range") from error


def encode_vision_del_event(
    *,
    object_mid: int = FIXED_LOCAL_ACTOR_MID,
    object_type: int = VISION_OBJECT_ACTOR,
    server_time_ms: int = 0,
) -> bytes:
    """Encode VISION DEL_EVENT for one object.

    Wire is selector 2 + object_num:i32 + one
    CS_PROTO_VISION_DEL_OBJECT_INFO (object_type:i32 + object_mid:u64)
    + svr_time:u64.
    """

    if object_type != VISION_OBJECT_ACTOR:
        raise ValueError(f"object_type must be ACTOR(1), got {object_type}")
    try:
        return struct.pack(
            ">HiiQQ",
            VISION_DEL_EVENT,
            1,
            object_type,
            object_mid,
            server_time_ms,
        )
    except struct.error as error:
        raise ValueError("vision del field is outside its TDR wire range") from error


# --- CC 攻城器械（E_VISION_OBJECT_CC_DYNAMIC，selector = 4）----------------------
#
# 为什么在这里
# ------------
# 樊城（tszz）的 24 个可交互攻城器械（攻城车/云梯/投石车/屋顶/城门/补给箱…）
# 是 **CC Object**，不在静态资产表 amodellist 里。数据早就抽出来了
# （``data/scene/tszz/ccobject.json``，脚本 ``t7_ccobject_extract.py``），
# 但服务端从来没用过 —— 所以实机里「少一个攻城车、三个云梯、两个投石车、还有
# 房顶」全都看不见。
#
# 协议出处（**实测，不是推断**）
# -----------------------------
# ``TieJiClient.exe`` 内嵌 TDR 块 ``sh_proto_cs``（offset 14,914,128，长 2,559,430）：
#
#   CS_PROTO_VISION_OBJECT_DATA 联合体共 6 个分支，服务端此前只做了第 1 个：
#     sel=1 E_VISION_OBJECT_ACTOR      CS_PROTO_VISION_ACTOR_INFO     ✅ 已实现
#     sel=4 E_VISION_OBJECT_CC_DYNAMIC  CS_PROTO_VISION_CC_DYNAMIC_INFO ❌ 本模块
#
#   配套：CS_PROTO_MOVE_CC_LAYER_CHANGE_BC（客户端自带描述「CC 碰撞过滤层改变广播」，
#   字段 svr_tick:u32 / target_inst_id:smalluint / layer:int）——「按 C 扶起云梯」
#   走的就是这条；本模块暂不实现。
#
# ⚠️ 编码规则（从**已跑通的** ACTOR 编码器逐字节反推，不是猜）
# -----------------------------------------------------------
# **变长数组只写「实际条数」，不按 TDR 声明的最大尺寸补齐。** 实证：
#
#   CS_PROTO_BUFF_INFO       声明 1540 B，ACTOR 报文里 buff_num=0  → 实际 4 B
#   CS_PROTO_ANIMATION_EVENT_AND_VAR 声明 68 B，event_num=param_num=0 → 实际 2 B
#   CS_PROTO_SCRIPT_ITEM_INFO 声明 772 B，item_num=0              → 实际 4 B
#
#   反过来 `ACTOR_CURR_MOVE_DATA`（定长）声明 62 B，实际就是 62 B。
#   用 ACTOR 全报文验证：实测 355 B = 按此规则逐字段累加的结果（误差 0）。
#
# ⚠️ 未验证边界（别当成已闭合）
# -----------------------------
# * ``rid`` / ``inst_id`` 的**取值规则**没有参考报文可对。本模块只保证「编码合法」，
#   不保证客户端认这两个 ID 的关系。默认 ``rid`` 由调用方给、``inst_id`` 由调用方给。
# * 没有实机报文可比对 —— 本模块的验证只到「字节布局与 TDR 声明一致」这一层。
# * ``state`` / ``completeness`` / ``havok_res_index`` 的语义未实测。
#
# 默认**不接线**：本模块只是编码器，谁调用由上层决定（建议开关默认关，
# 保证既有报文逐位不变）。

VISION_OBJECT_CC_DYNAMIC = 0x00000004

# 全部变长数组为空、display_name 为空时的定长 wire 尺寸（供自测断言）。
CC_DYNAMIC_STATIC_SIZE = 202


@dataclass(frozen=True, slots=True)
class CcDynamicObject:
    """一个 CC 攻城器械的「视野动态物件」快照。

    字段名与 ``CS_PROTO_VISION_CC_DYNAMIC_INFO`` 一一对应。

    ``res_id`` = **``cc_tid``（客户端认的器械类型号）**，``inst_id`` = ``pathId``
    低 16 位。这两条是 2026-09-21 从客户端场景实体表反推出来并**已实机验证**的
    （取证见 ``ccobject.py`` 的 ``CC_ID_MODE`` 段：客户端 GameObject 的 ``ID``
    == ``cc_tid`` / ``PathID`` == ``pathId``；改对之前投石车等器械**渲染不出来**）。

    ⚠️ 本 docstring 曾写作「``res_id`` 就是 ccobject.json 的 ``id``（2384…）」——
    那是**被推翻的旧结论**，已更正（2026-09-24）。
    """

    inst_id: int
    res_id: int
    position: tuple[float, float, float]
    rid: int = 0
    display_name: bytes = b""
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    camp: int = 0
    hp: int = 1
    moving: bool = False
    start_path_pos: tuple[float, float, float] = (0.0, 0.0, 0.0)
    end_path_pos: tuple[float, float, float] = (0.0, 0.0, 0.0)
    state: int = 0
    state_change_ms: int = 0
    state_time_ms: int = 0
    filter_group: int = 0
    sub_system_group: int = 0
    havok_res_index: int = 0
    completeness: int = 100
    owner_rid: int = 0
    controller_rid: int = 0
    horizontal_angle: float = 0.0
    vertical_angle: float = 0.0
    curr_bullet: int = 0
    max_bullet: int = 0


def _vector3(value, *, field_name: str) -> bytes:
    if not isinstance(value, tuple) or len(value) != 3:
        raise TypeError(f"{field_name} must be a tuple of three floats")
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in value):
        raise ValueError(f"{field_name} values must be finite numbers")
    return struct.pack(">fff", *value)


def encode_cc_dynamic_info(obj: CcDynamicObject) -> bytes:
    """编码一个 ``CS_PROTO_VISION_CC_DYNAMIC_INFO``（27 字段，声明顺序）。"""

    if not isinstance(obj, CcDynamicObject):
        raise TypeError("obj must be a CcDynamicObject")
    try:
        return b"".join(
            (
                struct.pack(">Q", obj.rid),
                _encode_tdr_string(
                    obj.display_name, maximum_size=1, field_name="display_name"
                ),
                struct.pack(">H", obj.inst_id),
                struct.pack(">i", obj.res_id),
                _vector3(obj.position, field_name="pos"),
                _vector3(obj.rotation, field_name="rotation"),
                struct.pack(">i", obj.camp),
                struct.pack(">i", obj.hp),
                _encode_move_data(
                    active=1 if obj.moving else 0, position=obj.position
                ),
                # buff_info：buff_num = 0，不写任何 entry
                struct.pack(">i", 0),
                _vector3(obj.start_path_pos, field_name="start_path_pos"),
                _vector3(obj.end_path_pos, field_name="end_path_pos"),
                struct.pack(">i", obj.state),
                struct.pack(">Q", obj.state_change_ms),
                struct.pack(">i", obj.state_time_ms),
                # event_var：event_num = 0，param_num = 0
                struct.pack(">bb", 0, 0),
                # script_item_info：item_num = 0
                struct.pack(">i", 0),
                struct.pack(">i", obj.filter_group),
                struct.pack(">i", obj.sub_system_group),
                struct.pack(">b", obj.havok_res_index),
                struct.pack(">h", obj.completeness),
                struct.pack(">Q", obj.owner_rid),
                struct.pack(">Q", obj.controller_rid),
                struct.pack(">f", obj.horizontal_angle),
                struct.pack(">f", obj.vertical_angle),
                struct.pack(">H", obj.curr_bullet),
                struct.pack(">H", obj.max_bullet),
            )
        )
    except struct.error as error:
        raise ValueError("CC dynamic field is outside its TDR wire range") from error


def encode_cc_dynamic_vision_add_event(
    *,
    objects: tuple[CcDynamicObject, ...],
    server_time_ms: int = 0,
) -> bytes:
    """把一批 CC 物件编成一条独立的 ``VISION ADD_EVENT``。

    Wire: ``selector:u16(=1) + object_num:i32 + Σ(object_type:i32 + data) + svr_time:u64``

    ⚠️ 独立发包是刻意的：既有 ``encode_fixed_local_actor_vision_add_event``
    保持逐位不变（那是回归底线）。
    """

    if not isinstance(objects, tuple):
        raise TypeError("objects must be a tuple")
    if not objects:
        raise ValueError("objects must not be empty")
    if len(objects) > 1000:
        raise ValueError("vision add event cannot carry more than 1000 objects")
    try:
        return b"".join(
            (
                struct.pack(">Hi", VISION_ADD_EVENT, len(objects)),
                # 每个物件各带一份 object_type:i32（CS_PROTO_VISION_OBJECT_INFO）
                b"".join(
                    struct.pack(">i", VISION_OBJECT_CC_DYNAMIC)
                    + encode_cc_dynamic_info(o)
                    for o in objects
                ),
                struct.pack(">Q", server_time_ms),
            )
        )
    except struct.error as error:
        raise ValueError("vision add event field is outside its TDR wire range") from error


# --- 视野第 5 号分支：简版 MO 物件（E_VISION_OBJECT_SIMPLE_MO）------------------
# 出处 = ``CS_PROTO_VISION_OBJECT_DATA`` 联合体成员 5（``python -m codec union`` 解
# ``TieJiClient.exe`` 的 metalib ``sh_proto_cs``），逐字段偏移（净 990B）：
#   rid u64@0 | display_name string@8..40 | inst_id u16@40 | res_id i32@42
#   | hero_data ACTOR_CURR_HERO_DATA 41@46 | weapon_data ACTOR_WEAPON_USE_DATA@87
#   | move_data ACTOR_CURR_MOVE_DATA 62@814 | act_state_data 60@876
#   | attr_info CS_ATTR_NOTIFY 12@936 | camp i32@948 | filter_group i32@952
#   | sub_system_group i32@956 | take_quests TAKE_QUEST_INFO[6] 5×6@960
# ⚠️ 变长段（string / weapon_data）按 CC 物件那条已跑通的路子**不补零**，所以实际
# 字节数比声明的 990 小；``take_quests`` 没有数量前缀 ⇒ 按声明写满 30 个 0。
VISION_OBJECT_SIMPLE_MO = 0x00000005
SIMPLE_MO_HERO_DATA_SIZE = 41
SIMPLE_MO_ACT_STATE_SIZE = 60
SIMPLE_MO_TAKE_QUESTS_SIZE = 30


def encode_simple_mo_vision_add_event(
    *,
    rid: int,
    inst_id: int,
    res_id: int,
    position: tuple[float, float, float],
    camp: int = 1,
    server_time_ms: int = 0,
    display_name: bytes = b"",
    filter_group: int = 0,
    sub_system_group: int = 0,
) -> bytes:
    """一条只装一个简版 MO 物件的 ``VISION ADD_EVENT``（给 ``flow.send(0xE, ...)``）。

    用途：烟筒落地时在落点挂一颗「烟雾弹范围」物件（res_id=1080511）。客户端的
    ``烟雾弹范围.btree``（data1.vfs 块 957 = 命名池 run 930）在**进入节点**就
    「播放特效 烟雾弹」+ 循环音效，退出节点才删 —— 也就是说烟是这颗物件一出现
    就自己放的，服务端只要把它加进视野。
    """

    hero_data = struct.pack(">ibi" + "i" * 8, 0, 0, 0, *([0] * 8))
    if len(hero_data) != SIMPLE_MO_HERO_DATA_SIZE:
        raise ValueError("hero_data must be %d bytes" % SIMPLE_MO_HERO_DATA_SIZE)
    act_state = (struct.pack(">HQh", 0, 0, 0)
                 + bytes(40)
                 + struct.pack(">Q", 0))
    if len(act_state) != SIMPLE_MO_ACT_STATE_SIZE:
        raise ValueError("act_state_data must be %d bytes" % SIMPLE_MO_ACT_STATE_SIZE)
    try:
        return b"".join(
            (
                struct.pack(">Hi", VISION_ADD_EVENT, 1),
                struct.pack(">i", VISION_OBJECT_SIMPLE_MO),
                struct.pack(">Q", rid),
                _encode_tdr_string(display_name, maximum_size=28,
                                   field_name="display_name"),
                struct.pack(">Hi", inst_id, res_id),
                hero_data,
                encode_weapon_use_data(()),
                _encode_move_data(active=0, position=position),
                act_state,
                struct.pack(">iii", 1, 1, 0),
                struct.pack(">iii", camp, filter_group, sub_system_group),
                bytes(SIMPLE_MO_TAKE_QUESTS_SIZE),
                struct.pack(">Q", server_time_ms),
            )
        )
    except struct.error as error:
        raise ValueError("simple-mo vision field is outside its TDR wire range") from error
