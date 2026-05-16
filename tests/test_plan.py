from datetime import datetime, timedelta

from reclaim.models import EventKind, Habit, Priority, Task
from reclaim.plan import Planner
from tests.conftest import meeting


def test_end_to_end(monday, prefs):
    existing = [meeting(monday, 14, 15, title="Standup"),
                meeting(monday, 15, 16, title="Design review")]
    tasks = [
        Task(
            id="spec", title="Spec",
            duration=timedelta(hours=2),
            due=datetime(2026, 5, 22, 17),
            priority=Priority.P1,
        ),
    ]
    habits = [
        Habit(id="lunch", title="Lunch", duration=timedelta(minutes=45)),
    ]
    planner = Planner(prefs)
    result = planner.plan(existing, tasks, habits, monday, monday + timedelta(days=4))

    assert result.tasks, "expected task to be scheduled"
    assert result.habits, "expected lunch habit"
    assert result.focus, "expected focus blocks"

    # No produced event should overlap an existing meeting.
    for ev in result.all_events:
        for m in existing:
            assert not ev.slot.overlaps(m.slot), (
                f"{ev.kind.value} {ev.title} {ev.slot.start}-{ev.slot.end} "
                f"overlaps meeting {m.title}"
            )
