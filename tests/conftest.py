from datetime import date, datetime, time, timedelta

import pytest

from reclaim.models import (
    Event,
    EventKind,
    FocusGoal,
    Habit,
    Preferences,
    Priority,
    Task,
    TimeSlot,
    WorkingHours,
)


@pytest.fixture
def monday() -> date:
    return date(2026, 5, 18)  # a Monday


@pytest.fixture
def prefs() -> Preferences:
    return Preferences(
        working_hours=WorkingHours(
            windows={
                i: [(time(9, 0), time(12, 0)), (time(13, 0), time(17, 0))]
                for i in range(0, 5)
            }
        ),
        focus=FocusGoal(
            weekly_hours=8,
            min_block=timedelta(hours=1),
            max_block=timedelta(hours=2),
            preferred_window=(time(9, 0), time(12, 0)),
        ),
        no_meeting_days=[4],
    )


def at(d, h, m=0):
    return datetime.combine(d, time(h, m))


def meeting(d, h_start, h_end, title="Meeting"):
    return Event(
        title=title,
        slot=TimeSlot(at(d, *_split(h_start)), at(d, *_split(h_end))),
        kind=EventKind.MEETING,
    )


def _split(h):
    if isinstance(h, tuple):
        return h
    return (int(h), 0)
