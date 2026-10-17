import json
from pathlib import Path
import sys

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from validate_battle_capture import CaptureError, validate_capture, validate_ingress_watermarks


def start():
    return {"schemaVersion": 1, "type": "captureStart", "sourceId": 1,
            "clock": "GetTickCount64", "ridMap": [
                {"actorId": actor, "clientRid": actor} for actor in range(1, 21)]}


def actor(attempt=0):
    return {"actorId": 2, "clientRid": 2, "entity": 102,
            "position": [1.0, 2.0, 0.0], "heading": 90.0,
            "forwardAttemptAtUptimeMs": attempt, "attemptedForward": bool(attempt)}


def contact():
    event = {"actorId": 2, "targetId": 0, "sourceEntity": 102, "targetEntity": 0,
             "sequence": 1, "phaseAtUptimeMs": 1000, "targetPhaseAtUptimeMs": 1000,
             "occurredAtUptimeMs": 1009.5, "sweepTime": 0.5, "along": 0.5,
             "region": 0, "bodyIndex": -1, "material": 0, "defenceWeaponId": 0,
             "sourceHeading": 90.0, "targetHeading": 0.0,
             "velocityReady": False, "airborne": False, "targetAirborne": False}
    for field in ("position", "normal", "direction", "sourcePosition", "targetPosition",
                  "sourceVelocity", "targetVelocity", "weaponVelocity"):
        event[field] = [0.0, 0.0, 1.0]
    return event


def sample(batch=1, contact_batch=1, sampled=1010, captured=1010, ready=True, contacts=None):
    return {"schemaVersion": 1, "type": "sample", "sourceId": 1, "epoch": 1000,
            "worldBatchId": batch, "contactBatchId": contact_batch,
            "sampledAtUptimeMs": sampled, "capturedAtUptimeMs": captured,
            "contactThroughAtUptimeMs": sampled, "ready": ready, "contactsReady": True,
            "contactFrames": 1, "droppedContacts": 0, "actors": [actor(1009)] if ready else [],
            "contacts": contacts if contacts is not None else []}


def write(tmp_path, rows):
    path = tmp_path / "capture.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def test_complete_capture_accepts_selection_stall_and_contact_watermark(tmp_path):
    rows = [start(), sample(contacts=[contact()]),
            sample(2, 1, 1010, 1020, ready=False),
            sample(3, 1, 1010, 4020, ready=False),
            sample(4, 2, 4030, 4030),
            {"schemaVersion": 1, "type": "captureEnd", "complete": True}]
    result = validate_capture(write(tmp_path, rows))
    assert result == {"sourceId": 1, "matchEpochs": 1, "samples": 4,
                      "matchSamples": 4, "readySamples": 2, "observedActors": 1,
                      "contacts": 1, "forwardAttempts": 2,
                      "actorsWithForwardAttempt": 1,
                      "freshForwardAttempts": None, "actorsWithFreshForwardAttempt": None,
                      "maxUnreadyRunMs": 3010,
                      "complete": True}


def test_fresh_forward_attempt_is_distinct_from_recorded_last_attempt(tmp_path):
    rows = [start(), sample(), sample(2, 2, 1300, 1300),
            {"schemaVersion": 1, "type": "captureEnd", "complete": True}]
    rows[1]["actors"][0]["freshForwardAttempt"] = True
    rows[2]["actors"][0]["freshForwardAttempt"] = False
    result = validate_capture(write(tmp_path, rows))
    assert result["forwardAttempts"] == 2
    assert result["freshForwardAttempts"] == 1
    assert result["actorsWithFreshForwardAttempt"] == 1

    rows[2]["actors"][0]["freshForwardAttempt"] = True
    with pytest.raises(CaptureError, match="fresh movement attempt"):
        validate_capture(write(tmp_path, rows))

    del rows[2]["actors"][0]["freshForwardAttempt"]
    with pytest.raises(CaptureError, match="coverage"):
        validate_capture(write(tmp_path, rows))


