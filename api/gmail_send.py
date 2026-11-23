"""
/api/gmail_send  —  Send one HTML email as the Google account behind GOOGLE_REFRESH_TOKEN.

Used by the scheduled mails (AI Daily 08:00, Daily Brief 08:30) so they go out
from the work mailbox instead of the personal Gmail.

This is the ONLY write path in the project; the read-only proxies
(/api/gmail, /api/gcal, /api/drive) are untouched and still refuse POST.

Request:
  POST /api/gmail_send
  Authorization: Bearer <GMAIL_PROXY_TOKEN>          (same token as the read proxies)
  Content-Type: application/json
  {"to": "a@x.com" | ["a@x.com", ...],
   "cc": "b@x.com" | ["b@x.com", ...],               (optional)
   "subject": "...",
   "html": "<!doctype html>..."}                     (≤ 500 KB, UTF-8)

Responses:
  200 {"ok": true, "id": "<Gmail message id>", "thread_id": "..."}
  400 bad / missing to, subject, html, or bad JSON
  401 missing or wrong bearer token
  405 anything other than POST
  413 html > 500 KB (or request body > 1 MB)
  403 Google refused, e.g. token lacks gmail.send scope
      {"ok": false, "error": "google api error 403", "reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}
  429 Google rate limit
  502 / 504 other Google errors (with "reason") / timeout,  500 missing env var

Requires the refresh token to carry https://www.googleapis.com/auth/gmail.send
(see vercel-daily-brief/setup_google_token.py).
"""

from __future__ import annotations

import base64
import hmac
import json
import os
import re
import socket
import sys
import urllib.error
import urllib.request
from email.message import EmailMessage
from email.policy import SMTP
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(__file__))
from _google_ro import BadRequest, ConfigError, access_token

SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
HTML_BYTE_CAP = 500 * 1024
REQUEST_BYTE_CAP = 1024 * 1024
MAX_RECIPIENTS = 50
_ADDR_RE = re.compile(r"^[^@\s,;<>\"]+@[^@\s,;<>\"]+\.[^@\s,;<>\"]+$")


def _recipients(value, field: str, required: bool) -> list[str]:
    if value is None or value == "" or value == []:
        if required:
            raise BadRequest(f"missing {field}")
        return []
    if isinstance(value, str):
        items = [v.strip() for v in value.split(",")]
    elif isinstance(value, list) and all(isinstance(v, str) for v in value):
        items = [v.strip() for v in value]
    else:
        raise BadRequest(f"{field} must be a string or an array of strings")
    items = [v for v in items if v]
    if required and not items:
        raise BadRequest(f"missing {field}")
    if len(items) > MAX_RECIPIENTS:
        raise BadRequest(f"too many {field} recipients (max {MAX_RECIPIENTS})")
    for addr in items:
        if not _ADDR_RE.match(addr):
            raise BadRequest(f"invalid {field} address: {addr[:100]}")
    return items


def build_raw(payload: dict) -> str:
    """Validate the JSON body and return the base64url RFC 822 message Gmail expects."""
    if not isinstance(payload, dict):
        raise BadRequest("body must be a JSON object")

    to = _recipients(payload.get("to"), "to", required=True)
    cc = _recipients(payload.get("cc"), "cc", required=False)

    subject = payload.get("subject")
    if not isinstance(subject, str) or not subject.strip():
        raise BadRequest("missing subject")
    if "\r" in subject or "\n" in subject:
        raise BadRequest("subject must be a single line")

    html = payload.get("html")
    if not isinstance(html, str) or not html.strip():
        raise BadRequest("missing html")
    if len(html.encode("utf-8")) > HTML_BYTE_CAP:
        raise BadRequest(f"html larger than {HTML_BYTE_CAP // 1024} KB", 413)

    msg = EmailMessage(policy=SMTP)
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject.strip()
    msg.set_content(html, subtype="html", charset="utf-8")
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def _google_reason(exc: urllib.error.HTTPError) -> str | None:
    """Pull only Google's short error code out of the error body (never the full body)."""
    try:
        err = json.loads(exc.read()).get("error", {})
    except Exception:
        return None
    for detail in err.get("details", []) or []:
        if detail.get("reason"):
            return str(detail["reason"])[:80]
    for item in err.get("errors", []) or []:
        if item.get("reason"):
            return str(item["reason"])[:80]
    return str(err.get("status"))[:80] if err.get("status") else None


def send(raw: str) -> dict:
    token, _scopes = access_token()
    req = urllib.request.Request(
        SEND_URL,
        data=json.dumps({"raw": raw}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=40) as resp:
        return json.loads(resp.read())


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

    def do_POST(self):
        if not self._authorised():
            return self._send(401, {"ok": False, "error": "unauthorized"})

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._send(400, {"ok": False, "error": "bad Content-Length"})
        if length > REQUEST_BYTE_CAP:
            return self._send(413, {"ok": False, "error": "request body larger than 1 MB"})
        if length <= 0:
            return self._send(400, {"ok": False, "error": "empty body"})

        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._send(400, {"ok": False, "error": "body must be valid JSON"})

        try:
            raw = build_raw(payload)
            result = send(raw)
            return self._send(200, {"ok": True, "id": result.get("id"),
                                    "thread_id": result.get("threadId")})
        except BadRequest as exc:
            return self._send(exc.status, {"ok": False, "error": str(exc)})
        except urllib.error.HTTPError as exc:
            out = {"ok": False, "error": f"google api error {exc.code}"}
            reason = _google_reason(exc)
            if reason:
                out["reason"] = reason
            # Pass Google's permission refusal / rate limit through; everything else is a
            # bad gateway (keeps Google 401/400 distinguishable from our own 401/400).
            status = exc.code if exc.code in (403, 429) else 502
            return self._send(status, out)
        except (TimeoutError, socket.timeout, urllib.error.URLError) as exc:
            if not isinstance(exc, urllib.error.URLError) or isinstance(exc.reason, (TimeoutError, socket.timeout)):
                return self._send(504, {"ok": False, "error": "google api timeout"})
            return self._send(502, {"ok": False, "error": "google api unreachable"})
        except ConfigError as exc:
            return self._send(500, {"ok": False, "error": str(exc)})
        except Exception as exc:
            return self._send(500, {"ok": False, "error": type(exc).__name__})

    def do_GET(self):
        self._send(405, {"ok": False, "error": "POST only"})

    do_PUT = do_PATCH = do_DELETE = do_GET

    def log_message(self, fmt, *args):
        pass
