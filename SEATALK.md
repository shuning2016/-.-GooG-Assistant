# SeaTalk Integration Instructions

## Purpose
Reads Shuning's SeaTalk Desktop messages as an information source, alongside Gmail and Google
Calendar. SeaTalk is the primary internal instant-messaging channel at Sea/Shopee. Messages here
carry high signal for real-time decisions, action items, and escalations that may never reach email.

## How SeaTalk messages are read
SeaTalk is an Electron app. Its messages live in an in-memory Redux store, accessed via Chrome
DevTools Protocol (CDP). There is no public API.

- **CDP reader**: `use-seatalk` skill at `$SEATALK_SKILL_ROOT`
  - Default path: `~/Library/CloudStorage/.../My Projects/Working Efficiency/use-seatalk`
  - Set `SEATALK_SKILL_ROOT` env var to override
- **SeaTalk must be running** with remote debugging enabled (port 19222)
  - Run `seatalk-restart-debug.sh` once to patch `app.asar` for Electron 30+; after that it
    auto-starts with debugging via the LaunchAgent
- **Key scripts** (in `$SEATALK_SKILL_ROOT/scripts/`):
  - `redux_related_messages.py` — time-window query: messages related to Shuning (sent by him,
    mentioning him, or in threads he participated in)
  - `cdp-reader.py` — full CDP client with `listen`, `threads`, `unread`, `reply` and more

## Data windows by run type
| Run | Window | Who calls it |
|---|---|---|
| 8 am daily brief | last 24 h | `seatalk_snapshot.py` (local 7:50am) → Redis → Vercel cron |
| 10:00 SGT summary | last 3 h (07:00–10:00) | `seatalk_summary.py --hours 3` (local LaunchAgent) |
| 12:00 SGT summary | last 2 h (10:00–12:00) | `seatalk_summary.py --hours 2` |
| 15:00 SGT summary | last 3 h (12:00–15:00) | `seatalk_summary.py --hours 3` |
| 19:00 SGT summary | last 4 h (15:00–19:00) | `seatalk_summary.py --hours 4` |

## SeaTalk message triage rules

Apply these rules when classifying SeaTalk messages. They mirror the Gmail triage rubric.

### P0 — Act today
A SeaTalk message is P0 when **any** of these are true:
- It is a **direct (private) message from a VIP**: jianghong.liu, hoi, fengc — regardless of group
  size
- It **mentions Shuning** (`@shuning.wang` / `@Shuning`) in any channel
- Its content is related to a **key domain**: Swarm/OSP, SIP, FP&A, Budget, BPM
- It contains a **direct ask, deadline, escalation, or blocker** directed at Shuning's area
- It is a **thread reply in a thread Shuning owns** (he is the original poster)

### P1 — Handle within 48 h
A SeaTalk message is P1 when **any** of these are true (and not already P0):
- Direct private message from a non-VIP colleague
- Reply to a thread where Shuning **previously posted** (but he is not the original poster)
- Message about a meeting, deadline, deliverable, approval, or contract that touches Shuning's
  work
- Group message in a channel with < 20 members where Shuning's input is implied

### P2 — Track but not urgent
A SeaTalk message is P2 when **all** of these are true:
- Not in a key domain
- No direct mention of Shuning
- No VIP involved in a small-group context
- Informational, FYI, or announcement with no action required from Shuning

### Suppressions
Suppress entirely unless they contain a new risk, direct ask, or deadline:
- Automated bot notifications (CI/CD, alerts, monitoring)
- Recurring daily report bots
- Pure emoji/reaction-only messages
- System join/leave notifications

## Groups and contacts to monitor
Configure watched groups and admin IDs in `~/.use-seatalk/seatalk-listener.conf`:
```
SEATALK_WATCH_GROUPS=group-id-1,group-id-2,...
SEATALK_ADMIN_IDS=shuning.wang@shopee.com,...
```
Leave blank to monitor all groups/buddies visible in the running client.

## SeaTalk summary email format
Each scheduled summary (10:00 / 12:00 / 15:00 / 19:00 SGT) follows this structure:

```
## SeaTalk Brief — HH:MM SGT
**Window**: XX:XX → HH:MM SGT  |  N messages reviewed

### Executive Snapshot
2–3 sentences covering: what requires action, biggest signal, who needs a reply.

### P0 — Act now
- [DM/Group name] **Sender** — summary — suggested action

### P1 — Handle soon
- [Group name] **Sender** — summary — suggested action

### P2 — FYI
- Brief bullets, no detail needed

### Action Items
- [ ] Concrete next steps for Shuning, with owner and deadline if mentioned
```

## Environment variables required
These must be set in `~/.goog-assistant.env` (local) or as Vercel environment variables (cloud):

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key for summary generation |
| `SENDGRID_API_KEY` | Primary email transport (or use SMTP vars below) |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` | Fallback SMTP transport |
| `FROM_EMAIL` | Sender address shown in email headers |
| `UPSTASH_REDIS_REST_URL` | Redis for snapshot handoff to Vercel |
| `UPSTASH_REDIS_REST_TOKEN` | Redis auth |
| `SEATALK_SKILL_ROOT` | Path to `use-seatalk` repo (default: auto-detected) |

## Local scripts (in `scripts/`)
| Script | Purpose |
|---|---|
| `seatalk_snapshot.py` | Run at 7:50am — reads CDP, pushes 24h snapshot to Redis |
| `seatalk_summary.py` | Run at 10/12/15/19h — reads CDP, generates summary, emails it |

## LaunchAgents (in `LaunchAgents/`)
| Plist | Schedule | Script |
|---|---|---|
| `com.goog-assistant.seatalk-snapshot.plist` | 07:50 SGT daily | `seatalk_snapshot.py` |
| `com.goog-assistant.seatalk-summary.plist` | 10:00, 12:00, 15:00, 19:00 SGT | `seatalk_summary.py` |

Install both into `~/Library/LaunchAgents/` and load with `launchctl load`.
See `LaunchAgents/README.md` for installation steps.

## Hard guardrails
- **Read-only by default.** Do not send SeaTalk messages unless explicitly asked.
- `SEATALK_ALLOW_SEND` must be explicitly set to `true` to enable any outbound messages.
- Never expose raw message bodies beyond what is needed for a useful summary.
- Never claim SeaTalk data is available if `seatalk_snapshot.py` was not run before 8am.
- If SeaTalk is not reachable via CDP, note it clearly in the briefing rather than omitting
  silently.

## Troubleshooting
- **CDP not reachable**: Run `seatalk-restart-debug.sh` to restart SeaTalk with debugging enabled.
- **No messages returned**: Check `SEATALK_WATCH_GROUPS` and ensure SeaTalk is open and logged in.
- **Snapshot missing from 8am brief**: Verify the LaunchAgent is loaded and ran at 7:50am;
  check `/tmp/goog-assistant-seatalk.log`.
- **Email not sent**: Verify `SENDGRID_API_KEY` or SMTP vars in `~/.goog-assistant.env`.
