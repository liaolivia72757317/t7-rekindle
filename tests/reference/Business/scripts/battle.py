"""Melee action phases shared by the local player and computer player."""
import struct
from copy import deepcopy

from . import actors, contracts as wire, controls, dash_resources, weapons
from .codec import battle_flow

ACTION_TIMER = "battle-action-phase"
BLOCK_PHASE_MS = 0
RIGHT_MOUSE_MASK = 1 << 8
ATTACK_RELEASE = 300020
BLOCK_RELEASE = 300620
SHIELD_GUARD, SHIELD_RELEASE = 300130, 300140
DASH_ATTACK = 700250
DASH_CHAIN = (438, 439)
ATTACK_CHAINS = {
    300560: (235, 237, 238, 239),  # up
    300550: (241, 243, 244, 245),  # right
    300570: (247, 249, 250, 251),  # down
    300540: (253, 255, 256, 257),  # left
}
BLOCK_CHAINS = {
    # Prepare -> ParryHold. Matching key-up ends the action without ParryRelease.
    300580: (305, 223),  # up
    300590: (308, 224),  # right
    300600: (307, 225),  # down
    300610: (306, 226),  # left
}
GUARD_CHAINS = {**BLOCK_CHAINS, SHIELD_GUARD: (95, 309)}
PARRIED_STATES = {300560: 240, 300550: 246, 300570: 252, 300540: 258, DASH_ATTACK: 443}
PARRY_SUCCESS_STATES = {300580: 231, 300590: 232, 300600: 233, 300610: 234}
TERRAIN_REBOUND_STATES = {300560: 466, 300550: 468, 300570: 467, 300540: 469, DASH_ATTACK: 443}
# s_battle_param_cli: group 2, weapon category 32, directions 1/4/2/3, mode 2.
# The held phase has no deadline. These are base timings, not an attack-speed formula.
WEAPON_ATTACK_PHASES_MS = {
    1030411: {
        300560: (360, 0, 640, 500),
        300550: (360, 0, 640, 500),
        300570: (400, 0, 700, 400),
        300540: (360, 0, 640, 530),
    },
    # s_battle_param_cli: group 2, category 104, mode 2 (native default fists).
    weapons.FIST_ID: {
        300560: (369, 0, 632, 369),
        300550: (369, 0, 632, 264),
        300570: (369, 0, 632, 264),
        300540: (369, 0, 632, 264),
    },
}
FIST_REACTIONS_MS = {300560: (520, 420), 300550: (450, 350), 300570: (560, 460), 300540: (450, 350)}


def phaseMs(intent, index, *, dash=None, weaponId=None, profile=None):
    if intent == DASH_ATTACK:
        if index == 0:
            return 0
        if dash is None:
            missingDash()
        return (0, dash['durationMs'])[index]
    if intent == SHIELD_GUARD:
        return profile['guard']['prepareMs'] if index == 0 else 0
    if intent not in ATTACK_CHAINS:
        return BLOCK_PHASE_MS
    if profile is not None:
        return profile['attacks'][str(intent)]['phases'][index]
    timings = WEAPON_ATTACK_PHASES_MS.get(wire.WEAPON_ID if weaponId is None else weaponId)
    if timings is None:
        raise ValueError("weapon has no verified attack timing")
    return timings[intent][index]


def attackChain(intent):
    return DASH_CHAIN if intent == DASH_ATTACK else ATTACK_CHAINS.get(intent)


def missingDash():
    raise ValueError('dash timing requires the action profile; restart Runtime after upgrading')


def dashProfile(flow, actorId=wire.ACTOR_ID):
    selected = weapons.profile(actorData(flow, actorId))
    value = selected['dashAttack'] if selected is not None else flow.context.get('dashAttack')
    if selected is not None and not value:
        return None
    if value is None:
        if not flow.session.get('dashUnavailableLogged'):
            flow.log('ERROR', 'dash-disabled: Runtime did not supply weapon resources; restart Runtime')
            flow.session['dashUnavailableLogged'] = True
        return None
    if not dash_resources.validProfile(value):
        raise ValueError('invalid Runtime dash profile')
    if value['enabled'] and value['weaponId'] != weapons.equipped(actorData(flow, actorId)):
        raise ValueError('dash profile does not match equipped weapon; restart Runtime')
    return dict(value) if value['enabled'] else None


