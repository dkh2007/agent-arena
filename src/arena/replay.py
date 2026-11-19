"""B-20b 第一步：把账本里的「有序调用序列」重放成**一组行**，交给推导规则。

## 它补的是哪一环

`derivation.py` 只做「行 → 数」，`reconciliation.py` 只做「数 → 有没有出处」。
两环之间缺一个东西：**行从哪来**。本模块就是那一段，并且只有那一段：

```text
账本 (call_index, name, params) ──重放──▶ 工具返回体 ──取行集──▶ rows ──derive──▶ 数
                ▲                                                              │
                └──────────── 任务 YAML 的 tools[*].mock（数据在 θ 里）────────┘
```

## 三条口径（都是选的，写下来免得后人以为是实现细节）

1. **行键从 `mock.pagination.response_shape` 取，不新立一条声明**。
   那个块已经在描述「分页响应长什么样」（`{orders: []}`），行集就在那个键下。
   另写一个 `replay_rows: orders` 等于让同一件事有两个说法，哪天改了一个另一个不会报错。
2. **只取成功页，失败页记下来但不补**。`skipped` 是给诊断看的，不是给重放看的：
   自动补一次失败页等于替被测 Agent 重试，而"失败之后有没有重试"正是要判的东西
   （与 YAML `execution.platform_retry: 0` 同一条理由）。
3. **`call_index` 必须连续 1..n，否则拒跑**。重放的正确性依赖顺序：故障额度按
   `(tool, match)` 计数（`test_replay_premise.py` 钉着），同一参数第 1 次与第 2 次
   结果不同。编号有洞或重复时「按数组顺序重放」可能**恰好**对，也可能不对——
   那种"碰巧对"不能留成静默行为（`platform_capture.compare_traces` 踩过同一类坑）。

## 它不是判定

产出的 `Rows` 与数值都不带 pass/fail。落点见 B-20b 硬约束：**不许做成 credit 断言**，
也不许复用 `process_fact.missing`。本模块只负责「重放得出什么」，怎么用由调用方定。
"""

from __future__ import annotations

import copy
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 脚本模式（python src/arena/replay.py）下 sys.path[0] 是 src/arena，包名 `arena` 不可见。
_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:  # pragma: no cover - 只在脚本模式生效
    sys.path.insert(0, str(_SRC))

from arena import mock_tools as mt  # noqa: E402
from arena.derivation import derive, load_rule  # noqa: E402


class ReplayError(ValueError):
    """重放的前提不成立（不是"这个 Agent 做错了"，是"这次没法重放"）。"""


@dataclass
class Rows:
    """重放拼出来的行集，连同它的来路。"""

    tool: str
    key: str
    rows: list[Any] = field(default_factory=list)
    used: list[int] = field(default_factory=list)        # 真正出行的 call_index
    skipped: list[dict] = field(default_factory=list)    # 本该出行却没出的（附原因）

    @property
    def page_count(self) -> int:
        return len(self.used)

    def as_dict(self) -> dict:
        return {
            "tool": self.tool,
            "key": self.key,
            "row_count": len(self.rows),
            "pages": list(self.used),
            "skipped": [dict(s) for s in self.skipped],
        }


def row_sources(task: dict) -> dict[str, str]:
    """{工具名: 响应体里的行键}，从既有声明里取（见模块文档第 1 条）。

    找不到任何带分页的工具，或同一任务有两个，都直接报错：
    后者意味着「行集该来自哪一个」没有口径，猜一个就是替任务作者决定。
    """
    tools = task.get("tools")
    if not isinstance(tools, list):
        raise ReplayError("任务没有 tools 列表，无从得知行集来自哪次调用")

    found: dict[str, str] = {}
    for tool in tools:
        mock = (tool or {}).get("mock") or {}
        pag = mock.get("pagination")
        if not isinstance(pag, dict):
            continue
        shape = pag.get("response_shape")
        if not isinstance(shape, dict) or len(shape) != 1:
            raise ReplayError(
                f"工具 {tool.get('name')!r} 的 pagination.response_shape 必须形如 "
                f"{{orders: []}}（恰好一个键），实际 {shape!r}——"
                "行集出自哪个键没有口径，不猜"
            )
        found[tool["name"]] = next(iter(shape))

    if not found:
        raise ReplayError(
            "任务里没有任何带 pagination 声明的工具，没有可重放的行集"
            "（fix-* 这类任务的答案不是推导值，本来就不该走这条路）"
        )
    if len(found) > 1:
        raise ReplayError(
            f"任务有多个分页工具 {sorted(found)}，「行集该来自哪一个」没有声明；"
            "要重放先把它写清楚"
        )
    return found


def calls_of(trace: dict) -> list[dict]:
    """从 trace 取有序调用序列（校验交给 `_checked_order`）。"""
    if not isinstance(trace, dict):
        raise ReplayError(f"trace 必须是映射，实际是 {type(trace).__name__}")
    calls = trace.get("tool_calls")
    if not isinstance(calls, list) or not calls:
        raise ReplayError("trace 里没有 tool_calls，无可重放")
    return _checked_order(calls)


