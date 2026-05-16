---
description: Scan Gmail for new interview confirmations / requests and add them to Google Calendar (asks before writing).
---

You are running the Reclaim interview-ingest flow. Your job: find interview-related emails the user has received, decide which represent **upcoming, confirmed time slots**, and (after confirmation) create matching Google Calendar events tagged so they're easy to identify later.

## Tools you'll use

- `mcp__f1d04bb9-05e3-4e76-a0a1-097c2708c18b__search_threads` — find candidate emails
- `mcp__f1d04bb9-05e3-4e76-a0a1-097c2708c18b__get_thread` (FULL_CONTENT) — read each candidate
- `mcp__6aae414e-d471-4da2-ad23-b767a363f2a4__list_events` — check for existing duplicates
- `mcp__6aae414e-d471-4da2-ad23-b767a363f2a4__create_event` — write confirmed events
- `AskUserQuestion` — get permission BEFORE creating anything

## Procedure

0. **Enumerate Gmail accounts.** Run
   `python -m reclaim.gmail_fetch accounts` to see which accounts are
   configured. Loop through each one when searching + fetching. The
   default account is named `default`; additional ones can be added
   with `python -m reclaim.gmail_fetch auth --account <name>`. When a
   thread is HTML-only, pass the same `--account` flag to the helper:
   `python -m reclaim.gmail_fetch thread <id> --account <name>`.

   Note: the Gmail MCP tool itself only reads the user's default Gmail
   account (the one they authorized in the harness). To scan a
   secondary account, use the Python helper for searches too:
   `python -m reclaim.gmail_fetch search "<query>" --account <name>` if
   the script supports it; otherwise tell the user that secondary
   accounts are currently scanned via the helper for thread fetches
   only, and the MCP search runs against their primary account.

1. **Search Gmail.** Use this query (adjust newer_than as needed):
   ```
   (subject:interview OR "interview request" OR "schedule an interview"
    OR "phone screen" OR "technical screen" OR "interview confirmation"
    OR "you are confirmed" OR "your interview is" OR "calendar invite")
   newer_than:30d -in:trash -in:spam
   ```
   pageSize: 25.

2. **Triage threads.** For each thread, classify by reading the snippets + subject + (if needed) the full body via `get_thread`:
   - **CONFIRMED upcoming**: Concrete date AND time AND it's in the future, no later cancellation/reschedule message in the same thread. Keep it.
   - **PAST**: The scheduled date has already passed. Skip.
   - **CANCELLED / RESCHEDULED**: Subject contains "Canceled", "Cancelled", "Reschedule", or the latest message in the thread cancels. Skip — and if a new time was proposed in the same thread, treat that as the candidate instead.
   - **REJECTION / FOLLOWUP**: "Interview Update", "unfortunately", "next steps", "thank you for interviewing". Skip.
   - **NO TIME YET**: Recruiter asking for availability with no firm slot. Skip — but list these as "pending" in the report so the user knows.
   - **ASYNC / VIDEO INTERVIEW**: HireFlix, Spark Hire, etc. (do-it-on-your-own-time). Skip — list under "async".

3. **De-duplicate.** Check the calendar with `list_events` over the next 60 days; if an event with the same company or interviewer in the title within ±30 min of the candidate already exists, skip it (don't double-book).

4. **Extract details** for each kept candidate. Use `get_thread` with
   `messageFormat: FULL_CONTENT` and read `plaintextBody`. Look for:
   - Company name (from sender domain or thread subject)
   - Interviewer (if named in the body)
   - Start time in the user's timezone (`America/Detroit` per `config/preferences.yaml`)
   - Duration — default **45 minutes** if unstated; use the stated duration otherwise
   - **Meeting join URL**:
     - MS Teams: `https://teams.microsoft.com/meet/...` or `https://teams.microsoft.com/l/meetup-join/...`
     - Google Meet: `https://meet.google.com/<xxx-xxxx-xxx>`
     - Zoom: `https://*.zoom.us/j/<id>?pwd=<pw>` or `https://*.zoom.us/my/<handle>`
     - Webex / GoToMeeting / generic — capture whatever join URL is in the body
   - **Meeting ID**: look for "Meeting ID:", "Conference ID:", "Phone conference ID:"
   - **Passcode**: look for "Passcode:", "Password:", "Passphrase:", "PIN:", or `?pwd=` in the URL
   - **Dial-in**: phone number + access code if a phone-only option is included
   - The Gmail thread ID (for traceability)

   If the email is HTML-only and `get_thread` returns no `plaintextBody`,
   **fall back to the local Gmail-API helper** before giving up:

   ```bash
   python -m reclaim.gmail_fetch thread <threadId>
   ```

   The helper hits Gmail's HTTP API directly with the user's own OAuth
   refresh token, decodes the HTML body, and emits a JSON `meeting`
   block with `join_url`, `meeting_id`, `passcode`, `conference_id`, and
   `dial_in`. Use those values as if they came from `plaintextBody`.

   If the helper itself errors out with "Missing OAuth client config" or
   "No refresh token", point the user at `reclaim/GMAIL_SETUP.md` —
   that's a one-time setup, not something to retry around.

5. **Show the user a clean summary** of what you'd create:
   ```
   Proposed calendar events:
     1. Interview — Valenz (Katelyn Crum)   Tue May 19, 11:00–11:45 EDT
     2. ...
   Skipped:
     - past:    LifeMD 5/14, Strala 5/14, ...
     - cancelled: Energage phone screen
     - pending (no time yet): Aspirion reschedule
   ```

6. **Ask via `AskUserQuestion`** whether to create all, a subset, or none. Never call `create_event` without explicit confirmation.

7. **Write approved events** with `create_event`:
   - `summary`: `Interview — <Company> (<Interviewer>)` (no `[Reclaim]` / `[DRAFT]` prefix — these are real interviews)
   - `colorId`: `11` (Tomato) so interviews stand out
   - `location`: the join URL if available, otherwise the dial-in phone number.
   - `description`: a structured block. Template:
     ```
     Auto-imported by Reclaim interview-ingest on <date> from Gmail thread <threadId>.
     Source: <sender>.

     Format: <Teams / Google Meet / Zoom / Phone / Onsite>
     Interviewer: <name(s) and role if known>

     Join URL: <full URL or "not provided in email">
     Meeting ID: <id or "n/a">
     Passcode:   <passcode or "n/a">
     Dial-in:    <phone, access code or "n/a">
     ```
     If a field isn't in the email, write "not provided in email" — never make
     one up.
   - `timeZone`: `America/Detroit`

8. **Report** the list of created event IDs + a per-event one-line summary, and call out conflicts with existing `[DRAFT]` Reclaim events so the user can re-plan focus blocks if needed.

## Rules

- Real interviews are NOT prefixed `[DRAFT]` or `[Reclaim]`. They're real commitments.
- Don't touch existing events. If an interview conflicts with a `[DRAFT]` Reclaim block, just call it out — let the user decide whether to re-run `/reclaim-sync` to re-plan around it.
- Never email a recruiter on the user's behalf, never label/archive Gmail threads — read-only on Gmail.
- All times in `America/Detroit` unless the email specifies otherwise.
- If you find zero new interviews to create, say so plainly: "Inbox scanned, no new interviews to add."
