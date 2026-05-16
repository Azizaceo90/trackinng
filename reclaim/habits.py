"""Schedule recurring flexible habits (lunch, gym, breaks)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from reclaim.freebusy import free_slots_for_day, slot_in_window, subtract
from reclaim.models import Event, EventKind, Habit, Preferences, TimeSlot, iter_days


def schedule_habits(
    habits: list[Habit],
    existing: list[Event],
    prefs: Preferences,
    horizon_start: date,
    horizon_end: date,
) -> list[Event]:
    placed: list[Event] = []
    # We rebuild free slots as we add events so habits don't double-book.
    current = list(existing)

    for habit in habits:
        if habit.times_per_week is not None:
            placed.extend(
                _flex_weekly(habit, current, prefs, horizon_start, horizon_end)
            )
        else:
            placed.extend(
                _daily(habit, current, prefs, horizon_start, horizon_end)
            )
        current = current + placed

    return placed


def _daily(
    habit: Habit,
    existing: list[Event],
    prefs: Preferences,
    horizon_start: date,
    horizon_end: date,
) -> list[Event]:
    out: list[Event] = []
    for d in iter_days(horizon_start, horizon_end):
        if d.weekday() not in habit.days:
            continue
        slot = _find_slot_for_day(habit, d, existing + out, prefs)
        if slot:
            out.append(_make_event(habit, slot))
    return out


def _flex_weekly(
    habit: Habit,
    existing: list[Event],
    prefs: Preferences,
    horizon_start: date,
    horizon_end: date,
) -> list[Event]:
    out: list[Event] = []
    days = [d for d in iter_days(horizon_start, horizon_end) if d.weekday() in habit.days]
    booked_this_week = 0
    for d in days:
        if booked_this_week >= (habit.times_per_week or 0):
            break
        slot = _find_slot_for_day(habit, d, existing + out, prefs)
        if slot:
            out.append(_make_event(habit, slot))
            booked_this_week += 1
    return out


def _find_slot_for_day(
    habit: Habit, d: date, existing: list[Event], prefs: Preferences
) -> TimeSlot | None:
    if habit.ideal_window:
        # Honor the ideal window even if it extends past working hours —
        # gym at 6pm is a thing.
        win_start, win_end = habit.ideal_window
        window = TimeSlot(datetime.combine(d, win_start), datetime.combine(d, win_end))
        busy_today = [e.slot for e in existing if e.slot.start.date() == d]
        in_window = subtract(window, busy_today)
        for s in sorted(in_window, key=lambda x: x.start):
            if s.duration >= habit.duration:
                return TimeSlot(s.start, s.start + habit.duration)
        # Fall through: try working hours if the ideal window is full.

    free = free_slots_for_day(d, existing, prefs)
    free = [s for s in free if s.duration >= habit.duration]
    if not free:
        return None
    best = min(free, key=lambda s: s.start)
    return TimeSlot(best.start, best.start + habit.duration)


def _make_event(habit: Habit, slot: TimeSlot) -> Event:
    return Event(
        title=habit.title,
        slot=slot,
        kind=habit.kind,
        source_id=habit.id,
        movable=True,
        notes="Reclaim: flexible habit",
    )
