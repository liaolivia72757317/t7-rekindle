"""Inventory guard-geometry resource facts without assigning an ordinary-guard source."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import re
import struct
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests/reference'), str(ROOT / 'tests/reference/Business/vendor')]

from Business.scripts import client_resources, hero_resources as heroes
from Business.scripts.dash_resources import readSelected

WEAPON_WIDTH = 2829
CATEGORY_WIDTH = 550
ASSOCIATED_CAPACITY = 5
DEFENCE_RESOURCE_CATEGORY = 20
SKIPPED_NATIVE_LAYERS = frozenset((3, 4, 7))
TEMPLATE_COMPONENTS = frozenset(('GeWeapon', 'GeBehaviorable', 'GeHurtable', 'GeAttachable',
                                'GeHavokPhysicable', 'GeHavokInheritable'))


def associatedIds(row):
    count = struct.unpack_from('<H', row, 0x3E2)[0]
    if count > ASSOCIATED_CAPACITY:
        raise ValueError('associated weapon count exceeds resource field capacity')
    return list(struct.unpack_from(f'<{count}I', row, 0x3E4))


def vector(row, offset):
    values = list(struct.unpack_from('<3f', row, offset))
    if not all(math.isfinite(value) for value in values):
        raise ValueError('nonfinite weapon geometry parameter')
    return values


def describeWeapon(tid, row, categories, associated, owners):
    categoryId = struct.unpack_from('<H', row, 56)[0]
    if categoryId not in categories:
        raise ValueError(f'weapon {tid} references missing category {categoryId}')
    category = categories[categoryId]
    layer = struct.unpack_from('<I', category, 0x34)[0]
    return dict(
        weaponId=tid, name=heroes.text(row, 16, 40), categoryId=categoryId,
        family=struct.unpack_from('<H', category, 2)[0],
        hand=struct.unpack_from('<H', category, 46)[0],
        selectableByHeroIds=sorted(owners.get(tid, ())),
        entityTemplate=heroes.text(row, 330, 60), modelPath=heroes.text(row, 390, 120),
        attachmentNode=heroes.text(row, 930, 60),
        rigidBodyName=heroes.text(category, 0x78, 64),
        rigidBodyBone=heroes.text(category, 0xB8, 64), collisionLayer=layer,
        # Passing this gate does not establish entity creation, activation or collision use.
        layerGate='skipped' if layer in SKIPPED_NATIVE_LAYERS else 'passes',
        bodyParameters=dict(positionRaw=vector(row, 0x3F8), rotationRaw=vector(row, 0x404),
                            sizeRaw=vector(row, 0x410)),
        associatedWeaponIds=associated, ordinaryGuardSource=None)


def buildInventory(weaponData, categoryData, catalogue, includeWeaponIds=()):
    weapons = heroes.uniqueRows(weaponData, WEAPON_WIDTH)
    categories = heroes.uniqueRows(categoryData, CATEGORY_WIDTH, '<H')
    associated = {tid: associatedIds(row) for tid, row in weapons.items()}
    owners = {}
    for heroId, hero in catalogue.heroes.items():
        for tid in {heroes.FIST_ID, *(tid for group in hero.groups for tid in group if tid)}:
            owners.setdefault(tid, set()).add(heroId)
    selected, requested = set(owners), set(includeWeaponIds)
    if any(type(tid) is not int or tid <= 0 for tid in requested):
        raise ValueError('requested weapon IDs must be positive integers')
    for tid in sorted(selected | requested):
        if tid not in weapons:
            raise ValueError(f'missing requested weapon {tid}')
    candidates = {tid for tid, row in weapons.items()
                  if struct.unpack_from('<H', row, 56)[0] == DEFENCE_RESOURCE_CATEGORY}
    pending, included, missing = list(selected | requested | candidates), set(), set()
    while pending:
        tid = pending.pop()
        if tid in included or tid in missing:
            continue
        if tid not in weapons:
            missing.add(tid)
            continue
        included.add(tid)
        pending.extend(child for child in associated[tid] if child)
    entries = [describeWeapon(tid, weapons[tid], categories, associated[tid], owners)
               for tid in sorted(included)]
    categoryCounts = Counter(entry['categoryId'] for entry in entries if entry['weaponId'] in selected)
    return dict(
        schemaVersion=1, resourceFactsOnly=True,
        tables=dict(weapons=heroes.WEAPON_PATH, categories=heroes.CATEGORY_PATH),
        heroCount=len(catalogue.heroes), selectedWeaponIds=sorted(selected),
        requestedWeaponIds=sorted(requested), candidateResourceIds=sorted(candidates),
        missingAssociatedWeaponIds=sorted(missing),
        selectedCategoryCounts=[dict(categoryId=key, count=value)
                                for key, value in sorted(categoryCounts.items())],
        candidateReferences=[dict(resourceId=tid, weaponIds=sorted(
            source for source, children in associated.items() if tid in children))
            for tid in sorted(candidates)], weapons=entries)


def templateBindings(data, entityName):
    encoding = re.search(br'encoding\s*=\s*[\"\x27]([^\"\x27]+)', data[:128])
    encoding = encoding.group(1).decode('ascii').lower() if encoding else 'utf-8-sig'
    if encoding in ('gb2312', 'gbk'):
        encoding = 'gbk'
    root = ET.fromstring(data.decode(encoding))
    entities = [entry for entry in root.findall('Entity') if entry.get('Name') == entityName]
    headers = root.findall('Header')
    if root.tag != 'GeEntitySheet' or len(entities) != 1 or len(headers) != 1:
        raise ValueError('expected one header and one matching entity binding')

    def properties(scope):
        return {component.tag: {field.tag: field.get('Value') for field in component}
                for component in scope if component.tag in TEMPLATE_COMPONENTS}

    # Preserve declarations; wildcard/default resolution is not inferred by this inventory.
    return dict(header=properties(headers[0]), entity=properties(entities[0]))


def loadTemplateBindings(clientDirectory, entries):
    sheets, bindings = {}, []
    for template in sorted({entry['entityTemplate'] for entry in entries if entry['entityTemplate']}):
        sheet, separator, entity = template.partition('.')
        if not separator or not sheet or not entity:
            raise ValueError('invalid entity template binding')
        path = f'data/entsheet/{sheet}.esf'
        if path not in sheets:
            sheets[path] = readSelected(clientDirectory, 'data1.vfs', path, lambda value: value)
        bindings.append(dict(template=template, resourcePath=path,
                             **templateBindings(sheets[path], entity)))
    return bindings


def loadInventory(clientDirectory, includeWeaponIds=()):
    directory = Path(clientDirectory)
    if not directory.is_absolute() or not directory.is_dir():
        raise ValueError('selected absolute existing Bin directory required')
    client_resources.checkLayout(directory.parent)
    catalogue = heroes.loadCatalogue(str(directory))
    data = [readSelected(str(directory), 'data1.vfs', path, lambda value: value)
            for path in (heroes.WEAPON_PATH, heroes.CATEGORY_PATH)]
    report = buildInventory(*data, catalogue, includeWeaponIds)
    report['templateBindings'] = loadTemplateBindings(str(directory), report['weapons'])
    return report


def writeInventory(path, value):
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + '\n'
    with Path(path).open('x', encoding='utf-8', newline='\n') as output:
        output.write(encoded)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client-directory', type=Path, required=True,
                        help='selected absolute Bin directory, read only')
    parser.add_argument('--include-weapon', type=int, action='append', default=[],
                        help='include an additional control weapon; repeat for multiple IDs')
    parser.add_argument('--output', type=Path, required=True,
                        help='new JSON file outside the client; existing files are not overwritten')
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(args.client_directory.resolve().parent):
        parser.error('inventory output must be outside the client directory')
    value = loadInventory(args.client_directory, args.include_weapon)
    writeInventory(args.output, value)
    print(json.dumps(dict(heroes=value['heroCount'], weapons=len(value['selectedWeaponIds']),
                          candidates=len(value['candidateResourceIds']), resourceFactsOnly=True)))


if __name__ == '__main__':
    main()
