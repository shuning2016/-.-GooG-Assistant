#!/usr/bin/env python3
"""
seatalk_doctor.py — preflight check for the two SeaTalk LaunchAgents.

Catches the drift that broke both jobs in Nov 2026:
  • installed plist ≠ repo plist (different interpreter / script paths)
  • interpreter missing a dependency (websocket-client, anthropic, …)
  • caller passes a flag the CDP reader doesn't support (--output)
  • stale script copies under ~/.goog-assistant still lying around
  • ~/.goog-assistant.env missing a required key

Stdlib only, so it runs under any python3. Never prints secret values.

Usage:
  python3 scripts/seatalk_doctor.py            # exit 0 = all good, 1 = problems
  python3 scripts/seatalk_doctor.py --repo-only   # skip installed-plist comparison
"""

from __future__ import annotations

import argparse
import filecmp
import json
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAUNCH_AGENTS = os.path.expanduser("~/Library/LaunchAgents")
ENV_FILE = os.path.expanduser("~/.goog-assistant.env")
STALE_COPIES = [
    os.path.expanduser("~/.goog-assistant/scripts"),
    os.path.expanduser("~/.goog-assistant/use-seatalk"),
]
LABELS = ["com.goog-assistant.seatalk-snapshot", "com.goog-assistant.seatalk-summary"]
REQUIRED_MODULES = ["websocket", "anthropic", "markdown", "upstash_redis"]
READER_REL = os.path.join("scripts", "redux_related_messages.py")

failures: list[str] = []
warnings: list[str] = []


def ok(msg: str) -> None:
    print(f"  ✅ {msg}")


def fail(msg: str) -> None:
    failures.append(msg)
    print(f"  ❌ {msg}")


def warn(msg: str) -> None:
    warnings.append(msg)
    print(f"  ⚠️  {msg}")


def dispatch_python(dispatch_sh: str) -> str | None:
    """Read the default interpreter out of seatalk_summary_dispatch.sh."""
    try:
        with open(dispatch_sh) as f:
            text = f.read()
    except OSError:
        return None
    m = re.search(r'PYTHON="\$\{GOOG_PYTHON:-([^}]+)\}"', text)
    return m.group(1) if m else None


def reader_flags_used(script: str) -> set[str]:
    """Flags the script passes to the CDP reader inside read_messages()."""
    with open(script) as f:
        text = f.read()
    m = re.search(r"\ndef read_messages\(.*?(?=\ndef |\Z)", text, re.DOTALL)
    return set(re.findall(r'"(--[a-z][a-z-]*)"', m.group(0))) if m else set()


def reader_flags_supported(python: str, reader: str) -> set[str] | None:
    try:
        out = subprocess.run([python, reader, "--help"], capture_output=True, text=True, timeout=30)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    return set(re.findall(r"(--[a-z][a-z-]*)", out.stdout))


def check_job(label: str, repo_only: bool) -> None:
    repo_plist = os.path.join(REPO, "LaunchAgents", f"{label}.plist")
    print(f"\n[{label}]")
    # Parse with plutil (what launchd accepts) rather than plistlib's strict XML parser.
    raw = subprocess.run(["plutil", "-convert", "json", "-o", "-", repo_plist],
                         capture_output=True, text=True)
    if raw.returncode != 0:
        fail(f"plist does not parse: {raw.stderr.strip()}")
        return
    cfg = json.loads(raw.stdout)

    # 1. Installed copy must match the repo byte-for-byte.
    if not repo_only:
        installed = os.path.join(LAUNCH_AGENTS, f"{label}.plist")
        if not os.path.exists(installed):
            fail(f"not installed: {installed}")
        elif filecmp.cmp(repo_plist, installed, shallow=False):
            ok("installed plist identical to repo")
        else:
            fail("installed plist differs from repo — run scripts/install_launchagents.sh")

    # 2. Every path the job touches must exist.
    args = cfg["ProgramArguments"]
    env = cfg.get("EnvironmentVariables", {})
    for path in [a for a in args if a.startswith("/")] + [cfg.get("WorkingDirectory", "")]:
        (ok if os.path.exists(path) else fail)(f"path exists: {path}")
    skill_root = env.get("SEATALK_SKILL_ROOT", "")
    reader = os.path.join(skill_root, READER_REL)
    (ok if os.path.exists(reader) else fail)(f"CDP reader exists: {reader}")

    # 3. Work out which interpreter actually runs the Python script.
    if args[0].endswith("bash"):
        python = dispatch_python(args[1])
        script = os.path.join(os.path.dirname(args[1]), "seatalk_summary.py")
    else:
        python, script = args[0], args[1]
    if not python or not os.path.exists(python):
        fail(f"interpreter not found: {python}")
        return
    ok(f"interpreter: {python}")

    # 4. Interpreter has every dependency.
    probe = "import importlib,sys\nfor m in sys.argv[1:]:\n  try: importlib.import_module(m)\n  except Exception: print(m)"
    missing = subprocess.run([python, "-c", probe, *REQUIRED_MODULES],
                             capture_output=True, text=True).stdout.split()
    if missing:
        fail(f"{python} missing modules: {', '.join(missing)} → {python} -m pip install "
             + " ".join("websocket-client" if m == "websocket" else m.replace("_", "-") for m in missing))
    else:
        ok(f"modules importable: {', '.join(REQUIRED_MODULES)}")

    # 5. Caller ↔ reader contract: every flag passed must be supported.
    used = reader_flags_used(script)
    supported = reader_flags_supported(python, reader) if os.path.exists(reader) else None
    if supported is None:
        fail(f"could not run `{os.path.basename(reader)} --help` with {python}")
    elif used - supported:
        fail(f"{os.path.basename(script)} passes unsupported reader flags: {sorted(used - supported)}")
    else:
        ok(f"reader supports all flags used: {sorted(used)}")


def check_env() -> None:
    print("\n[~/.goog-assistant.env]")
    if not os.path.exists(ENV_FILE):
        fail(f"missing {ENV_FILE}")
        return
    keys = set()
    with open(ENV_FILE) as f:
        lines = f.readlines()
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            if v.strip().strip("'\""):
                keys.add(k.strip())
    for k in ("ANTHROPIC_API_KEY", "UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN"):
        (ok if k in keys else fail)(f"{k} set")
    if "SENDGRID_API_KEY" in keys:
        ok("mail via SENDGRID_API_KEY")
    elif {"SMTP_HOST", "SMTP_USER", "SMTP_PASS"} <= keys:
        ok("mail via SMTP_HOST / SMTP_USER / SMTP_PASS")
    else:
        fail("no mail transport: set SENDGRID_API_KEY or SMTP_HOST/SMTP_USER/SMTP_PASS")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo-only", action="store_true", help="skip installed-plist comparison")
    a = p.parse_args()

    print(f"SeaTalk LaunchAgent doctor — repo: {REPO}")
    for label in LABELS:
        check_job(label, a.repo_only)
    check_env()

    print("\n[stale copies]")
    for path in STALE_COPIES:
        if os.path.exists(path):
            warn(f"{path} still exists — no longer used; safe to remove once jobs are verified")
        else:
            ok(f"absent: {path}")

    print(f"\n{'PASS' if not failures else 'FAIL'}: {len(failures)} problem(s), {len(warnings)} warning(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
