"""Minimal M4 BATTLE S2C codec for HIT / death / settlement after C2S cmd=4."""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass


BATTLE_COMMAND = 0x0004
STATE_SYNC_SIMPLE = 0x0003
ACT_STATE_IDLE = 2
HIT_POINT_NOTIFY = 0x001B
BATTLE_RESULT = 0x0009
DEAD_NOTIFY = 0x000A
DATA_UPDATE_NOTIFY = 0x000B
LIFE_STATE_NONE = 0
LIFE_STATE_KILL = 1
LIFE_STATE_REVIVE = 2
RESULT_DATA_INVALID = 0
RESULT_DATA_HP = 1
RESULT_DATA_DELTA_HP = 2
RESULT_DATA_FORCE_DAMAGE = 3
RESULT_DATA_NORMAL_DAMAGE = 4
RESULT_DATA_IS_CRITICAL = 5
RESULT_DATA_DECAY_DELTA_HP = 6
RESULT_DATA_MAX = 7


@dataclass(frozen=True, slots=True)
class BattleStateSyncSimple:
    instance_id: int
    seq_no: int
    state: int
    state_change_ms: int
    state_time_ms: int


def encode_battle_state_sync_simple(
    *, instance_id: int, seq_no: int, state: int,
    state_change_ms: int, state_time_ms: int,
) -> bytes:
    """编码不含攻击目标、破防和随机动画索引的状态同步。"""
    fields = (instance_id, seq_no, state, state_change_ms, state_time_ms)
    maxima = (0xFFFF, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFF)
    if any(type(value) is not int or not 0 <= value <= maximum
           for value, maximum in zip(fields, maxima)):
        raise ValueError("battle state-sync field is outside its unsigned TDR wire range")
    return struct.pack(">HHHHIH", STATE_SYNC_SIMPLE, *fields)


def decode_battle_state_sync_simple(body: bytes) -> BattleStateSyncSimple:
    """仅接受简化版14字节报文，拒绝完整版本和尾随数据。"""
    if not isinstance(body, bytes):
        raise TypeError("body must be bytes")
    if len(body) != 14:
        raise ValueError("battle state-sync simple must contain exactly 14 bytes")
    selector, *fields = struct.unpack(">HHHHIH", body)
    if selector != STATE_SYNC_SIMPLE:
        raise ValueError("battle state-sync simple expected selector 3")
    return BattleStateSyncSimple(*fields)


@dataclass(frozen=True, slots=True)
class BattleHitPointNotify:
    hit_pos: tuple[float, float, float]
    attacker_mid: int
    target_mid: int
    attacker_weapon_id: int
    target_weapon_id: int
    server_time_ms: int


@dataclass(frozen=True, slots=True)
class BattleDeadNotify:
    entity_rid: int
    server_time_ms: int


@dataclass(frozen=True, slots=True)
class BattleResult:
    attacker_rid: int
    target_rid: int
    attacker_seq_no: int
    target_life_state: int
    attack_weapon_id: int
    target_result_type: int
    target_result_data: int


@dataclass(frozen=True, slots=True)
class BattleDataUpdateNotify:
    result_type: int
    result_data: int


def encode_battle_data_update_notify(*, result_type: int, result_data: int) -> bytes:
    if result_type < RESULT_DATA_INVALID or result_type >= RESULT_DATA_MAX:
        raise ValueError("result_type must be in the declared range [0, 7)")
    try:
        return struct.pack(">Hhi", DATA_UPDATE_NOTIFY, result_type, result_data)
    except struct.error as error:
        raise ValueError("battle data-update field is outside its TDR wire range") from error


def decode_battle_data_update_notify(body: bytes) -> BattleDataUpdateNotify:
    if not isinstance(body, bytes):
        raise TypeError("body must be bytes")
    if len(body) != 8:
        raise ValueError(f"battle data-update must contain exactly 8 bytes, got {len(body)}")
    selector, result_type, result_data = struct.unpack(">Hhi", body)
    if selector != DATA_UPDATE_NOTIFY:
        raise ValueError(f"battle data-update expected selector 0x{DATA_UPDATE_NOTIFY:04X}")
    return BattleDataUpdateNotify(result_type=result_type, result_data=result_data)


def _require_triple(value: object, field_name: str) -> tuple[float, float, float]:
    if not isinstance(value, tuple) or len(value) != 3:
        raise TypeError(f"{field_name} must be a tuple of three floats")
    if not all(isinstance(item, (int, float)) and math.isfinite(item) for item in value):
        raise ValueError(f"{field_name} values must be finite numbers")
    return (float(value[0]), float(value[1]), float(value[2]))


def encode_battle_hit_point_notify(
    *,
    hit_pos: tuple[float, float, float],
    attacker_mid: int,
    target_mid: int,
    attacker_weapon_id: int,
    target_weapon_id: int,
    server_time_ms: int,
) -> bytes:
    position = _require_triple(hit_pos, "hit_pos")
    try:
        return struct.pack(
            ">Hfffb4f4fiQiQiIQBBi",
            HIT_POINT_NOTIFY,
            *position,
            0,
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0,
            attacker_mid,
            attacker_weapon_id,
            target_mid,
            target_weapon_id,
            0,
            server_time_ms,
            0,
            1,
            0,
        )
    except struct.error as error:
        raise ValueError("battle hit-point field is outside its TDR wire range") from error


