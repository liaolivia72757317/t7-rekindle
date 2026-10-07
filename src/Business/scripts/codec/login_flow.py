"""Minimal deterministic responses for verified post-login M3 requests."""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .method3 import Method3UplinkMessage
from .protocol import (
    encode_plain_tpdu_downlink,
    encode_raw_application_payload,
    encode_sh_package,
)

LOGIN_COMMAND = 0x0001
LOGIC_QUEST_COMMAND = 0x001B
LOGIC_ITEM_COMMAND = 0x0028
LOGIC_HERO_COMMAND = 0x0022
COUNTRY_COMMAND = 0x0042

PULL_CUSTOM_DATA_REQUEST = 0x0068
PULL_CUSTOM_DATA_RESPONSE = 0x0069
ACCEPT_QUEST_REQUEST = 0x0001
GET_ITEM_CONTAINER_REQUEST = 0x0001
GET_ITEM_CONTAINER_RESPONSE = 0x0002
GET_BATTLE_HERO_REQUEST = 0x0003
GET_BATTLE_HERO_RESPONSE = 0x0004
MERGE_ITEM_REQUEST = 0x0004
MERGE_ITEM_RESPONSE = 0x0005
MERGE_OP_HERO_CARD_UNLOCK = 7
ITEM_UPDATE_ITEM_MESSAGE = 0x0003
ITEM_UPDATE_CONT_NOTIFY = 0x0007
QUERY_GOVERNOR_HERO_REQUEST = 0x0030
QUERY_GOVERNOR_HERO_RESPONSE = 0x0031

HERO_CARD_CONTAINER_TYPE = 1
BATTLE_HERO_CONTAINER_TYPE = 2
HERO_SOUL_CONTAINER_TYPE = 4
HERO_CARD_ITEM_TYPE = 1
BATTLE_HERO_ITEM_TYPE = 2
HERO_SOUL_ITEM_TYPE = 4
FIXED_HERO_CARD_BATTLE_STATE = 2
FIXED_HERO_CARD_POSITION = 1
FIXED_HERO_CARD_GUID = 1
FIXED_HERO_CARD_RESOURCE_ID = 1101
FIXED_BATTLE_HERO_FORMATION_INDEX = 1
FIXED_BATTLE_HERO_GUID = FIXED_HERO_CARD_GUID
FIXED_BATTLE_HERO_POSITION = FIXED_HERO_CARD_POSITION
FIXED_BATTLE_HERO_REMAINING_STAMINA = 100

_UINT16 = struct.Struct(">H")
_INT16 = struct.Struct(">h")
_INT32 = struct.Struct(">i")
_UINT32 = struct.Struct(">I")
_INT8 = struct.Struct(">b")
_ITEM_CONTAINER_REQUEST = struct.Struct(">Hii")
_EMPTY_ITEM_CONTAINER_RESPONSE = struct.Struct(">Hiibbiiii")
_COUNTRY_GOVERNOR_REQUEST = struct.Struct(">Hi")
_EMPTY_COUNTRY_GOVERNOR_RESPONSE = struct.Struct(">HiIi")
_FIXED_HERO_CARD_CONTAINER_PREFIX = struct.Struct(">Hiibbiiii")
_ITEM_SLOT_PREFIX = struct.Struct(">ib")
_ITEM_PREFIX = struct.Struct(">QiiibQ")
_HERO_CARD_PREFIX = struct.Struct(">bbbihii")
_CONTAINER_COUNTS = struct.Struct(">iiii")
_BATTLE_HERO_PAYLOAD = struct.Struct(">hi8iQbbih")

_HERO_WEAPON_SET_WIRE_SIZE = 8 * 6 * (1 + 4 + 2 + 1 + 4 + 10 + 7 * 12)
_HERO_EQUIPMENT_REFERENCE_WIRE_SIZE = (8 + 3 + 1) * 16

