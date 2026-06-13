from datetime import datetime
from zoneinfo import ZoneInfo

from reclaim.interviews import _resolve_tzid, parse_ics


def _ics(dtstart: str, dtend: str) -> str:
    return (
        "BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:Interview\n"
        f"{dtstart}\n{dtend}\nEND:VEVENT\nEND:VCALENDAR"
    )


def test_outlook_windows_tzid_mountain():
    # Outlook writes quoted Windows zone names. 10:30 Mountain == 16:30 UTC.
    ics = _ics(
        'DTSTART;TZID="Mountain Standard Time":20260616T103000',
        'DTEND;TZID="Mountain Standard Time":20260616T113000',
    )
    dt = parse_ics(ics)["dtstart"]
    assert dt.astimezone(ZoneInfo("UTC")) == datetime(2026, 6, 16, 16, 30, tzinfo=ZoneInfo("UTC"))


def test_outlook_windows_tzid_unquoted_pacific():
    ics = _ics(
        "DTSTART;TZID=Pacific Standard Time:20260616T090000",
        "DTEND;TZID=Pacific Standard Time:20260616T093000",
    )
    dt = parse_ics(ics)["dtstart"]
    # 09:00 Pacific (PDT, -7) == 16:00 UTC
    assert dt.astimezone(ZoneInfo("UTC")) == datetime(2026, 6, 16, 16, 0, tzinfo=ZoneInfo("UTC"))


def test_iana_tzid_still_works():
    ics = _ics(
        "DTSTART;TZID=America/New_York:20260616T110000",
        "DTEND;TZID=America/New_York:20260616T120000",
    )
    dt = parse_ics(ics)["dtstart"]
    assert dt.astimezone(ZoneInfo("UTC")) == datetime(2026, 6, 16, 15, 0, tzinfo=ZoneInfo("UTC"))


def test_utc_zulu_unaffected():
    ics = _ics("DTSTART:20260616T150000Z", "DTEND:20260616T160000Z")
    dt = parse_ics(ics)["dtstart"]
    assert dt.astimezone(ZoneInfo("UTC")) == datetime(2026, 6, 16, 15, 0, tzinfo=ZoneInfo("UTC"))


def test_resolve_tzid_helpers():
    assert str(_resolve_tzid('"Mountain Standard Time"')) == "America/Denver"
    assert str(_resolve_tzid("Eastern Standard Time")) == "America/New_York"
    assert str(_resolve_tzid("America/Los_Angeles")) == "America/Los_Angeles"
    # Unknown name falls back to the local default zone.
    assert str(_resolve_tzid("Totally Bogus Zone")) == "America/Detroit"
