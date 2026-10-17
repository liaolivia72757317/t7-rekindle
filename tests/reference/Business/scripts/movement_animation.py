"""Locomotion blend coordinates from the selected character, not physical speeds."""
import math
from pathlib import Path
import struct

from .animation_resource import AnimationReader
from .vfs_resource import ResourceError, readResource

CHARACTERS = {
    (110001, 1030411): 'data/actors/havok/shgameexport/characters/man_huanshoudao.hkt',
}
PROPERTIES = {
    'forwardSlow': 'Speed_MoveF_Slow',
    'lateralSlow': 'Speed_MoveLR_Slow',
    'backwardSlow': 'Speed_MoveB_Slow',
    'forwardSprint': 'Speed_MoveF_Sprint',
}


def characterSpeeds(data):
    reader = AnimationReader(data)
    objects, character = [None], None
    while True:
        tag = reader.integer()
        if tag != 4:
            raise ValueError('missing character movement properties')
        classId = reader.integer()
        if not 0 < classId < len(reader.classes):
            raise ValueError('invalid character class reference')
        value = reader.members(reader.classes[classId])[0]
        objects.append(value)
        if value['className'] == 'hkbCharacterData':
            if character is not None:
                raise ValueError('ambiguous character data')
            character = value
        if character is None:
            continue
        stringRef, valueRef = (character.get(key, 0) for key in ('stringData', 'characterPropertyValues'))
        if stringRef <= 0 or valueRef <= 0:
            raise ValueError('invalid character property reference')
        if max(stringRef, valueRef) >= len(objects):
            continue
        strings, values = objects[stringRef], objects[valueRef]
        if strings['className'] != 'hkbCharacterStringData' or values['className'] != 'hkbVariableValueSet':
            raise ValueError('character property reference type mismatch')
        names = strings.get('characterPropertyNames', [])
        words = values.get('wordVariableValues', [])
        infos = character.get('characterPropertyInfos', [])
        if len(names) != len(words) or len(names) != len(infos):
            raise ValueError('character property count mismatch')
        result = {'enabled': True}
        for key, name in PROPERTIES.items():
            if names.count(name) != 1:
                raise ValueError(f'missing or duplicate character property: {name}')
            index = names.index(name)
            bits = words[index].get('value', 0)
            if infos[index].get('type') != 4 or not -0x80000000 <= bits <= 0x7fffffff:
                raise ValueError(f'character property is not a real: {name}')
            number = struct.unpack('<f', struct.pack('<i', bits))[0]
            if not math.isfinite(number) or number <= 0:
                raise ValueError(f'invalid character speed: {name}')
            result[key] = number
        if result['forwardSprint'] <= max(result['forwardSlow'], result['lateralSlow']):
            raise ValueError('character sprint coordinate must exceed forward slow coordinates')
        return result


def loadMovementAnimation(clientDirectory, heroId, weaponId):
    path = CHARACTERS.get((heroId, weaponId), '')
    try:
        if not path:
            raise ResourceError('entry_missing', 'actor.vfs', '', 'no verified movement character binding')
        try:
            (Path(clientDirectory).parent / path).stat()
        except FileNotFoundError:
            pass
        except OSError as error:
            raise ResourceError('archive_read', 'actor.vfs', path, str(error)) from error
        else:
            raise ResourceError('unsupported_overlay', 'actor.vfs', path, 'loose movement character is not supported')
        return readResource(clientDirectory, 'actor.vfs', path, characterSpeeds) | {'weaponId': weaponId}, []
    except ResourceError as error:
        return dict(enabled=False, weaponId=weaponId, **dict.fromkeys(PROPERTIES, 0.0)), [dict(
            level='WARN', message=f'{error} movement-animation-native heroId={heroId} weaponId={weaponId}')]
