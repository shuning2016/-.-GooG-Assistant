---
description: Generate Shuning's read-only executive daily briefing from work Gmail and the work calendar. Use only when Shuning explicitly invokes /daily-brief or directly asks for an inbox plus calendar briefing. Review the past 24 hours of email and calendar activity, identify direct asks, deadlines, prep needs, risks, and produce a concise action-oriented summary.
argument-hint: "[optional date override or focus]"
---

# Daily Brief
Run a read-only executive briefing for Shuning.

## Invocation notes
Optional user arguments:
- `$ARGUMENTS`
- Treat arguments as optional instructions such as a date override, a focus area, or a custom time window.
- If the user gives an explicit date range or scope in the conversation, that overrides the saved checkpoint.

## Non-negotiable constraints
- Never send or modify email.
- Never modify calendar events.
- Never archive, delete, move, label, snooze, or mark messages.
- Never fabricate access, findings, or dates.
- If the Google mail or calendar tools are not available, say so clearly and stop.
- **Never run any `gws drive` command** (no `drive files list`, no `drive files export`, no `drive files get`). Drive scanning is fully disabled. Do not include a "Google Drive Updates" section in any briefing output.

## Required scope
- Gmail scope: work Gmail only
- Calendar scope: work calendar only
- Timezone: Asia/Singapore
- Default lookback: always the past 24 hours from time of run (rolling window, not since last briefing)

## Files used by this skill
- Checkpoint file: `.claude/state/daily-brief.json`
- Saved briefings: `briefings/YYYY-MM-DD HH:mm.md`

If the checkpoint file does not exist, create it when the run completes successfully.

Recommended checkpoint structure:

```json
{
  "version": 1,
  "timezone": "Asia/Singapore",
  "last_briefing_at": null,
  "last_successful_run_at": null,
  "last_briefing_file": null,
  "notes": ""
}
```

## Step 1: determine the time window
1. The default window is always **the past 24 hours** from the current Singapore time (e.g. if it is 08:30 SGT now, look from 08:30 SGT yesterday to now). Do not use the checkpoint date as the lower bound.
2. Read `.claude/state/daily-brief.json` only to record the previous run for the delta comparison in Step 7.
3. If Shuning explicitly specifies a time window in the conversation, use that instead.
4. State the effective review window near the top of the briefing (e.g. "Review window: 2026-05-26 08:30 SGT → 2026-05-27 08:30 SGT").

## Step 2: collect email signals
Review new and newly relevant email activity since the effective lower bound.

Prioritize these messages and threads:
- From `shuning.wang@shopee.com`
- From any VIP sender listed in the project `CLAUDE.md`
- Subject contains `for your action` or the word `action`
- Shuning appears to be addressed directly
- Shuning is in `To:` rather than only `Cc:`
- Direct question or explicit ask
- Mentions deadlines, contracts, travel, interviews, meetings, approvals, confirmations, escalations, or blockers
- Likely needs a reply within the next 2 days

Treat these as direct-address clues:
- `Hi Shuning`
- `Shuning,`
- `Hey Shuning`
- direct question phrasing
- explicit ownership language such as `can you`, `please`, `need you to`, `for your review`, `for your input`, `for your action`

Suppress these unless they introduce a real new action or risk:
- newsletters
- promotions
- receipts
- automated alerts
- recurring daily digests
- repeated daily reports
- routine calendar notifications

Email handling rules:
- Dedupe threads so the briefing is not noisy.
- Use thread context when the latest message alone is ambiguous.
- If needed, check whether Shuning has already replied before labeling something as awaiting reply.
- If reply status cannot be confirmed, say `reply status unclear` rather than guessing.
- Prefer concrete output: sender, subject, due date, requested action, and why it matters.

## Step 3: collect calendar signals
Review today's work calendar and also tomorrow's first meeting.

Always include:
- today's meetings
- all-day events
- after-hours meetings
- tomorrow's first meeting if prep today would help

**Meeting P0** — flag when any of these are true:
- The title or description is related to a key domain (Swarm/OSP, SIP, FP&A, Budget, BPM) **and** Shuning has accepted the invite
- Shuning is specifically asked to present, decide, or provide an update **and** a VIP is involved **and** Shuning has accepted the invite

