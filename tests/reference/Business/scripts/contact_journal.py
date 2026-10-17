"""Settle retained native contacts by event time, independently of AI and delivery latency."""
import math

from . import actors, battle, blade_contacts, damage_state


def available(flow):
    raw = flow.context.get('botWorld')
    return type(raw) is dict and type(raw.get('contactJournal')) is dict


def swings(data):
    current = data.get('swing')
    return [*data.get('contactSwings', []), *([current] if current is not None else [])]


def valid(event, throughAt):
    if type(event) is not dict:
        return False
    integers = ('actorId', 'targetId', 'entity', 'targetEntity', 'state', 'seq', 'targetState', 'targetSeq',
                'phaseAt', 'targetPhaseAt', 'defenceWeaponId')
    if not all(type(event.get(key)) is int and event[key] >= 0 for key in integers):
        return False
    return (1 <= event['actorId'] <= actors.MAX_ACTORS and 0 <= event['targetId'] <= actors.MAX_ACTORS
            and event['actorId'] != event['targetId'] and 1 <= event['seq'] <= 0xFFFF
            and (event['targetId'] != 0 or event['defenceWeaponId'] == 0)
            and event.get('ready') is True and event.get('hit') is True
            and type(event.get('material')) is int and -1 <= event['material'] <= 7
            and all(type(event.get(key)) in (int, float) and math.isfinite(event[key])
                    for key in ('occurredAt', 'along', 'sourceHeading', 'targetHeading'))
            and 0 <= event['occurredAt'] <= throughAt and 0 <= event['along'] <= 1
            and abs(event['sourceHeading']) <= 180 and abs(event['targetHeading']) <= 180
            and type(event.get('bodyIndex')) is int and -1 <= event['bodyIndex'] <= 127
            and event.get('region') in ('unknown', 'head', 'torso', 'legs')
            and type(event.get('velocityReady')) is bool and type(event.get('airborne')) is bool
            and type(event.get('targetAirborne')) is bool
            and all(actors.validPosition(event.get(key))
                    for key in ('sourcePosition', 'targetPosition', 'position', 'normal', 'direction',
                                'sourceVelocity', 'targetVelocity', 'weaponVelocity'))
            and all(.9 <= math.hypot(*event[key]) <= 1.1 for key in ('normal', 'direction')))


def pair(raw, event):
    return dict(ready=True, sampledAt=int(event['occurredAt']), instanceStartedAt=raw['instanceStartedAt'],
                sourceId=event['actorId'], targetId=event['targetId'], bot=event['sourcePosition'],
                player=event['targetPosition'], botHeading=event['sourceHeading'], playerHeading=event['targetHeading'])


def settle(flow):
    from . import combat
    if not battle.enabled(flow):
        return
    raw = flow.context['botWorld']
    journal = raw['contactJournal']
    if journal.get('damageEvidenceVersion') != 2:
        raise ValueError('damage evidence contract changed; restart Runtime before upgrading')
    epoch = flow.session.get('instanceStartedAt')
    if raw.get('instanceStartedAt') != epoch:
        return
    throughAt, batchId = journal.get('throughAt'), journal.get('batchId')
    if (type(throughAt) not in (int, float) or not math.isfinite(throughAt) or not 0 <= throughAt <= flow.now
            or type(batchId) is not int or batchId < 0
            or type(journal.get('events')) is not list or len(journal['events']) > 8192
            or not all(valid(event, throughAt) for event in journal['events'])):
        raise ValueError('invalid native contact journal')
    cursor = [epoch, batchId]
    if flow.session.get('contactCursor') == cursor:
        return
    flow.session['contactCursor'] = cursor
    if journal.get('dropped', 0):
        flow.log("WARN", f"combat-contact-overflow dropped={journal['dropped']} batch={batchId}")
    actorIds = set(actors.ids(flow.session))
    settled, unmatched = 0, 0
    events = sorted(journal['events'], key=lambda item: (
        item['occurredAt'], item['along'], item['actorId'], item['targetId'], not item['defenceWeaponId']))
    for event in events:
        if flow.session.get('roundEnded'):
            break
        attacker, target, occurredAt = event['actorId'], event['targetId'], event['occurredAt']
        if attacker not in actorIds or not actors.active(flow, attacker):
            continue
        data = actors.data(flow, attacker)
        swing = next((value for value in swings(data) if value['seq'] == event['seq']
                      and value.get('state') == event['state'] and value['epoch'] == epoch), None)
        if swing is None:
            unmatched += 1
            continue
        intent = swing['intent']
        contactStart, contactEnd = combat.contactWindow(swing)
        source = raw.get('actors', {}).get(str(attacker), {})
        if (swing.get('stopped') or event['entity'] != source.get('entity')
                or occurredAt < data.get('contactLifeAt', 0)
                or not event['phaseAt'] + contactStart <= occurredAt <= event['phaseAt'] + contactEnd
                or occurredAt > swing.get('cutoff', math.inf)):
            continue
        flow.contactAt = occurredAt
        try:
            if blade_contacts.isGround(event, event['sourcePosition']):
                settled += combat.hitGround(flow, attacker, event, swing)
                continue
            swing['nativeUntil'] = event['phaseAt'] + contactEnd
            if target == 0:
                swing['stopped'] = True
                combat.hitTerrain(flow, attacker, event, intent=intent, dash=swing.get('dash'),
                                  weaponId=swing.get('weaponId'))
                settled += 1
                continue
            if target not in actorIds or not actors.active(flow, target) or target in swing['hits']:
                continue
            defender = actors.data(flow, target)
            targetPose = raw.get('actors', {}).get(str(target), {})
            if (event['targetEntity'] != targetPose.get('entity')
                    or occurredAt < defender.get('contactLifeAt', 0)):
                continue
            if event['defenceWeaponId']:
                if data.get('camp', 1) == defender.get('camp', 1):
                    continue
            elif damage_state.friendly(flow, attacker, target, swing):
                continue
            if not damage_state.canHit(swing):
                continue
            snapshot = pair(raw, event)
            if event['defenceWeaponId']:
                if combat.parry(flow, attacker, target, snapshot, contact=event,
                                attackIntent=intent, dash=swing.get('dash'),
                                weaponId=swing.get('weaponId'), swing=swing):
                    swing['stopped'] = True
            else:
                swing['hits'] = [*swing['hits'], target]
                combat.applyHit(flow, attacker, target, snapshot, contact=event, intent=intent,
                                weaponId=swing.get('weaponId'), swing=swing)
            settled += 1
        finally:
            del flow.contactAt
    for rid in actorIds:
        data = actors.data(flow, rid)
        flow.cancel(combat.hitTimer(rid))
        pose = raw.get('actors', {}).get(str(rid), {})
        pending = [value for value in data.get('contactSwings', [])
                   if value['epoch'] == epoch and (throughAt < value['closedAt'] or pose.get('seq') == value['seq'])]
        for value in data.get('contactSwings', []):
            if value not in pending and not value['hits'] and not value.get('stopped'):
                reason = 'interrupted' if 'cutoff' in value else 'native-no-contact'
                flow.log("DEBUG", f'combat-miss attacker={rid} reason={reason}')
        if pending:
            data['contactSwings'] = pending
        else:
            data.pop('contactSwings', None)
    if settled or unmatched:
        delay = max((flow.now - event['occurredAt'] for event in events), default=0)
        flow.log("DEBUG", f'combat-contact batch={batchId} frames={journal.get("frames", 0)} '
                                  f'events={len(events)} settled={settled} unmatched={unmatched} deliveryMs={delay:.1f}')
