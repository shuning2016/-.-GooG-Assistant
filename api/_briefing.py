"""
Shared briefing logic used by both /api/cron and /api/trigger.

Keeping this in a private module (_briefing.py) avoids cross-function
imports between sibling api/ files, which are unreliable in Vercel's
Python serverless runtime.
"""

import base64
import json
import os
import re
import sys
import smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

import anthropic
import markdown as md_lib
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from upstash_redis import Redis

sys.path.insert(0, os.path.dirname(__file__))
from _seatalk import fetch_seatalk_snapshot, format_seatalk_payload
from _pdf_reader import generate_pdf_qa, save_pdf_qa, select_best_pdf

SGT = ZoneInfo("Asia/Singapore")
RECIPIENT = "Shuning.wang@shopee.com"
DEDUP_WINDOW_SECONDS = 1800  # 30 minutes


class DuplicateRunError(RuntimeError):
    pass
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]


# ─── Google helpers ────────────────────────────────────────────────────────────

def google_creds() -> Credentials:
    creds = Credentials(
        token=None,
        refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"],
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=GOOGLE_SCOPES,
    )
    creds.refresh(Request())
    return creds


_KEY_DOMAIN_TERMS = (
    "swarm", "osp", "sip", "fp&a", "fpa", "budget", "bpm",
    "cncb", "cnsip", "cnls", "sls", "spx", "fbs",
)


def _is_key_domain(subject: str, snippet: str) -> bool:
    """Return True if subject or snippet mentions a key domain."""
    text = (subject + " " + snippet).lower()
    return any(term in text for term in _KEY_DOMAIN_TERMS)


_PREREREAD_MARKERS = ("[pre-read]", "[pre read]", "[preread]", "pre-read:", "pre read:")


def _is_prereread(subject: str) -> bool:
    """Return True if this is a pre-meeting pre-read/agenda email (NOT a reply to one).

    Reply emails (RE:/FWD: prefix) are NOT considered pre-reads — they may be
    post-meeting recap emails sent in the same thread and can contain action items.
    """
    s = subject.strip()
    # Replies and forwards are NOT pre-reads
    if re.match(r"^(re:|fwd?:)\s*", s, re.IGNORECASE):
        return False
    s_lower = s.lower()
    return any(s_lower.startswith(m) for m in _PREREREAD_MARKERS)


def _extract_plain_body(payload: dict, max_chars: int = 3000) -> str:
    """Recursively extract plain-text body from a Gmail message payload."""
    import base64

    mime = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data", "")

    if body_data:
        raw = base64.urlsafe_b64decode(body_data + "==").decode("utf-8", errors="replace")
        if "plain" in mime:
            return raw[:max_chars]
        if "html" in mime:
            import re
            text = re.sub(r"<[^>]+>", " ", raw)
            text = re.sub(r"\s{2,}", " ", text).strip()
            return text[:max_chars]

    parts = payload.get("parts", [])
    # Prefer plain text over HTML
    for p in parts:
        if "plain" in p.get("mimeType", ""):
            result = _extract_plain_body(p, max_chars)
            if result:
                return result
    for p in parts:
        result = _extract_plain_body(p, max_chars)
        if result:
            return result
    return ""


_CLAUDE_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


def _extract_images(
    payload: dict,
    service,
    msg_id: str,
    max_images: int = 3,
    max_bytes: int = 1_572_864,  # 1.5 MB per image
) -> list[dict]:
    """Download image attachments from a Gmail message payload.

    Returns up to max_images dicts: {data, media_type, filename}.
    Only Claude-supported image types (PNG, JPEG, GIF, WEBP).
    Images larger than max_bytes are skipped.
    """
    results: list[dict] = []

    def _walk(parts: list) -> None:
        for part in parts:
            if len(results) >= max_images:
                return
            mime = part.get("mimeType", "")
            if mime in _CLAUDE_IMAGE_TYPES:
                att_id = part.get("body", {}).get("attachmentId")
                if att_id:
                    try:
                        att = (
                            service.users()
                            .messages()
                            .attachments()
                            .get(userId="me", messageId=msg_id, id=att_id)
                            .execute()
                        )
                        raw_data = att.get("data", "")
                        decoded = base64.urlsafe_b64decode(raw_data + "==")
                        if len(decoded) > max_bytes:
                            continue  # skip oversized images
                        # Claude requires standard base64, not urlsafe
                        data_b64 = base64.b64encode(decoded).decode("utf-8")
                        results.append(
                            {
                                "data": data_b64,
                                "media_type": mime,
                                "filename": part.get("filename", "image"),
                            }
                        )
                    except Exception:
                        pass
            sub = part.get("parts", [])
            if sub:
                _walk(sub)

    _walk(payload.get("parts", []))
    return results