def reactionMs(intent, *, dash=None, weaponId=None, terrain=False, profile=None):
    if intent == DASH_ATTACK:
        if dash is None:
            missingDash()
        return dash['reboundMs']
    if profile is not None:
        return profile['attacks'][str(intent)]['rebound' if terrain else 'parried']
    if weaponId == weapons.FIST_ID:
        return FIST_REACTIONS_MS[intent][int(terrain)]
    return phaseMs(intent, -1, weaponId=weaponId)


def validAction(data):
    action = data.get('action', {})
    return (type(action) is dict and weapons.validState(action)
            and all(type(action.get(key, 0)) is int and action.get(key, 0) >= 0 for key in ('heldAt', 'heldMs'))
            and type(action.get('durationMs', 0)) is int and 0 <= action.get('durationMs', 0) <= 65535
            and ('dash' not in action or (
        dash_resources.validProfile(action['dash']) and action['dash']['enabled'])))


def strikeIndex(intent):
    return 1 if intent == DASH_ATTACK else 2


def actorData(flow, actorId):
    return actors.data(flow, actorId)


def actionTimer(actorId):
    return ACTION_TIMER if actorId == wire.ACTOR_ID else actors.timerName("bot-action-phase", actorId)


def actionState(flow, actorId=wire.ACTOR_ID):
    return actorData(flow, actorId).setdefault("action", {
        "intent": 0, "index": -1, "seq": 1, "released": False,
    })


def enabled(flow):
    return (flow.session["role"] == "instance" and not flow.session.get("leaving")
            and flow.session.get("controlBaseline") == wire.BASELINE_ID
            and controls.groundEnabled(flow))


def botInstanceMatches(flow):
    snapshot = flow.context.get("botWorld")
    return (type(snapshot) is dict
            and snapshot.get("instanceStartedAt") == flow.session.get("instanceStartedAt"))


def syncState(flow, state, actorId=wire.ACTOR_ID, *, targetId=None, durationMs=None):
    from . import combat
    startedAt = flow.session.get("instanceStartedAt")
    if type(startedAt) is not int:
        raise ValueError("battle animation requires an instance start time")
    action = actionState(flow, actorId)
    sequence = action["seq"] % 0xFFFF + 1
    # Havok enforcedDuration consumes the full state duration, not elapsed time.
    dash = action.get('dash')
    duration = phaseMs(DASH_ATTACK, 1, dash=dash) if state == DASH_CHAIN[1] else 0
    if state == 443 and action['intent'] == DASH_ATTACK:
        duration = reactionMs(DASH_ATTACK, dash=dash)
    if state == weapons.CHANGE_STATE:
        duration = weapons.changeMs(actorData(flow, actorId))
    profile = action.get('weaponProfile')
    if profile and action['intent'] == SHIELD_GUARD:
        duration = profile['guard']['prepareMs'] if state == 95 else profile['guard']['successMs'] if state == 111 else 0
    if profile and action['intent'] in ATTACK_CHAINS:
        intent = action['intent']
        if state in ATTACK_CHAINS[intent]:
            duration = phaseMs(intent, ATTACK_CHAINS[intent].index(state), profile=profile)
        elif state in (PARRIED_STATES[intent], TERRAIN_REBOUND_STATES[intent]):
            duration = reactionMs(intent, profile=profile, terrain=state == TERRAIN_REBOUND_STATES[intent])
    if durationMs is not None:
        if type(durationMs) is not int or not 0 <= durationMs <= 65535:
            raise ValueError('invalid battle state duration')
        duration = durationMs
    fields = dict(instance_id=actorId, seq_no=sequence, state=state,
                  state_change_ms=flow.now - startedAt, state_time_ms=duration)
    body = (battle_flow.encode_battle_state_sync_simple(**fields) if targetId is None
            else battle_flow.encode_battle_state_sync(**fields, target_rid=targetId))
    action["seq"] = sequence
    action.update(state=state, changedAt=flow.now, durationMs=duration)
    if actorId != wire.ACTOR_ID:
        if targetId is None:
            action.pop("targetId", None)
        else:
            action["targetId"] = targetId
    flow.send(battle_flow.BATTLE_COMMAND, body,
              ("battle-model-state-" if actorId == wire.ACTOR_ID else "bot-model-state-") + str(state))
    if actorId == wire.ACTOR_ID:
        flow.log('DEBUG', f'battle-state actor={actorId} state={state} seq={sequence} phaseAt={flow.now} epoch={startedAt}')
    combat.actionChanged(flow, actorId, state)


