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
MOVE_BC_WITH_SYSTEM_AND_ACTIVE = 0x0026
MOVE_BC_WITH_SYSTEM_AND_ACTIVE_BODY_LENGTH = 40
MOVE_IN_AIR_STATE_BC = 0x0036
# Semantic candidates from sh_proto_cs; selector 38's plain fields do not carry
# explicit TDR enum bindings, so the generic codec does not enforce them.
MOVE_GROUND_STATE_STOP = 1
MOVE_MESSAGE_GROUP_SERVER_INTERNAL = 3
_MOVE_BC_STRUCT_FORMAT = ">HIHBhhihfffhibh"
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

    theta = math.radians(heading_degrees)
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