def _checked_order(calls: list) -> list[dict]:
    """校验形状与编号，返回按 `call_index` 排好的序列。

    由 `rows_from_calls` 而不是只在 `calls_of` 里调用：编号不连续时，
    「按数组顺序重放」可能碰巧对、也可能把故障额度打到别的调用上——
    那是**两个入口**都该拒跑的事，不是只有走 trace 的那条路该管。
    """
    seen: list[int] = []
    for pos, call in enumerate(calls):
        if not isinstance(call, dict):
            raise ReplayError(f"第 {pos} 条调用不是映射")
        idx = call.get("call_index")
        if isinstance(idx, bool) or not isinstance(idx, int):
            raise ReplayError(f"第 {pos} 条调用缺 call_index（或不是整数）：{idx!r}")
        if not isinstance(call.get("name"), str) or not call["name"]:
            raise ReplayError(f"第 {pos} 条调用缺工具名")
        if not isinstance(call.get("params"), dict):
            raise ReplayError(f"第 {pos} 条调用的 params 不是映射")
        seen.append(idx)

    want = list(range(1, len(seen) + 1))
    if sorted(seen) != want:
        raise ReplayError(
            f"call_index 必须恰好是 1..{len(seen)}，实际 {seen}——"
            "编号有洞/重复时，按数组顺序重放可能碰巧对、也可能不对，不留这种静默行为"
        )
    return sorted(calls, key=lambda c: c["call_index"])


def replay(task: dict, calls: list[dict]) -> list[tuple[int, dict]]:
    """在**新服务**上按原顺序重放，返回每条 (status, body)。

    不开端口：`HTTPServer.shutdown()` 在没起过 `serve_forever` 时会永久阻塞，
    重放不需要 HTTP 层，也就不该为了对称去开一个（`test_replay_premise.py` 踩过）。
    """
    svc = mt.MockToolService(task)
    svc.reset()
    return [svc.call(c["name"], c["params"]) for c in calls]


def rows_from_calls(task: dict, calls: list[dict]) -> Rows:
    """重放并拼行：只取成功页，失败页记进 `skipped`（见模块文档第 2 条）。"""
    calls = _checked_order(calls)
    sources = row_sources(task)
    tool, key = next(iter(sources.items()))
    out = Rows(tool=tool, key=key)

    for call, (status, body) in zip(calls, replay(task, calls)):
        idx = call["call_index"]
        if call["name"] != tool:
            continue                       # 写报告之类的调用不参与行集，不必记
        if not (200 <= status < 300):
            out.skipped.append({"call_index": idx, "reason": f"http_{status}"})
            continue
        page = body.get(key)
        if not isinstance(page, list):
            raise ReplayError(
                f"第 {idx} 条调用成功了，但返回体没有 {key!r} 这一项（实际键 {sorted(body)}）——"
                "工具声明与实现对不上，不是这个 Agent 的问题"
            )
        out.rows.extend(copy.deepcopy(page))
        out.used.append(idx)
    return out


def rows_of_trace(task: dict, trace: dict) -> Rows:
    """`calls_of` + `rows_from_calls` 的合并入口。"""
    return rows_from_calls(task, calls_of(trace))


def derive_from_calls(task: dict, calls: list[dict]) -> tuple[Rows, dict[str, float]]:
    """重放 → 拼行 → 按任务的 `derivation:` 算出数值。

    返回行集本身，因为「用了哪几页」是诊断要看的东西：只拉了一页也会算出一个数，
    差别全在 `used` / `skipped` 里，只给数值等于把证据藏起来。
    """
    rows = rows_from_calls(task, calls)
    return rows, derive(rows.rows, load_rule(task))


def derive_from_trace(task: dict, trace: dict) -> tuple[Rows, dict[str, float]]:
    return derive_from_calls(task, calls_of(trace))


if __name__ == "__main__":  # pragma: no cover - 手工探查入口
    import json
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from arena.diagnosis_report import load_task
    from arena.reference_agent import ReferenceAgent, ToolClient
    from arena import mock_tools

    task_path = Path(__file__).resolve().parents[2] / "tasks" / "dbg-001-order-report.yaml"
    task = load_task(task_path)
    seen: list[dict] = []
    srv, svc = mock_tools.make_server(task, port=0, on_call=lambda n, p, s, b=None: seen.append(
        {"call_index": len(seen) + 1, "name": n, "params": dict(p)}
    ))
    import threading
    host, port = srv.server_address[:2]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        svc.reset()
        ReferenceAgent(task, ToolClient(f"http://{host}:{port}")).run()
    finally:
        srv.shutdown()
        srv.server_close()

    rows, values = derive_from_calls(task, seen)
    print(json.dumps({
        "rows": rows.as_dict(),
        "derived": values,
        "expected": task["test_inputs"]["expected"],
    }, ensure_ascii=False, indent=2))
