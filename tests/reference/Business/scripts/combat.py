"""Local melee contact and health; original client messages render the result."""
import math
from copy import deepcopy

from . import actors, battle, blade_contacts, body_collision, bot, bot_life, bot_roaming, contact_journal, contracts as wire
from . import dash_resources, player_life, weapons, loadouts, damage_state, guard_state, hit_reactions
from .codec import attr_flow, battle_flow, kill_flow

MAX_HEALTH = 100
HURT_STATE, HURT_MS = 35, 400
CONTACT_MS = 50
REACH, HEIGHT_LIMIT, FACING_COS = 2.2, 1.0, 0.5
HIT_HEIGHT = 1.0


def hitTimer(actorId):
    return f"combat-hit-{actorId}"


def hurtTimer(actorId):
    return f"combat-hurt-{actorId}"


def initialize(flow):
    player_life.reset(flow)
    flow.session['firstBloodAwarded'] = False
    for actorId in actors.ids(flow.session):
        data = battle.actorData(flow, actorId)
        data["health"] = MAX_HEALTH
        if actorId == wire.ACTOR_ID:
            loadouts.resetWeapons(flow)
        else:
            data.pop('weaponId', None)
            damage_state.reset(data)
        data['contactLifeAt'] = flow.now
        data.pop('contactSwings', None)
        data['killStreak'] = 0
        data.update(kills=0, deaths=0, battlePoints=0)
        data.pop("hurtUntil", None)
        data.pop('life', None)
        flow.cancel(actors.timerName(bot_life.RESPAWN_TIMER, actorId))
        cancelSwing(flow, actorId)
        flow.cancel(hurtTimer(actorId))


def validState(data):
    if not damage_state.validState(data):
        return False
    if type(data.get('firstBloodAwarded', False)) is not bool:
        return False
    if type(data.get('killStreak', 0)) is not int or not 0 <= data.get('killStreak', 0) <= kill_flow.MAX_STREAK:
        return False
    if any(type(data.get(key, 0)) is not int or not 0 <= data.get(key, 0) <= kill_flow.MAX_SCORE
           for key in ('kills', 'deaths', 'battlePoints')):
        return False
    if "health" not in data:
        return not any(key in data for key in ("swing", "hurtUntil"))
    if type(data["health"]) is not int or not 0 <= data["health"] <= MAX_HEALTH:
        return False
    if "hurtUntil" in data and (type(data["hurtUntil"]) is not int or data["hurtUntil"] < 0):
        return False
    if type(data.get('contactLifeAt', 0)) is not int or data.get('contactLifeAt', 0) < 0:
        return False
    previous = data.get('contactSwings', [])
    if type(previous) is not list or len(previous) > 32:
        return False
    if not all(validSwing(value) and type(value.get('closedAt')) is int and value['closedAt'] >= 0
               and type(value.get('intent')) is int and battle.attackChain(value['intent']) is not None
               and type(value.get('state')) is int for value in previous):
        return False
    swing = data.get('swing')
    return swing is None or validSwing(swing)


def validSwing(swing):
    if type(swing) is not dict or not weapons.validState(swing) or not all(
            type(swing.get(key)) is int and swing[key] >= 0 for key in ("seq", "epoch", "until")):
        return False
    hits = swing.get("hits")
    cutoff = swing.get('cutoff', 0)
    window = swing.get('contactStartMs', 0), swing.get('contactEndMs', 0)
    if (not all(type(value) is int and 0 <= value <= 65535 for value in window)
            or window[0] >= window[1] or not damage_state.validContext(swing.get('damageContext'))
            or any(type(swing.get(key)) is not int or not 0 <= swing[key] <= actors.MAX_ACTORS for key in ('allies', 'enemies'))
            or ('dash' in swing and (not dash_resources.validProfile(swing['dash']) or not swing['dash']['enabled']))):
        return False
    return (type(cutoff) in (int, float) and math.isfinite(cutoff) and cutoff >= 0
            and type(swing.get('groundSoundPlayed', False)) is bool
            and type(hits) is list and all(type(rid) is int and 1 <= rid <= actors.MAX_ACTORS for rid in hits)
            and len(hits) == len(set(hits)))


