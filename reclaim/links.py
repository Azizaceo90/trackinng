"""AI Scheduling Links: generate shareable booking offers from your free time."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from reclaim.freebusy import free_slots_for_day
from reclaim.models import Event, Preferences, TimeSlot, iter_days


@dataclass(frozen=True)
class SlotOffer:
    slot: TimeSlot
    label: str  # human-friendly, e.g. "Tue May 19 2026, 10:00–10:30"


def _label(slot: TimeSlot) -> str:
    return (
        f"{slot.start:%a %b %d %Y}, "
        f"{slot.start:%H:%M}–{slot.end:%H:%M}"
    )


def generate_offers(
    events: list[Event],
    prefs: Preferences,
    duration: timedelta,
    horizon_start: date,
    horizon_end: date,
    *,
    limit: int = 8,
    step: timedelta = timedelta(minutes=30),
    earliest: time | None = None,
    latest: time | None = None,
    weekdays: list[int] | None = None,
    spread: bool = True,
) -> list[SlotOffer]:
    """Generate up to `limit` bookable slots of `duration` from your free time.

    - `step`: granularity at which to anchor slot starts (e.g. on the half hour).
    - `earliest` / `latest`: optional time-of-day filter (e.g. 10:00 to 16:00).
    - `weekdays`: optional list of weekday indices (Mon=0).
    - `spread`: if True, only one offer per day (prevents flooding a single day).
    """
    out: list[SlotOffer] = []
    for d in iter_days(horizon_start, horizon_end):
        if weekdays is not None and d.weekday() not in weekdays:
            continue
        if prefs.is_no_meeting_day(d):
            continue
        free = free_slots_for_day(d, events, prefs)
        day_offers: list[SlotOffer] = []
        for f in free:
            anchor = _round_up(f.start, step)
            if earliest is not None:
                day_lo = datetime.combine(d, earliest)
                anchor = max(anchor, day_lo)
            day_hi = (
                datetime.combine(d, latest) if latest is not None else f.end
            )
            while anchor + duration <= min(f.end, day_hi):
                day_offers.append(
                    SlotOffer(TimeSlot(anchor, anchor + duration), _label(TimeSlot(anchor, anchor + duration)))
                )
                anchor = anchor + step
        if not day_offers:
            continue
        if spread:
            out.append(day_offers[0])
        else:
            out.extend(day_offers)
        if len(out) >= limit:
            break
    return out[:limit]


def render_offers(offers: list[SlotOffer], tz: str = "") -> str:
    if not offers:
        return "No bookable slots in the requested window."
    header = "Here are some times that work" + (f" ({tz})" if tz else "") + ":"
    lines = [header]
    for i, o in enumerate(offers, 1):
        lines.append(f"  {i}. {o.label}")
    lines.append("")
    lines.append("Reply with a number and I'll hold the slot.")
    return "\n".join(lines)


def _round_up(dt: datetime, step: timedelta) -> datetime:
    secs = int(step.total_seconds())
    if secs <= 0:
        return dt
    epoch = datetime(dt.year, dt.month, dt.day)
    delta = (dt - epoch).total_seconds()
    rounded = ((int(delta) + secs - 1) // secs) * secs
    return epoch + timedelta(seconds=rounded)