# 武器块内部布局（sh_proto_cs_metas.pkl 权威字段表，1831 metas）：
#   CS_HERO_WEAPON_SET_DEF { weapons[6] }            6 × 106 = 636B / 组
#   CS_HERO_WEAPON_DEF     { lock_state u8, weapon_info }   weapon_info = 105B
#   CS_WEAPON_INFO_DEF     { weapon_tid i32, upgrade_count u16, upgrade_level i8,
#                            upgrade_factor_total i32, bm_data[10], rune_data[7] }
#   CS_HERO_WEAPON_SLOT_DEF { slot u8, group i32, index i32 } = 9B
#   CS_HEROCARD_DEF: hero_weapon_set @17(5088) | weapon_slot_num u16 @5105
#                    | weapon_slots[12] @5107 | avatar_slots[8] @5215 ...
_HERO_WEAPON_GROUP_SIZE = 6 * 106
_HERO_WEAPON_ENTRY_SIZE = 1 + 105
_HERO_WEAPON_SLOT_ENTRY_SIZE = 1 + 4 + 4
_HERO_WEAPON_GROUP_COUNT = 8
_HERO_WEAPON_PER_GROUP = 6
_HERO_CARD_ITEM_TAIL_SIZE = 2 + _HERO_EQUIPMENT_REFERENCE_WIRE_SIZE + 2 + 2 + 16
_HERO_CARD_SLOT_SIZE = (
    _ITEM_SLOT_PREFIX.size + _ITEM_PREFIX.size + _HERO_CARD_PREFIX.size
    + _HERO_WEAPON_SET_WIRE_SIZE + _HERO_CARD_ITEM_TAIL_SIZE
)  # 5 + 29 + 17 + 5088 + 214 = 5353（没挂武器时）

_CONTAINER_HEADER_SIZE = _FIXED_HERO_CARD_CONTAINER_PREFIX.size  # 28
_BATTLE_HERO_SLOT_SIZE = (
    _ITEM_SLOT_PREFIX.size + _ITEM_PREFIX.size + _BATTLE_HERO_PAYLOAD.size
)  # 5 + 29 + 54 = 88
_BATTLE_FORMATION_ELEMENT_COUNT = 5


@dataclass(frozen=True, slots=True)
class LoginFlowResponse:
    command_id: int
    body: bytes
    reason: str

    def to_plain_tpdu(self, *, server_time_ms: int = 0) -> bytes:
        package = encode_sh_package(self.command_id, server_time_ms, self.body)
        return encode_plain_tpdu_downlink(encode_raw_application_payload(package))


def _selector(body: bytes) -> int | None:
    if len(body) < _UINT16.size:
        return None
    return _UINT16.unpack_from(body)[0]


def decode_item_container_request(body: bytes) -> tuple[int, int]:
    if len(body) != _ITEM_CONTAINER_REQUEST.size:
        raise ValueError(
            f"item-container request must contain exactly {_ITEM_CONTAINER_REQUEST.size} bytes, got {len(body)}"
        )
    selector, container_type, sequence = _ITEM_CONTAINER_REQUEST.unpack(body)
    if selector != GET_ITEM_CONTAINER_REQUEST:
        raise ValueError(
            f"item-container request expected selector 0x{GET_ITEM_CONTAINER_REQUEST:04X}, got 0x{selector:04X}"
        )
    return container_type, sequence


def encode_empty_item_container_response(container_type: int, sequence: int) -> bytes:
    try:
        return _EMPTY_ITEM_CONTAINER_RESPONSE.pack(
            GET_ITEM_CONTAINER_RESPONSE,
            container_type,
            sequence,
            1,
            0,
            0,
            0,
            0,
            0,
        )
    except struct.error as error:
        raise ValueError("item-container response field is outside its TDR wire range") from error