def cancelSwing(flow, actorId, *, reason=None):
    flow.cancel(hitTimer(actorId))
    data = battle.actorData(flow, actorId)
    if reason == 'interrupted':
        for previous in data.get('contactSwings', []):
            previous['cutoff'] = min(previous.get('cutoff', flow.now), getattr(flow, 'contactAt', flow.now))
    swing = data.pop("swing", None)
    if swing is not None and reason and contact_journal.available(flow):
        swing['closedAt'] = flow.now
        if reason != 'action-changed':
            swing['cutoff'] = getattr(flow, 'contactAt', flow.now)
        previous = data.get('contactSwings', [])
        if len(previous) >= 32:
            raise ValueError('native contact settlement stalled: swing history exhausted')
        data['contactSwings'] = [*previous, swing]
        return
    if swing is not None and reason and not swing.get("hits"):
        detail = swing.get("miss", "expired") if flow.now >= swing["until"] else reason
        flow.log("DEBUG", f"combat-miss attacker={actorId} reason={detail}")


def contactEndMs(intent, *, dash=None, weaponId=None, profile=None):
    if intent == battle.DASH_ATTACK:
        return dash['contactEndMs'] if dash is not None else battle.missingDash()
    if profile is not None:
        return profile['attacks'][str(intent)]['contactEndMs']
    return battle.phaseMs(intent, battle.strikeIndex(intent), weaponId=weaponId)


def contactWindow(swing):
    return (swing.get('contactStartMs', 0), swing['contactEndMs'] if 'contactEndMs' in swing
            else contactEndMs(swing['intent'], dash=swing.get('dash'), weaponId=swing.get('weaponId'),
                              profile=swing.get('weaponProfile')))


def actionChanged(flow, actorId, state):
    cancelSwing(flow, actorId, reason="action-changed")
    data = battle.actorData(flow, actorId)
    action = data.get("action", {})
    chain = battle.attackChain(action.get("intent"))
    strike = battle.strikeIndex(action.get("intent"))
    if (not actors.active(flow, actorId) or "health" not in data or not actors.bots(flow.session) or not battle.enabled(flow)
            or chain is None or action.get("index") != strike or state != chain[strike]):
        return
    dash = action.get('dash') if action['intent'] == battle.DASH_ATTACK else None
    profile = action.get('weaponProfile')
    start = (dash['contactStartMs'] if dash is not None else
             profile['attacks'][str(action['intent'])]['contactStartMs'] if profile is not None else 0)
    weaponId = action.get('weaponId', weapons.equipped(data))
    end = contactEndMs(action['intent'], dash=dash, weaponId=weaponId, profile=profile)
    data["swing"] = {"seq": action["seq"], "epoch": flow.session["instanceStartedAt"],
                     "until": flow.now + end, 'contactStartMs': start, 'contactEndMs': end,
                     "beganAt": flow.now, "intent": action['intent'], "state": state,
                     "hits": [], 'weaponId': weaponId, 'allies': 0, 'enemies': 0,
                     'damageContext': damage_state.capture(data, action, flow.now)}
    if dash is not None:
        data['swing']['dash'] = dict(dash)
    if profile is not None:
        data['swing']['weaponProfile'] = deepcopy(profile)
    flow.later(hitTimer(actorId), start)


def facingCosine(position, target, heading):
    dx, dy = target[0] - position[0], target[1] - position[1]
    length = math.hypot(dx, dy)
    yaw = math.radians(heading)
    return 1.0 if length < 0.01 else (dx * math.cos(yaw) - dy * math.sin(yaw)) / length


def facing(position, target, heading):
    return facingCosine(position, target, heading) >= FACING_COS


def heading(snapshot, actorId):
    value = snapshot.get(actors.positionName(snapshot, actorId) + "Heading")
    return value if type(value) in (int, float) and math.isfinite(value) and -180 <= value <= 180 else None


