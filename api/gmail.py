"""
/api/gmail  —  Read-only Gmail proxy for local tools (e.g. the ai-qaer sent-history check).

Auth: header  Authorization: Bearer <GMAIL_PROXY_TOKEN>   (see _google_ro.py)

Endpoints (GET only):
  /api/gmail?op=whoami
      → {"ok": true, "email": "...", "messages_total": 123, "scopes": [...]}
  /api/gmail?op=search&q=in:sent "Listing Optimisation" newer_than:90d&max=20
      → {"ok": true, "count": n, "messages": [{id, thread_id, date, from, to, cc, subject, snippet}]}
  /api/gmail?op=read&id=<message id>
      → {"ok": true, "message": {..., "body": "<plain text, max 20k chars>"}}

Full reference: docs/google-api.md
"""

from __future__ import annotations

import base64
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(__file__))
from _google_ro import ReadOnlyHandler, google_get, int_param, str_param

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
MAX_RESULTS_CAP = 50
BODY_CHAR_CAP = 20_000
_HEADERS = ("Date", "From", "To", "Cc", "Subject")


def _gmail_get(token: str, path: str, params: dict | None = None) -> dict:
    return google_get(token, f"{GMAIL_API}/{path}", params)


def _summarise(msg: dict) -> dict:
    headers = {
        h["name"]: h["value"]
        for h in msg.get("payload", {}).get("headers", [])
        if h["name"] in _HEADERS
    }
    return {
        "id": msg["id"],
        "thread_id": msg.get("threadId"),
        "date": headers.get("Date"),
        "from": headers.get("From"),
        "to": headers.get("To"),
        "cc": headers.get("Cc"),
        "subject": headers.get("Subject"),
        "snippet": msg.get("snippet"),
    }


def _plain_body(payload: dict) -> str:
    """Return the first text/plain part (depth-first), decoded."""
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        raw = payload["body"]["data"]
        return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8", "replace")
    for part in payload.get("parts", []) or []:
        text = _plain_body(part)
        if text:
            return text
    return ""


def _whoami(token: str, scopes: list[str], params: dict) -> dict:
    profile = _gmail_get(token, "profile")
    return {
        "ok": True,
        "email": profile.get("emailAddress"),
        "messages_total": profile.get("messagesTotal"),
        "scopes": scopes,
    }


def _search(token: str, scopes: list[str], params: dict) -> dict:
    q = str_param(params, "q", required=True)
    max_results = int_param(params, "max", 20, 1, MAX_RESULTS_CAP)
    listing = _gmail_get(token, "messages", {"q": q, "maxResults": max_results})
    messages = []
    for ref in listing.get("messages", []):
        msg = _gmail_get(token, f"messages/{ref['id']}", {
            "format": "metadata", "metadataHeaders": list(_HEADERS),
        })
        messages.append(_summarise(msg))
    return {"ok": True, "query": q, "count": len(messages), "messages": messages}


def _read(token: str, scopes: list[str], params: dict) -> dict:
    msg_id = str_param(params, "id", required=True)
    msg = _gmail_get(token, f"messages/{urllib.parse.quote(msg_id, safe='')}", {"format": "full"})
    body = _plain_body(msg.get("payload", {}))
    out = _summarise(msg)
    out["body"] = body[:BODY_CHAR_CAP]
    out["body_truncated"] = len(body) > BODY_CHAR_CAP
    return {"ok": True, "message": out}


class handler(ReadOnlyHandler):
    OPS = {"whoami": _whoami, "search": _search, "read": _read}
    DEFAULT_OP = "search"
