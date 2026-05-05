"""
/api/cron  —  Daily-brief cron endpoint for Vercel.

Schedule (vercel.json): "0 0 * * *"  →  0:00 UTC = 8:00 AM Asia/Singapore.
Vercel calls this endpoint automatically and passes Authorization: Bearer <CRON_SECRET>.
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(__file__))
from _briefing import run_briefing, DuplicateRunError


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def _handle(self):
        cron_secret = os.environ.get("CRON_SECRET", "")
        if cron_secret:
            auth = self.headers.get("Authorization", "")
            if auth != f"Bearer {cron_secret}":
                self._respond(401, {"error": "Unauthorized"})
                return

        try:
            today_str, view_url, briefing = run_briefing()
            self._respond(200, {"status": "ok", "date": today_str, "view_url": view_url})
        except DuplicateRunError as exc:
            self._respond(200, {"status": "skipped", "reason": str(exc)})
        except Exception as exc:
            self._respond(500, {"error": str(exc)})

    def _respond(self, code: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        pass
