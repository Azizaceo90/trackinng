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
from email.utils import parsedate_to_datetime
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
    r"\b(canceled|cancelled|reschedul[a-z]*|unfortunately|"
    r"not move forward|next time|thank you for interviewing|"
    r"interview update|thank you for your time|"
    r"we have decided|moved to the next step|"
    r"are no longer moving|been filled|filled the position)\b",
    re.IGNORECASE,
)
# Rejection / status language, separate from cancel/reschedule so the latter
# can be neutralised when it's only sign-off boilerplate.
_REJECTION_PATTERNS = re.compile(
    r"\b(unfortunately|not move forward|next time|thank you for interviewing|"
    r"interview update|thank you for your time|we have decided|"
    r"moved to the next step|are no longer moving|been filled|filled the position)\b",
    re.IGNORECASE,
)
CONFIRM_PATTERNS = re.compile(
    r"\b(confirm(ed|ation)?|you('re| are) all set|"
    r"your interview is|see you (on|at)|"
    r"this email is a confirmation|"
    r"interview details|booking is confirmed|"
    # Phone-screen phrasing — recruiters confirm a call without an invite.
    r"call added to|added (our|your|the) call|"
    r"have (our|your|the) call (added|set|scheduled)|"
    r"(i'?ll|i will) (be )?call(ing)? you|(i'?ll|i will) be calling|"
    r"call(ing)? you (from|at)|"
    r"look(ing)? forward to (our|the|your) (call|chat)|"
    r"(our|your) (call|chat|phone screen|interview) is (set|scheduled|confirmed)|"
    # Reschedule notices that carry a NEW time — re-book at the new slot.
    r"reschedul(ed|e)?\s+(to|for)|moved (to|your interview to)|"
    r"new time (is|of|will be)|updated (time|to))\b",
    re.IGNORECASE,
)
# Recruiter *agreeing* to a time you proposed earlier in the thread. Distinct
# from CONFIRM_PATTERNS: there's usually no time in the agreeing message itself
# ("I can do that! I'll call you then"), so it's only acted on when an earlier
# message supplied a concrete future time.
AGREE_PATTERNS = re.compile(
    r"\b(i can do that|that works|works for me|that time works|"
    r"sounds (good|great|perfect)|that('?s| is) (great|perfect|fine)|"
    r"see you (then|on|at)|talk (to|with) you then|"
    r"looking forward to (it|speaking|chatting|our (call|chat))|"
    r"let'?s do (it|that|mon|tue|wed|thu|fri)|"
    r"(i'?ll|i will) (be )?(call(ing)? you|give you a call))\b",
    re.IGNORECASE,
)
ASYNC_DOMAINS = {"hireflix.com", "sparkhire.com", "vidcruiter.com", "spark.hire"}
ASYNC_HINT = re.compile(r"\b(video interview|automated|on your own time|"
                        r"complete the video|record your)\b", re.IGNORECASE)
# "let me know if you need to reschedule or cancel" is standard sign-off
# boilerplate on a *confirmation* — it must not read as an actual cancel/move.
_RESCHEDULE_BOILERPLATE = re.compile(
    r"\b(if you|let me know if|feel free|should you|in case|need to reschedule or cancel)\b",
    re.IGNORECASE,
)


def is_skip(subject: str, snippet: str, sender: str) -> str | None:
    """Returns a skip reason or None."""
    haystack = f"{subject}\n{snippet}".lower()
    sender_lower = sender.lower()
    if SKIP_PATTERNS.search(haystack):
        boilerplate = bool(_RESCHEDULE_BOILERPLATE.search(haystack))
        if ("canceled" in haystack or "cancelled" in haystack) and not boilerplate:
            return "cancelled"
        if "reschedul" in haystack and not boilerplate:
            return "reschedule"
        # A genuine rejection/status term (not just cancel/reschedule boilerplate).
        if _REJECTION_PATTERNS.search(haystack):
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


# Outlook/Exchange write Windows timezone names into ICS TZIDs (e.g.
# TZID="Mountain Standard Time") rather than IANA names (America/Denver).
# ZoneInfo only knows IANA, so without this map every Outlook invite from a
# non-Eastern sender silently fell back to the local zone and landed on the
# calendar at the wrong hour. Subset of the CLDR windowsZones mapping covering
# the zones our senders actually use.
_WINDOWS_TZ = {
    "Dateline Standard Time": "Etc/GMT+12",
    "Hawaiian Standard Time": "Pacific/Honolulu",
    "Alaskan Standard Time": "America/Anchorage",
    "Pacific Standard Time": "America/Los_Angeles",
    "Pacific Standard Time (Mexico)": "America/Tijuana",
    "US Mountain Standard Time": "America/Phoenix",
    "Mountain Standard Time": "America/Denver",
    "Mountain Standard Time (Mexico)": "America/Chihuahua",
    "Central Standard Time": "America/Chicago",
    "Central Standard Time (Mexico)": "America/Mexico_City",
    "Canada Central Standard Time": "America/Regina",
    "Eastern Standard Time": "America/New_York",
    "US Eastern Standard Time": "America/Indiana/Indianapolis",
    "Atlantic Standard Time": "America/Halifax",
    "UTC": "UTC",
    "GMT Standard Time": "Europe/London",
    "Greenwich Standard Time": "Atlantic/Reykjavik",
    "W. Europe Standard Time": "Europe/Berlin",
    "Central Europe Standard Time": "Europe/Budapest",
    "Central European Standard Time": "Europe/Warsaw",
    "Romance Standard Time": "Europe/Paris",
    "GTB Standard Time": "Europe/Bucharest",
    "India Standard Time": "Asia/Kolkata",
    "China Standard Time": "Asia/Shanghai",
    "Singapore Standard Time": "Asia/Singapore",
    "Tokyo Standard Time": "Asia/Tokyo",
    "Korea Standard Time": "Asia/Seoul",
    "AUS Eastern Standard Time": "Australia/Sydney",
    "New Zealand Standard Time": "Pacific/Auckland",
}


def _resolve_tzid(tzid: str):
    """Resolve an ICS TZID to a tzinfo, defaulting to local on failure.

    Handles IANA names directly, Outlook's Windows zone names via a lookup,
    and the surrounding quotes Outlook adds (TZID="Mountain Standard Time")."""
    name = tzid.strip().strip('"').strip()
    if not name:
        return DEFAULT_TZ
    try:
        return ZoneInfo(name)
    except Exception:
        pass
    mapped = _WINDOWS_TZ.get(name)
    if mapped:
        try:
            return ZoneInfo(mapped)
        except Exception:
            pass
    return DEFAULT_TZ


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
            return naive.replace(tzinfo=_resolve_tzid(tzid))
        return naive.replace(tzinfo=DEFAULT_TZ)
    except ValueError:
        return None


def ics_from_message(msg: Message) -> str | None:
    """Return the text/calendar body from a fully-parsed message, or None.

    Works for any backend that hands us a complete MIME message (Gmail API
    format=raw, or IMAP RFC822) — no extra network call needed."""
    for part in msg.walk():
        if part.get_content_type() == "text/calendar":
            payload = part.get_payload(decode=True)
            if payload:
                return payload.decode("utf-8", errors="replace")
    return None


def find_ics_attachment(msg: Message, access_token: str, message_id: str) -> str | None:
    """Look through the parsed message for a text/calendar part, then fetch
    its body via the Gmail API if the inline part was empty."""
    inline = ics_from_message(msg)
    if inline:
        return inline
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
    # Minutes may be separated by ":" or "." — recruiters write "2.30pm".
    r"\b(?P<hour>\d{1,2})(?:[:.](?P<minute>\d{2}))?\s*(?P<ampm>am|pm|AM|PM)"
    r"(?:\s*(?P<tz>EST|EDT|CST|CDT|MST|MDT|PST|PDT|ET|CT|MT|PT|UTC))?",
)
# Numeric M/D or M/D/Y dates ("6/11", "06/11/2026") — phone-screen confirmations
# rarely spell the month out. Month 1-12 / day 1-31 to avoid matching ratios.
_NUMERIC_DATE_RE = re.compile(
    r"\b(?P<nmonth>1[0-2]|0?[1-9])/(?P<nday>3[01]|[12]\d|0?[1-9])"
    r"(?:/(?P<nyear>\d{4}|\d{2}))?\b"
)
_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
           "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}

_WEEKDAYS = {"monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1,
             "wednesday": 2, "wed": 2, "thursday": 3, "thu": 3, "thur": 3,
             "thurs": 3, "friday": 4, "fri": 4, "saturday": 5, "sat": 5,
             "sunday": 6, "sun": 6}