def _extract_pdf_attachments(payload: dict) -> list[dict]:
    """Return metadata for PDF attachments found in a Gmail message payload.

    Returns a list of dicts: {filename, attachment_id, size}.
    Does NOT download the attachment bytes — use the attachment_id later.
    """
    results: list[dict] = []

    def _walk(parts: list) -> None:
        for part in parts:
            mime = part.get("mimeType", "")
            if mime == "application/pdf":
                att_id = part.get("body", {}).get("attachmentId")
                if att_id:
                    results.append({
                        "filename": part.get("filename", "attachment.pdf"),
                        "attachment_id": att_id,
                        "size": part.get("body", {}).get("size", 0),
                    })
            sub = part.get("parts", [])
            if sub:
                _walk(sub)

    _walk(payload.get("parts", []))
    return results


def fetch_gmail(service, since: datetime) -> list[dict]:
    """Return up to 40 message summaries since `since`.

    For key-domain emails (Swarm/OSP/SIP/FP&A/Budget/BPM and related terms),
    fetch the full plain-text body so action items in tables are visible to Claude.
    All other emails get headers + snippet only.
    """
    after_ts = int(since.timestamp())
    q = f"after:{after_ts} -category:promotions -category:social -category:updates"
    result = service.users().messages().list(userId="me", q=q, maxResults=50).execute()
    messages = result.get("messages", [])[:40]

    out = []
    for m in messages:
        # First pass: metadata only (fast)
        meta = service.users().messages().get(
            userId="me",
            id=m["id"],
            format="metadata",
            metadataHeaders=["From", "To", "Cc", "Subject", "Date"],
        ).execute()
        h = {hdr["name"]: hdr["value"] for hdr in meta.get("payload", {}).get("headers", [])}
        subject = h.get("Subject", "")
        snippet = meta.get("snippet", "")

        is_prereread = _is_prereread(subject)
        body_text = ""
        images: list[dict] = []
        pdf_attachments: list[dict] = []
        if _is_key_domain(subject, snippet) or is_prereread:
            # Second pass: full body + image/PDF attachments for key-domain and pre-read emails
            try:
                full = service.users().messages().get(
                    userId="me",
                    id=m["id"],
                    format="full",
                ).execute()
                full_payload = full.get("payload", {})
                body_text = _extract_plain_body(full_payload)
                images = _extract_images(full_payload, service, m["id"])
                if is_prereread:
                    # Extract PDF metadata for pre-read emails (for the Q&A tab)
                    pdf_attachments = _extract_pdf_attachments(full_payload)
            except Exception:
                pass  # Fall back to snippet if full fetch fails

        out.append(
            {
                "id": m["id"],
                "thread_id": meta.get("threadId"),
                "from": h.get("From", ""),
                "to": h.get("To", ""),
                "cc": h.get("Cc", ""),
                "subject": subject,
                "date": h.get("Date", ""),
                "snippet": snippet,
                "is_prereread": is_prereread,
                "body": body_text,
                "images": images,  # image attachments for Claude Vision (key-domain only)
                "pdf_attachments": pdf_attachments,  # PDF metadata for pre-read Q&A
            }
        )
    return out


def fetch_calendar(service, now_sgt: datetime) -> list[dict]:
    """Return events from now through end of tomorrow (SGT)."""
    tomorrow_end = (now_sgt + timedelta(days=1)).replace(
        hour=23, minute=59, second=59, microsecond=0
    )
    result = service.events().list(
        calendarId="primary",
        timeMin=now_sgt.isoformat(),
        timeMax=tomorrow_end.isoformat(),
        singleEvents=True,
        orderBy="startTime",
        maxResults=25,
    ).execute()
    events = []
    for ev in result.get("items", []):
        events.append(
            {
                "summary": ev.get("summary", "No title"),
                "start": ev["start"].get("dateTime", ev["start"].get("date")),
                "end": ev["end"].get("dateTime", ev["end"].get("date")),
                "description": ev.get("description", ""),
                "location": ev.get("location", ""),
                "organizer": ev.get("organizer", {}).get("email", ""),
                "attendees": [
                    {
                        "email": a.get("email"),
                        "self": a.get("self", False),
                        "organizer": a.get("organizer", False),
                        "response": a.get("responseStatus"),
                    }
                    for a in ev.get("attendees", [])
                ],
                "htmlLink": ev.get("htmlLink", ""),
            }
        )
    return events



