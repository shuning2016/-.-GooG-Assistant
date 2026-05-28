#!/usr/bin/env python3
"""
seatalk_snapshot.py — Push a 24-hour SeaTalk message snapshot to Upstash Redis.

Run this locally at 07:50 SGT daily (via LaunchAgent) so the Vercel 8am cron
can include SeaTalk messages in the morning briefing.

Usage:
    python3 scripts/seatalk_snapshot.py [--hours N]

Environment (from ~/.goog-assistant.env or shell environment):
    UPSTASH_REDIS_REST_URL      Upstash Redis REST endpoint
    UPSTASH_REDIS_REST_TOKEN    Upstash Redis auth token
    SEATALK_SKILL_ROOT          Path to use-seatalk repo (auto-detected if unset)
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

SGT = ZoneInfo("Asia/Singapore")
DEFAULT_HOURS = 24
# Group-name substrings that are always captured regardless of Shuning's participation.
# Any group whose name contains one of these words (case-insensitive) is treated as a
# key-domain group and all its messages are included in the snapshot.
KEY_DOMAIN_GROUPS = "Swarm,OSP,SIP,FP&A,Budget,BPM"
# Default path mirrors the user's Google Drive layout; override with SEATALK_SKILL_ROOT
_DRIVE = os.path.expanduser(
    "~/Library/CloudStorage/GoogleDrive-shuning2016@gmail.com"
    "/My Drive/My Projects/Working Efficiency/use-seatalk"
)


def _load_env(path: str = "~/.goog-assistant.env") -> None:
    """Load KEY=VALUE pairs from a dotenv file into os.environ (no-op if missing)."""
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


def _seatalk_root() -> str:
    return os.environ.get("SEATALK_SKILL_ROOT", _DRIVE)


def read_messages(hours: int, retries: int = 3, retry_delay: int = 30) -> list[dict]:
    """Run redux_related_messages.py and return parsed message list.
    Retries up to `retries` times with `retry_delay` seconds between attempts
    to handle the case where Chrome/SeaTalk isn't open yet at script start.
    """
    reader = os.path.join(_seatalk_root(), "scripts", "redux_related_messages.py")
    if not os.path.exists(reader):
        raise FileNotFoundError(
            f"CDP reader not found at {reader}\n"
            "Set SEATALK_SKILL_ROOT to the use-seatalk repo path."
        )

    last_err = ""
    for attempt in range(1, retries + 1):
        result = subprocess.run(
            [sys.executable, reader, "--last-hours", str(hours),
             "--watch-groups", KEY_DOMAIN_GROUPS],
            capture_output=True,
            text=True,
            timeout=90,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            if isinstance(data, list):
                return data
            return data.get("messages", [])
        last_err = result.stderr.strip()
        if attempt < retries:
            print(f"  CDP attempt {attempt} failed, retrying in {retry_delay}s…", file=sys.stderr)
            time.sleep(retry_delay)

    raise RuntimeError(f"CDP reader failed after {retries} attempts:\n{last_err}")


def push_to_redis(date_str: str, messages: list[dict], retries: int = 4, retry_delay: int = 15) -> None:
    """Store messages in Upstash Redis under key seatalk-snapshot:{date}.
    Retries up to `retries` times to handle transient network/DNS issues
    that occur right after the Mac wakes from sleep.
    """
    try:
        from upstash_redis import Redis
    except ImportError:
        raise ImportError(
            "upstash-redis not installed. Run: pip3 install upstash-redis"
        )

    r = Redis(
        url=os.environ["UPSTASH_REDIS_REST_URL"],
        token=os.environ["UPSTASH_REDIS_REST_TOKEN"],
    )
    key = f"seatalk-snapshot:{date_str}"
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            r.set(key, json.dumps(messages, default=str), ex=24 * 3600)
            print(f"  Pushed {len(messages)} messages → Redis key: {key}")
            return
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                print(f"  Redis push attempt {attempt} failed ({exc}), retrying in {retry_delay}s…",
                      file=sys.stderr)
                time.sleep(retry_delay)
    raise RuntimeError(f"Redis push failed after {retries} attempts: {last_exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Push SeaTalk snapshot to Redis.")
    parser.add_argument(
        "--hours", type=int, default=DEFAULT_HOURS,
        help=f"Look-back window in hours (default: {DEFAULT_HOURS})"
    )
    args = parser.parse_args()

    _load_env()

    now_sgt = datetime.now(SGT)
    date_str = now_sgt.strftime("%Y-%m-%d")
    print(f"[seatalk_snapshot] {now_sgt.strftime('%Y-%m-%d %H:%M SGT')}  window={args.hours}h")

    try:
        messages = read_messages(args.hours)
        print(f"  Read {len(messages)} SeaTalk messages from CDP")
        push_to_redis(date_str, messages)
        print("  Done.")
    except FileNotFoundError as exc:
        print(f"  ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    except RuntimeError as exc:
        print(f"  ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
    except KeyError as exc:
        print(
            f"  ERROR: Missing environment variable {exc}.\n"
            "  Set UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN in ~/.goog-assistant.env",
            file=sys.stderr,
        )
        sys.exit(3)


if __name__ == "__main__":
    main()