**Meeting P1** — flag when any of these are true (and not P0):
- Organizer or attendee includes a VIP (10 or more attendees) **and** Shuning has accepted
- External attendees are involved **and** Shuning has accepted
- The title signals a review, decision, escalation, interview, travel, contract, hiring, or action item **and** Shuning has accepted
- A pre-read, deck, or deliverable is implied **and** Shuning has accepted
- **Always check RSVP.** If Shuning has not accepted (declined or no response), downgrade to P2 and note the RSVP status explicitly — do not generate prep recommendations.

**VIP attending a meeting NOT related to any key domain = P1, not P0, regardless of attendee count.**

**Meeting P2:** Total attendees > 30; not key domain; Shuning has not accepted.

Prep expectations:
- default important-meeting prep window: 30 minutes
- high-stakes meeting prep window: 60 minutes
- do not flag meeting overlaps or transit buffers; Shuning handles scheduling herself

## Step 4: fetch and analyze meeting pre-reads
For each important meeting identified in Step 3, search the last 24 hours of email for pre-read materials — typically emails with attachments sent ahead of the meeting.

> **⛔ Drive scanning is disabled.** Do NOT run `gws drive files export`, `gws drive files list`, or any other `gws drive` command at any point. If an email body contains a Google Drive link, note the link URL only — do not access it.

### Search for pre-read emails
For each important meeting, extract 2–3 significant words from the meeting title (strip filler words like "sync", "catch-up", "weekly", "check-in") and run:
```bash
gws gmail users messages list --params '{"userId": "me", "q": "has:attachment newer_than:1d \"MEETING_KEYWORDS\"", "maxResults": "10"}'
```
If that returns nothing, retry without quotes:
```bash
gws gmail users messages list --params '{"userId": "me", "q": "has:attachment newer_than:1d MEETING_KEYWORDS", "maxResults": "10"}'
```

### Get attachment metadata
For each matching email, fetch the full message to identify attachments:
```bash
gws gmail users messages get --params '{"userId": "me", "id": "MESSAGE_ID", "format": "full"}'
```
From the response, extract:
- `payload.parts[*].filename` — attachment name
- `payload.parts[*].mimeType` — file type
- `payload.parts[*].body.attachmentId` — download ID

If the email body contains a `drive.google.com` link, note the link text and sender but **do not access the Drive URL**.

### Download and read attachment content

**Only Option — Binary email attachment** (PDF, PPTX, DOCX, etc.):
Download the raw attachment:
```bash
gws gmail users messages attachments get --params '{"userId": "me", "messageId": "MESSAGE_ID", "id": "ATTACHMENT_ID"}'
```
Decode and save:
```bash
python3 -c "
import json, sys, base64
d = json.load(sys.stdin)
decoded = base64.urlsafe_b64decode(d['data'] + '==')
with open('/tmp/preread_out', 'wb') as f:
    f.write(decoded)
print(len(decoded), 'bytes written')
"
```
Then extract text based on type:
- **PDF** (try `pdftotext` first): `pdftotext /tmp/preread_out /tmp/preread_out.txt && cat /tmp/preread_out.txt`
- **PDF fallback**: `python3 -c "import pypdf, sys; r=pypdf.PdfReader(sys.argv[1]); [print(p.extract_text()) for p in r.pages]" /tmp/preread_out 2>/dev/null`
- **PPTX**: `python3 -c "from pptx import Presentation; prs=Presentation(sys.argv[1]); [print(shape.text_frame.text) for slide in prs.slides for shape in slide.shapes if shape.has_text_frame]" /tmp/preread_out 2>/dev/null`
- If extraction fails, note the attachment name, type, and sender — do not skip the meeting entry.

### What to capture per meeting
For each important meeting where a pre-read was found:
- Attachment name, type, and sender
- 3–5 bullet key points from the content
- Any explicit asks, decisions needed, or data points directly relevant to the meeting
- Open questions or action items addressed to Shuning

### Suppression rules
- Skip `.ics` calendar attachments
- Skip image attachments under 50 KB (likely logos or signatures)
- Skip attachments where text extraction yields fewer than 50 words
- Do not analyze more than 3 attachments per meeting
- If no pre-read is found for a meeting, note: `No pre-read found (past 24 h)`

## Step 6: classify by priority
Use this default rubric.