def _load_all_action_items(r: Redis) -> list[dict]:
    """Read ALL action items from Redis (including done). Returns empty list if none."""
    try:
        raw = r.get("open-action-items")
        if not raw:
            return []
        data = json.loads(raw) if isinstance(raw, str) else raw
        return data if isinstance(data, list) else []
    except Exception:
        return []


def fetch_open_action_items(r: Redis) -> list[dict]:
    """Read open (not done) action items from Redis."""
    return [item for item in _load_all_action_items(r) if not item.get("done", False)]


_NEW_ITEMS_RE = re.compile(
    r"---NEW_ACTION_ITEMS_START---\s*(.*?)\s*---NEW_ACTION_ITEMS_END---",
    re.DOTALL,
)


def _parse_and_save_new_action_items(r: Redis, briefing_raw: str) -> str:
    """Extract any NEW_ACTION_ITEMS block from the briefing, merge into Redis,
    and return the briefing text with the block stripped out."""
    match = _NEW_ITEMS_RE.search(briefing_raw)
    if not match:
        return briefing_raw

    briefing_clean = _NEW_ITEMS_RE.sub("", briefing_raw).strip()

    try:
        new_items: list[dict] = json.loads(match.group(1))
    except Exception:
        return briefing_clean  # malformed JSON — strip block but don't crash

    if not new_items:
        return briefing_clean

    all_items = _load_all_action_items(r)
    existing_ids = {item.get("id") for item in all_items}
    added = 0
    for item in new_items:
        if isinstance(item, dict) and item.get("id") and item["id"] not in existing_ids:
            all_items.append(item)
            added += 1

    if added:
        r.set("open-action-items", json.dumps(all_items), ex=30 * 24 * 3600)

    return briefing_clean


# ─── Claude ───────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are Shuning Wang's executive assistant generating his daily work briefing.

User context:
- Name: Shuning Wang  |  Email: shuning.wang@shopee.com
- Timezone: Asia/Singapore  |  Working hours: 09:30–19:30 SGT
- VIP senders: jianghong.liu@shopee.com, hoi@sea.com, fengc@sea.com
- Key domains: Swarm/OSP, SIP, FP&A, Budget, BPM

Produce a crisp, action-oriented daily briefing using this exact structure:

## Executive Brief
2–4 sentences: what matters most today, biggest risk, most critical reply/prep.
If SeaTalk has P0 items, surface them here.

## Prioritized Checklist
Checkboxes grouped under **P0** (urgent today), **P1** (important soon), **P2** (can wait).
Each item phrased as a concrete action. Include SeaTalk action items here too.

## Open Action Items
MANDATORY — always render this table. Never replace it with a flat list.

Read from the OPEN ACTION ITEMS data provided. Show every item where `done: false`.

Color coding (compute from today's SGT date vs each item's `eta` and `urgency`):
- 🔴 Chase now — ETA is today or already overdue, OR no ETA and `urgency` = "high"
- 🟠 Chase soon — ETA is 1–3 days away
- 🟡 Watch — ETA is 4–7 days away
- 🟢 Can wait — ETA is 8+ days away
- ⚪ When possible — no ETA and `urgency` is "low", "medium", or null

Sort order: 🔴 → 🟠 → 🟡 → 🟢 → ⚪. Within each color, sort by ETA ascending.

Use this exact table format:
🔴 Chase now  🟠 Chase soon (≤3 days)  🟡 Watch (4–7 days)  🟢 Can wait (8+ days)  ⚪ When possible

