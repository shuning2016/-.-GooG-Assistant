#!/usr/bin/env python3
"""
seatalk_summary.py — Read SeaTalk messages, generate a summary via Claude, and email it.

Scheduled runs (via LaunchAgent):
    10:00 SGT  →  python3 scripts/seatalk_summary.py --hours 3    (07:00–10:00)
    12:00 SGT  →  python3 scripts/seatalk_summary.py --hours 2    (10:00–12:00)
    15:00 SGT  →  python3 scripts/seatalk_summary.py --hours 3    (12:00–15:00)
    19:00 SGT  →  python3 scripts/seatalk_summary.py --hours 4    (15:00–19:00)

Environment (from ~/.goog-assistant.env or shell environment):
    ANTHROPIC_API_KEY           Claude API key
    SENDGRID_API_KEY            SendGrid key  (primary transport)
    SMTP_HOST / SMTP_PORT       SMTP server   (fallback transport)
    SMTP_USER / SMTP_PASS       SMTP credentials
    FROM_EMAIL                  Sender address (default: assistant@example.com)
    SEATALK_SKILL_ROOT          Path to use-seatalk repo (auto-detected if unset)
    UPSTASH_REDIS_REST_URL      Optional: cache summary to Redis for view.py
    UPSTASH_REDIS_REST_TOKEN    Optional: Redis auth token
"""

import argparse
import json
import os
import re
import smtplib
import subprocess
import sys
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

SGT = ZoneInfo("Asia/Singapore")
RECIPIENT = "Shuning.wang@shopee.com"
KEY_DOMAIN_GROUPS = "Swarm,OSP,SIP,FP&A,Budget,BPM"
_DRIVE = os.path.expanduser(
    "~/Library/CloudStorage/GoogleDrive-shuning2016@gmail.com"
    "/My Drive/My Projects/Working Efficiency/use-seatalk"
)

# ─── SeaTalk Claude prompt ────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are Shuning Wang's executive assistant summarising his SeaTalk (internal chat) messages.

SeaTalk is Shopee's primary internal instant-messenger.

User context:
- Name: Shuning Wang  |  Email: shuning.wang@shopee.com  |  Handle: @shuning.wang / @Shuning
- Timezone: Asia/Singapore
- VIP contacts: jianghong.liu@shopee.com, hoi@sea.com, fengc@sea.com
- Key domains: Swarm, OSP, SIP, FP&A, Budget, BPM

Produce a standalone SeaTalk summary email using this exact structure — no HTML tags:

## Executive Snapshot
2–3 sentences: what requires action, biggest signal, who needs a reply.

## ⏳ Waiting for Reply
- [Group/DM: Name] What Shuning asked — pending since HH:MM SGT  [NEW] or [day N]

## P0 — Act now
- [DM/Group: Name] **Sender** — what was said — suggested action

## P1 — Handle soon
- [Group: Name] **Sender** — what was said — suggested action

## P2 — FYI
- [Group: Name] Brief bullet only (no detail needed)

## Action Items
- [ ] Concrete next steps, with owner and deadline if mentioned in messages

Omit any section that has no items.

P0 — classify as P0 when ANY of these are true:
  1. Any 1:1 direct message (DM) from any person — regardless of VIP status or content
     EXCEPTION: personal friends (Kel Jin, Han Cheng) — apply the Friends rule below instead.
  2. Message @mentions Shuning (@shuning.wang or @Shuning) in any group
  3. Message CONTENT is about a key domain: Swarm, OSP, SIP, FP&A, Budget, or BPM
  4. Message is FROM A GROUP whose name contains a key domain word — e.g. a group called
     "Swarm", "BPM Leads", "SIP Core Leads", "OSP team", "FP&A". Every message in that
     group is P0 regardless of whether the content explicitly mentions the domain.
  5. Direct ask, deadline, escalation, or blocker directed at Shuning or his area
  6. Thread reply in a thread that Shuning originally started (he is the OP)