@pytest.mark.parametrize("change,expected", [
    (lambda rows: rows[2].update(worldBatchId=3), "world batch"),
    (lambda rows: rows[2].update(contactBatchId=4), "contact batch"),
    (lambda rows: rows[2].update(contactThroughAtUptimeMs=0), "capture clock"),
    (lambda rows: rows[2].update(droppedContacts=1), "evidence dropped"),
    (lambda rows: rows[2]["actors"][0].update(clientRid=3), "actor RID"),
    (lambda rows: rows[2]["actors"][0].update(forwardAttemptAtUptimeMs=9999), "movement attempt"),
    (lambda rows: rows[2].update(contacts=[contact()]), "repeated without a new batch"),
    (lambda rows: rows[1]["contacts"][0].update(occurredAtUptimeMs=1011), "event time"),
    (lambda rows: rows[-1].update(complete=False), "incomplete"),
])
def test_invalid_capture_reports_the_failing_line(tmp_path, change, expected):
    rows = [start(), sample(contacts=[contact()]), sample(2, 1, 1020, 1020),
            {"schemaVersion": 1, "type": "captureEnd", "complete": True}]
    change(rows)
    with pytest.raises(CaptureError, match=expected) as error:
        validate_capture(write(tmp_path, rows))
    assert "line " in str(error.value)


def test_missing_end_is_not_reported_complete(tmp_path):
    with pytest.raises(CaptureError, match="missing start, samples or complete end"):
        validate_capture(write(tmp_path, [start(), sample()]))


@pytest.mark.parametrize("count", [10, 32, 60])
def test_native_capacity_capture_keeps_all_actor_ids(tmp_path, count):
    header = start()
    header["ridMap"] = [{"actorId": i, "clientRid": i} for i in range(1, count + 1)]
    row = sample()
    row["actors"] = [dict(actor(), actorId=i, clientRid=i, entity=100+i) for i in range(1, count + 1)]
    row["contacts"] = [dict(contact(), targetId=count, targetEntity=100+count)]
    result = validate_capture(write(tmp_path, [header, row,
        {"schemaVersion": 1, "type": "captureEnd", "complete": True}]))
    assert result["observedActors"] == count
    assert result["contacts"] == 1


def test_ingress_watermarks_retain_delayed_evidence_until_safe_then_flush(tmp_path):
    rows = [start(), sample(contacts=[contact()]), sample(2, 2, 1120, 1120),
            sample(3, 3, 1300, 1300),
            {"schemaVersion": 1, "type": "captureEnd", "complete": True}]
    path = write(tmp_path, rows)
    assert validate_capture(path)["contacts"] == 1
    assert validate_ingress_watermarks(path) == {
        "matchEpochs": 1, "readyWorlds": 3, "contactWatermarks": 2,
        "publishedContacts": 1, "maxPendingContacts": 1,
    }


def test_ingress_audit_rejects_contact_after_a_published_watermark(tmp_path):
    delayed = contact()
    delayed["occurredAtUptimeMs"] = 1050
    rows = [start(), sample(contacts=[contact()]), sample(2, 2, 1200, 1200),
            sample(3, 3, 1300, 1300), sample(4, 4, 1400, 1400, contacts=[delayed]),
            {"schemaVersion": 1, "type": "captureEnd", "complete": True}]
    path = write(tmp_path, rows)
    validate_capture(path)
    with pytest.raises(CaptureError, match="older than published watermark"):
        validate_ingress_watermarks(path)


@pytest.mark.parametrize("with_contact", [False, True])
@pytest.mark.parametrize("resume_world", [False, True])
def test_ingress_audit_retains_contacts_while_world_unready(tmp_path, with_contact, resume_world):
    contacts = [dict(contact(), occurredAtUptimeMs=1050.5)] if with_contact else []
    rows = [start(), sample(), sample(2, 2, 1100, 1400, ready=False, contacts=contacts),
            sample(3, 3, 1200, 1500, ready=False),
            sample(4, 4, 1600, 1600, ready=resume_world),
            {"schemaVersion": 1, "type": "captureEnd", "complete": True}]
    path = write(tmp_path, rows)
    assert validate_capture(path)["contacts"] == len(contacts)
    result = validate_ingress_watermarks(path)
    assert result["readyWorlds"] == 1 + resume_world
    assert result["contactWatermarks"] == 2
    assert result["publishedContacts"] == len(contacts)


