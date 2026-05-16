from datetime import datetime, time, timedelta

from reclaim.freebusy import free_slots_for_day, subtract, total_free
from reclaim.models import TimeSlot
from tests.conftest import at, meeting


def test_subtract_no_overlap():
    win = TimeSlot(datetime(2026, 5, 18, 9), datetime(2026, 5, 18, 17))
    assert subtract(win, []) == [win]


def test_subtract_inside():
    win = TimeSlot(datetime(2026, 5, 18, 9), datetime(2026, 5, 18, 17))
    busy = [TimeSlot(datetime(2026, 5, 18, 10), datetime(2026, 5, 18, 11))]
    out = subtract(win, busy)
    assert len(out) == 2
    assert out[0].end.hour == 10
    assert out[1].start.hour == 11


def test_subtract_overlapping_busy_merge():
    win = TimeSlot(datetime(2026, 5, 18, 9), datetime(2026, 5, 18, 17))
    busy = [
        TimeSlot(datetime(2026, 5, 18, 10), datetime(2026, 5, 18, 12)),
        TimeSlot(datetime(2026, 5, 18, 11), datetime(2026, 5, 18, 13)),
    ]
    out = subtract(win, busy)
    assert len(out) == 2
    assert out[0].end.hour == 10
    assert out[1].start.hour == 13


def test_free_slots_for_day(monday, prefs):
    events = [meeting(monday, 10, 11)]
    free = free_slots_for_day(monday, events, prefs)
    # Working day = 9-12 and 13-17. Subtract 10-11.
    # Expect: 9-10, 11-12, 13-17.
    assert [(s.start.hour, s.end.hour) for s in free] == [(9, 10), (11, 12), (13, 17)]
    assert total_free(free) == timedelta(hours=6)
