"""Opt-in synthetic 20-actor bridge benchmark; never starts a client or server."""
import argparse
from copy import deepcopy
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
from time import perf_counter_ns
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests/reference/Business/vendor')]

import pytest
from Business.runtime import host_runtime as host
from Business.scripts import app
from conftest import respawnPoints, spawnPoints
from test_battle_actions import actionBody
from test_bot_roster import multi, sample


def fixture(patch):
    spawns = spawnPoints.__wrapped__(patch, respawnPoints.__wrapped__(patch))
    flow = multi.__wrapped__(spawns, patch)
    world = sample(flow)
    probe = dict(ready=True, hit=False, material=-1, position=[0., 0., 0.],
                 normal=[0., 0., 1.], direction=[1., 0., 0.])
    for rid, actor in world['actors'].items():
        actor.update(entity=100 + int(rid), state=2, seq=1, phaseAt=flow.now,
                     terrain={key: deepcopy(probe) for key in ('forward', 'target')},
                     targetTerrain={target: deepcopy(probe) for target in world['actors'] if target != rid})
    world.update(player=world['actors']['1']['position'], bot=world['actors']['2']['position'],
                 playerHeading=world['actors']['1']['heading'], botHeading=world['actors']['2']['heading'],
                 blades={rid: dict(ready=False, spanMs=0, length=0., base=[0., 0., 0.], tip=[0., 0., 0.], contacts=[])
                         for rid in world['actors']},
                 contactJournal=dict(damageEvidenceVersion=2, throughAt=flow.now, batchId=1, dropped=0, frames=0, events=[]))
    return (host.encode(flow.state), dict(type='message', connection=1, command=4, body=actionBody(300610)),
            dict(flow.context, connections=[1]))


def runtime(module):
    value = object.__new__(module.Runtime)
    value.active = SimpleNamespace(module=app)
    return value


def measure(operation, warmup, samples):
    for _ in range(warmup):
        operation()
    durations = []
    for _ in range(samples):
        started = perf_counter_ns()
        operation()
        durations.append((perf_counter_ns() - started) / 1e6)
    values = sorted(durations)
    return dict(p50Ms=statistics.median(values), p95Ms=values[math.ceil(.95 * len(values)) - 1], maxMs=max(values))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-ref', help='Read the baseline host_runtime.py from this local Git revision')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--warmup', type=int, default=100)
    parser.add_argument('--samples', type=int, default=1000)
    parser.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args()
    if args.warmup < 0 or args.samples < 1 or args.repeats < 1:
        parser.error('warmup must be nonnegative; samples and repeats must be positive')
    baseline = host
    if args.baseline_ref:
        source = subprocess.check_output(['git', 'show', f'{args.baseline_ref}:tests/reference/Business/runtime/host_runtime.py'],
                                         cwd=ROOT, text=True, encoding='utf-8')
        baseline = ModuleType('baseline_host_runtime')
        baseline.__file__ = host.__file__
        exec(compile(source, 'baseline_host_runtime.py', 'exec'), baseline.__dict__)
    with pytest.MonkeyPatch.context() as patch:
        state, event, context = fixture(patch)
        old, new = runtime(baseline), runtime(host)

        def serialized():
            result = old.dispatch(baseline.encode(event), state, baseline.encode(context))
            old.phase(result[0])
            return result

        def native():
            return new.dispatchNative(dict(event), state, dict(context))

        before = deepcopy(context)
        assert serialized() == native()[:4]
        runs = []
        for index in range(args.repeats):
            operations = [('serialized', serialized), ('native', native)]
            if index % 2:
                operations.reverse()
            runs.append({name: measure(operation, args.warmup, args.samples) for name, operation in operations})
        assert context == before
        report = dict(actors=20, stateBytes=len(state), contextBytes=len(host.encode(context)),
                      python=sys.version.split()[0], baseline=args.baseline_ref or 'current-serialized-entry',
                      warmup=args.warmup, samples=args.samples, repeats=args.repeats, runs=runs,
                      note='Synthetic Python bridge only; excludes native conversion, queues, I/O and rendering.')
        output = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output + '\n', encoding='utf-8')
        print(output)


if __name__ == '__main__':
    main()
