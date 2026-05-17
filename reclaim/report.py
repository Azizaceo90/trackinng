"""Markdown time report: day / week / month breakdown from calendar events.

Reads events from a JSON dump (Google Calendar export shape, or planner
output), classifies each event by title pattern when ``kind`` is absent,
and emits a markdown summary suitable for piping to a terminal or
pasting into a doc.

CLI: ``reclaim report --window week --events <path>``
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from reclaim.models import Event, EventKind, TimeSlot


# ---------------------------------------------------------------- categories ---

# Display category → set of EventKinds it absorbs.
CATEGORIES = ["Focus", "Meetings", "Interviews", "Habits", "Tasks", "Buffers", "Other"]


def _infer_kind(title: str) -> EventKind:
    t = title.lower().strip()
    if t.startswith("[reclaim] focus") or "deep work" in t or "focus block" in t:
        return EventKind.FOCUS
    if t.startswith("interview ") or " interview" in t or t.startswith("interview—"):
        return EventKind.MEETING  # interviews counted separately by classifier
    if t.startswith("[reclaim] habit") or t.startswith("[draft] habit"):
        return EventKind.HABIT
    if t.startswith("[reclaim] task") or t.startswith("[draft] task"):
        return EventKind.TASK
    if t.startswith("buffer") or "[reclaim] buffer" in t:
        return EventKind.BUFFER
    if t.startswith("ooo") or "out of office" in t:
        return EventKind.OOO
    if t.startswith("blocked") or "lunch" in t or "sleep" in t:
        return EventKind.BLOCKED
    return EventKind.MEETING


def _classify(event: Event) -> str:
    title = event.title.lower()
    if "interview" in title:
        return "Interviews"
    if event.kind == EventKind.FOCUS:
        return "Focus"
    if event.kind == EventKind.HABIT:
        return "Habits"
    if event.kind == EventKind.TASK:
        return "Tasks"
    if event.kind == EventKind.BUFFER:
        return "Buffers"
    if event.kind in (EventKind.BLOCKED, EventKind.OOO):
        return "Other"
    return "Meetings"


# -------------------------------------------------------------------- loader ---


def load_events_json(path: Path) -> list[Event]:
    import json
    raw = json.loads(path.read_text())
    out: list[Event] = []
    for e in raw:
        title = e.get("title") or e.get("summary") or ""
        start = _parse_dt(e.get("start"))
        end = _parse_dt(e.get("end"))
        if not (title and start and end and end > start):
            continue
        kind_raw = e.get("kind")
        try:
            kind = EventKind(kind_raw) if kind_raw else _infer_kind(title)
        except ValueError:
            kind = _infer_kind(title)
        out.append(Event(title=title, slot=TimeSlot(start, end), kind=kind))
    return out


def _parse_dt(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, dict):  # google calendar shape: {"dateTime": "...", "timeZone": "..."}
        value = value.get("dateTime") or value.get("date")
        if value is None:
            return None
    s = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    # Strip tzinfo for arithmetic — we want wall-clock hours.
    return dt.replace(tzinfo=None)


# ------------------------------------------------------------- window helpers ---


def resolve_window(window: str, anchor: date) -> tuple[date, date]:
    """Return inclusive [start, end] dates for the given window centred on anchor."""
    window = window.lower()
    if window == "day":
        return anchor, anchor
    if window == "week":
        start = anchor - timedelta(days=anchor.weekday())  # Monday
        return start, start + timedelta(days=6)
    if window == "month":
        start = anchor.replace(day=1)
        # Last day of this month
        if start.month == 12:
            next_month = start.replace(year=start.year + 1, month=1)
        else:
            next_month = start.replace(month=start.month + 1)
        end = next_month - timedelta(days=1)
        return start, end
    raise ValueError(f"unknown window: {window!r} (use day/week/month)")


# -------------------------------------------------------------- aggregation ---


@dataclass
class DayTotals:
    day: date
    hours: dict[str, float] = field(default_factory=lambda: {c: 0.0 for c in CATEGORIES})
    interview_count: int = 0
    longest_focus: timedelta = timedelta()

    @property
    def total_hours(self) -> float:
        return sum(self.hours.values())


@dataclass
class Report:
    start: date
    end: date
    days: list[DayTotals]
    window_label: str

    @property
    def total_hours(self) -> dict[str, float]:
        agg: dict[str, float] = {c: 0.0 for c in CATEGORIES}
        for d in self.days:
            for k, v in d.hours.items():
                agg[k] += v
        return agg

    @property
    def total_interviews(self) -> int:
        return sum(d.interview_count for d in self.days)


def _hours(td: timedelta) -> float:
    return td.total_seconds() / 3600.0


def build_report(events: list[Event], start: date, end: date, window_label: str) -> Report:
    days = []
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        day_start = datetime.combine(day, datetime.min.time())
        day_end = day_start + timedelta(days=1)
        totals = DayTotals(day=day)
        longest = timedelta()
        for ev in events:
            clipped = ev.slot.clip(TimeSlot(day_start, day_end))
            if clipped is None:
                continue
            dur = clipped.duration
            cat = _classify(ev)
            totals.hours[cat] += _hours(dur)
            if cat == "Interviews":
                # one interview per event per day, not per minute
                if datetime.combine(day, datetime.min.time()) <= ev.start < day_end:
                    totals.interview_count += 1
            if ev.kind == EventKind.FOCUS and dur > longest:
                longest = dur
        totals.longest_focus = longest
        days.append(totals)
    return Report(start=start, end=end, days=days, window_label=window_label)


# ------------------------------------------------------------------ render ---


def _fmt_h(hours: float) -> str:
    if hours == 0:
        return "—"
    return f"{hours:.1f}h"


def _fmt_td(td: timedelta) -> str:
    total = int(td.total_seconds())
    if total <= 0:
        return "—"
    h, rem = divmod(total, 3600)
    m = rem // 60
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{m}m"


def _load_goals() -> list[dict]:
    """Read config/goals.yaml. Returns [] if missing."""
    from pathlib import Path
    try:
        import yaml
    except ImportError:
        return []
    p = Path("config/goals.yaml")
    if not p.exists():
        return []
    try:
        data = yaml.safe_load(p.read_text()) or {}
    except Exception:
        return []
    return data.get("goals", []) or []


def _render_goal_block(goals: list[dict]) -> str:
    """Render the goal-tracking section for the HTML dashboard."""
    from datetime import date as _date
    if not goals:
        return ""
    today = _date.today()
    rows = []
    for g in goals:
        title = g.get("title", "Goal")
        target = int(g.get("target", 1))
        current = int(g.get("current", 0))
        metric = g.get("metric", "items")
        try:
            start = _date.fromisoformat(g["start"])
            deadline = _date.fromisoformat(g["deadline"])
        except (KeyError, ValueError):
            continue
        total_days = (deadline - start).days or 1
        elapsed_days = max(0, (today - start).days)
        days_left = max(0, (deadline - today).days)
        pct_elapsed = min(100, int(elapsed_days / total_days * 100))
        pct_done = min(100, int(current / target * 100)) if target else 0
        expected = round(elapsed_days / total_days * target, 1)
        status = "🟢 ahead" if current >= expected else (
                 "🟡 on pace" if current >= expected - 1 else "🔴 behind")
        rows.append(f"""
  <div class="goal">
    <div class="goal-title">{title}</div>
    <div class="goal-numbers">
      <span class="goal-current">{current}</span>
      <span class="goal-sep">/</span>
      <span class="goal-target">{target}</span>
      <span class="goal-metric">{metric}</span>
      <span class="goal-status">{status}</span>
    </div>
    <div class="goal-bar"><div class="goal-bar-fill" style="width: {pct_done}%"></div></div>
    <div class="goal-meta">
      {days_left} days left · expected pace: {expected} · started {start:%b %d} · due {deadline:%b %d}
    </div>
  </div>""")
    return f"""
  <section class="goals">
    <h2>Career goals</h2>
    {''.join(rows)}
  </section>"""


def render_html(report: Report) -> str:
    """Self-contained HTML dashboard. Chart.js is pulled from a CDN.

    Designed to be committed to docs/index.html and served by GitHub
    Pages. The whole page is one file — no build step, no dependencies.
    """
    import json
    cats = CATEGORIES
    totals = report.total_hours
    grand = sum(totals.values())
    focus = totals["Focus"]
    meet = totals["Meetings"] + totals["Interviews"]
    deep_share = (focus / grand * 100) if grand else 0
    meet_share = (meet / grand * 100) if grand else 0
    busiest = max(report.days, key=lambda d: d.total_hours, default=None)
    deepest = max(report.days, key=lambda d: d.longest_focus, default=None)

    chart_labels = [f"{d.day:%a %m/%d}" for d in report.days]
    chart_datasets = []
    palette = {
        "Focus":      "#22c55e",
        "Meetings":   "#3b82f6",
        "Interviews": "#ef4444",
        "Habits":     "#a855f7",
        "Tasks":      "#f59e0b",
        "Buffers":    "#6b7280",
        "Other":      "#9ca3af",
    }
    for c in cats:
        chart_datasets.append({
            "label": c,
            "backgroundColor": palette[c],
            "data": [round(d.hours[c], 2) for d in report.days],
        })

    rows = []
    for d in report.days:
        cells = [f"<td>{d.day:%a %m/%d}</td>"]
        for c in cats:
            cells.append(f"<td>{_fmt_h(d.hours[c])}</td>")
        cells.append(f"<td><strong>{_fmt_h(d.total_hours)}</strong></td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    total_cells = ["<td><strong>Total</strong></td>"]
    for c in cats:
        total_cells.append(f"<td><strong>{_fmt_h(totals[c])}</strong></td>")
    total_cells.append(f"<td><strong>{_fmt_h(grand)}</strong></td>")
    rows.append('<tr class="total">' + "".join(total_cells) + "</tr>")

    header_cells = "".join(f"<th>{c}</th>" for c in cats)
    rendered_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Time report — {report.start} → {report.end}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  body {{ font: 14px -apple-system, system-ui, sans-serif;
         max-width: 1000px; margin: 2rem auto; padding: 0 1rem;
         color: #111; background: #fafafa; }}
  h1 {{ font-size: 1.4rem; margin: 0 0 .25rem; }}
  .sub {{ color: #666; margin-bottom: 1.5rem; }}
  .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px,1fr));
            gap: .75rem; margin-bottom: 1.5rem; }}
  .stat {{ background: white; padding: .75rem 1rem; border-radius: 8px;
           border: 1px solid #e5e5e5; }}
  .stat .v {{ font-size: 1.5rem; font-weight: 600; }}
  .stat .l {{ font-size: .75rem; color: #666; text-transform: uppercase;
              letter-spacing: .05em; }}
  canvas {{ background: white; border: 1px solid #e5e5e5; border-radius: 8px;
            padding: 1rem; margin-bottom: 1.5rem; }}
  table {{ width: 100%; border-collapse: collapse; background: white;
           border: 1px solid #e5e5e5; border-radius: 8px; overflow: hidden; }}
  th, td {{ padding: .5rem .75rem; text-align: right; border-bottom: 1px solid #f0f0f0;
            font-variant-numeric: tabular-nums; }}
  th:first-child, td:first-child {{ text-align: left; }}
  th {{ background: #f5f5f5; font-weight: 600; font-size: .8rem;
        text-transform: uppercase; letter-spacing: .03em; color: #555; }}
  tr.total td {{ background: #f9fafb; border-top: 2px solid #e5e5e5; }}
  footer {{ color: #999; font-size: .75rem; margin-top: 2rem; text-align: center; }}
  .goals {{ background: linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%);
            color: white; padding: 1.25rem 1.5rem; border-radius: 12px;
            margin-bottom: 1.5rem; }}
  .goals h2 {{ font-size: .8rem; margin: 0 0 .75rem; text-transform: uppercase;
              letter-spacing: .08em; opacity: .85; }}
  .goal {{ margin-bottom: 1rem; }}
  .goal:last-child {{ margin-bottom: 0; }}
  .goal-title {{ font-size: 1.05rem; font-weight: 600; margin-bottom: .35rem; }}
  .goal-numbers {{ display: flex; align-items: baseline; gap: .35rem; margin-bottom: .5rem; }}
  .goal-current {{ font-size: 2.5rem; font-weight: 700; line-height: 1; }}
  .goal-sep    {{ font-size: 1.5rem; opacity: .6; }}
  .goal-target {{ font-size: 1.5rem; font-weight: 600; opacity: .9; }}
  .goal-metric {{ font-size: .85rem; opacity: .75; margin-left: .25rem; }}
  .goal-status {{ margin-left: auto; font-size: .85rem; }}
  .goal-bar {{ height: 8px; background: rgba(255,255,255,.2);
              border-radius: 4px; overflow: hidden; }}
  .goal-bar-fill {{ height: 100%; background: white; border-radius: 4px;
                   transition: width .3s ease; }}
  .goal-meta {{ font-size: .75rem; opacity: .8; margin-top: .35rem; }}
</style>
</head>
<body>
  <h1>Time report — {report.window_label}</h1>
  <div class="sub">{report.start:%a %b %-d} → {report.end:%a %b %-d, %Y} · <a href="https://calendar.app.google/fLn3bFqbPwZHFf4r9">Book time with me →</a></div>

  <div class="stats">
    <div class="stat"><div class="v">{_fmt_h(grand)}</div><div class="l">Tracked</div></div>
    <div class="stat"><div class="v">{report.total_interviews}</div><div class="l">Interviews</div></div>
    <div class="stat"><div class="v">{_fmt_h(focus)}</div><div class="l">Focus</div></div>
    <div class="stat"><div class="v">{deep_share:.0f}%</div><div class="l">Deep-work share</div></div>
    <div class="stat"><div class="v">{meet_share:.0f}%</div><div class="l">Meeting load</div></div>
    <div class="stat"><div class="v">{busiest.day:%a %m/%d}</div><div class="l">Busiest day</div></div>
  </div>

  {_render_goal_block(_load_goals())}

  <canvas id="chart" height="120"></canvas>

  <table>
    <thead><tr><th>Day</th>{header_cells}<th>Total</th></tr></thead>
    <tbody>{"".join(rows)}</tbody>
  </table>

  <footer>Generated {rendered_at} · <code>reclaim report --format html</code></footer>

<script>
const ctx = document.getElementById('chart').getContext('2d');
new Chart(ctx, {{
  type: 'bar',
  data: {{
    labels: {json.dumps(chart_labels)},
    datasets: {json.dumps(chart_datasets)}
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ position: 'bottom' }}, title: {{ display: false }} }},
    scales: {{
      x: {{ stacked: true }},
      y: {{ stacked: true, title: {{ display: true, text: 'Hours' }} }}
    }}
  }}
}});
</script>
</body>
</html>
"""


