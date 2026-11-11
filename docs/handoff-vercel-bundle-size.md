# Handoff：缩小 Vercel 部署体积（goo-g-assistant）

> 2026-11-11 · 交接人：Claude（GooG Assistant 会话） · 需求方：Shuning

## 1. 目标

Vercel 项目 `goo-g-assistant` 保留了 10 个部署，一共占 **3.3 GB**（占账号存储的 71%），平均每个部署约 **330 MB**。问题出在每个部署的包太大，而不是部署次数多。目标是在**不改变任何对外行为**的前提下，把单个部署压到 150 MB 以下。

## 2. 根因（已在本地实测）

| 事实 | 数据 |
|---|---|
| 仓库文件本身 | 约 2 MB，可以忽略 |
| Vercel Python 的打包方式 | `requirements.txt` 里的**全部依赖**会装进**每一个**函数包 |
| 函数数量 | 10 个：`auth`、`action_items`、`cron`、`trigger`、`view`、`seatalk_check`、`pdf_qa`、`gmail`、`gcal`、`drive` |
| 每个函数的依赖体积 | 解压后 129 MB / 压缩后 24.6 MB |
| 其中 Google 客户端库（`google-api-python-client` 及其依赖） | 解压后 **111 MB** / 压缩后 **18.7 MB**，占了 76% |
| 其中最大的一块 | `googleapiclient/discovery_cache`，99 MB，是所有 Google API 的离线描述文件，本项目只用到 Gmail 和 Calendar |
| 估算 | 10 个函数 × 24.6 MB ≈ 250 MB，加上运行时开销 ≈ 330 MB，和 Vercel 显示的一致 |

**真正用到 Google 客户端库的只有 `api/_briefing.py` 一个文件**（`cron` 和 `trigger` 会用到它）。另外 7 个函数完全不用这些库，却每个都带着 111 MB。

`api/gmail.py`、`api/gcal.py`、`api/drive.py` 已经通过 `api/_google_ro.py` 用标准库 `urllib` 直接调 REST 接口，没用客户端库，已经验证在线上可用，可以直接复用。

## 3. 方案和预计效果

| 步骤 | 做什么 | 每个部署 | 10 个部署合计 | 风险 |
|---|---|---|---|---|
| **第 1 步（主要）** | `_briefing.py` 改用 `_google_ro` 的 urllib 调用，从 `requirements.txt` 删掉 3 个 Google 包 | 330 → **约 140 MB（−57%）** | 3.3 → **约 1.4 GB** | 低：只改一个文件里 6 处调用 |
| 第 2 步（可选） | 合并函数，比如把 gmail/gcal/drive 合成一个 | 再减约 10–15 MB | 再减约 0.1 GB | 中：要改路由，收益很小，**不建议做** |
| 第 3 步 | 删掉旧部署，或者调短保留策略 | 不变 | 第 1 步的效果**立刻**生效，不用等 10 次新部署把旧的挤掉 | 低，但要 Shuning 本人在 Vercel 上操作 |

另一个附带收益：每个函数解压后的体积从 129 MB 降到 18 MB，冷启动会更快。

> 数字是在 macOS + Python 3.9 上用 `pip install --target` 测的。Vercel 用的是 Linux + Python 3.12，二进制包（比如 cryptography）大小会略有不同，但 discovery_cache 是纯 JSON，大小完全一样。部署后以 Vercel 上的实际数字为准。

## 4. 第 1 步的具体改法

### 4.1 要改的调用（都在 `api/_briefing.py`）

| 行号（约） | 现在的写法 | 改成 |
|---|---|---|
| 22–24 | `from google.auth…`、`from google.oauth2…`、`from googleapiclient.discovery import build` | 删掉，改为 `from _google_ro import access_token, google_get` |
| 46–56 | `google_creds()` 用 `Credentials` + `refresh` | 删掉。在 `run_briefing` 里调 `token, _ = access_token()` |
| 171–175 | `service.users().messages().attachments().get(userId="me", messageId=msg_id, id=att_id).execute()` | `google_get(token, f"{GMAIL}/messages/{msg_id}/attachments/{att_id}")` |
| 236 | `messages().list(userId="me", q=q, maxResults=50)` | `google_get(token, f"{GMAIL}/messages", {"q": q, "maxResults": 50})` |
| 242–247 | `messages().get(…, format="metadata", metadataHeaders=[…])` | `google_get(token, f"{GMAIL}/messages/{id}", {"format": "metadata", "metadataHeaders": [...]})`（`google_get` 用 `doseq=True` 编码，列表参数会正确展开） |
| 264–268 | `messages().get(…, format="full")` | `google_get(token, f"{GMAIL}/messages/{id}", {"format": "full"})` |
| 303–310 | `events().list(calendarId="primary", timeMin, timeMax, singleEvents=True, orderBy="startTime", maxResults=25)` | `google_get(token, f"{CAL}/calendars/primary/events", {…, "singleEvents": "true", …})` |
| 884–888 | `_run_pdf_qa` 里下载附件 | 同第 171 行的改法 |
| 926–938 | `run_briefing` 里的 `build("gmail"…)` / `build("calendar"…)` | 把 `token` 传给 `fetch_gmail` / `fetch_calendar` / `_run_pdf_qa`，替代原来的 service 对象 |

