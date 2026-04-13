"""
/api/auth  —  Google OAuth 2.0 login / callback handler.

Step 1 – initiate login:
  GET /api/auth?next=/api/view?date=2026-04-13
  → redirects browser to Google consent screen

Step 2 – OAuth callback (Google calls this automatically):
  GET /api/auth?code=CODE&state=NEXT_URL
  → exchanges code for token, verifies email is in the allow-list,
    sets a signed session cookie, then redirects to NEXT_URL.

Required Vercel environment variables:
  GOOGLE_CLIENT_ID      — OAuth 2.0 Web Client ID
  GOOGLE_CLIENT_SECRET  — OAuth 2.0 Web Client Secret
  SESSION_SECRET        — random string used to sign session cookies
"""

import json
import os
import sys
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(__file__))
from _session import ALLOWED_EMAILS, COOKIE_MAX_AGE, COOKIE_NAME, make_cookie

REDIRECT_URI = "https://goo-g-assistant.vercel.app/api/auth"


def _google_auth_url(state: str) -> str:
    params = {
        "client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)


def _exchange_code(code: str) -> dict:
    data = urllib.parse.urlencode({
        "code": code,
        "client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
        "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token", data=data, method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def _get_email(access_token: str) -> str:
    req = urllib.request.Request(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read()).get("email", "")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        code = params.get("code", [""])[0]
        state = params.get("state", ["/"])[0]       # carries the "next" URL
        next_url = params.get("next", [""])[0]

        if code:
            # ── Step 2: callback from Google ──────────────────────────────
            try:
                tokens = _exchange_code(code)
                email = _get_email(tokens.get("access_token", ""))
            except Exception as exc:
                self._write(500, "text/plain", f"OAuth error: {exc}".encode())
                return

            if email not in ALLOWED_EMAILS:
                body = (
                    f"<h2>Access denied</h2>"
                    f"<p><b>{email}</b> is not authorised to view this page.</p>"
                    f"<p>Authorised accounts: shuning.wang@shopee.com, shuning2016@gmail.com</p>"
                ).encode()
                self._write(403, "text/html; charset=utf-8", body)
                return

            cookie_val = make_cookie(email)
            redirect_to = state if state.startswith("/") else "/"
            self.send_response(302)
            self.send_header("Location", redirect_to)
            self.send_header(
                "Set-Cookie",
                (
                    f"{COOKIE_NAME}={cookie_val}; Path=/; HttpOnly; Secure; "
                    f"SameSite=Lax; Max-Age={COOKIE_MAX_AGE}"
                ),
            )
            self.send_header("Content-Length", "0")
            self.end_headers()

        else:
            # ── Step 1: start OAuth flow ───────────────────────────────────
            target = next_url or "/"
            self.send_response(302)
            self.send_header("Location", _google_auth_url(state=target))
            self.send_header("Content-Length", "0")
            self.end_headers()

    def _write(self, code: int, content_type: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass
