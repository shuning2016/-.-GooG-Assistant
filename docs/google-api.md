# 只读 Google 接口（Gmail / Calendar / Drive & Docs）— 调用文档

> 源码：`api/_google_ro.py`（公共部分）、`api/gmail.py`、`api/gcal.py`、`api/drive.py`
> 部署：Vercel 项目 `goo-g-assistant`（GitHub `main` 自动部署）

## 1. 这是什么

三个**只读**的 Google 代理接口。Google 的三个密钥（`GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_REFRESH_TOKEN`）只存在 Vercel 上，接口不会返回它们。调用方只需要持有一个口令 `GMAIL_PROXY_TOKEN`，三个接口共用这一个口令。

| 接口 | 能做 | 不能做 |
|---|---|---|
| `/api/gmail` | 查身份和权限、搜邮件、读纯文本正文 | 发信、回复、改标签、删除、标已读、读附件 |
| `/api/gcal` | 列日历、按时间段/关键词查日程、看单个日程和参会人回复 | 建/改/删日程、接受/拒绝邀请 |
| `/api/drive` | 搜文件、看元数据、读 Docs / Sheets / Slides / 纯文本内容 | 上传、编辑、共享、删除；读 PDF/图片内容 |

Google 端授权的 scope 只有 `gmail.readonly`、`calendar.readonly`、`drive.readonly`，所以即使代码出错，也不会有写权限。用 `op=whoami` 能看到 token 实际被授予的 scope。

## 2. 基本信息

| 项 | 值 |
|---|---|
| Base URL | `https://goo-g-assistant.vercel.app` |
| 接口路径 | `/api/gmail`、`/api/gcal`、`/api/drive` |
| 方法 | 只支持 `GET`，其他方法一律返回 `405` |
| 鉴权 | 请求头 `Authorization: Bearer <GMAIL_PROXY_TOKEN>` |
| 返回 | `application/json; charset=utf-8`，不缓存 |
| 时区 | 日历相关的时间都按新加坡时间（Asia/Singapore）处理 |

> ⚠️ **不要用 `googassistant.fuofuo.com`**：它前面有 Cloudflare 登录墙，所有请求都会被 302 跳转到 `fuofuo.com/login`。

## 3. 首次配置（一次性）

1. 生成口令，写进 `~/.zshrc`，同时复制到剪贴板（屏幕上不显示）：
   ```bash
   T=$(openssl rand -hex 32) && echo "export GMAIL_PROXY_TOKEN='$T'" >> ~/.zshrc && printf %s "$T" | pbcopy && unset T
   ```
2. Vercel → `goo-g-assistant` → Settings → Environment Variables → 新建 `GMAIL_PROXY_TOKEN`，粘贴口令，环境选 **Production**。
3. Deployments → 最新那次部署 → `⋯` → **Redeploy**。
4. 验证：
   ```bash
   source ~/.zshrc
   curl -s -H "Authorization: Bearer $GMAIL_PROXY_TOKEN" "https://goo-g-assistant.vercel.app/api/gmail?op=whoami"
   ```
   应返回 `"email": "shuning.wang@shopee.com"`，并且 `scopes` 里有 gmail / calendar / drive 三个 `.readonly`。

如果 `scopes` 里缺 `drive.readonly` 或 `calendar.readonly`，对应接口会返回 `502 google api error 403`。解决方法：用 `vercel-daily-brief/setup_google_token.py` 重新生成 refresh token（脚本已经包含这三个 scope），然后更新 Vercel 上的 `GOOGLE_REFRESH_TOKEN` 并 Redeploy。

## 4. Gmail — `/api/gmail`

### `op=whoami` — 核对身份和权限

```json
{
  "ok": true,
  "email": "shuning.wang@shopee.com",
  "messages_total": 425425,
  "scopes": [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/drive.readonly"
  ]
}
```

每个脚本开头先调一次，确认身份正确后再继续。三个接口用的是同一个 token，所以这个 whoami 对日历和 Drive 也有效。

### `op=search` — 搜索邮件（默认 op）

| 参数 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `q` | 是 | — | Gmail 搜索语句（见 7.1） |
| `max` | 否 | 20 | 1–50，超出范围时自动压到边界值 |

```json
{
  "ok": true, "query": "in:sent \"Listing Optimisation\" newer_than:90d", "count": 1,
  "messages": [{
    "id": "19a6c2f0e1b2c3d4", "thread_id": "19a6c2f0e1b2c3d4",
    "date": "Sun, 9 Nov 2026 10:12:33 +0800",
    "from": "Shuning Wang <shuning.wang@shopee.com>", "to": "someone@shopee.com", "cc": null,
    "subject": "Accepted: SBS Listing Optimisation Weekly", "snippet": "…"
  }]
}
```

