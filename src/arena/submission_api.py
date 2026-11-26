"""M1 / B-02：黑盒提交服务（HTTP）。

## 职责边界

本模块只做**协议层**：HTTP 请求 -> 内部链路 -> 报告原样返回。
判定语义、分数、归因一条都不写在这里，全部在
`trace_pipeline` / `assertion_evaluator` / `diagnosis_report` 里。
把协议与判定分开，是为了将来换 web 框架（或让 Android 端直连）时，
不必重跑一遍判定逻辑、也不必重新论证一遍分数口径。

## 为什么用标准库 `http.server`

`requirements.txt` 每个版本都 pin 死，`tests/test_dependencies.py` 会比对本机
**实际安装**的版本；引入框架要连带核 license、记入计划书开源边界表。
当前只有 3 个端点、没有并发压测需求，标准库够用。真要上生产只需替换
`make_server()` 这一层，`prepare()` / `evaluate()` 不动。

## 接口

| 方法 | 路径 | 作用 |
|---|---|---|
| POST | `/submissions` | 提交一份结果，返回诊断报告（DTO v2） |
| GET | `/submissions/{id}` | 读回**当前生效**的报告（被重评过就是最新那次） |
| GET | `/submissions/{id}?view=original` | 读回**当时按原判据判的**那份，逐字节同落库时 |
| GET | `/submissions/{id}/reappraisals` | 重评链（空数组 = 从没重评过；提交不存在则是 404） |
| GET | `/healthz` | 存活探针 + 已注册任务清单 |

**为什么默认给生效版**：这个端点的使用者是学习者与前端，"我现在得多少分"是它们要的答案。
库里已经有了重评结论、P 与复习排期都按新结论算了，接口还回旧的那份，就是"改了数据层、
忘了读接口"——round-92 遗留的一致性缺陷，本轮修掉。

**为什么 `view=original` 不是可选键**：报告体必须能原样过 `diagnosis-report.schema.json`
（`additionalProperties: false`）。把重评信息塞进报告体，schema 就再也验不过；
因此"当时那份"用查询参数选，重评元信息用独立子资源给，三个端点的返回值**各自只表示一件事**。

**`view` 只认 `effective` / `original`，写错就 400**：静默回落到默认值会让
"参数名记错了"变成本以为拿到原件、实际拿到新件，而两份都能过 schema、长得几乎一样。

POST 请求体（只有前四个必填）：

```json
{
  "task_id": "dbg-001-order-report",
  "task_version": "2026-11-13.1",
  "user_id": "u-001",
  "agent_id": "my-agent",
  "submission_id": "可选，缺省由平台生成",
  "agent_endpoint_url": "http://127.0.0.1:9000/run",
  "evaluated_at": "2026-11-13T12:00:00Z",
  "invoke": {"duration_ms": 21400, "timeout_seconds": 60, "platform_retries": 0},
  "trace": {"final_status": "completed", "tool_calls": []},
  "artifacts": {"/workspace/report.json": "{\"valid_count\": 17}"},
  "process_note": "可选：一段思路说明"
}
```

## 三条与判据直接相关的决定

1. **`task_version` 必填**（BACKLOG B-21，收紧 TD-038 的宽松口）。
   缺了直接 400 `task_version_missing`，**不产生 Submission**。
   但要分清：给了却**不一致**不是 400 —— 那是「这次不算数，但不是你不会」，
   由 `_task_version_mismatch()` 走 `voided` 报告（TD-038 的第三种处置）。
   **缺字段 = 客户端有问题（拒绝），版本不一致 = 一次合法但需重交的提交（出报告）。**
2. **端点不可达不是 5xx。** 探测到连不上时返回 200 + 一份**全部 `unscored`**
   的报告，`reason_code=tool_no_response`。理由：决策 7「数据缺失 ≠ 能力不足」——
   学习者可能完全无辜，他要看到「不是你的问题」，而不是一个读不懂的错误码。
   注意这里没有用 `blocked`：`blocked` 的语义是「前置断言已失败、无法判定」
   （`_assertion_item()` 强制要求存在真失败的前置），端点不可达时没有任何断言
   失败过，套 `blocked` 会被 builder 直接拒绝。两者分数贡献同为 0、同样不计
   掌握度，区别只在原因归属。
3. **`submission_id` 缺省由平台生成**，也允许外部带入（重放与对拍需要）。
   带入且重复时 409 且**不覆盖**——诊断记录要能回溯到「当时那份」。

## judge 双评（B-04b）：默认关闭，只能从 Python 层显式打开

HTTP 端点**不跑 judge**：它花钱、依赖网络，而且模型不可用会把提交挡在门外——
给学习者一个 503 只因为复核员掉线，是把平台成本转嫁成对方的失败。
需要双评的调用方走 `submit(..., judge=..., arbitration_threshold=...)`，
两个参数必须同时给（阈值没有默认值，见 `llm_judge.needs_arbitration`）。

开起来之后发生三件事，且只有这三件：报告多两个只读字段（`judge_scores` /
`judge_agreement`）、`submissions` 的两个同名派生列被填、一致率低于阈值时
`arbitration_queue` 多一条人工待办。**分数、断言、归因、错误模式一个字都不变。**

## 已知边界（是登记过的决定，不是遗漏）

- **无鉴权**，默认只绑 `127.0.0.1`。上公网前必须加，见 BACKLOG。
- **端点探测只做 TCP 连接**：不发业务请求、不采集 trace。发请求会带来副作用
  （被测 Agent 真的开始干活），污染评测。真正的黑盒调用与 OTel 采集是 B-03。
- **端点可达但没带证据 → 501**：本服务尚不具备主动采集能力，如实返回
  `endpoint_capture_not_implemented`，而不是假装评了一遍。
- **无排队 / 限流 / 重试**：单机演示用。平台重试会污染 R3（重试是学习者的被测能力）。

## 只读端点（B-50）

| 路径 | 给谁 | 说明 |
|---|---|---|
| `GET /tasks` | 前端选题页 | **白名单**：只有题面/工具签名/难度，`rubric_*`、`expected`、`fault_injection`、`tools[].mock` 一律不发 |
| `GET /tasks/{id}` | 做题的人 | 同上，单道；未知 id → 404 `unknown_task` |
| `GET /submissions/{id}/report.html` | 学习者本人 | 直接返回可读的 HTML（复用 `report_html`），浏览器打开即可，不用先存文件 |

这三条都是纯读：不落库、不改任何判定。`/tasks` 用**白名单**而不是黑名单——
任务 YAML 里有答案，黑名单漏一个字段就是一次泄露。

用法：
    python src/arena/submission_api.py --db demo.db --port 8080
    curl -X POST http://127.0.0.1:8080/submissions -H "Content-Type: application/json" \\
         -d @request.json
"""

