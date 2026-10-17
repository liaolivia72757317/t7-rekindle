"""Validate frame-local native blade contacts; never infer hits from actor distance."""
import math

from . import actors

GROUND_NORMAL_Z = 0.5  # Ground/slopes up to 60 degrees, regardless of ray-facing normal orientation.
GROUND_PLANE_TOLERANCE = 0.1


def isGround(contact, sourcePosition):
    normal = contact['normal']
    if contact['targetId'] != 0 or abs(normal[2]) < GROUND_NORMAL_Z * math.hypot(*normal):
        return False
    # Project the hit plane to the attacker's feet so reversed ground normals are not mistaken for ceilings.
    height = sum(axis * (point - source) for axis, point, source
                 in zip(normal, contact['position'], sourcePosition)) / normal[2]
    return height <= GROUND_PLANE_TOLERANCE


def contacts(snapshot, actorId, beganAt, endedAt=math.inf):
    pose = actors.pose(snapshot, actorId)
    if (pose is None or type(pose.get("heading")) not in (int, float)
            or not math.isfinite(pose["heading"]) or not -180 <= pose["heading"] <= 180):
        return None
    blades = snapshot.get("blades")
    trace = blades.get(str(actorId)) if type(blades) is dict else None
    if (type(trace) is not dict or trace.get("ready") is not True
            or type(trace.get("spanMs")) is not int or not 0 <= trace["spanMs"] <= 100
            or type(trace.get("contacts")) is not list or len(trace["contacts"]) > actors.MAX_ACTORS * 2 + 2
            or not all(actors.validPosition(trace.get(key)) for key in ("base", "tip"))
            or type(trace.get("length")) not in (int, float)
            or not .01 <= trace["length"] <= 8):
        return None
    result, seen = [], set()
    for hit in trace["contacts"]:
        if (type(hit) is not dict or hit.get("ready") is not True or hit.get("hit") is not True
                or type(hit.get("targetId")) is not int or not 0 <= hit["targetId"] <= actors.MAX_ACTORS
                or hit["targetId"] == actorId
                or type(hit.get('defenceWeaponId')) is not int or hit['defenceWeaponId'] < 0
                or hit['targetId'] == 0 and hit['defenceWeaponId'] != 0
                or type(hit.get("material")) is not int or not -1 <= hit["material"] <= 7
                or not all(actors.validPosition(hit.get(key)) for key in ("position", "normal", "direction"))
                or not all(.9 <= math.hypot(*hit[key]) <= 1.1 for key in ("normal", "direction"))
                or not all(type(hit.get(key)) in (int, float) and math.isfinite(hit[key])
                           and 0 <= hit[key] <= 1 for key in ("time", "along"))):
            return None
        slot = (hit['targetId'], isGround(hit, pose['position']), bool(hit['defenceWeaponId']))
        if slot in seen:
            return None
        seen.add(slot)
        contactAt = snapshot["sampledAt"] - trace["spanMs"] * (1 - hit["time"])
        if beganAt <= contactAt <= endedAt:
            result.append(hit)
    return sorted(result, key=lambda hit: (hit["time"], hit["along"], hit["targetId"], not hit['defenceWeaponId']))