P1 — when ANY of these are true (and not already P0):
  • Reply in a thread where Shuning previously posted (but is not the OP)
  • Message about a meeting, deadline, deliverable, approval, or contract touching Shuning's work
  • Group message in a channel with fewer than 20 members where Shuning's input is implied

P2 — informational, general group message, no action required from Shuning.

Friends — Kel Jin and Han Cheng are personal friends, not work colleagues:
  • DMs from them are NOT automatically P0/P1.
  • Only P0 if the message genuinely contains a key-domain topic or urgent work ask.
  • Otherwise P2.
  • Do NOT add any "Friend" label — the UI handles that automatically.

CRITICAL — Conversation filters (apply IN ORDER before writing output):

  Step 1 — Shuning-replied-last split:
    For each session/thread, find Shuning's last message (fromSelf=true).
    If such a message exists AND there are ZERO messages after it in that thread:
      a) If his last message is a SUBSTANTIVE ASK, REQUEST, ESCALATION, or ASSIGNMENT:
         → Add to ## ⏳ Waiting for Reply. Do NOT include in P0/P1/P2.
      b) If his last message is an ACKNOWLEDGEMENT, AGREEMENT, or SIMPLE REPLY
         ("ok", "noted", "sure", "thanks", "👍", emoji only):
         → OMIT entirely from all sections.

  Step 2 — Mutually-resolved filter:
    Also omit a conversation when ALL of these hold:
      • The last few messages form a clear question → decision → confirmation chain.
      • Shuning's role was to agree, confirm, or acknowledge (NOT to ask or escalate).
      • The other party's final message is a closure signal ("ok", "noted", "confirmed",
        "thanks", "sounds good", "will do", "sure", "👍", or equivalent).
      • No open question, outstanding deliverable, or new ask remains in the thread.
    → OMIT entirely. Example: "Move to Monday?" → Shuning: "sure" → Other: "confirmed" → OMIT.

Waiting for Reply rules:
  • Detect NEW pending items (Step 1a above) and label them [NEW].
  • Previously-tracked items (provided in context below) that still have no reply in the
    current messages → keep in this section, label [day N] where N = days since first seen.
  • RESOLUTION RULE — a previously-tracked item is RESOLVED only if:
      - DM: the other person has explicitly replied to Shuning's message.
      - GROUP: the specific person Shuning @mentioned or directly asked has replied IN THAT
        SAME THREAD. General new posts by OTHER group members do NOT resolve the item.
        If Shuning asked @Yang Wenxiao something and only others posted, still PENDING.
    When in doubt → keep as pending. False-positive reminders beat silent drops.
  • Previously-tracked items that are RESOLVED → omit from this section (appear in P0/P1/P2
    if still actionable).
  • Omit this section entirely if nothing is pending.

At the very END of your response, append this machine-readable block (it will be stripped before display):
<!-- PENDING_ITEMS: [{"source":"[Group/DM: Name]","summary":"What Shuning asked","since":"HH:MM SGT YYYY-MM-DD"}] -->
Use an empty array if nothing is pending: <!-- PENDING_ITEMS: [] -->

Suppress ONLY these (do not suppress key-domain group messages even if they look routine):
  • Automated bot notifications (CI/CD, monitoring, system alerts)
  • Recurring daily report bots with no new action
  • Pure emoji / reaction-only messages
  • System join/leave notifications

