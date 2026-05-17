"""Job-hunt funnel counter — scans Gmail for application + rejection signals.

Reads the last N days of Gmail and counts:
  - applications: inbound "thanks for applying" / "your application received"
                  from job boards + ATS confirmations
  - rejections:   "unfortunately not moving forward" / "filled the position"
  - interviews:   counted from the user's calendar (not Gmail) — already
                  driven by the interview-ingest cron
  - offers:       read from config/goals.yaml manual counter

Writes config/funnel-snapshot.json for the dashboard to read.

CLI:
    python -m reclaim.funnel scan [--days 30] [--account default]
    python -m reclaim.funnel snapshot   # print the saved snapshot
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from reclaim import calendar_fetch
from reclaim.gmail_fetch import (
    _api_get,
    _get_access_token as gmail_access_token,
    fetch_raw_message,
    html_to_text,
    message_bodies,
    search_threads,
)


DEFAULT_TZ = ZoneInfo("America/Detroit")
SNAPSHOT_PATH = Path("config/funnel-snapshot.json")
GOALS_PATH = Path("config/goals.yaml")


# Applications: ATS confirmations + job board acks
APPLICATION_QUERY = (
    '("thank you for applying" OR "thanks for applying" OR '
    '"thank you for your interest in" OR "your application has been received" '
    'OR "we have received your application" OR "we received your resume" '
    'OR "application confirmation" OR "we have received your resume" '
    'OR "your application was sent" OR "application received") '
    'newer_than:{days}d -in:trash -in:spam'
)

REJECTION_QUERY = (
    '("unfortunately" OR "decided to move forward with another" '
    'OR "chosen another candidate" OR "this position has been filled" '
    'OR "interview update" OR "won\'t be moving forward" '
    'OR "we regret to inform" OR "not selected" '
    'OR "pursued other candidates") '
    'newer_than:{days}d -in:trash -in:spam'
)

# Stricter regex to reduce false positives (subject + body must really match)
APP_PATTERN = re.compile(
    r"\b(thank\s+you\s+for\s+(applying|your\s+(interest|application))|"
    r"your\s+application\s+(was|has\s+been)\s+(received|sent|submitted)|"
    r"we'?ve?\s+received\s+your\s+(application|resume)|"
    r"application\s+confirmation|"
    r"received\s+your\s+resume)\b",
    re.IGNORECASE,
)

REJECT_PATTERN = re.compile(
    r"\b(unfortunately[, ]+(we|after)|"
    r"decided\s+(not\s+to\s+(move|proceed)|"
    r"to\s+(move\s+forward\s+with|select|hire|pursue)\s+(another|other))|"
    r"won'?t\s+be\s+moving\s+forward|"
    r"chosen\s+another\s+candidate|"
    r"position\s+has\s+been\s+filled|"
    r"not\s+selected\s+(for|to)|"
    r"we\s+regret\s+to\s+inform)\b",
    re.IGNORECASE,
)


def _read_offers_from_goals() -> int:
    if not GOALS_PATH.exists():
        return 0
    try:
        import yaml
        data = yaml.safe_load(GOALS_PATH.read_text()) or {}
        for g in data.get("goals", []):
            if g.get("metric") == "offers":
                return int(g.get("current", 0))
    except Exception:
        return 0
    return 0


def _count_calendar_interviews(calendar_account: str, days: int) -> int:
    """Count distinct Interview events on the user's calendar in the past N days."""
    cal_token = calendar_fetch.get_access_token(calendar_account)
    now = datetime.now(DEFAULT_TZ)
    events = calendar_fetch.list_events(
        cal_token,
        time_min=(now - timedelta(days=days)).isoformat(),
        time_max=now.isoformat(),
        max_results=250,
    )
    return sum(
        1 for e in events
        if "interview" in (e.get("summary") or "").lower()
        and not (e.get("summary") or "").lower().startswith("[reclaim]")
    )


def _scan_pattern(query: str, pattern: re.Pattern, gmail_token: str,
                  *, max_threads: int = 100) -> tuple[int, list[dict]]:
    """Search Gmail with `query`, then count threads whose latest inbound
    message body matches `pattern`. Returns (count, sample_evidence)."""
    threads = search_threads(query, gmail_token, max_results=max_threads)
    matched = 0
    samples: list[dict] = []
    for t in threads:
        tid = t["thread_id"]
        thr = _api_get(
            f"/users/me/threads/{tid}", gmail_token, {"format": "minimal"}
        )
        for m in thr.get("messages", []):
            if "SENT" in (m.get("labelIds") or []):
                continue
            msg = fetch_raw_message(m["id"], gmail_token)
            plain, html = message_bodies(msg)
            text = plain or (html_to_text(html) if html else "")
            if not text:
                continue
            if pattern.search(text):
                matched += 1
                if len(samples) < 5:
                    samples.append({
                        "thread": tid,
                        "subject": t.get("subject", ""),
                        "from": t.get("from", ""),
                    })
                break
    return matched, samples


def scan(days: int = 30,
         gmail_account: str = "default",
         calendar_account: str = "default") -> dict:
    gmail_token = gmail_access_token(gmail_account)

    app_count, app_samples = _scan_pattern(
        APPLICATION_QUERY.format(days=days), APP_PATTERN, gmail_token,
    )
    rej_count, rej_samples = _scan_pattern(
        REJECTION_QUERY.format(days=days), REJECT_PATTERN, gmail_token,
    )
    interviews = _count_calendar_interviews(calendar_account, days)
    offers = _read_offers_from_goals()

    snapshot = {
        "generated_at": datetime.now(DEFAULT_TZ).isoformat(),
        "window_days": days,
        "gmail_account": gmail_account,
        "calendar_account": calendar_account,
        "applications": app_count,
        "rejections":   rej_count,
        "interviews":   interviews,
        "offers":       offers,
        "samples": {
            "applications": app_samples,
            "rejections":   rej_samples,
        },
    }

    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(json.dumps(snapshot, indent=2, default=str))
    return snapshot


def show_snapshot() -> None:
    if not SNAPSHOT_PATH.exists():
        print("(no snapshot yet — run `python -m reclaim.funnel scan`)")
        return
    print(SNAPSHOT_PATH.read_text())


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="reclaim.funnel")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="Scan Gmail + calendar and save snapshot.")
    s.add_argument("--days", type=int, default=30)
    s.add_argument("--gmail-account", default="default")
    s.add_argument("--calendar-account", default="default")
    sub.add_parser("snapshot", help="Print the most recent snapshot.")
    args = p.parse_args(argv)
    if args.cmd == "scan":
        snap = scan(days=args.days,
                    gmail_account=args.gmail_account,
                    calendar_account=args.calendar_account)
        json.dump(snap, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
    elif args.cmd == "snapshot":
        show_snapshot()


if __name__ == "__main__":
    main()
