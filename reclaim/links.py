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


def _gcal_template_url(
    slot: TimeSlot,
    *,
    tz: str,
    title: str,
    host_email: str,
    details: str,
) -> str:
    """Build a Google Calendar 'create event' deeplink with the slot pre-filled.

    The booker clicks → Google Calendar opens with the meeting drafted and
    the host attached as an attendee. When they save, Google sends the host
    an invitation email. The host accepts → event lands on both calendars.
    """
    import urllib.parse
    from zoneinfo import ZoneInfo
    local_tz = ZoneInfo(tz) if tz else ZoneInfo("UTC")
    start_local = slot.start.replace(tzinfo=local_tz)
    end_local = slot.end.replace(tzinfo=local_tz)
    start_utc = start_local.astimezone(ZoneInfo("UTC"))
    end_utc = end_local.astimezone(ZoneInfo("UTC"))
    dates = f"{start_utc:%Y%m%dT%H%M%SZ}/{end_utc:%Y%m%dT%H%M%SZ}"
    params = {
        "action": "TEMPLATE",
        "text": title,
        "dates": dates,
        "details": details,
        "add": host_email,
    }
    return "https://calendar.google.com/calendar/render?" + urllib.parse.urlencode(params)


def render_offers_html(
    offers: list[SlotOffer],
    *,
    tz: str,
    host_name: str,
    host_email: str,
    duration_min: int,
    host_blurb: str = "",
    meeting_title: str = "",
) -> str:
    """Standalone HTML booking page.

    Slots are grouped by day. Each slot is an `<a>` that opens a pre-filled
    Google Calendar 'create event' page in the booker's browser. No server
    needed — the booking becomes real when the booker saves the event and
    the host accepts the resulting invite.
    """
    from collections import defaultdict
    import html
    from datetime import datetime
    title = meeting_title or f"Meeting with {host_name}"
    details = (
        f"Booked via {host_name}'s scheduling page. "
        f"{host_name} will receive an invite when you save this event."
    )
    by_day: dict = defaultdict(list)
    for o in offers:
        by_day[o.slot.start.date()].append(o)
    day_blocks = []
    for day in sorted(by_day):
        slots = sorted(by_day[day], key=lambda s: s.slot.start)
        buttons = []
        for o in slots:
            url = _gcal_template_url(
                o.slot, tz=tz, title=title,
                host_email=host_email, details=details,
            )
            label = f"{o.slot.start:%-I:%M %p}"
            buttons.append(
                f'<a class="slot" href="{html.escape(url)}" '
                f'target="_blank" rel="noopener">{label}</a>'
            )
        day_blocks.append(
            f'<section class="day"><h2>{day:%A, %B %-d}</h2>'
            f'<div class="slots">{"".join(buttons)}</div></section>'
        )
    if not day_blocks:
        day_blocks = ['<p class="empty">No bookable slots in the next two weeks. '
                      f'Try emailing <a href="mailto:{html.escape(host_email)}">'
                      f'{html.escape(host_email)}</a>.</p>']
    rendered_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    safe_name = html.escape(host_name)
    safe_blurb = html.escape(host_blurb) if host_blurb else ""
    tz_label = html.escape(tz) if tz else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Book time with {safe_name}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body {{ font: 15px -apple-system, system-ui, sans-serif; max-width: 720px;
         margin: 2rem auto; padding: 0 1rem; color: #111; background: #fafafa; }}
  h1 {{ font-size: 1.5rem; margin: 0 0 .25rem; }}
  .sub {{ color: #666; margin-bottom: .5rem; }}
  .blurb {{ color: #333; background: #fff; border-left: 3px solid #3b82f6;
            padding: .75rem 1rem; border-radius: 4px; margin: 1rem 0; }}
  .meta {{ font-size: .85rem; color: #666; margin-bottom: 1.5rem; }}
  .day {{ background: white; border: 1px solid #e5e5e5; border-radius: 8px;
          padding: 1rem 1.25rem; margin-bottom: 1rem; }}
  .day h2 {{ font-size: 1rem; margin: 0 0 .75rem; color: #444; }}
  .slots {{ display: flex; flex-wrap: wrap; gap: .5rem; }}
  a.slot {{ display: inline-block; padding: .55rem .9rem; background: #fff;
            border: 1px solid #3b82f6; color: #1d4ed8; border-radius: 6px;
            text-decoration: none; font-variant-numeric: tabular-nums;
            font-size: .95rem; transition: all .1s; }}
  a.slot:hover {{ background: #3b82f6; color: #fff; }}
  .empty {{ background: #fff; padding: 2rem; border-radius: 8px; text-align: center;
            color: #666; border: 1px dashed #ccc; }}
  footer {{ color: #888; font-size: .75rem; margin-top: 2rem; text-align: center; }}
  footer a {{ color: #888; }}
</style>
</head>
<body>
  <h1>Book {duration_min} minutes with {safe_name}</h1>
  <div class="sub">Pick a time below. Google Calendar will open with the meeting
  pre-filled — just hit <strong>Save</strong> and {safe_name} will get an invite.</div>
  {f'<div class="blurb">{safe_blurb}</div>' if safe_blurb else ''}
  <div class="meta">All times shown in <strong>{tz_label}</strong>. ({len(offers)} slot{'s' if len(offers) != 1 else ''} available.)</div>

  {''.join(day_blocks)}

  <footer>Last updated {rendered_at} · Powered by
  <a href="https://github.com/azizaceo90/trackinng">Reclaim</a></footer>
</body>
</html>
"""


def _round_up(dt: datetime, step: timedelta) -> datetime:
    secs = int(step.total_seconds())
    if secs <= 0:
        return dt
    epoch = datetime(dt.year, dt.month, dt.day)
    delta = (dt - epoch).total_seconds()
    rounded = ((int(delta) + secs - 1) // secs) * secs
    return epoch + timedelta(seconds=rounded)
