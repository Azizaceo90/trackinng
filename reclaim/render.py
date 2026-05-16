"""Pretty-print a schedule to the terminal."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from reclaim.models import Event, EventKind

KIND_TAG = {
    EventKind.MEETING: "MTG",
    EventKind.FOCUS:   "FOC",
    EventKind.HABIT:   "HAB",
    EventKind.TASK:    "TSK",
    EventKind.BUFFER:  "BUF",
    EventKind.BLOCKED: "BLK",
    EventKind.OOO:     "OOO",
}


def render_week(events: list[Event]) -> str:
    by_day: dict[date, list[Event]] = defaultdict(list)
    for e in events:
        by_day[e.slot.start.date()].append(e)

    lines: list[str] = []
    for d in sorted(by_day.keys()):
        lines.append(f"\n{d:%a %Y-%m-%d}")
        lines.append("-" * 40)
        for e in sorted(by_day[d], key=lambda x: x.slot.start):
            tag = KIND_TAG.get(e.kind, "???")
            t1 = e.slot.start.strftime("%H:%M")
            t2 = e.slot.end.strftime("%H:%M")
            mover = "~" if e.movable else " "
            lines.append(f"  {tag} {mover} {t1}-{t2}  {e.title}")
    return "\n".join(lines)


def render_summary(events: list[Event]) -> str:
    totals: dict[EventKind, timedelta] = defaultdict(lambda: timedelta())
    for e in events:
        totals[e.kind] += e.slot.duration
    lines = ["Totals:"]
    for k in EventKind:
        if totals[k]:
            lines.append(f"  {k.value:<8} {_fmt(totals[k])}")
    return "\n".join(lines)


def _fmt(td: timedelta) -> str:
    minutes = int(td.total_seconds() // 60)
    h, m = divmod(minutes, 60)
    return f"{h}h{m:02d}m"
