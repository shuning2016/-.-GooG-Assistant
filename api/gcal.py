"""
/api/gcal  —  Read-only Google Calendar proxy for local tools.

Auth: header  Authorization: Bearer <GMAIL_PROXY_TOKEN>   (see _google_ro.py)

Endpoints (GET only):
  /api/gcal?op=calendars
      → {"ok": true, "calendars": [{id, summary, primary, access_role, time_zone}]}
  /api/gcal?op=events&from=2026-11-10&to=2026-11-12&q=budget&calendar=primary&max=100
      → {"ok": true, "count": n, "events": [{id, summary, start, end, all_day, my_response, ...}]}
      from/to accept YYYY-MM-DD (Singapore midnight) or full RFC3339; default = today SGT.
  /api/gcal?op=get&id=<event id>&calendar=primary
      → {"ok": true, "event": {...}}

Full reference: docs/google-api.md
"""

from __future__ import annotations

import os
import sys
import urllib.parse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))
from _google_ro import BadRequest, ReadOnlyHandler, google_get, int_param, str_param

CAL_API = "https://www.googleapis.com/calendar/v3"
SGT = ZoneInfo("Asia/Singapore")
MAX_RESULTS_CAP = 250
DESCRIPTION_CHAR_CAP = 5_000


def _parse_time(value: str, name: str) -> datetime:
    """Accept YYYY-MM-DD (SGT midnight) or an ISO-8601 datetime (naive = SGT)."""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise BadRequest(f"{name} must be YYYY-MM-DD or ISO-8601 datetime")
    return dt if dt.tzinfo else dt.replace(tzinfo=SGT)


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _event(ev: dict) -> dict:
    start, end = ev.get("start", {}), ev.get("end", {})
    attendees = ev.get("attendees", [])
    me = next((a for a in attendees if a.get("self")), None)
    description = ev.get("description") or ""
    return {
        "id": ev.get("id"),
        "summary": ev.get("summary"),
        "start": start.get("dateTime") or start.get("date"),
        "end": end.get("dateTime") or end.get("date"),
        "all_day": "date" in start,
        "status": ev.get("status"),
        "location": ev.get("location"),
        "organizer": (ev.get("organizer") or {}).get("email"),
        # None when I'm the organizer of an event with no attendee list.
        "my_response": me.get("responseStatus") if me else None,
        "attendee_count": len(attendees),
        "attendees": [
            {"email": a.get("email"), "name": a.get("displayName"),
             "response": a.get("responseStatus"), "optional": a.get("optional", False)}
            for a in attendees
        ],
        "meet_link": ev.get("hangoutLink"),
        "description": description[:DESCRIPTION_CHAR_CAP],
        "description_truncated": len(description) > DESCRIPTION_CHAR_CAP,
        "attachments": [
            {"title": a.get("title"), "url": a.get("fileUrl"), "mime_type": a.get("mimeType")}
            for a in ev.get("attachments", [])
        ],
        "recurring_event_id": ev.get("recurringEventId"),
        "html_link": ev.get("htmlLink"),
    }


def _calendars(token: str, scopes: list[str], params: dict) -> dict:
    data = google_get(token, f"{CAL_API}/users/me/calendarList", {"maxResults": 250})
    return {"ok": True, "calendars": [
        {"id": c.get("id"), "summary": c.get("summary"), "primary": c.get("primary", False),
         "access_role": c.get("accessRole"), "time_zone": c.get("timeZone")}
        for c in data.get("items", [])
    ]}


def _events(token: str, scopes: list[str], params: dict) -> dict:
    calendar = str_param(params, "calendar") or "primary"
    raw_from, raw_to = str_param(params, "from"), str_param(params, "to")
    today = datetime.now(SGT).replace(hour=0, minute=0, second=0, microsecond=0)
    time_min = _parse_time(raw_from, "from") if raw_from else today
    time_max = _parse_time(raw_to, "to") if raw_to else time_min + timedelta(days=1)
    if time_max <= time_min:
        raise BadRequest("to must be after from")
    query = {
        "timeMin": time_min.isoformat(),
        "timeMax": time_max.isoformat(),
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": int_param(params, "max", 100, 1, MAX_RESULTS_CAP),
        "timeZone": "Asia/Singapore",
    }
    q = str_param(params, "q")
    if q:
        query["q"] = q
    data = google_get(token, f"{CAL_API}/calendars/{_quote(calendar)}/events", query)
    events = [_event(ev) for ev in data.get("items", [])]
    return {
        "ok": True, "calendar": calendar,
        "from": query["timeMin"], "to": query["timeMax"],
        "count": len(events), "events": events,
    }


def _get(token: str, scopes: list[str], params: dict) -> dict:
    calendar = str_param(params, "calendar") or "primary"
    event_id = str_param(params, "id", required=True)
    ev = google_get(token, f"{CAL_API}/calendars/{_quote(calendar)}/events/{_quote(event_id)}",
                    {"timeZone": "Asia/Singapore"})
    return {"ok": True, "event": _event(ev)}


class handler(ReadOnlyHandler):
    OPS = {"calendars": _calendars, "events": _events, "get": _get}
    DEFAULT_OP = "events"
