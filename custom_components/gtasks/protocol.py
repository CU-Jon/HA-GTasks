"""Tasks Sync schedule codec, derived from browser captures on 2026-11-14.

Never replay an old mutation: timestamps, record
IDs and entity version must come from the current operation/read. Query-only
reads omit a sync cursor; their write envelope uses the operation's timestamp.
"""

import json
import re
import secrets
import time as system_time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo


class ProtocolError(ValueError):
    """Safe, content-free protocol/validation error."""


def sync_read_body():
    return [
        [
            [
                [None, []],
                [[]],
                [None, None, []],
                [None, None, None, []],
                [None, None, None, None, None, []],
                [None, None, None, None, [None, None, [None, 1, None, 1, 1, 1]]],
                [None, None, None, None, [None, None, [1, None, None, 1, 1, 1]]],
            ],
            [],
        ]
    ]


def slot(value, index, default=None):
    return value[index] if isinstance(value, list) and len(value) > index else default


@dataclass(frozen=True)
class Schedule:
    due: date
    clock: time | None
    timezone: str
    epoch_seconds: int

    def wire(self):
        clock = None
        if self.clock is not None:
            clock = [self.clock.hour, self.clock.minute]
            # Match captured noon's [12]. Keep a present message for midnight.
            while len(clock) > 1 and clock[-1] == 0:
                clock.pop()
        return [
            None,
            [self.due.year, self.due.month, self.due.day],
            clock,
            self.timezone,
            None,
            [self.epoch_seconds],
        ]


def make_schedule(due: str, clock: str | None, timezone: str) -> Schedule:
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due):
            raise ValueError
        day = date.fromisoformat(due)
        if clock is not None and not re.fullmatch(r"\d{2}:\d{2}", clock):
            raise ValueError
        wall_clock = time.fromisoformat(clock) if clock is not None else None
        zone = ZoneInfo(timezone)
        naive = datetime.combine(day, wall_clock or time())
        instants = set()
        for fold in (0, 1):
            candidate = naive.replace(tzinfo=zone, fold=fold)
            if candidate.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == naive:
                instants.add(int(candidate.timestamp()))
        if len(instants) != 1:
            raise ProtocolError("Schedule is ambiguous or nonexistent in this timezone.")
        return Schedule(day, wall_clock, timezone, instants.pop())
    except ProtocolError:
        raise
    except ValueError, TypeError, KeyError:
        raise ProtocolError("Invalid schedule date, time or timezone.") from None


def decode_schedule(value) -> Schedule | None:
    if value in (None, []):
        return None
    schedule = slot(value, 0)
    try:
        parts = slot(schedule, 1)
        day = date(*parts)
        clock_parts = slot(schedule, 2)
        wall_clock = None
        if clock_parts is not None:
            if not isinstance(clock_parts, list) or len(clock_parts) > 4:
                raise ValueError
            parts_with_defaults = [(v or 0) for v in clock_parts] + [0] * 4
            hour, minute, second, nanos = parts_with_defaults[:4]
            if second or nanos:
                raise ValueError
            # An empty TimeOfDay message is present midnight; null is date-only.
            wall_clock = time(hour, minute)
        timezone = slot(schedule, 3)
        epoch = int(slot(slot(schedule, 5), 0))
        resolved = datetime.fromtimestamp(epoch, UTC).astimezone(ZoneInfo(timezone))
        if resolved.date() != day or (
            wall_clock is not None and resolved.time().replace(tzinfo=None) != wall_clock
        ):
            raise ValueError
        return Schedule(day, wall_clock, timezone, epoch)
    except ValueError, TypeError, KeyError, OverflowError:
        raise ProtocolError("Unrecognized or inconsistent schedule in Tasks response.") from None


@dataclass(frozen=True)
class TaskRecord:
    task_id: str = field(repr=False)
    list_id: str | None = field(repr=False)
    title: str = field(repr=False)
    notes: str | None = field(repr=False)
    completed: bool
    due: date | None
    version: int | None
    schedule_wire: object = field(repr=False)
    recurring: bool = False

    @property
    def schedule(self):
        return decode_schedule(self.schedule_wire)


@dataclass(frozen=True)
class Snapshot:
    tasks: dict[str, TaskRecord] = field(repr=False)
    cursor: list | None = field(repr=False)
    list_ids: frozenset[str] = field(default_factory=frozenset, repr=False)


def decode_sync_response(raw) -> Snapshot:
    try:
        if isinstance(raw, str):
            raw = raw.strip()
            if raw.startswith(")]}'"):
                raw = raw.partition("\n")[2]
            raw = json.loads(raw)
        if not isinstance(raw, list) or not isinstance(slot(raw, 0), list):
            raise ValueError
        tasks = {}
        lists = set()
        for wrapper in raw[0]:
            if not isinstance(wrapper, list):
                continue
            for record in wrapper:
                task_id, details = slot(record, 0), slot(record, 1)
                if isinstance(task_id, str) and isinstance(slot(details, 0), str):
                    lists.add(task_id)
                if (
                    not isinstance(task_id, str)
                    or not task_id
                    or not isinstance(details, list)
                    or slot(details, 0) not in (None, 0, 1)
                    or not isinstance(slot(details, 1, "") or "", str)
                ):
                    continue
                due_parts = slot(details, 3)
                version = slot(slot(record, 7), 1)
                if isinstance(slot(record, 3), str):
                    lists.add(record[3])
                tasks[task_id] = TaskRecord(
                    task_id,
                    slot(record, 3),
                    slot(details, 1, "") or "",
                    slot(details, 2),
                    slot(details, 0) == 1,
                    date(*due_parts) if due_parts else None,
                    int(version) if version is not None else None,
                    slot(record, 8),
                    bool(slot(details, 9)),
                )
        cursor = slot(slot(raw, 2), 0)
        if cursor is not None:
            if not isinstance(cursor, list) or not cursor or len(cursor) > 2:
                raise ValueError
            seconds = int(cursor[0])
            nanos = int(slot(cursor, 1, 0) or 0)
            if seconds < 0 or not 0 <= nanos < 1_000_000_000:
                raise ValueError
        return Snapshot(tasks, cursor, frozenset(lists))
    except ValueError, TypeError, OverflowError:
        raise ProtocolError("Unrecognized Tasks Sync response.") from None


def build_schedule_mutation(
    snapshot: Snapshot, task_id: str, schedule: Schedule | None, *, now_ns: int | None = None
):
    task = snapshot.tasks.get(task_id)
    if task is None or task.version is None:
        raise ProtocolError("A fresh task version is required before writing.")
    tick = system_time.time_ns() if now_ns is None else now_ns
    modified = [tick // 1_000_000_000, tick % 1_000_000_000]
    records = []
    for index in (0, 1):
        record = [None] * 28
        record[0] = secrets.token_urlsafe(12)
        record[1] = 3
        record[2] = task_id
        record[3] = [1] if index == 0 else [1, 1]
        record[9] = modified.copy()
        record[25] = 1
        record[26] = None if index == 0 else 1
        record[27] = [None, task.version] if index == 0 else [1]
        records.append(record)
    properties = (
        []
        if schedule is None
        else [None, None, None, [schedule.due.year, schedule.due.month, schedule.due.day]]
    )
    records[0][6] = [[None, [4]], properties]
    records[1][11] = [[None, [1]], [] if schedule is None else [schedule.wire()]]
    # Same payload as SOV0tb, omitting browser-only account/client metadata.
    cursor = snapshot.cursor if snapshot.cursor is not None else modified.copy()
    envelope = [None, [cursor, None, [[records, []]]]]
    return [envelope, None, None, None, None, []]
