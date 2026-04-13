"""
Shared session-cookie helpers used by auth.py and view.py.

Cookie format:  email:expiry_unix_ts:hmac_sha256_hex
Signed with SESSION_SECRET env var.  Expires after 7 days.
"""

import hashlib
import hmac
import os
import time

COOKIE_NAME = "_goog_session"
COOKIE_MAX_AGE = 7 * 24 * 3600  # 7 days
ALLOWED_EMAILS = frozenset({"shuning.wang@shopee.com", "shuning2016@gmail.com"})


def _secret() -> bytes:
    return os.environ.get("SESSION_SECRET", "change-me-in-vercel").encode()


def make_cookie(email: str) -> str:
    expiry = int(time.time()) + COOKIE_MAX_AGE
    payload = f"{email}:{expiry}"
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def verify_cookie(value: str) -> str | None:
    """Returns the email address if the cookie is valid, None otherwise."""
    try:
        # value = email:expiry:sig  (email may contain @, so rsplit from right)
        parts = value.rsplit(":", 2)
        if len(parts) != 3:
            return None
        email, expiry_str, sig = parts
        if int(expiry_str) < int(time.time()):
            return None
        payload = f"{email}:{expiry_str}"
        expected = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        return email if email in ALLOWED_EMAILS else None
    except Exception:
        return None


def parse_cookies(cookie_header: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in cookie_header.split(";"):
        part = part.strip()
        if "=" in part:
            k, _, v = part.partition("=")
            cookies[k.strip()] = v.strip()
    return cookies
