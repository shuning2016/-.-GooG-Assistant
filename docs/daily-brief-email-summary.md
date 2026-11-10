# Daily Brief 邮件 — Prompt 与规则总结

> 整理日期：2026-11-10 · 依据：`api/_briefing.py`（`SYSTEM_PROMPT` 第 393–594 行、`run_briefing` 第 904 行起）

每天收到的 daily update 邮件由 Vercel 上的程序生成：`api/_briefing.py` 抓取邮件、日历、SeaTalk 和待办事项，用一段**写死在代码里的 system prompt** 交给 Claude 总结，然后发出邮件。项目里的 `.md` 文件**不参与**这封邮件的生成。

## 1. 涉及的文件

| 文件 | 作用 | 和邮件的关系 |
|---|---|---|
| `api/_briefing.py` → `SYSTEM_PROMPT`（L393–594） | 邮件实际使用的 prompt | ✅ 直接决定邮件内容 |
| `api/_seatalk.py` → `SEATALK_BRIEF_PROMPT` | SeaTalk 单独总结（网页上的 SeaTalk Check 按钮，以及 10/12/15/19 点的 SeaTalk 邮件） | 不进 daily 邮件 |
| `.claude/commands/daily-brief.md` | 本地 `/daily-brief` 命令的指令 | ❌ 不使用 |
| `daily-brief.md`（根目录） | 上面那份的旧版本 | ❌ 不使用，且已过时 |
| `CLAUDE.md` | 本地 Claude Code 的项目规则 | ❌ 不使用，内容和 prompt 基本一致，但有出入（见第 6 节） |

## 2. 生成流程

| 步骤 | 内容 |
|---|---|
| 时间窗口 | 运行时刻往前推 24 小时 |
| 邮件 | 最多 40 封，排除推广、社交、更新三个分类；关键领域邮件读全文 + 图片，其余只读标题和摘要 |
| 关键领域关键词 | swarm, osp, sip, fp&a, fpa, budget, bpm, cncb, cnsip, cnls, sls, spx, fbs, artificial intelligence, ai, llm, genai, generative ai, machine learning, ai strategy, ai product |
| 日历 | 从现在到明天结束，最多 25 个；程序先拆分成"今天"和"明天" |
| SeaTalk | 读 07:50 推送的快照；没有快照就注明"不可用" |
| 待办事项 | 从 Redis 读取未完成的条目 |
| Pre-read PDF | 另外生成"Ian 风格"的预测问题，放在网页的 Q&A 标签页 |
| 模型 | `claude-sonnet-4-6`，失败时退回 `claude-haiku-4-5`；最多传 5 张图片；`max_tokens` 4096 |
| 后处理 | 解析 `NEW_ACTION_ITEMS` 数据块，把新待办存进 Redis，再从正文里删掉这段数据 |
| 发送 | 通过 SendGrid 或 SMTP 发给 Shuning.wang@shopee.com；标题格式 `shuning.wang \| Daily Brief - MM-DD HH:MM`；30 分钟内不重复发 |

## 3. 邮件结构（prompt 规定的固定顺序）

| # | 章节 | 要求 |
|---|---|---|
| 1 | Executive Brief | 2–4 句：今天最重要的事、最大风险、最关键的回复或准备；SeaTalk 的 P0 也放这里 |
| 2 | Prioritized Checklist | 按 P0 / P1 / P2 分组的勾选框，每条写成具体动作，SeaTalk 待办也放进来 |
| 3 | Open Action Items | **必须是表格**，按 🔴→🟠→🟡→🟢→⚪ 排序，同色内按 ETA 升序；为空时写 `No open action items.` |
| 4 | Today's Schedule | 表格：Time (SGT) \| Meeting \| Priority \| Prep / notes |
| 5 | Tomorrow's Schedule | 明天**全部**会议，规则和今天一样；明天没有会议时省略 |
| 6 | SeaTalk Activity | 分 P0 / P1 / P2，每条写 [DM/群] 谁 — 说了什么 — 建议动作 |
| 7 | What to Reply To | 发件人 — 主题或频道 — 建议回复；邮件和 SeaTalk DM 都包括 |
| 8 | Risks / Watchouts | 简短条目，区分事实和推断 |
| 9 | Suggested Priorities | 编号的下一步清单 |

待办颜色规则：

