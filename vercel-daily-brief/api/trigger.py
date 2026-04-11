"""
/api/trigger?token=SECRET  —  Manually trigger the daily briefing.

Verifies ?token= against TRIGGER_SECRET env var, runs the same generation
pipeline as /api/cron, stores to Redis, sends the email, then redirects
to /api/view so the user immediately sees the rendered result.

Bookmark: https://YOUR-APP.vercel.app/api/trigger?token=YOUR_SECRET
"""

from http.server import BaseHTTPRequestHandler
import json
import os
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

from googleapiclient.discovery import build

from cron import (
    SGT,
    _google_creds,
    fetch_calendar,
    fetch_drive,
    fetch_gmail,
    generate_briefing,
    send_email,
    store,
)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        token = params.get("token", [""])[0]

        secret = os.environ.get("TRIGGER_SECRET", "")
        if not secret or token != secret:
            self._respond(401, "text/plain", b"Unauthorized")
            return

        try:
            now_sgt = datetime.now(SGT)
            today_str = now_sgt.strftime("%Y-%m-%d")
            since = now_sgt - timedelta(hours=24)
            window = (
                f"{since.strftime('%Y-%m-%d %H:%M SGT')} "
                f"→ {now_sgt.strftime('%Y-%m-%d %H:%M SGT')}"
            )

            creds = _google_creds()
            gmail_svc = build("gmail", "v1", credentials=creds)
            cal_svc = build("calendar", "v3", credentials=creds)
            drive_svc = build("drive", "v3", credentials=creds)

            emails = fetch_gmail(gmail_svc, since)
            events = fetch_calendar(cal_svc, now_sgt)
            drive_files = fetch_drive(drive_svc, since)

            briefing = generate_briefing(emails, events, drive_files, today_str, window)
            store(today_str, briefing)

            base = os.environ.get("VERCEL_URL", "localhost:3000")
            if not base.startswith("http"):
                base = f"https://{base}"
            view_url = f"{base}/api/view?date={today_str}"

            send_email(briefing, today_str, view_url)

            # Redirect straight to the rendered briefing
            self.send_response(302)
            self.send_header("Location", view_url)
            self.send_header("Content-Length", "0")
            self.end_headers()

        except Exception as exc:
            msg = f"Briefing generation failed: {exc}".encode()
            self._respond(500, "text/plain", msg)

    def _respond(self, code: int, content_type: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass
