"""Small, tick-driven melee behaviour tree; persistent memory stays in the session."""
from dataclasses import dataclass
import math

import py_trees

from . import actors, battle, bot_profiles, contracts as wire

OBSERVATION_MS = 150
COUNTER_MIN_REMAINING_MS = 100
ATTACK_ORDER = tuple(battle.ATTACK_CHAINS)
# A preferred animation, not the lateral contact rule.
PARRY_FOR_ATTACK = {300560: 300580, 300550: 300610, 300570: 300600, 300540: 300590,
                    battle.DASH_ATTACK: 300600}


@dataclass(frozen=True)
class Decision:
    kind: str
    reason: str
    intent: int = 0
    seed: int = 0


class ChoiceNode(py_trees.behaviour.Behaviour):
    def __init__(self, name, evaluate):
        super().__init__(name)
        self.evaluate = evaluate
        self.decision = None

    def update(self):
        self.decision = self.evaluate()
        return py_trees.common.Status.FAILURE if self.decision is None else py_trees.common.Status.SUCCESS


def validState(value):
    if not bot_profiles.validState(value):
        return False
    if "decisionSeed" in value and (type(value["decisionSeed"]) is not int
                                     or not 0 <= value["decisionSeed"] <= 0xFFFFFFFF):
        return False
    if "lastAttack" in value and (type(value["lastAttack"]) is not int or value["lastAttack"] not in ATTACK_ORDER):
        return False
    observed = value.get("observedPlayer")
    return observed is None or (
        type(observed) is dict and type(observed.get("intent")) is int
        and type(observed.get("index")) is int and -1 <= observed["index"] <= 3
        and type(observed.get("seq")) is int and 0 <= observed["seq"] <= 65535
        and observed.get("reaction") in ("", "parried", "parry-success", "terrain-rebound")
        and all(type(observed.get(key)) is int and observed[key] >= 0 for key in ("seenAt", "phaseSeenAt"))
        and observed["seenAt"] <= observed["phaseSeenAt"])


def observe(flow, actorId=wire.BOT_ID):
    value = actors.data(flow, actorId)
    action = actors.data(flow, actors.data(flow, actorId).get("opponentId", wire.ACTOR_ID)).get("action", {})
    current = {key: action.get(key, default) for key, default in
               (("intent", 0), ("index", -1), ("seq", 0), ("reaction", ""))}
    previous = value.get("observedPlayer")
    if previous and all(previous[key] == current[key] for key in current):
        return
    continues = (previous and previous["intent"] == current["intent"]
                 and 0 <= previous["index"] < current["index"]
                 and previous["seq"] != current["seq"] and not current["reaction"])
    value["observedPlayer"] = {**current, "phaseSeenAt": flow.now,
                               "seenAt": previous["seenAt"] if continues else flow.now}


def phaseRemaining(flow, actorId=wire.BOT_ID):
    due = flow.session.get("pending", {}).get(battle.actionTimer(actors.data(flow, actorId).get("opponentId", wire.ACTOR_ID)))
    return max(0, due - flow.now) if type(due) is int else None


def observedFor(flow, actorId=wire.BOT_ID, *, phase=False):
    observed = actors.data(flow, actorId).get("observedPlayer", {})
    return flow.now - observed.get("phaseSeenAt" if phase else "seenAt", flow.now)


def clearContact(flow, snapshot, actorId):
    from . import combat
    if combat.contactFailure(flow, snapshot, actorId) is not None:
        return False
    contact = combat.terrainContact(snapshot, actorId, targeted=True)
    return contact is not None and not contact["hit"]


def attackChoice(flow, snapshot, reason, actorId=wire.BOT_ID):
    from . import combat
    value = actors.data(flow, actorId)
    player = actors.data(flow, actors.data(flow, actorId).get("opponentId", wire.ACTOR_ID)).get("action", {})
    blocked = set()
    if (observedFor(flow, actorId) >= bot_profiles.get(value).observationMs and player.get("index", -1) in (0, 1)
            and player.get("intent") in battle.GUARD_CHAINS and not player.get("released", False)
            and combat.facing(snapshot["player"], snapshot["bot"], snapshot["playerHeading"])):
        blocked = ({300540, 300550} if player["intent"] in (300590, 300610)
                   else {300560} if player["intent"] == 300580 else {300570})
    dx, dy = (snapshot["player"][i] - snapshot["bot"][i] for i in (0, 1))
    angle = (math.degrees(math.atan2(-dy, dx)) - snapshot["botHeading"] + 180) % 360 - 180
    height = snapshot["player"][2] - snapshot["bot"][2]
    seed = value.get("decisionSeed", (flow.session["instanceStartedAt"] ^ value["camp"] ^ actorId) & 0xFFFFFFFF)
    scores = []
    for intent in ATTACK_ORDER:
        seed = (1664525 * seed + 1013904223) & 0xFFFFFFFF
        score = 100 + (seed >> 27)
        if (angle > 15 and intent == 300550) or (angle < -15 and intent == 300540):
            score += 15
        if (height > .25 and intent == 300560) or (height < -.25 and intent == 300570):
            score += 8
        if math.hypot(dx, dy) > 1.7:
            score += max(0, 400 - battle.phaseMs(intent, 0)) // 5
        score -= 64 if intent == value.get("lastAttack") else 0
        score -= 128 if intent in blocked else 0
        scores.append((score, intent))
    intent = max(scores, key=lambda item: item[0])[1]
    return Decision("attack", reason, intent, seed)