| | Action | Source | ETA | Chase? |
|--|--------|--------|-----|--------|
| 🔴 | [action text] | Email: thread name | overdue | Chase now |
| 🟠 | [action text] | Email: thread name | May 29 (2 days) | Chase soon |
| 🟡 | [action text] | Email: thread name | Jun 5 (9 days) | Watch |
| ⚪ | [action text] | SeaTalk: channel | — | When possible |

If the OPEN ACTION ITEMS list is empty, write: `No open action items.`

## Today's Schedule
Markdown table — columns: Time (SGT) | Meeting | Priority | Prep / notes
Only include events from the "TODAY'S CALENDAR EVENTS" section here.
Flag overlaps, all-day events, and after-hours meetings explicitly.

## Tomorrow's Schedule
Markdown table — columns: Time (SGT) | Meeting | Priority | Prep / notes
Include ALL events from the "TOMORROW'S CALENDAR EVENTS" section, not just ones needing prep.
Apply the same priority classification (P0/P1/P2) and RSVP rules as today.
Omit this section only if TOMORROW'S CALENDAR EVENTS is empty.

## SeaTalk Activity
Summarise internal chat messages. Use the sub-structure:
**P0 (act today)** | **P1 (handle soon)** | **P2 (FYI)**
Each bullet: [DM/Group] Sender — what was said — suggested action.
If no SeaTalk snapshot was available, say: "SeaTalk snapshot not available for this run."
Suppress: bot alerts, automated reports, reaction-only messages, join/leave notifications.

## What to Reply To
Bullets: Sender — Subject/Channel — suggested action or talking point.
Cover both email and SeaTalk DMs.

## Risks / Watchouts
Short bullets. Distinguish facts from inference.

## Suggested Priorities
Numbered ordered list of concrete next steps.

─── EMAIL TRIAGE RULES ───────────────────────────────────────────────────────────────

P0 (super important — act today). An email is P0 when ANY of these are true:
  • From or to a VIP AND total recipients < 10
  • A VIP has specifically replied in the email thread — regardless of recipient count or domain
  • Subject, body, or thread context related to a key domain (Swarm/OSP, SIP, FP&A, Budget, BPM) — check the full thread, not just the subject line
  • Subject contains "for your action" or the word "action" AND Shuning is in To:
  • A direct ask, deadline, escalation, or blocker is present AND a VIP is involved

P1 (important — handle within 48 h). An email is P1 when ANY of these are true:
  • From a VIP sender but total recipients ≥ 10 (large-group VIP message)
  • Shuning is in To: (not only Cc:) AND email asks a direct question or assigns an action
  • Shuning is addressed directly (Hi Shuning / Shuning, / Hey Shuning) and not already P0
  • Mentions deadline, contract, travel, interview, meeting, approval, confirmation,
    escalation, or blocker — AND Shuning is in To: (not only Cc:) — and is NOT related to a key domain (which would make it P0)
  • Thread appears to require a reply within the next 2 days
  • Email materially changes risk, ownership, timing, or expectations
  • Email relates to team headcount, personnel changes, internal transfers, or org structure affecting Shuning's direct team

P2 (lower priority — track but not urgent). An email is P2 when ALL are true:
  • Not related to any key domain
  • No VIP in the recipient list with fewer than 10 total recipients
  • No direct ask or deadline
  • Informational, Cc-only update, or general announcement

Suppress entirely (omit from briefing unless they contain a new risk, direct ask, or deadline directed at Shuning):
  newsletters, promotions, receipts, obvious automated alerts, recurring daily digests or
  repeated daily reports, routine calendar/system notifications.
  Operational reports, warehouse reports, logistics alerts, or system-generated metrics from
  domains outside Shuning's five key domains — suppress even if they contain new numbers.

─── CALENDAR TRIAGE RULES ───────────────────────────────────────────────────────────

P0 meeting when ANY are true:
  • A VIP is organizer or attendee AND total attendees < 10 AND Shuning has accepted the invite
  • A VIP has specifically sent a direct reply or message related to the meeting — regardless of attendee count — AND Shuning has accepted
  • Title or description is related to a key domain AND Shuning has accepted the invite

P1 meeting when ANY are true (and not already P0):
  • VIP organizer/attendee with ≥ 10 total attendees AND Shuning has accepted
  • External participants are involved AND Shuning has accepted
  • Title or description signals a decision, review, escalation, interview, travel,
    contract, hiring, or action item AND Shuning has accepted
  • Shuning is expected to present, decide, approve, or provide an update
  • A pre-read, deck, document, or deliverable appears necessary AND Shuning has accepted
  • RSVP: always check Shuning's response status — if he has NOT accepted (declined or
    no response), downgrade to P2 and do NOT generate prep recommendations. Note RSVP explicitly.