from __future__ import annotations

import argparse
import json
import secrets
import socket
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

try:  # 作为包被 import
    from . import assertion_evaluator as ae
    from . import diagnosis_report as dr
    from . import judge_pipeline as jp
    from . import hosting
    from . import llm_judge as lj
    from . import report_html
    from . import store
    from . import trace_pipeline as tp
except ImportError:  # 作为独立脚本运行
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import assertion_evaluator as ae  # type: ignore[no-redef]
    import diagnosis_report as dr  # type: ignore[no-redef]
    import hosting  # type: ignore[no-redef]
    import report_html  # type: ignore[no-redef]
    import judge_pipeline as jp  # type: ignore[no-redef]
    import llm_judge as lj  # type: ignore[no-redef]
    import store  # type: ignore[no-redef]
    import trace_pipeline as tp  # type: ignore[no-redef]

ROOT = Path(__file__).resolve().parents[2]
TASKS_DIR = ROOT / "tasks"
MAX_BODY_BYTES = 8 * 1024 * 1024  # 8 MiB：一份 trace 远小于此，超了就是有人在打我们
PROBE_TIMEOUT = 1.5
UNREACHABLE_REASON = "agent_endpoint_unreachable"


class SubmissionError(Exception):
    """可以翻译成 HTTP 响应的错误。**未捕获的异常一律视为内部缺陷**，不许冒充业务错误。"""

    def __init__(self, code: str, message: str, *, status: int = 400,
                 details: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}

    def as_body(self) -> dict:
        body: dict[str, Any] = {"error": {"code": self.code, "message": self.message}}
        if self.details:
            body["error"]["details"] = self.details
        return body


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


_VIEWS = ("effective", "original")


def _parse_view(query: str) -> str:
    """解析 `?view=`。缺省 `effective`；**写了不认识的取值直接 400，不回落**。

    回落的代价不对等：默认值和 `original` 返回的报告**都能过同一份 schema**，
    长得几乎一样，只有几个数字不同。参数名记错（`?v=original`、`?view=raw`）
    若静默给默认值，人会把"新结论"当"当时那份"拿去对账，且没有任何信号提示他看错了。
    """
    if not query:
        return "effective"
    got = parse_qs(query, keep_blank_values=True).get("view")
    if got is None:
        return "effective"
    if len(got) > 1:
        raise SubmissionError("bad_request", f"view 只能给一个（收到 {got!r}）")
    if got[0] not in _VIEWS:
        raise SubmissionError("bad_request",
                              f"view 只接受 {'/'.join(_VIEWS)}（收到 {got[0]!r}）")
    return got[0]


def repo_head() -> str:
    """当前仓库提交短哈希，作为报告的 `task_git_hash`（提交时锁定的任务版本）。

    取不到就给 `unknown` 而不是编一个：编一个会让「这份分是哪版任务算的」永久失真。
    """
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


# ── 任务注册表 ────────────────────────────────────────────────────────────────

class TaskRegistry:
    """按 `task_id` 索引 `tasks/*.yaml`。

    构造时一次读盘（演示期单进程够用），`reload()` 供测试与开发期热更新。
    读不到 `task_id` 直接抛错——静默用文件名兜底会让注册表和报告里的 id 对不上。
    """

    def __init__(self, tasks_dir: str | Path = TASKS_DIR) -> None:
        self.tasks_dir = Path(tasks_dir)
        self._tasks: dict[str, dict] = {}
        self._paths: dict[str, Path] = {}
        self.reload()

    def reload(self) -> None:
        loaded: dict[str, dict] = {}
        paths: dict[str, Path] = {}
        for p in sorted(self.tasks_dir.glob("*.yaml")):
            task = dr.load_task(p)
            tid = task.get("task_id")
            if not isinstance(tid, str) or not tid.strip():
                raise SubmissionError(
                    "invalid_task_definition", f"任务文件 {p.name} 缺 task_id",
                    status=500, details={"file": p.name},
                )
            loaded[tid] = task
            paths[tid] = p
        self._tasks, self._paths = loaded, paths

    def get(self, task_id: str) -> dict:
        if task_id not in self._tasks:
            raise SubmissionError(
                "unknown_task", f"没有这个任务：{task_id}",
                status=404, details={"known_tasks": sorted(self._tasks)},
            )
        return self._tasks[task_id]

    def path_of(self, task_id: str) -> Path:
        self.get(task_id)
        return self._paths[task_id]

    def ids(self) -> list[str]:
        return sorted(self._tasks)

    def items(self) -> list[tuple[str, dict]]:
        return [(tid, self._tasks[tid]) for tid in sorted(self._tasks)]


