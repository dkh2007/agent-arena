"""托管会话（B-51）：把练习环境从学生电脑搬到服务器，**过程记录不再经过学生的手**。

## 为什么需要它（不是"功能"，是补一个漏洞）

本地方式（B-46/B-47）里，过程是一份 `calls.jsonl`：服务端记账写文件，学生把文件发回来。
它挡住了"Agent 少写一次失败调用"，但挡不住**学生自己删行**。事后重放（B-49）只能证明
"留下的那些行是真的"，证明不了"没有别的行"。

具体一点，删哪儿会被发现、删哪儿不会：

| 删掉的行 | 重放能发现吗 | 为什么 |
|---|---|---|
| 那条故意失败、随后重试成功的调用 | **能** | 故障额度是"第 N 次命中算第 N 次尝试"，抽掉一行后重放时编号前移，后面的返回体对不上 |
| 一次参数乱加、被 400 拒掉的调用 | **不能** | 它不消耗故障额度，抽掉后其余调用逐个对得上 |

而"参数有没有乱加"正是判分的一项（R4）。所以本地方案对"删行"这件事只能做到部分检测。

## 它改了什么是根本性的

| | 本地（B-46） | 托管（本模块） |
|---|---|---|
| 过程存在哪 | 学生磁盘上的文本 | 服务器 SQLite，`session_calls` 表 |
| 谁数调用编号 | 服务端（但结果交给学生保管） | 服务端（学生看不到这张表） |
| 学生能不能删 | 能，部分能查出来 | **不能**，没有可删的东西 |

## 三个设计决定，以及对它们的代价

1. **一人一个 token，token 就是凭证**。`secrets.token_urlsafe(32)`（约 256 bit），
   猜不出来；放在路径里，学生点开就能用，不需要注册登录。代价：链接可以转发，
   等同于"让别人替我做"——对诊断工具风险很低（结果不进成绩时）。
2. **每个会话一个独立的 `MockToolService` 实例**。这是搬上服务器**最容易出事**的地方：
   `MockToolService` 的故障额度（"第 N 次命中失败"）存在实例上，若所有人共用一个实例，
   A 的调用次数会决定 B 的失败时机。本模块按 token 各建一个，状态互不可见。
3. **交卷即钉住 `submission_id`**。重复点提交返回同一份报告，不重判——否则一个人会有
   两份分数，而报告页也说不清是哪一份。

## 已知边界（不装作没有）

- **进程重启后，故障额度从头算**。已记录的调用一条不丢（在库里），但"第 1 次命中失败"
  会重新触发。结果是学生可能多收到一次 500，记录仍如实反映。不为它加持久化：
  一次性活动里重启是异常事件，而把额度也落库要引入第二份状态。
- **不做登录**。token 泄露 = 别人能看这份报告。结果进成绩/排名时必须换做法
  （老师发放并登记编号），这条写进了 BACKLOG 的触发条件。
- **不限制并发会话数**。限的是每人调用次数（`max_calls`），不是人数——几十人的规模
  靠 `ThreadingHTTPServer` + SQLite WAL 就够，加队列是给没发生的问题付成本。

## 与其它模块的分工

- 本模块**不含 HTTP**：`create` / `call` / `calls` / `grade` 都是普通函数，路由在
  `submission_api`（它已经持有 handler 与统一错误出口）。
- `grade` 不自己调判分，而是收一个 `submit_fn`——判分只有一处实现（`submission_api.submit`），
  反过来 import 会成环。
"""

from __future__ import annotations

import html
import json
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import store
from .diagnosis_report import ReportError, load_task
from .lab import DEFAULT_CATALOG, LabError, artifact_record, resolve_task_file
from .mock_tools import MockConfigError, MockToolService

# 32 字节 ≈ 43 个 URL 安全字符 ≈ 256 bit 熵。128 bit 就够用，这里不在乎多几十 bit。
TOKEN_BYTES = 32

# 一道题正常 4–8 次调用。留 120 是"手滑重试几十次也够"，同时给"死循环刷接口"一个上限。
DEFAULT_MAX_CALLS = 120

# token 形状：`token_urlsafe` 的字符集。先按形状挡掉垃圾请求，再查库——
# 这样"随便扫路径"的请求连一次 SQL 都不花。
_TOKEN_ALPHABET = set(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
)
MIN_TOKEN_LEN = 32
MAX_TOKEN_LEN = 64


