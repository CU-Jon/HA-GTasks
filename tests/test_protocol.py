"""Capture-based codec checks and calendar edge cases."""

import json
from copy import deepcopy
from datetime import date, datetime, time
from pathlib import Path

import pytest

from custom_components.gtasks.native import due_for_record, private_id, public_id, schedule_for_due
from custom_components.gtasks.protocol import (
    ProtocolError,
    Snapshot,
    TaskRecord,
    build_schedule_mutation,
    decode_schedule,
    decode_sync_response,
    make_schedule,
)

CAPTURE = json.loads((Path(__file__).parent / "fixtures/tasks_schedule_capture.json").read_text())


@pytest.mark.parametrize("source", ["calendar", "tasks"])
@pytest.mark.parametrize("stage", ["date_only", "noon", "completed"])
def test_captured_schedule(source, stage):
    snapshot = decode_sync_response(CAPTURE["sources"][source][stage]["response"])
    record = snapshot.tasks["private-test-task"]
    assert record.due == date(2026, 11, 15)
    assert record.completed == (stage == "completed")
    assert record.schedule.clock == (None if stage == "date_only" else time(12))
    assert record.schedule.timezone == "America/New_York"


@pytest.mark.parametrize("clock", [None, "00:00", "12:00", "12:37", "23:59"])
def test_roundtrip_and_versioned_mutation(clock):
    snapshot = decode_sync_response(CAPTURE["sources"]["tasks"]["noon"]["response"])
    wanted = make_schedule("2026-11-16", clock, "America/New_York")
    assert decode_schedule([wanted.wire()]) == wanted
    snapshot = Snapshot(snapshot.tasks, None)
    packet = build_schedule_mutation(
        snapshot, "private-test-task", wanted, now_ns=1794780000123456789
    )
    envelope = packet[0][1]
    assert envelope[0] == [1794780000, 123456789]
    records = envelope[2][0][0]
    assert records[0][27] == [None, snapshot.tasks["private-test-task"].version]
    assert records[1][11][1] == [wanted.wire()]
    assert records[0][0] != records[1][0]
    assert records[0][6][1][3] == [2026, 11, 16]


def test_clear_fields():
    snapshot = decode_sync_response(CAPTURE["sources"]["tasks"]["noon"]["response"])
    records = build_schedule_mutation(snapshot, "private-test-task", None)[0][1][2][0][0]
    assert records[0][6] == [[None, [4]], []]
    assert records[1][11] == [[None, [1]], []]


@pytest.mark.parametrize("clock", [[], [0], [None, 0]])
def test_sparse_midnight_is_not_date_only(clock):
    wire = make_schedule("2026-11-16", "00:00", "America/New_York").wire()
    wire[2] = clock
    assert decode_schedule([wire]).clock == time(0)


@pytest.mark.parametrize("day,clock", [("2026-03-08", "02:30"), ("2026-11-01", "01:30")])
def test_dst_wall_time_rejected(day, clock):
    with pytest.raises(ProtocolError):
        make_schedule(day, clock, "America/New_York")


def test_explicit_offset_resolves_dst():
    for offset in ("-04:00", "-05:00"):
        due = datetime.fromisoformat("2026-11-01T01:30:00" + offset)
        result = schedule_for_due(due, "America/New_York")
        assert result.epoch_seconds == int(due.timestamp())
        assert decode_schedule([result.wire()]) == result


def test_aware_conversion_and_date_only():
    due = datetime.fromisoformat("2026-11-16T00:00:00+00:00")
    result = schedule_for_due(due, "America/New_York")
    assert result.due == date(2026, 11, 15)
    assert result.clock == time(19)
    assert schedule_for_due(date(2026, 11, 16), "America/New_York").clock is None


@pytest.mark.parametrize("value", ["bad!", "", "a", "_w"])
def test_bad_ids(value):
    from custom_components.gtasks.exceptions import TasksError

    with pytest.raises(TasksError):
        private_id(value)


def test_id_mapping():
    assert private_id(public_id("private-task-id")) == "private-task-id"


def test_list_extraction():
    raw = deepcopy(CAPTURE["sources"]["tasks"]["noon"]["response"])
    raw[0].append([["private-test-list", ["List title"]]])
    result = decode_sync_response(raw)
    assert result.list_ids == {"private-test-list"}


@pytest.mark.parametrize("details", [[], [None], [None, None], [None, ""]])
def test_empty_task_title_is_not_lost(details):
    raw = CAPTURE["sources"]["tasks"]["noon"]["response"]
    record = deepcopy(raw[0][0][0])
    record[1] = details
    result = decode_sync_response([[[record]]])
    assert result.tasks["private-test-task"].title == ""
    assert result.list_ids == {"private-test-list"}


def test_content_is_not_in_repr():
    record = TaskRecord("secret-id", "list", "private-title", "private-notes", False, None, 1, None)
    assert "private" not in repr(record)
    assert due_for_record(record) is None


@pytest.mark.parametrize("raw", ["not json", {}, [], [None]])
def test_invalid_envelope(raw):
    with pytest.raises(ProtocolError):
        decode_sync_response(raw)