def test_unready_pose_does_not_allow_missing_or_repeated_contact_evidence(tmp_path):
    for batch in [1, 3]:
        rows = [start(), sample(),
                sample(2, batch, 1100, 1400, ready=False, contacts=[contact()]),
                {"schemaVersion": 1, "type": "captureEnd", "complete": True}]
        with pytest.raises(CaptureError, match="contact (batch|events)"):
            validate_ingress_watermarks(write(tmp_path, rows))


@pytest.mark.parametrize("unready_batch", [1, 2])
def test_contact_journal_unready_does_not_publish_or_fabricate_a_watermark(tmp_path, unready_batch):
    unready = sample(2, unready_batch, 1120, 1120)
    unready["contactsReady"] = False
    rows = [start(), sample(contacts=[contact()]), unready,
            sample(3, 2, 1300, 1300),
            {"schemaVersion": 1, "type": "captureEnd", "complete": True}]
    path = write(tmp_path, rows)
    assert validate_capture(path)["contacts"] == 1
    assert validate_ingress_watermarks(path)["publishedContacts"] == 1

    unready["contacts"] = [contact()]
    with pytest.raises(CaptureError, match="unready contact journal has evidence"):
        validate_capture(write(tmp_path, rows))


@pytest.mark.parametrize("explicit", [False, True])
def test_target_phase_can_begin_after_collision_but_before_observation(tmp_path, explicit):
    event = contact()
    event["targetPhaseAtUptimeMs"] = 1011
    if explicit:
        event["targetObservedAtUptimeMs"] = 1012
    rows = [start(), sample(captured=1013, contacts=[event]),
            {"schemaVersion": 1, "type": "captureEnd", "complete": True}]
    assert validate_capture(write(tmp_path, rows))["contacts"] == 1
    event["targetPhaseAtUptimeMs"] = 1014
    with pytest.raises(CaptureError, match="phase time"):
        validate_capture(write(tmp_path, rows))


@pytest.mark.parametrize("observed", [None, -1, 1009, 1014, 1011.5, True])
def test_target_observation_rejects_invalid_or_future_read_time(tmp_path, observed):
    event = contact()
    event["targetObservedAtUptimeMs"] = observed
    rows = [start(), sample(captured=1013, contacts=[event]),
            {"schemaVersion": 1, "type": "captureEnd", "complete": True}]
    with pytest.raises(CaptureError, match="observation time"):
        validate_capture(write(tmp_path, rows))


@pytest.mark.parametrize("explicit", [False, True])
def test_source_phase_after_collision_is_evidence_not_batch_corruption(tmp_path, explicit):
    event = contact()
    event["phaseAtUptimeMs"] = 1011
    event["targetObservedAtUptimeMs"] = 1010
    if explicit:
        event["sourceObservedAtUptimeMs"] = 1012
    rows = [start(), sample(captured=1013, contacts=[event]),
            {"schemaVersion": 1, "type": "captureEnd", "complete": True}]
    path = write(tmp_path, rows)
    assert validate_capture(path)["contacts"] == 1
    assert validate_ingress_watermarks(path)["publishedContacts"] == 1
    event["phaseAtUptimeMs"] = 1014
    with pytest.raises(CaptureError, match="phase time"):
        validate_capture(write(tmp_path, rows))


@pytest.mark.parametrize("observed", [None, -1, 1009, 1014, 1011.5, True])
def test_source_observation_rejects_invalid_or_future_read_time(tmp_path, observed):
    event = contact()
    event["sourceObservedAtUptimeMs"] = observed
    rows = [start(), sample(captured=1013, contacts=[event]),
            {"schemaVersion": 1, "type": "captureEnd", "complete": True}]
    with pytest.raises(CaptureError, match="source observation time"):
        validate_capture(write(tmp_path, rows))
