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

## Step 1 — Generate Gmail App Passwords (4 min)

Interview-ingest scans **both** Gmail accounts, so generate an app
password on **each** (prayer-times only needs Calendar, no Gmail):

For **`muhammadaziza732@gmail.com`** and again for
**`aziza.muhammadx@gmail.com`**:

1. Sign in to that account. Make sure 2-Step Verification is on at
   <https://myaccount.google.com/security>. (App passwords are gated
   behind 2FA.)
2. Open <https://myaccount.google.com/apppasswords>
3. **App name:** `reclaim-interview-ingest`
4. Click **Create**. Google shows a 16-character password in a yellow
   box (e.g. `abcd efgh ijkl mnop`). Copy it now — you can't see it
   again.
5. Note which password goes with which account — in step 5 the
   muhammadaziza732 one is `GMAIL_APP_PASSWORD` and the aziza.muhammadx
   one is `GMAIL_APP_PASSWORD_PERSONAL2`.

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

## Step 4 — Share BOTH calendars with your Workspace user (2 min)

The service account impersonates one Workspace user, so every calendar
it writes to must be shared with that user. Interviews land on both the
muhammadaziza732 and aziza.muhammadx calendars; prayer times land on
aziza.muhammadx. So share **both**.

For **`muhammadaziza732@gmail.com`** and again for
**`aziza.muhammadx@gmail.com`**:

1. Open <https://calendar.google.com> signed in as that account.
2. Left sidebar → hover the calendar under "My calendars" → ⋮ →
   **Settings and sharing**.
3. **Share with specific people or groups** → **+ Add people**.
4. Type your **Workspace email address** (`WORKSPACE_USER_EMAIL`).
5. **Permissions: Make changes to events.**
6. **Send**, then accept the invite from the Workspace inbox.

---

## Step 5 — Update the GitHub repo secrets

You're replacing the three old OAuth secrets with three new
"permanent" ones.

Open <https://github.com/azizaceo90/trackinng/settings/secrets/actions>
and **delete** the old ones:

- `GOOGLE_OAUTH_CLIENT`
- `GMAIL_REFRESH_TOKEN`
- `CALENDAR_REFRESH_TOKEN`

Then **create** these secrets:

| Name                            | Value                                                                                       |
|---------------------------------|---------------------------------------------------------------------------------------------|
| `GMAIL_ADDRESS`                 | `muhammadaziza732@gmail.com`                                                                 |
| `GMAIL_APP_PASSWORD`            | muhammadaziza732 app password from step 1 (spaces ok — IMAP accepts either)                  |
| `GMAIL_ADDRESS_PERSONAL2`       | `aziza.muhammadx@gmail.com`                                                                  |
| `GMAIL_APP_PASSWORD_PERSONAL2`  | aziza.muhammadx app password from step 1                                                     |
| `SERVICE_ACCOUNT_JSON`          | The **entire contents** of the JSON file downloaded in step 2.8                             |
| `WORKSPACE_USER_EMAIL`          | The Workspace email from step 3 (the user the service account impersonates)                  |
| `CALENDAR_ID_DEFAULT`           | `muhammadaziza732@gmail.com` (interviews from the default account land here)                 |
| `CALENDAR_ID_PERSONAL2`         | `aziza.muhammadx@gmail.com` (interviews from the personal2 account land here)                |
| `PERSONAL_CALENDAR_ID`          | `aziza.muhammadx@gmail.com` (used by prayer-times)                                           |

Each value is read straight from the environment by the code — the IMAP
backend turns on per account when its `GMAIL_APP_PASSWORD*` is present,
and the service-account calendar path turns on when `SERVICE_ACCOUNT_JSON`
is present. Until you add them, the crons keep using the old OAuth
tokens, so you can add the secrets and flip over with zero downtime.

> **Note:** the **research** cron also writes a Google Doc, which still
> uses an OAuth token (Docs/Drive) and is *not* covered by this
> service-account setup — it will still hit the 7-day expiry. Only
> prayer-times and interview-ingest become truly no-expiry here.

---

## Step 6 — Validate with a dry run BEFORE trusting it

The IMAP + service-account paths can't be exercised by CI tests — they
need a real mailbox/calendar — so do one manual dry run first.

1. GitHub → **Actions** → **Interview ingest** → **Run workflow**.
2. Open the run. The scan steps print a JSON report. Confirm:
   - No auth errors (no 401/403, no "invalid_grant", no IMAP login fail).
   - `threads_examined` is > 0 (IMAP search is returning mail).
   - `created` / `skipped` look sane.
3. If the dry run looks right, the scheduled runs will do the same thing
   for real.

(To dry-run locally without writing events, the scan command supports
`--dry-run`.)

**Once validated:** the cron runs forever — no re-auth, no rotation, no
7-day expiry, no verification wall (because there's no OAuth restricted
scope in play anymore).

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
