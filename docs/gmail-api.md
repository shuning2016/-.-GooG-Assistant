# `/api/gmail` 只读 Gmail 接口 — 调用文档

> 源码：`api/gmail.py` · 部署：Vercel 项目 `goo-g-assistant`（GitHub `main` 自动部署）

## 1. 这是什么

一个**只读**的 Gmail 代理。Google 的三个密钥（`GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_REFRESH_TOKEN`）只存放在 Vercel，接口不会把它们返回给任何人。调用方只持有一个独立的口令 `GMAIL_PROXY_TOKEN`，凭它拿搜索结果和邮件内容。

| 能做 | 不能做 |
|---|---|
| 查当前登录邮箱 | 发信、回复、转发 |
| 按 Gmail 搜索语法搜邮件（含已发送） | 改标签、归档、删除、标已读 |
| 读单封邮件的纯文本正文 | 读附件、读 HTML 正文 |
| | 拿到 Google 的原始密钥 |

Google 端授权的 scope 是 `gmail.readonly`，即使接口代码出错，也没有写权限。

## 2. 基本信息

| 项 | 值 |
|---|---|
| Base URL | `https://goo-g-assistant.vercel.app/api/gmail` |
| 方法 | 只支持 `GET`，其他方法一律返回 `405` |
| 鉴权 | 请求头 `Authorization: Bearer <GMAIL_PROXY_TOKEN>` |
| 返回格式 | `application/json; charset=utf-8`，不缓存（`Cache-Control: no-store`） |
| 超时 | 函数最长运行 60 秒 |

> ⚠️ **不要用 `googassistant.fuofuo.com`。** 这个自定义域名前面有 Cloudflare 登录墙，所有请求都会被 302 跳转到 `fuofuo.com/login`，脚本调不通。

## 3. 首次配置（一次性）

1. 生成口令：
   ```bash
   openssl rand -hex 32
   ```
2. Vercel → 项目 `goo-g-assistant` → Settings → Environment Variables → 新建 `GMAIL_PROXY_TOKEN`，环境选 **Production**。
3. Deployments → 最新那次部署 → **Redeploy**（环境变量要重新部署后才生效）。
4. 在本机保存口令（不要提交进 git）：
   ```bash
   echo "export GMAIL_PROXY_TOKEN='上面生成的那串'" >> ~/.zshrc
   source ~/.zshrc
   ```
5. 验证：
   ```bash
   curl -s -H "Authorization: Bearer $GMAIL_PROXY_TOKEN" \
     "https://goo-g-assistant.vercel.app/api/gmail?op=whoami"
   ```
   应返回 `"email": "shuning.wang@shopee.com"`。

## 4. 接口列表

所有操作都是同一个 URL，用 `op` 参数区分。

### 4.1 `op=whoami` — 核对身份

| 参数 | 必填 | 说明 |
|---|---|---|
| `op` | 是 | `whoami` |

**返回**
```json
{
  "ok": true,
  "email": "shuning.wang@shopee.com",
  "messages_total": 425425
}
```

建议每个脚本在开头先调一次，确认邮箱身份正确后再继续。

### 4.2 `op=search` — 搜索邮件

| 参数 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `op` | 否 | `search` | 不传 `op` 时默认就是 search |
| `q` | 是 | — | Gmail 搜索语句，语法和 Gmail 搜索框一样（见第 6 节） |
| `max` | 否 | `20` | 最多返回几封，范围 1–50；超过 50 按 50 处理，小于 1 按 1 处理 |

**返回**
```json
{
  "ok": true,
  "query": "in:sent \"Listing Optimisation\" newer_than:90d",
  "count": 1,
  "messages": [
    {
      "id": "19a6c2f0e1b2c3d4",
      "thread_id": "19a6c2f0e1b2c3d4",
      "date": "Sun, 9 Nov 2026 10:12:33 +0800",
      "from": "Shuning Wang <shuning.wang@shopee.com>",
      "to": "someone@shopee.com",
      "cc": null,
      "subject": "Accepted: SBS Listing Optimisation Weekly",
      "snippet": "Shuning Wang has accepted this invitation..."
    }
  ]
}
```

| 字段 | 说明 |
|---|---|
| `id` | 邮件 ID，传给 `op=read` 用 |
| `thread_id` | 所属线程 ID |
| `date` / `from` / `to` / `cc` / `subject` | 原始邮件头，没有该头时为 `null` |
| `snippet` | Gmail 自动生成的约 200 字摘要 |

结果按 Gmail 默认顺序（最新在前）排列。没有匹配时，`count` 为 `0`，`messages` 为 `[]`。

### 4.3 `op=read` — 读单封邮件

| 参数 | 必填 | 说明 |
|---|---|---|
| `op` | 是 | `read` |
| `id` | 是 | 邮件 ID（来自 search 结果的 `id`） |

**返回**
```json
{
  "ok": true,
  "message": {
    "id": "19a6c2f0e1b2c3d4",
    "thread_id": "19a6c2f0e1b2c3d4",
    "date": "...",
    "from": "...",
    "to": "...",
    "cc": null,
    "subject": "...",
    "snippet": "...",
    "body": "纯文本正文……",
    "body_truncated": false
  }
}
```

| 字段 | 说明 |
|---|---|
| `body` | 第一个 `text/plain` 部分的正文；只有 HTML 版本的邮件这里为空字符串 `""` |
| `body_truncated` | 正文超过 20,000 字符时截断，并返回 `true` |

## 5. 错误码

