from datetime import date, time, timedelta

from reclaim.links import generate_offers, render_offers
from tests.conftest import meeting


def test_generate_offers_finds_free_slots(prefs):
    start = date(2026, 5, 18)  # Mon
    end = date(2026, 5, 22)    # Fri (no-meeting day in fixture)
    events = [meeting(date(2026, 5, 18), 10, 11, "Standup")]

    offers = generate_offers(
        events,
        prefs,
        duration=timedelta(minutes=30),
        horizon_start=start,
        horizon_end=end,
        limit=10,
    )

    # No-meeting Friday is skipped; spread=True yields at most 4 offers.
    assert 1 <= len(offers) <= 4
    for o in offers:
        assert o.slot.duration == timedelta(minutes=30)
        # Anchored to half-hour grid
        assert o.slot.start.minute in (0, 30)


def test_generate_offers_respects_time_window(prefs):
    start = date(2026, 5, 18)
    end = date(2026, 5, 20)

    offers = generate_offers(
        [],
        prefs,
        duration=timedelta(minutes=30),
        horizon_start=start,
        horizon_end=end,
        earliest=time(14, 0),
        latest=time(16, 0),
        limit=10,
        spread=False,
    )

    assert offers
    for o in offers:
        assert o.slot.start.time() >= time(14, 0)
        assert o.slot.end.time() <= time(16, 0)


def test_generate_offers_avoids_existing_events(prefs):
    start = date(2026, 5, 18)
    end = date(2026, 5, 18)
    # Block the whole morning + afternoon except 14:00-14:30
    events = [
        meeting(start, 9, 14, "All morning"),
        meeting(start, (14, 30), 17, "All afternoon"),
    ]

    offers = generate_offers(
        events,
        prefs,
        duration=timedelta(minutes=30),
        horizon_start=start,
        horizon_end=end,
        limit=5,
        spread=False,
    )

    assert len(offers) == 1
    assert offers[0].slot.start.hour == 14
    assert offers[0].slot.start.minute == 0 or offers[0].slot.start.minute == 30


def test_render_offers_lists_numbered_lines():
    from reclaim.models import TimeSlot
    from reclaim.links import SlotOffer
    from datetime import datetime

    offers = [
        SlotOffer(
            TimeSlot(datetime(2026, 5, 19, 10, 0), datetime(2026, 5, 19, 10, 30)),
            "Tue May 19 2026, 10:00–10:30",
        )
    ]
    out = render_offers(offers, tz="America/Los_Angeles")
    assert "1. Tue May 19 2026, 10:00–10:30" in out
    assert "America/Los_Angeles" in out


def test_render_offers_empty():
    assert "No bookable" in render_offers([])