def _hero_card_slot(position: int, guid: int, hero_resource_id: int,
                    weapons: tuple[tuple[int, ...], ...] = ()) -> bytes:
    """Encode one occupied HEROCARD slot (``CS_ITEM_DEF`` + herocard union).

    ``weapons`` are ``(weapon_slot, set_index, group_id, index, weapon_tid,
    lock_state)`` — each one writes a weapon into
    ``hero_weapon_set[set_index].weapons[index]`` **and** adds the matching
    ``weapon_slots`` entry that actually equips it.  Every entry grows the card
    by 9 bytes, so a container of equipped cards is longer than ``5353 * n``.

    ``set_index`` (0..7) is the array ordinal inside ``CS_HEROCARD_DEF``;
    ``group_id`` is the hero's *equipment group number* from the client's hero
    table (a 6-7 digit id such as ``104029``), which is what the slot entry
    carries.  The client resolves ``group_id`` against the hero's seven groups,
    so ``group_id = 0`` (that table's "no such group") reads as unequipped.
    See 武将装备链数据梳理 §七.

    The outer ``CS_ITEM_DEF.item_type`` stays ``E_ITEM_TYPE_HEROCARD`` so TDR
    decodes the matching union branch. The first byte of the nested
    ``CS_HEROCARD_DEF`` is ``E_HEROCARD_ITEM_STATE_BATTLE``. Nested equipment,
    achievement, and skill-book collections use their declared zero/default
    representation.

    One unarmed slot = 4 (pos) + 1 (free) + 5348 (item) bytes; that per-card
    size is cross-checked against the 2026-09-08 run where 86 cards in a single
    part (460 KB) black-screened the client, hence the container limit.
    """

    try:
        weapon_block = bytearray(_HERO_WEAPON_SET_WIRE_SIZE)
        for _slot, set_index, group_id, index, weapon_tid, lock_state in weapons:
            if not 0 <= set_index < _HERO_WEAPON_GROUP_COUNT:
                raise ValueError(f"weapon set index {set_index} outside 0..7")
            if not 0 <= index < _HERO_WEAPON_PER_GROUP:
                raise ValueError(f"weapon index {index} outside 0..5")
            if group_id <= 0:
                raise ValueError(f"weapon group id {group_id} is not an equipment group")
            entry = set_index * _HERO_WEAPON_GROUP_SIZE + index * _HERO_WEAPON_ENTRY_SIZE
            weapon_block[entry] = lock_state
            _INT32.pack_into(weapon_block, entry + 1, weapon_tid)

        slot = bytearray(_ITEM_SLOT_PREFIX.pack(position, 0))
        slot.extend(
            _ITEM_PREFIX.pack(
                guid,
                1,
                HERO_CARD_ITEM_TYPE,
                hero_resource_id,
                0,
                0,
            )
        )
        slot.extend(
            _HERO_CARD_PREFIX.pack(FIXED_HERO_CARD_BATTLE_STATE, 1, 0, 0, 100, 0, 0)
        )
        slot.extend(weapon_block)
        slot.extend(_UINT16.pack(len(weapons)))
        for slot_number, _set_index, group_id, index, _tid, _lock in weapons:
            slot.extend(struct.pack(">Bii", slot_number, group_id, index))
        slot.extend(bytes(_HERO_EQUIPMENT_REFERENCE_WIRE_SIZE))
        slot.extend(_INT16.pack(0))
        slot.extend(_INT16.pack(0))
        slot.extend(bytes(16))
    except (struct.error, IndexError) as error:
        raise ValueError("hero-card slot field is outside its TDR wire range") from error
    expected = _HERO_CARD_SLOT_SIZE + _HERO_WEAPON_SLOT_ENTRY_SIZE * len(weapons)
    if len(slot) != expected:
        raise AssertionError(
            f"hero-card slot length invariant failed: {len(slot)} != {expected}"
        )
    return bytes(slot)


def encode_hero_card_item_container_response(
    sequence: int,
    cards: tuple[tuple[int, int, int], ...],
    weapons_by_position: dict[int, tuple[tuple[int, ...], ...]] | None = None,
) -> bytes:
    """Encode a HEROCARD container holding ``cards`` = (position, guid, tid).

    ``cards`` must already be capped by the caller (see ``containerCardLimit``
    in ``data/hero/hero_roster.json``); the rest reach the client later as
    ITEM_UPDATE pushes, because a container part over ~270 KB stalls the lobby.
    ``weapons_by_position`` maps a herocard position to its equipped weapons.
    """

    try:
        response = bytearray(
            _FIXED_HERO_CARD_CONTAINER_PREFIX.pack(
                GET_ITEM_CONTAINER_RESPONSE,
                HERO_CARD_CONTAINER_TYPE,
                sequence,
                1,
                0,
                len(cards),
                len(cards),
                len(cards),
                len(cards),
            )
        )
    except struct.error as error:
        raise ValueError("hero-card container field is outside its TDR wire range") from error
    by_position = weapons_by_position or {}
    for position, guid, hero_resource_id in cards:
        response.extend(_hero_card_slot(position, guid, hero_resource_id,
                                        by_position.get(position, ())))
    return bytes(response)