| 颜色 | 条件 |
|---|---|
| 🔴 Chase now | ETA 是今天或已过期；或者没有 ETA 且 urgency = high |
| 🟠 Chase soon | ETA 在 1–3 天内 |
| 🟡 Watch | ETA 在 4–7 天内 |
| 🟢 Can wait | ETA 在 8 天以后 |
| ⚪ When possible | 没有 ETA，且 urgency 为 low / medium / null |

## 4. 优先级规则

VIP：jianghong.liu@shopee.com、hoi@sea.com、fengc@sea.com

| | 邮件 | 会议（都要求**已接受邀请**，否则降为 P2，也不给准备建议） |
|---|---|---|
| **P0** | P0 领域（**AI、BPM**，看完整线程）；VIP 参与且收件人少于 10 人；VIP 在线程里回复过；标题含 "action" 且你在 To；VIP 参与且有直接要求、截止日期、升级或卡点 | P0 领域；VIP 参与且少于 10 人；VIP 就该会议直接发过消息 |
| **P1** | P1 领域（**Swarm/OSP、SIP、FP&A、Budget**）；VIP 发给 10 人以上；你在 To 且有提问或分派；直接称呼你；涉及截止日期、合同、出差、面试、审批等且你在 To；2 天内需要回复；风险、负责人或时间有实质变化；团队人事变动 | P1 领域；VIP 且 10 人以上；有外部人员；涉及决策、评审、升级、面试、招聘等；需要你汇报、决策或审批；需要 pre-read |
| **P2** | 同时满足：非关键领域、没有少于 10 人的 VIP 邮件、没有直接要求或截止日期、只是通知或抄送 | 超过 30 人；非关键领域；只是抄送或可选参加；**未接受邀请** |
| 屏蔽 | Newsletter、推广、收据、自动告警、日报类、例行日历或系统通知、非你领域的运营/仓储/物流报表（除非里面有指向你的新风险、要求或截止日期） | — |

会议还要求一律标出：全天日程、工作时间外（09:30–19:30 以外）的会议、**会议冲突**、需要今天准备的明天第一场会议。

SeaTalk 规则：

| 级别 | 条件 |
|---|---|
| P0 | VIP 的 DM；群里 @你；关键领域话题；指向你的直接要求或待办；升级、卡点、紧急问题 |
| P1 | 非 VIP 的 DM；你发过言的线程有新回复；涉及截止日期、会议、交付物；群消息直接涉及你负责的领域 |
| P2 | 一般信息，不需要你做什么 |
| 屏蔽 | 机器人告警、自动报表、只有表情回应的消息、进群/退群通知 |

## 5. 待办提取规则

| 规则 | 内容 |
|---|---|
| 1. Pre-read 邮件 | **不提取**（`is_prereread: true`），里面是会上要讨论的问题，不是待办 |
| 2. 会后纪要 / RE: 回复 | **提取**，识别 Item / PIC / ETA 表格 |
| 3. 图片 | 是会后的待办表格就逐行读取；pre-read 里的图片只简单描述；图表、logo 等只提一句 |
| 4. 输出格式 | 只把**新发现**的待办，以 JSON 放在正文末尾的 `---NEW_ACTION_ITEMS_START/END---` 之间；没有新待办就不输出这个块 |
| 归属 | 只保留 PIC 是你或你团队的；归属不清的也收录，但 `urgency: null` |

## 6. 发现的不一致

| # | 问题 | 影响 |
|---|---|---|
| 1 | prompt 里的 **P0 规则比 `CLAUDE.md` 宽**：prompt 规定"VIP 参与且收件人少于 10 人"或"VIP 在线程里回复过"就是 P0（会议也有类似规则）；`CLAUDE.md` 明确说 VIP 参与但不在 P0 领域、也没有直接要求时只算 P1 | 邮件里的 P0 可能比预期多 |
| 2 | prompt 要求标出**会议冲突**；`CLAUDE.md` 说不要提示冲突和衔接时间 | 邮件可能出现不需要的冲突提醒 |
| 3 | prompt 用 "his" / "he" 指代 Shuning（第 394、514 行） | 用词问题，不影响逻辑 |
| 4 | prompt 第 497 行还写着 "five key domains"，但现在已经是 6 个（加了 AI） | 小瑕疵 |
| 5 | 根目录的 `daily-brief.md` 停在旧版本（5 个领域、没有 AI=P0）；`.claude/commands/daily-brief.md` 才是新的 | 不影响邮件，但容易看错文件 |
