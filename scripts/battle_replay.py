"""Freeze and verify synthetic Python oracle traces before migrating a subsystem."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests/reference'), str(ROOT / 'tests/reference/Business/vendor'), str(ROOT / 'tests/python')]

from battle_replay import SCHEMA_VERSION, runCase
from replay_fixtures import cases, resources

SOURCE_PATHS = ('tests/reference/Business/runtime', 'tests/reference/Business/scripts', 'tests/reference/Business/vendor',
                'tests/python/battle_replay.py', 'tests/python/replay_fixtures.py',
                'tests/python/loadout_fixture.py', 'scripts/battle_replay.py')


def readJson(path):
    return json.loads(path.read_text(encoding='utf-8'))


def writeJson(path, value):
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write('\n')


def sourceFiles(root):
    for relative in SOURCE_PATHS:
        path = root / relative
        files = sorted(path.rglob('*')) if path.is_dir() else [path]
        for item in files:
            if item.is_file() and '__pycache__' not in item.parts and item.suffix not in ('.pyc', '.pyo'):
                yield item.relative_to(root).as_posix(), item


def sourceManifest(root):
    return {relative: hashlib.sha256(path.read_bytes()).hexdigest() for relative, path in sourceFiles(root)}


def record(output):
    output = Path(output).resolve()
    # A baseline is immutable. Record a new directory instead of replacing evidence.
    output.mkdir(parents=True, exist_ok=False)
    before = sourceManifest(ROOT)
    with resources():
        inputs = cases()
        results = [runCase(case) for case in inputs]
    if any(step['status'] != 'committed' for result in results for step in result['steps']):
        raise ValueError('baseline contains an unexpected rejected transition')
    snapshot = output / 'snapshot'
    for relative, source in sourceFiles(ROOT):
        target = snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    if before != sourceManifest(ROOT) or before != sourceManifest(snapshot):
        raise ValueError('sources changed while recording; discard this incomplete baseline')
    revision = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, check=True,
                              capture_output=True, text=True).stdout.strip()
    writeJson(output / 'cases.json', inputs)
    writeJson(output / 'expected.json', [{key: value for key, value in result.items() if key != 'timings'}
                                       for result in results])
    writeJson(output / 'timings.json', [dict(name=result['name'], timings=result['timings']) for result in results])
    manifest = dict(schemaVersion=SCHEMA_VERSION, pythonVersion=platform.python_version(),
                    sourceRevision=revision, sources=before, cases=len(inputs),
                    events=sum(len(case['steps']) for case in inputs), synthetic=True,
                    scope='offline-business-only; excludes native conversion, queues, sockets and rendering')
    writeJson(output / 'manifest.json', manifest)
    return manifest


def firstDifference(expected, actual, path='$'):
    if type(expected) is not type(actual):
        return path + ': type mismatch'
    if isinstance(expected, dict):
        if expected.keys() != actual.keys():
            return path + ': field mismatch'
        for key in expected:
            difference = firstDifference(expected[key], actual[key], f'{path}.{key}')
            if difference:
                return difference
    elif isinstance(expected, list):
        if len(expected) != len(actual):
            return path + ': length mismatch'
        for index, (left, right) in enumerate(zip(expected, actual)):
            difference = firstDifference(left, right, f'{path}[{index}]')
            if difference:
                return difference
    elif expected != actual:
        return path + ': value mismatch'
    return None


def verify(directory):
    directory = Path(directory).resolve()
    manifest = readJson(directory / 'manifest.json')
    if manifest['schemaVersion'] != SCHEMA_VERSION:
        raise ValueError('unsupported baseline schema')
    if manifest['pythonVersion'] != platform.python_version():
        raise ValueError('baseline Python version differs')
    if sourceManifest(ROOT) != manifest['sources'] or sourceManifest(directory / 'snapshot') != manifest['sources']:
        raise ValueError('baseline source mismatch; run snapshot/scripts/battle_replay.py for the frozen oracle')
    inputs, expected = readJson(directory / 'cases.json'), readJson(directory / 'expected.json')
    if len(inputs) != manifest['cases'] or sum(len(case['steps']) for case in inputs) != manifest['events']:
        raise ValueError('baseline case/event count mismatch')
    with resources():
        actual = [{key: value for key, value in runCase(case).items() if key != 'timings'} for case in inputs]
    difference = firstDifference(expected, actual)
    if difference:
        raise ValueError('replay divergence at ' + difference)
    return dict(cases=manifest['cases'], events=manifest['events'], matched=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('record').add_argument('--output', type=Path, required=True)
    commands.add_parser('verify').add_argument('--input', type=Path, required=True)
    args = parser.parse_args()
    result = record(args.output) if args.command == 'record' else verify(args.input)
    print(json.dumps({key: value for key, value in result.items() if key != 'sources'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
