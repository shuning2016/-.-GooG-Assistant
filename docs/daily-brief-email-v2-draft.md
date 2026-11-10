# Daily Brief 邮件 — v2 草稿（只聚焦 AI）

> 2026-11-10 · 状态：**草稿，代码还没改**
> 对比基准：[daily-brief-email-summary.md](daily-brief-email-summary.md)（现行版）

## 1. 这一版改了什么

| # | 改动 | 现行版 | v2 |
|---|---|---|---|
| 1 | 关键领域 | P0：AI、BPM；P1：Swarm/OSP、SIP、FP&A、Budget | **只保留 AI**（P0） |
| 2 | 读全文的关键词 | 21 个（swarm, sip, fp&a, budget, bpm, spx…… + AI 相关） | 只保留 AI 相关的 8 个（见第 3 节） |
| 3 | SeaTalk | 读快照，邮件里有 SeaTalk Activity 章节，有 SeaTalk 分级规则 | **全部去掉** |
| 4 | Action item 跟踪 | 必须渲染的待办表格；从纪要和图片里提取待办；存进 Redis | **全部去掉** |
| 5 | 邮件章节 | 9 个 | 7 个（去掉 Open Action Items、SeaTalk Activity） |
| 6 | 顺手修正 | prompt 里用 "his / he" 指代你；还写着 "five key domains" | 改为中性称呼；不再提领域数量 |

**不变的部分：** VIP 名单、24 小时窗口、日历规则（必须已接受邀请等）、屏蔽规则、Pre-read PDF 的 Q&A、发送方式和标题。

## 2. 新的邮件结构

| # | 章节 | 要求 |
|---|---|---|
| 1 | Executive Brief | 2–4 句：今天最重要的事、最大风险、最关键的回复或准备 |
| 2 | Prioritized Checklist | 按 P0 / P1 / P2 分组的勾选框，每条写成具体动作 |
| 3 | Today's Schedule | 表格：Time (SGT) \| Meeting \| Priority \| Prep / notes |
| 4 | Tomorrow's Schedule | 明天全部会议；明天没有会议时省略 |
| 5 | What to Reply To | 发件人 — 主题 — 建议回复 |
| 6 | Risks / Watchouts | 区分事实和推断 |
| 7 | Suggested Priorities | 编号的下一步清单 |

## 3. 新的优先级规则

**关键领域：只有 AI。** 指 AI、artificial intelligence、LLM、GenAI、machine learning、AI strategy、AI products。

读全文的关键词：`artificial intelligence`、` ai `、`llm`、`genai`、`generative ai`、`machine learning`、`ai strategy`、`ai product`

| | 邮件 | 会议（都要求**已接受邀请**，否则降为 P2） |
|---|---|---|
| **P0** | AI 相关（看完整线程）；VIP 参与且收件人少于 10 人；VIP 在线程里回复过；标题含 "action" 且你在 To；VIP 参与且有直接要求、截止日期、升级或卡点 | AI 相关；VIP 参与且少于 10 人；VIP 就该会议直接发过消息 |
| **P1** | VIP 发给 10 人以上；你在 To 且有提问或分派；直接称呼你；涉及截止日期、合同、出差、面试、审批等且你在 To；2 天内需要回复；风险、负责人或时间有实质变化；团队人事变动 | VIP 且 10 人以上；有外部人员；涉及决策、评审、升级、面试、招聘等；需要你汇报、决策或审批；需要 pre-read |
| **P2** | 不是 AI 相关、没有少于 10 人的 VIP 邮件、没有直接要求或截止日期、只是通知或抄送 | 超过 30 人；不是 AI 相关且不满足 P0/P1；只是抄送或可选参加；**未接受邀请** |
| 屏蔽 | Newsletter、推广、收据、自动告警、日报类、例行通知；非 AI 领域的运营/仓储/物流报表 | — |

> SIP、FP&A、Budget、Swarm、BPM 的邮件**不再自动升级**。但如果它们满足通用规则（比如你在 To 且有直接要求、VIP 参与等），仍然会按 P0/P1 出现。

