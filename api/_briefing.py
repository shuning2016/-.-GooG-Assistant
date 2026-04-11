"""
Shared briefing logic used by both /api/cron and /api/trigger.

Keeping this in a private module (_briefing.py) avoids cross-function
imports between sibling api/ files, which are unreliable in Vercel's
Python serverless runtime.
"""

import json
import logging
import os
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

SGT = ZoneInfo("Asia/Singapore")
RECIPIENT = "Shuning.wang@shopee.com"
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
    """Return today's + tomorrow's calendar events."""
    day_start = now_sgt.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=2)
    result = service.events().list(
        calendarId="primary",
        timeMin=day_start.isoformat(),
        timeMax=day_end.isoformat(),
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

Produce a crisp, action-oriented daily briefing using this exact structure:

## Executive Brief
2–4 sentences covering: what matters most today, biggest risk, most critical reply/prep.

## Prioritized Checklist
Checkboxes grouped under **P0** (urgent today), **P1** (important soon), **P2** (can wait).
Each item phrased as a concrete action, not an observation.

## Today's Schedule
Markdown table — columns: Time (SGT) | Meeting | Why it matters | Prep / notes
Flag overlaps and after-hours events explicitly.

## Google Drive Updates
(Only include if relevant files found.) Each file as a clickable markdown link.
Columns: File | Owner | Changed | Why flagged

## What to Reply To
Bullets: Sender — Subject — suggested action or talking point.

## Risks / Watchouts
Short bullets. Distinguish facts from inference.

## Suggested Priorities
Numbered ordered list.

Priority rubric:
- P0: due today, VIP direct ask, blocks progress, needs reply today, meeting prep now
- P1: reply within 48 h, important meeting tomorrow needing today's prep, meaningful downside if delayed
- P2: informational, lower-risk, can wait 2+ days

Email triage — flag when: VIP sender; "for your action" in subject; directly addressed
(Hi Shuning / Shuning,); in To: not only Cc:; direct question or explicit ask;
mentions deadline, contract, travel, interview, approval, escalation, blocker.

Suppress: newsletters, promotions, receipts, automated alerts, recurring digests.

Use Singapore time (SGT) for all timestamps. Be concise — lead with the answer.\
"""


def generate_briefing(emails: list, events: list, drive_files: list, today: str, window: str) -> str:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    payload = (
        f"REVIEW WINDOW: {window}\n"
        f"TODAY: {today} (Asia/Singapore)\n\n"
        f"=== GMAIL ({len(emails)} messages) ===\n"
        f"{json.dumps(emails, indent=2, default=str)}\n\n"
        f"=== CALENDAR EVENTS ===\n"
        f"{json.dumps(events, indent=2, default=str)}\n\n"
        f"=== GOOGLE DRIVE CHANGES ({len(drive_files)} files) ===\n"
        f"{json.dumps(drive_files, indent=2, default=str)}\n"
    )

    msg = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Generate my daily briefing:\n\n{payload}"}],
    )
    return msg.content[0].text


# ─── Storage ──────────────────────────────────────────────────────────────────

def store(date_str: str, briefing: str) -> None:
    r = Redis(
        url=os.environ["UPSTASH_REDIS_REST_URL"],
        token=os.environ["UPSTASH_REDIS_REST_TOKEN"],
    )
    r.set(f"daily-brief:{date_str}", briefing, ex=7 * 24 * 3600)


# ─── Email ────────────────────────────────────────────────────────────────────

def _build_html(briefing_md: str, date_str: str, view_url: str) -> str:
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
  .btn{{display:inline-block;margin:20px 0 0;background:#4f46e5;color:#fff!important;
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
    <p>Generated 8:00 AM SGT &nbsp;·&nbsp; Asia/Singapore</p>
    <a href="{view_url}" class="btn">View in browser →</a>
  </div>
  <div class="card">
    {body_html}
  </div>
</div>
</body>
</html>"""


def send_email(briefing_md: str, date_str: str, view_url: str) -> None:
    html = _build_html(briefing_md, date_str, view_url)
    subject = f"Daily Brief — {date_str}"
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

def run_briefing() -> tuple[str, str, str]:
    """
    Execute the full briefing pipeline.
    Returns (today_str, view_url, briefing_md).
    """
    now_sgt = datetime.now(SGT)
    today_str = now_sgt.strftime("%Y-%m-%d")
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

    briefing = generate_briefing(emails, events, drive_files, today_str, window)
    store(today_str, briefing)

    base = os.environ.get("VERCEL_URL", "localhost:3000")
    if not base.startswith("http"):
        base = f"https://{base}"
    view_url = f"{base}/api/view?date={today_str}"

    send_email(briefing, today_str, view_url)
    return today_str, view_url, briefing