# ── 对外可发的任务形状（白名单，不是黑名单） ─────────────────────────────────

def _empty_session_page(token: str, message: str) -> str:
    """空会话点"交卷"时给一页人话，而不是一坨 JSON。

    这一步是浏览器里点的，返回 JSON 等于让人对着 `{"error": ...}` 猜。
    """
    import html as _html

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>还没有记录</title>
<style>body{{margin:0;padding:24px;font:16px/1.7 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
color:#1f2937;background:#f8fafc;}}main{{max-width:560px;margin:0 auto;}}
.card{{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:16px;}}
a{{color:#1d4ed8;}}</style></head>
<body><main><div class="card">
<h2 style="margin-top:0">这个链接还没有任何调用记录</h2>
<p>{_html.escape(message)}</p>
<p><a href="/s/{_html.escape(token)}">回到做题那一页</a></p>
</div></main></body></html>
"""


def public_task(task: dict, catalog: list[tuple[str, dict]] | None = None) -> dict:
    """把任务裁成"学习者该看见的那部分"。

    **必须是白名单**：任务 YAML 里有 `rubric_gate` / `rubric_credit` / `scoring` /
    `test_inputs.expected` / `fault_injection`——那是答案与故障位。黑名单漏一个字段就是一次泄露，
    白名单漏一个字段只是少显示一行。

    `tools[].mock` 也裁掉：它写着数据从哪来、分页怎么切，属于实现细节，学习者照题面调就行。
    """
    meta = task.get("variant_meta") or {}
    family = meta.get("family_id")
    levels_total = 0
    if family and catalog:
        from . import training

        try:
            levels_total = len(training.levels_in_family(family, [t for _, t in catalog]))
        except Exception:  # 族信息不全就不显示"共几档"，不影响别的字段
            levels_total = 0

    tools = []
    for tool in task.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        params = []
        for param in tool.get("parameters") or []:
            if not isinstance(param, dict):
                continue
            item = {k: param[k] for k in ("name", "type", "required", "description",
                                          "minimum", "maximum") if k in param}
            params.append(item)
        tools.append({
            "name": tool.get("name"),
            "description": tool.get("description"),
            "parameters": params,
        })

    return {
        "task_id": task.get("task_id"),
        "task_version": task.get("task_version"),
        "title": task.get("title"),
        "domain": task.get("domain"),
        "scenario": task.get("scenario"),
        "primary_skill_id": task.get("primary_skill_id"),
        "secondary_skill_ids": list(task.get("secondary_skill_ids") or []),
        "difficulty": meta.get("difficulty"),
        "family_id": family,
        "levels_total": levels_total,
        "tools": tools,
    }


# ── 端点探测 ──────────────────────────────────────────────────────────────────

def probe_endpoint(url: str, timeout: float = PROBE_TIMEOUT) -> tuple[bool, str]:
    """只做 TCP 连接探测，**不发送任何业务请求**。

    回答的是「这个地址现在有没有人在听」，不回答「它答得对不对」。
    发 HTTP 请求会让被测 Agent 真的开始干活，等于平白多跑一次，污染 R3。
    返回 `(可达?, 原因码)`；原因码进报告 note，必须是可公开的归类，
    不能把 OSError 原文（可能含本机路径）塞进给学习者看的报告。
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return False, "unsupported_scheme"
    host = parts.hostname
    if not host:
        return False, "no_host"
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, "reachable"
    except socket.gaierror:
        return False, "dns_failure"
    except ConnectionRefusedError:
        return False, "connection_refused"
    except (TimeoutError, socket.timeout):
        return False, "timeout"
    except OSError:
        return False, "network_error"


# ── 请求归一化 ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Prepared:
    """一份已经通过校验、可以进判定链路的提交。字段与来源一一对应，不含派生值。"""

    task: dict
    submission: dict
    user_id: str
    trace: dict | None
    artifacts: dict | None
    process_note: str | None
    endpoint_url: str | None


def _str_field(payload: dict, key: str, *, required: bool = True,
               max_len: int = 200, missing_code: str | None = None) -> str | None:
    """取一个非空字符串字段。`missing_code` 让「缺这个字段」可以说得更具体。

    B-21 要求「缺 task_version 返回明确错误码」：通用 `missing_field` 只能告诉
    客户端「你少了个字段」，而 `task_version_missing` 能直接说「你的客户端版本
    太老，升级后再交」——这是两种不同的修法。
    """
    value = payload.get(key)
    if value is None:
        if required:
            raise SubmissionError(
                missing_code or "missing_field", f"缺必填字段 {key}", details={"field": key}
            )
        return None
    if not isinstance(value, str) or not value.strip():
        raise SubmissionError(
            "invalid_field", f"{key} 必须是非空字符串",
            details={"field": key, "got": type(value).__name__},
        )
    if len(value) > max_len:
        raise SubmissionError(
            "invalid_field", f"{key} 超过 {max_len} 字符", details={"field": key}
        )
    return value.strip()


def prepare(payload: Any, registry: TaskRegistry, *, now: str | None = None) -> Prepared:
    """把外部请求体校验并归一化成 `Prepared`。**这里只做校验，不做判定。**

    字段出现的顺序就是错误码的优先级：先确认「说的是哪个任务」，再确认
    「哪个版本」，最后才是「谁交的」。这样客户端拿到第一个错误时，
    修完就能往下走一步，不会修了 user_id 才发现任务名也错了。
    """
    if not isinstance(payload, dict):
        raise SubmissionError("invalid_json", "请求体必须是 JSON 对象")

    task_id = _str_field(payload, "task_id")
    # B-21：task_version 必填，且给专用错误码（通用 missing_field 说的不是同一件事）。
    task_version = _str_field(payload, "task_version", max_len=64,
                              missing_code="task_version_missing")
    user_id = _str_field(payload, "user_id")
    agent_id = _str_field(payload, "agent_id")

    task = registry.get(task_id)

    trace = payload.get("trace")
    if trace is not None and not isinstance(trace, dict):
        raise SubmissionError(
            "invalid_field", "trace 必须是对象或 null",
            details={"field": "trace", "got": type(trace).__name__},
        )
    artifacts = payload.get("artifacts")
    if artifacts is not None and not isinstance(artifacts, dict):
        raise SubmissionError(
            "invalid_field", "artifacts 必须是对象",
            details={"field": "artifacts", "got": type(artifacts).__name__},
        )
    process_note = payload.get("process_note")
    if process_note is not None and not isinstance(process_note, str):
        raise SubmissionError(
            "invalid_field", "process_note 必须是字符串",
            details={"field": "process_note", "got": type(process_note).__name__},
        )

    submission_id = _str_field(payload, "submission_id", required=False, max_len=128)
    if submission_id is None:
        submission_id = f"sub-{uuid.uuid4().hex[:12]}"

    evaluated_at = _str_field(payload, "evaluated_at", required=False, max_len=64)
    git_hash = _str_field(payload, "task_git_hash", required=False, max_len=64) or repo_head()

    submission: dict[str, Any] = {
        "submission_id": submission_id,
        "task_id": task_id,
        "task_git_hash": git_hash,
        "task_version": task_version,  # 与 task["task_version"] 不一致 -> voided，不是 400
        "agent_id": agent_id,
        "evaluated_at": evaluated_at or now or _utcnow(),
    }
    invoke = payload.get("invoke")
    if invoke is not None:
        if not isinstance(invoke, dict):
            raise SubmissionError(
                "invalid_field", "invoke 必须是对象",
                details={"field": "invoke", "got": type(invoke).__name__},
            )
        submission["invoke"] = invoke

    endpoint_url = _str_field(payload, "agent_endpoint_url", required=False, max_len=500)

    return Prepared(
        task=task, submission=submission, user_id=user_id,
        trace=trace, artifacts=artifacts, process_note=process_note,
        endpoint_url=endpoint_url,
    )


# ── 未评测报告 ────────────────────────────────────────────────────────────────

def unreachable_report(prep: Prepared, detail: str) -> dict:
    """平台没取到任何证据时出的报告：**全部 `unscored`**，不是 `blocked`。

    为什么不复用 `recompute_all()`：那条路会真的去判 R1「文件在不在」——
    平台连不上端点时产物压根没到手，判 fail 等于把平台故障记成学习者失败。
    所以这里直接给出「无法判定」的结论，并让 `build_report()` 负责措辞与归因。
    """
    gate, credit = dr.rubric_layers(prep.task)
    raw = {
        "submission": prep.submission,
        "trace": None,
        "trace_audit": {"reported": False, "schema_valid": False,
                        "invalid_reasons": ["not_reported"], "call_count": 0},
        "artifacts": {},
        "assertion_results": [
            {"id": spec["id"], "state": "unscored", "reason_code": "tool_no_response"}
            for spec in gate + credit
        ],
    }
    report = dr.build_report(prep.task, raw, unreachable_reason=f"{UNREACHABLE_REASON}:{detail}")
    dr.validate_report(report)
    return report


# ── 评测（不落库） ────────────────────────────────────────────────────────────

def _evaluate_with_raw(prep: Prepared) -> tuple[dict, dict | None]:
    """内部主链路，顺带交出交给 judge 的 `raw`。**纯函数**（不写盘、不发网络包）。

    分成这个私有函数是为了让 B-04b 的双评拿到 trace 与产物原文：
    只拿报告喂 judge 等于把 `expected`（标准答案）递过去，那样的"一致"证明不了任何事
    （见 `llm_judge.build_judge_context` 的白名单说明）。公开的 `evaluate()` 只返回报告，
    签名不变——现有调用方与测试一条都不用改。
    """
    if prep.endpoint_url is not None:
        reachable, detail = probe_endpoint(prep.endpoint_url)
        if not reachable:
            return unreachable_report(prep, detail), None
        if prep.trace is None and not prep.artifacts:
            # 端点活着，但本服务还没有主动采集能力（B-03）。如实说，不假装评过。
            raise SubmissionError(
                "endpoint_capture_not_implemented",
                "Agent 端点可达，但本服务尚未实现主动调用与 trace 采集（B-03）；"
                "请直接在请求体里提交 trace / artifacts",
                status=501,
            )

    try:
        raw = tp.assemble_raw(
            prep.submission, prep.task, trace=prep.trace,
            artifacts=prep.artifacts, process_note=prep.process_note,
        )
        built = tp.recompute_all(raw, task=prep.task)
        report = dr.build_report(prep.task, built)
        dr.validate_report(report)
    except (tp.PipelineError, dr.ReportError, ae.EvaluatorError) as exc:
        # 输入能解析但评不了（trace 结构非法、rubric 对不上等）——422，不是 500
        raise SubmissionError("unevaluable", str(exc), status=422) from exc
    return report, raw


def evaluate(prep: Prepared) -> dict:
    """走内部主链路出报告（不跑 judge）。"""
    return _evaluate_with_raw(prep)[0]


def submit(payload: Any, *, registry: TaskRegistry, conn_factory, now: str | None = None,
           judge: lj.JudgeFn | None = None,
           arbitration_threshold: float | None = None,
           judge_runs: int = lj.DEFAULT_RUNS) -> dict:
    """校验 -> 评测 ->（可选）双评 -> 落库 ->（可能）入仲裁队列。返回报告。

    `conn_factory` 是「产出连接的可调用对象」而不是连接本身：`ThreadingHTTPServer`
    每请求一个线程，而 sqlite3 连接默认不允许跨线程使用。每请求新建连接最省心，
    本地文件打开成本是微秒级，不值得为它引入连接池与锁。

    **judge 默认不跑**（`judge=None`）：它花钱、要网络，且模型不可用时会挡住提交。
    要开启必须同时给出 `judge` 与 `arbitration_threshold`——只给一个属于部署配置错误，
    返回 500 而不是拿一个默认阈值顶上（`llm_judge` 拒绝给默认值的同一条理由）。
    """
    if (judge is None) != (arbitration_threshold is None):
        raise SubmissionError(
            "judge_config_incomplete",
            "judge 与 arbitration_threshold 必须同时给或同时不给；"
            "单独给一个，运行时就只能替你拍一个阈值",
            status=500,
        )

    prep = prepare(payload, registry, now=now)
    report, raw = _evaluate_with_raw(prep)

    outcome = None
    if judge is not None and raw is not None:
        # raw 为 None = 平台没拿到任何证据（端点不可达）。没材料就没有可复核的东西，
        # 硬跑双评只会让模型对着一份空壳打分，把"平台没采到"记成"考生不行"。
        try:
            outcome = jp.run_judge(
                report, raw, prep.task, judge,
                threshold=arbitration_threshold, runs=judge_runs,
                vocab=dr.load_vocabulary(),
            )
        except lj.JudgeError as exc:
            # 模型/接口不可用是平台侧故障，不是这份提交的错：503，且**不落库**。
            # 落了库就等于存了一份"评过但没评完"的证据，后续无法区分。
            raise SubmissionError("judge_unavailable", str(exc), status=503) from exc
        report = outcome.report
        dr.validate_report(report)

    conn = conn_factory()
    try:
        # 三步写成一笔（round-93 / B-07d ③）：用户、提交（含 P 快照刷新）、仲裁入队
        # 要么全成、要么全不成。以前各写各的，中断会留下"有提交、没成绩"或
        # "有提交、该排队却没排"的半状态——两者都不会被任何上游断言发现。
        with store.atomic(conn):
            store.ensure_user(conn, prep.user_id)
            try:
                store.save_submission(
                    conn, report, user_id=prep.user_id,
                    trace=prep.trace, artifacts=prep.artifacts,
                    agent_endpoint_url=prep.endpoint_url,
                    judge_scores=report.get("judge_scores"),
                    judge_agreement=report.get("judge_agreement"),
                )
            except store.StoreError as exc:
                # 重复 submission_id 走这里：409 + 不覆盖，诊断记录要能回溯到「当时那份」。
                # **就地转换再上抛**，而不是放到外层 except：外层还包含入队那一步，
                # 入队失败是平台侧故障（500 兜底），冒充成 409 "store_rejected"
                # 会让调用方去改一个本来没问题的请求体。
                raise SubmissionError("store_rejected", str(exc), status=409) from exc
            if outcome is not None:
                # 必须在落库之后：队列外键指向 submissions，先入队会指向不存在的证据。
                jp.record_arbitration(conn, outcome)
    finally:
        conn.close()
    return report


# ── HTTP 层 ──────────────────────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    server_version = "ArenaSubmission/0.1"
    protocol_version = "HTTP/1.1"

    @property
    def ctx(self) -> dict:
        return self.server.ctx  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        if self.ctx.get("quiet"):
            return
        sys.stderr.write(f"{self.address_string()} - {fmt % args}\n")

    # -- 响应工具 --

    def _send(self, status: int, body: dict, headers: dict | None = None) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, location: str, status: int = 303) -> None:
        """303：交完卷用 GET 去取报告页，浏览器地址栏停在报告上，刷新不会重复交卷。"""
        self.send_response(status)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_html(self, status: int, page: str) -> None:
        data = page.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> Any:
        raw_len = self.headers.get("Content-Length")
        if raw_len is None:
            raise SubmissionError("missing_body", "缺 Content-Length")
        try:
            length = int(raw_len)
        except ValueError as exc:
            raise SubmissionError("invalid_json", "Content-Length 不是整数") from exc
        if length <= 0:
            raise SubmissionError("invalid_json", "请求体为空")
        if length > MAX_BODY_BYTES:
            raise SubmissionError(
                "body_too_large", f"请求体超过 {MAX_BODY_BYTES} 字节", status=413
            )
        blob = self.rfile.read(length)
        try:
            return json.loads(blob.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SubmissionError("invalid_json", f"请求体不是合法 JSON：{exc}") from exc

    def _guard(self, fn) -> None:
        """统一错误出口。**只有这里能写响应**，避免一条请求写两次响应。"""
        try:
            fn()
        except SubmissionError as exc:
            self._send(exc.status, exc.as_body())
        except hosting.HostError as exc:
            # 托管层的错自带状态码（404 未知会话 / 429 超预算 / 409 空会话）。
            # 放到这里统一转，省得每个入口各写一遍——漏一处就是 500，而 500 会让人
            # 以为"服务挂了"，实际只是他手里的链接不完整。
            self._send(exc.status, exc.as_body())
        except Exception as exc:  # noqa: BLE001 - 兜底，绝不让连接空响应挂死
            self._send(500, {"error": {"code": "internal_error",
                                       "message": f"{type(exc).__name__}: {exc}"}})

    # -- 托管会话（B-51）：学生只拿一条链接，过程留在服务端 --

    def _sessions(self) -> hosting.SessionRegistry:
        sessions = self.ctx.get("sessions")
        if sessions is None:
            raise SubmissionError(
                "session_hosting_disabled",
                "这台服务没开托管会话：启动时加 --admin-token 才会开", status=403,
            )
        return sessions

    def _admin_ok(self) -> bool:
        """开新会话要口令。`compare_digest` 而不是 `==`：口令比较不该泄露时序。"""
        expected = str(self.ctx.get("admin_token") or "")
        given = str(self.headers.get("X-Admin-Token") or "")
        return bool(expected) and secrets.compare_digest(expected, given)

    def _base_url(self) -> str:
        """给页面拼绝对地址。用 `Host` 头——它由请求方给，但只影响他自己看到的链接，
        不会改变服务行为；反过来写死 `127.0.0.1` 则会让托管后的页面把地址写错。"""
        host = self.headers.get("Host")
        if not host:
            h, port = self.server.server_address[:2]
            host = f"{h}:{port}"
        return f"http://{host}"

    def _submit_fn(self):
        """判分入口只在这里组装：`hosting` 不 import 本模块（会成环）。"""
        registry = self.ctx["registry"]
        conn_factory = self.ctx["conn_factory"]
        return lambda payload: submit(payload, registry=registry, conn_factory=conn_factory)

    def _open_session(self) -> None:
        sessions = self._sessions()
        if not self._admin_ok():
            raise SubmissionError(
                "forbidden", "开新会话需要管理口令（请求头 X-Admin-Token）", status=403,
            )
        payload = self._read_json()
        task_id = payload.get("task_id") if isinstance(payload, dict) else None
        if not isinstance(task_id, str) or not task_id:
            raise SubmissionError("bad_request", "缺 task_id", status=400)
        session = sessions.create(task_id)
        self._send(201, {
            "session": session,
            "url": f"{self._base_url()}/s/{session['token']}",
            "tools_url": f"{self._base_url()}/s/{session['token']}/tools/",
        })

    def _session_post(self, path: str) -> bool:
        """`/s/<token>/tools/<name>` 与 `/s/<token>/submit`。命中返回 True。"""
        parts = [p for p in path.split("/") if p]
        if len(parts) < 2 or parts[0] != "s":
            return False
        sessions = self._sessions()
        token = parts[1]
        if len(parts) == 4 and parts[2] == "tools":
            self._session_tool(sessions, token, parts[3])
            return True
        if len(parts) == 3 and parts[2] == "submit":
            report, created = self._grade_session(sessions, token)
            sid = report["submission"]["submission_id"]
            self._send(201 if created else 200, {
                "submission_id": sid,
                "reused": not created,
                "report_url": f"/submissions/{sid}/report.html",
                "report": report,
            })
            return True
        return False

    def _session_tool(self, sessions, token: str, name: str) -> None:
        """代调一次工具，**状态码原样回放**。

        回放 500 不是"服务出错"，是这道题的考点：学习者要看到失败、决定要不要重试。
        这里若把 500 吞成 200，整道题就白出了。
        """
        payload = self._read_json()
        try:
            _index, status, body = sessions.call(token, name, payload)
        except hosting.HostError as exc:
            raise SubmissionError(exc.code, exc.message, status=exc.status) from exc
        self._send(status, body if isinstance(body, dict) else {"body": body})

    def _grade_session(self, sessions, token: str):
        try:
            return sessions.grade(token, self._submit_fn())
        except hosting.HostError as exc:
            raise SubmissionError(exc.code, exc.message, status=exc.status) from exc

    def _session_get(self, path: str) -> None:
        """`/s/<token>` 是做题那一页；`/s/<token>/submit` 是交卷并跳到报告。

        交卷用 GET 是刻意的：这一页不许有脚本（与报告页同一条约束），
        没有脚本就没法用按钮发 POST。重复点由 `sessions.grade` 的"钉住 submission_id"兜住。
        """
        sessions = self._sessions()
        parts = [p for p in path.split("/") if p]
        if len(parts) < 2:
            raise SubmissionError("not_found", "缺会话编号", status=404)
        token = parts[1]
        try:
            session = sessions.get(token)
        except hosting.HostError as exc:
            raise SubmissionError(exc.code, exc.message, status=exc.status) from exc

        if len(parts) == 2:
            task = sessions.task(session["task_id"])
            page = hosting.render_landing(
                session, public_task(task, self.ctx["registry"].items()), self._base_url(),
            )
            self._send_html(200, page)
            return

        if len(parts) == 3 and parts[2] == "submit":
            try:
                report, _created = sessions.grade(token, self._submit_fn())
            except hosting.HostError as exc:
                if exc.code != "nothing_to_grade":
                    raise SubmissionError(exc.code, exc.message, status=exc.status) from exc
                self._send_html(409, _empty_session_page(token, exc.message))
                return
            sid = report["submission"]["submission_id"]
            self._redirect(f"/submissions/{sid}/report.html")
            return

        raise SubmissionError("not_found", f"没有这个路径：{path}", status=404)

    # -- 路由 --

    def do_POST(self) -> None:  # noqa: N802
        def run() -> None:
            path = self.path.split("?", 1)[0]
            if path == "/sessions":
                self._open_session()
                return
            if self._session_post(path):
                return
            if path == "/submissions":
                payload = self._read_json()
                report = submit(payload, registry=self.ctx["registry"],
                                conn_factory=self.ctx["conn_factory"])
                self._send(201, report)
                return
            # `/submissions/{id}/appeals`：学习者对某一份判分提出异议。
            parts = [p for p in path.split("/") if p]
            if len(parts) == 3 and parts[0] == "submissions" and parts[2] == "appeals":
                self._send(201, self._create_appeal(parts[1], self._read_json()))
                return
            raise SubmissionError("not_found", f"没有这个路径：{self.path}", status=404)

        self._guard(run)

    def do_GET(self) -> None:  # noqa: N802
        def run() -> None:
            parts = urlsplit(self.path)
            path = parts.path
            if path == "/healthz":
                self._send(200, {
                    "ok": True,
                    "schema_version": store.SCHEMA_VERSION,
                    "tasks": self.ctx["registry"].ids(),
                })
                return
            if path == "/tasks":
                self._send(200, {"tasks": [
                    public_task(task, self.ctx["registry"].items())
                    for _tid, task in self.ctx["registry"].items()
                ]})
                return
            if path.startswith("/tasks/"):
                task_id = path[len("/tasks/"):].strip("/")
                if not task_id:
                    raise SubmissionError("bad_request", "缺 task_id", status=400)
                self._send(200, public_task(
                    self.ctx["registry"].get(task_id), self.ctx["registry"].items()
                ))
                return
            if path.startswith("/s/"):
                self._session_get(path)
                return
            if path.startswith("/submissions/"):
                rest = path[len("/submissions/"):].strip("/")
                if not rest:
                    raise SubmissionError("bad_request", "缺 submission_id", status=400)

                # 子资源先判：否则 `/submissions/x/reappraisals` 会被当成 id 为
                # "x/reappraisals" 的单份提交，然后 404 得像是"这份不存在"。
                if rest.endswith("/reappraisals"):
                    self._send_reappraisals(rest[: -len("/reappraisals")].strip("/"))
                    return
                if rest.endswith("/appeals"):
                    self._send_appeals(rest[: -len("/appeals")].strip("/"))
                    return
                if rest.endswith("/report.html"):
                    self._send_report_html(rest[: -len("/report.html")].strip("/"))
                    return

                self._send_report(rest, _parse_view(parts.query))
                return
            raise SubmissionError("not_found", f"没有这个路径：{path}", status=404)

        self._guard(run)

    def _send_report(self, submission_id: str, view: str) -> None:
        """`view=effective` 给生效报告，`view=original` 给当时那份。"""
        if not submission_id:
            raise SubmissionError("bad_request", "缺 submission_id", status=400)
        conn = self.ctx["conn_factory"]()
        try:
            report = (store.load_report(conn, submission_id) if view == "original"
                      else store.effective_report(conn, submission_id))
            self._send(200, report)
        except store.StoreError as exc:
            raise SubmissionError("not_found", str(exc), status=404) from exc
        finally:
            conn.close()

    def _send_report_html(self, submission_id: str) -> None:
        """把报告渲染成一页 HTML 直接返回——浏览器打开就能看，不用先存文件再跑命令。"""
        if not submission_id:
            raise SubmissionError("bad_request", "缺 submission_id", status=400)
        conn = self.ctx["conn_factory"]()
        try:
            report = store.effective_report(conn, submission_id)
        except store.StoreError as exc:
            raise SubmissionError("not_found", str(exc), status=404) from exc
        finally:
            conn.close()

        task = None
        task_id = (report.get("submission") or {}).get("task_id")
        if task_id:
            try:
                task = self.ctx["registry"].get(task_id)
            except SubmissionError:
                task = None          # 任务文件被换掉了，也照样把当时的报告给人看
        title = (task or {}).get("title") or ""
        self._send_html(200, report_html.render_html(report, task_title=title, task=task))

    def _send_reappraisals(self, submission_id: str) -> None:
        """重评链（按 revision 升序）。

        **提交不存在时 404，不返回空数组**：空数组的语义是"这份交过、没重评过"。
        两者混成一样，"我拼错了 id"会显示成"这份没人改过分"。
        """
        if not submission_id:
            raise SubmissionError("bad_request", "缺 submission_id", status=400)
        conn = self.ctx["conn_factory"]()
        try:
            store.load_report(conn, submission_id)          # 兼做存在性检查
            self._send(200, {"submission_id": submission_id,
                             "effective_ratio": store.effective_ratio(conn, submission_id),
                             "original_ratio": float(
                                 store.load_submission(conn, submission_id)["assert_score"]),
                             "reappraisals": store.list_reappraisals(conn, submission_id)})
        except store.StoreError as exc:
            raise SubmissionError("not_found", str(exc), status=404) from exc
        finally:
            conn.close()


    def _create_appeal(self, submission_id: str, payload: dict) -> dict:
        """提一条申诉。返回落库后的行（`status` 恒为 `pending`）。

        **只落库，不重评**：接受申诉是人的判断，新判据要人给（`reappraise(task=...)`）。
        这里若"顺手重算一遍"，等于替裁决人决定了标准。

        错误码分三类，因为修法完全不同：
        - 404：这份提交不存在（拼错 id / 还没交）
        - 409：你已经有一条待处理申诉（等结果，别再提）
        - 400：请求本身有问题（理由空 / 断言 id 不存在 / 替别人申诉）
        """
        if not submission_id:
            raise SubmissionError("bad_request", "缺 submission_id", status=400)
        user_id = _str_field(payload, "user_id", max_len=100)
        reason = _str_field(payload, "reason_text", max_len=2000)
        target = _str_field(payload, "target_assertion", required=False, max_len=100)
        conn = self.ctx["conn_factory"]()
        try:
            return store.create_appeal(conn, submission_id, user_id=user_id or "",
                                       reason_text=reason or "", target_assertion=target)
        except store.StoreError as exc:
            # 用消息分流而不是给 StoreError 派生子类：这三句话是给人看的、也稳定，
            # 而为三个错误码各造一个异常类，只会让 store 层多背一层协议知识。
            message = str(exc)
            if "还没落库" in message:
                raise SubmissionError("not_found", message, status=404) from exc
            if "已经在队列里" in message:
                raise SubmissionError("appeal_already_pending", message, status=409) from exc
            raise SubmissionError("invalid_appeal", message, status=400) from exc
        finally:
            conn.close()

    def _send_appeals(self, submission_id: str) -> None:
        """一份提交的申诉链（含历史，按提交先后）。

        **提交不存在时 404，不返回空数组**——与 `_send_reappraisals` 同一条理由：
        空数组的语义是"交过、没申诉过"；混成一样，"id 拼错了"会显示成"没人有异议"。
        """
        if not submission_id:
            raise SubmissionError("bad_request", "缺 submission_id", status=400)
        conn = self.ctx["conn_factory"]()
        try:
            store.load_report(conn, submission_id)          # 兼做存在性检查
            self._send(200, {"submission_id": submission_id,
                             "appeals": store.load_appeals(conn, submission_id)})
        except store.StoreError as exc:
            raise SubmissionError("not_found", str(exc), status=404) from exc
        finally:
            conn.close()



def bootstrap(db_path: str | Path, registry: TaskRegistry) -> None:
    """建库 + 种子导入（幂等）。技能 / 错误模式 / 任务三类种子都从仓库里的真相源读。

    **25 条任务注册包在一个事务里（2026-11-23，B-36）**：`register_task` 各自带
    `atomic()`，逐条调用就是 25 次真正的事务提交 = 25 次 fsync。实测本机一条 60ms，
    25 条 1.5 秒，而每次建临时库都要付——那是测试里最大的一笔固定开销。
    外层再套一个 `atomic()` 后，内层 savepoint 嵌套、只有最外层 `RELEASE` 落盘，
    语义反而更严：以前中途失败会留下"前 k 条已注册"的半成品库，现在全有或全无。
    """
    conn = store.connect(db_path)
    try:
        store.init_db(conn)
        store.register_skills(conn)
        store.register_patterns(conn)
        with store.atomic(conn):
            for tid, _task in registry.items():
                store.register_task(conn, registry.path_of(tid))
    finally:
        conn.close()


def make_server(db_path: str | Path, *, host: str = "127.0.0.1", port: int = 0,
                tasks_dir: str | Path = TASKS_DIR, quiet: bool = False,
                bootstrap_db: bool = True,
                admin_token: str | None = None,
                max_calls: int = hosting.DEFAULT_MAX_CALLS) -> ThreadingHTTPServer:
    """构造（但不启动）服务。`port=0` 由系统分配——测试据此拿真实端口，不猜。

    `admin_token` 决定**托管会话是否启用**：不给就没有 `/sessions` 这条路由（403），
    而不是给一个默认口令。默认关闭是刻意的——这条路由能开新会话，等于能消耗服务器资源，
    上线时忘了配口令比忘配数据库危险得多。
    """
    registry = TaskRegistry(tasks_dir)
    if bootstrap_db:
        bootstrap(db_path, registry)

    def conn_factory():
        return store.connect(db_path)

    server = ThreadingHTTPServer((host, port), _Handler)
    server.daemon_threads = True
    sessions = None
    if admin_token:
        sessions = hosting.SessionRegistry(tasks_dir, conn_factory=conn_factory,
                                           max_calls=max_calls)
    server.ctx = {  # type: ignore[attr-defined]
        "registry": registry,
        "conn_factory": conn_factory,
        "db_path": str(db_path),
        "quiet": quiet,
        "sessions": sessions,
        "admin_token": admin_token,
    }
    return server


def serve(db_path: str, *, host: str = "127.0.0.1", port: int = 8080,
          quiet: bool = False, admin_token: str | None = None,
          max_calls: int = hosting.DEFAULT_MAX_CALLS) -> int:
    server = make_server(db_path, host=host, port=port, quiet=quiet,
                         admin_token=admin_token, max_calls=max_calls)
    real_host, real_port = server.server_address[:2]
    print(f"listening on http://{real_host}:{real_port}  (db={db_path})")
    print(f"tasks: {server.ctx['registry'].ids()}")  # type: ignore[attr-defined]
    if admin_token:
        print(f"托管会话已开：每个会话最多 {max_calls} 次调用")
        print(f"  开一个：curl -X POST -H 'X-Admin-Token: <口令>' "
              f"-d '{{\"task_id\": \"dbg-001-order-report\"}}' "
              f"http://{real_host}:{real_port}/sessions")
    else:
        print("托管会话未开（加 --admin-token 才开）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="M1 / B-02：黑盒提交服务")
    ap.add_argument("--db", default="arena.db", help="SQLite 文件路径（不存在会自动建）")
    ap.add_argument("--host", default="127.0.0.1",
                    help="绑定地址；默认只绑本机——当前没有鉴权，不要绑 0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--quiet", action="store_true", help="不打印每条请求日志")
    ap.add_argument("--admin-token", default=None,
                    help="开了它才启用托管会话（/sessions）。不给=不开，不是给默认口令")
    ap.add_argument("--max-calls", type=int, default=hosting.DEFAULT_MAX_CALLS,
                    help="每个会话最多允许多少次工具调用")
    args = ap.parse_args(argv)
    return serve(args.db, host=args.host, port=args.port, quiet=args.quiet,
                 admin_token=args.admin_token, max_calls=args.max_calls)


if __name__ == "__main__":
    raise SystemExit(main())
