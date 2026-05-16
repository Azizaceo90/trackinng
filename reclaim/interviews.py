"""Headless interview-ingest: search Gmail, triage, create calendar events.

This is the pure-Python equivalent of the /reclaim-interviews slash
command. Designed to run from a GitHub Actions cron without an LLM
in the loop.

Pipeline:
  1. Search Gmail for interview-related threads in the last N days.
  2. For each thread, look for an ICS calendar invitation attachment
     first (most reliable signal — that's a real bookable event).
  3. If no ICS, fall back to heuristic body parsing: regex for
     confirmation language + date/time + meeting URL/ID/passcode.
  4. Triage: skip past, cancelled, rescheduled, rejection, async.
  5. Dedupe against existing calendar events (by Gmail thread ID
     stored in the event description).
  6. Create the event with full meeting details and a tag in the
     description so future runs don't double-book.

CLI:
    python -m reclaim.interviews scan [--days 14] [--account default]
                                      [--dry-run]
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import Message
from zoneinfo import ZoneInfo

from reclaim import calendar_fetch
from reclaim.gmail_fetch import (
    _api_get,
    _get_access_token as gmail_access_token,
    extract_meeting,
    fetch_raw_message,
    html_to_text,
    message_bodies,
    search_threads,
)


DEFAULT_TZ = ZoneInfo("America/Detroit")
INTERVIEW_QUERY = (
    '(subject:interview OR "interview request" OR "schedule an interview" '
    'OR "phone screen" OR "technical screen" OR "interview confirmation" '
    'OR "you are confirmed" OR "your interview is" OR "calendar invite" '
    'OR "invitation: interview") '
    'newer_than:{days}d -in:trash -in:spam'
)


# ----------------------------------------------------------------- triage ---

SKIP_PATTERNS = re.compile(
    r"\b(canceled|cancelled|reschedul|unfortunately|"
    r"not move forward|next time|thank you for interviewing|"
    r"interview update|thank you for your time|"
    r"we have decided|moved to the next step|"
    r"are no longer moving|been filled|filled the position)\b",
    re.IGNORECASE,
)
ASYNC_DOMAINS = {"hireflix.com", "sparkhire.com", "vidcruiter.com", "spark.hire"}
ASYNC_HINT = re.compile(r"\b(video interview|automated|on your own time|"
                        r"complete the video|record your)\b", re.IGNORECASE)


def is_skip(subject: str, snippet: str, sender: str) -> str | None:
    """Returns a skip reason or None."""
    haystack = f"{subject}\n{snippet}".lower()
    sender_lower = sender.lower()
    if SKIP_PATTERNS.search(haystack):
        if "canceled" in haystack or "cancelled" in haystack:
            return "cancelled"
        if "reschedul" in haystack:
            return "reschedule"
        return "rejection_or_status"
    if any(d in sender_lower for d in ASYNC_DOMAINS) or ASYNC_HINT.search(haystack):
        return "async"
    return None


# ------------------------------------------------------- ICS parsing path ---


def _unfold_ics(text: str) -> list[str]:
    """RFC 5545 line unfolding — joined continuation lines."""
    out: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.startswith((" ", "\t")) and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def parse_ics(text: str) -> dict | None:
    """Extract first VEVENT's relevant fields. Returns None if no event."""
    lines = _unfold_ics(text)
    in_event = False
    event: dict = {}
    for line in lines:
        if line.strip() == "BEGIN:VEVENT":
            in_event = True
            event = {}
        elif line.strip() == "END:VEVENT":
            if event.get("dtstart") and event.get("dtend"):
                return event
            in_event = False
        elif in_event:
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.upper().split(";", 1)[0]
            if key == "DTSTART":
                event["dtstart"] = _parse_ics_dt(line)
            elif key == "DTEND":
                event["dtend"] = _parse_ics_dt(line)
            elif key == "SUMMARY":
                event["summary"] = value
            elif key == "LOCATION":
                event["location"] = value
            elif key == "DESCRIPTION":
                event["description"] = value.replace("\\n", "\n").replace("\\,", ",")
            elif key == "ORGANIZER":
                event["organizer"] = value
            elif key == "UID":
                event["uid"] = value
    return None


def _parse_ics_dt(line: str) -> datetime | None:
    """Parse 'DTSTART:20260519T150000Z' or 'DTSTART;TZID=America/New_York:20260519T110000'."""
    header, _, value = line.partition(":")
    parts = header.split(";")
    tzid = None
    for p in parts[1:]:
        if p.startswith("TZID="):
            tzid = p[5:]
    value = value.strip()
    try:
        if value.endswith("Z"):
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        if "T" in value:
            naive = datetime.strptime(value, "%Y%m%dT%H%M%S")
        else:
            naive = datetime.strptime(value, "%Y%m%d")
        if tzid:
            try:
                return naive.replace(tzinfo=ZoneInfo(tzid))
            except Exception:
                return naive.replace(tzinfo=DEFAULT_TZ)
        return naive.replace(tzinfo=DEFAULT_TZ)
    except ValueError:
        return None