def encode_herosoul_item_container_response(
    sequence: int, souls: tuple[tuple[int, int, int, int], ...]
) -> bytes:
    """Encode a HERO SOUL (将魂, container type 4) container.

    ``souls`` = (position, guid, tid, count). ``CS_HEROSOUL_DEF`` is a single
    placeholder byte, so one item = 29-byte ``CS_ITEM_DEF`` header + 1. The
    将星录 "将魂 0/30" counters read ``curr_count`` from here.
    """

    try:
        response = bytearray(
            _FIXED_HERO_CARD_CONTAINER_PREFIX.pack(
                GET_ITEM_CONTAINER_RESPONSE,
                HERO_SOUL_CONTAINER_TYPE,
                sequence,
                1,
                0,
                len(souls),
                len(souls),
                len(souls),
                len(souls),
            )
        )
        for position, guid, item_tid, count in souls:
            response.extend(_ITEM_SLOT_PREFIX.pack(position, 0))
            response.extend(_ITEM_PREFIX.pack(guid, count, HERO_SOUL_ITEM_TYPE,
                                              item_tid, 0, 0))
            response.extend(_INT8.pack(0))  # CS_HEROSOUL_DEF.data placeholder
    except struct.error as error:
        raise ValueError("hero-soul container field is outside its TDR wire range") from error
    return bytes(response)


def encode_item_update_push(*, position: int, guid: int,
                            hero_resource_id: int, serial_id: int = 1,
                            weapons: tuple[tuple[int, ...], ...] = ()) -> bytes:
    """Encode ``CS_PROTO_ITEM_UPDATE_ITEM_MSG`` (sub 3) for one hero card.

    This is the async half of the unlock flow: cards that did not fit into the
    container part arrive this way, and it is also what a successful
    ITEM_MERGE (unlock) transaction is answered with.
    """

    try:
        body = bytearray(_UINT16.pack(ITEM_UPDATE_ITEM_MESSAGE))
        body.extend(_INT32.pack(HERO_CARD_CONTAINER_TYPE))
        body.extend(struct.pack(">q", serial_id))
        body.extend(_INT32.pack(1))  # item_num
    except struct.error as error:
        raise ValueError("item-update field is outside its TDR wire range") from error
    body.extend(_hero_card_slot(position, guid, hero_resource_id, weapons))
    return bytes(body)


def encode_item_container_notify(total_items: int) -> bytes:
    """Encode ``CS_PROTO_ITEM_UPDATE_CONT_NOTIFY`` (sub 7) for the HEROCARD
    container — the 2026-08 authoritative flow needs it after the sub-3
    updates for the cards to land in the running inventory."""

    try:
        return (_UINT16.pack(ITEM_UPDATE_CONT_NOTIFY)
                + _CONTAINER_COUNTS.pack(HERO_CARD_CONTAINER_TYPE,
                                         total_items, total_items, total_items))
    except struct.error as error:
        raise ValueError("container-notify field is outside its TDR wire range") from error


def decode_item_merge_request(body: bytes) -> tuple[bytes, int]:
    """Decode ``ITEM_MERGE_REQ`` (root40/sub4) — the unlock transaction.

    Live capture (70 bytes): selector u16 | op_type i32 = 7 | serial_id i64 |
    data 56B, where ``data[0:4]`` is the unlock_id (10000 = 程普).
    Returns (serial_id wire bytes for echo, unlock_id).
    """

    if len(body) != 70:
        raise ValueError(f"item-merge request must contain exactly 70 bytes, got {len(body)}")
    selector, op_type = _UINT16.unpack_from(body), _INT32.unpack_from(body, 2)[0]
    if selector[0] != MERGE_ITEM_REQUEST:
        raise ValueError(
            f"item-merge request expected selector 0x{MERGE_ITEM_REQUEST:04X}, got 0x{selector[0]:04X}"
        )
    if op_type != MERGE_OP_HERO_CARD_UNLOCK:
        raise ValueError(
            f"item-merge request expected op_type {MERGE_OP_HERO_CARD_UNLOCK}, got {op_type}"
        )
    return body[6:14], _UINT32.unpack_from(body, 14)[0]


def encode_item_merge_response(serial_id: bytes, *, result: int = 0) -> bytes:
    """``CS_PROTO_ITEM_MERGE_RSP``: op_type | serial_id echo | ret (SH_ERROR_DEF).

    The authoritative struct is 16 bytes on the wire (nUnitSize=16) — it has
    **no** item list member, so anything longer fails TDR decode silently and
    the client just retries the unlock forever.
    """

    try:
        return (_UINT16.pack(MERGE_ITEM_RESPONSE) + _INT32.pack(MERGE_OP_HERO_CARD_UNLOCK)
                + serial_id + _INT32.pack(result))
    except struct.error as error:
        raise ValueError("item-merge response field is outside its TDR wire range") from error


