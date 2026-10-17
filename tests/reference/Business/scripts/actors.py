"""Instance roster and frame-local actor views; RID 1 is the human."""
import math

from . import contracts as wire

MAX_ACTORS = 20
PULSE_TIMER = "actors-pulse"


def localTimer(session, name):
    # Motion/contact consume native frames; only AI/lifecycle work uses the slow pulse.
    return "bots" in session and name.startswith(("bot-think", "bot-move", "bot-respawn", "combat-hit-"))


def bots(session):
    # Existing hot-reloaded single-opponent rounds keep their roster until re-entry.
    return session.get("bots", {"2": session["bot"]} if "bot" in session else {})


def ids(session):
    return (wire.ACTOR_ID, *(int(key) for key in bots(session)))


def data(flow, actorId):
    if actorId == wire.ACTOR_ID:
        return flow.session
    value = bots(flow.session).get(str(actorId))
    if value is None:
        raise ValueError("battle actor is outside the current instance")
    return value


def timerName(name, actorId):
    return name if actorId == wire.BOT_ID else f"{name}-{actorId}"


def active(flow, actorId):
    return 'life' not in data(flow, actorId)


def validPosition(value):
    return (type(value) in (list, tuple) and len(value) == 3
            and all(type(v) in (float, int) and math.isfinite(v) and abs(v) < 100000 for v in value))


def world(flow):
    value = flow.context.get("botWorld")
    if (type(value) is not dict or value.get("ready") is not True
            or type(value.get("sampledAt")) is not int
            or not 0 <= flow.now - value["sampledAt"] <= 250
            or value.get("instanceStartedAt") != flow.session.get("instanceStartedAt")):
        return None
    return value


def pose(snapshot, actorId):
    if snapshot is None:
        return None
    if "actors" in snapshot:
        value = snapshot["actors"].get(str(actorId))
        if type(value) is not dict or value.get("ready") is not True or not validPosition(value.get("position")):
            return None
        return value
    if actorId not in (1, 2):
        return None
    name = "player" if actorId == 1 else "bot"
    if not validPosition(snapshot.get(name)):
        return None
    terrain = snapshot.get("terrain", [{}, {}])
    return {"position": snapshot[name], "heading": snapshot.get(name + "Heading"),
            "terrain": terrain[actorId - 1] if type(terrain) is list and len(terrain) == 2 else {},
            "targetId": 3 - actorId}


def enemies(flow, actorId, snapshot):
    origin = pose(snapshot, actorId)
    if origin is None or not active(flow, actorId):
        return []
    camp = data(flow, actorId).get("camp", 1)
    candidates = [rid for rid in ids(flow.session) if rid != actorId
                  and active(flow, rid) and data(flow, rid).get("camp", 1) != camp and pose(snapshot, rid) is not None]
    return sorted(candidates, key=lambda rid: (math.dist(origin["position"], pose(snapshot, rid)["position"]), rid))


def pair(snapshot, sourceId, targetId):
    source, target = pose(snapshot, sourceId), pose(snapshot, targetId)
    if source is None or target is None:
        return None
    def probes(value, otherId):
        result = dict(value.get("terrain", {}))
        if "targetTerrain" in value:
            result.pop("target", None)
            targets = value["targetTerrain"]
            if type(targets) is dict and type(targets.get(str(otherId))) is dict:
                result["target"] = targets[str(otherId)]
        elif value.get("targetId") != otherId:
            result.pop("target", None)
        return result
    return {"ready": True, "sampledAt": snapshot["sampledAt"], "instanceStartedAt": snapshot["instanceStartedAt"],
            "sourceId": sourceId, "targetId": targetId,
            "bot": source["position"], "player": target["position"],
            "botHeading": source.get("heading"), "playerHeading": target.get("heading"),
            "terrain": [probes(target, sourceId), probes(source, targetId)]}


def positionName(snapshot, actorId):
    return "bot" if actorId == snapshot.get("sourceId", 2) else "player"
