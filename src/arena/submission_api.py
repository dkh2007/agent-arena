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
| GET | `/submissions/{id}` | 读回已落库的报告 |
| GET | `/healthz` | 存活探针 + 已注册任务清单 |

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

## 已知边界（是登记过的决定，不是遗漏）

- **无鉴权**，默认只绑 `127.0.0.1`。上公网前必须加，见 BACKLOG。
- **端点探测只做 TCP 连接**：不发业务请求、不采集 trace。发请求会带来副作用
  （被测 Agent 真的开始干活），污染评测。真正的黑盒调用与 OTel 采集是 B-03。
- **端点可达但没带证据 → 501**：本服务尚不具备主动采集能力，如实返回
  `endpoint_capture_not_implemented`，而不是假装评了一遍。
- **无排队 / 限流 / 重试**：单机演示用。平台重试会污染 R3（重试是学习者的被测能力）。

用法：
    python src/arena/submission_api.py --db demo.db --port 8080
    curl -X POST http://127.0.0.1:8080/submissions -H "Content-Type: application/json" \\
         -d @request.json
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

try:  # 作为包被 import
    from . import assertion_evaluator as ae
    from . import diagnosis_report as dr
    from . import store
    from . import trace_pipeline as tp
except ImportError:  # 作为独立脚本运行
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import assertion_evaluator as ae  # type: ignore[no-redef]
    import diagnosis_report as dr  # type: ignore[no-redef]
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

def evaluate(prep: Prepared) -> dict:
    """走内部主链路出报告。**纯函数**（不写盘、不发网络包），便于单测对拍。"""
    if prep.endpoint_url is not None:
        reachable, detail = probe_endpoint(prep.endpoint_url)
        if not reachable:
            return unreachable_report(prep, detail)
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
    return report


def submit(payload: Any, *, registry: TaskRegistry, conn_factory, now: str | None = None) -> dict:
    """校验 -> 评测 -> 落库。返回报告。

    `conn_factory` 是「产出连接的可调用对象」而不是连接本身：`ThreadingHTTPServer`
    每请求一个线程，而 sqlite3 连接默认不允许跨线程使用。每请求新建连接最省心，
    本地文件打开成本是微秒级，不值得为它引入连接池与锁。
    """
    prep = prepare(payload, registry, now=now)
    report = evaluate(prep)

    conn = conn_factory()
    try:
        store.ensure_user(conn, prep.user_id)
        store.save_submission(
            conn, report, user_id=prep.user_id,
            trace=prep.trace, agent_endpoint_url=prep.endpoint_url,
        )
    except store.StoreError as exc:
        # 重复 submission_id 走这里：409 + 不覆盖，诊断记录要能回溯到「当时那份」
        raise SubmissionError("store_rejected", str(exc), status=409) from exc
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

    def _send(self, status: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
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
        except Exception as exc:  # noqa: BLE001 - 兜底，绝不让连接空响应挂死
            self._send(500, {"error": {"code": "internal_error",
                                       "message": f"{type(exc).__name__}: {exc}"}})

    # -- 路由 --

    def do_POST(self) -> None:  # noqa: N802
        def run() -> None:
            if self.path.split("?", 1)[0] != "/submissions":
                raise SubmissionError("not_found", f"没有这个路径：{self.path}", status=404)
            payload = self._read_json()
            report = submit(payload, registry=self.ctx["registry"],
                            conn_factory=self.ctx["conn_factory"])
            self._send(201, report)

        self._guard(run)

    def do_GET(self) -> None:  # noqa: N802
        def run() -> None:
            path = self.path.split("?", 1)[0]
            if path == "/healthz":
                self._send(200, {
                    "ok": True,
                    "schema_version": store.SCHEMA_VERSION,
                    "tasks": self.ctx["registry"].ids(),
                })
                return
            if path.startswith("/submissions/"):
                submission_id = path[len("/submissions/"):].strip("/")
                if not submission_id:
                    raise SubmissionError("bad_request", "缺 submission_id", status=400)
                conn = self.ctx["conn_factory"]()
                try:
                    self._send(200, store.load_report(conn, submission_id))
                except store.StoreError as exc:
                    raise SubmissionError("not_found", str(exc), status=404) from exc
                finally:
                    conn.close()
                return
            raise SubmissionError("not_found", f"没有这个路径：{path}", status=404)

        self._guard(run)


def bootstrap(db_path: str | Path, registry: TaskRegistry) -> None:
    """建库 + 种子导入（幂等）。技能 / 错误模式 / 任务三类种子都从仓库里的真相源读。"""
    conn = store.connect(db_path)
    try:
        store.init_db(conn)
        store.register_skills(conn)
        store.register_patterns(conn)
        for tid, _task in registry.items():
            store.register_task(conn, registry.path_of(tid))
    finally:
        conn.close()


def make_server(db_path: str | Path, *, host: str = "127.0.0.1", port: int = 0,
                tasks_dir: str | Path = TASKS_DIR, quiet: bool = False,
                bootstrap_db: bool = True) -> ThreadingHTTPServer:
    """构造（但不启动）服务。`port=0` 由系统分配——测试据此拿真实端口，不猜。"""
    registry = TaskRegistry(tasks_dir)
    if bootstrap_db:
        bootstrap(db_path, registry)

    def conn_factory():
        return store.connect(db_path)

    server = ThreadingHTTPServer((host, port), _Handler)
    server.daemon_threads = True
    server.ctx = {  # type: ignore[attr-defined]
        "registry": registry,
        "conn_factory": conn_factory,
        "db_path": str(db_path),
        "quiet": quiet,
    }
    return server


def serve(db_path: str, *, host: str = "127.0.0.1", port: int = 8080,
          quiet: bool = False) -> int:
    server = make_server(db_path, host=host, port=port, quiet=quiet)
    real_host, real_port = server.server_address[:2]
    print(f"listening on http://{real_host}:{real_port}  (db={db_path})")
    print(f"tasks: {server.ctx['registry'].ids()}")  # type: ignore[attr-defined]
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
    args = ap.parse_args(argv)
    return serve(args.db, host=args.host, port=args.port, quiet=args.quiet)


if __name__ == "__main__":
    raise SystemExit(main())
