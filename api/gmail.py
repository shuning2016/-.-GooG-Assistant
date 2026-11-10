"""
/api/gmail  —  Read-only Gmail proxy for local tools (e.g. the ai-qaer sent-history check).

Auth: header  Authorization: Bearer <GMAIL_PROXY_TOKEN>   (see _google_ro.py)

Endpoints (GET only):
  /api/gmail?op=whoami
      → {"ok": true, "email": "...", "messages_total": 123, "scopes": [...]}
  /api/gmail?op=search&q=in:sent "Listing Optimisation" newer_than:90d&max=20
      → {"ok": true, "count": n, "messages": [{id, thread_id, date, from, to, cc, subject, snippet}]}
  /api/gmail?op=read&id=<message id>
      → {"ok": true, "message": {..., "body": "<plain text, max 20k chars>",
                                 "attachments": [{part, filename, mime_type, size, readable}]}}
  /api/gmail?op=attachment&id=<message id>&part=<part from read>[&format=text|raw]
      → text (default): {"ok": true, "attachment": {...}, "content_format": "pdf-text", "content": "..."}
        raw:            {"ok": true, "attachment": {...}, "data_base64": "..."}   (≤ 3 MB)

Full reference: docs/google-api.md
"""

from __future__ import annotations

import base64
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(__file__))
from _extract import UnsupportedType, extract_text, kind_of
from _google_ro import BadRequest, ReadOnlyHandler, google_get, int_param, str_param

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
MAX_RESULTS_CAP = 50
BODY_CHAR_CAP = 20_000
ATTACHMENT_CHAR_CAP = 100_000
RAW_BYTE_CAP = 3 * 1024 * 1024  # Vercel caps responses at 4.5 MB; base64 adds 33%.
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
        return _b64decode(payload["body"]["data"]).decode("utf-8", "replace")
    for part in payload.get("parts", []) or []:
        text = _plain_body(part)
        if text:
            return text
    return ""


def _b64decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _attachment_parts(payload: dict) -> list[dict]:
    """All parts that carry a filename (real attachments and named inline files)."""
    found = []
    if payload.get("filename"):
        found.append(payload)
    for part in payload.get("parts", []) or []:
        found.extend(_attachment_parts(part))
    return found


def _attachment_meta(part: dict) -> dict:
    return {
        "part": part.get("partId"),
        "filename": part.get("filename"),
        "mime_type": part.get("mimeType"),
        "size": part.get("body", {}).get("size"),
        "readable": kind_of(part.get("mimeType", ""), part.get("filename", "")) is not None,
    }


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
    payload = msg.get("payload", {})
    body = _plain_body(payload)
    out = _summarise(msg)
    out["body"] = body[:BODY_CHAR_CAP]
    out["body_truncated"] = len(body) > BODY_CHAR_CAP
    out["attachments"] = [_attachment_meta(p) for p in _attachment_parts(payload)]
    return {"ok": True, "message": out}


def _attachment(token: str, scopes: list[str], params: dict) -> dict:
    msg_id = str_param(params, "id", required=True)
    part_id = str_param(params, "part", required=True)
    fmt = str_param(params, "format") or "text"
    if fmt not in ("text", "raw"):
        raise BadRequest("format must be text or raw")

    quoted = urllib.parse.quote(msg_id, safe="")
    msg = _gmail_get(token, f"messages/{quoted}", {"format": "full"})
    part = next((p for p in _attachment_parts(msg.get("payload", {}))
                 if p.get("partId") == part_id), None)
    if part is None:
        raise BadRequest(f"no attachment with part {part_id}; see op=read", 404)
    meta = _attachment_meta(part)

    body = part.get("body", {})
    if body.get("attachmentId"):
        att = _gmail_get(token, f"messages/{quoted}/attachments/"
                                f"{urllib.parse.quote(body['attachmentId'], safe='')}")
        data = _b64decode(att.get("data", ""))
    else:
        data = _b64decode(body.get("data", ""))  # small inline parts carry data directly

    if fmt == "raw":
        if len(data) > RAW_BYTE_CAP:
            raise BadRequest(f"attachment larger than {RAW_BYTE_CAP // 1024 // 1024} MB; "
                             "use format=text", 413)
        return {"ok": True, "attachment": meta,
                "data_base64": base64.b64encode(data).decode()}

    try:
        text, content_format = extract_text(data, meta["mime_type"] or "", meta["filename"] or "")
    except UnsupportedType as exc:
        raise BadRequest(f"cannot extract text from {exc}; try format=raw", 415)
    except Exception as exc:
        raise BadRequest(f"could not parse attachment ({type(exc).__name__}); try format=raw", 422)
    return {
        "ok": True,
        "attachment": meta,
        "content_format": content_format,
        "content": text[:ATTACHMENT_CHAR_CAP],
        "content_truncated": len(text) > ATTACHMENT_CHAR_CAP,
    }


class handler(ReadOnlyHandler):
    OPS = {"whoami": _whoami, "search": _search, "read": _read, "attachment": _attachment}
    DEFAULT_OP = "search"
