"""Account-owned titles: game requests remain pending until the launcher confirms."""
import json
import struct
from .codec import title_flow as wire


def empty():
    return dict(epoch=0, owned=[], equipped=0)


def valid(state):
    title = state.get('titles', empty())
    return (type(title) is dict and type(title.get('epoch')) is int and title['epoch'] >= 0
            and type(title.get('owned')) is list and len(title['owned']) <= 64
            and all(type(tid) is int and 0 < tid <= 0x7fffffff for tid in title['owned'])
            and len(set(title['owned'])) == len(title['owned'])
            and type(title.get('equipped')) is int and (title['equipped'] == 0 or title['equipped'] in title['owned']))


def publish(flow):
    if 'titles' not in flow.state:
        return
    title = flow.state.get('titles', empty())
    for connection, session in flow.state['sessions'].items():
        if session['role'] == 'logic' and session.get('authenticated'):
            for body in (wire.userInfo(flow.playerName, title['equipped']), wire.statInfo(title['owned'], title['equipped'])):
                flow.result['send'].append(dict(connection=int(connection), command=wire.COMMAND, body=body, reason='account-title-sync'))


def sync(flow, event):
    previous = flow.state.get('titles', empty())
    epoch = event['sequence']
    if epoch < previous['epoch']:
        return
    value = json.loads(event['body'].decode('utf-8'))
    items = value['titles']
    if type(items) is not list or len(items) > 64:
        raise ValueError('invalid title catalogue')
    current = dict(epoch=epoch, owned=[t['titleId'] for t in items if t['owned'] and t['enabled']],
                   equipped=value['equippedTitleId'] or 0)
    if not valid({'titles': current}):
        raise ValueError('invalid account title state')
    pending = flow.state.get('titlePending')
    if pending and (pending['epoch'] != epoch or event.get('serverTimeMs') == pending['requestId']):
        if str(pending['connection']) in flow.state['sessions']:
            flow.result['send'].append(dict(connection=pending['connection'], command=wire.COMMAND,
                body=wire.setResult(pending['epoch'] == epoch and event.get('command') == 0), reason='account-title-result'))
        flow.state.pop('titlePending')
    flow.state['titles'] = current
    publish(flow)


def message(flow, command, selector, body, event):
    if command != wire.COMMAND or selector not in (1, 3, 5):
        return False
    if len(body) != 6:
        raise ValueError('USER_INFO request requires exactly six bytes')
    value = struct.unpack_from('>I', body, 2)[0]
    title = flow.state.get('titles', empty())
    if selector in (1, 3):
        if value != wire.USER_ID:
            flow.send(command, struct.pack('>HiI', selector + 1, 1, value)
                      + (struct.pack('>I', 1) + b'\0' + wire.basicInfo() if selector == 1 else wire.statInfo([])[10:]), 'unknown-title-user')
        else:
            flow.send(command, wire.userInfo(flow.playerName, title['equipped']) if selector == 1
                      else wire.statInfo(title['owned'], title['equipped']), 'account-title-query')
        return True
    if title['epoch'] == 0 or (value and value not in title['owned']) or 'titlePending' in flow.state:
        flow.send(command, wire.setResult(False), 'account-title-unavailable')
        return True
    request = dict(requestId=event['eventId'], epoch=title['epoch'], titleId=value, connection=flow.connection)
    flow.state['titlePending'] = request
    flow.result['titleRequest'] = request.copy()
    flow.later('title-request-timeout', 10000)
    return True


def timeout(flow):
    pending = flow.state.get('titlePending')
    if pending and pending['connection'] == flow.connection:
        flow.state.pop('titlePending')
        flow.send(wire.COMMAND, wire.setResult(False), 'account-title-timeout')
