"""Thread-agreement booking: a time proposed in your reply is only booked once
a later inbound message agrees, and relative dates resolve off the email date."""
from datetime import datetime
from email.message import EmailMessage
from zoneinfo import ZoneInfo

from reclaim.interviews import _find_candidate, _relative_date

ET = ZoneInfo("America/Detroit")
NOW = datetime(2026, 6, 13, 12, 0, tzinfo=ET)


def _msg(frm: str, date: str, body: str) -> EmailMessage:
    m = EmailMessage()
    m["From"] = frm
    m["Date"] = date
    m.set_content(body)
    return m


class _FakeBackend:
    def __init__(self, messages):
        self._messages = messages  # list of (msg, id, is_sent)

    def thread_messages(self, thread_meta):
        return self._messages

    def ics_text(self, msg, msg_id):
        return None


RECRUITER = "Josh McFarland <jmcfarland@velarityhcs.com>"
ME = "Aziza <muhammadaziza732@gmail.com>"
WED = "Wed, 10 Jun 2026 11:43:00 -0400"


def test_relative_weekday_resolution():
    ref = datetime(2026, 6, 10, 11, 0, tzinfo=ET)  # Wednesday
    assert _relative_date("Monday next week", ref) == (2026, 6, 15)
    assert _relative_date("next Monday", ref) == (2026, 6, 15)
    assert _relative_date("tomorrow", ref) == (2026, 6, 11)


def test_agreement_books_time_from_your_reply():
    thread = [
        (_msg(RECRUITER, "Wed, 10 Jun 2026 11:33:00 -0400",
              "Would you be available on Monday next week? 20-30 minute call."), "m1", False),
        (_msg(ME, WED, "Yes lets do Monday next week 11am est"), "m2", True),
        (_msg(RECRUITER, "Wed, 10 Jun 2026 11:44:00 -0400",
              "I can do that! I will call you from my office line then."), "m3", False),
    ]
    cand = _find_candidate(_FakeBackend(thread), {"thread_id": "x"}, NOW)
    assert cand is not None
    assert cand["start"] == datetime(2026, 6, 15, 11, 0, tzinfo=ET)
    assert "velarity" in cand["sender"].lower()  # attributed to the recruiter


def test_no_agreement_means_no_booking():
    # You proposed a time but the recruiter never agreed -> nothing booked.
    thread = [
        (_msg(RECRUITER, "Wed, 10 Jun 2026 11:33:00 -0400",
              "Send me some times that work."), "m1", False),
        (_msg(ME, WED, "Yes lets do Monday next week 11am est"), "m2", True),
    ]
    assert _find_candidate(_FakeBackend(thread), {"thread_id": "x"}, NOW) is None


def test_recruiter_stated_time_still_books():
    # Carlie-style: recruiter's own message has the time + confirmation.
    thread = [
        (_msg("Carlie <cewright@e4.health>", "Wed, 10 Jun 2026 10:30:00 -0400",
              "I have our call added to my calendar for 2.30pm EST on 6/15. "
              "I'll be calling from 315-783-3588."), "m1", False),
    ]
    cand = _find_candidate(_FakeBackend(thread), {"thread_id": "x"}, NOW)
    assert cand is not None
    assert cand["start"] == datetime(2026, 6, 15, 14, 30, tzinfo=ET)


def test_past_proposed_time_not_booked():
    thread = [
        (_msg(ME, WED, "Lets do 6/11 at 10am"), "m1", True),
        (_msg(RECRUITER, "Wed, 10 Jun 2026 11:44:00 -0400", "Sounds good!"), "m2", False),
    ]
    # 6/11 is before NOW (6/13) -> never becomes a proposed future time.
    assert _find_candidate(_FakeBackend(thread), {"thread_id": "x"}, NOW) is None
