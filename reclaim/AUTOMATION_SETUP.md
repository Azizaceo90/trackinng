# Background interview-ingest setup

This pipeline runs every 2 hours on GitHub Actions, scans your Gmail
for new interview confirmations, and creates events on your Google
Calendar — no Claude in the loop, no clicks from you.

## What it does

`.github/workflows/interview-ingest.yml` runs `python -m reclaim.interviews scan`,
which:

1. Searches Gmail (last 14 days) for interview-related threads.
2. Parses **ICS calendar attachments** first (most recruiter tools — Ashby,
   Calendly, Microsoft Outlook — attach one to confirmation emails).
3. Falls back to **body heuristics** for HTML-only emails (applytojob.com,
   manually-written recruiter messages) — extracts date/time/Teams-Meet-Zoom
   link + ID + passcode with the regex from `reclaim/gmail_fetch.py`.
4. Skips: past events, cancellations, reschedule requests with no firm time,
   rejection emails, async video-on-your-own-time interviews.
5. **Dedupes** against existing calendar events by Gmail thread ID embedded
   in the event description — so re-runs never double-book.
6. Creates events in colorId 11 (Tomato) so interviews stand out.

## One-time setup

You'll need to:

1. Add `calendar.events` scope to your existing OAuth consent screen.
2. Re-authorize so you have refresh tokens for **both** Gmail and Calendar.
3. Copy three values into the repo's Actions secrets.

### Step 1 — Add the Calendar scope

1. Open <https://console.cloud.google.com/apis/credentials/consent>
2. Click **Edit App** → **Scopes** → **Add or remove scopes**
3. Search for `calendar.events`. Check
   `https://www.googleapis.com/auth/calendar.events`. Update. Save.

### Step 2 — Authorize Calendar access locally

From the repo root, run:

```bash
python -m reclaim.calendar_fetch auth
```

It prints a consent URL. Open it, approve, copy the `code=` value from
the localhost redirect URL, and run:

```bash
python -m reclaim.calendar_fetch auth-complete <paste-code-here>
```

This stores the calendar refresh token at
`~/.config/reclaim/tokens/calendar-default.json`.

### Step 3 — Dump the three values into GitHub secrets

Open <https://github.com/azizaceo90/trackinng/settings/secrets/actions>
and create three secrets:

| Secret name              | Value (copy from disk)                                  |
|--------------------------|---------------------------------------------------------|
| `GOOGLE_OAUTH_CLIENT`    | The full contents of `~/.config/reclaim/gmail_credentials.json`            |
| `GMAIL_REFRESH_TOKEN`    | Just the `refresh_token` value inside `~/.config/reclaim/tokens/default.json` |
| `CALENDAR_REFRESH_TOKEN` | Just the `refresh_token` value inside `~/.config/reclaim/tokens/calendar-default.json` |

Quick way to print the refresh tokens:

```bash
python -c 'import json,sys; print(json.load(open(sys.argv[1]))["refresh_token"])' \
  ~/.config/reclaim/tokens/default.json

python -c 'import json,sys; print(json.load(open(sys.argv[1]))["refresh_token"])' \
  ~/.config/reclaim/tokens/calendar-default.json
```

For `GOOGLE_OAUTH_CLIENT`, paste the **entire JSON file** including the
outer `{"installed": {...}}` wrapper.

### Step 4 — Trigger a manual run to test

1. Go to <https://github.com/azizaceo90/trackinng/actions/workflows/interview-ingest.yml>
2. Click **Run workflow** → **Run workflow**.
3. Wait ~30 sec. Click into the run.
4. The **Scan Gmail** step prints a JSON report.
5. The **Artifacts** section has `ingest-report.zip` with the same JSON.

If it created new events, check your calendar. If you see
`"skipped"` with reason `"duplicate"` for ones it shouldn't have,
that's expected — the dedup uses Gmail thread IDs embedded in event
descriptions.

## Operating the pipeline

**Schedule:** every 2 hours at HH:30. Edit the cron in the workflow
to change.

**Failures:** GitHub will email you if a run fails. Most likely cause
is the refresh token expiring — Google revokes refresh tokens for
unverified apps with restricted scopes (gmail.readonly,
calendar.events) **every 7 days**.

When a run fails with a 400/401 from Google:

```bash
# Re-auth both, then refresh the GitHub secrets.
python -m reclaim.gmail_fetch auth
python -m reclaim.gmail_fetch auth-complete <code>
python -m reclaim.calendar_fetch auth
python -m reclaim.calendar_fetch auth-complete <code>

# Print the new tokens to paste into the GitHub secrets:
python -c 'import json; print(json.load(open("/Users/you/.config/reclaim/tokens/default.json"))["refresh_token"])'
python -c 'import json; print(json.load(open("/Users/you/.config/reclaim/tokens/calendar-default.json"))["refresh_token"])'
```

Update `GMAIL_REFRESH_TOKEN` and `CALENDAR_REFRESH_TOKEN` at
<https://github.com/azizaceo90/trackinng/settings/secrets/actions>.

(The only way to get rid of the 7-day expiry is to submit the app for
Google verification, which is a multi-day process for an app that only
you use. Not worth it.)

## Dry runs

Test the logic without writing to your calendar:

```bash
python -m reclaim.interviews scan --dry-run --days 14
```

Outputs the events it WOULD create as JSON. Nothing is written to
Google Calendar.