### P0
- Email or meeting is related to a key domain (Swarm/OSP, SIP, FP&A, Budget, BPM)
- Shuning is specifically @-mentioned or in `To:` with a direct ask **and** a VIP is involved
- Due today or needs a reply today, AND related to a key domain
- Meeting today needs prep soon **and** Shuning has accepted **and** it is key-domain related
- Travel, interview, contract, approval, or escalation issue that can block progress
- **VIP involvement alone without key domain relevance = P1, not P0**

### P1
- Likely needs a reply within 48 hours
- Important meeting tomorrow that needs prep today
- Follow-up with meaningful downside if delayed
- Medium-term risk, dependency, or unresolved owner issue

### P2
- Useful to track but not urgent
- Informational updates with mild action value
- Lower-risk follow-up
- Items that can wait beyond the next 2 days

## Step 6b: include SeaTalk Activity section (if snapshot available)

If a SeaTalk snapshot was pushed to Redis today (by `seatalk_snapshot.py` at 07:50 SGT), read it and include a **SeaTalk Activity** section in the briefing. Follow the format and triage rules in `SEATALK.md` exactly.

**Formatting rules (mandatory):**
- Use `### P0 — Act now`, `### P1 — Handle soon`, `### P2 — FYI` as sub-headers — never inline priority labels like `(act today)`.
- Bold VIP names: `**Jianghong**`, `**Hoi**`, `**Fengc**`.
- Bold group/channel names: `**[Group: CB BPM Lead]**`, `**[DM: Nicholas Lim]**`.
- One bullet per message: `**[Group/DM: Name]** **Sender** — what was said — suggested action`
- Use markdown bold (`**text**`) only. **Never use HTML tags** (`<strong>`, `<em>`, etc.).
- If the snapshot is unavailable, note: `SeaTalk snapshot unavailable (seatalk_snapshot.py may not have run at 07:50 SGT).`

### SeaTalk question tracking
After analyzing the SeaTalk snapshot, check `.claude/state/seatalk-pending-questions.json`. If Shuning asked any questions in SeaTalk that show no clear reply from others yet:
1. Add them to the JSON file (if not already present): `{"id": "slug", "channel": "name", "date_asked": "YYYY-MM-DD", "question": "gist", "resolved": false}`
2. Include a **Pending SeaTalk Questions** sub-section listing all unresolved questions with date and channel.
Only Shuning can mark questions resolved.

## Step 7: produce the briefing
The final answer should be crisp, action-oriented, and easy to skim.

Use this structure:

### 1) Executive brief
Write 2 to 4 sentences that answer:
- what matters most today
- the single biggest risk
- the most important reply or prep item

### 2) Prioritized checklist
Use checkboxes grouped by priority:
- `P0`
- `P1`
- `P2`

Each item should be phrased as an action, not just an observation.

### 2b) Open Action Items ⚠️ MANDATORY — always render this widget, never use a flat list

> **Do NOT include a "Google Drive Updates" section anywhere in the briefing. Drive scanning is disabled.**

Load `.claude/state/open-action-items.json`. Display every item where `done: false` as a color-coded table, sorted by urgency (most urgent first).

**Color coding rule** (compute from today's SGT date and the item's `eta` + `urgency` fields):
- 🔴 **Chase now** — ETA is today or already overdue, OR no ETA and `urgency` = `"high"`
- 🟠 **Chase soon** — ETA is 1–3 days away
- 🟡 **Watch** — ETA is 4–7 days away
- 🟢 **Can wait** — ETA is 8+ days away
- ⚪ **When possible** — no ETA and `urgency` is `"low"`, `"medium"`, or `null`

Sort order within the table: 🔴 → 🟠 → 🟡 → 🟢 → ⚪. Within each color, sort by ETA ascending.

```
## Open Action Items

🔴 Chase now  🟠 Chase soon (≤3 days)  🟡 Watch (4–7 days)  🟢 Can wait (8+ days)  ⚪ When possible

| | Action | Source | ETA | Chase? |
|--|--------|--------|-----|--------|
| 🔴 | [action text] | Email / SeaTalk: [thread or channel name] | overdue / today | Chase now |
| 🟠 | [action text] | Email: [thread] | May 29 (2 days) | Chase soon |
| 🟡 | [action text] | Email: [thread] | Jun 5 (9 days) | Watch |
| ⚪ | [action text] | SeaTalk: [channel] | — | When possible |
```

