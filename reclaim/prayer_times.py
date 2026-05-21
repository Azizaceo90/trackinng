"""Daily prayer times + sync to Google Calendar.

Computes accurate Fajr / Dhuhr / Asr / Maghrib / Isha times locally
(no external API, ISNA method by default — most common in US) and
maintains a rolling 14-day window of individual prayer events on
the configured calendar.

Why individual events instead of recurring?
  Prayer times drift daily with the sun. Google Calendar's recurring
  events have a single fixed time, so they'd be wrong by 10-60 minutes
  most days. Easier to manage as standalone events that get refreshed.

CLI:
    python -m reclaim.prayer_times sync [--days 14] [--account personal2]
    python -m reclaim.prayer_times today                # show today's times
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from reclaim import calendar_fetch
from reclaim.prayer_calc import compute_prayer_times, DEFAULT_TZ, PRAYER_ORDER

PRAYERS = ["Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"]
PRAYER_DURATIONS = {        # how long each block defends, in minutes
    "Fajr":    30,
    "Dhuhr":   20,
    "Asr":     20,
    "Maghrib": 20,
    "Isha":    30,
}
PRAYER_EMOJI = {
    "Fajr":    "🌙",
    "Dhuhr":   "☀️",
    "Asr":     "🌤️",
    "Maghrib": "🌅",
    "Isha":    "🌌",
}
PRAYER_TAG = "[Reclaim:prayer]"   # marker in description so we can dedupe


def fetch_times(d: date, method: str = "ISNA") -> dict[str, str]:
    """Return {"Fajr": "04:55", "Dhuhr": "13:25", ...} for the given date.

    Computed locally via reclaim.prayer_calc — no external API.
    """
    all_times = compute_prayer_times(d, method=method)
    return {p: all_times[p] for p in PRAYERS}


def _event_body(prayer: str, when: datetime, duration_min: int) -> dict:
    end = when + timedelta(minutes=duration_min)
    return {
        "summary": f"{PRAYER_EMOJI[prayer]} {prayer} Prayer",
        "description": (
            f"{PRAYER_TAG} {prayer}\n"
            f"Computed locally (reclaim.prayer_calc, ISNA method, Detroit).\n"
            f"Automatically refreshed nightly by .github/workflows/prayer-times.yml."
        ),
        "colorId": "3",  # Grape — spiritual
        "start": {"dateTime": when.isoformat(), "timeZone": str(DEFAULT_TZ)},
        "end":   {"dateTime": end.isoformat(),  "timeZone": str(DEFAULT_TZ)},
    }


def _existing_prayer_event_ids(token: str, start: date, end: date) -> dict[tuple[date, str], str]:
    """Map (date, prayer_name) -> event_id for events created by this script."""
    items = calendar_fetch.list_events(
        token,
        time_min=datetime.combine(start, datetime.min.time(), tzinfo=DEFAULT_TZ).isoformat(),
        time_max=datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=DEFAULT_TZ).isoformat(),
        max_results=250,
    )
    out: dict[tuple[date, str], str] = {}
    for e in items:
        desc = e.get("description", "") or ""
        if PRAYER_TAG not in desc:
            continue
        # parse prayer name from description's first line
        # "[Reclaim:prayer] Fajr\n..."
        first = desc.split("\n", 1)[0]
        parts = first.split(" ", 1)
        if len(parts) != 2:
            continue
        name = parts[1].strip()
        if name not in PRAYERS:
            continue
        start_str = e.get("start", {}).get("dateTime")
        if not start_str:
            continue
        try:
            d = datetime.fromisoformat(start_str.replace("Z", "+00:00")).astimezone(DEFAULT_TZ).date()
        except ValueError:
            continue
        out[(d, name)] = e["id"]
    return out


def sync(days: int = 14, account: str = "personal2") -> dict:
    """Ensure the next `days` days have accurate prayer events. Idempotent."""
    token = calendar_fetch.get_access_token(account)
    today = date.today()
    horizon_start = today
    horizon_end = today + timedelta(days=days - 1)

    existing = _existing_prayer_event_ids(token, horizon_start, horizon_end)
    summary = {"created": 0, "updated": 0, "skipped": 0, "deleted_stale": 0, "errors": []}

    for offset in range(days):
        d = today + timedelta(days=offset)
        try:
            times = fetch_times(d)
        except Exception as ex:
            summary["errors"].append({"date": str(d), "error": str(ex)})
            continue
        for prayer in PRAYERS:
            hh, mm = times[prayer].split(":")
            when = datetime(d.year, d.month, d.day, int(hh), int(mm), tzinfo=DEFAULT_TZ)
            duration = PRAYER_DURATIONS[prayer]
            body = _event_body(prayer, when, duration)
            existing_id = existing.pop((d, prayer), None)
            if existing_id is None:
                calendar_fetch.create_event(token, body)
                summary["created"] += 1
            else:
                # Update via PATCH (calendar_fetch doesn't expose one — call API directly)
                _patch_event(token, existing_id, body)
                summary["updated"] += 1

    # Anything still in `existing` after popping is stale (date in window but no longer matches our refresh)
    for (d, name), eid in existing.items():
        try:
            _delete_event(token, eid)
            summary["deleted_stale"] += 1
        except Exception as ex:
            summary["errors"].append({"date": str(d), "prayer": name, "error": str(ex)})

    return summary


def _patch_event(token: str, event_id: str, body: dict) -> dict:
    url = f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{urllib.parse.quote(event_id)}"
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="PATCH",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def _delete_event(token: str, event_id: str) -> None:
    url = f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{urllib.parse.quote(event_id)}"
    req = urllib.request.Request(
        url, method="DELETE", headers={"Authorization": f"Bearer {token}"},
    )
    urllib.request.urlopen(req).read()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="reclaim.prayer_times")
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("today", help="Print today's prayer times.")
    t.add_argument("--method", default="ISNA",
                   choices=["ISNA", "MWL", "Egyptian", "Karachi", "Makkah", "Tehran"])

    s = sub.add_parser("sync", help="Sync N days of prayer events to the calendar.")
    s.add_argument("--days", type=int, default=14)
    s.add_argument("--account", default="personal2",
                   help="Calendar account name (default: personal2 = aziza.muhammadx).")

    args = p.parse_args(argv)
    if args.cmd == "today":
        times = fetch_times(date.today(), method=args.method)
        for prayer, time in times.items():
            print(f"  {PRAYER_EMOJI[prayer]} {prayer:8s} {time}")
    elif args.cmd == "sync":
        result = sync(days=args.days, account=args.account)
        json.dump(result, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")


if __name__ == "__main__":
    main()