# "(next|this) Monday" / "Monday next week" / "Tuesday" — relative to the email's
# own date. Captures an optional leading modifier and an optional "next week".
_REL_WEEKDAY_RE = re.compile(
    r"\b(?:(?P<pre>this|next)\s+)?"
    r"(?P<wd>mon|tues?|wed|thurs?|fri|sat|sun)[a-z]*"
    r"(?P<post>\s+next\s+week)?\b",
    re.IGNORECASE,
)


def _relative_date(text: str, ref: datetime) -> tuple[int, int, int] | None:
    """Resolve 'today' / 'tomorrow' / '(next) <weekday> (next week)' against the
    email's own send date `ref`. Returns (year, month, day) or None."""
    low = text.lower()
    if re.search(r"\btomorrow\b", low):
        d = ref + timedelta(days=1)
        return d.year, d.month, d.day
    m = _REL_WEEKDAY_RE.search(text)
    if m:
        target = _WEEKDAYS.get(m.group("wd").lower())
        if target is None:
            return None
        next_week = bool(m.group("post")) or (m.group("pre") or "").lower() == "next"
        if next_week:
            # Monday of next calendar week, then offset to the named weekday.
            monday_next = ref - timedelta(days=ref.weekday()) + timedelta(days=7)
            d = monday_next + timedelta(days=target)
        else:
            days_ahead = (target - ref.weekday()) % 7
            d = ref + timedelta(days=days_ahead)
        return d.year, d.month, d.day
    if re.search(r"\btoday\b", low):
        return ref.year, ref.month, ref.day
    return None

# Map the timezone abbreviation captured by _TIME_RE to an IANA zone. We use
# the IANA zone (not a fixed offset) so ZoneInfo applies the correct DST rule
# for the interview's actual date — e.g. "ET" in June resolves to EDT (-4),
# in January to EST (-5). People write "EST"/"PST" loosely year-round, so the
# standard/daylight spelling is treated as a hint for the region, not a literal
# offset.
_TZ_ABBREV = {
    "ET": "America/New_York", "EST": "America/New_York", "EDT": "America/New_York",
    "CT": "America/Chicago", "CST": "America/Chicago", "CDT": "America/Chicago",
    "MT": "America/Denver", "MST": "America/Denver", "MDT": "America/Denver",
    "PT": "America/Los_Angeles", "PST": "America/Los_Angeles", "PDT": "America/Los_Angeles",
    "UTC": "UTC",
}


def _tzinfo_from_abbrev(abbrev: str | None):
    """Resolve a captured tz abbreviation to a tzinfo, defaulting to local."""
    if not abbrev:
        return DEFAULT_TZ
    zone = _TZ_ABBREV.get(abbrev.upper())
    if not zone:
        return DEFAULT_TZ
    try:
        return ZoneInfo(zone)
    except Exception:
        return DEFAULT_TZ


def _extract_date(text: str, default_year: int,
                  ref_date: datetime | None = None) -> tuple[int, int, int] | None:
    """Pull (year, month, day) from a spelled-out, numeric, or (when ref_date is
    given) relative date. Returns None if no date is found."""
    m = _DATE_RE.search(text)
    if m:
        month = _MONTHS.get(m.group("month").lower()[:3])
        if not month:
            return None
        day = int(m.group("day"))
        year = int(m.group("year")) if m.group("year") else default_year
        return year, month, day
    n = _NUMERIC_DATE_RE.search(text)
    if n:
        month = int(n.group("nmonth"))
        day = int(n.group("nday"))
        if n.group("nyear"):
            y = int(n.group("nyear"))
            year = y + 2000 if y < 100 else y
        else:
            year = default_year
        return year, month, day
    if ref_date is not None:
        return _relative_date(text, ref_date)
    return None


def parse_datetime_from_text(text: str, default_year: int,
                             ref_date: datetime | None = None) -> datetime | None:
    """Best-effort parse of 'Tuesday May 19th at 11am EST' style phrases.

    Handles spelled-out ("May 19th"), numeric ("6/11"), and — when ref_date (the
    email's send time) is supplied — relative ("Monday next week", "tomorrow")
    dates, plus ":"/"." minute separators. Honors a stated timezone (EST/PST/…);
    falls back to the local default zone when no zone is given."""
    time_m = _TIME_RE.search(text)
    if not time_m:
        return None
    date = _extract_date(text, default_year, ref_date)
    if not date:
        return None
    year, month, day = date
    hour = int(time_m.group("hour"))
    minute = int(time_m.group("minute") or 0)
    ampm = time_m.group("ampm").lower()
    if ampm == "pm" and hour != 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    tzinfo = _tzinfo_from_abbrev(time_m.group("tz"))
    try:
        return datetime(year, month, day, hour, minute, tzinfo=tzinfo)
    except ValueError:
        return None


