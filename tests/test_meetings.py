from datetime import timedelta

from reclaim.meetings import find_mutual_slots
from tests.conftest import meeting


def test_finds_mutual_free_time(monday, prefs):
    me = [meeting(monday, 10, 11)]
    alice = [meeting(monday, 14, 15)]
    bob = [meeting(monday, 9, 10)]
    slots = find_mutual_slots(
        {"me": me, "alice": alice, "bob": bob},
        prefs,
        duration=timedelta(minutes=30),
        horizon_start=monday,
        horizon_end=monday,
        limit=3,
    )
    assert slots, "expected at least one mutual slot"
    # No slot should overlap any attendee's busy time.
    for s in slots:
        for ev in me + alice + bob:
            assert not s.overlaps(ev.slot)


def test_skips_no_meeting_days(monday, prefs):
    friday = monday.replace(day=22)  # 2026-05-22 = Friday
    slots = find_mutual_slots(
        {"me": []}, prefs, timedelta(minutes=30), friday, friday
    )
    assert slots == []
