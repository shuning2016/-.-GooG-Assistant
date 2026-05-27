#!/usr/bin/env python3
"""
sync_action_items.py — Push the local open-action-items.json to Upstash Redis.

Run this after the local /daily-brief skill updates open-action-items.json so
the Vercel web view and cron can display the same action items widget.

Usage:
    python3 scripts/sync_action_items.py

Environment (from ~/.goog-assistant.env or shell environment):
    UPSTASH_REDIS_REST_URL      Upstash Redis REST endpoint
    UPSTASH_REDIS_REST_TOKEN    Upstash Redis auth token

State file (relative to this script's parent directory):
    .claude/state/open-action-items.json
"""

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
STATE_FILE = PROJECT_ROOT / ".claude" / "state" / "open-action-items.json"
ENV_FILE = Path.home() / ".goog-assistant.env"


def _load_env(path: Path = ENV_FILE) -> None:
    """Load KEY=VALUE pairs from a dotenv file into os.environ (no-op if missing)."""
    if not path.exists():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            val = val.strip().strip('"').strip("'")
            os.environ.setdefault(key.strip(), val)


def main() -> None:
    _load_env()

    try:
        from upstash_redis import Redis
    except ImportError:
        print(
            "upstash-redis not installed. Run: pip3 install upstash-redis",
            file=sys.stderr,
        )
        sys.exit(1)

    if not STATE_FILE.exists():
        print(f"State file not found: {STATE_FILE}", file=sys.stderr)
        sys.exit(1)

    with open(STATE_FILE) as f:
        items = json.load(f)

    try:
        r = Redis(
            url=os.environ["UPSTASH_REDIS_REST_URL"],
            token=os.environ["UPSTASH_REDIS_REST_TOKEN"],
        )
        r.set("open-action-items", json.dumps(items), ex=30 * 24 * 3600)  # 30-day TTL
        open_count = sum(1 for item in items if not item.get("done", False))
        print(f"Synced {len(items)} action items ({open_count} open) to Redis.")
    except KeyError as exc:
        print(
            f"  ERROR: Missing environment variable {exc}.\n"
            "  Set UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN in ~/.goog-assistant.env",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
