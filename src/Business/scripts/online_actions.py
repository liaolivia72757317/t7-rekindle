"""Translate melee intentions and Rust action events for the frozen scene."""
from . import online_limits
import struct

from . import controls
from .codec import battle_flow

INTENTS = {
    300560: 'attackUp', 300550: 'attackRight', 300570: 'attackDown', 300540: 'attackLeft',
    300580: 'blockUp', 300590: 'blockRight', 300600: 'blockDown', 300610: 'blockLeft',
    300020: 'attackRelease', 300620: 'blockRelease',
}
STATES = frozenset((2, 235, 237, 238, 239, 241, 243, 244, 245, 247, 249, 250, 251,
                    253, 255, 256, 257, 305, 223, 227, 308, 224, 228, 307, 225, 229, 306, 226, 230, 35, 42, 240, 246, 252, 258, 231, 232, 233, 234, 466, 468, 467, 469))
REACTION_STATES = frozenset((35, 42, 240, 246, 252, 258, 231, 232, 233, 234, 466, 468, 467, 469))


def valid(session):
    value = session.get('onlineActions')
    if value is None:
        return True
    return (session['role'] == 'instance' and type(value) is dict
            and type(value.get('inputSeq')) is int and 0 <= value['inputSeq'] <= 0xffffffff
            and type(value.get('eventSeq')) is int and 0 <= value['eventSeq'] <= 0xffffffffffffffff
            and type(value.get('actors')) is dict and len(value['actors']) <= online_limits.ROOM_CAPACITY
            and all(key in online_limits.ALL_RIDS and type(seq) is int and 1 <= seq <= 0xffff
                    for key, seq in value['actors'].items())
            and type(value.get('reacting', [])) is list
            and len(value.get('reacting', [])) <= online_limits.ROOM_CAPACITY
            and all(type(key) is str and key in online_limits.ALL_RIDS for key in value.get('reacting', []))
            and len(set(value.get('reacting', []))) == len(value.get('reacting', [])))


def ledger(session):
    return session.setdefault('onlineActions', {'inputSeq': 0, 'eventSeq': 0, 'actors': {}})


def message(flow, command, selector, body):
    if command != 4 or selector != 1 or flow.session['role'] != 'instance':
        return False
    if len(body) != 11:
        raise ValueError('online action requires an eleven-byte body')
    _, intent, auto_parry = struct.unpack_from('>Iib', body, 2)
    if auto_parry not in (0, 1):
        raise ValueError('invalid online auto-parry flag')
    intent = INTENTS.get(intent)
    if intent is None:
        return False
    match = flow.state['onlineScene']
    if match['phase'] != 3 or not controls.groundEnabled(flow) or flow.session.get('leaving'):
        return True
    from . import custom_lobby
    state = ledger(flow.session)
    if state['inputSeq'] == 0xffffffff:
        raise ValueError('online action input sequence exhausted')
    state['inputSeq'] += 1
    connection, session = flow.connection, flow.session
    gateways = [(int(key), value) for key, value in flow.state['sessions'].items()
                if value.get('customLobby', {}).get('identified')
                and int(key) in flow.context.get('connections', [])]
    if len(gateways) != 1:
        raise ValueError('online action requires the identified logic gateway')
    try:
        flow.connection, flow.session = gateways[0]
        custom_lobby.send(flow, 'action', {'type': 'actionInput', 'payload': {
            'matchId': match['matchId'], 'inputSeq': state['inputSeq'], 'intent': intent}},
            matchId=match['matchId'], inputSeq=state['inputSeq'])
        flow.log('INFO', f'online-input input={state["inputSeq"]} intent={intent}')
    finally:
        flow.connection, flow.session = connection, session
    return True


def receive(flow, value):
    from .custom_lobby import integer
    match = flow.state.get('onlineScene')
    room = flow.session['customLobby'].get('room')
    if (match is None or value['matchId'] != match['matchId']
            or room is not None and value['roomId'] != room['roomId']):
        raise ValueError('online action belongs to another scene')
    if match['phase'] == 4:
        return
    if match['phase'] != 3 or room is None:
        raise ValueError('online action precedes Running')
    actor = integer(value['actorId'], 1, 0xffffffff)
    from . import online_scene
    rid = online_scene.actor_rid(match, actor)
    if rid is None:
        raise ValueError('online action actor is outside the frozen roster')
    sequence = integer(value['actionSeq'], 1, 0xffff)
    event_sequence = integer(value['eventSeq'], 1, 0xffffffffffffffff)
    integer(value['serverTimeMs'], 0, 0xffffffffffffffff)
    state = integer(value['state'], 1, 0xffff)
    if state not in STATES:
        raise ValueError('unsupported online melee state')
    instances = [(int(key), session) for key, session in flow.state['sessions'].items()
                 if session['role'] == 'instance' and session.get('initialized') and not session.get('leaving')
                 and int(key) in flow.context.get('connections', [])]
    if len(instances) != 1:
        raise ValueError('online action requires one live instance')
    connection, session = instances[0]
    actions = ledger(session)
    if event_sequence != actions['eventSeq'] + 1 or sequence != actions['actors'].get(str(rid), 1) % 0xffff + 1:
        raise ValueError('online action event sequence has a gap or replay')
    actions['eventSeq'] = event_sequence
    actions['actors'][str(rid)] = sequence
    reacting = actions.setdefault('reacting', [])
    if str(rid) in reacting:
        reacting.remove(str(rid))
    if state in REACTION_STATES:
        reacting.append(str(rid))
    # Rust owns deadlines; this timestamp starts the received animation in the local instance.
    fields = dict(instance_id=rid, seq_no=sequence, state=state,
        state_change_ms=flow.now - session['instanceStartedAt'], state_time_ms=0)
    target = value.get('targetActorId')
    if target is None:
        body = battle_flow.encode_battle_state_sync_simple(**fields)
    else:
        from . import online_scene
        target_rid = 0 if target == 0 else online_scene.actor_rid(match, target)
        if target_rid is None:
            raise ValueError('online reaction target outside roster')
        body = battle_flow.encode_battle_state_sync(**fields, target_rid=target_rid)
    flow.result['send'].append({'connection': connection, 'command': 4, 'body': body,
                                'reason': f'online-action actor={actor} state={state} event={event_sequence}'})
