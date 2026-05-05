"""
Shared briefing logic used by both /api/cron and /api/trigger.

Keeping this in a private module (_briefing.py) avoids cross-function
imports between sibling api/ files, which are unreliable in Vercel's
Python serverless runtime.
"""

import json
import os
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

SGT = ZoneInfo("Asia/Singapore")
RECIPIENT = "Shuning.wang@shopee.com"
DEDUP_WINDOW_SECONDS = 1800  # 30 minutes


class DuplicateRunError(RuntimeError):
    pass
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
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


def fetch_gmail(service, since: datetime) -> list[dict]:
    """Return up to 40 message summaries (headers + snippet) since `since`."""
    after_ts = int(since.timestamp())
    q = f"after:{after_ts} -category:promotions -category:social -category:updates"
    result = service.users().messages().list(userId="me", q=q, maxResults=50).execute()
    messages = result.get("messages", [])[:40]

    out = []
    for m in messages:
        detail = service.users().messages().get(
            userId="me",
            id=m["id"],
            format="metadata",
            metadataHeaders=["From", "To", "Cc", "Subject", "Date"],
        ).execute()
        h = {hdr["name"]: hdr["value"] for hdr in detail.get("payload", {}).get("headers", [])}
        out.append(
            {
                "id": m["id"],
                "thread_id": detail.get("threadId"),
                "from": h.get("From", ""),
                "to": h.get("To", ""),
                "cc": h.get("Cc", ""),
                "subject": h.get("Subject", ""),
                "date": h.get("Date", ""),
                "snippet": detail.get("snippet", ""),
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


def fetch_drive(service, since: datetime) -> list[dict]:
    """Return recently changed Drive files relevant to Shuning."""
    lb = since.isoformat()
    seen: set[str] = set()
    files: list[dict] = []

    queries = [
        f"modifiedTime > '{lb}' and not 'me' in owners",
        f"name contains 'TWCB' and modifiedTime > '{lb}'",
    ]
    for q in queries:
        res = service.files().list(
            q=q,
            fields="files(id,name,owners,modifiedTime,webViewLink,shared,sharingUser,createdTime)",
            pageSize=50,
        ).execute()
        for f in res.get("files", []):
            if f["id"] not in seen:
                seen.add(f["id"])
                files.append(f)
    return files


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

## Today's Schedule
Markdown table — columns: Time (SGT) | Meeting | Priority | Prep / notes
Only include events from the "TODAY'S CALENDAR EVENTS" section here.
Flag overlaps, all-day events, and after-hours meetings explicitly.

## Tomorrow's Schedule
Markdown table — columns: Time (SGT) | Meeting | Priority | Prep / notes
Include ALL events from the "TOMORROW'S CALENDAR EVENTS" section, not just ones needing prep.
Apply the same priority classification (P0/P1/P2) and RSVP rules as today.
Omit this section only if TOMORROW'S CALENDAR EVENTS is empty.

## Google Drive Updates
(Only include if relevant files found.) Each file as a clickable markdown link.
Columns: File | Owner | Changed | Why flagged

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
    drive_files: list,
    today: str,
    window: str,
    seatalk_msgs: list | None = None,
) -> str:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    seatalk_section = (
        format_seatalk_payload(seatalk_msgs, window)
        if seatalk_msgs is not None
        else "SEATALK: Snapshot not available for this run (seatalk_snapshot.py may not have run).\n"
    )

    today_events, tomorrow_events = _split_events_by_day(events, today)

    payload = (
        f"REVIEW WINDOW: {window}\n"
        f"TODAY: {today} (Asia/Singapore)\n\n"
        f"=== GMAIL ({len(emails)} messages) ===\n"
        f"{json.dumps(emails, indent=2, default=str)}\n\n"
        f"=== TODAY'S CALENDAR EVENTS ({today}, {len(today_events)} events) ===\n"
        f"{json.dumps(today_events, indent=2, default=str)}\n\n"
        f"=== TOMORROW'S CALENDAR EVENTS ({len(tomorrow_events)} events) ===\n"
        f"{json.dumps(tomorrow_events, indent=2, default=str)}\n\n"
        f"=== GOOGLE DRIVE CHANGES ({len(drive_files)} files) ===\n"
        f"{json.dumps(drive_files, indent=2, default=str)}\n\n"
        f"=== SEATALK MESSAGES ===\n"
        f"{seatalk_section}"
    )

    models = ["claude-sonnet-4-6", "claude-haiku-4-5-20251001"]
    last_err = None
    for model in models:
        try:
            msg = client.messages.create(
                model=model,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": f"Generate my daily briefing:\n\n{payload}"}],
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
    drive_svc = build("drive", "v3", credentials=creds)

    emails = fetch_gmail(gmail_svc, since)
    events = fetch_calendar(cal_svc, now_sgt)
    drive_files = fetch_drive(drive_svc, since)
    seatalk_msgs = fetch_seatalk_snapshot(today_str)  # None if snapshot not pushed

    briefing = generate_briefing(emails, events, drive_files, today_str, window, seatalk_msgs)
    store(today_str, briefing)

    base = os.environ.get("VERCEL_URL", "localhost:3000")
    if not base.startswith("http"):
        base = f"https://{base}"
    view_url = f"{base}/api/view?date={today_str}"

    send_email(briefing, today_str, view_url, now_sgt)
    return today_str, view_url, briefing