最新的邮件在前；没有结果时返回 `count: 0`、`messages: []`。

### `op=read` — 读单封邮件

| 参数 | 必填 | 说明 |
|---|---|---|
| `id` | 是 | search 结果里的 `id` |

返回 `message`：包含 search 里的全部字段，另加 `body`（第一个 `text/plain` 部分，最多 20,000 字符；只有 HTML 版本的邮件为空字符串）和 `body_truncated`。

## 5. Calendar — `/api/gcal`

### `op=calendars` — 列出能看到的日历

```json
{ "ok": true, "calendars": [
  { "id": "shuning.wang@shopee.com", "summary": "Shuning Wang", "primary": true,
    "access_role": "owner", "time_zone": "Asia/Singapore" }
]}
```

### `op=events` — 按时间段查日程（默认 op）

| 参数 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `from` | 否 | 今天 00:00（SGT） | `YYYY-MM-DD`（当天 SGT 零点）或 ISO 时间，如 `2026-11-10T14:00` |
| `to` | 否 | `from` + 1 天 | 同上，**不含**这个时间点；必须晚于 `from` |
| `q` | 否 | — | 关键词，匹配标题、描述、地点、参会人 |
| `calendar` | 否 | `primary` | 日历 ID（来自 `op=calendars`） |
| `max` | 否 | 100 | 1–250 |

不带时区的时间一律按 SGT 处理。重复日程会展开成单次，按开始时间排序。

```json
{
  "ok": true, "calendar": "primary",
  "from": "2026-11-10T00:00:00+08:00", "to": "2026-11-11T00:00:00+08:00", "count": 1,
  "events": [{
    "id": "abc123_20261110T020000Z", "summary": "SIP Budget Review",
    "start": "2026-11-10T10:00:00+08:00", "end": "2026-11-10T11:00:00+08:00", "all_day": false,
    "status": "confirmed", "location": "SG-Meeting Room 5",
    "organizer": "someone@shopee.com",
    "my_response": "accepted",
    "attendee_count": 8,
    "attendees": [{ "email": "a@shopee.com", "name": "A", "response": "accepted", "optional": false }],
    "meet_link": "https://meet.google.com/…",
    "description": "…", "description_truncated": false,
    "attachments": [{ "title": "Pre-read", "url": "https://docs.google.com/…", "mime_type": "…" }],
    "recurring_event_id": "abc123", "html_link": "https://www.google.com/calendar/event?eid=…"
  }]
}
```

| 字段 | 说明 |
|---|---|
| `my_response` | 你自己的回复：`accepted` / `declined` / `tentative` / `needsAction`。你是组织者且没有参会人列表时为 `null` |
| `all_day` | 全天日程为 `true`，这时 `start` / `end` 是 `YYYY-MM-DD` 格式，`end` 是结束后的第二天 |
| `description` | 最多 5,000 字符 |
| `attachments` | 挂在日程上的文件，其中 Google Docs 的链接可以交给 `/api/drive?op=read` 读 |

### `op=get` — 单个日程

| 参数 | 必填 | 说明 |
|---|---|---|
| `id` | 是 | events 结果里的 `id` |
| `calendar` | 否 | 默认 `primary` |

返回 `event`，字段和 events 里的一样。

## 6. Drive / Docs — `/api/drive`

### `op=search` — 搜文件（默认 op）

| 参数 | 必填 | 说明 |
|---|---|---|
| `text` | 否 | 全文搜索（文件名 + 内容） |
| `name` | 否 | 文件名包含 |
| `type` | 否 | `doc` / `sheet` / `slide` / `pdf` / `folder` |
| `q` | 否 | 原生 Drive 查询语句（见 7.3），用于更复杂的条件 |
| `max` | 否 | 默认 20，范围 1–100 |

- 多个条件之间是 AND 关系；回收站里的文件会自动排除。
- 一个条件都不传，就返回最近修改的文件。
- 用了 `text`（或在 `q` 里写了 `fullText`）时，结果按相关度排序；否则按修改时间倒序。
- 搜索范围包括"我的云端硬盘"、共享给我的文件和共享云端硬盘。

