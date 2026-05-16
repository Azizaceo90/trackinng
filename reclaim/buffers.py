"""Inject buffer time around long meetings for prep / decompress."""
from __future__ import annotations

from reclaim.models import Event, EventKind, Preferences, TimeSlot


def add_buffers(events: list[Event], prefs: Preferences) -> list[Event]:
    out: list[Event] = []
    meetings = [e for e in events if e.kind == EventKind.MEETING]
    occupied: list[TimeSlot] = [e.slot for e in events]

    for m in meetings:
        if m.slot.duration < prefs.long_meeting_threshold:
            continue
        if prefs.buffer_before_meeting > __zero():
            pre = TimeSlot(m.slot.start - prefs.buffer_before_meeting, m.slot.start)
            if _is_free(pre, occupied):
                ev = Event(
                    title="Prep buffer",
                    slot=pre,
                    kind=EventKind.BUFFER,
                    movable=True,
                    notes=f"Buffer before {m.title}",
                )
                out.append(ev)
                occupied.append(pre)
        if prefs.buffer_after_meeting > __zero():
            post = TimeSlot(m.slot.end, m.slot.end + prefs.buffer_after_meeting)
            if _is_free(post, occupied):
                ev = Event(
                    title="Decompress buffer",
                    slot=post,
                    kind=EventKind.BUFFER,
                    movable=True,
                    notes=f"Buffer after {m.title}",
                )
                out.append(ev)
                occupied.append(post)
    return out


def __zero():
    from datetime import timedelta
    return timedelta(0)


def _is_free(slot: TimeSlot, occupied: list[TimeSlot]) -> bool:
    return not any(slot.overlaps(o) for o in occupied)
