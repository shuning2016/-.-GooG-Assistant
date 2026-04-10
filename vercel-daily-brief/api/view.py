"""
/api/view?date=YYYY-MM-DD  —  Render a stored daily briefing as a styled HTML page.

Fetches the markdown from Upstash Redis and returns a self-contained HTML document.
No authentication — the link is only shared via the daily email.
"""

from http.server import BaseHTTPRequestHandler
import os
from datetime import datetime
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import markdown as md_lib
from upstash_redis import Redis

SGT = ZoneInfo("Asia/Singapore")


def _redis() -> Redis:
    return Redis(
        url=os.environ["UPSTASH_REDIS_REST_URL"],
        token=os.environ["UPSTASH_REDIS_REST_TOKEN"],
    )


def _render_html(briefing_md: str, date_str: str) -> str:
    body = md_lib.markdown(
        briefing_md,
        extensions=["tables", "nl2br", "fenced_code", "toc"],
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Daily Brief — {date_str}</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
        background:#f4f6f9;color:#1a1a2e;line-height:1.65}}
  /* sticky header */
  .header{{position:sticky;top:0;z-index:100;
           background:linear-gradient(135deg,#1a1a2e 0%,#16213e 100%);
           color:#fff;padding:18px 32px;
           box-shadow:0 2px 8px rgba(0,0,0,.25);
           display:flex;align-items:center;gap:20px}}
  .header h1{{font-size:1.25rem;font-weight:700;flex:1}}
  .header .meta{{font-size:.8rem;opacity:.6}}
  /* layout */
  .container{{max-width:920px;margin:32px auto;padding:0 20px 80px}}
  .card{{background:#fff;border-radius:14px;padding:36px 40px;
         box-shadow:0 1px 4px rgba(0,0,0,.07)}}
  /* typography */
  h1{{font-size:1.7rem;color:#1a1a2e;margin:1.6rem 0 .6rem}}
  h2{{font-size:1.25rem;color:#1a1a2e;margin:1.8rem 0 .5rem;
      border-bottom:2px solid #e9ecef;padding-bottom:.35rem}}
  h3{{font-size:1rem;color:#374151;margin:1.2rem 0 .3rem}}
  p{{margin:.55rem 0}}
  ul,ol{{padding-left:1.5rem;margin:.4rem 0}}
  li{{margin:.3rem 0}}
  /* tables */
  table{{border-collapse:collapse;width:100%;margin:1rem 0;font-size:.9rem}}
  th{{background:#f4f6f9;font-weight:600;padding:9px 13px;
      border:1px solid #dee2e6;text-align:left}}
  td{{padding:8px 13px;border:1px solid #dee2e6;vertical-align:top}}
  tr:nth-child(even){{background:#f9fafb}}
  /* code */
  code{{background:#f1f3f5;padding:2px 6px;border-radius:4px;
        font-family:'SF Mono',Menlo,monospace;font-size:.87em}}
  pre{{background:#f1f3f5;border-radius:8px;padding:16px;overflow-x:auto;margin:.8rem 0}}
  pre code{{background:none;padding:0}}
  /* misc */
  a{{color:#4f46e5;text-decoration:none}}
  a:hover{{text-decoration:underline}}
  blockquote{{border-left:4px solid #4f46e5;padding-left:14px;
              color:#6b7280;margin:.7rem 0}}
  hr{{border:none;border-top:1px solid #e9ecef;margin:1.4rem 0}}
  input[type=checkbox]{{margin-right:6px;accent-color:#4f46e5}}
  /* priority badges */
  li:has(> strong:first-child){{list-style:none;margin-left:-1.5rem;padding-left:1.5rem}}
  /* back-to-top */
  .btt{{position:fixed;bottom:28px;right:28px;background:#4f46e5;color:#fff;
        border:none;border-radius:50%;width:42px;height:42px;cursor:pointer;
        font-size:1.2rem;box-shadow:0 2px 8px rgba(0,0,0,.2);
        display:flex;align-items:center;justify-content:center;opacity:.8}}
  .btt:hover{{opacity:1}}
  @media(max-width:600px){{
    .card{{padding:20px 18px}}
    .container{{padding:0 10px 60px}}
  }}
</style>
</head>
<body>
<div class="header">
  <h1>Daily Brief</h1>
  <div class="meta">{date_str} &nbsp;·&nbsp; Asia/Singapore</div>
</div>
<div class="container">
  <div class="card">
    {body}
  </div>
</div>
<button class="btt" onclick="window.scrollTo({{top:0,behavior:'smooth'}})" title="Back to top">↑</button>
</body>
</html>"""


def _not_found(date_str: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Not found</title>
<style>
  body{{font-family:system-ui;display:flex;align-items:center;justify-content:center;
        min-height:100vh;margin:0;background:#f4f6f9;color:#1a1a2e}}
  .box{{text-align:center;padding:40px}}
  h2{{font-size:1.5rem;margin-bottom:8px}}
  p{{color:#6b7280}}
</style>
</head>
<body>
<div class="box">
  <h2>No brief found for {date_str}</h2>
  <p>Briefings are stored for 7 days. Check that the cron ran successfully.</p>
</div>
</body>
</html>"""


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        date_str = params.get("date", [datetime.now(SGT).strftime("%Y-%m-%d")])[0]

        # Basic date format guard
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            self._write(400, "text/plain", b"Invalid date format. Use YYYY-MM-DD.")
            return

        try:
            r = _redis()
            briefing_md = r.get(f"daily-brief:{date_str}")
        except Exception as exc:
            self._write(500, "text/plain", f"Redis error: {exc}".encode())
            return

        if not briefing_md:
            html = _not_found(date_str).encode("utf-8")
            self._write(404, "text/html; charset=utf-8", html)
            return

        html = _render_html(briefing_md, date_str).encode("utf-8")
        self._write(200, "text/html; charset=utf-8", html)

    def _write(self, code: int, content_type: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # Cache for 5 min in browser; CDN won't cache (private)
        self.send_header("Cache-Control", "private, max-age=300")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass
