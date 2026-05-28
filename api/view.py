"""
/api/view?date=YYYY-MM-DD  —  Render a stored daily briefing as a styled HTML page.

Fetches the markdown from Upstash Redis and returns a self-contained HTML document.
Requires a valid Google session cookie (set by /api/auth).
Only shuning.wang@shopee.com and shuning2016@gmail.com are authorised.
"""

from http.server import BaseHTTPRequestHandler
import json
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
        '<button class="run-btn" id="runBtn" onclick="runBrief()">&#9654; Run Now</button>'
        if turl else ""
    )
    # Build runBrief as string concat (avoids nested f-string quoting issues)
    run_brief_fn = (
        "function runBrief() {\n"
        "  if (!_confirmIfRecent('main', 'Daily brief')) return;\n"
        "  _setLastRun('main');\n"
        "  var btn = document.getElementById('runBtn');\n"
        "  if (btn) { btn.disabled = true; btn.textContent = 'Running…'; }\n"
        f"  window.location.href = '{turl}';\n"
        "}"
    ) if turl else ""

    stale_regen = (
        '<button class="stale-regen" onclick="runBrief()">&#9654; Run today\'s brief</button>'
        if turl else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Daily Brief — {date_str}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {{
    --primary: #EE4D2D;
    --teal:    #0080C6;
    --navy:    #172B4D;
    --green:   #16a34a;
    --amber:   #d97706;
    --red:     #dc2626;
    --bg:      #FFF5F3;
    --surface: #ffffff;
    --text:    #172B4D;
    --muted:   #6b7280;
    --border:  #e5e7eb;
    --header-h: 58px;
    --tabnav-h: 45px;
  }}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:'Roboto',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
        background:var(--bg);color:var(--text);line-height:1.65;font-size:15px}}

  /* ── Navbar ── */
  .navbar{{
    position:sticky;top:0;z-index:200;height:var(--header-h);
    background:linear-gradient(135deg,#EE4D2D 0%,#172B4D 100%);
    color:#fff;padding:0 24px;
    display:flex;align-items:center;gap:10px;
    box-shadow:0 2px 12px rgba(238,77,45,.35);
  }}
  .navbar-title{{font-size:1.05rem;font-weight:700;letter-spacing:.3px;flex:1}}
  .navbar-date{{font-size:.78rem;background:rgba(255,255,255,.1);border-radius:20px;padding:4px 12px;white-space:nowrap}}
  .navbar-tz{{font-size:.72rem;opacity:.5}}
  .run-btn{{
    background:rgba(255,255,255,.15);color:#fff;border:1px solid rgba(255,255,255,.4);
    border-radius:6px;padding:7px 16px;font-size:.82rem;font-weight:600;cursor:pointer;
    white-space:nowrap;transition:opacity .15s;font-family:inherit;
  }}
  .run-btn:hover{{opacity:.85}}
  .run-btn:disabled{{opacity:.45;cursor:default}}

  /* ── Tab nav ── */
  .tab-nav{{
    position:sticky;top:var(--header-h);z-index:150;
    background:#fff;border-bottom:2px solid var(--border);
    display:flex;padding:0 20px;overflow-x:auto;
    box-shadow:0 2px 6px rgba(0,0,0,.05);
  }}
  .tab-btn{{
    background:none;border:none;border-bottom:3px solid transparent;
    padding:13px 22px 10px;font-size:.9rem;font-weight:600;color:var(--muted);
    cursor:pointer;margin-bottom:-2px;transition:color .15s,border-color .15s;
    display:flex;align-items:center;gap:6px;white-space:nowrap;font-family:inherit;
    flex-shrink:0;
  }}
  .tab-btn.active{{color:var(--primary);border-bottom-color:var(--primary)}}
  .tab-btn:hover:not(.active){{color:var(--text);background:#fafafa}}
  .tab-badge{{
    background:var(--red);color:#fff;border-radius:10px;
    padding:1px 6px;font-size:.68rem;font-weight:700;min-width:18px;
    text-align:center;line-height:1.6;
  }}

  /* ── Tab panels ── */
  .tab-panel{{display:block}}
  .tab-panel[hidden]{{display:none!important}}

  /* ── Side panels (Actions, SeaTalk) ── */
  .side-panel{{max-width:960px;margin:0 auto;padding:24px 18px 80px}}
  .panel-card{{
    background:var(--surface);border-radius:14px;padding:26px 30px;
    box-shadow:0 1px 3px rgba(0,0,0,.06),0 4px 12px rgba(0,0,0,.04);
  }}
  .panel-header{{
    display:flex;align-items:center;gap:10px;margin-bottom:20px;
    padding-bottom:14px;border-bottom:2px solid var(--border);flex-wrap:wrap;
  }}
  .panel-title{{font-size:1.05rem;font-weight:700;color:var(--text);flex:1;min-width:0}}
  .meta-time{{font-size:.77rem;color:var(--muted);white-space:nowrap}}
  .refresh-btn{{
    background:none;border:1px solid var(--border);border-radius:6px;
    padding:6px 13px;font-size:.82rem;font-weight:500;cursor:pointer;
    color:var(--text);transition:border-color .15s,background .15s;font-family:inherit;
  }}
  .refresh-btn:hover{{background:#f3f4f6;border-color:#9ca3af}}
  .refresh-btn:disabled{{opacity:.5;cursor:default}}
  .add-btn{{
    background:var(--teal);color:#fff;border:none;border-radius:6px;
    padding:7px 16px;font-size:.82rem;font-weight:600;cursor:pointer;
    transition:opacity .15s;font-family:inherit;white-space:nowrap;
  }}
  .add-btn:hover{{opacity:.85}}

  /* ── Add Item form ── */
  .add-form-wrap{{
    background:#f0f9ff;border:1px solid #bae6fd;border-radius:10px;
    padding:16px 20px;margin-bottom:22px;
  }}
  .add-form-title{{font-size:.82rem;font-weight:700;color:var(--navy);margin-bottom:11px}}
  .add-form-row{{display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end}}
  .add-form-row input[type=text],
  .add-form-row input[type=date],
  .add-form-row select {{
    border:1px solid #c7d0e8;border-radius:6px;padding:7px 10px;
    font-size:.88rem;font-family:inherit;background:#fff;color:var(--text);
    transition:border-color .15s;
  }}
  .add-form-row input:focus,.add-form-row select:focus{{
    outline:2px solid var(--teal);border-color:var(--teal);
  }}
  .f-action{{flex:1;min-width:220px}}
  .f-eta{{width:148px}}
  .f-urgency{{width:132px}}
  .f-source{{width:178px}}
  .cancel-btn{{
    background:none;border:1px solid var(--border);border-radius:6px;
    padding:7px 14px;font-size:.82rem;cursor:pointer;color:var(--muted);
    font-family:inherit;transition:background .15s;
  }}
  .cancel-btn:hover{{background:#f3f4f6}}

  /* ── Action Items table ── */
  .ai-table{{width:100%;border-collapse:collapse;font-size:.88rem}}
  .ai-table th{{
    background:#f4f6fb;padding:9px 11px;border:1px solid #d8dff0;
    text-align:left;color:var(--navy);font-weight:600;white-space:nowrap;
  }}
  .ai-table td{{padding:9px 11px;border:1px solid #e5e9f5;vertical-align:top}}
  .ai-table tr:nth-child(even) td{{background:#f9fafc}}
  /* Manual items — teal left accent + light blue tint */
  .ai-row-manual td{{background:#f0faff!important}}
  .ai-row-manual td:first-child{{border-left:3px solid var(--teal)}}
  .manual-badge{{
    display:inline-block;background:#e0f2fe;color:var(--teal);
    border:1px solid #bae6fd;border-radius:3px;font-size:.65rem;
    font-weight:700;padding:1px 5px;margin-left:5px;vertical-align:middle;
  }}
  .ai-done-btn{{
    background:none;border:1px solid #d1d5db;color:#6b7280;
    border-radius:4px;padding:3px 10px;font-size:.75rem;cursor:pointer;
    transition:all .15s;white-space:nowrap;font-family:inherit;
  }}
  .ai-done-btn:hover{{background:var(--green);color:#fff;border-color:var(--green)}}
  .ai-done-btn:disabled{{opacity:.4;cursor:default}}
  .ai-row-done td{{opacity:.38;text-decoration:line-through}}
  .ai-empty{{color:var(--muted);padding:32px 0;text-align:center;font-size:.92rem}}
  .loading-msg{{color:var(--muted);font-size:.9rem;padding:20px 0}}

  /* ── SeaTalk content ── */
  .st-body h3{{font-size:.95rem;color:var(--text);margin:1rem 0 .3rem;font-weight:700}}
  .st-body p{{margin:.4rem 0;font-size:.88rem;color:#374151}}
  .st-body ul{{padding-left:0;list-style:none;margin:.3rem 0}}
  .st-body li{{
    font-size:.88rem;margin:.5rem 0;color:#374151;
    padding:.5rem .75rem;background:#f8fafc;
    border-radius:6px;border-left:3px solid var(--border);line-height:1.55;
  }}
  .st-body strong{{color:var(--text)}}
  .st-body .p0-badge{{display:inline-block;background:#fef2f2;color:var(--red);border:1px solid #fecaca;border-radius:4px;font-size:.68rem;font-weight:700;padding:1px 5px;margin-right:5px;vertical-align:middle}}
  .st-body .p1-badge{{display:inline-block;background:#fffbeb;color:var(--amber);border:1px solid #fde68a;border-radius:4px;font-size:.68rem;font-weight:700;padding:1px 5px;margin-right:5px;vertical-align:middle}}
  .st-body .p2-badge{{display:inline-block;background:#f0f9ff;color:#0369a1;border:1px solid #bae6fd;border-radius:4px;font-size:.68rem;font-weight:700;padding:1px 5px;margin-right:5px;vertical-align:middle}}
  .st-error{{color:#9f1239;background:#fff1f2;border:1px solid #fecdd3;border-radius:8px;padding:12px 16px;font-size:.88rem}}
  .st-src{{display:inline-flex;align-items:center;font-size:.7rem;font-weight:700;border-radius:4px;padding:2px 7px;margin-right:6px;white-space:nowrap;vertical-align:middle}}
  .st-src-group{{background:#EFF7FC;color:var(--teal);border:1px solid #bae6fd}}
  .st-src-dm{{background:#f5f3ff;color:#7c3aed;border:1px solid #ddd6fe}}
  .st-friend{{display:inline-block;background:#fef9c3;color:#854d0e;border:1px solid #fde68a;border-radius:4px;font-size:.65rem;font-weight:700;padding:1px 5px;margin-left:4px;vertical-align:middle}}
  .st-body strong.st-vip{{color:var(--primary)}}
  .st-body .st-action{{color:var(--primary);font-weight:700}}
  .st-section{{display:flex;align-items:center;gap:8px;margin:1rem 0 .4rem;padding-top:.75rem;border-top:2px solid var(--border)}}
  .st-section:first-of-type{{border-top:none;margin-top:.2rem;padding-top:0}}

  /* ── Briefing layout ── */
  .page{{max-width:940px;margin:28px auto;padding:0 18px 80px}}
  .card{{
    background:var(--surface);border-radius:14px;
    padding:28px 32px;margin-bottom:16px;
    box-shadow:0 1px 3px rgba(0,0,0,.06),0 4px 12px rgba(0,0,0,.04);
  }}

  /* ── Typography ── */
  h1{{font-size:1.55rem;color:var(--text);margin:1.4rem 0 .5rem;font-weight:700}}
  h2{{font-size:1.05rem;color:var(--text);margin:1.6rem 0 .5rem;font-weight:700;border-bottom:2px solid var(--border);padding-bottom:.35rem;display:flex;align-items:center;gap:6px}}
  h3{{font-size:.95rem;color:#374151;margin:1.1rem 0 .3rem;font-weight:600}}
  p{{margin:.5rem 0;color:var(--text)}}
  ul,ol{{padding-left:1.4rem;margin:.35rem 0}}
  li{{margin:.3rem 0}}
  a{{color:var(--primary);text-decoration:none}}
  a:hover{{text-decoration:underline}}
  blockquote{{border-left:3px solid var(--primary);padding:8px 14px;background:#fff0ec;border-radius:0 6px 6px 0;margin:.6rem 0;color:#374151;font-style:italic}}
  hr{{border:none;border-top:1px solid var(--border);margin:1.2rem 0}}
  code{{background:#f1f3f5;padding:2px 6px;border-radius:4px;font-family:'SF Mono',Menlo,Consolas,monospace;font-size:.85em}}
  pre{{background:#f1f3f5;border-radius:8px;padding:16px;overflow-x:auto;margin:.7rem 0}}
  pre code{{background:none;padding:0}}

  /* ── Tables (in briefing body) ── */
  table{{border-collapse:collapse;width:100%;margin:.8rem 0;font-size:.88rem}}
  th{{background:#FDF1EE;font-weight:600;padding:9px 13px;border:1px solid var(--border);text-align:left;color:var(--navy)}}
  td{{padding:8px 13px;border:1px solid var(--border);vertical-align:top}}
  tr:nth-child(even) td{{background:#f9fafb}}

  /* ── Priority badges ── */
  li.p0-item,li.p1-item,li.p2-item{{list-style:none;margin-left:-1.4rem;padding-left:1.4rem}}
  li.p0-item::before{{content:'P0';display:inline-block;background:#fef2f2;color:var(--red);border:1px solid #fecaca;border-radius:4px;font-size:.7rem;font-weight:700;padding:1px 6px;margin-right:8px;vertical-align:middle}}
  li.p1-item::before{{content:'P1';display:inline-block;background:#fffbeb;color:var(--amber);border:1px solid #fde68a;border-radius:4px;font-size:.7rem;font-weight:700;padding:1px 6px;margin-right:8px;vertical-align:middle}}
  li.p2-item::before{{content:'P2';display:inline-block;background:#f0f9ff;color:#0369a1;border:1px solid #bae6fd;border-radius:4px;font-size:.7rem;font-weight:700;padding:1px 6px;margin-right:8px;vertical-align:middle}}
  input[type=checkbox]{{margin-right:7px;accent-color:var(--primary);width:14px;height:14px}}

  /* ── Stale banner ── */
  .stale-banner{{background:#fef3c7;border-bottom:2px solid #d97706;padding:10px 24px;display:flex;align-items:center;gap:10px;font-size:.88rem;color:#92400e}}
  .stale-banner strong{{font-weight:700}}
  .stale-regen{{margin-left:auto;background:#d97706;color:#fff;border:none;border-radius:5px;padding:5px 14px;font-size:.82rem;font-weight:600;cursor:pointer;white-space:nowrap;font-family:inherit}}

  /* ── Past meetings ── */
  .past-item{{opacity:.38;text-decoration:line-through}}
  .past-item::after{{content:' ✓';font-size:.8em;opacity:.7;text-decoration:none;display:inline}}

  /* ── Delta / What Changed ── */
  .delta-header{{color:var(--primary)!important;border-bottom-color:var(--primary)!important}}
  .delta-body{{background:#fff0ec;border-radius:8px;padding:10px 14px;margin:.4rem 0}}

  /* ── Pre-reads tab ── */
  .pr-pdf-title{{font-size:.95rem;font-weight:700;color:var(--navy);margin-bottom:12px;display:flex;align-items:center;gap:8px;padding-bottom:8px;border-bottom:2px solid var(--border)}}
  .pr-pdf-icon{{font-size:1.1rem}}
  .pr-q-row{{display:flex;align-items:flex-start;gap:8px;padding:9px 0;border-bottom:1px solid #f0f1f5}}
  .pr-q-row:last-of-type{{border-bottom:none}}
  .pr-slide-ref{{font-size:.73rem;font-weight:700;color:var(--teal);background:#f0f9ff;border:1px solid #bae6fd;border-radius:4px;padding:2px 7px;white-space:nowrap;flex-shrink:0;align-self:flex-start;margin-top:2px}}
  .pr-slide-ref.pr-others{{color:#7c3aed;background:#f5f3ff;border-color:#ddd6fe}}
  .pr-slide-ref.pr-summary{{color:var(--muted);background:#f3f4f6;border-color:var(--border)}}
  .pr-q-text{{flex:1;font-size:.9rem;color:var(--text);line-height:1.55}}
  .pr-q-text.pr-is-summary{{font-style:italic;color:var(--muted);font-size:.82rem}}
  .pr-actions{{display:flex;gap:4px;flex-shrink:0}}
  .pr-edit-btn,.pr-del-btn{{background:none;border:1px solid var(--border);border-radius:4px;padding:3px 9px;font-size:.75rem;cursor:pointer;color:var(--muted);font-family:inherit;transition:all .15s}}
  .pr-edit-btn:hover{{border-color:var(--teal);color:var(--teal);background:#f0f9ff}}
  .pr-del-btn:hover{{border-color:var(--red);color:var(--red);background:#fff1f2}}
  .pr-add-row{{display:flex;gap:8px;padding-top:12px;margin-top:8px;border-top:1px dashed var(--border);flex-wrap:wrap}}
  .pr-add-input{{flex:1;min-width:200px;border:1px solid #c7d0e8;border-radius:6px;padding:7px 10px;font-size:.88rem;font-family:inherit}}
  .pr-add-input:focus{{outline:2px solid var(--teal);border-color:var(--teal)}}
  /* PDF sub-tabs (when >1 PDF) */
  .pr-subtabs{{display:flex;gap:6px;margin-bottom:20px;flex-wrap:wrap;border-bottom:2px solid var(--border);padding-bottom:0}}
  .pr-subtab{{border:1px solid var(--border);border-bottom:none;background:var(--surface);border-radius:8px 8px 0 0;padding:8px 16px;font-size:.84rem;font-weight:600;cursor:pointer;color:var(--muted);font-family:inherit;transition:all .15s;white-space:nowrap;margin-bottom:-2px}}
  .pr-subtab.active{{background:var(--navy);color:#fff;border-color:var(--navy)}}
  .pr-subtab:hover:not(.active){{border-color:var(--teal);color:var(--teal);background:#f0f9ff}}
  /* Questions / Answers view toggle */
  .pr-view-toggle{{display:flex;margin-bottom:16px;border:1px solid var(--border);border-radius:7px;overflow:hidden;width:fit-content}}
  .pr-view-btn{{border:none;background:#f8f9fa;padding:7px 20px;font-size:.82rem;font-weight:600;cursor:pointer;font-family:inherit;color:var(--muted);transition:all .15s}}
  .pr-view-btn.active{{background:var(--teal);color:#fff}}
  .pr-view-btn:hover:not(.active){{background:#e8f4fd;color:var(--teal)}}
  /* Answers view */
  .pr-ans-slide-hdr{{font-size:.78rem;font-weight:700;color:var(--teal);text-transform:uppercase;letter-spacing:.4px;margin:18px 0 8px;padding-bottom:5px;border-bottom:1px solid #bae6fd}}
  .pr-ans-slide-hdr:first-child{{margin-top:0}}
  .pr-ans-slide-hdr.pr-ans-others-hdr{{color:#7c3aed;border-color:#ddd6fe}}
  .pr-ans-item{{margin-bottom:12px;padding:11px 14px;background:#f8fafe;border-radius:8px;border-left:3px solid var(--teal)}}
  .pr-ans-item.pr-ans-others{{border-left-color:#7c3aed;background:#faf8ff}}
  .pr-ans-q{{font-size:.88rem;font-weight:600;color:var(--navy);margin-bottom:5px}}
  .pr-ans-a{{font-size:.87rem;color:#374151;line-height:1.6}}
  .pr-ans-empty{{font-size:.83rem;color:var(--muted);font-style:italic}}
  /* Deck summary card */
  .pr-summary-card{{background:#f8fafe;border-radius:10px;padding:16px 20px;margin-bottom:16px;border:1px solid #dbeafe;position:relative}}
  .pr-summary-card .del-corner{{position:absolute;top:10px;right:10px;background:none;border:1px solid var(--border);border-radius:4px;padding:2px 8px;font-size:.73rem;cursor:pointer;color:var(--muted);font-family:inherit}}
  .pr-summary-card .del-corner:hover{{border-color:var(--red);color:var(--red)}}
  .pr-summary-hdr{{font-size:.8rem;font-weight:700;color:var(--teal);text-transform:uppercase;letter-spacing:.5px;margin-bottom:10px;display:flex;align-items:center;gap:6px}}
  .pr-summary-card p{{font-size:.87rem;color:#374151;margin:.25rem 0;line-height:1.6}}
  .pr-summary-card ul{{padding-left:1.1rem;margin:.2rem 0 .5rem}}
  .pr-summary-card li{{font-size:.87rem;color:#374151;margin:.2rem 0;line-height:1.55}}
  .pr-summary-card strong{{color:var(--navy);font-weight:700}}
  .pr-summary-card hr{{border:none;border-top:1px solid var(--border);margin:.6rem 0}}

  /* ── Reason modal ── */
  .modal-overlay{{position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:500;display:flex;align-items:center;justify-content:center;padding:20px}}
  .modal-box{{background:#fff;border-radius:14px;padding:28px 32px;max-width:480px;width:100%;box-shadow:0 8px 40px rgba(0,0,0,.2)}}
  .modal-title{{font-size:1rem;font-weight:700;color:var(--navy);margin-bottom:16px}}
  .modal-reasons{{display:flex;flex-direction:column;gap:10px;margin-bottom:14px}}
  .modal-reasons label{{display:flex;align-items:center;gap:8px;font-size:.9rem;cursor:pointer;padding:6px 10px;border-radius:6px;transition:background .12s}}
  .modal-reasons label:hover{{background:#f3f4f6}}
  .modal-reasons input[type=radio]{{accent-color:var(--primary);width:15px;height:15px;flex-shrink:0}}
  .modal-textarea{{width:100%;border:1px solid var(--border);border-radius:6px;padding:8px 10px;font-family:inherit;font-size:.88rem;resize:vertical;margin-top:4px;color:var(--text)}}
  .modal-textarea:focus{{outline:2px solid var(--teal);border-color:var(--teal)}}
  .modal-label{{font-size:.82rem;color:var(--muted);margin-top:12px;margin-bottom:2px;display:block}}
  .modal-footer{{display:flex;gap:10px;justify-content:flex-end;margin-top:20px}}

  /* ── Back to top ── */
  .btt{{position:fixed;bottom:26px;right:26px;background:var(--primary);color:#fff;border:none;border-radius:50%;width:40px;height:40px;cursor:pointer;font-size:1.1rem;box-shadow:0 2px 8px rgba(0,0,0,.2);display:flex;align-items:center;justify-content:center;opacity:.75;transition:opacity .15s;z-index:100}}
  .btt:hover{{opacity:1}}

  /* ── Responsive ── */
  @media(max-width:600px){{
    .card,.panel-card{{padding:18px 16px}}
    .page,.side-panel{{padding-left:10px;padding-right:10px}}
    .navbar{{padding:0 14px;gap:8px}}
    .navbar-tz,.navbar-date{{display:none}}
    .tab-btn{{padding:12px 14px 10px;font-size:.82rem}}
    .add-form-row{{flex-direction:column}}
    .f-action,.f-eta,.f-urgency,.f-source{{width:100%!important;min-width:0!important}}
  }}
</style>
</head>
<body>

<nav class="navbar">
  <span class="navbar-title">&#9889; Daily Brief</span>
  <span class="navbar-date">{date_str}</span>
  <span class="navbar-tz">SGT</span>
  {run_btn}
</nav>

<nav class="tab-nav">
  <button class="tab-btn active" data-tab="briefing" onclick="switchTab('briefing')">
    &#128196; Briefing
  </button>
  <button class="tab-btn" data-tab="actions" onclick="switchTab('actions')">
    &#9989; Actions
    <span id="actBadge" class="tab-badge" style="display:none"></span>
  </button>
  <button class="tab-btn" data-tab="seatalk" onclick="switchTab('seatalk')">
    &#128172; SeaTalk
  </button>
  <button class="tab-btn" data-tab="prereads" onclick="switchTab('prereads')">
    &#128214; Pre-reads
    <span id="prBadge" class="tab-badge" style="display:none"></span>
  </button>
</nav>

<!-- ── Reason modal (shared by Pre-reads edit/delete) ── -->
<div id="reasonModal" class="modal-overlay" style="display:none" onclick="if(event.target===this)closeReasonModal()">
  <div class="modal-box" onclick="event.stopPropagation()">
    <div class="modal-title" id="reasonModalTitle">Why?</div>
    <div class="modal-reasons">
      <label><input type="radio" name="reason" value="not_relevant"> Not relevant to this meeting</label>
      <label><input type="radio" name="reason" value="answered_before"> Already answered in a previous meeting</label>
      <label><input type="radio" name="reason" value="too_trivial"> Too trivial to ask</label>
      <label><input type="radio" name="reason" value="other" checked> Other</label>
    </div>
    <div id="editQuestionWrap" style="display:none">
      <span class="modal-label">Edited question:</span>
      <textarea id="editQuestionText" class="modal-textarea" rows="3" placeholder="Enter updated question"></textarea>
    </div>
    <textarea id="reasonDetail" class="modal-textarea" rows="2" placeholder="Additional context (optional)"></textarea>
    <div class="modal-footer">
      <button class="cancel-btn" onclick="closeReasonModal()">Cancel</button>
      <button class="add-btn" id="reasonConfirmBtn" onclick="confirmReason()">Confirm</button>
    </div>
  </div>
</div>

<!-- ── Tab: Briefing ── -->
<div id="tab-briefing" class="tab-panel">
  <div id="staleBanner" style="display:none" class="stale-banner">
    <span>&#9888;</span>
    <span>This brief is from <strong>{date_str}</strong> — meetings may have already passed.</span>
    {stale_regen}
  </div>
  <div class="page">
    <div class="card">
      {body}
    </div>
  </div>
</div>

<!-- ── Tab: Action Items ── -->
<div id="tab-actions" class="tab-panel" hidden>
  <div class="side-panel">
    <div class="panel-card">
      <div class="panel-header">
        <span class="panel-title">&#9989; Action Items</span>
        <span id="aiTimestamp" class="meta-time"></span>
        <button class="refresh-btn" id="aiRefreshBtn" onclick="_fetchActionItems()">&#8635; Refresh</button>
        <button class="add-btn" onclick="toggleAddForm()">&#65291; Add Item</button>
      </div>

      <div id="addFormWrap" hidden>
        <form class="add-form-wrap" onsubmit="submitAddItem(event)">
          <div class="add-form-title">&#128221; New Action Item</div>
          <div class="add-form-row">
            <input type="text" id="newAction" class="f-action" placeholder="Action description (required)" required>
            <input type="date" id="newEta" class="f-eta" title="ETA (optional)">
            <select id="newUrgency" class="f-urgency">
              <option value="">Urgency</option>
              <option value="high">&#128308; High</option>
              <option value="medium">&#128992; Medium</option>
              <option value="low">&#128994; Low</option>
            </select>
            <input type="text" id="newSource" class="f-source" placeholder="Source / context (optional)">
            <button type="submit" class="add-btn" id="addSubmitBtn">Add</button>
            <button type="button" class="cancel-btn" onclick="toggleAddForm()">Cancel</button>
          </div>
        </form>
      </div>

      <div id="aiContent"><p class="loading-msg">Loading…</p></div>
    </div>
  </div>
</div>

<!-- ── Tab: SeaTalk ── -->
<div id="tab-seatalk" class="tab-panel" hidden>
  <div class="side-panel">
    <div class="panel-card">
      <div class="panel-header">
        <span class="panel-title">&#128172; SeaTalk</span>
        <span id="stTimestamp" class="meta-time"></span>
        <button class="refresh-btn" id="stRefreshBtn" onclick="_fetchSeatalk(true)">&#8635; Refresh</button>
      </div>
      <div class="st-body" id="stContent"><p class="loading-msg">Loading…</p></div>
    </div>
  </div>
</div>

<!-- ── Tab: Pre-reads ── -->
<div id="tab-prereads" class="tab-panel" hidden>
  <div class="side-panel">
    <div class="panel-card">
      <div class="panel-header">
        <span class="panel-title">&#128214; Pre-read Q&amp;A</span>
        <span id="prTimestamp" class="meta-time"></span>
        <button class="refresh-btn" id="prRefreshBtn" onclick="_fetchPrereads()">&#8635; Refresh</button>
      </div>
      <p style="font-size:.82rem;color:var(--muted);margin-bottom:18px;line-height:1.55">
        Predicted questions Ian Ho would ask based on today&#39;s pre-read PDF decks.
        Generated automatically during the daily brief.
        You can <strong>edit</strong>, <strong>delete</strong>, or <strong>add</strong> questions — all changes are logged for prompt improvement.
      </p>
      <div id="prContent"><p class="loading-msg">Loading&#8230;</p></div>
    </div>
  </div>
</div>

<button class="btt" onclick="window.scrollTo({{top:0,behavior:'smooth'}})" title="Back to top">↑</button>

<script>
var _PAGE_DATE = {json.dumps(date_str)};

// ── Utilities ─────────────────────────────────────────────────────────────────
function escHtml(s) {{
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}}

function mdToHtml(raw) {{
  // Convert basic markdown to HTML for display in the summary card.
  // Handles: **bold**, bullet lists (- item), blank-line paragraphs, --- hr.
  var lines = String(raw)
    .replace(/^\[DECK SUMMARY\]\s*/i, '')  // strip storage prefix
    .split('\\n');
  var out = '';
  var inList = false;

  lines.forEach(function(line) {{
    var trimmed = line.trim();
    // Horizontal rule
    if (trimmed === '---') {{
      if (inList) {{ out += '</ul>'; inList = false; }}
      out += '<hr>';
      return;
    }}
    // Bullet list item
    if (trimmed.startsWith('- ') && trimmed.length > 2) {{
      if (!inList) {{ out += '<ul>'; inList = true; }}
      var content = trimmed.slice(2).replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
      out += '<li>' + content + '</li>';
      return;
    }}
    // Close list before non-bullet content
    if (inList) {{ out += '</ul>'; inList = false; }}
    // Empty line
    if (!trimmed) return;
    // Normal paragraph line — convert **bold**
    var para = trimmed.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    out += '<p>' + para + '</p>';
  }});
  if (inList) out += '</ul>';
  return out;
}}
function _sgtNow() {{
  var sgt = new Date(Date.now() + 8 * 3600000);
  return sgt.toISOString().slice(11,16) + ' SGT';
}}
function fmtDate(s) {{
  if (!s) return '—';
  try {{
    var d = new Date(s + 'T00:00:00');
    return d.toLocaleDateString('en-US', {{month:'short', day:'numeric'}});
  }} catch(e) {{ return s; }}
}}
function fmtEta(eta, today) {{
  if (!eta) return '—';
  var days = Math.round((new Date(eta) - new Date(today)) / 86400000);
  if (days < 0) return '<span style="color:var(--red);font-weight:600">overdue</span>';
  if (days === 0) return '<span style="color:var(--red);font-weight:600">today</span>';
  return escHtml(eta.slice(5)) + ' <span style="color:var(--muted)">(' + days + 'd)</span>';
}}

// ── localStorage helpers ───────────────────────────────────────────────────────
function _getLastRun(type) {{
  try {{ var v = localStorage.getItem('brief-last-run:' + type + ':' + _PAGE_DATE); return v ? parseInt(v,10) : null; }} catch(e) {{ return null; }}
}}
function _setLastRun(type) {{
  try {{ localStorage.setItem('brief-last-run:' + type + ':' + _PAGE_DATE, Date.now().toString()); }} catch(e) {{}}
}}
function _confirmIfRecent(type, label) {{
  var last = _getLastRun(type);
  if (!last) return true;
  var diff = Date.now() - last;
  if (diff >= 7200000) return true;
  var mins = Math.round(diff / 60000);
  var ts = mins < 1 ? 'less than a minute' : mins + ' min' + (mins === 1 ? '' : 's');
  return window.confirm(label + ' was already run ' + ts + ' ago. Run again?');
}}

// ── Tab system ─────────────────────────────────────────────────────────────────
var _tabLoaded = {{briefing: true, actions: false, seatalk: false, prereads: false}};

function switchTab(name) {{
  document.querySelectorAll('.tab-btn').forEach(function(b) {{
    b.classList.toggle('active', b.dataset.tab === name);
  }});
  document.querySelectorAll('.tab-panel').forEach(function(p) {{
    p.hidden = (p.id !== 'tab-' + name);
  }});
  window.scrollTo({{top: 0, behavior: 'instant'}});
  try {{ history.replaceState(null, '', '#' + name); }} catch(e) {{}}
  if (!_tabLoaded[name]) {{
    _tabLoaded[name] = true;
    if (name === 'actions') _fetchActionItems();
    if (name === 'seatalk') _fetchSeatalk(false);
    if (name === 'prereads') _fetchPrereads();
  }}
}}

// ── Action Items ──────────────────────────────────────────────────────────────
var COLOR = {{'🔴':'#dc2626','🟠':'#ea580c','🟡':'#d97706','🟢':'#16a34a','⚪':'#9ca3af'}};
var LABEL = {{'🔴':'Chase now','🟠':'Chase soon','🟡':'Watch','🟢':'Can wait','⚪':'When possible'}};
var _addFormOpen = false;

function _aiColor(item) {{
  var today = new Date().toISOString().slice(0,10);
  var eta = item.eta || null;
  var urgency = item.urgency || null;
  if (!eta && urgency === 'high') return '🔴';
  if (eta && eta <= today) return '🔴';
  if (eta) {{
    var days = Math.round((new Date(eta) - new Date(today)) / 86400000);
    if (days <= 3) return '🟠';
    if (days <= 7) return '🟡';
    return '🟢';
  }}
  return '⚪';
}}

function _fetchActionItems() {{
  var btn = document.getElementById('aiRefreshBtn');
  if (btn) {{ btn.disabled = true; btn.textContent = 'Loading…'; }}
  fetch('/api/action_items')
    .then(function(r) {{ return r.json(); }})
    .then(function(items) {{
      document.getElementById('aiTimestamp').textContent = 'Updated ' + _sgtNow();
      if (btn) {{ btn.disabled = false; btn.innerHTML = '&#8635; Refresh'; }}
      _renderActionItems(items);
    }})
    .catch(function(err) {{
      if (btn) {{ btn.disabled = false; btn.innerHTML = '&#8635; Refresh'; }}
      document.getElementById('aiContent').innerHTML =
        '<div class="st-error">Failed to load: ' + escHtml(String(err)) + '</div>';
    }});
}}

function _renderActionItems(items) {{
  var open = items.filter(function(i) {{ return !i.done; }});

  // Update tab badge
  var urgent = open.filter(function(i) {{ return _aiColor(i) === '🔴'; }}).length;
  var badge = document.getElementById('actBadge');
  if (urgent > 0) {{ badge.textContent = String(urgent); badge.style.display = ''; }}
  else {{ badge.style.display = 'none'; }}

  if (open.length === 0) {{
    document.getElementById('aiContent').innerHTML =
      '<p class="ai-empty">No open action items 🎉</p>';
    return;
  }}

  var order = ['🔴','🟠','🟡','🟢','⚪'];
  open.sort(function(a, b) {{
    var oi = order.indexOf(_aiColor(a)) - order.indexOf(_aiColor(b));
    if (oi !== 0) return oi;
    var ea = a.eta || 'z', eb = b.eta || 'z';
    return ea < eb ? -1 : ea > eb ? 1 : 0;
  }});

  var today = new Date().toISOString().slice(0,10);
  var html = '<table class="ai-table"><thead><tr>'
    + '<th style="width:32px"></th>'
    + '<th>Action</th>'
    + '<th style="width:195px">Source</th>'
    + '<th style="width:88px">Identified</th>'
    + '<th style="width:115px">ETA</th>'
    + '<th style="width:96px">Chase?</th>'
    + '<th style="width:75px"></th>'
    + '</tr></thead><tbody>';

  open.forEach(function(item) {{
    var c = _aiColor(item);
    var isManual = (item.source_type === 'manual');
    var rowClass = isManual ? ' class="ai-row-manual"' : '';

    var srcText;
    if (isManual) {{
      srcText = escHtml(item.source || 'Manual entry')
        + '<span class="manual-badge">Manual</span>';
    }} else if (item.source_type === 'seatalk') {{
      srcText = 'SeaTalk: ' + escHtml(item.source || '');
    }} else {{
      srcText = 'Email: ' + escHtml(item.source || '');
    }}

    html += '<tr id="ai-row-' + escHtml(item.id) + '"' + rowClass + '>'
      + '<td><span title="' + escHtml(LABEL[c] || '') + '" style="font-size:1.1rem">' + c + '</span></td>'
      + '<td>' + escHtml(item.action || '') + '</td>'
      + '<td style="font-size:.8rem;color:var(--muted)">' + srcText + '</td>'
      + '<td style="font-size:.8rem;color:var(--muted);white-space:nowrap">' + fmtDate(item.date_identified) + '</td>'
      + '<td>' + fmtEta(item.eta, today) + '</td>'
      + '<td style="white-space:nowrap;color:' + (COLOR[c] || '#6b7280') + ';font-size:.82rem;font-weight:600">' + escHtml(LABEL[c] || '') + '</td>'
      + '<td><button class="ai-done-btn" data-id="' + escHtml(item.id) + '" onclick="markDone(this.dataset.id,this)">✓ Done</button></td>'
      + '</tr>';
  }});

  html += '</tbody></table>';
  document.getElementById('aiContent').innerHTML = html;
}}

function markDone(id, btn) {{
  btn.disabled = true;
  btn.textContent = '…';
  fetch('/api/action_items?id=' + encodeURIComponent(id), {{method:'POST'}})
    .then(function(r) {{ return r.json(); }})
    .then(function(d) {{
      if (d.ok) {{
        var row = document.getElementById('ai-row-' + id);
        if (row) {{
          row.classList.add('ai-row-done');
          btn.textContent = '✓';
          setTimeout(function() {{
            row.style.transition = 'opacity .4s';
            row.style.opacity = '0';
            setTimeout(function() {{
              row.remove();
              // Refresh badge count
              var badge = document.getElementById('actBadge');
              var n = parseInt(badge.textContent || '1', 10) - 1;
              if (n > 0) {{ badge.textContent = String(n); }}
              else {{ badge.style.display = 'none'; }}
            }}, 420);
          }}, 600);
        }}
      }} else {{
        btn.disabled = false;
        btn.textContent = '✓ Done';
        alert('Error: ' + (d.error || 'unknown'));
      }}
    }})
    .catch(function(err) {{
      btn.disabled = false;
      btn.textContent = '✓ Done';
      alert('Request failed: ' + String(err));
    }});
}}

function toggleAddForm() {{
  _addFormOpen = !_addFormOpen;
  document.getElementById('addFormWrap').hidden = !_addFormOpen;
  if (_addFormOpen) {{
    var el = document.getElementById('newAction');
    if (el) el.focus();
  }}
}}

function submitAddItem(e) {{
  e.preventDefault();
  var action  = (document.getElementById('newAction').value || '').trim();
  var eta     = document.getElementById('newEta').value || null;
  var urgency = document.getElementById('newUrgency').value || null;
  var source  = (document.getElementById('newSource').value || '').trim() || null;
  if (!action) return;

  var btn = document.getElementById('addSubmitBtn');
  btn.disabled = true;
  btn.textContent = 'Adding…';

  fetch('/api/action_items', {{
    method: 'PUT',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{action: action, eta: eta, urgency: urgency, source: source}})
  }})
    .then(function(r) {{ return r.json(); }})
    .then(function(d) {{
      btn.disabled = false;
      btn.textContent = 'Add';
      if (d.ok) {{
        document.getElementById('newAction').value = '';
        document.getElementById('newEta').value = '';
        document.getElementById('newUrgency').value = '';
        document.getElementById('newSource').value = '';
        toggleAddForm();
        _fetchActionItems();
      }} else {{
        alert('Error: ' + (d.error || 'unknown'));
      }}
    }})
    .catch(function(err) {{
      btn.disabled = false;
      btn.textContent = 'Add';
      alert('Request failed: ' + String(err));
    }});
}}

// ── SeaTalk ───────────────────────────────────────────────────────────────────
var _stFetching = false;

function _fetchSeatalk(force) {{
  if (_stFetching) return;
  _stFetching = true;
  var btn = document.getElementById('stRefreshBtn');
  if (btn) {{ btn.disabled = true; btn.textContent = '⏳ Fetching…'; }}
  document.getElementById('stContent').innerHTML = '<p class="loading-msg">Fetching SeaTalk messages…</p>';
  document.getElementById('stTimestamp').textContent = '';

  var url = '/api/seatalk_check?date=' + encodeURIComponent(_PAGE_DATE) + (force ? '&force=1' : '');
  fetch(url)
    .then(function(r) {{ return r.json(); }})
    .then(function(d) {{
      _stFetching = false;
      if (btn) {{ btn.disabled = false; btn.innerHTML = '&#8635; Refresh'; }}
      if (d.ok) {{
        var ts = d.generated_at || _sgtNow();
        var cached = (d.cached && d.age_min > 0) ? ' · cached ' + d.age_min + 'min ago' : '';
        var dateLbl = (d.snapshot_date && d.snapshot_date !== _PAGE_DATE)
          ? ' · snapshot from ' + d.snapshot_date : '';
        document.getElementById('stTimestamp').textContent =
          d.message_count + ' messages · ' + ts + cached + dateLbl;
        document.getElementById('stContent').innerHTML = stMd(d.summary);
        tagPriorities(document.getElementById('stContent'));
      }} else {{
        document.getElementById('stContent').innerHTML =
          '<div class="st-error">' + escHtml(d.error || 'Unknown error') + '</div>';
      }}
    }})
    .catch(function(err) {{
      _stFetching = false;
      if (btn) {{ btn.disabled = false; btn.innerHTML = '&#8635; Refresh'; }}
      document.getElementById('stContent').innerHTML =
        '<div class="st-error">Request failed: ' + escHtml(String(err)) + '</div>';
    }});
}}

// ── SeaTalk markdown renderer ──────────────────────────────────────────────────
function stMd(md) {{
  var lines = md.split('\\n');
  var html = '';
  var inUl = false;
  function closeUl() {{ if (inUl) {{ html += '</ul>'; inUl = false; }} }}
  for (var i = 0; i < lines.length; i++) {{
    var line = lines[i];
    if (line.startsWith('### ')) {{ closeUl(); html += '<h3>' + inlineMd(line.slice(4)) + '</h3>'; continue; }}
    if (line.startsWith('## '))  {{ closeUl(); html += '<h3 style="font-size:.97rem;color:#172B4D;margin:1.1rem 0 .3rem">' + inlineMd(line.slice(3)) + '</h3>'; continue; }}
    if (line.startsWith('# '))   {{ closeUl(); html += '<h3 style="font-size:1rem;color:#1a1a2e;margin:1.1rem 0 .3rem">' + inlineMd(line.slice(2)) + '</h3>'; continue; }}
    var bm = line.match(/^[-*]\s+(.*)/);
    if (bm) {{
      if (!inUl) {{ html += '<ul>'; inUl = true; }}
      var cbText = bm[1]
        .replace(/^\[ \]\s*/, function() {{ return '<input type="checkbox" disabled> '; }})
        .replace(/^\[x\]\s*/i, function() {{ return '<input type="checkbox" checked disabled> '; }});
      html += '<li>' + inlineMd(cbText) + '</li>';
      continue;
    }}
    if (line.trim() === '') {{ closeUl(); html += '<br>'; continue; }}
    var pSect = line.match(/^\*\*(P([012])[^*]*)\*\*\s*$/);
    if (pSect) {{ closeUl(); html += '<div class="st-section"><span class="p' + pSect[2] + '-badge" style="font-size:.75rem;padding:3px 10px">' + pSect[1] + '</span></div>'; continue; }}
    closeUl();
    html += '<p>' + inlineMd(line) + '</p>';
  }}
  closeUl();
  return html;
}}

function inlineMd(s) {{
  s = s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  var friends = ['kel jin','han cheng'];
  s = s.replace(/\[Group:\s*([^\]]+)\]/g, function(m,n) {{
    return '<span class="st-src st-src-group">&#128101; ' + n.trim() + '</span>';
  }});
  s = s.replace(/\[DM:\s*([^\]]+)\]/g, function(m,n) {{
    n = n.trim();
    var isFriend = friends.some(function(f) {{ return n.toLowerCase().indexOf(f) !== -1; }});
    return '<span class="st-src st-src-dm">&#128172; ' + n + '</span>'
      + (isFriend ? ' <span class="st-friend">Friend :)</span>' : '');
  }});
  var vips = ['jianghong','hoi','fengc','feng c'];
  s = s.replace(/\*\*(.+?)\*\*/g, function(m,t) {{
    var isVip = vips.some(function(v) {{ return t.toLowerCase().indexOf(v) !== -1; }});
    return isVip ? '<strong class="st-vip">' + t + '</strong>' : '<strong>' + t + '</strong>';
  }});
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
  s = s.replace(/<strong>P0<\/strong>/g, '<span class="p0-badge">P0</span>');
  s = s.replace(/<strong>P1<\/strong>/g, '<span class="p1-badge">P1</span>');
  s = s.replace(/<strong>P2<\/strong>/g, '<span class="p2-badge">P2</span>');
  s = s.replace(/<strong>Action:<\/strong>/g, '<span class="st-action">Action:</span>');
  return s;
}}

function tagPriorities(root) {{
  root.querySelectorAll('li').forEach(function(li) {{
    var t = li.textContent;
    if (/^\s*P0[^a-z0-9]/i.test(t)) li.classList.add('p0-item');
    else if (/^\s*P1[^a-z0-9]/i.test(t)) li.classList.add('p1-item');
    else if (/^\s*P2[^a-z0-9]/i.test(t)) li.classList.add('p2-item');
  }});
}}

// ── Pre-reads ─────────────────────────────────────────────────────────────────
var _prFetching = false;
var _reasonAction = null;

function _fetchPrereads() {{
  if (_prFetching) return;
  _prFetching = true;
  var btn = document.getElementById('prRefreshBtn');
  if (btn) {{ btn.disabled = true; btn.textContent = 'Loading…'; }}
  fetch('/api/pdf_qa?date=' + encodeURIComponent(_PAGE_DATE))
    .then(function(r) {{ return r.json(); }})
    .then(function(d) {{
      _prFetching = false;
      if (btn) {{ btn.disabled = false; btn.innerHTML = '&#8635; Refresh'; }}
      document.getElementById('prTimestamp').textContent = 'Updated ' + _sgtNow();
      _renderPrereads(d.items || []);
    }})
    .catch(function(err) {{
      _prFetching = false;
      if (btn) {{ btn.disabled = false; btn.innerHTML = '&#8635; Refresh'; }}
      document.getElementById('prContent').innerHTML =
        '<div class="st-error">Failed to load: ' + escHtml(String(err)) + '</div>';
    }});
}}

function _safePdfId(n) {{
  return n.replace(/[^a-zA-Z0-9]/g, '-').slice(0, 40);
}}

function switchPdfTab(sid) {{
  document.querySelectorAll('.pr-subtab').forEach(function(b) {{
    b.classList.toggle('active', b.id === 'pr-subtab-' + sid);
  }});
  document.querySelectorAll('.pr-pdf-panel').forEach(function(p) {{
    p.hidden = (p.id !== 'pr-panel-' + sid);
  }});
}}

function switchPrView(sid, view) {{
  var qBtn = document.getElementById('pr-vbtn-q-' + sid);
  var aBtn = document.getElementById('pr-vbtn-a-' + sid);
  var qView = document.getElementById('pr-view-q-' + sid);
  var aView = document.getElementById('pr-view-a-' + sid);
  if (!qBtn || !aBtn || !qView || !aView) return;
  var toQ = (view === 'q');
  qBtn.classList.toggle('active', toQ); aBtn.classList.toggle('active', !toQ);
  qView.hidden = !toQ; aView.hidden = toQ;
}}

function _renderPrereads(items) {{
  // Count non-summary questions for badge
  var qCount = items.filter(function(i) {{ return i.slide_ref !== 'Summary'; }}).length;
  var badge = document.getElementById('prBadge');
  if (qCount > 0) {{ badge.textContent = String(qCount); badge.style.display = ''; }}
  else {{ badge.style.display = 'none'; }}

  if (!items || items.length === 0) {{
    document.getElementById('prContent').innerHTML =
      '<p class="ai-empty">No pre-read Q&amp;A for ' + escHtml(_PAGE_DATE) + '.<br>'
      + '<span style="font-size:.82rem;color:var(--muted)">Q&amp;A is generated automatically during the daily brief when pre-read emails have PDF attachments.</span></p>';
    return;
  }}

  // Group by pdf_name, preserving insertion order
  var groups = {{}};
  var order = [];
  items.forEach(function(item) {{
    var name = item.pdf_name || 'Unknown PDF';
    if (!groups[name]) {{ groups[name] = []; order.push(name); }}
    groups[name].push(item);
  }});

  var multiPdf = order.length > 1;
  var html = '';

  // ── PDF sub-tab nav (only when >1 PDF) ───────────────────────────────────────
  if (multiPdf) {{
    html += '<div class="pr-subtabs" id="prSubtabs">';
    order.forEach(function(pdfName, i) {{
      var sid = _safePdfId(pdfName);
      var label = pdfName.replace(/^\[.*?\]\s*/, '').replace(/\.pdf$/i, '').slice(0, 45);
      var qc = groups[pdfName].filter(function(x) {{ return x.slide_ref !== 'Summary'; }}).length;
      html += '<button class="pr-subtab' + (i === 0 ? ' active' : '') + '"'
        + ' id="pr-subtab-' + sid + '"'
        + ' data-sid="' + sid + '"'
        + ' onclick="switchPdfTab(this.dataset.sid)">'
        + '&#128196; ' + escHtml(label)
        + ' <span style="font-size:.72rem;opacity:.65;margin-left:4px">(' + qc + 'q)</span>'
        + '</button>';
    }});
    html += '</div>';
  }}

  // ── One panel per PDF ─────────────────────────────────────────────────────────
  order.forEach(function(pdfName, i) {{
    var qs = groups[pdfName];
    var sid = _safePdfId(pdfName);

    html += '<div class="pr-pdf-panel" id="pr-panel-' + sid + '"'
      + (i === 0 || !multiPdf ? '' : ' hidden') + '>';

    // PDF title (only when single-PDF; multi-tab uses the tab button as title)
    if (!multiPdf) {{
      html += '<div class="pr-pdf-title"><span class="pr-pdf-icon">&#128196;</span>'
        + escHtml(pdfName) + '</div>';
    }}

    // Summary card — formatted markdown, shown above the Questions/Answers toggle
    var summaryItem = null;
    qs.forEach(function(x) {{ if (x.slide_ref === 'Summary') summaryItem = x; }});
    if (summaryItem) {{
      html += '<div class="pr-summary-card" id="pr-row-' + escHtml(summaryItem.id) + '">'
        + '<div class="pr-summary-hdr">&#128204; Deck Summary</div>'
        + '<button class="del-corner" data-id="' + escHtml(summaryItem.id) + '"'
        +   ' data-pdf="' + escHtml(pdfName) + '" onclick="openDeleteModal(this)">&#10005; Remove</button>'
        + mdToHtml(summaryItem.question || '')
        + '</div>';
    }}

    // View toggle — use data-sid/data-view to avoid quoting issues in onclick
    html += '<div class="pr-view-toggle">'
      + '<button class="pr-view-btn active" id="pr-vbtn-q-' + sid + '"'
      +   ' data-sid="' + sid + '" data-view="q"'
      +   ' onclick="switchPrView(this.dataset.sid,this.dataset.view)">&#128203; Questions</button>'
      + '<button class="pr-view-btn" id="pr-vbtn-a-' + sid + '"'
      +   ' data-sid="' + sid + '" data-view="a"'
      +   ' onclick="switchPrView(this.dataset.sid,this.dataset.view)">&#128161; Proposed Answers</button>'
      + '</div>';

    var nonSummary = qs.filter(function(x) {{ return x.slide_ref !== 'Summary'; }});

    // ── Questions view ────────────────────────────────────────────────────────
    html += '<div id="pr-view-q-' + sid + '">';
    nonSummary.forEach(function(item) {{
      var isOthers = (item.slide_ref === 'Others');
      var refClass = isOthers ? 'pr-slide-ref pr-others' : 'pr-slide-ref';
      html += '<div class="pr-q-row" id="pr-row-' + escHtml(item.id) + '">'
        + '<span class="' + refClass + '">' + escHtml(item.slide_ref || 'General') + '</span>'
        + '<span class="pr-q-text">' + escHtml(item.question || '') + '</span>'
        + '<div class="pr-actions">'
        + '<button class="pr-edit-btn" data-id="' + escHtml(item.id) + '"'
        +   ' data-pdf="' + escHtml(pdfName) + '"'
        +   ' data-q="' + escHtml(item.question || '') + '"'
        +   ' onclick="openEditModal(this)">&#9998; Edit</button>'
        + '<button class="pr-del-btn" data-id="' + escHtml(item.id) + '"'
        +   ' data-pdf="' + escHtml(pdfName) + '" onclick="openDeleteModal(this)">&#10005; Delete</button>'
        + '</div></div>';
    }});
    html += '<div class="pr-add-row">'
      + '<input type="text" id="pr-add-' + sid + '" class="pr-add-input"'
      +   ' data-pdf="' + escHtml(pdfName) + '"'
      +   ' placeholder="Add a question for this deck&#8230;"'
      +   ' onkeydown="if(event.keyCode===13){{event.preventDefault();addQuestion(this.nextElementSibling);}}">'
      + '<button class="add-btn" onclick="addQuestion(this)">&#65291; Add</button>'
      + '</div>';
    html += '</div>'; // end questions view

    // ── Proposed Answers view ─────────────────────────────────────────────────
    html += '<div id="pr-view-a-' + sid + '" hidden>';
    if (nonSummary.length === 0) {{
      html += '<p class="pr-ans-empty">No questions generated yet.</p>';
    }} else {{
      // Group by slide_ref preserving order
      var slideGroups = {{}};
      var slideOrder = [];
      nonSummary.forEach(function(item) {{
        var sr = item.slide_ref || 'General';
        if (!slideGroups[sr]) {{ slideGroups[sr] = []; slideOrder.push(sr); }}
        slideGroups[sr].push(item);
      }});
      slideOrder.forEach(function(sr) {{
        var isOthers = (sr === 'Others');
        html += '<div class="pr-ans-slide-hdr' + (isOthers ? ' pr-ans-others-hdr' : '') + '">'
          + escHtml(sr) + '</div>';
        slideGroups[sr].forEach(function(item) {{
          var ans = (item.answer || '').trim();
          html += '<div class="pr-ans-item' + (isOthers ? ' pr-ans-others' : '') + '">'
            + '<div class="pr-ans-q">Q: ' + escHtml(item.question || '') + '</div>'
            + '<div class="pr-ans-a">'
            + (ans ? escHtml(ans)
                   : '<em class="pr-ans-empty">No answer generated — re-run the daily brief to populate.</em>')
            + '</div></div>';
        }});
      }});
    }}
    html += '</div>'; // end answers view

    html += '</div>'; // end pdf panel
  }});

  document.getElementById('prContent').innerHTML = html;
}}

// ── Reason modal ───────────────────────────────────────────────────────────────
function openDeleteModal(btn) {{
  _reasonAction = {{type:'delete', id:btn.dataset.id, pdf:btn.dataset.pdf, date:_PAGE_DATE}};
  document.getElementById('reasonModalTitle').textContent = 'Why are you deleting this question?';
  document.getElementById('editQuestionWrap').style.display = 'none';
  document.getElementById('editQuestionText').value = '';
  document.getElementById('reasonDetail').value = '';
  document.querySelectorAll('input[name=reason]').forEach(function(r) {{ r.checked = r.value==='other'; }});
  document.getElementById('reasonModal').style.display = 'flex';
  setTimeout(function() {{ document.getElementById('reasonDetail').focus(); }}, 80);
}}

function openEditModal(btn) {{
  _reasonAction = {{type:'edit', id:btn.dataset.id, pdf:btn.dataset.pdf, date:_PAGE_DATE}};
  document.getElementById('reasonModalTitle').textContent = 'Why are you editing this question?';
  document.getElementById('editQuestionWrap').style.display = 'block';
  document.getElementById('editQuestionText').value = btn.dataset.q || '';
  document.getElementById('reasonDetail').value = '';
  document.querySelectorAll('input[name=reason]').forEach(function(r) {{ r.checked = r.value==='other'; }});
  document.getElementById('reasonModal').style.display = 'flex';
  setTimeout(function() {{ document.getElementById('editQuestionText').focus(); }}, 80);
}}

function closeReasonModal() {{
  document.getElementById('reasonModal').style.display = 'none';
  _reasonAction = null;
}}

function confirmReason() {{
  if (!_reasonAction) return;
  var reason = (document.querySelector('input[name=reason]:checked') || {{}}).value || 'other';
  var detail = (document.getElementById('reasonDetail').value || '').trim();
  var action = _reasonAction;
  var confirmBtn = document.getElementById('reasonConfirmBtn');
  confirmBtn.disabled = true;
  confirmBtn.textContent = 'Saving…';

  function _done(ok, errMsg) {{
    confirmBtn.disabled = false;
    confirmBtn.textContent = 'Confirm';
    closeReasonModal();
    if (!ok) alert('Error: ' + (errMsg || 'unknown'));
  }}

  if (action.type === 'delete') {{
    fetch('/api/pdf_qa?id=' + encodeURIComponent(action.id), {{
      method: 'DELETE',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{reason: reason, reason_detail: detail, date: action.date}})
    }})
    .then(function(r) {{ return r.json(); }})
    .then(function(d) {{
      if (d.ok) {{
        var row = document.getElementById('pr-row-' + action.id);
        if (row) {{
          row.style.transition = 'opacity .3s';
          row.style.opacity = '0';
          setTimeout(function() {{ row.remove(); }}, 350);
        }}
        _done(true);
      }} else {{ _done(false, d.error); }}
    }})
    .catch(function(err) {{ _done(false, String(err)); }});

  }} else if (action.type === 'edit') {{
    var newQ = (document.getElementById('editQuestionText').value || '').trim();
    if (!newQ) {{
      confirmBtn.disabled = false;
      confirmBtn.textContent = 'Confirm';
      alert('Question cannot be empty.');
      return;
    }}
    fetch('/api/pdf_qa?id=' + encodeURIComponent(action.id), {{
      method: 'PATCH',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{question: newQ, reason: reason, reason_detail: detail, date: action.date}})
    }})
    .then(function(r) {{ return r.json(); }})
    .then(function(d) {{
      if (d.ok) {{
        var row = document.getElementById('pr-row-' + action.id);
        if (row) {{
          var span = row.querySelector('.pr-q-text');
          if (span) span.textContent = newQ;
          var editBtn = row.querySelector('.pr-edit-btn');
          if (editBtn) editBtn.dataset.q = newQ;
        }}
        _done(true);
      }} else {{ _done(false, d.error); }}
    }})
    .catch(function(err) {{ _done(false, String(err)); }});
  }}
}}

function addQuestion(btn) {{
  var row = btn.parentElement;
  var input = row.querySelector('.pr-add-input');
  var pdfName = input ? input.dataset.pdf : '';
  var question = (input ? input.value : '').trim();
  if (!question || !pdfName) return;
  btn.disabled = true;
  btn.textContent = 'Adding…';
  fetch('/api/pdf_qa', {{
    method: 'PUT',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{question: question, pdf_name: pdfName, date: _PAGE_DATE}})
  }})
  .then(function(r) {{ return r.json(); }})
  .then(function(d) {{
    btn.disabled = false;
    btn.textContent = '&#65291; Add';
    if (d.ok) {{
      if (input) input.value = '';
      _fetchPrereads();
    }} else {{ alert('Error: ' + (d.error || 'unknown')); }}
  }})
  .catch(function(err) {{
    btn.disabled = false;
    btn.textContent = '&#65291; Add';
    alert('Failed: ' + String(err));
  }});
}}

// ── Run brief ──────────────────────────────────────────────────────────────────
{run_brief_fn}

// ── Briefing tab init ──────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', function() {{

  // P0/P1/P2 badges on briefing body
  document.querySelectorAll('.card li').forEach(function(li) {{
    var t = li.textContent;
    if (/^\s*P0[^a-z0-9]/i.test(t)) li.classList.add('p0-item');
    else if (/^\s*P1[^a-z0-9]/i.test(t)) li.classList.add('p1-item');
    else if (/^\s*P2[^a-z0-9]/i.test(t)) li.classList.add('p2-item');
  }});

  // Delta/What-Changed highlight
  document.querySelectorAll('h2,h3').forEach(function(h) {{
    if (h.textContent.includes('What Changed') || h.textContent.includes('\U0001F504')) {{
      h.classList.add('delta-header');
      var next = h.nextElementSibling;
      while (next && !['H2','H3'].includes(next.tagName)) {{
        next.classList.add('delta-body');
        next = next.nextElementSibling;
      }}
    }}
  }});

  // Past-meeting strikethrough
  var nowSgt = new Date(Date.now() + 8 * 3600000);
  var todayYMD = nowSgt.toISOString().slice(0,10);
  var nowMins = nowSgt.getUTCHours() * 60 + nowSgt.getUTCMinutes();

  if (_PAGE_DATE < todayYMD) {{
    var banner = document.getElementById('staleBanner');
    if (banner) banner.style.display = 'flex';
    var meetRe = /\b(\d{{1,2}}):\d{{2}}|\b(attend|review|prep|present|join|check|sync|meeting|brief)\b/i;
    document.querySelectorAll('.card li,.card td').forEach(function(el) {{
      if (meetRe.test(el.textContent)) el.classList.add('past-item');
    }});
  }} else if (_PAGE_DATE === todayYMD) {{
    var timeRe = /\b(\d{{1,2}}):(\d{{2}})\s*(am|pm|AM|PM)?(?:\s*SGT)?\b/;
    document.querySelectorAll('.card li,.card td').forEach(function(el) {{
      var m = el.textContent.match(timeRe);
      if (!m) return;
      var h = parseInt(m[1],10), mins = parseInt(m[2],10);
      var ampm = (m[3] || '').toLowerCase();
      if (ampm === 'pm' && h !== 12) h += 12;
      if (ampm === 'am' && h === 12) h = 0;
      if (!ampm && h >= 1 && h <= 8) h += 12;
      if (h * 60 + mins < nowMins) el.classList.add('past-item');
    }});
  }}

  // Restore tab from URL hash
  var hash = (window.location.hash || '').replace('#','');
  if (hash === 'actions' || hash === 'seatalk' || hash === 'prereads') {{
    switchTab(hash);
  }} else {{
    // Pre-fetch action items silently to populate badge even while on Briefing tab
    fetch('/api/action_items')
      .then(function(r) {{ return r.json(); }})
      .then(function(items) {{
        var urgent = items.filter(function(i) {{
          return !i.done && _aiColor(i) === '🔴';
        }}).length;
        if (urgent > 0) {{
          var badge = document.getElementById('actBadge');
          badge.textContent = String(urgent);
          badge.style.display = '';
        }}
      }})
      .catch(function() {{}});
  }}
}});
</script>
</body>
</html>"""


def _not_found(date_str: str) -> str:
    turl = _trigger_url()
    cta = (
        f"""<button class="run-btn" onclick="this.disabled=true;this.textContent='Running…';window.location.href='{turl}'">
      &#9654;&nbsp; Generate Brief Now
    </button>"""
        if turl
        else "<p class='hint'>Check that the cron ran successfully.</p>"
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>No brief — {date_str}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root{{--primary:#EE4D2D;--bg:#FFF5F3;--text:#172B4D}}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:'Roboto',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
        background:var(--bg);color:var(--text);min-height:100vh;
        display:flex;align-items:center;justify-content:center}}
  .box{{text-align:center;padding:48px 32px;max-width:420px}}
  .icon{{font-size:3rem;margin-bottom:18px;opacity:.4}}
  h2{{font-size:1.35rem;margin-bottom:10px;font-weight:700}}
  p{{color:#6b7280;margin-bottom:28px;font-size:.92rem;line-height:1.5}}
  .hint{{color:#9ca3af;font-size:.85rem;margin-top:16px}}
  .run-btn{{
    background:var(--primary);color:#fff;border:none;border-radius:8px;
    padding:12px 28px;font-size:.95rem;font-weight:600;cursor:pointer;
    transition:opacity .15s;font-family:inherit;
  }}
  .run-btn:hover{{opacity:.85}}
  .run-btn:disabled{{opacity:.45;cursor:default}}
</style>
</head>
<body>
<div class="box">
  <div class="icon">&#9889;</div>
  <h2>No brief yet for {date_str}</h2>
  <p>Your daily briefing will be ready at 8:00 AM SGT.<br>Briefings are kept for 7 days.</p>
  {cta}
</div>
</body>
</html>"""


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # ── Session check ──────────────────────────────────────────────────────
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
