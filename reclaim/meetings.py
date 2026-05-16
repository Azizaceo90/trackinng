"""Find mutual free time across multiple people for smart 1:1s / meetings."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from reclaim.freebusy import free_slots_for_day, subtract
from reclaim.models import Event, Preferences, TimeSlot, iter_days


def find_mutual_slots(
    attendees: dict[str, list[Event]],  # name -> their existing events
    prefs: Preferences,
    duration: timedelta,
    horizon_start: date,
    horizon_end: date,
    limit: int = 5,
) -> list[TimeSlot]:
    """Return up to `limit` mutually-free slots of `duration` within working hours.

    Working hours are taken from `prefs` (assumed shared); for asymmetric
    schedules, pass the intersection upstream.
    """
    candidates: list[TimeSlot] = []
    for d in iter_days(horizon_start, horizon_end):
        if prefs.is_no_meeting_day(d):
            continue
        # Start with my free slots, then subtract every attendee's busy time.
        slots = free_slots_for_day(d, [], prefs)  # full working day
        all_busy: list[TimeSlot] = []
        for events in attendees.values():
            for e in events:
                if e.slot.start.date() == d:
                    all_busy.append(e.slot)
        refined: list[TimeSlot] = []
        for s in slots:
            refined.extend(subtract(s, all_busy))
        for s in refined:
            if s.duration >= duration:
                candidates.append(TimeSlot(s.start, s.start + duration))
                if len(candidates) >= limit:
                    return candidates
    return candidates
