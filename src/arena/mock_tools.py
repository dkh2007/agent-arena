"""B-03：由任务 YAML 驱动的 mock 工具服务。

## 为什么需要它

在此之前，`raw-*.json` 里的 `trace.tool_calls` 全是**手写**的，
而任务 YAML 里的 `fault_injection`（首次 offset=10 返 500）与
`tools[].mock.pagination`（页大小上限 / 越界返回空 / total 由夹具长度推导）
**没有任何执行者**——`grep -rn fault_injection src/` 只命中 `store.py` 的一个 0/1 列。
也就是说：YAML 写「第一次调用会失败」，但从来没有任何东西真的失败过；
R3（失败重试证据）能满分，只是因为夹具里**恰好**写了一行 error。

本模块把这两块声明变成真的会跑的服务：一个**只实现 YAML 已声明语义**的 mock。

## 刻意不实现的部分（不发明语义）

任务 YAML 只声明了两件与工具行为有关的事：分页语义、故障注入。
其余行为（额外参数要不要拒、缺必填怎么答）**没有任何声明**，所以：

- 额外参数 → **忽略**（R4「参数越界/多余」是从 trace 判的，不靠服务端拒绝）；
- 缺必填参数 → 返回 `400 missing_param`。这是本模块**自己定的**边界，之所以要定，
  是因为没有它服务端就得假装能分页；已在该行标注「本模块定义，非 YAML 声明」。

**判断依据**：mock 的职责是让 YAML 里写的事真的发生，不是替评测器做判断。
一旦 mock 开始"帮忙判分"，它就变成了第二个真相源。

## 故障注入的重置时机

`reset_policy: per_submission` ⇒ **每次提交前必须 `reset()`**。
不重置的后果是「上一次用掉的故障额度漏到下一次」——第 3 轮评审 ①-2 记的正是这个缺口：
第二个学习者本该看到 500，却因为额度已被用掉而一次成功，R3 也就测不到重试。
"""

from __future__ import annotations

import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class MockConfigError(ValueError):
    """任务 YAML 的 mock 声明不完整或不自洽：宁可拒跑，也不猜。"""


# 本模块自定义的错误码（**不是** YAML 声明）。加进这个集合才算"说得出原因"，
# 防止以后有人往 body 里塞个新错误串而没人知道它从哪来。
_LOCAL_ERRORS = {"missing_param", "unknown_tool", "bad_request", "file_not_found"}


def _dotted(task: dict, path: str) -> Any:
    """按 `test_inputs.orders_fixture` 这种点路径取值；取不到就报错，不返回 None。"""
    node: Any = task
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            raise MockConfigError(f"mock 引用的 fixture 路径不存在：{path}（断在 {part}）")
        node = node[part]
    return node


def _match_key(match: dict) -> tuple:
    """把 `{offset: 10}` 变成可哈希键，保持声明顺序。"""
    return tuple(sorted((str(k), json.dumps(v, sort_keys=True)) for k, v in match.items()))


class ToolSpec:
    """一个工具的已声明语义。缺声明就在构造期报错。"""

    def __init__(self, raw: dict):
        name = raw.get("name")
        if not name:
            raise MockConfigError("tools[] 里有一项没有 name")
        self.name = name
        self.params = {p["name"]: p for p in raw.get("parameters") or []}
        self.additional_allowed = bool(raw.get("additional_properties", True))

        mock = raw.get("mock")
        self.has_mock = bool(mock)
        # round-86（B-05b）：第二种回放形态——按某个入参去夹具字典里取值。
        # 分页是「按 offset 切片」，本形态是「按 path 查表」；两者都只回放夹具，
        # 不判断对错（判断是断言层的事，mock 一旦开始判分就成了第二个真相源）。
        self.lookup = None
        if not mock:
            self.pagination = None
            self.rows = []
            return

        src = mock.get("source")
        if src != "fixture_ref":
            raise MockConfigError(
                f"{name}: mock.source={src!r} 未实现（当前只有 fixture_ref）"
            )
        self.fixture_path = mock.get("fixture")
        if not self.fixture_path:
            raise MockConfigError(f"{name}: mock.fixture 缺失")

        lookup = mock.get("lookup")
        if lookup is not None:
            if not isinstance(lookup, str) or not lookup:
                raise MockConfigError(f"{name}: mock.lookup 必须是非空字符串（入参名）")
            if lookup not in self.params:
                raise MockConfigError(f"{name}: mock.lookup={lookup!r} 不是本工具声明的参数")
            self.lookup = lookup

        pag = mock.get("pagination")
        if self.lookup is not None and pag is not None:
            # 两种回放形态互斥：一个工具不可能既按 offset 切片又按 path 查表。
            raise MockConfigError(f"{name}: mock 不能同时声明 lookup 与 pagination")
        if pag is None:
            self.pagination = None
        else:
            required = ("page_size_max", "offset_base", "range", "total_source")
            missing = [k for k in required if k not in pag]
            if missing:
                raise MockConfigError(f"{name}: pagination 缺字段 {missing}")
            if pag["range"] != "half_open":
                raise MockConfigError(
                    f"{name}: pagination.range={pag['range']!r} 未实现（只有 half_open）"
                )
            if pag["total_source"] != "fixture_length":
                raise MockConfigError(
                    f"{name}: pagination.total_source={pag['total_source']!r} 未实现"
                )
            self.pagination = pag


class MockToolService:
    """按 YAML 声明的行为响应工具调用。线程安全（HTTP 服务是多线程的）。"""

    def __init__(self, task: dict):
        self._task = task
        self._lock = threading.Lock()
        self._specs: dict[str, ToolSpec] = {}
        for raw in task.get("tools") or []:
            spec = ToolSpec(raw)
            self._specs[spec.name] = spec
        if not self._specs:
            raise MockConfigError("任务没有声明任何工具")

        # 夹具在这里一次性解析并**深拷贝**：mock 绝不能改到任务原文，
        # 否则第二次提交看到的就是被上一次污染的数据。
        for spec in self._specs.values():
            if spec.has_mock:
                spec.rows = copy.deepcopy(_dotted(task, spec.fixture_path))
                if spec.lookup is not None and not isinstance(spec.rows, dict):
                    raise MockConfigError(
                        f"{spec.name}: mock.lookup 要求夹具是「键 → 文本」的字典，"
                        f"实际是 {type(spec.rows).__name__}"
                    )

        fi = task.get("fault_injection") or {}
        self._fault = fi if fi.get("enabled") else None
        if self._fault:
            self._fault_target = self._fault.get("target_tool")
            self._fault_match = self._fault.get("match") or {}
            self._fault_failures = {
                int(f["attempt"]): f for f in self._fault.get("failures") or []
            }
            self._fault_then = self._fault.get("then", "success")

        # `_written` 是产物账本，跨调用累积；`reset()` 不清它
        # （reset 的语义是「清故障额度」，产物属于业务结果，不是额度）。
        self._written: dict[str, str] = {}
        self.reset()

    # ── 状态 ────────────────────────────────────────────────────────────────

    def reset(self) -> None:
        """按 `reset_policy: per_submission` 清空故障额度与调用账本。"""
        with self._lock:
            self._attempts: dict[tuple, int] = {}
            self.calls_served = 0

    def tool_names(self) -> list[str]:
        return sorted(self._specs)

    # ── 调用 ────────────────────────────────────────────────────────────────

    def call(self, name: str, params: dict) -> tuple[int, dict]:
        """返回 (http_status, body)。**不抛异常**：工具失败是正常业务结果。"""
        spec = self._specs.get(name)
        if spec is None:
            return 400, {
                "error": "unknown_tool",
                "detail": f"未声明的工具 {name!r}；已声明：{self.tool_names()}",
            }
        if not isinstance(params, dict):
            return 400, {"error": "bad_request", "detail": "params 必须是对象"}

        missing = [k for k, p in spec.params.items()
                   if p.get("required") and k not in params]
        if missing:
            # 本模块定义的边界，YAML 未声明（理由见模块文档「刻意不实现」）
            return 400, {"error": "missing_param", "detail": f"缺必填参数 {missing}"}

        with self._lock:
            self.calls_served += 1
            fault = self._take_fault(name, params)

        if fault is not None:
            return int(fault.get("http_status", 500)), {
                "error": fault.get("error", "internal_error")
            }

        if spec.lookup is not None:
            return self._replay_by_param(spec, params)

        if spec.pagination is not None:
            return self._paginate(spec, params)

        if name == "write_report":
            return self._write_report(params)

        # 有声明 mock 但既无分页也无专用行为：不假装成功
        return 501, {
            "error": "mock_not_implemented",
            "detail": f"{name} 的 mock 声明未覆盖到可执行行为",
        }

    # ── 内部 ────────────────────────────────────────────────────────────────

    def _take_fault(self, name: str, params: dict) -> dict | None:
        """调用方已持锁。命中第 attempt 次 ⇒ 返回该条失败配置。"""
        if not self._fault or name != self._fault_target:
            return None
        for key, want in self._fault_match.items():
            if params.get(key) != want:
                return None

        key = (name, _match_key(self._fault_match))
        self._attempts[key] = self._attempts.get(key, 0) + 1
        n = self._attempts[key]
        if n in self._fault_failures:
            return self._fault_failures[n]
        if self._fault_then != "success":
            raise MockConfigError(
                f"fault_injection.then={self._fault_then!r} 未实现（只有 success）"
            )
        return None

    def _paginate(self, spec: ToolSpec, params: dict) -> tuple[int, dict]:
        pag = spec.pagination
        rows = spec.rows
        base = int(pag["offset_base"])
        page_max = int(pag["page_size_max"])

        offset = params.get("offset", base)
        limit = params.get("limit", page_max)
        try:
            offset = int(offset)
            limit = int(limit)
        except (TypeError, ValueError):
            return 400, {"error": "bad_request", "detail": "offset/limit 必须是整数"}

        if limit > page_max or limit < 1:
            if pag.get("on_limit_exceed") == "error":
                return 400, {
                    "error": pag.get("error_code", "invalid_limit"),
                    "detail": f"limit 必须在 1..{page_max}",
                }
            return 400, {"error": "invalid_limit"}

        total = len(rows)
        start = offset - base
        if start < 0:
            return 400, {"error": "bad_request", "detail": "offset 小于 offset_base"}

        if start >= total:
            if pag.get("on_offset_out_of_range") == "empty_list":
                return 200, {"orders": [], "total": total}
            return 400, {"error": "offset_out_of_range"}

        page = rows[start:start + limit]
        return 200, {"orders": copy.deepcopy(page), "total": total}

    def _replay_by_param(self, spec: ToolSpec, params: dict) -> tuple[int, dict]:
        """按 `mock.lookup` 指定的入参去夹具字典里取文本原样返回。

        取不到就 404 并把**可用键**列出来：这不是"服务端判错"，是把"你要的东西不存在"
        如实告诉调用方（真实的读文件接口也会这么答）。有没有乱猜路径由 R4 从 trace 判。
        """
        key = params.get(spec.lookup)
        if key not in spec.rows:
            return 404, {
                "error": "file_not_found",
                "detail": f"{spec.lookup}={key!r} 不在可读范围内",
                "available": sorted(spec.rows),
            }
        text = spec.rows[key]
        if not isinstance(text, str):
            raise MockConfigError(
                f"{spec.name}: 夹具 {key!r} 的内容必须是字符串，实际是 {type(text).__name__}"
            )
        return 200, {
            "path": key,
            "content": text,
            "lines": len(text.splitlines()),
            "bytes": len(text.encode("utf-8")),
        }

    def _write_report(self, params: dict) -> tuple[int, dict]:
        path = params.get("path")
        content = params.get("content")
        if not isinstance(path, str) or not isinstance(content, str):
            return 400, {"error": "bad_request", "detail": "path/content 必须是字符串"}
        with self._lock:
            self._written[path] = content
        return 200, {"written": path, "bytes": len(content.encode("utf-8"))}

    def written(self) -> dict[str, str]:
        with self._lock:
            return dict(self._written)


# ---------------------------------------------------------------------------
# HTTP 层
# ---------------------------------------------------------------------------

def make_server(task: dict, *, host: str = "127.0.0.1", port: int = 0,
                quiet: bool = True) -> tuple[ThreadingHTTPServer, MockToolService]:
    """起一个 mock 工具服务，返回 (server, service) —— service 需要 reset()。"""
    service = MockToolService(task)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            if not quiet:
                super().log_message(fmt, *args)

        def _send(self, status: int, body: dict) -> None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
            if self.path == "/tools":
                self._send(200, {"tools": service.tool_names()})
            else:
                self._send(404, {"error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            if not self.path.startswith("/tools/"):
                self._send(404, {"error": "not_found"})
                return
            name = self.path[len("/tools/"):]
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                params = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send(400, {"error": "bad_request", "detail": "body 不是合法 JSON"})
                return
            status, body = service.call(name, params)
            self._send(status, body)

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server, service
