"""
/api/seatalk_check?date=YYYY-MM-DD HH:mm

Returns a fresh SeaTalk summary for the given date as JSON, for use by the
"SeaTalk Check" button on the view page.

Reads the SeaTalk snapshot that seatalk_snapshot.py pushed to Redis at 07:50 SGT,
then calls Claude to produce a prioritised summary.

Response shape:
  {
    "ok": true,
    "summary": "<markdown string>",
    "message_count": 42,
    "generated_at": "14:32 SGT"
  }
  or
  {
    "ok": false,
    "error": "<reason>",
    "message_count": 0
  }

Requires a valid Google session cookie (set by /api/auth).
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(__file__))
from _session import COOKIE_NAME, parse_cookies, verify_cookie
from _seatalk import fetch_seatalk_snapshot, format_seatalk_payload, SEATALK_BRIEF_PROMPT

import anthropic

SGT = ZoneInfo("Asia/Singapore")

_SNAPSHOT_SCRIPT = os.path.join(
    os.path.dirname(__file__), "..", "scripts", "seatalk_snapshot.py"
)


def _try_run_snapshot(date_str: str) -> list[dict] | None:
    """
    Attempt to run seatalk_snapshot.py on-demand (requires local CDP access).
    Returns the messages list if successful, None otherwise.
    """
    script = os.path.abspath(_SNAPSHOT_SCRIPT)
    if not os.path.exists(script):
        return None
    try:
        result = subprocess.run(
            [sys.executable, script, "--hours", "24"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            return None
        # Script pushed to Redis — now fetch it
        return fetch_seatalk_snapshot(date_str)
    except Exception:
        return None


def _generate(messages: list[dict], date_str: str, now_sgt: datetime) -> str:
    """Call Claude with SEATALK_BRIEF_PROMPT and return markdown summary."""
    window = f"SeaTalk snapshot for {date_str} (checked at {now_sgt.strftime('%H:%M SGT')})"
    payload = format_seatalk_payload(messages, window)

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    msg = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1024,
        system=SEATALK_BRIEF_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"Summarise these SeaTalk messages:\n\n{payload}",
            }
        ],
    )
    return msg.content[0].text


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # ── Auth ──────────────────────────────────────────────────────────────
        cookies = parse_cookies(self.headers.get("Cookie", ""))
        if not verify_cookie(cookies.get(COOKIE_NAME, "")):
            self._json(401, {"ok": False, "error": "Unauthorized", "message_count": 0})
            return

        # ── Date param ────────────────────────────────────────────────────────
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        now_sgt = datetime.now(SGT)
        date_str = params.get("date", [now_sgt.strftime("%Y-%m-%d")])[0]

        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            self._json(400, {"ok": False, "error": "Invalid date. Use YYYY-MM-DD.", "message_count": 0})
            return

        # ── Fetch snapshot from Redis (auto-refresh if missing) ───────────────
        messages = fetch_seatalk_snapshot(date_str)
        if messages is None:
            # No pre-scheduled snapshot — try running the script on-demand
            messages = _try_run_snapshot(date_str)
        if messages is None:
            self._json(200, {
                "ok": False,
                "error": (
                    f"No SeaTalk snapshot found for {date_str} and the on-demand "
                    "fetch failed (SeaTalk must be open in Chrome with CDP accessible). "
                    "Run: python3 scripts/seatalk_snapshot.py"
                ),
                "message_count": 0,
            })
            return

        # ── Generate summary ──────────────────────────────────────────────────
        try:
            summary = _generate(messages, date_str, now_sgt)
            self._json(200, {
                "ok": True,
                "summary": summary,
                "message_count": len(messages),
                "generated_at": now_sgt.strftime("%H:%M SGT"),
            })
        except Exception as exc:
            self._json(500, {"ok": False, "error": str(exc), "message_count": len(messages)})

    def _json(self, code: int, body: dict) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        pass