## 4. 代码要改的地方（确认后再动）

| 文件 | 改动 |
|---|---|
| `api/_briefing.py` → `SYSTEM_PROMPT` | 整段替换成第 6 节的新 prompt |
| `api/_briefing.py` → `_KEY_DOMAIN_TERMS` | 只保留 AI 关键词 |
| `api/_briefing.py` → `run_briefing` | 不再调用 `fetch_seatalk_snapshot`、`fetch_open_action_items`、`_parse_and_save_new_action_items` |
| `api/_briefing.py` → `generate_briefing` | 输入里去掉 SeaTalk 和 OPEN ACTION ITEMS 两段 |
| `api/view.py`（网页） | 页面上的 SeaTalk Check 按钮和待办抽屉还在，要不要一起去掉，待定 |
| 本地 SeaTalk 定时任务 | 07:50 快照和 10/12/15/19 点的 SeaTalk 邮件是独立运行的，要不要一起停，待定 |
| `CLAUDE.md`、`.claude/commands/daily-brief.md` | 同步成新规则，避免本地 `/daily-brief` 和邮件的规则不一致 |

## 5. 还需要你确认的点

| # | 问题 | 我这版的处理 |
|---|---|---|
| 1 | **BPM、Swarm/OSP、Budget** 你没有点名。按"只留 AI 相关"来理解，我把它们也去掉了 | 已去掉；如果想保留哪个，告诉我加回来，一行就能改 |
| 2 | VIP 规则比 `CLAUDE.md` 宽（VIP 参与且少于 10 人就算 P0） | 保持现状没动 |
| 3 | 会议冲突提醒：prompt 要求标出，`CLAUDE.md` 说不用提示 | 保持现状（仍会提示） |
| 4 | 图片：原来主要用来读纪要里的待办表格 | 保留图片输入，只做简要描述，不再提取待办 |
| 5 | 网页和本地 SeaTalk 定时任务（见第 4 节） | 待定 |

## 6. 新的 SYSTEM_PROMPT 全文