所有错误都返回 `{"ok": false, "error": "..."}`。

| HTTP | `error` | 原因 | 处理 |
|---|---|---|---|
| 400 | `missing q` | search 没传 `q` | 补上 `q` |
| 400 | `max must be an integer` | `max` 不是数字 | 改成整数 |
| 400 | `missing id` | read 没传 `id` | 补上 `id` |
| 400 | `unknown op: xxx` | `op` 拼错了 | 只能用 `whoami` / `search` / `read` |
| 401 | `unauthorized` | 没带口令、口令错误，或 Vercel 上没配 `GMAIL_PROXY_TOKEN` | 检查请求头格式和口令；确认已 Redeploy |
| 405 | `read-only: GET only` | 用了 POST / PUT / PATCH / DELETE | 改用 GET |
| 500 | `missing env var 'GOOGLE_...'` | Vercel 上缺少某个 Google 变量 | 去 Vercel 补上，再 Redeploy |
| 500 | 异常类名，如 `TimeoutError` | 其他内部错误 | 重试；持续出现就看 Vercel 函数日志 |
| 502 | `google api error 400/401/403/404/429` | Google 拒绝了请求 | 见下表 |

| Google 错误码 | 常见原因 |
|---|---|
| 400 | refresh token 失效或被撤销（换 token 这一步就失败了），或者 `q` 语法有问题 |
| 401 / 403 | access token 无效，或 scope 不够 |
| 404 | `op=read` 的 `id` 不存在 |
| 429 | 调用太频繁，等一会再试 |

为了不泄露请求细节，接口不会回传 Google 的原始报错内容。

## 6. Gmail 搜索语法速查（`q` 参数）

| 写法 | 含义 |
|---|---|
| `in:sent` | 只搜已发送 |
| `from:me` / `to:xx@shopee.com` | 发件人 / 收件人 |
| `"Listing Optimisation"` | 精确短语（带引号） |
| `subject:weekly` | 主题里包含 |
| `newer_than:90d` / `older_than:7d` | 相对时间（d / m / y） |
| `after:2026/08/01 before:2026/11/10` | 绝对日期 |
| `has:attachment filename:pdf` | 带 PDF 附件 |
| `-subject:accepted` | 排除（前面加 `-`） |
| `A OR B` | 或（`OR` 必须大写） |

示例：过去 90 天里，我发出的、带 "Listing Optimisation"、而且不是日历回执的邮件：
```
in:sent "Listing Optimisation" newer_than:90d -subject:(accepted OR declined OR tentative)
```

## 7. 调用示例

### 7.1 curl

```bash
B=https://goo-g-assistant.vercel.app/api/gmail
AUTH="Authorization: Bearer $GMAIL_PROXY_TOKEN"

# 核对身份
curl -s -H "$AUTH" "$B?op=whoami"

# 搜索：含空格、引号、中文的 q 用 -G + --data-urlencode 自动编码
curl -s -G -H "$AUTH" "$B" \
  --data-urlencode "op=search" \
  --data-urlencode 'q=in:sent "Listing Optimisation" newer_than:90d' \
  --data-urlencode "max=20"

# 读单封
curl -s -H "$AUTH" "$B?op=read&id=19a6c2f0e1b2c3d4"
```

> zsh 提示：URL 里有 `?` 时**一定要加引号**，否则会报 `no matches found`。

### 7.2 Python（requests）

本机出网要走代理时，`requests` 会自动读取 `HTTPS_PROXY` 环境变量。

```python
import os
import requests

BASE = "https://goo-g-assistant.vercel.app/api/gmail"
HEADERS = {"Authorization": f"Bearer {os.environ['GMAIL_PROXY_TOKEN']}"}


def gmail(op: str, **params) -> dict:
    r = requests.get(BASE, headers=HEADERS, params={"op": op, **params}, timeout=70)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"{r.status_code} {data.get('error')}")
    return data


me = gmail("whoami")
assert me["email"] == "shuning.wang@shopee.com", me["email"]

res = gmail("search", q='in:sent "Listing Optimisation" newer_than:90d', max=20)
for m in res["messages"]:
    print(m["date"], "|", m["subject"])

if res["messages"]:
    full = gmail("read", id=res["messages"][0]["id"])["message"]
    print(full["body"][:500])
```

## 8. 限制与注意事项

| 项 | 说明 |
|---|---|
| 单次最多 50 封 | 想看更多，就把时间范围拆小，分几次搜 |
| 没有分页 | 目前不支持 `pageToken`，只返回最新的 N 封 |
| 速度 | search 会逐封拉取邮件头，50 封大约需要 5–15 秒 |
| 正文 | 只返回纯文本，最多 20,000 字符；不返回 HTML 和附件 |
| 账号 | 固定是 Vercel 上 refresh token 对应的账号（`shuning.wang@shopee.com`），不能切换 |

## 9. 安全与口令轮换

- 口令只放在本机环境变量里，**不要**写进代码、git、memory 或聊天记录。
- 怀疑口令泄露时，在 Vercel 上把 `GMAIL_PROXY_TOKEN` 改成新值，再 Redeploy，旧口令立刻失效。Google 那边的 token 不用动。
- 要彻底停用这个接口：删掉 Vercel 上的 `GMAIL_PROXY_TOKEN`，再 Redeploy。之后所有请求都会返回 401。
- 怀疑 Google refresh token 泄露：到 Google 账号 → 安全性 → 第三方应用访问权限，撤销授权，然后重新生成 token，更新到 Vercel。
