"""One frozen GAME duration and epoch per instance, shared by wire and timers."""
from . import contracts as wire

MAX_DURATION_MS = 86400000
LEGACY_DURATION_MS = 1200000


def validDuration(value):
    return type(value) is int and 0 < value <= MAX_DURATION_MS


def initialize(flow):
    duration = flow.context.get('gameDurationMs', wire.GAME_MS)
    if not validDuration(duration):
        raise ValueError('battle duration must be an integer in 1..86400000 ms')
    flow.session['battleClock'] = {'durationMs': duration, 'startedAt': None}


def current(flow):
    if 'battleClock' not in flow.session:
        initialize(flow)
    return flow.session['battleClock']


def start(flow):
    clock = current(flow)
    if clock['startedAt'] is not None or flow.session.get('roundEnded'):
        return False
    flow.session['battleClock'] = dict(clock, startedAt=flow.now)
    return True


def deadline(session):
    clock = session.get('battleClock', {})
    startedAt = clock.get('startedAt')
    return None if startedAt is None else startedAt + clock['durationMs']


def expired(flow):
    due = deadline(flow.session)
    return due is not None and flow.now >= due


def validState(session):
    if 'battleClock' not in session:
        return True
    clock = session['battleClock']
    return (type(clock) is dict and validDuration(clock.get('durationMs'))
            and (clock.get('startedAt') is None or type(clock['startedAt']) is int
                 and clock['startedAt'] >= 0))


def migrate(session):
    if session['role'] != 'instance' or 'battleClock' in session:
        return
    due = session.get('pending', {}).get('round-end')
    if due is None:
        return
    # Schema 12 announced this duration; a newly configured value must not extend it.
    session['battleClock'] = {'durationMs': LEGACY_DURATION_MS,
                              'startedAt': due - LEGACY_DURATION_MS}