Rules:
- Be concise. Lead with the answer.
- Distinguish facts from inference.
- Use Singapore time (SGT) for all timestamps.
- If there are no P0 or P1 items: say so explicitly — do not pad with filler.
- If the window has no relevant messages: write one line: "No relevant SeaTalk activity in this window."\
"""


# ─── Env loader ───────────────────────────────────────────────────────────────

def _load_env(path: str = "~/.goog-assistant.env") -> None:
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            val = val.strip().strip('"').strip("'")
            os.environ.setdefault(key.strip(), val)


# ─── SeaTalk reader ───────────────────────────────────────────────────────────

def _seatalk_root() -> str:
    return os.environ.get("SEATALK_SKILL_ROOT", _DRIVE)


def read_messages(hours: int) -> tuple[list[dict], str]:
    """
    Call redux_related_messages.py for the last `hours` hours.
    Returns (messages, window_str).
    """
    reader = os.path.join(_seatalk_root(), "scripts", "redux_related_messages.py")
    if not os.path.exists(reader):
        raise FileNotFoundError(
            f"CDP reader not found at {reader}\n"
            "Set SEATALK_SKILL_ROOT to the use-seatalk repo path."
        )

    now_sgt = datetime.now(SGT)
    since_sgt = now_sgt - timedelta(hours=hours)

    result = subprocess.run(
        [sys.executable, reader, "--last-hours", str(hours),
         "--watch-groups", KEY_DOMAIN_GROUPS],
        capture_output=True,
        text=True,
        timeout=90,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"CDP reader exited {result.returncode}:\n{result.stderr.strip()}"
        )

    data = json.loads(result.stdout)
    messages = data.get("messages", data) if isinstance(data, dict) else data
    window = (
        f"{since_sgt.strftime('%H:%M')} → {now_sgt.strftime('%H:%M SGT')} "
        f"({hours}h window)"
    )
    return messages, window


# ─── Pending-item helpers ─────────────────────────────────────────────────────

def _load_pending_items() -> list[dict]:
    """Load previously-tracked pending items from Redis. Returns [] on any failure."""
    try:
        from upstash_redis import Redis

        r = Redis(
            url=os.environ["UPSTASH_REDIS_REST_URL"],
            token=os.environ["UPSTASH_REDIS_REST_TOKEN"],
        )
        raw = r.get("seatalk-pending")
        if not raw:
            return []
        data = json.loads(raw) if isinstance(raw, str) else raw
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_pending_items(items: list[dict]) -> None:
    """Persist pending items to Redis (TTL: 7 days). Never raises."""
    try:
        from upstash_redis import Redis

        r = Redis(
            url=os.environ["UPSTASH_REDIS_REST_URL"],
            token=os.environ["UPSTASH_REDIS_REST_TOKEN"],
        )
        r.set("seatalk-pending", json.dumps(items, default=str), ex=7 * 24 * 3600)
    except Exception:
        pass


def _extract_pending_items(summary: str) -> tuple[str, list[dict]]:
    """
    Strip the <!-- PENDING_ITEMS: [...] --> marker from Claude's output.
    Returns (clean_summary, items_list).
    """
    pattern = r"<!--\s*PENDING_ITEMS:\s*(\[.*?\])\s*-->"
    match = re.search(pattern, summary, re.DOTALL)
    if not match:
        return summary.strip(), []
    try:
        items = json.loads(match.group(1))
    except (json.JSONDecodeError, ValueError):
        items = []
    clean = re.sub(pattern, "", summary, flags=re.DOTALL).strip()
    return clean, items


def _format_pending_context(items: list[dict]) -> str:
    """Format stored pending items as context block for Claude."""
    if not items:
        return ""
    lines = [
        "Previously-tracked items still waiting for a reply (check if now resolved in the messages):"
    ]
    for item in items:
        lines.append(
            f"  - {item.get('source', '')} {item.get('summary', '')} — since {item.get('since', 'unknown')}"
        )
    return "\n".join(lines)


# ─── Claude summary ───────────────────────────────────────────────────────────

def generate_summary(messages: list[dict], window: str, now_sgt: datetime) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    payload = (
        f"WINDOW: {window}\n"
        f"NOW: {now_sgt.strftime('%Y-%m-%d %H:%M SGT')}\n\n"
        f"MESSAGES ({len(messages)} total):\n"
        f"{json.dumps(messages, indent=2, default=str)}\n"
    )

    # Inject previously-tracked pending items so Claude can resolve or carry them forward
    pending_ctx = _format_pending_context(_load_pending_items())
    user_content = f"Summarise these SeaTalk messages:\n\n{payload}"
    if pending_ctx:
        user_content += f"\n\n{pending_ctx}"

    msg = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    raw_output = msg.content[0].text

    # Extract the PENDING_ITEMS marker and persist for next run
    clean_summary, new_pending = _extract_pending_items(raw_output)
    _save_pending_items(new_pending)
    if new_pending:
        print(f"  Saved {len(new_pending)} pending item(s) to Redis")
    return clean_summary


# ─── Email ────────────────────────────────────────────────────────────────────

_VIP_NAMES = ["jianghong", "hoi", "fengc", "feng c"]
_FRIEND_NAMES = ["kel jin", "han cheng"]


def _apply_seatalk_styles(html: str) -> str:
    """Post-process rendered HTML to add SeaTalk-specific visual styling."""

    # [Group: Name] → teal pill
    html = re.sub(
        r"\[Group:\s*([^\]]+)\]",
        r'<span class="st-src st-src-group">&#128101; \1</span>',
        html,
    )

    # [DM: Name] → purple pill + friend badge where applicable
    def _dm(m: re.Match) -> str:
        name = m.group(1).strip()
        fb = (
            ' <span class="st-friend">Friend &#128578;</span>'
            if any(f in name.lower() for f in _FRIEND_NAMES)
            else ""
        )
        return f'<span class="st-src st-src-dm">&#128172; {name}</span>{fb}'

    html = re.sub(r"\[DM:\s*([^\]]+)\]", _dm, html)

    # <strong>Name</strong> → orange if VIP
    def _bold(m: re.Match) -> str:
        text = m.group(1)
        if any(v in text.lower() for v in _VIP_NAMES):
            return f'<strong class="st-vip">{text}</strong>'
        return f"<strong>{text}</strong>"

    html = re.sub(r"<strong>([^<]+)</strong>", _bold, html)

    return html


def _build_html(summary_md: str, run_label: str, date_str: str) -> str:
    try:
        import markdown as md_lib
        body_html = md_lib.markdown(summary_md, extensions=["tables", "nl2br", "fenced_code"])
    except ImportError:
        # Fallback: wrap in <pre> if markdown lib not installed locally
        body_html = f"<pre>{summary_md}</pre>"

    body_html = _apply_seatalk_styles(body_html)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SeaTalk Brief — {run_label}</title>
<style>
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
        background:#f4f6f9;color:#1a1a2e;margin:0;padding:0}}
  .wrap{{max-width:700px;margin:0 auto;padding:0 0 40px}}
  .hdr{{background:linear-gradient(135deg,#0a3d62 0%,#1e3799 100%);
        color:#fff;padding:20px 28px;border-radius:0 0 12px 12px}}
  .hdr h1{{margin:0;font-size:1.3rem}}
  .hdr p{{margin:4px 0 0;opacity:.65;font-size:.82rem}}
  .card{{background:#fff;border-radius:12px;padding:24px 28px;
         margin:16px 14px 0;box-shadow:0 1px 4px rgba(0,0,0,.08)}}
  h2{{color:#1a1a2e;border-bottom:2px solid #e9ecef;padding-bottom:.3rem;margin-top:1.2rem}}
  table{{border-collapse:collapse;width:100%;margin:.6rem 0}}
  th{{background:#f4f6f9;padding:7px 10px;border:1px solid #dee2e6;text-align:left;font-size:.83rem}}
  td{{padding:6px 10px;border:1px solid #dee2e6;vertical-align:top;font-size:.88rem}}
  tr:nth-child(even){{background:#f8f9fa}}
  a{{color:#1e3799}}
  ul,ol{{padding-left:1.3rem}}li{{margin:.2rem 0}}
  input[type=checkbox]{{margin-right:4px}}
  code{{background:#f1f3f5;padding:2px 4px;border-radius:3px;font-size:.86em}}
  /* SeaTalk source pills */
  .st-src{{display:inline-flex;align-items:center;font-size:.72rem;font-weight:700;
           border-radius:4px;padding:2px 7px;margin-right:5px;white-space:nowrap;
           vertical-align:middle}}
  .st-src-group{{background:#EFF7FC;color:#0080C6;border:1px solid #bae6fd}}
  .st-src-dm{{background:#f5f3ff;color:#7c3aed;border:1px solid #ddd6fe}}
  /* VIP name highlight */
  strong.st-vip{{color:#EE4D2D}}
  /* Friend badge */
  .st-friend{{display:inline-block;background:#fef9c3;color:#854d0e;
              border:1px solid #fde68a;border-radius:4px;font-size:.65rem;
              font-weight:700;padding:1px 5px;margin-left:3px;vertical-align:middle}}
</style>
</head>
<body>
<div class="wrap">
  <div class="hdr">
    <h1>SeaTalk Brief — {run_label}</h1>
    <p>{date_str} &nbsp;·&nbsp; Asia/Singapore</p>
  </div>
  <div class="card">
    {body_html}
  </div>
</div>
</body>
</html>"""


