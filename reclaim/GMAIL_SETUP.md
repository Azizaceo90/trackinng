# Gmail HTML body fetcher — one-time setup

The Gmail MCP integration available to Claude Code only returns
`plaintextBody`. Many recruiter emails (applytojob.com, Ashby,
TeamTailor, raw Microsoft Teams invites, …) are HTML-only — the Teams
join URL / Meeting ID / passcode are invisible to the MCP tool.

`reclaim/gmail_fetch.py` talks to the Gmail HTTP API directly with your
own OAuth refresh token, so it can read those HTML bodies. The
`/reclaim-interviews` slash command falls back to this script whenever
MCP returns no plaintext.

You only need to do this setup once.

## 1. Enable the Gmail API in a Google Cloud project

1. Open <https://console.cloud.google.com/projectcreate> and create a new
   project (any name, e.g. `reclaim-interview-ingest`).
2. Switch to that project, then go to **APIs & Services → Library** and
   enable **Gmail API**.

## 2. Configure the OAuth consent screen

1. **APIs & Services → OAuth consent screen**.
2. User type: **External**. Fill the required fields (app name, your
   email). You don't need to publish — keep it in "Testing".
3. On the **Test users** step, add your own Gmail address. (Without
   this, OAuth will refuse to issue a refresh token.)
4. On the **Scopes** step, add `https://www.googleapis.com/auth/gmail.readonly`.

## 3. Create OAuth client credentials

1. **APIs & Services → Credentials → Create credentials → OAuth client ID**.
2. Application type: **Desktop app**. Name it anything.
3. Click **Download JSON** on the resulting credential row.
4. Save the file to `~/.config/reclaim/gmail_credentials.json`
   (`mkdir -p ~/.config/reclaim` first).

The downloaded file looks like:
```json
{"installed":{"client_id":"...apps.googleusercontent.com","client_secret":"...",...}}
```

## 4. Authorize once

Run from the repo root:

```bash
python -m reclaim.gmail_fetch auth
```

The script prints a URL. Open it in any browser (no need for it to be
on the same machine), approve the consent screen, then **copy the
`code=...` value out of the localhost URL** Google redirects you to.
The localhost page will fail to load — that's expected; the URL bar
still contains the code.

Paste the code back into the running script. It will save a refresh
token to `~/.config/reclaim/gmail_token.json` (mode 600). That refresh
token is the long-lived secret — never commit it.

## 5. Verify

```bash
# replace with any real Gmail thread ID
python -m reclaim.gmail_fetch thread 19e2ccf861b37440
```

You should see a JSON blob on stdout with `meeting.join_url`,
`meeting.meeting_id`, and `meeting.passcode` filled in for any thread
that contains a Teams / Meet / Zoom / Webex invite.

## What's stored where

| Path                                       | Contents                | Secret? |
|--------------------------------------------|-------------------------|---------|
| `~/.config/reclaim/gmail_credentials.json` | OAuth client id+secret  | Low — app-level identifier; still don't commit. |
| `~/.config/reclaim/gmail_token.json`       | Your refresh token      | **Yes — full read access to your Gmail until revoked.** |

Override the directory by setting `RECLAIM_CONFIG_DIR=/some/path`.

Revoke any time at <https://myaccount.google.com/permissions>.

## Running this inside Claude Code's remote container

The remote execution environment is ephemeral, so the token doesn't
persist across sessions. Two options:

- **Run the auth flow on your local machine once**, then paste the
  contents of `~/.config/reclaim/gmail_token.json` into the remote
  session as a one-time setup at the start of each session.
- **Set up a session-start hook** (`.claude/settings.json`) that
  materializes the token from an env var into
  `~/.config/reclaim/gmail_token.json` before any tools run. The env
  var itself can be stored in the harness's secrets settings.
