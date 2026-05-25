from reclaim.prayer_times import _prayer_from_summary, _event_body
from datetime import datetime
from zoneinfo import ZoneInfo


def test_prayer_from_summary_exact_titles():
    assert _prayer_from_summary("🌙 Fajr Prayer") == "Fajr"
    assert _prayer_from_summary("☀️ Dhuhr Prayer") == "Dhuhr"
    assert _prayer_from_summary("🌤️ Asr Prayer") == "Asr"
    assert _prayer_from_summary("🌅 Maghrib Prayer") == "Maghrib"
    assert _prayer_from_summary("🌌 Isha Prayer") == "Isha"


def test_prayer_from_summary_tolerates_missing_emoji():
    # A title with the emoji stripped or changed still resolves by its tail.
    assert _prayer_from_summary("Fajr Prayer") == "Fajr"
    assert _prayer_from_summary("  Isha Prayer  ") == "Isha"


def test_prayer_from_summary_rejects_non_prayer():
    assert _prayer_from_summary("Lunch") is None
    assert _prayer_from_summary("Prayer request meeting") is None
    assert _prayer_from_summary("") is None


def test_event_body_has_no_reclaim_wording():
    when = datetime(2026, 5, 25, 4, 24, tzinfo=ZoneInfo("America/Detroit"))
    body = _event_body("Fajr", when, 30)
    assert "Reclaim" not in body["description"]
    assert "reclaim" not in body["description"]
    assert body["description"].rstrip().endswith("[planner-managed]")
    assert body["summary"] == "🌙 Fajr Prayer"