def parse_duration_minutes(text: str) -> int | None:
    """Pull a stated meeting length ('20-30 minute call', '1 hour', 'half hour')
    from free text. Returns minutes, or None when nothing plausible is stated.

    For a range we take the longer end so the block fully covers the call."""
    t = (text or "").lower()
    m = re.search(r"\b(\d{1,3})\s*(?:-|–|—|to)\s*(\d{1,3})\s*(?:min\b|minute)", t)
    if m:
        mins = max(int(m.group(1)), int(m.group(2)))
    elif re.search(r"\bhalf[\s-]?(?:an\s+)?hour\b", t):
        mins = 30
    elif re.search(r"\b(?:an|one|1)\s+hour\b", t):
        mins = 60
    else:
        m = re.search(r"\b(\d{1,3})[\s-]*(?:min\b|minute)", t)
        if m:
            mins = int(m.group(1))
        else:
            m = re.search(r"\b(\d(?:\.\d)?)[\s-]*(?:hour|hr)s?\b", t)
            if not m:
                return None
            mins = int(float(m.group(1)) * 60)
    return mins if 5 <= mins <= 480 else None


# -------------------------------------------------------------- pipeline ---


def gmail_search(query: str, access_token: str, max_results: int = 25) -> list[dict]:
    return search_threads(query, access_token, max_results=max_results)


# First line of every event description we write — used to recognise our own
# auto-created events when cleaning up after a cancellation/reschedule.
INGEST_MARKER = "Auto-imported by interview-ingest"


def already_on_calendar(thread_id: str, existing: list[dict]) -> bool:
    for e in existing:
        desc = e.get("description", "") or ""
        if thread_id in desc:
            return True
    return False


def _thread_event_ids(thread_id: str, existing: list[dict]) -> list[str]:
    """Calendar ids of events WE auto-created for this Gmail thread."""
    ids = []
    for e in existing:
        desc = e.get("description", "") or ""
        if INGEST_MARKER in desc and thread_id in desc and e.get("id"):
            ids.append(e["id"])
    return ids


def _remove_thread_events(cal_token, cal_id, thread_id, existing, dry_run):
    """Delete the events we previously auto-created for a thread (cancellation /
    reschedule). Also drops them from the in-memory `existing` list so later
    dedupe in the same run doesn't trip over a now-deleted event. Returns the
    ids removed."""
    ids = _thread_event_ids(thread_id, existing)
    if not ids:
        return []
    if not dry_run:
        for eid in ids:
            try:
                calendar_fetch.delete_event(cal_token, eid, calendar_id=cal_id)
            except Exception:
                pass
    removed = set(ids)
    existing[:] = [e for e in existing if e.get("id") not in removed]
    return ids


def conflicts_with_existing(start_dt: datetime, existing: list[dict],
                            tolerance_min: int = 90) -> bool:
    """Belt-and-suspenders dedupe: if a separately-imported interview event
    sits within `tolerance_min` of our candidate start, treat it as the
    same interview even if the Gmail thread IDs differ."""
    tol = timedelta(minutes=tolerance_min)
    for e in existing:
        summary = (e.get("summary") or "")
        if "Interview" not in summary:
            continue
        ev_start = e.get("start", {}).get("dateTime")
        if not ev_start:
            continue
        try:
            existing_dt = datetime.fromisoformat(ev_start.replace("Z", "+00:00"))
        except ValueError:
            continue
        if abs(existing_dt - start_dt) <= tol:
            return True
    return False


_URL_RE = re.compile(r'https?://[^\s<>"\')]+', re.IGNORECASE)


def _normalize_meeting_url(url: str) -> str:
    """Reduce a meeting URL to a stable key identifying the meeting room.

    Different copies of the same invite (Google's auto-added attendee event vs.
    our ingest) carry the same join link but may differ in tracking params, so
    we key on the provider's unique meeting id rather than the raw URL."""
    u = url.lower().rstrip('.,;)>"\'')
    m = re.search(r'[?&]mtid=([a-z0-9]+)', u)          # Webex
    if m:
        return f"webex:{m.group(1)}"
    m = re.search(r'zoom\.us/(?:j|w|wc)/(\d+)', u)      # Zoom
    if m:
        return f"zoom:{m.group(1)}"
    m = re.search(r'meet\.google\.com/([a-z0-9-]+)', u)  # Google Meet
    if m:
        return f"meet:{m.group(1)}"
    m = re.search(r'teams\.microsoft\.com/l/meetup-join/([^?\s]+)', u)  # Teams
    if m:
        return f"teams:{m.group(1)}"
    # Fallback: host + path, query/fragment dropped.
    m = re.match(r'https?://([^/]+)(/[^?#\s]*)?', u)
    if m:
        return f"{m.group(1)}{(m.group(2) or '').rstrip('/')}"
    return u


