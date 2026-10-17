import json
from pathlib import Path
import sys

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import validate_battle_round as round_check
from validate_battle_capture import CaptureError


def fixture(tmp_path, monkeypatch, count=20):
    monkeypatch.setattr(round_check, "validate_capture", lambda _: {
        "matchEpochs": 1, "complete": True, "observedActors": count, "contacts": 3})
    capture = tmp_path / "capture.jsonl"
    capture.write_text("".join(json.dumps({"type": "sample", "capturedAtUptimeMs": at}) + "\n"
                               for at in (1000, 9000)), encoding="utf-8")
    journal = tmp_path / "wire"
    journal.mkdir()
    rows = []

    def add(at, direction, reason, connection=2):
        name = reason.split()[-1]
        rows.append({"recordId": len(rows) + 1, "monotonicUs": at,
                     "direction": direction, "connectionId": connection, "reason": reason,
                     "wireLength": round_check.WIRE_LENGTHS.get(name, 0)})

    add(1_100_000, "BUSINESS",
        "event=10 settlement reason=kill-target result=1 campKills={'1': 60, '2': 9} members=" + str(count), 0)
    for number in range(count):
        add(1_100_100 + number, "S2C", "event=10 settlement-score")
    add(1_100_200, "S2C", "event=10 instance-round-result")
    add(1_100_300, "S2C", "event=10 instance-round-state-end")
    add(6_100_300, "S2C", "event=11 instance-settlement")
    add(6_100_400, "S2C", "event=11 instance-finish-game")
    add(6_100_500, "S2C", "event=12 instance-leave-instance")
    add(6_350_500, "S2C", "event=13 sync-logout-response")
    add(7_100_500, "S2C", "event=14 user-group-quit-response", 1)
    return capture, journal, rows


def write_journal(journal, rows):
    (journal / "frames-1.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_natural_round_has_twenty_scores_five_second_hold_and_room_return(tmp_path, monkeypatch):
    capture, journal, rows = fixture(tmp_path, monkeypatch)
    write_journal(journal, rows)
    result = round_check.validate_round(capture, journal)
    assert result == {"reason": "kill-target", "result": 1, "scoreMessages": 20,
                      "endToFinishMs": 5000.1, "leaveToLogoutMs": 250.0,
                      "logoutToRoomQuitMs": 750.0, "inputCaptureComplete": True,
                      "observedActors": 20, "contacts": 3}


@pytest.mark.parametrize("count", [10, 32, 60])
def test_native_capacity_round_requires_every_final_score(tmp_path, monkeypatch, count):
    capture, journal, rows = fixture(tmp_path, monkeypatch, count)
    write_journal(journal, rows)
    assert round_check.validate_round(capture, journal)["scoreMessages"] == count
    rows[1]["reason"] = "event=10 unrelated"
    write_journal(journal, rows)
    with pytest.raises(CaptureError, match=f"lacks {count} final score"):
        round_check.validate_round(capture, journal)


@pytest.mark.parametrize("change,expected", [
    (lambda rows: rows.pop(), "return-to-room output is incomplete"),
    (lambda rows: rows.pop(1), "record order regressed or has a gap"),
    (lambda rows: rows[0].update(reason="event=10 settlement reason=quit result=0 members=20"),
     "no natural settlement"),
    (lambda rows: rows[22].update(monotonicUs=2_100_300),
     "END-to-FINISH hold"),
    (lambda rows: rows[1].update(wireLength=60), "partial or invalid settlement-score"),
])
def test_incomplete_or_inconsistent_round_is_rejected(tmp_path, monkeypatch, change, expected):
    capture, journal, rows = fixture(tmp_path, monkeypatch)
    change(rows)
    write_journal(journal, rows)
    with pytest.raises(CaptureError, match=expected):
        round_check.validate_round(capture, journal)
