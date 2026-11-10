"""
Shared plumbing for the read-only Google proxies (/api/gmail, /api/calendar, /api/drive).

The Google OAuth secrets (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET /
GOOGLE_REFRESH_TOKEN) never leave Vercel. Callers authenticate with
  Authorization: Bearer <GMAIL_PROXY_TOKEN>
and only ever receive API results.
"""

from __future__ import annotations

import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse


class BadRequest(Exception):
    """Raised by op handlers for invalid caller input → HTTP 400 (or a given 4xx)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class ConfigError(Exception):
    """A required Vercel env var is missing → HTTP 500."""


def _env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise ConfigError(f"missing env var {name}")
    return value


def access_token() -> tuple[str, list[str]]:
    """Exchange the refresh token; return (access_token, granted scopes)."""
    data = urllib.parse.urlencode({
        "client_id": _env("GOOGLE_CLIENT_ID"),
        "client_secret": _env("GOOGLE_CLIENT_SECRET"),
        "refresh_token": _env("GOOGLE_REFRESH_TOKEN"),
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token", data=data, method="POST"
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        payload = json.loads(resp.read())
    return payload["access_token"], payload.get("scope", "").split()


def google_get(token: str, url: str, params: dict | None = None, raw: bool = False):
    """GET a Google API URL. Returns parsed JSON, or (bytes, content-type) when raw=True."""
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        body = resp.read()
        if raw:
            return body, resp.headers.get("Content-Type", "")
        return json.loads(body)


def int_param(params: dict, name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(params.get(name, [str(default)])[0])
    except ValueError:
        raise BadRequest(f"{name} must be an integer")
    return max(lo, min(value, hi))


def str_param(params: dict, name: str, required: bool = False) -> str:
    value = params.get(name, [""])[0].strip()
    if required and not value:
        raise BadRequest(f"missing {name}")
    return value


class ReadOnlyHandler(BaseHTTPRequestHandler):
    """Subclasses set OPS = {"op_name": fn(token, scopes, params) -> dict} and DEFAULT_OP."""

    OPS: dict = {}
    DEFAULT_OP = ""

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
        op = params.get("op", [self.DEFAULT_OP])[0]
        fn = self.OPS.get(op)
        if fn is None:
            return self._send(400, {"ok": False, "error": f"unknown op: {op}"})

        try:
            token, scopes = access_token()
            return self._send(200, fn(token, scopes, params))
        except BadRequest as exc:
            return self._send(exc.status, {"ok": False, "error": str(exc)})
        except urllib.error.HTTPError as exc:
            # Never echo Google's response body — it can include request details.
            return self._send(502, {"ok": False, "error": f"google api error {exc.code}"})
        except ConfigError as exc:
            return self._send(500, {"ok": False, "error": str(exc)})
        except Exception as exc:
            return self._send(500, {"ok": False, "error": type(exc).__name__})

    def do_POST(self):
        self._send(405, {"ok": False, "error": "read-only: GET only"})

    do_PUT = do_PATCH = do_DELETE = do_POST

    def log_message(self, fmt, *args):
        pass