def contactFailure(flow, snapshot, attacker):
    origin = snapshot[actors.positionName(snapshot, attacker)]
    target = snapshot["player" if actors.positionName(snapshot, attacker) == "bot" else "bot"]
    distance = math.dist(origin[:2], target[:2])
    if distance > REACH:
        return f"range distance={distance:.2f}"
    if abs(origin[2] - target[2]) > HEIGHT_LIMIT:
        return "height"
    if any(heading(snapshot, actorId) is None for actorId in (snapshot.get("sourceId", 2), snapshot.get("targetId", 1))):
        return "heading-unavailable"
    yaw = heading(snapshot, attacker)
    if not facing(origin, target, yaw):
        bearing = math.degrees(math.atan2(-(target[1] - origin[1]), target[0] - origin[0]))
        angle = abs((bearing - yaw + 180) % 360 - 180)
        return f"facing distance={distance:.2f} angle={angle:.1f}"
    return None


def terrainContact(snapshot, attacker, *, targeted):
    index = 0 if actors.positionName(snapshot, attacker) == "player" else 1
    probes = snapshot.get("terrain")
    if type(probes) is not list or len(probes) != 2 or type(probes[index]) is not dict:
        return None
    contact = probes[index].get("target" if targeted else "forward")
    if (type(contact) is not dict or contact.get("ready") is not True
            or type(contact.get("hit")) is not bool):
        return None
    if contact["hit"] and (type(contact.get("material")) is not int
            or not -1 <= contact["material"] <= 7
            or not all(bot.validPosition(contact.get(name)) for name in ("position", "normal", "direction"))
            or not all(.9 <= math.hypot(*contact[name]) <= 1.1 for name in ("normal", "direction"))):
        return None
    return contact


def hitTerrain(flow, attacker, contact, *, intent=None, dash=None, weaponId=None):
    cancelSwing(flow, attacker)
    if attacker != wire.ACTOR_ID:
        battle.actorData(flow, attacker)['forwardMs'] = 0
        flow.cancel(actors.timerName(bot.RELEASE_TIMER, attacker))
        battle.actorData(flow, attacker)["reactAt"] = None
    battle.terrainReaction(flow, attacker, intent=intent, dash=dash, weaponId=weaponId)
    terrainFeedback(flow, attacker, contact, reason='combat-terrain-hit', weaponId=weaponId)
    point = ",".join(f"{v:.2f}" for v in contact["position"])
    flow.log("DEBUG",
        f"combat-terrain attacker={attacker} material={contact['material']} point={point}")


def hitGround(flow, attacker, contact, swing):
    if swing.get('groundSoundPlayed'):
        return False
    terrainFeedback(flow, attacker, contact, reason='combat-ground-hit', weaponId=swing.get('weaponId'))
    swing['groundSoundPlayed'] = True
    return True


def terrainFeedback(flow, attacker, contact, *, reason, weaponId=None):
    flow.send(battle_flow.BATTLE_COMMAND, battle_flow.encode_battle_hit_point_notify(
        hit_pos=tuple(contact["position"]), hit_direction=tuple(contact["direction"]),
        hit_normal=tuple(contact["normal"]), material_id=contact["material"], ignore_hurt=True,
        attacker_mid=attacker, target_mid=0, attacker_weapon_id=(weaponId if weaponId is not None
            else weapons.equipped(actors.data(flow, attacker))),
        target_weapon_id=0, server_time_ms=int(getattr(flow, 'contactAt', flow.now))), reason)


def publishHealth(flow, actorId):
    health = battle.actorData(flow, actorId)["health"]
    if actorId == wire.ACTOR_ID:
        command = battle_flow.BATTLE_COMMAND
        body = battle_flow.encode_battle_data_update_notify(
            result_type=battle_flow.RESULT_DATA_HP, result_data=health)
    else:
        command = attr_flow.ATTR_COMMAND
        body = attr_flow.encode_health_update(entity_rid=actorId, health=health, server_time_ms=flow.now)
    flow.send(command, body, f"combat-health-{actorId}")


def scoreTotals(data):
    return data.get('kills', 0), data.get('deaths', 0), data.get('battlePoints', 0)


def publishScore(flow, actorId, *, previous=None, statMessageId=0):
    current = scoreTotals(battle.actorData(flow, actorId))
    if current == (0, 0, 0):
        return
    previous = current if previous is None else previous
    flow.send(kill_flow.SCORE_COMMAND, kill_flow.encode_score_update_event(
        server_time_ms=flow.now, entity_rid=actorId, previous_kills=previous[0], kills=current[0],
        previous_deaths=previous[1], deaths=current[1],
        previous_points=previous[2], points=current[2], stat_message_id=statMessageId), 'combat-score')