class HostError(Exception):
    """托管层的失败。带 HTTP 状态码，路由层直接 `_send`，不再猜。"""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

    def as_body(self) -> dict:
        return {"error": {"code": self.code, "message": self.message}}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _looks_like_token(token: str) -> bool:
    return (isinstance(token, str)
            and MIN_TOKEN_LEN <= len(token) <= MAX_TOKEN_LEN
            and set(token) <= _TOKEN_ALPHABET)


class SessionRegistry:
    """会话的创建、调用记账与交卷。

    线程安全：`ThreadingHTTPServer` 每请求一个线程，而一个学习者也可能并行发两次请求
    （脚本里的并发拉取）。`call_index` 必须唯一且连续，所以同一 token 的记账串行化。
    **不同 token 之间不共享锁**——否则一个人慢请求会拖住全班。
    """

    def __init__(self, catalog: str | Path = DEFAULT_CATALOG, *, conn_factory,
                 max_calls: int = DEFAULT_MAX_CALLS):
        self._catalog = Path(catalog)
        self._conn_factory = conn_factory
        self._max_calls = int(max_calls)
        self._lock = threading.Lock()                 # 只保护两个 dict 本身
        self._services: dict[str, MockToolService] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._tasks: dict[str, dict] = {}

    # ── 任务 ────────────────────────────────────────────────────────────────

    def task(self, task_id: str) -> dict:
        """按 task_id 读任务（带缓存）。

        缓存的是**已经解析过的任务 dict**，不是文件路径——托盘里没有"重新读文件"这一步，
        活动期间换题就得重启服务。这是刻意的：中途换题会让同一批学生的判分标准不一致。
        """
        with self._lock:
            cached = self._tasks.get(task_id)
        if cached is not None:
            return cached
        try:
            task = load_task(resolve_task_file(task_id, self._catalog))
        # `LabError` 是"找不到这个 task_id"（按文件名与 task_id 都没命中），
        # 不接住它就会漏到 `_guard` 的兜底分支变成 500，而经理那边只看到"服务出错"。
        except (ReportError, LabError, OSError) as exc:
            raise HostError("unknown_task", f"没有这道题：{task_id}", status=404) from exc
        with self._lock:
            self._tasks[task_id] = task
        return task

    # ── 会话 ────────────────────────────────────────────────────────────────

    def create(self, task_id: str) -> dict:
        """开一个会话，返回 `{token, task_id, user_id, max_calls, ...}`。

        `user_id` 由 token 派生，**不收集姓名学号**：随机编号 + "本人拿编号找老师对号"
        就够了，隐私负担接近于零（这也是外部回件里"完全可以不收集"那条的具体落地）。
        """
        task = self.task(task_id)                      # 先确认题存在，别建半个会话
        token = secrets.token_urlsafe(TOKEN_BYTES)
        user_id = f"u-{token[:12]}"
        now = _now()
        conn = self._conn_factory()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO sessions (token, task_id, user_id, max_calls, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (token, task_id, user_id, self._max_calls, now),
                )
        finally:
            conn.close()
        with self._lock:
            self._services[token] = MockToolService(task)
            self._locks[token] = threading.Lock()
        return {"token": token, "task_id": task_id, "user_id": user_id,
                "max_calls": self._max_calls, "created_at": now, "used": 0,
                "submission_id": None,
                "task_version": str(task.get("task_version") or "")}

    def get(self, token: str) -> dict:
        """查会话。形状不对的 token 直接 404，不打库。"""
        if not _looks_like_token(token):
            raise HostError("unknown_session", "这个链接不完整或已失效", status=404)
        conn = self._conn_factory()
        try:
            row = conn.execute(
                "SELECT token, task_id, user_id, max_calls, created_at, submission_id "
                "FROM sessions WHERE token = ?", (token,),
            ).fetchone()
            if row is None:
                raise HostError("unknown_session", "这个链接不完整或已失效", status=404)
            used = conn.execute(
                "SELECT COUNT(*) FROM session_calls WHERE token = ?", (token,),
            ).fetchone()[0]
        finally:
            conn.close()
        session = dict(row)
        session["used"] = int(used)
        return session

    # ── 调用 ────────────────────────────────────────────────────────────────

    def call(self, token: str, name: str, params: dict) -> tuple[int, int, dict]:
        """代学习者调一次工具，返回 `(call_index, http_status, body)`。

        与 `lab.CallLog` 的关键差别：**记录不需要交给任何人**，所以没有"事后补一行"的可能。
        """
        session = self.get(token)                      # 404 在这里抛，下面就不用再判
        if not isinstance(params, dict):
            raise HostError("bad_params", "参数必须是 JSON 对象", status=400)
        if not isinstance(name, str) or not name:
            raise HostError("bad_params", "缺工具名", status=400)

        with self._lock:
            per_token = self._locks.setdefault(token, threading.Lock())
        with per_token:
            if session["used"] >= session["max_calls"]:
                raise HostError(
                    "session_exhausted",
                    f"这个会话已经调了 {session['used']} 次（上限 {session['max_calls']}）。"
                    "正常做这道题只需要几次；如果是脚本在死循环重试，先停下看看它为什么失败。",
                    status=429,
                )
            index = session["used"] + 1
            status, body = self._service(token, session["task_id"]).call(name, params)
            self._record(token, index, name, params, status, body)
        return index, status, body

    def _service(self, token: str, task_id: str) -> MockToolService:
        """取（或补建）这个会话自己的 mock 实例——**按 token 隔离故障额度**。

        进程重启后这里会补建一个：调用记录仍在库里，但"第几次命中失败"从 1 重新数。
        见模块文档"已知边界"。
        """
        with self._lock:
            service = self._services.get(token)
            if service is not None:
                return service
            try:
                service = MockToolService(self.task(task_id))
            except MockConfigError as exc:
                raise HostError("task_broken", f"这道题的工具配置有问题：{exc}", status=500) from exc
            self._services[token] = service
            return service

    def _record(self, token: str, index: int, name: str, params: dict,
                status: int, body: dict) -> None:
        """一行一次调用。**先落库再返回**：返回了却没记，等于给了学生一次不可追溯的调用。"""
        conn = self._conn_factory()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO session_calls "
                    "(token, call_index, name, params, http_status, body, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (token, index, name, json.dumps(params, ensure_ascii=False),
                     int(status), json.dumps(body, ensure_ascii=False), _now()),
                )
        finally:
            conn.close()

    def calls(self, token: str) -> tuple[list[dict], dict[str, str]]:
        """读回调用与产物，形状贴着提交契约（直接喂 `lab.build_submission`）。"""
        conn = self._conn_factory()
        try:
            rows = conn.execute(
                "SELECT call_index, name, params, http_status, body FROM session_calls "
                "WHERE token = ? ORDER BY call_index", (token,),
            ).fetchall()
        finally:
            conn.close()

        calls: list[dict] = []
        artifacts: dict[str, str] = {}
        for row in rows:
            params = json.loads(row["params"])
            calls.append({"call_index": row["call_index"], "name": row["name"],
                          "params": params, "http_status": row["http_status"],
                          "body": json.loads(row["body"])})
            record = artifact_record(row["name"], row["http_status"], params)
            if record is not None:
                path, content = record
                artifacts[path] = content
        return calls, artifacts

    # ── 交卷 ────────────────────────────────────────────────────────────────

    def grade(self, token: str, submit_fn) -> tuple[dict, bool]:
        """把会话里的调用交上去判分，返回 `(报告, 是否新判)`。

        `submit_fn(payload) -> report` 由路由层传进来：判分只有一处实现，
        本模块反过来 import `submission_api` 会成环。
        """
        from .lab import build_submission

        session = self.get(token)
        if session.get("submission_id"):
            # 已经交过：返回同一份。重判会造出"一个人两份分数"，而报告页说不清是哪一份。
            conn = self._conn_factory()
            try:
                report = store.effective_report(conn, session["submission_id"])
            finally:
                conn.close()
            return report, False

        calls, artifacts = self.calls(token)
        if not calls:
            raise HostError(
                "nothing_to_grade",
                "这个链接还没有任何调用记录。先让 AI 助手（或你的脚本）对着上面的地址做一遍题。",
                status=409,
            )
        task = self.task(session["task_id"])
        payload = build_submission(calls, artifacts, task, user_id=session["user_id"])
        report = submit_fn(payload)
        submission_id = str(report["submission"]["submission_id"])
        conn = self._conn_factory()
        try:
            with conn:
                conn.execute("UPDATE sessions SET submission_id = ? WHERE token = ?",
                             (submission_id, token))
        finally:
            conn.close()
        return report, True