def send_email(summary_md: str, window: str, now_sgt: datetime) -> None:
    run_label = now_sgt.strftime("%H:%M SGT")
    date_str = now_sgt.strftime("%Y-%m-%d")
    subject = f"Shuning | SeaTalk Brief - {run_label}"
    from_email = os.environ.get("FROM_EMAIL", "assistant@example.com")
    html = _build_html(summary_md, run_label, date_str)

    if os.environ.get("SENDGRID_API_KEY"):
        try:
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
            print(f"  Email sent via SendGrid → {RECIPIENT}")
            return
        except ImportError:
            pass  # fall through to SMTP

    if os.environ.get("SMTP_HOST"):
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_email
        msg["To"] = RECIPIENT
        msg.attach(MIMEText(summary_md, "plain"))
        msg.attach(MIMEText(html, "html"))

        host = os.environ["SMTP_HOST"]
        port = int(os.environ.get("SMTP_PORT", 465))
        user = os.environ["SMTP_USER"]
        password = os.environ["SMTP_PASS"]

        with smtplib.SMTP_SSL(host, port) as server:
            server.login(user, password)
            server.send_message(msg)
        print(f"  Email sent via SMTP → {RECIPIENT}")
        return

    raise RuntimeError(
        "No email transport configured. "
        "Set SENDGRID_API_KEY or SMTP_HOST/SMTP_USER/SMTP_PASS in ~/.goog-assistant.env"
    )


