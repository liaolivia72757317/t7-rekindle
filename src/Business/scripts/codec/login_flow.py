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
QUERY_GOVERNOR_HERO_REQUEST = 0x0030
QUERY_GOVERNOR_HERO_RESPONSE = 0x0031

HERO_CARD_CONTAINER_TYPE = 1
BATTLE_HERO_CONTAINER_TYPE = 2
HERO_CARD_ITEM_TYPE = 1
BATTLE_HERO_ITEM_TYPE = 2
FIXED_HERO_CARD_BATTLE_STATE = 2
FIXED_HERO_CARD_POSITION = 1
FIXED_HERO_CARD_GUID = 1
FIXED_HERO_CARD_RESOURCE_ID = 110001
FIXED_BATTLE_HERO_FORMATION_INDEX = 1
FIXED_BATTLE_HERO_GUID = FIXED_HERO_CARD_GUID
FIXED_BATTLE_HERO_POSITION = FIXED_HERO_CARD_POSITION
FIXED_BATTLE_HERO_REMAINING_STAMINA = 100

_UINT16 = struct.Struct(">H")
_INT16 = struct.Struct(">h")
_INT32 = struct.Struct(">i")
_UINT32 = struct.Struct(">I")
_ITEM_CONTAINER_REQUEST = struct.Struct(">Hii")
_EMPTY_ITEM_CONTAINER_RESPONSE = struct.Struct(">Hiibbiiii")
_COUNTRY_GOVERNOR_REQUEST = struct.Struct(">Hi")
_EMPTY_COUNTRY_GOVERNOR_RESPONSE = struct.Struct(">HiIi")
_FIXED_HERO_CARD_CONTAINER_PREFIX = struct.Struct(">Hiibbiiii")
_ITEM_SLOT_PREFIX = struct.Struct(">ib")
_ITEM_PREFIX = struct.Struct(">QiiibQ")
_HERO_CARD_PREFIX = struct.Struct(">bbbihii")
_BATTLE_HERO_PAYLOAD = struct.Struct(">hi8iQbbih")

_HERO_WEAPON_SET_WIRE_SIZE = 8 * 6 * (1 + 4 + 2 + 1 + 4 + 10 + 7 * 12)
_HERO_EQUIPMENT_REFERENCE_WIRE_SIZE = (8 + 3 + 1) * 16


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


def encode_fixed_hero_card_item_container_response(
    *,
    sequence: int,
    position: int = FIXED_HERO_CARD_POSITION,
    guid: int = FIXED_HERO_CARD_GUID,
    hero_resource_id: int = FIXED_HERO_CARD_RESOURCE_ID,
) -> bytes:
    """Encode one occupied HEROCARD slot for the fixed M4 identity.

    The outer ``CS_ITEM_DEF.item_type`` remains ``E_ITEM_TYPE_HEROCARD`` so
    TDR decodes the matching union branch. The first byte of the nested
    ``CS_HEROCARD_DEF`` is ``E_HEROCARD_ITEM_STATE_BATTLE``. Nested weapon,
    equipment, achievement, and skill-book collections use their declared
    zero/default representation.
    """

    try:
        response = bytearray(
            _FIXED_HERO_CARD_CONTAINER_PREFIX.pack(
                GET_ITEM_CONTAINER_RESPONSE,
                HERO_CARD_CONTAINER_TYPE,
                sequence,
                1,
                0,
                1,
                1,
                1,
                1,
            )
        )
        response.extend(_ITEM_SLOT_PREFIX.pack(position, 0))
        response.extend(
            _ITEM_PREFIX.pack(
                guid,
                1,
                HERO_CARD_ITEM_TYPE,
                hero_resource_id,
                0,
                0,
            )
        )
        response.extend(
            _HERO_CARD_PREFIX.pack(FIXED_HERO_CARD_BATTLE_STATE, 1, 0, 0, 100, 0, 0)
        )
        response.extend(bytes(_HERO_WEAPON_SET_WIRE_SIZE))
        response.extend(_INT16.pack(0))
        response.extend(bytes(_HERO_EQUIPMENT_REFERENCE_WIRE_SIZE))
        response.extend(_INT16.pack(0))
        response.extend(_INT16.pack(0))
        response.extend(bytes(16))
        return bytes(response)
    except struct.error as error:
        raise ValueError("fixed hero-card response field is outside its TDR wire range") from error


