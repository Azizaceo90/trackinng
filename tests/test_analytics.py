from datetime import date, datetime, time, timedelta

from reclaim.analytics import analyze
from reclaim.models import Event, EventKind, TimeSlot
from tests.conftest import at, meeting


def _focus(d, h1, h2):
    return Event(
        title="Focus",
        slot=TimeSlot(at(d, h1), at(d, h2)),
        kind=EventKind.FOCUS,
        movable=True,
    )


def test_analyze_basic_totals(prefs):
    start = date(2026, 5, 18)
    end = date(2026, 5, 22)
    events = [
        meeting(date(2026, 5, 18), 10, 11),
        meeting(date(2026, 5, 19), 14, 15),
        _focus(date(2026, 5, 20), 9, 12),
    ]
    report = analyze(events, prefs, start, end)

    assert report.meeting_hours == 2.0
    assert report.focus_hours == 3.0
    # 5 days * 7h working hours each (9-12 + 13-17 = 7h)
    assert report.working_hours == 35.0
    assert report.deep_work_index == round(3.0 / 35.0, 2) or abs(
        report.deep_work_index - 3.0 / 35.0
    ) < 1e-6


def test_analyze_overload_detection(prefs):
    start = date(2026, 5, 18)
    end = date(2026, 5, 18)
    # 6 hours of meetings on Monday — over the 5h cap
    events = [
        meeting(start, 9, 12),
        meeting(start, 13, 16),
    ]
    report = analyze(events, prefs, start, end)
    assert start in report.overload_days
    assert report.meeting_hours == 6.0


def test_context_switches_counted(prefs):
    start = date(2026, 5, 18)
    end = date(2026, 5, 18)
    events = [
        meeting(start, 9, 10),     # MEETING
        _focus(start, 10, 11),     # FOCUS  -> switch 1
        meeting(start, 11, 12),    # MEETING -> switch 2
        meeting(start, 13, 14),    # MEETING -> no switch
        _focus(start, 14, 16),     # FOCUS  -> switch 3
    ]
    report = analyze(events, prefs, start, end)
    assert report.context_switches == 3


def test_longest_unbroken_focus(prefs):
    start = date(2026, 5, 18)
    end = date(2026, 5, 18)
    events = [
        _focus(start, 9, 11),
        _focus(start, 11, 12),   # contiguous -> 3h run
        _focus(start, 14, 15),   # separate 1h run
    ]
    report = analyze(events, prefs, start, end)
    assert report.longest_unbroken_focus == timedelta(hours=3)


def test_analyze_render_contains_key_lines(prefs):
    start = date(2026, 5, 18)
    end = date(2026, 5, 22)
    report = analyze([], prefs, start, end)
    text = report.render()
    assert "Weekly report" in text
    assert "Deep-work index" in text
    assert "Fragmentation idx" in text
