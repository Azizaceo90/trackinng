from reclaim.interviews import (
    _candidate_link_text,
    _normalize_meeting_url,
    meeting_link_on_calendar,
)

WEBEX = "https://denverhealth.webex.com/denverhealth/j.php?MTID=m73688e6d640fe188ad86677fcdf879c5"


def test_normalize_webex():
    assert _normalize_meeting_url(WEBEX) == "webex:m73688e6d640fe188ad86677fcdf879c5"


def test_normalize_zoom_meet_teams():
    assert _normalize_meeting_url("https://us02web.zoom.us/j/87654321009") == "zoom:87654321009"
    assert _normalize_meeting_url("https://meet.google.com/abc-defg-hij") == "meet:abc-defg-hij"
    assert _normalize_meeting_url(
        "https://teams.microsoft.com/l/meetup-join/19%3aMeeting_abc/0?context=x"
    ) == "teams:19%3ameeting_abc/0"


def test_skips_when_google_already_added_same_webex():
    # The real auto-added attendee invite carries the link in its location.
    existing = [{
        "summary": "AS Level IV interview",
        "location": WEBEX,
        "description": "Interview for Level IV candidate.",
    }]
    candidate = {"via": "ics", "event": {
        "location": WEBEX,
        "description": "Join URL: not provided in email",
    }}
    assert meeting_link_on_calendar(_candidate_link_text(candidate), existing) is True


def test_matches_link_buried_in_description():
    existing = [{"summary": "Chat", "location": "", "description": f"Join here: {WEBEX}"}]
    candidate = {"via": "heuristic", "meeting": {"join_url": WEBEX}, "text": "see you then"}
    assert meeting_link_on_calendar(_candidate_link_text(candidate), existing) is True


def test_distinct_meeting_not_a_duplicate():
    existing = [{"summary": "Interview", "location": WEBEX, "description": ""}]
    other = WEBEX.replace("m73688e6d640fe188ad86677fcdf879c5", "DIFFERENThash")
    candidate = {"via": "ics", "event": {"location": other, "description": ""}}
    assert meeting_link_on_calendar(_candidate_link_text(candidate), existing) is False


def test_no_link_is_not_a_duplicate():
    existing = [{"summary": "Interview", "location": WEBEX, "description": ""}]
    candidate = {"via": "heuristic", "meeting": {}, "text": "phone call, they will call you"}
    assert meeting_link_on_calendar(_candidate_link_text(candidate), existing) is False