def _meeting_keys(text: str) -> set[str]:
    """Extract comparable meeting-room keys from any free text / URL blob."""
    return {
        k for k in (_normalize_meeting_url(m.group(0)) for m in _URL_RE.finditer(text or ""))
        if k
    }


def meeting_link_on_calendar(candidate_text: str, existing: list[dict]) -> bool:
    """Skip interviews whose meeting link already lives on the calendar.

    Google auto-adds events you're an attendee of, so the real invite is often
    already present under the organizer's title and (pre-fix) a different time —
    which slipped past the thread-id and time-proximity dedupe. Matching on the
    Webex/Zoom/Meet/Teams meeting id catches those duplicates."""
    keys = _meeting_keys(candidate_text)
    if not keys:
        return False
    for e in existing:
        hay = f"{e.get('location', '') or ''}\n{e.get('description', '') or ''}"
        if keys & _meeting_keys(hay):
            return True
    return False


def _candidate_link_text(candidate: dict) -> str:
    """Gather everything on a candidate that might carry a meeting URL."""
    if candidate["via"] == "ics":
        ev = candidate["event"]
        return f"{ev.get('location', '') or ''}\n{ev.get('description', '') or ''}"
    meeting = candidate.get("meeting", {}) or {}
    return f"{meeting.get('join_url', '') or ''}\n{candidate.get('text', '') or ''}"


def _company_from_domain(domain: str) -> str | None:
    """Pick a likely company name out of an email domain, or None."""
    parts = domain.lower().split(".")
    skip = {"applytojob", "ashbyhq", "teamtailor-mail", "indeed", "lever", "greenhouse",
            "myworkdayjobs", "smartrecruiters", "icims", "workday", "successfactors",
            "ziprecruiter", "linkedin", "glassdoor", "gmail", "outlook", "googlemail",
            "yahoo", "hotmail", "icloud", "proton", "protonmail", "fastmail",
            "jobs", "careers", "apply", "hire", "hiring", "talent", "recruiting",
            "mail", "email", "noreply", "smtp", "no-reply"}
    tlds = {"com", "co", "io", "ai", "net", "org", "us", "uk", "ca"}
    for part in parts:
        if part not in skip and len(part) > 2 and part not in tlds:
            return part.capitalize()
    return None


# Company-name capture: a Capitalized token plus up to 2 more Capitalized
# tokens. Case-sensitive on purpose — lowercase tokens like "on", "to",
# "and" must NOT extend the match past the company name.
_COMPANY_TOKENS = r"([A-Z][\w&'.-]+(?:\s+[A-Z][\w&'.-]+){0,2})"

_DISPLAY_NAME_RE = re.compile(r'^\s*"?([^"<@]+?)"?\s*<')
_FROM_COMPANY_RE = re.compile(r"(?i:\bfrom)\s+" + _COMPANY_TOKENS)
_SUBJECT_COMPANY_RE = re.compile(
    r"(?i:interview\s+(?:with|at|for))\s+" + _COMPANY_TOKENS
)
_BODY_COMPANY_RES = [
    re.compile(r"(?i:\binterview(?:ing)?\s+(?:with|at|for))\s+" + _COMPANY_TOKENS),
    re.compile(
        r"(?i:\b(?:thank\s+you|thanks)\s+for\s+"
        r"(?:applying\s+to|your\s+interest\s+in|reaching\s+out\s+to))\s+"
        + _COMPANY_TOKENS
    ),
    re.compile(r"(?i:\bwe\s+at)\s+" + _COMPANY_TOKENS),
    re.compile(r"(?i:\bon\s+behalf\s+of)\s+" + _COMPANY_TOKENS),
]

# Role/seniority noise that often trails the company in subject lines
# (e.g. "Interview for Globex Senior Role"). Trim from the end of a match.
_ROLE_NOISE = {"role", "position", "job", "opening", "senior", "junior", "lead",
               "principal", "staff", "director", "vp", "engineer", "engineering",
               "manager", "designer", "developer", "analyst", "team"}


