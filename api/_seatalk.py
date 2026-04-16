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
- Shuning's handle: @shuning.wang / @Shuning
- VIP contacts: jianghong.liu@shopee.com, hoi@sea.com, fengc@sea.com
- Key domains: Swarm, OSP, SIP, FP&A, Budget, BPM

Produce a compact SeaTalk section to embed in the daily briefing.
Use this EXACT markdown format — no HTML tags:

**P0 (act today)**
- [Group: Name] **Sender** — what was said — suggested action

**P1 (handle soon)**
- [Group: Name] **Sender** — what was said — suggested action

**P2 (FYI)**
- [Group: Name] Brief bullet only

Use [DM: Name] for private messages, [Group: Name] for group messages.
Bold VIP names with **Name**.

P0 — classify as P0 when ANY of these are true:
  1. Direct private message (DM) from a VIP (jianghong.liu, hoi, fengc) — regardless of content
  2. Message @mentions Shuning (@shuning.wang or @Shuning) in any group
  3. Message CONTENT is about a key domain: Swarm, OSP, SIP, FP&A, Budget, or BPM
  4. Message is FROM A GROUP whose name contains a key domain word — e.g. a group called
     "Swarm", "BPM Leads", "SIP Core Leads", "OSP team", "FP&A". Every message in that
     group is P0 regardless of whether the content explicitly mentions the domain.
  5. Direct ask, deadline, escalation, or blocker directed at Shuning or his area
  6. Thread reply in a thread that Shuning originally started (he is the OP)

P1 — when ANY of these are true (and not already P0):
  • Direct private message from a non-VIP colleague
  • Reply in a thread where Shuning previously posted (but is not the OP)
  • Message about a meeting, deadline, deliverable, approval, or contract touching Shuning's work
  • Group message in a channel with fewer than 20 members where Shuning's input is implied

P2 — informational, general group message, no action required from Shuning.

Suppress ONLY these (do not suppress key-domain group messages even if they look routine):
  • Automated bot notifications (CI/CD, monitoring, system alerts)
  • Recurring daily report bots with no new action
  • Pure emoji / reaction-only messages
  • System join/leave notifications

Be concise. Lead with facts. Distinguish inference from fact.
Use Singapore time (SGT) for all timestamps.
If no messages: output a single line "No SeaTalk activity in this window."\
"""

SEATALK_SUMMARY_PROMPT = """\
You are Shuning Wang's executive assistant summarising his SeaTalk (internal chat) messages.

SeaTalk context:
- SeaTalk is Shopee's primary internal instant-messenger.
- Shuning's handle: @shuning.wang / @Shuning
- VIP contacts: jianghong.liu@shopee.com, hoi@sea.com, fengc@sea.com
- Key domains: Swarm, OSP, SIP, FP&A, Budget, BPM

Produce a standalone SeaTalk summary email using this exact structure — no HTML tags:

## Executive Snapshot
2–3 sentences: what requires action, biggest signal, who needs a reply.

## P0 — Act now
- [DM/Group: Name] **Sender** — what was said — suggested action

## P1 — Handle soon
- [Group: Name] **Sender** — what was said — suggested action

## P2 — FYI
- [Group: Name] Brief bullet only (no detail needed)

## Action Items
- [ ] Concrete next steps, with owner and deadline if mentioned

P0 — classify as P0 when ANY of these are true:
  1. Direct private message (DM) from a VIP (jianghong.liu, hoi, fengc) — regardless of content
  2. Message @mentions Shuning (@shuning.wang or @Shuning) in any group
  3. Message CONTENT is about a key domain: Swarm, OSP, SIP, FP&A, Budget, or BPM
  4. Message is FROM A GROUP whose name contains a key domain word — e.g. a group called
     "Swarm", "BPM Leads", "SIP Core Leads", "OSP team", "FP&A". Every message in that
     group is P0 regardless of whether the content explicitly mentions the domain.
  5. Direct ask, deadline, escalation, or blocker directed at Shuning or his area
  6. Thread reply in a thread that Shuning originally started (he is the OP)

P1 — when ANY of these are true (and not already P0):
  • Direct private message from a non-VIP colleague
  • Reply in a thread where Shuning previously posted (but is not the OP)
  • Message about a meeting, deadline, deliverable, approval, or contract touching Shuning's work
  • Group message in a channel with fewer than 20 members where Shuning's input is implied

P2 — informational, general group message, no action required from Shuning.

Suppress ONLY these (do not suppress key-domain group messages even if they look routine):
  • Automated bot notifications (CI/CD, monitoring, system alerts)
  • Recurring daily report bots with no new action
  • Pure emoji / reaction-only messages
  • System join/leave notifications

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
