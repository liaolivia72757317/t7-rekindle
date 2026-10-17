"""Native weapon slot requests and full equipment-use broadcasts (command 5)."""
import struct

WEAPON_COMMAND = 5
CHANGE_REQUEST, CHANGE_RESPONSE, USE_UPDATE = 1, 2, 3
BREAK_SHIELD, SHIELD_HP_UPDATE = 5, 6
MAIN_SLOT, FIST_SLOT = 1, 13
CANNOT_CHANGE, INVALID_SLOT = 620000, 620001


def decode_change_request(body):
    if type(body) is not bytes or len(body) != 4:
        raise ValueError('weapon change request requires exactly 4 bytes')
    selector, slot = struct.unpack('>Hh', body)
    if selector != CHANGE_REQUEST:
        raise ValueError('unexpected weapon change selector')
    return slot


def encode_change_response(result, right_weapon_id, left_weapon_id=0):
    return struct.pack('>Hiii', CHANGE_RESPONSE, result, right_weapon_id, left_weapon_id)


def encode_shield_break(*, actor_id, weapon_id):
    return struct.pack('>HQi', BREAK_SHIELD, actor_id, weapon_id)


def encode_shield_health(*, weapon_id, weapon_slot, health, maximum):
    if not 0 <= health <= maximum or maximum <= 0:
        raise ValueError('invalid shield health')
    return struct.pack('>Hihii', SHIELD_HP_UPDATE, weapon_id, weapon_slot, health, maximum)


def encode_equipment(items, equipped_weapon_id, offhand_id=0):
    if len(items) > 12 or len({item['slot'] for item in items}) != len(items):
        raise ValueError('invalid equipment slots')
    return struct.pack('>h', len(items)) + b''.join(
        struct.pack('>iBBB', item['weaponId'], item['slot'], 0,
                    int(item['weaponId'] in (equipped_weapon_id, offhand_id))) + bytes(22)
        for item in items)


def encode_equipment_update(*, actor_id, items, equipped_weapon_id, offhand_id=0, now_ms):
    return (struct.pack('>HQ', USE_UPDATE, actor_id)
            + encode_equipment(items, equipped_weapon_id, offhand_id) + struct.pack('>hQ', 1, now_ms))


def encode_use_update(*, actor_id, main_weapon_id, fist_weapon_id, equipped_weapon_id, now_ms):
    return encode_equipment_update(actor_id=actor_id, equipped_weapon_id=equipped_weapon_id, now_ms=now_ms,
        items=[dict(weaponId=main_weapon_id, slot=MAIN_SLOT), dict(weaponId=fist_weapon_id, slot=FIST_SLOT)])
