"""Tests for the meeting-detail extractor in reclaim.gmail_fetch."""
from __future__ import annotations

from reclaim.gmail_fetch import extract_meeting, html_to_text


def test_extract_teams_full_block():
    text = """
    Microsoft Teams meeting
    Join: https://teams.microsoft.com/meet/251284111316760?p=TBYCuCNDpWsIkXW97X
    Meeting ID: 251 284 111 316 760
    Passcode: mg6yT7fa
    Phone conference ID: 799 266 912#
    Dial in: +1 312-319-1727
    """
    out = extract_meeting(text)
    assert out["platform"] == "teams"
    assert out["join_url"] == (
        "https://teams.microsoft.com/meet/251284111316760?p=TBYCuCNDpWsIkXW97X"
    )
    assert out["meeting_id"] == "251 284 111 316 760"
    assert out["passcode"] == "mg6yT7fa"
    assert out["conference_id"] == "799 266 912#"
    assert out["dial_in"].startswith("+1 312-319-1727")


def test_extract_zoom_with_pwd():
    text = "Join Zoom: https://us02web.zoom.us/j/89123456789?pwd=AbCdEf123_-"
    out = extract_meeting(text)
    assert out["platform"] == "zoom"
    assert out["join_url"].startswith("https://us02web.zoom.us/j/89123456789?pwd=")


def test_extract_google_meet():
    text = "Join with Google Meet: https://meet.google.com/abc-defg-hij"
    out = extract_meeting(text)
    assert out["platform"] == "google_meet"
    assert out["join_url"] == "https://meet.google.com/abc-defg-hij"


def test_extract_nothing():
    assert extract_meeting("hi this email has no meeting info") == {}


def test_html_to_text_extracts_anchor_urls():
    html = """
      <p>Click <a href="https://teams.microsoft.com/meet/123?p=xyz">join</a></p>
      <p>Passcode: abc123</p>
    """
    text = html_to_text(html)
    out = extract_meeting(text)
    assert out["platform"] == "teams"
    assert out["join_url"] == "https://teams.microsoft.com/meet/123?p=xyz"
    assert out["passcode"] == "abc123"


def test_html_to_text_drops_script_style():
    html = "<style>p{color:red}</style><script>x=1</script><p>Hello</p>"
    text = html_to_text(html)
    assert "color" not in text
    assert "x=1" not in text
    assert "Hello" in text
