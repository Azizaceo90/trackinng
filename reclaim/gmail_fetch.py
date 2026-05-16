"""Gmail HTML body fetcher with meeting-detail extraction.

Workaround for the Gmail MCP integration available to Claude Code, which
only exposes ``plaintextBody``. Many recruiter emails (applytojob.com,
Ashby, TeamTailor, Microsoft Teams invites, ...) ship HTML-only with no
text/plain alternative, so the join URL / Meeting ID / passcode are
invisible to the rest of the toolchain. This module hits the Gmail HTTP
API directly with the user's own OAuth refresh token, decodes the HTML
body, and pulls the meeting fields out with regex.

CLI:
    python -m reclaim.gmail_fetch auth                 # one-time OAuth setup
    python -m reclaim.gmail_fetch thread <thread_id>   # fetch a whole thread
    python -m reclaim.gmail_fetch message <message_id> # fetch a single message

Output is JSON on stdout. /reclaim-interviews uses this as a fallback
whenever the MCP ``get_thread`` returns no ``plaintextBody``.

Stdlib only — no third-party dependencies. See reclaim/GMAIL_SETUP.md
for the one-time OAuth setup steps.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from email import message_from_bytes
from email.message import Message
from html.parser import HTMLParser
from pathlib import Path


CONFIG_DIR = Path(
    os.environ.get("RECLAIM_CONFIG_DIR", Path.home() / ".config" / "reclaim")
)
CRED_PATH = CONFIG_DIR / "gmail_credentials.json"
TOKENS_DIR = CONFIG_DIR / "tokens"
LEGACY_TOKEN_PATH = CONFIG_DIR / "gmail_token.json"

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1"


# --------------------------------------------------------------------- auth ---


def _load_oauth_client() -> dict:
    if not CRED_PATH.exists():
        sys.exit(
            f"Missing OAuth client config at {CRED_PATH}.\n"
            "See reclaim/GMAIL_SETUP.md for the one-time setup."
        )
    raw = json.loads(CRED_PATH.read_text())
    inner = raw.get("installed") or raw.get("web") or raw
    return {"client_id": inner["client_id"], "client_secret": inner["client_secret"]}


def _token_path(account: str) -> Path:
    return TOKENS_DIR / f"{account}.json"


def _save_token(token: dict, account: str = "default") -> None:
    TOKENS_DIR.mkdir(parents=True, exist_ok=True)
    p = _token_path(account)
    p.write_text(json.dumps(token, indent=2))
    p.chmod(0o600)


def _load_token(account: str = "default") -> dict | None:
    p = _token_path(account)
    if p.exists():
        return json.loads(p.read_text())
    # One-time migration from the pre-multi-account layout.
    if account == "default" and LEGACY_TOKEN_PATH.exists():
        token = json.loads(LEGACY_TOKEN_PATH.read_text())
        _save_token(token, "default")
        LEGACY_TOKEN_PATH.unlink()
        return token
    return None


def list_accounts() -> list[str]:
    if not TOKENS_DIR.exists():
        return []
    return sorted(p.stem for p in TOKENS_DIR.glob("*.json"))


def _post_form(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def _refresh_access_token(refresh_token: str) -> str:
    client = _load_oauth_client()
    out = _post_form(TOKEN_URL, {
        "client_id": client["client_id"],
        "client_secret": client["client_secret"],
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    })
    return out["access_token"]


def _get_access_token(account: str = "default") -> str:
    token = _load_token(account)
    if not token or "refresh_token" not in token:
        sys.exit(
            f"No refresh token for account {account!r} at {_token_path(account)}.\n"
            f"Run `python -m reclaim.gmail_fetch auth --account {account}` first."
        )
    return _refresh_access_token(token["refresh_token"])


def authenticate_print_url() -> None:
    """Print the consent URL. User opens it, approves, copies the `code=`
    value from the localhost redirect address bar."""
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
    print("Google will redirect to a localhost URL that does NOT load — that's")
    print("expected. Copy the value of the `code=` query param from your")
    print("browser's address bar (between `code=` and `&scope=`).")
    print()
    print("Then run:")
    print("  python -m reclaim.gmail_fetch auth-complete <paste-code-here>")


def authenticate_exchange(code: str, account: str = "default") -> None:
    """Exchange the auth code for a refresh token."""
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
            "OAuth response did not include a refresh_token. "
            "Revoke prior consent at https://myaccount.google.com/permissions "
            "and re-run."
        )
    _save_token({"refresh_token": token["refresh_token"]}, account)
    print(f"Saved refresh token for account {account!r} to {_token_path(account)} (mode 600).")


# ----------------------------------------------------------- gmail fetching ---


def _api_get(path: str, access_token: str, params: dict | None = None) -> dict:
    url = f"{GMAIL_API}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {access_token}"}
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def fetch_raw_message(message_id: str, access_token: str) -> Message:
    payload = _api_get(
        f"/users/me/messages/{message_id}", access_token, {"format": "raw"}
    )
    raw = base64.urlsafe_b64decode(payload["raw"].encode())
    return message_from_bytes(raw)


def fetch_thread_messages(thread_id: str, access_token: str) -> list[Message]:
    thread = _api_get(
        f"/users/me/threads/{thread_id}", access_token, {"format": "minimal"}
    )
    return [fetch_raw_message(m["id"], access_token) for m in thread.get("messages", [])]


def search_threads(query: str, access_token: str, max_results: int = 25) -> list[dict]:
    """Search Gmail threads. Returns minimal thread metadata (id + snippet)."""
    out = _api_get(
        "/users/me/threads", access_token,
        {"q": query, "maxResults": str(max_results)},
    )
    threads = out.get("threads", [])
    detailed = []
    for t in threads:
        full = _api_get(
            f"/users/me/threads/{t['id']}", access_token,
            {"format": "metadata", "metadataHeaders": "From,Subject,Date"},
        )
        msgs = full.get("messages", [])
        first = msgs[0] if msgs else {}
        headers = {h["name"]: h["value"] for h in first.get("payload", {}).get("headers", [])}
        detailed.append({
            "thread_id": t["id"],
            "snippet": t.get("snippet", ""),
            "message_count": len(msgs),
            "from": headers.get("From", ""),
            "subject": headers.get("Subject", ""),
            "date": headers.get("Date", ""),
        })
    return detailed


# -------------------------------------------------------------- html → text ---


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        if tag == "a":
            for k, v in attrs:
                if k == "href" and v:
                    self.parts.append(f" {v} ")
        if tag in ("br", "p", "div", "tr", "li", "table"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip > 0:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    p = _TextExtractor()
    p.feed(html)
    text = "".join(p.parts)
    return re.sub(r"\n{3,}", "\n\n", text)


def message_bodies(msg: Message) -> tuple[str, str]:
    """Return (plaintext, html). Either may be empty."""
    plain = ""
    html = ""
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        payload = part.get_payload(decode=True) or b""
        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except LookupError:
            text = payload.decode("utf-8", errors="replace")
        if ctype == "text/plain" and not plain:
            plain = text
        elif ctype == "text/html" and not html:
            html = text
    return plain, html


# ---------------------------------------------------- meeting-detail regex ---

_TEAMS_URL = re.compile(
    r"https://teams\.microsoft\.com/(?:meet/\d+(?:\?p=[A-Za-z0-9_-]+)?"
    r"|l/meetup-join/[^\s\"'<>]+)"
)
_MEET_URL = re.compile(r"https://meet\.google\.com/[a-z]{3,4}-[a-z]{3,4}-[a-z]{3,4}")
_ZOOM_URL = re.compile(
    r"https://[\w.-]*zoom\.us/(?:j/\d+(?:\?pwd=[A-Za-z0-9._-]+)?|my/[\w-]+)"
)
_WEBEX_URL = re.compile(
    r"https://[\w.-]*webex\.com/(?:meet/[\w.-]+|[\w-]+\.php\?[^\s\"'<>]+)"
)
_MEETING_ID = re.compile(r"Meeting\s*ID[:\s]+([\d][\d\s]{6,}\d)", re.IGNORECASE)
_PASSCODE = re.compile(
    r"(?:Passcode|Password|Passphrase|Pin)[:\s]+([A-Za-z0-9._!@#$%^&*?-]{4,40})",
    re.IGNORECASE,
)
_CONF_ID = re.compile(
    r"(?:Phone\s+conference\s+ID|Conference\s+ID)[:\s]+([\d#\s]{6,})",
    re.IGNORECASE,
)
_DIAL_IN = re.compile(r"\+\d[\d\s().-]{8,18}\d")


def extract_meeting(text: str) -> dict:
    out: dict = {}
    for label, pat in (
        ("teams", _TEAMS_URL),
        ("google_meet", _MEET_URL),
        ("zoom", _ZOOM_URL),
        ("webex", _WEBEX_URL),
    ):
        m = pat.search(text)
        if m:
            out["platform"] = label
            out["join_url"] = m.group(0)
            break
    if (m := _MEETING_ID.search(text)):
        out["meeting_id"] = re.sub(r"\s+", " ", m.group(1).strip())
    if (m := _PASSCODE.search(text)):
        out["passcode"] = m.group(1).strip().rstrip(".")
    if (m := _CONF_ID.search(text)):
        out["conference_id"] = re.sub(r"\s+", " ", m.group(1).strip())
    if (m := _DIAL_IN.search(text)):
        out["dial_in"] = m.group(0)
    return out


def summarize_message(msg: Message) -> dict:
    plain, html = message_bodies(msg)
    text = plain or (html_to_text(html) if html else "")
    return {
        "from": msg.get("From", ""),
        "to": msg.get("To", ""),
        "subject": msg.get("Subject", ""),
        "date": msg.get("Date", ""),
        "has_plain": bool(plain),
        "has_html": bool(html),
        "meeting": extract_meeting(text),
        "text": text,
    }


# ---------------------------------------------------------- CLI entrypoints ---


def cmd_thread(thread_id: str, account: str = "default") -> None:
    access = _get_access_token(account)
    messages = fetch_thread_messages(thread_id, access)
    payload = {
        "account": account,
        "thread_id": thread_id,
        "messages": [summarize_message(m) for m in messages],
    }
    json.dump(payload, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


def cmd_message(message_id: str, account: str = "default") -> None:
    access = _get_access_token(account)
    msg = fetch_raw_message(message_id, access)
    payload = summarize_message(msg)
    payload["account"] = account
    json.dump(payload, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


def cmd_search(query: str, account: str = "default", max_results: int = 25) -> None:
    access = _get_access_token(account)
    threads = search_threads(query, access, max_results)
    json.dump({"account": account, "query": query, "threads": threads},
              sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


def cmd_accounts() -> None:
    accounts = list_accounts()
    if not accounts:
        print("(no accounts configured — run `python -m reclaim.gmail_fetch auth`)")
        return
    for a in accounts:
        print(a)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="reclaim.gmail_fetch",
        description=__doc__.splitlines()[0] if __doc__ else None,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    auth_p = sub.add_parser("auth", help="Print the OAuth consent URL.")
    auth_p.add_argument("--account", default="default",
                        help="Account name to authorize (default: 'default').")

    c = sub.add_parser("auth-complete",
                       help="Exchange the auth code for a refresh token.")
    c.add_argument("code", help="The `code=` value from the localhost redirect.")
    c.add_argument("--account", default="default")

    t = sub.add_parser("thread", help="Fetch a Gmail thread by ID.")
    t.add_argument("thread_id")
    t.add_argument("--account", default="default")

    m = sub.add_parser("message", help="Fetch a single Gmail message by ID.")
    m.add_argument("message_id")
    m.add_argument("--account", default="default")

    s = sub.add_parser("search", help="Search Gmail threads by query.")
    s.add_argument("query")
    s.add_argument("--account", default="default")
    s.add_argument("--max-results", type=int, default=25)

    sub.add_parser("accounts", help="List configured Gmail accounts.")

    args = parser.parse_args(argv)
    if args.cmd == "auth":
        authenticate_print_url()
    elif args.cmd == "auth-complete":
        authenticate_exchange(args.code, args.account)
    elif args.cmd == "thread":
        cmd_thread(args.thread_id, args.account)
    elif args.cmd == "message":
        cmd_message(args.message_id, args.account)
    elif args.cmd == "search":
        cmd_search(args.query, args.account, args.max_results)
    elif args.cmd == "accounts":
        cmd_accounts()


if __name__ == "__main__":
    main()