def campKills(flow):
    members = tuple(actors.data(flow, actorId) for actorId in actors.ids(flow.session))
    return {camp: sum(value.get('kills', 0) for value in members if value.get('camp', 1) == camp)
            for camp in (1, 2)}


def publishCampScore(flow):
    # The deathmatch HUD's kill counters are script variables, not combat points.
    totals = campKills(flow)
    flow.send(kill_flow.SCRIPT_COMMAND, kill_flow.encode_camp_kill_update(
        server_time_ms=flow.now, instance_id=1,
        blue_kills=min(kill_flow.MAX_SCORE, totals[1]), red_kills=min(kill_flow.MAX_SCORE, totals[2]),
        kill_target=flow.session.get('killTarget')), 'combat-camp-score')


def parryCheck(attack, defence, snapshot, attacker, defender):
    if defence['index'] != 1 or defence['released']:
        return 'not-holding', None
    lateral = attack["intent"] in (300540, 300550)
    acceptsAttack = (defence['intent'] == battle.SHIELD_GUARD or
                     (defence["intent"] in (300590, 300610) if lateral
                      else defence["intent"] == bot.PARRY_FOR_ATTACK[attack["intent"]]))
    if not acceptsAttack:
        return 'direction-mismatch', None
    origin, target = snapshot[actors.positionName(snapshot, defender)], snapshot[actors.positionName(snapshot, attacker)]
    yaw = heading(snapshot, defender)
    if yaw is None:
        return 'heading-unavailable', None
    cosine = facingCosine(origin, target, yaw)
    return ('success' if cosine >= FACING_COS else 'facing-mismatch'), cosine


def parry(flow, attacker, defender, snapshot, **kwargs):
    from . import guard_combat
    return guard_combat.parry(flow, attacker, defender, snapshot, **kwargs)


def bodyContact(snapshot, attacker, target):
    """Project the torso-center line onto the target's upright body proxy."""
    origin = snapshot[actors.positionName(snapshot, attacker)]
    destination = snapshot[actors.positionName(snapshot, target)]
    delta = tuple(b - a for a, b in zip(origin, destination))
    horizontal = math.hypot(*delta[:2])
    if horizontal < .01:
        # Coincident XY has no incoming side; avoid a vertical render orientation.
        yaw = math.radians(heading(snapshot, attacker))
        direction = (math.cos(yaw), -math.sin(yaw), 0.)
        normal = tuple(-value for value in direction)
        position = (destination[0] + normal[0] * body_collision.RADIUS,
                    destination[1] + normal[1] * body_collision.RADIUS, destination[2] + HIT_HEIGHT)
        return position, direction, normal
    start = (origin[0], origin[1], origin[2] + HIT_HEIGHT)
    normal = (-delta[0] / horizontal, -delta[1] / horizontal, 0.)
    # Negative entry fractions keep overlapping bodies' feedback on the surface.
    fraction = 1 - body_collision.RADIUS / horizontal
    if delta[2] != 0:
        cap = destination[2] + (body_collision.HEIGHT if delta[2] < 0 else 0)
        capFraction = (cap - start[2]) / delta[2]
        if capFraction > fraction:
            fraction = capFraction
            normal = (0., 0., -math.copysign(1., delta[2]))
    position = tuple(start[i] + delta[i] * fraction for i in range(3))
    length = math.hypot(*delta)
    direction = tuple(value / length for value in delta)
    return position, direction, normal