P2 meeting when ANY are true:
  • Total attendees > 30, even if a VIP is present
  • Not related to any key domain and does not meet P0/P1 criteria
  • Shuning is only Cc'd or optionally invited with no expected contribution
  • Shuning has NOT accepted the invite (declined or no response) — regardless of other factors

Always flag:
  • All-day events
  • After-hours meetings (outside 09:30–19:30 SGT)
  • Overlapping meetings (call out both titles and the overlap window)
  • Tomorrow's first meeting if preparation today would be useful

IMPORTANT: Events are pre-split into "TODAY'S CALENDAR EVENTS" and "TOMORROW'S CALENDAR EVENTS".
Use only today's events for "Today's Schedule". Never label today's events as tomorrow or vice versa.

─── SEATALK TRIAGE RULES ────────────────────────────────────────────────────────────

P0: DM from VIP; @mention of Shuning in any group; key domain topic; direct ask or
    action item addressed to Shuning; escalation, blocker, or urgent issue.
P1: DM from non-VIP; reply in a thread where Shuning posted; message about a deadline,
    meeting, or deliverable; group message directly addressing Shuning's area of ownership.
P2: general FYI; no action required from Shuning.
Suppress: bot alerts, automated reports, reaction-only messages, join/leave notifications.

─── ACTION ITEM EXTRACTION FROM EMAILS AND IMAGES ───────────────────────────────────

RULE 1 — Pre-read emails: SKIP
  If `is_prereread: true` on an email, DO NOT extract action items from it.
  Pre-read / agenda emails contain questions, topics, and discussion points that will be
  answered in the meeting. Tracking them as action items creates noise.

RULE 2 — Post-meeting emails: EXTRACT
  Extract action items from recap / follow-up emails (`is_prereread: false`).
  These are usually sent after the meeting, often as a reply in the pre-read thread
  (subject may start with "RE:") or as a standalone recap email.
  Post-meeting action item tables typically have columns like:
    • Item / Action description
    • PIC (Person in Charge)
    • ETA / Due Date

RULE 3 — Images (Claude Vision)
  Images attached to emails are provided as vision inputs immediately after this prompt.
  Each image is labelled with its source email, subject, and whether it is a pre-read.
  • If the image shows an action item table (columns: Item, PIC, ETA) AND it is from a
    post-meeting email (NOT pre-read), read each row and extract it as an action item.
  • If the image is from a pre-read email, describe it briefly but do NOT extract action items.
  • If the image is not an action item table (e.g. a chart, diagram, logo), just mention it.

RULE 4 — New action items output block
  If you find NEW action items not already present in the OPEN ACTION ITEMS data:
  Append this exact block at the very end of your briefing output (after all other sections):

---NEW_ACTION_ITEMS_START---
[
  {
    "id": "kebab-slug-unique",
    "source": "email subject or source description",
    "source_type": "email",
    "date_identified": "YYYY-MM-DD",
    "action": "one-sentence description of what needs to be done",
    "eta": "YYYY-MM-DD or null",
    "urgency": "high | medium | low | null",
    "done": false
  }
]
---NEW_ACTION_ITEMS_END---

  Rules for the JSON block:
  • Only include genuinely NEW items not already in OPEN ACTION ITEMS.
  • PIC must be Shuning or Shuning's team; skip items assigned to others entirely.
    If ownership is unclear, include with urgency: null.
  • Use today's SGT date for date_identified.
  • If no new action items were found, omit the block entirely — do NOT output an empty block.

─────────────────────────────────────────────────────────────────────────────────────

