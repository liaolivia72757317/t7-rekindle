"""Player death and respawn through the client's native spectator/hero UI flow."""
import math

from . import actors, battle, body_collision, bot_life, contracts as wire, controls, loadouts
from .codec import battle_flow, room_flow, vision_flow

SELECT_TIMER, RESPAWN_TIMER = 'player-select', 'player-respawn'
RESPAWN_MS, RETRY_MS = 8000, 50


def validState(session):
    life = session.get('life')
    if life is None:
        return True
    if (type(life) is not dict or life.get('state') not in ('dead', 'selecting', 'waiting', 'spawning')
            or any(type(life.get(key)) is not int for key in ('epoch', 'diedAt', 'respawnAt'))
            or not 0 <= life['epoch'] <= life['diedAt']
            # Preserve an already scheduled five-second life across script reloads.
            or life['respawnAt'] - life['diedAt'] not in (5000, RESPAWN_MS)):
        return False
    if life['state'] == 'spawning':
        return (session.get('health') == 100 and actors.validPosition(session.get('spawnPosition'))
                and type(life.get('spawnedAt')) is int and life['spawnedAt'] >= life['respawnAt'])
    return session.get('health') == 0


def reset(flow):
    flow.cancel(SELECT_TIMER)
    flow.cancel(RESPAWN_TIMER)
    flow.session.pop('life', None)


def dead(flow):
    return flow.session.get('life', {}).get('state') in ('dead', 'selecting', 'waiting')


def canChoose(flow):
    life = flow.session.get('life', {})
    return (life.get('state') == 'selecting' and battle.enabled(flow)
            and life['epoch'] == flow.session.get('instanceStartedAt') and flow.now >= life['respawnAt'])


def die(flow, attacker, position, *, weaponId=None):
    from . import combat, scene
    if not actors.active(flow, wire.ACTOR_ID) or not battle.enabled(flow):
        return
    battle.resetAction(flow)
    controls.cancelMotionTimers(flow)
    flow.cancel(combat.hurtTimer(wire.ACTOR_ID))
    flow.session.pop('hurtUntil', None)
    ground = controls.groundState(flow)
    ground.update(position=list(position), mask=0, crouched=False, jumpPressed=False,
                  moveStartedAt=None, lastAdvanceAt=None, nextDeadline=None)
    flow.session.update(health=0, heroChosen=False, life=dict(
        state='dead', diedAt=flow.now, respawnAt=flow.now + RESPAWN_MS,
        epoch=flow.session['instanceStartedAt']))
    # Resolve HUD identities before DEAD_NOTIFY unregisters the local entity RID.
    bot_life.announceKill(flow, attacker, wire.ACTOR_ID, weaponId=weaponId)
    battle.syncState(flow, bot_life.DEATH_STATE, targetId=attacker)
    controls.activate(flow)
    flow.send(4, battle_flow.encode_battle_dead_notify(entity_rid=wire.ACTOR_ID, server_time_ms=flow.now),
              'player-dead')
    if scene.checkKillTarget(flow, actors.data(flow, attacker).get('camp', 1)):
        return
    # Native DEATH owns the camera delay, friend cycling and free mouse-look.
    flow.send(0x36, wire.actorState(flow.now, 9), 'player-observer')
    flow.later(SELECT_TIMER, RESPAWN_MS)
    flow.log("INFO", f'player-death attacker={attacker} respawn-at={flow.now + RESPAWN_MS}')


def chooseSpawn(flow, snapshot):
    from .roster import loadRespawns
    heroes = flow.session.get('loadoutProfile', {}).get('formation', [wire.HERO_ID])
    if flow.session.get('heroId') not in heroes:
        raise ValueError('selected hero has no supported respawn troop')
    points = flow.session.get('respawnPoints')
    if points is None:
        resource = loadRespawns(flow.context.get('clientDirectory', ''))
        points = {str(camp): [[list(pos), yaw] for pos, yaw in group] for camp, group in resource.items()}
        flow.session['respawnPoints'] = points
    candidates = points[str(flow.session['camp'])]
    offset = flow.session['life']['diedAt'] // RESPAWN_MS % len(candidates)
    neighbors = body_collision.obstacles(flow, wire.ACTOR_ID, snapshot)
    for index in (*range(offset, len(candidates)), *range(offset)):
        position, heading = candidates[index]
        if all(abs(position[2] - other[2]) >= body_collision.HEIGHT
               or math.dist(position[:2], other[:2]) >= body_collision.DIAMETER + body_collision.SKIN
               for other in neighbors):
            return list(position), heading
    return None