def applyHit(flow, attacker, target, snapshot, *, contact=None, intent=None, weaponId=None, swing=None, damageScale=1,
             guardBreak=False):
    if flow.session.get('roundEnded') or not actors.active(flow, attacker) or not actors.active(flow, target):
        return
    hitPosition, hitDirection, hitNormal = (bodyContact(snapshot, attacker, target) if contact is None
                                          else tuple(tuple(contact[key]) for key in ("position", "direction", "normal")))
    data = battle.actorData(flow, target)
    action = battle.actionState(flow, attacker)
    intent = action['intent'] if intent is None else intent
    weaponId = weapons.equipped(actors.data(flow, attacker)) if weaponId is None else weaponId
    result = damage_state.evaluate(flow, attacker, target, snapshot, swing=swing, contact=contact,
                                   intent=intent, weaponId=weaponId, damageScale=damageScale)
    damage = result.hpDamage
    if damage == 0:
        return
    if swing is not None:
        swing['enemies'] = swing.get('enemies', 0) + 1
    previousHealth = data["health"]
    data["health"] = max(0, data["health"] - damage)
    publishHealth(flow, target)
    position = snapshot[actors.positionName(snapshot, target)]
    flow.send(4, battle_flow.encode_battle_hit_point_notify(
        hit_pos=hitPosition, hit_direction=hitDirection, hit_normal=hitNormal,
        attacker_mid=attacker, target_mid=target, attacker_weapon_id=weaponId,
        target_weapon_id=0, ignore_hurt=not (result.interrupt or guardBreak),
        server_time_ms=int(getattr(flow, 'contactAt', flow.now))), "combat-hit-point")
    if attacker == wire.ACTOR_ID:
        # Retained contacts can arrive after the attacker has advanced to another state.
        sequence = action['seq'] if contact is None else contact.get('seq', action['seq'])
        flow.send(battle_flow.BATTLE_COMMAND, battle_flow.encode_battle_result(
            attacker_rid=attacker, target_rid=target, attacker_seq_no=sequence,
            target_life_state=battle_flow.LIFE_STATE_KILL if data['health'] == 0 else battle_flow.LIFE_STATE_NONE,
            attack_weapon_id=weaponId, is_critical=result.head,
            target_rb_index={'head': 1, 'torso': 2, 'legs': 3}.get((contact or {}).get('region'), 2),
            target_result_type=battle_flow.RESULT_DATA_DECAY_DELTA_HP if result.decay else battle_flow.RESULT_DATA_DELTA_HP,
            target_result_data=data['health'] - previousHealth), 'combat-damage')
    flow.log("DEBUG", f"combat-hit attacker={attacker} target={target} hp={data['health']}")
    at = (contact or {}).get('occurredAt', getattr(flow, 'contactAt', flow.now))
    guard_state.restore(flow, attacker, at)
    damage_state.grant(flow, attacker, 'combo', at)
    if data['health'] == 0:
        damage_state.grant(flow, attacker, 'rage', at)
        if target == wire.ACTOR_ID:
            player_life.die(flow, attacker, position, weaponId=weaponId)
        else:
            bot_life.die(flow, target, attacker, position, weaponId=weaponId)
        return
    bot_roaming.noticeHit(flow, attacker, target, snapshot, damage=damage)
    if not result.interrupt and not guardBreak:
        return
    profile = weapons.profile(data)
    duration = profile.get('hurtMs', HURT_MS) if profile is not None else HURT_MS
    state = (362 if result.knockdown else 360) if guardBreak else (
        hit_reactions.KNOCKDOWN_STATE if result.knockdown else HURT_STATE)
    hit_reactions.hurt(flow, target, attacker, max(duration, result.movementMs), state=state, movementMs=result.movementMs)


