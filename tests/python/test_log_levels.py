import sys
from pathlib import Path

import pytest

from Business.scripts import app


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests/reference/Business/runtime'))
import host_runtime  # noqa: E402


@pytest.mark.parametrize('level', ['DEBUG', 'INFO', 'WARN', 'ERROR'])
def test_structured_business_log(level):
    result = dict(state={}, send=[], timers=[], logs=[{'level': level, 'message': 'fixture'}])
    host_runtime.validateTransition(result, {})


@pytest.mark.parametrize('log', [
    {'level': 'TRACE', 'message': 'fixture'},
    {'level': 'WARNING', 'message': 'fixture'},
    {'level': 1, 'message': 'fixture'},
    {'level': 'INFO', 'message': None},
    {'level': 'INFO', 'message': 'x' * 4097},
    {'level': 'INFO'},
    {'level': 'INFO', 'message': 'fixture', 'extra': True},
    None, 'x' * 4097,
])
def test_invalid_business_log(log):
    with pytest.raises(ValueError, match='invalid business log'):
        host_runtime.validateTransition(dict(state={}, send=[], timers=[], logs=[log]), {})


def test_legacy_business_log():
    host_runtime.validateTransition(dict(state={}, send=[], timers=[], logs=['legacy']), {})


@pytest.mark.parametrize('entry, expected', [
    ('legacy', {'level': 'INFO', 'message': 'legacy'}),
    ({'level': 'WARN', 'message': 'fixture'}, {'level': 'WARN', 'message': 'fixture'}),
])
def test_dispatch_normalizes_business_logs(tmp_path, monkeypatch, entry, expected):
    runtime = host_runtime.Runtime(ROOT / 'tests/reference/Business/scripts', tmp_path / 'revisions')
    try:
        state = runtime.create(host_runtime.encode({}))
        monkeypatch.setattr(runtime.active.module, 'handleEvent', lambda event, value, context:
                            dict(state=value, send=[], timers=[], logs=[entry]))
        result = runtime.dispatch(host_runtime.encode({}), state, host_runtime.encode({}))
        assert result[3] == [expected]
    finally:
        runtime.close()


def test_flow_records_explicit_level():
    flow = app.Flow({'connection': 1}, app.createState({}), {'nowMs': 0})
    flow.log('DEBUG', 'fixture diagnostic')
    flow.log('INFO', 'fixture lifecycle')
    assert flow.result['logs'] == [
        {'level': 'DEBUG', 'message': 'fixture diagnostic'},
        {'level': 'INFO', 'message': 'fixture lifecycle'},
    ]