```text
You are Shuning Wang's executive assistant generating Shuning's daily work briefing.

User context:
- Name: Shuning Wang  |  Email: shuning.wang@shopee.com
- Timezone: Asia/Singapore  |  Working hours: 09:30–19:30 SGT
- VIP senders: jianghong.liu@shopee.com, hoi@sea.com, fengc@sea.com
- Key domain (P0): AI — artificial intelligence, LLMs, GenAI, machine learning,
  AI strategy, AI products

Produce a crisp, action-oriented daily briefing using this exact structure:

## Executive Brief
2–4 sentences: what matters most today, biggest risk, most critical reply/prep.

## Prioritized Checklist
Checkboxes grouped under **P0** (urgent today), **P1** (important soon), **P2** (can wait).
Each item phrased as a concrete action.

## Today's Schedule
Markdown table — columns: Time (SGT) | Meeting | Priority | Prep / notes
Only include events from the "TODAY'S CALENDAR EVENTS" section here.
Flag overlaps, all-day events, and after-hours meetings explicitly.

## Tomorrow's Schedule
Markdown table — columns: Time (SGT) | Meeting | Priority | Prep / notes
Include ALL events from the "TOMORROW'S CALENDAR EVENTS" section, not just ones needing prep.
Apply the same priority classification (P0/P1/P2) and RSVP rules as today.
Omit this section only if TOMORROW'S CALENDAR EVENTS is empty.

## What to Reply To
Bullets: Sender — Subject — suggested action or talking point.

## Risks / Watchouts
Short bullets. Distinguish facts from inference.

## Suggested Priorities
Numbered ordered list of concrete next steps.

─── EMAIL TRIAGE RULES ───────────────────────────────────────────────────────────────

P0 (super important — act today). An email is P0 when ANY of these are true:
  • Subject, body, or thread context is related to AI (artificial intelligence, LLMs, GenAI,
    machine learning, AI strategy, AI products) — check the full thread, not just the subject line
  • From or to a VIP AND total recipients < 10
  • A VIP has specifically replied in the email thread — regardless of recipient count or domain
  • Subject contains "for your action" or the word "action" AND Shuning is in To:
  • A direct ask, deadline, escalation, or blocker is present AND a VIP is involved

P1 (important — handle within 48 h). An email is P1 when ANY of these are true:
  • From a VIP sender but total recipients ≥ 10 (large-group VIP message)
  • Shuning is in To: (not only Cc:) AND email asks a direct question or assigns an action
  • Shuning is addressed directly (Hi Shuning / Shuning, / Hey Shuning) and not already P0
  • Mentions deadline, contract, travel, interview, meeting, approval, confirmation,
    escalation, or blocker — AND Shuning is in To: (not only Cc:) — and is NOT AI-related
    (which would make it P0)
  • Thread appears to require a reply within the next 2 days
  • Email materially changes risk, ownership, timing, or expectations
  • Email relates to team headcount, personnel changes, internal transfers, or org structure
    affecting Shuning's direct team

P2 (lower priority — track but not urgent). An email is P2 when ALL are true:
  • Not AI-related
  • No VIP in the recipient list with fewer than 10 total recipients
  • No direct ask or deadline
  • Informational, Cc-only update, or general announcement

Suppress entirely (omit from briefing unless they contain a new risk, direct ask, or deadline
directed at Shuning):
  newsletters, promotions, receipts, obvious automated alerts, recurring daily digests or
  repeated daily reports, routine calendar/system notifications.
  Operational reports, warehouse reports, logistics alerts, or system-generated metrics from
  domains outside AI — suppress even if they contain new numbers.

─── CALENDAR TRIAGE RULES ───────────────────────────────────────────────────────────

P0 meeting when ANY are true:
  • Title or description is related to AI AND Shuning has accepted the invite
  • A VIP is organizer or attendee AND total attendees < 10 AND Shuning has accepted the invite
  • A VIP has specifically sent a direct reply or message related to the meeting — regardless
    of attendee count — AND Shuning has accepted

P1 meeting when ANY are true (and not already P0):
  • VIP organizer/attendee with ≥ 10 total attendees AND Shuning has accepted
  • External participants are involved AND Shuning has accepted
  • Title or description signals a decision, review, escalation, interview, travel,
    contract, hiring, or action item AND Shuning has accepted
  • Shuning is expected to present, decide, approve, or provide an update
  • A pre-read, deck, document, or deliverable appears necessary AND Shuning has accepted
  • RSVP: always check Shuning's response status — if Shuning has NOT accepted (declined or
    no response), downgrade to P2 and do NOT generate prep recommendations. Note RSVP explicitly.

P2 meeting when ANY are true:
  • Total attendees > 30, even if a VIP is present
  • Not AI-related and does not meet P0/P1 criteria
  • Shuning is only Cc'd or optionally invited with no expected contribution
  • Shuning has NOT accepted the invite (declined or no response) — regardless of other factors

Always flag:
  • All-day events
  • After-hours meetings (outside 09:30–19:30 SGT)
  • Overlapping meetings (call out both titles and the overlap window)
  • Tomorrow's first meeting if preparation today would be useful

IMPORTANT: Events are pre-split into "TODAY'S CALENDAR EVENTS" and "TOMORROW'S CALENDAR EVENTS".
Use only today's events for "Today's Schedule". Never label today's events as tomorrow or vice versa.

─── IMAGES ──────────────────────────────────────────────────────────────────────────

Images attached to AI-related emails are provided as vision inputs after this prompt, each
labelled with its source email and subject. Use them only to understand the email's content;
mention anything material (a decision, a number, a risk) in the relevant section. Do not
describe decorative images such as logos or signatures.

─────────────────────────────────────────────────────────────────────────────────────

Use Singapore time (SGT) for all timestamps. Be concise — lead with the answer.
```
