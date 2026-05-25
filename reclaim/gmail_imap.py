"""Read Gmail over IMAP with an app password — no OAuth, no token expiry.

A drop-in alternative to the gmail_fetch HTTP-API backend for the
interview-ingest pipeline. Selected automatically when an app password
is present in the environment (see `available`).

App passwords (https://myaccount.google.com/apppasswords) don't expire on
the 7-day OAuth clock, so a cron using this backend runs indefinitely
without re-auth. Gmail's IMAP supports server-side search with native
Gmail query syntax via `X-GM-RAW`, and exposes thread ids (`X-GM-THRID`)
and labels (`X-GM-LABELS`), so we can mirror the API backend's behavior:
thread grouping + skipping messages you sent.

Stdlib only (imaplib + email).
"""
from __future__ import annotations

import email
import imaplib
import re
from email.message import Message

from reclaim.gmail_fetch import html_to_text, message_bodies

IMAP_HOST = "imap.gmail.com"
_ALL_MAIL = "[Gmail]/All Mail"


# --------------------------------------------------------------- credentials ---


def _env(account: str, base: str) -> str | None:
    import os
    key = base if account == "default" else f"{base}_{account.upper()}"
    val = os.environ.get(key)
    return val.strip() if val else None


def credentials(account: str = "default") -> tuple[str | None, str | None]:
    return _env(account, "GMAIL_ADDRESS"), _env(account, "GMAIL_APP_PASSWORD")


def available(account: str = "default") -> bool:
    addr, pw = credentials(account)
    return bool(addr and pw)


# ----------------------------------------------------------- pure parse helpers ---


def is_sent(labels_response: str) -> bool:
    """True if an X-GM-LABELS value marks the message as sent by the user.

    Gmail returns labels like: (\\Sent \\Important "Job hunt"). The Sent
    label is the IMAP equivalent of the API's "SENT" labelId.
    """
    s = (labels_response or "")
    return "\\\\Sent" in s or "\\Sent" in s or '"[Gmail]/Sent Mail"' in s


def derive_snippet(msg: Message, limit: int = 200) -> str:
    """A short text preview from a message body, mirroring the API snippet
    that triage (is_skip) relies on."""
    plain, html = message_bodies(msg)
    text = plain or (html_to_text(html) if html else "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _decode_header(raw: str | None) -> str:
    if not raw:
        return ""
    from email.header import decode_header, make_header
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return raw


def _parse_thrid(fetch_meta: bytes) -> str | None:
    """Pull the X-GM-THRID value out of a FETCH metadata line."""
    m = re.search(rb"X-GM-THRID\s+(\d+)", fetch_meta or b"")
    return m.group(1).decode() if m else None


# --------------------------------------------------------------- IMAP client ---


class GmailIMAP:
    """Minimal Gmail IMAP client matching the slice of behavior the
    interview-ingest pipeline needs."""

    def __init__(self, account: str = "default"):
        addr, pw = credentials(account)
        if not (addr and pw):
            raise RuntimeError(
                f"No IMAP credentials for account {account!r} "
                f"(set GMAIL_ADDRESS / GMAIL_APP_PASSWORD)."
            )
        self.address = addr
        self._conn = imaplib.IMAP4_SSL(IMAP_HOST)
        self._conn.login(addr, pw)
        self._conn.select(f'"{_ALL_MAIL}"', readonly=True)

    def close(self) -> None:
        try:
            self._conn.logout()
        except Exception:
            pass

    def __enter__(self) -> "GmailIMAP":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _search_uids(self, gmail_query: str) -> list[bytes]:
        # The Gmail X-GM-RAW query contains quotes and parentheses, which break
        # an IMAP quoted string ("BAD Could not parse command"). Send it as an
        # IMAP literal instead: imaplib emits `... X-GM-RAW {N}\r\n<query>` and
        # clears self.literal after the command runs.
        self._conn.literal = gmail_query.encode("utf-8")
        typ, data = self._conn.uid("SEARCH", "X-GM-RAW")
        if typ != "OK" or not data or not data[0]:
            return []
        return data[0].split()

    def _fetch_message(self, uid: bytes) -> tuple[Message, str]:
        """Return (parsed message, raw X-GM-LABELS metadata string)."""
        typ, data = self._conn.uid(
            "FETCH", uid, "(X-GM-LABELS X-GM-THRID RFC822)"
        )
        if typ != "OK" or not data:
            raise RuntimeError(f"FETCH failed for uid {uid!r}")
        meta = b""
        raw = b""
        for part in data:
            if isinstance(part, tuple):
                meta += part[0] or b""
                raw = part[1] or raw
            elif isinstance(part, (bytes, bytearray)):
                meta += part
        msg = email.message_from_bytes(raw)
        return msg, meta.decode("utf-8", errors="replace")

    def search_threads(self, gmail_query: str, max_results: int = 25) -> list[dict]:
        """Mirror gmail_fetch.search_threads: group matching messages by
        Gmail thread id, newest-first, capped at max_results threads."""
        uids = self._search_uids(gmail_query)
        # Newest first (IMAP returns ascending UID order).
        uids = list(reversed(uids))
        threads: dict[str, dict] = {}
        for uid in uids:
            msg, meta = self._fetch_message(uid)
            thrid = _parse_thrid(meta) or uid.decode()
            entry = threads.get(thrid)
            if entry is None:
                # Stop discovering NEW threads once we have enough, but keep
                # collecting messages for threads we're already tracking.
                if len(threads) >= max_results:
                    continue
                entry = {
                    "thread_id": thrid,
                    "from": _decode_header(msg.get("From")),
                    "subject": _decode_header(msg.get("Subject")),
                    "date": msg.get("Date", ""),
                    "snippet": derive_snippet(msg),
                    "message_count": 0,
                    "_messages": [],
                }
                threads[thrid] = entry
            entry["message_count"] += 1
            entry["_messages"].append({
                "uid": uid,
                "message": msg,
                "is_sent": is_sent(meta),
            })
        return list(threads.values())
