"""Duration parsing, boilerplate-safe triage, and cancel/reschedule cleanup."""
from reclaim.interviews import (
    INGEST_MARKER,
    _remove_thread_events,
    _thread_event_ids,
    is_skip,
    parse_duration_minutes,
)


# ---- duration ---------------------------------------------------------------

def test_duration_variants():
    assert parse_duration_minutes("20-30 minute call") == 30  # longer end
    assert parse_duration_minutes("a 30 minute screen") == 30
    assert parse_duration_minutes("45-minute interview") == 45
    assert parse_duration_minutes("1 hour conversation") == 60
    assert parse_duration_minutes("half hour chat") == 30


def test_duration_none_when_unstated():
    assert parse_duration_minutes("I'll call you from 317-680-9422") is None
    assert parse_duration_minutes("available 11am-5pm EST") is None
    assert parse_duration_minutes("") is None


# ---- triage: boilerplate must not read as cancel/reschedule -----------------

def test_signoff_boilerplate_is_not_a_skip():
    assert is_skip("Phone Screen",
                   "Please let me know if you need to reschedule or cancel!",
                   "rec@corp.com") is None


def test_real_cancellation_and_reschedule():
    assert is_skip("Interview cancelled", "We have cancelled your interview", "x@y.com") == "cancelled"
    assert is_skip("Update", "We need to reschedule your interview to June 20", "x@y.com") == "reschedule"


def test_rejection_still_skipped():
    assert is_skip("Interview Update", "Unfortunately we will not move forward",
                   "x@y.com") == "rejection_or_status"


# ---- cleanup helpers --------------------------------------------------------

def _our_event(eid, thread_id):
    return {"id": eid, "summary": "Interview — Acme",
            "description": f"{INGEST_MARKER} on 2026-06-13.\nGmail thread: {thread_id}\n"}


def test_thread_event_ids_matches_only_our_events():
    existing = [
        _our_event("a1", "T1"),
        {"id": "b2", "summary": "Interview", "description": "some other tool, thread T1"},
        _our_event("c3", "T2"),
    ]
    assert _thread_event_ids("T1", existing) == ["a1"]
    assert _thread_event_ids("T2", existing) == ["c3"]
    assert _thread_event_ids("T9", existing) == []


def test_remove_thread_events_dry_run_drops_from_memory_without_deleting():
    existing = [_our_event("a1", "T1"), _our_event("a2", "T1"), _our_event("z9", "T2")]
    removed = _remove_thread_events(None, "primary", "T1", existing, dry_run=True)
    assert set(removed) == {"a1", "a2"}
    # in-memory list no longer contains the removed events
    assert [e["id"] for e in existing] == ["z9"]