def _battle_hero_slot(
    slot: int,
    guid: int,
    hero_resource_id: int,
    remaining_stamina: int = FIXED_BATTLE_HERO_REMAINING_STAMINA,
    weapons: tuple[tuple[int, int, int], ...] = (),
    mount_tid: int = 0,
) -> bytes:
    """Encode one occupied ``CS_BATTLE_HERO_DEF`` slot.

    ``slot`` is the position inside the battle-hero container (the 编队 slots),
    while ``guid`` is the *hero card*'s guid — the formation response binds the
    two containers by that guid.

    ``weapons`` are ``(weapon_slot, weapon_tid, is_using)`` triples straight from
    ``contracts.battleLoadout``.  An empty tuple keeps the slot at 88B (unarmed);
    each entry appends a 106B ``CS_BATTLE_HERO_WEAPON_SLOT_DEF`` so 1/2/3 换武器在
    客户端能点亮。``is_using`` is a VISION-only concept and is not on the wire
    here — the client defaults to the lowest ``weapon_slot``.

    ``mount_tid`` 是名册推出来的坐骑 tid（步兵 0）。⭐ 2026-10-01 修正：这里**以前恒写
    0**（返回类型 6 元组里第 2 位在 ``battleHeroes`` 被丢掉），所以骑术/骑射训练关的
    马超、张任在外头装备栏有马、进图却是步战。名册坐骑组生效后，同一条 ``battleLoadout``
    的第 2 位要同时喂 VISION actor（``actorVision`` 一直在喂）和这条本体容器，两边口径
    才一致。默认 0 ⇒ 步兵逐字节不变。

    ``CS_BATTLE_HERO_DEF``::

        weapon_slot_num u16 | weapon_slots: weapon_slot_num ×
        CS_BATTLE_HERO_WEAPON_SLOT_DEF(106B)
        | mount_tid i32 | avatar_slots[8] | cd_time_ms | upgrade_lv | spirit_lv
        | hero_badge | remaining_stamina

    单条 106B = ``{slot u8 1B, weapon_info 105B}``，``weapon_info`` 是那件武器完整的
    ``CS_WEAPON_INFO_DEF``（``weapon_tid i32`` + 101 字节 0），**不是**大厅卡那套 9B
    ``CS_HERO_WEAPON_SLOT_DEF`` 引用（``{slot, group, index}``，两个结构名字只差一个
    BATTLE）。按 9B 填过，出战容器从 491B 变 617B、客户端读不到出战武将、卡在选将
    （2026-09-21 实机）。这里改用 106B 真布局，105B 部分与大厅卡 ``_hero_card_slot``
    里那条同一字节口径（大厅不崩，故这段布局已验证）。
    """
    if mount_tid is None:
        mount_tid = 0


    try:
        body = bytearray(_ITEM_SLOT_PREFIX.pack(slot, 0))
        body.extend(
            _ITEM_PREFIX.pack(
                guid,
                1,
                BATTLE_HERO_ITEM_TYPE,
                hero_resource_id,
                0,
                0,
            )
        )
        body.extend(_UINT16.pack(len(weapons)))  # weapon_slot_num
        for weapon_slot, weapon_tid, *_rest in weapons:
            body.extend(struct.pack(">Bi", weapon_slot, weapon_tid))
            body.extend(bytes(101))  # 其余 CS_WEAPON_INFO_DEF 字段：升级次数/等级/rune 等全 0
        body.extend(
            struct.pack(
                ">i8iQbbih",
                mount_tid,  # 坐骑 tid（名册推；步兵 0）
                *(0 for _ in range(8)),  # avatar_slots
                0,  # cd_time_ms
                1,  # upgrade_lv
                0,  # spirit_lv
                0,  # hero_badge
                remaining_stamina,
            )
        )
    except struct.error as error:
        raise ValueError("battle-hero slot field is outside its TDR wire range") from error
    expected = _BATTLE_HERO_SLOT_SIZE + _HERO_WEAPON_ENTRY_SIZE * len(weapons)
    if len(body) != expected:
        raise AssertionError(
            f"battle-hero slot length invariant failed: {len(body)} != {expected}"
        )
    return bytes(body)