# ─── Optional Redis cache ─────────────────────────────────────────────────────

def _cache_to_redis(summary_md: str, now_sgt: datetime) -> None:
    """Store summary in Redis so it can be retrieved later if needed. Best-effort."""
    try:
        from upstash_redis import Redis

        r = Redis(
            url=os.environ["UPSTASH_REDIS_REST_URL"],
            token=os.environ["UPSTASH_REDIS_REST_TOKEN"],
        )
        key = f"seatalk-summary:{now_sgt.strftime('%Y-%m-%d-%H%M')}"
        r.set(key, summary_md, ex=48 * 3600)
        print(f"  Cached to Redis: {key}")
    except Exception:
        pass  # Redis cache is optional; never fail the run because of it


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate and email a SeaTalk summary."
    )
    parser.add_argument(
        "--hours", type=int, default=2,
        help="Look-back window in hours (default: 2)"
    )
    args = parser.parse_args()

    _load_env()

    now_sgt = datetime.now(SGT)
    print(f"[seatalk_summary] {now_sgt.strftime('%Y-%m-%d %H:%M SGT')}  window={args.hours}h")

    try:
        messages, window = read_messages(args.hours)
        print(f"  Read {len(messages)} messages from CDP")

        summary = generate_summary(messages, window, now_sgt)
        print("  Summary generated")

        send_email(summary, window, now_sgt)
        _cache_to_redis(summary, now_sgt)

    except FileNotFoundError as exc:
        print(f"  ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    except RuntimeError as exc:
        print(f"  ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
    except KeyError as exc:
        print(
            f"  ERROR: Missing environment variable {exc}.\n"
            "  Add it to ~/.goog-assistant.env",
            file=sys.stderr,
        )
        sys.exit(3)


if __name__ == "__main__":
    main()
