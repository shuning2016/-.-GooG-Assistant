"""
/api/view?date=YYYY-MM-DD  —  Render a stored daily briefing as a styled HTML page.

Fetches the markdown from Upstash Redis and returns a self-contained HTML document.
Requires a valid Google session cookie (set by /api/auth).
Only shuning.wang@shopee.com and shuning2016@gmail.com are authorised.
"""

from http.server import BaseHTTPRequestHandler
import os
import sys
import urllib.parse
from datetime import datetime
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))
from _session import COOKIE_NAME, parse_cookies, verify_cookie

import markdown as md_lib
from upstash_redis import Redis

SGT = ZoneInfo("Asia/Singapore")


def _redis() -> Redis:
    return Redis(
        url=os.environ["UPSTASH_REDIS_REST_URL"],
        token=os.environ["UPSTASH_REDIS_REST_TOKEN"],
    )


def _trigger_url() -> str:
    secret = os.environ.get("TRIGGER_SECRET", "")
    return f"/api/trigger?token={secret}" if secret else ""


def _render_html(briefing_md: str, date_str: str) -> str:
    body = md_lib.markdown(
        briefing_md,
        extensions=["tables", "nl2br", "fenced_code", "toc"],
    )
    turl = _trigger_url()
    run_btn = (
        f'<button class="run-btn" id="runBtn" onclick="runBrief()">&#9654; Run Now</button>'
        if turl else ""
    )
    # Build the runBrief JS function as a separate variable — nested f-strings
    # with the same quote type are a syntax error in Python < 3.12 (Vercel runtime).
    run_brief_fn = (
        "function runBrief() {\n"
        "  var btn = document.getElementById('runBtn');\n"
        "  if (btn) { btn.disabled = true; btn.textContent = 'Running\u2026'; }\n"
        f"  window.location.href = '{turl}';\n"
        "}"
    ) if turl else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Daily Brief \u2014 {date_str}</title>
