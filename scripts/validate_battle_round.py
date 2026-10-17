"""Correlate a complete native input capture with legacy round/return wire metadata."""

import argparse
import json
from pathlib import Path
import re

from validate_battle_capture import CaptureError, MAX_ACTORS, validate_capture


SETTLEMENT = re.compile(r"\bsettlement reason=(timeout|kill-target) result=([123])\b.*\bmembers=(\d+)$")
WIRE_NAME = re.compile(r"^event=\d+ (\S+)$")
WIRE_LENGTHS = {
    "settlement-score": 121,
    "instance-round-result": 49,
    "instance-round-state-end": 65,
    "instance-settlement": 54,
    "instance-finish-game": 141,
    "instance-leave-instance": 49,
    "sync-logout-response": 38,
    "user-group-quit-response": 41,
}


def capture_window(path):
    first = last = None
    with Path(path).open("r", encoding="utf-8") as source:
        for line in source:
            row = json.loads(line)
            if row["type"] == "sample":
                at = row["capturedAtUptimeMs"]
                first = at if first is None else first
                last = at
    return first, last


def wire_records(directory):
    files = []
    for path in Path(directory).glob("frames-*.jsonl"):
        match = re.fullmatch(r"frames-(\d+)\.jsonl", path.name)
        if match:
            files.append((int(match.group(1)), path))
    if not files:
        raise CaptureError("wire journal has no frame index")
    previous_id = previous_at = None
    for _, path in sorted(files):
        with path.open("r", encoding="utf-8") as source:
            for line in source:
                row = json.loads(line)
                record_id, at = row["recordId"], row["monotonicUs"]
                if previous_id is not None and (record_id != previous_id + 1 or at < previous_at):
                    raise CaptureError("wire journal record order regressed or has a gap")
                previous_id, previous_at = record_id, at
                yield row


def validate_round(capture_path, journal_directory):
    capture = validate_capture(capture_path)
    first, last = capture_window(capture_path)
    if capture["matchEpochs"] != 1 or first is None:
        raise CaptureError("round baseline requires one captured match epoch")
    settlement = None
    events = []
    for row in wire_records(journal_directory):
        at = row["monotonicUs"]
        if at < (first - 1000) * 1000 or at > (last + 1000) * 1000:
            continue
        direction, reason = row["direction"], row["reason"]
        if direction == "BUSINESS":
            match = SETTLEMENT.search(reason)
            if match:
                if settlement is not None:
                    raise CaptureError("multiple natural settlements overlap the capture")
                member_count = int(match.group(3))
                if not 1 <= member_count <= MAX_ACTORS:
                    raise CaptureError("invalid settlement member count")
                settlement = (at, match.group(1), int(match.group(2)), member_count)
        elif direction == "S2C":
            match = WIRE_NAME.fullmatch(reason)
            if match and match.group(1) in WIRE_LENGTHS:
                name = match.group(1)
                if row.get("wireLength") != WIRE_LENGTHS[name]:
                    raise CaptureError(f"partial or invalid {name} wire send")
                events.append((at, row["connectionId"], name))
    if settlement is None:
        raise CaptureError("no natural settlement overlaps the capture")
    settled_at, reason, result, member_count = settlement
    instance = next((conn for at, conn, name in events
                     if at >= settled_at and name == "instance-round-state-end"), None)
    if instance is None:
        raise CaptureError("natural settlement has no instance END")
    score = [(at, name) for at, conn, name in events
             if conn == instance and at >= settled_at and name == "settlement-score"]
    if len(score) != member_count:
        raise CaptureError(f"natural settlement lacks {member_count} final score messages")

    def next_event(name, after, same_instance=True):
        return next((at for at, conn, event in events if at >= after and event == name
                     and (not same_instance or conn == instance)), None)

    result_at = next_event("instance-round-result", score[-1][0])
    end_at = next_event("instance-round-state-end", result_at) if result_at is not None else None
    settle_at = next_event("instance-settlement", end_at) if end_at is not None else None
    finish_at = next_event("instance-finish-game", settle_at) if settle_at is not None else None
    leave_at = next_event("instance-leave-instance", finish_at) if finish_at is not None else None
    logout_at = next_event("sync-logout-response", leave_at) if leave_at is not None else None
    room_at = next_event("user-group-quit-response", logout_at, False) if logout_at is not None else None
    if None in (result_at, end_at, settle_at, finish_at, leave_at, logout_at, room_at):
        raise CaptureError("natural settlement or return-to-room output is incomplete")
    hold_ms = (finish_at - end_at) / 1000
    if not 4500 <= hold_ms <= 8000:
        raise CaptureError("natural END-to-FINISH hold differs from the five-second baseline")
    return {
        "reason": reason, "result": result, "scoreMessages": len(score),
        "endToFinishMs": round(hold_ms, 3),
        "leaveToLogoutMs": round((logout_at - leave_at) / 1000, 3),
        "logoutToRoomQuitMs": round((room_at - logout_at) / 1000, 3),
        "inputCaptureComplete": capture["complete"],
        "observedActors": capture["observedActors"], "contacts": capture["contacts"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("journal_directory", type=Path, help="existing wire index directory")
    args = parser.parse_args()
    try:
        print(json.dumps(validate_round(args.capture, args.journal_directory), sort_keys=True))
    except (CaptureError, OSError, ValueError, KeyError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
