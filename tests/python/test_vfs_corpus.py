"""Original Python readers verify public synthetic vectors consumed by native Rust."""
from dataclasses import asdict
import json
from pathlib import Path
import struct
import zlib

import pytest

from Business.scripts import bot_names, hero_resources, navmesh_resource, spawn_resource, vfs_resource
from loadout_fixture import resourceTables, table
from vfs_fixture import HEADER, NAV_PATH, NAME_PATH, SPAWN_PATH, archiveBytes, encodeRecord, nameTable, spawnXml, syntheticTagfile, vint

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / 'tests/fixtures/resources'
PATH = '../data/scene/测试.bin'
TABLE_PATHS = (hero_resources.HERO_PATH, hero_resources.UNLOCK_PATH, hero_resources.GROUP_PATH,
               hero_resources.WEAPON_PATH, hero_resources.CATEGORY_PATH)


def vfsCases():
    cases = []

    def add(name, archive, *, unit=b'', repeat=1, error=None, path=PATH, openError=False):
        cases.append(dict(name=name, archiveHex=archive.hex(), path=path, unitHex=unit.hex(), repeat=repeat,
                          error=error, openError=openError))

    add('raw', archiveBytes([(PATH, b'fixture', 1)]), unit=b'fixture')
    add('compressed-repacked', archiveBytes([('../data/other.bin', b'other', 3), (PATH, b'fixture' * 9000, 3)],
        padding=137, level=9, inactive=True), unit=b'fixture', repeat=9000, path='DATA\\SCENE\\测试.BIN')
    add('empty-compressed', archiveBytes([(PATH, b'', 3)]))
    add('empty-raw', archiveBytes([(PATH, b'', 1)]))
    add('false-prefix', archiveBytes([(PATH, b'../data/false\0', 1)]), unit=b'../data/false\0')
    for field, value in [(0, 2), (1, 1), (1, 0xfffffffe), (2, 99999), (3, 0), (7, 2), (8, 1), (9, 1), (10, 1)]:
        add(f'invalid-field-{field}-{value}', archiveBytes([(PATH, b'content', 3)],
            change=lambda row: row.__setitem__(field, value)), error='directory_invalid', openError=True)
    valid = archiveBytes([(PATH, b'content', 3)])
    for name, data in [('empty', b''), ('header', HEADER[:100]), ('garbage', HEADER + b'garbage'),
                       ('truncated-directory', valid[:-1]), ('bad-gbk', valid.replace('测试'.encode('gbk'), b'\xff' * 4))]:
        add(name, data, error='directory_invalid', openError=True)
    for name, path in [('duplicate', '../data/scene/测试.BIN'), ('traversal', '../data/scene/../测试.bin')]:
        add(name, archiveBytes([(PATH, b'a', 1), (path, b'b', 1)]), error='directory_invalid', openError=True)
    add('missing', valid, path='data/missing', error='entry_missing')
    corrupt = bytearray(valid)
    corrupt[2048] = 0
    add('corrupt-zlib', corrupt, error='entry_decode')
    raw = b'fixture' * 100
    packed = zlib.compress(raw[:300]) + zlib.compress(raw[300:])
    name = PATH.encode('gbk')
    record = encodeRecord([3, 2048, len(packed), len(raw), 0, 0, 0, 1, 0, len(name), 0])
    add('actual-chunk-count', HEADER + packed + name + b'\0' + record, error='entry_decode')
    add('entry-size', archiveBytes([(PATH, b'content', 3)], change=lambda row:
        (row.__setitem__(3, 17 * 1024 * 1024), row.__setitem__(7, 1088))), error='entry_size')
    return cases


def catalogueFixture():
    tables = list(resourceTables())
    hero = tables[0][140:140 + 7286]
    second = bytearray(hero)
    struct.pack_into('<I', second, 0, 2102)
    second[34] = 2
    tables[0] = table(7286, [second, hero, tables[0][140 + 7286:]])
    unlock = bytearray(tables[1][140:])
    struct.pack_into('<I', unlock, 42, 2102)
    tables[1] = table(300, [unlock, tables[1][140:]])
    groups = bytearray(tables[2])
    struct.pack_into('<I', groups, 140 + 66 + 228, 799)
    struct.pack_into('<I', groups, 140 + 66 + 456, 703)
    tables[2] = bytes(groups)
    weaponRows = [bytearray(tables[3][i:i + 2829]) for i in range(140, len(tables[3]), 2829)]
    weaponRows[0][16:22] = '单手剑'.encode('gbk')
    weaponRows[1][16:20] = '空手'.encode('gbk')
    categoryRows = [tables[4][i:i + 550] for i in range(140, len(tables[4]), 550)]
    for tid, category, family, name in [(703, 17, 12, '盾'), (799, 77, 7, '弓')]:
        weapon = bytearray(2829)
        struct.pack_into('<I', weapon, 0, tid)
        struct.pack_into('<H', weapon, 56, category)
        weapon[16:16 + len(name.encode('gbk'))] = name.encode('gbk')
        weaponRows.append(weapon)
        spec = bytearray(550)
        struct.pack_into('<HH', spec, 0, category, family)
        struct.pack_into('<H', spec, 46, 1)
        categoryRows.append(spec)
    tables[3], tables[4] = table(2829, weaponRows), table(550, categoryRows)
    parsed = hero_resources.parseCatalogue(*tables)
    return dict(archiveHex=archiveBytes([('../' + path, data, 3) for path, data in zip(TABLE_PATHS, tables)]).hex(),
        expected=dict(heroes=[asdict(hero) for hero in parsed.heroes.values()],
                      weapons=[asdict(parsed.weapons[tid]) for tid in sorted(parsed.weapons)]))


