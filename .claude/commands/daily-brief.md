---
name: daily-brief
description: Generate Shuning's read-only executive daily briefing from work Gmail, the work calendar, and Google Drive. Use only when Shuning explicitly invokes /daily-brief or directly asks for an inbox plus calendar briefing. Review new activity since the last saved briefing, identify direct asks, deadlines, prep, conflicts, risks, Drive file changes, and produce a concise action-oriented summary.
argument-hint: "[optional date override or focus]"
disable-model-invocation: true
effort: high
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

## Required scope
- Gmail scope: work Gmail only
- Calendar scope: work calendar only
- Timezone: Asia/Singapore
- Default lookback: since the last saved briefing checkpoint
- First-run fallback: previous 72 hours

## Files used by this skill
- Checkpoint file: `.claude/state/daily-brief.json`
- Saved briefings: `briefings/YYYY-MM-DD.md`

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
1. Read `.claude/state/daily-brief.json` if it exists.
2. Use `last_briefing_at` as the default lower bound.
3. If the file is missing, invalid, or empty, fall back to the previous 72 hours.
4. If Shuning explicitly specifies a time window, use that instead.
5. State the effective review window near the top of the briefing.

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

## Step 3: collect Google Drive signals
Review Google Drive files modified within the effective review window using the `gws` CLI.

### Queries to run
Run up to 3 `gws drive files list` queries. Adjust the date range to match the effective review window from Step 1. Use `--page-all` if results may exceed one page.

1. **Recently modified files not owned by Shuning** (catches new shares and collaborator edits):
   ```bash
   gws drive files list --params '{"q": "modifiedTime > '\''LOWER_BOUND_ISO'\'' and not '\''me'\'' in owners", "fields": "files(id,name,owners,modifiedTime,webViewLink,shared,sharingUser,createdTime)", "pageSize": "100"}'
   ```

2. **Files with "TWCB" in the title** (job-scope keyword):
   ```bash
   gws drive files list --params '{"q": "name contains '\''TWCB'\'' and modifiedTime > '\''LOWER_BOUND_ISO'\''", "fields": "files(id,name,owners,modifiedTime,webViewLink)", "pageSize": "50"}'
   ```

3. **Files containing Shuning's name** (direct address via full-text search):
   ```bash
   gws drive files list --params '{"q": "fullText contains '\''Shuning'\'' and modifiedTime > '\''LOWER_BOUND_ISO'\''", "fields": "files(id,name,owners,modifiedTime,webViewLink)", "pageSize": "50"}'
   ```

Replace `LOWER_BOUND_ISO` with the effective lower-bound timestamp in RFC 3339 format (e.g. `2026-04-05T00:00:00+08:00`).

### Relevance criteria
A file is flagged as important if any of these are true:
- **Newly shared with Shuning** — the file was not previously accessible to him (e.g. `createdTime` ≈ `modifiedTime` for his view, or `sharingUser` is present)
- **Title contains "TWCB"** — relevant to Shuning's job scope
- **Addresses Shuning directly** — file content or comments mention him by name

### What to capture for each relevant file
- File name (with `webViewLink` so Shuning can click through)
- Owner
- Last modified time (Singapore time)
- What changed: new share, content edit, or comment
- Why it was flagged (TWCB / shared / direct address)

### Suppression rules
- Suppress files Shuning owns and edited himself (self-edits are not news)
- Suppress trivial or auto-generated files (e.g. system logs, temp files) unless they match TWCB or direct-address criteria
- Deduplicate files that appear in multiple queries

## Step 4: collect calendar signals
Review today's work calendar and also tomorrow's first meeting.

Always include:
- today's meetings
- all-day events
- after-hours meetings
- overlapping meetings
- tomorrow's first meeting if prep today would help

A meeting is important when any of these are true:
- organizer or attendee includes Shuning or a VIP
- external attendees are involved
- the title or description signals a review, decision, escalation, interview, travel, contract, hiring, or action item
- Shuning is likely expected to present, decide, approve, or provide an update
- supporting material, deck, pre-read, or prep is implied

Prep expectations:
- default important-meeting prep window: 30 minutes
- high-stakes meeting prep window: 60 minutes
- flag overlaps explicitly
- do not flag transit or location buffers

## Step 5: classify by priority
Use this default rubric.

### P0
- Due today
- Needs a reply today
- Meeting today needs prep soon
- Boss or VIP direct ask with time sensitivity
- Travel, interview, contract, approval, or escalation issue that can block progress
- Calendar conflict affecting today's execution

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

## Step 6: produce the briefing
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

### 3) Today's schedule table
Use a markdown table with these columns:
- Time
- Meeting
- Why it matters
- Prep / owner note

Include overlaps and after-hours items clearly.

### 4) Google Drive updates
Include this section when there are relevant Drive file changes. For each file show:
- File name as a clickable link (using `webViewLink`)
- Owner
- What changed: new share, content edit, or comment
- Why it was flagged: `TWCB` keyword / newly shared / addresses Shuning directly

Group by reason when there are many items. Skip this section entirely if no relevant Drive activity was found.

### 5) Action sections
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

## Step 7: save the result locally
After producing the briefing:
1. Ensure `briefings/` exists.
2. Save or append the briefing to `briefings/YYYY-MM-DD.md`.
3. Include metadata in the saved file:
   - generated timestamp
   - effective review window
   - any explicit user override
4. If the file already exists for the same date, append a new run section with the current time.
5. Update `.claude/state/daily-brief.json` only after the save succeeds.

Recommended checkpoint update:
- `last_briefing_at` = current Singapore timestamp
- `last_successful_run_at` = current Singapore timestamp
- `last_briefing_file` = saved markdown path

## Step 8: end-state quality check
Before finishing, verify that the briefing answers:
- What must Shuning do today?
- Who needs a reply?
- Which meetings matter?
- What needs prep?
- What is risky or slipping?
- What can safely wait?
- Are there Drive file changes Shuning should be aware of?

If any of those remain unanswered and the source tools could answer them, check once more before concluding.