def _trim_role_suffix(name: str) -> str:
    tokens = name.split()
    while tokens and tokens[-1].lower() in _ROLE_NOISE:
        tokens.pop()
    return " ".join(tokens) if tokens else name


def _company_from_display_name(header: str) -> str | None:
    """Pull a company name out of an RFC 2822 From-style header's display name.

    Handles: `"Acme Recruiting" <…>`, `John from Acme <…>`, `Acme Talent <…>`.
    Returns None if the display name is just a person's name with no obvious
    company token.
    """
    m = _DISPLAY_NAME_RE.match(header or "")
    if not m:
        return None
    name = m.group(1).strip()
    # "John from Acme"
    f = _FROM_COMPANY_RE.search(name)
    if f:
        return f.group(1).strip()
    # "Acme Recruiting" / "Acme Talent" / "Acme Hiring" — last word is a role,
    # earlier words are the company.
    role_words = {"recruiting", "recruiter", "talent", "hiring", "careers",
                  "people", "team", "hr"}
    tokens = name.split()
    if len(tokens) >= 2 and tokens[-1].lower() in role_words:
        return " ".join(tokens[:-1])
    return None


def extract_company(
    sender: str,
    subject: str,
    body: str = "",
    ics_organizer: str = "",
) -> str | None:
    """Best-effort company-name extraction.

    Returns None when no signal hits; callers decide the fallback title.
    Signals tried, in order of trust:
      1. ICS organizer domain
      2. ICS organizer display name
      3. Sender email domain
      4. Sender display name
      5. Subject "interview with X" pattern
      6. Body patterns: "interview with X", "thanks for your interest in X", etc.
    """
    if ics_organizer:
        if "@" in ics_organizer:
            domain = ics_organizer.split("@", 1)[1].rstrip(">").split()[0]
            guess = _company_from_domain(domain)
            if guess:
                return guess
        guess = _company_from_display_name(ics_organizer)
        if guess:
            return guess

    if "@" in sender:
        domain = sender.split("@", 1)[1].rstrip(">").split()[0]
        guess = _company_from_domain(domain)
        if guess:
            return guess

    guess = _company_from_display_name(sender)
    if guess:
        return guess

    m = _SUBJECT_COMPANY_RE.search(subject or "")
    if m:
        return _trim_role_suffix(m.group(1).strip())

    for pat in _BODY_COMPANY_RES:
        m = pat.search(body or "")
        if m:
            return _trim_role_suffix(m.group(1).strip())

    return None


def _clean_subject(subject: str) -> str:
    """Strip Re:/Fwd: prefixes and cap length so it reads as a calendar title."""
    s = (subject or "").strip()
    while True:
        stripped = re.sub(r"^(re|fwd?|fw)\s*:\s*", "", s, flags=re.IGNORECASE)
        if stripped == s:
            break
        s = stripped
    s = s.strip() or "needs review"
    return s if len(s) <= 80 else s[:77].rstrip() + "…"


class _ApiGmailBackend:
    """Gmail via the HTTP API + OAuth token (the original backend)."""

    def __init__(self, account: str):
        self.token = gmail_access_token(account)

    def search(self, query: str, max_results: int) -> list[dict]:
        return gmail_search(query, self.token, max_results=max_results)

    def thread_messages(self, thread_meta: dict) -> list[tuple[Message, str, bool]]:
        thread = _api_get(
            f"/users/me/threads/{thread_meta['thread_id']}", self.token,
            {"format": "minimal"},
        )
        out: list[tuple[Message, str, bool]] = []
        for m in thread.get("messages", []):
            is_sent = "SENT" in (m.get("labelIds") or [])
            out.append((fetch_raw_message(m["id"], self.token), m["id"], is_sent))
        return out

    def ics_text(self, msg: Message, msg_id: str) -> str | None:
        return find_ics_attachment(msg, self.token, msg_id)

    def close(self) -> None:
        pass


class _ImapGmailBackend:
    """Gmail via IMAP + app password — no OAuth, no token expiry."""

    def __init__(self, account: str):
        from reclaim import gmail_imap
        self.client = gmail_imap.GmailIMAP(account)
        self._threads: dict[str, dict] = {}

    def search(self, query: str, max_results: int) -> list[dict]:
        threads = self.client.search_threads(query, max_results=max_results)
        for t in threads:
            self._threads[t["thread_id"]] = t
        return threads

    def thread_messages(self, thread_meta: dict) -> list[tuple[Message, str, bool]]:
        t = self._threads.get(thread_meta["thread_id"], thread_meta)
        return [
            (m["message"], str(m["uid"]), bool(m["is_sent"]))
            for m in t.get("_messages", [])
        ]

    def ics_text(self, msg: Message, msg_id: str) -> str | None:
        return ics_from_message(msg)

    def close(self) -> None:
        self.client.close()