def encode_fixed_battle_hero_item_container_response(
    *,
    sequence: int,
    position: int = FIXED_BATTLE_HERO_POSITION,
    guid: int = FIXED_BATTLE_HERO_GUID,
    hero_resource_id: int = FIXED_HERO_CARD_RESOURCE_ID,
    remaining_stamina: int = FIXED_BATTLE_HERO_REMAINING_STAMINA,
    selector: int = GET_ITEM_CONTAINER_RESPONSE,
) -> bytes:
    """Encode one occupied ``CS_BATTLE_HERO_DEF`` item slot.

    This is separate from the type-1 HEROCARD slot.  The instance server uses
    selector ``0x0066`` around the same ``CS_PROTO_ITEM_GET_CONT_RSP`` payload,
    while the lobby LOGIC_ITEM form uses selector ``0x0002``.
    """

    try:
        response = bytearray(
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
        response.extend(_ITEM_SLOT_PREFIX.pack(position, 0))
        response.extend(
            _ITEM_PREFIX.pack(
                guid,
                1,
                BATTLE_HERO_ITEM_TYPE,
                hero_resource_id,
                0,
                0,
            )
        )
        response.extend(
            _BATTLE_HERO_PAYLOAD.pack(
                0,  # weapon_slot_num: no variable weapon-slot entries
                0,  # mount_tid
                *(0 for _ in range(8)),  # avatar_slots
                0,  # cd_time_ms
                1,  # upgrade_lv
                0,  # spirit_lv
                0,  # hero_badge
                remaining_stamina,
            )
        )
        if len(response) != 116:
            raise AssertionError(f"fixed battle-hero item length invariant failed: {len(response)}")
        return bytes(response)
    except struct.error as error:
        raise ValueError("fixed battle-hero item field is outside its TDR wire range") from error


def encode_fixed_battle_hero_sync_item_container_response(
    *,
    sequence: int,
    position: int = FIXED_BATTLE_HERO_POSITION,
    guid: int = FIXED_BATTLE_HERO_GUID,
    hero_resource_id: int = FIXED_HERO_CARD_RESOURCE_ID,
    remaining_stamina: int = FIXED_BATTLE_HERO_REMAINING_STAMINA,
) -> bytes:
    return encode_fixed_battle_hero_item_container_response(
        sequence=sequence,
        position=position,
        guid=guid,
        hero_resource_id=hero_resource_id,
        remaining_stamina=remaining_stamina,
        selector=0x0066,
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


def encode_fixed_battle_hero_response(
    *,
    formation_index: int = FIXED_BATTLE_HERO_FORMATION_INDEX,
    guid: int = FIXED_BATTLE_HERO_GUID,
    hero_card_position: int = FIXED_HERO_CARD_POSITION,
) -> bytes:
    """Encode one active formation linked to the occupied HEROCARD position."""

    try:
        response = bytearray(_UINT16.pack(GET_BATTLE_HERO_RESPONSE))
        response.extend(_INT32.pack(formation_index))
        response.extend(_INT32.pack(0))  # formation[0].max_battle_hero_num
        response.extend(_INT32.pack(0))  # formation[0].battle_hero_num
        response.extend(_INT32.pack(0))  # formation[0].comp_num
        response.extend(_INT32.pack(1))  # max_battle_hero_num
        response.extend(_INT32.pack(1))  # battle_hero_num
        response.extend(struct.pack(">Qi", guid, hero_card_position))
        response.extend(_INT32.pack(0))  # comp_num
        for _ in range(3):
            response.extend(_INT32.pack(0))  # max_battle_hero_num
            response.extend(_INT32.pack(0))  # battle_hero_num
            response.extend(_INT32.pack(0))  # comp_num
        if len(response) != 78:
            raise AssertionError(f"fixed battle-hero formation length invariant failed: {len(response)}")
        return bytes(response)
    except struct.error as error:
        raise ValueError("fixed battle-hero formation field is outside its TDR wire range") from error


def build_login_flow_responses(
    message: Method3UplinkMessage,
    *,
    include_fixed_hero_card: bool = False,
    include_fixed_battle_hero: bool = False,
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
        if include_fixed_hero_card and container_type == HERO_CARD_CONTAINER_TYPE:
            return (
                LoginFlowResponse(
                    LOGIC_ITEM_COMMAND,
                    encode_fixed_hero_card_item_container_response(sequence=sequence),
                    "fixed-hero-card-item-container",
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
    if message.command_id == LOGIC_HERO_COMMAND and selector == GET_BATTLE_HERO_REQUEST:
        if len(message.body) != 3:
            raise ValueError(
                f"get-battle-hero request must contain exactly 3 bytes, got {len(message.body)}"
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
