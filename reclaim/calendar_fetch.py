"""Google Calendar API client (stdlib only).

Mirrors reclaim/gmail_fetch.py's OAuth pattern. Used by the automated
interview-ingest script so it can both READ the user's calendar (to
dedupe) and CREATE new interview events without a human in the loop.

Requires the OAuth client to be authorized with the scope
``https://www.googleapis.com/auth/calendar.events``. Re-run
``python -m reclaim.calendar_fetch auth`` to grant it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from reclaim.gmail_fetch import (
    CONFIG_DIR,
    TOKENS_DIR,
    _load_oauth_client,
    _post_form,
    TOKEN_URL,
)


SCOPE = "https://www.googleapis.com/auth/calendar.events"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"


def _token_path(account: str) -> Path:
    return TOKENS_DIR / f"calendar-{account}.json"


def _save_token(token: dict, account: str = "default") -> None:
    TOKENS_DIR.mkdir(parents=True, exist_ok=True)
    p = _token_path(account)
    p.write_text(json.dumps(token, indent=2))
    p.chmod(0o600)


def _load_token(account: str = "default") -> dict | None:
    p = _token_path(account)
    if p.exists():
        return json.loads(p.read_text())
    return None


def _refresh_access_token(refresh_token: str) -> str:
    client = _load_oauth_client()
    out = _post_form(TOKEN_URL, {
        "client_id": client["client_id"],
        "client_secret": client["client_secret"],
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    })
    return out["access_token"]


def _service_account_token() -> str | None:
    """If a service account is configured via env, mint an access token with
    it (impersonating WORKSPACE_USER_EMAIL via domain-wide delegation).

    Returns None when no service account is configured, so callers fall back
    to the OAuth refresh-token path.
    """
    sa_json = os.environ.get("SERVICE_ACCOUNT_JSON")
    if not sa_json:
        return None
    from reclaim import google_sa  # local import: stdlib-only, no cycle
    sa_info = json.loads(sa_json)
    subject = os.environ.get("WORKSPACE_USER_EMAIL") or None
    return google_sa.access_token(sa_info, [SCOPE], subject=subject)


def get_access_token(account: str = "default") -> str:
    # Prefer the no-expiry service-account path when it's configured.
    sa = _service_account_token()
    if sa is not None:
        return sa
    token = _load_token(account)
    if not token or "refresh_token" not in token:
        sys.exit(
            f"No calendar refresh token for account {account!r} at "
            f"{_token_path(account)}.\n"
            f"Run `python -m reclaim.calendar_fetch auth --account {account}` first."
        )
    return _refresh_access_token(token["refresh_token"])


def target_calendar_id(default: str = "primary") -> str:
    """Calendar to write to. With a service account impersonating a Workspace
    user, "primary" is that user's calendar, so writes must target the shared
    personal calendar id from PERSONAL_CALENDAR_ID instead."""
    return os.environ.get("PERSONAL_CALENDAR_ID") or default


def authenticate_print_url() -> None:
    client = _load_oauth_client()
    redirect_uri = "http://localhost"
    auth_url = f"{AUTH_URL}?" + urllib.parse.urlencode({
        "client_id": client["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
    })
    print("Open this URL in any browser and approve the consent screen:\n")
    print(auth_url)
    print()
    print("After authorizing, copy the `code=` value from the localhost URL")
    print("Google redirects to (the page won't load — that's fine).\n")
    print("Then run:")
    print("  python -m reclaim.calendar_fetch auth-complete <paste-code-here>")


def authenticate_exchange(code: str, account: str = "default") -> None:
    client = _load_oauth_client()
    redirect_uri = "http://localhost"
    token = _post_form(TOKEN_URL, {
        "client_id": client["client_id"],
        "client_secret": client["client_secret"],
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    })
    if "refresh_token" not in token:
        sys.exit(
            "No refresh_token in OAuth response. Revoke prior consent at "
            "https://myaccount.google.com/permissions and re-run."
        )
    _save_token({"refresh_token": token["refresh_token"]}, account)
    print(f"Saved calendar refresh token for {account!r} to {_token_path(account)}.")


def _api(method: str, path: str, access_token: str, body: dict | None = None,
         params: dict | None = None) -> dict:
    url = f"{CALENDAR_API}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = None
    headers = {"Authorization": f"Bearer {access_token}"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req) as resp:
        raw = resp.read()
        if not raw:
            return {}
        return json.loads(raw)


def list_events(
    access_token: str,
    *,
    time_min: str,
    time_max: str,
    calendar_id: str = "primary",
    max_results: int = 250,
) -> list[dict]:
    out = _api(
        "GET", f"/calendars/{urllib.parse.quote(calendar_id)}/events",
        access_token,
        params={
            "timeMin": time_min, "timeMax": time_max,
            "singleEvents": "true", "orderBy": "startTime",
            "maxResults": str(max_results),
        },
    )
    return out.get("items", [])


def create_event(access_token: str, body: dict, calendar_id: str = "primary") -> dict:
    return _api(
        "POST", f"/calendars/{urllib.parse.quote(calendar_id)}/events",
        access_token, body=body,
    )


def patch_event(access_token: str, event_id: str, body: dict,
                calendar_id: str = "primary") -> dict:
    return _api(
        "PATCH",
        f"/calendars/{urllib.parse.quote(calendar_id)}/events/{urllib.parse.quote(event_id)}",
        access_token, body=body,
    )


def delete_event(access_token: str, event_id: str,
                 calendar_id: str = "primary") -> None:
    _api(
        "DELETE",
        f"/calendars/{urllib.parse.quote(calendar_id)}/events/{urllib.parse.quote(event_id)}",
        access_token,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="reclaim.calendar_fetch")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("auth")
    c = sub.add_parser("auth-complete")
    c.add_argument("code")
    c.add_argument("--account", default="default")
    args = parser.parse_args(argv)
    if args.cmd == "auth":
        authenticate_print_url()
    elif args.cmd == "auth-complete":
        authenticate_exchange(args.code, args.account)


if __name__ == "__main__":
    main()
