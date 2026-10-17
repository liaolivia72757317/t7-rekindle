"""Portable synthetic replay inputs; no installed client resources are read."""
from contextlib import contextmanager
from copy import deepcopy
import json
import struct
from unittest.mock import patch

from Business.scripts import actors, app, battle, bot, contracts, controls, roster, hero_resources
from Business.scripts.codec import loadout_flow
from Business.scripts.navigation import NavMesh
from battle_replay import SCHEMA_VERSION, encodeValue
from loadout_fixture import catalogue


@contextmanager
def resources():
    spawns = {camp: tuple(((float(i * 2), float(camp * 20), .2), -90 if camp == 1 else 90)
                         for i in range(10)) for camp in (1, 2)}
    respawns = {camp: tuple(((float(i * 2), float(camp * 20 + 5), .2), -90 if camp == 1 else 90)
                           for i in range(14 if camp == 1 else 12)) for camp in (1, 2)}
    nav = NavMesh.fromPolygons([[(-100, -100, 0), (100, -100, 0), (100, 100, 0), (-100, 100, 0)]])
    with (patch.object(roster, 'loadSpawns', lambda _: spawns),
          patch.object(roster, 'loadRespawns', lambda _: respawns),
          patch.object(roster, 'loadNames', lambda _: tuple(f'Replay{i:02}' for i in range(30))),
          patch.object(bot, 'navigationFor', lambda _: nav),
          patch.object(hero_resources, 'loadCatalogue', lambda _: catalogue())):
        yield


def context():
    return dict(nowMs=1000, runtimeMovement=True, connections=[1],
                advertisedAddress='127.0.0.1', instancePort=12003, playerName='Replay')


def arena():
    ctx = context()
    state = app.createState(ctx)
    state['sessions']['1'] = dict(role='instance', hydration=[], pending={}, camp=1,
        authenticated=True, battleEntered=True, groundEnabled=True, heroChosen=True,
        instanceStartedAt=100, moveClock=controls.MOVE_CLOCK, controlBaseline=contracts.BASELINE_ID)
    flow = app.Flow({'connection': 1}, state, ctx)
    roster.assemble(flow)
    return flow


def world(flow, now):
    allActors = {}
    for rid in actors.ids(flow.session):
        data = actors.data(flow, rid)
        position = data['spawnPosition'] if rid == 1 else data['position']
        heading = data['spawnHeading'] if rid == 1 else data['heading']
        probe = dict(ready=True, hit=False, material=-1, position=[0., 0., 0.],
                     normal=[0., 0., 1.], direction=[1., 0., 0.])
        allActors[str(rid)] = dict(ready=True, position=list(position), heading=heading,
            entity=100 + rid, state=2, seq=1, phaseAt=now, targetId=0,
            terrain={name: deepcopy(probe) for name in ('forward', 'target')},
            targetTerrain={str(target): deepcopy(probe) for target in actors.ids(flow.session) if target != rid})
    return dict(ready=True, sampledAt=now, instanceStartedAt=100, actors=allActors,
        player=allActors['1']['position'], bot=allActors['2']['position'],
        playerHeading=allActors['1']['heading'], botHeading=allActors['2']['heading'],
        blades={}, contactJournal=dict(damageEvidenceVersion=2, throughAt=now, batchId=1,
                                      dropped=0, frames=0, events=[]))


def message(command, body):
    return encodeValue(dict(type='message', connection=1, sequence=1, serverTimeMs=0,
                           command=command, body=body))


def action(intent):
    return message(4, struct.pack('>HIib', 1, 0x8e, intent, 1))


def case(name, state, steps, *, worlds=None, timers=None, actorCount=0):
    return dict(schemaVersion=SCHEMA_VERSION, name=name, actorCount=actorCount,
        initialState=encodeValue(state), context=encodeValue(context()),
        initialTimers=encodeValue(timers or {}), worlds=encodeValue(worlds or {}), steps=steps)


def timerLedger(flow):
    timers = {}
    for item in flow.result['timers']:
        if item['delayMs'] < 0:
            timers.pop(item['id'], None)
        else:
            timers[item['id']] = dict(dueMs=flow.session['pending'][item['event']['name']], event=item['event'])
    return timers


def contactCase(lethal=False):
    flow = arena()
    if lethal:
        actors.data(flow, 2)['health'] = 1
    battle.startAction(flow, 300560, released=True)
    flow.now = flow.session['pending'][battle.actionTimer(1)]
    app.handleTimer(flow, battle.actionTimer(1))
    actionState = battle.actionState(flow, 1)
    phaseAt = flow.now
    delivered = phaseAt + 350
    raw = world(flow, delivered)
    raw['actors']['1'].update(position=[0., 0., .2], heading=0, state=actionState['state'],
                              seq=actionState['seq'], phaseAt=phaseAt)
    raw['actors']['2'].update(position=[1.5, 0., .2], heading=180)
    contact = dict(ready=True, hit=True, material=0, position=[1., 0., 1.], normal=[-1., 0., 0.],
        direction=[1., 0., 0.], actorId=1, targetId=2, entity=101, targetEntity=102,
        state=actionState['state'], seq=actionState['seq'], phaseAt=phaseAt, occurredAt=phaseAt + 30,
        sourcePosition=[0., 0., .2], targetPosition=[1.5, 0., .2], sourceHeading=0, targetHeading=180,
        targetState=2, targetSeq=1, targetPhaseAt=phaseAt, along=.5, bodyIndex=0, region='torso',
        defenceWeaponId=0, sourceVelocity=[0., 0., 0.], targetVelocity=[0., 0., 0.],
        weaponVelocity=[0., 0., 0.], velocityReady=False, airborne=False, targetAirborne=False)
    raw['contactJournal'].update(events=[contact], frames=12)
    steps = [dict(atMs=delivered, worldVersion=1, event=dict(type='world', connection=0)),
             dict(atMs=delivered + 1, worldVersion=1, event=dict(type='world', connection=0))]
    worlds = {'1': raw}
    if lethal:
        worlds['2'] = world(flow, delivered + 5000)
        worlds['2']['contactJournal']['batchId'] = 2
        steps.append(dict(atMs=delivered + 5000, worldVersion=2,
                          event=dict(type='timer', connection=1, name='bot-respawn')))
    return case('twenty-actor-respawn' if lethal else 'twenty-actor-contact', flow.state,
                steps, worlds=worlds, timers=timerLedger(flow), actorCount=20)


