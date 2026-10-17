"""Bot death, kill credit and timed own-camp respawn; player death stays separate."""
import math

from . import actors, battle, body_collision, contracts as wire, spawn_navigation
from .codec import battle_flow, kill_flow, vision_flow

RESPAWN_TIMER = 'bot-respawn'
RESPAWN_MS, RETRY_MS = 5000, 50
DEATH_STATE = 42
KILL_POINTS = 100


def validState(value):
    if 'life' not in value:
        return True
    life = value['life']
    if (type(life) is not dict or life.get('state') not in ('dead', 'spawning')
            or type(life.get('epoch')) is not int or life['epoch'] < 0):
        return False
    if life['state'] == 'spawning':
        return (type(value.get('health')) is int and value['health'] > 0 and value.get('initialized') is False
                and type(life.get('spawnedAt')) is int and life['spawnedAt'] >= life['epoch'])
    return (value.get('health') == 0
            and all(type(life.get(key)) is int for key in ('diedAt', 'respawnAt'))
            and life['diedAt'] >= life['epoch'] and life['respawnAt'] == life['diedAt'] + RESPAWN_MS)


def notifyDeath(flow, actorId):
    flow.send(battle_flow.BATTLE_COMMAND, battle_flow.encode_battle_dead_notify(
        entity_rid=actorId, server_time_ms=flow.now), 'bot-dead')


def clearPerception(value):
    for key in ('opponentId', 'observedPlayer', 'lastSeenPosition', 'searchTargetId', 'searchUntil', 'senseAt',
                'roamGoal', 'roamAnchor', 'routeGoal', 'roamWaitUntil', 'roamCount', 'lastMoveAt', 'lastTurnAt', 'spawnExit',
                'tactic', 'assist', 'footwork', 'stepPlan', 'motionProbe', 'unstuck', 'meshRecovery',
                'threats', 'targetSince', 'pressAt', 'pursuit'):
        value.pop(key, None)
    value.update(reactAt=None, repathAt=0)


def announceKill(flow, attacker, target, *, weaponId=None):
    from . import combat
    winner = actors.data(flow, attacker)
    victim = actors.data(flow, target)
    # Older running rounds have no first-blood flag; existing kills/deaths still consume it.
    firstBlood = not flow.session.get('firstBloodAwarded', False) and not any(
        value.get(key, 0) for value in (flow.session, *actors.bots(flow.session).values())
        for key in ('kills', 'deaths', 'killStreak'))
    flow.session['firstBloodAwarded'] = True
    # The life streak is the temporary identity; capture it before clearing the victim.
    wasButcher = victim.get('killStreak', 0) >= kill_flow.BUTCHER_STREAK
    previousWinner, previousVictim = combat.scoreTotals(winner), combat.scoreTotals(victim)
    winner.update(kills=min(kill_flow.MAX_SCORE, previousWinner[0] + 1),
                  battlePoints=min(kill_flow.MAX_SCORE, previousWinner[2] + KILL_POINTS))
    victim.update(deaths=min(kill_flow.MAX_SCORE, previousVictim[1] + 1), killStreak=0)
    streak = min(kill_flow.MAX_STREAK, winner.get('killStreak', 0) + 1)
    winner['killStreak'] = streak
    flow.send(kill_flow.INSTANCE_COMMAND, kill_flow.encode_kill_event(
        server_time_ms=flow.now, source_rid=attacker, target_rid=target,
        weapon_tid=wire.WEAPON_ID if weaponId is None else weaponId), 'combat-kill')
    if streak >= 2:
        flow.send(kill_flow.SCORE_COMMAND, kill_flow.encode_score_stat_message(
            server_time_ms=flow.now, entity_rid=attacker, count=streak), 'combat-kill-streak')
    if wasButcher:
        flow.send(kill_flow.SCORE_COMMAND, kill_flow.encode_butcher_killer_message(
            server_time_ms=flow.now, entity_rid=attacker), 'combat-butcher-killer')
    combat.publishScore(flow, attacker, previous=previousWinner,
                        statMessageId=kill_flow.FIRST_BLOOD_MESSAGE_ID if firstBlood else 0)
    combat.publishScore(flow, target, previous=previousVictim)
    combat.publishCampScore(flow)
    streakMessageId = (kill_flow.BATTLEFIELD_BUTCHER_MESSAGE_ID if streak >= kill_flow.BUTCHER_STREAK
                       else 710000 + streak * 10 if streak >= 2 else 0)
    flow.log('DEBUG', f'Audio announcement; serverTimeMs={flow.now}; attacker={attacker}; '
                     f'localPlayer={int(attacker == wire.ACTOR_ID)}; streak={streak}; '
                     f'streakMessageId={streakMessageId}; firstBlood={int(firstBlood)}; '
                     f'butcherEnded={int(wasButcher)}; stage=queued; clientPlayback=not-observed')
    flow.log("INFO", f"combat-kill attacker={attacker} target={target} streak={streak} "
                              f"first-blood={firstBlood} butcher-ended={wasButcher} "
                              f"kills={winner['kills']} points={winner['battlePoints']} target-deaths={victim['deaths']}")