```json
{
  "ok": true, "query": "trashed = false and fullText contains 'SIP' and mimeType = 'application/vnd.google-apps.document'",
  "count": 1,
  "files": [{
    "id": "1AbC…", "name": "SIP 2027 Plan", "mime_type": "application/vnd.google-apps.document", "type": "doc",
    "modified_time": "2026-11-08T03:21:00.000Z", "created_time": "…", "size": null,
    "owners": ["someone@shopee.com"], "last_modified_by": "someone@shopee.com",
    "web_link": "https://docs.google.com/document/d/1AbC…/edit", "shared_drive_id": null
  }]
}
```

`type` 不属于上面 5 种的文件，返回 `other`。Google 原生文件没有 `size`，为 `null`。

### `op=meta` — 文件信息

| 参数 | 必填 | 说明 |
|---|---|---|
| `id` | 是 | 文件 ID，**也可以直接贴 Docs/Sheets/Slides/Drive 的链接** |

### `op=read` — 读内容

| 参数 | 必填 | 说明 |
|---|---|---|
| `id` | 是 | 文件 ID 或链接 |
| `format` | 否 | 只对 Google Docs 有效：传 `md` 导出 Markdown（保留标题、列表、表格；内嵌图片替换成 `(image omitted)`），默认是纯文本 |

| 文件类型 | 返回内容 | `content_format` |
|---|---|---|
| Google Docs | 纯文本，或 Markdown（`format=md`） | `text` / `markdown` |
| Google Sheets | **第一个工作表**的 CSV | `csv` |
| Google Slides | 所有幻灯片的文字 | `text` |
| 纯文本 / CSV / JSON / XML 文件（≤ 5 MB） | 原文 | `text` |
| PDF、图片、Office 文件等 | 不支持，返回 `415` | — |

```json
{ "ok": true, "file": { … 同 meta … }, "content_format": "markdown",
  "content": "# SIP 2027 Plan\n…", "content_truncated": false }
```

`content` 最多 50,000 字符。Google 导出接口的上限是 10 MB，特别大的 Doc 会返回 `502 google api error 403`。

## 7. 查询语法速查

### 7.1 Gmail（`/api/gmail` 的 `q`）

| 写法 | 含义 |
|---|---|
| `in:sent` / `from:me` / `to:xx@shopee.com` | 已发送 / 发件人 / 收件人 |
| `"Listing Optimisation"` | 精确短语 |
| `subject:weekly` | 主题里包含 |
| `newer_than:90d` / `after:2026/08/01 before:2026/11/10` | 时间范围 |
| `has:attachment filename:pdf` | 带 PDF 附件 |
| `-subject:(accepted OR declined)` | 排除（`OR` 要大写） |

### 7.2 Calendar（`/api/gcal` 的 `q`）

只能写关键词，不支持运算符。例如 `q=budget` 会匹配标题、描述、地点或参会人里含 budget 的日程。

### 7.3 Drive（`/api/drive` 的 `q`）

| 写法 | 含义 |
|---|---|
| `name contains 'Weekly'` | 文件名包含 |
| `fullText contains 'SIP'` | 全文包含 |
| `modifiedTime > '2026-11-01T00:00:00'` | 修改时间晚于（UTC） |
| `'someone@shopee.com' in owners` | 所有者 |
| `'<文件夹ID>' in parents` | 在某个文件夹里 |
| `sharedWithMe` | 共享给我的 |
| `A and (B or C)` / `not name contains 'draft'` | 组合条件 |

字符串用单引号包起来，里面的 `'` 要写成 `\'`。用 `text` / `name` 参数时，接口会自动转义。

## 8. 错误码（三个接口相同）

所有错误都返回 `{"ok": false, "error": "..."}`。

| HTTP | 典型 `error` | 原因 / 处理 |
|---|---|---|
| 400 | `missing q` / `missing id` / `max must be an integer` / `unknown op: xxx` / `type must be one of …` / `from must be YYYY-MM-DD…` / `to must be after from` | 参数有误，按提示修改 |
| 401 | `unauthorized` | 没带口令、口令错误，或 Vercel 上没配 `GMAIL_PROXY_TOKEN`（或配了但还没 Redeploy） |
| 405 | `read-only: GET only` | 用了 GET 以外的方法 |
| 413 | `file larger than 5 MB` | Drive 上的纯文本文件太大 |
| 415 | `cannot read content of application/pdf; use op=meta` | 这种文件类型不能读内容 |
| 500 | `missing env var GOOGLE_…` | Vercel 上缺少 Google 变量，补上后 Redeploy |
| 500 | 异常类名 | 内部错误，重试；持续出现就看 Vercel 函数日志 |
| 502 | `google api error <code>` | Google 拒绝了请求，见下表 |
| 502 | `google api unreachable` | 连不上 Google，稍后重试 |
| 504 | `google api timeout` | Google 响应太慢（读文件时超过 45 秒），常见于特别大的 Sheet，稍后重试或改读别的文件 |