def resetAction(flow, actorId=wire.ACTOR_ID, *, preserveBuffered=False):
    from . import combat
    combat.cancelSwing(flow, actorId, reason="interrupted")
    flow.cancel(actionTimer(actorId))
    data = actorData(flow, actorId)
    if "action" in data:
        data["action"].update(intent=0, index=-1, released=False)
        if not preserveBuffered or data["action"].get("buffered", {}).get("released", True):
            data["action"].pop("buffered", None)
        data["action"].pop("reaction", None)
        data["action"].pop("dash", None)
        data['action'].pop('weaponChange', None)
        data['action'].pop('weaponId', None)
        data['action'].pop('weaponProfile', None)
        data['action'].pop('heldAt', None)
        data['action'].pop('heldMs', None)


def startAction(flow, intent, released=False, *, actorId=wire.ACTOR_ID):
    data = actorData(flow, actorId)
    if (not actors.active(flow, actorId) or "hurtUntil" in data or data.get("action", {}).get("reaction")
            or data.get('action', {}).get('weaponChange')):
        return
    weaponId = weapons.equipped(data)
    profile = weapons.profile(data)
    if intent == SHIELD_GUARD and not data.get('offhandId'):
        return
    if intent == DASH_ATTACK and weaponId == weapons.FIST_ID:
        return
    dash = dashProfile(flow, actorId) if intent == DASH_ATTACK else None
    if intent == DASH_ATTACK and dash is None:
        return
    index = strikeIndex(intent) if intent == DASH_ATTACK else 0
    delay = phaseMs(intent, index, dash=dash, weaponId=weaponId, profile=profile)
    chain = attackChain(intent) or GUARD_CHAINS[intent]
    action = actionState(flow, actorId)
    action.update(intent=intent, index=index, released=released or intent == DASH_ATTACK)
    action.pop('heldAt', None)
    action['heldMs'] = 0
    action['weaponId'] = weaponId
    if profile is not None:
        action['weaponProfile'] = deepcopy(profile)
    action.pop('dash', None)
    if dash is not None:
        action['dash'] = dash
    action.pop("buffered", None)
    syncState(flow, chain[index], actorId)
    flow.later(actionTimer(actorId), delay)


def startBufferedAction(flow, actorId=wire.ACTOR_ID):
    buffered = actionState(flow, actorId).get("buffered")
    if buffered is None or buffered["released"]:
        return False
    startAction(flow, buffered["intent"], actorId=actorId)
    return True


def parryReaction(flow, actorId, targetId, *, success, intent=None, dash=None, weaponId=None, minimumMs=0):
    action = actionState(flow, actorId)
    if success and action.get('reaction') == 'parry-success':
        remainingMs = flow.session.get('pending', {}).get(actionTimer(actorId), flow.now) - flow.now
        minimumMs = max(minimumMs, remainingMs)
    weaponId = weapons.equipped(actorData(flow, actorId)) if weaponId is None else weaponId
    intent = action['intent'] if intent is None else intent
    dash = action.get('dash') if dash is None else dash
    profile = weapons.profile(actorData(flow, actorId), weaponId)
    resetAction(flow, actorId, preserveBuffered=not success)
    action.update(intent=intent, released=not success,
                  reaction="parry-success" if success else "parried", weaponId=weaponId)
    if profile is not None:
        action['weaponProfile'] = deepcopy(profile)
    if intent == DASH_ATTACK:
        if dash is None:
            missingDash()
        action['dash'] = dict(dash)
    state = (111 if intent == SHIELD_GUARD else PARRY_SUCCESS_STATES[intent]) if success else PARRIED_STATES[intent]
    successMs = profile['guard']['successMs'] if intent == SHIELD_GUARD else BLOCK_PHASE_MS
    duration = max(successMs, minimumMs) if success else reactionMs(intent, dash=dash, weaponId=weaponId, profile=profile)
    syncState(flow, state, actorId, targetId=targetId, durationMs=duration)
    flow.later(actionTimer(actorId), duration)


def finishReaction(flow, actorId):
    action = actionState(flow, actorId)
    if not botInstanceMatches(flow):
        resetAction(flow, actorId)
    elif action["reaction"] == "parry-success" and not action["released"]:
        action.pop("reaction")
        action["index"] = 1
        syncState(flow, GUARD_CHAINS[action["intent"]][1], actorId)
    else:
        action.pop("reaction")
        if not startBufferedAction(flow, actorId):
            syncState(flow, battle_flow.ACT_STATE_IDLE, actorId)
            resetAction(flow, actorId)