def die(flow, actorId, attacker, position, *, weaponId=None):
    from . import bot, combat, scene
    if actorId == wire.ACTOR_ID or flow.session.get('roundEnded') or not actors.active(flow, actorId):
        return
    value = actors.data(flow, actorId)
    value['position'] = list(position)
    bot.stop(flow, actorId)
    flow.cancel(combat.hurtTimer(actorId))
    value.pop('hurtUntil', None)
    clearPerception(value)
    value.update(health=0, status='dead', life=dict(state='dead', diedAt=flow.now,
                 respawnAt=flow.now + RESPAWN_MS, epoch=flow.session['instanceStartedAt']))
    # DEAD_NOTIFY unregisters the RID as well as closing GePlayer's life gate.
    announceKill(flow, attacker, actorId, weaponId=weaponId)
    battle.syncState(flow, DEATH_STATE, actorId, targetId=attacker)
    notifyDeath(flow, actorId)
    if scene.checkKillTarget(flow, actors.data(flow, attacker).get('camp', 1)):
        return
    flow.later(actors.timerName(RESPAWN_TIMER, actorId), RESPAWN_MS)
    flow.log("INFO", f"bot-death actor={actorId} attacker={attacker} respawn-at={flow.now + RESPAWN_MS}")


def chooseSpawn(flow, actorId, snapshot):
    from . import bot
    from .roster import loadRespawns, loadSpawns
    value = actors.data(flow, actorId)
    points = flow.session.get('respawnPoints')
    if points is None:
        # A running pre-respawn revision keeps its roster, but uses the same map's revive points.
        resource = loadRespawns(flow.context.get('clientDirectory', ''))
        points = {str(camp): [[list(pos), heading] for pos, heading in group] for camp, group in resource.items()}
        flow.session['respawnPoints'] = points
    references = tuple((tuple(position), heading) for position, heading in points[str(value['camp'])])
    battlefield = spawn_navigation.enabled(flow)
    if battlefield:
        anchor = loadSpawns(flow.context.get('clientDirectory', ''))[value['camp']][0][0]
        groups = spawn_navigation.candidates(bot.navigationFor(flow), references, tuple(anchor))
    else:
        groups = tuple((point,) for point in references)
    if not any(groups):
        bot.status(flow, 'respawn-no-battlefield-point', actorId)
        return None
    offset = (actorId + value['life']['diedAt'] // RESPAWN_MS) % len(groups)
    neighbors = body_collision.obstacles(flow, actorId, snapshot)
    for index in (*range(offset, len(groups)), *range(offset)):
        for position, heading in groups[index]:
            if all(abs(position[2] - point[2]) >= body_collision.HEIGHT
                   or math.dist(position[:2], point[:2]) >= body_collision.DIAMETER + body_collision.SKIN
                   for point in neighbors):
                if battlefield:
                    reference = references[index][0]
                    distance = math.dist(position[:2], reference[:2])
                    flow.log("DEBUG", f'bot-respawn-point actor={actorId} reference={list(reference)} '
                                              f'position={list(position)} horizontal-distance={distance:.3f}')
                return position, heading
    bot.status(flow, 'respawn-occupied', actorId)
    return None


def respawn(flow, actorId):
    from . import bot, combat
    value = actors.data(flow, actorId)
    life = value.get('life', {})
    if (life.get('state') != 'dead' or not battle.enabled(flow)
            or life['epoch'] != flow.session.get('instanceStartedAt')):
        return
    name = actors.timerName(RESPAWN_TIMER, actorId)
    if flow.now < life['respawnAt']:
        flow.later(name, life['respawnAt'] - flow.now)
        return
    snapshot = actors.world(flow)
    try:
        point = chooseSpawn(flow, actorId, snapshot) if snapshot is not None else None
    except (ValueError, OSError) as error:
        bot.status(flow, 'respawn-resource-unavailable' if isinstance(error, OSError) else 'respawn-resource-invalid', actorId)
        point = None
    if point is None:
        flow.later(name, RETRY_MS)
        return
    position, heading = point
    bot.stop(flow, actorId, publish=False)
    clearPerception(value)
    value.pop('heightOffset', None)
    from . import damage_state
    damage_state.reset(value)
    value.update(position=list(position), heading=heading, health=combat.MAX_HEALTH,
                 contactLifeAt=flow.now, contactSwings=[],
                 initialized=False, status='spawning',
                 life=dict(state='spawning', spawnedAt=flow.now, epoch=life['epoch']))
    # Recreate the same RID to reset native life/collision state, not just its visible HP.
    flow.send(0xE, vision_flow.encode_vision_del_event(object_mid=actorId), 'bot-respawn-remove')
    flow.send(0xE, bot.vision(value, actorId), 'bot-respawn-add')
    battle.syncState(flow, battle_flow.ACT_STATE_IDLE, actorId)
    flow.later(actors.timerName(bot.THINK_TIMER, actorId), bot.THINK_MS)
    flow.log("INFO", f"bot-respawn actor={actorId} camp={value['camp']} position={list(position)}")


def ready(flow, actorId):
    value = actors.data(flow, actorId)
    life = value.get('life')
    if life is None:
        return True
    if life['state'] != 'spawning' or life['epoch'] != flow.session.get('instanceStartedAt'):
        return False
    snapshot = actors.world(flow)
    pose = actors.pose(snapshot, actorId)
    if (pose is None or snapshot['sampledAt'] <= life['spawnedAt']
            or math.dist(pose['position'][:2], value['position'][:2]) > 1.
            or abs(pose['position'][2] - value['position'][2]) > (12. if 'spawnExit' in value else 1.)):
        return False
    value.pop('life')
    return True


def timer(flow, name):
    actorId = next((int(key) for key in actors.bots(flow.session)
                    if name == actors.timerName(RESPAWN_TIMER, int(key))), None)
    if actorId is None:
        return False
    respawn(flow, actorId)
    return True
