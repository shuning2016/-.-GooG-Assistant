"""
/api/action_items  —  Read or update open action items stored in Redis.

GET  /api/action_items          → JSON list of all items (done and open)
POST /api/action_items?id=ID    → Mark item with given id as done; returns updated item
PUT  /api/action_items          → Create a new manual action item; returns created item
"""

import json
import os
import sys
import time
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from upstash_redis import Redis

sys.path.insert(0, os.path.dirname(__file__))
from _session import COOKIE_NAME, parse_cookies, verify_cookie

REDIS_KEY = "open-action-items"


def _redis() -> Redis:
    return Redis(
        url=os.environ["UPSTASH_REDIS_REST_URL"],
        token=os.environ["UPSTASH_REDIS_REST_TOKEN"],
    )


def _load_items(r: Redis) -> list[dict]:
    raw = r.get(REDIS_KEY)
    if not raw:
        return []
    data = json.loads(raw) if isinstance(raw, str) else raw
    return data if isinstance(data, list) else []


def _save_items(r: Redis, items: list[dict]) -> None:
    r.set(REDIS_KEY, json.dumps(items), ex=30 * 24 * 3600)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not self._auth():
            return
        try:
            r = _redis()
            items = _load_items(r)
            self._json(200, items)
        except Exception as exc:
            self._json(500, {"error": str(exc)})

    def do_POST(self):
        if not self._auth():
            return
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        item_id = params.get("id", [None])[0]
        if not item_id:
            self._json(400, {"error": "Missing id parameter"})
            return
        try:
            r = _redis()
            items = _load_items(r)
            updated = None
            for item in items:
                if item.get("id") == item_id:
                    item["done"] = True
                    updated = item
                    break
            if updated is None:
                self._json(404, {"error": f"Item not found: {item_id}"})
                return
            _save_items(r, items)
            self._json(200, {"ok": True, "item": updated})
        except Exception as exc:
            self._json(500, {"error": str(exc)})

    def do_PUT(self):
        """Create a new manual action item from JSON body."""
        if not self._auth():
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            self._json(400, {"error": "Invalid JSON body"})
            return

        action = str(data.get("action") or "").strip()
        if not action:
            self._json(400, {"error": "action is required"})
            return

        item_id = "manual-" + str(int(time.time() * 1000))
        sgt_date = datetime.now(ZoneInfo("Asia/Singapore")).strftime("%Y-%m-%d")
        source_raw = str(data.get("source") or "").strip()
        eta_raw = str(data.get("eta") or "").strip() or None
        urgency_raw = str(data.get("urgency") or "").strip() or None

        new_item = {
            "id": item_id,
            "source": source_raw if source_raw else "Manual entry",
            "source_type": "manual",
            "date_identified": sgt_date,
            "action": action,
            "eta": eta_raw,
            "urgency": urgency_raw,
            "done": False,
        }

        try:
            r = _redis()
            items = _load_items(r)
            items.append(new_item)
            _save_items(r, items)
            self._json(201, {"ok": True, "item": new_item})
        except Exception as exc:
            self._json(500, {"error": str(exc)})

    def _auth(self) -> bool:
        cookie_header = self.headers.get("Cookie", "")
        cookies = parse_cookies(cookie_header)
        session_email = verify_cookie(cookies.get(COOKIE_NAME, ""))
        if not session_email:
            next_url = urllib.parse.quote(self.path, safe="")
            self.send_response(302)
            self.send_header("Location", f"/api/auth?next={next_url}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False
        return True

    def _json(self, code: int, body) -> None:
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        pass