def find_ics_attachment(msg: Message, access_token: str, message_id: str) -> str | None:
    """Look through the parsed message for a text/calendar part, then fetch
    its body if it's referenced by an attachment id."""
    for part in msg.walk():
        if part.get_content_type() == "text/calendar":
            payload = part.get_payload(decode=True)
            if payload:
                return payload.decode("utf-8", errors="replace")
    # Fallback: pull the attachment via API (some MIME parts have empty payload)
    full = _api_get(
        f"/users/me/messages/{message_id}", access_token, {"format": "full"}
    )
    for part in _walk_parts(full.get("payload", {})):
        mime = part.get("mimeType", "")
        filename = part.get("filename", "")
        if mime == "text/calendar" or filename.endswith(".ics"):
            att_id = part.get("body", {}).get("attachmentId")
            if not att_id:
                continue
            att = _api_get(
                f"/users/me/messages/{message_id}/attachments/{att_id}",
                access_token,
            )
            data = att.get("data", "")
            if data:
                return base64.urlsafe_b64decode(data.encode()).decode("utf-8", errors="replace")
    return None


def _walk_parts(payload: dict):
    yield payload
    for p in payload.get("parts", []) or []:
        yield from _walk_parts(p)


# ------------------------------------------------------ heuristic fallback ---


_DATE_RE = re.compile(
    r"(?:on\s+)?"
    r"(?:(?P<weekday>Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*[,\s]+)?"
    r"(?:(?P<month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+"
    r"(?P<day>\d{1,2})(?:st|nd|rd|th)?(?:[,\s]+(?P<year>\d{4}))?)",
    re.IGNORECASE,
)
_TIME_RE = re.compile(
    r"\b(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>am|pm|AM|PM)"
    r"(?:\s*(?P<tz>EST|EDT|CST|CDT|MST|MDT|PST|PDT|ET|CT|MT|PT|UTC))?",
)
_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
           "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def parse_datetime_from_text(text: str, default_year: int) -> datetime | None:
    """Best-effort parse of 'Tuesday May 19th at 11am EST' style phrases."""
    date_m = _DATE_RE.search(text)
    time_m = _TIME_RE.search(text)
    if not (date_m and time_m):
        return None
    month = _MONTHS.get(date_m.group("month").lower()[:3])
    if not month:
        return None
    day = int(date_m.group("day"))
    year = int(date_m.group("year")) if date_m.group("year") else default_year
    hour = int(time_m.group("hour"))
    minute = int(time_m.group("minute") or 0)
    ampm = time_m.group("ampm").lower()
    if ampm == "pm" and hour != 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    try:
        return datetime(year, month, day, hour, minute, tzinfo=DEFAULT_TZ)
    except ValueError:
        return None


# -------------------------------------------------------------- pipeline ---


def gmail_search(query: str, access_token: str, max_results: int = 25) -> list[dict]:
    return search_threads(query, access_token, max_results=max_results)


def already_on_calendar(thread_id: str, existing: list[dict]) -> bool:
    for e in existing:
        desc = e.get("description", "") or ""
        if thread_id in desc:
            return True
    return False