def render_landing(session: dict, public_task: dict, base_url: str) -> str:
    """学习者打开链接看到的那一页：题面、他要贴给 AI 的地址、用了多少次、交卷入口。

    `public_task` 必须是**已经过白名单**的题面（`submission_api.public_task`），
    本函数只负责排版——白名单逻辑不在这里复制第二份。

    无脚本、无外链、手机 viewport：与报告页同一条约束，理由也一样（可审计、离线可开）。
    """
    token = session["token"]
    root = f"{base_url.rstrip('/')}/s/{token}"
    title = html.escape(str(public_task.get("title") or session["task_id"]))
    # 题面先保持原样：`relay` 整段会在模板里被 `html.escape` 一次。
    # 在这里先转一次会变成 `&amp;lt;`——学习者看到的是一串实体，而不是题面。
    scenario = " ".join(str(public_task.get("scenario") or "").split())

    tool_rows = []
    for tool in public_task.get("tools") or []:
        params = "、".join(
            f"{p.get('name')}" + ("（必填）" if p.get("required") else "")
            for p in tool.get("parameters") or []
        ) or "无参数"
        tool_rows.append(
            f"<tr><td><code>{html.escape(str(tool.get('name')))}</code></td>"
            f"<td>{html.escape(params)}</td>"
            f"<td>{html.escape(str(tool.get('description') or ''))}</td></tr>"
        )

    tools = public_task.get("tools") or []
    # 工具个数、产物路径都不写死：这道页面对全库 27 道题都用，写死"两个接口"在别的题上就是错的。
    # 该做什么由题面（scenario）说，这里只给地址与工具名。
    relay = (
        f"用下面这些工具完成这道题。地址前缀是 {root}/tools/，"
        f"每次调用用 POST，参数是 JSON，工具名接在前缀后面。\n\n"
        f"题面：{scenario}\n\n"
        f"工具：{', '.join(str(t.get('name')) for t in tools)}"
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
body {{ margin:0; padding:20px; font:16px/1.65 -apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
        color:#1f2937; background:#f8fafc; }}
main {{ max-width:720px; margin:0 auto; }}
h1 {{ font-size:22px; margin:0 0 6px; }}
h2 {{ font-size:16px; margin:26px 0 8px; color:#111827; }}
.card {{ background:#fff; border:1px solid #e5e7eb; border-radius:10px; padding:14px 16px; margin:10px 0; }}
code, pre {{ font-family:ui-monospace,Consolas,monospace; font-size:13px; }}
pre {{ background:#0f172a; color:#e2e8f0; padding:12px; border-radius:8px; overflow-x:auto; white-space:pre-wrap; }}
table {{ width:100%; border-collapse:collapse; font-size:14px; }}
th, td {{ text-align:left; padding:7px 6px; border-bottom:1px solid #eef2f7; vertical-align:top; }}
th {{ color:#6b7280; font-weight:600; }}
.muted {{ color:#6b7280; font-size:13px; }}
.cta {{ display:inline-block; margin-top:6px; padding:10px 16px; background:#1d4ed8; color:#fff;
        text-decoration:none; border-radius:8px; font-weight:600; }}
</style></head>
<body><main>
<h1>{title}</h1>
<div class="muted">做题地址（这条链接就是你，别转发）</div>

<h2>① 让 AI 助手对着这个地址做</h2>
<div class="card">
<pre>{html.escape(relay)}</pre>
<div class="muted">完整前缀：<code>{html.escape(root)}/tools/</code>　
已经调用 {session['used']} 次（上限 {session['max_calls']}）</div>
</div>

<h2>② 这道题有哪些工具</h2>
<div class="card"><table>
<tr><th>工具</th><th>参数</th><th>做什么</th></tr>
{''.join(tool_rows)}
</table></div>

<h2>③ 做完看报告</h2>
<div class="card">
<a class="cta" href="{html.escape(root)}/submit">看我这次的报告</a>
<div class="muted">同一份提交只会判一次；再点开还是这一份。</div>
</div>

<h2>④ 说明</h2>
<div class="card muted">
你调工具的每一次请求都由服务器记下（包括失败的）。这不是监视，是判分的依据：
「失败后有没有重试」「参数有没有按题面给」只能从过程里看出来。
报告里不会出现你的姓名学号——这个链接就是你的编号。
</div>
</main></body></html>
"""