def terrainReaction(flow, actorId, *, intent=None, dash=None, weaponId=None):
    action = actionState(flow, actorId)
    weaponId = weapons.equipped(actorData(flow, actorId)) if weaponId is None else weaponId
    intent = action['intent'] if intent is None else intent
    dash = action.get('dash') if dash is None else dash
    profile = weapons.profile(actorData(flow, actorId), weaponId)
    resetAction(flow, actorId, preserveBuffered=True)
    action.update(intent=intent, released=True, reaction="terrain-rebound", weaponId=weaponId)
    if profile is not None:
        action['weaponProfile'] = deepcopy(profile)
    if intent == DASH_ATTACK:
        if dash is None:
            missingDash()
        action['dash'] = dict(dash)
    syncState(flow, TERRAIN_REBOUND_STATES[intent], actorId, targetId=0)
    flow.later(actionTimer(actorId), reactionMs(intent, dash=dash, weaponId=weaponId, terrain=True, profile=profile))


def bufferActionInput(action, intent, *, locked=False):
    chain = attackChain(action["intent"]) or GUARD_CHAINS.get(action["intent"])
    if not locked:
        if chain is None or action["index"] < 0 or action["intent"] in GUARD_CHAINS:
            return False
    if intent in ATTACK_CHAINS or intent in GUARD_CHAINS:
        action["buffered"] = {"intent": intent, "released": False}
        return True
    buffered = action.get("buffered")
    if buffered is not None:
        if ((intent == ATTACK_RELEASE and buffered["intent"] in ATTACK_CHAINS)
                or (intent == BLOCK_RELEASE and buffered["intent"] in GUARD_CHAINS)):
            action["buffered"] = {**buffered, "released": True}
    # Key-up must also release the current attack during prepare or hold.
    return False


def timer(flow, name):
    actorId = next((rid for rid in actors.ids(flow.session) if name == actionTimer(rid)), None)
    if actorId is None:
        return False
    if not actors.active(flow, actorId):
        return True
    if actorId != wire.ACTOR_ID and not botInstanceMatches(flow):
        resetAction(flow, actorId)
        return True
    action = actorData(flow, actorId).get("action")
    if not enabled(flow) or action is None:
        resetAction(flow, actorId)
        return True
    if action.get('weaponChange'):
        syncState(flow, battle_flow.ACT_STATE_IDLE, actorId)
        resetAction(flow, actorId)
        return True
    if action.get("reaction"):
        finishReaction(flow, actorId)
        return True
    if action["index"] < 0:
        resetAction(flow, actorId)
        return True
    intent = action["intent"]
    chain = attackChain(intent) or GUARD_CHAINS.get(intent)
    if chain is None:
        raise ValueError("unknown active battle animation")
    nextIndex = action["index"] + 1
    if intent in ATTACK_CHAINS and action["released"] and nextIndex == 1:
        nextIndex = 2
    holdIndex = 1
    if not action["released"] and nextIndex > holdIndex:
        return True
    if intent in ATTACK_CHAINS and nextIndex == len(chain) - 1 and startBufferedAction(flow, actorId):
        return True
    if nextIndex >= len(chain):
        if not startBufferedAction(flow, actorId):
            syncState(flow, battle_flow.ACT_STATE_IDLE, actorId)
            resetAction(flow, actorId)
        return True
    action["index"] = nextIndex
    if intent in ATTACK_CHAINS and nextIndex == 1:
        action['heldAt'] = flow.now
    syncState(flow, chain[nextIndex], actorId)
    if action["released"] or nextIndex < holdIndex:
        flow.later(name, phaseMs(intent, nextIndex, dash=action.get('dash'), weaponId=action.get('weaponId'),
                                profile=action.get('weaponProfile')))
    return True