If the file doesn't exist or has no open items, write: `No open action items.`

**Adding new items:** When you find a new action item for Shuning in a key-domain email thread or SeaTalk message, append it to `.claude/state/open-action-items.json` before producing the briefing. Use this schema:
```json
{
  "id": "unique-kebab-slug",
  "source": "email subject or SeaTalk channel/thread",
  "source_type": "email" | "seatalk",
  "date_identified": "YYYY-MM-DD",
  "action": "one-sentence description of what Shuning needs to do",
  "eta": "YYYY-MM-DD or null",
  "urgency": "high" | "medium" | "low" | null,
  "done": false
}
```

### 3) Today's schedule table
Use a markdown table with these columns:
- Time
- Meeting
- Why it matters
- Prep / owner note

### 4) Meeting pre-reads
Include this section when important meetings exist today. For each important meeting:
- **[Meeting name]** — [time, SGT]
  - Source: attachment name, type, and sender
  - Key points: 3–5 bullets summarizing the content
  - Action items or open questions flagged in the document (if any)
  - `No pre-read found (past 24 h)` if nothing was found for this meeting

Skip this section entirely only if no important meetings exist today.

### 6) Action sections
Include these sections when relevant:
- `What matters today`
- `What can wait`
- `What to reply to`
- `Follow-ups`
- `Risks / watchouts`
- `Suggested priorities`
- `Prep needed for meetings`
- `Short reply suggestions`

Rules for the action sections:
- Keep bullets tight and explicit.
- Use dates and times in Singapore time.
- Distinguish facts from inference.
- Note uncertainty plainly.
- For reply suggestions, provide only short suggested responses or talking points unless Shuning explicitly asks for a full draft.

## Step 8: delta comparison (if last briefing was more than 1 hour ago)

Before saving, check whether this is a refresh run (last briefing exists and was completed more than 1 hour ago in SGT). If yes, read the previous briefing file and compare it with the new briefing. Produce a **"🔄 What Changed Since Last Brief"** section and insert it immediately after the Executive Brief.

Only include a sub-section if there is a real change to report. Skip sub-sections with nothing new.

### 8a. VIP replies
Check email signals collected in Step 2. Flag any thread where a VIP sender (jianghong.liu@shopee.com, hoi@sea.com, fengc@sea.com) has sent a message that was not present in the previous briefing window. Format as:
- **[VIP name]** replied to "[subject]" — [one-line summary of what they said or asked]

### 8b. Meeting changes involving VIPs
Compare today's calendar events against what was listed in the previous briefing. Flag any meeting that:
- Was added, cancelled, or rescheduled since the last briefing
- Had a VIP added or removed as attendee
- Had its time, location, or description materially changed

Format as:
- **[Meeting title]** — [what changed: rescheduled / cancelled / VIP added / details updated]

If the last briefing was less than 1 hour ago, or no previous briefing file exists, skip this step entirely.

## Step 9: save the result and sync state
After producing the briefing:
1. Ensure `briefings/` exists.
2. Save the briefing to `briefings/YYYY-MM-DD HH:mm.md` using the current Singapore time for HH:mm.
3. Include metadata in the saved file:
   - generated timestamp
   - effective review window
   - any explicit user override
4. Update `.claude/state/daily-brief.json` only after the save succeeds.
5. Push action items to Redis so the web view shows the same widget:
   ```bash
   python3 scripts/sync_action_items.py
   ```
   If this fails (e.g. env vars not set), note it but do not block the briefing output.

Recommended checkpoint update:
- `last_briefing_at` = current Singapore timestamp
- `last_successful_run_at` = current Singapore timestamp
- `last_briefing_file` = saved markdown path

## Step 9: end-state quality check
Before finishing, verify that the briefing answers:
- What must Shuning do today?
- Who needs a reply?
- Which meetings matter?
- What needs prep?
- What is risky or slipping?
- What can safely wait?
- Were pre-reads found and summarized for today's important meetings?
- Is the Open Action Items table complete and correctly color-coded?

If any of those remain unanswered and the source tools could answer them, check once more before concluding.
