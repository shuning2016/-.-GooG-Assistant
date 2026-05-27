# GooG Assistant — Cost & Optimisation Plan

---

## Current Cost per Daily Run: ~$0.32

| Call | What it does | Input tokens | Output tokens | Cost |
|------|-------------|-------------|--------------|------|
| **PDF Q&A** (claude-sonnet-4-6) | Reads pre-read PDF, generates Ian-style questions + proposed answers | ~55,000 (37,500 PDF + 17,500 system prompt) | ~3,000 | **~$0.21** |
| **Daily briefing** (claude-sonnet-4-6) | Reads emails + calendar + SeaTalk → briefing markdown | ~20,000 | ~3,500 | **~$0.11** |
| Gmail / Calendar API (gws) | Email + calendar fetch | — | — | Free (Google Workspace quota) |
| Redis (Upstash) | Store Q&A, action items, SeaTalk state | — | — | ~Free (free tier) |
| Vercel | Serve the web app | — | — | ~Free (hobby tier) |

**Monthly total (22 working days): ~$7**
**Monthly total (30 days): ~$9.70**

---

## Why the PDF Call is So Expensive

The **25-slide PDF** alone costs ~$0.11 because Anthropic's native PDF API charges ~1,500 tokens per page (processed as images). Add the **70KB Ian QAer system prompt** (5 knowledge files) at ~17,500 tokens and it becomes the dominant cost driver.

| PDF cost breakdown | Tokens | Cost |
|-------------------|--------|------|
| 25 pages × 1,500/page | 37,500 | $0.113 |
| Ian QAer system prompt (5 MDs) | 17,500 | $0.053 |
| User instruction + output | ~3,200 | $0.055 |
| **Total** | **~58,200** | **~$0.21** |

---

## Improvement Plan

| # | Change | Saves/day | Saves/month | Effort | Risk |
|---|--------|-----------|-------------|--------|------|
| **1** | Switch briefing to **claude-haiku** (emails don't need Sonnet reasoning) | ~$0.09 | ~$2.00 | 1 line | Low |
| **2** | **Cap PDF at 20 pages** (most Ian questions come from first 15–20 slides) | ~$0.02 | ~$0.44 | 5 lines | Low |
| **3** | **Trim `persona_summary.md`** (25.7KB → biggest single file, compress redundant sections) | ~$0.02 | ~$0.44 | Manual edit | Low |
| **4** | **Skip PDF Q&A if Redis already has today's data** (prevents double-billing on cron restart) | up to $0.21 on retries | Situational | Already partial | None |

### Projected cost after #1 + #2 + #3

| | Before | After |
|--|--------|-------|
| Per day | ~$0.32 | ~$0.19 |
| Per month (22 days) | ~$7.00 | ~$4.20 |
| Per month (30 days) | ~$9.70 | ~$5.70 |

---

## Model Routing (the Right Tool for Each Job)

| Task | Current | Recommended | Why |
|------|---------|-------------|-----|
| PDF Q&A + Ian-style questions | Sonnet ✅ | Sonnet ✅ | Needs document reasoning + style mimicking |
| Email triage + briefing | Sonnet | **Haiku** | Pattern matching + summarisation, no deep reasoning needed |
| Proposed answers from deck | Sonnet ✅ | Sonnet ✅ | Factual extraction from PDF, accuracy matters |

---

## Implementation Priority

1. **Now (5 min):** Switch briefing to Haiku → biggest ROI, zero risk
2. **Now (5 min):** Add 20-page cap on PDF input → simple guard, saves ~$0.02/PDF
3. **Next (30 min):** Trim `persona_summary.md` — keep core style rules, remove repetitive sections
4. **Later:** Skip PDF Q&A if Redis already has today's data (defensive guard for cron restarts)
