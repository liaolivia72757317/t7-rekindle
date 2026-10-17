"""Validate a native battle JSONL capture without copying it into the repository."""

import argparse
from collections import Counter
import json
import math
from pathlib import Path

MAX_ACTORS = 60

class CaptureError(ValueError):
    pass


def require(condition, line, message):
    if not condition:
        raise CaptureError(f"line {line}: {message}")


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def vector(value):
    return isinstance(value, list) and len(value) == 3 and all(number(v) and abs(v) < 100000 for v in value)


def validate_capture(path):
    source_id = None
    rid_map = {}
    previous = None
    seen_epochs = set()
    counts = Counter()
    actor_ids = set()
    attempt_ids = set()
    fresh_attempt_ids = set()
    fresh_field_present = None
    unready_start = None
    max_unready_ms = 0
    ended = False
    with Path(path).open("r", encoding="utf-8") as capture:
        for line_no, line in enumerate(capture, 1):
            require(len(line) <= 4 * 1024 * 1024, line_no, "sample exceeds line limit")
            try:
                row = json.loads(line, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
            except (ValueError, TypeError) as error:
                raise CaptureError(f"line {line_no}: invalid JSON: {error}") from error
            require(isinstance(row, dict) and type(row.get("schemaVersion")) is int
                    and row["schemaVersion"] == 1, line_no, "invalid schema")
            kind = row.get("type")
            if kind == "captureStart":
                require(source_id is None and line_no == 1, line_no, "duplicate or late captureStart")
                source_id = row.get("sourceId")
                require(integer(source_id) and source_id != 0 and row.get("clock") == "GetTickCount64",
                        line_no, "invalid source or clock")
                geometry = row.get("guardGeometry")
                if geometry is not None:
                    require(isinstance(geometry, dict) and geometry.get("shape") == "frontObb"
                            and number(geometry.get("forwardOffset")) and geometry["forwardOffset"] > 0
                            and number(geometry.get("heightOffset")) and geometry["heightOffset"] > 0
                            and vector(geometry.get("halfExtents")) and all(v > 0 for v in geometry["halfExtents"]),
                            line_no, "invalid guard geometry")
                mapping = row.get("ridMap")
                require(isinstance(mapping, list) and 1 <= len(mapping) <= MAX_ACTORS, line_no, "invalid RID map")
                for entry in mapping:
                    actor = entry.get("actorId") if isinstance(entry, dict) else None
                    rid = entry.get("clientRid") if isinstance(entry, dict) else None
                    require(integer(actor) and 1 <= actor <= MAX_ACTORS and integer(rid) and rid > 0
                            and actor not in rid_map and rid not in rid_map.values(), line_no, "invalid RID mapping")
                    rid_map[actor] = rid
            elif kind == "captureEnd":
                require(source_id is not None and not ended and row.get("complete") is True,
                        line_no, "incomplete or duplicate captureEnd")
                ended = True
            elif kind == "sample":
                require(source_id is not None and not ended, line_no, "sample outside capture")
                require(type(row.get("sourceId")) is int and row["sourceId"] == source_id,
                        line_no, "source changed")
                for field in ("epoch", "worldBatchId", "contactBatchId", "sampledAtUptimeMs",
                              "capturedAtUptimeMs", "contactThroughAtUptimeMs", "contactFrames", "droppedContacts"):
                    require(integer(row.get(field)), line_no, f"invalid {field}")
                epoch = row["epoch"]
                batch = row["worldBatchId"]
                sampled = row["sampledAtUptimeMs"]
                captured = row["capturedAtUptimeMs"]
                through = row["contactThroughAtUptimeMs"]
                require(batch > 0 and sampled <= captured and through == sampled,
                        line_no, "invalid batch or capture clock")
                require(isinstance(row.get("contactsReady"), bool) and isinstance(row.get("ready"), bool),
                        line_no, "invalid readiness")
                require(row["droppedContacts"] == 0, line_no, "native contact evidence dropped")
                actors, contacts = row.get("actors"), row.get("contacts")
                require(isinstance(actors, list) and len(actors) <= len(rid_map) and isinstance(contacts, list),
                        line_no, "invalid actor or contact list")
                require(row["contactsReady"] or not contacts, line_no,
                        "unready contact journal has evidence")
                require(row["ready"] or not actors, line_no, "unready sample has actor poses")
                if previous is None or epoch != previous["epoch"]:
                    require(epoch not in seen_epochs and batch == 1, line_no, "epoch reused or batch not reset")
                    require(previous is None or captured >= previous["capturedAtUptimeMs"],
                            line_no, "capture clock regressed across epochs")
                    seen_epochs.add(epoch)
                    if unready_start is not None:
                        max_unready_ms = max(max_unready_ms, previous["capturedAtUptimeMs"] - unready_start)
                        unready_start = None
                else:
                    require(batch == previous["worldBatchId"] + 1 and captured >= previous["capturedAtUptimeMs"],
                            line_no, "world batch or capture clock regressed")
                    require(sampled >= previous["sampledAtUptimeMs"] and through >= previous["contactThroughAtUptimeMs"],
                            line_no, "sample or contact watermark regressed")
                    old_contact = previous["contactBatchId"]
                    require(row["contactBatchId"] in (old_contact, old_contact + 1),
                            line_no, "contact batch skipped or regressed")
                    require(row["contactBatchId"] != old_contact or not contacts,
                            line_no, "contact events repeated without a new batch")
                if epoch and not row["ready"]:
                    if unready_start is None:
                        unready_start = captured
                elif unready_start is not None:
                    max_unready_ms = max(max_unready_ms, captured - unready_start)
                    unready_start = None
                seen_actors = set()
                for actor in actors:
                    aid = actor.get("actorId") if isinstance(actor, dict) else None
                    require(aid in rid_map and actor.get("clientRid") == rid_map.get(aid)
                            and aid not in seen_actors, line_no, "unbound or duplicate actor RID")
                    seen_actors.add(aid)
                    require(integer(actor.get("entity")) and actor["entity"] > 0
                            and vector(actor.get("position")) and number(actor.get("heading"))
                            and abs(actor["heading"]) <= 180, line_no, "invalid actor pose")
                    attempt_at = actor.get("forwardAttemptAtUptimeMs")
                    require(integer(attempt_at) and attempt_at <= sampled
                            and actor.get("attemptedForward") is (attempt_at != 0),
                            line_no, "invalid native movement attempt")
                    has_fresh_field = "freshForwardAttempt" in actor
                    if fresh_field_present is None:
                        fresh_field_present = has_fresh_field
                    require(has_fresh_field == fresh_field_present,
                            line_no, "inconsistent fresh movement attempt coverage")
                    if has_fresh_field:
                        fresh = attempt_at != 0 and sampled - attempt_at <= 250
                        require(actor["freshForwardAttempt"] is fresh,
                                line_no, "invalid fresh movement attempt")
                        if fresh:
                            fresh_attempt_ids.add(aid)
                            counts["freshForwardAttempts"] += 1
                    actor_ids.add(aid)
                    if attempt_at:
                        attempt_ids.add(aid)
                        counts["forwardAttempts"] += 1
                for event in contacts:
                    aid = event.get("actorId") if isinstance(event, dict) else None
                    target = event.get("targetId") if isinstance(event, dict) else None
                    occurred = event.get("occurredAtUptimeMs") if isinstance(event, dict) else None
                    require(aid in rid_map and integer(target) and (target == 0 or target in rid_map) and target != aid
                            and number(occurred) and epoch <= occurred <= through,
                            line_no, "invalid contact identity or event time")
                    # Older captures have only the later batch capture bound.
                    observed = event.get("targetObservedAtUptimeMs", captured)
                    require(integer(observed) and occurred <= observed <= captured,
                            line_no, "invalid target observation time")
                    source_observed = event.get("sourceObservedAtUptimeMs", captured)
                    require(integer(source_observed) and occurred <= source_observed <= captured,
                            line_no, "invalid source observation time")
                    require(integer(event.get("sourceEntity")) and event["sourceEntity"] > 0
                            and integer(event.get("targetEntity"))
                            and (target == 0 or event["targetEntity"] > 0)
                            and integer(event.get("sequence")) and event["sequence"] > 0
                            and integer(event.get("phaseAtUptimeMs"))
                            and integer(event.get("targetPhaseAtUptimeMs"))
                            and event["phaseAtUptimeMs"] <= source_observed
                            and event["targetPhaseAtUptimeMs"] <= observed,
                            line_no, "invalid contact entity, sequence or phase time")
                    require(number(event.get("sweepTime")) and 0 <= event["sweepTime"] <= 1
                            and number(event.get("along")) and 0 <= event["along"] <= 1
                            and integer(event.get("region")) and event["region"] <= 3
                            and type(event.get("bodyIndex")) is int and -1 <= event["bodyIndex"] <= 127
                            and type(event.get("material")) is int and -1 <= event["material"] <= 7
                            and integer(event.get("defenceWeaponId")),
                            line_no, "invalid contact geometry or classification")
                    require(all(vector(event.get(key)) for key in ("position", "normal", "direction",
                            "sourcePosition", "targetPosition", "sourceVelocity", "targetVelocity", "weaponVelocity")),
                            line_no, "invalid contact vector")
                    require(all(0.9 <= math.sqrt(sum(axis * axis for axis in event[key])) <= 1.1
                            for key in ("normal", "direction")), line_no, "invalid contact normal or direction")
                    require(all(number(event.get(key)) and abs(event[key]) <= 180
                            for key in ("sourceHeading", "targetHeading"))
                            and all(type(event.get(key)) is bool
                            for key in ("velocityReady", "airborne", "targetAirborne")),
                            line_no, "invalid contact heading or flags")
                    origin = event.get("contactOrigin", "native")
                    require(origin in ("native", "guardVolume"), line_no, "invalid contact origin")
                    if origin == "guardVolume":
                        require(target != 0 and event["defenceWeaponId"] > 0
                                and event["region"] == 0 and event["bodyIndex"] == -1
                                and event.get("targetState") not in (111, 309), line_no, "invalid guard volume contact")
                    counts["contacts"] += 1
                counts["samples"] += 1
                counts["readySamples"] += bool(row["ready"])
                counts["matchSamples"] += bool(epoch)
                previous = row
            else:
                raise CaptureError(f"line {line_no}: unknown record type")
    require(source_id is not None and ended and counts["samples"] > 0, line_no if source_id else 0,
            "capture missing start, samples or complete end")
    if unready_start is not None:
        max_unready_ms = max(max_unready_ms, previous["capturedAtUptimeMs"] - unready_start)
    return {"sourceId": source_id, "matchEpochs": len(seen_epochs - {0}),
            "samples": counts["samples"], "matchSamples": counts["matchSamples"],
            "readySamples": counts["readySamples"], "observedActors": len(actor_ids),
            "contacts": counts["contacts"], "forwardAttempts": counts["forwardAttempts"],
            "actorsWithForwardAttempt": len(attempt_ids),
            "freshForwardAttempts": counts["freshForwardAttempts"] if fresh_field_present else None,
            "actorsWithFreshForwardAttempt": len(fresh_attempt_ids) if fresh_field_present else None,
            "maxUnreadyRunMs": max_unready_ms,
            "complete": True}


def validate_ingress_watermarks(path):
    """Audit contact continuity independently of pose freshness after world binding."""
    summary = validate_capture(path)
    ready_worlds = 0
    contact_watermarks = 0
    published_contacts = 0
    max_pending = 0
    epoch = None
    first_ready_at = None
    last_sampled_at = None
    last_contact_batch = 0
    emitted_through = 0
    pending = []

    def publish(through, line_no):
        nonlocal contact_watermarks, published_contacts, emitted_through, pending
        require(through > emitted_through, line_no, "contact watermark did not advance")
        ready = [at for at in pending if at <= through]
        pending = [at for at in pending if at > through]
        published_contacts += len(ready)
        contact_watermarks += 1
        emitted_through = through

    def finish(line_no):
        if last_contact_batch:
            if last_sampled_at > emitted_through:
                publish(last_sampled_at, line_no)
            require(not pending, line_no, "contact evidence remains after final watermark")

    with Path(path).open("r", encoding="utf-8") as capture:
        for line_no, line in enumerate(capture, 1):
            row = json.loads(line)
            if row["type"] != "sample":
                continue
            if row["epoch"] != epoch:
                if epoch is not None:
                    finish(line_no)
                epoch = row["epoch"]
                first_ready_at = None
                last_sampled_at = None
                last_contact_batch = 0
                emitted_through = 0
                pending = []
            if not epoch or (not row["ready"] and first_ready_at is None):
                continue
            ready_worlds += bool(row["ready"])
            sampled_at = row["sampledAtUptimeMs"]
            if first_ready_at is None:
                first_ready_at = sampled_at
            contact_batch = row["contactBatchId"]
            new_contacts = row["contactsReady"] and contact_batch > last_contact_batch
            if not row["ready"] and not new_contacts:
                continue
            if not row["contactsReady"]:
                require(not row["contacts"], line_no, "unready contact journal has evidence")
            elif contact_batch > last_contact_batch:
                require(last_contact_batch == 0 or contact_batch == last_contact_batch + 1,
                        line_no, "contact batch has a gap")
                require(len(pending) + len(row["contacts"]) <= 8192,
                        line_no, "pending contact evidence exceeds native capacity")
                for event in row["contacts"]:
                    at = event["occurredAtUptimeMs"]
                    require(at > emitted_through, line_no,
                            "contact arrived older than published watermark")
                    pending.append(at)
                max_pending = max(max_pending, len(pending))
                last_contact_batch = contact_batch
                if (last_sampled_at is not None and last_sampled_at - first_ready_at >= 100
                        and last_sampled_at - 100 > emitted_through):
                    publish(last_sampled_at - 100, line_no)
            else:
                require(contact_batch == last_contact_batch and not row["contacts"],
                        line_no, "ready contact batch repeated or regressed")
            last_sampled_at = sampled_at
    finish(line_no)
    return {"matchEpochs": summary["matchEpochs"], "readyWorlds": ready_worlds,
            "contactWatermarks": contact_watermarks,
            "publishedContacts": published_contacts, "maxPendingContacts": max_pending}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path, help="explicit path to an existing native capture")
    parser.add_argument("--ingress-watermarks", action="store_true",
                        help="also audit native ready-sample contact-watermark capacity and order")
    args = parser.parse_args()
    try:
        result = validate_capture(args.capture)
        if args.ingress_watermarks:
            result["ingress"] = validate_ingress_watermarks(args.capture)
        print(json.dumps(result, sort_keys=True))
    except (CaptureError, OSError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