def gbkCorpus():
    lines = ['# Standard GBK: leads 81-FE, trails 40-FE; U+FFFD marks a rejected byte pair.']
    for lead in range(0x81, 0xff):
        values = []
        for trail in range(0x40, 0xff):
            try:
                values.append(bytes([lead, trail]).decode('gbk'))
            except UnicodeDecodeError:
                values.append('\ufffd')
        lines.append(f'{lead:02x}|' + ''.join(values))
    return '\n'.join(lines) + '\n'


def test_shared_vectors_match_the_original_readers(tmp_path):
    expected = json.loads((FIXTURES / 'vfs-v1.json').read_text(encoding='utf-8'))
    assert expected == vfsCases()
    for case in expected:
        archive = tmp_path / 'fixture.vfs'
        archive.write_bytes(bytes.fromhex(case['archiveHex']))
        vfs_resource.clearCache()
        if case['error']:
            with pytest.raises(vfs_resource.ResourceError) as error:
                vfs_resource.readEntry(archive, case['path'])
            assert error.value.code == case['error'], case['name']
        else:
            assert vfs_resource.readEntry(archive, case['path']) == bytes.fromhex(case['unitHex']) * case['repeat']
    vfs_resource.clearCache()


def test_catalogue_vector_matches_original_tables(tmp_path):
    value = json.loads((FIXTURES / 'catalogue-v1.json').read_text(encoding='utf-8'))
    assert value == json.loads(json.dumps(catalogueFixture()))
    archive = tmp_path / 'data1.vfs'
    archive.write_bytes(bytes.fromhex(value['archiveHex']))
    vfs_resource.clearCache()
    parsed = hero_resources.parseCatalogue(*(vfs_resource.readEntry(archive, path) for path in TABLE_PATHS))
    assert tuple(parsed.heroes) == (1101, 2102)
    assert parsed.heroes[1101].nativeGroups[0][:3] == (701, 799, 703)
    assert parsed.heroes[1101].groups[0][:3] == (701, 0, 703)
    vfs_resource.clearCache()


def test_gbk_vector_matches_strict_python_codec():
    assert (FIXTURES / 'gbk-v1.txt').read_text(encoding='utf-8') == gbkCorpus()


def mapFixture():
    import xml.etree.ElementTree as ET
    names = nameTable([('npc', f'测试电脑{i:02}') for i in range(30)] + [('other', 'ignored'), ('npc', '测试电脑00')])
    spawns = ET.tostring(spawnXml(), encoding='gbk')
    navigation = syntheticTagfile()
    parsed = navmesh_resource.TagReader(navigation).document()
    return dict(namesArchiveHex=archiveBytes([(NAME_PATH, names, 3)]).hex(),
        mapArchiveHex=archiveBytes([(SPAWN_PATH, spawns, 3), (NAV_PATH, navigation, 3)]).hex(),
        names=bot_names.parseNames(names), spawns=spawn_resource.parseSpawns(spawns),
        respawns=spawn_resource.parseRespawns(spawns), navigation=dict(
            vertices=[row[:3] for row in parsed['vertices']],
            edges=[[row[field] for field in navmesh_resource.EDGE_FIELDS] for row in parsed['edges']],
            faces=[[row[field] for field in navmesh_resource.FACE_FIELDS] for row in parsed['faces']]))


def test_map_vectors_match_original_parsers():
    expected = json.loads((FIXTURES / 'map-input-v1.json').read_text(encoding='utf-8'))
    assert expected == json.loads(json.dumps(mapFixture()))
    assert (FIXTURES / 'navigation-v1.bin').read_bytes() == syntheticTagfile()