def _make_gmail_backend(account: str):
    """Prefer the no-expiry IMAP backend when an app password is configured;
    otherwise fall back to the OAuth HTTP-API backend."""
    from reclaim import gmail_imap
    if gmail_imap.available(account):
        return _ImapGmailBackend(account)
    return _ApiGmailBackend(account)


def _message_datetime(msg: Message) -> datetime | None:
    """The message's send time in the local zone — anchor for relative dates."""
    raw = msg.get("Date")
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=DEFAULT_TZ)
    return dt.astimezone(DEFAULT_TZ)


def _find_candidate(backend, thread_meta: dict, now: datetime) -> dict | None:
    """Walk a thread chronologically and return the bookable candidate, or None.

    Three ways an interview gets booked:
      1. ICS invite (inbound) with a future start — authoritative.
      2. Recruiter's own message states a future time AND confirms (Carlie).
      3. Recruiter agrees to a time proposed earlier in the thread — often in
         your reply (Josh). A time from your sent mail is only ever promoted
         once a later inbound message agrees, which keeps unconfirmed proposals
         from creating phantom events.
    """
    last_proposed: datetime | None = None  # newest future time seen in-thread
    last_duration: int | None = None       # newest stated call length, any message
    for msg, msg_id, is_sent in backend.thread_messages(thread_meta):
        if not is_sent:
            ics_text = backend.ics_text(msg, msg_id)
            if ics_text:
                ev = parse_ics(ics_text)
                if ev and ev.get("dtstart") and ev["dtstart"] > now:
                    return {"via": "ics", "event": ev, "msg": msg, "msg_id": msg_id}

        plain, html = message_bodies(msg)
        text = plain or (html_to_text(html) if html else "")
        if not text:
            continue
        own_dt = parse_datetime_from_text(
            text, default_year=now.year, ref_date=_message_datetime(msg)
        )
        if own_dt and own_dt > now:
            last_proposed = own_dt
        dur = parse_duration_minutes(text)
        if dur:
            last_duration = dur

        if is_sent:
            continue  # your own message is never the confirmation

        # Path 2 — recruiter states a future time and confirms it.
        if own_dt and own_dt > now and CONFIRM_PATTERNS.search(text):
            return {"via": "heuristic", "start": own_dt, "text": text,
                    "meeting": extract_meeting(text), "msg": msg, "msg_id": msg_id,
                    "sender": msg.get("From", ""), "duration_min": last_duration}
        # Path 3 — recruiter agrees to a previously-proposed future time.
        if last_proposed and AGREE_PATTERNS.search(text):
            return {"via": "heuristic", "start": last_proposed, "text": text,
                    "meeting": extract_meeting(text), "msg": msg, "msg_id": msg_id,
                    "sender": msg.get("From", ""), "duration_min": last_duration}
    return None


