import json
import struct
from copy import deepcopy

import pytest
from Business.scripts import app, titles
from Business.scripts.codec import title_flow


def fixture():
    state = app.createState({})
    state['sessions']['1'] = dict(role='logic', hydration=[], pending={}, authenticated=True)
    return state, dict(nowMs=0, connections=[1])


def snapshot(epoch=1, equipped=1008, owned=True):
    return dict(type='titles', connection=0, sequence=epoch, serverTimeMs=0, command=0,
                body=json.dumps(dict(titles=[dict(titleId=1008, name='内测精英', description='测试纪念',
                    rule='获取：2026年10月功能上线后登录；永久。', enabled=True, owned=owned)],
                    equippedTitleId=equipped)).encode())


def test_verified_wire_layout_and_empty_arrays():
    basic = title_flow.basicInfo(1008)
    assert len(basic) == 296 and struct.unpack_from('>i', basic, 64)[0] == 1008
    body = title_flow.statInfo([1007, 1008], 1008)
    assert struct.unpack_from('>HiI', body) == (4, 0, 10000)
    assert struct.unpack_from('>iii', body, 10 + 296 + 4460) == (2, 1007, 1008)
    assert len(body) == 10 + 296 + 4554 + 8
    assert title_flow.setResult(False) == struct.pack('>Hi', 6, 570005)


def test_game_selection_waits_for_server_and_matching_response():
    state, context = fixture()
    result = app.handleEvent(snapshot(), state, context)
    request = dict(type='message', connection=1, eventId=22, command=31, body=struct.pack('>HI', 5, 0))
    pending = app.handleEvent(request, result['state'], context)
    assert pending['titleRequest'] == dict(requestId=22, epoch=1, titleId=0, connection=1)
    assert pending['send'] == []
    assert pending['state']['titles']['equipped'] == 1008
    response = snapshot(equipped=None)
    response['serverTimeMs'] = 22
    done = app.handleEvent(response, pending['state'], context)
    assert done['state']['titles']['equipped'] == 0
    assert any(x['body'] == struct.pack('>Hi', 6, 0) for x in done['send'])


def test_offline_epoch_and_rejected_request_do_not_change_possession():
    state, context = fixture()
    state = app.handleEvent(snapshot(), state, context)['state']
    old = deepcopy(state)
    request = dict(type='message', connection=1, eventId=20, command=31, body=struct.pack('>HI', 5, 1007))
    result = app.handleEvent(request, state, context)
    assert 'titleRequest' not in result
    assert result['send'][-1]['body'] == title_flow.setResult(False)
    state = app.handleEvent(snapshot(2, None, False), state, context)['state']
    assert state['titles']['owned'] == [] and state['titles']['equipped'] == 0
    assert app.handleEvent(snapshot(1), state, context)['state'] == state
    assert old['titles']['equipped'] == 1008


def test_title_protocol_queries_and_bad_payloads():
    state, context = fixture()
    for selector in (1, 3):
        result = app.handleEvent(dict(type='message', connection=1, command=31,
            body=struct.pack('>HI', selector, 10000)), state, context)
        assert result['send'][0]['body'][:2] == struct.pack('>H', selector + 1)
    with pytest.raises(ValueError):
        app.handleEvent(dict(type='message', connection=1, command=31, body=b'\0\5'), state, context)


def test_pending_selection_rejects_duplicates_times_out_and_ignores_stale_completion():
    state, context = fixture()
    state = app.handleEvent(snapshot(), state, context)['state']
    request = dict(type='message', connection=1, eventId=22, command=31, body=struct.pack('>HI', 5, 0))
    pending = app.handleEvent(request, state, context)
    assert 'titleRequest' not in app.handleEvent(request, pending['state'], context)
    timer = pending['timers'][0]['event']
    assert app.handleEvent(timer, pending['state'], dict(context, nowMs=9999))['send'] == []
    expired = app.handleEvent(timer, pending['state'], dict(context, nowMs=10000))
    assert 'titlePending' not in expired['state']
    assert expired['send'][0]['body'] == title_flow.setResult(False)
    assert expired['state']['titles']['equipped'] == 1008
    offline = app.handleEvent(snapshot(2, None, False), expired['state'], context)
    late = snapshot(1, None); late['serverTimeMs'] = 22
    assert app.handleEvent(late, offline['state'], context)['state'] == offline['state']


def test_rejected_selection_and_new_epoch_cancel_pending_without_false_success():
    for changeEpoch in (False, True):
        state, context = fixture()
        state = app.handleEvent(snapshot(), state, context)['state']
        state = app.handleEvent(dict(type='message', connection=1, eventId=22, command=31,
            body=struct.pack('>HI', 5, 0)), state, context)['state']
        response = snapshot(epoch=2 if changeEpoch else 1)
        response.update(serverTimeMs=22, command=1)
        result = app.handleEvent(response, state, context)
        assert result['send'][0]['body'] == title_flow.setResult(False)
        assert 'titlePending' not in result['state']
        assert result['state']['titles']['equipped'] == 1008


def test_invalid_catalogue_and_unowned_selection_are_rejected():
    state, context = fixture()
    invalid = snapshot(equipped=1007)
    with pytest.raises(ValueError):
        app.handleEvent(invalid, state, context)
    assert 'titles' not in state
    assert not titles.valid({'titles': dict(epoch=True, owned=[], equipped=0)})
    assert not titles.valid({'titles': dict(epoch=1, owned=[1008, 1008], equipped=1008)})


def test_custom_title_uses_original_wire_and_preserves_server_confirmed_ownership():
    state, context = fixture()
    event = snapshot(equipped=1000000)
    event['body'] = json.dumps(dict(titles=[dict(titleId=1000000, name='重燃共建者',
        enabled=True, owned=True)], equippedTitleId=1000000)).encode()
    synced = app.handleEvent(event, state, context)
    assert synced['state']['titles']['owned'] == [1000000]
    stats = next(item['body'] for item in synced['send'] if item['body'][:2] == b'\0\4')
    assert struct.unpack_from('>i', stats, 10 + 64)[0] == 1000000
    assert struct.unpack_from('>ii', stats, 10 + 296 + 4460) == (1, 1000000)
    request = dict(type='message', connection=1, eventId=30, command=31, body=struct.pack('>HI', 5, 1000000))
    pending = app.handleEvent(request, synced['state'], context)
    assert pending['titleRequest']['titleId'] == 1000000 and pending['send'] == []
    revoked = dict(event, serverTimeMs=30, command=1,
        body=json.dumps(dict(titles=[dict(titleId=1000000, enabled=False, owned=True)], equippedTitleId=None)).encode())
    result = app.handleEvent(revoked, pending['state'], context)
    assert result['state']['titles']['owned'] == [] and result['state']['titles']['equipped'] == 0
    assert result['send'][0]['body'] == title_flow.setResult(False)
    assert 'titleRequest' not in app.handleEvent(request, result['state'], context)