Use Singapore time (SGT) for all timestamps. Be concise — lead with the answer.\
"""


def _event_sgt_date(start_str: str) -> str:
    """Extract the SGT date (YYYY-MM-DD) from a Google Calendar start string.

    Handles timezone-aware ISO datetimes (e.g. '2026-04-15T17:00:00Z',
    '2026-04-16T01:00:00+08:00') and plain date strings ('2026-04-16')."""
    if not start_str:
        return ""
    # All-day events are plain YYYY-MM-DD — no conversion needed
    if len(start_str) == 10:
        return start_str
    try:
        dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        return dt.astimezone(SGT).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return start_str[:10]


def _split_events_by_day(events: list, today_str: str) -> tuple[list, list]:
    """Split calendar events into today's and tomorrow's based on SGT date."""
    today_events = []
    tomorrow_events = []
    for ev in events:
        event_date = _event_sgt_date(ev.get("start", ""))
        if event_date == today_str:
            today_events.append(ev)
        else:
            tomorrow_events.append(ev)
    return today_events, tomorrow_events


def generate_briefing(
    emails: list,
    events: list,
    today: str,
    window: str,
    seatalk_msgs: list | None = None,
    action_items: list | None = None,
) -> str:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    seatalk_section = (
        format_seatalk_payload(seatalk_msgs, window)
        if seatalk_msgs is not None
        else "SEATALK: Snapshot not available for this run (seatalk_snapshot.py may not have run).\n"
    )

    today_events, tomorrow_events = _split_events_by_day(events, today)
    open_items = action_items or []

    # Separate image attachments from the text payload so base64 data doesn't
    # bloat the text prompt. Images are passed as Claude Vision content blocks.
    all_images: list[dict] = []
    emails_text: list[dict] = []
    for email in emails:
        imgs = email.get("images", [])
        for img in imgs:
            all_images.append(
                {
                    "from": email.get("from", ""),
                    "subject": email.get("subject", ""),
                    "is_prereread": email.get("is_prereread", False),
                    **img,
                }
            )
        # Strip images and pdf_attachments (binary/metadata) from the serialised email object
        emails_text.append({k: v for k, v in email.items() if k not in ("images", "pdf_attachments")})

    # Cap total images to avoid oversized requests
    all_images = all_images[:5]

    payload = (
        f"REVIEW WINDOW: {window}\n"
        f"TODAY: {today} (Asia/Singapore)\n\n"
        f"=== GMAIL ({len(emails_text)} messages) ===\n"
        f"{json.dumps(emails_text, indent=2, default=str)}\n\n"
        f"=== TODAY'S CALENDAR EVENTS ({today}, {len(today_events)} events) ===\n"
        f"{json.dumps(today_events, indent=2, default=str)}\n\n"
        f"=== TOMORROW'S CALENDAR EVENTS ({len(tomorrow_events)} events) ===\n"
        f"{json.dumps(tomorrow_events, indent=2, default=str)}\n\n"
        f"=== OPEN ACTION ITEMS ({len(open_items)} items) ===\n"
        f"{json.dumps(open_items, indent=2, default=str)}\n\n"
        f"=== SEATALK MESSAGES ===\n"
        f"{seatalk_section}"
    )

    intro = f"Generate my daily briefing:\n\n{payload}"

    # Build content: text first, then interleaved image label + image blocks
    if all_images:
        content: list[dict] = [{"type": "text", "text": intro}]
        for img in all_images:
            prereread_flag = (
                " [PRE-READ EMAIL — do NOT extract action items from this image]"
                if img["is_prereread"]
                else " [POST-MEETING/FOLLOW-UP EMAIL — extract action items if this is an action item table]"
            )
            content.append(
                {
                    "type": "text",
                    "text": (
                        f"Image from: '{img['subject']}' (sender: {img['from']})"
                        f"{prereread_flag}\nFilename: {img.get('filename', 'image')}"
                    ),
                }
            )
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": img["media_type"],
                        "data": img["data"],
                    },
                }
            )
    else:
        content = intro  # type: ignore[assignment]

    models = ["claude-sonnet-4-6", "claude-haiku-4-5-20251001"]
    last_err = None
    for model in models:
        try:
            msg = client.messages.create(
                model=model,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": content}],
            )
            return msg.content[0].text
        except Exception as exc:
            # Usage-limit errors affect all models on the same key — fail fast.
            err_str = str(exc)
            if "API usage limits" in err_str or "reached your specified" in err_str:
                raise RuntimeError(
                    f"Anthropic API usage limit reached. {err_str}\n"
                    "Action: raise your spend/usage limit at console.anthropic.com."
                ) from exc
            last_err = exc
            continue
    raise last_err


# ─── Storage ──────────────────────────────────────────────────────────────────

def store(date_str: str, briefing: str) -> None:
    _redis().set(f"daily-brief:{date_str}", briefing, ex=7 * 24 * 3600)


# ─── Email ────────────────────────────────────────────────────────────────────

def _build_html(briefing_md: str, date_str: str, view_url: str, generated_at: str) -> str:
    body_html = md_lib.markdown(briefing_md, extensions=["tables", "nl2br", "fenced_code"])
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Daily Brief — {date_str}</title>
<style>
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
        background:#f4f6f9;color:#1a1a2e;margin:0;padding:0}}
  .wrap{{max-width:780px;margin:0 auto;padding:0 0 40px}}
  .hdr{{background:linear-gradient(135deg,#1a1a2e 0%,#16213e 100%);
        color:#fff;padding:24px 32px;border-radius:0 0 12px 12px}}
  .hdr h1{{margin:0;font-size:1.4rem}}
  .hdr p{{margin:4px 0 0;opacity:.65;font-size:.85rem}}
  .btn{{display:inline-block;margin:20px 0 0;background:rgba(255,255,255,0.15);color:#fff!important;
        border:1px solid rgba(255,255,255,0.4);
        padding:10px 22px;border-radius:6px;text-decoration:none;font-weight:600;font-size:.9rem}}
  .card{{background:#fff;border-radius:12px;padding:28px 32px;
         margin:20px 16px 0;box-shadow:0 1px 4px rgba(0,0,0,.08)}}
  h1,h2,h3{{color:#1a1a2e}}
  h2{{border-bottom:2px solid #e9ecef;padding-bottom:.3rem;margin-top:1.4rem}}
  table{{border-collapse:collapse;width:100%;margin:.8rem 0}}
  th{{background:#f4f6f9;padding:8px 12px;border:1px solid #dee2e6;text-align:left;font-size:.85rem}}
  td{{padding:7px 12px;border:1px solid #dee2e6;vertical-align:top;font-size:.9rem}}
  tr:nth-child(even){{background:#f8f9fa}}
  a{{color:#4f46e5}}
  code{{background:#f1f3f5;padding:2px 5px;border-radius:3px;font-size:.88em}}
  ul,ol{{padding-left:1.4rem}}li{{margin:.25rem 0}}
  blockquote{{border-left:3px solid #4f46e5;margin:.5rem 0;padding-left:1rem;color:#555}}
  input[type=checkbox]{{margin-right:5px}}
</style>
</head>
<body>
<div class="wrap">
  <div class="hdr">
    <h1>Daily Brief — {date_str}</h1>
    <p>Generated {generated_at} SGT &nbsp;·&nbsp; Asia/Singapore</p>
    <a href="{view_url}" class="btn">View in browser →</a>
  </div>
  <div class="card">
    {body_html}
  </div>
</div>
</body>
</html>"""


