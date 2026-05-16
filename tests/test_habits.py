from datetime import date, time, timedelta

from reclaim.habits import schedule_habits
from reclaim.models import EventKind, Habit


def test_daily_lunch(monday, prefs):
    h = Habit(
        id="lunch", title="Lunch", duration=timedelta(minutes=45),
        days=[0, 1, 2, 3, 4],
        ideal_window=(time(12, 0), time(13, 30)),
    )
    events = schedule_habits([h], [], prefs, monday, monday + timedelta(days=4))
    assert len(events) == 5
    assert all(e.kind == EventKind.HABIT for e in events)
    # All should land in or near 12:00-13:30.
    for e in events:
        assert e.slot.start.hour in (12, 13)


def test_times_per_week(monday, prefs):
    h = Habit(
        id="gym", title="Gym", duration=timedelta(hours=1),
        times_per_week=3,
        days=[0, 1, 2, 3, 4],
    )
    events = schedule_habits([h], [], prefs, monday, monday + timedelta(days=4))
    assert len(events) == 3