def render_markdown(report: Report) -> str:
    title = (
        f"# Time report — {report.window_label} "
        f"({report.start:%a %b %-d} → {report.end:%a %b %-d, %Y})"
    )
    cats = CATEGORIES
    rows = []
    header = "| Day | " + " | ".join(cats) + " | Total |"
    sep = "|---|" + "|".join("---" for _ in cats) + "|---|"
    rows.extend([header, sep])
    for d in report.days:
        cells = [f"{d.day:%a %m/%d}"]
        for c in cats:
            cells.append(_fmt_h(d.hours[c]))
        cells.append(_fmt_h(d.total_hours))
        rows.append("| " + " | ".join(cells) + " |")
    totals = report.total_hours
    total_cells = ["**Total**"]
    for c in cats:
        total_cells.append(f"**{_fmt_h(totals[c])}**")
    total_cells.append(f"**{_fmt_h(sum(totals.values()))}**")
    rows.append("| " + " | ".join(total_cells) + " |")

    busiest = max(report.days, key=lambda d: d.total_hours, default=None)
    deepest_focus = max(report.days, key=lambda d: d.longest_focus, default=None)
    most_meetings = max(
        report.days, key=lambda d: d.hours["Meetings"] + d.hours["Interviews"], default=None
    )

    highlights = ["", "## Highlights"]
    highlights.append(f"- **Interviews this {report.window_label}:** {report.total_interviews}")
    if deepest_focus and deepest_focus.longest_focus > timedelta():
        highlights.append(
            f"- **Longest focus run:** {_fmt_td(deepest_focus.longest_focus)} "
            f"on {deepest_focus.day:%a %m/%d}"
        )
    if busiest and busiest.total_hours > 0:
        highlights.append(
            f"- **Busiest day:** {busiest.day:%a %m/%d} "
            f"({_fmt_h(busiest.total_hours)})"
        )
    if most_meetings:
        m_hours = most_meetings.hours["Meetings"] + most_meetings.hours["Interviews"]
        if m_hours > 0:
            highlights.append(
                f"- **Heaviest meeting day:** {most_meetings.day:%a %m/%d} "
                f"({_fmt_h(m_hours)})"
            )
    focus_total = totals["Focus"]
    meet_total = totals["Meetings"] + totals["Interviews"]
    tracked = sum(totals.values())
    if tracked > 0:
        highlights.append(
            f"- **Deep-work share:** {focus_total / tracked * 100:.0f}% "
            f"(focus / tracked time)"
        )
        highlights.append(
            f"- **Meeting load:** {meet_total / tracked * 100:.0f}% "
            f"(meetings + interviews / tracked time)"
        )

    return "\n".join([title, ""] + rows + highlights) + "\n"