def encode_battle_hero_item_container_response(
    sequence: int,
    heroes: tuple[tuple[int, int, int], ...],
) -> bytes:
    """Encode the type-2 (出战武将) container holding ``heroes`` = (pos, guid, tid).

    ``heroes`` are already resolved to container slots 1..N by the caller.
    """

    try:
        response = bytearray(
            _FIXED_HERO_CARD_CONTAINER_PREFIX.pack(
                GET_ITEM_CONTAINER_RESPONSE,
                BATTLE_HERO_CONTAINER_TYPE,
                sequence,
                1,
                0,
                len(heroes),
                len(heroes),
                len(heroes),
                len(heroes),
            )
        )
    except struct.error as error:
        raise ValueError("battle-hero container field is outside its TDR wire range") from error
    for position, guid, hero_resource_id in heroes:
        response.extend(_battle_hero_slot(position, guid, hero_resource_id))
    expected = _CONTAINER_HEADER_SIZE + _BATTLE_HERO_SLOT_SIZE * len(heroes)
    if len(response) != expected:
        raise AssertionError(
            f"battle-hero container length invariant failed: {len(response)} != {expected}"
        )
    return bytes(response)


def encode_fixed_battle_hero_item_container_response(
    *,
    sequence: int,
    position: int = FIXED_BATTLE_HERO_POSITION,
    guid: int = FIXED_BATTLE_HERO_GUID,
    hero_resource_id: int = FIXED_HERO_CARD_RESOURCE_ID,
    remaining_stamina: int = FIXED_BATTLE_HERO_REMAINING_STAMINA,
    selector: int = GET_ITEM_CONTAINER_RESPONSE,
    weapons: tuple[tuple[int, int, int], ...] = (),
    mount_tid: int = 0,
) -> bytes:
    """Encode the single-hero form of the type-2 container.

    This is separate from the type-1 HEROCARD slot.  The instance server uses
    selector ``0x0066`` around the same ``CS_PROTO_ITEM_GET_CONT_RSP`` payload,
    while the lobby LOGIC_ITEM form uses selector ``0x0002``.

    ``weapons`` are forwarded to :func:`_battle_hero_slot` so the 出战 container
    carries the hero's 1/2/3 换武器条 目（106B/条）；省略则维持 88B 空手。
    ``mount_tid`` 同样透传（步兵 0）。
    """

    try:
        prefix = bytearray(
            _FIXED_HERO_CARD_CONTAINER_PREFIX.pack(
                selector,
                BATTLE_HERO_CONTAINER_TYPE,
                sequence,
                1,
                0,
                1,
                1,
                1,
                1,
            )
        )
    except struct.error as error:
        raise ValueError("fixed battle-hero item field is outside its TDR wire range") from error
    prefix.extend(_battle_hero_slot(
        position, guid, hero_resource_id, remaining_stamina, weapons, mount_tid))
    return bytes(prefix)


def encode_fixed_battle_hero_sync_item_container_response(
    *,
    sequence: int,
    position: int = FIXED_BATTLE_HERO_POSITION,
    guid: int = FIXED_BATTLE_HERO_GUID,
    hero_resource_id: int = FIXED_HERO_CARD_RESOURCE_ID,
    remaining_stamina: int = FIXED_BATTLE_HERO_REMAINING_STAMINA,
    weapons: tuple[tuple[int, int, int], ...] = (),
    mount_tid: int = 0,
) -> bytes:
    return encode_fixed_battle_hero_item_container_response(
        sequence=sequence,
        position=position,
        guid=guid,
        hero_resource_id=hero_resource_id,
        remaining_stamina=remaining_stamina,
        selector=0x0066,
        weapons=weapons,
        mount_tid=mount_tid,
    )


def encode_empty_custom_data_response() -> bytes:
    return _UINT16.pack(PULL_CUSTOM_DATA_RESPONSE) + _INT16.pack(0)


def decode_country_governor_request(body: bytes) -> int:
    if len(body) != _COUNTRY_GOVERNOR_REQUEST.size:
        raise ValueError(
            f"country-governor request must contain exactly {_COUNTRY_GOVERNOR_REQUEST.size} bytes, got {len(body)}"
        )
    selector, country_id = _COUNTRY_GOVERNOR_REQUEST.unpack(body)
    if selector != QUERY_GOVERNOR_HERO_REQUEST:
        raise ValueError(
            f"country-governor request expected selector 0x{QUERY_GOVERNOR_HERO_REQUEST:04X}, got 0x{selector:04X}"
        )
    return country_id


def encode_empty_country_governor_response(country_id: int) -> bytes:
    try:
        return _EMPTY_COUNTRY_GOVERNOR_RESPONSE.pack(
            QUERY_GOVERNOR_HERO_RESPONSE,
            0,
            country_id,
            0,
        )
    except struct.error as error:
        raise ValueError("country-governor response field is outside its TDR wire range") from error


