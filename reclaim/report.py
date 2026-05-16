"""Markdown time report: day / week / month breakdown from calendar events.

Reads events from a JSON dump (Google Calendar export shape, or planner
output), classifies each event by title pattern when ``kind`` is absent,
and emits a markdown summary suitable for piping to a terminal or
pasting into a doc.

CLI: ``reclaim report --window week --events <path>``
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from reclaim.models import Event, EventKind, TimeSlot


# ---------------------------------------------------------------- categories ---

# Display category → set of EventKinds it absorbs.
CATEGORIES = ["Focus", "Meetings", "Interviews", "Habits", "Tasks", "Buffers", "Other"]


def _infer_kind(title: str) -> EventKind:
    t = title.lower().strip()
    if t.startswith("[reclaim] focus") or "deep work" in t or "focus block" in t:
        return EventKind.FOCUS
    if t.startswith("interview ") or " interview" in t or t.startswith("interview—"):
        return EventKind.MEETING  # interviews counted separately by classifier
    if t.startswith("[reclaim] habit") or t.startswith("[draft] habit"):
        return EventKind.HABIT
    if t.startswith("[reclaim] task") or t.startswith("[draft] task"):
        return EventKind.TASK
    if t.startswith("buffer") or "[reclaim] buffer" in t:
        return EventKind.BUFFER
    if t.startswith("ooo") or "out of office" in t:
        return EventKind.OOO
    if t.startswith("blocked") or "lunch" in t or "sleep" in t:
        return EventKind.BLOCKED
    return EventKind.MEETING


def _classify(event: Event) -> str:
    title = event.title.lower()
    if "interview" in title:
        return "Interviews"
    if event.kind == EventKind.FOCUS:
        return "Focus"
    if event.kind == EventKind.HABIT:
        return "Habits"
    if event.kind == EventKind.TASK:
        return "Tasks"
    if event.kind == EventKind.BUFFER:
        return "Buffers"
    if event.kind in (EventKind.BLOCKED, EventKind.OOO):
        return "Other"
    return "Meetings"


# -------------------------------------------------------------------- loader ---


def load_events_json(path: Path) -> list[Event]:
    import json
    raw = json.loads(path.read_text())
    out: list[Event] = []
    for e in raw:
        title = e.get("title") or e.get("summary") or ""
        start = _parse_dt(e.get("start"))
        end = _parse_dt(e.get("end"))
        if not (title and start and end and end > start):
            continue
        kind_raw = e.get("kind")
        try:
            kind = EventKind(kind_raw) if kind_raw else _infer_kind(title)
        except ValueError:
            kind = _infer_kind(title)
        out.append(Event(title=title, slot=TimeSlot(start, end), kind=kind))
    return out


def _parse_dt(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, dict):  # google calendar shape: {"dateTime": "...", "timeZone": "..."}
        value = value.get("dateTime") or value.get("date")
        if value is None:
            return None
    s = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    # Strip tzinfo for arithmetic — we want wall-clock hours.
    return dt.replace(tzinfo=None)


# ------------------------------------------------------------- window helpers ---


def resolve_window(window: str, anchor: date) -> tuple[date, date]:
    """Return inclusive [start, end] dates for the given window centred on anchor."""
    window = window.lower()
    if window == "day":
        return anchor, anchor
    if window == "week":
        start = anchor - timedelta(days=anchor.weekday())  # Monday
        return start, start + timedelta(days=6)
    if window == "month":
        start = anchor.replace(day=1)
        # Last day of this month
        if start.month == 12:
            next_month = start.replace(year=start.year + 1, month=1)
        else:
            next_month = start.replace(month=start.month + 1)
        end = next_month - timedelta(days=1)
        return start, end
    raise ValueError(f"unknown window: {window!r} (use day/week/month)")


# -------------------------------------------------------------- aggregation ---


@dataclass
class DayTotals:
    day: date
    hours: dict[str, float] = field(default_factory=lambda: {c: 0.0 for c in CATEGORIES})
    interview_count: int = 0
    longest_focus: timedelta = timedelta()

    @property
    def total_hours(self) -> float:
        return sum(self.hours.values())


@dataclass
class Report:
    start: date
    end: date
    days: list[DayTotals]
    window_label: str

    @property
    def total_hours(self) -> dict[str, float]:
        agg: dict[str, float] = {c: 0.0 for c in CATEGORIES}
        for d in self.days:
            for k, v in d.hours.items():
                agg[k] += v
        return agg

    @property
    def total_interviews(self) -> int:
        return sum(d.interview_count for d in self.days)


def _hours(td: timedelta) -> float:
    return td.total_seconds() / 3600.0


def build_report(events: list[Event], start: date, end: date, window_label: str) -> Report:
    days = []
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        day_start = datetime.combine(day, datetime.min.time())
        day_end = day_start + timedelta(days=1)
        totals = DayTotals(day=day)
        longest = timedelta()
        for ev in events:
            clipped = ev.slot.clip(TimeSlot(day_start, day_end))
            if clipped is None:
                continue
            dur = clipped.duration
            cat = _classify(ev)
            totals.hours[cat] += _hours(dur)
            if cat == "Interviews":
                # one interview per event per day, not per minute
                if datetime.combine(day, datetime.min.time()) <= ev.start < day_end:
                    totals.interview_count += 1
            if ev.kind == EventKind.FOCUS and dur > longest:
                longest = dur
        totals.longest_focus = longest
        days.append(totals)
    return Report(start=start, end=end, days=days, window_label=window_label)


# ------------------------------------------------------------------ render ---


def _fmt_h(hours: float) -> str:
    if hours == 0:
        return "—"
    return f"{hours:.1f}h"


def _fmt_td(td: timedelta) -> str:
    total = int(td.total_seconds())
    if total <= 0:
        return "—"
    h, rem = divmod(total, 3600)
    m = rem // 60
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{m}m"


def render_markdown(report: Report) -> str:
    title = (
        f"# Time report — {report.window_label} "
        f"({report.start:%a %b %-d} → {report.end:%a %b %-d, %Y})"
    )
    cats = CATEGORIES
    rows = []
    header = "| Day | " + " | ".join(cats) + " | Total |"
    sep = "|---|" + "|".join("---" for _ in cats) + "|---|"
    rows.extend([header, sep])
    for d in report.days:
        cells = [f"{d.day:%a %m/%d}"]
        for c in cats:
            cells.append(_fmt_h(d.hours[c]))
        cells.append(_fmt_h(d.total_hours))
        rows.append("| " + " | ".join(cells) + " |")
    totals = report.total_hours
    total_cells = ["**Total**"]
    for c in cats:
        total_cells.append(f"**{_fmt_h(totals[c])}**")
    total_cells.append(f"**{_fmt_h(sum(totals.values()))}**")
    rows.append("| " + " | ".join(total_cells) + " |")

    busiest = max(report.days, key=lambda d: d.total_hours, default=None)
    deepest_focus = max(report.days, key=lambda d: d.longest_focus, default=None)
    most_meetings = max(
        report.days, key=lambda d: d.hours["Meetings"] + d.hours["Interviews"], default=None
    )

    highlights = ["", "## Highlights"]
    highlights.append(f"- **Interviews this {report.window_label}:** {report.total_interviews}")
    if deepest_focus and deepest_focus.longest_focus > timedelta():
        highlights.append(
            f"- **Longest focus run:** {_fmt_td(deepest_focus.longest_focus)} "
            f"on {deepest_focus.day:%a %m/%d}"
        )
    if busiest and busiest.total_hours > 0:
        highlights.append(
            f"- **Busiest day:** {busiest.day:%a %m/%d} "
            f"({_fmt_h(busiest.total_hours)})"
        )
    if most_meetings:
        m_hours = most_meetings.hours["Meetings"] + most_meetings.hours["Interviews"]
        if m_hours > 0:
            highlights.append(
                f"- **Heaviest meeting day:** {most_meetings.day:%a %m/%d} "
                f"({_fmt_h(m_hours)})"
            )
    focus_total = totals["Focus"]
    meet_total = totals["Meetings"] + totals["Interviews"]
    tracked = sum(totals.values())
    if tracked > 0:
        highlights.append(
            f"- **Deep-work share:** {focus_total / tracked * 100:.0f}% "
            f"(focus / tracked time)"
        )
        highlights.append(
            f"- **Meeting load:** {meet_total / tracked * 100:.0f}% "
            f"(meetings + interviews / tracked time)"
        )

    return "\n".join([title, ""] + rows + highlights) + "\n"