| Google 错误码 | 常见原因 |
|---|---|
| 400 | refresh token 失效或被撤销；或者查询语句语法错误 |
| 403 | 缺少对应的 scope（用 whoami 看 `scopes`）；没有这个文件的访问权限；或者导出文件超过 10 MB |
| 404 | ID 不存在，或者你没有权限看到 |
| 429 | 调用太频繁，等一会再试 |

为了不泄露请求细节，接口不会回传 Google 的原始报错内容。

## 9. 调用示例

### 9.1 curl

```bash
B=https://goo-g-assistant.vercel.app
AUTH="Authorization: Bearer $GMAIL_PROXY_TOKEN"

# 身份 + 权限
curl -s -H "$AUTH" "$B/api/gmail?op=whoami"

# Gmail：过去 90 天的已发送
curl -s -G -H "$AUTH" "$B/api/gmail" \
  --data-urlencode "op=search" \
  --data-urlencode 'q=in:sent "Listing Optimisation" newer_than:90d'

# Calendar：今天的日程
curl -s -H "$AUTH" "$B/api/gcal?op=events"

# Calendar：本周带 budget 的日程
curl -s -G -H "$AUTH" "$B/api/gcal" \
  --data-urlencode "op=events" --data-urlencode "from=2026-11-10" \
  --data-urlencode "to=2026-11-15" --data-urlencode "q=budget"

# Drive：全文搜 SIP 相关的 Doc
curl -s -G -H "$AUTH" "$B/api/drive" \
  --data-urlencode "op=search" --data-urlencode "text=SIP" --data-urlencode "type=doc"

# Docs：直接用链接读成 Markdown
curl -s -G -H "$AUTH" "$B/api/drive" \
  --data-urlencode "op=read" --data-urlencode "format=md" \
  --data-urlencode "id=https://docs.google.com/document/d/<文件ID>/edit"
```

> zsh 提示：URL 里有 `?` 时一定要加引号；参数里有空格、引号、中文或链接时，用 `-G --data-urlencode`。

### 9.2 Python（requests）

`requests` 会自动读取 `HTTPS_PROXY`，本机出网要走代理时也能直接用。

```python
import os
import requests

BASE = "https://goo-g-assistant.vercel.app/api"
HEADERS = {"Authorization": f"Bearer {os.environ['GMAIL_PROXY_TOKEN']}"}


def google(service: str, op: str, **params) -> dict:
    """service: gmail | gcal | drive"""
    r = requests.get(f"{BASE}/{service}", headers=HEADERS,
                     params={"op": op, **params}, timeout=70)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"{service}.{op} → {r.status_code} {data.get('error')}")
    return data


me = google("gmail", "whoami")
assert me["email"] == "shuning.wang@shopee.com", me["email"]

sent = google("gmail", "search", q='in:sent "Listing Optimisation" newer_than:90d')
today = google("gcal", "events")
accepted = [e for e in today["events"] if e["my_response"] == "accepted"]

docs = google("drive", "search", text="SIP", type="doc", max=5)
if docs["files"]:
    doc = google("drive", "read", id=docs["files"][0]["id"], format="md")
    print(doc["content"][:500])
```

## 10. 限制

| 项 | 说明 |
|---|---|
| 不分页 | Gmail 最多 50 封、Calendar 最多 250 个、Drive 最多 100 个，都只返回前 N 个；要更多就把时间范围拆小 |
| Gmail 速度 | search 会逐封拉取邮件头，50 封大约 5–15 秒 |
| 只有文本 | 不返回 HTML、附件或图片；PDF 只能看元数据 |
| Sheets | 只导出第一个工作表 |
| 账号固定 | 都是 Vercel 上 refresh token 对应的账号（`shuning.wang@shopee.com`） |

## 11. 安全与口令轮换

- 口令只放在本机环境变量或 Muse 的安全存储里，**不要**写进代码、git、memory 或聊天记录。
- 怀疑口令泄露：在 Vercel 上把 `GMAIL_PROXY_TOKEN` 改成新值并 Redeploy，旧口令立刻失效，三个接口同时生效。
- 要停用全部接口：删除 `GMAIL_PROXY_TOKEN` 并 Redeploy，之后所有请求都会返回 401。
- 怀疑 Google refresh token 泄露：到 Google 账号 → 安全性 → 第三方应用访问权限里撤销授权，再用 `setup_google_token.py` 重新生成 token，更新到 Vercel。