def encode_empty_battle_hero_response(*, formation_index: int = 0) -> bytes:
    try:
        response = bytearray(_UINT16.pack(GET_BATTLE_HERO_RESPONSE))
        response.extend(_INT32.pack(formation_index))
        for _ in range(5):
            response.extend(_INT32.pack(0))  # max_battle_hero_num
            response.extend(_INT32.pack(0))  # battle_hero_num
            response.extend(_INT32.pack(0))  # comp_num
        return bytes(response)
    except struct.error as error:
        raise ValueError("battle-hero response field is outside its TDR wire range") from error


def encode_battle_hero_response(
    *,
    formation_index: int,
    heroes: tuple[tuple[int, int], ...],
) -> bytes:
    """Encode ``CS_PROTO_LOGIC_HERO_GET_BATTLE_HERO_RSP``.

    ``heroes`` = (hero_card_guid, hero_card_position) for the formation the
    client currently has selected.  Prior art (原版客户端恢复与人机入口实现原理 §2)
    fixes five formation elements and selects the active one through
    ``curr_battle_form_index``; the rest stay empty.  ``max_battle_hero_num`` is
    what opens the 编队 slots in the lobby UI.
    """

    try:
        response = bytearray(_UINT16.pack(GET_BATTLE_HERO_RESPONSE))
        response.extend(_INT32.pack(formation_index))
        for index in range(_BATTLE_FORMATION_ELEMENT_COUNT):
            entries = heroes if index == formation_index else ()
            response.extend(_INT32.pack(len(entries)))  # max_battle_hero_num
            response.extend(_INT32.pack(len(entries)))  # battle_hero_num
            for guid, hero_card_position in entries:
                response.extend(struct.pack(">Qi", guid, hero_card_position))
            response.extend(_INT32.pack(0))  # hero_comp.comp_num
    except struct.error as error:
        raise ValueError("battle-hero response field is outside its TDR wire range") from error
    expected = _UINT16.size + _INT32.size * (
        1 + 3 * _BATTLE_FORMATION_ELEMENT_COUNT
    ) + 12 * len(heroes)
    if len(response) != expected:
        raise AssertionError(
            f"battle-hero formation length invariant failed: {len(response)} != {expected}"
        )
    return bytes(response)


def encode_fixed_battle_hero_response(
    *,
    formation_index: int = FIXED_BATTLE_HERO_FORMATION_INDEX,
    guid: int = FIXED_BATTLE_HERO_GUID,
    hero_card_position: int = FIXED_HERO_CARD_POSITION,
) -> bytes:
    """Encode one active formation linked to the occupied HEROCARD position."""

    return encode_battle_hero_response(
        formation_index=formation_index,
        heroes=((guid, hero_card_position),),
    )


def _battle_hero_container_slots(
    battle_heroes: tuple[tuple[int, int, int], ...],
) -> tuple[tuple[int, int, int], ...]:
    """(herocard_pos, guid, tid) -> type-2 container (slot 1..N, guid, tid)."""

    return tuple(
        (index + 1, guid, hero_resource_id)
        for index, (_hero_card_position, guid, hero_resource_id) in enumerate(battle_heroes)
    )


def _battle_hero_formation_entries(
    battle_heroes: tuple[tuple[int, int, int], ...],
) -> tuple[tuple[int, int], ...]:
    """(herocard_pos, guid, tid) -> formation (guid, herocard_pos)."""

    return tuple((guid, hero_card_position)
                 for hero_card_position, guid, _hero_resource_id in battle_heroes)


