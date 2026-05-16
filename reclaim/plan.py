"""Top-level planner that orchestrates focus, habits, tasks, and buffers."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from reclaim.buffers import add_buffers
from reclaim.focus import defend_focus_time
from reclaim.habits import schedule_habits
from reclaim.models import Event, EventKind, Habit, Preferences, Task
from reclaim.scheduler import schedule_tasks


@dataclass
class PlannedSchedule:
    focus: list[Event] = field(default_factory=list)
    habits: list[Event] = field(default_factory=list)
    tasks: list[Event] = field(default_factory=list)
    buffers: list[Event] = field(default_factory=list)
    unplaced: list[Task] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def all_events(self) -> list[Event]:
        return self.focus + self.habits + self.tasks + self.buffers

    def summary(self) -> str:
        lines = [
            f"Planned {len(self.focus)} focus block(s), "
            f"{len(self.habits)} habit(s), "
            f"{len(self.tasks)} task chunk(s), "
            f"{len(self.buffers)} buffer(s).",
        ]
        if self.unplaced:
            lines.append(f"Unplaced tasks ({len(self.unplaced)}):")
            for t in self.unplaced:
                lines.append(f"  - [{t.priority.value}] {t.title} ({t.duration})")
        for w in self.warnings:
            lines.append(f"WARN: {w}")
        return "\n".join(lines)


class Planner:
    def __init__(self, prefs: Preferences) -> None:
        self.prefs = prefs

    def plan(
        self,
        existing: list[Event],
        tasks: list[Task],
        habits: list[Habit],
        horizon_start: date,
        horizon_end: date,
    ) -> PlannedSchedule:
        result = PlannedSchedule()

        # Order: buffers around real meetings first so nothing books over them,
        # then habits, tasks (deadline-bound), focus (fills the rest).
        buffers = add_buffers(existing, self.prefs)
        result.buffers = buffers

        habit_events = schedule_habits(
            habits, existing + buffers, self.prefs, horizon_start, horizon_end
        )
        result.habits = habit_events

        task_events, unplaced = schedule_tasks(
            tasks,
            existing + buffers + habit_events,
            self.prefs,
            horizon_start,
            horizon_end,
        )
        result.tasks = task_events
        result.unplaced = unplaced

        focus_events = defend_focus_time(
            existing + buffers + habit_events + task_events,
            self.prefs,
            horizon_start,
            horizon_end,
        )
        result.focus = focus_events

        result.warnings.extend(self._density_warnings(existing, horizon_start, horizon_end))

        return result

    def _density_warnings(
        self, existing: list[Event], start: date, end: date
    ) -> list[str]:
        warnings: list[str] = []
        from collections import defaultdict
        per_day: dict[date, timedelta] = defaultdict(lambda: timedelta())
        for e in existing:
            if e.kind == EventKind.MEETING:
                per_day[e.slot.start.date()] += e.slot.duration
        for d, total in sorted(per_day.items()):
            if total > self.prefs.max_meeting_density_per_day:
                warnings.append(
                    f"{d}: {_fmt(total)} of meetings exceeds your cap of "
                    f"{_fmt(self.prefs.max_meeting_density_per_day)}"
                )
        return warnings


def _fmt(td: timedelta) -> str:
    minutes = int(td.total_seconds() // 60)
    h, m = divmod(minutes, 60)
    return f"{h}h{m:02d}m"
