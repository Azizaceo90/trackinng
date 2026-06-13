from datetime import datetime
from zoneinfo import ZoneInfo

from reclaim.interviews import parse_datetime_from_text


def test_parses_stated_pacific_time():
    # "11am PST" must be Pacific wall-clock, not Detroit wall-clock.
    dt = parse_datetime_from_text("Interview on May 19th at 11am PST", 2026)
    assert dt is not None
    # 11:00 Pacific (PDT in May, -7) == 18:00 UTC
    assert dt.astimezone(ZoneInfo("UTC")) == datetime(2026, 5, 19, 18, 0, tzinfo=ZoneInfo("UTC"))


def test_parses_stated_eastern_time():
    dt = parse_datetime_from_text("Confirmed for June 2nd at 2pm ET", 2026)
    assert dt is not None
    # 14:00 Eastern (EDT in June, -4) == 18:00 UTC
    assert dt.astimezone(ZoneInfo("UTC")) == datetime(2026, 6, 2, 18, 0, tzinfo=ZoneInfo("UTC"))


def test_utc_honored():
    dt = parse_datetime_from_text("Scheduled Mar 3 at 9am UTC", 2026)
    assert dt is not None
    assert dt.astimezone(ZoneInfo("UTC")) == datetime(2026, 3, 3, 9, 0, tzinfo=ZoneInfo("UTC"))


def test_dst_aware_for_winter_eastern():
    # January Eastern is EST (-5), so 10am ET == 15:00 UTC.
    dt = parse_datetime_from_text("Interview on Jan 15th at 10am ET", 2026)
    assert dt is not None
    assert dt.astimezone(ZoneInfo("UTC")) == datetime(2026, 1, 15, 15, 0, tzinfo=ZoneInfo("UTC"))


def test_bare_time_defaults_to_local_zone():
    # No timezone in the text -> falls back to America/Detroit.
    dt = parse_datetime_from_text("Interview on May 19th at 11am", 2026)
    assert dt is not None
    assert str(dt.tzinfo) == "America/Detroit"
    assert (dt.hour, dt.minute) == (11, 0)
