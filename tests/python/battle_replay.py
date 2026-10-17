"""Offline business oracle. No sockets, client process or production state writes."""
from copy import deepcopy
import math
from time import perf_counter_ns
from types import SimpleNamespace

from Business.runtime import host_runtime as host
from Business.scripts import app

SCHEMA_VERSION = 1
ANIMATION_PREFIXES = ('battle-action-phase', 'bot-action-phase', 'bot-release', 'combat-hurt-')


def encodeValue(value):
    if value is None or type(value) in (str, bool):
        return value
    if type(value) is int and -(1 << 63) <= value < (1 << 64):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if type(value) is bytes:
        return {'$bytes': value.hex()}
    if type(value) in (list, tuple):
        return [encodeValue(item) for item in value]
    if type(value) is dict and all(type(key) is str for key in value) and '$bytes' not in value:
        return {key: encodeValue(item) for key, item in value.items()}
    raise TypeError('replay value must be finite, portable and have no reserved $bytes key')


def decodeValue(value):
    if type(value) is dict:
        if '$bytes' in value:
            text = value.get('$bytes')
            if (set(value) != {'$bytes'} or type(text) is not str or len(text) % 2
                    or any(char not in '0123456789abcdef' for char in text)):
                raise ValueError('replay bytes must be lowercase hexadecimal')
            return bytes.fromhex(text)
        return {key: decodeValue(item) for key, item in value.items()}
    if type(value) is list:
        return [decodeValue(item) for item in value]
    encodeValue(value)
    return value


def timerName(timerId):
    return timerId.split(':', 2)[-1] if timerId.startswith('match:') else timerId.split(':', 1)[-1]


class MemoryProfileStore:
    def __init__(self, profile):
        self.value = deepcopy(profile)
        self.fail = False

    def save(self, path, value, previous):
        if path != 'replay-profile' or previous != self.value:
            raise ValueError('replay profile storage mismatch')
        if self.fail:
            raise OSError('synthetic profile write failure')
        self.value = deepcopy(value)


class ReplaySession:
    def __init__(self, case):
        if case.get('schemaVersion') != SCHEMA_VERSION:
            raise ValueError('unsupported replay schema')
        self.context = decodeValue(deepcopy(case['context']))
        if 'playerProfilePath' in self.context or 'clientDirectory' in self.context:
            raise ValueError('offline replay uses synthetic resources, not filesystem context')
        value = decodeValue(case['initialState'])
        if not app.validateState(value):
            raise ValueError('invalid replay initial state')
        self.state = host.encode(value)
        self.timers = decodeValue(deepcopy(case.get('initialTimers', {})))
        self.worlds = decodeValue(deepcopy(case.get('worlds', {})))
        self.worldVersion = None
        self.now = self.context['nowMs']
        self.runtime = object.__new__(host.Runtime)
        self.runtime.active = SimpleNamespace(module=app, name=app.__package__)
        self.profileStore = None
        if case.get('profileStorage') is True:
            self.profileStore = MemoryProfileStore(value['playerProfile'])
            self.context.update(clientDirectory='replay-catalogue', playerProfilePath='replay-profile')
            self.runtime.profileStore = lambda: self.profileStore

    def snapshot(self):
        result = {'state': encodeValue(host.decode(self.state)), 'timers': encodeValue(self.timers)}
        if self.profileStore is not None:
            result['profile'] = encodeValue(self.profileStore.value)
        return result

    def dispatch(self, step):
        now = step['atMs']
        if type(now) is not int or now < self.now:
            raise ValueError('replay clock must be monotonic')
        context = deepcopy(self.context)
        context['nowMs'] = now
        context['connections'] = list(step.get('connections', context['connections']))
        version = step.get('worldVersion')
        if version is not None:
            if str(version) not in self.worlds:
                raise ValueError('unknown replay world version')
            if self.worldVersion is not None and version < self.worldVersion:
                raise ValueError('replay world version must be monotonic')
            self.worldVersion = version
        event = decodeValue(deepcopy(step['event']))
        if self.worldVersion is not None:
            context['botWorld'] = deepcopy(self.worlds[str(self.worldVersion)])
            if event['type'] != 'world' and 'contactJournal' in context['botWorld']:
                context['botWorld']['contactJournal']['events'] = []
        self.now = now
        if self.profileStore is not None:
            self.profileStore.fail = step.get('profileSaveError', False)
        if event['type'] == 'timer':
            prefix = f"match:{event['matchId']}" if event.get('scope') == 'match' else str(event['connection'])
            self.timers.pop(f"{prefix}:{event['name']}", None)
        started = perf_counter_ns()
        try:
            result = self.runtime.dispatchNative(event, self.state, context)
            nextState, send, changes, logs, phase, timing, titleRequest = result
            live = step.get('commitConnections', context['connections'])
            totals = {}
            for packet in send:
                totals[packet['connection']] = totals.get(packet['connection'], 0) + len(packet['body']) + 23
            if any(connection not in live or size > step.get('sendCapacity', 4 * 1024 * 1024)
                   for connection, size in totals.items()):
                raise ValueError('transition rejected: dead target or send backpressure')
            timers = deepcopy(self.timers)
            committedAt = step.get('committedAtMs', now)
            if type(committedAt) is not int or committedAt < now:
                raise ValueError('invalid replay commit time')
            for timerId, delay, serialized, connection in changes:
                if delay < 0:
                    timers.pop(timerId, None)
                else:
                    if connection and connection not in live:
                        raise ValueError('timer target disconnected before commit')
                    origin = committedAt if timerName(timerId).startswith(ANIMATION_PREFIXES) else now
                    timers[timerId] = {'dueMs': origin + delay, 'event': host.decode(serialized)}
            if event['type'] == 'closed':
                timers = {key: item for key, item in timers.items()
                          if item['event']['connection'] != event['connection']}
            if len(timers) > 1024:
                raise ValueError('timer capacity exceeded')
            self.state, self.timers = nextState, timers
            outcome = dict(status='committed', send=encodeValue(send), logs=encodeValue(logs),
                           phase=phase, titleRequest=encodeValue(titleRequest))
        except (ValueError, TypeError, OverflowError) as error:
            timing = {}
            outcome = dict(status='rejected', send=[], error=type(error).__name__ + ': ' + str(error))
        elapsed = (perf_counter_ns() - started) / 1e6
        outcome.update(self.snapshot())
        return outcome, dict(timing, dispatchMs=elapsed)


def runCase(case):
    session = ReplaySession(case)
    outcomes, timings = [], []
    for step in case['steps']:
        outcome, timing = session.dispatch(step)
        outcomes.append(outcome)
        timings.append(timing)
    return dict(name=case['name'], actorCount=case['actorCount'], steps=outcomes, timings=timings)
