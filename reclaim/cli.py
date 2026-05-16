"""CLI: reclaim plan / reclaim show / reclaim export."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from reclaim.models import Event, EventKind, TimeSlot
from reclaim.plan import Planner
from reclaim.preferences import load_habits, load_preferences, load_tasks
from reclaim.render import render_summary, render_week


CONFIG_DIR = Path("config")


def _existing_events(path: Path) -> list[Event]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text())
    out: list[Event] = []
    for e in raw:
        out.append(
            Event(
                title=e["title"],
                slot=TimeSlot(
                    datetime.fromisoformat(e["start"]),
                    datetime.fromisoformat(e["end"]),
                ),
                kind=EventKind(e.get("kind", "meeting")),
                calendar_id=e.get("calendar_id"),
                source_id=e.get("id"),
                movable=e.get("movable", False),
                notes=e.get("notes", ""),
            )
        )
    return out


def _events_to_json(events: list[Event]) -> str:
    return json.dumps(
        [
            {
                "title": e.title,
                "start": e.slot.start.isoformat(),
                "end": e.slot.end.isoformat(),
                "kind": e.kind.value,
                "movable": e.movable,
                "source_id": e.source_id,
                "notes": e.notes,
            }
            for e in events
        ],
        indent=2,
    )


def _resolve_horizon(args) -> tuple[date, date]:
    if args.start:
        start = date.fromisoformat(args.start)
    else:
        start = date.today()
        # snap to Monday for "this week"
        start -= timedelta(days=start.weekday())
    end = start + timedelta(days=args.days - 1)
    return start, end


def cmd_plan(args: argparse.Namespace) -> int:
    prefs = load_preferences(args.prefs)
    tasks = load_tasks(args.tasks) if Path(args.tasks).exists() else []
    habits = load_habits(args.habits) if Path(args.habits).exists() else []
    existing = _existing_events(Path(args.existing)) if args.existing else []

    start, end = _resolve_horizon(args)
    planner = Planner(prefs)
    result = planner.plan(existing, tasks, habits, start, end)

    all_events = existing + result.all_events
    print(f"=== Reclaim plan for {start} to {end} ===")
    print(render_week(all_events))
    print()
    print(render_summary(all_events))
    print()
    print(result.summary())

    if args.out:
        Path(args.out).write_text(_events_to_json(result.all_events))
        print(f"\nWrote proposed events to {args.out}")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    CONFIG_DIR.mkdir(exist_ok=True)
    # Copy defaults from package config if not present.
    pkg_config = Path(__file__).parent.parent / "config"
    for name in ("preferences.yaml", "tasks.yaml", "habits.yaml"):
        dest = CONFIG_DIR / name
        if dest.exists():
            print(f"skip {dest} (exists)")
            continue
        src = pkg_config / name
        if src.exists():
            dest.write_text(src.read_text())
            print(f"wrote {dest}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="reclaim", description="Personal scheduling engine")
    sub = p.add_subparsers(dest="cmd", required=True)

    plan = sub.add_parser("plan", help="Build a schedule")
    plan.add_argument("--prefs", default="config/preferences.yaml")
    plan.add_argument("--tasks", default="config/tasks.yaml")
    plan.add_argument("--habits", default="config/habits.yaml")
    plan.add_argument("--existing", default="", help="JSON file of existing calendar events")
    plan.add_argument("--start", default="", help="YYYY-MM-DD (defaults to start of this week)")
    plan.add_argument("--days", type=int, default=7)
    plan.add_argument("--out", default="", help="Write proposed events to JSON file")
    plan.set_defaults(func=cmd_plan)

    init = sub.add_parser("init", help="Bootstrap local config")
    init.set_defaults(func=cmd_init)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