def checkHit(flow, attacker):
    if contact_journal.available(flow):
        return  # Native contact batches are settled once, globally in occurrence order.
    data = battle.actorData(flow, attacker)
    swing = data.get("swing")
    action = data.get("action", {})
    strike = battle.strikeIndex(action.get("intent"))
    if (swing is None or not actors.active(flow, attacker) or not battle.enabled(flow) or action.get("index") != strike
            or action.get("seq") != swing["seq"]
            or swing["epoch"] != flow.session.get("instanceStartedAt")):
        cancelSwing(flow, attacker, reason="interrupted")
        return
    raw = actors.world(flow)
    start, end = contactWindow(swing)
    beganAt = swing['until'] - end
    pose = actors.pose(raw, attacker)
    matchingPhase = True
    if swing.get('intent') == battle.DASH_ATTACK and pose is not None and 'seq' in pose:
        matchingPhase = (pose.get('seq') == swing['seq'] and pose.get('state') == swing['state']
                         and type(pose.get('phaseAt')) is int and pose['phaseAt'] >= 0)
        if matchingPhase:
            beganAt = pose['phaseAt']
            swing['until'] = beganAt + end
    contacts = blade_contacts.contacts(raw, attacker, beganAt + start, swing['until']) if matchingPhase else None
    failure = "blade-unavailable" if contacts is None else "blade-clear"
    for contact in contacts or ():
        if blade_contacts.isGround(contact, pose['position']):
            hitGround(flow, attacker, contact, swing)
            continue
        target = contact["targetId"]
        if target == 0:
            hitTerrain(flow, attacker, contact)
            return
        if target not in actors.ids(flow.session) or not actors.active(flow, target) or target in swing.get("hits", []):
            continue
        snapshot = actors.pair(raw, attacker, target)
        if snapshot is None:
            failure = "world-unavailable"
            continue
        if contact['defenceWeaponId']:
            if data.get('camp', 1) == actors.data(flow, target).get('camp', 1):
                continue
        elif damage_state.friendly(flow, attacker, target, swing):
            continue
        if not damage_state.canHit(swing):
            continue
        trace = raw['blades'][str(attacker)]
        contact = {**contact, 'phaseAt': beganAt,
                   'occurredAt': raw['sampledAt'] - trace['spanMs'] * (1 - contact['time'])}
        if contact['defenceWeaponId']:
            if parry(flow, attacker, target, snapshot, contact=contact, weaponId=swing['weaponId'], swing=swing):
                return
            continue
        swing["hits"] = [*swing.get("hits", []), target]
        applyHit(flow, attacker, target, snapshot, contact=contact, intent=swing['intent'], weaponId=swing['weaponId'], swing=swing)
        if flow.session.get('roundEnded'):
            return
    hits = swing.get("hits", [])
    camp = data.get("camp", 1)
    remaining = any(rid not in hits and actors.active(flow, rid)
                    and battle.actorData(flow, rid).get("camp", 1) != camp for rid in actors.ids(flow.session))
    nextCheckAt = min(max(flow.now + CONTACT_MS, beganAt + start), swing["until"])
    if hits and not remaining:
        cancelSwing(flow, attacker)
    elif flow.now < nextCheckAt <= swing["until"]:
        swing["miss"] = failure
        flow.later(hitTimer(attacker), nextCheckAt - flow.now)
    else:
        cancelSwing(flow, attacker, reason=failure)


def worldUpdated(flow):
    if not battle.enabled(flow):
        return
    if contact_journal.available(flow):
        if flow.context['botWorld'].get('instanceStartedAt') != flow.session.get('instanceStartedAt'):
            return
        contact_journal.settle(flow)
        guard_state.update(flow, flow.context['botWorld']['contactJournal']['throughAt'])
        return
    pending = flow.session.get("pending", {})
    swings = sorted((pending.get(hitTimer(rid), flow.now), rid) for rid in actors.ids(flow.session)
                    if battle.actorData(flow, rid).get("swing") is not None)
    for _, actorId in swings:
        # A fresh native frame is damage evidence; the legacy retry deadline is not a hit gate.
        flow.cancel(hitTimer(actorId))
        checkHit(flow, actorId)
        if flow.session.get('roundEnded'):
            return
    guard_state.update(flow, flow.now)


def finishHurt(flow, actorId):
    data = battle.actorData(flow, actorId)
    if "hurtUntil" not in data:
        return
    if not actors.active(flow, actorId) or not battle.enabled(flow) or not battle.botInstanceMatches(flow):
        data.pop("hurtUntil")
        battle.resetAction(flow, actorId)
        return
    if actors.pose(actors.world(flow), actorId) is None:
        flow.later(hurtTimer(actorId), CONTACT_MS)
        return
    data.pop("hurtUntil")
    if not battle.startBufferedAction(flow, actorId):
        battle.resetAction(flow, actorId)
        battle.syncState(flow, battle_flow.ACT_STATE_IDLE, actorId)


def timer(flow, name):
    if hit_reactions.timer(flow, name):
        return True
    for actorId in actors.ids(flow.session):
        if name not in (hitTimer(actorId), hurtTimer(actorId)):
            continue
        if actors.bots(flow.session):
            if name == hitTimer(actorId):
                checkHit(flow, actorId)
            else:
                finishHurt(flow, actorId)
        return True
    return False