其中 `GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"`，`CAL = "https://www.googleapis.com/calendar/v3"`。

**注意事项：**
- 4 个函数的签名会从 `service` 改成 `token`：`fetch_gmail`、`fetch_calendar`、`_run_pdf_qa` 由 `run_briefing` 调用；`_extract_images` 由 `fetch_gmail` 调用（第 271 行）。其他地方没有调用（已 grep 确认）。
- `google_get` 默认超时 25 秒。附件下载如果之前没有超时限制，建议传 `timeout=45`。
- 原来的 `try/except` 吞掉异常、回退到摘要的逻辑要**原样保留**。
- 消息 ID、附件 ID 拼进 URL 前，用 `urllib.parse.quote(x, safe="")` 编码，和 `api/gmail.py` 的写法保持一致。
- 返回的 JSON 结构和客户端库完全一样（都是同一个 REST 接口），所以下游解析代码不用改。

### 4.2 `requirements.txt`

删掉这 3 行：
```
google-auth>=2.0.0
google-auth-oauthlib>=1.2.0
google-api-python-client>=2.100.0
```

`google-auth-oauthlib` 只有本地脚本 `vercel-daily-brief/setup_google_token.py` 在用（第 23 行有 import 提示）。在那个脚本的 docstring 里加一句：运行前先 `pip install google-auth-oauthlib`。

### 4.3 改完后确认没有残留

```bash
grep -rn "googleapiclient\|google\.oauth2\|google\.auth\b\|from google" api/
```
应该没有任何结果。`auth.py` 里有个函数叫 `_google_auth_url`，它用的是 urllib，不依赖这些库，不用管。

## 5. 验收

| # | 检查 | 方法 |
|---|---|---|
| 1 | 语法和导入 | `python3 -c "import ast; ast.parse(open('api/_briefing.py').read())"` |
| 2 | 单元测试 | 用 mock 替换 `google_get`，喂入真实结构的 Gmail/Calendar JSON，确认 `fetch_gmail` / `fetch_calendar` 的输出字段和改之前一样（可以参考 `api/gmail.py` 上线前的 mock 测试写法） |
| 3 | 其他函数没受影响 | 部署后：`/api/gmail?op=whoami`（带口令）返回 200；`/api/view` 不带 cookie 返回 302（跳转登录是正常行为） |
| 4 | **部署体积** | Vercel → Deployments → 新部署 → 看 Functions / Output 的大小，确认每个函数明显变小；告诉 Shuning 实际数字 |
| 5 | Daily brief 端到端 | **要先问 Shuning**：访问 `/api/trigger?token=…` 会真的发一封简报邮件（30 分钟内去重）。征得同意后再触发，确认邮件正常、网页 `/api/view` 能显示 |

## 6. 第 3 步：把旧部署占的空间立刻释放出来

旧部署会一直占着 330 MB，直到被新部署挤出保留数量，或者被删除。新部署上线并验证通过后：

1. Vercel → `goo-g-assistant` → Deployments，把第 1 步之前的旧部署删掉。**保留当前 Production 和上一个能用的版本**，方便回滚。
2. 或者在项目 Settings 里找 Deployment Retention，调短保留时间。

这一步要 Shuning 本人在 Vercel 网页上操作，agent 手上没有 Vercel 的权限，本机也没装 Vercel CLI。

## 7. 约束（必须遵守）

| 约束 | 说明 |
|---|---|
| 不改行为 | 只换调用方式。不改 prompt、优先级规则、邮件格式、发送逻辑 |
| 只读 | Google 侧只用 GET；不新增任何写操作 |
| 不碰密钥 | 不读取、打印、记录 `GOOGLE_*`、`GMAIL_PROXY_TOKEN`；token 交换只在 Vercel 服务端发生 |
| 提交 | 每次改动单独 commit，只 stage 改动的文件，不用 `git add -A`，不 amend，不跳过 hooks |
| Push | Vercel 由 GitHub `github` remote 的 `main` 自动部署（不是 `origin`，`origin` 是公司 GitLab）。**push 之前要先问 Shuning** |
| 本机 Python | 是 3.9，Vercel 是 3.12；新代码文件开头加 `from __future__ import annotations`，保证本地也能跑测试 |
| 别顺手修 | `send_email` 里的 `import sendgrid` 不在 `requirements.txt` 里，这是**已有问题**（只有配了 `SENDGRID_API_KEY` 才会触发）。不要顺手改，在汇报里提一句就行 |

## 8. 相关文件

| 文件 | 作用 |
|---|---|
| `api/_briefing.py` | 唯一要改的代码文件 |
| `api/_google_ro.py` | 要复用的 `access_token()`、`google_get()` |
| `api/gmail.py`、`api/gcal.py` | 同样的 REST 调用写法，可以参照 |
| `requirements.txt` | 删 3 个包 |
| `vercel-daily-brief/setup_google_token.py` | 加安装提示 |
| `vercel.json` | 函数列表；第 1 步不用改 |
| `docs/google-api.md` | 只读代理接口文档；第 1 步不用改 |

## 9. 汇报给 Shuning 的内容

完成后用表格汇报：改了哪些文件、测试结果、改前改后每个函数的体积和每个部署的体积（以 Vercel 显示的为准），以及第 6 节需要 Shuning 本人操作的步骤。
