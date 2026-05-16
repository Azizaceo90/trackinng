"""Tests for the markdown time-report dashboard."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from reclaim.models import Event, EventKind, TimeSlot
from reclaim.report import (
    build_report,
    load_events_json,
    render_markdown,
    resolve_window,
)


def _ev(title: str, start: datetime, hours: float, kind: EventKind = EventKind.MEETING) -> Event:
    return Event(
        title=title,
        slot=TimeSlot(start, start + timedelta(hours=hours)),
        kind=kind,
    )


def test_resolve_window_day():
    d = date(2026, 5, 16)
    assert resolve_window("day", d) == (d, d)


def test_resolve_window_week_starts_monday():
    # 2026-05-16 is a Saturday → week Mon 5/11 → Sun 5/17
    d = date(2026, 5, 16)
    assert resolve_window("week", d) == (date(2026, 5, 11), date(2026, 5, 17))


def test_resolve_window_month():
    d = date(2026, 5, 16)
    assert resolve_window("month", d) == (date(2026, 5, 1), date(2026, 5, 31))


def test_resolve_window_december_rollover():
    d = date(2026, 12, 31)
    assert resolve_window("month", d) == (date(2026, 12, 1), date(2026, 12, 31))


def test_build_report_categorizes_by_kind_and_title():
    base = datetime(2026, 5, 11)  # Monday
    events = [
        _ev("Interview — Valenz", base.replace(hour=11), 0.75),
        _ev("[Reclaim] Focus block", base.replace(hour=14), 2.0, kind=EventKind.FOCUS),
        _ev("Team standup", base.replace(hour=9), 0.5),
        _ev("[Reclaim] Habit: gym", base.replace(hour=18), 1.0, kind=EventKind.HABIT),
    ]
    report = build_report(events, base.date(), base.date(), "day")
    monday = report.days[0]
    assert monday.hours["Interviews"] == pytest.approx(0.75)
    assert monday.hours["Focus"] == pytest.approx(2.0)
    assert monday.hours["Meetings"] == pytest.approx(0.5)
    assert monday.hours["Habits"] == pytest.approx(1.0)
    assert monday.interview_count == 1


def test_build_report_clips_events_at_day_boundary():
    base = datetime(2026, 5, 11, 22)  # 10pm Monday
    events = [_ev("Late call", base, 4.0)]  # spans into Tuesday
    report = build_report(events, date(2026, 5, 11), date(2026, 5, 12), "week")
    assert report.days[0].hours["Meetings"] == pytest.approx(2.0)
    assert report.days[1].hours["Meetings"] == pytest.approx(2.0)


def test_render_markdown_includes_totals_and_highlights():
    base = datetime(2026, 5, 11)
    events = [
        _ev("Interview — Valenz", base.replace(hour=11), 0.75),
        _ev("[Reclaim] Focus", base.replace(hour=14), 2.5, kind=EventKind.FOCUS),
    ]
    report = build_report(events, date(2026, 5, 11), date(2026, 5, 17), "week")
    md = render_markdown(report)
    assert "# Time report — week" in md
    assert "**Total**" in md
    assert "Interviews this week:** 1" in md
    assert "Longest focus run:** 2h 30m" in md


def test_load_events_json_infers_kind_from_title(tmp_path: Path):
    data = [
        {"summary": "[Reclaim] Focus block",
         "start": "2026-05-11T09:00:00", "end": "2026-05-11T10:30:00"},
        {"summary": "Interview — Acme",
         "start": "2026-05-11T13:00:00", "end": "2026-05-11T13:30:00"},
        {"summary": "Lunch",
         "start": "2026-05-11T12:00:00", "end": "2026-05-11T13:00:00"},
    ]
    p = tmp_path / "events.json"
    p.write_text(json.dumps(data))
    events = load_events_json(p)
    kinds = {e.title: e.kind for e in events}
    assert kinds["[Reclaim] Focus block"] == EventKind.FOCUS
    assert kinds["Interview — Acme"] == EventKind.MEETING
    assert kinds["Lunch"] == EventKind.BLOCKED


def test_load_events_json_handles_google_calendar_shape(tmp_path: Path):
    data = [
        {"summary": "Standup",
         "start": {"dateTime": "2026-05-11T09:00:00-04:00", "timeZone": "America/Detroit"},
         "end":   {"dateTime": "2026-05-11T09:15:00-04:00", "timeZone": "America/Detroit"}},
    ]
    p = tmp_path / "events.json"
    p.write_text(json.dumps(data))
    events = load_events_json(p)
    assert len(events) == 1
    assert events[0].title == "Standup"
