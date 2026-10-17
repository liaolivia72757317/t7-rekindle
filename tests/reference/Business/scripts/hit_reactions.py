"""Action interruption and movement locks have independent lifetimes."""
from . import actors, battle, contracts as wire, controls
from .codec import move_flow

HURT_STATE, KNOCKDOWN_STATE, EXHAUSTION_STATE = 35, 40, 598


def movementTimer(actorId):
    return f'combat-movement-{actorId}'


def lockMovement(flow, actorId, duration):
    from . import bot
    if duration <= 0:
        return
    data = actors.data(flow, actorId)
    data['movementUntil'] = max(data.get('movementUntil', 0), flow.now + duration)
    if actorId == wire.ACTOR_ID:
        flow.send(2, move_flow.encode_move_notify_active(server_tick=controls.nextGroundTick(flow),
            target_instance_id=actorId, active=0), 'combat-movement-lock')
    else:
        bot.stopMotion(flow, actorId)
    flow.later(movementTimer(actorId), data['movementUntil'] - flow.now)


def hurt(flow, actorId, attacker, duration, *, state=HURT_STATE, movementMs=0):
    from . import bot, combat
    data = actors.data(flow, actorId)
    battle.resetAction(flow, actorId, preserveBuffered=True)
    data['defenceInterruptedAt'] = getattr(flow, 'contactAt', flow.now)
    if actorId != wire.ACTOR_ID:
        data['forwardMs'] = 0
        flow.cancel(actors.timerName(bot.RELEASE_TIMER, actorId))
        data['reactAt'] = None
    duration = max(duration, data.get('hurtUntil', 0) - flow.now)
    data['hurtUntil'] = flow.now + duration
    battle.syncState(flow, state, actorId, targetId=attacker, durationMs=duration)
    flow.later(combat.hurtTimer(actorId), duration)
    lockMovement(flow, actorId, movementMs)


def timer(flow, name):
    actorId = next((rid for rid in actors.ids(flow.session) if name == movementTimer(rid)), None)
    if actorId is None:
        return False
    data = actors.data(flow, actorId)
    if 'movementUntil' not in data:
        return True
    if flow.now < data['movementUntil']:
        flow.later(name, data['movementUntil'] - flow.now)
        return True
    data.pop('movementUntil')
    if actorId == wire.ACTOR_ID and battle.enabled(flow) and actors.active(flow, actorId):
        controls.activate(flow)
    return True