<style>
  :root {{
    --blue:    #1D6FE8;
    --teal:    #0891b2;
    --green:   #16a34a;
    --amber:   #d97706;
    --red:     #dc2626;
    --bg:      #F5F6FA;
    --surface: #ffffff;
    --text:    #1a1a2e;
    --muted:   #6b7280;
    --border:  #e5e7eb;
    --header-h: 58px;
  }}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
        background:var(--bg);color:var(--text);line-height:1.65;font-size:15px}}

  /* ── Navbar ── */
  .navbar{{
    position:sticky;top:0;z-index:200;height:var(--header-h);
    background:linear-gradient(135deg,#1a1a2e 0%,#16213e 100%);
    color:#fff;padding:0 28px;
    display:flex;align-items:center;gap:10px;
    box-shadow:0 2px 10px rgba(0,0,0,.3);
  }}
  .navbar-title{{font-size:1.05rem;font-weight:700;letter-spacing:.3px;flex:1}}
  .navbar-date{{
    font-size:.78rem;background:rgba(255,255,255,.1);
    border-radius:20px;padding:4px 12px;white-space:nowrap;
  }}
  .navbar-tz{{font-size:.72rem;opacity:.5}}
  .run-btn{{
    background:var(--blue);color:#fff;border:none;border-radius:6px;
    padding:7px 16px;font-size:.82rem;font-weight:600;cursor:pointer;
    white-space:nowrap;transition:opacity .15s;letter-spacing:.2px;
  }}
  .run-btn:hover{{opacity:.85}}
  .run-btn:disabled{{opacity:.45;cursor:default}}
  /* SeaTalk check button */
  .st-btn{{
    background:var(--teal);color:#fff;border:none;border-radius:6px;
    padding:7px 14px;font-size:.82rem;font-weight:600;cursor:pointer;
    white-space:nowrap;transition:opacity .15s;letter-spacing:.2px;
    display:flex;align-items:center;gap:6px;
  }}
  .st-btn:hover{{opacity:.85}}
  .st-btn:disabled{{opacity:.45;cursor:default}}
  .st-spinner{{
    width:13px;height:13px;border:2px solid rgba(255,255,255,.35);
    border-top-color:#fff;border-radius:50%;animation:spin .7s linear infinite;
    display:none;
  }}
  .st-btn.loading .st-spinner{{display:inline-block}}
  .st-btn.loading .st-label{{opacity:.6}}
  @keyframes spin{{to{{transform:rotate(360deg)}}}}

  /* ── SeaTalk drawer ── */
  .st-drawer{{
    position:sticky;top:var(--header-h);z-index:190;
    max-height:0;overflow:hidden;
    transition:max-height .35s ease,box-shadow .35s ease;
    background:#ecfeff;border-bottom:2px solid var(--teal);
    box-shadow:none;
  }}
  .st-drawer.open{{
    max-height:520px;
    box-shadow:0 4px 16px rgba(8,145,178,.15);
    overflow-y:auto;
  }}
  .st-inner{{
    max-width:940px;margin:0 auto;padding:18px 24px 20px;
  }}
  .st-meta{{
    display:flex;align-items:center;gap:12px;margin-bottom:12px;
    font-size:.8rem;color:var(--teal);font-weight:600;
  }}
  .st-close{{
    margin-left:auto;background:none;border:none;font-size:1.1rem;
    cursor:pointer;color:var(--teal);opacity:.7;line-height:1;padding:2px 6px;
  }}
  .st-close:hover{{opacity:1}}
  .st-body h3{{font-size:.95rem;color:#164e63;margin:1rem 0 .3rem;font-weight:700}}
  .st-body p{{margin:.4rem 0;font-size:.88rem;color:#374151}}
  .st-body ul{{padding-left:1.3rem;margin:.3rem 0}}
  .st-body li{{font-size:.88rem;margin:.25rem 0;color:#374151}}
  .st-body strong{{color:var(--text)}}
  .st-body .p0-badge{{
    display:inline-block;background:#fef2f2;color:var(--red);
    border:1px solid #fecaca;border-radius:4px;font-size:.68rem;
    font-weight:700;padding:1px 5px;margin-right:5px;vertical-align:middle;
  }}
  .st-body .p1-badge{{
    display:inline-block;background:#fffbeb;color:var(--amber);
    border:1px solid #fde68a;border-radius:4px;font-size:.68rem;
    font-weight:700;padding:1px 5px;margin-right:5px;vertical-align:middle;
  }}
  .st-body .p2-badge{{
    display:inline-block;background:#f0f9ff;color:#0369a1;
    border:1px solid #bae6fd;border-radius:4px;font-size:.68rem;
    font-weight:700;padding:1px 5px;margin-right:5px;vertical-align:middle;
  }}
  .st-error{{
    color:#9f1239;background:#fff1f2;border:1px solid #fecdd3;
    border-radius:8px;padding:12px 16px;font-size:.88rem;
  }}

  /* ── Layout ── */
  .page{{max-width:940px;margin:28px auto;padding:0 18px 80px}}

  /* ── Cards ── */
  .card{{
    background:var(--surface);border-radius:14px;
    padding:28px 32px;margin-bottom:16px;
    box-shadow:0 1px 3px rgba(0,0,0,.06),0 4px 12px rgba(0,0,0,.04);
  }}

  /* ── Typography ── */
  h1{{font-size:1.55rem;color:var(--text);margin:1.4rem 0 .5rem;font-weight:700}}
  h2{{
    font-size:1.05rem;color:var(--text);
    margin:1.6rem 0 .5rem;font-weight:700;
    border-bottom:2px solid var(--border);padding-bottom:.35rem;
    display:flex;align-items:center;gap:6px;
  }}
  h3{{font-size:.95rem;color:#374151;margin:1.1rem 0 .3rem;font-weight:600}}
  p{{margin:.5rem 0;color:var(--text)}}
  ul,ol{{padding-left:1.4rem;margin:.35rem 0}}
  li{{margin:.3rem 0}}
  a{{color:var(--blue);text-decoration:none}}
  a:hover{{text-decoration:underline}}
  blockquote{{
    border-left:3px solid var(--blue);padding:8px 14px;
    background:#f0f4ff;border-radius:0 6px 6px 0;
    margin:.6rem 0;color:#374151;font-style:italic;
  }}
  hr{{border:none;border-top:1px solid var(--border);margin:1.2rem 0}}
  code{{
    background:#f1f3f5;padding:2px 6px;border-radius:4px;
    font-family:'SF Mono',Menlo,Consolas,monospace;font-size:.85em;
  }}
  pre{{background:#f1f3f5;border-radius:8px;padding:16px;overflow-x:auto;margin:.7rem 0}}
  pre code{{background:none;padding:0}}

  /* ── Tables ── */
  table{{border-collapse:collapse;width:100%;margin:.8rem 0;font-size:.88rem}}
  th{{
    background:#f4f6f9;font-weight:600;padding:9px 13px;
    border:1px solid var(--border);text-align:left;color:var(--text);
  }}
  td{{padding:8px 13px;border:1px solid var(--border);vertical-align:top}}
  tr:nth-child(even) td{{background:#f9fafb}}

  /* ── Priority badges on list items ── */
  li.p0-item{{list-style:none;padding-left:0;margin-left:-1.4rem;padding-left:1.4rem}}
  li.p1-item{{list-style:none;padding-left:0;margin-left:-1.4rem;padding-left:1.4rem}}
  li.p2-item{{list-style:none;padding-left:0;margin-left:-1.4rem;padding-left:1.4rem}}
  li.p0-item::before{{
    content:'P0';display:inline-block;
    background:#fef2f2;color:var(--red);border:1px solid #fecaca;
    border-radius:4px;font-size:.7rem;font-weight:700;
    padding:1px 6px;margin-right:8px;vertical-align:middle;
  }}
  li.p1-item::before{{
    content:'P1';display:inline-block;
    background:#fffbeb;color:var(--amber);border:1px solid #fde68a;
    border-radius:4px;font-size:.7rem;font-weight:700;
    padding:1px 6px;margin-right:8px;vertical-align:middle;
  }}
  li.p2-item::before{{
    content:'P2';display:inline-block;
    background:#f0f9ff;color:#0369a1;border:1px solid #bae6fd;
    border-radius:4px;font-size:.7rem;font-weight:700;
    padding:1px 6px;margin-right:8px;vertical-align:middle;
  }}

  /* ── Checkboxes ── */
  input[type=checkbox]{{margin-right:7px;accent-color:var(--blue);width:14px;height:14px}}

  /* ── Delta / What Changed section ── */
  .delta-header{{
    color:var(--blue) !important;
    border-bottom-color:var(--blue) !important;
  }}
  .delta-body{{
    background:#f0f4ff;border-radius:8px;padding:10px 14px;margin:.4rem 0;
  }}

  /* ── Back to top ── */
  .btt{{
    position:fixed;bottom:26px;right:26px;
    background:var(--blue);color:#fff;border:none;border-radius:50%;
    width:40px;height:40px;cursor:pointer;font-size:1.1rem;
    box-shadow:0 2px 8px rgba(0,0,0,.2);
    display:flex;align-items:center;justify-content:center;opacity:.75;
    transition:opacity .15s;
  }}
  .btt:hover{{opacity:1}}

  /* ── Responsive ── */
  @media(max-width:600px){{
    .card{{padding:18px 16px}}
    .page{{padding:0 10px 60px}}
    .navbar{{padding:0 14px;gap:8px}}
    .navbar-tz,.navbar-date{{display:none}}
  }}
</style>
</head>
<body>
<nav class="navbar">
  <span class="navbar-title">&#128203; Daily Brief</span>
  <span class="navbar-date">{date_str}</span>
  <span class="navbar-tz">Asia/Singapore</span>
  <button class="st-btn" id="stBtn" onclick="checkSeatalk()">
    <span class="st-spinner" id="stSpinner"></span>
    <span class="st-label">&#128172; SeaTalk</span>
  </button>
  {run_btn}
</nav>

<!-- SeaTalk result drawer (hidden until button is clicked) -->
<div class="st-drawer" id="stDrawer">
  <div class="st-inner">
    <div class="st-meta">
      <span>&#128172; SeaTalk Check</span>
      <span id="stMeta"></span>
      <button class="st-close" onclick="closeDrawer()" title="Close">&times;</button>
    </div>
    <div class="st-body" id="stBody"></div>
  </div>
</div>

<div class="page">
  <div class="card">
    {body}
  </div>
</div>
<button class="btt" onclick="window.scrollTo({{top:0,behavior:'smooth'}})" title="Back to top">\u2191</button>

<script>
// ── SeaTalk check ────────────────────────────────────────────────────────────
var _stDate = {json.dumps(date_str)};

function checkSeatalk() {{
  var btn = document.getElementById('stBtn');
  btn.disabled = true;
  btn.classList.add('loading');
  document.getElementById('stBody').innerHTML = '<p style="color:#0891b2;font-size:.85rem">Fetching SeaTalk messages\u2026</p>';
  document.getElementById('stMeta').textContent = '';
  openDrawer();

  fetch('/api/seatalk_check?date=' + encodeURIComponent(_stDate))
    .then(function(r) {{ return r.json(); }})
    .then(function(d) {{
      btn.disabled = false;
      btn.classList.remove('loading');
      if (d.ok) {{
        document.getElementById('stMeta').textContent =
          d.message_count + ' messages \u00b7 ' + d.generated_at;
        document.getElementById('stBody').innerHTML = stMd(d.summary);
        tagPriorities(document.getElementById('stBody'));
      }} else {{
        document.getElementById('stBody').innerHTML =
          '<div class="st-error">' + escHtml(d.error) + '</div>';
      }}
    }})
    .catch(function(err) {{
      btn.disabled = false;
      btn.classList.remove('loading');
      document.getElementById('stBody').innerHTML =
        '<div class="st-error">Request failed: ' + escHtml(String(err)) + '</div>';
    }});
}}

function openDrawer() {{
  document.getElementById('stDrawer').classList.add('open');
}}
function closeDrawer() {{
  document.getElementById('stDrawer').classList.remove('open');
}}

function escHtml(s) {{
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}}

// Lightweight markdown → HTML for SeaTalk summary output.
// Handles: ### headers, **bold**, - bullets, [ ] checkboxes, blank lines.
function stMd(md) {{
  var lines = md.split('\\n');
  var html = '';
  var inUl = false;

  function closeUl() {{
    if (inUl) {{ html += '</ul>'; inUl = false; }}
  }}

  for (var i = 0; i < lines.length; i++) {{
    var line = lines[i];

    // Headers
    if (line.startsWith('### ')) {{
      closeUl();
      html += '<h3>' + inlineMd(line.slice(4)) + '</h3>';
      continue;
    }}
    if (line.startsWith('## ')) {{
      closeUl();
      html += '<h3 style="font-size:.97rem;color:#164e63;margin:1.1rem 0 .3rem">' + inlineMd(line.slice(3)) + '</h3>';
      continue;
    }}
    if (line.startsWith('# ')) {{
      closeUl();
      html += '<h3 style="font-size:1rem;color:#1a1a2e;margin:1.1rem 0 .3rem">' + inlineMd(line.slice(2)) + '</h3>';
      continue;
    }}

    // Bullets
    var bulletMatch = line.match(/^[-*]\\s+(.*)/);
    if (bulletMatch) {{
      if (!inUl) {{ html += '<ul>'; inUl = true; }}
      // Checkbox
      var cbText = bulletMatch[1].replace(/^\\[ \\]\\s*/, function() {{
        return '<input type="checkbox" disabled> ';
      }}).replace(/^\\[x\\]\\s*/i, function() {{
        return '<input type="checkbox" checked disabled> ';
      }});
      html += '<li>' + inlineMd(cbText) + '</li>';
      continue;
    }}

    // Blank line
    if (line.trim() === '') {{
      closeUl();
      html += '<br>';
      continue;
    }}

    // Paragraph
    closeUl();
    html += '<p>' + inlineMd(line) + '</p>';
  }}
  closeUl();
  return html;
}}

function inlineMd(s) {{
  // Bold: **text**
  s = s.replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>');
  // Inline code: `text`
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
  // Escape remaining < >
  s = s.replace(/</g, '&lt;').replace(/>/g, '&gt;');
  // P0/P1/P2 badges inline (e.g. **P0** already bolded above)
  s = s.replace(/<strong>P0<\/strong>/g, '<span class="p0-badge">P0</span>');
  s = s.replace(/<strong>P1<\/strong>/g, '<span class="p1-badge">P1</span>');
  s = s.replace(/<strong>P2<\/strong>/g, '<span class="p2-badge">P2</span>');
  return s;
}}

function tagPriorities(root) {{
  root.querySelectorAll('li').forEach(function(li) {{
    var t = li.textContent;
    if (/^\\s*P0[^a-z0-9]/i.test(t)) li.classList.add('p0-item');
    else if (/^\\s*P1[^a-z0-9]/i.test(t)) li.classList.add('p1-item');
    else if (/^\\s*P2[^a-z0-9]/i.test(t)) li.classList.add('p2-item');
  }});
}}

// ── Run brief ────────────────────────────────────────────────────────────────
{run_brief_fn}

// ── P0/P1/P2 badge tagging on main briefing ──────────────────────────────────
document.addEventListener('DOMContentLoaded', function() {{
  document.querySelectorAll('.card li').forEach(function(li) {{
    var t = li.textContent;
    if (/^\\s*P0[^a-z0-9]/i.test(t)) li.classList.add('p0-item');
    else if (/^\\s*P1[^a-z0-9]/i.test(t)) li.classList.add('p1-item');
    else if (/^\\s*P2[^a-z0-9]/i.test(t)) li.classList.add('p2-item');
  }});
  document.querySelectorAll('h2,h3').forEach(function(h) {{
    if (h.textContent.includes('What Changed') || h.textContent.includes('\ud83d\udd04')) {{
      h.classList.add('delta-header');
      var next = h.nextElementSibling;
      while (next && !['H2','H3'].includes(next.tagName)) {{
        next.classList.add('delta-body');
        next = next.nextElementSibling;
      }}
    }}
  }});
}});
</script>
</body>
</html>"""


def _not_found(date_str: str) -> str:
    turl = _trigger_url()
    cta = f"""
    <button class="run-btn" onclick="this.disabled=true;this.textContent='Running\u2026';window.location.href='{turl}'">
      &#9654;&nbsp; Generate Brief Now
    </button>""" if turl else "<p class='hint'>Check that the cron ran successfully.</p>"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>No brief \u2014 {date_str}</title>
<style>
  :root{{--blue:#1D6FE8;--bg:#F5F6FA;--text:#1a1a2e}}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
        background:var(--bg);color:var(--text);min-height:100vh;
        display:flex;align-items:center;justify-content:center}}
  .box{{text-align:center;padding:48px 32px;max-width:420px}}
  .icon{{font-size:3rem;margin-bottom:18px;opacity:.4}}
  h2{{font-size:1.35rem;margin-bottom:10px;font-weight:700}}
  p{{color:#6b7280;margin-bottom:28px;font-size:.92rem;line-height:1.5}}
  .hint{{color:#9ca3af;font-size:.85rem;margin-top:16px}}
  .run-btn{{
    background:var(--blue);color:#fff;border:none;border-radius:8px;
    padding:12px 28px;font-size:.95rem;font-weight:600;cursor:pointer;
    transition:opacity .15s;letter-spacing:.2px;
  }}
  .run-btn:hover{{opacity:.85}}
  .run-btn:disabled{{opacity:.45;cursor:default}}
</style>
</head>
<body>
<div class="box">
  <div class="icon">&#128203;</div>
  <h2>No brief for {date_str}</h2>
  <p>Briefings are stored for 7 days.<br>The scheduled cron runs daily at 8:00 AM SGT.</p>
  {cta}
</div>
</body>
</html>"""


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # ── Session check ─────────────────────────────────────────────────
        cookie_header = self.headers.get("Cookie", "")
        cookies = parse_cookies(cookie_header)
        session_email = verify_cookie(cookies.get(COOKIE_NAME, ""))
        if not session_email:
            next_url = urllib.parse.quote(self.path, safe="")
            self.send_response(302)
            self.send_header("Location", f"/api/auth?next={next_url}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        # ── End session check ─────────────────────────────────────────────

        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        date_str = params.get("date", [datetime.now(SGT).strftime("%Y-%m-%d")])[0]

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
        self.send_header("Cache-Control", "private, max-age=300")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass
