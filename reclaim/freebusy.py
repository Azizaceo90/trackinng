"""Compute free time slots from a list of events within working hours."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from reclaim.models import Event, Preferences, TimeSlot, iter_days


def _merge(slots: list[TimeSlot]) -> list[TimeSlot]:
    if not slots:
        return []
    ordered = sorted(slots, key=lambda s: s.start)
    merged = [ordered[0]]
    for s in ordered[1:]:
        last = merged[-1]
        if s.start <= last.end:
            if s.end > last.end:
                merged[-1] = TimeSlot(last.start, s.end)
        else:
            merged.append(s)
    return merged


def subtract(window: TimeSlot, busy: list[TimeSlot]) -> list[TimeSlot]:
    """Return the parts of `window` not covered by any slot in `busy`."""
    free: list[TimeSlot] = []
    cursor = window.start
    for b in _merge([s for s in busy if s.overlaps(window)]):
        bs = max(b.start, window.start)
        be = min(b.end, window.end)
        if bs > cursor:
            free.append(TimeSlot(cursor, bs))
        cursor = max(cursor, be)
    if cursor < window.end:
        free.append(TimeSlot(cursor, window.end))
    return free


def free_slots_for_day(
    day: date,
    events: list[Event],
    prefs: Preferences,
) -> list[TimeSlot]:
    """Return the free time within `day`'s working hours, minus existing events."""
    working = prefs.working_slots(day)
    busy = [e.slot for e in events]
    free: list[TimeSlot] = []
    for w in working:
        free.extend(subtract(w, busy))
    return free


def free_slots_in_range(
    start: date,
    end: date,
    events: list[Event],
    prefs: Preferences,
) -> dict[date, list[TimeSlot]]:
    out: dict[date, list[TimeSlot]] = {}
    for d in iter_days(start, end):
        out[d] = free_slots_for_day(d, events, prefs)
    return out


def total_free(slots: list[TimeSlot]) -> timedelta:
    return sum((s.duration for s in slots), timedelta())


def slot_in_window(slot: TimeSlot, window_start, window_end) -> bool:
    """True if any part of slot falls in (time-of-day) window."""
    day = slot.start.date()
    ws = datetime.combine(day, window_start)
    we = datetime.combine(day, window_end)
    return slot.overlaps(TimeSlot(ws, we))