def send_email(briefing_md: str, date_str: str, view_url: str, now_sgt: datetime) -> None:
    generated_at = now_sgt.strftime("%H:%M")
    html = _build_html(briefing_md, date_str, view_url, generated_at)
    username = RECIPIENT.split("@")[0]
    subject = f"{username} | Daily Brief - {now_sgt.strftime('%m-%d %H:%M')}"
    from_email = os.environ.get("FROM_EMAIL", "assistant@example.com")

    if os.environ.get("SENDGRID_API_KEY"):
        import sendgrid
        from sendgrid.helpers.mail import Mail

        sg = sendgrid.SendGridAPIClient(api_key=os.environ["SENDGRID_API_KEY"])
        message = Mail(
            from_email=from_email,
            to_emails=RECIPIENT,
            subject=subject,
            html_content=html,
        )
        sg.send(message)

    elif os.environ.get("SMTP_HOST"):
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_email
        msg["To"] = RECIPIENT
        msg.attach(MIMEText(briefing_md, "plain"))
        msg.attach(MIMEText(html, "html"))

        host = os.environ["SMTP_HOST"]
        port = int(os.environ.get("SMTP_PORT", 465))
        user = os.environ["SMTP_USER"]
        password = os.environ["SMTP_PASS"]

        with smtplib.SMTP_SSL(host, port) as server:
            server.login(user, password)
            server.send_message(msg)

    else:
        raise RuntimeError("No email transport configured. Set SENDGRID_API_KEY or SMTP_* env vars.")


