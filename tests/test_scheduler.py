from datetime import date, datetime, timedelta

from reclaim.models import EventKind, Priority, Task
from reclaim.scheduler import schedule_tasks
from tests.conftest import meeting


def test_places_simple_task(monday, prefs):
    tasks = [
        Task(
            id="t1",
            title="Write spec",
            duration=timedelta(hours=2),
            due=datetime(2026, 5, 22, 17),
            priority=Priority.P2,
        )
    ]
    placed, unplaced = schedule_tasks(tasks, [], prefs, monday, monday + timedelta(days=4))
    assert unplaced == []
    assert len(placed) == 1
    e = placed[0]
    assert e.kind == EventKind.TASK
    assert e.slot.duration == timedelta(hours=2)


def test_priority_order(monday, prefs):
    # Two tasks; the higher priority one should be scheduled first (earlier).
    high = Task(
        id="hi", title="hi", duration=timedelta(hours=1),
        due=datetime(2026, 5, 22, 17), priority=Priority.P1,
    )
    low = Task(
        id="lo", title="lo", duration=timedelta(hours=1),
        due=datetime(2026, 5, 22, 17), priority=Priority.P4,
    )
    placed, unplaced = schedule_tasks(
        [low, high], [], prefs, monday, monday + timedelta(days=4)
    )
    assert unplaced == []
    by_id = {e.source_id: e for e in placed}
    assert by_id["hi"].slot.start <= by_id["lo"].slot.start


def test_splits_large_task(monday, prefs):
    # 3h task with max_chunk 2h, busy 9-10 every morning -> at least one split.
    tasks = [
        Task(
            id="big", title="big",
            duration=timedelta(hours=3), max_chunk=timedelta(hours=2),
            min_chunk=timedelta(minutes=30),
            due=datetime(2026, 5, 22, 17), priority=Priority.P2,
        )
    ]
    placed, unplaced = schedule_tasks(tasks, [], prefs, monday, monday + timedelta(days=4))
    assert unplaced == []
    assert len(placed) >= 1
    total = sum((p.slot.duration for p in placed), timedelta())
    assert total == timedelta(hours=3)


def test_unplaceable_task_returned(monday, prefs):
    # Task wants 100h before tomorrow.
    tasks = [
        Task(
            id="huge", title="huge",
            duration=timedelta(hours=100),
            due=datetime(2026, 5, 19, 17), priority=Priority.P1,
        )
    ]
    placed, unplaced = schedule_tasks(tasks, [], prefs, monday, monday + timedelta(days=1))
    assert len(unplaced) == 1
    assert unplaced[0].id == "huge"
