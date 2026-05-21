"""Defend weekly focus time goals by booking deep-work blocks in free time."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from reclaim.freebusy import free_slots_in_range, slot_in_window
from reclaim.models import Event, EventKind, FocusGoal, Preferences, TimeSlot


def defend_focus_time(
    existing: list[Event],
    prefs: Preferences,
    week_start: date,
    week_end: date,
) -> list[Event]:
    """Book focus blocks until weekly goal is met (or no more free time fits)."""
    goal: FocusGoal = prefs.focus
    target = timedelta(hours=goal.weekly_hours)

    # Already-booked focus time on the calendar counts toward the goal.
    booked = sum(
        (e.slot.duration for e in existing if e.kind == EventKind.FOCUS),
        timedelta(),
    )
    needed = target - booked
    if needed <= timedelta(0):
        return []

    free_by_day = free_slots_in_range(week_start, week_end, existing, prefs)

    # Rank candidate slots: prefer preferred window, then mornings (if pref),
    # then longest slots, then earliest in the week.
    win_start, win_end = goal.preferred_window
    candidates: list[TimeSlot] = []
    for d in sorted(free_by_day.keys()):
        for s in free_by_day[d]:
            if s.duration < goal.min_block:
                continue
            candidates.append(s)

    def rank(s: TimeSlot) -> tuple:
        in_pref = 0 if slot_in_window(s, win_start, win_end) else 1
        morning = 0 if (prefs.deep_work_morning and s.start.hour < 12) else 1
        return (in_pref, morning, -s.duration.total_seconds(), s.start)

    candidates.sort(key=rank)

    placed: list[Event] = []
    for slot in candidates:
        if needed <= timedelta(0):
            break
        length = min(slot.duration, goal.max_block, needed)
        if length < goal.min_block:
            continue
        # Snap focus block to the preferred window when possible.
        snapped = _snap_to_window(slot, win_start, win_end, length)
        block = TimeSlot(snapped.start, snapped.start + length)
        placed.append(
            Event(
                title="Focus Time",
                slot=block,
                kind=EventKind.FOCUS,
                movable=True,
                notes="Defended deep-work block",
            )
        )
        needed -= length
    return placed


def _snap_to_window(
    slot: TimeSlot, win_start: time, win_end: time, length: timedelta
) -> TimeSlot:
    """Shift the start of the block into the preferred window if possible."""
    day = slot.start.date()
    ws = datetime.combine(day, win_start)
    we = datetime.combine(day, win_end)
    earliest = max(slot.start, ws)
    latest = min(slot.end, we) - length
    if earliest <= latest:
        return TimeSlot(earliest, earliest + length)
    return slot