def roundCase():
    flow = arena()
    flow.session['battleClock'] = dict(startedAt=1000, durationMs=1000)
    bot.start(flow)
    flow.later('round-end', 1000)
    return case('twenty-actor-settlement-restart', flow.state, [
        dict(atMs=2000, event=dict(type='timer', connection=1, name='round-end')),
        dict(atMs=7000, event=dict(type='timer', connection=1, name='round-finish')),
        dict(atMs=7010, connections=[], event=dict(type='closed', connection=1)),
        dict(atMs=7020, connections=[2], event=dict(type='connected', role='instance', connection=2)),
        dict(atMs=7021, connections=[2], event=dict(type='authenticated', connection=2)),
        dict(atMs=7041, connections=[2], event=dict(type='timer', connection=2, name='instance-init')),
        dict(atMs=7042, connections=[2], event=dict(type='timer', connection=1, name='actors-pulse')),
    ], timers=timerLedger(flow), actorCount=20)


def titleCase():
    state = app.createState(context())
    state['sessions']['1'] = dict(role='logic', hydration=[], pending={}, authenticated=True)

    def snapshot(epoch=1, equipped=1008, owned=True, requestId=0):
        payload = dict(titles=[dict(titleId=1008, enabled=True, owned=owned)], equippedTitleId=equipped)
        return encodeValue(dict(type='titles', connection=0, sequence=epoch, serverTimeMs=requestId,
                                command=0, body=json.dumps(payload).encode('utf-8')))

    request = message(31, struct.pack('>HI', 5, 0))
    request['eventId'] = 22
    return case('title-confirmation-and-epoch', state, [
        dict(atMs=1000, event=snapshot()),
        dict(atMs=1001, event=request),
        dict(atMs=1002, event=snapshot(equipped=None, requestId=22)),
        dict(atMs=1003, event=snapshot(epoch=2, equipped=None, owned=False)),
        dict(atMs=1004, event=snapshot()),
        dict(atMs=11001, event=dict(type='timer', connection=1, name='title-request-timeout')),
    ])


def profileCase():
    ctx = dict(context(), clientDirectory='replay-catalogue', playerProfilePath='replay-profile')
    state = app.createState(ctx)
    state['sessions']['1'] = dict(role='logic', hydration=[], pending={}, authenticated=True)
    def formation(ids):
        body = struct.pack('>Hii', 5, 1, len(ids)) + b''.join(loadout_flow.reference(catalogue(), tid) for tid in ids)
        return message(0x22, body)
    result = case('profile-save-and-rollback', state, [
        dict(atMs=1000, event=formation([2102, 1101])),
        dict(atMs=1001, event=formation([2102, 1101])),
        dict(atMs=1002, event=formation([1101]), profileSaveError=True),
        dict(atMs=1003, event=formation([1101, 1101])),
        dict(atMs=1004, event=formation([1101])),
    ])
    result['profileStorage'] = True
    return result


def cases():
    heartbeat = message(29, struct.pack('>HQIH', 1, 123456789, 7, 8))
    login = case('login-lifecycle', app.createState(context()), [
        dict(atMs=1000, event=dict(type='connected', role='login', connection=1)),
        dict(atMs=1001, event=dict(type='authenticated', connection=1)),
        dict(atMs=2001, event=dict(type='timer', connection=1, name='version')),
        dict(atMs=2100, event=heartbeat),
        dict(atMs=2200, connections=[], event=dict(type='closed', connection=1)),
    ])
    flow = arena()
    actionCase = case('twenty-actor-action', flow.state, [
        dict(atMs=1000, event=heartbeat),
        dict(atMs=1010, event=action(300560)),
        dict(atMs=1020, event=action(battle.ATTACK_RELEASE)),
        dict(atMs=1370, event=dict(type='timer', connection=1, name='battle-action-phase')),
        dict(atMs=1400, worldVersion=1, event=dict(type='world', connection=0)),
        dict(atMs=2010, event=dict(type='timer', connection=1, name='battle-action-phase')),
        dict(atMs=2510, event=dict(type='timer', connection=1, name='battle-action-phase')),
    ], worlds={'1': world(flow, 1400)}, actorCount=20)
    bot.start(flow)
    timers = timerLedger(flow)
    ai = case('twenty-actor-ai', flow.state, [
        dict(atMs=1100, worldVersion=1, event=dict(type='timer', connection=1, name='actors-pulse')),
        *[dict(atMs=1100, worldVersion=1, event=dict(type='timer', connection=1, name='actors-decision'))
          for _ in range(19)],
        dict(atMs=1110, worldVersion=1, event=heartbeat),
    ], timers=timers, worlds={'1': world(flow, 1100)}, actorCount=20)
    return [login, actionCase, ai, contactCase(), contactCase(lethal=True), roundCase(), titleCase(), profileCase()]
