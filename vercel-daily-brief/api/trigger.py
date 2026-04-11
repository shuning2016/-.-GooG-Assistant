"""
/api/trigger?token=SECRET  —  Manually trigger the daily briefing.

Verifies ?token= against TRIGGER_SECRET env var, runs the full pipeline,
then redirects to /api/view so you immediately see the rendered result.

Bookmark: https://YOUR-APP.vercel.app/api/trigger?token=YOUR_SECRET
"""

import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from _briefing import run_briefing


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        token = params.get("token", [""])[0]

        secret = os.environ.get("TRIGGER_SECRET", "")
        if not secret or token != secret:
            body = b"Unauthorized"
            self.send_response(401)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        try:
            _today_str, view_url, _briefing = run_briefing()
            self.send_response(302)
            self.send_header("Location", view_url)
            self.send_header("Content-Length", "0")
            self.end_headers()
        except Exception as exc:
            body = f"Briefing generation failed: {exc}".encode()
            self.send_response(500)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass
