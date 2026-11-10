"""
/api/gmail  —  Read-only Gmail proxy for local tools (e.g. the ai-qaer sent-history check).

The Google OAuth secrets (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET /
GOOGLE_REFRESH_TOKEN) never leave Vercel. Callers get search results only.

Auth: header  Authorization: Bearer <GMAIL_PROXY_TOKEN>

Endpoints (GET only):
  /api/gmail?op=whoami
      → {"ok": true, "email": "...", "messages_total": 123}
  /api/gmail?op=search&q=in:sent "Listing Optimisation" newer_than:90d&max=20
      → {"ok": true, "count": n, "messages": [{id, thread_id, date, from, to, cc, subject, snippet}]}
  /api/gmail?op=read&id=<message id>
      → {"ok": true, "message": {..., "body": "<plain text, max 20k chars>"}}

Required Vercel environment variables:
  GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN  — already set
  GMAIL_PROXY_TOKEN  — random string; the only credential callers ever hold
"""

from __future__ import annotations

import base64
import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
MAX_RESULTS_CAP = 50
BODY_CHAR_CAP = 20_000
_HEADERS = ("Date", "From", "To", "Cc", "Subject")


def _access_token() -> str:
    data = urllib.parse.urlencode({
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
        "refresh_token": os.environ["GOOGLE_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token", data=data, method="POST"
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())["access_token"]


def _gmail_get(token: str, path: str, params: dict | None = None) -> dict:
    url = f"{GMAIL_API}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


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


def _search(token: str, q: str, max_results: int) -> dict:
    listing = _gmail_get(token, "messages", {"q": q, "maxResults": max_results})
    messages = []
    for ref in listing.get("messages", []):
        msg = _gmail_get(token, f"messages/{ref['id']}", {
            "format": "metadata", "metadataHeaders": list(_HEADERS),
        })
        messages.append(_summarise(msg))
    return {"ok": True, "query": q, "count": len(messages), "messages": messages}


def _read(token: str, msg_id: str) -> dict:
    msg = _gmail_get(token, f"messages/{urllib.parse.quote(msg_id, safe='')}", {"format": "full"})
    body = _plain_body(msg.get("payload", {}))
    out = _summarise(msg)
    out["body"] = body[:BODY_CHAR_CAP]
    out["body_truncated"] = len(body) > BODY_CHAR_CAP
    return {"ok": True, "message": out}


def _whoami(token: str) -> dict:
    profile = _gmail_get(token, "profile")
    return {
        "ok": True,
        "email": profile.get("emailAddress"),
        "messages_total": profile.get("messagesTotal"),
    }


class handler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorised(self) -> bool:
        secret = os.environ.get("GMAIL_PROXY_TOKEN", "")
        supplied = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        return bool(secret) and hmac.compare_digest(supplied.encode(), secret.encode())

    def do_GET(self):
        if not self._authorised():
            return self._send(401, {"ok": False, "error": "unauthorized"})

        params = parse_qs(urlparse(self.path).query)
        op = params.get("op", ["search"])[0]

        try:
            token = _access_token()
            if op == "whoami":
                return self._send(200, _whoami(token))
            if op == "search":
                q = params.get("q", [""])[0].strip()
                if not q:
                    return self._send(400, {"ok": False, "error": "missing q"})
                try:
                    max_results = int(params.get("max", ["20"])[0])
                except ValueError:
                    return self._send(400, {"ok": False, "error": "max must be an integer"})
                max_results = max(1, min(max_results, MAX_RESULTS_CAP))
                return self._send(200, _search(token, q, max_results))
            if op == "read":
                msg_id = params.get("id", [""])[0].strip()
                if not msg_id:
                    return self._send(400, {"ok": False, "error": "missing id"})
                return self._send(200, _read(token, msg_id))
            return self._send(400, {"ok": False, "error": f"unknown op: {op}"})
        except urllib.error.HTTPError as exc:
            # Never echo Google's response body — it can include request details.
            return self._send(502, {"ok": False, "error": f"google api error {exc.code}"})
        except KeyError as exc:
            return self._send(500, {"ok": False, "error": f"missing env var {exc}"})
        except Exception as exc:
            return self._send(500, {"ok": False, "error": type(exc).__name__})

    def do_POST(self):
        self._send(405, {"ok": False, "error": "read-only: GET only"})

    do_PUT = do_PATCH = do_DELETE = do_POST

    def log_message(self, fmt, *args):
        pass
