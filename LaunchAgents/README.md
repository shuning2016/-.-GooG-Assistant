# LaunchAgents — Installation Guide

Two macOS LaunchAgents schedule the SeaTalk integration scripts.
Both require the Mac to be **on and awake** at the scheduled time.

| Job | Schedule (SGT) | Runs | Output |
|---|---|---|---|
| `com.goog-assistant.seatalk-snapshot` | 07:50, 08:05 | `scripts/seatalk_snapshot.py --hours 24` | Redis key `seatalk-snapshot:YYYY-MM-DD` for the 8am Vercel brief |
| `com.goog-assistant.seatalk-summary` | 10:00, 12:00, 15:00, 19:00 | `scripts/seatalk_summary_dispatch.sh` → `seatalk_summary.py` | Summary email to Shuning.wang@shopee.com |

---

## Source of truth — one copy only

| What | Canonical location |
|---|---|
| Plists | this repo, `LaunchAgents/*.plist` (installed copies must be byte-identical) |
| Job scripts | this repo, `scripts/` — the plists run them **in place** |
| CDP reader | `~/My Projects/Working Efficiency/use-seatalk` (its own git repo), via `SEATALK_SKILL_ROOT` |
| Interpreter | `/usr/local/bin/python3.11` |
| Credentials | `~/.goog-assistant.env` |

Do **not** copy scripts or use-seatalk anywhere else. In Nov 2026 stale copies under
`~/.goog-assistant/scripts` and `~/.goog-assistant/use-seatalk` drifted from the repo and
broke both jobs (summary passed `--output`, which the reader never supported; snapshot ran
under a python with no `websocket-client`).

---

## Prerequisites

1. **SeaTalk running with the CDP debug port** — set up from the use-seatalk repo
   (`scripts/seatalk-debug-autostart.sh` / `com.seatalk.debug.plist`). Not managed here.

2. **Python dependencies for python3.11:**
   ```bash
   /usr/local/bin/python3.11 -m pip install anthropic upstash-redis websocket-client markdown
   ```
   (`/usr/bin/python3` 3.9 lacks `anthropic`, so the jobs don't use it.)

3. **Credentials** in `~/.goog-assistant.env`:
   ```bash
   ANTHROPIC_API_KEY=sk-ant-...
   SMTP_HOST=... SMTP_PORT=465 SMTP_USER=... SMTP_PASS=...   # or SENDGRID_API_KEY=SG....
   UPSTASH_REDIS_REST_URL=https://...
   UPSTASH_REDIS_REST_TOKEN=...
   SEATALK_SELF_ID=...
   ```

---

## Install / re-sync

```bash
bash scripts/install_launchagents.sh
```

Copies both plists, reloads them (same jobs and schedules; nothing runs immediately), then
runs the doctor. Re-run it after **any** plist change.

## Check health

```bash
python3 scripts/seatalk_doctor.py                       # exit 0 = healthy
python3 -m unittest scripts/test_seatalk_doctor.py      # doctor's own tests
```

The doctor checks: installed plists match the repo, every path exists, the interpreter has
all modules, every flag the scripts pass is supported by the reader, the env file has the
required keys, and warns about stale copies.

## Manual run (same environment as launchd)

```bash
launchctl kickstart -k gui/$(id -u)/com.goog-assistant.seatalk-snapshot
launchctl kickstart -k gui/$(id -u)/com.goog-assistant.seatalk-summary   # sends an email
launchctl print gui/$(id -u)/com.goog-assistant.seatalk-summary | grep "last exit code"
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
for l in com.goog-assistant.seatalk-snapshot com.goog-assistant.seatalk-summary; do
  launchctl bootout gui/$(id -u)/$l; rm ~/Library/LaunchAgents/$l.plist
done
```
