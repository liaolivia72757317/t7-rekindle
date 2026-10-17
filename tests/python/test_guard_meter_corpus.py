"""Frozen per-life guard transitions from the existing Python implementation."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

from Business.scripts import guard_state

CORPUS = Path(__file__).resolve().parents[1] / 'fixtures/rules/guard-meter-v1.json'


def vectors():
    cases = [
        ('exhaustion', [('consume', 1000 + i * 100, 1) for i in range(5)]),
        ('watermark', [('consume', 1000, 2), ('advance', 3499), ('advance', 3750),
                       ('consume', 3750, 1), ('advance', 6250), ('advance', 7000), ('advance', 9000)]),
        ('late-contact', [('consume', 4000, 2), ('advance', 7000), ('consume', 4100, 1),
                         ('advance', 6999), ('advance', 7250), ('restore', 5000)]),
        ('fractional', [('consume', 1000.25, 2.5), ('advance', 3625.75),
                        ('consume', 3625.75, 20), ('restore', 3625.75), ('restore', 3625.75)]),
        ('epsilon', [('consume', 1, 4.9999999995), ('consume', 2, 0), ('reset', 0), ('advance', 0)]),
    ]
    result = []
    for name, operations in cases:
        flow = SimpleNamespace(session={}, send=lambda *args: None)
        guard_state.reset(flow.session)
        events = []
        for kind, at, *args in operations:
            exhausted = False
            previous = flow.session['guardMeter']['value']
            if kind == 'consume':
                exhausted = guard_state.consume(flow, 1, args[0], at)
            elif kind == 'restore':
                guard_state.restore(flow, 1, at)
            elif kind == 'reset':
                guard_state.reset(flow.session)
            else:
                guard_state.advance(flow.session, at)
            meter = deepcopy(flow.session['guardMeter'])
            events.append(dict(kind=kind, at=at, amount=args[0] if args else 0,
                               meter=meter, changed=meter['value'] != previous, exhausted=exhausted))
        result.append(dict(name=name, events=events))
    return dict(schemaVersion=1, cases=result)


def test_guard_meter_corpus_matches_reference():
    assert json.loads(CORPUS.read_text(encoding='utf-8')) == vectors()