def scan(
    *,
    days: int = 14,
    gmail_account: str = "default",
    calendar_account: str = "default",
    dry_run: bool = False,
    horizon_days: int = 60,
    calendar_id: str | None = None,
) -> dict:
    backend = _make_gmail_backend(gmail_account)
    cal_token = calendar_fetch.get_access_token(calendar_account)
    # Under a service account the impersonated user is fixed, so the target
    # calendar must be passed explicitly per account (default vs personal2);
    # falls back to the global PERSONAL_CALENDAR_ID / "primary".
    cal_id = calendar_id or calendar_fetch.target_calendar_id()

    # Pull existing calendar events for the horizon (to dedupe).
    now = datetime.now(DEFAULT_TZ)
    time_min = now.isoformat()
    time_max = (now + timedelta(days=horizon_days)).isoformat()
    existing = calendar_fetch.list_events(
        cal_token, time_min=time_min, time_max=time_max, calendar_id=cal_id,
    )

    try:
        threads = backend.search(INTERVIEW_QUERY.format(days=days), max_results=30)
    except Exception:
        backend.close()
        raise
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

        skip = is_skip(subject, snippet, sender)
        if skip == "cancelled":
            # Pull any event we previously auto-created for this thread.
            removed = _remove_thread_events(cal_token, cal_id, thread_id, existing, dry_run)
            report["skipped"].append({"thread": thread_id, "reason": "cancelled",
                                      "subject": subject, "removed_events": removed})
            continue
        if skip == "reschedule":
            # Old slot is wrong now — drop our stale event, then fall through to
            # re-book at the new time if the thread states one.
            removed = _remove_thread_events(cal_token, cal_id, thread_id, existing, dry_run)
            report.setdefault("rescheduled", []).append(
                {"thread": thread_id, "subject": subject, "removed_events": removed})
        elif skip:
            report["skipped"].append({"thread": thread_id, "reason": skip, "subject": subject})
            continue
        elif already_on_calendar(thread_id, existing):
            report["skipped"].append({"thread": thread_id, "reason": "duplicate", "subject": subject})
            continue

        # Walk the whole thread (your replies included) to find a bookable
        # interview: an ICS invite, a recruiter-stated time, or a recruiter
        # agreeing to a time proposed earlier in the thread.
        candidate = _find_candidate(backend, t, now)

        if not candidate:
            report["skipped"].append({"thread": thread_id, "reason": "no_future_time",
                                      "subject": subject})
            continue

        cand_start = (candidate["event"]["dtstart"] if candidate["via"] == "ics"
                      else candidate["start"])

        # Skip if Google already auto-added this same meeting (matched by the
        # Webex/Zoom/Meet/Teams join link) under the organizer's own invite.
        if meeting_link_on_calendar(_candidate_link_text(candidate), existing):
            report["skipped"].append({"thread": thread_id,
                                      "reason": "duplicate_meeting_link",
                                      "subject": subject,
                                      "start": cand_start.isoformat()})
            continue

        if conflicts_with_existing(cand_start, existing):
            report["skipped"].append({"thread": thread_id,
                                      "reason": "time_conflict_with_existing_interview",
                                      "subject": subject,
                                      "start": cand_start.isoformat()})
            continue

        # Gmail's thread-search metadata sometimes returns an empty "from".
        # Fall back to the actual message's From header (captured on the
        # candidate dict for the heuristic path; the raw msg has it for ICS).
        effective_sender = sender or (candidate.get("sender") or "")
        if not effective_sender and candidate.get("msg"):
            effective_sender = candidate["msg"].get("From", "")

        if candidate["via"] == "ics":
            body_text = candidate["event"].get("description", "") or ""
            ics_organizer = candidate["event"].get("organizer", "") or ""
        else:
            body_text = candidate.get("text", "") or ""
            ics_organizer = ""

        company = extract_company(effective_sender, subject, body=body_text,
                                  ics_organizer=ics_organizer)
        title = (f"Interview — {company}" if company
                 else f"Interview — {_clean_subject(subject)}")
        event_body = _build_event(candidate, thread_id, effective_sender, title)

        if dry_run:
            report["created"].append({"thread": thread_id, "dry_run": True,
                                       "event": event_body, "via": candidate["via"]})
            continue

        created = calendar_fetch.create_event(cal_token, event_body, calendar_id=cal_id)
        report["created"].append({
            "thread": thread_id,
            "event_id": created.get("id"),
            "summary": created.get("summary"),
            "start": created.get("start", {}).get("dateTime"),
            "via": candidate["via"],
            "html_link": created.get("htmlLink"),
        })

    backend.close()
    return report


def _build_event(candidate: dict, thread_id: str, sender: str, title: str) -> dict:
    if candidate["via"] == "ics":
        ev = candidate["event"]
        start_dt: datetime = ev["dtstart"]
        end_dt: datetime = ev.get("dtend") or (start_dt + timedelta(minutes=45))
        # Derive meeting details from ICS description if present
        description_src = ev.get("description", "") or ""
        meeting = extract_meeting(description_src)
        location = ev.get("location", "") or meeting.get("join_url", "")
    else:
        start_dt = candidate["start"]
        end_dt = start_dt + timedelta(minutes=candidate.get("duration_min") or 45)
        meeting = candidate.get("meeting", {})
        location = meeting.get("join_url", "")
    summary = title

    desc_lines = [
        f"Auto-imported by interview-ingest on {datetime.now(DEFAULT_TZ):%Y-%m-%d %H:%M %Z}.",
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
    s.add_argument("--calendar-id", default=None,
                   help="Explicit calendar id to write to (overrides "
                        "PERSONAL_CALENDAR_ID; needed when a service account "
                        "writes to per-account calendars).")
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
            calendar_id=args.calendar_id,
        )
        json.dump(report, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")


if __name__ == "__main__":
    main()