def defend(flow, snapshot, actorId=wire.BOT_ID):
    from . import combat
    settings = bot_profiles.get(actors.data(flow, actorId))
    if not settings.canDefend:
        return None
    player = actors.data(flow, actors.data(flow, actorId).get("opponentId", wire.ACTOR_ID)).get("action", {})
    intent, index = player.get("intent"), player.get("index", -1)
    if (intent not in PARRY_FOR_ATTACK or not 0 <= index <= battle.strikeIndex(intent)
            or player.get("reaction") or not clearContact(flow, snapshot, snapshot.get("targetId", wire.ACTOR_ID))
            or not combat.facing(snapshot["bot"], snapshot["player"], snapshot["botHeading"])):
        return None
    if observedFor(flow, actorId) < settings.observationMs:
        return Decision("wait", "observe-threat")
    remaining = phaseRemaining(flow, actorId)
    if index == battle.strikeIndex(intent) and (
            remaining is None or remaining <= battle.BLOCK_PHASE_MS):
        return Decision("wait", "late-threat")
    return Decision("parry", "parry", PARRY_FOR_ATTACK[intent])


def counter(flow, snapshot, actorId=wire.BOT_ID):
    player = actors.data(flow, actors.data(flow, actorId).get("opponentId", wire.ACTOR_ID)).get("action", {})
    chain = battle.attackChain(player.get("intent"))
    recovering = ((chain is not None and player.get("index") == len(chain) - 1)
                  or player.get("reaction") in ("parried", "terrain-rebound"))
    remaining = phaseRemaining(flow, actorId)
    if (recovering and observedFor(flow, actorId, phase=True) >= bot_profiles.get(actors.data(flow, actorId)).counterMs
            and remaining is not None and remaining >= COUNTER_MIN_REMAINING_MS
            and clearContact(flow, snapshot, actorId)):
        return attackChoice(flow, snapshot, "counterattack", actorId)
    return None


def pressure(flow, snapshot, actorId):
    value = actors.data(flow, actorId)
    if value.get('profile') != 'aggressive' or value.get('reactAt') is None or flow.now < value['reactAt']:
        return None
    target = actors.data(flow, value.get('opponentId', wire.ACTOR_ID)).get('action', {})
    if (target.get('intent') not in ATTACK_ORDER or target.get('index') != 1 or target.get('reaction')
            or observedFor(flow, actorId) < bot_profiles.get(value).observationMs
            or not clearContact(flow, snapshot, actorId)):
        return None
    return attackChoice(flow, snapshot, 'pressure', actorId)


def normalAttack(flow, snapshot, actorId=wire.BOT_ID):
    readyAt = actors.data(flow, actorId).get("reactAt")
    if readyAt is None or flow.now < readyAt:
        return Decision("wait", "ready")
    if not clearContact(flow, snapshot, actorId):
        return Decision("wait", "contact-unavailable")
    return attackChoice(flow, snapshot, "attack", actorId)


def choose(flow, snapshot, actorId=wire.BOT_ID):
    value = actors.data(flow, actorId)
    action = value.get("action", {})
    locked = "hurtUntil" in value or action.get("reaction") or action.get("index", -1) >= 0
    nodes = [
        ChoiceNode("finish-current-action", lambda: Decision("wait", "action-locked") if locked else None),
        ChoiceNode("pressure-held-attack", lambda: pressure(flow, snapshot, actorId)),
        ChoiceNode("defend-threat", lambda: defend(flow, snapshot, actorId)),
        ChoiceNode("counter-recovery", lambda: counter(flow, snapshot, actorId)),
        ChoiceNode("choose-attack", lambda: normalAttack(flow, snapshot, actorId)),
    ]
    # Rebuild transient nodes per tick; only JSON-compatible observations and seeds persist.
    tree = py_trees.composites.Selector("melee-decision", memory=False, children=nodes)
    tree.tick_once()
    return tree.current_child.decision