def extract_company(sender: str, subject: str) -> str:
    if "@" in sender:
        domain = sender.split("@", 1)[1].rstrip(">").split()[0]
        parts = domain.lower().split(".")
        skip = {"applytojob", "ashbyhq", "teamtailor-mail", "indeed", "lever", "greenhouse",
                "myworkdayjobs", "smartrecruiters", "icims", "workday", "successfactors",
                "ziprecruiter", "linkedin", "glassdoor", "gmail", "outlook"}
        for part in parts:
            if part not in skip and len(part) > 2 and part not in {"com", "co", "io", "ai", "net", "org"}:
                return part.capitalize()
    # Fallback: take the first segment of the subject after "Interview"
    m = re.search(r"interview\s+(?:with\s+)?([A-Z][\w&'.-]+(?:\s+[A-Z][\w&'.-]+){0,2})",
                  subject, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return "Unknown"


def scan(
    *,
    days: int = 14,
    gmail_account: str = "default",
    calendar_account: str = "default",
    dry_run: bool = False,
    horizon_days: int = 60,
) -> dict:
    gmail_token = gmail_access_token(gmail_account)
    cal_token = calendar_fetch.get_access_token(calendar_account)

    # Pull existing calendar events for the horizon (to dedupe).
    now = datetime.now(DEFAULT_TZ)
    time_min = now.isoformat()
    time_max = (now + timedelta(days=horizon_days)).isoformat()
    existing = calendar_fetch.list_events(
        cal_token, time_min=time_min, time_max=time_max,
    )

    threads = gmail_search(INTERVIEW_QUERY.format(days=days), gmail_token, max_results=30)
    report: dict = {
        "scanned_at": now.isoformat(),
        "threads_examined": len(threads),
        "created": [],
        "skipped": [],
    }

    for t in threads:
        thread_id = t["thread_id"]
        subject = t.get("subject", "")
        sender = t.get("from", "")
        snippet = t.get("snippet", "")

        if already_on_calendar(thread_id, existing):
            report["skipped"].append({"thread": thread_id, "reason": "duplicate", "subject": subject})
            continue

        skip = is_skip(subject, snippet, sender)
        if skip:
            report["skipped"].append({"thread": thread_id, "reason": skip, "subject": subject})
            continue

        # Walk every message in the thread; prefer the latest non-sent one.
        thread = _api_get(
            f"/users/me/threads/{thread_id}", gmail_token, {"format": "minimal"}
        )
        candidate = None
        for m in thread.get("messages", []):
            msg = fetch_raw_message(m["id"], gmail_token)
            ics_text = find_ics_attachment(msg, gmail_token, m["id"])
            if ics_text:
                ev = parse_ics(ics_text)
                if ev and ev.get("dtstart") and ev["dtstart"] > now:
                    candidate = {"via": "ics", "event": ev, "msg": msg, "msg_id": m["id"]}
                    break  # stop at the first matching ICS in the thread
            # heuristic fallback collected only if no ICS hit yet
            if candidate is None:
                plain, html = message_bodies(msg)
                text = plain or (html_to_text(html) if html else "")
                if text:
                    dt = parse_datetime_from_text(text, default_year=now.year)
                    if dt and dt > now:
                        meeting = extract_meeting(text)
                        candidate = {"via": "heuristic", "start": dt,
                                     "text": text, "meeting": meeting,
                                     "msg": msg, "msg_id": m["id"]}

        if not candidate:
            report["skipped"].append({"thread": thread_id, "reason": "no_future_time",
                                      "subject": subject})
            continue

        company = extract_company(sender, subject)
        event_body = _build_event(candidate, thread_id, sender, company)

        if dry_run:
            report["created"].append({"thread": thread_id, "dry_run": True,
                                       "event": event_body, "via": candidate["via"]})
            continue

        created = calendar_fetch.create_event(cal_token, event_body)
        report["created"].append({
            "thread": thread_id,
            "event_id": created.get("id"),
            "summary": created.get("summary"),
            "start": created.get("start", {}).get("dateTime"),
            "via": candidate["via"],
            "html_link": created.get("htmlLink"),
        })

    return report


def _build_event(candidate: dict, thread_id: str, sender: str, company: str) -> dict:
    if candidate["via"] == "ics":
        ev = candidate["event"]
        start_dt: datetime = ev["dtstart"]
        end_dt: datetime = ev.get("dtend") or (start_dt + timedelta(minutes=45))
        summary = f"Interview — {company}"
        # Derive meeting details from ICS description if present
        description_src = ev.get("description", "") or ""
        meeting = extract_meeting(description_src)
        location = ev.get("location", "") or meeting.get("join_url", "")
    else:
        start_dt = candidate["start"]
        end_dt = start_dt + timedelta(minutes=45)
        summary = f"Interview — {company}"
        meeting = candidate.get("meeting", {})
        location = meeting.get("join_url", "")

    desc_lines = [
        f"Auto-imported by Reclaim interview-ingest on {datetime.now(DEFAULT_TZ):%Y-%m-%d %H:%M %Z}.",
        f"Source: {sender}",
        f"Gmail thread: {thread_id}",
        f"Via: {candidate['via']}",
        "",
        f"Join URL:    {meeting.get('join_url', 'not provided in email')}",
        f"Meeting ID:  {meeting.get('meeting_id', 'n/a')}",
        f"Passcode:    {meeting.get('passcode', 'n/a')}",
        f"Dial-in:     {meeting.get('dial_in', 'n/a')}",
    ]

    return {
        "summary": summary,
        "description": "\n".join(desc_lines),
        "location": location,
        "colorId": "11",  # Tomato — same as the interactive flow
        "start": {"dateTime": start_dt.isoformat(), "timeZone": str(DEFAULT_TZ)},
        "end":   {"dateTime": end_dt.isoformat(),   "timeZone": str(DEFAULT_TZ)},
    }


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="reclaim.interviews")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="Scan Gmail and add new interviews to the calendar.")
    s.add_argument("--days", type=int, default=14,
                   help="How many days of Gmail to scan (default: 14)")
    s.add_argument("--gmail-account", default="default")
    s.add_argument("--calendar-account", default="default")
    s.add_argument("--horizon-days", type=int, default=60,
                   help="How far ahead on the calendar to dedupe against (default: 60)")
    s.add_argument("--dry-run", action="store_true",
                   help="Print the events that would be created, don't actually create them.")
    args = p.parse_args(argv)
    if args.cmd == "scan":
        report = scan(
            days=args.days,
            gmail_account=args.gmail_account,
            calendar_account=args.calendar_account,
            horizon_days=args.horizon_days,
            dry_run=args.dry_run,
        )
        json.dump(report, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")


if __name__ == "__main__":
    main()
