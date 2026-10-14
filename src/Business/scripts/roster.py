"""One actor per initial infantry spawn, with the first own-camp point reserved."""
import struct

from . import actors, body_collision, bot, bot_names, bot_profiles, combat, contracts as wire, controls
from .bot_names import loadNames
from .spawn_resource import loadRespawns, loadSpawns


def assemble(flow):
    camp = flow.session.get("camp", 1)
    spawns = loadSpawns(flow.context.get("clientDirectory", ""))
    respawns = loadRespawns(flow.context.get("clientDirectory", ""))
    names = bot_names.assignNames(loadNames(flow.context.get("clientDirectory", "")), 19,
                                  flow.session['instanceStartedAt'], flow.playerName.decode('gbk'))
    position, heading = spawns[camp][0]
    values = {}
    # Fixed relative-team RID ranges also let the native sampler exclude teammates.
    for rid, (team, point) in enumerate(
            [(3 - camp, point) for point in spawns[3 - camp]]
            + [(camp, point) for point in spawns[camp][1:]], start=2):
        value = bot.createBot(3 - team)
        value.update(position=list(point[0]), heading=point[1])
        bot_profiles.initialize(value, rid, rid - (2 if rid <= 11 else 12), spawns,
                                flow.session['instanceStartedAt'])
        value['name'] = bot_names.PREFIXES[value['profile']] + names[rid - 2]
        values[str(rid)] = value
        flow.log("DEBUG", f"bot-profile actor={rid} template={value['profile']} tempo={value['tempo']} lane={value['lane']}")
    flow.session.pop("bot", None)
    flow.session.pop('bodyPlayer', None)
    flow.session.update(bots=values, resourceId=wire.RESOURCE_ID, spawnPosition=list(position), spawnHeading=heading,
                        respawnPoints={str(team): [[list(pos), yaw] for pos, yaw in points]
                                       for team, points in respawns.items()})
    controls.groundState(flow).update(position=list(position), heading=heading)
    combat.initialize(flow)
    flow.log("INFO", f"infantry-spawns blue=10 red=10 player-camp={camp} friendly-bots=9 enemy-bots=10")


def playerVision(flow):
    ground = flow.session.get("ground", {})
    position = ground.get("position") or flow.session.get("spawnPosition", wire.POSITION)
    body = wire.actorVision(flow.session.get("camp", 1), flow.session.get("heroId", wire.HERO_ID), flow.playerName,
                            runtimeMovement=controls.runtimeMovement(flow), position=tuple(position),
                            currentHp=flow.session.get("health", 100), userId=flow.userId)
    heading = ground.get("heading", flow.session.get("spawnHeading", 0))
    return body[:45] + struct.pack(">h", heading) + body[47:]


def validState(session):
    if not body_collision.validState(session):
        return False
    if 'resourceId' in session and (type(session['resourceId']) is not int or session['resourceId'] <= 0):
        return False
    respawns = session.get('respawnPoints')
    if respawns is not None and (type(respawns) is not dict or set(respawns) != {'1', '2'} or any(
            type(points) is not list or len(points) != (14 if camp == '1' else 12) or any(
                type(point) is not list or len(point) != 2 or not actors.validPosition(point[0])
                or type(point[1]) is not int or not -180 <= point[1] <= 180 for point in points)
            for camp, points in respawns.items())):
        return False
    if "onlinePeers" in session:
        values = session["onlinePeers"]
        return ("bots" not in session and "bot" not in session and type(values) is dict and len(values) <= 19
            and all(key in {str(i) for i in range(2, 21)} and type(peer) is dict
                and actors.validPosition(peer.get("position")) and actors.validPosition(peer.get("spawnPosition"))
                and type(peer.get("heading")) is int and -180 <= peer["heading"] <= 180
                and peer.get("camp") == (3 - session.get("camp", 1) if int(key) <= 11 else session.get("camp", 1))
                for key, peer in values.items()))
    if "bots" not in session:
        return True
    values = session["bots"]
    if ("bot" in session or type(values) is not dict or len(values) != 19
            or set(values) != {str(rid) for rid in range(2, actors.MAX_ACTORS + 1)}
            or not actors.validPosition(session.get("spawnPosition"))
            or type(session.get("spawnHeading")) is not int or not -180 <= session["spawnHeading"] <= 180):
        return False
    camp = session.get("camp", 1)
    return all(bot.validState(value) and combat.validState(value)
               and value["camp"] == (3 - camp if int(key) <= 11 else camp)
               and all(value.get(name) is None or value[name] in actors.ids(session)
                       and value[name] != int(key) for name in ('opponentId', 'searchTargetId'))
               for key, value in values.items())
