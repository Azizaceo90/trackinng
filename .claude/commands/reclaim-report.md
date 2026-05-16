---
description: Markdown time-report dashboard — hours by category, plan vs. actual, focus / meeting load, for day / week / month.
---

Run a time report for the user. Default window is **week**, anchored to today, but accept any of: `today`, `yesterday`, `this week`, `last week`, `this month`, `last month`, or an explicit `YYYY-MM-DD` from the arguments.

## Procedure

1. **Resolve the window.** From the user's request:
   - "today" → `--window day --date <today>`
   - "yesterday" → `--window day --date <yesterday>`
   - "this week" (default) → `--window week --date <today>`
   - "last week" → `--window week --date <last-Monday>`
   - "this month" → `--window month --date <today>`
   - "last month" → `--window month --date <last-month-anywhere>`
   - explicit date → `--window <user-choice-or-week> --date <date>`

2. **Fetch the calendar events** for that window via the Google
   Calendar MCP. Use `mcp__6aae414e-d471-4da2-ad23-b767a363f2a4__list_events`
   with the window's start/end converted to RFC3339 timestamps in
   `America/Detroit`. Request `maxResults: 500` and include all
   calendar IDs the user owns (call `list_calendars` if you're not
   sure which ones to use; default to `primary`).

3. **Dump the events to JSON** at `config/calendar_events.json`. The
   loader (`reclaim/report.py::load_events_json`) accepts the Google
   Calendar shape directly — `summary`, `start.dateTime`,
   `end.dateTime` — so you can write the events list as-is, no
   transformation needed. Use `Write` to overwrite.

4. **Run the report.** From the repo root:
   ```bash
   reclaim report --window <day|week|month> --date <YYYY-MM-DD> \
       --events config/calendar_events.json
   ```
   The command prints a markdown table + a highlights block to stdout.

5. **Show the user the rendered markdown** as your reply. Don't
   summarize it further — the markdown table is the deliverable.

6. **Optional follow-ups.** If the report looks unbalanced (e.g.
   deep-work share <20%, meeting load >50%, zero focus runs >45 min),
   call it out in one short sentence at the end and offer to re-run
   `/reclaim-sync` to add more focus blocks.

## Notes

- The events dump at `config/calendar_events.json` is intentionally
  not committed (it's regenerated every run). Add it to
  `.gitignore` if the user complains about git noise.
- Events from multiple calendars get merged into one timeline. If the
  user has multiple calendars and wants to filter, ask which ones.
- All-day events are skipped (the loader requires a real start/end
  datetime).
- The report ignores events outside the window even if they overlap
  the edges — it clips to the window boundary.
