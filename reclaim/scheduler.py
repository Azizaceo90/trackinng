"""Task auto-scheduling: place tasks into free slots by priority and due date."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from reclaim.freebusy import free_slots_in_range
from reclaim.models import Event, EventKind, Preferences, Task, TimeSlot


def _split_slot(slot: TimeSlot, length: timedelta) -> tuple[TimeSlot, TimeSlot | None]:
    head = TimeSlot(slot.start, slot.start + length)
    if slot.end > head.end:
        return head, TimeSlot(head.end, slot.end)
    return head, None


def schedule_tasks(
    tasks: list[Task],
    existing: list[Event],
    prefs: Preferences,
    horizon_start: date,
    horizon_end: date,
) -> tuple[list[Event], list[Task]]:
    """Place tasks into free time.

    Returns (newly_placed_events, unplaced_tasks). Tasks may be split across
    multiple chunks; a task is "placed" only if all of its duration fits.
    """
    # Higher priority first, then earlier due date, then shorter duration.
    queue = sorted(
        tasks,
        key=lambda t: (t.priority.rank, t.due, t.duration),
    )

    free_by_day = free_slots_in_range(horizon_start, horizon_end, existing, prefs)
    placed: list[Event] = []
    unplaced: list[Task] = []

    for task in queue:
        remaining = task.duration
        chunks: list[TimeSlot] = []
        # Walk days in order up to (and including) due date.
        for d in sorted(free_by_day.keys()):
            if datetime.combine(d, datetime.min.time()) > task.due:
                break
            slots = free_by_day[d]
            # Pack earliest-first within the day.
            slots_sorted = sorted(slots, key=lambda s: s.start)
            for slot in list(slots_sorted):
                if remaining <= timedelta(0):
                    break
                if slot.duration < task.min_chunk:
                    continue
                if task.earliest_start and slot.end <= task.earliest_start:
                    continue
                usable_start = slot.start
                if task.earliest_start and task.earliest_start > slot.start:
                    if (slot.end - task.earliest_start) < task.min_chunk:
                        continue
                    usable_start = task.earliest_start
                usable = TimeSlot(usable_start, slot.end)
                take = min(remaining, task.max_chunk, usable.duration)
                if take < task.min_chunk:
                    continue
                chunk = TimeSlot(usable.start, usable.start + take)
                chunks.append(chunk)
                remaining -= take
                # Replace this slot in free_by_day with the leftover.
                free_by_day[d] = _remove_slot(free_by_day[d], slot, chunk)
                if remaining <= timedelta(0):
                    break
            if remaining <= timedelta(0):
                break

        if remaining <= timedelta(0):
            for i, chunk in enumerate(chunks):
                suffix = f" ({i+1}/{len(chunks)})" if len(chunks) > 1 else ""
                placed.append(
                    Event(
                        title=f"[{task.priority.value}] {task.title}{suffix}",
                        slot=chunk,
                        kind=EventKind.TASK,
                        source_id=task.id,
                        movable=True,
                        notes=task.notes,
                    )
                )
        else:
            unplaced.append(task)

    return placed, unplaced


def _remove_slot(slots: list[TimeSlot], original: TimeSlot, used: TimeSlot) -> list[TimeSlot]:
    out: list[TimeSlot] = []
    for s in slots:
        if s is original or (s.start == original.start and s.end == original.end):
            if used.start > s.start:
                out.append(TimeSlot(s.start, used.start))
            if used.end < s.end:
                out.append(TimeSlot(used.end, s.end))
        else:
            out.append(s)
    return out
