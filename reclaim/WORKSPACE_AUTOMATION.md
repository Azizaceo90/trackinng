# No-expiry automation — Workspace setup

Replaces the 7-day OAuth rotation with permanent credentials. After
this, the GitHub Action runs forever without re-auth.

**Architecture**

- **Gmail read** → IMAP with a personal Gmail [App Password](https://myaccount.google.com/apppasswords).
  App passwords don't expire until you revoke them.
- **Calendar write** → Service account in your Google Workspace, with
  domain-wide delegation, impersonating your Workspace user. Your
  Workspace user has edit access to your personal calendar via a
  one-time "share" link. Service accounts use private-key signing, no
  refresh tokens, no expiry.

**Prerequisites:** You're the admin of the Workspace.

---

## Step 1 — Generate a Gmail App Password (2 min)

Personal Gmail (`muhammadaziza732@gmail.com`):

1. Make sure 2-Step Verification is on at
   <https://myaccount.google.com/security>. (App passwords are gated
   behind 2FA.)
2. Open <https://myaccount.google.com/apppasswords>
3. **App name:** `reclaim-interview-ingest`
4. Click **Create**. Google shows a 16-character password in a yellow
   box (e.g. `abcd efgh ijkl mnop`). Copy it now — you can't see it
   again.
5. Keep this open in a tab; you'll paste it as a GitHub secret in
   step 5.

---

## Step 2 — Create the service account (3 min)

Reuses your existing GCP project (`calendar-intergration-496519`).

1. Open <https://console.cloud.google.com/iam-admin/serviceaccounts>
2. Click **+ Create service account**
3. **Service account name:** `reclaim-cron`
4. ID auto-fills. Click **Create and continue**.
5. Role step: skip (click **Continue**).
6. User access step: skip (click **Done**).
7. Back on the service accounts list, click `reclaim-cron@...iam.gserviceaccount.com`
8. Tab **Keys** → **Add key** → **Create new key** → JSON → **Create**.
   A JSON file downloads — keep it, you'll paste it into a GitHub
   secret in step 5.
9. Tab **Details** → expand **Advanced settings** → copy the
   **Unique ID** (a long numeric string like `123456789012345678901`).
   You'll need this in step 3.

---

## Step 3 — Authorize the service account in Workspace Admin (3 min)

1. Open <https://admin.google.com> → **Security** → **Access and data
   control** → **API controls**
2. Scroll to **Domain wide delegation** → **Manage Domain Wide
   Delegation**
3. Click **Add new**
4. **Client ID:** paste the Unique ID from step 2.9
5. **OAuth scopes (comma-delimited):**
   ```
   https://www.googleapis.com/auth/calendar.events
   ```
6. Click **Authorize**

---

## Step 4 — Share your personal calendar with your Workspace user (1 min)

So the service account (impersonating your Workspace user) can write
events to your personal calendar.

1. Open <https://calendar.google.com> while signed in as your **personal
   Gmail** account
2. Left sidebar → hover your name under "My calendars" → ⋮ → **Settings
   and sharing**
3. Scroll to **Share with specific people or groups** → **+ Add people**
4. Type your **Workspace email address**
5. **Permissions:** **Make changes to events**
6. Click **Send**
7. Open the invite email in your Workspace inbox (or accept via the
   Calendar UI). Once accepted, the calendar appears on your Workspace
   account too.

---

## Step 5 — Update the GitHub repo secrets

You're replacing the three old OAuth secrets with three new
"permanent" ones.

Open <https://github.com/azizaceo90/trackinng/settings/secrets/actions>
and **delete** the old ones:

- `GOOGLE_OAUTH_CLIENT`
- `GMAIL_REFRESH_TOKEN`
- `CALENDAR_REFRESH_TOKEN`

Then **create** these four new ones:

| Name                          | Value                                                                                          |
|-------------------------------|-----------------------------------------------------------------------------------------------|
| `GMAIL_ADDRESS`               | `muhammadaziza732@gmail.com`                                                                  |
| `GMAIL_APP_PASSWORD`          | The 16-char password from step 1 (paste with or without spaces — IMAP accepts either)         |
| `SERVICE_ACCOUNT_JSON`        | The **entire contents** of the JSON file downloaded in step 2.8                               |
| `WORKSPACE_USER_EMAIL`        | The Workspace email you used in step 3 (this is the user the service account impersonates)    |
| `PERSONAL_CALENDAR_ID`        | `muhammadaziza732@gmail.com` (your personal Gmail address is the ID of your default calendar) |

---

## Step 6 — Trigger a fresh run

Once steps 1-5 are done, push any small change (or wait for the next
2-hour cron). The new workflow:

- Connects to Gmail over IMAP with your App Password
- Signs Calendar API requests with the service account's private key,
  impersonating your Workspace user
- Writes events to your personal calendar via the share you set up in
  step 4

**Verify it worked:** the run goes green, no token expiry warning.
From now on the cron runs forever — no re-auth, no rotation.

---

## Reverting to OAuth (if anything breaks)

The old OAuth code paths are still in the repo (`reclaim/gmail_fetch.py`
+ OAuth section of `reclaim/calendar_fetch.py`). To switch back, just
restore the three old secrets and revert the workflow file.

## Cost

- Service account: free (Google Cloud's free tier)
- App passwords: free
- Workspace: you already pay for this
- GitHub Actions for a personal account: free under public-repo or
  light-use limits

Total: $0 ongoing.
