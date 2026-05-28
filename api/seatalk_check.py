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
from _seatalk import (
    fetch_seatalk_snapshot, fetch_latest_seatalk_snapshot, format_seatalk_payload, SEATALK_BRIEF_PROMPT,
    load_pending_items, format_pending_context,
)

import anthropic

SGT = ZoneInfo("Asia/Singapore")

_SNAPSHOT_SCRIPT = os.path.join(
    os.path.dirname(__file__), "..", "scripts", "seatalk_snapshot.py"
)

# ── Summary cache ─────────────────────────────────────────────────────────────
_SUMMARY_FRESH_S = 3600        # treat cached summary as fresh for 1 hour
_SUMMARY_CACHE_TTL_S = 2 * 3600  # Redis TTL: keep for 2 hours


def _load_summary_cache(date_str: str) -> dict | None:
    """Return cached summary dict if it was generated less than 1 hour ago, else None."""
    try:
        from upstash_redis import Redis
        r = Redis(
            url=os.environ["UPSTASH_REDIS_REST_URL"],
            token=os.environ["UPSTASH_REDIS_REST_TOKEN"],
        )
        raw = r.get(f"seatalk-summary:{date_str}")
        if not raw:
            return None
        data = json.loads(raw) if isinstance(raw, str) else raw
        age_s = datetime.now(SGT).timestamp() - data.get("generated_at_ts", 0)
        return data if age_s <= _SUMMARY_FRESH_S else None
    except Exception:
        return None


def _save_summary_cache(
    date_str: str, summary: str, message_count: int, now_sgt: datetime
) -> None:
    """Persist the generated summary to Redis with a 2-hour TTL."""
    try:
        from upstash_redis import Redis
        r = Redis(
            url=os.environ["UPSTASH_REDIS_REST_URL"],
            token=os.environ["UPSTASH_REDIS_REST_TOKEN"],
        )
        r.set(
            f"seatalk-summary:{date_str}",
            json.dumps({
                "summary": summary,
                "message_count": message_count,
                "generated_at": now_sgt.strftime("%H:%M SGT"),
                "generated_at_ts": now_sgt.timestamp(),
            }),
            ex=_SUMMARY_CACHE_TTL_S,
        )
    except Exception:
        pass


def _try_run_snapshot(date_str: str) -> list[dict] | None:
    """
    Attempt to run seatalk_snapshot.py on-demand (requires local CDP access).
    Returns the messages list if successful, None otherwise.
    Works when the app is running locally (vercel dev); no-ops on Vercel cloud.
    """
    # Prefer the stable local copy that always has the latest script
    local_script = os.path.expanduser("~/.goog-assistant/scripts/seatalk_snapshot.py")
    script = local_script if os.path.exists(local_script) else os.path.abspath(_SNAPSHOT_SCRIPT)
    if not os.path.exists(script):
        return None

    # Pass SEATALK_SKILL_ROOT so the script finds the CDP reader even when the
    # parent process doesn't have it in its environment.
    env = os.environ.copy()
    drive = os.path.expanduser(
        "~/Library/CloudStorage/GoogleDrive-shuning2016@gmail.com"
        "/My Drive/My Projects/Working Efficiency/use-seatalk"
    )
    env.setdefault("SEATALK_SKILL_ROOT", drive)

    try:
        result = subprocess.run(
            [sys.executable, script, "--hours", "24"],
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
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

    # Inject previously-tracked pending items so Claude can mark them resolved or carry them forward
    pending_ctx = format_pending_context(load_pending_items())
    user_content = f"Summarise these SeaTalk messages:\n\n{payload}"
    if pending_ctx:
        user_content += f"\n\n{pending_ctx}"

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    msg = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1024,
        system=SEATALK_BRIEF_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    return msg.content[0].text


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # ── Auth ──────────────────────────────────────────────────────────────
        cookies = parse_cookies(self.headers.get("Cookie", ""))
        if not verify_cookie(cookies.get(COOKIE_NAME, "")):
            self._json(401, {"ok": False, "error": "Unauthorized", "message_count": 0})
            return

        # ── Date + force params ───────────────────────────────────────────────
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        now_sgt = datetime.now(SGT)
        date_str = params.get("date", [now_sgt.strftime("%Y-%m-%d")])[0]
        force = params.get("force", ["0"])[0] == "1"

        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            self._json(400, {"ok": False, "error": "Invalid date. Use YYYY-MM-DD.", "message_count": 0})
            return

        # ── Return cached summary if fresh (< 1 hour) and not forced ─────────
        if not force:
            cached = _load_summary_cache(date_str)
            if cached:
                age_s = int(now_sgt.timestamp() - cached["generated_at_ts"])
                age_min = age_s // 60
                self._json(200, {
                    "ok": True,
                    "summary": cached["summary"],
                    "message_count": cached["message_count"],
                    "generated_at": cached["generated_at"],
                    "cached": True,
                    "age_min": age_min,
                })
                return

        # ── Fetch snapshot from Redis (auto-refresh if missing) ───────────────
        snapshot_date = date_str
        messages = fetch_seatalk_snapshot(date_str)
        if messages is None:
            # No pre-scheduled snapshot — try running the script on-demand
            messages = _try_run_snapshot(date_str)
        if messages is None:
            # Fall back to the most recent available snapshot (up to 7 days back)
            messages, snapshot_date = fetch_latest_seatalk_snapshot(date_str)
        if messages is None:
            self._json(200, {
                "ok": False,
                "error": f"No SeaTalk snapshot found for {date_str}.",
                "hint": "run_snapshot",
                "message_count": 0,
            })
            return

        # ── Generate summary and cache it ─────────────────────────────────────
        try:
            summary = _generate(messages, snapshot_date, now_sgt)
            _save_summary_cache(date_str, summary, len(messages), now_sgt)
            self._json(200, {
                "ok": True,
                "summary": summary,
                "message_count": len(messages),
                "generated_at": now_sgt.strftime("%H:%M SGT"),
                "cached": False,
                "age_min": 0,
                "snapshot_date": snapshot_date,
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
