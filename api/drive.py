"""
/api/drive  —  Read-only Google Drive / Docs / Sheets / Slides proxy for local tools.

Auth: header  Authorization: Bearer <GMAIL_PROXY_TOKEN>   (see _google_ro.py)

Endpoints (GET only):
  /api/drive?op=search&text=SIP budget&type=doc&max=20
  /api/drive?op=search&q=name contains 'Weekly' and modifiedTime > '2026-11-01T00:00:00'
      → {"ok": true, "count": n, "files": [{id, name, mime_type, type, modified_time, owners, web_link}]}
      Filters combine with AND; trashed files are always excluded.
  /api/drive?op=meta&id=<file id>
      → {"ok": true, "file": {...}}
  /api/drive?op=read&id=<file id>&format=md
      → {"ok": true, "file": {...}, "content": "<text, max 50k chars>", "content_format": "..."}
      Docs → text (or markdown with format=md), Sheets → CSV of the first sheet,
      Slides → text, plain-text files → as-is. Other types (PDF, images…) → 415.

Accepts Google Docs/Sheets/Slides URLs in place of an id.

Full reference: docs/google-api.md
"""

from __future__ import annotations

import os
import re
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(__file__))
from _google_ro import BadRequest, ReadOnlyHandler, google_get, int_param, str_param

DRIVE_API = "https://www.googleapis.com/drive/v3/files"
MAX_RESULTS_CAP = 100
CONTENT_CHAR_CAP = 50_000
DOWNLOAD_BYTE_CAP = 5 * 1024 * 1024
_FILE_FIELDS = (
    "id,name,mimeType,modifiedTime,createdTime,size,webViewLink,"
    "owners(emailAddress,displayName),lastModifyingUser(emailAddress,displayName),"
    "parents,driveId"
)
_ALL_DRIVES = {"supportsAllDrives": "true"}

_TYPE_TO_MIME = {
    "doc": "application/vnd.google-apps.document",
    "sheet": "application/vnd.google-apps.spreadsheet",
    "slide": "application/vnd.google-apps.presentation",
    "folder": "application/vnd.google-apps.folder",
    "pdf": "application/pdf",
}
_MIME_TO_TYPE = {v: k for k, v in _TYPE_TO_MIME.items()}

# Google-native type → (export mime, content_format); "md" variant only for Docs.
_EXPORTS = {
    "application/vnd.google-apps.document": ("text/plain", "text"),
    "application/vnd.google-apps.spreadsheet": ("text/csv", "csv"),
    "application/vnd.google-apps.presentation": ("text/plain", "text"),
}
_TEXT_MIMES = ("application/json", "application/xml", "application/csv")

_URL_ID = re.compile(r"/d/([A-Za-z0-9_-]{10,})|[?&]id=([A-Za-z0-9_-]{10,})")


def _file_id(params: dict) -> str:
    """Accept a bare id or any Docs/Sheets/Slides/Drive share URL."""
    raw = str_param(params, "id", required=True)
    m = _URL_ID.search(raw)
    return (m.group(1) or m.group(2)) if m else raw


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _file(f: dict) -> dict:
    mime = f.get("mimeType", "")
    return {
        "id": f.get("id"),
        "name": f.get("name"),
        "mime_type": mime,
        "type": _MIME_TO_TYPE.get(mime, "other"),
        "modified_time": f.get("modifiedTime"),
        "created_time": f.get("createdTime"),
        "size": int(f["size"]) if f.get("size") else None,
        "owners": [o.get("emailAddress") for o in f.get("owners", [])],
        "last_modified_by": (f.get("lastModifyingUser") or {}).get("emailAddress"),
        "web_link": f.get("webViewLink"),
        "shared_drive_id": f.get("driveId"),
    }


def _get_meta(token: str, file_id: str) -> dict:
    return google_get(token, f"{DRIVE_API}/{urllib.parse.quote(file_id, safe='')}",
                      {"fields": _FILE_FIELDS, **_ALL_DRIVES})


def _search(token: str, scopes: list[str], params: dict) -> dict:
    clauses = ["trashed = false"]
    q, text, name = str_param(params, "q"), str_param(params, "text"), str_param(params, "name")
    kind = str_param(params, "type")
    if q:
        clauses.append(f"({q})")
    if text:
        clauses.append(f"fullText contains '{_escape(text)}'")
    if name:
        clauses.append(f"name contains '{_escape(name)}'")
    if kind:
        if kind not in _TYPE_TO_MIME:
            raise BadRequest(f"type must be one of: {', '.join(_TYPE_TO_MIME)}")
        clauses.append(f"mimeType = '{_TYPE_TO_MIME[kind]}'")
    query = {
        "q": " and ".join(clauses),
        "pageSize": int_param(params, "max", 20, 1, MAX_RESULTS_CAP),
        "fields": f"files({_FILE_FIELDS})",
        "corpora": "allDrives",
        "includeItemsFromAllDrives": "true",
        **_ALL_DRIVES,
    }
    # Drive rejects orderBy on fullText queries; those come back relevance-ranked.
    if "fullText" not in query["q"]:
        query["orderBy"] = "modifiedTime desc"
    data = google_get(token, DRIVE_API, query)
    files = [_file(f) for f in data.get("files", [])]
    return {"ok": True, "query": query["q"], "count": len(files), "files": files}


def _meta(token: str, scopes: list[str], params: dict) -> dict:
    return {"ok": True, "file": _file(_get_meta(token, _file_id(params)))}


def _read(token: str, scopes: list[str], params: dict) -> dict:
    file_id = _file_id(params)
    meta = _get_meta(token, file_id)
    mime = meta.get("mimeType", "")
    quoted = urllib.parse.quote(file_id, safe="")

    if mime in _EXPORTS:
        export_mime, fmt = _EXPORTS[mime]
        if mime == "application/vnd.google-apps.document" and str_param(params, "format") == "md":
            export_mime, fmt = "text/markdown", "markdown"
        body, _ = google_get(token, f"{DRIVE_API}/{quoted}/export",
                             {"mimeType": export_mime}, raw=True)
    elif mime.startswith("text/") or mime in _TEXT_MIMES:
        if int(meta.get("size") or 0) > DOWNLOAD_BYTE_CAP:
            raise BadRequest(f"file larger than {DOWNLOAD_BYTE_CAP // 1024 // 1024} MB", 413)
        body, _ = google_get(token, f"{DRIVE_API}/{quoted}",
                             {"alt": "media", **_ALL_DRIVES}, raw=True)
        fmt = "text"
    else:
        raise BadRequest(f"cannot read content of {mime or 'unknown type'}; use op=meta", 415)

    text = body.decode("utf-8", "replace").lstrip("﻿")
    return {
        "ok": True,
        "file": _file(meta),
        "content_format": fmt,
        "content": text[:CONTENT_CHAR_CAP],
        "content_truncated": len(text) > CONTENT_CHAR_CAP,
    }


class handler(ReadOnlyHandler):
    OPS = {"search": _search, "meta": _meta, "read": _read}
    DEFAULT_OP = "search"