def decode_battle_hit_point_notify(body: bytes) -> BattleHitPointNotify:
    if not isinstance(body, bytes):
        raise TypeError("body must be bytes")
    if len(body) != 93:
        raise ValueError(f"battle hit-point must contain exactly 93 bytes, got {len(body)}")
    selector = struct.unpack_from(">H", body)[0]
    if selector != HIT_POINT_NOTIFY:
        raise ValueError(f"battle hit-point expected selector 0x{HIT_POINT_NOTIFY:04X}")
    hit_pos = struct.unpack_from(">fff", body, 2)
    attacker_mid = struct.unpack_from(">Q", body, 51)[0]
    attacker_weapon_id = struct.unpack_from(">i", body, 59)[0]
    target_mid = struct.unpack_from(">Q", body, 63)[0]
    target_weapon_id = struct.unpack_from(">i", body, 71)[0]
    server_time_ms = struct.unpack_from(">Q", body, 79)[0]
    return BattleHitPointNotify(
        hit_pos=hit_pos,
        attacker_mid=attacker_mid,
        target_mid=target_mid,
        attacker_weapon_id=attacker_weapon_id,
        target_weapon_id=target_weapon_id,
        server_time_ms=server_time_ms,
    )


def encode_battle_dead_notify(*, entity_rid: int, server_time_ms: int) -> bytes:
    try:
        return struct.pack(">HQQ", DEAD_NOTIFY, entity_rid, server_time_ms)
    except struct.error as error:
        raise ValueError("battle dead-notify field is outside its TDR wire range") from error


def decode_battle_dead_notify(body: bytes) -> BattleDeadNotify:
    if not isinstance(body, bytes):
        raise TypeError("body must be bytes")
    if len(body) != 18:
        raise ValueError(f"battle dead-notify must contain exactly 18 bytes, got {len(body)}")
    selector, entity_rid, server_time_ms = struct.unpack(">HQQ", body)
    if selector != DEAD_NOTIFY:
        raise ValueError(f"battle dead-notify expected selector 0x{DEAD_NOTIFY:04X}")
    return BattleDeadNotify(entity_rid=entity_rid, server_time_ms=server_time_ms)


def encode_battle_result(
    *,
    attacker_rid: int,
    target_rid: int,
    attacker_seq_no: int,
    target_life_state: int,
    attack_weapon_id: int,
    target_result_type: int,
    target_result_data: int,
) -> bytes:
    if target_life_state not in (LIFE_STATE_NONE, LIFE_STATE_KILL, LIFE_STATE_REVIVE):
        raise ValueError("life_state must be NONE(0), KILL(1), or REVIVE(2)")
    empty_result = struct.pack(">hi", 0, 0)
    attacker_results = empty_result * 10
    target_results = struct.pack(">hi", target_result_type, target_result_data) + (
        empty_result * 9
    )
    try:
        return struct.pack(
            ">HQQHbbh",
            BATTLE_RESULT,
            attacker_rid,
            target_rid,
            attacker_seq_no,
            0,
            target_life_state,
            0,
        ) + attacker_results + struct.pack(">h", 1) + target_results + struct.pack(
            ">ibb",
            attack_weapon_id,
            0,
            0,
        )
    except struct.error as error:
        raise ValueError("battle result field is outside its TDR wire range") from error


def decode_battle_result(body: bytes) -> BattleResult:
    if not isinstance(body, bytes):
        raise TypeError("body must be bytes")
    if len(body) != 152:
        raise ValueError(f"battle result must contain exactly 152 bytes, got {len(body)}")
    selector = struct.unpack_from(">H", body)[0]
    if selector != BATTLE_RESULT:
        raise ValueError(f"battle result expected selector 0x{BATTLE_RESULT:04X}")
    attacker_rid = struct.unpack_from(">Q", body, 2)[0]
    target_rid = struct.unpack_from(">Q", body, 10)[0]
    attacker_seq_no = struct.unpack_from(">H", body, 18)[0]
    target_life_state = body[21]
    attacker_num = struct.unpack_from(">h", body, 22)[0]
    target_num = struct.unpack_from(">h", body, 84)[0]
    if attacker_num != 0 or target_num != 1:
        raise ValueError("battle result fixed counts must be attacker=0 target=1")
    target_result_type = struct.unpack_from(">h", body, 86)[0]
    target_result_data = struct.unpack_from(">i", body, 88)[0]
    attack_weapon_id = struct.unpack_from(">i", body, 146)[0]
    return BattleResult(
        attacker_rid=attacker_rid,
        target_rid=target_rid,
        attacker_seq_no=attacker_seq_no,
        target_life_state=target_life_state,
        attack_weapon_id=attack_weapon_id,
        target_result_type=target_result_type,
        target_result_data=target_result_data,
    )
