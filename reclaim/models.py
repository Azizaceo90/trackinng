from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import Iterable


class Priority(str, Enum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"

    @property
    def rank(self) -> int:
        return {"P1": 0, "P2": 1, "P3": 2, "P4": 3}[self.value]


class EventKind(str, Enum):
    MEETING = "meeting"
    FOCUS = "focus"
    HABIT = "habit"
    TASK = "task"
    BUFFER = "buffer"
    BLOCKED = "blocked"
    OOO = "ooo"

    @property
    def is_flexible(self) -> bool:
        return self in {EventKind.FOCUS, EventKind.HABIT, EventKind.TASK, EventKind.BUFFER}


@dataclass(frozen=True)
class TimeSlot:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError(f"slot end {self.end} must be after start {self.start}")

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    def overlaps(self, other: TimeSlot) -> bool:
        return self.start < other.end and other.start < self.end

    def contains(self, other: TimeSlot) -> bool:
        return self.start <= other.start and other.end <= self.end

    def clip(self, window: TimeSlot) -> TimeSlot | None:
        start = max(self.start, window.start)
        end = min(self.end, window.end)
        if end <= start:
            return None
        return TimeSlot(start, end)


@dataclass
class Event:
    title: str
    slot: TimeSlot
    kind: EventKind = EventKind.MEETING
    calendar_id: str | None = None
    source_id: str | None = None  # id of the originating task/habit/goal
    movable: bool = False         # true for reclaim-created flexible events
    notes: str = ""

    @property
    def start(self) -> datetime:
        return self.slot.start

    @property
    def end(self) -> datetime:
        return self.slot.end


@dataclass
class Task:
    id: str
    title: str
    duration: timedelta
    due: datetime
    priority: Priority = Priority.P3
    min_chunk: timedelta = timedelta(minutes=30)
    max_chunk: timedelta = timedelta(hours=2)
    earliest_start: datetime | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        if self.duration <= timedelta(0):
            raise ValueError(f"task {self.id} duration must be positive")
        if self.min_chunk > self.duration:
            self.min_chunk = self.duration
        if self.max_chunk < self.min_chunk:
            self.max_chunk = self.min_chunk


@dataclass
class Habit:
    id: str
    title: str
    duration: timedelta
    # daily / weekly cadence
    days: list[int] = field(default_factory=lambda: [0, 1, 2, 3, 4])  # Mon=0
    times_per_week: int | None = None  # if set, overrides `days` and is scheduled flexibly
    ideal_window: tuple[time, time] | None = None  # (start, end) preferred time of day
    priority: Priority = Priority.P3
    kind: EventKind = EventKind.HABIT


@dataclass
class FocusGoal:
    weekly_hours: float = 20.0
    min_block: timedelta = timedelta(hours=1)
    max_block: timedelta = timedelta(hours=3)
    preferred_window: tuple[time, time] = (time(9, 0), time(12, 0))


@dataclass
class WorkingHours:
    # weekday (Mon=0) -> list of (start, end) time-of-day windows
    windows: dict[int, list[tuple[time, time]]] = field(default_factory=dict)

    def for_day(self, d: date) -> list[TimeSlot]:
        windows = self.windows.get(d.weekday(), [])
        return [
            TimeSlot(datetime.combine(d, start), datetime.combine(d, end))
            for start, end in windows
        ]

    @classmethod
    def standard_9_to_5(cls) -> WorkingHours:
        return cls(windows={i: [(time(9, 0), time(17, 0))] for i in range(0, 5)})


@dataclass
class Preferences:
    timezone: str = "UTC"
    working_hours: WorkingHours = field(default_factory=WorkingHours.standard_9_to_5)
    focus: FocusGoal = field(default_factory=FocusGoal)
    no_meeting_days: list[int] = field(default_factory=list)  # weekday indexes
    lunch_window: tuple[time, time] = (time(12, 0), time(13, 0))
    buffer_before_meeting: timedelta = timedelta(minutes=5)
    buffer_after_meeting: timedelta = timedelta(minutes=10)
    long_meeting_threshold: timedelta = timedelta(minutes=45)  # only buffer "long" meetings
    deep_work_morning: bool = True  # bias focus to mornings
    max_meeting_density_per_day: timedelta = timedelta(hours=5)  # alert if exceeded

    def is_no_meeting_day(self, d: date) -> bool:
        return d.weekday() in self.no_meeting_days

    def working_slots(self, day: date) -> list[TimeSlot]:
        return self.working_hours.for_day(day)


def iter_days(start: date, end: date) -> Iterable[date]:
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)