def requestSpawn(flow):
    if not canChoose(flow) or not flow.session.get('heroChosen'):
        # Repeated PLAY during recreation is an acknowledgement, not another life.
        result = 0 if flow.session.get('life', {}).get('state') == 'spawning' else 1
        flow.send(0x36, room_flow.encode_actor_play_response(result=result), 'player-play-not-ready')
        return
    flow.session['life'] = {**flow.session['life'], 'state': 'waiting'}
    respawn(flow)


def respawn(flow):
    from . import combat, roster
    life = flow.session['life']
    snapshot = actors.world(flow)
    try:
        point = chooseSpawn(flow, snapshot) if snapshot is not None else None
    except (OSError, ValueError) as error:
        status = 'resource-unavailable' if isinstance(error, OSError) else 'resource-invalid'
        if life.get('status') != status:
            flow.log("WARN", 'player-respawn-' + status)
        flow.session['life'] = {**life, 'status': status}
        point = None
    if point is None:
        flow.later(RESPAWN_TIMER, RETRY_MS)
        return
    position, heading = point
    loadouts.resetWeapons(flow)
    flow.session.update(spawnPosition=position, spawnHeading=heading, health=combat.MAX_HEALTH,
                        contactLifeAt=flow.now, contactSwings=[],
                        life={**life, 'state': 'spawning', 'spawnedAt': flow.now})
    controls.groundState(flow).update(position=list(position), heading=heading, mask=0,
                                     crouched=False, jumpPressed=False, moveStartedAt=None,
                                     lastAdvanceAt=None, nextDeadline=None)
    flow.send(0x36, room_flow.encode_actor_play_response(), 'player-respawn-play')
    flow.send(0x36, wire.actorState(flow.now, 6), 'player-respawn-in-scene')
    flow.send(0xE, vision_flow.encode_vision_del_event(), 'player-respawn-remove')
    flow.send(0xE, roster.playerVision(flow), 'player-respawn-add')
    from . import shields, guard_state
    shields.publishAll(flow)
    guard_state.publish(flow, wire.ACTOR_ID)
    battle.syncState(flow, battle_flow.ACT_STATE_IDLE)
    combat.publishHealth(flow, wire.ACTOR_ID)
    controls.activate(flow)
    flow.later(RESPAWN_TIMER, RETRY_MS)
    flow.log("INFO", f"player-respawn hero={flow.session['heroId']} camp={flow.session['camp']} "
                              f'position={position}')


def ready(flow):
    snapshot = actors.world(flow)
    pose = actors.pose(snapshot, wire.ACTOR_ID)
    life = flow.session['life']
    position = flow.session['spawnPosition']
    if (pose is None or snapshot['sampledAt'] <= life['spawnedAt']
            or math.dist(pose['position'][:2], position[:2]) > 1.
            or abs(pose['position'][2] - position[2]) > 12.):
        flow.later(RESPAWN_TIMER, RETRY_MS)
        return
    # The native controller resolves the authored platform's surface height.
    controls.groundState(flow)['position'] = list(pose['position'])
    flow.session.pop('life')
    controls.activate(flow)


def timer(flow, name):
    if name not in (SELECT_TIMER, RESPAWN_TIMER):
        return False
    life = flow.session.get('life')
    if (life is None or not battle.enabled(flow)
            or life['epoch'] != flow.session.get('instanceStartedAt')):
        return True
    if name == SELECT_TIMER and life['state'] == 'dead':
        if flow.now < life['respawnAt']:
            flow.later(name, life['respawnAt'] - flow.now)
        else:
            flow.session['life'] = {**life, 'state': 'selecting'}
            flow.send(0x36, wire.actorState(flow.now, 7), 'player-respawn-select-hero')
    elif name == RESPAWN_TIMER:
        if life['state'] == 'waiting':
            respawn(flow)
        elif life['state'] == 'spawning':
            ready(flow)
    return True
