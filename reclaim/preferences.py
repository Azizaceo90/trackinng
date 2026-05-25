"""Load Preferences, Tasks, and Habits from YAML config."""
from __future__ import annotations

from datetime import datetime, time, timedelta
from pathlib import Path

import yaml

from reclaim.models import (
    FocusGoal,
    Habit,
    Preferences,
    Priority,
    Task,
    WorkingHours,
)


WEEKDAY_NAMES = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def _parse_time(s: str) -> time:
    return time.fromisoformat(s)


def _parse_duration(s: str | int | float) -> timedelta:
    if isinstance(s, (int, float)):
        return timedelta(minutes=float(s))
    s = s.strip().lower()
    # Support combined forms like "1h30m" or "2h15m"
    if "h" in s and s.endswith("m") and not s.endswith("min"):
        h_part, m_part = s.split("h", 1)
        m_part = m_part[:-1] or "0"
        return timedelta(hours=float(h_part), minutes=float(m_part))
    if s.endswith("min"):
        return timedelta(minutes=float(s[:-3]))
    if s.endswith("h"):
        return timedelta(hours=float(s[:-1]))
    if s.endswith("m"):
        return timedelta(minutes=float(s[:-1]))
    return timedelta(minutes=float(s))


def _parse_weekday(v: str | int) -> int:
    if isinstance(v, int):
        return v
    return WEEKDAY_NAMES[v.lower()]


def load_preferences(path: str | Path) -> Preferences:
    data = yaml.safe_load(Path(path).read_text())
    wh_raw = data.get("working_hours", {})
    windows: dict[int, list[tuple[time, time]]] = {}
    for day_key, ranges in wh_raw.items():
        wd = _parse_weekday(day_key)
        windows[wd] = [(_parse_time(r["start"]), _parse_time(r["end"])) for r in ranges]
    working_hours = WorkingHours(windows=windows) if windows else WorkingHours.standard_9_to_5()

    focus_raw = data.get("focus", {})
    focus_pref_win = focus_raw.get("preferred_window", {"start": "09:00", "end": "12:00"})
    focus = FocusGoal(
        weekly_hours=float(focus_raw.get("weekly_hours", 20.0)),
        min_block=_parse_duration(focus_raw.get("min_block", "1h")),
        max_block=_parse_duration(focus_raw.get("max_block", "3h")),
        preferred_window=(_parse_time(focus_pref_win["start"]), _parse_time(focus_pref_win["end"])),
    )

    lunch_raw = data.get("lunch_window", {"start": "12:00", "end": "13:00"})

    return Preferences(
        timezone=data.get("timezone", "UTC"),
        working_hours=working_hours,
        focus=focus,
        no_meeting_days=[_parse_weekday(d) for d in data.get("no_meeting_days", [])],
        lunch_window=(_parse_time(lunch_raw["start"]), _parse_time(lunch_raw["end"])),
        buffer_before_meeting=_parse_duration(data.get("buffer_before_meeting", "5m")),
        buffer_after_meeting=_parse_duration(data.get("buffer_after_meeting", "10m")),
        long_meeting_threshold=_parse_duration(data.get("long_meeting_threshold", "45m")),
        deep_work_morning=bool(data.get("deep_work_morning", True)),
        max_meeting_density_per_day=_parse_duration(data.get("max_meeting_density_per_day", "5h")),
    )


def load_tasks(path: str | Path) -> list[Task]:
    data = yaml.safe_load(Path(path).read_text()) or {}
    out: list[Task] = []
    for raw in data.get("tasks", []):
        out.append(
            Task(
                id=raw["id"],
                title=raw["title"],
                duration=_parse_duration(raw["duration"]),
                due=datetime.fromisoformat(raw["due"]),
                priority=Priority(raw.get("priority", "P3")),
                min_chunk=_parse_duration(raw.get("min_chunk", "30m")),
                max_chunk=_parse_duration(raw.get("max_chunk", "2h")),
                earliest_start=(
                    datetime.fromisoformat(raw["earliest_start"])
                    if raw.get("earliest_start")
                    else None
                ),
                notes=raw.get("notes", ""),
            )
        )
    return out


def load_habits(path: str | Path) -> list[Habit]:
    data = yaml.safe_load(Path(path).read_text()) or {}
    out: list[Habit] = []
    for raw in data.get("habits", []):
        iw = raw.get("ideal_window")
        ideal_window = (_parse_time(iw["start"]), _parse_time(iw["end"])) if iw else None
        out.append(
            Habit(
                id=raw["id"],
                title=raw["title"],
                duration=_parse_duration(raw["duration"]),
                days=[_parse_weekday(d) for d in raw.get("days", ["mon", "tue", "wed", "thu", "fri"])],
                times_per_week=raw.get("times_per_week"),
                ideal_window=ideal_window,
                priority=Priority(raw.get("priority", "P3")),
            )
        )
    return out
