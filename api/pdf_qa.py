"""
/api/pdf_qa  —  Read and manage PDF pre-read Q&A stored in Redis.

GET  /api/pdf_qa?date=YYYY-MM-DD          → JSON list of Q&A items for that date
     /api/pdf_qa                           → uses today's SGT date
PUT  /api/pdf_qa                           → Add a manual question (JSON body)
PATCH /api/pdf_qa?id=ID                    → Edit a question (JSON body: {question, reason, reason_detail})
DELETE /api/pdf_qa?id=ID                   → Delete a question (JSON body: {reason, reason_detail})

Redis keys:
  pdf-qa:{YYYY-MM-DD}   → list of Q&A items for that date (30-day TTL)
  pdf-qa-log            → append-only list of audit log entries
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

SGT = ZoneInfo("Asia/Singapore")
LOG_KEY = "pdf-qa-log"


def _redis() -> Redis:
    return Redis(
        url=os.environ["UPSTASH_REDIS_REST_URL"],
        token=os.environ["UPSTASH_REDIS_REST_TOKEN"],
    )


def _today_sgt() -> str:
    return datetime.now(SGT).strftime("%Y-%m-%d")


def _qa_key(date: str) -> str:
    return f"pdf-qa:{date}"


def _load_items(r: Redis, date: str) -> list[dict]:
    raw = r.get(_qa_key(date))
    if not raw:
        return []
    data = json.loads(raw) if isinstance(raw, str) else raw
    return data if isinstance(data, list) else []


def _save_items(r: Redis, date: str, items: list[dict]) -> None:
    r.set(_qa_key(date), json.dumps(items), ex=30 * 24 * 3600)


def _append_log(r: Redis, entry: dict) -> None:
    """Append an audit log entry to the running log."""
    try:
        raw = r.get(LOG_KEY)
        log: list[dict] = json.loads(raw) if isinstance(raw, str) else (raw or [])
        if not isinstance(log, list):
            log = []
    except Exception:
        log = []
    log.append(entry)
    r.set(LOG_KEY, json.dumps(log), ex=365 * 24 * 3600)  # 1-year TTL


REASON_LABELS = {
    "not_relevant": "Not relevant to meeting",
    "answered_before": "Already answered in a previous meeting",
    "too_trivial": "Too trivial to ask",
    "other": "Other",
}


class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        if not self._auth():
            return
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        date = params.get("date", [_today_sgt()])[0]
        try:
            r = _redis()
            items = _load_items(r, date)
            self._json(200, {"date": date, "items": items})
        except Exception as exc:
            self._json(500, {"error": str(exc)})

    def do_PUT(self):
        """Add a new manual question."""
        if not self._auth():
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            self._json(400, {"error": "Invalid JSON body"})
            return

        question = str(data.get("question") or "").strip()
        pdf_name = str(data.get("pdf_name") or "").strip()
        if not question:
            self._json(400, {"error": "question is required"})
            return
        if not pdf_name:
            self._json(400, {"error": "pdf_name is required"})
            return

        date = str(data.get("date") or _today_sgt()).strip()
        item_id = f"manual-{int(time.time() * 1000)}"
        new_item = {
            "id": item_id,
            "pdf_name": pdf_name,
            "question": question,
            "slide_ref": str(data.get("slide_ref") or "").strip() or None,
            "type": "manual",
            "date": date,
            "created_at": datetime.now(SGT).isoformat(),
        }

        try:
            r = _redis()
            items = _load_items(r, date)
            items.append(new_item)
            _save_items(r, date, items)
            _append_log(r, {
                "action": "add",
                "id": item_id,
                "pdf_name": pdf_name,
                "question": question,
                "date": date,
                "ts": datetime.now(SGT).isoformat(),
            })
            self._json(201, {"ok": True, "item": new_item})
        except Exception as exc:
            self._json(500, {"error": str(exc)})

    def do_PATCH(self):
        """Edit a question, recording the reason."""
        if not self._auth():
            return
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        item_id = params.get("id", [None])[0]
        if not item_id:
            self._json(400, {"error": "Missing id parameter"})
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            self._json(400, {"error": "Invalid JSON body"})
            return

        new_question = str(data.get("question") or "").strip()
        reason = str(data.get("reason") or "other").strip()
        reason_detail = str(data.get("reason_detail") or "").strip()
        date = str(data.get("date") or _today_sgt()).strip()

        if not new_question:
            self._json(400, {"error": "question is required"})
            return

        try:
            r = _redis()
            items = _load_items(r, date)
            updated = None
            for item in items:
                if item.get("id") == item_id:
                    old_question = item.get("question", "")
                    item["question"] = new_question
                    item["last_edited_at"] = datetime.now(SGT).isoformat()
                    item["edit_reason"] = reason
                    item["edit_reason_detail"] = reason_detail
                    updated = item
                    _append_log(r, {
                        "action": "edit",
                        "id": item_id,
                        "pdf_name": item.get("pdf_name", ""),
                        "old_question": old_question,
                        "new_question": new_question,
                        "reason": reason,
                        "reason_label": REASON_LABELS.get(reason, reason),
                        "reason_detail": reason_detail,
                        "date": date,
                        "ts": datetime.now(SGT).isoformat(),
                    })
                    break
            if updated is None:
                self._json(404, {"error": f"Item not found: {item_id}"})
                return
            _save_items(r, date, items)
            self._json(200, {"ok": True, "item": updated})
        except Exception as exc:
            self._json(500, {"error": str(exc)})

    def do_DELETE(self):
        """Delete a question, recording the reason."""
        if not self._auth():
            return
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        item_id = params.get("id", [None])[0]
        if not item_id:
            self._json(400, {"error": "Missing id parameter"})
            return

        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b"{}"
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            data = {}

        reason = str(data.get("reason") or "other").strip()
        reason_detail = str(data.get("reason_detail") or "").strip()
        date = str(data.get("date") or _today_sgt()).strip()

        try:
            r = _redis()
            items = _load_items(r, date)
            deleted = None
            new_items = []
            for item in items:
                if item.get("id") == item_id:
                    deleted = item
                else:
                    new_items.append(item)
            if deleted is None:
                self._json(404, {"error": f"Item not found: {item_id}"})
                return
            _save_items(r, date, new_items)
            _append_log(r, {
                "action": "delete",
                "id": item_id,
                "pdf_name": deleted.get("pdf_name", ""),
                "question": deleted.get("question", ""),
                "reason": reason,
                "reason_label": REASON_LABELS.get(reason, reason),
                "reason_detail": reason_detail,
                "date": date,
                "ts": datetime.now(SGT).isoformat(),
            })
            self._json(200, {"ok": True, "deleted": deleted})
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