# ─── Shared run logic ─────────────────────────────────────────────────────────

def _redis() -> Redis:
    return Redis(
        url=os.environ["UPSTASH_REDIS_REST_URL"],
        token=os.environ["UPSTASH_REDIS_REST_TOKEN"],
    )


def _run_pdf_qa(gmail_svc, emails: list[dict], today_str: str, r) -> None:
    """
    For each pre-read email with PDF attachments, download the best PDF and
    generate Ian Ho–style predicted questions using Claude. Results are stored
    in Redis under pdf-qa:{today_str}.

    Errors are silently swallowed so they don't block the main briefing.
    """
    try:
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    except Exception:
        return

    # Collect pre-read emails that have PDF attachments
    prereads_with_pdfs = [
        e for e in emails
        if e.get("is_prereread") and e.get("pdf_attachments")
    ]

    processed_pdfs: set[str] = set()  # avoid duplicates across emails

    for email in prereads_with_pdfs:
        best = select_best_pdf(email["pdf_attachments"])
        if not best:
            continue
        pdf_name = best["filename"]
        att_id = best["attachment_id"]
        msg_id = email["id"]

        # Skip if we already processed a PDF with this name today
        if pdf_name in processed_pdfs:
            continue
        processed_pdfs.add(pdf_name)

        try:
            # Download the PDF bytes from Gmail
            att_resp = (
                gmail_svc.users()
                .messages()
                .attachments()
                .get(userId="me", messageId=msg_id, id=att_id)
                .execute()
            )
            raw_data = att_resp.get("data", "")
            pdf_bytes = base64.urlsafe_b64decode(raw_data + "==")
            if len(pdf_bytes) < 1000:
                continue  # Skip suspiciously small "PDFs"

            # Generate questions
            questions = generate_pdf_qa(pdf_bytes, pdf_name, today_str, client)
            if questions:
                save_pdf_qa(r, today_str, questions)
        except Exception:
            # Non-fatal: continue with next email
            continue


def run_briefing() -> tuple[str, str, str]:
    """
    Execute the full briefing pipeline.
    Returns (today_str, view_url, briefing_md).
    Raises DuplicateRunError if a brief was sent within the last 30 minutes.
    """
    now_sgt = datetime.now(SGT)
    today_str = now_sgt.strftime("%Y-%m-%d")

    r = _redis()
    acquired = r.set("daily-brief-lock", now_sgt.isoformat(), ex=DEDUP_WINDOW_SECONDS, nx=True)
    if acquired is None:
        raise DuplicateRunError(
            f"Daily brief already sent within the last {DEDUP_WINDOW_SECONDS // 60} minutes — skipping duplicate run."
        )

    since = now_sgt - timedelta(hours=24)
    window = (
        f"{since.strftime('%Y-%m-%d %H:%M SGT')} "
        f"→ {now_sgt.strftime('%Y-%m-%d %H:%M SGT')}"
    )

    creds = google_creds()
    gmail_svc = build("gmail", "v1", credentials=creds)
    cal_svc = build("calendar", "v3", credentials=creds)

    emails = fetch_gmail(gmail_svc, since)
    events = fetch_calendar(cal_svc, now_sgt)
    seatalk_msgs = fetch_seatalk_snapshot(today_str)  # None if snapshot not pushed
    action_items = fetch_open_action_items(r)

    # ── PDF Q&A generation for pre-read emails ──────────────────────────────
    # For each pre-read email with PDF attachments, select the main discussion
    # deck, download it, and generate Ian Ho–style predicted questions.
    _run_pdf_qa(gmail_svc, emails, today_str, r)

    briefing_raw = generate_briefing(emails, events, today_str, window, seatalk_msgs, action_items)
    # Parse any new action items Claude found (from images or email text), save to Redis,
    # and strip the machine-readable block from the briefing before storing/sending.
    briefing = _parse_and_save_new_action_items(r, briefing_raw)
    store(today_str, briefing)

    base = os.environ.get("VERCEL_URL", "localhost:3000")
    if not base.startswith("http"):
        base = f"https://{base}"
    view_url = f"{base}/api/view?date={today_str}"

    send_email(briefing, today_str, view_url, now_sgt)
    return today_str, view_url, briefing
