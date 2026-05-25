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
# Identify prayer events by their title, e.g. "🌙 Fajr Prayer". Matching on
# the title (not a hidden description tag) means editing or scrubbing the
# description can never orphan an event and cause the sync to re-create it.
_TITLE_TO_PRAYER = {f"{PRAYER_EMOJI[p]} {p} Prayer": p for p in PRAYERS}


def _prayer_from_summary(summary: str) -> str | None:
    s = (summary or "").strip()
    if s in _TITLE_TO_PRAYER:
        return _TITLE_TO_PRAYER[s]
    # Tolerate emoji/spacing drift — match the "<Prayer> Prayer" tail.
    for p in PRAYERS:
        if s.endswith(f"{p} Prayer"):
            return p
    return None


def fetch_times(d: date, method: str = "ISNA") -> dict[str, str]:
    """Return {"Fajr": "04:55", "Dhuhr": "13:25", ...} for the given date.

    Computed locally via prayer_calc — no external API.
    """
    all_times = compute_prayer_times(d, method=method)
    return {p: all_times[p] for p in PRAYERS}


def _event_body(prayer: str, when: datetime, duration_min: int) -> dict:
    end = when + timedelta(minutes=duration_min)
    return {
        "summary": f"{PRAYER_EMOJI[prayer]} {prayer} Prayer",
        "description": (
            f"{prayer} prayer.\n"
            f"Computed locally (ISNA method, Detroit).\n"
            f"Automatically refreshed nightly.\n\n"
            f"[planner-managed]"
        ),
        "colorId": "3",  # Grape — spiritual
        "start": {"dateTime": when.isoformat(), "timeZone": str(DEFAULT_TZ)},
        "end":   {"dateTime": end.isoformat(),  "timeZone": str(DEFAULT_TZ)},
    }


def _existing_prayer_events(token: str, start: date, end: date) -> dict[tuple[date, str], list[str]]:
    """Map (date, prayer_name) -> [event_id, ...] for prayer events on the calendar.

    Returns a list per key so that accidental duplicates are surfaced and
    collapsed by `sync` rather than silently ignored.
    """
    items = calendar_fetch.list_events(
        token,
        time_min=datetime.combine(start, datetime.min.time(), tzinfo=DEFAULT_TZ).isoformat(),
        time_max=datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=DEFAULT_TZ).isoformat(),
        max_results=250,
    )
    out: dict[tuple[date, str], list[str]] = {}
    for e in items:
        name = _prayer_from_summary(e.get("summary", ""))
        if name is None:
            continue
        start_str = e.get("start", {}).get("dateTime")
        if not start_str:
            continue
        try:
            d = datetime.fromisoformat(start_str.replace("Z", "+00:00")).astimezone(DEFAULT_TZ).date()
        except ValueError:
            continue
        out.setdefault((d, name), []).append(e["id"])
    return out


def sync(days: int = 14, account: str = "personal2") -> dict:
    """Ensure the next `days` days have accurate prayer events. Idempotent."""
    token = calendar_fetch.get_access_token(account)
    today = date.today()
    horizon_start = today
    horizon_end = today + timedelta(days=days - 1)

    existing = _existing_prayer_events(token, horizon_start, horizon_end)
    summary = {"created": 0, "updated": 0, "deleted_duplicate": 0, "deleted_stale": 0, "errors": []}

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
            ids = existing.pop((d, prayer), [])
            if not ids:
                calendar_fetch.create_event(token, body)
                summary["created"] += 1
                continue
            # Keep (and refresh) the first; any others are duplicates — delete them.
            _patch_event(token, ids[0], body)
            summary["updated"] += 1
            for dup in ids[1:]:
                try:
                    _delete_event(token, dup)
                    summary["deleted_duplicate"] += 1
                except Exception as ex:
                    summary["errors"].append({"date": str(d), "prayer": prayer, "error": str(ex)})

    # Anything still in `existing` after popping is stale (a prayer event on a
    # date in the window that no longer matches a current prayer time).
    for (d, name), ids in existing.items():
        for eid in ids:
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
