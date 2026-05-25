import email
from email.message import EmailMessage

from reclaim import gmail_imap
from reclaim.interviews import ics_from_message


def test_credentials_default_account(monkeypatch):
    monkeypatch.setenv("GMAIL_ADDRESS", "me@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")
    addr, pw = gmail_imap.credentials("default")
    assert addr == "me@gmail.com"
    assert pw == "abcd efgh ijkl mnop"
    assert gmail_imap.available("default") is True


def test_credentials_named_account_uses_suffix(monkeypatch):
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    monkeypatch.setenv("GMAIL_ADDRESS_PERSONAL2", "other@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD_PERSONAL2", "wxyz")
    addr, pw = gmail_imap.credentials("personal2")
    assert addr == "other@gmail.com"
    assert pw == "wxyz"
    assert gmail_imap.available("personal2") is True
    # default account has no creds set -> not available
    monkeypatch.delenv("GMAIL_ADDRESS", raising=False)
    assert gmail_imap.available("default") is False


def test_available_false_when_unset(monkeypatch):
    monkeypatch.delenv("GMAIL_ADDRESS", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    assert gmail_imap.available("default") is False


def test_is_sent_detects_sent_label():
    assert gmail_imap.is_sent(r"(\\Sent \\Important)") is True
    assert gmail_imap.is_sent(r"(\Sent)") is True
    assert gmail_imap.is_sent('("[Gmail]/Sent Mail")') is True
    assert gmail_imap.is_sent(r"(\\Inbox \\Important)") is False
    assert gmail_imap.is_sent("") is False


def test_parse_thrid():
    # bytes input
    meta = b"1 (X-GM-THRID 1790271649293001234 X-GM-LABELS (\\Inbox) RFC822 {123}"
    assert gmail_imap._parse_thrid(meta) == "1790271649293001234"
    assert gmail_imap._parse_thrid(b"no thrid here") is None
    # str input (the real code path decodes metadata to text before parsing)
    meta_str = "1 (X-GM-LABELS (\\Inbox) X-GM-THRID 1790271649293009999 RFC822 {123}"
    assert gmail_imap._parse_thrid(meta_str) == "1790271649293009999"
    assert gmail_imap._parse_thrid("") is None


def test_derive_snippet_from_plaintext():
    msg = EmailMessage()
    msg["Subject"] = "Hi"
    msg.set_content("Your   interview\n\nis confirmed for Thursday.   ")
    snip = gmail_imap.derive_snippet(msg, limit=40)
    assert snip == "Your interview is confirmed for Thursday."[:40]


def test_ics_from_message_reads_text_calendar():
    raw = (
        "From: r@acme.com\r\n"
        "Subject: Invite\r\n"
        'Content-Type: multipart/mixed; boundary="b"\r\n'
        "\r\n"
        "--b\r\n"
        "Content-Type: text/plain\r\n\r\n"
        "See attached invite.\r\n"
        "--b\r\n"
        "Content-Type: text/calendar; method=REQUEST\r\n\r\n"
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Interview\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
        "--b--\r\n"
    )
    msg = email.message_from_string(raw)
    ics = ics_from_message(msg)
    assert ics is not None
    assert "BEGIN:VCALENDAR" in ics
    assert "SUMMARY:Interview" in ics


def test_ics_from_message_none_when_absent():
    msg = email.message_from_string(
        "From: r@acme.com\r\nContent-Type: text/plain\r\n\r\njust text\r\n"
    )
    assert ics_from_message(msg) is None
