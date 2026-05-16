"""Productivity analytics: focus, meeting load, fragmentation, deep-work index."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from reclaim.models import Event, EventKind, Preferences, iter_days


@dataclass
class WeeklyReport:
    start: date
    end: date
    focus_hours: float = 0.0
    meeting_hours: float = 0.0
    habit_hours: float = 0.0
    task_hours: float = 0.0
    buffer_hours: float = 0.0
    working_hours: float = 0.0
    longest_unbroken_focus: timedelta = timedelta()
    context_switches: int = 0  # transitions between distinct event kinds
    fragmentation_index: float = 0.0  # 0..1, higher = more fragmented
    deep_work_index: float = 0.0      # 0..1, focus / working time
    meeting_load: float = 0.0         # 0..1, meeting / working time
    overload_days: list[date] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            f"=== Weekly report: {self.start} → {self.end} ===",
            f"  Working hours     : {self.working_hours:5.1f}h",
            f"  Focus time        : {self.focus_hours:5.1f}h",
            f"  Meeting time      : {self.meeting_hours:5.1f}h",
            f"  Habits            : {self.habit_hours:5.1f}h",
            f"  Tasks             : {self.task_hours:5.1f}h",
            f"  Buffers           : {self.buffer_hours:5.1f}h",
            "",
            f"  Longest focus run : {_fmt(self.longest_unbroken_focus)}",
            f"  Context switches  : {self.context_switches}",
            f"  Fragmentation idx : {self.fragmentation_index:.2f}  (0 = solid, 1 = shredded)",
            f"  Deep-work index   : {self.deep_work_index:.2f}  (focus / working)",
            f"  Meeting load      : {self.meeting_load:.2f}  (meetings / working)",
        ]
        if self.overload_days:
            lines.append("")
            lines.append("  Overload days (>5h meetings):")
            for d in self.overload_days:
                lines.append(f"    - {d:%a %Y-%m-%d}")
        return "\n".join(lines)


def analyze(
    events: list[Event],
    prefs: Preferences,
    start: date,
    end: date,
) -> WeeklyReport:
    report = WeeklyReport(start=start, end=end)

    working_total = timedelta()
    for d in iter_days(start, end):
        for w in prefs.working_slots(d):
            working_total += w.duration
    report.working_hours = _hours(working_total)

    totals: dict[EventKind, timedelta] = defaultdict(lambda: timedelta())
    meeting_per_day: dict[date, timedelta] = defaultdict(lambda: timedelta())
    for e in events:
        if not (start <= e.slot.start.date() <= end):
            continue
        totals[e.kind] += e.slot.duration
        if e.kind == EventKind.MEETING:
            meeting_per_day[e.slot.start.date()] += e.slot.duration

    report.focus_hours = _hours(totals[EventKind.FOCUS])
    report.meeting_hours = _hours(totals[EventKind.MEETING])
    report.habit_hours = _hours(totals[EventKind.HABIT])
    report.task_hours = _hours(totals[EventKind.TASK])
    report.buffer_hours = _hours(totals[EventKind.BUFFER])

    report.longest_unbroken_focus = _longest_run(events, EventKind.FOCUS)
    report.context_switches = _context_switches(events, start, end)

    # Fragmentation: switches normalized by number of working days.
    days = max(1, (end - start).days + 1)
    report.fragmentation_index = min(1.0, report.context_switches / (days * 8))

    if report.working_hours > 0:
        report.deep_work_index = report.focus_hours / report.working_hours
        report.meeting_load = report.meeting_hours / report.working_hours

    cap = prefs.max_meeting_density_per_day
    report.overload_days = [d for d, t in sorted(meeting_per_day.items()) if t > cap]

    return report


def _hours(td: timedelta) -> float:
    return round(td.total_seconds() / 3600.0, 2)


def _fmt(td: timedelta) -> str:
    minutes = int(td.total_seconds() // 60)
    h, m = divmod(minutes, 60)
    return f"{h}h{m:02d}m"


def _longest_run(events: list[Event], kind: EventKind) -> timedelta:
    by_day: dict[date, list[Event]] = defaultdict(list)
    for e in events:
        if e.kind == kind:
            by_day[e.slot.start.date()].append(e)
    longest = timedelta()
    for day_events in by_day.values():
        day_events.sort(key=lambda e: e.slot.start)
        run_start: datetime | None = None
        run_end: datetime | None = None
        for e in day_events:
            if run_end is not None and e.slot.start <= run_end:
                run_end = max(run_end, e.slot.end)
            else:
                if run_start is not None and run_end is not None:
                    longest = max(longest, run_end - run_start)
                run_start, run_end = e.slot.start, e.slot.end
        if run_start is not None and run_end is not None:
            longest = max(longest, run_end - run_start)
    return longest


def _context_switches(events: list[Event], start: date, end: date) -> int:
    in_range = [
        e for e in events
        if start <= e.slot.start.date() <= end
        and e.kind in {EventKind.MEETING, EventKind.FOCUS, EventKind.TASK}
    ]
    in_range.sort(key=lambda e: e.slot.start)
    switches = 0
    prev_kind: EventKind | None = None
    prev_day: date | None = None
    for e in in_range:
        d = e.slot.start.date()
        if prev_kind is None or d != prev_day:
            prev_kind = e.kind
            prev_day = d
            continue
        if e.kind != prev_kind:
            switches += 1
        prev_kind = e.kind
    return switches
