"""
SeaTalk helpers shared by _briefing.py (Vercel) and seatalk_summary.py (local).

Vercel usage:
  from _seatalk import fetch_seatalk_snapshot, SEATALK_BRIEF_PROMPT

Local usage (seatalk_summary.py adds its own sys.path setup before importing):
  from api._seatalk import SEATALK_SUMMARY_PROMPT, format_seatalk_payload
"""

import json
import os
from typing import Optional


# ─── System prompts ───────────────────────────────────────────────────────────

SEATALK_BRIEF_PROMPT = """\
You are summarising SeaTalk (internal chat) messages for Shuning Wang's daily briefing.

SeaTalk context:
- SeaTalk is Shopee's primary internal instant-messenger.
- VIP contacts: jianghong.liu@shopee.com, hoi@sea.com, fengc@sea.com
- Key domains: Swarm/OSP, SIP, FP&A, Budget, BPM

Produce a compact SeaTalk section to be embedded inside the daily briefing.
Use this structure:

### SeaTalk Activity
**P0 (act today)**
- [DM/Group] Sender — what was said — suggested action

**P1 (handle soon)**
- [Group] Sender — what was said — suggested action

**P2 (FYI)**
- Brief bullets only

Rules:
- P0: direct message from VIP; @mention of Shuning; key domain topic; direct ask to Shuning
- P1: DM from non-VIP; thread reply where Shuning posted; deadline/approval/meeting message
- P2: general group messages with no action required
- Suppress: bot alerts, automated reports, reaction-only messages, join/leave notifications
- Be concise. Lead with facts. Distinguish inference from fact.
- Use Singapore time (SGT) for all timestamps.
- If no messages: output a single line "No SeaTalk activity in this window."\
"""

SEATALK_SUMMARY_PROMPT = """\
You are Shuning Wang's executive assistant summarising his SeaTalk (internal chat) messages.

SeaTalk context:
- SeaTalk is Shopee's primary internal instant-messenger.
- VIP contacts: jianghong.liu@shopee.com, hoi@sea.com, fengc@sea.com
- Key domains: Swarm/OSP, SIP, FP&A, Budget, BPM

Produce a standalone SeaTalk summary email using this exact structure:

## Executive Snapshot
2–3 sentences: what requires action, biggest signal, who needs a reply.

## P0 — Act now
- [DM/Group] **Sender** — summary — suggested action

## P1 — Handle soon
- [Group] **Sender** — summary — suggested action

## P2 — FYI
- Brief bullets only

## Action Items
- [ ] Concrete next steps, with owner and deadline if mentioned

Priority rubric:
- P0: direct message from VIP; @mention of Shuning; key domain; direct ask; escalation/blocker
- P1: DM from non-VIP; thread where Shuning posted; deadline/meeting/approval message
- P2: general group, no action needed
Suppress: bot alerts, automated reports, reactions, join/leave notifications.

Be concise. Lead with the answer. Use Singapore time (SGT).
If no relevant messages: say so plainly — do not pad with filler.\
"""


# ─── Payload formatter ────────────────────────────────────────────────────────

def format_seatalk_payload(messages: list[dict], window: str) -> str:
    """Format SeaTalk messages as a Claude-ready payload string."""
    if not messages:
        return f"SEATALK WINDOW: {window}\nNo messages in this window.\n"
    return (
        f"SEATALK WINDOW: {window}\n"
        f"MESSAGES ({len(messages)} total):\n"
        f"{json.dumps(messages, indent=2, default=str)}\n"
    )


# ─── Redis snapshot (Vercel-side read) ───────────────────────────────────────

def fetch_seatalk_snapshot(date_str: str) -> Optional[list[dict]]:
    """
    Read the SeaTalk snapshot pushed by seatalk_snapshot.py at 7:50am SGT.
    Returns None if no snapshot is available for that date (script did not run).
    Called from _briefing.py inside the Vercel 8am cron.
    """
    try:
        from upstash_redis import Redis

        r = Redis(
            url=os.environ["UPSTASH_REDIS_REST_URL"],
            token=os.environ["UPSTASH_REDIS_REST_TOKEN"],
        )
        raw = r.get(f"seatalk-snapshot:{date_str}")
        if raw is None:
            return None
        data = json.loads(raw) if isinstance(raw, str) else raw
        # data may be a list of messages or a dict with a "messages" key
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("messages", [])
        return None
    except Exception:
        return None