def message(flow, command, selector, body):
    if (command != battle_flow.BATTLE_COMMAND or selector != 1
            or flow.session["role"] != "instance"):
        return False
    if (flow.session.get("leaving")
            or flow.session.get("controlBaseline") != wire.BASELINE_ID):
        resetAction(flow)
        return False
    wire.exact(body, 11, "battle action")
    keys, intent, autoParry = struct.unpack_from(">Iib", body, 2)
    if autoParry not in (0, 1):
        raise ValueError("battle auto-parry flag must be 0 or 1")
    if intent == SHIELD_RELEASE:
        intent = BLOCK_RELEASE
    if intent == SHIELD_GUARD and not flow.session.get('offhandId'):
        return True
    chain = attackChain(intent) or GUARD_CHAINS.get(intent)
    if chain is None and intent not in (ATTACK_RELEASE, BLOCK_RELEASE):
        return False

    traceRelease = False

    def report(result, reason):
        if intent in GUARD_CHAINS or traceRelease:
            flow.log('DEBUG', f'battle-input intent={intent} at={flow.now} result={result} reason={reason}')
        return True

    if not enabled(flow) or not actors.active(flow, wire.ACTOR_ID):
        resetAction(flow)
        return report('ignored', 'inactive')
    # Native attack selection requires RMB up; its release can itself select the next attack.
    if intent in ATTACK_CHAINS and keys & RIGHT_MOUSE_MASK:
        return report('ignored', 'defence-priority')
    if chain is not None:
        phaseMs(intent, 0, weaponId=weapons.equipped(flow.session), profile=weapons.profile(flow.session))
    action = actionState(flow)
    buffered = action.get('buffered', {})
    traceRelease = intent == BLOCK_RELEASE and (
        buffered.get('intent') in GUARD_CHAINS and not buffered.get('released', True)
        or action['intent'] in GUARD_CHAINS and not action['released']
        and (action['index'] >= 0 or action.get('reaction') == 'parry-success'))
    if action.get('weaponChange'):
        return report('ignored', 'weapon-change')
    if "hurtUntil" in flow.session:
        buffered = bufferActionInput(action, intent, locked=True)
        return report('buffered' if buffered else 'released', 'hurt')
    if intent == DASH_ATTACK:
        # Movement keys use a separate cache; the native hook checks them before arming RUN.
        if (keys & (1 << 26)
                and keys & ((1 << 21) | (1 << 7))
                and action["index"] < 0 and not action.get("reaction")):
            startAction(flow, intent)
        return True
    if action.get("reaction"):
        buffered = False
        if action["reaction"] in ("parried", "terrain-rebound"):
            buffered = bufferActionInput(action, intent, locked=True)
        elif action['reaction'] == 'parry-success':
            if intent in ATTACK_CHAINS:
                action['released'] = True
                flow.session['defenceInterruptedAt'] = flow.now
                buffered = bufferActionInput(action, intent, locked=True)
            elif action['released'] or action.get('buffered'):
                buffered = bufferActionInput(action, intent, locked=True)
        releaseAction(flow, intent)
        return report('buffered' if buffered else 'released' if intent == BLOCK_RELEASE else 'ignored', action['reaction'])
    interruptsAttack = (intent in GUARD_CHAINS and action["intent"] in ATTACK_CHAINS
                        and action["index"] in (0, 1))
    interruptsParry = (intent in ATTACK_CHAINS and action["intent"] in GUARD_CHAINS
                       and action["index"] in (0, 1))
    interruptsRecovery = (action["intent"] in ATTACK_CHAINS and action["index"] == 3
                          and (intent in ATTACK_CHAINS or intent in GUARD_CHAINS))
    if interruptsAttack or interruptsRecovery or (interruptsParry and not action["released"]):
        if interruptsParry:
            flow.session['defenceInterruptedAt'] = flow.now
        resetAction(flow)
        startAction(flow, intent)
        return report('accepted', 'interrupt')
    if bufferActionInput(action, intent):
        return report('buffered', 'attack-active')
    if chain is not None:
        if action["index"] >= 0:
            return report('ignored', 'action-active')
        startAction(flow, intent)
        return report('accepted', 'idle')
    releaseAction(flow, intent)
    return report('released', 'key-up')


def releaseAction(flow, intent, actorId=wire.ACTOR_ID):
    action = actionState(flow, actorId)
    if action.get("reaction"):
        if action["reaction"] == "parry-success" and intent == BLOCK_RELEASE:
            action["released"] = True
            actorData(flow, actorId)['defenceInterruptedAt'] = flow.now
        return True
    expectedRelease = ATTACK_RELEASE if action["intent"] in ATTACK_CHAINS else BLOCK_RELEASE
    if action["index"] < 0 or action["released"] or intent != expectedRelease:
        return True
    if intent == ATTACK_RELEASE and action["index"] == 0:
        action["released"] = True
        return True
    flow.cancel(actionTimer(actorId))
    if intent == BLOCK_RELEASE:
        actorData(flow, actorId)['defenceInterruptedAt'] = flow.now
        if not startBufferedAction(flow, actorId):
            syncState(flow, battle_flow.ACT_STATE_IDLE, actorId)
            resetAction(flow, actorId)
        return True
    action.update(index=2, released=True, heldMs=max(0, flow.now - action.get('heldAt', flow.now)))
    chain = ATTACK_CHAINS[action["intent"]]
    syncState(flow, chain[2], actorId)
    flow.later(actionTimer(actorId), phaseMs(action["intent"], 2, weaponId=action.get('weaponId'),
                                            profile=action.get('weaponProfile')))
    return True
