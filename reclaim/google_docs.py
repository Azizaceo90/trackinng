"""Google Docs + Drive API client (stdlib only).

Uses the personal2 OAuth refresh token, which after the 2026-05-17
re-scope covers calendar.events + documents + drive.file.

Drive scope is `drive.file`, which means this client can only see and
modify files it itself created — never the user's whole Drive. Safer
than full `drive` scope.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

from reclaim import calendar_fetch


DOCS_API = "https://docs.googleapis.com/v1"
DRIVE_API = "https://www.googleapis.com/drive/v3"


def get_access_token(account: str = "personal2") -> str:
    """Reuse the calendar refresh token (now also has Docs + Drive scopes)."""
    return calendar_fetch.get_access_token(account)


def _api(method: str, base: str, path: str, access_token: str,
         body: dict | None = None, params: dict | None = None) -> dict:
    url = f"{base}{path}"
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


def create_doc(title: str, access_token: str) -> dict:
    """Create a blank Google Doc. Returns {doc_id, url}."""
    out = _api("POST", DOCS_API, "/documents", access_token, body={"title": title})
    doc_id = out["documentId"]
    return {
        "doc_id": doc_id,
        "title": title,
        "url": f"https://docs.google.com/document/d/{doc_id}/edit",
    }


def batch_update(doc_id: str, requests: list[dict], access_token: str) -> dict:
    """Apply a batch of Docs-API edit requests to the document."""
    return _api(
        "POST", DOCS_API, f"/documents/{doc_id}:batchUpdate",
        access_token, body={"requests": requests},
    )


def write_doc(doc_id: str, sections: list[dict], access_token: str) -> None:
    """Render a structured set of sections into the doc.

    Each section is {kind: 'heading'|'subheading'|'paragraph'|'bullet', text: str}.
    Sections are appended in order. Existing doc content is preserved.
    """
    # Get current end index
    doc = _api("GET", DOCS_API, f"/documents/{doc_id}", access_token)
    cursor = doc["body"]["content"][-1]["endIndex"] - 1

    requests: list[dict] = []
    for section in sections:
        text = section["text"].rstrip("\n") + "\n"
        kind = section.get("kind", "paragraph")
        start = cursor
        end = cursor + len(text)
        requests.append({
            "insertText": {"location": {"index": start}, "text": text}
        })
        # Apply paragraph style
        style_map = {
            "heading":    "HEADING_1",
            "subheading": "HEADING_2",
            "paragraph":  "NORMAL_TEXT",
            "bullet":     "NORMAL_TEXT",
        }
        named = style_map.get(kind, "NORMAL_TEXT")
        requests.append({
            "updateParagraphStyle": {
                "range": {"startIndex": start, "endIndex": end},
                "paragraphStyle": {"namedStyleType": named},
                "fields": "namedStyleType",
            }
        })
        if kind == "bullet":
            requests.append({
                "createParagraphBullets": {
                    "range": {"startIndex": start, "endIndex": end},
                    "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
                }
            })
        cursor = end
    batch_update(doc_id, requests, access_token)


def make_doc_link_shareable(doc_id: str, access_token: str) -> str:
    """Grant 'anyone with the link can view' permission. Returns the URL."""
    _api(
        "POST", DRIVE_API, f"/files/{doc_id}/permissions",
        access_token,
        body={"role": "reader", "type": "anyone"},
    )
    return f"https://docs.google.com/document/d/{doc_id}/edit"


# --- folder management (drive.file scope, app-created files only) -----------

def find_folder_by_name(name: str, access_token: str) -> str | None:
    """Return the folder ID if a folder with this exact name (created by
    this app) exists. None otherwise.

    With drive.file scope we can only see files this OAuth client created,
    so this won't find folders the user made manually.
    """
    q = (f"mimeType='application/vnd.google-apps.folder' "
         f"and name='{name}' and trashed=false")
    out = _api(
        "GET", DRIVE_API, "/files", access_token,
        params={"q": q, "fields": "files(id,name)"},
    )
    files = out.get("files", [])
    return files[0]["id"] if files else None


def create_folder(name: str, access_token: str) -> dict:
    """Create a Drive folder. Returns {folder_id, url}."""
    out = _api(
        "POST", DRIVE_API, "/files", access_token,
        body={
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
        },
    )
    fid = out["id"]
    return {"folder_id": fid, "name": name,
            "url": f"https://drive.google.com/drive/folders/{fid}"}


def get_or_create_folder(name: str, access_token: str) -> dict:
    fid = find_folder_by_name(name, access_token)
    if fid:
        return {"folder_id": fid, "name": name,
                "url": f"https://drive.google.com/drive/folders/{fid}",
                "existed": True}
    folder = create_folder(name, access_token)
    folder["existed"] = False
    return folder


def move_to_folder(file_id: str, folder_id: str, access_token: str) -> None:
    """Move a file into a folder by replacing its parents."""
    # First get current parents
    info = _api(
        "GET", DRIVE_API, f"/files/{file_id}", access_token,
        params={"fields": "parents"},
    )
    current = ",".join(info.get("parents", []))
    _api(
        "PATCH", DRIVE_API, f"/files/{file_id}", access_token,
        params={"addParents": folder_id, "removeParents": current or "root"},
    )
