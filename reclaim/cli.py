"""CLI: reclaim plan / reclaim show / reclaim export."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from datetime import time as _time

from reclaim.analytics import analyze
from reclaim.links import generate_offers, render_offers
from reclaim.models import Event, EventKind, TimeSlot
from reclaim.plan import Planner
from reclaim.preferences import load_habits, load_preferences, load_tasks
from reclaim.render import render_summary, render_week
from reclaim.report import (
    build_report,
    load_events_json,
    render_html,
    render_markdown,
    resolve_window,
)


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


def cmd_links(args: argparse.Namespace) -> int:
    prefs = load_preferences(args.prefs)
    existing = _existing_events(Path(args.existing)) if args.existing else []
    start, end = _resolve_horizon(args)
    duration = timedelta(minutes=args.duration)
    earliest = _time.fromisoformat(args.earliest) if args.earliest else None
    latest = _time.fromisoformat(args.latest) if args.latest else None
    weekdays = (
        [int(x) for x in args.weekdays.split(",")] if args.weekdays else None
    )
    offers = generate_offers(
        existing,
        prefs,
        duration,
        start,
        end,
        limit=args.limit,
        step=timedelta(minutes=args.step),
        earliest=earliest,
        latest=latest,
        weekdays=weekdays,
        spread=not args.no_spread,
    )
    print(render_offers(offers, tz=prefs.timezone))
    if args.out:
        Path(args.out).write_text(
            json.dumps(
                [
                    {
                        "start": o.slot.start.isoformat(),
                        "end": o.slot.end.isoformat(),
                        "label": o.label,
                    }
                    for o in offers
                ],
                indent=2,
            )
        )
        print(f"\nWrote offers to {args.out}")
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    prefs = load_preferences(args.prefs)
    existing = _existing_events(Path(args.existing)) if args.existing else []
    start, end = _resolve_horizon(args)
    if args.include_plan:
        tasks = load_tasks(args.tasks) if Path(args.tasks).exists() else []
        habits = load_habits(args.habits) if Path(args.habits).exists() else []
        planner = Planner(prefs)
        result = planner.plan(existing, tasks, habits, start, end)
        events = existing + result.all_events
    else:
        events = existing
    report = analyze(events, prefs, start, end)
    print(report.render())
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    anchor = date.fromisoformat(args.date) if args.date else date.today()
    start, end = resolve_window(args.window, anchor)
    events = load_events_json(Path(args.events))
    report = build_report(events, start, end, args.window)
    rendered = render_html(report) if args.format == "html" else render_markdown(report)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered)
        print(f"Wrote {out_path}")
    else:
        print(rendered)
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

    links = sub.add_parser("links", help="Generate bookable scheduling-link slots")
    links.add_argument("--prefs", default="config/preferences.yaml")
    links.add_argument("--existing", default="", help="JSON file of existing calendar events")
    links.add_argument("--start", default="", help="YYYY-MM-DD")
    links.add_argument("--days", type=int, default=7)
    links.add_argument("--duration", type=int, default=30, help="Slot duration in minutes")
    links.add_argument("--step", type=int, default=30, help="Anchor step in minutes")
    links.add_argument("--limit", type=int, default=8)
    links.add_argument("--earliest", default="", help="Earliest time-of-day, e.g. 10:00")
    links.add_argument("--latest", default="", help="Latest time-of-day, e.g. 16:00")
    links.add_argument("--weekdays", default="", help="Comma list of weekday indices, Mon=0")
    links.add_argument("--no-spread", action="store_true", help="Allow multiple offers per day")
    links.add_argument("--out", default="", help="Write offers to JSON file")
    links.set_defaults(func=cmd_links)

    analyze_p = sub.add_parser("analyze", help="Weekly productivity report")
    analyze_p.add_argument("--prefs", default="config/preferences.yaml")
    analyze_p.add_argument("--tasks", default="config/tasks.yaml")
    analyze_p.add_argument("--habits", default="config/habits.yaml")
    analyze_p.add_argument("--existing", default="", help="JSON file of existing calendar events")
    analyze_p.add_argument("--start", default="")
    analyze_p.add_argument("--days", type=int, default=7)
    analyze_p.add_argument("--include-plan", action="store_true", help="Also count planner output")
    analyze_p.set_defaults(func=cmd_analyze)

    report_p = sub.add_parser("report", help="Markdown time-report dashboard")
    report_p.add_argument(
        "--window", default="week", choices=["day", "week", "month"],
        help="Time window (default: week).",
    )
    report_p.add_argument(
        "--date", default="",
        help="Anchor date YYYY-MM-DD (defaults to today).",
    )
    report_p.add_argument(
        "--events", default="config/calendar_events.json",
        help="JSON file of calendar events (default: config/calendar_events.json).",
    )
    report_p.add_argument(
        "--format", default="markdown", choices=["markdown", "html"],
        help="Output format (default: markdown).",
    )
    report_p.add_argument(
        "--out", default="",
        help="Write to a file instead of stdout (path, e.g. docs/index.html).",
    )
    report_p.set_defaults(func=cmd_report)

    init = sub.add_parser("init", help="Bootstrap local config")
    init.set_defaults(func=cmd_init)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
