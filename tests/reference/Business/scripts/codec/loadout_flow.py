"""Native lobby formation and weapon-slot messages; no UI-specific side channel."""
import struct

HERO_COMMAND, ITEM_COMMAND = 0x22, 0x28
INVALID, SAVE_FAILED = 630001, 630000


def reference(catalogue, heroId):
    return struct.pack('>Qi', heroId, catalogue.position(heroId))


def weaponInfo(tid):
    return struct.pack('>ihbf', tid, 0, 0, 0.0) + bytes(10 + 7 * 12)


def slotItem(position, heroId, kind, payload):
    return struct.pack('>ibQiiibQ', position, 0, heroId, 1, kind, heroId, 0, 0) + payload


def heroCard(profile, catalogue, heroId):
    hero = catalogue.heroes[heroId]
    payload = struct.pack('>bbbihii', 2 if heroId in profile['formation'] else 1, 1, 0, 0, 100, 0, 0)
    # Native UI compacts zero-TID groups: keep unsupported entries present but locked.
    payload += b''.join(struct.pack('>b', 1 if hero.groups[g][i] else 2) + weaponInfo(tid)
                        for g, group in enumerate(hero.nativeGroups) for i, tid in enumerate(group))
    slots = profile['weapons'][str(heroId)]
    payload += struct.pack('>h', len(slots))
    payload += b''.join(struct.pack('>bii', s['slot'], hero.groupIds[s['group']], s['index']) for s in slots)
    payload += bytes(12 * 16) + struct.pack('>hhiiQ', 0, 0, 0, 0, 0)
    return slotItem(catalogue.position(heroId), heroId, 1, payload)


def battleHero(profile, catalogue, heroId, position):
    slots = profile['weapons'][str(heroId)]
    payload = struct.pack('>h', len(slots)) + b''.join(
        struct.pack('>b', s['slot']) + weaponInfo(catalogue.heroes[heroId].weapon(s['group'], s['index']))
        for s in slots)
    payload += struct.pack('>i8iQbbih', 0, *([0] * 8), 0, 1, 0, 0, 100)
    return slotItem(position, heroId, 2, payload)


def containers(profile, catalogue, container, sequence, *, selector=2):
    items = ([heroCard(profile, catalogue, tid) for tid in catalogue.heroes] if container == 1 else
             [battleHero(profile, catalogue, tid, i + 1) for i, tid in enumerate(profile['formation'])])
    capacity = len(catalogue.heroes) if container == 1 else 4
    count = max(1, (len(items) + 49) // 50)
    return [struct.pack('>Hiibbiiii', selector, container, sequence, count, i, capacity, capacity,
                        len(items), len(items[i * 50:(i + 1) * 50])) + b''.join(items[i * 50:(i + 1) * 50])
            for i in range(count)]


def formations(profile, catalogue):
    result = struct.pack('>Hi', 4, 1)
    for formation in range(5):
        ids = profile['formation'] if formation == 1 else []
        result += struct.pack('>ii', 4 if formation == 1 else 0, len(ids))
        result += b''.join(reference(catalogue, tid) for tid in ids) + struct.pack('>i', 0)
    return result


def decodeFormation(body, catalogue):
    if len(body) < 10:
        raise ValueError('truncated formation request')
    selector, form, count = struct.unpack_from('>Hii', body)
    if selector != 5 or form != 1 or not 0 <= count <= 4 or len(body) != 10 + count * 12:
        raise ValueError('invalid formation request')
    return [catalogue.reference(*struct.unpack_from('>Qi', body, 10 + i * 12)) for i in range(count)]


def decodeWeapons(body, catalogue):
    if len(body) < 16:
        raise ValueError('truncated weapon-slot request')
    selector, guid, pos, count = struct.unpack_from('>HQih', body)
    if selector != 14 or not 0 <= count <= 12 or len(body) != 16 + count * 12:
        raise ValueError('invalid weapon-slot request')
    heroId = catalogue.reference(guid, pos)
    hero, slots, occupied = catalogue.heroes[heroId], [], set()
    for i in range(count):
        slot, groupId, index = struct.unpack_from('>iii', body, 16 + i * 12)
        if not 0 <= slot <= 12 or (slot and slot in occupied):
            raise ValueError('invalid or duplicate weapon slot')
        occupied.add(slot)
        if groupId == 0 and index in (-1, 0):
            continue
        if groupId <= 0 or groupId not in hero.groupIds:
            raise ValueError('weapon group does not belong to this hero')
        group = hero.groupIds.index(groupId)
        hero.weapon(group, index)
        slots.append(dict(slot=slot, group=group, index=index))
    return heroId, slots


def result(selector, error=0):
    if selector == 5:
        return struct.pack('>Hiii', 6, error, 1, 0)
    if selector == 14:
        return struct.pack('>Hi', 15, error)
    if selector == 8:
        return struct.pack('>Hi', 9, error)
    raise ValueError('unsupported loadout result selector')


def weaponNotify(profile, catalogue, heroId):
    slots = profile['weapons'][str(heroId)]
    return struct.pack('>H', 16) + reference(catalogue, heroId) + struct.pack('>h', len(slots)) + b''.join(
        struct.pack('>iii', s['slot'], catalogue.heroes[heroId].groupIds[s['group']], s['index']) for s in slots)


def heroUpdate(profile, catalogue, heroId):
    return struct.pack('>HiQi', 3, 1, profile['revision'], 1) + heroCard(profile, catalogue, heroId)