def navigationCases():
    from Business.scripts.navigation import NavMesh
    from copy import deepcopy
    source = navmesh_resource.TagReader(syntheticTagfile()).document()

    def encode(resource):
        def string(value):
            return vint(len(value)) + value.encode('ascii')
        def schema(name, version, parent, fields):
            data = vint(2) + string(name) + vint(version) + vint(parent) + vint(len(fields))
            return data + b''.join(string(name) + vint(kind) + (string(ref) if ref else b'') for name, kind, ref in fields)
        def integers(values):
            return vint(4) + b''.join(map(vint, values))
        data = navmesh_resource.MAGIC + vint(1) + vint(5) + string('hk_2014.2.5-r1') + vint(-10) + vint(0) * 3
        data += schema('Base', 0, 0, [('referenceCount', 0, None)])
        data += schema('hkaiNavMesh', 14, 1, [('faces', 25, 'Face'), ('edges', 25, 'Edge'), ('vertices', 20, None), ('streamingSets', 18, None)])
        data += schema('Face', 0, 0, [(field, 2, None) for field in navmesh_resource.FACE_FIELDS])
        data += schema('Edge', 0, 0, [(field, 2, None) for field in navmesh_resource.EDGE_FIELDS])
        data += vint(4) + vint(2) + b'\x1e'
        for key, fields in [('faces', navmesh_resource.FACE_FIELDS), ('edges', navmesh_resource.EDGE_FIELDS)]:
            rows = resource[key]
            data += vint(len(rows))
            if rows:
                data += bytes([(1 << len(fields)) - 1])
                for field in fields:
                    data += integers([row[field] for row in rows])
        data += vint(len(resource['vertices']))
        if resource['vertices']:
            data += vint(4) + b''.join(struct.pack('<4f', *row) for row in resource['vertices'])
        data += vint(len(resource['streamingSets']))
        if resource['streamingSets']:
            data += integers(resource['streamingSets'])
        return data + vint(7)

    values = [('original', source)]
    for field, value in [('oppositeFace', 99), ('oppositeEdge', 6), ('b', 99), ('b', 3)]:
        resource = deepcopy(source)
        resource['edges'][1][field] = value
        values.append((field + str(value), resource))
    for key in ('faces', 'edges', 'vertices'):
        resource = deepcopy(source)
        resource[key] = []
        values.append(('empty-' + key, resource))
    resource = deepcopy(source)
    resource['streamingSets'] = [0]
    values.append(('streaming', resource))
    resource = deepcopy(source)
    resource['faces'][1]['startEdgeIndex'] = 0
    values.append(('edge-multiple-owners', resource))
    resource = deepcopy(source)
    resource['vertices'] = [tuple(value * 1000 for value in row) for row in resource['vertices']]
    values.append(('face-extent', resource))
    resource = deepcopy(source)
    resource['vertices'].extend([(4., 0., 2., 0.), (4., 4., 2., 0.)])
    resource['edges'][4]['a'] = 6
    resource['edges'][6]['b'] = 7
    resource['edges'][7]['a'], resource['edges'][7]['b'] = 7, 6
    values.append(('height-seam-disconnected', resource))
    result = []
    for name, resource in values:
        raw = encode(resource)
        parsed = navmesh_resource.TagReader(raw).document()
        try:
            nav = NavMesh.fromResource(parsed)
            expected = dict(vertices=[row[:3] for row in parsed['vertices']],
                edges=[[row[field] for field in navmesh_resource.EDGE_FIELDS] for row in parsed['edges']],
                faces=[[row[field] for field in navmesh_resource.FACE_FIELDS] for row in parsed['faces']])
            if name == 'height-seam-disconnected':
                assert nav.links == ((), ())
        except ValueError:
            expected = None
        result.append(dict(name=name, dataHex=raw.hex(), expected=expected))
    return result


def test_navigation_vectors_match_original_topology_validation():
    expected = json.loads((FIXTURES / 'navigation-input-v1.json').read_text(encoding='utf-8'))
    assert expected == json.loads(json.dumps(navigationCases()))


def clientCacheFixture():
    tables = [bytearray(value) for value in resourceTables()]
    tables[0][144:174] = b'Fixture' + bytes(23)
    struct.pack_into('<I', tables[0], 140 + 4215, 100000)
    struct.pack_into('<I', tables[2], 140 + 66, 100000)
    struct.pack_into('<H', tables[4], 140 + 550 + 46, 3)
    names = nameTable([('npc', f'Fixture{i:02}') for i in range(30)])
    entries = [('../' + path, bytes(value), 3) for path, value in zip(TABLE_PATHS, tables)]
    entries.append((NAME_PATH, names, 1))
    return archiveBytes(entries)


def test_native_cache_client_fixture_uses_only_synthetic_tables(tmp_path):
    expected = (FIXTURES / 'client-cache-v1.vfs').read_bytes()
    assert expected == clientCacheFixture()
    archive = tmp_path / 'data1.vfs'
    archive.write_bytes(expected)
    vfs_resource.clearCache()
    parsed = hero_resources.parseCatalogue(*(vfs_resource.readEntry(archive, path) for path in TABLE_PATHS))
    assert tuple(parsed.heroes) == (1101,)
    assert parsed.heroes[1101].weapon(0, 0) == 100000
    assert parsed.weapons[100000].hand == 3
    assert len(bot_names.parseNames(vfs_resource.readEntry(archive, NAME_PATH))) == 30
    vfs_resource.clearCache()