def build_login_flow_responses(
    message: Method3UplinkMessage,
    *,
    include_fixed_battle_hero: bool = False,
    hero_cards: tuple[tuple[int, int, int], ...] = (),
    hero_souls: tuple[tuple[int, int, int, int], ...] = (),
    battle_heroes: tuple[tuple[int, int, int], ...] = (),
    hero_weapons: dict[int, tuple[tuple[int, ...], ...]] | None = None,
    container_card_limit: int = 0,
    unlocks: dict[int, tuple[int, int, int]] | None = None,
) -> tuple[LoginFlowResponse, ...]:
    selector = _selector(message.body)
    if message.command_id == LOGIN_COMMAND and selector == PULL_CUSTOM_DATA_REQUEST:
        if len(message.body) != 3:
            raise ValueError(
                f"pull-custom-data request must contain exactly 3 bytes, got {len(message.body)}"
            )
        return (
            LoginFlowResponse(
                LOGIN_COMMAND,
                encode_empty_custom_data_response(),
                "empty-custom-data",
            ),
        )
    if message.command_id == COUNTRY_COMMAND and selector == QUERY_GOVERNOR_HERO_REQUEST:
        country_id = decode_country_governor_request(message.body)
        return (
            LoginFlowResponse(
                COUNTRY_COMMAND,
                encode_empty_country_governor_response(country_id),
                "empty-country-governor-heroes",
            ),
        )
    if message.command_id == LOGIC_ITEM_COMMAND and selector == GET_ITEM_CONTAINER_REQUEST:
        container_type, sequence = decode_item_container_request(message.body)
        if hero_cards and container_type == HERO_CARD_CONTAINER_TYPE:
            part = hero_cards[:container_card_limit] if container_card_limit else hero_cards
            return (
                LoginFlowResponse(
                    LOGIC_ITEM_COMMAND,
                    encode_hero_card_item_container_response(sequence, part, hero_weapons),
                    "hero-card-item-container",
                ),
            )
        if hero_souls and container_type == HERO_SOUL_CONTAINER_TYPE:
            return (
                LoginFlowResponse(
                    LOGIC_ITEM_COMMAND,
                    encode_herosoul_item_container_response(sequence, hero_souls),
                    "hero-soul-item-container",
                ),
            )
        if container_type == BATTLE_HERO_CONTAINER_TYPE and battle_heroes:
            return (
                LoginFlowResponse(
                    LOGIC_ITEM_COMMAND,
                    encode_battle_hero_item_container_response(
                        sequence,
                        _battle_hero_container_slots(battle_heroes)),
                    "battle-hero-item-container",
                ),
            )
        if include_fixed_battle_hero and container_type == BATTLE_HERO_CONTAINER_TYPE:
            return (
                LoginFlowResponse(
                    LOGIC_ITEM_COMMAND,
                    encode_fixed_battle_hero_item_container_response(sequence=sequence),
                    "fixed-battle-hero-item-container",
                ),
            )
        return (
            LoginFlowResponse(
                LOGIC_ITEM_COMMAND,
                encode_empty_item_container_response(container_type, sequence),
                "empty-item-container",
            ),
        )
    if (message.command_id == LOGIC_ITEM_COMMAND
            and selector == MERGE_ITEM_REQUEST and len(message.body) == 70):
        # 「解锁武将」按钮 = ITEM_MERGE_REQ。应答只有 18B（结构里没有物品
        # 列表），新卡靠紧随其后的 sub3/sub7 推送进入运行库存。
        serial_id, unlock_id = decode_item_merge_request(message.body)
        responses = [LoginFlowResponse(
            LOGIC_ITEM_COMMAND,
            encode_item_merge_response(serial_id),
            "item-merge-response",
        )]
        card = (unlocks or {}).get(unlock_id)
        if card is not None:
            position, guid, hero_resource_id = card
            responses.append(LoginFlowResponse(
                LOGIC_ITEM_COMMAND,
                encode_item_update_push(position=position, guid=guid,
                                        hero_resource_id=hero_resource_id,
                                        serial_id=int.from_bytes(serial_id, "big"),
                                        weapons=(hero_weapons or {}).get(position, ())),
                "item-update-push",
            ))
            responses.append(LoginFlowResponse(
                LOGIC_ITEM_COMMAND,
                encode_item_container_notify(
                    total_items=len(hero_cards) if hero_cards else 1),
                "item-container-notify",
            ))
        return tuple(responses)
    if message.command_id == LOGIC_HERO_COMMAND and selector == GET_BATTLE_HERO_REQUEST:
        if len(message.body) != 3:
            raise ValueError(
                f"get-battle-hero request must contain exactly 3 bytes, got {len(message.body)}"
            )
        if battle_heroes:
            return (
                LoginFlowResponse(
                    LOGIC_HERO_COMMAND,
                    encode_battle_hero_response(
                        formation_index=FIXED_BATTLE_HERO_FORMATION_INDEX,
                        heroes=_battle_hero_formation_entries(battle_heroes)),
                    "battle-hero-formations",
                ),
            )
        return (
            LoginFlowResponse(
                LOGIC_HERO_COMMAND,
                (
                    encode_fixed_battle_hero_response()
                    if include_fixed_battle_hero
                    else encode_empty_battle_hero_response()
                ),
                (
                    "fixed-battle-hero-formations"
                    if include_fixed_battle_hero
                    else "empty-battle-hero-formations"
                ),
            ),
        )
    return ()
