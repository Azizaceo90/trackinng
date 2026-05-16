from datetime import timedelta

from reclaim.buffers import add_buffers
from reclaim.models import EventKind
from tests.conftest import at, meeting


def test_buffers_long_meeting(monday, prefs):
    events = [meeting(monday, 14, 15)]  # 1h meeting, exceeds 45m threshold
    buffers = add_buffers(events, prefs)
    assert len(buffers) == 2
    kinds = {b.kind for b in buffers}
    assert kinds == {EventKind.BUFFER}


def test_skips_short_meeting(monday, prefs):
    # 30m meeting is under the 45m threshold by default.
    from reclaim.models import Event, TimeSlot
    short = Event(
        title="standup",
        slot=TimeSlot(at(monday, 10), at(monday, 10, 30)),
        kind=EventKind.MEETING,
    )
    buffers = add_buffers([short], prefs)
    assert buffers == []


def test_no_buffer_when_conflict(monday, prefs):
    # Back-to-back long meetings: post-buffer of first conflicts with second.
    events = [meeting(monday, 10, 11), meeting(monday, 11, 12)]
    buffers = add_buffers(events, prefs)
    # The post-buffer for 10-11 (11:00-11:10) conflicts with 11-12 -> not added.
    # The pre-buffer for 11-12 (10:55-11:00) conflicts with 10-11 -> not added.
    titles = [b.title for b in buffers]
    assert "Prep buffer" in titles  # for the first meeting
    assert "Decompress buffer" in titles  # for the second meeting
    # But not the conflicting ones.
    assert len(buffers) == 2
