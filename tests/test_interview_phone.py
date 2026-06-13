"""Phone-screen confirmations: numeric dates, '.' time separators, and the
recruiter phrasing that doesn't come with a calendar invite."""
from datetime import datetime
from zoneinfo import ZoneInfo

from reclaim.interviews import CONFIRM_PATTERNS, parse_datetime_from_text

CARLIE = (
    "Thank you for sending over your availability - I have our call added to my "
    "calendar for 2.30pm EST tomorrow 6/11, please let me know if you this time "
    "doesn't work for you! I'll be calling from my phone # - 315-783-3588."
)
JOSH = (
    "Thank you for getting back to me on Indeed. I know you mentioned you would be "
    "available to chat today and tomorrow between 11am-5pm EST. Unfortunately, I am "
    "traveling. Would you happen to be available on Monday next week? 20-30 minute call."
)


def test_period_time_separator():
    dt = parse_datetime_from_text("Our call is set for 6/11 at 2.30pm EST", 2026)
    assert dt is not None
    assert (dt.hour, dt.minute) == (14, 30)


def test_numeric_date():
    dt = parse_datetime_from_text("confirmed for 6/15 at 9am ET", 2026)
    assert dt is not None
    assert (dt.year, dt.month, dt.day) == (2026, 6, 15)


def test_numeric_date_with_year():
    dt = parse_datetime_from_text("scheduled you for 03/02/2027 at 1pm CT", 2026)
    assert dt is not None
    assert (dt.year, dt.month, dt.day, dt.hour) == (2027, 3, 2, 13)


def test_carlie_phone_confirmation_parses():
    assert CONFIRM_PATTERNS.search(CARLIE)
    dt = parse_datetime_from_text(CARLIE, 2026)
    assert dt is not None
    # 2:30pm Eastern (EDT in June) == 18:30 UTC
    assert dt.astimezone(ZoneInfo("UTC")) == datetime(2026, 6, 11, 18, 30, tzinfo=ZoneInfo("UTC"))


def test_josh_proposal_is_not_confirmed():
    # An availability request with no firm slot must not look confirmed...
    assert not CONFIRM_PATTERNS.search(JOSH)
    # ...and "Monday next week" has no parseable concrete date.
    assert parse_datetime_from_text(JOSH, 2026) is None


def test_phone_confirm_phrases():
    for s in [
        "I'll be calling you at 10am",
        "I will call you from my cell",
        "your phone screen is scheduled",
        "I have your call added to my calendar",
    ]:
        assert CONFIRM_PATTERNS.search(s), s


def test_spelled_out_date_still_works():
    dt = parse_datetime_from_text("Interview on May 19th at 11am PST", 2026)
    assert dt is not None
    assert (dt.month, dt.day, dt.hour) == (5, 19, 11)
