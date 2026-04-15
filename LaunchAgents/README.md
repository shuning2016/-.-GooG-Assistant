# LaunchAgents — Installation Guide

Two macOS LaunchAgents schedule the SeaTalk integration scripts.
Both require the Mac to be **on and awake** at the scheduled time.

---

## Prerequisites

1. **SeaTalk running with CDP debug port** (one-time setup):
   ```bash
   cd ~/Library/CloudStorage/.../use-seatalk
   bash scripts/seatalk-restart-debug.sh
   ```
   Install the auto-debug LaunchAgent from use-seatalk so SeaTalk always restarts with debugging:
   ```bash
   cp scripts/com.seatalk.debug.plist ~/Library/LaunchAgents/
   launchctl load ~/Library/LaunchAgents/com.seatalk.debug.plist
   ```

2. **Python dependencies** (installed for the user running the LaunchAgent):
   ```bash
   pip3 install anthropic upstash-redis websocket-client markdown sendgrid
   ```

3. **Credentials** in `~/.goog-assistant.env`:
   ```bash
   ANTHROPIC_API_KEY=sk-ant-...
   SENDGRID_API_KEY=SG....           # or SMTP_HOST/SMTP_USER/SMTP_PASS
   FROM_EMAIL=assistant@example.com
   UPSTASH_REDIS_REST_URL=https://...
   UPSTASH_REDIS_REST_TOKEN=...
   # Optional: override use-seatalk repo location
   # SEATALK_SKILL_ROOT=/path/to/use-seatalk
   ```

---

## Install the LaunchAgents

```bash
REPO="/Users/shuning.wang/Library/CloudStorage/GoogleDrive-shuning2016@gmail.com/My Drive/My Projects/Working Efficiency/GooG Assistant"

# 1. Snapshot agent (07:50 SGT) — feeds the Vercel 8am briefing
cp "$REPO/LaunchAgents/com.goog-assistant.seatalk-snapshot.plist" ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.goog-assistant.seatalk-snapshot.plist

# 2. Summary agent (10:00 / 12:00 / 15:00 / 19:00 SGT) — emails SeaTalk summaries
cp "$REPO/LaunchAgents/com.goog-assistant.seatalk-summary.plist" ~/Library/LaunchAgents/
chmod +x "$REPO/scripts/seatalk_summary_dispatch.sh"
launchctl load ~/Library/LaunchAgents/com.goog-assistant.seatalk-summary.plist
```

## Verify

```bash
launchctl list | grep goog-assistant
```
Both entries should appear (exit code 0 after first fire = success).

## Test a manual run

```bash
# Test snapshot
python3 "$REPO/scripts/seatalk_snapshot.py" --hours 1

# Test summary (sends an email)
python3 "$REPO/scripts/seatalk_summary.py" --hours 1
```

## Logs

| File | Contents |
|---|---|
| `/tmp/goog-assistant-seatalk-snapshot.log` | Snapshot stdout |
| `/tmp/goog-assistant-seatalk-snapshot.err` | Snapshot errors |
| `/tmp/goog-assistant-seatalk-summary.log` | Summary stdout |
| `/tmp/goog-assistant-seatalk-summary.err` | Summary errors |

## Uninstall

```bash
launchctl unload ~/Library/LaunchAgents/com.goog-assistant.seatalk-snapshot.plist
launchctl unload ~/Library/LaunchAgents/com.goog-assistant.seatalk-summary.plist
rm ~/Library/LaunchAgents/com.goog-assistant.seatalk-*.plist
```
