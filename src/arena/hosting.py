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

# 一次最多开多少个会话。防手滑（`--count 3000`），不是防坏人——开链接要管理口令。
MAX_MINT = 500

# 少于这么多份提交就只给事实、不给判读。3 个人的数据改题面，改的是噪声。
MIN_SUMMARY_SAMPLE = 4

# 组织者指定"这批链接是谁的"时的长度上限（落库列是 TEXT，这是显示与核对上的限制）。
MAX_USER_LEN = 40


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


def _check_user_id(user_id: str) -> str:
    """组织者可以显式指定"这批链接是谁的"，用于跨题累积掌握度与复习项。

    不校验格式的话，`--user "  "` 会造出一个空用户名，而 `users.display_name` 有 NOT NULL
    约束——报错会出现在落库那一刻，离出错的地方很远。这里先挡住空白与控制字符。
    """
    text = (user_id or "").strip()
    if not text:
        raise HostError("bad_user", "user_id 不能是空白", status=400)
    if len(text) > MAX_USER_LEN:
        raise HostError("bad_user", f"user_id 最长 {MAX_USER_LEN} 个字符", status=400)
    if any(ch.isspace() for ch in text):
        raise HostError("bad_user", "user_id 里不能有空格", status=400)
    return text


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

    def create(self, task_id: str, *, user_id: str | None = None) -> dict:
        """开一个会话，返回 `{token, task_id, user_id, max_calls, ...}`。

        默认 `user_id` 由 token 派生，**不收集姓名学号**：随机编号 + "本人拿编号找老师对号"
        就够了，隐私负担接近于零（这也是外部回件里"完全可以不收集"那条的具体落地）。

        给了 `user_id` 就让这批链接绑到同一个人。**这是跨题累积的前提**：掌握度、FSRS 卡、
        复习项都是以 `user_id` 为键的，一条链接一个人意味着"做三题 = 三个人"，
        复习队列永远是空的。
        """
        session = self.mint(task_id, 1, user_id=user_id)[0]
        self._warm(session["token"], task_id)
        return session

    def mint(self, task_id: str, count: int, *, now: str | None = None,
             user_id: str | None = None) -> list[dict]:
        """一次开 `count` 个会话，**只写库、不建 mock 实例**。

        为什么和 `create` 分开：办一场活动要一次发几十条链接，而 mock 实例是"用的时候才有
        意义"的东西（它还持有夹具的一份深拷贝）。离线批量发链接时谁也不会立刻做题，
        先建几十个实例纯属浪费；服务端第一次收到调用时会自己补建（见 `_service`）。
        """
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise HostError("bad_request", "count 必须是正整数", status=400)
        if count > MAX_MINT:
            raise HostError(
                "too_many", f"一次最多开 {MAX_MINT} 个会话；要更多请分批", status=400,
            )
        self.task(task_id)                              # 题不存在就一个都不建
        fixed_user = _check_user_id(user_id) if user_id is not None else None
        stamp = now or _now()
        sessions: list[dict] = []
        conn = self._conn_factory()
        try:
            with conn:
                for _ in range(count):
                    token = secrets.token_urlsafe(TOKEN_BYTES)
                    owner = fixed_user or f"u-{token[:12]}"
                    conn.execute(
                        "INSERT INTO sessions (token, task_id, user_id, max_calls, created_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (token, task_id, owner, self._max_calls, stamp),
                    )
                    sessions.append({"token": token, "task_id": task_id, "user_id": owner,
                                     "max_calls": self._max_calls, "created_at": stamp,
                                     "used": 0, "submission_id": None,
                                     "task_version": str(self.task(task_id).get("task_version") or "")})
        finally:
            conn.close()
        return sessions

    def _warm(self, token: str, task_id: str) -> None:
        """建实例并登记锁。只在 `create`（有人马上就要做）时调。

        **任务必须在拿锁之前解析**：`self.task()` 自己也要 `self._lock`，而
        `threading.Lock` 不可重入——在里面调就是死锁（真踩过：整个测试文件挂住 7 分钟）。
        """
        task = self.task(task_id)
        with self._lock:
            self._services.setdefault(token, MockToolService(task))
            self._locks.setdefault(token, threading.Lock())

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

        与 `_warm` 同一条纪律：**任务在拿锁之前解析**，`threading.Lock` 不可重入。
        """
        with self._lock:
            service = self._services.get(token)
        if service is not None:
            return service
        try:
            service = MockToolService(self.task(task_id))
        except MockConfigError as exc:
            raise HostError("task_broken", f"这道题的工具配置有问题：{exc}", status=500) from exc
        with self._lock:
            # 两个线程同时补建时留先到的那个；两边行为一致，覆盖也无害。
            self._services.setdefault(token, service)
            return self._services[token]

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


def roster(conn) -> list[dict]:
    """一次活动结束后要的那张表：编号、题、调了几次、分数、有没有交卷。

    **编号 = token 前 12 位**。学生手上只有整条链接，报给我们时也只会报一小段，
    所以表里直接给这一段——但完整 token 不在这张表里：它只出现在发链接那一份 CSV，
    那份是凭证，不该跟着成绩到处传。
    """
    rows = conn.execute(
        "SELECT s.token, s.task_id, s.user_id, s.created_at, s.submission_id, "
        "       (SELECT COUNT(*) FROM session_calls c WHERE c.token = s.token) AS calls, "
        "       sub.assert_score "
        "FROM sessions s LEFT JOIN submissions sub ON sub.submission_id = s.submission_id "
        "ORDER BY s.created_at, s.token"
    ).fetchall()
    return [{"code": row["token"][:12], "task_id": row["task_id"], "user_id": row["user_id"],
             "calls": int(row["calls"]), "ratio": row["assert_score"],
             "submitted": bool(row["submission_id"]),
             "submission_id": row["submission_id"], "created_at": row["created_at"]}
            for row in rows]


def summary(conn) -> dict:
    """把"谁卡在哪"聚成一张表——**助教真正要的那个视图**。

    分数只回答"这个人怎么样"，回答不了"这题怎么样"。一个问题如果 8/10 的人都没做到，
    先该怀疑题面说不清或判据过严，而不是这 8 个人都不行。这张表就是为这句话服务的。

    三条口径：
    1. **用 `effective_report`（当前生效）而不是落库那份**：重评过的提交若还按旧分数汇总，
       表上会出现"某人 0.5，但断言一列全过"这种自相矛盾的行。
    2. **"没做到"只算 `fail`**。`blocked` 是平台没判成（没交产物/没交过程），
       `unscored` 是这一条不适用于这份提交——把它们混进"没做到"，等于替学习者认错。
    3. **只看交集**：分母是"已交卷的人数"，不是会话总数。没交卷的人不是"没做到某条要求"，
       他们是没来。
    """
    from .diagnosis_report import load_pattern_labels

    rows = conn.execute(
        "SELECT submission_id FROM sessions WHERE submission_id IS NOT NULL"
    ).fetchall()
    labels = load_pattern_labels()

    per_assertion: dict[str, dict] = {}
    per_pattern: dict[str, int] = {}
    ratios: list[float] = []
    for row in rows:
        try:
            report = store.effective_report(conn, row["submission_id"])
        except store.StoreError:      # 会话指向的提交被删了：跳过这一行，不让整张表崩
            continue
        ratios.append(float((report.get("score") or {}).get("ratio") or 0.0))
        for item in report.get("assertions") or []:
            key = str(item.get("id") or "")
            if not key:
                continue
            bucket = per_assertion.setdefault(
                key, {"id": key, "title": str(item.get("title") or "这项检查"),
                      "fail": 0, "blocked": 0, "pass": 0, "unscored": 0},
            )
            state = str(item.get("state") or "")
            if state in bucket:
                bucket[state] += 1
            if state == "fail":
                for pattern in item.get("error_patterns") or []:
                    per_pattern[str(pattern)] = per_pattern.get(str(pattern), 0) + 1

    total = len(ratios)
    assertions = []
    for bucket in per_assertion.values():
        missed = bucket["fail"]
        assertions.append({**bucket, "share": (missed / total) if total else 0.0})
    # 先按"没做到的比例"排，再按人数：10 个人里 5 个没做到，比 2 个人里 1 个更值得看。
    assertions.sort(key=lambda a: (-a["share"], -a["fail"], a["id"]))

    patterns = [
        {"pattern_id": pid, "label": labels.get(pid, pid), "count": count}
        for pid, count in sorted(per_pattern.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    return {
        "sessions": int(conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]),
        "submitted": total,
        "avg_ratio": (sum(ratios) / total) if total else None,
        "assertions": assertions,
        "patterns": patterns,
    }


def _pad(text: str, width: int) -> str:
    """按**显示宽度**补空格：一个汉字占两格，`str.ljust` 只数代码点，中文表格会歪。"""
    import unicodedata

    used = sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)
    return text + " " * max(0, width - used)


def render_summary(data: dict) -> str:
    """把 `summary()` 的结果排成人能读的几段。**不带内部编号**：看的是"哪条要求"，不是 id。"""
    lines: list[str] = []
    submitted, sessions = data["submitted"], data["sessions"]
    if not submitted:
        return f"共 {sessions} 个会话，还没有人交卷——先让他们做题。"
    avg = data["avg_ratio"] or 0.0
    lines.append(f"共 {sessions} 个会话，{submitted} 份已交卷，平均分 {avg:.2f}。")
    lines.append("")

    rows = data["assertions"]
    width = max((sum(2 if "\u4e00" <= ch <= "\u9fff" else 1 for ch in r["title"])
                 for r in rows), default=8)
    lines.append(_pad("要求", width) + "  没做到   通过  没判成")
    for row in rows:
        share = f"{row['fail']}/{submitted}"
        lines.append(f"{_pad(row['title'], width)}  {share:>6}   {row['pass']:>4}   {row['blocked']:>4}")

    if data["patterns"]:
        lines.append("")
        lines.append("最常见的错法：")
        for item in data["patterns"][:5]:
            lines.append(f"  {item['label']}　{item['count']} 次")

    lines.append("")
    lines.extend(_summary_verdict(rows, submitted))
    return "\n".join(lines)


def render_progress(data: dict) -> str:
    """命令行版的「和上一次比」。与页面同一句话打头，下面逐条列。

    **不印断言 id**：助教看到的是"结论是否与判据一致"，不是 `R2_report_correct`。
    id 只在 `roster` / `summary` 的 CSV 里出现（那是给维护者对题面用的）。
    """
    lines = [data["headline"]]
    for label, key in (("这次改好了", "fixed"), ("还是没过", "still_failing"),
                       ("上次过了、这次没过", "broke")):
        rows = data.get(key) or []
        if rows:
            lines.append("")
            lines.append(f"{label}：")
            lines.extend(f"  - {row['title']}" for row in rows)

    patterns = data.get("patterns") or {}
    if patterns.get("cleared"):
        lines.append("")
        lines.append("上次犯的错法这次没再出现：" + "、".join(patterns["cleared"]))
    if patterns.get("persisted"):
        lines.append("两次都在犯的错法：" + "、".join(patterns["persisted"]))
    if patterns.get("new"):
        lines.append("这次新出现的错法：" + "、".join(patterns["new"]))
    return "\n".join(lines)


def _summary_verdict(rows: list[dict], submitted: int) -> list[str]:
    """判读提示。**样本不足时不下结论**——2 份提交里 1 个人没做到，什么都说明不了。

    这一步是这张表的意义所在：没有判读，它只会被当成一张排行榜，然后被拿去说
    "这届学生不行"。有这个门槛，才不会拿 3 个人的数据去改题面。
    """
    if submitted < MIN_SUMMARY_SAMPLE:
        return [f"样本只有 {submitted} 份（少于 {MIN_SUMMARY_SAMPLE} 份），"
                "这张表只能当线索：别据此改题面，等更多人来。"]
    worst = max(rows, key=lambda r: (r["share"], r["fail"]), default=None)
    if worst and worst["share"] >= 0.5:
        return [f"**先看这一条**：「{worst['title']}」{worst['fail']}/{submitted} 的人没做到。"
                "超过一半时，优先怀疑题面说得不够清楚、或者判据太严——不是这半数人都不行。"]
    if worst and worst["fail"] == 0:
        return ["所有要求都有人做到过。若分数普遍很高，检查这道题是不是没有区分度。"]
    return ["没有哪条要求被一半以上的人漏掉：失分是分散的，属于个人差异。"]


def main(argv: list[str] | None = None) -> int:
    """`python scripts/host.py mint|roster ...`。

    为什么放在本模块而不是独立脚本：`mint` 只是 `SessionRegistry.mint` 的一层壳，
    逻辑在类里、命令在外层，测试就能直接测类而不必起子进程。
    """
    import argparse
    import csv

    from . import progress                       # 同上：只在这个入口用得到
    from . import submission_api as api          # 函数内导入：本模块被它 import，顶层引入会成环

    parser = argparse.ArgumentParser(description="托管会话的运维入口（B-51）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--db", default="arena.db", help="SQLite 文件（不存在会自动建）")
        p.add_argument("--tasks", default=str(DEFAULT_CATALOG), help="任务目录")

    mint_p = sub.add_parser("mint", help="一次开 N 个会话并打印链接")
    common(mint_p)
    mint_p.add_argument("--task", required=True)
    mint_p.add_argument("--count", type=int, default=1)
    mint_p.add_argument("--base-url", default="http://127.0.0.1:8080",
                        help="学生能访问到的地址前缀（不要写 127.0.0.1，除非只在本机试）")
    mint_p.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS)
    mint_p.add_argument("--out", help="把 token/链接写成 CSV")
    mint_p.add_argument("--user", help="把这批链接绑到同一个人：跨题累积掌握度与复习项用。"
                                       "不给就一条链接一个人（各自独立，做完一题复习队列是空的）")

    roster_p = sub.add_parser("roster", help="导出名单与分数")
    common(roster_p)
    roster_p.add_argument("--out", help="写成 CSV（不带完整 token）")

    summary_p = sub.add_parser("summary", help="聚合：哪条要求最多人没做到")
    common(summary_p)
    summary_p.add_argument("--out", help="写成 CSV")

    progress_p = sub.add_parser("progress", help="这个人这道题：最近一次比上一次怎样")
    common(progress_p)
    progress_p.add_argument("--user", required=True)
    progress_p.add_argument("--task", required=True)

    args = parser.parse_args(argv)

    registry = api.TaskRegistry(args.tasks)
    api.bootstrap(args.db, registry)
    conn = store.connect(args.db)
    try:
        if args.cmd == "mint":
            sessions = SessionRegistry(args.tasks, conn_factory=lambda: store.connect(args.db),
                                       max_calls=args.max_calls).mint(
                args.task, args.count, user_id=getattr(args, "user", None))
            base = args.base_url.rstrip("/")
            rows = [(s["token"], f"{base}/s/{s['token']}", s["user_id"]) for s in sessions]
            for index, (token, url, _uid) in enumerate(rows, 1):
                print(f"{index:3d}  {token[:12]}  {url}")
            print(f"\n共 {len(rows)} 条。**这一份就是凭证**：谁拿到链接谁就是他，别发群里。")
            if args.out:
                with open(args.out, "w", encoding="utf-8-sig", newline="") as handle:
                    writer = csv.writer(handle)
                    writer.writerow(["序号", "编号", "链接", "内部用户号"])
                    for index, (token, url, uid) in enumerate(rows, 1):
                        writer.writerow([index, token[:12], url, uid])
                print(f"已写出 {args.out}（含完整链接，发完就删，不要进仓库）")
            return 0

        if args.cmd == "summary":
            data = summary(conn)
            print(render_summary(data))
            if args.out and data["submitted"]:
                with open(args.out, "w", encoding="utf-8-sig", newline="") as handle:
                    writer = csv.writer(handle)
                    # 内部 id 只进 CSV：它是给维护者对题面用的，不该出现在给人看的表里
                    writer.writerow(["断言id", "要求", "没做到", "通过", "没判成", "占比"])
                    for row in data["assertions"]:
                        writer.writerow([row["id"], row["title"], row["fail"], row["pass"],
                                         row["blocked"], f"{row['share']:.2f}"])
                print(f"\n已写出 {args.out}")
            return 0

        if args.cmd == "progress":
            data = progress.of(conn, args.user, args.task)
            if data is None:
                print(f"{args.user} 在 {args.task} 上还没有两次提交，没有可对比的。"
                      "（要拿到对比，得给他两条链接、做两遍。）")
                return 0
            print(f"{args.user}　{args.task}")
            print(f"上一次 {data['prev_at']}　这一次 {data['cur_at']}")
            print()
            print(render_progress(data))
            return 0

        rows = roster(conn)
        if not rows:
            print("这个库里还没有任何会话。先 mint。")
            return 0
        print(f"{'编号':<14}{'调用':>4}{'分数':>8}  交卷  题")
        for row in rows:
            score = "—" if row["ratio"] is None else f"{row['ratio']:.2f}"
            print(f"{row['code']:<14}{row['calls']:>4}{score:>8}  "
                  f"{'是' if row['submitted'] else '否':<4}{row['task_id']}")
        done = sum(1 for r in rows if r["submitted"])
        print(f"\n共 {len(rows)} 个会话，{done} 份已交卷。")
        if args.out:
            with open(args.out, "w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            print(f"已写出 {args.out}")
        return 0
    finally:
        conn.close()


def review_of(conn, user_id: str, *, at: str | None = None) -> dict | None:
    """这个人今天该复习什么。回答不了就返回 `None`（页面少一块），不编造。

    三段数据各归各家：复习项读 `review_items`、每日上限读 `users`、技能中文名读 `skills`。
    排序与截断**不在这里重写**，直接调 `reminder.build_reminder`（它内部走
    `scheduler.take_daily`）——两处各排一次，迟早出现"页面上今天有、明天又冒出来"。

    返回 `None` 的两种情况，都是"这块本来就没有"，不是出错：
    还没做过任何题的人没有 `users` 行；技能名缺失时 `reminder` 会拒绝显示英文 id，
    那也不该把整页拖垮。
    """
    from . import reminder

    try:
        cap = store.daily_review_cap(conn, user_id)
    except store.StoreError:
        return None
    items = store.list_review_items(conn, user_id, status="pending")
    names = {row["skill_id"]: row["name"]
             for row in conn.execute("SELECT skill_id, name FROM skills")}
    titles = {row["task_id"]: row["title"]
              for row in conn.execute("SELECT task_id, title FROM tasks")}
    try:
        return reminder.build_reminder(items, at=at or _now(), cap=cap,
                                       skill_names=names, task_titles=titles)
    except reminder.ReminderError as exc:
        sys.stderr.write(f"[复习项没渲染] {type(exc).__name__}: {exc}\n")
        return None


def _review_block(review: dict | None) -> str:
    """「今天该复习」那一块。没有可复习的就整块不出现——空盒子比没有更让人困惑。"""
    if not review or review.get("empty"):
        return ""
    rows = []
    for row in review.get("today") or []:
        extra = f"（{row['task_title']}）" if row.get("task_title") else ""
        rows.append(
            f"<li>{html.escape(str(row['skill']))}　{html.escape(str(row['kind_label']))}"
            f"{html.escape(extra)}　<span class=\"muted\">{html.escape(str(row['overdue_text']))}</span></li>"
        )
    notice = f'<div class="muted">{html.escape(str(review["notice"]))}</div>' if review.get("notice") else ""
    return (
        '<h2>今天该复习</h2><div class="card">'
        f'<div>{html.escape(str(review["headline"]))}</div>'
        f'<ul style="margin:8px 0 0; padding-left:20px">{"".join(rows)}</ul>{notice}'
        "</div>"
    )


def since_last_of(conn, submission_id: str) -> dict | None:
    """这一份比它上一份怎样（B-56）。回答不了就返回 `None`（页面少一块），不编造。

    三件"没有"都归到 `None`：这是他第一次做这道题；库里的上一份被删了；
    组合页面的那一步出了任何差错。**渲染一块对比不该让整页 500**——
    报告链接才是他要的东西，对比是附赠。
    """
    from . import progress

    try:
        return progress.of_submission(conn, submission_id)
    except Exception as exc:                       # noqa: BLE001 - 见上：附赠内容不配拖垮整页
        sys.stderr.write(f"[和上次比没渲染] {type(exc).__name__}: {exc}\n")
        return None


def _short_time(value: str | None) -> str:
    """`2026-11-24T09:12:00Z` → `2026-11-24 09:12`。认不出来就原样返回，不猜。"""
    text = str(value or "")
    if len(text) >= 16 and text[4] == "-" and text[10] in "T ":
        return f"{text[:10]} {text[11:16]}"
    return text


def _progress_block(since_last: dict | None) -> str:
    """「和上一次比」那一块。没有上一次就整块不出现。

    **不可比时只印那一句原因，不印两边各自的分数**：0.20 和 0.95 并排摆着，
    任何人都会自己相减，而这两次之间题目改过——白纸黑字的两个数字会推翻那句提醒。
    """
    if not since_last:
        return ""
    head = html.escape(str(since_last.get("headline") or ""))
    if not since_last.get("comparable"):
        return f'<h2>和上一次比</h2><div class="card">{head}</div>'

    groups = [
        ("这次改好了", [row["title"] for row in since_last.get("fixed") or []]),
        ("还是没过", [row["title"] for row in since_last.get("still_failing") or []]),
        ("上次过了、这次没过", [row["title"] for row in since_last.get("broke") or []]),
    ]
    rows = "".join(
        f'<li>{html.escape(label)}：{"、".join(html.escape(str(t)) for t in titles)}</li>'
        for label, titles in groups if titles
    )
    extra = []
    if since_last.get("prev_at"):
        extra.append("上一次交卷：" + _short_time(since_last["prev_at"]))
    cleared = since_last.get("patterns", {}).get("cleared") or []
    if cleared:
        extra.append("上次那几个错法这次没再出现：" + "、".join(cleared))
    if since_last.get("unjudged"):
        extra.append(f"另有 {len(since_last['unjudged'])} 条这次没判成（没交过程或产物），没有算进对比。")
    notes = "".join(f'<div class="muted">{html.escape(text)}</div>' for text in extra)
    return (
        '<h2>和上一次比</h2><div class="card">'
        f"<div>{head}</div>"
        + (f'<ul style="margin:8px 0 0; padding-left:20px">{rows}</ul>' if rows else "")
        + notes
        + "</div>"
    )


def render_landing(session: dict, public_task: dict, base_url: str, *,
                   review: dict | None = None, report_url: str | None = None,
                   since_last: dict | None = None) -> str:
    """学习者打开链接看到的那一页。

    **同一页要能回答四种状态**，否则回访的人只会看到一段过时的做题说明：
    | 状态 | 页面先给什么 |
    |---|---|
    | 还没做 | 贴给 AI 的那段话 + 工具清单 |
    | 已经交过卷 | 报告入口；做题地址收起来，并说明这条链接只能用一次 |
    | 交过不止一次 | 「和上一次比」：哪条改好了、哪条还没过——**这是他重做的全部回报** |
    | 有复习项 | 「今天该复习」放在报告入口下面（不管交没交） |

    `since_last` 由 `since_last_of()` 现算，锚在**这一份提交**上而不是"最近两次"
    （同一个人交了四次时，回看第二次那一页不能拿第三、四次来比）。

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

    submitted = bool(report_url)
    if submitted:
        # 回访的人：先给他"我那次的结果"，做题说明不该再占屏。
        head = (
            '<h2>你这次已经交过卷</h2><div class="card">'
            f'<a class="cta" href="{html.escape(str(report_url))}">打开我的报告</a>'
            '<div class="muted">报告里有：哪一步没做到、证据是什么、下一步练什么、下一道做什么题。</div>'
            "</div>"
            '<div class="card muted">这条链接已经用过了（同一份提交只判一次，不会因为再点而重判）。'
            "想再做一遍，找老师要一条新的链接。</div>"
        )
        subtitle = "你的做题链接（已交卷）"
    else:
        head = (
            '<h2>① 让 AI 助手对着这个地址做</h2><div class="card">'
            f"<pre>{html.escape(relay)}</pre>"
            f'<div class="muted">完整前缀：<code>{html.escape(root)}/tools/</code>　'
            f"已经调用 {session['used']} 次（上限 {session['max_calls']}）</div></div>"
            '<h2>② 这道题有哪些工具</h2><div class="card"><table>'
            "<tr><th>工具</th><th>参数</th><th>做什么</th></tr>"
            f"{''.join(tool_rows)}</table></div>"
            '<h2>③ 做完看报告</h2><div class="card">'
            f'<a class="cta" href="{html.escape(root)}/submit">看我这次的报告</a>'
            '<div class="muted">同一份提交只会判一次；再点开还是这一份。</div></div>'
        )
        subtitle = "做题地址（这条链接就是你，别转发）"

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
<div class="muted">{subtitle}</div>
{head}
{_progress_block(since_last)}
{_review_block(review)}
<h2>④ 说明</h2>
<div class="card muted">
你调工具的每一次请求都由服务器记下（包括失败的）。这不是监视，是判分的依据：
「失败后有没有重试」「参数有没有按题面给」只能从过程里看出来。
报告里不会出现你的姓名学号——这个链接就是你的编号。
</div>
</main></body></html>
"""
