from datetime import timedelta

from reclaim.focus import defend_focus_time
from reclaim.models import EventKind


def test_books_weekly_focus_goal(monday, prefs):
    # Goal is 8h/week with min 1h, max 2h, no existing events.
    events = defend_focus_time([], prefs, monday, monday + timedelta(days=4))
    total = sum((e.slot.duration for e in events), timedelta())
    assert total == timedelta(hours=8)
    assert all(e.kind == EventKind.FOCUS for e in events)


def test_prefers_morning_window(monday, prefs):
    events = defend_focus_time([], prefs, monday, monday + timedelta(days=4))
    morning = [e for e in events if e.slot.start.hour < 12]
    assert len(morning) >= len(events) // 2


def test_respects_existing_focus(monday, prefs):
    # Pretend 6h of focus already on the calendar.
    from reclaim.models import Event, TimeSlot
    from tests.conftest import at
    pre_existing = [
        Event(
            title="pre-focus",
            slot=TimeSlot(at(monday, 9), at(monday, 12)),
            kind=EventKind.FOCUS,
        ),
        Event(
            title="pre-focus 2",
            slot=TimeSlot(at(monday + timedelta(days=1), 9), at(monday + timedelta(days=1), 12)),
            kind=EventKind.FOCUS,
        ),
    ]
    events = defend_focus_time(pre_existing, prefs, monday, monday + timedelta(days=4))
    new_total = sum((e.slot.duration for e in events), timedelta())
    # Goal 8h, already 6h, so should book ~2h more.
    assert new_total == timedelta(hours=2)
